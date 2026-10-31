"""今天的笔记（2026-10-26 起四库只读）：只读取当天的日记文件，生成/追加在 Obsidian。"""

import hashlib
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app import journal_note
from backend.app.application import create_app
from backend.app.db import Database

SHANGHAI = ZoneInfo("Asia/Shanghai")
TODAY = datetime.now(SHANGHAI).date().isoformat()
WEEKDAY = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[date.fromisoformat(TODAY).weekday()]


def make_client(tmp_path, *, read_only=False):
    environ = {"VAULT_READ_ONLY": "1"} if read_only else {}
    app = create_app(
        Database(tmp_path / "journal-note.sqlite3"), tmp_path / "vault",
        security_environ=environ, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client


def note_file(tmp_path):
    return tmp_path / "vault" / "00_人生路" / "01_日记" / f"{TODAY}.md"


def test_read_returns_state_and_rejects_invalid_days(tmp_path):
    client = make_client(tmp_path)
    missing = client.get("/api/journal/note", params={"day": TODAY}).json()
    assert missing["exists"] is False and missing["content"] == ""
    assert missing["sha256"] == ""
    assert missing["relpath"] == f"00_人生路/01_日记/{TODAY}.md"

    path = note_file(tmp_path)
    path.parent.mkdir(parents=True)
    raw = f"---\ntype: journal\ndate: {TODAY}\n---\n\n今天写下的正文。\n".encode("utf-8")
    path.write_bytes(raw)
    present = client.get("/api/journal/note", params={"day": TODAY}).json()
    assert present["exists"] is True and "今天写下的正文。" in present["content"]
    assert present["sha256"] == hashlib.sha256(raw).hexdigest()
    assert present["updated_at"]

    assert client.get("/api/journal/note", params={"day": "2099-01-01"}).status_code == 422
    assert client.get("/api/journal/note", params={"day": "not-a-day"}).status_code == 422


def test_delete_moves_a_journal_entry_to_the_note_trash(tmp_path):
    client = make_client(tmp_path)
    path = note_file(tmp_path)
    path.parent.mkdir(parents=True)
    raw = f"---\ntype: journal\ndate: {TODAY}\n---\n\n今天写下的正文。\n".encode("utf-8")
    path.write_bytes(raw)
    relative = f"00_人生路/01_日记/{TODAY}.md"
    # 阅读器里的 revision 来自 /api/notes，与 markdown_store 一样是内容哈希前 16 位。
    revision = hashlib.sha256(raw).hexdigest()[:16]

    stale = client.post("/api/journal/delete", json={
        "path": relative, "expected_revision": "0" * 16})
    assert stale.status_code == 409
    assert path.is_file()

    deleted = client.post("/api/journal/delete", json={
        "path": relative, "expected_revision": revision})
    assert deleted.status_code == 200, deleted.text
    payload = deleted.json()
    assert payload["deleted"] is True
    assert payload["path"] == relative
    assert payload["trash_path"].startswith(".torch-os/笔记回收站/")
    assert (tmp_path / "vault" / payload["trash_path"]).is_file()
    assert not path.exists()
    assert client.get("/api/journal/note", params={"day": TODAY}).json()["exists"] is False


def test_delete_refuses_non_journal_paths_missing_files_and_read_only_vault(tmp_path):
    client = make_client(tmp_path)
    other = client.post("/api/journal/delete", json={
        "path": "00_人生路/09_随笔/旧文.md"})
    assert other.status_code == 422
    template = client.post("/api/journal/delete", json={
        "path": "00_人生路/01_日记/_模板/日记模板.md"})
    assert template.status_code == 422
    legacy = client.post("/api/journal/delete", json={
        "path": f"02-Life/journal/{TODAY}.md"})
    assert legacy.status_code == 422

    missing = client.post("/api/journal/delete", json={
        "path": f"00_人生路/01_日记/{TODAY}.md"})
    assert missing.status_code == 404

    read_only_client = make_client(tmp_path / "readonly", read_only=True)
    path = tmp_path / "readonly" / "vault" / "00_人生路" / "01_日记" / f"{TODAY}.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\ntype: journal\n---\n\n正文\n", encoding="utf-8")
    refused = read_only_client.post("/api/journal/delete", json={
        "path": f"00_人生路/01_日记/{TODAY}.md"})
    assert refused.status_code == 409
    assert path.is_file()


def test_read_only_vault_still_reads_the_note(tmp_path):
    client = make_client(tmp_path, read_only=True)
    path = note_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("# 手写的今天\n", encoding="utf-8")
    payload = client.get("/api/journal/note", params={"day": TODAY}).json()
    assert payload["exists"] is True
    assert payload["content"] == "# 手写的今天\n"


def test_vault_template_file_overrides_the_builtin_skeleton(tmp_path):
    template = tmp_path / "vault" / "00-System" / "templates" / "日记模板（journal-template）.md"
    template.parent.mkdir(parents=True)
    template.write_text(
        "---\ntype: template\nname: 日记模板\n---\n\n# {{date}}（{{weekday}}）· 我的模板\n\n## 我的固定段落\n",
        encoding="utf-8",
    )

    content = journal_note.render_skeleton(tmp_path / "vault", TODAY)
    # 模板自己的 front matter 不被继承，笔记固定用 journal 的。
    assert content.startswith(f"---\ntype: journal\ndate: {TODAY}\n---\n")
    assert "type: template" not in content and "name: 日记模板" not in content
    assert f"# {TODAY}（{WEEKDAY}）· 我的模板" in content
    assert "## 我的固定段落" in content
    assert "每日三问" not in content
