"""Memory export stays read-only: portable snapshots, no review changes."""

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


def add_note(client, title, content, kind="fact"):
    response = client.post("/api/notes", json={"title": title, "content": content, "type": kind})
    assert response.status_code == 201, response.text
    memories = response.json()["memories"]
    assert len(memories) == 1, memories
    return memories[0]["id"]


def test_json_export_contains_all_states_with_counts_and_no_review_changes(tmp_path):
    client, database = build_client(tmp_path)
    active = add_note(client, "已确认标题", "已确认内容 <tag>")
    assert client.patch(f"/api/memories/{active}/status", json={"status": "active"}).status_code == 200
    add_note(client, "待确认标题", "待确认内容")
    rejected = add_note(client, "拒绝标题", "拒绝内容")
    assert client.patch(f"/api/memories/{rejected}/status", json={"status": "rejected"}).status_code == 200

    with database.connect() as connection:
        before = [dict(row) for row in connection.execute(
            "SELECT id,status,revision,updated_at FROM memories ORDER BY id"
        )]

    response = client.get("/api/export/memories.json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    payload = json.loads(response.text)
    assert payload["schema"] == 1 and payload["filter"] == "all" and payload["total"] == 3
    assert payload["counts"] == {"active": 1, "candidate": 1, "rejected": 1, "archived": 0}
    by_title = {row["title"]: row for row in payload["memories"]}
    assert set(by_title) == {"已确认内容", "待确认内容", "拒绝内容"}
    assert by_title["已确认内容"]["content"] == "已确认内容"
    assert by_title["已确认内容"]["source_quote"] == "已确认内容 <tag>"
    assert by_title["已确认内容"]["source_path"].endswith(".md")
    assert by_title["已确认内容"]["usage_recent"] == 0
    assert by_title["待确认内容"]["status"] == "candidate"
    assert payload["exported_at"].endswith("+08:00")

    with database.connect() as connection:
        after = [dict(row) for row in connection.execute(
            "SELECT id,status,revision,updated_at FROM memories ORDER BY id"
        )]
        audit = [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type='memory_exported'"
        )]
    assert before == after, "export must not change memories"
    assert len(audit) == 1
    detail = json.loads(audit[0]["detail_json"])
    assert detail == {
        "format": "json", "filter": "all", "total": 3,
        "counts": {"active": 1, "candidate": 1, "rejected": 1, "archived": 0},
    }
    assert "已确认内容" not in audit[0]["detail_json"]


def test_markdown_export_renders_statuses_sources_and_filter(tmp_path):
    client, _ = build_client(tmp_path)
    active = add_note(client, "Markdown 标题", "第一行完整内容。")
    assert client.patch(f"/api/memories/{active}/status", json={"status": "active"}).status_code == 200
    add_note(client, "候选标题", "候选内容")

    response = client.get("/api/export/memories.md")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    text = response.text
    assert text.startswith("# 薪火OS 记忆导出")
    assert "## 已确认（1）" in text and "## 待确认（1）" in text
    assert "### 第一行完整内容。" in text
    assert "来源：03_知识库/00_收件箱" in text

    filtered = client.get("/api/export/memories.md?status=active")
    assert filtered.status_code == 200
    assert "第一行完整内容。" in filtered.text and "候选内容" not in filtered.text
    assert "## 已确认（1）" in filtered.text and "## 待确认" not in filtered.text

    assert client.get("/api/export/memories.md?status=missing").status_code == 422


def test_export_requires_the_local_session(tmp_path):
    client, _ = build_client(tmp_path)
    anonymous = TestClient(client.app)
    assert anonymous.get("/api/export/memories.json").status_code == 401
    assert anonymous.get("/api/export/memories.md").status_code == 401
