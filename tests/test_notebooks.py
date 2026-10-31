"""人生路自定义本子：本子 = 00_人生路 下的文件夹，前端创建、后端落盘。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import vault_layout
from backend.app.application import create_app
from backend.app.db import Database


def make_client(tmp_path: Path, *, read_only: bool = False):
    vault = tmp_path / "vault"
    database = Database(tmp_path / "notebooks.sqlite3")
    environ = {"VAULT_READ_ONLY": "1"} if read_only else {}
    app = create_app(database, vault, security_environ=environ,
                     env_file=tmp_path / ".env", enable_dsh=False)
    client = TestClient(app)
    client.get("/")
    return client, database, vault


def test_create_notebook_creates_a_real_folder_and_can_hold_notes(tmp_path):
    client, database, vault = make_client(tmp_path)
    created = client.post("/api/notebooks", json={"pillar": "life", "name": "读书摘记"})
    assert created.status_code == 200, created.text
    notebook = created.json()
    assert notebook["key"] == "custom:读书摘记"
    assert notebook["path"] == "00_人生路/读书摘记"
    assert (vault / "00_人生路" / "读书摘记").is_dir()

    listing = client.get("/api/notebooks").json()
    assert [item["key"] for item in listing["items"]] == ["custom:读书摘记"]

    overview = client.get("/api/pillars/life/overview").json()
    row = next(item for item in overview["custom_notebooks"] if item["key"] == notebook["key"])
    assert row["title"] == "读书摘记" and row["count"] == 0 and row["latest"] is None

    chapters = client.get("/api/writing/chapters").json()["life"]
    assert any(item["key"] == notebook["key"] for item in chapters)

    note = client.post("/api/writing/notes", json={
        "chapter": notebook["key"], "title": "第一条",
        "body": "本子里的正文。", "tags": ["读书"],
    })
    assert note.status_code == 200, note.text
    saved = note.json()
    assert saved["chapter"] == notebook["key"]
    assert saved["path"].startswith("00_人生路/读书摘记/")
    assert (vault / saved["path"]).is_file()

    # 本子内的笔记能被列表、概览和编辑器再次读到。
    listed = client.get("/api/pillars/life/notes",
                        params={"chapter": notebook["key"]}).json()
    assert [item["path"] for item in listed["items"]] == [saved["path"]]
    overview = client.get("/api/pillars/life/overview").json()
    row = next(item for item in overview["custom_notebooks"] if item["key"] == notebook["key"])
    assert row["count"] == 1 and row["latest"]["title"] == "第一条"
    listing = client.get("/api/notebooks").json()
    listed_book = next(item for item in listing["items"] if item["key"] == notebook["key"])
    assert listed_book["count"] == 1 and listed_book["latest"]["title"] == "第一条"

    updated = client.put("/api/writing/notes", json={
        "path": saved["path"], "expected_revision": saved["revision"],
        "body": "改过的正文。",
    })
    assert updated.status_code == 200, updated.text
    assert "改过的正文" in updated.json()["body"]

    with database.connect() as connection:
        audit = [dict(row) for row in connection.execute(
            "SELECT event_type, detail_json FROM audit_events ORDER BY id")]
    created_audit = next(item for item in audit if item["event_type"] == "notebook_created")
    assert set(json.loads(created_audit["detail_json"])) == {"pillar", "name", "path"}
    assert "本子里的正文" not in created_audit["detail_json"]


@pytest.mark.parametrize("name", [
    "", "   ", "../外面", "a/b", "a:b", ".hidden", "_系统", "02_反思复盘",
    "日记", "反思复盘", "目标", "主题笔记", "知识库", "CON", "超" * 49,
])
@pytest.mark.parametrize("pillar", ["life", "knowledge"])
def test_invalid_notebook_names_are_rejected_without_touching_disk(tmp_path, pillar, name):
    client, _, vault = make_client(tmp_path)
    response = client.post("/api/notebooks",
                           json={"pillar": pillar, "name": name})
    assert response.status_code == 422, (pillar, name, response.text)
    assert vault_layout.list_custom_notebooks(vault, pillar) == []


def test_duplicate_names_are_per_pillar_and_knowledge_notebooks_are_supported(tmp_path):
    client, _, vault = make_client(tmp_path)
    assert client.post("/api/notebooks", json={"name": "慢慢写"}).status_code == 200
    duplicate = client.post("/api/notebooks", json={"name": "慢慢写"})
    assert duplicate.status_code == 409

    knowledge = client.post("/api/notebooks",
                            json={"pillar": "knowledge", "name": "慢慢写"})
    assert knowledge.status_code == 200, knowledge.text
    created = knowledge.json()
    assert created["key"] == "custom:knowledge:慢慢写"
    assert created["pillar"] == "knowledge"
    assert created["path"] == "03_知识库/慢慢写"
    assert (vault / "03_知识库" / "慢慢写").is_dir()
    listing = client.get("/api/notebooks", params={"pillar": "knowledge"}).json()
    assert [item["key"] for item in listing["items"]] == [created["key"]]
    assert client.get("/api/notebooks", params={"pillar": "self"}).status_code == 422


def test_knowledge_custom_notebooks_can_hold_and_move_notes(tmp_path):
    client, _, vault = make_client(tmp_path)
    first = client.post("/api/notebooks", json={
        "pillar": "knowledge", "name": "甲卡"}).json()
    second = client.post("/api/notebooks", json={
        "pillar": "knowledge", "name": "乙卡"}).json()
    chapters = client.get("/api/writing/chapters").json()["knowledge"]
    keys = {item["key"] for item in chapters}
    assert first["key"] in keys and second["key"] in keys

    created = client.post("/api/writing/notes", json={
        "chapter": first["key"], "title": "知识卡片", "body": "正文",
    })
    assert created.status_code == 200, created.text
    note = created.json()
    assert note["chapter"] == first["key"]
    assert note["pillar"] == "knowledge"
    assert note["path"].startswith("03_知识库/甲卡/")
    assert (vault / note["path"]).is_file()

    listed = client.get("/api/pillars/knowledge/notes",
                        params={"chapter": first["key"]}).json()
    assert [item["path"] for item in listed["items"]] == [note["path"]]
    overview = client.get("/api/pillars/knowledge/overview").json()
    row = next(item for item in overview["custom_notebooks"]
               if item["key"] == first["key"])
    assert row["count"] == 1 and row["latest"]["title"] == "知识卡片"

    moved = client.post("/api/writing/notes/move", json={
        "path": note["path"], "target_chapter": second["key"],
        "expected_revision": note["revision"],
    })
    assert moved.status_code == 200, moved.text
    moved_note = moved.json()
    assert moved_note["chapter"] == second["key"]
    assert moved_note["path"].startswith("03_知识库/乙卡/")

    fixed = client.post("/api/writing/notes/move", json={
        "path": moved_note["path"], "target_chapter": "topic",
        "expected_revision": moved_note["revision"],
    })
    assert fixed.status_code == 200, fixed.text
    fixed_note = fixed.json()
    assert fixed_note["chapter"] == "topic"
    assert fixed_note["path"].startswith("03_知识库/01_主题笔记/")

    deleted = client.post("/api/writing/notes/delete", json={
        "path": fixed_note["path"], "expected_revision": fixed_note["revision"],
    })
    assert deleted.status_code == 200, deleted.text
    assert (vault / deleted.json()["trash_path"]).is_file()


def test_delete_custom_notebook_moves_the_whole_folder_to_trash(tmp_path):
    client, database, vault = make_client(tmp_path)
    created = client.post("/api/notebooks", json={
        "pillar": "knowledge", "name": "待删本子"}).json()
    note = client.post("/api/writing/notes", json={
        "chapter": created["key"], "title": "留在本子里", "body": "正文",
    }).json()

    no_confirm = client.post("/api/notebooks/delete", json={
        "pillar": "knowledge", "key": created["key"]})
    assert no_confirm.status_code == 422
    wrong_pillar = client.post("/api/notebooks/delete", json={
        "pillar": "life", "key": created["key"], "confirm": True})
    assert wrong_pillar.status_code == 422
    protected_inbox = client.post("/api/notebooks/delete", json={
        "pillar": "knowledge", "key": "inbox", "confirm": True})
    assert protected_inbox.status_code == 422

    deleted = client.post("/api/notebooks/delete", json={
        "pillar": "knowledge", "key": created["key"], "confirm": True})
    assert deleted.status_code == 200, deleted.text
    payload = deleted.json()
    assert payload["deleted"] is True and payload["count"] == 1
    assert not (vault / "03_知识库" / "待删本子").exists()
    trash_folder = vault / payload["trash_path"]
    assert trash_folder.is_dir()
    assert (trash_folder / note["path"].rsplit("/", 1)[-1]).is_file()
    manifest = json.loads((vault / payload["trash_batch"] / "manifest.json")
                          .read_text(encoding="utf-8"))
    assert manifest["original_path"] == "03_知识库/待删本子"
    assert manifest["note_count"] == 1

    listing = client.get("/api/notebooks", params={"pillar": "knowledge"}).json()
    assert all(item["key"] != created["key"] for item in listing["items"])
    with database.connect() as connection:
        events = [row["event_type"] for row in connection.execute(
            "SELECT event_type FROM audit_events ORDER BY id")]
    assert "notebook_deleted" in events


def test_delete_life_custom_notebook_uses_its_own_trash_path(tmp_path):
    client, _, vault = make_client(tmp_path)
    created = client.post("/api/notebooks", json={"name": "待删人生本"}).json()
    assert created["key"] == "custom:待删人生本"
    assert (vault / "00_人生路" / "待删人生本").is_dir()

    protected_essay = client.post("/api/notebooks/delete", json={
        "pillar": "life", "key": "essay", "confirm": True})
    assert protected_essay.status_code == 422

    deleted = client.post("/api/notebooks/delete", json={
        "pillar": "life", "key": created["key"], "confirm": True})
    assert deleted.status_code == 200, deleted.text
    payload = deleted.json()
    assert payload["path"] == "00_人生路/待删人生本"
    assert payload["trash_path"].startswith(".torch-os/本子回收站/")
    assert not (vault / "00_人生路" / "待删人生本").exists()
    assert (vault / payload["trash_path"]).is_dir()


def test_delete_builtin_life_notebook_hides_it_and_moves_all_chapters(tmp_path):
    client, database, vault = make_client(tmp_path)
    first = client.post("/api/writing/notes", json={
        "chapter": "reflection", "title": "一条反思", "body": "正文"}).json()
    second = client.post("/api/writing/notes", json={
        "chapter": "review_week", "title": "周复盘", "body": "正文"}).json()
    overview = client.get("/api/pillars/life/overview").json()
    assert any(item["key"] == "reflection" for item in overview["chapters"])

    deleted = client.post("/api/notebooks/delete", json={
        "pillar": "life", "key": "reflection", "confirm": True})
    assert deleted.status_code == 200, deleted.text
    payload = deleted.json()
    assert payload["builtin"] is True and payload["count"] == 2
    assert not (vault / first["path"]).exists()
    assert not (vault / second["path"]).exists()
    assert (vault / payload["trash_path"]).is_dir()
    manifest = json.loads((vault / payload["trash_batch"] / "manifest.json")
                          .read_text(encoding="utf-8"))
    assert manifest["builtin"] is True
    assert manifest["chapters"] == ["reflection", "review_week", "review_month"]

    overview = client.get("/api/pillars/life/overview").json()
    assert "reflection" in overview["deleted_notebooks"]
    assert all(item["key"] not in {"reflection", "review_week", "review_month"}
               for item in overview["chapters"])
    chapters = client.get("/api/writing/chapters").json()["life"]
    assert all(item["key"] not in {"reflection", "review_week", "review_month"}
               for item in chapters)
    blocked = client.post("/api/writing/notes", json={
        "chapter": "reflection", "title": "不能再写", "body": "x"})
    assert blocked.status_code == 422

    # 章节轨/分组清单也过滤；重启 bootstrap 不再重建被删章节。
    manifest_api = client.get("/api/pillars").json()
    life = next(item for item in manifest_api["pillars"] if item["key"] == "life")
    assert all(item["key"] not in {"reflection", "review_week", "review_month"}
               for item in life["chapters"])
    assert all(group["key"] != "review" for group in life["groups"])
    created = vault_layout.ensure_layout(vault)
    assert "00_人生路/02_反思复盘/01_反思" not in created
    assert not (vault / "00_人生路" / "02_反思复盘" / "01_反思").exists()

    with database.connect() as connection:
        rows = [dict(row) for row in connection.execute(
            "SELECT event_type, detail_json FROM audit_events ORDER BY id")]
    detail = json.loads(next(row for row in rows
                             if row["event_type"] == "notebook_deleted")["detail_json"])
    assert detail["builtin"] is True and detail["pillar"] == "life"


def test_delete_builtin_knowledge_notebook_but_inbox_is_protected(tmp_path):
    client, _, vault = make_client(tmp_path)
    created = client.post("/api/writing/notes", json={
        "chapter": "topic", "title": "主题卡片", "body": "正文"}).json()
    protected = client.post("/api/notebooks/delete", json={
        "pillar": "knowledge", "key": "inbox", "confirm": True})
    assert protected.status_code == 422

    deleted = client.post("/api/notebooks/delete", json={
        "pillar": "knowledge", "key": "topic", "confirm": True})
    assert deleted.status_code == 200, deleted.text
    assert not (vault / created["path"]).exists()
    overview = client.get("/api/pillars/knowledge/overview").json()
    assert "topic" in overview["deleted_notebooks"]
    chapters = client.get("/api/writing/chapters").json()["knowledge"]
    assert all(item["key"] != "topic" for item in chapters)
    assert client.post("/api/writing/notes", json={
        "chapter": "topic", "title": "不能再写", "body": "x"}).status_code == 422
    created_dirs = vault_layout.ensure_layout(vault)
    assert "03_知识库/01_主题笔记" not in created_dirs
    assert not (vault / "03_知识库" / "01_主题笔记").exists()


def test_delete_journal_blocks_journal_writes_until_restored(tmp_path):
    client, _, _ = make_client(tmp_path)
    deleted = client.post("/api/notebooks/delete", json={
        "pillar": "life", "key": "journal", "confirm": True})
    assert deleted.status_code == 200, deleted.text
    from backend.app.journal_review import shanghai_today
    response = client.post("/api/journal/write", json={
        "day": shanghai_today().isoformat(),
        "content": "---\ntype: journal\n---\n\n今天",
        "confirm": True,
    })
    assert response.status_code == 422


def test_writing_to_a_missing_custom_notebook_is_refused(tmp_path):
    client, _, _ = make_client(tmp_path)
    response = client.post("/api/writing/notes", json={
        "chapter": "custom:还不存在", "title": "无主笔记", "body": "x",
    })
    assert response.status_code == 422
    knowledge = client.post("/api/writing/notes", json={
        "chapter": "custom:knowledge:还不存在", "title": "无主笔记", "body": "x",
    })
    assert knowledge.status_code == 422


def test_read_only_vault_refuses_notebook_creation_and_deletion(tmp_path):
    client, _, vault = make_client(tmp_path, read_only=True)
    life = client.post("/api/notebooks", json={"name": "不该创建"})
    assert life.status_code == 409
    assert not (vault / "00_人生路" / "不该创建").exists()
    knowledge = client.post("/api/notebooks",
                            json={"pillar": "knowledge", "name": "不该创建"})
    assert knowledge.status_code == 409
    assert not (vault / "03_知识库" / "不该创建").exists()
    delete = client.post("/api/notebooks/delete", json={
        "pillar": "knowledge", "key": "custom:knowledge:不该创建",
        "confirm": True})
    assert delete.status_code == 409


def test_ops_task_notebook_is_a_protected_builtin(tmp_path):
    assert vault_layout.builtin_notebook_title("ops", "task") == "待办事项"
    assert vault_layout.builtin_notebook_chapters("ops", "task") == ("task",)
    assert vault_layout.is_protected_notebook("ops", "task") is True
    assert vault_layout.can_delete_builtin_notebook("ops", "task") is False
    client, _, _ = make_client(tmp_path)
    response = client.post("/api/notebooks/delete", json={
        "pillar": "ops", "key": "task", "confirm": True})
    assert response.status_code == 422
