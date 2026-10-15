"""Explicit, read-only export of goals and tasks as Markdown or JSON.

The export is a snapshot: it never changes task state, never creates a new
task and never calls a model. Markdown checkboxes can be copied into an
editor like Obsidian, but the app keeps SQLite as the only authority.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Response

from .db import Database
from .goals import GoalTaskService


SHANGHAI = ZoneInfo("Asia/Shanghai")
GOAL_STATUS_LABELS = {"active": "进行中", "paused": "已暂停", "completed": "已完成", "archived": "已归档"}
TASK_STATUS_LABELS = {"todo": "待办", "in_progress": "进行中", "completed": "已完成", "cancelled": "已取消"}
PRIORITY_LABELS = {"low": "低", "medium": "中", "high": "高", "urgent": "紧急"}


class PlanningExportService:
    def __init__(self, database: Database):
        self.database = database
        self.goals = GoalTaskService(database)

    def collect(self) -> dict[str, Any]:
        goals = self.goals.list_goals()
        tasks = self.goals.list_tasks()
        by_goal: dict[int, list[dict[str, Any]]] = {}
        unlinked: list[dict[str, Any]] = []
        for task in tasks:
            goal_id = task.get("goal_id")
            if goal_id is None:
                unlinked.append(task)
            else:
                by_goal.setdefault(int(goal_id), []).append(task)

        goal_counts = {key: 0 for key in GOAL_STATUS_LABELS}
        task_counts = {key: 0 for key in TASK_STATUS_LABELS}
        rows = []
        for goal in goals:
            status = str(goal.get("status") or "")
            if status in goal_counts:
                goal_counts[status] += 1
            goal_tasks = by_goal.get(int(goal["id"]), [])
            for task in goal_tasks:
                task_status = str(task.get("status") or "")
                if task_status in task_counts:
                    task_counts[task_status] += 1
            rows.append({**goal, "tasks": goal_tasks})
        for task in unlinked:
            task_status = str(task.get("status") or "")
            if task_status in task_counts:
                task_counts[task_status] += 1
        return {
            "schema": 1,
            "exported_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "counts": {
                "goals": len(rows), "tasks": len(tasks),
                "goals_by_status": goal_counts, "tasks_by_status": task_counts,
            },
            "goals": rows,
            "unlinked_tasks": unlinked,
        }


def _task_line(task: dict[str, Any]) -> str:
    status = str(task.get("status") or "")
    box = "x" if status == "completed" else " "
    meta = [PRIORITY_LABELS.get(task.get("priority"), task.get("priority") or "中") + "优先级"]
    if task.get("due_date"):
        meta.append(f"截止 {task['due_date']}")
    if task.get("completed_at"):
        meta.append(f"完成于 {task['completed_at'][:10]}")
    if status == "cancelled":
        meta.append("已取消")
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
        "- 这是本地只读快照，不改变任何任务状态；复选框可以复制到 Obsidian 等编辑器，但这里才是权威来源。",
        "",
    ]
    for goal in payload["goals"]:
        status = GOAL_STATUS_LABELS.get(goal.get("status"), goal.get("status") or "未标注")
        lines.append(f"## 目标：{goal.get('title') or '未命名目标'}（{status} · 进度 {int(goal.get('progress') or 0)}%）")
        meta = []
        if goal.get("category"):
            meta.append(f"领域：{goal['category']}")
        if goal.get("target_date"):
            meta.append(f"目标日期：{goal['target_date']}")
        if meta:
            lines.append("- " + " · ".join(meta))
        description = str(goal.get("description") or "").strip()
        if description:
            lines.append("- 说明：" + " ".join(description.split()))
        lines.append("")
        for task in goal["tasks"]:
            lines.append(_task_line(task))
        if not goal["tasks"]:
            lines.append("- （这个目标下还没有任务）")
        lines.append("")
    lines.append("## 未关联目标的任务")
    lines.append("")
    if payload["unlinked_tasks"]:
        for task in payload["unlinked_tasks"]:
            lines.append(_task_line(task))
    else:
        lines.append("- （没有未关联目标的任务）")
    return "\n".join(lines).rstrip() + "\n"


def create_planning_export_router(database: Database, prefix: str = "/api") -> APIRouter:
    service = PlanningExportService(database)
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
