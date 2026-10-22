from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway
from backend.app.scheduler import SchedulerService, create_scheduler_router


SHANGHAI = ZoneInfo("Asia/Shanghai")


def make_client(tmp_path):
    database = Database(tmp_path / "app.sqlite3")
    gateway = ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1)
    app = create_app(database, tmp_path / "vault", gateway, security_environ={})
    client = TestClient(app)
    client.get("/")
    return client, database


def at(day: str, hour: int, minute: int = 0) -> datetime:
    year, month, date = (int(part) for part in day.split("-"))
    return datetime(year, month, date, hour, minute, tzinfo=SHANGHAI)


def schedule_payload(**overrides):
    payload = {
        "morning_enabled": True, "morning_time": "08:00",
        "evening_enabled": True, "evening_time": "21:30",
        "weekly_enabled": True, "weekly_day": 6, "weekly_time": "20:00",
    }
    payload.update(overrides)
    return payload


def test_run_due_respects_schedule_idempotency_catchup_and_new_day(tmp_path):
    client, database = make_client(tmp_path)
    # This test isolates the daily drafts; the weekly digest has its own test.
    assert client.put("/api/schedule", json=schedule_payload(weekly_enabled=False)).status_code == 200
    service = SchedulerService(database)

    early = service.run_due(now=at("2026-10-04", 7, 30))
    assert early["created"] == [] and early["existing"] == []
    # 0.99 retired the old suggestion cards; the scheduler only creates drafts now.
    assert "suggestions" not in early

    on_time = service.run_due(now=at("2026-10-04", 8, 2))
    assert len(on_time["created"]) == 1
    morning = on_time["created"][0]
    assert morning["kind"] == "morning" and morning["catch_up"] is False and morning["manual"] is False
    assert morning["title"] == "今日规划草稿"

    evening = service.run_due(now=at("2026-10-04", 22, 0))
    assert len(evening["created"]) == 1 and evening["created"][0]["catch_up"] is True
    assert evening["created"][0]["kind"] == "evening"

    repeat = service.run_due(now=at("2026-10-04", 23, 0))
    assert repeat["created"] == [] and len(repeat["existing"]) == 2

    next_day = service.run_due(now=at("2026-10-05", 9, 0))
    assert len(next_day["created"]) == 1
    assert next_day["created"][0]["source_date"] == "2026-10-05"


def test_weekly_digest_runs_on_the_configured_day_without_backfilling_whole_days(tmp_path):
    client, database = make_client(tmp_path)
    service = SchedulerService(database)
    # 2026-10-04 is a Sunday; the default weekly schedule is Sunday 20:00.
    saturday = service.run_due(now=at("2026-10-03", 21, 0))
    assert [item for item in saturday["created"] if item["kind"] == "weekly"] == []

    sunday_early = service.run_due(now=at("2026-10-04", 19, 0))
    assert [item for item in sunday_early["created"] if item["kind"] == "weekly"] == []

    on_time = service.run_due(now=at("2026-10-04", 20, 2))
    created = [item for item in on_time["created"] if item["kind"] == "weekly"]
    assert len(created) == 1
    weekly = created[0]
    assert weekly["source_date"] == "2026-10-04" and weekly["catch_up"] is False and weekly["manual"] is False
    assert weekly["title"] == "本周回顾草稿"

    repeat = service.run_due(now=at("2026-10-04", 22, 0))
    assert [item for item in repeat["created"] if item["kind"] == "weekly"] == []
    assert "weekly" in [item["kind"] for item in repeat["existing"]]

    monday = service.run_due(now=at("2026-10-05", 20, 0))
    assert [item for item in monday["created"] if item["kind"] == "weekly"] == []

    next_sunday = service.run_due(now=at("2026-10-11", 20, 30))
    created = [item for item in next_sunday["created"] if item["kind"] == "weekly"]
    assert len(created) == 1 and created[0]["source_date"] == "2026-10-11" and created[0]["catch_up"] is True

    assert client.put("/api/schedule", json=schedule_payload(weekly_enabled=False)).status_code == 200
    disabled = service.run_due(now=at("2026-10-18", 20, 30))
    assert [item for item in disabled["created"] if item["kind"] == "weekly"] == []


