"""Explicit, read-only export of habits and check-ins (2026-10-22).

Study subjects/sessions were retired with the old learning backend. This export
now contains habits and their entries only; the snapshot never changes state.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Response

from .db import Database
from .habits import HabitService


SHANGHAI = ZoneInfo("Asia/Shanghai")
WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
HABIT_STATUS_LABELS = {"active": "进行中", "archived": "已归档"}
ENTRY_STATUS_LABELS = {"done": "完成", "skipped": "主动休息", "unrecorded": "未记录"}


def _weekdays_text(weekdays: Any) -> str:
    if not isinstance(weekdays, list):
        return "未标注"
    values = sorted({int(value) for value in weekdays if isinstance(value, int) and 0 <= value <= 6})
    if not values:
        return "未标注"
    if len(values) == 7:
        return "每一天"
    return "、".join(WEEKDAY_LABELS[value] for value in values)


class GrowthExportService:
    def __init__(self, database: Database, vault_dir: Any = None):
        self.database = database
        self.habits = HabitService(database, vault_dir)

    def collect(self) -> dict[str, Any]:
        payload = self.habits.export_payload()
        entry_rows = payload["entries"]
        entry_counts = {key: 0 for key in ENTRY_STATUS_LABELS}
        for entry in entry_rows:
            status = str(entry.get("status") or "")
            if status in entry_counts:
                entry_counts[status] += 1
        habit_counts = {key: 0 for key in HABIT_STATUS_LABELS}
        habit_rows = []
        for habit in payload["habits"]:
            status = str(habit.get("status") or "")
            if status in habit_counts:
                habit_counts[status] += 1
            habit_rows.append({**habit, "entries": habit.get("entries") or []})

        return {
            "schema": 1,
            "exported_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "counts": {
                "habits": len(habit_rows), "habit_entries": len(entry_rows),
                "habits_by_status": habit_counts,
                "entries_by_status": entry_counts,
            },
            "habits": habit_rows,
        }


def _entry_line(entry: dict[str, Any]) -> str:
    status = ENTRY_STATUS_LABELS.get(entry.get("status"), entry.get("status") or "未记录")
    line = f"  - {entry.get('date')} · {status}"
    note = str(entry.get("note") or "").strip()
    if note:
        line += " · 备注：" + " ".join(note.split())
    return line


def render_markdown(payload: dict[str, Any]) -> str:
    counts = payload["counts"]
    habit_status = counts["habits_by_status"]
    entry_status = counts["entries_by_status"]
    habit_summary = " · ".join(f"{HABIT_STATUS_LABELS[key]} {value}" for key, value in habit_status.items() if value)
    entry_summary = " · ".join(f"{ENTRY_STATUS_LABELS[key]} {value}" for key, value in entry_status.items() if value)
    lines = [
        "# 薪火OS 习惯与打卡导出",
        "",
        f"- 导出时间：{payload['exported_at']}（Asia/Shanghai）",
        f"- 习惯 {counts['habits']}" + (f"（{habit_summary}）" if habit_summary else ""),
        f"- 打卡记录 {counts['habit_entries']} 条" + (f"（{entry_summary}）" if entry_summary else ""),
        "- 本地只读快照，不改变任何打卡记录；未记录不等于失败。",
        "",
    ]
    for habit in payload["habits"]:
        status = HABIT_STATUS_LABELS.get(habit.get("status"), habit.get("status") or "未标注")
        lines.append(f"## 习惯：{habit.get('title') or '未命名习惯'}（{status}）")
        meta = [
            f"最低行动：{habit.get('minimum_action') or '未标注'}",
            f"频率：{habit.get('frequency_label') or _weekdays_text(habit.get('weekdays'))}",
            f"开始日期：{habit.get('start_date') or '未标注'}",
        ]
        if habit.get("group"):
            meta.append(f"分组：{habit['group']}")
        if habit.get("priority"):
            meta.append(f"优先级：{habit['priority']}")
        if habit.get("reminder"):
            meta.append(f"提醒：{habit['reminder']}")
        if habit.get("cue"):
            meta.insert(0, f"触发提示：{habit['cue']}")
        lines.append("- " + " · ".join(meta))
        lines.append("")
        if habit["entries"]:
            lines.append("- 记录：")
            lines.extend(_entry_line(entry) for entry in habit["entries"])
        else:
            lines.append("- （还没有打卡记录）")
        lines.append("")
    if not payload["habits"]:
        lines.append("（还没有习惯）")
    return "\n".join(lines).rstrip() + "\n"


def create_growth_export_router(database: Database, vault_dir: Any = None,
                                prefix: str = "/api") -> APIRouter:
    service = GrowthExportService(database, vault_dir)
    router = APIRouter(prefix=prefix, tags=["growth-export"])

    def _stamp() -> str:
        return datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")

    def _record(payload: dict[str, Any], export_format: str) -> None:
        database.audit("growth_exported", {
            "format": export_format,
            "habits": payload["counts"]["habits"],
            "habit_entries": payload["counts"]["habit_entries"],
        })

    @router.get("/export/growth.md")
    def export_markdown():
        payload = service.collect()
        _record(payload, "markdown")
        return Response(
            content=render_markdown(payload),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="torch-os-growth-{_stamp()}.md"'},
        )

    @router.get("/export/growth.json")
    def export_json():
        payload = service.collect()
        _record(payload, "json")
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="torch-os-growth-{_stamp()}.json"'},
        )

    return router
