"""Explicit, read-only export of habits and study records (Markdown or JSON).

The snapshot never changes habit or study state, never creates anything and
never calls a model. Habit entries and study sessions are exported in full;
this is personal-scale data, not a server-side report.
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
SUBJECT_STATUS_LABELS = {"active": "进行中", "archived": "已归档"}


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
    def __init__(self, database: Database):
        self.database = database
        self.habits = HabitService(database)

    def collect(self) -> dict[str, Any]:
        habits = self.habits.list()
        with self.database.connect() as connection:
            entry_rows = [dict(row) for row in connection.execute(
                """SELECT habit_id,date,status,note,revision,updated_at
                FROM habit_entries ORDER BY habit_id, date"""
            ).fetchall()]
            subject_rows = [dict(row) for row in connection.execute(
                """SELECT id,title,goal_id,weekly_target_minutes,status,revision,created_at,updated_at
                FROM study_subjects ORDER BY id"""
            ).fetchall()]
            session_rows = [dict(row) for row in connection.execute(
                """SELECT subject_id,study_date,minutes,content,next_step,
                          goal_id_at_recording,goal_title_at_recording,revision,created_at,updated_at
                FROM study_sessions ORDER BY subject_id, study_date, id"""
            ).fetchall()]

        entries_by_habit: dict[int, list[dict[str, Any]]] = {}
        entry_counts = {key: 0 for key in ENTRY_STATUS_LABELS}
        for entry in entry_rows:
            entries_by_habit.setdefault(int(entry["habit_id"]), []).append(entry)
            status = str(entry.get("status") or "")
            if status in entry_counts:
                entry_counts[status] += 1
        habit_counts = {key: 0 for key in HABIT_STATUS_LABELS}
        habit_rows = []
        for habit in habits:
            status = str(habit.get("status") or "")
            if status in habit_counts:
                habit_counts[status] += 1
            habit_rows.append({**habit, "entries": entries_by_habit.get(int(habit["id"]), [])})

        sessions_by_subject: dict[int, list[dict[str, Any]]] = {}
        for session in session_rows:
            sessions_by_subject.setdefault(int(session["subject_id"]), []).append(session)
        subject_counts = {key: 0 for key in SUBJECT_STATUS_LABELS}
        subject_export = []
        for subject in subject_rows:
            status = str(subject.get("status") or "")
            if status in subject_counts:
                subject_counts[status] += 1
            subject_sessions = sessions_by_subject.get(int(subject["id"]), [])
            subject_export.append({
                **subject,
                "total_minutes": sum(int(item.get("minutes") or 0) for item in subject_sessions),
                "sessions": subject_sessions,
            })

        return {
            "schema": 1,
            "exported_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "counts": {
                "habits": len(habit_rows), "habit_entries": len(entry_rows),
                "subjects": len(subject_export), "sessions": len(session_rows),
                "total_study_minutes": sum(int(item.get("minutes") or 0) for item in session_rows),
                "habits_by_status": habit_counts,
                "entries_by_status": entry_counts,
                "subjects_by_status": subject_counts,
            },
            "habits": habit_rows,
            "subjects": subject_export,
        }


def _entry_line(entry: dict[str, Any]) -> str:
    status = ENTRY_STATUS_LABELS.get(entry.get("status"), entry.get("status") or "未记录")
    line = f"  - {entry.get('date')} · {status}"
    note = str(entry.get("note") or "").strip()
    if note:
        line += " · 备注：" + " ".join(note.split())
    return line


def _session_line(session: dict[str, Any]) -> str:
    line = f"  - {session.get('study_date')} · {int(session.get('minutes') or 0)} 分钟"
    content = str(session.get("content") or "").strip()
    if content:
        line += " · " + " ".join(content.split())
    next_step = str(session.get("next_step") or "").strip()
    if next_step:
        line += " · 下一步：" + " ".join(next_step.split())
    return line


def render_markdown(payload: dict[str, Any]) -> str:
    counts = payload["counts"]
    habit_status = counts["habits_by_status"]
    entry_status = counts["entries_by_status"]
    subject_status = counts["subjects_by_status"]
    habit_summary = " · ".join(f"{HABIT_STATUS_LABELS[key]} {value}" for key, value in habit_status.items() if value)
    entry_summary = " · ".join(f"{ENTRY_STATUS_LABELS[key]} {value}" for key, value in entry_status.items() if value)
    subject_summary = " · ".join(f"{SUBJECT_STATUS_LABELS[key]} {value}" for key, value in subject_status.items() if value)
    lines = [
        "# 薪火OS 习惯与学习导出",
        "",
        f"- 导出时间：{payload['exported_at']}（Asia/Shanghai）",
        f"- 习惯 {counts['habits']}" + (f"（{habit_summary}）" if habit_summary else ""),
        f"- 打卡记录 {counts['habit_entries']} 条" + (f"（{entry_summary}）" if entry_summary else ""),
        f"- 学习科目 {counts['subjects']}" + (f"（{subject_summary}）" if subject_summary else "") +
        f" · 学习记录 {counts['sessions']} 条 · 累计 {counts['total_study_minutes']} 分钟",
        "- 本地只读快照，不改变任何打卡或学习记录；未记录不等于失败。",
        "",
    ]
    for habit in payload["habits"]:
        status = HABIT_STATUS_LABELS.get(habit.get("status"), habit.get("status") or "未标注")
        lines.append(f"## 习惯：{habit.get('title') or '未命名习惯'}（{status}）")
        meta = [
            f"最低行动：{habit.get('minimum_action') or '未标注'}",
            f"计划日：{_weekdays_text(habit.get('weekdays'))}",
            f"开始日期：{habit.get('start_date') or '未标注'}",
        ]
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
        lines.append("")
    for subject in payload["subjects"]:
        status = SUBJECT_STATUS_LABELS.get(subject.get("status"), subject.get("status") or "未标注")
        lines.append(
            f"## 学习科目：{subject.get('title') or '未命名科目'}"
            f"（{status} · 每周目标 {int(subject.get('weekly_target_minutes') or 0)} 分钟）"
        )
        meta = [f"累计 {int(subject.get('total_minutes') or 0)} 分钟（{len(subject['sessions'])} 条）"]
        meta.append(f"关联目标：{subject['goal_id']}" if subject.get("goal_id") else "未关联目标")
        lines.append("- " + " · ".join(meta))
        lines.append("")
        if subject["sessions"]:
            lines.append("- 记录：")
            lines.extend(_session_line(session) for session in subject["sessions"])
        else:
            lines.append("- （这个科目还没有学习记录）")
        lines.append("")
    if not payload["subjects"]:
        lines.append("（还没有学习科目）")
    return "\n".join(lines).rstrip() + "\n"


def create_growth_export_router(database: Database, prefix: str = "/api") -> APIRouter:
    service = GrowthExportService(database)
    router = APIRouter(prefix=prefix, tags=["growth-export"])

    def _stamp() -> str:
        return datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")

    def _record(payload: dict[str, Any], export_format: str) -> None:
        database.audit("growth_exported", {
            "format": export_format,
            "habits": payload["counts"]["habits"],
            "habit_entries": payload["counts"]["habit_entries"],
            "subjects": payload["counts"]["subjects"],
            "sessions": payload["counts"]["sessions"],
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
