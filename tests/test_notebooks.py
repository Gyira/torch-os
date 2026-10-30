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
    "日记", "反思复盘", "目标", "CON", "超" * 49,
])
def test_invalid_notebook_names_are_rejected_without_touching_disk(tmp_path, name):
    client, _, vault = make_client(tmp_path)
    response = client.post("/api/notebooks", json={"pillar": "life", "name": name})
    assert response.status_code == 422, (name, response.text)
    assert vault_layout.list_custom_notebooks(vault) == []


def test_duplicate_and_non_life_notebooks_are_rejected(tmp_path):
    client, _, _ = make_client(tmp_path)
    assert client.post("/api/notebooks", json={"name": "慢慢写"}).status_code == 200
    duplicate = client.post("/api/notebooks", json={"name": "慢慢写"})
    assert duplicate.status_code == 409
    assert client.get("/api/notebooks", params={"pillar": "knowledge"}).status_code == 422
    assert client.post("/api/notebooks",
                       json={"pillar": "knowledge", "name": "知识"}).status_code == 422


def test_writing_to_a_missing_custom_notebook_is_refused(tmp_path):
    client, _, _ = make_client(tmp_path)
    response = client.post("/api/writing/notes", json={
        "chapter": "custom:还不存在", "title": "无主笔记", "body": "x",
    })
    assert response.status_code == 422


def test_read_only_vault_refuses_notebook_creation(tmp_path):
    client, _, vault = make_client(tmp_path, read_only=True)
    response = client.post("/api/notebooks", json={"name": "不该创建"})
    assert response.status_code == 409
    assert not (vault / "00_人生路" / "不该创建").exists()
