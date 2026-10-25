"""Goals read from the life-plan Markdown notes.

The old SQLite ``goals`` backend is retired (2026-10-22). Annual, quarterly and
monthly goals already live under ``00_人生路/04_目标``; this module is the
single read source for the task-import goal picker, scheduler drafts and tools.

A goal's stable id is its vault-relative path. Nothing here writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import pillar_notes, vault_layout

GOAL_CHAPTERS = ("goal_year", "goal_quarter", "goal_month")


def _active(note: dict[str, Any]) -> bool:
    return str(note.get("status") or "active").casefold() in {"", "active", "进行中", "ongoing"}


def list_goals(vault_dir: Path | str, *, active_only: bool = False,
               limit: int = 100) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chapter in GOAL_CHAPTERS:
        for note in pillar_notes.list_notes(vault_dir, chapter=chapter, limit=500):
            if active_only and not _active(note):
                continue
            rows.append({
                "id": note["path"],
                "path": note["path"],
                "title": note["title"],
                "status": "active" if _active(note) else str(note.get("status") or "active"),
                "chapter": chapter,
                "date": note.get("date") or "",
                "type": note.get("type") or "goal",
            })
            if len(rows) >= limit:
                return rows
    return rows


def resolve_goal(vault_dir: Path | str, path: str) -> dict[str, Any] | None:
    """Return a goal note only when the path points inside a goal chapter."""
    relative = str(path or "").strip().replace("\\", "/")
    if not relative or vault_layout.chapter_key_of(relative) not in GOAL_CHAPTERS:
        return None
    try:
        note = pillar_notes.read_note(vault_dir, relative)
    except Exception:
        return None
    return {
        "id": note["path"],
        "path": note["path"],
        "title": note["title"],
        "status": note.get("status") or "active",
        "chapter": note.get("chapter") or vault_layout.chapter_key_of(relative),
        "date": note.get("date") or "",
        "type": note.get("type") or "goal",
    }


def active_goals(vault_dir: Path | str, limit: int = 3) -> list[dict[str, Any]]:
    return list_goals(vault_dir, active_only=True, limit=max(1, min(50, limit)))


def count_active(vault_dir: Path | str) -> int:
    return len(list_goals(vault_dir, active_only=True, limit=500))


__all__ = ["GOAL_CHAPTERS", "list_goals", "resolve_goal", "active_goals", "count_active"]
