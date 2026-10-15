from pathlib import Path

from backend.app.db import Database
from backend.app.vault import parse_markdown, scan_vault


def test_parse_markdown_frontmatter(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("---\ntitle: 测试\ntype: experience\n---\n\n正文内容", encoding="utf-8")
    metadata, body = parse_markdown(note)
    assert metadata["title"] == "测试"
    assert body == "正文内容"


def test_scan_vault_is_incremental(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "goal.md").write_text("---\ntitle: 目标\ntype: goal\n---\n\n完成一个闭环", encoding="utf-8")
    db = Database(tmp_path / "test.sqlite3")
    db.initialize()
    first = scan_vault(vault, db)
    second = scan_vault(vault, db)
    assert first["changed"] == 1
    assert second["changed"] == 0
    assert len(db.list_memories("candidate")) == 1
