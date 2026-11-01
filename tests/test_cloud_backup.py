"""云端完整备份：全库快照、SQLite WAL、加密、设置与后台任务。"""

from __future__ import annotations

import json
import sqlite3
import time
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.cloud_backup import (
    CloudBackupError,
    CloudBackupService,
    create_cloud_backup_router,
    create_snapshot,
    decrypt_file,
    get_password,
    load_config,
    password_is_set,
)
from backend.app.db import Database


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "00_人生路").mkdir(parents=True)
    (vault / "00_人生路" / "day.md").write_text("# 今天\n", encoding="utf-8")
    (vault / ".torch-os" / "logs").mkdir(parents=True)
    (vault / ".torch-os" / "logs" / "skip.log").write_text("不要进快照\n", encoding="utf-8")
    (vault / ".torch-os" / "backups").mkdir(parents=True)
    (vault / ".torch-os" / "backups" / "old.zip").write_bytes(b"old")
    (vault / ".torch-os" / "personal-data.json").write_text(
        '{"values":{"torch-os:test":"ok"}}\n', encoding="utf-8")
    return vault


def _chat_db(vault: Path) -> tuple[Path, sqlite3.Connection]:
    directory = vault / "02_生活运营" / "01_恋爱系统" / "02_微信聊天"
    directory.mkdir(parents=True)
    path = directory / "chat.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE messages(content TEXT)")
    connection.execute("INSERT INTO messages(content) VALUES ('还没检查点的 WAL 消息')")
    connection.commit()
    return path, connection


def _cloud(tmp_path: Path) -> Path:
    directory = tmp_path / "cloud"
    directory.mkdir()
    return directory


def test_full_snapshot_includes_vault_and_wal_database(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _chat, connection = _chat_db(vault)
    cloud = _cloud(tmp_path)
    try:
        result = create_snapshot(vault, cloud)
    finally:
        connection.close()
    assert result["encrypted"] is False
    archive_path = cloud / result["filename"]
    assert archive_path.is_file()
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "vault/00_人生路/day.md" in names
        assert "vault/.torch-os/personal-data.json" in names
        assert "vault/02_生活运营/01_恋爱系统/02_微信聊天/chat.db" in names
        assert not any("日志" in name or "skip.log" in name for name in names)
        assert not any(name.endswith(".zip") for name in names)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "torch-os-full-backup"
        assert manifest["encrypted"] is False
        assert manifest["totals"]["files"] == len(manifest["files"])
        snapshot = tmp_path / "restored-chat.db"
        snapshot.write_bytes(archive.read(
            "vault/02_生活运营/01_恋爱系统/02_微信聊天/chat.db"))
    with sqlite3.connect(snapshot) as restored:
        assert restored.execute("SELECT content FROM messages").fetchone()[0] == \
            "还没检查点的 WAL 消息"


def test_full_snapshot_password_roundtrip_and_tamper(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    cloud = _cloud(tmp_path)
    result = create_snapshot(vault, cloud, password="correct horse battery")
    assert result["encrypted"] is True
    assert result["filename"].endswith(".zip.enc")
    encrypted = cloud / result["filename"]
    raw = encrypted.read_bytes()
    assert "# 今天".encode("utf-8") not in raw
    assert b"SQLite format 3" not in raw

    decrypted = tmp_path / "decrypted.zip"
    decrypt_file(encrypted, decrypted, "correct horse battery")
    with zipfile.ZipFile(decrypted) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["encrypted"] is True
        assert archive.read("vault/00_人生路/day.md").decode("utf-8") == \
            (vault / "00_人生路" / "day.md").read_bytes().decode("utf-8")

    wrong = tmp_path / "wrong.zip"
    with pytest.raises(CloudBackupError, match="密码|损坏"):
        decrypt_file(encrypted, wrong, "wrong password")
    assert not wrong.exists()


def test_settings_encrypt_password_and_reject_directory_inside_vault(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    database = Database(vault / ".torch-os" / "torch_os.sqlite3")
    database.initialize()
    service = CloudBackupService(database, vault)
    cloud = _cloud(tmp_path)
    result = service.update_settings(
        directory=str(cloud), frequency="daily", keep=7,
        encrypt=True, password="secret123")
    assert result["settings"]["password_set"] is True
    assert result["settings"]["encrypt"] is True
    assert "secret123" not in json.dumps(result, ensure_ascii=False)
    assert password_is_set(vault) is True
    assert get_password(vault) == "secret123"
    config_text = (vault / ".torch-os" / "cloud-backup.json").read_text(encoding="utf-8")
    assert "secret123" not in config_text
    assert load_config(vault)["frequency"] == "daily"

    with pytest.raises(CloudBackupError, match="不能放在个人数据库里面"):
        service.update_settings(
            directory=str(vault / "备份"), frequency="manual", keep=7,
            encrypt=False)
    with pytest.raises(CloudBackupError, match="不存在"):
        service.update_settings(
            directory=str(tmp_path / "missing"), frequency="manual", keep=7,
            encrypt=False)


def test_retention_and_api_run(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    database = Database(vault / ".torch-os" / "torch_os.sqlite3")
    database.initialize()
    service = CloudBackupService(database, vault)
    cloud = _cloud(tmp_path)
    app = FastAPI()
    app.include_router(create_cloud_backup_router(service))
    client = TestClient(app)

    saved = client.put("/api/cloud-backup/settings", json={
        "directory": str(cloud), "frequency": "manual", "keep": 1,
        "encrypt": True, "password": "secret123",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"]["password_set"] is True
    assert "secret123" not in saved.text

    started = client.post("/api/cloud-backup/run", json={"confirm": True})
    assert started.status_code == 200, started.text
    run = {}
    for _ in range(100):
        time.sleep(0.1)
        run = client.get("/api/cloud-backup/status").json()
        if run["job"]["status"] in {"completed", "failed"}:
            break
    assert run["job"]["status"] == "completed", run
    assert run["job"]["filename"].endswith(".zip.enc")
    assert len(run["snapshots"]) == 1

    # 再补一次，keep=1 会删除更早的快照。
    second = client.post("/api/cloud-backup/run", json={"confirm": True})
    assert second.status_code == 200, second.text
    for _ in range(100):
        time.sleep(0.1)
        run = client.get("/api/cloud-backup/status").json()
        if run["job"]["status"] in {"completed", "failed"}:
            break
    assert run["job"]["status"] == "completed", run
    assert len(run["snapshots"]) == 1
    assert run["snapshots"][0]["filename"] == run["job"]["filename"]


def test_missing_password_and_read_only_are_rejected(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    database = Database(vault / ".torch-os" / "torch_os.sqlite3")
    database.initialize()
    service = CloudBackupService(database, vault)
    cloud = _cloud(tmp_path)
    with pytest.raises(CloudBackupError, match="密码"):
        service.update_settings(
            directory=str(cloud), frequency="manual", keep=7, encrypt=True)
    with pytest.raises(CloudBackupError, match="只读"):
        CloudBackupService(database, vault, read_only=True).update_settings(
            directory=str(cloud), frequency="manual", keep=7, encrypt=False)
