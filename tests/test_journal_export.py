"""Every saved daily record can be exported read-only; audit counts only."""

import json
from datetime import date

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def build_client(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.app.journal.shanghai_today", lambda: date(2026, 10, 4))
    database = Database(tmp_path / "torch_os.sqlite3")
    gateway = ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1)
    app = create_app(database, tmp_path / "vault", gateway, security_environ={})
    client = TestClient(app)
    client.get("/")
    return client, database


def test_journal_export_contains_all_fields_and_counts_only_audit(tmp_path, monkeypatch):
    client, database = build_client(tmp_path, monkeypatch)
    client.put("/api/journal/2026-10-02", json={"revision": 0, "content": "前两天", "mood": "平静"})
    client.put("/api/journal/2026-10-03", json={
        "revision": 0, "content": "今天完成了章节阅读。", "highlights": "记住方法",
        "obstacles": "注意力分散", "diet_summary": "三餐正常", "mood": "轻松",
        "next_action": "明天读第二章",
    })
    with database.connect() as connection:
        before = [dict(row) for row in connection.execute("SELECT * FROM journal_entries ORDER BY date")]

    payload = client.get("/api/export/journal.json").json()
    assert payload["schema"] == 1
    assert payload["counts"]["days"] == 2
    assert payload["counts"]["first_date"] == "2026-10-02"
    assert payload["counts"]["last_date"] == "2026-10-03"
    assert [entry["date"] for entry in payload["entries"]] == ["2026-10-03", "2026-10-02"]
    assert payload["entries"][0]["mood"] == "轻松"
    assert payload["entries"][0]["diet_summary"] == "三餐正常"

    markdown = client.get("/api/export/journal.md")
    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    text = markdown.text
    assert text.startswith("# 薪火OS 每日记录导出")
    assert "已保存 2 天记录（2026-10-02 至 2026-10-03）" in text
    assert "## 2026-10-03" in text
    assert "### 每日记录\n\n今天完成了章节阅读。" in text
    assert "### 情绪\n\n轻松" in text
    assert "### 饮食摘要\n\n三餐正常" in text

    with database.connect() as connection:
        after = [dict(row) for row in connection.execute("SELECT * FROM journal_entries ORDER BY date")]
        audit = [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type='journal_bulk_exported' ORDER BY id"
        )]
    assert before == after, "export must not change daily records"
    assert [json.loads(row["detail_json"]) for row in audit] == [
        {"format": "json", "days": 2},
        {"format": "markdown", "days": 2},
    ]
    assert "章节阅读" not in audit[0]["detail_json"]


def test_journal_export_handles_empty_state_and_requires_the_local_session(tmp_path, monkeypatch):
    client, _ = build_client(tmp_path, monkeypatch)
    payload = client.get("/api/export/journal.json").json()
    assert payload["counts"]["days"] == 0 and payload["entries"] == []
    assert "（还没有保存的每日记录）" in client.get("/api/export/journal.md").text

    anonymous = TestClient(client.app)
    assert anonymous.get("/api/export/journal.json").status_code == 401
    assert anonymous.get("/api/export/journal.md").status_code == 401
