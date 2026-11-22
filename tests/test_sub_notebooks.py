import pytest
from fastapi import HTTPException

from backend.app import notebooks, note_editor, pillar_notes, pillars, recycle, vault_layout as layout
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


def test_builtin_chapter_sub_notebook_is_registered_and_counted(setup):
    vault, db = setup
    write(vault / layout.chapter_dir("essay") / "已有随笔.md")
    sub = notebooks.create_notebook(
        db, vault, "灵感", pillar="life", parent_chapter="essay")
    assert sub["key"] == "sub:essay:灵感"
    assert sub["path"] == "人生路/随笔/灵感"
    assert layout.sub_notebook_exists(vault, "essay", "灵感")
    assert not layout.is_sub_notebook_key("essay")

    note = note_editor.create_note(db, vault, chapter=sub["key"],
                                   title="小点子", body="正文")
    assert note["path"] == "人生路/随笔/灵感/小点子.md"
    # 内置子本子不改变基础章节：随笔的浏览/计数仍然完整。
    assert note["chapter"] == "essay"
    overview = pillars._overview(vault, db, "life")
    row = next(item for item in overview["sub_notebooks"] if item["key"] == sub["key"])
    assert row["chapter"] == "essay" and row["count"] == 1
    essay_row = next(item for item in overview["chapters"] if item["key"] == "essay")
    assert essay_row["count"] == 2
    assert len(pillar_notes.list_notes(vault, chapter=sub["key"])) == 1


def test_unregistered_subfolder_is_not_a_builtin_sub_notebook(setup):
    vault, db = setup
    folder = vault / layout.chapter_dir("essay") / "普通文件夹"
    folder.mkdir(parents=True)
    (folder / "笔记.md").write_text("正文", encoding="utf-8")
    overview = pillars._overview(vault, db, "life")
    assert all(item["key"] != "sub:essay:普通文件夹"
               for item in overview.get("sub_notebooks", []))


def test_builtin_sub_notebook_delete_and_restore_keeps_registration(setup):
    vault, db = setup
    write(vault / layout.chapter_dir("essay") / "保留在父本子.md")
    parent = notebooks.create_notebook(
        db, vault, "专题", pillar="life", parent_chapter="essay")
    child = notebooks.create_notebook(
        db, vault, "子层", pillar="life", parent_chapter="essay", parent="专题")
    note = note_editor.create_note(db, vault, chapter=child["key"],
                                   title="子层笔记", body="正文")
    assert child["key"] == "sub:essay:专题/子层"

    notebooks.delete_notebook(db, vault, pillar="life", key=parent["key"], confirm=True)
    assert not (vault / parent["path"]).exists()
    assert (vault / layout.chapter_dir("essay") / "保留在父本子.md").is_file()
    assert not layout.sub_notebook_exists(vault, "essay", "专题")
    assert not layout.sub_notebook_exists(vault, "essay", "专题/子层")

    row = next(item for group in recycle.list_recycle(vault)["groups"]
               for item in group["items"] if item["kind"] == "notebook")
    assert row["title"] == "专题"
    restored = recycle.restore_recycle(db, vault, paths=[row["trash_path"]])
    assert restored["restored"] == 1, restored
    assert (vault / note["path"]).is_file()
    assert layout.sub_notebook_exists(vault, "essay", "专题")
    assert layout.sub_notebook_exists(vault, "essay", "专题/子层")


def test_journal_and_habit_do_not_support_sub_notebooks(setup):
    vault, db = setup
    for chapter in ["journal", "habit"]:
        with pytest.raises(HTTPException) as error:
            notebooks.create_notebook(
                db, vault, "子本", pillar="life", parent_chapter=chapter)
        assert error.value.status_code == 422


def test_builtin_sub_notebook_rejects_hidden_base_chapter(setup):
    vault, db = setup
    layout.mark_notebook_deleted(vault, "life", "reflection")
    with pytest.raises(HTTPException) as error:
        notebooks.create_notebook(db, vault, "专题", pillar="life", parent_chapter="reflection")
    assert error.value.status_code == 422
