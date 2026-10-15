"""时间管理模块的任务本体：`00-System/任务.md`（2026-10-08，用户点名的“时间管理工具模块”）。

沿用「今日待办」那套 Markdown-first 纪律（见 `todo_note.py` 的模块说明）：**文件是数据本体**，
产品只做前台与外科手术式写回——按 key 定位 → 校验该行原文未变 → 只重写这一行 → 原子替换
（tmp + os.replace）；文件在别处（Obsidian）被改过就 409，让界面刷新后重来。

文件结构：

```markdown
---
type: tasks
清单:
  - 名称: 收集箱
    颜色: "#3b82f6"
  - 名称: 客户A
    颜色: "#ef4444"
    文件夹: 工作
    置顶: true
标签:
  - 名称: 重要
    颜色: "#ef4444"
---

# 任务

## 收集箱
- [ ] 给客户回邮件 📌 ⏫ 📅 2026-10-09 #重要 ➕ 2026-10-02 ✅ 2026-10-05
```

- 一个清单 = 一个 `## <清单名>` 小节；文件夹是清单定义上的一个字段（逻辑分组，不是真目录）。
- 任务行沿用 Obsidian Tasks 插件的通用标记：`📅 日期`（哪天显示）、`⏰ 时间`（可选，`HH:MM` 或
  `HH:MM-HH:MM`，2026-10-11 随日历视图加入）、`⏫高/🔼中/🔽低`（缺省=无优先级）、
  `#标签`、`✅ 完成日`、`➕ 创建日`、`📌 置顶`。不认识的符号留在标题里，绝不吞掉用户写的字。
- 清单 / 文件夹 / 标签的增删改也写这个文件：定义住在 front matter（这一段由产品托管），
  正文只动受影响的行（重命名清单换 `## 标题`；重命名/删除标签改任务行里的 `#标签`）。
- 批量动作（`/api/time/tasks/bulk`）：delete / move / tag / date / priority / pin / duplicate —— 一次写回。

`00-System` 在 `vault.EXCLUDED_DIRECTORIES` 里（有意为之）：任务文件不进资料库索引、不参与记忆提取。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .db import Database, utc_now
from .task_import import parse_checkboxes
from .todo_note import _guarded_write, _heading_line, _section_bounds  # 同一套外科手术协议
from .vault import MAX_NOTE_BYTES, safe_note_path
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SHANGHAI = ZoneInfo("Asia/Shanghai")
TASKS_REL = Path("00-System", "任务.md")
TASKS_TITLE = "任务"
INBOX = "收集箱"
MAX_TITLE = 200
MAX_LIST = 40
MAX_TAGS = 12
MAX_TAG_NAME = 24

PRIORITY_MARK = {"high": "⏫", "medium": "🔼", "low": "🔽"}
MARK_PRIORITY = {mark: name for name, mark in PRIORITY_MARK.items()}
PRIORITIES = ("none", "low", "medium", "high")

DATE_RE = re.compile(r"(?:^|\s)📅\s*(\d{4}-\d{2}-\d{2})(?=\s|$)")
TIME_RE = re.compile(r"(?:^|\s)⏰\s*(\d{1,2}:\d{2}(?:\s*-\s*\d{1,2}:\d{2})?)(?=\s|$)")
TIME_VALUE_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(?:-([01]?\d|2[0-3]):([0-5]\d))?$")
DONE_RE = re.compile(r"(?:^|\s)✅\s*(\d{4}-\d{2}-\d{2})(?=\s|$)")
CREATED_RE = re.compile(r"(?:^|\s)➕\s*(\d{4}-\d{2}-\d{2})(?=\s|$)")
PIN_RE = re.compile(r"(?:^|\s)📌(?=\s|$)")
TAG_RE = re.compile(r"(?:^|\s)#([^\s#]+)")
# 复选框标记与 todo_note 的 `- [ ] ` 同源；这里只要把标记从行首剥掉。
CHECKBOX_PREFIX = re.compile(r"^\s*[-*+]\s*\[[ xX]\]\s*")

SKELETON = """---
type: tasks
清单:
  - 名称: 收集箱
    颜色: "#3b82f6"
标签: []
---

# 任务

