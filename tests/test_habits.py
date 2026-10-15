"""Habit tests only use temporary databases and a frozen Shanghai date."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import habits
from backend.app.db import Database
from backend.app.habits import HabitCreate, HabitService, create_habits_router


TODAY = date(2026, 10, 3)
FIRST = "2026-09-28"  # Monday; the frozen today is Saturday.


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(habits, "shanghai_today", lambda: TODAY)
    database = Database(tmp_path / "habits.sqlite3")
    app = FastAPI()
    app.include_router(create_habits_router(database))
    return database, TestClient(app)


def create(client, **changes):
    return client.post("/api/habits", json={"title": "读书", "minimum_action": "读一页", "start_date": FIRST, **changes})


def put(client, habit_id, day=FIRST, **changes):
    return client.put(f"/api/habits/{habit_id}/entries/{day}", json={"revision": 0, "status": "done", **changes})


def summary(client, habit_id, first=FIRST, last="2026-10-03"):
    return client.get(f"/api/habits/{habit_id}/summary", params={"from_date": first, "to_date": last})


def events(database):
    with database.connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM audit_events WHERE event_type LIKE 'habit_%' ORDER BY id")]


def test_missing_days_remain_unrecorded_and_rest_days_are_not_planned(setup):
    database, client = setup
    habit = create(client, weekdays=[0, 2, 4]).json()
    report = summary(client, habit["id"]).json()
    assert {key: report[key] for key in ("scheduled_days", "done", "skipped", "unrecorded", "completion_rate")} == {
        "scheduled_days": 3, "done": 0, "skipped": 0, "unrecorded": 3, "completion_rate": 0,
    }
    assert "未记录不代表失败" in report["warning"]
    assert [entry["date"] for entry in report["entries"] if entry["scheduled"]] == [FIRST, "2026-09-30", "2026-10-02"]
    assert all(entry["status"] == "unrecorded" and entry["revision"] == 0 for entry in report["entries"])
    assert len(events(database)) == 1  # Listing and summary do not create records or audit noise.
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM habit_entries").fetchone()[0] == 0


def test_summary_distinguishes_done_skipped_missing_and_zero_denominator(setup):
    _, client = setup
    habit = create(client, weekdays=[0, 2, 4]).json()
    put(client, habit["id"])
    put(client, habit["id"], "2026-09-30", status="skipped", note="明确休息")
    report = summary(client, habit["id"]).json()
    assert (report["done"], report["skipped"], report["unrecorded"], report["completion_rate"]) == (1, 1, 1, 0.5)
    rest = summary(client, habit["id"], "2026-09-29", "2026-09-29").json()
    assert rest["scheduled_days"] == 0 and rest["completion_rate"] is None
    skipped = summary(client, habit["id"], "2026-09-30", "2026-09-30").json()
    assert skipped["skipped"] == 1 and skipped["completion_rate"] is None


def test_week_summary_counts_remaining_planned_days_to_sunday(setup):
    _, client = setup
    # The frozen today is Saturday 2026-10-03; the week is 09-28 (Mon) to 10-04 (Sun).
    habit = create(client, weekdays=[0, 4, 6]).json()  # 周一、周五、周日
    put(client, habit["id"])  # 周一已完成
    put(client, habit["id"], "2026-10-02", status="skipped", note="明确休息")
    week = summary(client, habit["id"]).json()["week"]
    assert (week["start"], week["end"]) == ("2026-09-28", "2026-10-04")
    assert (week["scheduled"], week["done"], week["skipped"], week["unrecorded"]) == (3, 1, 1, 1)
    assert week["remaining"] == 1  # 只剩周日的计划日；已完成与已跳过都不算“还剩”。
    assert "未记录不等于必须完成" in week["note"]
    # 选择一个尚未到周六的日期时，周六也算“本周还剩”的计划日。
    early = summary(client, habit["id"], FIRST, "2026-10-02").json()["week"]
    assert early["remaining"] == 1
    # 当天的计划日做完后，本周不再有未记录的计划日。
    saturday = create(client, weekdays=[5]).json()
    pending = summary(client, saturday["id"], FIRST, "2026-10-02").json()["week"]
    assert pending["remaining"] == 1
    put(client, saturday["id"], "2026-10-03")
    assert summary(client, saturday["id"]).json()["week"]["remaining"] == 0
    # 非计划日不进入本周计划。
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
    assert "weekdays_json" not in habit
    HabitService(database)
    assert client.get("/api/habits").json() == [habit]


@pytest.mark.parametrize("change", [
    {"title": "  "}, {"title": None}, {"minimum_action": ""}, {"minimum_action": None},
    {"title": "中" * 121}, {"cue": "中" * 501}, {"weekdays": []}, {"weekdays": [1, 1]},
    {"weekdays": [7]}, {"weekdays": [-1]}, {"weekdays": [True]}, {"weekdays": ["1"]},
    {"weekdays": [1.0]}, {"weekdays": None}, {"start_date": None}, {"status": "archived"},
])
def test_invalid_create_does_not_write(setup, change):
    database, client = setup
    assert create(client, **change).status_code == 422
    assert client.get("/api/habits").json() == [] and events(database) == []


@pytest.mark.parametrize("invalid", ["2026-9-28", "20260928", "2026-02-30", "2026-10-04", "2026-09-28T00:00:00Z"])
def test_strict_dates_and_future_rejected_across_create_entry_and_range(setup, invalid):
    _, client = setup
    habit = create(client).json()
    assert create(client, start_date=invalid).status_code == 422
    assert put(client, habit["id"], invalid).status_code == 422
    assert summary(client, habit["id"], invalid).status_code == 422
    assert summary(client, habit["id"], FIRST, invalid).status_code == 422


def test_shanghai_day_and_creation_default_cross_utc_midnight(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 2, 16, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(habits, "datetime", FrozenDateTime)
    assert habits.shanghai_today() == TODAY
    assert HabitCreate(title="记录", minimum_action="一行").start_date == "2026-10-03"
    assert habits.validate_day("2026-10-03") == TODAY


def test_before_start_and_rest_day_cannot_receive_records(setup):
    database, client = setup
    habit = create(client, weekdays=[0, 4]).json()
    assert put(client, habit["id"], "2026-09-25").status_code == 422
    assert put(client, habit["id"], "2026-09-29").status_code == 422
    report = summary(client, habit["id"], "2026-09-25").json()
    assert report["scheduled_days"] == 2
    assert len(events(database)) == 1


def test_conflicts_noop_repeat_and_explicit_undo_preserve_revision_and_audit(setup):
    database, client = setup
    habit = create(client).json()
    no_record = put(client, habit["id"], status="unrecorded").json()
    assert no_record["revision"] == 0 and len(events(database)) == 1
    first = put(client, habit["id"], note="  看了一页  ").json()
    assert first["note"] == "看了一页" and first["revision"] == 1
    assert put(client, habit["id"], note="看了一页").status_code == 409
    same = put(client, habit["id"], revision=1, note="看了一页").json()
    assert same == first and len(events(database)) == 2
    undone = put(client, habit["id"], revision=1, status="unrecorded", note="之前误点").json()
    assert undone["revision"] == 2 and undone["status"] == "unrecorded"
    assert put(client, habit["id"], revision=1).status_code == 409
    assert summary(client, habit["id"]).json()["done"] == 0
    event = json.loads(events(database)[-1]["detail_json"])
    assert (event["previous_status"], event["status"], event["previous_revision"], event["revision"]) == ("done", "unrecorded", 1, 2)
    assert "之前误点" not in str(events(database))
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM habit_entries").fetchone()[0] == 1


def test_habit_revision_schedule_immutability_and_archive_history(setup):
    database, client = setup
    habit = create(client).json()
    habit_id = habit["id"]
    put(client, habit_id)
    path = f"/api/habits/{habit_id}"
    for changes in ({"start_date": "2026-09-27"}, {"weekdays": [0]}, {"title": None}, {"title": " "}):
        assert client.patch(path, json={"revision": 1, **changes}).status_code == 422
    updated = client.patch(path, json={"revision": 1, "cue": "早餐后", "title": "阅读"}).json()
    assert updated["revision"] == 2
    assert client.patch(path, json={"revision": 1, "status": "archived"}).status_code == 409
    assert client.patch(path, json={"revision": 2, "cue": "早餐后"}).json() == updated
    archived = client.patch(path, json={"revision": 2, "status": "archived"}).json()
    assert archived["revision"] == 3
    assert client.get("/api/habits?status=active").json() == []
    assert client.get("/api/habits?status=archived").json() == [archived]
    assert put(client, habit_id, "2026-09-29").status_code == 409
    assert put(client, habit_id, revision=1, status="skipped").status_code == 200
    assert summary(client, habit_id).json()["skipped"] == 1
    restored = client.patch(path, json={"revision": 3, "status": "active"})
    assert restored.status_code == 200
    assert put(client, habit_id, "2026-09-29").status_code == 200


def test_get_habit_refetches_latest_definition_and_preserves_revision_contract(setup):
    _, client = setup
    habit = create(client, cue="早餐后").json()
    path = f"/api/habits/{habit['id']}"
    assert client.get(path).json() == habit

    changed = client.patch(path, json={"revision": 1, "cue": ""})
    assert changed.status_code == 200
    latest = changed.json()
    assert latest["cue"] == "" and latest["revision"] == 2
    assert client.get(path).json() == latest

    # The idempotent no-op still returns the current revision, while an old
    # editor cannot overwrite it with a stale optimistic write.
    assert client.patch(path, json={"revision": 2, "cue": ""}).json() == latest
    assert client.patch(path, json={"revision": 1, "title": "旧草稿"}).status_code == 409
    assert client.get("/api/habits/0").status_code == 422
    assert client.get("/api/habits/9223372036854775808").status_code == 422
    assert client.get("/api/habits/9223372036854775807").status_code == 404
    assert client.get("/api/habits/9999").status_code == 404


def test_range_is_inclusive_bounded_and_ordered_and_unknown_ids_are_404(setup):
    _, client = setup
    habit = create(client, start_date="2026-07-01").json()
    report = summary(client, habit["id"], "2026-07-03").json()
    assert report["scheduled_days"] == 93 and len(report["entries"]) == 93
    assert report["entries"][0]["date"] == "2026-07-03" and report["entries"][-1]["date"] == "2026-10-03"
    assert summary(client, habit["id"], "2026-07-02").status_code == 422
    assert summary(client, habit["id"], "2026-10-03", "2026-10-02").status_code == 422
    for endpoint in ("entries", "summary"):
        assert client.get(f"/api/habits/{habit['id']}/{endpoint}").status_code == 422
        assert client.get(f"/api/habits/999/{endpoint}", params={"from_date": FIRST, "to_date": FIRST}).status_code == 404
    assert client.patch("/api/habits/999", json={"revision": 1, "title": "其他"}).status_code == 404
    assert put(client, 999).status_code == 404
    assert summary(client, 10**30).status_code == 422  # Invalid IDs must not overflow SQLite's integer binding.


def test_record_rejects_invalid_states_revision_notes_and_extra_fields(setup):
    database, client = setup
    habit = create(client).json()
    for payload in ({"status": "failed"}, {"status": None}, {"note": None}, {"note": "中" * 2001},
                    {"revision": True}, {"revision": "0"}, {"revision": -1}, {"unexpected": 1}):
        assert put(client, habit["id"], **payload).status_code == 422
    assert client.put(f"/api/habits/{habit['id']}/entries/{FIRST}", json={"status": "done"}).status_code == 422
    assert len(events(database)) == 1


def test_concurrent_writes_have_one_winner_and_one_conflict(setup):
    database, client = setup
    habit = create(client).json()
    gate = Barrier(2)

    def write(status):
        gate.wait()
        return put(client, habit["id"], status=status).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ["done", "skipped"]))
    assert sorted(results) == [200, 409]
    assert len(events(database)) == 2


def test_audit_failure_rolls_back_business_changes(setup):
    database, client = setup
    habit = create(client).json()
    with database.connect() as connection:
        connection.executescript("""CREATE TRIGGER fail_habit_audit BEFORE INSERT ON audit_events
        WHEN NEW.event_type='habit_entry_saved' BEGIN SELECT RAISE(ABORT, 'test audit failure'); END;""")
    with pytest.raises(sqlite3.IntegrityError, match="test audit failure"):
        put(client, habit["id"])
    assert summary(client, habit["id"]).json()["done"] == 0
    assert len(events(database)) == 1


def test_schema_constraints_reject_invalid_persisted_status_and_orphan(setup):
    database, client = setup
    habit = create(client).json()
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute("UPDATE habit_definitions SET status='failed' WHERE id=?", (habit["id"],))
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute("""INSERT INTO habit_entries(habit_id,date,status,note,revision,updated_at)
                            VALUES (999,'2026-09-28','done','',1,'test')""")
