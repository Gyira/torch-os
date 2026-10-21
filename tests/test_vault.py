from pathlib import Path

from backend.app.db import Database
from backend.app.vault import managed_note_path, note_relative, parse_markdown, scan_vault


def test_parse_markdown_frontmatter(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("---\ntitle: 测试\ntype: experience\n---\n\n正文内容", encoding="utf-8")
    metadata, body = parse_markdown(note)
    assert metadata["title"] == "测试"
    assert body == "正文内容"


def test_scan_vault_is_incremental(tmp_path: Path):
    vault = tmp_path / "vault"
    memory_dir = vault / "01_自我档案" / "02_记忆与自传"
    memory_dir.mkdir(parents=True)
    (memory_dir / "goal.md").write_text(
        "---\ntitle: 目标\ntype: goal\n---\n\n完成一个闭环", encoding="utf-8")
    db = Database(tmp_path / "test.sqlite3")
    db.initialize()
    first = scan_vault(vault, db)
    second = scan_vault(vault, db)
    assert first["changed"] == 1
    assert second["changed"] == 0
    assert len(db.list_memories("candidate")) == 1


def test_scan_vault_only_extracts_memories_from_memory_eligible_paths(tmp_path: Path):
    vault = tmp_path / "vault"
    note_dir = vault / "00_人生路" / "01_日记"
    note_dir.mkdir(parents=True)
    (note_dir / "2026-10-20.md").write_text(
        "---\ntitle: 日记\ntype: journal\n---\n\n今天想清楚了一个重要问题。", encoding="utf-8")
    db = Database(tmp_path / "test.sqlite3")
    db.initialize()
    assert scan_vault(vault, db)["changed"] == 1
    assert db.list_memories("candidate") == []


def test_managed_note_path_keeps_old_files_working(tmp_path: Path):
    """2026-10-17 改名成「中文（english）.md」后，旧库里的 `任务.md` 必须继续被读写。"""
    current_rel = "00-System/任务（tasks）.md"
    legacy_rel = "00-System/任务.md"
    legacy = tmp_path / legacy_rel
    legacy.parent.mkdir(parents=True)
    legacy.write_text("旧文件", encoding="utf-8")
    assert managed_note_path(tmp_path, current_rel, legacy_rel) == legacy  # 只有旧文件 → 用旧的
    assert note_relative(legacy, tmp_path) == legacy_rel

    current = tmp_path / current_rel
    current.write_text("新文件", encoding="utf-8")
    assert managed_note_path(tmp_path, current_rel, legacy_rel) == current  # 两个都在 → 用新的

    empty = tmp_path / "empty"
    empty.mkdir()
    assert managed_note_path(empty, current_rel, legacy_rel) == empty / current_rel  # 全新库 → 新名字
