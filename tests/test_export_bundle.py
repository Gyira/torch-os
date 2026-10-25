"""The readable export bundle is a read-only ZIP of every domain export."""

import io
import json
import zipfile
from datetime import date

from fastapi.testclient import TestClient

from backend.app import pillar_notes, task_notes
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
    "journal.md", "journal.json", "body.csv", "body.json",
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
    client.post("/api/ops/health/months/2026-10/rows", json={
        "date": "2026-10-03", "weight_kg": "68.5", "sleep_minutes": "450",
    })

    response = client.get("/api/export/all.zip")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert "attachment" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == EXPECTED_MEMBERS
        readme = archive.read("README.txt").decode("utf-8")
        assert "不是恢复包" in readme
        assert "memories 1" in readme and "journal_days 1" in readme and "body_days 1" in readme
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

    with database.connect() as connection:
        audit = [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type='export_bundle_created' ORDER BY id"
        )]
    assert len(audit) == 1
    assert json.loads(audit[0]["detail_json"]) == {"files": 13}
    assert "包内正文" not in audit[0]["detail_json"]


def test_export_bundle_works_on_empty_data_and_requires_the_local_session(tmp_path, monkeypatch):
    client, _, _ = build_client(tmp_path, monkeypatch)
    response = client.get("/api/export/all.zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == EXPECTED_MEMBERS
        assert "（还没有保存的每日记录）" in archive.read("journal.md").decode("utf-8")

    anonymous = TestClient(client.app)
    assert anonymous.get("/api/export/all.zip").status_code == 401
