"""Assemble read-only context for a role round.

Why this is server-side
-----------------------
A role must never receive an arbitrary blob from the browser: that would let a
malicious page (or a note) choose what the model sees.  The client sends only
typed selectors (``{"entry": "note", "path": "..."}``); this module reads the
real Markdown/DB sources, numbers them ``[S1]``…, applies a character budget,
and tells the UI exactly what was included and whether it was truncated.

Nothing here writes.  A missing source is reported as a short honest line, not
silently dropped, so the role can say "库里没有".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import identity, journal_note, pillar_notes, task_reports, vault_layout, workbench
from .db import Database
from .journal_shared import shanghai_today, validate_day

MAX_SELECTORS = 8
MAX_TOTAL_CHARS = 24_000
DEFAULT_TOTAL_CHARS = 16_000
SUPPORTED_ENTRIES = {"identity", "note", "journal", "tasks"}
FUTURE_ENTRIES = {"memory_card", "proposal", "share", "love", "health", "finance"}

IDENTITY_LABELS = {
    "name": "称呼",
    "motto": "座右铭",
    "worldview": "世界观",
    "lifeview": "人生观",
    "values": "价值观",
    "notes": "补充",
}


class ContextError(ValueError):
    """A selector is malformed or not enabled yet; the API turns this into 422."""


@dataclass(frozen=True)
class ContextBundle:
    text: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    chars: int = 0
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "sources": self.sources,
            "chars": self.chars,
            "truncated": self.truncated,
        }


def _trim(text: str, limit: int) -> tuple[str, bool]:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value, False
    return value[: max(0, limit - 1)].rstrip() + "…", True


def _source(source_id: str, kind: str, title: str, path: str, body: str,
            limit: int) -> tuple[dict[str, Any], str, bool]:
    text, truncated = _trim(body, limit)
    meta = {"id": source_id, "kind": kind, "title": title or kind,
            "path": path, "chars": len(text)}
    header = f"[{source_id}] {meta['title']}" + (f"（{path}）" if path else "")
    return meta, f"{header}\n{text}", truncated


def _identity_context(vault_dir: Path | str, source_id: str) -> tuple[dict[str, Any], str, bool]:
    data = identity.read_identity(vault_dir)
    lines = []
    for key, label in IDENTITY_LABELS.items():
        value = str(data.get(key) or "").strip()
        if value:
            lines.append(f"{label}：{value}")
    body = "\n".join(lines) or "身份笔记还是空的。"
    return _source(source_id, "identity", "身份与三观",
                   str(data.get("path") or ""), body, 2_500)


def _note_context(vault_dir: Path | str, selector: Mapping[str, Any],
                  source_id: str) -> tuple[dict[str, Any], str, bool]:
    path = str(selector.get("path") or "").strip()
    if not path:
        raise ContextError("note 选择器缺少 path")
    try:
        note = pillar_notes.read_note(vault_dir, path)
    except Exception as exc:  # HTTPException/OSError/ValueError
        detail = getattr(exc, "detail", None) or str(exc)
        return _source(source_id, "note", Path(path).stem, path,
                       f"读不到这篇笔记：{detail}", 500)
    body = str(note.get("body") or "").strip()
    header = f"标题：{note.get('title') or Path(path).stem}\n类型：{note.get('type') or ''}\n\n"
    return _source(source_id, "note", str(note.get("title") or Path(path).stem),
                   path, header + body, 8_000)


def _journal_context(vault_dir: Path | str, selector: Mapping[str, Any],
                     source_id: str) -> tuple[dict[str, Any], str, bool]:
    raw_day = str(selector.get("day") or "").strip()
    try:
        day = validate_day(raw_day).isoformat() if raw_day else shanghai_today().isoformat()
    except Exception as exc:
        raise ContextError(f"journal 日期不合法：{raw_day}") from exc
    note = journal_note.read_note(vault_dir, day)
    if not note.get("exists"):
        body = "这一天没有日记。"
    else:
        body = str(note.get("content") or "").strip()
    return _source(source_id, "journal", f"{day} 日记",
                   str(note.get("relpath") or vault_layout.journal_path(day)), body, 4_000)


def _tasks_context(database: Database, vault_dir: Path | str, selector: Mapping[str, Any],
                   source_id: str) -> tuple[dict[str, Any], str, bool]:
    raw_day = str(selector.get("day") or "").strip()
    try:
        day = validate_day(raw_day).isoformat() if raw_day else shanghai_today().isoformat()
    except Exception as exc:
        raise ContextError(f"tasks 日期不合法：{raw_day}") from exc
    lines = [f"日期：{day}"]
    try:
        rows = task_reports.open_tasks(vault_dir)
    except (OSError, ValueError):
        rows = []
    rows.sort(key=lambda note: (note.get("date") or "9999-99-99",
                                note.get("title") or ""))
    overdue = [note for note in rows if note.get("date") and note["date"] < day]
    due_today = [note for note in rows if note.get("date") == day]
    undated = [note for note in rows if not note.get("date")]
    for label, group in (("逾期", overdue), ("今天到期", due_today), ("未排期", undated)):
        if not group:
            continue
        lines.append(f"\n{label}（{len(group)}）：")
        for note in group[:20]:
            bits = [f"- {note.get('title') or '未命名'}"]
            if note.get("priority") and note["priority"] != "none":
                bits.append(f"优先级 {note['priority']}")
            if note.get("list_name"):
                bits.append(f"清单 {note['list_name']}")
            if note.get("tags"):
                bits.append("标签 " + "、".join(note["tags"]))
            lines.append("；".join(bits))
    if not rows:
        lines.append("\n目前没有未完成任务。")
    try:
        payload = workbench.collect_day(database, vault_dir, day)
        habits = payload.get("habits") or []
        if habits:
            done = sum(1 for habit in habits if habit.get("done"))
            lines.append(f"\n今日习惯：{done}/{len(habits)} 完成。")
    except Exception:
        pass
    return _source(source_id, "tasks", f"{day} 任务与习惯", "", "\n".join(lines), 3_000)


def _build_one(database: Database, vault_dir: Path | str, selector: Any,
               source_id: str) -> tuple[dict[str, Any], str, bool]:
    if not isinstance(selector, Mapping):
        raise ContextError("选择器必须是对象")
    entry = str(selector.get("entry") or "").strip()
    if not entry:
        raise ContextError("选择器缺少 entry")
    if entry in FUTURE_ENTRIES:
        raise ContextError(f"上下文来源 {entry} 还没有启用")
    if entry not in SUPPORTED_ENTRIES:
        raise ContextError(f"未知上下文来源：{entry}")
    if entry == "identity":
        return _identity_context(vault_dir, source_id)
    if entry == "note":
        return _note_context(vault_dir, selector, source_id)
    if entry == "journal":
        return _journal_context(vault_dir, selector, source_id)
    return _tasks_context(database, vault_dir, selector, source_id)


def assemble(database: Database, vault_dir: Path | str, *,
             selectors: Iterable[Any] | None = None,
             entry: str = "", max_chars: int = DEFAULT_TOTAL_CHARS) -> ContextBundle:
    """Read the selected sources and return numbered, budget-limited text.

    Empty selectors fall back to the role's entry defaults (identity + tasks for
    ``workbench.tasks``; identity for ``self.*``).  This keeps the common case
    simple without letting the browser upload free text.
    """
    items = list(selectors or [])
    if not items:
        # 空选择器 = 不发送任何用户资料。润色/判类等一次性能力不该顺手把身份发出去；
        # 角色对话的默认上下文由 RoleService 按角色显式补齐。
        return ContextBundle(text="", sources=[], chars=0, truncated=False)
    if len(items) > MAX_SELECTORS:
        raise ContextError(f"一次最多选择 {MAX_SELECTORS} 个来源")
    budget = max(1_000, min(int(max_chars or DEFAULT_TOTAL_CHARS), MAX_TOTAL_CHARS))
    sources: list[dict[str, Any]] = []
    blocks: list[str] = []
    truncated = False
    used = 0
    for index, selector in enumerate(items, start=1):
        meta, block, cut = _build_one(database, vault_dir, selector, f"S{index}")
        truncated = truncated or cut
        remaining = budget - used
        if remaining <= 0:
            truncated = True
            break
        if len(block) > remaining:
            block, _ = _trim(block, remaining)
            truncated = True
        sources.append(meta)
        blocks.append(block)
        used += len(block) + 2
    text = "\n\n".join(blocks).strip()
    return ContextBundle(text=text, sources=sources, chars=len(text), truncated=truncated)


__all__ = [
    "ContextBundle", "ContextError", "SUPPORTED_ENTRIES", "FUTURE_ENTRIES",
    "DEFAULT_TOTAL_CHARS", "MAX_TOTAL_CHARS", "assemble",
]
