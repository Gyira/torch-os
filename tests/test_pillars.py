"""四库浏览、捕获、笔记读写与搜索。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def make_client(tmp_path, *, read_only=False):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    environ = {"VAULT_READ_ONLY": "1"} if read_only else {}
    app = create_app(
        Database(tmp_path / "torch_os.sqlite3"), vault,
        ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1),
        security_environ=environ, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client, vault


def test_manifest_lists_four_pillars(tmp_path):
    client, _ = make_client(tmp_path)
    payload = client.get("/api/pillars").json()
    assert payload["layout_version"] == "pillars-v1"
    assert [item["key"] for item in payload["pillars"]] == [
        "life", "self", "ops", "knowledge",
    ]
    inbox = next(chapter for pillar in payload["pillars"] for chapter in pillar["chapters"]
                 if chapter["key"] == "inbox")
    assert inbox["path"] == "03_知识库/00_收件箱"


def test_capture_writes_inbox_and_notes_are_listed(tmp_path):
    client, vault = make_client(tmp_path)
    created = client.post("/api/capture", json={
        "title": "一条灵感", "body": "值得记住的想法", "tags": ["灵感"],
    })
    assert created.status_code == 200, created.text
    note = created.json()
    assert note["pillar"] == "knowledge"
    assert note["chapter"] == "inbox"
    assert note["status"] == "inbox"
    assert (vault / note["path"]).is_file()

    listed = client.get("/api/pillars/knowledge/notes?chapter=inbox").json()["items"]
    assert [item["title"] for item in listed] == ["一条灵感"]
    overview = client.get("/api/pillars/knowledge/overview").json()
    assert overview["inbox_count"] == 1


def test_read_update_move_and_search(tmp_path):
    client, vault = make_client(tmp_path)
    note = client.post("/api/capture", json={"title": "待提炼", "body": "原始内容"}).json()
    read = client.get("/api/notes", params={"path": note["path"]}).json()
    assert read["body"] == "原始内容"
    updated = client.post("/api/notes/update", params={"path": note["path"]}, json={
        "body": "提炼后的内容", "tags": ["已整理"], "status": "active",
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["body"] == "提炼后的内容"
    assert "已整理" in updated.json()["tags"]

    moved = client.post("/api/notes/move", json={
        "path": note["path"], "chapter": "concept",
    })
    assert moved.status_code == 200, moved.text
    moved_note = moved.json()
    assert moved_note["chapter"] == "concept"
    assert moved_note["path"].startswith("03_知识库/04_概念卡片/")
    assert (vault / moved_note["path"]).is_file()

    found = client.get("/api/search", params={"q": "提炼"}).json()["items"]
    assert any(item["path"] == moved_note["path"] for item in found)


def test_capture_and_update_respect_read_only(tmp_path):
    client, _ = make_client(tmp_path, read_only=True)
    blocked = client.post("/api/capture", json={"title": "写不进去", "body": "只读"})
    assert blocked.status_code == 409


def test_create_does_not_overwrite_same_title(tmp_path):
    client, vault = make_client(tmp_path)
    first = client.post("/api/capture", json={"title": "同名", "body": "一"}).json()
    second = client.post("/api/capture", json={"title": "同名", "body": "二"}).json()
    assert first["path"] != second["path"]
    assert (vault / first["path"]).is_file() and (vault / second["path"]).is_file()


def test_weekly_and_monthly_review_skeletons_are_idempotent(tmp_path):
    client, vault = make_client(tmp_path)
    journal_dir = vault / "00_人生路" / "01_日记"
    journal_dir.mkdir(parents=True)
    (journal_dir / "2026-10-20.md").write_text(
        "---\ntype: journal\ndate: 2026-10-20\n---\n\n今天很充实，完成了复盘，心情平静。\n",
        encoding="utf-8")
    weekly = client.post("/api/reviews/weekly", json={"day": "2026-10-20"})
    assert weekly.status_code == 200, weekly.text
    assert weekly.json()["created"] is True
    assert "2026-W43" in weekly.json()["path"]
    again = client.post("/api/reviews/weekly", json={"day": "2026-10-20"})
    assert again.json()["created"] is False

    monthly = client.post("/api/reviews/monthly", json={"month": "2026-10"})
    assert monthly.status_code == 200, monthly.text
    assert monthly.json()["path"].startswith("00_人生路/05_月复盘/")
    assert "2026-10" in monthly.json()["body"]
