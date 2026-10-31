"""Workbench: one read of everything today, plus a deterministic local draft.

The workbench is the product's daily loop: greeting and motto from the identity
note, the day's todo list (Markdown: `02_生活运营/00_待办事项/_系统/待办（todos）.md`), the 每日必做 checklist
(today's journal note), the habits planned for today, and whether the journal was
written. Progress is the plain average of those items (every item counts the
same), so finishing nine of ten shows 90%.

`GET /workbench/draft` builds the "今天干了什么" draft locally. AI polish is
not part of torch; free-form work happens in the embedded DeepSeek Harness.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from . import journal_note, todo_note, vault_layout
from .db import Database, utc_now
from .fortune import FortuneService
from .habits import HabitService
from .identity import DEFAULT_HEAT_COLOR, greeting_for, read_identity

SHANGHAI = ZoneInfo("Asia/Shanghai")
SESSION_PREFIX = "dsh-session-"
MAX_SESSION_NOTES = 3
MAX_SESSION_CHARS = 700
MAX_DRAFT_CHARS = 8_000


def shanghai_now() -> datetime:
    return datetime.now(SHANGHAI)


def validate_day(value: str, *, today: date | None = None) -> date:
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError
        result = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "日期必须是有效的 YYYY-MM-DD") from exc
    if result > (today if today is not None else shanghai_now().date()):
        raise HTTPException(422, "工作台不能查看未来日期")
    return result


def _error_text(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    return str(detail) if detail else str(exc)


def collect_day(database: Database, vault_dir: Path | str, day: str) -> dict[str, Any]:
    """Everything the workbench shows for one day. Read-only, no model call."""
    identity = read_identity(vault_dir)

    todos_error = None
    try:
        note = todo_note.read_todo_note(vault_dir)
    except (HTTPException, OSError, ValueError) as exc:
        note = {"exists": False, "revision": "", "todos": [], "daily_master": []}
        todos_error = _error_text(exc)
    todos = [
        {"key": item["key"], "line": item["line"], "title": item["title"],
         "completed": item["checked"], "completed_on": item["completed_on"]}
        for item in todo_note.select_day_items(note["todos"], day)
    ]

    daily_error = None
    try:
        daily_state = todo_note.read_daily_state(vault_dir, day)
    except (HTTPException, OSError, ValueError) as exc:
        daily_state = {"note_exists": False, "note_revision": "", "section_exists": False, "items": []}
        daily_error = _error_text(exc)
    in_journal = {todo_note.normalize_title(item["title"]): item for item in daily_state["items"]}
    daily: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for master in note["daily_master"]:
        title_key = todo_note.normalize_title(master["title"])
        entry = in_journal.get(title_key)
        daily.append({
            "title": master["title"],
            "key": entry["key"] if entry else None,
            "line": entry["line"] if entry else None,
            "done": bool(entry["done"]) if entry else False,
            "in_master": True,
        })
        seen_titles.add(title_key)
    for entry in daily_state["items"]:
        if todo_note.normalize_title(entry["title"]) not in seen_titles:
            daily.append({"title": entry["title"], "key": entry["key"], "line": entry["line"],
                          "done": bool(entry["done"]), "in_master": False})
    daily_missing = sum(1 for item in daily if item["in_master"] and item["key"] is None)

    habits_error = None
    habits: list[dict[str, Any]] = []
    try:
        habits = HabitService(database, vault_dir).day_rows(day)
    except (HTTPException, OSError, ValueError) as exc:
        habits_error = _error_text(exc)

    # 进度里的"日记"以**文件**为准（md-first）：今天的日记存在就算写了。
    journal = journal_note.read_note(vault_dir, day)
    journal_written = bool(journal.get("exists"))

    done_todos = [todo for todo in todos if todo["completed"]]
    done_daily = [item for item in daily if item["done"]]
    done_habits = [habit for habit in habits if habit["done"]]
    total = len(todos) + len(daily) + len(habits) + 1
    done = len(done_todos) + len(done_daily) + len(done_habits) + (1 if journal_written else 0)
    percent = round(done * 100 / total) if total else 0
    fortune = FortuneService(database).status(day)

    return {
        "day": day,
        "generated_at": utc_now(),
        "greeting": greeting_for(shanghai_now().hour) if day == shanghai_now().date().isoformat() else "那天",
        "name": identity.get("name") or "",
        "motto": identity.get("motto") or "",
        "mottos": identity.get("mottos") or [],
        "hero": identity.get("hero") or "",
        "heroes": identity.get("heroes") or [],
        "hero_overlay": identity.get("hero_overlay", 0.45),
        "hero_focal": identity.get("hero_focal") or "",
        "banner_mode": identity.get("banner_mode") or "quote",
        "banner_accent": identity.get("banner_accent") or "",
        "banner_blur": identity.get("banner_blur", 6.0),
        "banner_darkness": identity.get("banner_darkness", 30.0),
        "banner_left_stat": identity.get("banner_left_stat") or "notes",
        "banner_center_stat": identity.get("banner_center_stat") or "streak",
        "banner_heat_color": identity.get("banner_heat_color") or DEFAULT_HEAT_COLOR,
        "banner_details": identity.get("banner_details", True),
        "todos": todos,
        "todos_revision": note["revision"],
        "todos_exists": note["exists"],
        "todos_error": todos_error,
        "daily": daily,
        "daily_revision": daily_state["note_revision"],
        "daily_note_exists": daily_state["note_exists"],
        "daily_section_exists": daily_state["section_exists"],
        "daily_missing": daily_missing,
        "daily_error": daily_error,
        "habits": habits,
        "habits_error": habits_error,
        "journal": {
            "recorded": journal_written,
            "updated_at": journal.get("updated_at"),
            "has_next_action": _has_next_action(journal.get("content") or ""),
        },
        "progress": {"done": done, "total": total, "percent": percent},
        "fortune": fortune,
    }


def _has_next_action(content: str) -> bool:
    """日记正文里是否有「下一步」小节或行；文件是唯一权威，不再读 SQLite。"""
    return bool(journal_note.next_action_text(content))


def _strip_front_matter(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2].lstrip("\n") if len(parts) >= 3 else text


def session_notes(vault_dir: Path | str, day: str) -> list[dict[str, str]]:
    """Today's dsh session notes, trimmed to bullets (local files only)."""
    inboxes = [Path(vault_dir) / vault_layout.inbox_dir(), Path(vault_dir) / "90-Inbox"]
    found: list[tuple[float, dict[str, str]]] = []
    seen: set[Path] = set()
    for inbox in inboxes:
        if not inbox.is_dir():
            continue
        for path in inbox.glob(f"{SESSION_PREFIX}*.md"):
            if path in seen:
                continue
            seen.add(path)
            try:
                stamp = datetime.fromtimestamp(path.stat().st_mtime, SHANGHAI).date().isoformat()
            except OSError:
                continue
            if stamp != day:
                continue
            try:
                body = _strip_front_matter(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            lines = [line.strip("-* \t") for line in body.splitlines() if line.strip().startswith(("-", "*"))]
            text = "\n".join(lines) if lines else body.strip()
            found.append((path.stat().st_mtime, {"path": path.name, "text": text[:MAX_SESSION_CHARS]}))
    found.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in found[:MAX_SESSION_NOTES]]


