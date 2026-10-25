"""Read-only self-check: integrity and counts only, never note contents."""

import json
from datetime import date

from fastapi.testclient import TestClient

from backend.app import pillar_notes, structured_notes, task_notes
from backend.app.application import create_app
from backend.app.db import Database


def build_client(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.app.journal_shared.shanghai_today", lambda: date(2026, 10, 4))
    database = Database(tmp_path / "torch_os.sqlite3")
    app = create_app(database, tmp_path / "vault", security_environ={})
    client = TestClient(app)
    client.get("/")
    return client, database, app


def test_diagnostics_summary_counts_without_leaking_content_or_writing_audit(tmp_path, monkeypatch):
    client, database, app = build_client(tmp_path, monkeypatch)
    client.post("/api/notes", json={"title": "自检标题", "content": "虚构自检内容，不应出现在自检里。", "type": "note"})
    goal = pillar_notes.create_note(app.state.vault_dir, pillar="life", chapter="goal_year",
                                    title="自检目标", note_type="goal")
    task_notes.create_task_note(app.state.vault_dir, "", "自检任务", {"goal": goal["path"]})
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO journal_entries(date, content, revision, created_at, updated_at)
               VALUES (?,?,?,?,?)""",
            ("2026-10-04", "自检日记", 1,
             "2026-10-04T00:00:00+00:00", "2026-10-04T00:00:00+00:00"),
        )
    structured_notes.upsert_row(app.state.vault_dir, "health", "2026-10", {
        "date": "2026-10-04", "weight_kg": "68.5"})
    client.post("/api/schedule/run-now", json={"kind": "morning"})

    with database.connect() as connection:
        audit_before = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    response = client.get("/api/diagnostics/summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["integrity"] == "ok"
    assert payload["version"] == app.version
    counts = payload["counts"]
    assert counts["documents_available"] == 1
    assert counts["memories_candidate"] == 1 and counts["memories_active"] == 0
    assert counts["goals"] == 0 and counts["tasks"] == 0  # retired tables stay empty
    assert counts["journal_entries"] == 1
    assert counts["body_entries"] == 0  # body now lives in Markdown
    assert counts["inbox_items"] >= 1
    assert payload["vault"]["markdown_files"] == 4  # note + goal + task + health table
    assert payload["vault"]["relationships_present"] is False
    assert payload["backups"]["count"] == 0 and payload["backups"]["latest"] is None

    text = json.dumps(payload, ensure_ascii=False)
    for secret in ("虚构自检内容", "自检标题", "自检日记", "自检目标", "自检任务"):
        assert secret not in text, f"self-check must not leak {secret}"

    with database.connect() as connection:
        audit_after = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert audit_before == audit_after, "the self-check must not write audit events"


def test_diagnostics_excludes_the_relationship_partition(tmp_path, monkeypatch):
    client, _, _ = build_client(tmp_path, monkeypatch)
    client.post("/api/notes", json={"title": "普通笔记", "content": "普通内容。"})
    secret_dir = tmp_path / "vault" / "relationships"
    secret_dir.mkdir(parents=True)
    (secret_dir / "secret.md").write_text("关系内容不应计入普通 Vault 文件数。", encoding="utf-8")

    payload = client.get("/api/diagnostics/summary").json()
    assert payload["vault"]["relationships_present"] is True
    assert payload["vault"]["markdown_files"] == 1


def test_diagnostics_works_on_an_empty_database(tmp_path, monkeypatch):
    client, _, _ = build_client(tmp_path / "empty", monkeypatch)
    payload = client.get("/api/diagnostics/summary").json()
    assert payload["integrity"] == "ok"
    assert payload["counts"]["documents_available"] == 0
    assert payload["counts"]["memories_candidate"] == 0
    assert payload["vault"]["markdown_files"] == 0


def test_diagnostics_requires_the_local_session(tmp_path, monkeypatch):
    client, _, app = build_client(tmp_path, monkeypatch)
    anonymous = TestClient(app)
    assert anonymous.get("/api/diagnostics/summary").status_code == 401


def test_diagnostics_reverifies_the_latest_backup_and_reports_corruption(tmp_path, monkeypatch):
    from backend.app.backup import create_backup

    client, database, _ = build_client(tmp_path, monkeypatch)
    relations = tmp_path / "vault" / "relationships"
    relations.mkdir(parents=True)
    (relations / "secret.md").write_text("关系分区内容。", encoding="utf-8")
    result = create_backup(database, tmp_path / "vault", tmp_path / "backups")

    assert result["relationship_files"] == 1
    payload = client.get("/api/diagnostics/summary").json()
    assert payload["backups"]["count"] == 1
    assert payload["backups"]["verification"]["checked"] == result["filename"]
    assert payload["backups"]["verification"]["ok"] is True
    assert payload["backups"]["verification"]["relationship_files"] == 1

    # Corrupt the archive in place: the self-check must report, not raise.
    (tmp_path / "backups" / result["filename"]).write_bytes(b"not a zip any more")
    payload = client.get("/api/diagnostics/summary").json()
    assert payload["backups"]["verification"]["ok"] is False
    assert payload["backups"]["verification"]["error"]
