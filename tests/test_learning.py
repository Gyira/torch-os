"""Learning checks run only against temporary databases and invented records."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app import learning
from backend.app.db import Database
from backend.app.goals import GoalCreate, GoalTaskService, GoalUpdate, TaskCreate, TaskUpdate
from backend.app.learning import LearningService, SessionCreate, SessionUpdate, create_learning_router


TODAY = date(2026, 10, 3)
DAY = "2026-10-02"
MONDAY = "2026-09-28"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(learning, "shanghai_today", lambda: TODAY)
    database = Database(tmp_path / "learning.sqlite3")
    service = LearningService(database)
    app = FastAPI()
    app.include_router(create_learning_router(database))
    return service, GoalTaskService(database), TestClient(app)


def subject(client, **values):
    response = client.post("/api/learning/subjects", json={"title": "虚构科目", "weekly_target_minutes": 120, **values})
    assert response.status_code == 200, response.text
    return response.json()


def session_payload(selected_subject_id, **values):
    return {"subject_id": selected_subject_id, "study_date": DAY, "minutes": 30, "content": "阅读虚构章节", "next_step": "整理例题",
            "request_key": str(uuid4()), **values}


def record(client, selected_subject_id, **values):
    return client.post("/api/learning/sessions", json=session_payload(selected_subject_id, **values))


def sessions(client, **query):
    return client.get("/api/learning/sessions", params={"from_date": MONDAY, "to_date": "2026-10-04", **query})


def test_subject_and_session_are_independent_of_tasks_and_memories(setup):
    service, goals, client = setup
    goal = goals.create_goal(GoalCreate(title="虚构学习目标"))
    task = goals.create_task(TaskCreate(title="完成学习任务", goal_id=goal["id"]))
    goals.update_task(task["id"], TaskUpdate(status="completed"))
    science = subject(client, title="  虚构数学  ", goal_id=goal["id"])
    assert science["title"] == "虚构数学" and science["revision"] == 1
    assert sessions(client).json() == []
    saved = record(client, science["id"]).json()
    assert saved["created"] is True and saved["idempotent"] is False
    item = saved["session"]
    assert item["goal_id_at_recording"] == goal["id"]
    assert item["goal_title_at_recording"] == "虚构学习目标"
    assert item["subject_title"] == "虚构数学" and item["goal_id"] == goal["id"]
    assert "request_hash" not in item
    with service.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0


def test_empty_week_is_honest_and_does_not_create_data(setup):
    service, goals, client = setup
    empty = client.get("/api/learning/weekly").json()
    assert empty["week_start"] == MONDAY and empty["week_end"] == "2026-10-04"
    assert empty["today"] == "2026-10-03"
    assert empty["subjects"] == []
    assert empty["totals"] == {"target_minutes": 0, "studied_minutes": 0, "sessions_count": 0,
                                "active_studied_minutes": 0, "progress_percent": None}
    assert "没有记录不代表没有学习" in empty["warning"]
    assert "不代表知识掌握程度" in empty["warning"]
    assert client.get("/api/learning/subjects").json() == []
    with service.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0


def test_week_statistics_are_from_recorded_minutes_and_current_targets(setup):
    service, goals, client = setup
    science = subject(client, weekly_target_minutes=100)
    language = subject(client, title="虚构语言", weekly_target_minutes=200)
    record(client, science["id"], minutes=60, study_date="2026-09-27")
    record(client, science["id"], minutes=60, study_date=MONDAY)
    record(client, science["id"], minutes=90)
    weekly = client.get("/api/learning/weekly").json()
    first, second = weekly["subjects"]
    assert (first["studied_minutes"], first["sessions_count"], first["progress_percent"]) == (150, 2, 150.0)
    assert (second["studied_minutes"], second["sessions_count"], second["progress_percent"]) == (0, 0, 0)
    assert weekly["totals"] == {"target_minutes": 300, "studied_minutes": 150, "sessions_count": 2,
                                 "active_studied_minutes": 150, "progress_percent": 50.0}
    changed = client.patch(f'/api/learning/subjects/{science["id"]}', json={"revision": 1, "weekly_target_minutes": 200})
    assert changed.status_code == 200
    assert client.get("/api/learning/weekly").json()["subjects"][0]["progress_percent"] == 75.0
    assert client.get("/api/learning/weekly", params={"week_start": "2026-09-21"}).json()["subjects"][0]["target_minutes"] == 200


def test_week_subjects_report_days_since_last_recorded_session(setup):
    _, _, client = setup
    recent = subject(client, title="虚构近期")
    five_days = subject(client, title="虚构五天前")
    empty = subject(client, title="虚构未开始")
    archived = subject(client, title="虚构归档")
    record(client, recent["id"], study_date="2026-10-03")
    record(client, five_days["id"], study_date="2026-09-28")
    record(client, archived["id"], study_date="2026-10-02")
    assert client.patch(f'/api/learning/subjects/{archived["id"]}',
                        json={"revision": 1, "status": "archived"}).status_code == 200

    weekly = client.get("/api/learning/weekly").json()
    rows = {item["title"]: item for item in weekly["subjects"]}
    assert (rows["虚构近期"]["last_studied_date"], rows["虚构近期"]["days_since_last"]) == ("2026-10-03", 0)
    assert (rows["虚构五天前"]["last_studied_date"], rows["虚构五天前"]["days_since_last"]) == ("2026-09-28", 5)
    assert (rows["虚构未开始"]["last_studied_date"], rows["虚构未开始"]["days_since_last"]) == (None, None)
    assert (rows["虚构归档"]["last_studied_date"], rows["虚构归档"]["days_since_last"]) == ("2026-10-02", 1)
    assert "最近一次记录按全部历史记录计算" in weekly["warning"]
    # 较早的一周里，“最近一次记录”仍按全部历史计算，不受所选周窗口限制。
    past = client.get("/api/learning/weekly", params={"week_start": "2026-09-21"}).json()
    past_rows = {item["title"]: item for item in past["subjects"]}
    assert past_rows["虚构五天前"]["days_since_last"] == 5


@pytest.mark.parametrize("values", [
    {"title": " \n "}, {"title": "x" * 121}, {"title": None},
    {"weekly_target_minutes": 0}, {"weekly_target_minutes": 10081},
    {"weekly_target_minutes": True}, {"weekly_target_minutes": "60"},
    {"goal_id": True}, {"status": "active"}, {"extra": "unrecognized"},
])
def test_subject_validation_prevents_invalid_persistence(setup, values):
    service, goals, client = setup
    response = client.post("/api/learning/subjects", json={"title": "虚构科目", "weekly_target_minutes": 120, **values})
    assert response.status_code == 422
    assert service.list_subjects() == []


def test_subject_revision_null_noop_and_goal_detach(setup):
    service, goals, client = setup
    goal = goals.create_goal(GoalCreate(title="虚构目标"))
    science = subject(client, goal_id=goal["id"])
    path = f'/api/learning/subjects/{science["id"]}'
    assert client.patch(path, json={"title": "未给版本"}).status_code == 422
    assert client.patch(path, json={"revision": True, "title": "错误版本"}).status_code == 422
    assert client.patch(path, json={"revision": 1, "title": None}).status_code == 422
    assert client.patch(path, json={"revision": 1, "weekly_target_minutes": None}).status_code == 422
    assert client.patch(path, json={"revision": 1, "status": None}).status_code == 422
    assert client.patch(path, json={"revision": 1, "goal_id": 9999}).status_code == 404
    assert client.patch(path, json={"revision": 1}).json() == science
    assert client.patch(path, json={"revision": 1, "title": "  虚构科目  "}).json() == science
    changed = client.patch(path, json={"revision": 1, "goal_id": None, "title": "修正科目"}).json()
    assert changed["goal_id"] is None and changed["revision"] == 2
    assert changed["created_at"] == science["created_at"]
    assert client.patch(path, json={"revision": 1, "title": "旧草稿"}).status_code == 409
    assert client.patch("/api/learning/subjects/9999", json={"revision": 1, "title": "无此科目"}).status_code == 404


def test_get_subject_refetches_latest_definition_and_preserves_revision_contract(setup):
    service, goals, client = setup
    goal = goals.create_goal(GoalCreate(title="虚构目标"))
    science = subject(client, goal_id=goal["id"])
    path = f'/api/learning/subjects/{science["id"]}'
    assert client.get(path).json() == science

    changed = client.patch(path, json={"revision": 1, "goal_id": None, "title": "修正科目"})
    assert changed.status_code == 200
    latest = changed.json()
    assert latest["goal_id"] is None and latest["revision"] == 2
    assert client.get(path).json() == latest

    # A no-op keeps the same revision; stale optimistic edits remain conflicts.
    assert client.patch(path, json={"revision": 2, "goal_id": None}).json() == latest
    assert client.patch(path, json={"revision": 1, "title": "旧草稿"}).status_code == 409
    assert client.get("/api/learning/subjects/0").status_code == 422
    assert client.get("/api/learning/subjects/9223372036854775808").status_code == 422
    assert client.get("/api/learning/subjects/9223372036854775807").status_code == 404
    assert client.get("/api/learning/subjects/9999").status_code == 404


@pytest.mark.parametrize("values", [
    {"minutes": 0}, {"minutes": 1441}, {"minutes": True}, {"minutes": "30"}, {"minutes": 30.0},
    {"subject_id": True}, {"content": None}, {"next_step": None}, {"study_date": None},
    {"content": "中" * 4001}, {"next_step": "中" * 501}, {"request_key": "not-a-uuid"},
    {"request_key": None}, {"unexpected": True},
])
def test_invalid_session_inputs_are_rejected_without_records(setup, values):
    service, goals, client = setup
    science = subject(client)
    assert record(client, science["id"], **values).status_code == 422
    assert sessions(client).json() == []


@pytest.mark.parametrize("day", ["2026-1-02", "20261002", "2026-02-30", "2026-10-02T00:00:00Z", "0000-01-01", "2026-10-04", "2026-10-02 "])
def test_session_dates_strict_and_never_future(setup, day):
    service, goals, client = setup
    science = subject(client)
    assert record(client, science["id"], study_date=day).status_code == 422
    item = record(client, science["id"]).json()["session"]
    assert client.patch(f'/api/learning/sessions/{item["id"]}', json={"revision": 1, "study_date": day}).status_code == 422
    assert sessions(client).json() == [item]


def test_session_text_limits_and_empty_reflection_are_supported(setup):
    service, goals, client = setup
    science = subject(client)
    item = record(client, science["id"], content="  " + "中" * 4000 + "  ", next_step="中" * 500).json()["session"]
    assert len(item["content"]) == 4000 and len(item["next_step"]) == 500
    changed = client.patch(f'/api/learning/sessions/{item["id"]}', json={"revision": 1, "content": "", "next_step": ""}).json()
    assert changed["content"] == changed["next_step"] == ""


def test_request_key_retry_is_stable_even_after_session_edit_and_subject_archive(setup):
    service, goals, client = setup
    science = subject(client)
    payload = session_payload(science["id"])
    first = client.post("/api/learning/sessions", json=payload).json()
    retry = client.post("/api/learning/sessions", json={**payload, "request_key": payload["request_key"].upper()}).json()
    assert retry == {"created": False, "idempotent": True, "session": first["session"]}
    assert client.post("/api/learning/sessions", json={**payload, "minutes": 31}).status_code == 409
    item = first["session"]
    changed = client.patch(f'/api/learning/sessions/{item["id"]}', json={"revision": 1, "minutes": 45}).json()
    client.patch(f'/api/learning/subjects/{science["id"]}', json={"revision": 1, "status": "archived"})
    later = client.post("/api/learning/sessions", json=payload).json()
    assert later["created"] is False and later["session"]["id"] == item["id"]
    assert later["session"]["revision"] == 2 and later["session"]["minutes"] == 45
    assert later["session"]["subject_status"] == "archived"
    assert len(sessions(client).json()) == 1
    with service.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='study_session_created'").fetchone()[0] == 1


def test_same_payload_concurrent_retries_create_one_record(setup):
    service, goals, client = setup
    science = subject(client)
    payload = SessionCreate(**session_payload(science["id"]))
    barrier = Barrier(2)

    def save():
        barrier.wait()
        return service.create_session(payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: save(), range(2)))
    assert sorted(row["created"] for row in results) == [False, True]
    assert results[0]["session"]["id"] == results[1]["session"]["id"]


def test_request_key_conflict_concurrently_does_not_add_minutes(setup):
    service, goals, client = setup
    science = subject(client)
    request_key = str(uuid4())
    barrier = Barrier(2)

    def save(minutes):
        barrier.wait()
        try:
            return service.create_session(SessionCreate(**session_payload(science["id"], minutes=minutes, request_key=request_key)))
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [10, 20]))
    assert sum(isinstance(row, dict) for row in results) == 1
    assert 409 in results
    assert len(sessions(client).json()) == 1


def test_daily_limit_counts_all_subjects_and_correction_destination(setup):
    service, goals, client = setup
    science = subject(client)
    language = subject(client, title="第二虚构科目")
    first = record(client, science["id"], minutes=1000).json()["session"]
    second = record(client, language["id"], minutes=440).json()["session"]
    assert record(client, language["id"], minutes=1).status_code == 422
    assert client.patch(f'/api/learning/sessions/{first["id"]}', json={"revision": 1, "minutes": 1001}).status_code == 422
    previous_day = record(client, science["id"], minutes=1, study_date="2026-10-01").json()["session"]
    assert client.patch(f'/api/learning/sessions/{previous_day["id"]}', json={"revision": 1, "study_date": DAY}).status_code == 422
    assert client.patch(f'/api/learning/sessions/{second["id"]}', json={"revision": 1, "minutes": 439}).status_code == 200
    moved = client.patch(f'/api/learning/sessions/{previous_day["id"]}', json={"revision": 1, "study_date": DAY})
    assert moved.status_code == 200
    assert sum(row["minutes"] for row in sessions(client).json()) == 1440


def test_daily_limit_survives_competing_writers(setup):
    service, goals, client = setup
    first, second = subject(client), subject(client, title="另一科目")
    barrier = Barrier(2)

    def save(subject_id):
        barrier.wait()
        try:
            service.create_session(SessionCreate(**session_payload(subject_id, minutes=800)))
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [first["id"], second["id"]]))
    assert sorted(results) == [200, 422]
    assert client.get("/api/learning/weekly").json()["totals"]["studied_minutes"] == 800


def test_edits_require_revision_and_preserve_unset_fields(setup):
    service, goals, client = setup
    science = subject(client)
    item = record(client, science["id"]).json()["session"]
    path = f'/api/learning/sessions/{item["id"]}'
    assert client.patch(path, json={"minutes": 20}).status_code == 422
    assert client.patch(path, json={"revision": 1, "subject_id": science["id"]}).status_code == 422
    assert client.patch(path, json={"revision": 1, "minutes": None}).status_code == 422
    assert client.patch(path, json={"revision": 1, "request_key": str(uuid4())}).status_code == 422
    assert client.patch(path, json={"revision": 1}).json() == item
    assert client.patch(path, json={"revision": 1, "content": " 阅读虚构章节 "}).json() == item
    changed = client.patch(path, json={"revision": 1, "minutes": 50}).json()
    assert changed["revision"] == 2 and changed["minutes"] == 50
    assert changed["content"] == item["content"] and changed["request_key"] == item["request_key"]
    assert changed["created_at"] == item["created_at"]
    assert client.patch(path, json={"revision": 1, "minutes": 80}).status_code == 409
    assert client.patch("/api/learning/sessions/9999", json={"revision": 1, "minutes": 80}).status_code == 404


def test_competing_corrections_do_not_overwrite_the_new_revision(setup):
    service, goals, client = setup
    item = record(client, subject(client)["id"]).json()["session"]
    barrier = Barrier(2)

    def update(minutes):
        barrier.wait()
        try:
            service.update_session(item["id"], SessionUpdate(revision=1, minutes=minutes))
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [40, 50]))
    assert sorted(results) == [200, 409]
    assert sessions(client).json()[0]["revision"] == 2


def test_archived_records_remain_visible_and_correctable_without_false_goal_progress(setup):
    service, goals, client = setup
    science = subject(client, weekly_target_minutes=60)
    unused = subject(client, title="未使用归档科目")
    active = subject(client, title="仍活跃科目", weekly_target_minutes=100)
    item = record(client, science["id"], minutes=100).json()["session"]
    record(client, active["id"], minutes=20)
    for archived in (science, unused):
        assert client.patch(f'/api/learning/subjects/{archived["id"]}', json={"revision": 1, "status": "archived"}).status_code == 200
    assert record(client, science["id"]).status_code == 409
    corrected = client.patch(f'/api/learning/sessions/{item["id"]}', json={"revision": 1, "minutes": 120})
    assert corrected.status_code == 200
    weekly = client.get("/api/learning/weekly").json()
    assert [row["subject_id"] for row in weekly["subjects"]] == [active["id"], science["id"]]
    old = weekly["subjects"][1]
    assert old["target_minutes"] == 0 and old["weekly_target_minutes"] == 60
    assert old["studied_minutes"] == 120 and old["progress_percent"] is None
    assert weekly["totals"] == {"target_minutes": 100, "studied_minutes": 140, "active_studied_minutes": 20,
                                 "sessions_count": 2, "progress_percent": 20.0}
    assert "周目标进度按当前活跃科目计算" in weekly["warning"]
    assert len(client.get("/api/learning/subjects").json()) == 3
    assert client.patch(f'/api/learning/subjects/{science["id"]}', json={"revision": 2, "status": "active"}).status_code == 200
    assert record(client, science["id"]).status_code == 200


def test_goal_snapshot_survives_reassignment_rename_and_deletion(setup):
    service, goals, client = setup
    first = goals.create_goal(GoalCreate(title="虚构原始目标"))
    second = goals.create_goal(GoalCreate(title="虚构新目标"))
    science = subject(client, goal_id=first["id"])
    original = record(client, science["id"]).json()["session"]
    goals.update_goal(first["id"], GoalUpdate(title="虚构改名目标"))
    client.patch(f'/api/learning/subjects/{science["id"]}', json={"revision": 1, "goal_id": second["id"]})
    later = record(client, science["id"]).json()["session"]
    goals.delete_goal(first["id"])
    goals.delete_goal(second["id"])
    rows = {row["id"]: row for row in sessions(client).json()}
    assert rows[original["id"]]["goal_id_at_recording"] == first["id"]
    assert rows[original["id"]]["goal_title_at_recording"] == "虚构原始目标"
    assert rows[later["id"]]["goal_id_at_recording"] == second["id"]
    assert rows[later["id"]]["goal_title_at_recording"] == "虚构新目标"
    assert rows[original["id"]]["goal_id"] is None
    with service.database.connect() as connection:
        audit = json.loads(connection.execute("SELECT detail_json FROM audit_events WHERE event_type='study_subject_updated'").fetchone()[0])
        assert audit["previous_goal_id"] == first["id"] and audit["goal_id"] == second["id"]


def test_session_range_uses_inclusive_93_days_and_subject_filter(setup):
    service, goals, client = setup
    science = subject(client)
    language = subject(client, title="另一虚构科目")
    record(client, science["id"], study_date="2026-07-03")
    record(client, science["id"])
    record(client, language["id"])
    all_rows = sessions(client, from_date="2026-07-03", to_date="2026-10-03").json()
    assert len(all_rows) == 3
    assert len(sessions(client, from_date="2026-07-03", to_date="2026-10-03", subject_id=science["id"]).json()) == 2
    assert sessions(client, from_date="2026-07-02", to_date="2026-10-03").status_code == 422
    assert sessions(client, from_date=DAY, to_date=MONDAY).status_code == 422
    assert sessions(client, from_date="2026-02-30").status_code == 422
    assert sessions(client, subject_id=9999).status_code == 404
    assert sessions(client, subject_id=0).status_code == 422
    assert record(client, 9999).status_code == 404


def test_shanghai_today_and_cross_year_week(monkeypatch, tmp_path):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 12, 31, 16, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(learning, "datetime", FrozenDateTime)
    assert learning.shanghai_today() == date(2027, 1, 1)
    weekly = LearningService(Database(tmp_path / "crossyear.sqlite3")).weekly()
    assert weekly["week_start"] == "2026-12-28"
    assert weekly["week_end"] == "2027-01-03"


def test_week_query_rejects_non_monday_or_future_week(setup):
    service, goals, client = setup
    for day in ("2026-10-01", "2026-10-05", "2026-02-30", "20260928"):
        assert client.get("/api/learning/weekly", params={"week_start": day}).status_code == 422
    assert client.get("/api/learning/weekly", params={"week_start": "2026-09-21"}).status_code == 200


def test_audit_failure_rolls_back_subject_create_and_session_edit(setup, monkeypatch):
    service, goals, client = setup
    science = subject(client)
    original = record(client, science["id"]).json()["session"]

    def fail(*_args):
        raise RuntimeError("invented audit write failure")

    monkeypatch.setattr(learning, "_audit", fail)
    with pytest.raises(RuntimeError, match="invented audit"):
        subject(client, title="回滚科目")
    with pytest.raises(RuntimeError, match="invented audit"):
        record(client, science["id"])
    with pytest.raises(RuntimeError, match="invented audit"):
        client.patch(f'/api/learning/sessions/{original["id"]}', json={"revision": 1, "minutes": 80})
    assert service.list_subjects() == [science]
    assert sessions(client).json() == [original]


def test_schema_constraints_and_reinitialization_preserve_records(setup):
    service, goals, client = setup
    science = subject(client)
    original = record(client, science["id"], minutes=1440).json()["session"]
    assert LearningService(service.database).list_sessions(MONDAY, DAY) == [original]
    mutations = [
        ("UPDATE study_subjects SET weekly_target_minutes=0", ()),
        ("UPDATE study_subjects SET status='deleted'", ()),
        ("UPDATE study_subjects SET goal_id=9999", ()),
        ("DELETE FROM study_subjects", ()),
        ("UPDATE study_sessions SET study_date='2026-02-30'", ()),
        ("UPDATE study_sessions SET minutes=0", ()),
        ("UPDATE study_sessions SET subject_id=9999", ()),
        ("UPDATE study_sessions SET revision=0", ()),
        ("""INSERT INTO study_sessions(subject_id,study_date,minutes,request_key,request_hash,created_at,updated_at)
            VALUES (?,?,1,?,?,?,?)""", (science["id"], DAY, str(uuid4()), "a" * 64, "now", "now")),
    ]
    for statement, params in mutations:
        with pytest.raises(sqlite3.IntegrityError):
            with service.database.connect() as connection:
                connection.execute(statement, params)
    assert sessions(client).json() == [original]


@pytest.mark.parametrize("field,operation", [
    ("goal_id", "create_subject"), ("goal_id", "update_subject"), ("subject_id", "create_session"),
    ("revision", "update_subject"), ("revision", "update_session"),
])
def test_huge_body_integer_returns_422_without_sqlite_overflow(setup, field, operation):
    service, goals, client = setup
    science = subject(client)
    item = record(client, science["id"]).json()["session"]
    invalid = {field: 2**63}
    if operation == "create_subject":
        response = client.post("/api/learning/subjects", json={"title": "新科目", "weekly_target_minutes": 60, **invalid})
    elif operation == "update_subject":
        response = client.patch(f'/api/learning/subjects/{science["id"]}', json={"revision": 1, **invalid})
    elif operation == "create_session":
        response = client.post("/api/learning/sessions", json=session_payload(science["id"], **invalid))
    else:
        response = client.patch(f'/api/learning/sessions/{item["id"]}', json={"minutes": 50, **invalid})
    assert response.status_code == 422
    assert service.list_subjects() == [science]
    assert sessions(client).json() == [item]


@pytest.mark.parametrize("endpoint", ["subject_path", "session_path", "session_read_path", "subject_query"])
def test_integer_id_http_boundary_rejects_overflow_but_preserves_missing_404(setup, endpoint):
    _, _, client = setup

    def request(entity_id):
        if endpoint == "subject_path":
            return client.patch(f"/api/learning/subjects/{entity_id}", json={"revision": 1, "title": "虚构科目"})
        if endpoint == "session_path":
            return client.patch(f"/api/learning/sessions/{entity_id}", json={"revision": 1, "minutes": 30})
        if endpoint == "session_read_path":
            return client.get(f"/api/learning/sessions/{entity_id}")
        return sessions(client, subject_id=entity_id)

    assert request(2**63).status_code == 422
    assert request(0).status_code == 422
    assert request(2**63 - 1).status_code == 404


def test_get_session_reloads_latest_record_after_another_tab_moves_date(setup):
    service, goals, client = setup
    goal = goals.create_goal(GoalCreate(title="虚构阅读目标"))
    science = subject(client, goal_id=goal["id"])
    item = record(client, science["id"]).json()["session"]
    path = f'/api/learning/sessions/{item["id"]}'
    changed = client.patch(path, json={"revision": 1, "study_date": "2026-09-27", "minutes": 60}).json()
    assert sessions(client).json() == []  # No longer in the caller's previous week.
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == changed
    assert response.json()["goal_id_at_recording"] == goal["id"]
    assert response.json()["goal_title_at_recording"] == goal["title"]
    assert response.json()["revision"] == 2
    assert client.get("/api/learning/sessions/9999").status_code == 404


def test_monthly_summary_counts_recorded_minutes_and_days_with_previous_month(setup):
    service, goals, client = setup
    science = subject(client, weekly_target_minutes=100)
    archived = subject(client, title="虚构历史科目", weekly_target_minutes=60)
    # Previous month (2026-09) minutes for both subjects.
    record(client, science["id"], minutes=90, study_date="2026-09-15")
    record(client, archived["id"], minutes=30, study_date="2026-09-15")
    # Current month (2026-10) minutes on two distinct days.
    record(client, science["id"], minutes=60, study_date="2026-10-01")
    record(client, science["id"], minutes=45, study_date="2026-10-03")
    monthly = client.get("/api/learning/monthly").json()
    assert monthly["month"] == "2026-10" and monthly["month_start"] == "2026-10-01" and monthly["month_end"] == "2026-10-31"
    assert monthly["totals"] == {"studied_minutes": 105, "sessions_count": 2, "active_studied_minutes": 105, "days_studied": 2}
    assert [item["studied_minutes"] for item in monthly["subjects"]] == [105, 0]
    previous = monthly["previous_month"]
    assert previous["month"] == "2026-09"
    assert previous["totals"] == {"studied_minutes": 120, "sessions_count": 2, "active_studied_minutes": 120, "days_studied": 1}
    # Archived subject keeps its previous-month minutes but never appears for months without records.
    archived_row = next(item for item in previous["subjects"] if item["subject_id"] == archived["id"])
    assert archived_row["studied_minutes"] == 30 and archived_row["status"] == "active"
    assert "没有记录不代表没有学习" in monthly["warning"] and "不代表知识掌握程度" in monthly["warning"]
    # Explicit past month view: September only, August comparison stays empty.
    september = client.get("/api/learning/monthly", params={"month": "2026-09"}).json()
    assert september["totals"]["studied_minutes"] == 120 and september["totals"]["days_studied"] == 1
    assert september["previous_month"]["month"] == "2026-08"
    assert september["previous_month"]["totals"] == {"studied_minutes": 0, "sessions_count": 0, "active_studied_minutes": 0, "days_studied": 0}
    # Read-only: monthly reads add no audit events and create no sessions.
    with service.database.connect() as connection:
        before = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert client.get("/api/learning/monthly", params={"month": "2026-08"}).status_code == 200
    with service.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == before
        assert connection.execute("SELECT COUNT(*) FROM study_sessions").fetchone()[0] == 4


@pytest.mark.parametrize("month,expected", [
    ("2026-13", 422), ("2026-00", 422), ("202611", 422), ("abcd-ef", 422),
    ("2026-11", 422), ("", 422), ("2026-1", 422),
])
def test_monthly_validation_rejects_future_and_malformed_months(setup, month, expected):
    service, goals, client = setup
    response = client.get("/api/learning/monthly", params={"month": month})
    assert response.status_code == expected


def test_monthly_defaults_to_current_month_without_parameter(setup):
    service, goals, client = setup
    science = subject(client)
    record(client, science["id"], minutes=25)
    response = client.get("/api/learning/monthly")
    assert response.status_code == 200
    assert response.json()["month"] == "2026-10"
    assert response.json()["totals"]["studied_minutes"] == 25


def test_monthly_archived_subject_minutes_stay_out_of_active_total(setup):
    service, goals, client = setup
    active = subject(client, title="虚构进行中科目", weekly_target_minutes=100)
    old = subject(client, title="虚构旧科目", weekly_target_minutes=100)
    record(client, active["id"], minutes=50)
    record(client, old["id"], minutes=70)
    assert client.patch(f'/api/learning/subjects/{old["id"]}', json={"revision": 1, "status": "archived"}).status_code == 200
    monthly = client.get("/api/learning/monthly").json()
    assert monthly["totals"]["studied_minutes"] == 120
    assert monthly["totals"]["active_studied_minutes"] == 50
    old_row = next(item for item in monthly["subjects"] if item["subject_id"] == old["id"])
    assert old_row["status"] == "archived" and old_row["studied_minutes"] == 70
