from pathlib import Path
from contextlib import closing
import sqlite3
import pytest
from backend.app.storage_layout import new_layout, StorageLayout, register_layout
from backend.app.storage_migration import prepare_split
from backend.app.love_layout import love_paths, CHAT_DIR, GALLERY_DIR
from backend.app.db import Database
from backend.app.share_store import ShareStore
from backend.app.share_capture import capture
from backend.app.device_registry import DeviceRegistry


def test_prepare_preserves_source_and_routes_unique_data_to_separate_roots(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    note = source / "03_知识库/00_收件箱/笔记.md"; note.parent.mkdir(parents=True); note.write_text("原文", encoding="utf-8")
    media = source / CHAT_DIR / "media/image.jpg"; media.parent.mkdir(parents=True); media.write_bytes(b"image")
    db = source / ".torch-os/torch_os.sqlite3"; db.parent.mkdir()
    Database(db).initialize()
    chat = source / CHAT_DIR / "chat.db"
    with closing(sqlite3.connect(chat)) as con:
        con.execute("CREATE TABLE unique_chat(id TEXT)"); con.execute("INSERT INTO unique_chat VALUES('retained')"); con.commit()
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    layout = new_layout(tmp_path / "new")
    result = prepare_split(source, layout, database_path=db, writers_stopped=True)
    assert result["status"] == "prepared_not_active"
    assert {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()} == before
    loaded = StorageLayout.load(Path(result["device_config"]))
    register_layout(loaded)
    paths = love_paths(loaded.notes)
    assert paths.chat_db.is_relative_to(loaded.state)
    assert paths.chat_media.is_relative_to(loaded.archive)
    assert (paths.chat_media / "image.jpg").read_bytes() == b"image"
    assert (loaded.notes / note.relative_to(source)).read_text(encoding="utf-8") == "原文"
    assert not list(loaded.notes.rglob("*.db"))
    with closing(sqlite3.connect(paths.chat_db)) as con:
        assert con.execute("SELECT * FROM unique_chat").fetchone()[0] == "retained"


def test_prepare_requires_explicit_stop_window_and_never_uses_nonempty_target(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    db = tmp_path / "main.sqlite3"; Database(db).initialize()
    layout = new_layout(tmp_path / "new")
    with pytest.raises(ValueError, match="停写"):
        prepare_split(source, layout, database_path=db)
    assert not layout.notes.exists()
    layout.notes.mkdir(parents=True)
    with pytest.raises(ValueError, match="新目录"):
        prepare_split(source, layout, database_path=db, writers_stopped=True)


def test_external_import_ledger_and_legacy_identity_survive_split(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    db = Database(tmp_path / "external-state/main.sqlite3"); db.initialize()
    store = ShareStore(db, source)
    original = capture(pasted_text="已确认的内容")
    preview = store.save_preview(original)
    archived = store.archive(original, chapter="inbox", title="保留")
    old = StorageLayout.legacy(source, db.path)
    export = old.context_exports / "existing/manifest.json"; export.parent.mkdir(parents=True); export.write_text('{"existing":true}', encoding="utf-8")
    layout = new_layout(tmp_path / "new")
    result = prepare_split(source, layout, database_path=db.path, writers_stopped=True)
    split_store = ShareStore(Database(layout.state / "vault-state/torch_os.sqlite3"), layout.notes, storage_layout=layout)
    assert split_store.load(preview["preview_id"])["content_hash"] == original["content_hash"]
    assert split_store.archive(original, chapter="inbox", title="不同标题")["duplicate"]
    assert (layout.context_exports / "existing/manifest.json").is_file()
    assert DeviceRegistry(layout.state / "device").lookup(result["legacy_alias"])["workspace_id"] == layout.workspace_id
    assert not (layout.notes / ".torch-os").exists()
