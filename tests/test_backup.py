import hashlib
import json
import sqlite3
import stat
import zipfile
from pathlib import Path

import pytest

from backend.app import backup
from backend.app.backup import BackupError, create_backup, restore_backup, verify_backup
from backend.app.db import Database


@pytest.fixture
def backup_source(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    database = Database(data / "live.sqlite3")
    database.initialize()
    database.upsert_document("profile.md", "个人资料", "测试用户喜欢阅读", "test-hash", {})
    vault = data / "vault"
    (vault / "学习").mkdir(parents=True)
    (vault / "学习" / "笔记.md").write_text("# 学习\n测试用户喜欢阅读。\n", encoding="utf-8")
    return database, vault, data / "backups", data


def make_backup(source):
    database, vault, backups, _ = source
    return Path(create_backup(database, vault, backups)["path"])


def rewrite_archive(path: Path, mutate, name="modified.zip") -> Path:
    with zipfile.ZipFile(path) as archive:
        entries = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    mutate(entries)
    destination = path.parent / name
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for entry_name, content in entries.items():
            archive.writestr(entry_name, content)
    return destination


def test_the_workspace_hidden_folder_can_hold_backups_and_is_not_snapshotted(backup_source):
    """工作区模型：<Vault>/.torch-os 放索引/诊断/备份；它不进备份，也不该被“备份在库外”挡住。"""
    database, vault, _backups, _data = backup_source
    hidden = vault / ".torch-os"
    (hidden / "diagnostics").mkdir(parents=True)
    (hidden / "diagnostics" / "restore-drill.md").write_text("内部报告", encoding="utf-8")

    archive_path = Path(create_backup(database, vault, hidden / "backups")["path"])
    assert archive_path.is_relative_to(hidden)
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert "vault/学习/笔记.md" in names
    assert not any(name.startswith("vault/.torch-os/") for name in names)

    # 库内的普通目录仍然拒绝
    with pytest.raises(BackupError):
        create_backup(database, vault, vault / "backups")


def test_backup_round_trip_preserves_database_and_unicode_markdown(backup_source):
    database, vault, backups, data = backup_source
    conversation = database.create_conversation("备份测试")
    database.add_message(conversation, "user", "测试对话", [])
    result = create_backup(database, vault, backups)
    archive_path = Path(result["path"])
    assert result["encrypted"] is False
    assert result["verified"] is True
    assert result["vault_files"] == 1
    assert result["file_count"] == 2
    assert verify_backup(archive_path)["format_version"] == 1
    with database.connect() as connection:
        connection.execute("UPDATE documents SET title='备份后的修改'")
    restored = restore_backup(archive_path, data / "recovered", allowed_parent=data)
    restored_database = Database(Path(restored["database_path"]))
    assert restored_database.list_documents()[0]["title"] == "个人资料"
    with restored_database.connect() as connection:
        assert connection.execute("SELECT content FROM messages").fetchone()[0] == "测试对话"
    assert (Path(restored["vault_dir"]) / "学习" / "笔记.md").read_bytes() == (vault / "学习" / "笔记.md").read_bytes()
    assert database.list_documents()[0]["title"] == "备份后的修改"
    assert not list(backups.glob(".backup-*"))
    assert not list(backups.glob(".verify-*"))
    assert not list(data.glob(".restore-*"))


def test_backup_includes_committed_wal_data(backup_source):
    database, vault, backups, data = backup_source
    writer = sqlite3.connect(database.path)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE documents SET title='WAL内的提交'")
        writer.commit()
        assert Path(str(database.path) + "-wal").exists()
        result = create_backup(database, vault, backups)
        restored = restore_backup(Path(result["path"]), data / "from-wal", allowed_parent=data)
        assert Database(Path(restored["database_path"])).list_documents()[0]["title"] == "WAL内的提交"
    finally:
        writer.close()


def test_backup_excludes_environment_keys_logs_and_non_markdown(backup_source):
    database, vault, backups, data = backup_source
    (data / ".env").write_text("MODEL_API_KEY=test-only-secret", encoding="utf-8")
    (vault / ".env.md").write_text("test-only-env-secret", encoding="utf-8")
    (vault / "api_key.md").write_text("test-only-key", encoding="utf-8")
    (vault / "photo.png").write_bytes(b"test-image")
    (vault / "logs").mkdir()
    (vault / "logs" / "event.md").write_text("log-only", encoding="utf-8")
    result = create_backup(database, vault, backups)
    with zipfile.ZipFile(result["path"]) as archive:
        assert set(archive.namelist()) == {"database.sqlite3", "vault/学习/笔记.md", "manifest.json"}


def test_restores_to_existing_empty_directory(backup_source):
    archive = make_backup(backup_source)
    data = backup_source[3]
    destination = data / "empty"
    destination.mkdir()
    restore_backup(archive, destination, allowed_parent=data)
    assert (destination / "torch_os.sqlite3").is_file()


def test_restore_refuses_existing_data_without_changes(backup_source):
    archive = make_backup(backup_source)
    data = backup_source[3]
    destination = data / "keep"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("original", encoding="utf-8")
    with pytest.raises(BackupError, match="new or empty"):
        restore_backup(archive, destination, allowed_parent=data)
    assert list(destination.iterdir()) == [marker]
    assert marker.read_text(encoding="utf-8") == "original"


def test_restore_requires_configured_parent_and_refuses_traversal(backup_source):
    archive = make_backup(backup_source)
    data = backup_source[3]
    for destination in [data, data.parent / "outside", data / ".." / "outside"]:
        with pytest.raises(BackupError):
            restore_backup(archive, destination, allowed_parent=data)
    assert not (data.parent / "outside").exists()


@pytest.mark.parametrize("name", ["../outside.md", "/absolute.md", "vault/../../outside.md", "vault\\outside.md", "vault/C:evil.md", "vault/con.md", "vault/note.md.", "vault/double//note.md", "vault/.env.md", "vault/logs/test.md"])
def test_restore_rejects_unsafe_member_paths(backup_source, name):
    archive = make_backup(backup_source)
    modified = rewrite_archive(archive, lambda entries: entries.update({name: b"malicious"}))
    destination = backup_source[3] / "bad-path"
    with pytest.raises(BackupError):
        restore_backup(modified, destination, allowed_parent=backup_source[3])
    assert not destination.exists()


def test_checksum_failure_leaves_empty_target_untouched(backup_source):
    archive = make_backup(backup_source)
    modified = rewrite_archive(archive, lambda entries: entries.update({"vault/学习/笔记.md": b"x" * len(entries["vault/学习/笔记.md"])}))
    data = backup_source[3]
    destination = data / "empty"
    destination.mkdir()
    with pytest.raises(BackupError, match="checksum"):
        restore_backup(modified, destination, allowed_parent=data)
    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert not list(data.glob(".restore-*"))


@pytest.mark.parametrize("version", [2, True, "1"])
def test_unknown_or_invalid_manifest_version_is_rejected(backup_source, version):
    archive = make_backup(backup_source)

    def mutate(entries):
        manifest = json.loads(entries["manifest.json"])
        manifest["version"] = version
        entries["manifest.json"] = json.dumps(manifest).encode()

    modified = rewrite_archive(archive, mutate)
    with pytest.raises(BackupError, match="version"):
        verify_backup(modified)


def test_sqlite_corruption_is_rejected_even_with_updated_checksum(backup_source):
    archive = make_backup(backup_source)

    def mutate(entries):
        contents = b"This is not a SQLite database"
        entries["database.sqlite3"] = contents
        manifest = json.loads(entries["manifest.json"])
        for entry in manifest["files"]:
            if entry["path"] == "database.sqlite3":
                entry.update(size=len(contents), sha256=hashlib.sha256(contents).hexdigest())
        entries["manifest.json"] = json.dumps(manifest).encode()

    modified = rewrite_archive(archive, mutate)
    with pytest.raises(BackupError, match="SQLite"):
        verify_backup(modified)


def test_backup_rejects_unsupported_database_schema_version(backup_source):
    database, vault, backups, _ = backup_source
    with database.connect() as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(BackupError, match="schema version"):
        create_backup(database, vault, backups)
    assert not list(backups.iterdir())


def test_archive_rejects_symlink_entries(backup_source):
    archive = make_backup(backup_source)
    modified = archive.parent / "link.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(modified, "w") as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        link = zipfile.ZipInfo("vault/linked.md")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        target.writestr(link, "../../outside.md")
    with pytest.raises(BackupError, match="link"):
        verify_backup(modified)


def test_archive_rejects_duplicate_paths(backup_source):
    archive = make_backup(backup_source)
    modified = archive.parent / "duplicate.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(modified, "w") as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        with pytest.warns(UserWarning, match="Duplicate name"):
            target.writestr("vault/学习/笔记.md", "duplicate")
    with pytest.raises(BackupError, match="duplicate"):
        verify_backup(modified)


def test_archive_rejects_zip_bomb_ratio(backup_source):
    archive = make_backup(backup_source)
    modified = archive.parent / "ratio.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(modified, "w") as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr("vault/bomb.md", b"0" * 1_000_000, compress_type=zipfile.ZIP_DEFLATED)
    with pytest.raises(BackupError, match="compression ratio"):
        verify_backup(modified)


def test_backup_size_limit_keeps_existing_backups(backup_source, monkeypatch):
    archive = make_backup(backup_source)
    original = archive.read_bytes()
    database, vault, backups, _ = backup_source
    monkeypatch.setattr(backup, "MAX_FILE_BYTES", 1)
    with pytest.raises(BackupError, match="size limit"):
        create_backup(database, vault, backups)
    assert archive.read_bytes() == original
    assert list(backups.glob("*.zip")) == [archive]


def test_backup_detects_vault_changed_after_copy_and_keeps_old_backups(backup_source, monkeypatch):
    archive = make_backup(backup_source)
    database, vault, backups, _ = backup_source
    original_copy = backup._copy_stable_file

    def change_after_copy(source, destination, expected):
        original_copy(source, destination, expected)
        source.write_text("Changed while backup was running", encoding="utf-8")

    monkeypatch.setattr(backup, "_copy_stable_file", change_after_copy)
    with pytest.raises(BackupError, match="changed"):
        create_backup(database, vault, backups)
    assert list(backups.glob("*.zip")) == [archive]
    assert not list(backups.glob(".backup-*"))


def test_source_and_restore_reject_symlink_paths(backup_source):
    database, vault, backups, data = backup_source
    archive = make_backup(backup_source)
    external = data.parent / "external"
    external.mkdir()
    linked = data / "linked"
    try:
        linked.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable for this Windows account")
    with pytest.raises(BackupError, match="links"):
        restore_backup(archive, linked / "restore", allowed_parent=data)
    note_link = vault / "linked.md"
    note_link.symlink_to(vault / "学习" / "笔记.md")
    with pytest.raises(BackupError, match="link"):
        create_backup(database, vault, backups)
    assert list(external.iterdir()) == []


def test_backup_refuses_destination_inside_vault(backup_source):
    database, vault, _, _ = backup_source
    with pytest.raises(BackupError, match="outside the Vault"):
        create_backup(database, vault, vault / "backups")
    assert not (vault / "backups").exists()


def test_empty_vault_round_trip(backup_source):
    database, vault, backups, data = backup_source
    (vault / "学习" / "笔记.md").unlink()
    result = create_backup(database, vault, backups)
    assert result["vault_files"] == 0
    restored = restore_backup(Path(result["path"]), data / "empty-vault", allowed_parent=data)
    assert Path(restored["vault_dir"]).is_dir()
    assert list(Path(restored["vault_dir"]).iterdir()) == []


def test_existing_target_populated_during_validation_is_preserved(backup_source, monkeypatch):
    archive = make_backup(backup_source)
    data = backup_source[3]
    destination = data / "changed-target"
    destination.mkdir()
    original_validate = backup._extract_and_validate

    def add_new_content(archive_path, stage):
        manifest = original_validate(archive_path, stage)
        (destination / "created-during-restore.txt").write_text("Keep this", encoding="utf-8")
        return manifest

    monkeypatch.setattr(backup, "_extract_and_validate", add_new_content)
    with pytest.raises(BackupError, match="changed"):
        restore_backup(archive, destination, allowed_parent=data)
    assert (destination / "created-during-restore.txt").read_text(encoding="utf-8") == "Keep this"
    assert not (destination / "torch_os.sqlite3").exists()


def test_failed_publication_restores_original_empty_directory(backup_source, monkeypatch):
    archive = make_backup(backup_source)
    data = backup_source[3]
    destination = data / "empty-target"
    destination.mkdir()
    original_rename = Path.rename

    def fail_directory_rename(source, target):
        if source.name == "content":
            raise OSError("Simulated publication failure")
        return original_rename(source, target)

    monkeypatch.setattr(Path, "rename", fail_directory_rename)
    with pytest.raises(BackupError, match="restoration failed"):
        restore_backup(archive, destination, allowed_parent=data)
    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert not list(data.glob(".restore-*"))


def test_duplicate_manifest_properties_are_rejected(backup_source):
    archive = make_backup(backup_source)

    def mutate(entries):
        raw = entries["manifest.json"].decode("utf-8")
        entries["manifest.json"] = raw.replace('"version": 1', '"version": 1, "version": 1').encode("utf-8")

    modified = rewrite_archive(archive, mutate)
    with pytest.raises(BackupError, match="Duplicate manifest"):
        verify_backup(modified)


def test_total_size_and_file_count_limits_are_enforced(backup_source, monkeypatch):
    archive = make_backup(backup_source)
    with monkeypatch.context() as patch:
        patch.setattr(backup, "MAX_TOTAL_BYTES", 1)
        with pytest.raises(BackupError, match="size limit"):
            verify_backup(archive)
    with monkeypatch.context() as patch:
        patch.setattr(backup, "MAX_FILES", 1)
        with pytest.raises(BackupError, match="count"):
            verify_backup(archive)


def test_backup_publication_failure_preserves_existing_backups(backup_source, monkeypatch):
    archive = make_backup(backup_source)
    original_bytes = archive.read_bytes()
    database, vault, backups, _ = backup_source

    def fail_link(source, destination):
        raise OSError("Simulated filesystem without hard links")

    monkeypatch.setattr(backup.os, "link", fail_link)
    with pytest.raises(BackupError, match="complete backup"):
        create_backup(database, vault, backups)
    assert archive.read_bytes() == original_bytes
    assert list(backups.iterdir()) == [archive]
