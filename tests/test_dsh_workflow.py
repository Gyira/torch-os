"""dsh 工作流规则归属项目：随仓库走，不写进数据库。"""

from __future__ import annotations

from pathlib import Path

from backend.app import dsh_web, vault_layout

AGENTS = Path(__file__).resolve().parents[1] / "dsh" / "AGENTS.md"


def test_workflow_file_ships_with_the_repo() -> None:
    assert AGENTS.is_file()
    text = AGENTS.read_text(encoding="utf-8")
    assert not text.startswith("\ufeff")
    assert "DSH_AGENTS_HOME" in text
    assert "TORCH_REPO_ROOT" in text
    # 分类表跟着当前四库布局走（v2 路径），不能残留 v1 目录。
    for chapter in ("reflection", "review_week", "review_month", "goal_year",
                    "goal_quarter", "goal_month", "project_plan", "work_review_project"):
        assert vault_layout.chapter_dir(chapter) in text, chapter
    for stale in ("00_人生路/02_反思`", "00_人生路/04_周复盘", "00_人生路/05_月复盘",
                  "00_人生路/06_目标与计划/01_年度目标"):
        assert stale not in text, stale
    assert "00_人生路/03_心得体会/01_生活心得" in text
    assert "03_知识库/00_收件箱" in text


def test_environment_points_dsh_at_the_project_rules(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "dsh").mkdir(parents=True)
    (repo / "dsh" / "AGENTS.md").write_text("# rules\n", encoding="utf-8")

    env = dsh_web.workflow_environment({}, repo_root=repo)

    assert env["DSH_AGENTS_HOME"] == str(repo / "dsh")
    assert env["TORCH_REPO_ROOT"] == str(repo)
    assert env["PYTHONUTF8"] == "1"


def test_explicit_environment_wins_and_missing_rules_are_not_injected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dsh_web.workflow_environment({"DSH_AGENTS_HOME": "D:/mine", "TORCH_REPO_ROOT": "D:/other"},
                                       repo_root=repo)
    assert env["DSH_AGENTS_HOME"] == "D:/mine"
    assert env["TORCH_REPO_ROOT"] == "D:/other"
    assert "DSH_AGENTS_HOME" not in dsh_web.workflow_environment({}, repo_root=repo)
