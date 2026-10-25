"""记录模块：按真实 mtime 列"今天动过的保险库文件"。"""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app.activity import list_day_activity
from backend.app.application import create_app
from backend.app.db import Database

SHANGHAI = ZoneInfo("Asia/Shanghai")
TODAY = datetime.now(SHANGHAI).date().isoformat()


def make_app(tmp_path):
    app = create_app(
        Database(tmp_path / "activity.sqlite3"), tmp_path / "vault",
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return app, client


def write(path, text, *, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def stamp(day: str, hour: int = 12) -> float:
    return datetime.fromisoformat(f"{day}T{hour:02d}:00").replace(tzinfo=SHANGHAI).timestamp()


def test_lists_today_changes_by_mtime_newest_first(tmp_path):
    app, _ = make_app(tmp_path)
    vault = tmp_path / "vault"
    yesterday = (datetime.now(SHANGHAI).date() - timedelta(days=1)).isoformat()
    write(vault / "02-Life" / "journal" / f"{TODAY}.md", "# 今天的日记", mtime=stamp(TODAY, 21))
    write(vault / "03-Learning" / "读书笔记.md", "# 笔记", mtime=stamp(TODAY, 9))
    write(vault / "02-Life" / "旧文件.md", "# 旧", mtime=stamp(yesterday, 9))
    write(vault / "03-Learning" / "图片说明.txt", "非 md", mtime=stamp(TODAY, 10))

    entries = list_day_activity(app.state.database, vault, TODAY)
    assert [item["path"] for item in entries] == [
        f"02-Life/journal/{TODAY}.md", "03-Learning/读书笔记.md"]
    assert entries[0]["mtime"].startswith(TODAY) and entries[0]["size"] > 0
    assert entries[0]["title"] == f"{TODAY}"  # 未入索引：用文件名去后缀


def test_restricted_directories_never_show_up(tmp_path):
    app, _ = make_app(tmp_path)
    vault = tmp_path / "vault"
    write(vault / "00-System" / "待办（todos）.md", "- [ ] 买菜", mtime=stamp(TODAY, 20))
    write(vault / ".obsidian" / "workspace.json", "{}", mtime=stamp(TODAY, 20))
    assert list_day_activity(app.state.database, vault, TODAY) == []


def test_indexed_files_use_their_document_title(tmp_path):
    app, _ = make_app(tmp_path)
    vault = tmp_path / "vault"
    write(vault / "02-Life" / "恋爱日志" / "恋爱日志.md", "# 恋爱日志", mtime=stamp(TODAY, 8))
    with app.state.database.connect() as connection:
        connection.execute(
            "INSERT INTO documents(path,title,content,sha256,metadata_json,indexed_at) VALUES (?,?,?,?,?,?)",
            ("02-Life/恋爱日志/恋爱日志.md", "恋爱日志 · 正式标题", "", "x" * 64, "{}",
             f"{TODAY}T00:00:00+00:00"))
    entries = list_day_activity(app.state.database, vault, TODAY)
    assert entries[0]["title"] == "恋爱日志 · 正式标题"


def test_router_returns_day_payload_and_rejects_future_days(tmp_path):
    app, client = make_app(tmp_path)
    write(tmp_path / "vault" / "90-Inbox" / "灵感.md", "# 灵感", mtime=stamp(TODAY, 15))
    payload = client.get(f"/api/vault/activity?day={TODAY}").json()
    assert payload["day"] == TODAY and payload["count"] == 1
    assert payload["files"][0]["path"] == "90-Inbox/灵感.md"

    future = (datetime.now(SHANGHAI).date() + timedelta(days=1)).isoformat()
    assert client.get(f"/api/vault/activity?day={future}").status_code == 422
