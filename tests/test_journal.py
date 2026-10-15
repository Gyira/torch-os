"""Journal checks use temporary databases/vaults and never connect to a model."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app import journal
from backend.app.db import Database
from backend.app.goals import GoalCreate, GoalTaskService, TaskCreate, TaskUpdate
from backend.app.journal import JournalAction, JournalService, JournalWrite, create_journal_router
from backend.app.vault import parse_markdown, scan_vault


DAY = "2026-10-02"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "shanghai_today", lambda: date(2026, 10, 3))
    database = Database(tmp_path / "journal.sqlite3")
    vault = tmp_path / "vault"
    service = JournalService(database, vault)
    goals = GoalTaskService(database)
    app = FastAPI()
    app.include_router(create_journal_router(database, vault))
    return service, goals, TestClient(app)


def save(client, day=DAY, **fields):
    return client.put(f"/api/journal/{day}", json={"revision": 0, "content": "今天完成了章节阅读。", **fields})


def action(client, kind, day=DAY, revision=1):
    return client.post(f"/api/journal/{day}/{kind}", json={"revision": revision, "confirm": True})


def test_missing_record_is_not_failure_and_review_is_read_only(setup):
    service, goals, client = setup
    empty = client.get(f"/api/journal/{DAY}").json()
    assert empty == {"date": DAY, "content": "", "highlights": "", "obstacles": "", "diet_summary": "", "mood": "", "next_action": "",
                     "notes": "", "reflection": "", "weather": "", "location": "",
                     "goal_id": None, "revision": 0, "recorded": False, "created_at": None, "updated_at": None}
    draft = client.post(f"/api/journal/{DAY}/review").json()
    assert draft["recorded"] is False
    assert "未记录不代表没有进展" in draft["summary"]
    assert "不是历史状态快照" in draft["warning"]
    assert draft["completed_tasks"] == draft["open_tasks"] == []
    assert draft["goal"] is None
    assert client.get(f"/api/journal/{DAY}").json() == empty
    assert service.database.list_memories() == []


def test_save_strip_noop_update_and_explicit_goal_detach(setup):
    service, goals, client = setup
    goal = goals.create_goal(GoalCreate(title="学习目标"))
    first = save(client, content="  写下事实  ", highlights="  记住方法  ", goal_id=goal["id"])
    assert first.status_code == 200
    first = first.json()
    assert first["content"] == "写下事实"
    assert first["highlights"] == "记住方法"
    assert first["revision"] == 1 and first["recorded"] is True
    same = save(client, revision=1, content="写下事实", highlights="记住方法", goal_id=goal["id"]).json()
    assert same == first
    updated = save(client, revision=1, content="修正后的事实", goal_id=None).json()
    assert updated["revision"] == 2 and updated["created_at"] == first["created_at"]
    assert updated["goal_id"] is None and updated["highlights"] == ""
    assert service.database.list_memories() == []


@pytest.mark.parametrize("fields", [
    {"content": "  \n ", "highlights": " ", "obstacles": " ", "diet_summary": " ", "mood": " ", "next_action": " "},
    {"content": None}, {"highlights": None}, {"obstacles": None}, {"diet_summary": None}, {"mood": None}, {"next_action": None},
    {"revision": None}, {"revision": True}, {"revision": "0"}, {"revision": -1},
    {"goal_id": True}, {"goal_id": "1"}, {"goal_id": 0}, {"unexpected": "x"},
    {"content": "x" * 12001}, {"highlights": "x" * 4001},
    {"obstacles": "x" * 4001}, {"diet_summary": "x" * 301}, {"mood": "x" * 101}, {"next_action": "x" * 501},
])
def test_invalid_input_rejected_without_writing(setup, fields):
    service, goals, client = setup
    assert save(client, **fields).status_code == 422
    assert not service.get(DAY)["recorded"]


@pytest.mark.parametrize("field,limit", [("content", 12000), ("highlights", 4000), ("obstacles", 4000), ("diet_summary", 300), ("mood", 100), ("next_action", 500)])
def test_exact_text_limit_applies_after_trim(setup, field, limit):
    service, goals, client = setup
    response = save(client, **{field: "  " + "中" * limit + "  "})
    assert response.status_code == 200
    assert len(response.json()[field]) == limit


def test_missing_revision_and_all_empty_fields_are_rejected_but_single_reflection_saves(setup):
    service, goals, client = setup
    assert client.put(f"/api/journal/{DAY}", json={"content": "missing revision"}).status_code == 422
    assert client.put(f"/api/journal/{DAY}", json={"revision": 0}).status_code == 422
    response = client.put(f"/api/journal/{DAY}", json={"revision": 0, "next_action": "只记录下一步"})
    assert response.status_code == 200
    assert response.json()["content"] == ""


def test_mood_and_diet_are_optional_fields_and_export_as_candidates(setup):
    service, goals, client = setup
    only_mood = client.put(f"/api/journal/{DAY}", json={"revision": 0, "mood": "  平静，有点累  "})
    assert only_mood.status_code == 200
    record = only_mood.json()
    assert record["recorded"] is True and record["mood"] == "平静，有点累" and record["content"] == ""
    assert record["diet_summary"] == ""

    updated = save(client, revision=1, diet_summary="三餐正常，晚上外食", mood="轻松")
    assert updated.status_code == 200
    body = updated.json()
    assert body["diet_summary"] == "三餐正常，晚上外食" and body["mood"] == "轻松" and body["revision"] == 2

    exported = action(client, "export", revision=2)
    assert exported.status_code == 200
    payload = exported.json()
    _, document_body = parse_markdown(service.vault_dir / payload["document"]["path"])
    assert "## 饮食摘要\n\n三餐正常，晚上外食" in document_body
    assert "## 情绪\n\n轻松" in document_body
    titles = {memory["title"] for memory in payload["memories"]}
    assert f"{DAY} · 饮食摘要" in titles and f"{DAY} · 情绪" in titles
    assert service.database.search_memories("三餐正常") == [], "exported candidates are not active memories"


@pytest.mark.parametrize("day", ["2026-2-01", "20261002", "2026-02-30", "2026-10-02T00:00:00Z", "2026-10-04", "0000-01-01"])
def test_dates_are_strict_and_future_is_rejected_on_all_operations(setup, day):
    service, goals, client = setup
    assert client.get(f"/api/journal/{day}").status_code == 422
    assert save(client, day=day).status_code == 422
    assert client.post(f"/api/journal/{day}/review").status_code == 422
    assert action(client, "next-task", day=day).status_code == 422
    assert action(client, "export", day=day).status_code == 422


def test_today_uses_shanghai_across_utc_midnight(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 2, 16, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(journal, "datetime", FrozenDateTime)
    assert journal.shanghai_today() == date(2026, 10, 3)
    assert journal.validate_day("2026-10-03") == date(2026, 10, 3)
    with pytest.raises(HTTPException) as exc:
        journal.validate_day("2026-10-04")
    assert exc.value.status_code == 422


def test_unknown_goal_and_stale_revision_do_not_mutate_record(setup):
    service, goals, client = setup
    assert save(client, goal_id=99999).status_code == 404
    assert not service.get(DAY)["recorded"]
    first = save(client).json()
    assert save(client, revision=1, goal_id=99999).status_code == 404
    assert save(client, content="stale").status_code == 409
    assert service.get(DAY) == first


def test_parent_deletion_retains_diary_and_detaches_goal(setup):
    service, goals, client = setup
    goal = goals.create_goal(GoalCreate(title="暂时目标"))
    save(client, goal_id=goal["id"])
    goals.delete_goal(goal["id"])
    assert service.get(DAY)["goal_id"] is None
    assert service.get(DAY)["content"] == "今天完成了章节阅读。"


def test_list_range_boundaries_and_only_recorded_days(setup):
    service, goals, client = setup
    save(client, day="2026-09-03")
    save(client)
    data = client.get("/api/journal?from_date=2026-09-03&to_date=2026-10-03").json()
    assert [row["date"] for row in data] == [DAY, "2026-09-03"]
    assert client.get("/api/journal?from_date=2026-09-02&to_date=2026-10-03").status_code == 422
    assert client.get("/api/journal?from_date=2026-10-02&to_date=2026-10-01").status_code == 422
    assert client.get("/api/journal?from_date=2026-10-02&to_date=2026-10-04").status_code == 422
    assert client.get("/api/journal?from_date=2026-10-01&to_date=2026-10-01").json() == []


def test_review_uses_real_completed_time_shanghai_and_current_open_states(setup):
    service, goals, client = setup
    goal = goals.create_goal(GoalCreate(title="关联目标"))
    save(client, goal_id=goal["id"], highlights="完成一次练习", next_action="下一次练习")
    rows = [
        ("start-inclusive", "completed", "2026-10-01T16:00:00+00:00", None, "2026-09-01T00:00:00Z"),
        ("end-inclusive", "completed", "2026-10-02T15:59:59+00:00", None, "2026-09-01T00:00:00Z"),
        ("previous-day", "completed", "2026-10-01T15:59:59+00:00", None, "2026-09-01T00:00:00Z"),
        ("next-day", "completed", "2026-10-02T16:00:00+00:00", None, "2026-09-01T00:00:00Z"),
        ("no-time", "completed", None, None, "2026-09-01T00:00:00Z"),
        ("invalid-time", "completed", "not-a-timestamp", None, "2026-09-01T00:00:00Z"),
        ("reopened", "todo", "2026-10-02T01:00:00Z", "2026-10-02", "2026-10-01T16:00:00Z"),
        ("overdue", "in_progress", None, "2026-10-01", "2026-09-01T00:00:00Z"),
        ("created-later", "todo", None, "2026-10-02", "2026-10-02T16:00:00Z"),
        ("cancelled", "cancelled", None, "2026-10-01", "2026-09-01T00:00:00Z"),
        ("no-due", "todo", None, None, "2026-09-01T00:00:00Z"),
        ("not-yet-due", "todo", None, "2026-10-03", "2026-09-01T00:00:00Z"),
    ]
    for title, status, completed_at, due, created in rows:
        task = goals.create_task(TaskCreate(title=title, status=status, due_date=due))
        with service.database.connect() as connection:
            connection.execute("UPDATE tasks SET completed_at=?,created_at=? WHERE id=?", (completed_at, created, task["id"]))
    review = client.post(f"/api/journal/{DAY}/review").json()
    assert {task["title"] for task in review["completed_tasks"]} == {"start-inclusive", "end-inclusive"}
    assert {task["title"] for task in review["open_tasks"]} == {"reopened", "overdue"}
    assert review["goal"]["id"] == goal["id"]
    assert review["next_action"] == "下一次练习"
    assert "完成一次练习" in review["summary"]


@pytest.mark.parametrize("kind", ["next-task", "export"])
@pytest.mark.parametrize("payload", [{}, {"revision": 1}, {"revision": 1, "confirm": False},
                                     {"revision": 1, "confirm": "true"}, {"revision": 1, "confirm": 1},
                                     {"revision": True, "confirm": True}, {"revision": 0, "confirm": True}])
def test_actions_require_explicit_confirmation_and_valid_revision(setup, kind, payload):
    service, goals, client = setup
    save(client, next_action="可以创建的任务")
    assert client.post(f"/api/journal/{DAY}/{kind}", json=payload).status_code == 422
    assert goals.list_tasks() == []
    assert service.database.list_documents() == []


def test_next_task_is_associated_complete_and_idempotent(setup):
    service, goals, client = setup
    goal = goals.create_goal(GoalCreate(title="学习目标"))
    text = "完整行动" * 100
    save(client, next_action=text, goal_id=goal["id"])
    first = action(client, "next-task").json()
    assert first["created"] is True and first["idempotent"] is False
    task = first["task"]
    assert task["goal_id"] == goal["id"]
    assert task["description"] == text and task["title"] == text[:200]
    assert task["due_date"] is None and task["status"] == "todo"
    second = action(client, "next-task").json()
    assert second["created"] is False and second["task"]["id"] == task["id"]
    assert len(goals.list_tasks()) == 1
    save(client, revision=1, next_action="第二个独立行动")
    assert action(client, "next-task").status_code == 409
    assert goals.get_task(task["id"])["description"] == text
    assert action(client, "next-task", revision=2).json()["task"]["title"] == "第二个独立行动"
    assert len(goals.list_tasks()) == 2


def test_next_task_missing_action_missing_record_and_deleted_task(setup):
    service, goals, client = setup
    assert action(client, "next-task").status_code == 404
    save(client)
    assert action(client, "next-task").status_code == 422
    save(client, revision=1, next_action="一步行动")
    task = action(client, "next-task", revision=2).json()["task"]
    goals.delete_task(task["id"])
    assert action(client, "next-task", revision=2).status_code == 409
    assert goals.list_tasks() == []


def test_next_task_and_ledger_rollback_together_on_audit_failure(setup):
    service, goals, client = setup
    save(client, next_action="事务行动")
    with service.database.connect() as connection:
        connection.execute("""CREATE TRIGGER fail_journal_task_audit BEFORE INSERT ON audit_events
        WHEN NEW.event_type='journal_task_created' BEGIN SELECT RAISE(ABORT, 'test audit failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="test audit failure"):
        service.next_task(DAY, JournalAction(revision=1, confirm=True))
    assert goals.list_tasks() == []
    with service.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM journal_actions").fetchone()[0] == 0


def test_export_preserves_full_authored_text_date_and_only_candidate_memories(setup):
    service, goals, client = setup
    text = "# 这也是用户正文\n\n" + "长段落完整保留" * 1000
    save(client, content=text, highlights="收获", obstacles="卡点", next_action="下一步")
    response = action(client, "export")
    assert response.status_code == 200
    exported = response.json()
    assert exported["created"] is True
    document = exported["document"]
    assert document["path"] == f"20-Journal/{DAY}-r1.md"
    metadata, body = parse_markdown(service.vault_dir / document["path"])
    assert metadata["event_date"] == DAY and metadata["journal_revision"] == 1
    assert text in body and "收获" in body and "卡点" in body and "下一步" in body
    memories = exported["memories"]
    assert all(memory["status"] == "candidate" for memory in memories)
    assert all(memory["source_quote"] in body for memory in memories)
    assert "".join(memory["content"] for memory in memories if memory["title"].endswith("每日记录")) == text
    assert service.database.search_memories("长段落完整保留") == []
    for memory in memories:
        history = service.database.memory_history(memory["id"])
        assert history[0]["event_type"] == "created"
    assert scan_vault(service.vault_dir, service.database)["changed"] == 0
    assert len(service.database.list_memories()) == len(memories)


def test_export_records_structured_coverage_and_exact_locations_for_repeated_text(setup):
    service, goals, client = setup
    # Deliberately repeat whole sections and place whitespace on the 2,000-character
    # boundary: exact slicing must retain it and never point at an earlier copy.
    repeated = "重复原文\n# 用户标题\n```text\n用户代码\n```\n"
    content = "中" * 1999 + " \n" + repeated + repeated + "末尾"
    values = {"content": content, "highlights": repeated.strip(), "obstacles": repeated.strip(), "next_action": "😀 " + repeated.strip()}
    assert save(client, **values).status_code == 200
    result = action(client, "export").json()
    body = result["document"]["content"]
    memories = result["memories"]
    coverage = result["document"]["coverage_summary"]
    assert coverage["extraction_version"] == "journal-fields-v1"
    assert coverage["candidate_count"] == coverage["total_candidate_segments"] == len(memories) == 5
    assert coverage["omitted_segments"] == 0
    assert coverage["truncated"] is False and coverage["coverage_complete"] is True
    assert coverage["excluded_heading_lines"] == 4
    assert coverage["excluded_code_blocks"] == coverage["excluded_code_lines"] == 0
    assert coverage["source_location_basis"] == "document_body"
    assert "不代表 AI 已理解" in coverage["scope_note"]
    assert "仅排除系统生成的字段标题" in coverage["scope_note"]
    assert coverage["segment_char_limit"] == 2000 and coverage["candidate_limit"] is None
    cursor = 0
    for index, (field, value) in enumerate(values.items()):
        if index:
            cursor += 2
        cursor += len(f"## {journal.FIELD_LABELS[field]}\n\n")
        expected_start = cursor
        fragments = [item for item in memories if item["title"] == f"{DAY} · {journal.FIELD_LABELS[field]}"]
        assert "".join(item["content"] for item in fragments) == value
        for fragment in fragments:
            start = fragment["source_start_offset"]
            end = fragment["source_end_offset"]
            assert start == expected_start and end > start
            assert body[start:end] == fragment["source_quote"] == fragment["content"]
            assert fragment["source_start_line"] == body.count("\n", 0, start) + 1
            assert fragment["source_end_line"] == body.count("\n", 0, end - 1) + 1
            assert fragment["source_location_basis"] == "document_body"
            expected_start = end
        cursor += len(value)
        assert expected_start == cursor
    assert cursor == len(body)
    assert service.database.get_document(result["document"]["id"])["coverage_summary"] == coverage
    assert service.database.list_documents()[0]["coverage_summary"] == coverage
    again = action(client, "export").json()
    assert again["document"]["coverage_summary"] == coverage
    assert again["memories"] == memories


def test_export_coverage_counts_only_nonempty_system_fields(setup):
    service, goals, client = setup
    assert save(client, content="", highlights="## 用户小标题\n\n只有收获").status_code == 200
    result = action(client, "export").json()
    coverage = result["document"]["coverage_summary"]
    assert coverage["excluded_heading_lines"] == 1
    assert coverage["candidate_count"] == coverage["total_candidate_segments"] == 1
    fragment = result["memories"][0]
    assert fragment["source_quote"] == "## 用户小标题\n\n只有收获"
    assert fragment["source_start_offset"] == len("## 完成与收获\n\n")
    assert fragment["source_start_line"] == 3 and fragment["source_end_line"] == 5


def test_export_repeats_without_resetting_confirmed_memory_and_keeps_revision_snapshots(setup):
    service, goals, client = setup
    save(client, content="原始记录")
    first = action(client, "export").json()
    old_path = service.vault_dir / first["document"]["path"]
    original = old_path.read_bytes()
    service.database.update_memory_status(first["memories"][0]["id"], "active")
    repeated = action(client, "export").json()
    assert repeated["created"] is False and repeated["idempotent"] is True
    assert repeated["document"]["id"] == first["document"]["id"]
    assert repeated["memories"][0]["status"] == "active"
    hits = service.database.search_memories("原始记录")
    assert hits[0]["source_date"] == DAY
    save(client, revision=1, content="更新的记录")
    assert action(client, "export").status_code == 409
    second = action(client, "export", revision=2).json()
    assert second["document"]["id"] != first["document"]["id"]
    assert all(memory["status"] == "candidate" for memory in second["memories"])
    assert old_path.read_bytes() == original


@pytest.mark.parametrize("change", ["content", "missing", "index"])
def test_export_refuses_to_overwrite_altered_or_unavailable_snapshot(setup, change):
    service, goals, client = setup
    save(client)
    first = action(client, "export").json()
    path = service.vault_dir / first["document"]["path"]
    if change == "content":
        path.write_text("人工更改的资料", encoding="utf-8")
    elif change == "missing":
        path.unlink()
    else:
        with service.database.connect() as connection:
            connection.execute("UPDATE documents SET available=0 WHERE id=?", (first["document"]["id"],))
    assert action(client, "export").status_code == 409
    if change == "content":
        assert path.read_text(encoding="utf-8") == "人工更改的资料"
    assert len(service.database.list_memories()) == len(first["memories"])


def test_export_recovers_interrupted_sql_transaction_without_duplicate_index(setup):
    service, goals, client = setup
    save(client)
    with service.database.connect() as connection:
        connection.execute("""CREATE TRIGGER fail_journal_export BEFORE INSERT ON audit_events
        WHEN NEW.event_type='journal_exported' BEGIN SELECT RAISE(ABORT, 'test export failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="test export failure"):
        service.export(DAY, JournalAction(revision=1, confirm=True))
    assert service.database.list_documents() == []
    assert service.database.list_memories() == []
    snapshot = service.vault_dir / f"20-Journal/{DAY}-r1.md"
    original_bytes = snapshot.read_bytes()
    with service.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM journal_actions").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_journal_export")
    recovered = action(client, "export").json()
    assert recovered["created"] is True
    assert snapshot.read_bytes() == original_bytes
    assert recovered["document"]["sha256"] == hashlib.sha256(original_bytes).hexdigest()


def _leave_scanned_export_after_failure(service, client):
    assert save(client, content="待保留的第一条事实\n\n待拒绝的第二条事实\n\n待修订的第三条事实").status_code == 200
    with service.database.connect() as connection:
        connection.execute("""CREATE TRIGGER fail_journal_export BEFORE INSERT ON audit_events
        WHEN NEW.event_type='journal_exported' BEGIN SELECT RAISE(ABORT, 'test export failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="test export failure"):
        service.export(DAY, JournalAction(revision=1, confirm=True))
    with service.database.connect() as connection:
        connection.execute("DROP TRIGGER fail_journal_export")
    assert scan_vault(service.vault_dir, service.database)["changed"] == 1
    document = service.database.list_documents()[0]
    return service.database.get_document(document["id"])


def test_export_recovers_scanned_snapshot_once_without_resetting_decisions(setup):
    service, goals, client = setup
    document = _leave_scanned_export_after_failure(service, client)
    candidates = service.database.list_memories()
    service.database.update_memory_status(candidates[0]["id"], "active")
    service.database.update_memory_status(candidates[1]["id"], "rejected")
    service.database.edit_memory(candidates[2]["id"], "用户确认前的人工修订内容", "人工修订标题")
    before = service.database.list_memories()
    event_counts = {memory["id"]: len(service.database.memory_history(memory["id"])) for memory in before}
    snapshot = service.vault_dir / document["path"]
    before_bytes = snapshot.read_bytes()
    response = action(client, "export")
    assert response.status_code == 200
    recovered = response.json()
    assert recovered["created"] is False and recovered["idempotent"] is True and recovered["recovered"] is True
    assert recovered["document"] == document
    assert recovered["document"]["coverage_summary"]["extraction_version"] == "sentences-v3"
    assert service.database.list_memories() == before
    assert {memory["id"]: len(service.database.memory_history(memory["id"])) for memory in before} == event_counts
    assert snapshot.read_bytes() == before_bytes
    repeated = action(client, "export").json()
    assert repeated["recovered"] is False and repeated["created"] is False
    assert repeated["document"]["id"] == document["id"]
    assert len(service.database.list_documents()) == 1
    with service.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM journal_actions WHERE action='export'").fetchone()[0] == 1
        events = connection.execute("SELECT detail_json FROM audit_events WHERE event_type='journal_exported'").fetchall()
        assert len(events) == 1 and json.loads(events[0][0])["recovered"] is True


def test_scanned_snapshot_recovery_ledger_and_audit_are_atomic(setup):
    service, goals, client = setup
    document = _leave_scanned_export_after_failure(service, client)
    memories = service.database.list_memories()
    with service.database.connect() as connection:
        connection.execute("""CREATE TRIGGER fail_journal_recovery BEFORE INSERT ON audit_events
        WHEN NEW.event_type='journal_exported' BEGIN SELECT RAISE(ABORT, 'test recovery failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="test recovery failure"):
        service.export(DAY, JournalAction(revision=1, confirm=True))
    assert service.database.get_document(document["id"]) == document
    assert service.database.list_memories() == memories
    with service.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM journal_actions").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_journal_recovery")
    assert action(client, "export").json()["recovered"] is True


@pytest.mark.parametrize("rescan", [False, True])
def test_scanned_snapshot_recovery_refuses_external_file_change(setup, rescan):
    service, goals, client = setup
    document = _leave_scanned_export_after_failure(service, client)
    path = service.vault_dir / document["path"]
    updated = path.read_text(encoding="utf-8") + "\n人工追加不属于日记快照的内容\n"
    path.write_text(updated, encoding="utf-8")
    if rescan:
        scan_vault(service.vault_dir, service.database)
    before_memories = service.database.list_memories()
    assert action(client, "export").status_code == 409
    assert path.read_text(encoding="utf-8") == updated
    assert service.database.list_memories() == before_memories
    with service.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM journal_actions").fetchone()[0] == 0


@pytest.mark.parametrize("mismatch", ["source_key", "path", "content", "title", "sha256", "metadata", "revision", "unavailable", "duplicate_id"])
def test_scanned_snapshot_recovery_requires_exact_document_identity_and_content(setup, mismatch):
    service, goals, client = setup
    document = _leave_scanned_export_after_failure(service, client)
    original = (service.vault_dir / document["path"]).read_bytes()
    with service.database.connect() as connection:
        if mismatch == "source_key":
            connection.execute("UPDATE documents SET source_key='different-source' WHERE id=?", (document["id"],))
        elif mismatch == "path":
            connection.execute("UPDATE documents SET path='different-path.md' WHERE id=?", (document["id"],))
        elif mismatch == "content":
            connection.execute("UPDATE documents SET content='人工改写的正文' WHERE id=?", (document["id"],))
        elif mismatch == "title":
            connection.execute("UPDATE documents SET title='其他标题' WHERE id=?", (document["id"],))
        elif mismatch == "sha256":
            connection.execute("UPDATE documents SET sha256='incorrect-hash' WHERE id=?", (document["id"],))
        elif mismatch == "metadata":
            metadata = json.loads(document["metadata_json"])
            metadata["visibility"] = "public"
            connection.execute("UPDATE documents SET metadata_json=? WHERE id=?", (json.dumps(metadata), document["id"]))
        elif mismatch == "revision":
            connection.execute("UPDATE documents SET revision=2 WHERE id=?", (document["id"],))
        elif mismatch == "unavailable":
            connection.execute("UPDATE documents SET available=0 WHERE id=?", (document["id"],))
        else:
            connection.execute("""INSERT INTO documents(path,title,content,sha256,metadata_json,indexed_at,source_key)
                VALUES ('another-document.md','不同文档','其他正文','other-hash','{}',?,?)""",
                               (document["indexed_at"], document["source_key"]))
    before_memories = service.database.list_memories()
    assert action(client, "export").status_code == 409
    assert service.database.list_memories() == before_memories
    assert (service.vault_dir / document["path"]).read_bytes() == original
    with service.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM journal_actions").fetchone()[0] == 0


def test_export_conflicting_unindexed_file_is_preserved(setup):
    service, goals, client = setup
    save(client)
    path = service.vault_dir / f"20-Journal/{DAY}-r1.md"
    path.parent.mkdir(parents=True)
    path.write_text("different source", encoding="utf-8")
    assert action(client, "export").status_code == 409
    assert path.read_text(encoding="utf-8") == "different source"
    assert service.database.list_documents() == []


def test_failed_file_publish_never_leaves_partial_snapshot_or_index(setup, monkeypatch):
    service, goals, client = setup
    save(client)

    def fail_publish(*args, **kwargs):
        raise OSError("filesystem does not support hard links")

    monkeypatch.setattr(journal.os, "link", fail_publish)
    response = action(client, "export")
    assert response.status_code == 409
    assert service.database.list_documents() == []
    assert service.database.list_memories() == []
    assert list((service.vault_dir / "20-Journal").iterdir()) == []


def test_export_path_validation_refuses_unsafe_vault_target(setup, monkeypatch):
    service, goals, client = setup
    save(client)

    def reject_path(*args, **kwargs):
        raise ValueError("unsafe symbolic link")

    monkeypatch.setattr(journal, "safe_note_path", reject_path)
    assert action(client, "export").status_code == 409
    assert service.database.list_documents() == []
    assert not service.vault_dir.exists()


def test_concurrent_save_has_one_winner_and_preserves_both_drafts_in_results(setup):
    service, goals, client = setup
    gate = Barrier(2)

    def submit(content):
        gate.wait(timeout=5)
        try:
            return service.save(DAY, JournalWrite(revision=0, content=content))
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ["窗口 A", "窗口 B"]))
    assert results.count(409) == 1
    winner = next(result for result in results if isinstance(result, dict))
    assert service.get(DAY)["content"] == winner["content"]
    assert service.get(DAY)["revision"] == 1


@pytest.mark.parametrize("kind", ["next_task", "export"])
def test_concurrent_actions_create_exactly_one_entity(setup, kind):
    service, goals, client = setup
    save(client, next_action="并发行动")
    gate = Barrier(2)

    def submit(_):
        gate.wait(timeout=5)
        return getattr(service, kind)(DAY, JournalAction(revision=1, confirm=True))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert sorted(result["created"] for result in results) == [False, True]
    if kind == "next_task":
        assert len(goals.list_tasks()) == 1
        assert results[0]["task"]["id"] == results[1]["task"]["id"]
    else:
        assert len(service.database.list_documents()) == 1
        assert results[0]["document"]["id"] == results[1]["document"]["id"]


def test_initialization_is_additive_and_repeatable(setup):
    service, goals, client = setup
    save(client)
    goal = goals.create_goal(GoalCreate(title="保留目标"))
    service.initialize()
    service.initialize()
    assert goals.get_goal(goal["id"])["title"] == "保留目标"
    assert service.get(DAY)["revision"] == 1
    with service.database.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