def draft_parts(database: Database, vault_dir: Path | str, day: str) -> dict[str, Any]:
    """The local facts a journal draft is built from."""
    payload = collect_day(database, vault_dir, day)
    journal = journal_note.read_note(vault_dir, day)
    return {
        "day": day,
        "greeting": payload["greeting"],
        "done_todos": [todo["title"] for todo in payload["todos"] if todo["completed"]],
        "open_todos": [todo["title"] for todo in payload["todos"] if not todo["completed"]],
        "done_daily": [item["title"] for item in payload["daily"] if item["done"]],
        "open_daily": [item["title"] for item in payload["daily"] if not item["done"]],
        "done_habits": [habit["title"] for habit in payload["habits"] if habit["done"]],
        "journal_content": _strip_front_matter(journal.get("content") or "")[:1_000],
        "sessions": session_notes(vault_dir, day),
        "progress": payload["progress"],
    }


def local_draft(parts: dict[str, Any]) -> str:
    """A plain first-person draft, no model involved."""
    lines: list[str] = []
    if parts["done_todos"]:
        lines.append("今天完成：" + "、".join(parts["done_todos"]) + "。")
    if parts.get("done_daily"):
        lines.append("每日必做：" + "、".join(parts["done_daily"]) + " 完成了。")
    if parts.get("open_daily"):
        lines.append("每日必做还差：" + "、".join(parts["open_daily"]) + "。")
    if parts["done_habits"]:
        lines.append("习惯：" + "、".join(parts["done_habits"]) + " 都做了。")
    if parts["open_todos"]:
        lines.append("还没做完：" + "、".join(parts["open_todos"]) + "，明天继续。")
    for session in parts["sessions"]:
        summary = " ".join(line.strip() for line in session["text"].splitlines() if line.strip())
        if summary:
            lines.append("在 dsh 里：" + summary[:300])
    if parts["journal_content"].strip():
        lines.append("日记里已经写了：" + parts["journal_content"].strip()[:300])
    if not lines:
        lines.append("今天还没有留下记录。")
    return "\n".join(lines)[:MAX_DRAFT_CHARS]


def create_workbench_router(database: Database, vault_dir: Path | str,
                            prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["workbench"])

    @router.get("/workbench/today")
    def workbench_today(day: str = Query(default="")):
        target = validate_day(day) if day else shanghai_now().date()
        return collect_day(database, vault_dir, target.isoformat())

    @router.get("/workbench/draft")
    def workbench_draft(day: str = Query(default="")):
        target = validate_day(day) if day else shanghai_now().date()
        parts = draft_parts(database, vault_dir, target.isoformat())
        return {"day": target.isoformat(), "parts": parts, "text": local_draft(parts), "mode": "local"}

    return router
