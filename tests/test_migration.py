"""旧布局 / 人生路 v1 → 四库 v2 的预览、执行与回滚。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.db import Database
from backend.app.migration_apply import apply_migration, migration_status, rollback_migration
from backend.app.migration_plan import plan_migration

JOURNAL = "---\ntype: journal\ndate: 2026-10-20\n---\n\n# 今天\n"
PLAN = "---\ntype: plan\ntitle: 考研规划\n---\n\n计划正文\n"
TASK = "---\ntype: task\n---\n\n见 [[00-System/任务/计划.md|计划]]\n"
NOTE = "---\ntype: note\ntitle: 灵感\n---\n\n想法\n"
DASHBOARD = "---\ndashboard: true\nlayout: immersive\n---\n\n看板\n"

V1_JOURNAL = ("---\ntype: journal\ndate: 2026-10-20\n---\n\n"
              "今天看了 [[00_人生路/06_目标与计划/05_项目计划/项目.md|项目]]。\n")
V1_REFLECTION = ("---\ntype: reflection\ntitle: 日省\n---\n\n"
                 "年度目标见 [年度目标](../06_目标与计划/01_年度目标/年度目标.md)。\n")
V1_WEEKLY = ("---\ntype: weekly_review\ntitle: 2026-W43\n---\n\n"
             "月度参考 [[00_人生路/05_月复盘/2026-10.md|月复盘]]。\n")
V1_MONTHLY = "---\ntype: monthly_review\ntitle: 2026-10\n---\n\n月复盘。\n"
V1_GOAL_YEAR = "---\ntype: goal\ntitle: 年度目标\n---\n\n今年完成一件事。\n"
V1_GOAL_QUARTER = "---\ntype: goal\ntitle: 2026-Q4\n---\n\n四季度目标。\n"
V1_GOAL_MONTH = "---\ntype: goal\ntitle: 十月目标\n---\n\n十月目标。\n"
V1_PROJECT = "---\ntype: plan\ntitle: 项目\n---\n\n项目计划。\n"
V1_PROJECT_REVIEW = "---\ntype: reflection\ntitle: 复盘\n---\n\n项目复盘。\n"

# 旧的 v1 章节路径 → (v2 路径, 原文)；覆盖所有需要搬迁的目录。
V1_MIGRATIONS = {
    "00_人生路/02_反思/日省.md": (
        "00_人生路/02_反思复盘/01_反思/日省.md", V1_REFLECTION),
    "00_人生路/04_周复盘/2026-W43.md": (
        "00_人生路/02_反思复盘/02_周复盘/2026-W43.md", V1_WEEKLY),
    "00_人生路/05_月复盘/2026-10.md": (
        "00_人生路/02_反思复盘/03_月复盘/2026-10.md", V1_MONTHLY),
    "00_人生路/06_目标与计划/01_年度目标/年度目标.md": (
        "00_人生路/04_目标/01_年度目标/年度目标.md", V1_GOAL_YEAR),
    "00_人生路/06_目标与计划/02_季度目标/2026-Q4.md": (
        "00_人生路/04_目标/02_季度目标/2026-Q4.md", V1_GOAL_QUARTER),
    "00_人生路/06_目标与计划/03_月度目标/2026-10.md": (
        "00_人生路/04_目标/03_月度目标/2026-10.md", V1_GOAL_MONTH),
    "00_人生路/06_目标与计划/05_项目计划/项目.md": (
        "00_人生路/05_项目/01_项目计划/项目.md", V1_PROJECT),
    "00_人生路/07_工作复盘/01_项目复盘/复盘.md": (
        "00_人生路/05_项目/02_项目复盘/复盘.md", V1_PROJECT_REVIEW),
}


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "02-Life" / "journal").mkdir(parents=True)
    (vault / "00-System" / "任务").mkdir(parents=True)
    (vault / "90-Inbox").mkdir()
    (vault / "02-Life" / "journal" / "2026-10-20.md").write_text(JOURNAL, encoding="utf-8")
    (vault / "00-System" / "任务" / "计划.md").write_text(PLAN, encoding="utf-8")
    (vault / "00-System" / "任务" / "任务.md").write_text(TASK, encoding="utf-8")
    (vault / "90-Inbox" / "灵感.md").write_text(NOTE, encoding="utf-8")
    (vault / "dashboard.md").write_text(DASHBOARD, encoding="utf-8")
    return vault


def make_database(vault: Path) -> Database:
    database = Database(vault / ".torch-os" / "torch_os.sqlite3")
    database.initialize()
    return database


def test_preview_plans_moves_without_writing(tmp_path):
    vault = make_vault(tmp_path)
    plan = plan_migration(vault)
    assert plan.conflicts == []
    assert plan.stats()["moves"] == 3
    assert plan.stats()["keep_system"] == 1
    assert plan.stats()["keep_foreign"] == 1
    assert (vault / "02-Life/journal/2026-10-20.md").is_file()


def test_apply_moves_rewrites_links_and_rollback_restores(tmp_path):
    vault = make_vault(tmp_path)
    database = make_database(vault)
    plan = plan_migration(vault)
    result = apply_migration(vault, database, expected_hash=plan.plan_hash, confirm=True)
    assert result["applied"] is True
    assert (vault / "00_人生路/01_日记/2026-10-20.md").is_file()
    assert (vault / "00_人生路/05_项目/01_项目计划/计划.md").is_file()
    assert (vault / "03_知识库/00_收件箱/灵感.md").is_file()
    assert (vault / "00-System/任务/任务.md").is_file()
    assert (vault / "dashboard.md").is_file()
    link = (vault / "00-System/任务/任务.md").read_text(encoding="utf-8")
    assert "[[00_人生路/05_项目/01_项目计划/计划.md|计划]]" in link
    assert migration_status(vault)["layout"]["state"] == "v1"
    assert plan_migration(vault).stats()["moves"] == 0

    rolled = rollback_migration(vault, result["run_id"], database)
    assert rolled["moves"] == 3
    assert (vault / "02-Life/journal/2026-10-20.md").is_file()
    assert (vault / "00-System/任务/计划.md").is_file()
    assert "[[00-System/任务/计划.md|计划]]" in (
        vault / "00-System/任务/任务.md").read_text(encoding="utf-8")
    assert not (vault / "00_人生路/01_日记/2026-10-20.md").exists()


def test_apply_requires_the_current_plan_hash(tmp_path):
    vault = make_vault(tmp_path)
    database = make_database(vault)
    plan = plan_migration(vault)
    with pytest.raises(ValueError):
        apply_migration(vault, database, expected_hash="stale", confirm=True)
    assert (vault / "02-Life/journal/2026-10-20.md").is_file()


def test_target_conflict_blocks_apply(tmp_path):
    vault = make_vault(tmp_path)
    target = vault / "00_人生路/01_日记/2026-10-20.md"
    target.parent.mkdir(parents=True)
    target.write_text("已存在的另一篇\n", encoding="utf-8")
    plan = plan_migration(vault)
    assert plan.conflicts
    with pytest.raises(ValueError):
        apply_migration(vault, make_database(vault), expected_hash=plan.plan_hash,
                        confirm=True)


def test_foreign_dashboard_is_never_moved(tmp_path):
    vault = make_vault(tmp_path)
    database = make_database(vault)
    plan = plan_migration(vault)
    apply_migration(vault, database, expected_hash=plan.plan_hash, confirm=True)
    assert (vault / "dashboard.md").is_file()
    assert not (vault / "03_知识库/00_收件箱/dashboard.md").exists()


def test_v1_chapters_move_to_v2_paths_and_rollback_restores(tmp_path):
    vault = tmp_path / "vault"
    for relative, (_target, text) in V1_MIGRATIONS.items():
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    journal = vault / "00_人生路/01_日记/2026-10-20.md"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(V1_JOURNAL, encoding="utf-8")

    database = make_database(vault)
    plan = plan_migration(vault)
    assert plan.conflicts == []
    assert {item.source: item.target for item in plan.moves()} == {
        source: target for source, (target, _text) in V1_MIGRATIONS.items()}
    assert plan.stats()["keep_v1"] == 1, "日记目录不搬迁"
    assert all(item.reason.startswith("layout-v2:") for item in plan.moves())
    # 预览不写盘。
    assert (vault / "00_人生路/02_反思/日省.md").is_file()

    result = apply_migration(vault, database, expected_hash=plan.plan_hash, confirm=True)
    assert result["applied"] is True
    for source, (target, _text) in V1_MIGRATIONS.items():
        assert (vault / target).is_file(), target
        assert not (vault / source).exists(), source

    # 未搬走的日记链接、以及搬走的文件内部的 wiki / markdown 链接都要改写。
    assert "[[00_人生路/05_项目/01_项目计划/项目.md|项目]]" in journal.read_text(
        encoding="utf-8")
    weekly = (vault / "00_人生路/02_反思复盘/02_周复盘/2026-W43.md").read_text(
        encoding="utf-8")
    assert "[[00_人生路/02_反思复盘/03_月复盘/2026-10.md|月复盘]]" in weekly
    reflection = (vault / "00_人生路/02_反思复盘/01_反思/日省.md").read_text(
        encoding="utf-8")
    assert "[年度目标](../../04_目标/01_年度目标/年度目标.md)" in reflection
    assert migration_status(vault)["layout_version"] == "pillars-v2"
    assert migration_status(vault)["layout"]["state"] == "v1"
    assert plan_migration(vault).stats()["moves"] == 0

    rolled = rollback_migration(vault, result["run_id"], database)
    assert rolled["moves"] == len(V1_MIGRATIONS)
    assert rolled["links"] == 3
    for source, (target, text) in V1_MIGRATIONS.items():
        assert (vault / source).read_text(encoding="utf-8") == text, source
        assert not (vault / target).exists(), target
    assert journal.read_text(encoding="utf-8") == V1_JOURNAL
    assert plan_migration(vault).stats()["moves"] == len(V1_MIGRATIONS)