def test_weekly_run_now_is_manual_and_idempotent(tmp_path):
    client, _ = make_client(tmp_path)
    manual = client.post("/api/schedule/run-now", json={"kind": "weekly"})
    assert manual.status_code == 200 and manual.json()["created"] is True
    item = manual.json()["item"]
    assert item["kind"] == "weekly" and item["manual"] is True and item["catch_up"] is False
    assert item["source_date"] == datetime.now(SHANGHAI).date().isoformat()

    again = client.post("/api/schedule/run-now", json={"kind": "weekly"})
    assert again.json()["created"] is False and again.json()["item"]["id"] == item["id"]


def test_disabled_reports_do_not_generate_and_force_marks_manual(tmp_path):
    client, database = make_client(tmp_path)
    service = SchedulerService(database)
    updated = client.put("/api/schedule", json=schedule_payload(
        morning_enabled=False, evening_enabled=False, weekly_enabled=False,
    ))
    assert updated.status_code == 200
    disabled_run = service.run_due(now=at("2026-10-04", 23, 0))
    assert disabled_run["created"] == [] and disabled_run["existing"] == []
    assert "suggestions" not in disabled_run

    first = client.post("/api/schedule/run-now", json={"kind": "evening"})
    assert first.status_code == 200
    assert first.json()["created"] is True
    item = first.json()["item"]
    assert item["manual"] is True and item["catch_up"] is False and item["kind"] == "evening"

    again = client.post("/api/schedule/run-now", json={"kind": "evening"})
    assert again.json()["created"] is False
    assert again.json()["item"]["id"] == item["id"]
    assert client.post("/api/schedule/run-now", json={"kind": "midnight"}).status_code == 422


def test_settings_validation_rejects_bad_times_and_fields(tmp_path):
    client, _ = make_client(tmp_path)
    base = schedule_payload()
    assert client.put("/api/schedule", json=base).status_code == 200
    for bad in ("8:00", "24:00", "08:60", "08:0", "0800"):
        assert client.put("/api/schedule", json={**base, "morning_time": bad}).status_code == 422, bad
    for bad in ("8:00", "24:00", "20:60"):
        assert client.put("/api/schedule", json={**base, "weekly_time": bad}).status_code == 422, bad
    for bad in (7, -1, True, "6", None):
        assert client.put("/api/schedule", json={**base, "weekly_day": bad}).status_code == 422, bad
    # Surrounding whitespace is normalized by the module convention before validation.
    trimmed = client.put("/api/schedule", json={**base, "morning_time": " 08:00 "})
    assert trimmed.status_code == 200 and trimmed.json()["morning_time"] == "08:00"
    assert client.put("/api/schedule", json={**base, "extra": 1}).status_code == 422
    assert client.put("/api/schedule", json=schedule_payload(morning_enabled=1)).status_code == 422
    roundtrip = client.put("/api/schedule", json=schedule_payload(weekly_enabled=False, weekly_day=2, weekly_time="19:15"))
    assert roundtrip.status_code == 200
    settings = roundtrip.json()
    assert settings["weekly_enabled"] is False and settings["weekly_day"] == 2 and settings["weekly_time"] == "19:15"


def test_inbox_read_and_dismiss_are_idempotent_and_bounded(tmp_path):
    client, _ = make_client(tmp_path)
    item = client.post("/api/schedule/run-now", json={"kind": "morning"}).json()["item"]
    listing = client.get("/api/inbox").json()
    assert [row["id"] for row in listing] == [item["id"]] and listing[0]["unread"] is True

    read = client.post(f"/api/inbox/{item['id']}/read")
    assert read.status_code == 200 and read.json()["read_at"] and read.json()["unread"] is False
    assert client.post(f"/api/inbox/{item['id']}/read").status_code == 200

    dismissed = client.post(f"/api/inbox/{item['id']}/dismiss")
    assert dismissed.status_code == 200 and dismissed.json()["dismissed_at"]
    assert client.get("/api/inbox").json() == []
    assert client.post(f"/api/inbox/{item['id']}/dismiss").status_code == 200

    assert client.post("/api/inbox/9999/read").status_code == 404
    assert client.post("/api/inbox/0/read").status_code == 422
    assert client.post("/api/inbox/9223372036854775808/dismiss").status_code == 422


