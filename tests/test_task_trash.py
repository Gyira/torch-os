"""任务回收站：文件/文件夹条目、按状态恢复、清单定义恢复。"""

from __future__ import annotations

from backend.app import task_notes, task_trash


def fields(**changes):
    base = {"date": "", "time": "", "priority": "none", "tags": [],
            "done": False, "done_on": "", "pinned": False, "created": "2026-10-30"}
    base.update(changes)
    return base


def test_restore_routes_done_to_completed_and_open_to_inbox(tmp_path):
    open_note = task_notes.create_task_note(tmp_path, "", "普通任务", fields())
    done_note = task_notes.create_task_note(tmp_path, "工作", "完成任务", fields())
    done_note = task_notes.rewrite_task_note(
        tmp_path, done_note, folder="工作", title=done_note["title"],
        values=fields(done=True, done_on="2026-10-30"))

    task_notes.trash_note(tmp_path, open_note)
    task_notes.trash_note(tmp_path, done_note)

    items = task_trash.list_trash(tmp_path)
    assert {item["title"] for item in items} == {"普通任务", "完成任务"}
    for item in items:
        result = task_trash.restore_trash(tmp_path, paths=[item["trash_path"]])
        assert result["restored"] == 1

    assert (task_notes.task_root(tmp_path) / "收集箱" / "普通任务.md").is_file()
    restored_done = task_notes.task_root(tmp_path) / "已完成" / "完成任务.md"
    assert restored_done.is_file()
    note = task_notes.read_task_note(tmp_path, restored_done)
    assert note is not None and note["done"] is True and note["list_name"] == "工作"
    assert task_trash.list_trash(tmp_path) == []


def test_deleted_list_folder_restores_with_completed_tasks(tmp_path):
    open_note = task_notes.create_task_note(tmp_path, "工作", "清单任务", fields())
    done_note = task_notes.create_task_note(tmp_path, "工作", "已完成任务", fields())
    done_note = task_notes.rewrite_task_note(
        tmp_path, done_note, folder="工作", title=done_note["title"],
        values=fields(done=True, done_on="2026-10-30"))

    result = task_notes.trash_list_folder(
        tmp_path, "工作", list_definition={"名称": "工作", "颜色": "#22c55e"})
    assert result["count"] == 2
    assert not (task_notes.task_root(tmp_path) / "工作").exists()

    items = task_trash.list_trash(tmp_path)
    folder = next(item for item in items if item["kind"] == "folder")
    assert folder["title"] == "工作" and folder["count"] == 1
    assert "清单任务" not in {item["title"] for item in items if item["kind"] == "file"}

    restored = task_trash.restore_trash(tmp_path, paths=[folder["trash_path"]])
    assert restored["restored"] == 2
    assert restored["list_definitions"] == [{"名称": "工作", "颜色": "#22c55e"}]
    assert (task_notes.task_root(tmp_path) / "工作" / "清单任务.md").is_file()
    assert (task_notes.task_root(tmp_path) / "已完成" / "已完成任务.md").is_file()
    assert task_trash.list_trash(tmp_path) == []


def test_purge_removes_file_and_folder_entries(tmp_path):
    note = task_notes.create_task_note(tmp_path, "工作", "清单任务", fields())
    task_notes.trash_list_folder(tmp_path, "工作")
    items = task_trash.list_trash(tmp_path)
    folder = next(item for item in items if item["kind"] == "folder")
    purged = task_trash.purge_trash(tmp_path, paths=[folder["trash_path"]])
    assert purged["deleted"] == 1
    assert not (tmp_path / folder["trash_path"]).exists()
    assert task_trash.list_trash(tmp_path) == []
    assert note["path"].exists() is False
