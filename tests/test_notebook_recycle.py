import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app import notebooks, recycle, vault_layout as layout
from backend.app.db import Database


@pytest.fixture
def setup(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    db = Database(tmp_path / "state.sqlite")
    db.initialize()
    return vault, db


def write(path, text="kept"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def items(vault):
    return [item for group in recycle.list_recycle(vault)["groups"] for item in group["items"]]


def test_builtin_whole_folder_with_attachments_restores_and_unhides(setup):
    vault, db = setup
    note = vault / layout.chapter_dir("reflection") / "反思.md"
    parent = note.parent.parent
    write(note)
    write(parent / "附件" / "图片.bin", "image")
    deleted = notebooks.delete_notebook(db, vault, pillar="life", key="reflection", confirm=True)
    assert not parent.exists()
    row = items(vault)[0]
    assert row["kind"] == "notebook" and row["title"] == "反思复盘" and row["count"] == 1
    assert layout.is_notebook_deleted(vault, "life", "reflection")
    restored = recycle.restore_recycle(db, vault, paths=[row["trash_path"]])
    assert restored["restored"] == 1, restored
    assert note.read_text() == "kept"
    assert (parent / "附件" / "图片.bin").read_text() == "image"
    assert not layout.is_notebook_deleted(vault, "life", "reflection")
    assert not (vault / deleted["trash_batch"]).exists()
    assert not items(vault)


def test_custom_purge_removes_trash_folder_not_recreated_live_notebook(setup):
    vault, db = setup
    book = notebooks.create_notebook(db, vault, "测试本子")
    write(vault / book["path"] / "记事.md")
    deleted = notebooks.delete_notebook(db, vault, pillar="life", key=book["key"], confirm=True)
    write(vault / book["path"] / "新内容.md", "new")
    row = items(vault)[0]
    result = recycle.purge_recycle(db, vault, paths=[row["trash_path"]])
    assert result["deleted"] == 1, result
    assert not (vault / deleted["trash_batch"]).exists()
    assert (vault / book["path"] / "新内容.md").read_text() == "new"


def test_restore_collision_preserves_both_sides(setup):
    vault, db = setup
    book = notebooks.create_notebook(db, vault, "冲突本子")
    write(vault / book["path"] / "旧内容.md", "old")
    notebooks.delete_notebook(db, vault, pillar="life", key=book["key"], confirm=True)
    write(vault / book["path"] / "新内容.md", "new")
    row = items(vault)[0]
    result = recycle.restore_recycle(db, vault, paths=[row["trash_path"]])
    assert result["restored"] == 0 and result["skipped"]
    assert (vault / row["trash_path"]).exists()
    assert (vault / book["path"] / "新内容.md").read_text() == "new"


def test_legacy_chapter_batch_visible_and_purge_prunes_only_empty_parent(setup):
    vault, db = setup
    original = layout.chapter_dir("reflection")
    source = vault / original
    write(source / "旧笔记.md")
    batch = vault / notebooks.NOTEBOOK_TRASH_DIR / "legacy-batch"
    target = batch / "notebooks/life/reflection" / Path(*original.split("/")[1:])
    target.parent.mkdir(parents=True)
    os.replace(source, target)
    layout.mark_notebook_deleted(vault, "life", "reflection")
    write(batch / "manifest.json", json.dumps({"pillar": "life", "key": "reflection", "name": "反思复盘", "builtin": True,
                                              "original_paths": [original], "trash_paths": [target.relative_to(vault).as_posix()]}))
    row = items(vault)[0]
    assert row["restorable"] and row["count"] == 1
    result = recycle.purge_recycle(db, vault, paths=[row["trash_path"]])
    assert result["deleted"] == 1
    assert not source.parent.exists()
    assert (vault / layout.PILLAR_DIRS["life"]).exists()


def test_missing_manifest_custom_batch_can_be_recovered(setup):
    vault, db = setup
    book = notebooks.create_notebook(db, vault, "旧版遗留")
    write(vault / book["path"] / "内容.md")
    deleted = notebooks.delete_notebook(db, vault, pillar="life", key=book["key"], confirm=True)
    (vault / deleted["trash_batch"] / "manifest.json").unlink()
    row = items(vault)[0]
    assert row["restorable"]
    assert recycle.restore_recycle(db, vault, paths=[row["trash_path"]])["restored"] == 1
    assert (vault / book["path"] / "内容.md").is_file()


def test_empty_builtin_has_recoverable_record(setup):
    vault, db = setup
    notebooks.delete_notebook(db, vault, pillar="life", key="reflection", confirm=True)
    row = items(vault)[0]
    assert row["restorable"] and row["count"] == 0
    assert recycle.restore_recycle(db, vault, paths=[row["trash_path"]])["restored"] == 1
    assert (vault / layout.chapter_dir("reflection")).is_dir()


def test_tampered_original_is_visible_but_not_restorable(setup):
    vault, db = setup
    book = notebooks.create_notebook(db, vault, "路径校验")
    deleted = notebooks.delete_notebook(db, vault, pillar="life", key=book["key"], confirm=True)
    manifest_path = vault / deleted["trash_batch"] / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["original_path"] = "../../outside"
    write(manifest_path, json.dumps(data))
    row = items(vault)[0]
    assert not row["restorable"]
    assert recycle.restore_recycle(db, vault, paths=[row["trash_path"]])["restored"] == 0
    assert manifest_path.exists()


def test_read_only_never_purges_notebook(setup):
    vault, db = setup
    book = notebooks.create_notebook(db, vault, "只读")
    notebooks.delete_notebook(db, vault, pillar="life", key=book["key"], confirm=True)
    row = items(vault)[0]
    with pytest.raises(HTTPException):
        recycle.purge_recycle(db, vault, paths=[row["trash_path"]], read_only=True)
    assert (vault / row["trash_path"]).exists()


def test_missing_builtin_manifest_preserves_root_attachments(setup):
    vault, db = setup
    root = (vault / layout.chapter_dir("reflection")).parent
    write(root / "附件.png", "attachment")
    write(vault / layout.chapter_dir("reflection") / "笔记.md")
    deleted = notebooks.delete_notebook(db, vault, pillar="life", key="reflection", confirm=True)
    (vault / deleted["trash_batch"] / "manifest.json").unlink()
    row = items(vault)[0]
    assert row["restorable"]
    assert recycle.restore_recycle(db, vault, paths=[row["trash_path"]])["restored"] == 1
    assert (root / "附件.png").read_text(encoding="utf-8") == "attachment"
