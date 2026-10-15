"""Body-record tests use only temporary databases and a frozen Shanghai date."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import body
from backend.app.body import BodyRecordService, create_body_router
from backend.app.db import Database


TODAY = date(2026, 10, 4)
DAY = "2026-10-04"
EARLIER = "2026-10-02"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(body, "shanghai_today", lambda: TODAY)
    database = Database(tmp_path / "body.sqlite3")
    app = FastAPI()
    app.include_router(create_body_router(database))
    return database, TestClient(app)


def put(client, day=DAY, **changes):
    return client.put(f"/api/body/entries/{day}", json={"revision": 0, **changes})


def events(database, event_type=None):
    with database.connect() as connection:
        if event_type:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE event_type=? ORDER BY id", (event_type,)
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM audit_events ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def row_count(database, table):
    with database.connect() as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_unrecorded_day_is_readable_and_reading_writes_nothing(setup):
    database, client = setup
    response = client.get(f"/api/body/entries/{DAY}")
    assert response.status_code == 200
    assert response.json() == {
        "date": DAY, "recorded": False, "weight_kg": None, "sleep_minutes": None,
        "exercise_minutes": None, "revision": 0, "updated_at": None,
    }
    assert row_count(database, "body_entries") == 0
    assert events(database) == []


def test_partial_upserts_merge_and_exact_repeat_is_a_silent_noop(setup):
    database, client = setup
    first = put(client, weight_kg=68.5).json()
    assert first["recorded"] is True and first["revision"] == 1
    assert first["weight_kg"] == 68.5 and first["sleep_minutes"] is None
    assert first["updated_at"]

    second = put(client, weight_kg=68.5, sleep_minutes=420, revision=1).json()
    assert second["revision"] == 2 and second["weight_kg"] == 68.5 and second["sleep_minutes"] == 420
    assert second["exercise_minutes"] is None  # Omitted fields stay unchanged, not nulled.

    repeat = put(client, weight_kg=68.5, sleep_minutes=420, revision=2).json()
    assert repeat == second
    assert len(events(database, "body_entry_saved")) == 2

    zero = put(client, exercise_minutes=0, revision=2).json()
    assert zero["revision"] == 3 and zero["exercise_minutes"] == 0
    assert zero["weight_kg"] == 68.5


def test_explicit_null_clears_one_field_and_clearing_everything_removes_the_row(setup):
    database, client = setup
    put(client, weight_kg=68.5, sleep_minutes=420, exercise_minutes=30)
    cleared = put(client, sleep_minutes=None, revision=1).json()
    assert cleared["sleep_minutes"] is None and cleared["revision"] == 2
    assert cleared["weight_kg"] == 68.5 and cleared["exercise_minutes"] == 30

    empty = put(client, weight_kg=None, sleep_minutes=None, exercise_minutes=None, revision=2).json()
    assert empty == {
        "date": DAY, "recorded": False, "weight_kg": None, "sleep_minutes": None,
        "exercise_minutes": None, "revision": 0, "updated_at": None,
    }
    assert row_count(database, "body_entries") == 0
    cleared_events = events(database, "body_entry_cleared")
    assert len(cleared_events) == 1
    assert json.loads(cleared_events[0]["detail_json"]) == {"date": DAY, "previous_revision": 2}
    # Clearing a day that already has no row is a no-op, not a new audit event.
    assert put(client, weight_kg=None).json()["recorded"] is False
    assert len(events(database, "body_entry_cleared")) == 1


@pytest.mark.parametrize("invalid", ["2026-10-05", "2026-10-4", "20261004", "2026-02-30", "2026-10-04T00:00:00Z"])
def test_strict_dates_and_future_rejected(setup, invalid):
    database, client = setup
    assert client.get(f"/api/body/entries/{invalid}").status_code == 422
    assert put(client, invalid, weight_kg=60).status_code == 422
    assert client.get("/api/body/entries", params={"from_date": invalid, "to_date": DAY}).status_code == 422
    assert row_count(database, "body_entries") == 0
    assert events(database) == []


@pytest.mark.parametrize("payload", [
    {"revision": 0},  # No value field at all.
    {"revision": 0, "note": "不允许的字段"},
    {"weight_kg": 60},  # revision is required.
    {"revision": -1, "weight_kg": 60},
    {"revision": True, "weight_kg": 60},
    {"revision": "0", "weight_kg": 60},
    {"revision": 0, "weight_kg": 19.9},
    {"revision": 0, "weight_kg": 300.1},
    {"revision": 0, "weight_kg": 68.55},
    {"revision": 0, "weight_kg": True},
    {"revision": 0, "weight_kg": "68.5"},
    {"revision": 0, "sleep_minutes": -1},
    {"revision": 0, "sleep_minutes": 1441},
    {"revision": 0, "sleep_minutes": 30.0},
    {"revision": 0, "sleep_minutes": True},
    {"revision": 0, "sleep_minutes": "30"},
    {"revision": 0, "exercise_minutes": 1441},
    {"revision": 0, "exercise_minutes": -1},
])
def test_invalid_payloads_do_not_write(setup, payload):
    database, client = setup
    assert client.put(f"/api/body/entries/{DAY}", json=payload).status_code == 422
    assert row_count(database, "body_entries") == 0
    assert events(database) == []


def test_range_listing_is_ordered_inclusive_and_bounded(setup):
    database, client = setup
    put(client, weight_kg=60.0)
    put(client, EARLIER, sleep_minutes=400)
    listing = client.get("/api/body/entries", params={"from_date": "2026-09-30", "to_date": DAY}).json()
    assert [entry["date"] for entry in listing["entries"]] == [EARLIER, DAY]
    assert listing["from_date"] == "2026-09-30" and listing["to_date"] == DAY
    assert "只列出有记录" in listing["warning"]

    assert client.get("/api/body/entries", params={"from_date": DAY, "to_date": EARLIER}).status_code == 422
    assert client.get("/api/body/entries", params={"from_date": "2026-06-01", "to_date": DAY}).status_code == 422
    assert client.get("/api/body/entries").status_code == 422
    assert client.get("/api/body/entries", params={"from_date": "2026-06-01", "to_date": "2026-10-04"}).status_code == 422
    assert row_count(database, "body_entries") == 2


def test_stale_revision_conflicts_and_retry_contract(setup):
    database, client = setup
    put(client, weight_kg=68.5)
    conflict = put(client, weight_kg=70, revision=0)
    assert conflict.status_code == 409
    assert "保留草稿" in conflict.json()["detail"]
    current = client.get(f"/api/body/entries/{DAY}").json()
    assert current["weight_kg"] == 68.5 and current["revision"] == 1
    ok = put(client, weight_kg=70, revision=1)
    assert ok.status_code == 200 and ok.json()["weight_kg"] == 70 and ok.json()["revision"] == 2


def test_concurrent_writers_have_one_winner_and_one_conflict(setup):
    database, client = setup
    put(client, weight_kg=68.5)
    gate = Barrier(2)

    def write(weight):
        gate.wait()
        return put(client, weight_kg=weight, revision=1).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, [66.0, 67.0]))
    assert sorted(results) == [200, 409]
    assert row_count(database, "body_entries") == 1
    assert len(events(database, "body_entry_saved")) == 2


def test_audit_records_fields_but_never_measured_values(setup):
    database, client = setup
    put(client, weight_kg=68.5, sleep_minutes=421, exercise_minutes=31)
    detail = events(database, "body_entry_saved")[0]["detail_json"]
    assert json.loads(detail)["changed_fields"] == ["exercise_minutes", "sleep_minutes", "weight_kg"]
    for secret in ("68.5", "421", "31"):
        assert secret not in detail


def test_body_writes_create_no_memories_tasks_conversations_or_documents(setup):
    database, client = setup
    put(client, weight_kg=68.5, sleep_minutes=420)
    with database.connect() as connection:
        counts = connection.execute(
            """SELECT (SELECT COUNT(*) FROM memories) AS memories,
                      (SELECT COUNT(*) FROM tasks) AS tasks,
                      (SELECT COUNT(*) FROM conversations) AS conversations,
                      (SELECT COUNT(*) FROM messages) AS messages,
                      (SELECT COUNT(*) FROM documents) AS documents"""
        ).fetchone()
    assert tuple(counts) == (0, 0, 0, 0, 0)


def test_schema_rejects_invalid_rows_and_service_reinitialization_keeps_data(setup):
    database, client = setup
    put(client, weight_kg=68.5)
    BodyRecordService(database)  # Idempotent schema creation must not lose rows.
    assert client.get(f"/api/body/entries/{DAY}").json()["weight_kg"] == 68.5
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute(
            "INSERT INTO body_entries(date,weight_kg,sleep_minutes,exercise_minutes,revision,updated_at)"
            " VALUES (?,?,?,?,?,?)", ("2026-10-01", 10.0, None, None, 1, "test"),
        )
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute(
            "INSERT INTO body_entries(date,weight_kg,sleep_minutes,exercise_minutes,revision,updated_at)"
            " VALUES (?,?,?,?,?,?)", ("bad-date", 60.0, None, None, 1, "test"),
        )


def test_audit_failure_rolls_back_business_changes(setup):
    database, client = setup
    with database.connect() as connection:
        connection.executescript("""CREATE TRIGGER fail_body_audit BEFORE INSERT ON audit_events
        WHEN NEW.event_type='body_entry_saved' BEGIN SELECT RAISE(ABORT, 'test audit failure'); END;""")
    with pytest.raises(sqlite3.IntegrityError, match="test audit failure"):
        put(client, weight_kg=68.5)
    assert row_count(database, "body_entries") == 0


def test_shanghai_day_crosses_utc_midnight(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 3, 16, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(body, "datetime", FrozenDateTime)
    assert body.shanghai_today() == TODAY
    assert body.validate_day(DAY) == TODAY


def test_body_export_json_and_csv_are_read_only_and_audit_counts_only(setup):
    database, client = setup
    assert put(client, weight_kg=68.5, sleep_minutes=450, exercise_minutes=30).status_code == 200
    assert put(client, day=EARLIER, weight_kg=70.2, sleep_minutes=None, exercise_minutes=45).status_code == 200
    with database.connect() as connection:
        before = [dict(row) for row in connection.execute("SELECT * FROM body_entries ORDER BY date")]

    response = client.get(f"/api/body/export.json?from_date={EARLIER}&to_date={DAY}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    payload = response.json()
    assert payload["schema"] == 1 and payload["total"] == 2
    by_date = {row["date"]: row for row in payload["entries"]}
    assert [row["date"] for row in payload["entries"]] == [EARLIER, DAY]
    assert by_date[DAY]["weight_kg"] == 68.5 and by_date[DAY]["sleep_minutes"] == 450
    assert by_date[EARLIER]["sleep_minutes"] is None and by_date[EARLIER]["exercise_minutes"] == 45

    csv_response = client.get(f"/api/body/export.csv?from_date={EARLIER}&to_date={DAY}")
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    lines = csv_response.text.strip().splitlines()
    assert lines[0] == "date,weight_kg,sleep_minutes,exercise_minutes"
    assert lines[1] == f"{EARLIER},70.2,,45"
    assert lines[2] == f"{DAY},68.5,450,30"

    default = client.get("/api/body/export.json").json()
    assert default["to_date"] == DAY and default["from_date"] == "2026-07-07"
    assert client.get(f"/api/body/export.csv?from_date={DAY}&to_date={EARLIER}").status_code == 422

    with database.connect() as connection:
        after = [dict(row) for row in connection.execute("SELECT * FROM body_entries ORDER BY date")]
        audit = [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type='body_exported' ORDER BY id"
        )]
    assert before == after, "export must not change body rows"
    assert [json.loads(row["detail_json"]) for row in audit] == [
        {"format": "json", "from_date": EARLIER, "to_date": DAY, "total": 2},
        {"format": "csv", "from_date": EARLIER, "to_date": DAY, "total": 2},
        {"format": "json", "from_date": "2026-07-07", "to_date": DAY, "total": 2},
    ]


def test_body_summary_counts_days_and_minutes_without_values(setup):
    database, client = setup
    assert put(client, DAY, weight_kg=68.5, sleep_minutes=420, exercise_minutes=30).status_code == 200
    assert put(client, EARLIER, sleep_minutes=400).status_code == 200
    response = client.get("/api/body/summary.json", params={"period": "week"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema"] == 1 and payload["period"] == "week"
    assert payload["from_date"] == "2026-09-28" and payload["to_date"] == "2026-10-04"  # Monday..Sunday
    assert payload["partial"] is False  # the frozen Sunday is the last day of the week
    assert payload["days_recorded"] == 2
    assert payload["days_weight"] == 1 and payload["days_sleep"] == 2 and payload["days_exercise"] == 1
    assert payload["totals"] == {"sleep_minutes": 820, "exercise_minutes": 30}
    assert "不做健康评价" in payload["warning"]
    assert "68.5" not in response.text and "weight_kg" not in response.text
    audit = events(database, "body_summary_exported")
    assert len(audit) == 1
    detail = json.loads(audit[0]["detail_json"])
    assert detail == {"format": "json", "period": "week", "from_date": "2026-09-28",
                      "to_date": "2026-10-04", "days_recorded": 2}
    assert "68.5" not in audit[0]["detail_json"]

    # Default window is the current month and marks itself partial.
    default = client.get("/api/body/summary.json").json()
    assert default["period"] == "month" and default["from_date"] == "2026-10-01"
    assert default["to_date"] == "2026-10-04" and default["partial"] is True


def test_body_summary_month_markdown_uses_the_anchor_month(setup):
    database, client = setup
    assert put(client, "2026-09-10", sleep_minutes=480, weight_kg=70.2).status_code == 200
    assert put(client, EARLIER, exercise_minutes=45).status_code == 200
    response = client.get("/api/body/summary.md", params={"period": "month", "anchor": "2026-09-15"})
    assert response.status_code == 200, response.text
    assert "text/markdown" in response.headers["content-type"]
    text = response.text
    assert "身体记录汇总（month）" in text
    assert "2026-09-01 至 2026-09-30" in text
    assert "有记录天数：1" in text
    assert "体重记录天数：1（不含数值）" in text
    assert "睡眠记录天数：1；合计 480 分钟" in text
    assert "运动记录天数：0；合计 0 分钟" in text
    assert "不做健康评价" in text
    assert "70.2" not in text
    audit = events(database, "body_summary_exported")
    assert len(audit) == 1 and json.loads(audit[0]["detail_json"])["period"] == "month"


@pytest.mark.parametrize("params", [
    {"period": "year"}, {"period": "week", "anchor": "not-a-day"},
    {"period": "month", "anchor": "2026-10-05"}, {"period": "WEEK"},
])
def test_body_summary_rejects_invalid_periods_and_anchors(setup, params):
    database, client = setup
    response = client.get("/api/body/summary.json", params=params)
    assert response.status_code == 422, response.text
    assert events(database, "body_summary_exported") == []
