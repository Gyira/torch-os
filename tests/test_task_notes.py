"""一任务一笔记的存储层：文件名、frontmatter、正文保留、扫描与回收站。"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app import task_notes
from backend.app.task_notes import (
    INBOX,
    board_revision,
    create_task_note,
    read_task_note,
    rewrite_task_note,
    safe_task_stem,
    scan_task_notes,
    task_root,
    trash_list_folder,
    trash_note,
)


def fields(**changes):
    base = {"date": "2026-10-17", "time": "09:00-10:30", "priority": "high",
            "tags": ["重要", "工作"], "done": False, "done_on": "",
            "pinned": True, "created": "2026-10-02"}
    base.update(changes)
    return base


def test_safe_stem_cleans_and_limits_names():
    assert "/" not in safe_task_stem("写方案/讨论")
    assert safe_task_stem("").startswith("未命名任务")
    assert safe_task_stem("CON").startswith("任务-CON")
    assert safe_task_stem("  .  ") == "未命名任务"
    long = safe_task_stem("长" * 300)
    assert len(long) <= task_notes.MAX_STEM_CHARS
    assert len(long.encode("utf-8")) <= task_notes.MAX_STEM_BYTES
    # 文件名不能以点开头/结尾，也不能保留 Windows 禁用字符。
    assert not safe_task_stem("...x...").startswith(".")
    assert not safe_task_stem("a:b?c*d").count(":")


def test_create_roundtrip_keeps_strings_and_body(tmp_path):
    note = create_task_note(tmp_path, "", "写方案", fields(), body="第一行\n\n第二行")
    assert note["path"] == task_root(tmp_path) / "写方案.md"
    assert note["title"] == "写方案"
    assert note["date"] == "2026-10-17"
    assert note["time"] == "09:00-10:30"
    assert note["priority"] == "high"
    assert note["tags"] == ["重要", "工作"]
    assert note["pinned"] is True and note["created"] == "2026-10-02"
    assert "标题" not in note["metadata"]
    text = note["path"].read_text(encoding="utf-8")
    assert "日期: \"2026-10-17\"" in text
    assert "时间: \"09:00-10:30\"" in text
    assert note["path"].read_text(encoding="utf-8").endswith("第一行\n\n第二行\n")


def test_duplicate_titles_number_files_but_keep_full_title(tmp_path):
    first = create_task_note(tmp_path, "", "同名任务", fields())
    second = create_task_note(tmp_path, "", "同名任务", fields())
    assert first["path"].name == "同名任务.md"
    assert second["path"].name == "同名任务 2.md"
    assert second["title"] == "同名任务"
    assert second["metadata"]["标题"] == "同名任务"

    # 大小写/NFC 不同的同名也视为冲突。
    third = create_task_note(tmp_path, "", "同名任务", fields())
    assert third["path"].name == "同名任务 3.md"
    assert unicodedata.normalize("NFC", third["title"]) == "同名任务"


def test_long_title_uses_frontmatter_override(tmp_path):
    title = "很长的任务标题" * 20
    note = create_task_note(tmp_path, "工作", title, fields())
    assert note["title"] == title
    assert note["metadata"]["标题"] == title
    assert len(note["path"].stem) < len(title)


def test_rewrite_preserves_unknown_frontmatter_body_and_crlf(tmp_path):
    note = create_task_note(tmp_path, "", "旧标题", fields(), body="只属于我的正文\n")
    raw = note["path"].read_bytes().decode("utf-8")
    raw = raw.replace("type: task", "type: task\n自定义: 保留我", 1)
    raw = raw.replace("\n", "\r\n")
    note["path"].write_bytes(raw.encode("utf-8"))
    note = read_task_note(tmp_path, note["path"])
    assert note is not None and note["metadata"]["自定义"] == "保留我"

    updated = rewrite_task_note(tmp_path, note, folder="工作", title="新标题",
                                values=fields(priority="low", tags=["新"]))
    assert updated["path"].name == "新标题.md"
    assert updated["title"] == "新标题"
    assert updated["metadata"]["自定义"] == "保留我"
    assert updated["metadata"]["优先级"] == "low"
    assert updated["metadata"]["标签"] == ["新"]
    new_raw = updated["path"].read_bytes()
    assert b"\r\n" in new_raw
    assert "只属于我的正文" in new_raw.decode("utf-8")
    assert not note["path"].exists()


def test_move_into_occupied_list_numbers_without_losing_title(tmp_path):
    moved = create_task_note(tmp_path, "", "去海边", fields())
    create_task_note(tmp_path, "心愿", "去海边", fields())
    updated = rewrite_task_note(tmp_path, moved, folder="心愿", title=moved["title"],
                                values=fields(date="", time="", priority="none", tags=[],
                                              done=True, done_on="2026-10-17", pinned=False))
    assert updated["path"].parent.name == "心愿"
    assert updated["path"].name == "去海边 2.md"
    assert updated["title"] == "去海边"
    assert updated["done"] is True and updated["done_on"] == "2026-10-17"
    assert updated["date"] == "" and updated["priority"] == "none"


def test_case_only_rename(tmp_path):
    note = create_task_note(tmp_path, "", "Foo", fields())
    updated = rewrite_task_note(tmp_path, note, folder="", title="foo", values=fields())
    assert updated["path"].name == "foo.md"
    assert updated["title"] == "foo"
    names = {path.name for path in updated["path"].parent.iterdir()}
    assert "foo.md" in names and "Foo.md" not in names


def test_scan_filters_non_tasks_and_collects_warnings(tmp_path):
    create_task_note(tmp_path, "", "正常任务", fields())
    create_task_note(tmp_path, "工作", "清单任务", fields())
    root = task_root(tmp_path)
    (root / "不是任务.md").write_text("---\ntype: note\n---\n随手记\n", encoding="utf-8")
    (root / "坏文件.md").write_text("---\ntype: task\n标题: 没结束\n", encoding="utf-8")
    (root / "附件.txt").write_text("not markdown", encoding="utf-8")
    nested = root / "工作" / "更深"
    nested.mkdir(parents=True)
    (nested / "深层.md").write_text("---\ntype: task\n---\n", encoding="utf-8")

    notes, warnings = scan_task_notes(tmp_path)
    assert {note["title"] for note in notes} == {"清单任务", "正常任务"}
    paths = {item["path"] for item in warnings}
    assert "坏文件.md" in paths
    assert "工作/更深/" in paths


def test_bad_yaml_is_not_editable(tmp_path):
    note = create_task_note(tmp_path, "", "坏 YAML", fields())
    note["path"].write_text("---\ntype: task\n标题: [\n---\n", encoding="utf-8")
    with pytest.raises(task_notes.TaskNoteFormatError):
        read_task_note(tmp_path, note["path"])


def test_trash_note_and_list_folder_safety(tmp_path):
    note = create_task_note(tmp_path, "", "先扔这里", fields())
    batch = trash_note(tmp_path, note)
    assert batch.startswith(".torch-os/task-trash/")
    assert not note["path"].exists()

    listed = create_task_note(tmp_path, "工作", "清单任务", fields())
    (listed["path"].parent / "附件.txt").write_text("附件", encoding="utf-8")
    with pytest.raises(HTTPException) as error:
        trash_list_folder(tmp_path, "工作")
    assert error.value.status_code == 409

    (listed["path"].parent / "附件.txt").unlink()
    config = tmp_path / "00-System" / "任务（tasks）.md"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("---\ntype: tasks\n---\n", encoding="utf-8")
    result = trash_list_folder(tmp_path, "工作", config_path=config)
    assert result["count"] == 1
    assert not listed["path"].exists()
    assert not (task_root(tmp_path) / "工作").exists()
    trash_files = list((tmp_path / result["batch"]).rglob("*"))
    assert any(path.name == "任务（tasks）.md" for path in trash_files)
    with pytest.raises(HTTPException):
        trash_list_folder(tmp_path, INBOX)


def test_trash_list_folder_moves_baidu_sync_sidecars(tmp_path):
    note = create_task_note(tmp_path, "工作", "清单任务", fields())
    sidecar = note["path"].with_name(note["path"].name + ".baiduyun.uploading.cfg")
    sidecar.write_text('{"state":"uploading"}', encoding="utf-8")

    result = trash_list_folder(tmp_path, "工作")

    assert result["count"] == 1
    assert not (task_root(tmp_path) / "工作").exists()
    trashed = {path.name for path in (tmp_path / result["batch"]).rglob("*") if path.is_file()}
    assert note["path"].name in trashed
    assert sidecar.name in trashed


def test_trash_list_folder_still_refuses_unknown_cfg(tmp_path):
    note = create_task_note(tmp_path, "工作", "清单任务", fields())
    (note["path"].parent / "附件.cfg").write_text("keep me", encoding="utf-8")

    with pytest.raises(HTTPException) as error:
        trash_list_folder(tmp_path, "工作")

    assert error.value.status_code == 409
    assert note["path"].exists()


def test_board_revision_changes_when_any_note_changes(tmp_path):
    first = create_task_note(tmp_path, "", "A", fields())
    create_task_note(tmp_path, "工作", "B", fields())
    notes, _warnings = scan_task_notes(tmp_path)
    revision = board_revision("cfg", notes)
    rewrite_task_note(tmp_path, first, folder="", title="A", values=fields(priority="low"))
    changed, _warnings = scan_task_notes(tmp_path)
    assert board_revision("cfg", changed) != revision
    assert board_revision("cfg2", changed) != board_revision("cfg", changed)


def test_sort_order_roundtrip_and_preserved_when_rewrite_omits_it(tmp_path):
    note = create_task_note(tmp_path, "", "可排序", fields(sort_order=1500))
    assert note["sort_order"] == 1500
    assert note["metadata"]["排序"] == 1500

    # 滴答回写等旧调用方不会传 sort_order：重写时不能把手动顺序丢掉。
    updated = rewrite_task_note(tmp_path, note, folder="", title=note["title"],
                                values=fields())
    assert updated["sort_order"] == 1500
    assert updated["metadata"]["排序"] == 1500

    fractional = create_task_note(tmp_path, "", "小数顺序", fields(sort_order=999.5))
    assert fractional["sort_order"] == 999.5
