"""Markdown-first todos: `00-System/待办（todos）.md` is the data, the workbench is the front.

用户定案（2026-10-05）：能用 Markdown 语法实现的功能尽量用 md，vault 文件是数据本体。

- `00-System/待办（todos）.md`：`## 待办` 小节 = 待办清单（`- [ ]`，勾选时行尾追 ` ✅ YYYY-MM-DD`）；
  `## 每日必做` 小节 = 长期清单的**定义**。
- 每日必做的勾选状态写进**当天的日记**（`02-Life/journal/YYYY-MM-DD.md` 的 `## 每日必做`
  小节）——历史天然按天留存；产品只做镜像与勾选，绝不改用户已写的行。
- 所有写操作都是外科手术：按 key 定位 → 校验该行原文未变 → 只重写这一行 → 原子替换
  （tmp + os.replace，先例见 identity.write_identity）；文件在别处被改过就 409，让用户刷新。

`00-System` 在 vault.EXCLUDED_DIRECTORIES 里：待办文件不会进资料库索引、不参与记忆提取——
这是有意为之，别"修"。

注意：`journal_note` 必须**函数内惰性导入**（journal_note → workbench → todo_note 构成
模块环，模块级导入会得到部分初始化的模块）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .db import Database, utc_now
from .journal import validate_day
from .markdown_store import (
    guarded_write as _guarded_write,
    heading_line as _heading_line,
    load_note,
    revision as _revision,
    section_bounds as _section_bounds,
)
from .task_import import parse_checkboxes
from .vault import managed_note_path, note_relative
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SHANGHAI = ZoneInfo("Asia/Shanghai")
TODO_REL = Path("00-System", "待办（todos）.md")
TODO_LEGACY_REL = Path("00-System", "待办.md")
TODOS_HEADING = "待办"
DAILY_HEADING = "每日必做"
MAX_TITLE = 200
CHECKBOX_DATE = re.compile(r"\s*✅\s*(\d{4}-\d{2}-\d{2})\s*$")


def _todo_path(vault_dir: Path | str) -> Path:
    """现行文件名优先；旧库里的 `待办.md` 继续读写（不自动改名）。"""
    return managed_note_path(vault_dir, TODO_REL.as_posix(), TODO_LEGACY_REL.as_posix())

SKELETON = """---
type: todo
---

# 待办

## 待办

