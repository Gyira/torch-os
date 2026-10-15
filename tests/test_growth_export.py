"""Habits/study export stays a read-only snapshot with count-only audit."""

import json
from datetime import date

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def build_client(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.app.habits.shanghai_today", lambda: date(2026, 10, 4))
    monkeypatch.setattr("backend.app.learning.shanghai_today", lambda: date(2026, 10, 4))
    database = Database(tmp_path / "torch_os.sqlite3")
    gateway = ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1)
    app = create_app(database, tmp_path / "vault", gateway, security_environ={})
    client = TestClient(app)
    client.get("/")
    return client, database


def _snapshot(database):
    with database.connect() as connection:
        return {
            "habits": [dict(row) for row in connection.execute(
                "SELECT id,status,updated_at FROM habit_definitions ORDER BY id"
            )],
            "entries": [dict(row) for row in connection.execute(
                "SELECT habit_id,date,status,updated_at FROM habit_entries ORDER BY habit_id, date"
            )],
            "subjects": [dict(row) for row in connection.execute(
                "SELECT id,status,updated_at FROM study_subjects ORDER BY id"
            )],
            "sessions": [dict(row) for row in connection.execute(
                "SELECT id,minutes,revision FROM study_sessions ORDER BY id"
            )],
        }


def test_growth_export_nests_entries_and_writes_counts_only(tmp_path, monkeypatch):
    client, database = build_client(tmp_path, monkeypatch)
    habit = client.post("/api/habits", json={
        "title": "导出习惯", "minimum_action": "一页", "start_date": "2026-09-28",
    }).json()
    client.put(f"/api/habits/{habit['id']}/entries/2026-10-03", json={
        "revision": 0, "status": "done", "note": "睡前读完",
    })
    client.put(f"/api/habits/{habit['id']}/entries/2026-10-04", json={"revision": 0, "status": "skipped"})
    subject = client.post("/api/learning/subjects", json={"title": "导出科目", "weekly_target_minutes": 120}).json()
    client.post("/api/learning/sessions", json={
        "subject_id": subject["id"], "study_date": "2026-10-03", "minutes": 30,
        "content": "第一章", "next_step": "第二章",
        "request_key": "00000000-0000-4000-8000-000000000010",
    })
    before = _snapshot(database)

    payload = client.get("/api/export/growth.json").json()
    assert payload["schema"] == 1
    counts = payload["counts"]
    assert counts["habits"] == 1 and counts["habit_entries"] == 2
    assert counts["subjects"] == 1 and counts["sessions"] == 1
    assert counts["total_study_minutes"] == 30
    assert counts["habits_by_status"] == {"active": 1, "archived": 0}
    assert counts["entries_by_status"] == {"done": 1, "skipped": 1, "unrecorded": 0}
    assert counts["subjects_by_status"] == {"active": 1, "archived": 0}
    assert [entry["status"] for entry in payload["habits"][0]["entries"]] == ["done", "skipped"]
    assert payload["habits"][0]["entries"][0]["note"] == "睡前读完"
    assert payload["subjects"][0]["total_minutes"] == 30
    assert payload["subjects"][0]["sessions"][0]["content"] == "第一章"
    assert "request_hash" not in payload["subjects"][0]["sessions"][0]

    markdown = client.get("/api/export/growth.md")
    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    text = markdown.text
    assert text.startswith("# 薪火OS 习惯与学习导出")
    assert "## 习惯：导出习惯（进行中）" in text
    assert "  - 2026-10-03 · 完成 · 备注：睡前读完" in text
    assert "  - 2026-10-04 · 主动休息" in text
    assert "## 学习科目：导出科目（进行中 · 每周目标 120 分钟）" in text
    assert "  - 2026-10-03 · 30 分钟 · 第一章 · 下一步：第二章" in text

    assert _snapshot(database) == before, "export must not change growth records"
    with database.connect() as connection:
        audit = [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type='growth_exported' ORDER BY id"
        )]
    assert [json.loads(row["detail_json"]) for row in audit] == [
        {"format": "json", "habits": 1, "habit_entries": 2, "subjects": 1, "sessions": 1},
        {"format": "markdown", "habits": 1, "habit_entries": 2, "subjects": 1, "sessions": 1},
    ]
    assert "导出习惯" not in audit[0]["detail_json"]


def test_growth_export_handles_empty_state_and_requires_the_local_session(tmp_path, monkeypatch):
    client, _ = build_client(tmp_path, monkeypatch)
    payload = client.get("/api/export/growth.json").json()
    assert payload["counts"]["habits"] == 0 and payload["counts"]["sessions"] == 0
    text = client.get("/api/export/growth.md").text
    assert "（还没有习惯）" in text and "（还没有学习科目）" in text

    anonymous = TestClient(client.app)
    assert anonymous.get("/api/export/growth.json").status_code == 401
    assert anonymous.get("/api/export/growth.md").status_code == 401