## 收集箱
"""

TaskKey = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    list_name: str = Field(default="", max_length=MAX_LIST)
    date: str = Field(default="", max_length=10)
    time: str = Field(default="", max_length=11)
    priority: str = Field(default="none", max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    revision: str = Field(default="", max_length=64)


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: TaskKey
    line: int = Field(default=0, ge=0)
    revision: str = Field(default="", max_length=64)
    title: str = Field(default="", max_length=MAX_TITLE)
    list_name: str = Field(default="", max_length=MAX_LIST)
    date: str = Field(default="", max_length=10)
    time: str = Field(default="", max_length=11)
    priority: str = Field(default="", max_length=10)
    tags: list[str] | None = Field(default=None, max_length=MAX_TAGS)
    pinned: bool | None = None
    done: bool | None = None
    day: str = Field(default="", max_length=10)  # 完成日期（缺省=今天，上海时区）


class TaskKeyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: TaskKey
    line: int = Field(default=0, ge=0)
    revision: str = Field(default="", max_length=64)


class TaskDuplicate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: TaskKey
    line: int = Field(default=0, ge=0)
    revision: str = Field(default="", max_length=64)
    list_name: str = Field(default="", max_length=MAX_LIST)


def _today() -> str:
    return datetime.now(SHANGHAI).date().isoformat()


def _audit(database: Database, event_type: str, details: dict[str, Any]) -> None:
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO audit_events(event_type, detail_json, created_at) VALUES (?, ?, ?)",
            (event_type, json.dumps(details, ensure_ascii=False), utc_now()),
        )


def _revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def _clean_date(value: str, field: str) -> str:
    """日期只校验格式——注意不能复用日记的 validate_day：它拦未来日期，而任务就是要排到未来。"""
    text = (value or "").strip()
    if not text:
        return ""
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as error:
        raise HTTPException(422, f"{field}要写成 YYYY-MM-DD") from error
    return text


def _clean_time(value: str) -> str:
    """时间只认 `HH:MM` 或 `HH:MM-HH:MM`（24 小时制）；空串 = 没有时间（进日历的「全天」区）。"""
    text = re.sub(r"\s+", "", str(value or "").strip())
    if not text:
        return ""
    match = TIME_VALUE_RE.match(text)
    if match is None:
        raise HTTPException(422, "时间要写成 HH:MM 或 HH:MM-HH:MM（24 小时制）")
    start = f"{int(match.group(1)):02d}:{match.group(2)}"
    if match.group(3) is None:
        return start
    end = f"{int(match.group(3)):02d}:{match.group(4)}"
    if end <= start:
        raise HTTPException(422, "结束时间要晚于开始时间")
    return f"{start}-{end}"


def _normalize_time_text(raw: str) -> str:
    """解析已有任务行里的时间：宽容版——认不出来就返回空（调用方把原文留在标题里）。"""
    match = TIME_VALUE_RE.match(re.sub(r"\s+", "", raw or ""))
    if match is None:
        return ""
    start = f"{int(match.group(1)):02d}:{match.group(2)}"
    if match.group(3) is None:
        return start
    end = f"{int(match.group(3)):02d}:{match.group(4)}"
    return f"{start}-{end}" if end > start else start


def _clean_priority(value: str) -> str:
    text = (value or "").strip().lower() or "none"
    if text not in PRIORITIES:
        raise HTTPException(422, "优先级只能是 high/medium/low/none")
    return text


def _clean_tags(values: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for raw in values or []:
        name = re.sub(r"\s+", "", str(raw or "")).lstrip("#").strip()
        if not name:
            continue
        if len(name) > MAX_TAG_NAME or any(char in name for char in "#\n"):
            raise HTTPException(422, f"标签名最多 {MAX_TAG_NAME} 个字，且不能带 #")
        if name not in cleaned:
            cleaned.append(name)
    return cleaned[:MAX_TAGS]


def _clean_title(value: str) -> str:
    title = re.sub(r"\s+", " ", str(value or "").strip())
    if not title or len(title) > MAX_TITLE or any(ord(char) < 32 for char in title):
        raise HTTPException(422, f"任务标题需要 1–{MAX_TITLE} 个字符，且不能包含换行")
    return title


def _load_tasks(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False, "path": path, "raw": None, "revision": "",
                "newline": "\n", "lines": [], "body_start": 0}
    raw = path.read_bytes()
    if len(raw) > MAX_NOTE_BYTES:
        raise HTTPException(413, "任务文件过大，已停止编辑")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(409, "任务文件不是 UTF-8 编码，暂不能编辑") from exc
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    body_start = 0
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                body_start = index + 1
                break
    return {"exists": True, "path": path, "raw": raw, "revision": _revision(raw),
            "newline": newline, "lines": lines, "body_start": body_start}


def _front_matter(loaded: dict[str, Any]) -> dict[str, Any]:
    if not loaded["exists"] or loaded["body_start"] == 0:
        return {}
    block = "\n".join(loaded["lines"][1:loaded["body_start"] - 1])
    try:
        data = yaml.safe_load(block) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def defined_lists(loaded: dict[str, Any]) -> list[dict[str, Any]]:
    """front-matter 里的清单定义（缺字段补默认值；收集箱永远在内置兜底里）。"""
    raw = _front_matter(loaded).get("清单") or []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = re.sub(r"\s+", " ", str(item.get("名称") or "").strip())
            if not name or name in seen:
                continue
            seen.add(name)
            entries.append({
                "name": name,
                "color": str(item.get("颜色") or "").strip(),
                "folder": re.sub(r"\s+", " ", str(item.get("文件夹") or "").strip()),
                "pinned": item.get("置顶") is True,
            })
    if INBOX not in seen:
        entries.insert(0, {"name": INBOX, "color": "#3b82f6", "folder": "", "pinned": False})
    return entries


def defined_tags(loaded: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _front_matter(loaded).get("标签") or []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = re.sub(r"\s+", "", str(item.get("名称") or "").strip()).lstrip("#")
                color, pinned = str(item.get("颜色") or "").strip(), item.get("置顶") is True
            else:
                name = re.sub(r"\s+", "", str(item or "").strip()).lstrip("#")
                color, pinned = "", False
            if not name or name in seen:
                continue
            seen.add(name)
            entries.append({"name": name, "color": color, "pinned": pinned})
    return entries


def _extract_tags(text: str) -> tuple[str, list[str]]:
    """剥出 `#标签`；纯数字的 `#1` 更像标题（“给 #1 issue 加”）——留着不动。"""
    tags: list[str] = []
    pieces: list[str] = []
    last = 0
    for match in TAG_RE.finditer(text):
        name = match.group(1)
        if name.isdigit():
            continue
        pieces.append(text[last:match.start()])
        last = match.end()
        if name not in tags:
            tags.append(name)
    pieces.append(text[last:])
    return "".join(pieces), tags


def split_task_text(text: str) -> tuple[str, dict[str, Any]]:
    """把一行的标题与标记拆开；不认识的符号留在标题里（绝不吞用户写的字）。"""
    rest = text
    meta: dict[str, Any] = {"date": "", "time": "", "done_on": "", "created": "", "priority": "none",
                            "tags": [], "pinned": False}
    for pattern, key in ((DATE_RE, "date"), (DONE_RE, "done_on"), (CREATED_RE, "created")):
        match = pattern.search(rest)
        if match:
            meta[key] = match.group(1)
            rest = pattern.sub(" ", rest, count=1)
    time_match = TIME_RE.search(rest)
    if time_match:
        normalized = _normalize_time_text(time_match.group(1))
        if normalized:  # 认不出来的（如 ⏰ 25:99）留在标题里，不吞
            meta["time"] = normalized
            rest = TIME_RE.sub(" ", rest, count=1)
    if PIN_RE.search(rest):
        meta["pinned"] = True
        rest = PIN_RE.sub(" ", rest, count=1)
    for mark, name in MARK_PRIORITY.items():
        if re.search(rf"(?:^|\s){re.escape(mark)}(?=\s|$)", rest):
            meta["priority"] = name
            rest = re.sub(rf"(?:^|\s){re.escape(mark)}(?=\s|$)", " ", rest, count=1)
            break
    rest, tags = _extract_tags(rest)
    meta["tags"] = tags
    return re.sub(r"\s+", " ", rest).strip(), meta


