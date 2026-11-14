import hashlib
import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.storage_layout import StorageLayout, new_layout, initialize_empty_workspace, workspace_identity, migration_plan
from backend.app.share_store import ShareStore
from backend.app.db import Database
from backend.app.share_capture import capture


def test_construction_does_not_create_directories(tmp_path):
    layout = new_layout(tmp_path / "new")
    assert layout.mode == "split"
    assert not (tmp_path / "new").exists()


def test_identity_survives_path_move_and_new_device(tmp_path):
    layout = new_layout(tmp_path / "original")
    initialize_empty_workspace(layout)
    moved = tmp_path / "moved"
    layout.notes.rename(moved)
    assert workspace_identity(moved) == layout.workspace_id
    other = StorageLayout.split(notes=moved, state=tmp_path / "device2/state", archive=tmp_path / "device2/archive",
                                cache=tmp_path / "device2/cache", exports=tmp_path / "device2/exports",
                                workspace_id=layout.workspace_id)
    assert other.scope == layout.scope


def test_mismatch_and_corrupt_identity_fail_closed(tmp_path):
    layout = new_layout(tmp_path / "new")
    initialize_empty_workspace(layout)
    with pytest.raises(ValueError, match="不匹配"):
        StorageLayout.split(notes=layout.notes, state=layout.state, archive=layout.archive, cache=layout.cache,
                            exports=layout.exports, workspace_id=str(uuid4()))
    (layout.notes / "_Torch/工作区.md").write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError):
        StorageLayout.legacy(layout.notes, tmp_path / "db.sqlite3")


def test_never_adopts_existing_vault_implicitly(tmp_path):
    layout = new_layout(tmp_path)
    layout.notes.mkdir()
    (layout.notes / "diary.md").write_text("original")
    with pytest.raises(ValueError, match="空笔记目录"):
        initialize_empty_workspace(layout)
    assert not (layout.notes / "_Torch").exists()


def test_overlap_rejected_and_device_config_requires_marker(tmp_path):
    with pytest.raises(ValueError, match="重叠"):
        StorageLayout.split(notes=tmp_path, state=tmp_path / "state", archive=tmp_path / "archive",
                            cache=tmp_path / "cache", exports=tmp_path / "exports", workspace_id=str(uuid4()))
    layout = new_layout(tmp_path / "new")
    config = tmp_path / "device.json"
    config.write_text(json.dumps(dict(version=1, workspace_id=layout.workspace_id,
        **{key: str(getattr(layout, key)) for key in ("notes", "state", "archive", "cache", "exports")})))
    with pytest.raises(ValueError, match="缺失"):
        StorageLayout.load(config)
    initialize_empty_workspace(layout)
    assert StorageLayout.load(config) == layout


def test_legacy_paths_match_p1_byte_for_byte(tmp_path):
    notes, db = tmp_path / "vault", tmp_path / "runtime/main.sqlite3"
    layout = StorageLayout.legacy(notes, db)
    digest = hashlib.sha256(str(notes.resolve()).encode()).hexdigest()[:16]
    assert layout.share_database == db.parent / f"share-captures-{digest}.sqlite3"
    assert layout.context_exports == db.parent / "context-packs" / digest


def test_share_archive_can_use_isolated_state_without_vault_database(tmp_path):
    layout = new_layout(tmp_path)
    initialize_empty_workspace(layout)
    db = Database(layout.state / "main.sqlite3")
    db.initialize()
    result = ShareStore(db, layout.notes, storage_layout=layout).save_preview(capture(pasted_text="sample"))
    assert ShareStore(db, layout.notes, storage_layout=layout).load(result["preview_id"])
    assert not list(layout.notes.rglob("*.sqlite3"))
    with pytest.raises(ValueError):
        ShareStore(db, tmp_path / "other", storage_layout=layout)


def test_migration_plan_preserves_unknown_files_and_changes_nothing(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    (source / "unknown.bin").write_bytes(b"private")
    (source / "business.db").write_bytes(b"not actually a database")
    layout = new_layout(tmp_path / "new")
    before = {p: p.read_bytes() for p in source.iterdir()}
    report = migration_plan(source, layout)
    assert report["dry_run"] and not report["executable"]
    assert len(report["files"]) == 2
    assert {p: p.read_bytes() for p in source.iterdir()} == before
    assert not (tmp_path / "new").exists()


def test_migration_overlap_and_nonempty_target_refused(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    with pytest.raises(ValueError, match="重叠"):
        migration_plan(source, new_layout(source))
    layout = new_layout(tmp_path / "new")
    layout.state.mkdir(parents=True)
    (layout.state / "important").write_text("preserve")
    with pytest.raises(ValueError, match="非空"):
        migration_plan(source, layout)