## 每日必做
<!-- 每天都要做的事，一行一条；勾选状态记在当天日记里 -->
"""

TodoKey = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class TodoAdd(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    revision: str = Field(default="", max_length=64)


class TodoToggle(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: TodoKey
    line: int = Field(default=0, ge=0)
    done: bool
    day: str = ""


class TodoDelete(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: TodoKey
    line: int = Field(default=0, ge=0)


class TodoReorder(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    keys: list[TodoKey] = Field(min_length=1, max_length=200)
    day: str = ""


class TodoRename(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: TodoKey
    line: int = Field(default=0, ge=0)
    title: str = Field(min_length=1, max_length=MAX_TITLE)


class DailySync(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    day: str = ""


class DailyToggle(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    day: str = ""
    key: str = Field(default="", max_length=64)
    title: str = Field(default="", max_length=MAX_TITLE)
    done: bool


def _today() -> str:
    return datetime.now(SHANGHAI).date().isoformat()


def _audit(database: Database, event_type: str, details: dict[str, Any]) -> None:
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO audit_events(event_type, detail_json, created_at) VALUES (?, ?, ?)",
            (event_type, json.dumps(details, ensure_ascii=False), utc_now()),
        )


def normalize_title(value: str) -> str:
    return " ".join(value.split())


def _load(path: Path) -> dict[str, Any]:
    """Read one todo-ish Markdown file; missing file is a normal state."""
    return load_note(path, bad_encoding="待办文件不是 UTF-8 编码，暂不能编辑")


def _parse_items(loaded: dict[str, Any]) -> list[dict[str, Any]]:
    if not loaded["exists"]:
        return []
    body = "\n".join(loaded["lines"][loaded["body_start"]:])
    items, _warnings = parse_checkboxes(body)
    parsed: list[dict[str, Any]] = []
    for item in items:
        title = item["title"]
        completed_on = None
        match = CHECKBOX_DATE.search(title)
        if match:
            completed_on = match.group(1)
            title = CHECKBOX_DATE.sub("", title).strip()
        heading = str(item.get("heading") or "")
        section = heading.split(" / ")[-1] if heading else ""
        parsed.append({
            "key": item["key"],
            "line": loaded["body_start"] + item["line"],
            "title": title or item["title"],
            "checked": bool(item["checked"]),
            "completed_on": completed_on,
            "raw": item["description"],
            "section": section,
        })
    return parsed


def _format_line(title: str, *, checked: bool, day: str | None) -> str:
    suffix = f" ✅ {day}" if checked and day else ""
    return f"- [{'x' if checked else ' '}] {title}{suffix}\n"


def read_todo_note(vault_dir: Path | str) -> dict[str, Any]:
    """Parse the todo note: everything plus the 每日必做 master definition."""
    path = _todo_path(vault_dir)
    loaded = _load(path)
    items = _parse_items(loaded)
    todos = [item for item in items if item["section"] != DAILY_HEADING]
    daily_master = [item for item in items if item["section"] == DAILY_HEADING]
    return {
        "exists": loaded["exists"],
        "revision": loaded["revision"],
        "path": note_relative(path, vault_dir),
        "todos": todos,
        "daily_master": daily_master,
    }


def select_day_items(items: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    """工作台口径：未完成 + 今天完成的；更早勾完的（带旧 ✅ 日期）留在文件里不占列表。"""
    selected = [
        item for item in items
        if not item["checked"] or item["completed_on"] in (day, None)
    ]
    return sorted(selected, key=lambda item: item["checked"])


def _locate(items: list[dict[str, Any]], key: str) -> dict[str, Any]:
    for item in items:
        if item["key"] == key:
            return item
    raise HTTPException(409, "这条待办已不在文件里（可能已在 Obsidian 中被修改或删除），请刷新后重试")


def _assert_line(loaded: dict[str, Any], item: dict[str, Any]) -> None:
    lines = loaded["lines"]
    index = item["line"] - 1
    if index < 0 or index >= len(lines) or lines[index] != item["raw"]:
        raise HTTPException(409, "文件内容已变化，请刷新后重试")


def add_todo(vault_dir: Path | str, payload: TodoAdd, *, read_only: bool = False) -> dict[str, Any]:
    try:
        ensure_vault_writable(read_only, "新增待办")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    title = payload.title.strip()
    if not title or len(title) > MAX_TITLE or any(ord(char) < 32 for char in title):
        raise HTTPException(422, f"待办标题需要 1–{MAX_TITLE} 个字符，且不能包含换行")
    path = _todo_path(vault_dir)
    loaded = _load(path)
    expected = payload.revision.strip()
    if loaded["exists"] and expected != loaded["revision"]:
        raise HTTPException(409, "待办文件已在别处修改：请刷新后再添加")
    if not loaded["exists"] and expected:
        raise HTTPException(409, "待办文件已被删除：请刷新后再添加")

    if loaded["exists"]:
        lines = list(loaded["lines"])
    else:
        lines = SKELETON.replace("\n", loaded["newline"]).split(loaded["newline"])
        loaded = {**loaded, "lines": lines}
    heading_index = _heading_line(lines, 0, TODOS_HEADING)
    if heading_index < 0:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([f"## {TODOS_HEADING}", ""])
        insert_at = len(lines)
    else:
        content_start, insert_at = _section_bounds(lines, 0, heading_index)
    line = _format_line(title, checked=False, day=None).rstrip("\n")
    lines.insert(insert_at, line)

    raw = _guarded_write(loaded, lines)
    return {"revision": _revision(raw), "item": {"title": title, "line": insert_at + 1}}


def toggle_todo(vault_dir: Path | str, payload: TodoToggle, day: str, *,
                read_only: bool = False) -> dict[str, Any]:
    try:
        ensure_vault_writable(read_only, "勾选待办")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    path = _todo_path(vault_dir)
    loaded = _load(path)
    if not loaded["exists"]:
        raise HTTPException(409, "待办文件不存在，请先添加一条待办")
    items = _parse_items(loaded)
    item = _locate(items, payload.key)
    _assert_line(loaded, item)
    lines = list(loaded["lines"])
    lines[item["line"] - 1] = _rewrite_checkbox(lines[item["line"] - 1], done=payload.done, day=day)
    raw = _guarded_write(loaded, lines)
    return {"revision": _revision(raw), "key": payload.key, "done": payload.done}


def delete_todo(vault_dir: Path | str, payload: TodoDelete, *, read_only: bool = False) -> dict[str, Any]:
    try:
        ensure_vault_writable(read_only, "删除待办")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    path = _todo_path(vault_dir)
    loaded = _load(path)
    if not loaded["exists"]:
        raise HTTPException(409, "待办文件不存在")
    items = _parse_items(loaded)
    item = _locate(items, payload.key)
    _assert_line(loaded, item)
    lines = list(loaded["lines"])
    del lines[item["line"] - 1]
    raw = _guarded_write(loaded, lines)
    return {"revision": _revision(raw), "deleted": True}


def rename_todo(vault_dir: Path | str, payload: TodoRename, *,
                read_only: bool = False) -> dict[str, Any]:
    """行内编辑：只换标题，勾选状态与 ✅ 日期原样保留。"""
    try:
        ensure_vault_writable(read_only, "编辑待办")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    title = payload.title.strip()
    if not title or len(title) > MAX_TITLE or any(ord(char) < 32 for char in title):
        raise HTTPException(422, f"待办标题需要 1–{MAX_TITLE} 个字符，且不能包含换行")
    path = _todo_path(vault_dir)
    loaded = _load(path)
    if not loaded["exists"]:
        raise HTTPException(409, "待办文件不存在")
    items = _parse_items(loaded)
    item = _locate(items, payload.key)
    _assert_line(loaded, item)
    from .task_import import CHECKBOX

    line = loaded["lines"][item["line"] - 1]
    match = CHECKBOX.match(line)
    if not match or match.group(2) is None:
        raise HTTPException(409, "目标行已不是复选框，请刷新后重试")
    content = match.group(2)
    date_match = CHECKBOX_DATE.search(content)
    suffix = f" ✅ {date_match.group(1)}" if date_match else ""
    lines = list(loaded["lines"])
    lines[item["line"] - 1] = line[: match.start(2)] + title + suffix
    raw = _guarded_write(loaded, lines)
    return {"revision": _revision(raw), "key": payload.key, "title": title}


def reorder_todos(vault_dir: Path | str, payload: TodoReorder, *,
                  read_only: bool = False) -> dict[str, Any]:
    """把工作台可见的那批待办按新顺序写回文件（隐藏的旧完成项留在原位不动）。

    payload.keys 必须是当日可见集合的一个排列；集合不一致 = 文件已在别处变化 → 409。
    """
    try:
        ensure_vault_writable(read_only, "调整待办顺序")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    path = _todo_path(vault_dir)
    loaded = _load(path)
    if not loaded["exists"]:
        raise HTTPException(409, "待办文件不存在")
    section_items = [item for item in _parse_items(loaded) if item["section"] != DAILY_HEADING]
    visible = select_day_items(section_items, payload.day) if payload.day else section_items
    expected_keys = {item["key"] for item in visible}
    if len(payload.keys) != len(expected_keys) or set(payload.keys) != expected_keys:
        raise HTTPException(409, "待办清单已变化，请刷新后再调整顺序")
    by_key = {item["key"]: item for item in visible}
    slots = sorted(item["line"] for item in visible)
    lines = list(loaded["lines"])
    for slot, key in zip(slots, payload.keys):
        lines[slot - 1] = by_key[key]["raw"]
    raw = _guarded_write(loaded, lines)
    return {"revision": _revision(raw), "count": len(slots)}


def _rewrite_checkbox(line: str, *, done: bool, day: str | None) -> str:
    """Flip one checkbox in place, preserving marker, indentation and spacing."""
    from .task_import import CHECKBOX

    match = CHECKBOX.match(line)
    if not match or match.group(2) is None:
        raise HTTPException(409, "目标行已不是复选框，请刷新后重试")
    content = CHECKBOX_DATE.sub("", match.group(2)).rstrip()
    if done and day:
        content = f"{content} ✅ {day}"
    state = "x" if done else " "
    return line[: match.start(1)] + state + line[match.end(1): match.start(2)] + content


# ── 每日必做：镜像进当天日记 ────────────────────────────────────────────────

def _journal_path(vault_dir: Path | str, day: str) -> Path:
    from .journal_note import note_abspath

    return note_abspath(vault_dir, day)


def read_daily_state(vault_dir: Path | str, day: str) -> dict[str, Any]:
    """Read today's journal 每日必做 section. Pure read — never writes."""
    path = _journal_path(vault_dir, day)
    loaded = _load(path)
    items = [item for item in _parse_items(loaded) if item["section"] == DAILY_HEADING]
    section_exists = loaded["exists"] and _heading_line(
        loaded["lines"], loaded["body_start"], DAILY_HEADING) >= 0
    return {
        "note_exists": loaded["exists"],
        "note_revision": loaded["revision"],
        "section_exists": section_exists,
        "items": [
            {"key": item["key"], "line": item["line"], "title": item["title"],
             "done": item["checked"]}
            for item in items
        ],
    }


