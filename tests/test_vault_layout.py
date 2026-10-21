"""四库布局：章节清单、路径策略与目录创建。"""

from __future__ import annotations

from pathlib import Path

from backend.app import vault_layout


def test_chapter_manifest_covers_the_four_pillars():
    manifest = vault_layout.chapter_manifest()
    assert manifest["layout_version"] == "pillars-v1"
    assert [pillar["key"] for pillar in manifest["pillars"]] == [
        "life", "self", "ops", "knowledge",
    ]
    paths = {chapter["path"] for pillar in manifest["pillars"]
             for chapter in pillar["chapters"]}
    assert "00_人生路/01_日记" in paths
    assert "01_自我档案/01_内核与三观" in paths
    assert "02_生活运营/01_健康与体重" in paths
    assert "03_知识库/00_收件箱" in paths
    assert len(paths) == len(vault_layout.CHAPTERS)


def test_index_policy_separates_system_journal_and_memory_sources():
    assert vault_layout.index_policy("00-System/任务/某任务.md") == "skip"
    assert vault_layout.index_policy(".torch-os/notes.md") == "skip"
    assert vault_layout.index_policy("dashboard.md", {"dashboard": True}) == "skip"
    assert vault_layout.index_policy("00_人生路/01_日记/2026-10-20.md") == "document_only"
    assert vault_layout.index_policy("02_生活运营/01_健康与体重/2026-10.md") == "document_only"
    assert vault_layout.index_policy("01_自我档案/01_内核与三观/身份.md") == "memory_eligible"
    assert vault_layout.index_policy("00_人生路/02_反思/某日.md", {"memory": True}) == "memory_eligible"


def test_pillar_and_chapter_lookup():
    assert vault_layout.pillar_of("00_人生路/01_日记/a.md") == "life"
    assert vault_layout.pillar_of("03_知识库/00_收件箱/a.md") == "knowledge"
    assert vault_layout.pillar_of("00-System/任务/a.md") == ""
    assert vault_layout.chapter_key_of("00_人生路/06_目标与计划/05_项目计划/a.md") == "project_plan"
    assert vault_layout.chapter_key_of("03_知识库/05_方法库/02_通用方法/a.md") == "method_general"


def test_ensure_layout_creates_pillars_and_chapters(tmp_path):
    created = vault_layout.ensure_layout(tmp_path)
    assert "00_人生路" in created
    assert (tmp_path / "00_人生路/01_日记").is_dir()
    assert (tmp_path / "03_知识库/00_收件箱").is_dir()
    assert vault_layout.layout_status(tmp_path)["state"] == "v1"


def test_validate_target_only_allows_the_four_pillars():
    assert vault_layout.validate_target("03_知识库/00_收件箱/x.md") == "03_知识库/00_收件箱/x.md"
    for bad in ("00-System/任务/x.md", "dashboard.md", "../outside.md", "随便/x.md"):
        try:
            vault_layout.validate_target(bad)
        except ValueError:
            continue
        raise AssertionError(f"应当拒绝：{bad}")


def test_safe_note_stem_removes_forbidden_characters():
    assert "/" not in vault_layout.safe_note_stem("a/b:c*d")
    assert vault_layout.safe_note_stem("   ") == "未命名"
    assert vault_layout.safe_note_stem("CON") == "笔记-CON"
