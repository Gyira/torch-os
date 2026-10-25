"""Backup retention preview is read-only: it lists, never deletes."""

import os

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database


def build_client(tmp_path):
    database = Database(tmp_path / "torch_os.sqlite3")
    app = create_app(database, tmp_path / "vault", security_environ={})
    client = TestClient(app)
    client.get("/")
    return client, database


def test_backup_plan_lists_keep_and_remove_without_deleting(tmp_path):
    client, database = build_client(tmp_path)
    folder = tmp_path / "backups"
    folder.mkdir()
    names = ["torch-os-old.zip", "torch-os-mid.zip", "torch-os-new.zip"]
    for index, name in enumerate(names):
        path = folder / name
        path.write_bytes(b"x" * (100 * (index + 1)))
        os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))
    with database.connect() as connection:
        audit_before = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    payload = client.get("/api/backups/plan?keep=2").json()
    assert payload["total"] == 3 and payload["keep"] == 2
    assert [item["filename"] for item in payload["keep_list"]] == [
        "torch-os-new.zip", "torch-os-mid.zip",
    ]
    assert [item["filename"] for item in payload["remove_list"]] == ["torch-os-old.zip"]
    assert payload["remove_bytes"] == 100
    assert payload["total_bytes"] == 100 + 200 + 300
    assert "不会删除" in payload["note"]

    assert (folder / "torch-os-old.zip").exists(), "preview must not delete"
    with database.connect() as connection:
        audit_after = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert audit_before == audit_after, "preview must not write audit"

    assert client.get("/api/backups/plan?keep=0").status_code == 422
    assert client.get("/api/backups/plan?keep=201").status_code == 422


def test_backup_plan_handles_missing_folder_and_requires_the_local_session(tmp_path):
    client, _ = build_client(tmp_path)
    payload = client.get("/api/backups/plan").json()
    assert payload["total"] == 0 and payload["keep_list"] == [] and payload["remove_list"] == []

    anonymous = TestClient(client.app)
    assert anonymous.get("/api/backups/plan").status_code == 401
