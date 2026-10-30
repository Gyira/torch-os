"""在真实库的副本上验证：迁移会把 torchos项目管理 补登记成普通清单。

只读真实库，所有写入都发生在 .test-data/verify-tasks 下的副本里。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app import task_layout_bootstrap, tasks  # noqa: E402
from app.db import Database  # noqa: E402

REAL = Path(r"C:\Users\19814\Desktop\Torch\个人数据库")
WORK = Path(__file__).resolve().parent / ".test-data" / "verify-tasks"
VAULT = WORK / "vault"
DATA = WORK / "data"
TASK_ROOT = "00_人生路/07_任务与待办"


def main() -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    DATA.mkdir(parents=True)
    (VAULT / "00_人生路").mkdir(parents=True)
    shutil.copytree(REAL / TASK_ROOT, VAULT / TASK_ROOT)
    config = VAULT / TASK_ROOT / "_系统" / "任务（tasks）.md"
    print("迁移前配置：")
    print(config.read_text(encoding="utf-8"))

    before = tasks.read_tasks(VAULT)
    entry = next(item for item in before["lists"] if item["name"] == "torchos项目管理")
    print("迁移前列表项：", entry)
    assert entry["synthetic"] is True

    plan = task_layout_bootstrap.plan_task_layout(VAULT)
    print("迁移计划：", plan["actions"])
    assert any("torchos项目管理" in action for action in plan["actions"])

    database = Database(DATA / "torch_os.sqlite3")
    database.initialize()
    result = task_layout_bootstrap.ensure_task_data_layout(VAULT, database=database)
    print("迁移结果：lists_adopted =", result.get("lists_adopted"),
          "| moved =", len(result.get("moves") or []),
          "| errors =", result.get("errors"))

    after = tasks.read_tasks(VAULT)
    entry = next(item for item in after["lists"] if item["name"] == "torchos项目管理")
    print("迁移后列表项：", entry)
    assert entry["synthetic"] is False
    print("迁移后配置：")
    print(config.read_text(encoding="utf-8"))

    titles = [task["title"] for task in after["tasks"] if task["list_name"] == "torchos项目管理"]
    print("该清单下的任务数：", len(titles))
    assert len(titles) == 8

    again = task_layout_bootstrap.ensure_task_data_layout(VAULT, database=database)
    print("再跑一次：", again)
    assert again.get("already_done") is True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
