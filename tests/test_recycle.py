"""全局回收中心：四库文档删除后统一列出、恢复与彻底删除。"""

from __future__ import annotations

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database

SHANGHAI = ZoneInfo("Asia/Shanghai")
TODAY = datetime.now(SHANGHAI).date().isoformat()

TASK_CONFIG = """---
type: tasks
清单:
  - 名称: 收集箱
    颜色: "#3b82f6"
---

# 任务

## 收集箱
"""


def make_client(tmp_path, *, read_only: bool = False, vault=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    vault = vault or (tmp_path / "vault")
    vault.mkdir(parents=True, exist_ok=True)
    database = Database(tmp_path / "recycle.sqlite3")
    environ = {"VAULT_READ_ONLY": "1"} if read_only else {}
    app = create_app(
        database, vault, security_environ=environ,
        env_file=tmp_path / ".env", enable_dsh=False)
    client = TestClient(app)
    client.get("/")
    return client, database, vault


def seed_task_config(vault):
    path = vault / "02_生活运营" / "00_待办事项" / "_系统" / "任务（tasks）.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TASK_CONFIG, encoding="utf-8")


def delete_one_task(client):
    seeded = client.post("/api/time/tasks", json={"title": "写方案"})
    assert seeded.status_code == 200, seeded.text
    board = client.get("/api/time/tasks").json()
    task = next(item for item in board["tasks"] if item["title"] == "写方案")
    deleted = client.post("/api/time/tasks/delete", json={
        "key": task["key"], "revision": board["revision"]})
    assert deleted.status_code == 200, deleted.text


def delete_journal(vault, client):
    path = vault / "00_人生路" / "01_日记" / f"{TODAY}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = f"---\ntype: journal\ndate: {TODAY}\n---\n\n今天的正文。\n".encode("utf-8")
    path.write_bytes(raw)
    revision = hashlib.sha256(raw).hexdigest()[:16]
    deleted = client.post("/api/journal/delete", json={
        "path": f"00_人生路/01_日记/{TODAY}.md", "expected_revision": revision})
    assert deleted.status_code == 200, deleted.text


def groups_by_key(payload):
    return {group["key"]: group for group in payload["groups"]}


def test_recycle_lists_deleted_notes_journals_and_tasks_by_pillar_and_restores_them(tmp_path):
    client, _database, vault = make_client(tmp_path)
    note = client.post("/api/writing/notes", json={
        "chapter": "reflection", "title": "旧反思", "body": "反思正文。",
    }).json()
    deleted_note = client.post("/api/writing/notes/delete", json={
        "path": note["path"], "expected_revision": note["revision"]})
    assert deleted_note.status_code == 200, deleted_note.text
    delete_journal(vault, client)
    seed_task_config(vault)
    delete_one_task(client)

    listing = client.get("/api/recycle")
    assert listing.status_code == 200, listing.text
    payload = listing.json()
    assert payload["total"] == 3
    groups = groups_by_key(payload)
    assert [group["key"] for group in payload["groups"]][:4] == [
        "life", "self", "ops", "knowledge"]
    life = groups["life"]
    assert {item["kind"] for item in life["items"]} == {"note", "journal"}
    reflection = next(item for item in life["items"] if item["kind"] == "note")
    assert reflection["title"] == "旧反思"
    assert reflection["original_path"] == note["path"]
    journal = next(item for item in life["items"] if item["kind"] == "journal")
    assert journal["original_path"] == f"00_人生路/01_日记/{TODAY}.md"
    assert groups["ops"]["items"][0]["kind"] == "task"
    assert groups["self"]["count"] == 0

    restored = client.post("/api/recycle/restore", json={
        "paths": [item["trash_path"] for item in life["items"]] + [
            groups["ops"]["items"][0]["trash_path"]],
    })
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored"] == 3
    assert (vault / note["path"]).is_file()
    assert (vault / f"00_人生路/01_日记/{TODAY}.md").is_file()
    assert (vault / "02_生活运营" / "00_待办事项" / "收集箱" / "写方案.md").is_file()
    assert client.get("/api/recycle").json()["total"] == 0


def test_recycle_purge_really_removes_files_and_task_trash_entries(tmp_path):
    client, _database, vault = make_client(tmp_path)
    note = client.post("/api/writing/notes", json={
        "chapter": "topic", "title": "要清掉的知识", "body": "正文。",
    }).json()
    client.post("/api/writing/notes/delete", json={
        "path": note["path"], "expected_revision": note["revision"]})
    seed_task_config(vault)
    delete_one_task(client)

    payload = client.get("/api/recycle").json()
    groups = groups_by_key(payload)
    knowledge = groups["knowledge"]["items"][0]
    task = groups["ops"]["items"][0]
    trash_paths = [knowledge["trash_path"], task["trash_path"]]
    assert (vault / knowledge["trash_path"]).is_file()
    assert (vault / task["trash_path"]).is_file()

    purged = client.post("/api/recycle/purge", json={"paths": trash_paths})
    assert purged.status_code == 200, purged.text
    assert purged.json()["deleted"] == 2
    assert not (vault / knowledge["trash_path"]).exists()
    assert not (vault / task["trash_path"]).exists()
    assert not (vault / note["path"]).exists()
    assert client.get("/api/recycle").json()["total"] == 0
    assert client.get("/api/time/trash").json()["items"] == []


def test_recycle_lists_deleted_task_lists_and_restores_their_definition(tmp_path):
    client, _database, vault = make_client(tmp_path)
    seed_task_config(vault)
    board = client.get("/api/time/tasks").json()
    created = client.post("/api/time/lists", json={
        "name": "临时清单", "revision": board["revision"]})
    assert created.status_code == 200, created.text
    board = client.get("/api/time/tasks").json()
    task = client.post("/api/time/tasks", json={
        "title": "临时任务", "list_name": "临时清单",
        "revision": board["revision"]})
    assert task.status_code == 200, task.text
    board = client.get("/api/time/tasks").json()
    removed = client.post("/api/time/lists/delete", json={
        "name": "临时清单", "revision": board["revision"]})
    assert removed.status_code == 200, removed.text

    ops = groups_by_key(client.get("/api/recycle").json())["ops"]
    folder = next(item for item in ops["items"] if item["kind"] == "task_folder")
    assert folder["count"] == 1

    restored = client.post("/api/recycle/restore", json={
        "paths": [folder["trash_path"]]})
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored"] >= 1
    assert restored.json()["lists_restored"] == 1
    assert (vault / "02_生活运营" / "00_待办事项" / "临时清单").is_dir()
    board = client.get("/api/time/tasks").json()
    assert any(item["name"] == "临时清单" for item in board["lists"])
    assert any(item["title"] == "临时任务" for item in board["tasks"])


def test_recycle_restores_a_manifestless_note_batch_by_its_files_layout(tmp_path):
    client, _database, vault = make_client(tmp_path)
    batch = vault / ".torch-os" / "笔记回收站" / "20261030T120000000000Z-test"
    trash_file = batch / "files" / "00_人生路" / "09_随笔" / "旧文.md"
    trash_file.parent.mkdir(parents=True)
    trash_file.write_text("---\ntitle: 旧文\n---\n\n正文\n", encoding="utf-8")

    payload = client.get("/api/recycle").json()
    life = groups_by_key(payload)["life"]
    assert life["count"] == 1
    item = life["items"][0]
    assert item["original_path"] == "00_人生路/09_随笔/旧文.md"
    assert item["title"] == "旧文"

    restored = client.post("/api/recycle/restore", json={"paths": [item["trash_path"]]})
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored"] == 1
    assert (vault / "00_人生路" / "09_随笔" / "旧文.md").is_file()
    assert not batch.exists()


def test_recycle_refuses_paths_outside_the_trash_and_read_only_actions(tmp_path):
    client, _database, vault = make_client(tmp_path)
    note = client.post("/api/writing/notes", json={
        "chapter": "essay", "title": "留在原地", "body": "正文。",
    }).json()
    outside = client.post("/api/recycle/purge", json={"paths": [note["path"]]})
    assert outside.status_code == 200, outside.text
    assert outside.json()["deleted"] == 0
    assert outside.json()["skipped"]
    assert (vault / note["path"]).is_file()

    client.post("/api/writing/notes/delete", json={
        "path": note["path"], "expected_revision": note["revision"]})
    item = groups_by_key(client.get("/api/recycle").json())["life"]["items"][0]
    read_only_client, _db, _vault = make_client(
        tmp_path / "readonly", read_only=True, vault=vault)
    listed = read_only_client.get("/api/recycle")
    assert listed.status_code == 200 and listed.json()["total"] == 1
    refused_restore = read_only_client.post("/api/recycle/restore", json={
        "paths": [item["trash_path"]]})
    assert refused_restore.status_code == 409
    refused_purge = read_only_client.post("/api/recycle/purge", json={
        "paths": [item["trash_path"]]})
    assert refused_purge.status_code == 409
    assert (vault / item["trash_path"]).is_file()
