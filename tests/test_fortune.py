"""Daily fortune check-in: idempotent, today-only, and carried by the workbench."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import fortune
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.fortune import create_fortune_router

SHANGHAI = ZoneInfo("Asia/Shanghai")
TODAY = datetime.now(SHANGHAI).date().isoformat()


def make_router_client(tmp_path):
    database = Database(tmp_path / "fortune.sqlite3")
    app = FastAPI()
    app.include_router(create_fortune_router(database))
    return TestClient(app), database


def test_checkin_is_idempotent_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(fortune, "shanghai_today", lambda: date(2026, 10, 27))
    client, database = make_router_client(tmp_path)

    assert client.get("/api/fortune/checkins/2026-10-27").json() == {
        "day": "2026-10-27", "checked_in": False, "checked_at": None,
    }

    first = client.post("/api/fortune/checkins/2026-10-27").json()
    assert first["checked_in"] is True and first["checked_at"]

    second = client.post("/api/fortune/checkins/2026-10-27").json()
    assert second["checked_at"] == first["checked_at"]

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM fortune_checkins").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type='fortune_checkin'"
        ).fetchone()[0] == 1

    # A new service/client on the same database file still sees the check-in.
    reopened, _ = make_router_client(tmp_path)
    assert reopened.get("/api/fortune/checkins/2026-10-27").json()["checked_in"] is True


def test_checkin_requires_a_valid_today(tmp_path, monkeypatch):
    monkeypatch.setattr(fortune, "shanghai_today", lambda: date(2026, 10, 27))
    client, _ = make_router_client(tmp_path)

    assert client.post("/api/fortune/checkins/2026-10-26").status_code == 422
    assert client.post("/api/fortune/checkins/2026-10-28").status_code == 422
    assert client.post("/api/fortune/checkins/not-a-day").status_code == 422
    # Status is readable for another day; it just reports unchecked.
    assert client.get("/api/fortune/checkins/2026-10-28").json()["checked_in"] is False


def test_workbench_payload_carries_fortune_status(tmp_path):
    app = create_app(
        Database(tmp_path / "workbench.sqlite3"), tmp_path / "vault",
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")

    payload = client.get("/api/workbench/today").json()
    assert payload["fortune"] == {"day": TODAY, "checked_in": False, "checked_at": None}

    checked = client.post(f"/api/fortune/checkins/{TODAY}").json()
    assert checked["checked_in"] is True
    assert client.get("/api/workbench/today").json()["fortune"]["checked_in"] is True
