"""习惯 ↔ 滴答双向同步集成测试。"""

from __future__ import annotations

import dataclasses
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import habits
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.dida_client import (
    DidaError,
    RemoteHabit,
    RemoteHabitCheckin,
    RemoteSnapshot,
    checkin_from_payload,
    habit_from_payload,
)

TODAY = date(2026, 11, 5)
DAY = "2026-11-05"


def make_remote_habit(**overrides):
    values = dict(
        id="h" * 24, name="读书", color="#97e38b", icon_res="habit_daily_check_in",
        status=0, goal=1.0, step=0.0, unit="Count", habit_type="Boolean",
        repeat_rule="RRULE:FREQ=DAILY;INTERVAL=1", reminders=(),
        section_id="", target_days=None, target_start_date="20261101",
        sort_order=0, record_enable=False, total_check_ins=0,
        current_streak=0, completed_cycles=0, etag="e1", modified_time="",
        created_time="2026-11-01T00:00:00.000+0000",
    )
    values.update(overrides)
    values.setdefault("raw", {
        "id": values["id"], "name": values["name"], "color": values["color"],
        "status": values["status"], "repeatRule": values["repeat_rule"],
        "reminders": list(values["reminders"]),
        "targetDays": values["target_days"] or 0,
        "targetStartDate": int(values["target_start_date"] or 0),
        "etag": values["etag"],
    })
    return RemoteHabit(**values)


def make_checkin(habit_id, day, status=2, **overrides):
    stamp = int(day.replace("-", ""))
    values = dict(
        id=f"c{stamp}" + "0" * 16, habit_id=habit_id, checkin_stamp=stamp,
        value=1.0 if status == 2 else 0.0, goal=1.0, status=status,
        checkin_time="", op_time="",
    )
    values.update(overrides)
    values.setdefault("raw", {
        "id": values["id"], "habitId": values["habit_id"],
        "checkinStamp": values["checkin_stamp"], "value": values["value"],
        "goal": values["goal"], "status": values["status"],
    })
    return RemoteHabitCheckin(**values)


