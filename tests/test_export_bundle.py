"""The readable export bundle is a read-only ZIP of every domain export."""

import io
import json
import zipfile
from datetime import date

from fastapi.testclient import TestClient

from backend.app import pillar_notes, structured_notes, task_notes
from backend.app.application import create_app
from backend.app.db import Database


def build_client(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.app.habits.shanghai_today", lambda: date(2026, 10, 4))
    database = Database(tmp_path / "torch_os.sqlite3")
    vault = tmp_path / "vault"
    app = create_app(database, vault, security_environ={})
    client = TestClient(app)
    client.get("/")
    return client, database, vault


EXPECTED_MEMBERS = {
    "README.txt", "memories.md", "memories.json", "planning.md", "planning.json",
    "growth.md", "growth.json", "conversations.md", "conversations.json",
    "ai.md", "ai.json", "journal.md", "journal.json", "body.csv", "body.json",
}


def test_export_bundle_contains_every_section_and_counts_only_audit(tmp_path, monkeypatch):
    client, database, vault = build_client(tmp_path, monkeypatch)
    client.post("/api/notes", json={"title": "打包档案", "content": "包内正文不应进入审计。", "type": "fact"})
    goal = pillar_notes.create_note(vault, pillar="life", chapter="goal_year",
                                    title="打包目标", note_type="goal")
    task_notes.create_task_note(vault, "", "打包任务", {
        "goal": goal["path"], "created": "2026-10-01",
    })
    habit = client.post("/api/habits", json={
        "title": "打包习惯", "minimum_action": "一页", "start_date": "2026-09-28",
    }).json()
    client.put(f"/api/habits/{habit['id']}/entries/2026-10-03", json={"revision": 0, "status": "done"})
    client.post("/api/chat", json={"message": "打包问题？"})
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO journal_entries(date, content, revision, created_at, updated_at)
               VALUES (?,?,?,?,?)""",
            ("2026-10-03", "打包日记", 1,
             "2026-10-03T00:00:00+00:00", "2026-10-03T00:00:00+00:00"),
        )
    structured_notes.upsert_row(vault, "health", "2026-10", {
        "date": "2026-10-03", "weight_kg": "68.5", "sleep_minutes": "450",
    })
    stamp = "2026-10-03T00:00:00+00:00"
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO ai_threads(id,role_id,mode,title,dsh_session_id,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("thread-export", "journal_companion", "discuss", "导出角色会话",
             "session-secret", stamp, stamp),
        )
        connection.execute(
            "INSERT INTO ai_turns(thread_id,turn_index,role,content,sources_json,created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("thread-export", 0, "assistant", "角色抽屉回复", '[{"path":"日记/2026-10-03.md"}]', stamp),
        )
        connection.execute(
            "INSERT INTO ai_proposals(id,kind,title,payload_json,dedupe_key,created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("prop-export", "new_task", "导出提案", '{"title":"导出任务"}',
             "export-proposal", stamp),
        )

    response = client.get("/api/export/all.zip")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert "attachment" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == EXPECTED_MEMBERS
        readme = archive.read("README.txt").decode("utf-8")
        assert "不是恢复包" in readme
        assert "memories 1" in readme and "journal_days 1" in readme and "body_days 1" in readme
        assert "ai_threads 1" in readme and "ai_proposals 1" in readme
        assert "study" not in readme and "学习科目" not in readme
        memories = json.loads(archive.read("memories.json"))
        assert memories["total"] == 1
        journal = json.loads(archive.read("journal.json"))
        assert journal["counts"]["days"] == 1 and journal["entries"][0]["content"] == "打包日记"
        planning = json.loads(archive.read("planning.json"))
        assert planning["counts"]["goals"] == 1 and planning["counts"]["tasks"] == 1
        growth = json.loads(archive.read("growth.json"))
        assert growth["counts"]["habits"] == 1 and "subjects" not in growth["counts"]
        body_lines = archive.read("body.csv").decode("utf-8").strip().splitlines()
        assert body_lines[1] == "2026-10-03,68.5,450,"
        ai = json.loads(archive.read("ai.json"))
        assert ai["counts"] == {"threads": 1, "turns": 1, "proposals": 1}
        assert ai["threads"][0]["title"] == "导出角色会话"
        assert ai["threads"][0]["turns"][0]["content"] == "角色抽屉回复"
        assert ai["proposals"][0]["title"] == "导出提案"
        assert "dsh_session_id" not in ai["threads"][0]
        ai_markdown = archive.read("ai.md").decode("utf-8")
        assert "导出角色会话" in ai_markdown and "角色抽屉回复" in ai_markdown
        assert "导出提案" in ai_markdown and "session-secret" not in ai_markdown

    with database.connect() as connection:
        audit = [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type='export_bundle_created' ORDER BY id"
        )]
    assert len(audit) == 1
    assert json.loads(audit[0]["detail_json"]) == {"files": 15}
    assert "包内正文" not in audit[0]["detail_json"]


def test_export_bundle_works_on_empty_data_and_requires_the_local_session(tmp_path, monkeypatch):
    client, _, _ = build_client(tmp_path, monkeypatch)
    response = client.get("/api/export/all.zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == EXPECTED_MEMBERS
        assert "（还没有保存的每日记录）" in archive.read("journal.md").decode("utf-8")
        assert "（还没有 AI 角色会话）" in archive.read("ai.md").decode("utf-8")

    anonymous = TestClient(client.app)
    assert anonymous.get("/api/export/all.zip").status_code == 401
