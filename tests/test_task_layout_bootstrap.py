"""任务数据层启动迁移：展开 07/任务、已完成归档、旧回收站与滴答路径更新。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.app import dida_sync, task_layout_bootstrap, task_notes, task_trash, tasks
from backend.app.db import Database

TASK_ROOT = "02_生活运营/00_待办事项"
OLD_TASK_ROOT = "00_人生路/07_任务与待办"
CONFIG = """---
type: tasks
清单:
  - 名称: 收集箱
    颜色: '#3b82f6'
  - 名称: 项目清单
    颜色: '#22c55e'
---
"""


def _task(title: str, *, done: bool = False, list_name: str = "",
          dida_id: str = "") -> str:
    lines = ["---", "type: task"]
    if done:
        lines.extend(["完成: true", "完成于: \"2026-10-30\""])
    if list_name:
        lines.append(f"清单: {list_name}")
    if dida_id:
        lines.append(f"dida_id: {dida_id}")
    lines.extend(["---", "", f"# {title}", ""])
    return "\n".join(lines)


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    root = vault / TASK_ROOT
    (root / "任务" / "项目清单").mkdir(parents=True)
    (root / "任务" / "收集箱任务.md").write_text(_task("收集箱任务"), encoding="utf-8")
    (root / "任务" / "项目清单" / "清单任务.md").write_text(
        _task("清单任务", dida_id="dida-1"), encoding="utf-8")
    (root / "任务" / "项目清单" / "已完成任务.md").write_text(
        _task("已完成任务", done=True, list_name="项目清单"), encoding="utf-8")
    (root / "_系统").mkdir(parents=True)
    (root / "_系统" / "任务（tasks）.md").write_text(CONFIG, encoding="utf-8")
    old_batch = vault / ".torch-os" / "task-trash" / "20261030T000000000000Z-deadbeef"
    old_file = old_batch / "00_人生路" / "08_任务与待办" / "任务" / "旧任务.md"
    old_file.parent.mkdir(parents=True)
    old_file.write_text(_task("旧任务"), encoding="utf-8")
    return vault


def test_bootstrap_flattens_inner_task_dir_and_moves_done(tmp_path):
    vault = _make_vault(tmp_path)
    database = Database(tmp_path / "torch.sqlite3")
    database.initialize()
    with database.connect() as connection:
        connection.executescript(dida_sync.SCHEMA)
        connection.execute(
            """INSERT INTO dida_task_links
               (source, external_id, relpath, last_local_revision, created_at, updated_at)
               VALUES ('dida_web', 'dida-1', ?, '', '2026-10-30', '2026-10-30')""",
            (f"{TASK_ROOT}/任务/项目清单/清单任务.md",),
        )

    result = task_layout_bootstrap.ensure_task_data_layout(vault, database=database)

    assert result["status"] == "done"
    root = vault / TASK_ROOT
    assert (root / "收集箱" / "收集箱任务.md").is_file()
    assert (root / "项目清单" / "清单任务.md").is_file()
    assert (root / "已完成" / "已完成任务.md").is_file()
    assert not (root / "任务").exists()
    assert (root / "回收站").is_dir()
    assert (vault / ".torch-os" / "task-layout.json").is_file()
    assert (vault / ".torch-os" / "backups" / "task-layout").is_dir()

    done_note = (root / "已完成" / "已完成任务.md").read_text(encoding="utf-8")
    assert "清单: 项目清单" in done_note
    notes, _warnings = task_notes.scan_task_notes(vault)
    assert {note["list_name"] for note in notes if note["title"] == "已完成任务"} == {"项目清单"}

    with sqlite3.connect(tmp_path / "torch.sqlite3") as connection:
        relpath = connection.execute(
            "SELECT relpath FROM dida_task_links WHERE external_id = 'dida-1'"
        ).fetchone()[0]
    assert relpath == f"{TASK_ROOT}/项目清单/清单任务.md"
    assert (vault / relpath).is_file()

    # 旧回收站批次已经搬进新回收站，仍然可以列出。
    trash_titles = {item["title"] for item in task_trash.list_trash(vault)}
    assert "旧任务" in trash_titles


def test_bootstrap_is_idempotent(tmp_path):
    vault = _make_vault(tmp_path)
    database = Database(tmp_path / "torch.sqlite3")
    database.initialize()

    first = task_layout_bootstrap.ensure_task_data_layout(vault, database=database)
    second = task_layout_bootstrap.ensure_task_data_layout(vault, database=database)

    assert first["status"] == "done"
    assert second.get("already_done") is True
    assert (vault / TASK_ROOT / "收集箱" / "收集箱任务.md").is_file()
    assert not list((vault / TASK_ROOT / "收集箱").glob("* 2.md"))


def test_plan_does_not_write(tmp_path):
    vault = _make_vault(tmp_path)
    before = sorted(path.relative_to(vault).as_posix()
                    for path in vault.rglob("*") if path.is_file())

    plan = task_layout_bootstrap.plan_task_layout(vault)

    after = sorted(path.relative_to(vault).as_posix()
                   for path in vault.rglob("*") if path.is_file())
    assert plan["dry_run"] is True and plan["count"] > 0
    assert before == after
    assert not (vault / TASK_ROOT / "收集箱").exists()


def test_bootstrap_registers_list_folders_without_definitions(tmp_path):
    """`任务/<清单>` 上移后配置里没有定义的目录，启动迁移要补登记成普通清单。"""
    vault = tmp_path / "vault"
    root = vault / TASK_ROOT
    (root / "torchos项目管理").mkdir(parents=True)
    (root / "torchos项目管理" / "健康系统.md").write_text(_task("健康系统"), encoding="utf-8")
    for name in ("收集箱", "已完成", "回收站", "_系统"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "_系统" / "任务（tasks）.md").write_text(CONFIG, encoding="utf-8")

    database = Database(tmp_path / "torch.sqlite3")
    database.initialize()
    result = task_layout_bootstrap.ensure_task_data_layout(vault, database=database)

    assert result["status"] == "done"
    assert result["lists_adopted"] == ["torchos项目管理"]
    assert (root / "torchos项目管理" / "健康系统.md").is_file()  # 只登记，不搬任务
    board = tasks.read_tasks(vault)
    entry = next(item for item in board["lists"] if item["name"] == "torchos项目管理")
    assert entry["synthetic"] is False
    assert next(task for task in board["tasks"] if task["title"] == "健康系统")["list_name"] == "torchos项目管理"

    # 幂等：第二次启动发现无事可做，不再备份、不再写配置。
    second = task_layout_bootstrap.ensure_task_data_layout(vault, database=database)
    assert second.get("already_done") is True


def test_bootstrap_returns_already_done_for_clean_vault(tmp_path):
    vault = tmp_path / "vault"
    root = vault / TASK_ROOT
    (root / "收集箱").mkdir(parents=True)
    (root / "已完成").mkdir()
    (root / "回收站").mkdir()
    (root / "_系统").mkdir()

    result = task_layout_bootstrap.ensure_task_data_layout(vault)

    assert result.get("already_done") is True


def test_bootstrap_moves_previous_root_into_ops(tmp_path):
    vault = tmp_path / "vault"
    old = vault / OLD_TASK_ROOT
    (old / "收集箱").mkdir(parents=True)
    (old / "收集箱" / "旧任务.md").write_text(_task("旧任务"), encoding="utf-8")
    (old / "_系统").mkdir()
    (old / "_系统" / "任务（tasks）.md").write_text(CONFIG, encoding="utf-8")

    result = task_layout_bootstrap.ensure_task_data_layout(vault)

    assert result["status"] == "done"
    assert result["schema"] == 2
    new = vault / TASK_ROOT
    assert (new / "收集箱" / "旧任务.md").is_file()
    assert (new / "_系统" / "任务（tasks）.md").is_file()
    assert not old.exists()
    backup = vault / result["backup_dir"]
    assert (backup / OLD_TASK_ROOT / "收集箱" / "旧任务.md").is_file()
    assert task_notes.task_root(vault) == new


def test_bootstrap_merges_08_and_keeps_duplicate_names(tmp_path):
    vault = tmp_path / "vault"
    old08 = vault / "00_人生路" / "08_任务与待办" / "任务" / "收集箱"
    old08.mkdir(parents=True)
    (old08 / "同名任务.md").write_text(_task("08 任务"), encoding="utf-8")
    new = vault / TASK_ROOT
    (new / "收集箱").mkdir(parents=True)
    (new / "收集箱" / "同名任务.md").write_text(_task("07 任务"), encoding="utf-8")

    task_layout_bootstrap.ensure_task_data_layout(vault)

    names = sorted(path.name for path in (new / "收集箱").glob("同名任务*.md"))
    assert names == ["同名任务 2.md", "同名任务.md"]
    bodies = [(new / "收集箱" / name).read_text(encoding="utf-8") for name in names]
    assert any("08 任务" in body for body in bodies)
    assert any("07 任务" in body for body in bodies)


def test_bootstrap_skips_read_only_without_writing(tmp_path):
    vault = tmp_path / "vault"
    old = vault / OLD_TASK_ROOT
    (old / "收集箱").mkdir(parents=True)
    (old / "收集箱" / "只读任务.md").write_text(_task("只读任务"), encoding="utf-8")

    result = task_layout_bootstrap.ensure_task_data_layout(vault, read_only=True)

    assert result["status"] == "skipped"
    assert result["reason"] == "read_only"
    assert not (vault / TASK_ROOT).exists()
    assert task_notes.task_root(vault) == old