def format_task_line(title: str, *, done: bool, done_on: str = "", date: str = "", time: str = "",
                     priority: str = "none", tags: list[str] | None = None,
                     pinned: bool = False, created: str = "") -> str:
    """一行任务的规范写法：标题在前，标记按固定顺序跟在后面（空值的标记不写）。"""
    parts = [f"- [{'x' if done else ' '}] {title}"]
    if pinned:
        parts.append("📌")
    if PRIORITY_MARK.get(priority):
        parts.append(PRIORITY_MARK[priority])
    if date:
        parts.append(f"📅 {date}")
    if time:
        parts.append(f"⏰ {time}")
    parts.extend(f"#{name}" for name in tags or [])
    if created:
        parts.append(f"➕ {created}")
    if done and done_on:
        parts.append(f"✅ {done_on}")
    return " ".join(parts)


def parse_tasks(loaded: dict[str, Any]) -> list[dict[str, Any]]:
    """整份文件的复选框 → 任务字典（清单名取自所在 `## 小节`）。"""
    if not loaded["exists"]:
        return []
    body = "\n".join(loaded["lines"][loaded["body_start"]:])
    items, _warnings = parse_checkboxes(body)
    tasks: list[dict[str, Any]] = []
    for item in items:
        raw = item["description"]
        without_box = CHECKBOX_PREFIX.sub("", raw)
        title, meta = split_task_text(without_box)
        if not title:
            continue
        heading = str(item.get("heading") or "")
        section = heading.split(" / ")[-1] if heading else ""
        tasks.append({
            "key": item["key"],
            "line": loaded["body_start"] + item["line"],
            "list_name": section or INBOX,
            "title": title,
            "done": bool(item["checked"]),
            "done_on": meta["done_on"],
            "date": meta["date"],
            "time": meta["time"],
            "priority": meta["priority"],
            "tags": meta["tags"],
            "pinned": meta["pinned"],
            "created": meta["created"],
            "raw": raw,
        })
    return tasks


def read_tasks(vault_dir: Path | str) -> dict[str, Any]:
    """任务板面的全部数据：清单/标签定义 + 任务（过滤、分组、排序交给前端纯函数做）。

    标签 = front-matter 里的定义（有颜色/置顶）+ 任务行里用到的（还没定义过）——
    这样第 1 轮里“在添加栏打一个新标签”就能立刻出现在侧栏，不用先写定义。
    """
    path = safe_note_path(Path(vault_dir), TASKS_REL.as_posix())
    loaded = _load_tasks(path)
    tasks = parse_tasks(loaded)
    listed = defined_tags(loaded)
    known = {entry["name"] for entry in listed}
    used = sorted({name for task in tasks for name in task["tags"] if name not in known})
    raw_folders = _front_matter(loaded).get("文件夹") or []
    folders: list[str] = []
    if isinstance(raw_folders, list):
        for item in raw_folders:
            name = re.sub(r"\s+", " ", str(item or "").strip())
            if name and name not in folders:
                folders.append(name)
    # 清单上写了、但还没登记进 文件夹 列表的（手写文件时会有）：补进来，别让它们凭空消失。
    for entry in defined_lists(loaded):
        if entry["folder"] and entry["folder"] not in folders:
            folders.append(entry["folder"])
    return {
        "exists": loaded["exists"],
        "revision": loaded["revision"],
        "path": TASKS_REL.as_posix(),
        "lists": defined_lists(loaded),
        "folders": folders,
        "tags": listed + [{"name": name, "color": "", "pinned": False} for name in used],
        "tasks": tasks,
    }


def _locate(tasks: list[dict[str, Any]], key: str) -> dict[str, Any]:
    for task in tasks:
        if task["key"] == key:
            return task
    raise HTTPException(409, "这条任务已不在文件里（可能已在 Obsidian 中被改动），请刷新后重试")


def _assert_line(loaded: dict[str, Any], task: dict[str, Any]) -> None:
    lines = loaded["lines"]
    index = task["line"] - 1
    if index < 0 or index >= len(lines) or lines[index] != task["raw"]:
        raise HTTPException(409, "文件内容已变化，请刷新后重试")


def _check_revision(loaded: dict[str, Any], expected: str) -> None:
    expected = expected.strip()
    if loaded["exists"] and expected and expected != loaded["revision"]:
        raise HTTPException(409, "任务文件已在别处修改：请刷新后重试")
    if not loaded["exists"] and expected:
        raise HTTPException(409, "任务文件已被删除：请刷新后重试")


def _list_insert_point(lines: list[str], name: str) -> int:
    """新任务插到清单小节的**最上面**（照参考图：新加的排最前）；没有这个小节就在末尾补一个。"""
    heading_index = _heading_line(lines, 0, name)
    if heading_index < 0:
        while lines and not lines[-1].strip():
            lines.pop()
        lines.extend(["", f"## {name}", ""])
        heading_index = len(lines) - 2
    return heading_index + 1


def _known_lists(loaded: dict[str, Any]) -> set[str]:
    return {entry["name"] for entry in defined_lists(loaded)}


def _require_list(loaded: dict[str, Any], name: str) -> str:
    target = name.strip() or INBOX
    if target not in _known_lists(loaded):
        raise HTTPException(422, f"清单“{target}”不存在；清单的增删改在下一轮开放")
    return target