def test_report_bodies_summarize_markdown_facts_without_writing_them(tmp_path):
    from backend.app import pillar_notes, structured_notes, task_notes

    client, database = make_client(tmp_path)
    vault = tmp_path / "vault"
    today = datetime.now(SHANGHAI).date()
    day = today.isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()
    goal = pillar_notes.create_note(vault, pillar="life", chapter="goal_year",
                                    title="虚构目标", note_type="goal")
    open_task = task_notes.create_task_note(vault, "", "虚构待办", {
        "date": day, "goal": goal["path"], "created": day})
    task_notes.create_task_note(vault, "", "虚构任务", {
        "date": day, "goal": goal["path"], "created": day, "done": True, "done_on": day})
    journal_dir = vault / "00_人生路" / "01_日记"
    journal_dir.mkdir(parents=True, exist_ok=True)
    (journal_dir / f"{yesterday}.md").write_text(
        f"---\ntype: journal\ndate: {yesterday}\n---\n\n昨日虚构记录\n", encoding="utf-8")
    (journal_dir / f"{day}.md").write_text(
        f"---\ntype: journal\ndate: {day}\n---\n\n今日虚构记录\n\n下一步：虚构下一步\n",
        encoding="utf-8")
    structured_notes.upsert_row(vault, "health", day[:7], {
        "date": day, "weight_kg": "68.5", "sleep_minutes": "450", "exercise_minutes": "30"})
    habit = client.post("/api/habits", json={"title": "虚构习惯", "minimum_action": "一页", "start_date": day}).json()
    client.put(f"/api/habits/{habit['id']}/entries/{day}", json={"revision": 0, "status": "done"})

    with database.connect() as connection:
        before = {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                  for name in ("tasks", "memories", "conversations", "messages", "documents")}
    morning = client.post("/api/schedule/run-now", json={"kind": "morning"}).json()["item"]["body"]
    evening = client.post("/api/schedule/run-now", json={"kind": "evening"}).json()["item"]["body"]
    with database.connect() as connection:
        after = {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                 for name in ("tasks", "memories", "conversations", "messages", "documents")}
        audit = connection.execute(
            "SELECT detail_json FROM audit_events WHERE event_type='scheduler_report_created'"
        ).fetchall()

    assert before == after
    assert [row["title"] for row in morning["goals"]] == ["虚构目标"]
    assert [row["title"] for row in morning["open_tasks"]] == ["虚构待办"]
    assert morning["journal_yesterday_recorded"] is True
    assert "study_week" not in morning
    assert "不会自动创建任务" in morning["disclaimer"]

    assert evening["completed_tasks"] == 1 and evening["open_overdue_tasks"] == 0
    assert "study_today" not in evening
    assert evening["habits"] == {"done": 1, "skipped": 0, "unrecorded": 0}
    assert evening["journal"] == {"recorded": True, "next_action": "虚构下一步"}
    assert evening["body_records"] == {"weight_kg": 68.5, "sleep_minutes": 450, "exercise_minutes": 30}
    assert "body_records" not in morning  # The morning draft never reads forward-looking body data.
    assert len(evening["reflection_prompts"]) == 3

    weekly = client.post("/api/schedule/run-now", json={"kind": "weekly"}).json()["item"]["body"]
    assert weekly["completed_tasks"] == 1
    assert weekly["completed_examples"] == ["虚构任务"]
    assert "study_week" not in weekly
    assert weekly["habits"] == {"done": 1, "skipped": 0, "unrecorded": 0}
    assert weekly["journal_days"] == 2
    assert weekly["body_record_days"] == 1
    assert len(weekly["reflection_prompts"]) == 3
    assert all("虚构" not in row["detail_json"] for row in audit)
    assert open_task["date"] == day


