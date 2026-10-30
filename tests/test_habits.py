"""Habit API tests: cache-only contract plus Markdown authority (2026-10-28).

The first half keeps the historical SQLite-facing API contract (integer ids,
integer revisions, tri-state entries).  The second half proves that the Vault
Markdown files are authoritative: external note/log edits are read back, and
app writes land in Markdown before the cache.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import habits
from backend.app.db import Database
from backend.app.habits import HabitCreate, HabitService, create_habits_router

TODAY = date(2026, 10, 3)
FIRST = "2026-09-28"  # Monday; the frozen today is Saturday.
HABIT_DIR = "00_人生路/04_目标/04_习惯追踪"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(habits, "shanghai_today", lambda: TODAY)
    database = Database(tmp_path / "habits.sqlite3")
    app = FastAPI()
    app.include_router(create_habits_router(database))
    return database, TestClient(app)


@pytest.fixture
def vault_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(habits, "shanghai_today", lambda: TODAY)
    vault = tmp_path / "vault"
    vault.mkdir()
    database = Database(tmp_path / "habits.sqlite3")
    app = FastAPI()
    app.include_router(create_habits_router(database, vault_dir=vault))
    return database, TestClient(app), vault


def create(client, **changes):
    return client.post("/api/habits", json={
        "title": "读书", "minimum_action": "读一页", "start_date": FIRST, **changes,
    })


def put(client, habit_id, day=FIRST, **changes):
    return client.put(f"/api/habits/{habit_id}/entries/{day}",
                      json={"revision": 0, "status": "done", **changes})


def summary(client, habit_id, first=FIRST, last="2026-10-03"):
    return client.get(f"/api/habits/{habit_id}/summary",
                      params={"from_date": first, "to_date": last})


def events(database):
    with database.connect() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type LIKE 'habit_%' ORDER BY id")]


def test_missing_days_remain_unrecorded_and_rest_days_are_not_planned(setup):
    _, client = setup
    habit = create(client, weekdays=[0, 2, 4]).json()
    report = summary(client, habit["id"]).json()
    assert {key: report[key] for key in (
        "scheduled_days", "done", "skipped", "unrecorded", "completion_rate")} == {
        "scheduled_days": 3, "done": 0, "skipped": 0, "unrecorded": 3, "completion_rate": 0,
    }
    assert "未记录不代表失败" in report["warning"]
    assert [entry["date"] for entry in report["entries"] if entry["scheduled"]] == [
        FIRST, "2026-09-30", "2026-10-02"]
    assert all(entry["status"] == "unrecorded" and entry["revision"] == 0
               for entry in report["entries"])


def test_summary_distinguishes_done_skipped_missing_and_zero_denominator(setup):
    _, client = setup
    habit = create(client, weekdays=[0, 2, 4]).json()
    put(client, habit["id"])
    put(client, habit["id"], "2026-09-30", status="skipped", note="明确休息")
    report = summary(client, habit["id"]).json()
    assert (report["done"], report["skipped"], report["unrecorded"],
            report["completion_rate"]) == (1, 1, 1, 0.5)
    rest = summary(client, habit["id"], "2026-09-29", "2026-09-29").json()
    assert rest["scheduled_days"] == 0 and rest["completion_rate"] is None
    skipped = summary(client, habit["id"], "2026-09-30", "2026-09-30").json()
    assert skipped["skipped"] == 1 and skipped["completion_rate"] is None


def test_week_summary_counts_remaining_planned_days_to_sunday(setup):
    _, client = setup
    habit = create(client, weekdays=[0, 4, 6]).json()
    put(client, habit["id"])
    put(client, habit["id"], "2026-10-02", status="skipped", note="明确休息")
    week = summary(client, habit["id"]).json()["week"]
    assert (week["start"], week["end"]) == ("2026-09-28", "2026-10-04")
    assert (week["scheduled"], week["done"], week["skipped"], week["unrecorded"]) == (3, 1, 1, 1)
    assert week["remaining"] == 1
    early = summary(client, habit["id"], FIRST, "2026-10-02").json()["week"]
    assert early["remaining"] == 1
    saturday = create(client, weekdays=[5]).json()
    pending = summary(client, saturday["id"], FIRST, "2026-10-02").json()["week"]
    assert pending["remaining"] == 1
    put(client, saturday["id"], "2026-10-03")
    assert summary(client, saturday["id"]).json()["week"]["remaining"] == 0
    resting = create(client, weekdays=[0]).json()
    assert summary(client, resting["id"]).json()["week"]["remaining"] == 0


def test_create_trim_defaults_and_idempotent_schema_initialization(setup):
    database, client = setup
    response = client.post("/api/habits", json={"title": "  阅读  ", "minimum_action": "  一页  "})
    assert response.status_code == 201
    habit = response.json()
    assert habit["title"] == "阅读" and habit["minimum_action"] == "一页"
    assert habit["start_date"] == TODAY.isoformat() and habit["weekdays"] == list(range(7))
    assert habit["status"] == "active" and habit["revision"] == 1
    assert habit["frequency"]["kind"] == "daily"
    assert "weekdays_json" not in habit
    HabitService(database)
    assert client.get("/api/habits").json() == [habit]


@pytest.mark.parametrize("change", [
    {"title": "  "}, {"title": None}, {"title": "中" * 121},
    {"cue": "中" * 501}, {"weekdays": []}, {"weekdays": [1, 1]},
    {"weekdays": [7]}, {"weekdays": [-1]}, {"weekdays": [True]},
    {"weekdays": ["1"]}, {"weekdays": [1.0]}, {"weekdays": None},
    {"start_date": None}, {"status": "archived"}, {"priority": "urgent"},
    {"reminder": "25:00"}, {"color": "red"}, {"frequency": {"kind": "weekly"}},
])
def test_invalid_create_does_not_write(setup, change):
    database, client = setup
    assert create(client, **change).status_code == 422
    assert client.get("/api/habits").json() == [] and events(database) == []


@pytest.mark.parametrize("invalid", ["2026-9-28", "20260928", "2026-02-30",
                                     "2026-09-28T00:00:00Z"])
def test_strict_dates_reject_malformed_values(setup, invalid):
    _, client = setup
    habit = create(client).json()
    assert create(client, start_date=invalid).status_code == 422
    assert put(client, habit["id"], invalid).status_code == 422
    assert summary(client, habit["id"], invalid).status_code == 422
    assert summary(client, habit["id"], FIRST, invalid).status_code == 422


def test_future_start_is_allowed_but_future_checkins_are_not(setup):
    _, client = setup
    future = create(client, start_date="2026-10-04")
    assert future.status_code == 201
    assert client.put(f"/api/habits/{future.json()['id']}/entries/2026-10-04",
                      json={"revision": 0, "status": "done"}).status_code == 422
    assert summary(client, future.json()["id"], "2026-10-04", "2026-10-04").status_code == 422


def test_shanghai_day_and_creation_default_cross_utc_midnight(monkeypatch):
    from datetime import datetime, timezone

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 2, 16, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(habits, "datetime", FrozenDateTime)
    assert habits.shanghai_today() == TODAY
    assert HabitCreate(title="记录", minimum_action="一行").start_date == "2026-10-03"
    assert habits.validate_day("2026-10-03") == TODAY


def test_before_start_and_rest_day_cannot_receive_records(setup):
    _, client = setup
    habit = create(client, weekdays=[0, 4]).json()
    assert put(client, habit["id"], "2026-09-25").status_code == 422
    assert put(client, habit["id"], "2026-09-29").status_code == 422
    report = summary(client, habit["id"], "2026-09-25").json()
    assert report["scheduled_days"] == 2


def test_conflicts_noop_repeat_and_explicit_undo_preserve_revision_and_audit(setup):
    database, client = setup
    habit = create(client).json()
    no_record = put(client, habit["id"], status="unrecorded").json()
    assert no_record["revision"] == 0
    first = put(client, habit["id"], note="  看了一页  ").json()
    assert first["note"] == "看了一页" and first["revision"] == 1
    assert put(client, habit["id"], note="看了一页").status_code == 409
    same = put(client, habit["id"], revision=1, note="看了一页").json()
    assert same == first
    undone = put(client, habit["id"], revision=1, status="unrecorded", note="之前误点").json()
    assert undone["revision"] == 2 and undone["status"] == "unrecorded"
    assert put(client, habit["id"], revision=1).status_code == 409
    assert summary(client, habit["id"]).json()["done"] == 0
    event = json.loads(events(database)[-1]["detail_json"])
    assert (event["previous_status"], event["status"],
            event["previous_revision"], event["revision"]) == (
        "done", "unrecorded", 1, 2)
    assert "之前误点" not in str(events(database))


def test_schedule_edits_archive_and_history(setup):
    _, client = setup
    habit = create(client).json()
    habit_id = habit["id"]
    put(client, habit_id)
    path = f"/api/habits/{habit_id}"
    changed = client.patch(path, json={
        "revision": 1, "frequency": {"kind": "weekly", "weekdays": [0, 2]},
        "start_date": "2026-09-27", "title": "阅读",
    })
    assert changed.status_code == 200, changed.text
    latest = changed.json()
    assert latest["frequency"]["kind"] == "weekly"
    assert latest["weekdays"] == [0, 2] and latest["revision"] == 2
    archived = client.patch(path, json={"revision": 2, "status": "archived"}).json()
    assert archived["status"] == "archived" and archived["revision"] == 3
    assert client.get("/api/habits?status=active").json() == []
    assert client.get("/api/habits?status=archived").json() == [archived]
    assert put(client, habit_id, "2026-09-30").status_code == 409
    assert put(client, habit_id, revision=1, status="skipped").status_code == 200
    restored = client.patch(path, json={"revision": 3, "status": "active"})
    assert restored.status_code == 200


def test_reducing_target_days_below_existing_history_is_rejected(setup):
    _, client = setup
    habit = create(client, start_date=FIRST).json()
    put(client, habit["id"], "2026-10-03")
    response = client.patch(f"/api/habits/{habit['id']}", json={
        "revision": habit["revision"], "target_days": 1,
    })
    assert response.status_code == 422
    assert "坚持天数" in response.json()["detail"]


def test_get_habit_refetch_and_unknown_ids(setup):
    _, client = setup
    habit = create(client, cue="早餐后").json()
    path = f"/api/habits/{habit['id']}"
    assert client.get(path).json() == habit
    changed = client.patch(path, json={"revision": 1, "cue": ""}).json()
    assert changed["revision"] == 2
    assert client.patch(path, json={"revision": 1, "title": "旧草稿"}).status_code == 409
    assert client.get("/api/habits/0").status_code == 422
    assert client.get("/api/habits/9223372036854775808").status_code == 422
    assert client.get("/api/habits/9223372036854775807").status_code == 404
    assert client.get("/api/habits/9999").status_code == 404


def test_range_is_inclusive_bounded_and_ordered(setup):
    _, client = setup
    habit = create(client, start_date="2026-07-01").json()
    report = summary(client, habit["id"], "2026-07-03").json()
    assert report["scheduled_days"] == 93 and len(report["entries"]) == 93
    assert report["entries"][0]["date"] == "2026-07-03"
    assert report["entries"][-1]["date"] == "2026-10-03"
    assert summary(client, habit["id"], "2026-07-02").status_code == 422
    assert summary(client, habit["id"], "2026-10-03", "2026-10-02").status_code == 422
    for endpoint in ("entries", "summary"):
        assert client.get(f"/api/habits/{habit['id']}/{endpoint}").status_code == 422
        assert client.get(f"/api/habits/999/{endpoint}",
                          params={"from_date": FIRST, "to_date": FIRST}).status_code == 404


def test_record_rejects_invalid_states_revision_notes_and_extra_fields(setup):
    _, client = setup
    habit = create(client).json()
    for payload in ({"status": "failed"}, {"status": None}, {"note": None},
                    {"note": "中" * 2001}, {"revision": True}, {"revision": "0"},
                    {"revision": -1}, {"unexpected": 1}):
        assert put(client, habit["id"], **payload).status_code == 422
    assert client.put(f"/api/habits/{habit['id']}/entries/{FIRST}",
                      json={"status": "done"}).status_code == 422


def test_concurrent_writes_have_one_winner_and_one_conflict(setup):
    _, client = setup
    habit = create(client).json()
    gate = Barrier(2)

    def write(status):
        gate.wait()
        return put(client, habit["id"], status=status).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ["done", "skipped"]))
    assert sorted(results) == [200, 409]


def test_overview_includes_today_stats_week_and_logs(setup):
    _, client = setup
    habit = create(client).json()
    day = TODAY.isoformat()
    put(client, habit["id"], day, note="私密备注")
    overview = client.get("/api/habits/overview", params={"month": day[:7]}).json()
    assert overview["today"] == TODAY.isoformat()
    assert overview["habits"][0]["entry_today"]["note"] == "私密备注"
    assert overview["logs"][0]["note"] == "私密备注"
    assert overview["habits"][0]["stats"]["total_done"] == 1
    assert overview["week"]["days"][-1]["date"] == TODAY.isoformat()
    assert overview["habits_revision"].count(":") == 1


def test_markdown_note_and_log_roundtrip(vault_setup):
    database, client, vault = vault_setup
    habit = create(client).json()
    note_path = vault / habit["path"]
    assert note_path.is_file()
    text = note_path.read_text(encoding="utf-8")
    assert "type: habit" in text and f"uid: {habit['uid']}" in text
    index = vault / HABIT_DIR / "习惯定义.md"
    assert index.is_file() and "每天" in index.read_text(encoding="utf-8")

    saved = put(client, habit["id"], note="读完一章")
    assert saved.status_code == 200, saved.text
    log_path = vault / HABIT_DIR / "打卡" / "2026-09.md"
    log_text = log_path.read_text(encoding="utf-8")
    assert "2026-09-28" in log_text and "读完一章" in log_text

    # Obsidian edit: title and frequency are read back; the revision bumps.
    text = note_path.read_text(encoding="utf-8")
    text = text.replace("title: 读书", "title: 读书改")
    text = text.replace("kind: daily", "kind: weekly\n  weekdays: [0, 2]")
    note_path.write_text(text, encoding="utf-8")
    refreshed = HabitService(database, vault).list()
    assert refreshed[0]["title"] == "读书改"
    assert refreshed[0]["frequency"]["kind"] == "weekly"
    assert refreshed[0]["revision"] > habit["revision"]

    # Obsidian edit: the log row is read back and the entry revision bumps.
    log_text = log_path.read_text(encoding="utf-8").replace("| done |", "| skipped |")
    log_path.write_text(log_text, encoding="utf-8")
    service = HabitService(database, vault)
    entry = service.entries(habit["id"], FIRST, FIRST)[0]
    assert entry["status"] == "skipped" and entry["revision"] == 2

    # A stale editor cannot overwrite the externally edited note.
    stale = client.patch(f"/api/habits/{habit['id']}", json={
        "revision": habit["revision"], "title": "旧草稿"})
    assert stale.status_code == 409


def test_vault_delete_moves_to_trash_and_keeps_other_logs(vault_setup):
    _, client, vault = vault_setup
    first = create(client, title="读书").json()
    second = create(client, title="跑步").json()
    put(client, first["id"], FIRST, note="第一")
    put(client, second["id"], FIRST, note="第二")
    deleted = client.post(f"/api/habits/{first['id']}/delete",
                          json={"revision": first["revision"]})
    assert deleted.status_code == 200, deleted.text
    trash = vault / deleted.json()["trash_path"]
    assert (trash / "habit.md").is_file()
    assert (trash / "manifest.json").is_file()
    log_text = (vault / HABIT_DIR / "打卡" / "2026-09.md").read_text(encoding="utf-8")
    assert "第一" not in log_text and "第二" in log_text
    titles = [row["title"] for row in client.get("/api/habits").json()]
    assert titles == ["跑步"]


def test_reorder_writes_sort_order_and_returns_revision(vault_setup):
    _, client, vault = vault_setup
    first = create(client, title="甲").json()
    second = create(client, title="乙").json()
    result = client.post("/api/habits/reorder", json={
        "moved_id": second["id"], "before_id": first["id"], "revision": "",
    })
    assert result.status_code == 200, result.text
    assert result.json()["sort_order"] < first["sort_order"]
    order = [row["title"] for row in client.get("/api/habits").json()]
    assert order == ["乙", "甲"]
    note = (vault / second["path"]).read_text(encoding="utf-8")
    assert "sort_order:" in note
