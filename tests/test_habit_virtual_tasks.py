"""Today's habits are virtual tasks on /api/time/tasks (2026-10-28)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.app import habits, tasks
from backend.app.application import create_app
from backend.app.db import Database

TODAY = date(2026, 9, 28)  # Monday
DAY = TODAY.isoformat()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(habits, "shanghai_today", lambda: TODAY)
    monkeypatch.setattr(tasks, "_today", lambda: DAY)
    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(
        Database(tmp_path / "torch_os.sqlite3"), vault,
        security_environ={}, env_file=tmp_path / ".env")
    test_client = TestClient(app)
    test_client.get("/")
    return test_client, vault


def make_habit(client, **changes):
    payload = {
        "title": "每天阅读", "minimum_action": "读一页",
        "start_date": DAY, "priority": "high", **changes,
    }
    response = client.post("/api/habits", json=payload)
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_board_appends_only_scheduled_active_habits(client):
    test_client, vault = client
    daily = make_habit(test_client)
    weekly_today = make_habit(test_client, title="周一习惯",
                              frequency={"kind": "weekly", "weekdays": [0]})
    make_habit(test_client, title="周三习惯",
               frequency={"kind": "weekly", "weekdays": [2]})
    archived = make_habit(test_client, title="已归档")
    test_client.patch(f"/api/habits/{archived['id']}",
                      json={"revision": archived["revision"], "status": "archived"})
    make_habit(test_client, title="已结束", start_date="2026-09-27", target_days=1)

    board = test_client.get("/api/time/tasks").json()
    habit_tasks = [task for task in board["tasks"] if task.get("kind") == "habit"]
    assert {task["title"] for task in habit_tasks} == {"每天阅读", "周一习惯"}
    assert board["habit_day"] == DAY
    assert board["habit_board_revision"]
    daily_task = next(task for task in habit_tasks if task["title"] == "每天阅读")
    assert daily_task["virtual"] is True
    assert daily_task["list_name"] == "习惯打卡"
    assert daily_task["date"] == DAY
    assert daily_task["priority"] == "high"
    assert daily_task["entry_status"] == "unrecorded"
    assert daily_task["done"] is False
    assert daily_task["definition_revision"] == daily["revision"]
    assert daily_task["path"].endswith(".md")
    # read_tasks stays habit-free so Dida sync/reports never see virtual rows.
    raw = tasks.read_tasks(vault)
    assert all(task.get("kind") != "habit" for task in raw["tasks"])


def test_toggle_rename_priority_sync_from_board(client):
    test_client, _ = client
    habit = make_habit(test_client)
    before = test_client.get("/api/time/tasks").json()
    task = next(item for item in before["tasks"] if item.get("kind") == "habit")
    assert "habit_board_revision" in before

    toggled = test_client.post(f"/api/habits/{habit['id']}/toggle", json={
        "day": DAY, "done": True, "revision": task["entry_revision"],
    })
    assert toggled.status_code == 200, toggled.text
    after = test_client.get("/api/time/tasks").json()
    assert after["revision"] == before["revision"]  # task lock must not move
    assert after["habit_board_revision"] != before["habit_board_revision"]
    task = next(item for item in after["tasks"] if item.get("kind") == "habit")
    assert task["done"] is True and task["entry_status"] == "done"
    assert task["entry_revision"] == toggled.json()["revision"]

    renamed = test_client.patch(f"/api/habits/{habit['id']}", json={
        "revision": task["definition_revision"], "title": "每日阅读 10 分钟",
    })
    assert renamed.status_code == 200, renamed.text
    after = test_client.get("/api/time/tasks").json()
    task = next(item for item in after["tasks"] if item.get("kind") == "habit")
    assert task["title"] == "每日阅读 10 分钟"

    moved = test_client.patch(f"/api/habits/{habit['id']}", json={
        "revision": task["definition_revision"], "priority": "low",
    })
    assert moved.status_code == 200, moved.text
    after = test_client.get("/api/time/tasks").json()
    task = next(item for item in after["tasks"] if item.get("kind") == "habit")
    assert task["priority"] == "low"


def test_skipped_habit_is_done_for_quadrant_but_not_checked(client):
    test_client, _ = client
    habit = make_habit(test_client)
    entry = test_client.put(f"/api/habits/{habit['id']}/entries/{DAY}", json={
        "revision": 0, "status": "skipped", "note": "今天休息",
    })
    assert entry.status_code == 200, entry.text
    board = test_client.get("/api/time/tasks").json()
    task = next(item for item in board["tasks"] if item.get("kind") == "habit")
    assert task["done"] is True
    assert task["entry_status"] == "skipped"
    assert task["done_on"] == ""
