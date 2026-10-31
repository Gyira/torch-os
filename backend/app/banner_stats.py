"""横幅统计（工作台 2.0）：统计模式的大数字 + 7 行热力图（后端 56 天，前端按屏宽铺满）。

- 连续日记天数：从今天往回数，连续存在 `00_人生路/01_日记/YYYY-MM-DD.md` 的天数
  （上限一年；旧库自动兜底 `02-Life/journal`）；
- 今日完成度：复用 `workbench.collect_day` 的口径（待办 + 每日必做 + 习惯 + 日记文件）；
- 记忆 / 资料：数据库里的已确认记忆与可用资料计数；
- 未完成待办：当日清单里还没勾掉的条数；
- 热力图：`heatmap.build_heatmap` 的 7 行（人生路/知识库/自我档案/任务创建/任务完成/
  习惯/总热度），并把当天明细存档到 `<个人数据库>/.obsidian/torch-os-heatmap/`。

除热力快照外不写文件、不发审计、不调用模型；只读模式下快照写入会被跳过。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from . import heatmap, workbench
from .db import Database
from .identity import DEFAULT_HEAT_COLOR
from .journal_note import note_abspath

SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_STREAK = 366
BANNER_HEAT_DAYS = 56


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
                         day: str | None = None, *,
                         read_only: bool = False) -> dict[str, Any]:
    target = day or datetime.now(SHANGHAI).date().isoformat()
    payload = workbench.collect_day(database, vault_dir, target)
    with database.connect() as connection:
        memories = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE status='active'").fetchone()[0]
        notes = connection.execute(
            "SELECT COUNT(*) FROM documents WHERE available=1").fetchone()[0]
    todos = payload.get("todos") or []
    daily = payload.get("daily") or []

    # 横幅热力图：最近 56 天 × 7 行；前端按可用宽度取最近的 14–42 天铺满区域。
    # 详细版顺手把今天的文档/习惯清单存档到 .obsidian/torch-os-heatmap/daily/，
    # 这样“回看”时能直接翻到当天新增了什么。
    end = date.fromisoformat(target)
    start = end - timedelta(days=BANNER_HEAT_DAYS - 1)
    ledger = heatmap.load_ledger(vault_dir)
    raw = heatmap.build_heatmap(database, vault_dir, start, end,
                                include_documents=True, ledger=ledger)
    if raw.get("ledger_changed"):
        heatmap.save_ledger(vault_dir, ledger, read_only=read_only)
    today_row = next((item for item in raw["days"]
                      if item["date"] == target), None)
    storage = {"status": "skipped", "reason": "no-today-row"}
    if today_row is not None and not today_row.get("future"):
        storage = heatmap.record_daily(
            vault_dir, today_row, read_only=read_only,
            task_present=bool(raw.get("task_present")))
    activity = []
    for item in raw["days"]:
        rows = item.get("rows") or {}
        activity.append({
            "day": item["date"],
            "count": sum(int((rows.get(key) or {}).get("count") or 0)
                         for key in ("life", "knowledge", "self")),
        })
    today_habits = (today_row or {}).get("rows", {}).get("habits") or {}
    today_total = (today_row or {}).get("rows", {}).get("total") or {}
    return {
        "day": target,
        "journal_streak": journal_streak(vault_dir, target),
        "today_percent": (payload.get("progress") or {}).get("percent") or 0,
        "memories": int(memories),
        "notes": int(notes),
        "open_todos": sum(1 for item in todos if not item.get("completed")),
        "daily_done": sum(1 for item in daily if item.get("done")),
        "daily_total": len(daily),
        "habits_done": int(today_habits.get("done") or 0),
        "habits_total": int(today_habits.get("scheduled") or 0),
        "habits_skipped": int(today_habits.get("skipped") or 0),
        "activity_today": sum(int((today_row or {}).get("rows", {}).get(
            key, {}).get("count") or 0) for key in ("life", "knowledge", "self")),
        "activity": activity,
        "heat_level": int(today_total.get("level") or 0),
        "heat_score": float(today_total.get("score") or 0),
        "heat_color": payload.get("banner_heat_color") or DEFAULT_HEAT_COLOR,
        "heatmap": heatmap.banner_heatmap(raw),
        "heatmap_storage": storage,
    }


def create_banner_stats_router(database: Database, vault_dir: Path | str, *,
                               read_only: bool = False,
                               prefix: str = "/api") -> APIRouter:
    """挂载时套用应用级数据锁（与扫描/日记写入同一把）。"""
    router = APIRouter(prefix=prefix, tags=["banner"])

    @router.get("/banner/stats")
    def banner_stats():
        return collect_banner_stats(database, vault_dir, read_only=read_only)

    return router
