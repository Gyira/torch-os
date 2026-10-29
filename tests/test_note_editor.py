"""In-app writing: allow-list, revision guard, move rules and deterministic fallbacks."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import note_editor
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.identity import IdentityWrite
from backend.app import identity


def make_client(tmp_path):
    database = Database(tmp_path / "writing.sqlite3")
    app = create_app(database, tmp_path / "vault", security_environ={},
                     env_file=tmp_path / ".env", enable_dsh=False)
    client = TestClient(app)
    client.get("/")
    return client, database, tmp_path / "vault"


def test_create_edit_and_move_a_life_note_with_revision_guard(tmp_path):
    client, database, vault = make_client(tmp_path)
    created = client.post("/api/writing/notes", json={
        "chapter": "reflection", "title": "今天的反思",
        "body": "今天在会议上太急了。", "tags": ["会议"],
    })
    assert created.status_code == 200, created.text
    note = created.json()
    assert note["chapter"] == "reflection"
    assert note["type"] == "reflection"
    assert note["body"].startswith("今天在会议上")
    assert (vault / note["path"]).is_file()

    updated = client.put("/api/writing/notes", json={
        "path": note["path"], "expected_revision": note["revision"],
        "body": "今天在会议上太急了，下次先停三秒。",
    })
    assert updated.status_code == 200, updated.text
    assert "先停三秒" in updated.json()["body"]

    stale = client.put("/api/writing/notes", json={
        "path": note["path"], "expected_revision": note["revision"],
        "body": "旧版本覆盖",
    })
    assert stale.status_code == 409

    moved = client.post("/api/writing/notes/move", json={
        "path": note["path"], "target_chapter": "essay",
        "expected_revision": updated.json()["revision"],
    })
    assert moved.status_code == 200, moved.text
    assert moved.json()["chapter"] == "essay"
    assert not (vault / note["path"]).exists()
    assert (vault / moved.json()["path"]).is_file()

    with database.connect() as connection:
        events = [row["event_type"] for row in connection.execute(
            "SELECT event_type FROM audit_events ORDER BY id")]
    assert "note_created" in events and "note_updated" in events and "note_moved" in events


def test_writing_allow_list_rejects_journal_identity_tasks_and_cross_pillar_move(tmp_path):
    client, _, _ = make_client(tmp_path)
    for chapter in ("journal", "task", "habit", "memory", "health", "family"):
        response = client.post("/api/writing/notes", json={
            "chapter": chapter, "title": "不该写", "body": "x"})
        assert response.status_code == 422, chapter

    knowledge = client.post("/api/writing/notes", json={
        "chapter": "topic", "title": "概念", "body": "正文"}).json()
    moved = client.post("/api/writing/notes/move", json={
        "path": knowledge["path"], "target_chapter": "reflection"})
    assert moved.status_code == 422


def test_update_rejects_a_note_outside_the_writing_allow_list(tmp_path):
    client, database, vault = make_client(tmp_path)
    identity.write_identity(vault, IdentityWrite(name="小明", revision=""))
    path = "01_自我档案/01_内核与三观/身份（identity）.md"
    response = client.put("/api/writing/notes", json={
        "path": path, "body": "偷偷改身份"})
    assert response.status_code == 422


def test_classify_falls_back_to_rules_without_ai(tmp_path):
    client, _, _ = make_client(tmp_path)
    life = client.post("/api/writing/classify", json={
        "text": "今天下午写完周报，发现自己还是太急。", "context": "life"}).json()
    assert life["source"] == "rules"
    assert life["cards"] and life["cards"][0]["chapter"] in note_editor.LIFE_CHAPTERS | {"journal"}

    knowledge = client.post("/api/writing/classify", json={
        "text": "什么是 RAG？它和传统检索有什么区别？", "context": "knowledge"}).json()
    assert knowledge["source"] == "rules"
    assert knowledge["cards"][0]["chapter"] in note_editor.KNOWLEDGE_CHAPTERS


def test_polish_without_ai_is_refused(tmp_path):
    client, _, _ = make_client(tmp_path)
    response = client.post("/api/writing/polish", json={
        "text": "一段话", "context": "life"})
    assert response.status_code == 409


def test_essay_is_a_journal_inbox_type(tmp_path):
    from backend.app import journal_inbox
    cards = journal_inbox.classify(
        "夜里忽然想起小时候的窗，好像还能听见楼下的风。", "2026-10-29")
    assert cards and cards[0]["kind"] == "essay"
    assert cards[0]["target"] == "00_人生路/09_随笔"
