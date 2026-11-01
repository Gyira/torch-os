"""自我档案 v1 → v2 迁移：预览、备份、归位、幂等。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app import self_archive, self_migration, vault_layout


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_old_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault_layout.ensure_layout(vault)
    write(vault / "01_自我档案/01_内核与三观/身份（identity）.md",
          "---\ntype: identity\nname: 王衡岳\nworldview: 世界基本可理解\n"
          "lifeview: 成长最重要\nvalues: 不骗人\nmottos:\n- 去看更大的世界\n---\n\n")
    write(vault / "01_自我档案/02_记忆与自传/2025-09 入职.md",
          "---\ntype: memory\ntitle: 入职\ndate: 2025-09-01\n---\n\n九月入职铁路工务段。\n")
    write(vault / "01_自我档案/03_想法与灵感/想法.md",
          "---\ntype: idea\ntitle: 想法\n---\n\n也许可以试试固定槽位。\n")
    write(vault / "01_自我档案/07_我的八字参考/我的八字参考.md",
          "---\ntype: bazi\ntitle: 我的八字参考\n八字: 癸未 辛酉 丙戌 戊子\n"
          "日主: 丙火\n格局: 正财格\n---\n\n> **王衡岳** · 男 · 二〇〇三年九月十日 子时 · 江西赣州章贡区\n\n"
          "## 命盘\n\n丙火身弱。\n")
    write(vault / "01_自我档案/07_我的八字参考/八字速查卡.md",
          "---\ntype: bazi\ntitle: 八字速查卡\n八字: 癸未 辛酉 丙戌 戊子\n---\n\n一页看完。\n")
    write(vault / "01_自我档案/08_忆灵观察/观察-1.md",
          "---\ntype: observation\ntitle: 观察\n---\n\n旧观察。\n")
    write(vault / ".torch-os/忆灵/画像.json", '{"portrait": "旧画像"}\n')
    write(vault / ".torch-os/身份待确认/候选.md",
          "---\nkind: field\nfield: name\ntext: 小林\n---\n")
    return vault


def test_preview_lists_plan_without_writing(tmp_path):
    vault = make_old_vault(tmp_path)
    before = sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*.md"))
    plan = self_migration.preview(vault)
    assert plan["needed"] is True
    assert plan["files"] >= 5
    kinds = {row["kind"] for row in plan["plan"]}
    assert {"entry", "bazi", "yiling_history"} <= kinds
    after = sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*.md"))
    assert before == after


def test_apply_requires_confirmation(tmp_path):
    vault = make_old_vault(tmp_path)
    with pytest.raises(ValueError):
        self_migration.apply(vault, confirm=False)


def test_apply_migrates_entries_bazi_and_history(tmp_path):
    vault = make_old_vault(tmp_path)
    result = self_migration.apply(vault, confirm=True)
    assert result["status"] == "done"
    assert result["moved"] >= 5
    assert (vault / result["backup"]).is_dir()
    assert (vault / result["backup"] / "01_内核与三观" / "身份（identity）.md").is_file()
    # 身份文件归位；三观字段变成条目。
    assert (vault / vault_layout.SELF_IDENTITY_RELATIVE).is_file()
    values = self_archive.list_entries(vault, "self_values")
    titles = {item["title"] for item in values}
    assert {"世界观", "人生观", "价值观"} <= titles
    # 旧记忆/想法归位。
    assert self_archive.list_entries(vault, "self_timeline")
    assert self_archive.list_entries(vault, "self_paradox")
    # 八字：命盘 + 两篇解读（速查卡 + 原参考）。
    profile = vault / vault_layout.SELF_BAZI_ROOT / "命盘.md"
    assert profile.is_file()
    assert "癸未 辛酉 丙戌 戊子" in profile.read_text(encoding="utf-8")
    readings = list((vault / vault_layout.SELF_BAZI_READINGS_DIR).glob("*.md"))
    assert len(readings) >= 2
    # 旧忆灵观察进历史；.torch-os 画像复制进历史。
    assert list((vault / vault_layout.SELF_MENTOR_OLD_OBSERVATIONS).glob("*.md"))
    assert (vault / vault_layout.SELF_MENTOR_HISTORY_DIR / "旧忆灵画像.json").is_file()
    assert (vault / vault_layout.SELF_MENTOR_HISTORY_DIR / "旧身份候选" / "候选.md").is_file()
    # 原始旧文件移进备份，不再占旧章节。
    assert not list((vault / "01_自我档案/02_记忆与自传").glob("*.md"))
    marker = self_migration.preview(vault)["marker"]
    assert marker["schema"] == vault_layout.SELF_ARCHIVE_LAYOUT_SCHEMA


def test_apply_is_idempotent(tmp_path):
    vault = make_old_vault(tmp_path)
    self_migration.apply(vault, confirm=True)
    again = self_migration.apply(vault, confirm=True)
    assert again["status"] == "already_done"
    assert again["moved"] == 0
