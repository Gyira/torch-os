"""Conversation export keeps stored answers/citations; audit carries counts only."""

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


def _snapshot(database):
    with database.connect() as connection:
        return {
            "conversations": [dict(row) for row in connection.execute(
                "SELECT id,title,updated_at FROM conversations ORDER BY id"
            )],
            "messages": [dict(row) for row in connection.execute(
                "SELECT id,role,content,created_at FROM messages ORDER BY id"
            )],
        }


def test_conversation_export_contains_answers_and_citations_with_count_only_audit(tmp_path):
    client, database = build_client(tmp_path)
    note = client.post("/api/notes", json={
        "title": "导出档案", "type": "fact", "content": "我是本地示例用户。",
    }).json()
    memory_id = note["memories"][0]["id"]
    assert client.patch(f"/api/memories/{memory_id}/status", json={"status": "active"}).status_code == 200
    chat = client.post("/api/chat", json={"message": "我是谁？"}).json()
    assert chat["citations"], chat
    before = _snapshot(database)

    payload = client.get("/api/export/conversations.json").json()
    assert payload["schema"] == 1
    counts = payload["counts"]
    assert counts["conversations"] == 1
    assert counts["messages"] == 2
    assert counts["user_messages"] == 1 and counts["assistant_messages"] == 1
    assert counts["citations"] == len(chat["citations"])
    conversation = payload["conversations"][0]
    assert [message["role"] for message in conversation["messages"]] == ["user", "assistant"]
    assert conversation["messages"][0]["content"] == "我是谁？"
    assistant = conversation["messages"][1]
    assert assistant["citations"][0]["memory_id"] == memory_id
    assert assistant["citations"][0]["quote"]

    markdown = client.get("/api/export/conversations.md")
    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    text = markdown.text
    assert text.startswith("# 薪火OS 对话导出")
    assert "### 你（" in text and "### 回答（" in text
    assert "我是谁？" in text
    assert "引用 1：我是本地示例用户。" in text
    assert "来源：" in text

    assert _snapshot(database) == before, "export must not change conversations"
    with database.connect() as connection:
        audit = [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type='conversations_exported' ORDER BY id"
        )]
    expected_counts = {
        "conversations": 1, "messages": 2, "citations": len(chat["citations"]),
    }
    assert [json.loads(row["detail_json"]) for row in audit] == [
        {"format": "json", **expected_counts},
        {"format": "markdown", **expected_counts},
    ]
    assert "我是谁" not in audit[0]["detail_json"]
    assert "导出档案" not in audit[0]["detail_json"]


def test_conversation_export_handles_empty_state_and_requires_the_local_session(tmp_path):
    client, _ = build_client(tmp_path)
    payload = client.get("/api/export/conversations.json").json()
    assert payload["counts"]["conversations"] == 0 and payload["conversations"] == []
    assert "（还没有保存的对话）" in client.get("/api/export/conversations.md").text

    anonymous = TestClient(client.app)
    assert anonymous.get("/api/export/conversations.json").status_code == 401
    assert anonymous.get("/api/export/conversations.md").status_code == 401