class FakeProvider:
    def __init__(self):
        self.remote_habits: list[RemoteHabit] = []
        self.remote_checkins: dict[str, list[RemoteHabitCheckin]] = {}
        self.habit_adds = []
        self.habit_updates = []
        self.habit_deletes = []
        self.checkin_adds = []
        self.checkin_updates = []
        self.checkin_deletes = []
        self.fetch_habits_error: DidaError | None = None
        self.checkin_query_error: DidaError | None = None
        self.fail_habit_adds = False
        self.fail_checkin_adds = False
        self.closed = False

    async def fetch_snapshot(self, checkpoint=0):
        return RemoteSnapshot(checkpoint=0, inbox_id="", projects=(), tasks=(),
                              projects_complete=True)

    async def fetch_completed(self):
        return ()

    async def fetch_habits(self):
        if self.fetch_habits_error is not None:
            raise self.fetch_habits_error
        return tuple(self.remote_habits)

    async def query_habit_checkins(self, habit_ids, *, after_stamp=19700101):
        if self.checkin_query_error is not None:
            raise self.checkin_query_error
        return {
            habit_id: tuple(rows) for habit_id, rows in self.remote_checkins.items()
            if habit_id in habit_ids
        }

    async def add_habits(self, items):
        self.habit_adds.extend(items)
        if self.fail_habit_adds:
            return {"ok": True, "etags": {},
                    "errors": {item["id"]: "temporary" for item in items}}
        for item in items:
            self.remote_habits.append(habit_from_payload(item))
        return {"ok": True, "etags": {item["id"]: "e-new" for item in items}, "errors": {}}

    async def update_habits(self, items):
        self.habit_updates.extend(items)
        for item in items:
            self.remote_habits = [
                habit_from_payload(item) if habit.id == item["id"] else habit
                for habit in self.remote_habits
            ]
        return {"ok": True, "etags": {item["id"]: "e-upd" for item in items}, "errors": {}}

    async def delete_habits(self, habit_ids):
        self.habit_deletes.extend(habit_ids)
        self.remote_habits = [
            habit for habit in self.remote_habits if habit.id not in habit_ids
        ]
        self.remote_checkins = {
            key: value for key, value in self.remote_checkins.items()
            if key not in habit_ids
        }
        return {"ok": True, "etags": {}, "errors": {}}

    async def add_habit_checkins(self, items):
        self.checkin_adds.extend(items)
        if self.fail_checkin_adds:
            return {"ok": True, "etags": {},
                    "errors": {item["id"]: "temporary" for item in items}}
        for item in items:
            checkin = checkin_from_payload(item)
            self.remote_checkins.setdefault(checkin.habit_id, []).append(checkin)
        return {"ok": True, "etags": {}, "errors": {}}

    async def update_habit_checkins(self, items):
        self.checkin_updates.extend(items)
        for item in items:
            checkin = checkin_from_payload(item)
            rows = self.remote_checkins.setdefault(checkin.habit_id, [])
            self.remote_checkins[checkin.habit_id] = [
                checkin if row.id == checkin.id else row for row in rows
            ]
        return {"ok": True, "etags": {}, "errors": {}}

    async def delete_habit_checkins(self, checkin_ids):
        self.checkin_deletes.extend(checkin_ids)
        for habit_id, rows in list(self.remote_checkins.items()):
            self.remote_checkins[habit_id] = [
                row for row in rows if row.id not in checkin_ids
            ]
        return {"ok": True, "etags": {}, "errors": {}}

    async def add_tasks(self, tasks):
        return {"ok": True, "etags": {}, "errors": {}}

    async def update_tasks(self, tasks):
        return {"ok": True, "etags": {}, "errors": {}}

    async def move_tasks(self, entries):
        return {"ok": True, "etags": {}, "errors": {}}

    async def delete_tasks(self, entries):
        return {"ok": True, "etags": {}, "errors": {}}

    async def restore_tasks(self, entries):
        return {"ok": True, "etags": {}, "errors": {}}

    async def add_projects(self, projects):
        return {"ok": True, "etags": {}, "errors": {}}

    async def update_projects(self, projects):
        return {"ok": True, "etags": {}, "errors": {}}

    async def delete_projects(self, project_ids):
        return {"ok": True, "etags": {}, "errors": {}}

    async def test_connection(self):
        return {"ok": True, "task_count": 0, "project_count": 0,
                "habit_count": len(self.remote_habits), "latency_ms": 1}

    async def aclose(self):
        self.closed = True


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(habits, "shanghai_today", lambda: TODAY)
    vault = tmp_path / "vault"
    vault.mkdir()
    database = Database(tmp_path / "torch.sqlite3")
    provider = FakeProvider()
    app = create_app(
        database, vault, security_environ={}, env_file=tmp_path / ".env",
        dida_provider_factory=lambda _credentials: provider,
        enable_dida_auto_push=False,
    )
    client = TestClient(app)
    client.get("/")
    # 后台 15 秒循环不应在测试里自动触发完整同步；测试显式调用 /run。
    app.state.dida_sync_service.due = lambda: False
    response = client.put("/api/sync/dida/settings", json={
        "enabled": False, "region": "dida365", "interval_minutes": 15,
        "completed_days": 30, "push_deletes": True, "push_changes": True,
        "habits_enabled": True, "timezone": "Asia/Shanghai",
        "device_version": 8000, "cookie": "t=secret; _csrf_token=csrf-secret",
    })
    assert response.status_code == 200, response.text
    return SimpleNamespace(client=client, vault=vault, db=database,
                           provider=provider, app=app)


