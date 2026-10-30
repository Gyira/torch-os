"""In-app writing: allow-list, revision guard, move rules and deterministic fallbacks."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app import note_editor
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.identity import IdentityWrite
from backend.app import identity
from backend.app.vault import scan_vault


def make_client(tmp_path, *, read_only=False):
    database = Database(tmp_path / "writing.sqlite3")
    environ = {"VAULT_READ_ONLY": "1"} if read_only else {}
    app = create_app(database, tmp_path / "vault", security_environ=environ,
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


def test_move_updates_the_scanned_document_index_path(tmp_path):
    client, database, vault = make_client(tmp_path)
    created = client.post("/api/writing/notes", json={
        "chapter": "reflection", "title": "移动索引", "body": "移动前的正文",
    }).json()
    assert scan_vault(vault, database)["changed"] >= 1
    with database.connect() as connection:
        before = connection.execute(
            "SELECT * FROM documents WHERE path=?", (created["path"],)).fetchone()
    assert before is not None and before["available"] == 1

    moved = client.post("/api/writing/notes/move", json={
        "path": created["path"], "target_chapter": "essay",
        "expected_revision": created["revision"],
    })
    assert moved.status_code == 200, moved.text
    new_path = moved.json()["path"]

    with database.connect() as connection:
        after = connection.execute(
            "SELECT * FROM documents WHERE id=?", (before["id"],)).fetchone()
        old = connection.execute(
            "SELECT id FROM documents WHERE path=?", (created["path"],)).fetchone()
    assert after is not None
    assert after["path"] == new_path and after["available"] == 1
    assert old is None
    metadata = json.loads(after["metadata_json"])
    assert metadata["chapter"] == "essay"
    assert metadata["pillar"] == "life"
    # 索引已经带着新路径/哈希，下一次扫描不需要把它当成新文档重扫。
    assert scan_vault(vault, database)["changed"] == 0


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


def test_notes_move_between_custom_notebooks_and_delete_into_trash(tmp_path):
    client, database, vault = make_client(tmp_path)
    for name in ("甲本", "乙本"):
        assert client.post("/api/notebooks", json={"name": name}).status_code == 200

    created = client.post("/api/writing/notes", json={
        "chapter": "custom:甲本", "title": "分错的本子", "body": "正文",
    })
    assert created.status_code == 200, created.text
    note = created.json()
    assert note["path"].startswith("00_人生路/甲本/")

    moved = client.post("/api/writing/notes/move", json={
        "path": note["path"], "target_chapter": "custom:乙本",
        "expected_revision": note["revision"],
    })
    assert moved.status_code == 200, moved.text
    moved_note = moved.json()
    assert moved_note["chapter"] == "custom:乙本"
    assert moved_note["path"].startswith("00_人生路/乙本/")
    assert (vault / moved_note["path"]).is_file()
    assert not (vault / note["path"]).exists()

    # 自定义本子 → 固定章节 → 另一个自定义本子，都应该走同一条移动路径。
    back = client.post("/api/writing/notes/move", json={
        "path": moved_note["path"], "target_chapter": "essay",
        "expected_revision": moved_note["revision"],
    })
    assert back.status_code == 200, back.text
    essay_note = back.json()
    assert essay_note["chapter"] == "essay"
    assert essay_note["path"].startswith("00_人生路/09_随笔/")
    assert not (vault / moved_note["path"]).exists()

    final = client.post("/api/writing/notes/move", json={
        "path": essay_note["path"], "target_chapter": "custom:甲本",
        "expected_revision": essay_note["revision"],
    })
    assert final.status_code == 200, final.text
    final_note = final.json()
    assert final_note["chapter"] == "custom:甲本"
    assert final_note["path"].startswith("00_人生路/甲本/")

    stale = client.post("/api/writing/notes/delete", json={
        "path": final_note["path"], "expected_revision": note["revision"],
    })
    assert stale.status_code == 409

    deleted = client.post("/api/writing/notes/delete", json={
        "path": final_note["path"], "expected_revision": final_note["revision"],
    })
    assert deleted.status_code == 200, deleted.text
    payload = deleted.json()
    assert payload["deleted"] is True
    assert payload["path"] == final_note["path"]
    assert payload["trash_path"].startswith(".torch-os/笔记回收站/")
    assert (vault / payload["trash_path"]).is_file()
    assert not (vault / final_note["path"]).exists()

    with database.connect() as connection:
        events = [row["event_type"] for row in connection.execute(
            "SELECT event_type FROM audit_events ORDER BY id")]
    assert "note_moved" in events and "note_deleted" in events


def test_delete_is_refused_for_journal_unknown_files_and_read_only_vault(tmp_path):
    client, _, vault = make_client(tmp_path)
    journal = client.post("/api/writing/notes/delete", json={
        "path": "00_人生路/01_日记/2026-10-30.md"})
    assert journal.status_code == 422

    missing = client.post("/api/writing/notes/delete", json={
        "path": "00_人生路/09_随笔/不存在.md"})
    assert missing.status_code == 404

    readonly_root = tmp_path / "readonly"
    readonly_root.mkdir()
    read_only_client, _, read_only_vault = make_client(readonly_root, read_only=True)
    path = read_only_vault / "00_人生路" / "09_随笔" / "旧文.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntitle: 旧文\n---\n\n正文\n", encoding="utf-8")
    refused = read_only_client.post("/api/writing/notes/delete", json={
        "path": "00_人生路/09_随笔/旧文.md"})
    assert refused.status_code == 409
    assert path.is_file()
