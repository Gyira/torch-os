"""Read-only export of Markdown goals and tasks (2026-10-22).

The old SQLite goals/tasks export was retired. Goals come from the life-plan
notes under ``00_人生路/04_目标``; tasks come from one-task-one-note
Markdown files under ``00_人生路/07_任务与待办``. This is a snapshot: it never changes a
note and never calls a model.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Response

from . import goal_notes, task_reports
from .db import Database


SHANGHAI = ZoneInfo("Asia/Shanghai")
GOAL_STATUS_LABELS = {"active": "进行中", "paused": "已暂停", "completed": "已完成", "archived": "已归档"}
TASK_STATUS_LABELS = {"todo": "待办", "in_progress": "进行中", "completed": "已完成", "cancelled": "已取消"}
PRIORITY_LABELS = {"low": "低", "medium": "中", "high": "高", "urgent": "紧急", "none": "无"}
STATUS_KEYS = {
    "active": "active", "进行中": "active", "ongoing": "active",
    "paused": "paused", "已暂停": "paused",
    "completed": "completed", "已完成": "completed", "done": "completed",
    "archived": "archived", "已归档": "archived",
}


def _status_key(value: Any) -> str:
    return STATUS_KEYS.get(str(value or "").strip().casefold(), "active")


class PlanningExportService:
    def __init__(self, vault_dir: Path | str):
        self.vault_dir = Path(vault_dir)

    def collect(self) -> dict[str, Any]:
        goals = goal_notes.list_goals(self.vault_dir, limit=500)
        tasks = [task_reports.report_task(note) for note in task_reports.tasks(self.vault_dir)]
        by_goal: dict[str, list[dict[str, Any]]] = {}
        for task in tasks:
            goal_path = str(task.get("goal") or "")
            if goal_path:
                by_goal.setdefault(goal_path, []).append(task)

        goal_counts = {key: 0 for key in GOAL_STATUS_LABELS}
        task_counts = {key: 0 for key in TASK_STATUS_LABELS}
        rows: list[dict[str, Any]] = []
        for goal in goals:
            status = _status_key(goal.get("status"))
            goal_counts[status] += 1
            goal_tasks = by_goal.pop(goal["id"], [])
            for task in goal_tasks:
                task_status = str(task.get("status") or "todo")
                if task_status in task_counts:
                    task_counts[task_status] += 1
            rows.append({
                **goal, "status": status, "progress": 0,
                "tasks": goal_tasks,
            })
        unlinked = [task for task in tasks if not str(task.get("goal") or "")]
        for task in tasks:
            if str(task.get("goal") or "") in by_goal:
                # A link to a goal note that no longer exists: keep the task,
                # never silently drop it from the export.
                unlinked.append(task)
        for task in unlinked:
            status = str(task.get("status") or "todo")
            if status in task_counts:
                task_counts[status] += 1
        return {
            "schema": 1,
            "exported_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "counts": {
                "goals": len(rows),
                "tasks": len(tasks),
                "goals_by_status": goal_counts,
                "tasks_by_status": task_counts,
            },
            "goals": rows,
            "unlinked_tasks": unlinked,
            "note": "目标与任务都来自 Markdown；这是只读快照。",
        }


def _task_line(task: dict[str, Any]) -> str:
    status = str(task.get("status") or "todo")
    box = "x" if status == "completed" else " "
    priority = PRIORITY_LABELS.get(task.get("priority"), task.get("priority") or "无")
    meta = [f"{priority}优先级"]
    if task.get("due_date"):
        meta.append(f"截止 {task['due_date']}")
    if task.get("completed_at"):
        meta.append(f"完成于 {task['completed_at']}")
    if task.get("goal"):
        meta.append(f"目标 {task['goal']}")
    tags = task.get("tags")
    if isinstance(tags, list) and tags:
        meta.append("标签 " + "、".join(str(tag) for tag in tags))
    lines = [f"- [{box}] {task.get('title') or '未命名任务'} · " + " · ".join(meta)]
    description = str(task.get("description") or "").strip()
    if description:
        lines.append("  " + " ".join(description.split()))
    return "\n".join(lines)


def render_markdown(payload: dict[str, Any]) -> str:
    counts = payload["counts"]
    goal_status = counts["goals_by_status"]
    task_status = counts["tasks_by_status"]
    goal_summary = " · ".join(f"{GOAL_STATUS_LABELS[key]} {value}" for key, value in goal_status.items() if value)
    task_summary = " · ".join(f"{TASK_STATUS_LABELS[key]} {value}" for key, value in task_status.items() if value)
    lines = [
        "# 薪火OS 目标与任务导出",
        "",
        f"- 导出时间：{payload['exported_at']}（Asia/Shanghai）",
        f"- 目标 {counts['goals']}" + (f"（{goal_summary}）" if goal_summary else ""),
        f"- 任务 {counts['tasks']}" + (f"（{task_summary}）" if task_summary else ""),
        "- 来源：人生路目标笔记与 00_人生路/07_任务与待办 下的一任务一笔记；这是只读快照。",
        "",
    ]
    for goal in payload["goals"]:
        status = GOAL_STATUS_LABELS.get(goal.get("status"), goal.get("status") or "未标注")
        lines.append(f"## 目标：{goal.get('title') or '未命名目标'}（{status} · 进度 0%）")
        lines.append(f"- 路径：{goal.get('path') or ''}")
        if goal.get("date"):
            lines.append(f"- 日期：{goal['date']}")
        for task in goal.get("tasks") or []:
            lines.append(_task_line(task))
        lines.append("")
    if payload["unlinked_tasks"]:
        lines.append("## 未关联目标的任务")
        for task in payload["unlinked_tasks"]:
            lines.append(_task_line(task))
    else:
        lines.append("## 未关联目标的任务")
        lines.append("- （没有未关联目标的任务）")
    return "\n".join(lines).rstrip() + "\n"


def create_planning_export_router(database: Database, vault_dir: Path | str,
                                  prefix: str = "/api") -> APIRouter:
    service = PlanningExportService(vault_dir)
    router = APIRouter(prefix=prefix, tags=["planning-export"])

    def _stamp() -> str:
        return datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")

    def _record(payload: dict[str, Any], export_format: str) -> None:
        database.audit("planning_exported", {
            "format": export_format,
            "goals": payload["counts"]["goals"],
            "tasks": payload["counts"]["tasks"],
        })

    @router.get("/export/planning.md")
    def export_markdown():
        payload = service.collect()
        _record(payload, "markdown")
        return Response(
            content=render_markdown(payload),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="torch-os-planning-{_stamp()}.md"'},
        )

    @router.get("/export/planning.json")
    def export_json():
        payload = service.collect()
        _record(payload, "json")
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="torch-os-planning-{_stamp()}.json"'},
        )

    return router
