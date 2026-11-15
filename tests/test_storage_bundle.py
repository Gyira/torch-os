import sqlite3
from contextlib import closing
import pytest
from backend.app.storage_layout import new_layout, initialize_empty_workspace
from backend.app.storage_layout import register_layout
from backend.app.storage_bundle import create_bundle, restore_bundle
from backend.app.cloud_backup import create_snapshot


def test_encrypted_multiroot_bundle_restores_notes_unique_database_and_attachment(tmp_path):
    layout = new_layout(tmp_path / "data")
    initialize_empty_workspace(layout)
    layout.state.mkdir(); layout.archive.mkdir()
    (layout.notes / "笔记.md").write_text("原文", encoding="utf-8")
    (layout.archive / "图片.png").write_bytes(b"image")
    db = layout.state / "business.db"
    with closing(sqlite3.connect(db)) as con:
        con.execute("CREATE TABLE unique_records(id TEXT)"); con.execute("INSERT INTO unique_records VALUES('retained')"); con.commit()
    package = tmp_path / "snapshot.enc"
    create_bundle(layout, package, password="recovery-password-123")
    assert not list(tmp_path.glob(".torch-publish-*"))
    assert (layout.cache / "backup-staging").is_dir()
    target = tmp_path / "restored"
    result = restore_bundle(package, target, password="recovery-password-123")
    assert not result["active"]
    assert (target / "notes/笔记.md").read_text(encoding="utf-8") == "原文"
    assert (target / "archive/图片.png").read_bytes() == b"image"
    with closing(sqlite3.connect(target / "state/business.db")) as con:
        assert con.execute("SELECT * FROM unique_records").fetchone()[0] == "retained"
    with pytest.raises(ValueError): restore_bundle(package, target, password="recovery-password-123")
    with pytest.raises(ValueError): create_bundle(layout, package)


def test_wrong_password_does_not_create_restore_directory(tmp_path):
    layout = new_layout(tmp_path / "data"); initialize_empty_workspace(layout)
    layout.state.mkdir(); layout.archive.mkdir()
    package = tmp_path / "snapshot.enc"; create_bundle(layout, package, password="right-password-123")
    with pytest.raises(Exception): restore_bundle(package, tmp_path / "wrong", password="wrong-password-123")
    assert not (tmp_path / "wrong").exists()


def test_split_cloud_snapshot_retention_and_legacy_password_are_preserved(tmp_path):
    layout = new_layout(tmp_path / "data"); initialize_empty_workspace(layout)
    layout.state.mkdir(); layout.archive.mkdir(); register_layout(layout)
    cloud = tmp_path / "completed-backups"; cloud.mkdir()
    first = create_snapshot(layout.notes, cloud, password="12345678", keep=1)
    second = create_snapshot(layout.notes, cloud, password="12345678", keep=1)
    assert first["filename"] in second["removed"]
    assert not (cloud / first["filename"]).exists()
    assert (cloud / second["filename"]).is_file()
    assert len(list(cloud.iterdir())) == 1
    assert restore_bundle(cloud / second["filename"], tmp_path / "restored", password="12345678")["active"] is False
