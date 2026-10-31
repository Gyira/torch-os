"""四库布局：章节清单、分组、路径策略与目录创建（人生路 v2）。"""

from __future__ import annotations

from backend.app import vault_layout

MOVED_CHAPTERS = {
    "reflection": ("00_人生路/02_反思", "00_人生路/02_反思复盘/01_反思"),
    "review_week": ("00_人生路/04_周复盘", "00_人生路/02_反思复盘/02_周复盘"),
    "review_month": ("00_人生路/05_月复盘", "00_人生路/02_反思复盘/03_月复盘"),
    "goal_year": ("00_人生路/06_目标与计划/01_年度目标", "00_人生路/04_目标/01_年度目标"),
    "goal_quarter": ("00_人生路/06_目标与计划/02_季度目标", "00_人生路/04_目标/02_季度目标"),
    "goal_month": ("00_人生路/06_目标与计划/03_月度目标", "00_人生路/04_目标/03_月度目标"),
    "project_plan": ("00_人生路/06_目标与计划/05_项目计划", "00_人生路/05_项目/01_项目计划"),
    "work_review_project": ("00_人生路/07_工作复盘/01_项目复盘", "00_人生路/05_项目/02_项目复盘"),
    "habit": ("00_人生路/06_目标与计划/04_习惯追踪", "00_人生路/04_目标/04_习惯追踪"),
}

UNCHANGED_CHAPTERS = {
    "journal": "00_人生路/01_日记",
    "insight_life": "00_人生路/03_心得体会/01_生活心得",
    "insight_work": "00_人生路/03_心得体会/02_工作心得",
    "insight_study": "00_人生路/03_心得体会/03_学习心得",
    "work_review_meeting": "00_人生路/06_工作复盘/02_会议收获",
    "work_review_pitfall": "00_人生路/06_工作复盘/03_踩坑记录",
}


def test_chapter_manifest_covers_the_four_pillars():
    manifest = vault_layout.chapter_manifest()
    assert manifest["layout_version"] == "pillars-v2"
    assert [pillar["key"] for pillar in manifest["pillars"]] == [
        "life", "self", "ops", "knowledge",
    ]
    paths = {chapter["path"] for pillar in manifest["pillars"]
             for chapter in pillar["chapters"]}
    assert "00_人生路/02_反思复盘/01_反思" in paths
    assert "00_人生路/04_目标/01_年度目标" in paths
    assert "00_人生路/05_项目/01_项目计划" in paths
    assert "01_自我档案/01_内核与三观" in paths
    assert "02_生活运营/02_健康系统" in paths
    assert "03_知识库/00_收件箱" in paths
    assert len(paths) == len(vault_layout.CHAPTERS)


def test_moved_chapters_use_v2_paths_and_others_keep_v1_paths():
    for chapter, (old, new) in MOVED_CHAPTERS.items():
        assert vault_layout.chapter_dir(chapter) == new, chapter
        assert old not in {item[3] for item in vault_layout.CHAPTERS}, chapter
    for chapter, path in UNCHANGED_CHAPTERS.items():
        assert vault_layout.chapter_dir(chapter) == path, chapter
    assert vault_layout.chapter_dir("task") == "02_生活运营/00_待办事项"
    assert vault_layout.chapter_key_of("00_人生路/05_项目/01_项目计划/a.md") == "project_plan"
    assert vault_layout.chapter_key_of("00_人生路/02_反思复盘/03_月复盘/2026-10.md") == "review_month"
    assert vault_layout.chapter_key_of("00_人生路/04_目标/04_习惯追踪/a.md") == "habit"


def test_manifest_exposes_life_groups_without_reordering_chapters():
    manifest = vault_layout.chapter_manifest()
    pillars = {pillar["key"]: pillar for pillar in manifest["pillars"]}
    assert pillars["life"]["groups"] == [
        {"key": "insight", "title": "心得体会",
         "chapters": ["insight_life", "insight_work", "insight_study"]},
        {"key": "review", "title": "反思复盘",
         "chapters": ["reflection", "review_week", "review_month"]},
        {"key": "goal", "title": "目标",
         "chapters": ["goal_year", "goal_quarter", "goal_month"]},
        {"key": "project", "title": "项目",
         "chapters": ["project_plan", "work_review_project"]},
    ]
    for key in ("self", "ops", "knowledge"):
        assert pillars[key]["groups"] == []

    life_keys = [chapter["key"] for chapter in pillars["life"]["chapters"]]
    assert life_keys == [item[0] for item in vault_layout.CHAPTERS if item[1] == "life"]
    # 未分组章节的相对位置不变：日记在最前，习惯在目标与项目之间。
    assert life_keys[:1] == ["journal"]
    assert "task" not in life_keys
    assert life_keys.index("habit") == life_keys.index("goal_month") + 1
    assert life_keys.index("project_plan") == life_keys.index("habit") + 1
    ops_keys = [chapter["key"] for chapter in pillars["ops"]["chapters"]]
    assert ops_keys[0] == "task"
    task_chapter = pillars["ops"]["chapters"][0]
    assert task_chapter["title"] == "待办事项"
    assert task_chapter["path"] == "02_生活运营/00_待办事项"
    assert "待办事项" in {row[2] for row in vault_layout.CHAPTERS}
    for pillar in manifest["pillars"]:
        chapter_keys = {chapter["key"] for chapter in pillar["chapters"]}
        grouped = [key for group in pillar["groups"] for key in group["chapters"]]
        assert set(grouped) <= chapter_keys
        assert len(grouped) == len(set(grouped)), "一个章节只能出现在一个分组里"


