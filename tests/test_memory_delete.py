"""Hard delete removes a memory, its links, and suppresses re-extraction."""

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


def test_memory_delete_removes_row_links_and_suppresses_reextraction(tmp_path):
    client, database = build_client(tmp_path)
    note = client.post("/api/notes", json={
        "title": "待删除", "content": "这条内容将被永久删除。", "type": "fact",
    }).json()
    memory_id = note["memories"][0]["id"]
    document_id = note["document"]["id"]
    document_path = note["document"]["path"]
    assert client.patch(f"/api/memories/{memory_id}/status", json={"status": "active"}).status_code == 200
    chat = client.post("/api/chat", json={"message": "这条内容将被永久删除？"}).json()
    assert chat["citations"] and chat["citations"][0]["memory_id"] == memory_id

    assert client.post(f"/api/memories/{memory_id}/delete", json={"confirm": False}).status_code == 422
    assert client.post(f"/api/memories/{memory_id}/delete", json={"confirm": 1}).status_code == 422
    response = client.post(f"/api/memories/{memory_id}/delete", json={"confirm": True})
    assert response.status_code == 200
    result = response.json()
    assert result["deleted"] is True and result["document_id"] == document_id
    assert result["usages_removed"] >= 1
    assert result["citations_removed"] >= 1

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM memories WHERE id=?", (memory_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM memory_embeddings WHERE memory_id=?", (memory_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM memory_usages WHERE memory_id=?", (memory_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM memory_events WHERE memory_id=?", (memory_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM memory_deletions WHERE document_id=?", (document_id,)).fetchone()[0] == 1
        citations_raw = connection.execute(
            "SELECT citations_json FROM messages WHERE role='assistant' ORDER BY id DESC LIMIT 1"
        ).fetchone()["citations_json"]
    assert json.loads(citations_raw) == []

    with database.connect() as connection:
        audit = [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type='memory_deleted' ORDER BY id DESC LIMIT 1"
        )]
    assert len(audit) == 1
    detail = json.loads(audit[0]["detail_json"])
    assert detail["memory_id"] == memory_id and detail["document_id"] == document_id
    assert "这条内容" not in audit[0]["detail_json"]

    # Re-extraction with the same content stays suppressed; different content still works.
    database.replace_document_memories(document_id, [{
        "kind": "fact", "title": "待删除", "content": "这条内容将被永久删除。",
        "source_quote": "这条内容将被永久删除。", "confidence": "explicit",
    }])
    assert client.get("/api/memories?status=candidate").json() == []
    database.replace_document_memories(document_id, [{
        "kind": "note", "title": "另一条", "content": "这是另一条不同的候选内容。",
        "source_quote": "这是另一条不同的候选内容。", "confidence": "explicit",
    }])
    remaining = client.get("/api/memories?status=candidate").json()
    assert [item["title"] for item in remaining] == ["另一条"]

    # The source Markdown itself is never deleted.
    assert (tmp_path / "vault" / document_path).exists()
    assert client.post("/api/memories/999999/delete", json={"confirm": True}).status_code == 404
