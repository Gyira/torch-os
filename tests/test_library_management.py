import pytest
from fastapi import HTTPException

from backend.app import library_management as library, notebooks, note_editor, pillar_notes, vault_layout, recycle
from backend.app.db import Database


@pytest.fixture
def setup(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    db = Database(tmp_path / "state.sqlite")
    db.initialize()
    return vault, db


def test_titles_persist_without_changing_notebook_identity(setup):
    vault, db = setup
    book = notebooks.create_notebook(db, vault, "测试本")
    for key, title in [("essay", "随心记录"), (book["key"], "我的资料")]:
        library.set_notebook_title(db, vault, library.NotebookTitleRequest(pillar="life", key=key, title=title))
        assert library.notebook_titles(vault, "life")[key] == title
    assert (vault / book["path"]).is_dir()
    assert library.notebook_titles(vault, "knowledge") == {}


def test_missing_deleted_and_readonly_notebook_titles_rejected(setup):
    vault, db = setup
    for key in ["custom:不存在", "reflection"]:
        if key == "reflection":
            vault_layout.mark_notebook_deleted(vault, "life", key)
        with pytest.raises(HTTPException) as error:
            library.set_notebook_title(db, vault, library.NotebookTitleRequest(pillar="life", key=key, title="新名"))
        assert error.value.status_code == 404
    with pytest.raises(HTTPException):
        library.set_notebook_title(db, vault, library.NotebookTitleRequest(pillar="life", key="essay", title="新名"), read_only=True)
    assert not (vault / ".torch-os/notebook-titles.json").exists()


def test_renamed_notebook_keeps_title_in_recycle_and_restores_files(setup):
    vault, db = setup
    book = notebooks.create_notebook(db, vault, "原文件夹")
    note = note_editor.create_note(db, vault, chapter=book["key"], title="记录", body="保留")
    library.set_notebook_title(db, vault, library.NotebookTitleRequest(pillar="life", key=book["key"], title="新的本子标题"))
    notebooks.delete_notebook(db, vault, pillar="life", key=book["key"], confirm=True)
    row = next(item for group in recycle.list_recycle(vault)["groups"] for item in group["items"] if item["kind"] == "notebook")
    assert row["title"] == "新的本子标题"
    result = recycle.restore_recycle(db, vault, paths=[row["trash_path"]])
    assert result["restored"] == 1 and (vault / note["path"]).exists()
    assert library.notebook_titles(vault, "life")[book["key"]] == "新的本子标题"


def test_recreated_folder_does_not_inherit_deleted_book_title(setup):
    vault, db = setup
    book = notebooks.create_notebook(db, vault, "相同目录")
    library.set_notebook_title(db, vault, library.NotebookTitleRequest(pillar="life", key=book["key"], title="旧本子标题"))
    notebooks.delete_notebook(db, vault, pillar="life", key=book["key"], confirm=True)
    notebooks.create_notebook(db, vault, "相同目录")
    assert book["key"] not in library.notebook_titles(vault, "life")
    # Remove only the newly created empty folder, then restore the older batch.
    (vault / book["path"]).rmdir()
    row = next(item for group in recycle.list_recycle(vault)["groups"] for item in group["items"] if item["kind"] == "notebook")
    assert recycle.restore_recycle(db, vault, paths=[row["trash_path"]])["restored"] == 1
    assert library.notebook_titles(vault, "life")[book["key"]] == "旧本子标题"


def test_file_title_conflict_move_and_recycle(setup):
    vault, db = setup
    book = notebooks.create_notebook(db, vault, "移动目标本")
    note = note_editor.create_note(db, vault, chapter="essay", title="旧标题", body="正文")
    title = library.LibraryNoteRequest(action="title", path=note["path"], expected_revision=note["revision"], title="新标题")
    updated = library.manage_note(db, vault, title)
    assert updated["title"] == "新标题" and updated["body"] == note["body"] and updated["path"] == note["path"]
    with pytest.raises(HTTPException) as conflict:
        library.manage_note(db, vault, title)
    assert conflict.value.status_code == 409
    moved = library.manage_note(db, vault, library.LibraryNoteRequest(action="move", path=updated["path"], expected_revision=updated["revision"], target_chapter=book["key"]))
    assert not (vault / note["path"]).exists()
    assert (vault / moved["path"]).is_file()
    deleted = library.manage_note(db, vault, library.LibraryNoteRequest(action="delete", path=moved["path"], expected_revision=moved["revision"]))
    assert not (vault / moved["path"]).exists()
    assert (vault / deleted["trash_path"]).is_file()


def test_journal_title_and_delete_and_cross_library_guard(setup):
    vault, db = setup
    path = vault / vault_layout.chapter_dir("journal") / "2026-11-01.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\ndate: 2026-11-01\n---\n\n旧日记", encoding="utf-8")
    note = pillar_notes.read_note(vault, path.relative_to(vault).as_posix())
    with pytest.raises(HTTPException):
        library.manage_note(db, vault, library.LibraryNoteRequest(action="move", path=note["path"], expected_revision=note["revision"], target_chapter="inbox"))
    assert path.exists()
    updated = library.manage_note(db, vault, library.LibraryNoteRequest(action="title", path=note["path"], expected_revision=note["revision"], title="有意义的一天"))
    assert updated["title"] == "有意义的一天"
    result = library.manage_note(db, vault, library.LibraryNoteRequest(action="delete", path=updated["path"], expected_revision=updated["revision"]))
    assert not path.exists() and (vault / result["trash_path"]).is_file()