def test_schedule_panel_reports_today_runs(tmp_path):
    client, _ = make_client(tmp_path)
    client.post("/api/schedule/run-now", json={"kind": "morning"})
    payload = client.get("/api/schedule").json()
    assert payload["settings"]["morning_enabled"] is True
    assert payload["settings"]["morning_time"] == "08:00"
    assert len(payload["today_runs"]) == 1 and payload["today_runs"][0]["kind"] == "morning"


def test_scheduler_migration_from_051_preserves_inbox_rows(tmp_path):
    database = Database(tmp_path / "app.sqlite3")
    database.initialize()
    with database.connect() as connection:
        connection.executescript(
            """
            CREATE TABLE scheduler_settings (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                morning_enabled INTEGER NOT NULL DEFAULT 1 CHECK(morning_enabled IN (0, 1)),
                morning_time TEXT NOT NULL DEFAULT '08:00',
                evening_enabled INTEGER NOT NULL DEFAULT 1 CHECK(evening_enabled IN (0, 1)),
                evening_time TEXT NOT NULL DEFAULT '21:30',
                updated_at TEXT NOT NULL
            );
            INSERT INTO scheduler_settings(id, morning_enabled, morning_time, evening_enabled, evening_time, updated_at)
                VALUES (1, 1, '08:00', 1, '21:30', '2026-10-03T00:00:00+00:00');
            CREATE TABLE inbox_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(kind IN ('morning', 'evening')),
                source_date TEXT NOT NULL,
                title TEXT NOT NULL,
                body_json TEXT NOT NULL,
                manual INTEGER NOT NULL DEFAULT 0 CHECK(manual IN (0, 1)),
                catch_up INTEGER NOT NULL DEFAULT 0 CHECK(catch_up IN (0, 1)),
                created_at TEXT NOT NULL,
                read_at TEXT,
                dismissed_at TEXT,
                UNIQUE(kind, source_date)
            );
            CREATE INDEX idx_inbox_items_created ON inbox_items(created_at DESC, id DESC);
            INSERT INTO inbox_items(kind, source_date, title, body_json, manual, catch_up, created_at, read_at, dismissed_at)
                VALUES ('morning', '2026-10-03', '旧草稿', '{"date":"2026-10-03"}', 0, 1,
                        '2026-10-03T00:00:00+00:00', '2026-10-03T01:00:00+00:00', NULL);
            """
        )

    service = SchedulerService(database)
    settings = service.get_settings()
    assert settings["weekly_enabled"] is True and settings["weekly_day"] == 6 and settings["weekly_time"] == "20:00"

    rows = service.list_inbox()
    assert len(rows) == 1
    assert rows[0]["id"] == 1 and rows[0]["source_date"] == "2026-10-03"
    assert rows[0]["read_at"] == "2026-10-03T01:00:00+00:00" and rows[0]["unread"] is False

    created = service.run_due(force_kind="weekly")
    assert len(created["created"]) == 1 and created["created"][0]["kind"] == "weekly"
    with database.connect() as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='inbox_items'"
        ).fetchone()["sql"]
        kinds = {row["kind"] for row in connection.execute("SELECT kind FROM inbox_items")}
    assert "'weekly'" in sql
    assert kinds == {"morning", "weekly"}


def test_scheduler_router_reports_runtime_status_from_provider(tmp_path):
    database = Database(tmp_path / "app.sqlite3")
    database.initialize()
    app = FastAPI()
    app.include_router(create_scheduler_router(database, status_provider=lambda: {
        "last_check_at": "2026-10-04T15:00:00+08:00",
        "consecutive_failures": 2,
        "last_error_at": "2026-10-04T14:59:00+08:00",
    }))
    payload = TestClient(app).get("/api/schedule").json()
    assert payload["runtime"]["consecutive_failures"] == 2
    assert payload["runtime"]["last_check_at"] == "2026-10-04T15:00:00+08:00"


def test_app_schedule_exposes_runtime_state(tmp_path):
    client, _ = make_client(tmp_path)
    runtime = client.get("/api/schedule").json()["runtime"]
    assert runtime["consecutive_failures"] == 0
    assert "last_check_at" in runtime and "last_error_at" in runtime
