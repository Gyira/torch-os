import pytest
from fastapi import HTTPException

from backend.app import notebooks, note_editor, pillars, recycle, vault_layout as layout
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
    return path


def test_sub_notebook_is_a_nested_folder_and_key(setup):
    vault, db = setup
    parent = notebooks.create_notebook(db, vault, "学习")
    child = notebooks.create_notebook(db, vault, "数学", parent="学习")
    assert child["path"] == "人生路/学习/数学"
    assert child["key"] == "custom:学习/数学"
    assert child["parent"] == parent["key"]
    assert (vault / child["path"]).is_dir()
    assert layout.custom_notebook_name_from_relpath(
        "人生路/学习/数学/笔记.md") == "学习/数学"
    assert layout.chapter_key_of("人生路/学习/数学/笔记.md") == "custom:学习/数学"
    assert layout.list_all_custom_notebooks(vault, "life") == ["学习", "学习/数学"]


def test_sub_notebook_creation_rejects_missing_parent_and_bad_depth(setup):
    vault, db = setup
    with pytest.raises(HTTPException) as missing:
        notebooks.create_notebook(db, vault, "数学", parent="不存在")
    assert missing.value.status_code == 422

    name = ""
    for index in range(layout.CUSTOM_NOTEBOOK_MAX_DEPTH):
        segment = f"层{index}"
        notebooks.create_notebook(db, vault, segment, parent=name)
        name = f"{name}/{segment}" if name else segment
    with pytest.raises(HTTPException) as too_deep:
        notebooks.create_notebook(db, vault, "再深一层", parent=name)
    assert too_deep.value.status_code == 422

    notebooks.create_notebook(db, vault, "重复测")
    notebooks.create_notebook(db, vault, "子", parent="重复测")
    with pytest.raises(HTTPException) as again:
        notebooks.create_notebook(db, vault, "子", parent="重复测")
    assert again.value.status_code == 409


def test_notes_in_sub_notebook_count_up_and_move(setup):
    vault, db = setup
    parent = notebooks.create_notebook(db, vault, "写作")
    child = notebooks.create_notebook(db, vault, "杂记", parent="写作")
    note = note_editor.create_note(db, vault, chapter=child["key"],
                                   title="第一篇", body="正文")
    assert note["path"] == "人生路/写作/杂记/第一篇.md"

    overview = pillars._overview(vault, db, "life")
    rows = {row["name"]: row for row in overview["custom_notebooks"]}
    assert rows["写作"]["count"] == 1
    assert rows["写作/杂记"]["count"] == 1
    assert rows["写作/杂记"]["parent"] == parent["key"]
    assert rows["写作/杂记"]["title"] == "杂记"

    moved = note_editor.move_note(
        db, vault, path=note["path"], target_chapter="essay",
        expected_revision=note["revision"])
    assert moved["chapter"] == "essay"
    back = note_editor.move_note(
        db, vault, path=moved["path"], target_chapter=child["key"],
        expected_revision=moved["revision"])
    assert back["path"] == "人生路/写作/杂记/第一篇.md"
    assert (vault / back["path"]).is_file()
    assert layout.custom_notebook_pillar(child["key"]) == "life"
    assert child["key"] in {
        layout.custom_notebook_key(name, "life")
        for name in layout.list_all_custom_notebooks(vault, "life")
    }


def test_deleting_sub_notebook_moves_only_its_folder_and_restores(setup):
    vault, db = setup
    notebooks.create_notebook(db, vault, "父本子")
    child = notebooks.create_notebook(db, vault, "子本子", parent="父本子")
    note = note_editor.create_note(db, vault, chapter=child["key"],
                                   title="保留", body="正文")
    sibling = write(vault / "人生路/父本子/兄弟.md", "sibling")

    deleted = notebooks.delete_notebook(
        db, vault, pillar="life", key=child["key"], confirm=True)
    assert not (vault / child["path"]).exists()
    assert (vault / "人生路/父本子").is_dir()
    assert sibling.read_text(encoding="utf-8") == "sibling"

    row = next(
        item for group in recycle.list_recycle(vault)["groups"]
        for item in group["items"] if item["kind"] == "notebook")
    assert row["title"] == "子本子"
    restored = recycle.restore_recycle(db, vault, paths=[row["trash_path"]])
    assert restored["restored"] == 1, restored
    assert (vault / note["path"]).is_file()
    assert not (vault / deleted["trash_batch"]).exists()


def test_sub_notebook_cannot_escape_root_via_parent(setup):
    vault, db = setup
    notebooks.create_notebook(db, vault, "正常")
    for parent in ["../外面", "正常/../外面", "正常//子"]:
        with pytest.raises(HTTPException) as error:
            notebooks.create_notebook(db, vault, "子", parent=parent)
        assert error.value.status_code == 422
    assert not (vault.parent / "外面").exists()