def ensure_daily_section(vault_dir: Path | str, day: str, master_titles: list[str], *,
                         read_only: bool = False) -> dict[str, Any]:
    """Mirror the master list into today's journal note. Never edits existing lines."""
    from .journal_note import render_skeleton

    try:
        ensure_vault_writable(read_only, "同步每日必做")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    path = _journal_path(vault_dir, day)
    loaded = _load(path)
    master = [normalize_title(title) for title in master_titles if normalize_title(title)]
    result = {"created_note": False, "created_section": False, "added": [], "changed": False}

    if not loaded["exists"]:
        body = render_skeleton(vault_dir, day).rstrip("\n")
        items_text = "".join(_format_line(title, checked=False, day=None) for title in master)
        content = f"{body}\n\n## {DAILY_HEADING}\n\n{items_text}"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            result.update({"created_note": True, "created_section": True,
                           "added": list(master), "changed": True})
            return {**read_daily_state(vault_dir, day), **result}
        except FileExistsError:
            loaded = _load(path)  # a parallel writer won; mirror below

    items = [item for item in _parse_items(loaded) if item["section"] == DAILY_HEADING]
    present = {normalize_title(item["title"]) for item in items}
    missing = [title for title in master if title not in present]

    heading_index = _heading_line(loaded["lines"], loaded["body_start"], DAILY_HEADING)
    if heading_index < 0:
        lines = list(loaded["lines"])
        while lines and not lines[-1].strip():
            lines.pop()
        lines.extend(["", f"## {DAILY_HEADING}", ""])
        lines.extend(_format_line(title, checked=False, day=None).rstrip("\n") for title in master)
        raw = _guarded_write(loaded, lines)
        result.update({"created_section": True, "added": list(master), "changed": True})
        return {**read_daily_state(vault_dir, day), **result, "revision": _revision(raw)}

    if not missing:
        return {**read_daily_state(vault_dir, day), **result, "revision": loaded["revision"]}

    lines = list(loaded["lines"])
    _, insert_at = _section_bounds(lines, loaded["body_start"], heading_index)
    for offset, title in enumerate(missing):
        lines.insert(insert_at + offset, _format_line(title, checked=False, day=None).rstrip("\n"))
    raw = _guarded_write(loaded, lines)
    result.update({"added": missing, "changed": True})
    return {**read_daily_state(vault_dir, day), **result, "revision": _revision(raw)}