def create_habit(client, **overrides):
    payload = {"title": "读书", "start_date": "2026-11-01", **overrides}
    response = client.post("/api/habits", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def put_entry(client, habit_id, day, status, note="", revision=0):
    response = client.put(f"/api/habits/{habit_id}/entries/{day}", json={
        "revision": revision, "status": status, "note": note,
    })
    assert response.status_code == 200, response.text
    return response.json()


def run(client, *, dry_run=False, force=False):
    response = client.post("/api/sync/dida/run",
                           json={"dry_run": dry_run, "force": force})
    assert response.status_code == 200, response.text
    return response.json()


def link_rows(db):
    with db.connect() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM dida_habit_links ORDER BY external_id")]


def checkin_rows(db):
    with db.connect() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM dida_habit_checkin_links ORDER BY external_id, date")]


def notes(vault):
    return list((vault / "00_人生路" / "04_目标" / "04_习惯追踪" / "习惯").glob("*.md"))


def test_settings_habits_default_on_and_can_disable(env):
    status = env.client.get("/api/sync/dida/status").json()
    assert status["habits_enabled"] is True
    response = env.client.put("/api/sync/dida/settings", json={
        "enabled": False, "region": "dida365", "interval_minutes": 15,
        "completed_days": 30, "push_deletes": True, "push_changes": True,
        "habits_enabled": False, "timezone": "Asia/Shanghai",
        "device_version": 8000,
    })
    assert response.status_code == 200
    assert response.json()["habits_enabled"] is False


def test_pull_remote_habit_and_checkin(env):
    remote = make_remote_habit()
    env.provider.remote_habits = [remote]
    env.provider.remote_checkins[remote.id] = [make_checkin(remote.id, "2026-11-03")]
    result = run(env.client)
    assert result["counts"]["habits_created"] == 1
    assert result["counts"]["checkins_updated"] == 1
    [note] = notes(env.vault)
    text = note.read_text(encoding="utf-8")
    assert "type: habit" in text
    assert f"dida_id: {remote.id}" in text
    assert "title: 读书" in text
    assert "kind: daily" in text
    habit = env.client.get("/api/habits").json()[0]
    entries = env.client.get(
        f"/api/habits/{habit['id']}/entries",
        params={"from_date": "2026-11-03", "to_date": "2026-11-03"}).json()
    assert entries[0]["status"] == "done"
    assert link_rows(env.db)[0]["status"] == "synced"
    assert checkin_rows(env.db)[0]["last_local_status"] == "done"


def test_local_habit_push_creates_remote_and_dida_id(env):
    habit = create_habit(env.client)
    result = run(env.client)
    assert result["counts"]["habits_created_pushed"] == 1
    assert len(env.provider.habit_adds) == 1
    remote = env.provider.remote_habits[0]
    assert remote.name == "读书"
    assert f"dida_id: {remote.id}" in notes(env.vault)[0].read_text(encoding="utf-8")
    second = run(env.client)
    assert second["counts"].get("habits_created_pushed", 0) == 0
    assert len(env.provider.habit_adds) == 1
    assert habit["title"] == "读书"


def test_local_checkin_push_and_uncheck(env):
    habit = create_habit(env.client)
    run(env.client)
    remote = env.provider.remote_habits[0]
    put_entry(env.client, habit["id"], "2026-11-03", "done")
    result = run(env.client)
    assert result["counts"]["checkins_created_pushed"] == 1
    [checkin] = env.provider.remote_checkins[remote.id]
    assert checkin.status == 2
    entry = env.client.get(
        f"/api/habits/{habit['id']}/entries",
        params={"from_date": "2026-11-03", "to_date": "2026-11-03"}).json()[0]
    put_entry(env.client, habit["id"], "2026-11-03", "unrecorded",
              revision=entry["revision"])
    result = run(env.client)
    assert result["counts"]["checkins_deleted_pushed"] == 1
    assert env.provider.remote_checkins[remote.id] == []


def test_local_failed_pushes_as_status_one(env):
    habit = create_habit(env.client)
    put_entry(env.client, habit["id"], "2026-11-03", "failed")
    run(env.client)
    remote = env.provider.remote_habits[0]
    [checkin] = env.provider.remote_checkins[remote.id]
    assert checkin.status == 1 and checkin.value == 0


def test_same_title_first_link_does_not_duplicate(env):
    create_habit(env.client)
    env.provider.remote_habits = [make_remote_habit()]
    result = run(env.client)
    assert result["counts"].get("habits_created_pushed", 0) == 0
    assert len(env.provider.remote_habits) == 1
    assert link_rows(env.db)[0]["status"] == "synced"


def test_local_only_field_change_does_not_push(env):
    create_habit(env.client)
    run(env.client)
    habit = env.client.get("/api/habits").json()[0]
    response = env.client.patch(f"/api/habits/{habit['id']}", json={
        "revision": habit["revision"], "cue": "换鞋", "group": "健康",
        "priority": "high", "icon": "🏃",
    })
    assert response.status_code == 200
    result = run(env.client)
    assert result["counts"].get("habits_updated_pushed", 0) == 0
    assert env.provider.habit_updates == []


def test_definition_conflict_and_keep_local(env):
    create_habit(env.client)
    run(env.client)
    habit = env.client.get("/api/habits").json()[0]
    env.client.patch(f"/api/habits/{habit['id']}", json={
        "revision": habit["revision"], "title": "本地新名"})
    remote = dataclasses.replace(env.provider.remote_habits[0],
                                 name="远端新名", etag="e3")
    env.provider.remote_habits = [remote]
    result = run(env.client)
    assert result["counts"]["habit_conflicts"] == 1
    conflicts = env.client.get("/api/sync/dida/habit-conflicts").json()["conflicts"]
    assert conflicts and conflicts[0]["conflict_reason"] == "local_and_remote_changed"
    response = env.client.post(
        f"/api/sync/dida/habit-conflicts/{remote.id}/keep-local")
    assert response.status_code == 200
    result = run(env.client)
    assert result["counts"]["habits_updated_pushed"] == 1
    assert env.provider.remote_habits[0].name == "本地新名"


def test_remote_delete_archives_local_without_deleting_note(env):
    create_habit(env.client)
    run(env.client)
    env.provider.remote_habits = []
    result = run(env.client)
    assert result["counts"]["habits_archived"] == 1
    habit = env.client.get("/api/habits?status=archived").json()[0]
    assert habit["status"] == "archived"
    assert notes(env.vault)
    assert link_rows(env.db)[0]["status"] == "remote_deleted"


def test_remote_failed_maps_to_failed(env):
    remote = make_remote_habit()
    env.provider.remote_habits = [remote]
    env.provider.remote_checkins[remote.id] = [make_checkin(remote.id, "2026-11-03")]
    run(env.client)
    habit = env.client.get("/api/habits").json()[0]
    env.provider.remote_checkins[remote.id] = [
        make_checkin(remote.id, "2026-11-03", status=1)
    ]
    run(env.client)
    entry = env.client.get(
        f"/api/habits/{habit['id']}/entries",
        params={"from_date": "2026-11-03", "to_date": "2026-11-03"}).json()[0]
    assert entry["status"] == "failed"


def test_local_skipped_is_not_pushed_and_deletes_remote_checkin(env):
    habit = create_habit(env.client)
    put_entry(env.client, habit["id"], "2026-11-03", "done")
    run(env.client)
    remote = env.provider.remote_habits[0]
    assert env.provider.remote_checkins[remote.id][0].status == 2
    entry = env.client.get(
        f"/api/habits/{habit['id']}/entries",
        params={"from_date": "2026-11-03", "to_date": "2026-11-03"}).json()[0]
    put_entry(env.client, habit["id"], "2026-11-03", "skipped",
              revision=entry["revision"])
    result = run(env.client)
    assert result["counts"]["checkins_deleted_pushed"] == 1
    assert env.provider.remote_checkins[remote.id] == []
    # 下一次拉取不能把本地主动休息改成未记录。
    run(env.client)
    entry = env.client.get(
        f"/api/habits/{habit['id']}/entries",
        params={"from_date": "2026-11-03", "to_date": "2026-11-03"}).json()[0]
    assert entry["status"] == "skipped"


def test_local_delete_pushes_remote_delete(env):
    create_habit(env.client)
    run(env.client)
    habit = env.client.get("/api/habits").json()[0]
    response = env.client.post(f"/api/habits/{habit['id']}/delete", json={
        "revision": habit["revision"]})
    assert response.status_code == 200
    result = run(env.client)
    assert result["counts"]["habits_deleted_pushed"] == 1
    assert env.provider.remote_habits == []
    assert link_rows(env.db) == []


def test_push_deletes_off_marks_local_missing(env):
    create_habit(env.client)
    run(env.client)
    habit = env.client.get("/api/habits").json()[0]
    env.client.put("/api/sync/dida/settings", json={
        "enabled": False, "region": "dida365", "interval_minutes": 15,
        "completed_days": 30, "push_deletes": False, "push_changes": True,
        "habits_enabled": True, "timezone": "Asia/Shanghai", "device_version": 8000,
    })
    env.client.post(f"/api/habits/{habit['id']}/delete", json={
        "revision": habit["revision"]})
    result = run(env.client)
    assert result["counts"].get("habits_deleted_pushed", 0) == 0
    assert link_rows(env.db)[0]["status"] == "local_missing"


def test_dry_run_writes_nothing(env):
    env.provider.remote_habits = [make_remote_habit()]
    result = run(env.client, dry_run=True)
    assert result["counts"]["habits_created"] == 1
    assert notes(env.vault) == []
    assert link_rows(env.db) == []


def test_habit_api_failure_does_not_fail_task_sync(env):
    env.provider.fetch_habits_error = DidaError("http_404", "not found")
    result = run(env.client)
    assert any(warning.startswith("habit_fetch_error_") for warning in result["warnings"])


def test_failed_habit_create_reuses_pending_id(env):
    create_habit(env.client)
    env.provider.fail_habit_adds = True
    run(env.client)
    pending = link_rows(env.db)[0]
    assert pending["status"] == "pending_create"
    external_id = pending["external_id"]
    assert env.provider.habit_adds[0]["id"] == external_id
    env.provider.fail_habit_adds = False
    run(env.client)
    assert env.provider.habit_adds[-1]["id"] == external_id
    assert any(habit.id == external_id for habit in env.provider.remote_habits)
    assert link_rows(env.db)[0]["status"] == "synced"


def test_failed_checkin_add_reuses_pending_id(env):
    habit = create_habit(env.client)
    put_entry(env.client, habit["id"], "2026-11-03", "done")
    env.provider.fail_checkin_adds = True
    run(env.client)
    [pending] = checkin_rows(env.db)
    assert pending["status"] == "pending_add"
    checkin_id = pending["remote_checkin_id"]
    assert env.provider.checkin_adds[0]["id"] == checkin_id
    env.provider.fail_checkin_adds = False
    run(env.client)
    assert env.provider.checkin_adds[-1]["id"] == checkin_id
    remote = env.provider.remote_habits[0]
    assert [item.id for item in env.provider.remote_checkins[remote.id]] == [checkin_id]
    assert checkin_rows(env.db)[0]["status"] == "synced"


def test_checkin_query_failure_does_not_uncheck_local_history(env):
    remote = make_remote_habit()
    env.provider.remote_habits = [remote]
    env.provider.remote_checkins[remote.id] = [make_checkin(remote.id, "2026-11-03")]
    run(env.client)
    habit = env.client.get("/api/habits").json()[0]
    env.provider.checkin_query_error = DidaError(
        "checkins_read_forbidden", "需要 CSRF")
    result = run(env.client)
    assert "checkin_query_error_checkins_read_forbidden" in result["warnings"]
    entry = env.client.get(
        f"/api/habits/{habit['id']}/entries",
        params={"from_date": "2026-11-03", "to_date": "2026-11-03"}).json()[0]
    assert entry["status"] == "done"


def test_checkin_mass_uncheck_guard(env):
    remote = make_remote_habit()
    env.provider.remote_habits = [remote]
    env.provider.remote_checkins[remote.id] = [
        make_checkin(remote.id, f"2026-10-{day:02d}") for day in range(1, 8)
    ]
    run(env.client)
    env.provider.remote_checkins[remote.id] = []
    result = run(env.client)
    assert "checkin_mass_uncheck_guard" in result["warnings"]
    habit = env.client.get("/api/habits").json()[0]
    entries = env.client.get(
        f"/api/habits/{habit['id']}/entries",
        params={"from_date": "2026-10-01", "to_date": "2026-10-07"}).json()
    assert any(item["status"] == "done" for item in entries)
