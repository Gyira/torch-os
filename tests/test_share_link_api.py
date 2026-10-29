"""DeepSeek share links: host allow-list, preview, archive and AI digest proposal."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import knowledge_share, share_link
from backend.app.application import create_app
from backend.app.db import Database

PREVIEW = {
    "share_id": "abcd1234",
    "share_url": "https://chat.deepseek.com/share/abcd1234",
    "title": "RAG 是什么",
    "turns": 1,
    "chars": 42,
    "messages": [{"role": "user", "chars": 8, "at": None, "preview": "RAG 是什么"}],
    "total_rounds": 1,
    "picked_rounds": [1],
    "markdown": "> 来源：https://chat.deepseek.com/share/abcd1234\n\n## 我问\n\nRAG 是什么\n\n## 回答\n\n检索增强生成。\n",
    "chars_markdown": 80,
    "too_long": False,
    "suggested_chapter": "topic",
}


class FakeResponse:
    status_code = 200
    headers = {"content-type": "application/json"}

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params))
        return FakeResponse(self.payload)


def payload_with(content: str = "RAG 是什么") -> dict:
    return {"code": 0, "data": {"biz_data": {"messages": [
        {"role": "USER", "inserted_at": 1792637351.0,
         "fragments": [{"type": "REQUEST", "content": content}]},
        {"role": "ASSISTANT", "inserted_at": 1792637360.0,
         "fragments": [{"type": "THINK", "content": "思考过程"},
                        {"type": "RESPONSE", "content": "检索增强生成。"}]},
    ]}}}


def test_share_link_only_accepts_deepseek_and_drops_thinking():
    with pytest.raises(share_link.ShareLinkError):
        share_link.parse_share_id("https://evil.example/share/abcd1234")
    assert share_link.parse_share_id(
        "https://chat.deepseek.com/share/abcd1234") == "abcd1234"
    client = FakeClient(payload_with())
    result = share_link.preview("https://chat.deepseek.com/share/abcd1234", client=client)
    assert result["turns"] == 1
    assert result["suggested_chapter"] == "concept"
    assert "RAG 是什么" in result["markdown"]
    assert "思考过程" not in result["markdown"]
    assert client.calls[0][0] == share_link.SHARE_API


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_share.share_link, "preview",
                        lambda url, rounds="": dict(PREVIEW))
    database = Database(tmp_path / "share.sqlite3")
    app = create_app(database, tmp_path / "vault", security_environ={},
                     env_file=tmp_path / ".env", enable_dsh=False)
    client = TestClient(app)
    client.get("/")
    return client, app, tmp_path / "vault"


def test_archive_writes_a_knowledge_note_after_confirmation(tmp_path, monkeypatch):
    client, _, vault = make_client(tmp_path, monkeypatch)
    preview = client.post("/api/knowledge/share/preview", json={
        "url": "https://chat.deepseek.com/share/abcd1234"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["title"] == "RAG 是什么"

    blocked = client.post("/api/knowledge/share/archive", json={
        "url": "https://chat.deepseek.com/share/abcd1234", "confirm": False})
    assert blocked.status_code == 422

    archived = client.post("/api/knowledge/share/archive", json={
        "url": "https://chat.deepseek.com/share/abcd1234",
        "confirm": True, "chapter": "topic", "title": "RAG 是什么"})
    assert archived.status_code == 200, archived.text
    note = archived.json()["note"]
    assert note["chapter"] == "topic"
    assert "检索增强生成" in note["body"]
    assert (vault / note["path"]).is_file()


def test_digest_creates_a_new_note_proposal_and_applies_it(tmp_path, monkeypatch):
    client, app, vault = make_client(tmp_path, monkeypatch)
    app.state.ai_service.run_once = lambda *args, **kwargs: {
        "structured": {
            "summary": "RAG 是检索增强生成",
            "note": {"title": "RAG 笔记", "chapter": "concept",
                     "markdown": "> 摘要\n\nRAG = 检索 + 生成。\n"},
        },
        "seconds": 0.1,
    }
    response = client.post("/api/knowledge/share/digest", json={
        "url": "https://chat.deepseek.com/share/abcd1234"})
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal"]
    assert proposal["kind"] == "new_note"
    assert proposal["payload"]["chapter"] == "concept"

    applied = client.post(f"/api/proposals/{proposal['id']}/apply",
                          json={"confirm": True})
    assert applied.status_code == 200, applied.text
    note = applied.json()["result"]["note"]
    assert note["chapter"] == "concept"
    assert (vault / note["path"]).is_file()


def test_archive_rejects_non_knowledge_chapter(tmp_path, monkeypatch):
    client, _, _ = make_client(tmp_path, monkeypatch)
    response = client.post("/api/knowledge/share/archive", json={
        "url": "https://chat.deepseek.com/share/abcd1234",
        "confirm": True, "chapter": "reflection"})
    assert response.status_code == 422