def toggle_daily(vault_dir: Path | str, day: str, key: str, title: str, done: bool, *,
                 read_only: bool = False) -> dict[str, Any]:
    try:
        ensure_vault_writable(read_only, "勾选每日必做")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    note = read_todo_note(vault_dir)
    master_titles = [item["title"] for item in note["daily_master"]]
    ensure_daily_section(vault_dir, day, master_titles, read_only=read_only)

    path = _journal_path(vault_dir, day)
    loaded = _load(path)
    items = [item for item in _parse_items(loaded) if item["section"] == DAILY_HEADING]
    item = None
    if key:
        item = next((candidate for candidate in items if candidate["key"] == key), None)
    if item is None and title:
        wanted = normalize_title(title)
        item = next(
            (candidate for candidate in items if normalize_title(candidate["title"]) == wanted),
            None)
    if item is None:
        raise HTTPException(409, "这条每日必做已不在今天的笔记里，请刷新后重试")
    _assert_line(loaded, item)
    lines = list(loaded["lines"])
    lines[item["line"] - 1] = _rewrite_checkbox(lines[item["line"] - 1], done=done, day=None)
    raw = _guarded_write(loaded, lines)
    return {"revision": _revision(raw), "done": done, "day": day}


def create_todo_router(vault_dir: Path | str, database: Database, prefix: str = "/api",
                       read_only: bool = False) -> APIRouter:
    """Mount with the app's shared data-lock dependency, like the journal routers."""
    router = APIRouter(prefix=prefix, tags=["todos"])

    @router.get("/todos")
    def get_todos():
        return read_todo_note(vault_dir)

    @router.post("/todos")
    def post_todo(payload: TodoAdd):
        result = add_todo(vault_dir, payload, read_only=read_only)
        _audit(database, "todo_added", {"count": 1})
        return result

    @router.post("/todos/toggle")
    def post_toggle(payload: TodoToggle):
        target = validate_day(payload.day).isoformat() if payload.day else _today()
        result = toggle_todo(vault_dir, payload, target, read_only=read_only)
        _audit(database, "todo_toggled", {"count": 1, "day": target})
        return result

    @router.post("/todos/delete")
    def post_delete(payload: TodoDelete):
        result = delete_todo(vault_dir, payload, read_only=read_only)
        _audit(database, "todo_deleted", {"count": 1})
        return result

    @router.post("/todos/reorder")
    def post_reorder(payload: TodoReorder):
        result = reorder_todos(vault_dir, payload, read_only=read_only)
        _audit(database, "todos_reordered", {"count": result["count"]})
        return result

    @router.post("/todos/rename")
    def post_rename(payload: TodoRename):
        result = rename_todo(vault_dir, payload, read_only=read_only)
        _audit(database, "todo_renamed", {"count": 1})
        return result

    @router.get("/todos/daily")
    def get_daily(day: str = Query(default="")):
        target = validate_day(day).isoformat() if day else _today()
        state = read_daily_state(vault_dir, target)
        note = read_todo_note(vault_dir)
        return {"day": target, "master": note["daily_master"], **state}

    @router.post("/todos/daily/sync")
    def post_daily_sync(payload: DailySync):
        target = validate_day(payload.day).isoformat() if payload.day else _today()
        note = read_todo_note(vault_dir)
        result = ensure_daily_section(
            vault_dir, target, [item["title"] for item in note["daily_master"]],
            read_only=read_only)
        if result.get("changed"):
            _audit(database, "daily_synced", {"day": target, "added": len(result.get("added") or [])})
        return {"day": target, **result}

    @router.post("/todos/daily/toggle")
    def post_daily_toggle(payload: DailyToggle):
        target = validate_day(payload.day).isoformat() if payload.day else _today()
        result = toggle_daily(vault_dir, target, payload.key, payload.title, payload.done,
                              read_only=read_only)
        _audit(database, "daily_toggled", {"day": target, "done": payload.done})
        return result

    return router