def test_v1_chapter_move_maps_only_the_moved_directories():
    for chapter, (old, new) in MOVED_CHAPTERS.items():
        assert vault_layout.v1_chapter_move(f"{old}/子目录/笔记.md") == (
            f"{new}/子目录/笔记.md", chapter)
    for path in (
        "00_人生路/01_日记/2026-10-20.md",
        "02_生活运营/00_待办事项/收集箱/写方案.md",
        "00_人生路/03_心得体会/01_生活心得/早起.md",
        "00_人生路/06_工作复盘/02_会议收获/周会.md",
        "00_人生路/06_工作复盘/03_踩坑记录/坑.md",
        "00_人生路/02_反思复盘/01_反思/已在 v2.md",
        "03_知识库/00_收件箱/灵感.md",
        "",
    ):
        assert vault_layout.v1_chapter_move(path) is None, path


def test_index_policy_separates_system_journal_and_memory_sources():
    assert vault_layout.index_policy("00-System/任务/某任务.md") == "skip"
    assert vault_layout.index_policy("02_生活运营/00_待办事项/回收站/a.md") == "skip"
    assert vault_layout.index_policy("02_生活运营/00_待办事项/_系统/待办（todos）.md") == "skip"
    assert vault_layout.index_policy("02_生活运营/00_待办事项/收集箱/a.md") == "document_only"
    assert vault_layout.index_policy(".torch-os/notes.md") == "skip"
    assert vault_layout.index_policy("dashboard.md", {"dashboard": True}) == "skip"
    assert vault_layout.index_policy("00_人生路/01_日记/2026-10-20.md") == "document_only"
    assert vault_layout.index_policy("02_生活运营/01_健康与体重/2026-10.md") == "document_only"
    assert vault_layout.index_policy("01_自我档案/01_内核与三观/身份.md") == "memory_eligible"
    assert vault_layout.index_policy(
        "00_人生路/02_反思复盘/01_反思/某日.md", {"memory": True}) == "memory_eligible"


def test_pillar_and_chapter_lookup():
    assert vault_layout.pillar_of("00_人生路/01_日记/a.md") == "life"
    assert vault_layout.pillar_of("03_知识库/00_收件箱/a.md") == "knowledge"
    assert vault_layout.pillar_of("00-System/任务/a.md") == ""
    assert vault_layout.chapter_key_of("00_人生路/05_项目/01_项目计划/a.md") == "project_plan"
    assert vault_layout.chapter_key_of("03_知识库/05_方法库/02_通用方法/a.md") == "method_general"


def test_ensure_layout_creates_pillars_and_chapters(tmp_path):
    created = vault_layout.ensure_layout(tmp_path)
    assert "00_人生路" in created
    assert (tmp_path / "00_人生路/01_日记").is_dir()
    assert (tmp_path / "00_人生路/02_反思复盘/01_反思").is_dir()
    assert (tmp_path / "00_人生路/04_目标/01_年度目标").is_dir()
    assert (tmp_path / "00_人生路/04_目标/04_习惯追踪").is_dir()
    assert (tmp_path / "00_人生路/05_项目/01_项目计划").is_dir()
    assert (tmp_path / "02_生活运营/00_待办事项/收集箱").is_dir()
    assert (tmp_path / "02_生活运营/00_待办事项/已完成").is_dir()
    assert (tmp_path / "02_生活运营/00_待办事项/回收站").is_dir()
    assert (tmp_path / "03_知识库/00_收件箱").is_dir()
    assert vault_layout.layout_status(tmp_path)["state"] == "v1"


def test_validate_target_only_allows_the_four_pillars():
    assert vault_layout.validate_target("03_知识库/00_收件箱/x.md") == "03_知识库/00_收件箱/x.md"
    assert vault_layout.validate_target("00_人生路/05_项目/01_项目计划/x.md") == (
        "00_人生路/05_项目/01_项目计划/x.md")
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
