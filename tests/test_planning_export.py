"""Goals/tasks export stays a read-only snapshot with count-only audit."""

import json

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def build_client(tmp_path):
    database = Database(tmp_path / "torch_os.sqlite3")
    gateway = ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1)
    app = create_app(database, tmp_path / "vault", gateway, security_environ={})
    client = TestClient(app)
    client.get("/")
    return client, database


def test_planning_export_nests_tasks_and_writes_counts_only(tmp_path):
    client, database = build_client(tmp_path)
    goal = client.post("/api/goals", json={
        "title": "导出目标", "description": "说明文本", "category": "学习", "target_date": "2026-12-31",
    }).json()
    task = client.post("/api/tasks", json={
        "title": "导出任务甲", "goal_id": goal["id"], "priority": "high",
        "due_date": "2026-10-10", "tags": ["a"],
    }).json()
    assert client.patch(f"/api/tasks/{task['id']}", json={"status": "completed"}).status_code == 200
    client.post("/api/tasks", json={"title": "无目标任务", "priority": "low"})

    with database.connect() as connection:
        before = [dict(row) for row in connection.execute(
            "SELECT id,status,priority,updated_at FROM tasks ORDER BY id"
        )]

    response = client.get("/api/export/planning.json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    payload = response.json()
    assert payload["schema"] == 1
    assert payload["counts"]["goals"] == 1 and payload["counts"]["tasks"] == 2
    assert payload["counts"]["goals_by_status"] == {"active": 1, "paused": 0, "completed": 0, "archived": 0}
    assert payload["counts"]["tasks_by_status"] == {"todo": 1, "in_progress": 0, "completed": 1, "cancelled": 0}
    assert payload["goals"][0]["title"] == "导出目标"
    assert [item["title"] for item in payload["goals"][0]["tasks"]] == ["导出任务甲"]
    assert payload["goals"][0]["tasks"][0]["tags"] == ["a"]
    assert [item["title"] for item in payload["unlinked_tasks"]] == ["无目标任务"]

    markdown = client.get("/api/export/planning.md")
    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    text = markdown.text
    assert text.startswith("# 薪火OS 目标与任务导出")
    assert "## 目标：导出目标（进行中 · 进度 0%）" in text
    assert "- [x] 导出任务甲 · 高优先级 · 截止 2026-10-10 · 完成于" in text
    assert "## 未关联目标的任务" in text and "- [ ] 无目标任务" in text

    with database.connect() as connection:
        after = [dict(row) for row in connection.execute(
            "SELECT id,status,priority,updated_at FROM tasks ORDER BY id"
        )]
        audit = [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type='planning_exported' ORDER BY id"
        )]
    assert before == after, "export must not change tasks"
    assert [json.loads(row["detail_json"]) for row in audit] == [
        {"format": "json", "goals": 1, "tasks": 2},
        {"format": "markdown", "goals": 1, "tasks": 2},
    ]
    assert "导出目标" not in audit[0]["detail_json"]


def test_planning_export_handles_empty_state_and_requires_the_local_session(tmp_path):
    client, _ = build_client(tmp_path)
    payload = client.get("/api/export/planning.json").json()
    assert payload["counts"]["goals"] == 0 and payload["unlinked_tasks"] == []
    text = client.get("/api/export/planning.md").text
    assert "- （没有未关联目标的任务）" in text

    anonymous = TestClient(client.app)
    assert anonymous.get("/api/export/planning.json").status_code == 401
    assert anonymous.get("/api/export/planning.md").status_code == 401