def create_task(vault_dir: Path | str, payload: TaskCreate, database: Database, *,
                read_only: bool = False) -> dict[str, Any]:
    try:
        ensure_vault_writable(read_only, "新增任务")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    title = _clean_title(payload.title)
    path = safe_note_path(Path(vault_dir), TASKS_REL.as_posix())
    loaded = _load_tasks(path)
    _check_revision(loaded, payload.revision)
    if loaded["exists"]:
        lines = list(loaded["lines"])
    else:
        lines = SKELETON.replace("\n", loaded["newline"]).split(loaded["newline"])
        loaded = {**loaded, "lines": lines}
    target = _require_list(loaded, payload.list_name)
    insert_at = _list_insert_point(lines, target)
    line = format_task_line(
        title, done=False, date=_clean_date(payload.date, "日期"), time=_clean_time(payload.time),
        priority=_clean_priority(payload.priority), tags=_clean_tags(payload.tags),
        created=_today())
    lines.insert(insert_at, line)
    raw = _guarded_write(loaded, lines)
    _audit(database, "task_added", {"list": target, "title": title})
    return {"revision": _revision(raw), "task": {"title": title, "list_name": target, "line": insert_at + 1}}


def update_task(vault_dir: Path | str, payload: TaskUpdate, database: Database, *,
                read_only: bool = False) -> dict[str, Any]:
    """改一条任务：标题/清单/日期/优先级/标签/置顶/完成——只传要改的字段。"""
    try:
        ensure_vault_writable(read_only, "修改任务")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    path = safe_note_path(Path(vault_dir), TASKS_REL.as_posix())
    loaded = _load_tasks(path)
    if not loaded["exists"]:
        raise HTTPException(409, "任务文件还不存在，请刷新后重试")
    _check_revision(loaded, payload.revision)
    tasks = parse_tasks(loaded)
    task = _locate(tasks, payload.key)
    _assert_line(loaded, task)

    title = _clean_title(payload.title) if payload.title else task["title"]
    date = _clean_date(payload.date, "日期") if "date" in payload.model_fields_set else task["date"]
    time = _clean_time(payload.time) if "time" in payload.model_fields_set else task["time"]
    priority = _clean_priority(payload.priority) if payload.priority else task["priority"]
    tags = _clean_tags(payload.tags) if payload.tags is not None else task["tags"]
    pinned = payload.pinned if payload.pinned is not None else task["pinned"]
    done = payload.done if payload.done is not None else task["done"]
    done_on = task["done_on"]
    if done and not done_on:
        done_on = _clean_date(payload.day, "完成日期") or _today()
    if not done:
        done_on = ""
    target = _require_list(loaded, payload.list_name or task["list_name"])

    lines = list(loaded["lines"])
    new_line = format_task_line(title, done=done, done_on=done_on, date=date, time=time,
                                priority=priority, tags=tags, pinned=pinned, created=task["created"])
    lines[task["line"] - 1] = new_line
    moved = target != task["list_name"]
    if moved:
        del lines[task["line"] - 1]
        lines.insert(_list_insert_point(lines, target), new_line)
    raw = _guarded_write(loaded, lines)
    _audit(database, "task_updated", {"list": target, "title": title, "moved": moved})
    return {"revision": _revision(raw), "task": {"title": title, "list_name": target, "done": done}}


def delete_task(vault_dir: Path | str, payload: TaskKeyPayload, database: Database, *,
                read_only: bool = False) -> dict[str, Any]:
    try:
        ensure_vault_writable(read_only, "删除任务")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    path = safe_note_path(Path(vault_dir), TASKS_REL.as_posix())
    loaded = _load_tasks(path)
    if not loaded["exists"]:
        raise HTTPException(409, "任务文件还不存在，请刷新后重试")
    _check_revision(loaded, payload.revision)
    task = _locate(parse_tasks(loaded), payload.key)
    _assert_line(loaded, task)
    lines = list(loaded["lines"])
    del lines[task["line"] - 1]
    raw = _guarded_write(loaded, lines)
    _audit(database, "task_deleted", {"list": task["list_name"], "title": task["title"]})
    return {"revision": _revision(raw), "deleted": True}


def duplicate_task(vault_dir: Path | str, payload: TaskDuplicate, database: Database, *,
                   read_only: bool = False) -> dict[str, Any]:
    """创建副本：同一清单里复制一条（未完成状态、今天为创建日，标题与标签照抄）。"""
    try:
        ensure_vault_writable(read_only, "创建任务副本")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    path = safe_note_path(Path(vault_dir), TASKS_REL.as_posix())
    loaded = _load_tasks(path)
    if not loaded["exists"]:
        raise HTTPException(409, "任务文件还不存在，请刷新后重试")
    _check_revision(loaded, payload.revision)
    task = _locate(parse_tasks(loaded), payload.key)
    _assert_line(loaded, task)
    target = _require_list(loaded, payload.list_name or task["list_name"])
    lines = list(loaded["lines"])
    insert_at = _list_insert_point(lines, target)
    line = format_task_line(task["title"], done=False, date=task["date"], time=task["time"],
                            priority=task["priority"], tags=task["tags"],
                            pinned=task["pinned"], created=_today())
    lines.insert(insert_at, line)
    raw = _guarded_write(loaded, lines)
    _audit(database, "task_duplicated", {"list": target, "title": task["title"]})
    return {"revision": _revision(raw), "task": {"title": task["title"], "list_name": target}}


# ===== 清单 / 文件夹 / 标签（第 2 轮）：结构定义住在 front matter，产品托管这一段 2026-10-08 =====


class ListCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_LIST)
    color: str = Field(default="", max_length=9)
    folder: str = Field(default="", max_length=MAX_LIST)
    revision: str = Field(default="", max_length=64)


class ListUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_LIST)
    new_name: str = Field(default="", max_length=MAX_LIST)
    color: str | None = Field(default=None, max_length=9)
    folder: str | None = Field(default=None, max_length=MAX_LIST)
    pinned: bool | None = None
    revision: str = Field(default="", max_length=64)


class ListDuplicate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_LIST)
    # 照滴答的三个范围：只复制未完成 / 全部保留完成状态 / 全部不保留完成状态
    scope: str = "undone"
    revision: str = Field(default="", max_length=64)


class NamedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_LIST)
    revision: str = Field(default="", max_length=64)


class FolderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_LIST)
    new_name: str = Field(default="", max_length=MAX_LIST)
    revision: str = Field(default="", max_length=64)


class TagCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_TAG_NAME)
    color: str = Field(default="", max_length=9)
    revision: str = Field(default="", max_length=64)


class TagUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_TAG_NAME)
    new_name: str = Field(default="", max_length=MAX_TAG_NAME)
    color: str | None = Field(default=None, max_length=9)
    pinned: bool | None = None
    revision: str = Field(default="", max_length=64)


def _clean_color(value: str | None) -> str:
    text = (value or "").strip()
    if text and not re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        raise HTTPException(422, "颜色要写成 #RRGGBB 或留空")
    return text.lower()


def _clean_name(value: str, *, what: str = "名称", limit: int = MAX_LIST) -> str:
    name = re.sub(r"\s+", " ", str(value or "").strip())
    if not name or len(name) > limit or any(char in name for char in "#|\n"):
        raise HTTPException(422, f"{what}需要 1–{limit} 个字，且不能包含 # 或竖线")
    return name


def _entries(metadata: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """把 清单/标签 的条目统一成字典形态（也容忍手写的字符串写法）。"""
    raw = metadata.get(key) or []
    entries: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                entries.append(dict(item))
            elif str(item or "").strip():
                entries.append({"名称": re.sub(r"\s+", " ", str(item).strip())})
    return entries


def _find_entry(entries: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((entry for entry in entries
                 if str(entry.get("名称") or "").strip() == name), None)


def _replace_front_matter(loaded: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    """重写 front matter（结构定义由产品托管），**正文逐字保留**。"""
    body = list(loaded["lines"][loaded["body_start"]:]) if loaded["exists"] else []
    block = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip("\n").split("\n")
    return ["---", *block, "---", *body]


def _base_metadata(loaded: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(_front_matter(loaded)) if loaded["exists"] else {}
    metadata.setdefault("type", "tasks")
    return metadata


def _ensure_section(lines: list[str], name: str) -> int:
    """确保有 `## name` 小节，返回它的标题行下标。"""
    heading = _heading_line(lines, 0, name)
    if heading >= 0:
        return heading
    while lines and not lines[-1].strip():
        lines.pop()
    lines.extend(["", f"## {name}", ""])
    return len(lines) - 2


def _remove_section(lines: list[str], name: str) -> bool:
    heading = _heading_line(lines, 0, name)
    if heading < 0:
        return False
    _content_start, insert_at = _section_bounds(lines, 0, heading)
    del lines[heading:insert_at]
    return True


def _unique_name(existing: set[str], base: str) -> str:
    if base not in existing:
        return base
    index = 2
    while f"{base} {index}" in existing:
        index += 1
    return f"{base} {index}"


def _rewrite_tag(lines: list[str], source: str, target: str | None) -> int:
    """把任务行里的 `#source` 换成 `#target`（target=None 表示删掉）；只动复选框行。"""
    token = re.compile(rf"(?<=\s)#{re.escape(source)}(?=\s|$)")
    changed = 0
    for index, line in enumerate(lines):
        if not CHECKBOX_PREFIX.match(line):
            continue
        if token.search(line):
            lines[index] = token.sub(f"#{target}" if target else "", line)
            changed += 1
    return changed


def _load_for_structure(vault_dir: Path | str, revision: str, action: str,
                        read_only: bool) -> tuple[Path, dict[str, Any]]:
    try:
        ensure_vault_writable(read_only, action)
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    path = safe_note_path(Path(vault_dir), TASKS_REL.as_posix())
    loaded = _load_tasks(path)
    _check_revision(loaded, revision)
    return path, loaded


def create_list(vault_dir: Path | str, payload: ListCreate, database: Database, *,
                read_only: bool = False) -> dict[str, Any]:
    path, loaded = _load_for_structure(vault_dir, payload.revision, "新增清单", read_only)
    name = _clean_name(payload.name, what="清单名")
    folder = _clean_name(payload.folder, what="文件夹名") if payload.folder.strip() else ""
    metadata = _base_metadata(loaded)
    entries = _entries(metadata, "清单")
    if _find_entry(entries, name):
        raise HTTPException(422, f"已经有叫“{name}”的清单了")
    entry = {"名称": name, "颜色": _clean_color(payload.color) or "#3b82f6"}
    if folder:
        if folder not in [str(item) for item in (metadata.get("文件夹") or [])]:
            metadata.setdefault("文件夹", []).append(folder)
        entry["文件夹"] = folder
    entries.append(entry)
    metadata["清单"] = entries
    lines = _replace_front_matter(loaded, metadata)
    _ensure_section(lines, name)
    raw = _guarded_write(loaded, lines)
    _audit(database, "list_created", {"name": name, "folder": folder})
    return {"revision": _revision(raw), "list": {"name": name, "folder": folder}}


def update_list(vault_dir: Path | str, payload: ListUpdate, database: Database, *,
                read_only: bool = False) -> dict[str, Any]:
    path, loaded = _load_for_structure(vault_dir, payload.revision, "修改清单", read_only)
    name = _clean_name(payload.name, what="清单名")
    metadata = _base_metadata(loaded)
    entries = _entries(metadata, "清单")
    entry = _find_entry(entries, name)
    if entry is None:
        raise HTTPException(422, f"没有叫“{name}”的清单")
    lines = list(loaded["lines"]) if loaded["exists"] else []
    new_name = _clean_name(payload.new_name, what="清单名") if payload.new_name.strip() else ""
    if new_name and new_name != name:
        if _find_entry(entries, new_name):
            raise HTTPException(422, f"已经有叫“{new_name}”的清单了")
        heading = _heading_line(lines, 0, name)
        if heading >= 0:
            lines[heading] = f"## {new_name}"
        entry["名称"] = new_name
        name = new_name
    if payload.color is not None:
        color = _clean_color(payload.color)
        if color:
            entry["颜色"] = color
        else:
            entry.pop("颜色", None)
    if payload.folder is not None:
        folder = _clean_name(payload.folder, what="文件夹名") if payload.folder.strip() else ""
        if folder:
            if folder not in [str(item) for item in (metadata.get("文件夹") or [])]:
                metadata.setdefault("文件夹", []).append(folder)
            entry["文件夹"] = folder
        else:
            entry.pop("文件夹", None)
    if payload.pinned is not None:
        if payload.pinned:
            entry["置顶"] = True
        else:
            entry.pop("置顶", None)
    metadata["清单"] = entries
    # 正文可能改过一行（重命名时的 `## 标题`），所以直接把改过的 lines 写回。
    raw = _write_lines(loaded, lines or (loaded["lines"] if loaded["exists"] else []), metadata)
    _audit(database, "list_updated", {"name": name})
    return {"revision": _revision(raw), "list": {"name": name}}


def duplicate_list(vault_dir: Path | str, payload: ListDuplicate, database: Database, *,
                   read_only: bool = False) -> dict[str, Any]:
    """复制清单（照滴答）：只复制未完成 / 全部保留完成状态 / 全部不保留完成状态。"""
    path, loaded = _load_for_structure(vault_dir, payload.revision, "复制清单", read_only)
    if payload.scope not in ("undone", "all_keep", "all_reset"):
        raise HTTPException(422, "复制范围只能是 undone / all_keep / all_reset")
    name = _clean_name(payload.name, what="清单名")
    metadata = _base_metadata(loaded)
    entries = _entries(metadata, "清单")
    source = _find_entry(entries, name)
    if source is None:
        raise HTTPException(422, f"没有叫“{name}”的清单")
    copy_name = _unique_name({str(entry.get("名称")) for entry in entries}, f"{name} 副本")
    tasks = [task for task in parse_tasks(loaded) if task["list_name"] == name]
    if payload.scope == "undone":
        tasks = [task for task in tasks if not task["done"]]
    copied = [
        format_task_line(task["title"], done=task["done"] and payload.scope == "all_keep",
                         done_on=task["done_on"] if payload.scope == "all_keep" else "",
                         date=task["date"], time=task["time"], priority=task["priority"],
                         tags=task["tags"], pinned=task["pinned"], created=_today())
        for task in tasks
    ]
    lines = list(loaded["lines"]) if loaded["exists"] else []
    if _heading_line(lines, 0, name) < 0:
        lines = _replace_front_matter(loaded, metadata)
        _ensure_section(lines, name)
    heading = _heading_line(lines, 0, name)
    block = [f"## {copy_name}", ""] if not copied else [f"## {copy_name}", *copied, ""]
    # 副本插在原清单**前面**（照滴答：正文与侧栏顺序一致）
    lines[heading:heading] = block
    # 定义也插在原清单前面（侧栏顺序与正文一致：副本在上，照滴答）
    entries.insert(entries.index(source), {"名称": copy_name, "颜色": source.get("颜色", "#3b82f6"),
                                           **({"文件夹": source["文件夹"]} if source.get("文件夹") else {})})
    metadata["清单"] = entries
    # 注意：正文里已经插好了副本小节（lines），必须连正文一起写回，不能只重建 front matter。
    raw = _write_lines(loaded, lines, metadata)
    _audit(database, "list_duplicated", {"name": name, "copy": copy_name, "scope": payload.scope,
                                         "tasks": len(copied)})
    return {"revision": _revision(raw), "list": {"name": copy_name, "tasks": len(copied)}}


def delete_list(vault_dir: Path | str, payload: NamedPayload, database: Database, *,
                read_only: bool = False) -> dict[str, Any]:
    path, loaded = _load_for_structure(vault_dir, payload.revision, "删除清单", read_only)
    name = _clean_name(payload.name, what="清单名")
    if name == INBOX:
        raise HTTPException(422, "收集箱不能删除")
    metadata = _base_metadata(loaded)
    entries = _entries(metadata, "清单")
    entry = _find_entry(entries, name)
    if entry is None:
        raise HTTPException(422, f"没有叫“{name}”的清单")
    removed = sum(1 for task in parse_tasks(loaded) if task["list_name"] == name)
    entries.remove(entry)
    metadata["清单"] = entries
    lines = _replace_front_matter(loaded, metadata)
    _remove_section(lines, name)
    raw = _guarded_write(loaded, lines)
    _audit(database, "list_deleted", {"name": name, "tasks": removed})
    return {"revision": _revision(raw), "deleted": name, "tasks": removed}


def create_folder(vault_dir: Path | str, payload: NamedPayload, database: Database, *,
                  read_only: bool = False) -> dict[str, Any]:
    path, loaded = _load_for_structure(vault_dir, payload.revision, "新增文件夹", read_only)
    name = _clean_name(payload.name, what="文件夹名")
    metadata = _base_metadata(loaded)
    folders = [str(item) for item in (metadata.get("文件夹") or [])]
    if name in folders:
        raise HTTPException(422, f"已经有叫“{name}”的文件夹了")
    folders.append(name)
    metadata["文件夹"] = folders
    raw = _guarded_write(loaded, _replace_front_matter(loaded, metadata))
    _audit(database, "folder_created", {"name": name})
    return {"revision": _revision(raw), "folder": name}


def update_folder(vault_dir: Path | str, payload: FolderUpdate, database: Database, *,
                  read_only: bool = False) -> dict[str, Any]:
    path, loaded = _load_for_structure(vault_dir, payload.revision, "重命名文件夹", read_only)
    name = _clean_name(payload.name, what="文件夹名")
    new_name = _clean_name(payload.new_name, what="文件夹名")
    metadata = _base_metadata(loaded)
    folders = [str(item) for item in (metadata.get("文件夹") or [])]
    if name not in folders:
        raise HTTPException(422, f"没有叫“{name}”的文件夹")
    if new_name != name and new_name in folders:
        raise HTTPException(422, f"已经有叫“{new_name}”的文件夹了")
    folders[folders.index(name)] = new_name
    metadata["文件夹"] = folders
    entries = _entries(metadata, "清单")
    for entry in entries:
        if str(entry.get("文件夹") or "") == name:
            entry["文件夹"] = new_name
    metadata["清单"] = entries
    raw = _guarded_write(loaded, _replace_front_matter(loaded, metadata))
    _audit(database, "folder_renamed", {"name": name, "new_name": new_name})
    return {"revision": _revision(raw), "folder": new_name}


def delete_folder(vault_dir: Path | str, payload: NamedPayload, database: Database, *,
                  read_only: bool = False) -> dict[str, Any]:
    """解散文件夹：里面的清单回到根级（不连坐删清单）。"""
    path, loaded = _load_for_structure(vault_dir, payload.revision, "删除文件夹", read_only)
    name = _clean_name(payload.name, what="文件夹名")
    metadata = _base_metadata(loaded)
    folders = [str(item) for item in (metadata.get("文件夹") or [])]
    if name not in folders:
        raise HTTPException(422, f"没有叫“{name}”的文件夹")
    folders.remove(name)
    metadata["文件夹"] = folders
    entries = _entries(metadata, "清单")
    for entry in entries:
        if str(entry.get("文件夹") or "") == name:
            entry.pop("文件夹", None)
    metadata["清单"] = entries
    raw = _guarded_write(loaded, _replace_front_matter(loaded, metadata))
    _audit(database, "folder_deleted", {"name": name})
    return {"revision": _revision(raw), "deleted": name}


def create_tag(vault_dir: Path | str, payload: TagCreate, database: Database, *,
               read_only: bool = False) -> dict[str, Any]:
    path, loaded = _load_for_structure(vault_dir, payload.revision, "新增标签", read_only)
    name = _clean_name(payload.name, what="标签名", limit=MAX_TAG_NAME)
    metadata = _base_metadata(loaded)
    entries = _entries(metadata, "标签")
    if _find_entry(entries, name):
        raise HTTPException(422, f"已经有叫“{name}”的标签了")
    entry = {"名称": name}
    color = _clean_color(payload.color)
    if color:
        entry["颜色"] = color
    entries.append(entry)
    metadata["标签"] = entries
    raw = _guarded_write(loaded, _replace_front_matter(loaded, metadata))
    _audit(database, "tag_created", {"name": name})
    return {"revision": _revision(raw), "tag": {"name": name}}


def update_tag(vault_dir: Path | str, payload: TagUpdate, database: Database, *,
               read_only: bool = False) -> dict[str, Any]:
    """改标签：重命名会同步改写所有任务行里的 `#旧名`；颜色/置顶写进定义。"""
    path, loaded = _load_for_structure(vault_dir, payload.revision, "修改标签", read_only)
    name = _clean_name(payload.name, what="标签名", limit=MAX_TAG_NAME)
    metadata = _base_metadata(loaded)
    entries = _entries(metadata, "标签")
    entry = _find_entry(entries, name)
    if entry is None:
        entry = {"名称": name}
        entries.append(entry)
    new_name = _clean_name(payload.new_name, what="标签名", limit=MAX_TAG_NAME) if payload.new_name.strip() else ""
    lines = list(loaded["lines"]) if loaded["exists"] else []
    renamed = 0
    if new_name and new_name != name:
        if _find_entry(entries, new_name):
            raise HTTPException(422, f"已经有叫“{new_name}”的标签了")
        renamed = _rewrite_tag(lines, name, new_name)
        entry["名称"] = new_name
        name = new_name
    if payload.color is not None:
        color = _clean_color(payload.color)
        if color:
            entry["颜色"] = color
        else:
            entry.pop("颜色", None)
    if payload.pinned is not None:
        if payload.pinned:
            entry["置顶"] = True
        else:
            entry.pop("置顶", None)
    metadata["标签"] = entries
    # 重命名时正文里的 `#旧名` 已经在 lines 里改好了；没重命名就是原样正文。一次原子替换写回。
    raw = _write_lines(loaded, lines, metadata)
    _audit(database, "tag_updated", {"name": name, "renamed_tasks": renamed})
    return {"revision": _revision(raw), "tag": {"name": name, "renamed_tasks": renamed}}


def _write_lines(loaded: dict[str, Any], body_lines: list[str], metadata: dict[str, Any]) -> bytes:
    """front matter 用 metadata 重写 + 正文用改过的 body_lines 写回（同一次原子替换）。"""
    body = list(body_lines[loaded["body_start"]:]) if loaded["exists"] else list(body_lines)
    block = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip("\n").split("\n")
    return _guarded_write(loaded, ["---", *block, "---", *body])


def delete_tag(vault_dir: Path | str, payload: NamedPayload, database: Database, *,
               read_only: bool = False) -> dict[str, Any]:
    """删标签：定义移除，并且把所有任务行里的 `#标签` 去掉（照滴答）。"""
    path, loaded = _load_for_structure(vault_dir, payload.revision, "删除标签", read_only)
    name = _clean_name(payload.name, what="标签名", limit=MAX_TAG_NAME)
    metadata = _base_metadata(loaded)
    entries = _entries(metadata, "标签")
    entry = _find_entry(entries, name)
    if entry is not None:
        entries.remove(entry)
    metadata["标签"] = entries
    lines = list(loaded["lines"]) if loaded["exists"] else []
    stripped = _rewrite_tag(lines, name, None)
    raw = _write_lines(loaded, lines, metadata)
    _audit(database, "tag_deleted", {"name": name, "tasks": stripped})
    return {"revision": _revision(raw), "deleted": name, "tasks": stripped}


class TaskBulk(BaseModel):
    """批量操作（2026-10-08）：一次写回处理多条，避免 N 次改文件、N 次版本号抖动。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    keys: list[TaskKey] = Field(min_length=1, max_length=200)
    # delete / move / tag / date / priority / pin / duplicate
    action: str = Field(max_length=10)
    list_name: str = Field(default="", max_length=MAX_LIST)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    date: str = Field(default="", max_length=10)      # date：改到哪一天（空 = 清除日期）
    priority: str = Field(default="", max_length=10)  # priority：high/medium/low/none
    pinned: bool = True                               # pin：true 置顶 / false 取消置顶
    revision: str = Field(default="", max_length=64)


def bulk_tasks(vault_dir: Path | str, payload: TaskBulk, database: Database, *,
               read_only: bool = False) -> dict[str, Any]:
    """批量删除 / 移动 / 打标签：定位到的行一次改完，再整份写回（同一套原子替换协议）。"""
    label = {"delete": "批量删除任务", "move": "批量移动任务", "tag": "批量打标签",
             "date": "批量改日期", "priority": "批量改优先级", "pin": "批量置顶/取消置顶",
             "duplicate": "批量创建副本"}.get(payload.action)
    if label is None:
        raise HTTPException(422, "批量动作只能是 delete / move / tag / date / priority / pin / duplicate")
    try:
        ensure_vault_writable(read_only, label)
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    path = safe_note_path(Path(vault_dir), TASKS_REL.as_posix())
    loaded = _load_tasks(path)
    if not loaded["exists"]:
        raise HTTPException(409, "任务文件还不存在，请刷新后重试")
    _check_revision(loaded, payload.revision)
    tasks = parse_tasks(loaded)
    picked: list[dict[str, Any]] = []
    for key in payload.keys:
        task = next((item for item in tasks if item["key"] == key), None)
        if task is None:
            raise HTTPException(409, "选中的任务里有已经不在文件里的（可能已在 Obsidian 中改过），请刷新后重试")
        picked.append(task)
    # 从下往上改，行号才不会互相挤
    picked.sort(key=lambda item: item["line"], reverse=True)

    def rewritten(task, **changes):
        fields = {"date": task["date"], "time": task["time"], "priority": task["priority"],
                  "tags": task["tags"], "pinned": task["pinned"]}
        fields.update(changes)
        return format_task_line(task["title"], done=task["done"], done_on=task["done_on"],
                                created=task["created"], **fields)

    lines = list(loaded["lines"])
    if payload.action == "delete":
        for task in picked:
            del lines[task["line"] - 1]
    elif payload.action == "move":
        target = _require_list(loaded, payload.list_name)
        for task in picked:
            line = rewritten(task)
            del lines[task["line"] - 1]
            lines.insert(_list_insert_point(lines, target), line)
    elif payload.action == "tag":
        tags = _clean_tags(payload.tags)
        if not tags:
            raise HTTPException(422, "要打的标签不能为空")
        for task in picked:
            merged = list(dict.fromkeys([*task["tags"], *tags]))
            lines[task["line"] - 1] = rewritten(task, tags=merged)
    elif payload.action == "date":
        date = _clean_date(payload.date, "日期")   # 空串 = 清除日期
        for task in picked:
            lines[task["line"] - 1] = rewritten(task, date=date)
    elif payload.action == "priority":
        if not payload.priority:
            raise HTTPException(422, "批量改优先级要给出优先级")
        priority = _clean_priority(payload.priority)
        for task in picked:
            lines[task["line"] - 1] = rewritten(task, priority=priority)
    elif payload.action == "pin":
        for task in picked:
            lines[task["line"] - 1] = rewritten(task, pinned=payload.pinned)
    else:  # duplicate：不改原有的行，只在各自清单最上面插一条副本
        inserts: list[tuple[int, str]] = []
        for task in picked:
            copy = format_task_line(task["title"], done=False, date=task["date"], time=task["time"],
                                    priority=task["priority"], tags=task["tags"],
                                    pinned=task["pinned"], created=_today())
            inserts.append((_list_insert_point(lines, task["list_name"]), copy))
        for position, line in sorted(inserts, key=lambda item: item[0], reverse=True):
            lines.insert(position, line)

    raw = _guarded_write(loaded, lines)
    _audit(database, f"tasks_bulk_{payload.action}",
           {"count": len(picked), "list": payload.list_name, "tags": payload.tags})
    return {"revision": _revision(raw), "count": len(picked), "action": payload.action}


def create_tasks_router(vault_dir: Path | str, database: Database, *,
                        read_only: bool = False, prefix: str = "/api") -> APIRouter:
    # 前缀用 /api/time：`/api/tasks` 已经被“数字分身 → 目标与任务”那套 SQLite 任务占用了。
    router = APIRouter(prefix=f"{prefix}/time", tags=["time-tasks"])

    @router.get("/tasks")
    def tasks_read():
        return read_tasks(vault_dir)

    @router.post("/tasks")
    def tasks_create(payload: TaskCreate):
        return create_task(vault_dir, payload, database, read_only=read_only)

    @router.post("/tasks/update")
    def tasks_update(payload: TaskUpdate):
        return update_task(vault_dir, payload, database, read_only=read_only)

    @router.post("/tasks/delete")
    def tasks_delete(payload: TaskKeyPayload):
        return delete_task(vault_dir, payload, database, read_only=read_only)

    @router.post("/tasks/duplicate")
    def tasks_duplicate(payload: TaskDuplicate):
        return duplicate_task(vault_dir, payload, database, read_only=read_only)

    @router.post("/tasks/bulk")
    def tasks_bulk(payload: TaskBulk):
        return bulk_tasks(vault_dir, payload, database, read_only=read_only)

    # ---- 清单 / 文件夹 / 标签（第 2 轮）----
    @router.post("/lists")
    def lists_create(payload: ListCreate):
        return create_list(vault_dir, payload, database, read_only=read_only)

    @router.post("/lists/update")
    def lists_update(payload: ListUpdate):
        return update_list(vault_dir, payload, database, read_only=read_only)

    @router.post("/lists/duplicate")
    def lists_duplicate(payload: ListDuplicate):
        return duplicate_list(vault_dir, payload, database, read_only=read_only)

    @router.post("/lists/delete")
    def lists_delete(payload: NamedPayload):
        return delete_list(vault_dir, payload, database, read_only=read_only)

    @router.post("/folders")
    def folders_create(payload: NamedPayload):
        return create_folder(vault_dir, payload, database, read_only=read_only)

    @router.post("/folders/update")
    def folders_update(payload: FolderUpdate):
        return update_folder(vault_dir, payload, database, read_only=read_only)

    @router.post("/folders/delete")
    def folders_delete(payload: NamedPayload):
        return delete_folder(vault_dir, payload, database, read_only=read_only)

    @router.post("/tags")
    def tags_create(payload: TagCreate):
        return create_tag(vault_dir, payload, database, read_only=read_only)

    @router.post("/tags/update")
    def tags_update(payload: TagUpdate):
        return update_tag(vault_dir, payload, database, read_only=read_only)

    @router.post("/tags/delete")
    def tags_delete(payload: NamedPayload):
        return delete_tag(vault_dir, payload, database, read_only=read_only)

    return router
