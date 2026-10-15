"""dsh session harvesting: both sides land as candidates, never confirmed."""

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def make_app(tmp_path):
    return create_app(
        Database(tmp_path / "dsh.sqlite3"), tmp_path / "vault",
        ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1),
        security_environ={}, env_file=tmp_path / ".env")


def test_ingest_writes_candidates_for_both_sides(tmp_path):
    client = TestClient(make_app(tmp_path))
    client.get("/")
    response = client.post("/api/dsh-sessions/ingest", json={
        "session_id": "sess-123",
        "turns": [
            {"role": "user", "content": "我最近在学德语，每周三晚上上课，老师叫安娜。"},
            {"role": "assistant", "content": "我会留意你每周三晚上的德语课安排。"},
        ],
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["written"] is True and payload["kept"] >= 1
    note = tmp_path / "vault" / "90-Inbox" / "dsh-session-sess-123.md"
    assert note.is_file()
    text = note.read_text(encoding="utf-8")
    assert "德语" in text and "安娜" in text
    with Database(tmp_path / "dsh.sqlite3").connect() as connection:
        rows = [dict(row) for row in connection.execute("SELECT status FROM memories")]
    assert rows and all(row["status"] == "candidate" for row in rows)
    assert "source_kind: dsh-session-extract" in text


def test_reingesting_the_same_session_rewrites_without_duplicating(tmp_path):
    client = TestClient(make_app(tmp_path))
    client.get("/")
    body = {"session_id": "sess-9", "turns": [
        {"role": "user", "content": "我喜欢用本地优先的笔记工具整理资料。"}]}
    first = client.post("/api/dsh-sessions/ingest", json=body).json()
    second = client.post("/api/dsh-sessions/ingest", json=body).json()
    assert first["written"] and second["written"]
    with Database(tmp_path / "dsh.sqlite3").connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE status='candidate'").fetchone()[0]
        documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == second["memory_count"] and documents == 1


def test_short_chatter_and_questions_stay_out(tmp_path):
    client = TestClient(make_app(tmp_path))
    client.get("/")
    payload = client.post("/api/dsh-sessions/ingest", json={
        "session_id": "sess-quiet",
        "turns": [
            {"role": "user", "content": "你好"},
            {"role": "user", "content": "今天天气怎么样？"},
            {"role": "assistant", "content": "好的"},
        ],
    }).json()
    assert payload["written"] is False and payload["kept"] == 0
    assert not (tmp_path / "vault" / "90-Inbox" / "dsh-session-sess-quiet.md").exists()
