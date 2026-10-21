"""横幅统计（工作台 2.0）：横幅"统计模式"的三个大数字——接我们自己的本地数据。

- 连续日记天数：从今天往回数，连续存在 `00_人生路/01_日记/YYYY-MM-DD.md` 的天数（上限一年；旧库自动兜底 `02-Life/journal`）；
- 今日完成度：复用 `workbench.collect_day` 的口径（待办 + 每日必做 + 习惯 + 日记文件）；
- 记忆 / 资料：数据库里的已确认记忆与可用资料计数；
- 未完成待办：当日清单里还没勾掉的条数。

纯读：不写文件、不发审计、不调用模型。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from . import workbench
from .activity import count_activity_days
from .db import Database
from .journal_note import note_abspath

SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_STREAK = 366


def journal_streak(vault_dir: Path | str, day: str) -> int:
    streak = 0
    current = date.fromisoformat(day)
    while streak < MAX_STREAK:
        if not note_abspath(vault_dir, current.isoformat()).is_file():
            break
        streak += 1
        current -= timedelta(days=1)
    return streak


def collect_banner_stats(database: Database, vault_dir: Path | str,
                         day: str | None = None) -> dict[str, Any]:
    target = day or datetime.now(SHANGHAI).date().isoformat()
    payload = workbench.collect_day(database, vault_dir, target)
    with database.connect() as connection:
        memories = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE status='active'").fetchone()[0]
        notes = connection.execute(
            "SELECT COUNT(*) FROM documents WHERE available=1").fetchone()[0]
    todos = payload.get("todos") or []
    daily = payload.get("daily") or []
    habits = payload.get("habits") or []
    by_day = count_activity_days(vault_dir, target, days=14)
    activity = [{"day": day, "count": count} for day, count in by_day.items()]
    return {
        "day": target,
        "journal_streak": journal_streak(vault_dir, target),
        "today_percent": (payload.get("progress") or {}).get("percent") or 0,
        "memories": int(memories),
        "notes": int(notes),
        "open_todos": sum(1 for item in todos if not item.get("completed")),
        "daily_done": sum(1 for item in daily if item.get("done")),
        "daily_total": len(daily),
        "habits_done": sum(1 for item in habits if item.get("done")),
        "habits_total": len(habits),
        "activity_today": by_day.get(target, 0),
        "activity": activity,
    }


def create_banner_stats_router(database: Database, vault_dir: Path | str,
                               prefix: str = "/api") -> APIRouter:
    """挂载时套用应用级数据锁（与扫描/日记写入同一把）。"""
    router = APIRouter(prefix=prefix, tags=["banner"])

    @router.get("/banner/stats")
    def banner_stats():
        return collect_banner_stats(database, vault_dir)

    return router
