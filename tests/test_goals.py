import json
from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.db import Database
from backend.app.goals import (
    GoalCreate,
    GoalTaskService,
    GoalUpdate,
    TaskCreate,
    TaskUpdate,
    create_goals_router,
)


def make_service(tmp_path):
    return GoalTaskService(Database(tmp_path / "goals.sqlite3"))


def test_goal_task_lifecycle_and_task_completion(tmp_path):
    service = make_service(tmp_path)
    goal = service.create_goal(
        GoalCreate(
            title="准备研究生考试",
            category="学习",
            target_date=date(2027, 6, 1),
            metadata={"source": "test"},
        )
    )
    assert goal["status"] == "active"
    assert goal["metadata"] == {"source": "test"}

    task = service.create_task(
        TaskCreate(
            goal_id=goal["id"],
            title="完成数学章节练习",
            due_date=date.today() + timedelta(days=1),
            tags=["数学", "每日"],
        )
    )
    assert task["status"] == "todo"
    assert task["tags"] == ["数学", "每日"]

    completed = service.update_task(task["id"], TaskUpdate(status="completed"))
    assert completed["status"] == "completed"
    assert completed["completed_at"]
    assert service.list_tasks(goal_id=goal["id"], status="completed")[0]["id"] == task["id"]

    updated_goal = service.update_goal(goal["id"], GoalUpdate(progress=40, status="paused"))
    assert updated_goal["progress"] == 40
    assert updated_goal["status"] == "paused"

    assert service.delete_goal(goal["id"]) is True
    # ON DELETE SET NULL keeps task history after removing its parent goal.
    assert service.get_task(task["id"])["goal_id"] is None


def test_goals_router_contract_and_validation(tmp_path):
    database = Database(tmp_path / "api.sqlite3")
    app = FastAPI()
    app.include_router(create_goals_router(database))
    client = TestClient(app)

    response = client.post(
        "/api/goals",
        json={"title": "改善作息", "description": "连续记录睡眠", "progress": 10},
    )
    assert response.status_code == 201
    goal = response.json()
    assert goal["title"] == "改善作息"

    task_response = client.post(
        "/api/tasks",
        json={"goal_id": goal["id"], "title": "今晚 23:30 前上床"},
    )
    assert task_response.status_code == 201
    task_id = task_response.json()["id"]

    assert client.get("/api/goals?status=unknown").status_code == 400
    assert client.get("/api/tasks?status=unknown").status_code == 400
    assert client.patch(f"/api/tasks/{task_id}", json={"status": "completed"}).json()["completed_at"]
    assert client.delete(f"/api/tasks/{task_id}").status_code == 204
    assert client.get(f"/api/tasks/{task_id}").status_code == 404


def test_invalid_parent_goal_is_rejected(tmp_path):
    service = make_service(tmp_path)
    try:
        service.create_task(TaskCreate(goal_id=999, title="孤立任务"))
    except ValueError as exc:
        assert str(exc) == "goal not found"
    else:
        raise AssertionError("creating a task for a missing goal should fail")


def test_task_reorder_renumbers_atomically_and_detects_stale_tokens(tmp_path):
    database = Database(tmp_path / "reorder.sqlite3")
    app = FastAPI()
    app.include_router(create_goals_router(database))
    client = TestClient(app)
    first = client.post("/api/tasks", json={"title": "第一件"}).json()
    second = client.post("/api/tasks", json={"title": "第二件"}).json()
    third = client.post("/api/tasks", json={"title": "第三件"}).json()

    result = client.post("/api/tasks/reorder", json={"items": [
        {"id": third["id"], "updated_at": third["updated_at"]},
        {"id": first["id"], "updated_at": first["updated_at"]},
        {"id": second["id"], "updated_at": second["updated_at"]},
    ]})
    assert result.status_code == 200
    assert result.json() == {"updated": 3, "ids": [third["id"], first["id"], second["id"]]}
    listed = client.get("/api/tasks").json()
    assert [task["id"] for task in listed] == [third["id"], first["id"], second["id"]]
    assert [task["sort_order"] for task in listed] == [10, 20, 30]
    with database.connect() as connection:
        events = [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type='tasks_reordered'")]
    assert len(events) == 1
    assert json.loads(events[0]["detail_json"]) == {"count": 3}

    # 旧令牌立即失效；混合新旧令牌时整批回滚，先出现的合法任务也不会被写入。
    fresh = {task["id"]: task["updated_at"] for task in client.get("/api/tasks").json()}
    stale = client.post("/api/tasks/reorder", json={"items": [
        {"id": first["id"], "updated_at": "2000-01-01T00:00:00+00:00"},
    ]})
    assert stale.status_code == 409
    mixed = client.post("/api/tasks/reorder", json={"items": [
        {"id": third["id"], "updated_at": fresh[third["id"]]},
        {"id": first["id"], "updated_at": "2000-01-01T00:00:00+00:00"},
    ]})
    assert mixed.status_code == 409
    assert [task["sort_order"] for task in client.get("/api/tasks").json()] == [10, 20, 30]


def test_task_reorder_rejects_invalid_batches_cancelled_and_unknown_tasks(tmp_path):
    database = Database(tmp_path / "reorder-validation.sqlite3")
    app = FastAPI()
    app.include_router(create_goals_router(database))
    client = TestClient(app)
    task = client.post("/api/tasks", json={"title": "可排序任务"}).json()
    cancelled = client.post("/api/tasks", json={"title": "已取消任务"}).json()
    client.patch(f"/api/tasks/{cancelled['id']}", json={"status": "cancelled"})
    cancelled = client.get(f"/api/tasks/{cancelled['id']}").json()

    assert client.post("/api/tasks/reorder", json={"items": []}).status_code == 422
    assert client.post("/api/tasks/reorder", json={"items": [
        {"id": task["id"], "updated_at": task["updated_at"]},
        {"id": task["id"], "updated_at": task["updated_at"]},
    ]}).status_code == 422
    assert client.post("/api/tasks/reorder", json={"items": [
        {"id": task["id"]},
    ]}).status_code == 422
    assert client.post("/api/tasks/reorder", json={"items": [
        {"id": task["id"], "updated_at": task["updated_at"], "extra": 1},
    ]}).status_code == 422
    assert client.post("/api/tasks/reorder", json={"items": [
        {"id": 999_999, "updated_at": "2026-01-01T00:00:00+00:00"},
    ]}).status_code == 404
    assert client.post("/api/tasks/reorder", json={"items": [
        {"id": cancelled["id"], "updated_at": cancelled["updated_at"]},
    ]}).status_code == 409
    too_many = [{"id": index, "updated_at": task["updated_at"]} for index in range(1, 202)]
    assert client.post("/api/tasks/reorder", json={"items": too_many}).status_code == 422
    # 校验失败不产生排序审计，任务保持可读。
    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='tasks_reordered'").fetchone()[0]
    assert count == 0
