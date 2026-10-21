"""旧布局 → 四库的预览、执行与回滚。"""

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
    assert (vault / "00_人生路/06_目标与计划/05_项目计划/计划.md").is_file()
    assert (vault / "03_知识库/00_收件箱/灵感.md").is_file()
    assert (vault / "00-System/任务/任务.md").is_file()
    assert (vault / "dashboard.md").is_file()
    link = (vault / "00-System/任务/任务.md").read_text(encoding="utf-8")
    assert "[[00_人生路/06_目标与计划/05_项目计划/计划.md|计划]]" in link
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
