"""Read-only task views for tools, scheduler drafts and exports.

Markdown task notes are the authority. The old SQLite ``tasks`` table is retired
from live reads (2026-10-22); legacy checkbox tasks remain read-only in
``00-System/任务（tasks）.md`` and are intentionally not counted here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from . import task_notes


def scan(vault_dir: Path | str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    return task_notes.scan_task_notes(vault_dir)


def tasks(vault_dir: Path | str) -> list[dict[str, Any]]:
    return scan(vault_dir)[0]


def open_tasks(vault_dir: Path | str) -> list[dict[str, Any]]:
    return [note for note in tasks(vault_dir) if not note["done"]]


def due_on(vault_dir: Path | str, day: str) -> list[dict[str, Any]]:
    return [note for note in open_tasks(vault_dir) if note.get("date") == day]


def overdue(vault_dir: Path | str, day: str) -> list[dict[str, Any]]:
    return [note for note in open_tasks(vault_dir)
            if note.get("date") and note["date"] < day]


def completed_between(vault_dir: Path | str, first: str, last: str) -> list[dict[str, Any]]:
    return [note for note in tasks(vault_dir)
            if note["done"] and first <= (note.get("done_on") or "") <= last]


def next_open(vault_dir: Path | str, limit: int = 5) -> list[dict[str, Any]]:
    """Soonest dated open tasks first; undated tasks after them."""
    rows = open_tasks(vault_dir)
    rows.sort(key=lambda note: (note.get("date") or "9999-99-99", note["title"].casefold()))
    return rows[:max(1, limit)]


def report_task(note: dict[str, Any]) -> dict[str, Any]:
    """The field names the retired SQLite task rows used to expose."""
    return {
        "title": note.get("title") or "",
        "description": task_notes.note_body(note),
        "status": "completed" if note.get("done") else "todo",
        "priority": note.get("priority") or "none",
        "due_date": note.get("date") or None,
        "completed_at": note.get("done_on") or None,
        "goal": note.get("goal") or "",
        "tags": list(note.get("tags") or []),
        "list_name": note.get("list_name") or "",
        "path": note.get("relpath") or "",
        "key": note.get("key") or "",
    }


def sorted_due(rows: Iterable[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    priority = {"urgent": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
    ordered = sorted(rows, key=lambda note: (
        priority.get(str(note.get("priority") or "none"), 9),
        note.get("date") or "9999-99-99",
        note.get("title") or "",
    ))
    return ordered[:max(1, limit)]


__all__ = ["scan", "tasks", "open_tasks", "due_on", "overdue", "completed_between",
           "next_open", "report_task", "sorted_due"]
