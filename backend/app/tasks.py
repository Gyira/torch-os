"""时间管理模块的任务：新任务一篇笔记，旧复选框任务原地只读共存（2026-10-17）。

数据本体分两层：

- **新任务** = `02_生活运营/00_待办事项/…md`（见 `task_notes.py`）：标题来自文件名或 frontmatter，
  清单来自文件夹；勾选完成会移到 `已完成/`，改名/换清单就是改文件名/移动文件；正文原样保留。
- **旧任务** = `_系统/任务（tasks）.md` 里的复选框行：继续解析、展示，但应用内只读
  （可在 Obsidian 里直接修改这份文件）。产品不自动迁移旧行。

清单/标签/逻辑文件夹定义仍在 `任务（tasks）.md` 的 front matter（兼容旧文件名 `任务.md` 和
迁移前的 `00-System/任务（tasks）.md`）。
旧任务只允许被清单/标签这类结构性操作（改名、复制、删除清单，改/删标签）连带改写；
单个任务与批量的按 key 写操作只作用于新笔记，旧 key 一律 409。

所有 Markdown 写入沿用 `markdown_store` 的「先比对、再临时文件 + os.replace」协议；
board 的 revision 是「配置文件 + 全部任务笔记」的复合修订号，任何一处被别处改过就 409。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import yaml
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .db import Database, utc_now
from .markdown_store import (
    guarded_write as _guarded_write,
    heading_line as _heading_line,
    load_note,
    section_bounds as _section_bounds,
)
from . import task_notes, task_trash, vault_layout
from .task_notes import INBOX, TASK_ROOT_POSIX
from .task_import import parse_checkboxes
from .vault import managed_note_path, note_relative
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SHANGHAI = ZoneInfo("Asia/Shanghai")
TASKS_REL = Path(vault_layout.TASK_ROOT, "_系统", "任务（tasks）.md")
TASKS_CANONICAL_LEGACY_REL = Path(vault_layout.TASK_ROOT, "_系统", "任务.md")
TASKS_07_REL = Path("00_人生路", "07_任务与待办", "_系统", "任务（tasks）.md")
TASKS_07_LEGACY_REL = Path("00_人生路", "07_任务与待办", "_系统", "任务.md")
TASKS_08_REL = Path("00_人生路", "08_任务与待办", "_系统", "任务（tasks）.md")
TASKS_08_LEGACY_REL = Path("00_人生路", "08_任务与待办", "_系统", "任务.md")
TASKS_OLD_REL = Path("00-System", "任务（tasks）.md")
TASKS_LEGACY_REL = Path("00-System", "任务.md")
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
CHECKBOX_PREFIX = re.compile(r"^\s*[-*+]\s*\[[ xX]\]\s*")

TaskKey = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


# ===== 请求模型 =====


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
    revision: str = Field(default="", max_length=64)
    title: str = Field(default="", max_length=MAX_TITLE)
    list_name: str = Field(default="", max_length=MAX_LIST)
    date: str = Field(default="", max_length=10)
    time: str = Field(default="", max_length=11)
    priority: str = Field(default="", max_length=10)
    tags: list[str] | None = Field(default=None, max_length=MAX_TAGS)
    pinned: bool | None = None
    done: bool | None = None
    day: str = Field(default="", max_length=10)


class TaskDocumentSave(BaseModel):
    """任务文档的正文/标题保存；revision 是单文件的 revision，不是任务板复合 revision。"""

    model_config = ConfigDict(extra="forbid")

    key: TaskKey
    expected_revision: str = Field(default="", max_length=64)
    title: str = Field(default="", max_length=MAX_TITLE)
    body: str = Field(default="", max_length=200_000)


class TaskReorder(BaseModel):
    """把任务放到 before 那条任务的前面；before 为空 = 放到全部任务末尾。

    `order` 是「首次切换到手动排序」时的整屏顺序：前端按当前显示顺序发上来，
    后端一次性把顺序写进笔记，避免刚切到手动时整块列表按路径重排。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: TaskKey
    before: str = Field(default="", max_length=64)
    order: list[TaskKey] | None = Field(default=None, max_length=500)
    revision: str = Field(default="", max_length=64)


class TaskKeyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: TaskKey
    revision: str = Field(default="", max_length=64)


class TaskDuplicate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: TaskKey
    revision: str = Field(default="", max_length=64)
    list_name: str = Field(default="", max_length=MAX_LIST)


class TaskBulk(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    keys: list[TaskKey] = Field(min_length=1, max_length=200)
    action: str = Field(max_length=10)
    list_name: str = Field(default="", max_length=MAX_LIST)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    date: str = Field(default="", max_length=10)
    priority: str = Field(default="", max_length=10)
    pinned: bool = True
    revision: str = Field(default="", max_length=64)

    @model_validator(mode="after")
    def _unique_keys(self):
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("批量操作里同一个任务不能出现两次")
        return self


class TrashDelete(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    paths: list[str] = Field(default_factory=list, max_length=500)
    all: bool = False

    @model_validator(mode="after")
    def _selected(self):
        if not self.all and not self.paths:
            raise ValueError("至少选择一个回收站条目")
        return self


class TrashRestore(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    paths: list[str] = Field(min_length=1, max_length=500)


class ListCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_LIST)
    color: str = Field(default="", max_length=9)
    folder: str = Field(default="", max_length=MAX_LIST)
    dida_project_id: str = Field(default="", max_length=64)
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
    scope: str = "undone"
    revision: str = Field(default="", max_length=64)


class ListReorder(BaseModel):
    """把清单放到同一文件夹里另一条清单的前面；before 为空 = 放到该组末尾。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_LIST)
    before: str = Field(default="", max_length=MAX_LIST)
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


# ===== 通用校验 =====


def _today() -> str:
    return datetime.now(SHANGHAI).date().isoformat()


def _audit(database: Database, event_type: str, details: dict[str, Any]) -> None:
    if database is None:  # 启动迁移/离线脚本允许不传库；有库时审计照写
        return
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO audit_events(event_type, detail_json, created_at) VALUES (?, ?, ?)",
            (event_type, json.dumps(details, ensure_ascii=False), utc_now()),
        )


def _clean_date(value: str, field: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as error:
        raise HTTPException(422, f"{field}要写成 YYYY-MM-DD") from error
    return text


def _clean_time(value: str) -> str:
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


def _tag_key(value: str) -> str:
    """标签的规范名：不含空白、不带前导 #。

    ``defined_tags`` / 任务笔记 / 旧任务行都按这个形式存标签；配置里的标签名
    也必须用同一个规范名比较，否则“工作 标签”和“工作标签”会被当成两个标签，
    删除时就会只删掉其中一份（2026-10-30 用户报“标签删不掉但提示已删除”）。
    """
    return re.sub(r"\s+", "", str(value or "")).lstrip("#").strip()


def _clean_tag_name(value: str) -> str:
    name = _tag_key(value)
    if not name or len(name) > MAX_TAG_NAME or any(char in name for char in "#\n"):
        raise HTTPException(422, f"标签名需要 1–{MAX_TAG_NAME} 个字，且不能带 #")
    return name


def _clean_tags(values: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for raw in values or []:
        name = _tag_key(raw)
        if not name:
            continue
        if len(name) > MAX_TAG_NAME or any(char in name for char in "#\n"):
            raise HTTPException(422, f"标签名最多 {MAX_TAG_NAME} 个字，且不能带 #")
        if name not in cleaned:
            cleaned.append(name)
    if len(cleaned) > MAX_TAGS:
        raise HTTPException(422, f"一个任务最多 {MAX_TAGS} 个标签")
    return cleaned


def _clean_title(value: str) -> str:
    title = re.sub(r"\s+", " ", str(value or "").strip())
    if not title or len(title) > MAX_TITLE or any(ord(char) < 32 for char in title):
        raise HTTPException(422, f"任务标题需要 1–{MAX_TITLE} 个字符，且不能包含换行")
    return title


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


def _clean_list_name(value: str) -> str:
    name = _clean_name(value, what="清单名")
    return task_notes.clean_list_name(name)


def _ensure_write(read_only: bool, action: str) -> None:
    try:
        ensure_vault_writable(read_only, action)
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error


# ===== 旧复选框任务的解析（保持旧格式兼容） =====


def _tasks_path(vault_dir: Path | str) -> Path:
    """任务配置：生活运营现行路径优先，其次旧 07 / 08 / 00-System。"""
    for relative in (TASKS_REL, TASKS_CANONICAL_LEGACY_REL,
                     TASKS_07_REL, TASKS_07_LEGACY_REL,
                     TASKS_08_REL, TASKS_08_LEGACY_REL,
                     TASKS_OLD_REL, TASKS_LEGACY_REL):
        path = managed_note_path(vault_dir, relative.as_posix())
        if path.exists():
            return path
    return managed_note_path(vault_dir, TASKS_REL.as_posix())


def _load_tasks(path: Path) -> dict[str, Any]:
    return load_note(path, too_large="任务文件过大，已停止编辑",
                     bad_encoding="任务文件不是 UTF-8 编码，暂不能编辑")


def _front_matter_state(loaded: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """读 front matter；解析失败时第二个返回值是给用户看的原因（"" = 正常）。

    ``_front_matter`` 把坏 YAML 当成空配置，任何一次写回都会把清单/标签定义
    整段抹掉（2026-10-31 追查「torchos项目管理」为什么变成不可编辑的合成清单时发现的
    损失路径）。读的时候只用空配置兜底，写之前必须先用这里的 error 拦住。
    """
    if not loaded["exists"] or loaded["body_start"] == 0:
        return {}, ""
    block = "\n".join(loaded["lines"][1:loaded["body_start"] - 1])
    try:
        data = yaml.safe_load(block) or {}
    except yaml.YAMLError as error:
        detail = getattr(error, "problem", "") or str(error).splitlines()[0]
        return {}, f"任务配置文件的 front matter 解析失败（{detail}）；先在 Obsidian 里修好"
    if not isinstance(data, dict):
        return {}, "任务配置文件的 front matter 不是键值结构；先在 Obsidian 里修好"
    return data, ""


def _front_matter(loaded: dict[str, Any]) -> dict[str, Any]:
    return _front_matter_state(loaded)[0]


def defined_lists(loaded: dict[str, Any]) -> list[dict[str, Any]]:
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
                "dida_project_id": str(item.get("dida_project_id") or "").strip(),
                "synthetic": False,
            })
    if INBOX not in seen:
        entries.insert(0, {"name": INBOX, "color": "#3b82f6", "folder": "",
                           "pinned": False, "synthetic": False})
    return entries


def defined_tags(loaded: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _front_matter(loaded).get("标签") or []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = _tag_key(str(item.get("名称") or ""))
                color, pinned = str(item.get("颜色") or "").strip(), item.get("置顶") is True
            else:
                name = _tag_key(str(item or ""))
                color, pinned = "", False
            if not name or name in seen:
                continue
            seen.add(name)
            entries.append({"name": name, "color": color, "pinned": pinned})
    return entries


def _extract_tags(text: str) -> tuple[str, list[str]]:
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
        if normalized:
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


def _parse_legacy_tasks(loaded: dict[str, Any], config_rel: str) -> list[dict[str, Any]]:
    """配置里的复选框行 → 只读旧任务（保留内部 line/raw 供结构操作使用）。"""
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
            "raw": raw,
            "path": config_rel,
            "legacy": True,
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
        })
    return tasks


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    return {key: task[key] for key in (
        "key", "path", "legacy", "title", "list_name", "done", "done_on", "date",
        "time", "priority", "tags", "pinned", "created")}


def _note_public(note: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": note["key"], "path": note["relpath"], "legacy": False,
        "title": note["title"], "list_name": note["list_name"], "done": note["done"],
        "done_on": note["done_on"], "date": note["date"], "time": note["time"],
        "priority": note["priority"], "tags": list(note["tags"]), "pinned": note["pinned"],
        "created": note["created"], "sort_order": note.get("sort_order"),
        "goal": note.get("goal", ""), "storage": note.get("storage", ""),
    }


def _note_values(note: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return task_notes.note_values(note, **changes)


# ===== 状态、修订、定位 =====


def _read_state(vault_dir: Path | str) -> dict[str, Any]:
    path = _tasks_path(vault_dir)
    loaded = _load_tasks(path)
    notes, warnings = task_notes.scan_task_notes(vault_dir)
    config_rel = note_relative(path, vault_dir)
    legacy = _parse_legacy_tasks(loaded, config_rel)
    _metadata, config_error = _front_matter_state(loaded)
    return {
        "path": path, "loaded": loaded, "notes": notes, "warnings": warnings,
        "config_rel": config_rel, "legacy": legacy, "config_error": config_error,
        "revision": task_notes.board_revision(loaded.get("revision", ""), notes),
    }


def _check_revision(expected: str, actual: str) -> None:
    if (expected or "").strip() and expected.strip() != actual:
        raise HTTPException(409, "任务文件已在别处修改：请刷新后重试")


def _revision_now(vault_dir: Path | str) -> str:
    return _read_state(vault_dir)["revision"]


def _require_note(state: dict[str, Any], key: str) -> dict[str, Any]:
    for note in state["notes"]:
        if note["key"] == key:
            return note
    if any(task["key"] == key for task in state["legacy"]):
        raise HTTPException(409, "旧任务仍保存在 任务（tasks）.md，请在 Obsidian 中修改（应用内只读）")
    raise HTTPException(409, "这条任务已不在文件里（可能已在 Obsidian 中被改动），请刷新后重试")


def _all_list_names(state: dict[str, Any]) -> set[str]:
    names = {entry["name"] for entry in defined_lists(state["loaded"])}
    names.add(INBOX)
    names.update(_observed_list_names(state))
    return names


def _observed_list_names(state: dict[str, Any]) -> set[str]:
    """任务里实际用到的清单名：目录里有任务、配置里还没登记的也算（合成清单）。"""
    return {task["list_name"] for task in [*state["legacy"], *state["notes"]]}


def _adopt_definition(state: dict[str, Any], name: str) -> dict[str, Any] | None:
    """合成清单第一次被写操作碰到时的定义；不是合成清单就返回 None。

    目录里已经在用的清单在配置里没有定义（历史迁移把 `任务/<清单>` 上移却没写定义），
    侧栏以前对这类清单不给菜单，编辑/删除根本无从下手（2026-10-31 用户报右键没反应）。
    这里按 create_list 的默认值补一条定义，让四个操作都能落到配置里。
    """
    if name not in _observed_list_names(state):
        return None
    return {"名称": name, "颜色": "#3b82f6"}


def undefined_list_names(vault_dir: Path | str) -> list[str]:
    """有任务、但配置 `清单:` 里没有定义、而且能补登记成清单的名字。

    配置坏掉时返回空：读不出定义就分不清“没登记”和“读不出来”，硬写只会丢定义
    （`_base_metadata` 会 409），启动迁移也会因此每次都想跑一遍。
    """
    state = _read_state(vault_dir)
    if state["config_error"]:
        return []
    known = {entry["name"] for entry in defined_lists(state["loaded"])}
    names: list[str] = []
    for name in sorted(_observed_list_names(state) - known):
        try:
            task_notes.clean_list_name(name)  # 名字不能当文件夹：登记也建不出目录
        except HTTPException:
            continue
        names.append(name)
    return names


def adopt_undefined_lists(vault_dir: Path | str, database: Database, *,
                          read_only: bool = False) -> list[str]:
    """把“目录里在用、配置没定义”的清单补登记（幂等；只写定义，不移动任务）。"""
    _ensure_write(read_only, "登记清单")
    adopted: list[str] = []
    for name in undefined_list_names(vault_dir):
        try:
            create_list(vault_dir, ListCreate(name=name), database, read_only=read_only)
        except HTTPException:
            continue  # 单个清单失败不影响其它清单；下次启动还会再试
        adopted.append(name)
    return adopted


def _require_list(state: dict[str, Any], value: str) -> str:
    target = re.sub(r"\s+", " ", str(value or "").strip()) or INBOX
    if target.casefold() == INBOX.casefold():
        return INBOX
    if target.casefold() in {name.casefold() for name in task_notes.RESERVED_FOLDER_NAMES}:
        raise HTTPException(422, f"“{target}”是系统任务文件夹，不能作为清单")
    if target not in _all_list_names(state):
        raise HTTPException(422, f"清单“{target}”不存在；先创建清单，或把任务放进收集箱")
    task_notes.clean_list_name(target)  # 新任务要能落进文件夹；失败是 422
    return target


def _folder_for_list(name: str) -> str:
    return "" if name.casefold() == INBOX.casefold() else name


# ===== 配置文件的读写 =====


def _entries(metadata: dict[str, Any], key: str) -> list[dict[str, Any]]:
    raw = metadata.get(key) or []
    entries: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                entries.append(dict(item))
            elif str(item or "").strip():
                entries.append({"名称": re.sub(r"\s+", " ", str(item).strip())})
    # 收集箱是内置清单；任何结构写回都把这条定义补齐，避免新库里
    # 「收集箱」只存在于读模型的兜底里、复制/编辑它时找不到定义。
    if key == "清单" and not any(
            str(entry.get("名称") or "").strip() == INBOX for entry in entries):
        entries.insert(0, {"名称": INBOX, "颜色": "#3b82f6"})
    return entries


def _find_entry(entries: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((entry for entry in entries
                 if str(entry.get("名称") or "").strip() == name), None)


def _find_tag_entry(entries: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """按规范名找标签定义；兼容配置里带空格的旧名字。"""
    key = _tag_key(name)
    return next((entry for entry in entries
                 if _tag_key(str(entry.get("名称") or "")) == key), None)


def _base_metadata(loaded: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if loaded["exists"]:
        parsed, error = _front_matter_state(loaded)
        if error:
            # 坏 front matter 写回去 = 静默丢掉全部清单/标签定义；宁可让用户先修文件。
            raise HTTPException(409, f"{error}（为避免覆盖清单/标签定义，本次写入已取消）")
        metadata = dict(parsed)
    metadata.setdefault("type", "tasks")
    return metadata


def _config_body(loaded: dict[str, Any]) -> list[str]:
    return list(loaded["lines"][loaded["body_start"]:]) if loaded["exists"] else []


def _full_config(metadata: dict[str, Any], body: list[str]) -> list[str]:
    block = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip("\n").split("\n")
    return ["---", *block, "---", *body]


def _write_config(loaded: dict[str, Any], metadata: dict[str, Any], body: list[str]) -> bytes:
    return _guarded_write(loaded, _full_config(metadata, body))


def _unique_list_name(existing: set[str], base: str) -> str:
    folded = {name.casefold() for name in existing}
    if base.casefold() not in folded:
        return base
    index = 2
    while f"{base} {index}".casefold() in folded:
        index += 1
    return f"{base} {index}"


def _replace_tag(lines: list[str], source: str, target: str | None) -> int:
    token = re.compile(rf"(?<=\s)#{re.escape(source)}(?=\s|$)")
    changed = 0
    for index, line in enumerate(lines):
        if not CHECKBOX_PREFIX.match(line):
            continue
        if token.search(line):
            lines[index] = token.sub(f"#{target}" if target else "", line)
            changed += 1
    return changed


def _config_lines_with_section(metadata: dict[str, Any], body: list[str],
                               source: str, new_name: str,
                               copied: list[str]) -> list[str]:
    lines = _full_config(metadata, body)
    heading = _heading_line(lines, 0, source)
    block = [f"## {new_name}", "", *copied, ""]
    if heading >= 0:
        lines[heading:heading] = block
    else:
        while lines and not lines[-1].strip():
            lines.pop()
        lines.extend(["", *block])
    return lines


def _config_lines_without_section(metadata: dict[str, Any], body: list[str],
                                  name: str) -> list[str]:
    lines = _full_config(metadata, body)
    heading = _heading_line(lines, 0, name)
    if heading >= 0:
        _content_start, insert_at = _section_bounds(lines, 0, heading)
        del lines[heading:insert_at]
    return lines


# ===== 读板面 =====


def read_task_board(vault_dir: Path | str, database: Database, day: str, *,
                    read_only: bool = False) -> dict[str, Any]:
    """The time-module board, with today's scheduled habits as virtual tasks.

    ``read_tasks`` stays habit-free on purpose: Dida sync, task reports and
    exports must never see virtual habit rows.  Only this HTTP-facing wrapper
    appends them, and it keeps the real-task optimistic lock (``revision``)
    untouched; ``habit_board_revision`` is the refresh token for habits.
    """
    board = read_tasks(vault_dir, read_only=read_only)
    try:
        from .habits import HabitService

        habit_board = HabitService(database, vault_dir, read_only=read_only).board_day(day)
        board["tasks"].extend(habit_board["tasks"])
        board["habit_board_revision"] = habit_board["revision"]
        board["habit_day"] = habit_board["day"]
        board["warnings"] = list(board.get("warnings") or []) + list(habit_board.get("warnings") or [])
    except Exception as exc:  # a broken habit must not blank the whole task board
        board["habit_board_revision"] = ""
        board["habit_day"] = day
        board["warnings"] = list(board.get("warnings") or []) + [
            {"path": "习惯", "reason": str(getattr(exc, "detail", exc))}]
    return board


def read_tasks(vault_dir: Path | str, *, read_only: bool = False) -> dict[str, Any]:
    state = _read_state(vault_dir)
    loaded = state["loaded"]
    lists = defined_lists(loaded)
    known = {entry["name"] for entry in lists}
    observed_lists: set[str] = {INBOX}
    for task in [*state["legacy"], *state["notes"]]:
        observed_lists.add(task["list_name"])
    for name in sorted(observed_lists - known):
        lists.append({"name": name, "color": "", "folder": "", "pinned": False, "synthetic": True})

    tags = defined_tags(loaded)
    known_tags = {entry["name"] for entry in tags}
    used = sorted({name for task in [*state["legacy"], *state["notes"]]
                   for name in task["tags"] if name not in known_tags})
    folders = [str(item) for item in (_front_matter(loaded).get("文件夹") or [])]
    cleaned_folders: list[str] = []
    for item in folders:
        name = re.sub(r"\s+", " ", str(item or "").strip())
        if name and name not in cleaned_folders:
            cleaned_folders.append(name)
    for entry in lists:
        if entry["folder"] and entry["folder"] not in cleaned_folders:
            cleaned_folders.append(entry["folder"])

    tasks = [_public_task(task) for task in state["legacy"]]
    tasks.extend(_note_public(note) for note in state["notes"])
    warnings = list(state["warnings"])
    if state["config_error"]:
        # 配置读不出来时，清单会全部退化成“合成清单”（只读展示）；明确告诉前端。
        warnings.append({"path": state["config_rel"], "reason": state["config_error"]})
    return {
        "exists": loaded["exists"],
        "revision": state["revision"],
        "path": state["config_rel"],
        "vault_name": Path(vault_dir).name,
        "task_dir": TASK_ROOT_POSIX,
        "inbox_dir": vault_layout.TASK_INBOX_DIR,
        "done_dir": vault_layout.TASK_DONE_DIR,
        "trash_dir": vault_layout.TASK_TRASH_DIR,
        "read_only": read_only,
        "legacy_tasks": len(state["legacy"]),
        "lists": lists,
        "folders": cleaned_folders,
        "tags": tags + [{"name": name, "color": "", "pinned": False} for name in used],
        "tasks": tasks,
        "warnings": warnings,
    }


# ===== 新任务 CRUD =====


def create_task(vault_dir: Path | str, payload: TaskCreate, database: Database, *,
                read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "新增任务")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    target = _require_list(state, payload.list_name)
    title = _clean_title(payload.title)
    orders = [note.get("sort_order") for note in state["notes"]
              if note.get("sort_order") is not None]
    values = {
        "date": _clean_date(payload.date, "日期"),
        "time": _clean_time(payload.time),
        "priority": _clean_priority(payload.priority),
        "tags": _clean_tags(payload.tags),
        "done": False,
        "done_on": "",
        "pinned": False,
        "created": _today(),
        # 已经有手动排序时，新任务排在最后；否则保持旧的稳定顺序。
        "sort_order": max(orders) + 1000 if orders else None,
    }
    try:
        note = task_notes.create_task_note(vault_dir, _folder_for_list(target), title, values)
    except HTTPException:
        raise
    _audit(database, "task_added", {"list": target})
    return {"revision": _revision_now(vault_dir), "task": _note_public(note)}


def update_task(vault_dir: Path | str, payload: TaskUpdate, database: Database, *,
                read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "修改任务")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    note = _require_note(state, payload.key)
    title = _clean_title(payload.title) if payload.title else note["title"]
    date = _clean_date(payload.date, "日期") if "date" in payload.model_fields_set else note["date"]
    time = _clean_time(payload.time) if "time" in payload.model_fields_set else note["time"]
    priority = _clean_priority(payload.priority) if payload.priority else note["priority"]
    tags = _clean_tags(payload.tags) if payload.tags is not None else list(note["tags"])
    pinned = payload.pinned if payload.pinned is not None else note["pinned"]
    done = payload.done if payload.done is not None else note["done"]
    done_on = note["done_on"]
    if done and not done_on:
        done_on = _clean_date(payload.day, "完成日期") or _today()
    if not done:
        done_on = ""
    target = _require_list(state, payload.list_name) if payload.list_name else note["list_name"]
    updated = task_notes.rewrite_task_note(
        vault_dir, note, folder=_folder_for_list(target), title=title,
        values={"date": date, "time": time, "priority": priority, "tags": tags,
                "done": done, "done_on": done_on, "pinned": pinned,
                "created": note["created"], "sort_order": note.get("sort_order"),
                "dida_id": note.get("dida_id", ""), "goal": note.get("goal", ""),
                "import_key": note.get("import_key", "")})
    _audit(database, "task_updated", {"list": target, "moved": target != note["list_name"]})
    return {"revision": _revision_now(vault_dir), "task": _note_public(updated)}


# ===== 任务文档（应用内编辑器） =====


def _document_payload(note: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": note["key"],
        "path": note["relpath"],
        "revision": note["revision"],
        "title": note["title"],
        "body": task_notes.note_body(note),
        "list_name": note["list_name"],
        "done": note["done"],
    }


def _document_note(state: dict[str, Any], *, key: str = "",
                   path: str = "") -> dict[str, Any]:
    """按 key 或相对路径定位任务笔记；路径只在扫描结果里比对，不碰文件系统。"""
    if key:
        return _require_note(state, key)
    target = str(path or "").replace("\\", "/").strip()
    if not target:
        raise HTTPException(422, "需要任务 key 或文档路径")
    for note in state["notes"]:
        if note["relpath"] == target:
            return note
    raise HTTPException(404, "任务文档不存在；请刷新任务板后重试")


def read_task_document(vault_dir: Path | str, *, key: str = "",
                       path: str = "") -> dict[str, Any]:
    state = _read_state(vault_dir)
    return _document_payload(_document_note(state, key=key, path=path))


def save_task_document(vault_dir: Path | str, payload: TaskDocumentSave,
                       database: Database, *, read_only: bool = False) -> dict[str, Any]:
    """保存任务文档的标题与正文；只校验该文件的 revision，避免被无关任务改动误伤。"""
    _ensure_write(read_only, "编辑任务文档")
    state = _read_state(vault_dir)
    note = _document_note(state, key=payload.key)
    expected = str(payload.expected_revision or "").strip()
    if expected and expected != note["revision"]:
        raise HTTPException(409, "任务文档已在别处修改：请重新载入后再保存")
    title = _clean_title(payload.title)
    updated = task_notes.rewrite_task_note(
        vault_dir, note, folder=_folder_for_list(note["list_name"]), title=title,
        values=_note_values(note), body=payload.body)
    _audit(database, "task_document_saved", {
        "list": updated["list_name"],
        "renamed": updated["relpath"] != note["relpath"],
        "bytes": len(payload.body.encode("utf-8")),
    })
    return _document_payload(updated)


def _stable_task_key(note: dict[str, Any]) -> str:
    return f"{note['relpath']}\u0000{note['key']}"


def _manual_sort_key(note: dict[str, Any]) -> tuple:
    """和前端「手动」排序一致：置顶在前，其次按 排序 数值，缺省的稳定沉底。"""
    order = note.get("sort_order")
    rank = 0 if note.get("pinned") else 1
    if order is None:
        return (rank, 1, 0.0, _stable_task_key(note))
    return (rank, 0, float(order), _stable_task_key(note))


def _reorder_values(sequence: list[dict[str, Any]],
                    index: int) -> dict[str, float | int]:
    """给拖拽后的序列分配「排序」值；空间不够时整组按 1000 递增重排。"""
    moved = sequence[index]
    previous = sequence[index - 1] if index > 0 else None
    following = sequence[index + 1] if index + 1 < len(sequence) else None
    previous_order = previous.get("sort_order") if previous else None
    following_order = following.get("sort_order") if following else None
    candidate: float | None = None
    if previous_order is None and following_order is None:
        if previous is None:
            candidate = 1000.0
    elif previous_order is None:
        candidate = float(following_order) - 1000.0
    elif following_order is None:
        candidate = float(previous_order) + 1000.0
    elif float(previous_order) < float(following_order):
        middle = (float(previous_order) + float(following_order)) / 2
        if float(previous_order) < middle < float(following_order):
            candidate = middle

    def sort_key(item: dict[str, Any], order: Any) -> tuple:
        rank = 0 if item.get("pinned") else 1
        if order is None:
            return (rank, 1, 0.0, _stable_task_key(item))
        return (rank, 0, float(order), _stable_task_key(item))

    if candidate is not None:
        used = {float(item["sort_order"]) for item in sequence
                if item["key"] != moved["key"] and item.get("sort_order") is not None}
        keys = [sort_key(item, candidate if item["key"] == moved["key"]
                         else item.get("sort_order")) for item in sequence]
        ordered = all(keys[position] <= keys[position + 1]
                      for position in range(len(keys) - 1))
        if candidate not in used and ordered:
            return {moved["key"]: candidate}
    return {item["key"]: (position + 1) * 1000
            for position, item in enumerate(sequence)}


def _apply_task_order(vault_dir: Path | str, state: dict[str, Any],
                      sequence: list[dict[str, Any]], moved: dict[str, Any],
                      database: Database, *, before: str = "",
                      orders: dict[str, Any] | None = None,
                      mode: str = "sparse") -> dict[str, Any]:
    if orders is None:
        orders = {item["key"]: (position + 1) * 1000
                  for position, item in enumerate(sequence)}
    changed = 0
    for item in sequence:
        target = orders.get(item["key"])
        if target is None:
            continue
        current = item.get("sort_order")
        if current is not None and float(current) == float(target):
            continue
        task_notes.rewrite_task_note(
            vault_dir, item, folder=_folder_for_list(item["list_name"]),
            title=item["title"], values=_note_values(item, sort_order=target))
        changed += 1
    if not changed:
        return {"revision": state["revision"], "reordered": False}
    _audit(database, "tasks_reordered", {
        "key": moved["key"], "before": before or None, "mode": mode,
        "changed": changed,
    })
    return {"revision": _revision_now(vault_dir), "reordered": True}


def reorder_task(vault_dir: Path | str, payload: TaskReorder, database: Database, *,
                 read_only: bool = False) -> dict[str, Any]:
    """拖拽调整任务顺序（2026-10-20）：只改「排序」字段，不动其它 frontmatter。"""
    _ensure_write(read_only, "调整任务顺序")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    note = _require_note(state, payload.key)
    if payload.order is not None:
        wanted = list(payload.order)
        if not wanted:
            raise HTTPException(422, "整屏顺序不能为空")
        if len(set(wanted)) != len(wanted):
            raise HTTPException(422, "整屏顺序里同一个任务不能出现两次")
        if note["key"] not in wanted:
            raise HTTPException(422, "整屏顺序里缺少被拖动的任务")
        by_key = {item["key"]: item for item in state["notes"]}
        sequence = []
        for key in wanted:
            item = by_key.get(key)
            if item is None:
                if any(task["key"] == key for task in state["legacy"]):
                    raise HTTPException(409, "旧任务仍保存在 任务（tasks）.md，不参与应用内排序")
                raise HTTPException(422, "整屏顺序里有不在任务笔记里的任务")
            sequence.append(item)
        return _apply_task_order(vault_dir, state, sequence, note, database,
                                 before="", mode="screen")

    before_key = payload.before.strip()
    if before_key and not re.fullmatch(r"[0-9a-f]{64}", before_key):
        raise HTTPException(422, "锚点任务 key 格式不正确")
    if before_key == note["key"]:
        return {"revision": state["revision"], "reordered": False}
    sequence = sorted(state["notes"], key=_manual_sort_key)
    rest = [item for item in sequence if item["key"] != note["key"]]
    if before_key:
        index = next((position for position, item in enumerate(rest)
                      if item["key"] == before_key), None)
        if index is None:
            if any(task["key"] == before_key for task in state["legacy"]):
                raise HTTPException(409, "旧任务仍保存在 任务（tasks）.md，不参与应用内排序")
            raise HTTPException(422, "锚点任务不在可排序的笔记里")
    else:
        index = len(rest)
    sequence = [*rest[:index], note, *rest[index:]]
    return _apply_task_order(vault_dir, state, sequence, note, database,
                             before=before_key,
                             orders=_reorder_values(sequence, index))


def delete_task(vault_dir: Path | str, payload: TaskKeyPayload, database: Database, *,
                read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "删除任务")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    note = _require_note(state, payload.key)
    task_notes.trash_note(vault_dir, note)
    _audit(database, "task_deleted", {"list": note["list_name"]})
    return {"revision": _revision_now(vault_dir), "deleted": True}


def duplicate_task(vault_dir: Path | str, payload: TaskDuplicate, database: Database, *,
                   read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "创建任务副本")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    note = _require_note(state, payload.key)
    target = _require_list(state, payload.list_name) if payload.list_name else note["list_name"]
    order = note.get("sort_order")
    copy = task_notes.create_task_note(
        vault_dir, _folder_for_list(target), note["title"],
        _note_values(note, done=False, done_on="", created=_today(), dida_id="",
                     import_key="", sort_order=(order - 0.5) if order is not None else None),
        body=task_notes.note_body(note))
    _audit(database, "task_duplicated", {"list": target})
    return {"revision": _revision_now(vault_dir), "task": _note_public(copy)}


# ===== 批量动作 =====


def bulk_tasks(vault_dir: Path | str, payload: TaskBulk, database: Database, *,
               read_only: bool = False) -> dict[str, Any]:
    labels = {"delete": "批量删除任务", "move": "批量移动任务", "tag": "批量打标签",
              "date": "批量改日期", "priority": "批量改优先级", "pin": "批量置顶/取消置顶",
              "duplicate": "批量创建副本"}
    label = labels.get(payload.action)
    if label is None:
        raise HTTPException(422, "批量动作只能是 delete / move / tag / date / priority / pin / duplicate")
    _ensure_write(read_only, label)
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    by_key = {note["key"]: note for note in state["notes"]}
    legacy_keys = {task["key"] for task in state["legacy"]}
    picked: list[dict[str, Any]] = []
    for key in payload.keys:
        note = by_key.get(key)
        if note is None:
            if key in legacy_keys:
                raise HTTPException(409, "旧任务仍保存在 任务（tasks）.md，请在 Obsidian 中修改（应用内只读）")
            raise HTTPException(409, "选中的任务里有已经不在文件里的（可能已在 Obsidian 中改过），请刷新后重试")
        picked.append(note)

    target = ""
    tags: list[str] = []
    if payload.action in {"move"}:
        target = _require_list(state, payload.list_name)
    elif payload.action == "tag":
        tags = _clean_tags(payload.tags)
        if not tags:
            raise HTTPException(422, "要打的标签不能为空")
    elif payload.action == "date":
        date = _clean_date(payload.date, "日期")
    elif payload.action == "priority":
        if not payload.priority:
            raise HTTPException(422, "批量改优先级要给出优先级")
        priority = _clean_priority(payload.priority)

    try:
        if payload.action == "delete":
            batch = task_notes.new_trash_batch(vault_dir)
            for note in picked:
                task_notes.trash_note(vault_dir, note, batch=batch)
        elif payload.action == "move":
            for note in picked:
                task_notes.rewrite_task_note(
                    vault_dir, note, folder=_folder_for_list(target),
                    title=note["title"], values=_note_values(note))
        elif payload.action == "tag":
            for note in picked:
                merged = list(dict.fromkeys([*note["tags"], *tags]))
                task_notes.rewrite_task_note(
                    vault_dir, note, folder=_folder_for_list(note["list_name"]),
                    title=note["title"], values=_note_values(note, tags=merged))
        elif payload.action == "date":
            for note in picked:
                task_notes.rewrite_task_note(
                    vault_dir, note, folder=_folder_for_list(note["list_name"]),
                    title=note["title"], values=_note_values(note, date=date))
        elif payload.action == "priority":
            for note in picked:
                task_notes.rewrite_task_note(
                    vault_dir, note, folder=_folder_for_list(note["list_name"]),
                    title=note["title"], values=_note_values(note, priority=priority))
        elif payload.action == "pin":
            for note in picked:
                task_notes.rewrite_task_note(
                    vault_dir, note, folder=_folder_for_list(note["list_name"]),
                    title=note["title"], values=_note_values(note, pinned=payload.pinned))
        else:  # duplicate
            for note in picked:
                order = note.get("sort_order")
                task_notes.create_task_note(
                    vault_dir, _folder_for_list(note["list_name"]), note["title"],
                    _note_values(note, done=False, done_on="", created=_today(), dida_id="",
                                 import_key="", sort_order=(order - 0.5) if order is not None else None),
                    body=task_notes.note_body(note))
    except Exception:
        # 跨多个文件的写入没有文件系统事务；失败时刷新页面看真实状态，并留下审计。
        _audit(database, "tasks_bulk_failed",
               {"action": payload.action, "count": len(picked), "list": target or None})
        raise

    _audit(database, f"tasks_bulk_{payload.action}",
           {"count": len(picked), "list": target or None, "tags": tags})
    return {"revision": _revision_now(vault_dir), "count": len(picked), "action": payload.action}


# ===== 清单 =====


def create_list(vault_dir: Path | str, payload: ListCreate, database: Database, *,
                read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "新增清单")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    name = _clean_list_name(payload.name)
    if name.casefold() == INBOX.casefold():
        raise HTTPException(422, "收集箱是内置清单，不能重复创建")
    if name.casefold() in {item.casefold() for item in task_notes.RESERVED_FOLDER_NAMES}:
        raise HTTPException(422, f"“{name}”是系统任务文件夹，不能作为清单名")
    folder = _clean_name(payload.folder, what="文件夹名") if payload.folder.strip() else ""
    metadata = _base_metadata(state["loaded"])
    entries = _entries(metadata, "清单")
    if any(str(entry.get("名称") or "").casefold() == name.casefold() for entry in entries):
        raise HTTPException(422, f"已经有叫“{name}”的清单了")
    folder_path = task_notes.list_folder(vault_dir, name)
    if folder_path.exists() and not folder_path.is_dir():
        raise HTTPException(409, f"任务目录里已经有同名文件“{name}”")
    entry: dict[str, Any] = {"名称": name, "颜色": _clean_color(payload.color) or "#3b82f6"}
    if payload.dida_project_id:
        entry["dida_project_id"] = payload.dida_project_id
    if folder:
        folders = [str(item) for item in (metadata.get("文件夹") or [])]
        if folder not in folders:
            metadata["文件夹"] = [*folders, folder]
        entry["文件夹"] = folder
    entries.append(entry)
    metadata["清单"] = entries
    _write_config(state["loaded"], metadata, _config_body(state["loaded"]))
    try:
        folder_path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise HTTPException(409, f"清单定义已保存，但建不了文件夹：{error}") from error
    _audit(database, "list_created", {"name": name, "folder": folder})
    return {"revision": _revision_now(vault_dir), "list": {"name": name, "folder": folder}}


def update_list(vault_dir: Path | str, payload: ListUpdate, database: Database, *,
                read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "修改清单")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    name = _clean_name(payload.name, what="清单名")
    metadata = _base_metadata(state["loaded"])
    entries = _entries(metadata, "清单")
    entry = _find_entry(entries, name)
    if entry is None:
        entry = _adopt_definition(state, name)
        if entry is None:
            raise HTTPException(422, f"没有叫“{name}”的清单")
        entries.append(entry)  # 合成清单：这次写操作顺手把定义补上
    old_name = name
    new_name = _clean_list_name(payload.new_name) if payload.new_name.strip() else ""
    rename = bool(new_name and new_name != name)
    if rename:
        if name.casefold() == INBOX.casefold():
            raise HTTPException(422, "收集箱不能改名")
        if new_name.casefold() == INBOX.casefold():
            raise HTTPException(422, "不能把清单改名为收集箱")
        if new_name.casefold() in {item.casefold() for item in task_notes.RESERVED_FOLDER_NAMES}:
            raise HTTPException(422, f"“{new_name}”是系统任务文件夹，不能作为清单名")
        if any(str(item.get("名称") or "").casefold() == new_name.casefold()
               for item in entries if item is not entry):
            raise HTTPException(422, f"已经有叫“{new_name}”的清单了")
    color = _clean_color(payload.color) if payload.color is not None else None
    folder = (_clean_name(payload.folder, what="文件夹名")
              if payload.folder is not None and payload.folder.strip() else
              ("" if payload.folder is not None else None))
    # 所有校验通过后再改 entry / 文件夹，避免校验失败留下半改状态。
    if rename:
        entry["名称"] = new_name
        name = new_name
    if payload.color is not None:
        if color:
            entry["颜色"] = color
        else:
            entry.pop("颜色", None)
    if payload.folder is not None:
        if folder:
            folders = [str(item) for item in (metadata.get("文件夹") or [])]
            if folder not in folders:
                metadata["文件夹"] = [*folders, folder]
            entry["文件夹"] = folder
        else:
            entry.pop("文件夹", None)
    if payload.pinned is not None:
        if payload.pinned:
            entry["置顶"] = True
        else:
            entry.pop("置顶", None)
    metadata["清单"] = entries
    lines = _full_config(metadata, _config_body(state["loaded"]))
    if rename:
        heading = _heading_line(lines, 0, old_name)
        if heading >= 0:
            lines[heading] = f"## {new_name}"
    renamed_folder = False
    rewritten_done: list[dict[str, Any]] = []
    if rename:
        renamed_folder = task_notes.rename_list_folder(vault_dir, old_name, new_name)
        try:
            for note in list(state["notes"]):
                if note["storage"] != "done" or note["list_name"] != old_name:
                    continue
                fresh = task_notes.rewrite_task_note(
                    vault_dir, note, folder=new_name, title=note["title"],
                    values=_note_values(note),
                )
                rewritten_done.append(fresh)
        except Exception:
            for fresh in reversed(rewritten_done):
                try:
                    task_notes.rewrite_task_note(
                        vault_dir, fresh, folder=old_name, title=fresh["title"],
                        values=_note_values(fresh),
                    )
                except Exception:
                    pass
            if renamed_folder:
                try:
                    task_notes.rename_list_folder(vault_dir, new_name, old_name)
                except Exception:
                    pass
            raise
    try:
        _guarded_write(state["loaded"], lines)
    except Exception:
        for fresh in reversed(rewritten_done):
            try:
                task_notes.rewrite_task_note(
                    vault_dir, fresh, folder=old_name, title=fresh["title"],
                    values=_note_values(fresh),
                )
            except Exception:
                pass
        if renamed_folder:
            try:
                task_notes.rename_list_folder(vault_dir, new_name, old_name)
            except Exception:
                pass
        raise
    _audit(database, "list_updated", {"name": name})
    return {"revision": _revision_now(vault_dir), "list": {"name": name}}


def reorder_list(vault_dir: Path | str, payload: ListReorder, database: Database, *,
                 read_only: bool = False) -> dict[str, Any]:
    """调整清单顺序（2026-10-20）：只动同文件夹内的相对顺序，跨文件夹的相对位置不变。

    前端只发 before 锚点；找不到清单或锚点不在同一文件夹里都拒绝，
    避免拖拽把一个清单意外挪进别的文件夹。
    """
    _ensure_write(read_only, "调整清单顺序")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    name = _clean_name(payload.name, what="清单名")
    before = _clean_name(payload.before, what="清单名") if payload.before.strip() else ""
    metadata = _base_metadata(state["loaded"])
    entries = _entries(metadata, "清单")
    entry = _find_entry(entries, name)
    if entry is None:
        raise HTTPException(422, f"没有叫“{name}”的清单")
    folder = str(entry.get("文件夹") or "").strip()

    def in_folder(item: dict[str, Any]) -> bool:
        return str(item.get("文件夹") or "").strip() == folder

    if before:
        anchor = _find_entry([item for item in entries if in_folder(item)], before)
        if anchor is None:
            raise HTTPException(422, f"清单“{before}”不在同一个文件夹里")
        if anchor is entry:
            # 拖到自己前面 = 没变化；不写文件，但也不报错。
            return {"revision": state["revision"], "list": {"name": name}}

    group = [item for item in entries if in_folder(item)]
    rest = [item for item in group if item is not entry]
    index = (next(index for index, item in enumerate(rest)
                  if str(item.get("名称") or "").strip() == before)
             if before else len(rest))
    reordered = [*rest[:index], entry, *rest[index:]]
    slots = [index for index, item in enumerate(entries) if in_folder(item)]
    for slot, item in zip(slots, reordered):
        entries[slot] = item
    metadata["清单"] = entries
    _write_config(state["loaded"], metadata, _config_body(state["loaded"]))
    _audit(database, "list_reordered", {"name": name, "before": before or None})
    return {"revision": _revision_now(vault_dir), "list": {"name": name}}


def duplicate_list(vault_dir: Path | str, payload: ListDuplicate, database: Database, *,
                   read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "复制清单")
    if payload.scope not in ("undone", "all_keep", "all_reset"):
        raise HTTPException(422, "复制范围只能是 undone / all_keep / all_reset")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    name = _clean_name(payload.name, what="清单名")
    metadata = _base_metadata(state["loaded"])
    entries = _entries(metadata, "清单")
    source = _find_entry(entries, name)
    if source is None:
        source = _adopt_definition(state, name)
        if source is None:
            raise HTTPException(422, f"没有叫“{name}”的清单")
        entries.append(source)  # 复制合成清单：顺带把原清单登记成正式清单
    existing_names = {str(entry.get("名称")) for entry in entries}
    copy_name = _clean_list_name(_unique_list_name(existing_names, f"{name} 副本"))

    note_copies = [note for note in state["notes"] if note["list_name"] == name]
    legacy_copies = [task for task in state["legacy"] if task["list_name"] == name]
    if payload.scope == "undone":
        note_copies = [note for note in note_copies if not note["done"]]
        legacy_copies = [task for task in legacy_copies if not task["done"]]

    copied_lines = [
        format_task_line(
            task["title"], done=task["done"] and payload.scope == "all_keep",
            done_on=task["done_on"] if payload.scope == "all_keep" else "",
            date=task["date"], time=task["time"], priority=task["priority"],
            tags=task["tags"], pinned=task["pinned"], created=_today())
        for task in legacy_copies
    ]
    copy_entry = {"名称": copy_name, "颜色": source.get("颜色", "#3b82f6"),
                  **({"文件夹": source["文件夹"]} if source.get("文件夹") else {})}
    entries.insert(entries.index(source), copy_entry)
    metadata["清单"] = entries
    lines = _config_lines_with_section(metadata, _config_body(state["loaded"]),
                                       name, copy_name, copied_lines)
    _guarded_write(state["loaded"], lines)

    created = 0
    for note in note_copies:
        task_notes.create_task_note(
            vault_dir, copy_name, note["title"],
            _note_values(note, done=note["done"] and payload.scope == "all_keep",
                         done_on=note["done_on"] if payload.scope == "all_keep" else "",
                         created=_today(), dida_id="", import_key=""),
            body=task_notes.note_body(note))
        created += 1
    task_notes.task_folder(vault_dir, copy_name, create=True)
    _audit(database, "list_duplicated", {"name": name, "copy": copy_name,
                                         "scope": payload.scope,
                                         "tasks": created + len(copied_lines)})
    return {"revision": _revision_now(vault_dir),
            "list": {"name": copy_name, "tasks": created + len(copied_lines)}}


def delete_list(vault_dir: Path | str, payload: NamedPayload, database: Database, *,
                read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "删除清单")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    name = _clean_name(payload.name, what="清单名")
    if name.casefold() == INBOX.casefold():
        raise HTTPException(422, "收集箱不能删除")
    metadata = _base_metadata(state["loaded"])
    entries = _entries(metadata, "清单")
    entry = _find_entry(entries, name)
    adopted = False
    if entry is None:
        entry = _adopt_definition(state, name)
        if entry is None:
            raise HTTPException(422, f"没有叫“{name}”的清单")
        adopted = True  # 合成清单：删掉目录即可，配置里本来就没有这条定义
    legacy_removed = sum(1 for task in state["legacy"] if task["list_name"] == name)
    result = task_notes.trash_list_folder(
        vault_dir, name,
        config_path=state["path"] if state["path"].is_file() else None,
        list_definition=entry,
    )
    removed = int(result.get("count") or 0) + legacy_removed
    if not adopted:
        entries.remove(entry)
        metadata["清单"] = entries
        lines = _config_lines_without_section(metadata, _config_body(state["loaded"]), name)
        _guarded_write(state["loaded"], lines)
    _audit(database, "list_deleted", {"name": name, "tasks": removed})
    return {"revision": _revision_now(vault_dir), "deleted": name, "tasks": removed,
            "trash": result.get("trash") or ""}


def _update_dida_link_relpath(database: Database, old_note: dict[str, Any],
                              new_note: dict[str, Any]) -> None:
    """任务文件移动后同步滴答链接的 relpath，避免下一次同步误判成本地编辑。"""
    dida_id = str(old_note.get("dida_id") or "").strip()
    if not dida_id or old_note.get("relpath") == new_note.get("relpath"):
        return
    try:
        with database.connect() as connection:
            connection.execute(
                """UPDATE dida_task_links
                   SET relpath = ?, last_list_name = ?, last_local_revision = ?, updated_at = ?
                   WHERE external_id = ?""",
                (new_note["relpath"], new_note["list_name"],
                 new_note.get("revision", ""), utc_now(), dida_id),
            )
    except Exception:
        # 直接调用任务的测试/只读场景可能没有 dida_task_links；同步会自行兜底。
        pass


def restore_trash_list_definitions(vault_dir: Path | str, definitions: list[dict[str, Any]],
                                   database: Database, *, read_only: bool = False) -> int:
    """恢复被删除清单的定义（不覆盖现有清单，不恢复滴答项目 ID）。"""
    _ensure_write(read_only, "恢复回收站清单定义")
    state = _read_state(vault_dir)
    metadata = _base_metadata(state["loaded"])
    entries = _entries(metadata, "清单")
    added = 0
    for definition in definitions or []:
        if not isinstance(definition, dict):
            continue
        name = re.sub(r"\s+", " ", str(definition.get("名称") or definition.get("name") or "").strip())
        if not name or name.casefold() == INBOX.casefold() or _find_entry(entries, name):
            continue
        try:
            name = task_notes.clean_list_name(name)
        except HTTPException:
            continue
        entry: dict[str, Any] = {"名称": name}
        try:
            color = _clean_color(str(definition.get("颜色") or definition.get("color") or ""))
        except HTTPException:
            color = ""
        if color:
            entry["颜色"] = color
        if definition.get("置顶") is True:
            entry["置顶"] = True
        entries.append(entry)
        try:
            task_notes.list_folder(vault_dir, name, create=True)
        except HTTPException:
            continue
        added += 1
    if added:
        metadata["清单"] = entries
        _write_config(state["loaded"], metadata, _config_body(state["loaded"]))
        _audit(database, "list_restored_from_trash", {"count": added})
    return added


def attach_list_dida_project(vault_dir: Path | str, name: str, project_id: str,
                             database: Database, *, read_only: bool = False) -> bool:
    """把滴答项目 ID 写进一条已存在的本地清单定义；清单已不存在时返回 False。"""
    _ensure_write(read_only, "关联滴答清单")
    state = _read_state(vault_dir)
    metadata = _base_metadata(state["loaded"])
    entries = _entries(metadata, "清单")
    entry = _find_entry(entries, name)
    if entry is None:
        return False
    current = str(entry.get("dida_project_id") or "").strip()
    if current == project_id:
        return True
    entry["dida_project_id"] = project_id
    metadata["清单"] = entries
    _write_config(state["loaded"], metadata, _config_body(state["loaded"]))
    _audit(database, "list_dida_project_attached",
           {"name": name, "project_id": project_id})
    return True


# ===== 逻辑文件夹（不是磁盘目录） =====


def create_folder(vault_dir: Path | str, payload: NamedPayload, database: Database, *,
                  read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "新增文件夹")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    name = _clean_name(payload.name, what="文件夹名")
    if name.casefold() in {item.casefold() for item in task_notes.RESERVED_FOLDER_NAMES}:
        raise HTTPException(422, f"“{name}”是系统任务文件夹，不能作为分组文件夹名")
    metadata = _base_metadata(state["loaded"])
    folders = [str(item) for item in (metadata.get("文件夹") or [])]
    if name in folders:
        raise HTTPException(422, f"已经有叫“{name}”的文件夹了")
    metadata["文件夹"] = [*folders, name]
    _write_config(state["loaded"], metadata, _config_body(state["loaded"]))
    _audit(database, "folder_created", {"name": name})
    return {"revision": _revision_now(vault_dir), "folder": name}


def update_folder(vault_dir: Path | str, payload: FolderUpdate, database: Database, *,
                  read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "重命名文件夹")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    name = _clean_name(payload.name, what="文件夹名")
    new_name = _clean_name(payload.new_name, what="文件夹名")
    if new_name.casefold() in {item.casefold() for item in task_notes.RESERVED_FOLDER_NAMES}:
        raise HTTPException(422, f"“{new_name}”是系统任务文件夹，不能作为分组文件夹名")
    metadata = _base_metadata(state["loaded"])
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
    _write_config(state["loaded"], metadata, _config_body(state["loaded"]))
    _audit(database, "folder_renamed", {"name": name, "new_name": new_name})
    return {"revision": _revision_now(vault_dir), "folder": new_name}


def delete_folder(vault_dir: Path | str, payload: NamedPayload, database: Database, *,
                  read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "解散文件夹")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    name = _clean_name(payload.name, what="文件夹名")
    metadata = _base_metadata(state["loaded"])
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
    _write_config(state["loaded"], metadata, _config_body(state["loaded"]))
    _audit(database, "folder_deleted", {"name": name})
    return {"revision": _revision_now(vault_dir), "deleted": name}


# ===== 标签 =====


def create_tag(vault_dir: Path | str, payload: TagCreate, database: Database, *,
               read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "新增标签")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    name = _clean_tag_name(payload.name)
    metadata = _base_metadata(state["loaded"])
    entries = _entries(metadata, "标签")
    if _find_tag_entry(entries, name):
        raise HTTPException(422, f"已经有叫“{name}”的标签了")
    entry: dict[str, Any] = {"名称": name}
    color = _clean_color(payload.color)
    if color:
        entry["颜色"] = color
    entries.append(entry)
    metadata["标签"] = entries
    _write_config(state["loaded"], metadata, _config_body(state["loaded"]))
    _audit(database, "tag_created", {"name": name})
    return {"revision": _revision_now(vault_dir), "tag": {"name": name}}


def _rewrite_note_tags(vault_dir: Path | str, note: dict[str, Any],
                       old: str, new: str | None) -> bool:
    tags: list[str] = []
    for tag in note["tags"]:
        replacement = new if tag == old else tag
        if replacement and replacement not in tags:
            tags.append(replacement)
    if tags == note["tags"]:
        return False
    task_notes.rewrite_task_note(vault_dir, note, folder=_folder_for_list(note["list_name"]),
                                 title=note["title"], values=_note_values(note, tags=tags))
    return True


def update_tag(vault_dir: Path | str, payload: TagUpdate, database: Database, *,
               read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "修改标签")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    old_name = _clean_tag_name(payload.name)
    name = old_name
    metadata = _base_metadata(state["loaded"])
    entries = _entries(metadata, "标签")
    entry = _find_tag_entry(entries, name)
    if entry is None:
        entry = {"名称": name}
        entries.append(entry)
    else:
        # 兼容旧配置里带空白的标签名：一旦编辑就写回规范名。
        entry["名称"] = name
    new_name = _clean_tag_name(payload.new_name) if payload.new_name.strip() else ""
    rename = bool(new_name and new_name != old_name)
    if rename:
        conflict = _find_tag_entry(entries, new_name)
        if conflict is not None and conflict is not entry:
            raise HTTPException(422, f"已经有叫“{new_name}”的标签了")
    color = _clean_color(payload.color) if payload.color is not None else None
    renamed = 0
    if rename:
        for note in state["notes"]:
            if old_name in note["tags"]:
                if _rewrite_note_tags(vault_dir, note, old_name, new_name):
                    renamed += 1
        entry["名称"] = new_name
        name = new_name
    if payload.color is not None:
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
    lines = _full_config(metadata, _config_body(state["loaded"]))
    legacy_renamed = _replace_tag(lines, old_name, name) if rename else 0
    _guarded_write(state["loaded"], lines)
    _audit(database, "tag_updated", {"name": name, "renamed_tasks": renamed + legacy_renamed})
    return {"revision": _revision_now(vault_dir),
            "tag": {"name": name, "renamed_tasks": renamed + legacy_renamed}}


def delete_tag(vault_dir: Path | str, payload: NamedPayload, database: Database, *,
               read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "删除标签")
    state = _read_state(vault_dir)
    _check_revision(payload.revision, state["revision"])
    name = _clean_tag_name(payload.name)
    metadata = _base_metadata(state["loaded"])
    entries = _entries(metadata, "标签")
    # 配置里可能有历史遗留的“工作 标签”；按规范名一并删掉，不能只删第一份。
    entries[:] = [entry for entry in entries
                  if _tag_key(str(entry.get("名称") or "")) != name]
    metadata["标签"] = entries
    touched = 0
    for note in state["notes"]:
        if name in note["tags"]:
            if _rewrite_note_tags(vault_dir, note, name, None):
                touched += 1
    lines = _full_config(metadata, _config_body(state["loaded"]))
    legacy = _replace_tag(lines, name, None)
    _guarded_write(state["loaded"], lines)
    _audit(database, "tag_deleted", {"name": name, "tasks": touched + legacy})
    return {"revision": _revision_now(vault_dir), "deleted": name, "tasks": touched + legacy}


# ===== 路由器 =====


def create_tasks_router(vault_dir: Path | str, database: Database, *,
                        read_only: bool = False, prefix: str = "/api") -> APIRouter:
    # `/api/tasks` 已被旧“目标与任务”的 SQLite 任务占用，时间管理统一走 /api/time。
    router = APIRouter(prefix=f"{prefix}/time", tags=["time-tasks"])

    @router.get("/tasks")
    def tasks_read():
        return read_task_board(vault_dir, database, _today(), read_only=read_only)

    @router.post("/tasks")
    def tasks_create(payload: TaskCreate):
        return create_task(vault_dir, payload, database, read_only=read_only)

    @router.post("/tasks/update")
    def tasks_update(payload: TaskUpdate):
        return update_task(vault_dir, payload, database, read_only=read_only)

    @router.get("/tasks/document")
    def tasks_document(key: str = Query(default="", max_length=64),
                       path: str = Query(default="", max_length=2048)):
        return read_task_document(vault_dir, key=key, path=path)

    @router.post("/tasks/document")
    def tasks_document_save(payload: TaskDocumentSave):
        return save_task_document(vault_dir, payload, database, read_only=read_only)

    @router.post("/tasks/reorder")
    def tasks_reorder(payload: TaskReorder):
        return reorder_task(vault_dir, payload, database, read_only=read_only)

    @router.post("/tasks/delete")
    def tasks_delete(payload: TaskKeyPayload):
        return delete_task(vault_dir, payload, database, read_only=read_only)

    @router.post("/tasks/duplicate")
    def tasks_duplicate(payload: TaskDuplicate):
        return duplicate_task(vault_dir, payload, database, read_only=read_only)

    @router.post("/tasks/bulk")
    def tasks_bulk(payload: TaskBulk):
        return bulk_tasks(vault_dir, payload, database, read_only=read_only)

    @router.get("/trash")
    def trash_list():
        return {"items": task_trash.list_trash(vault_dir)}

    @router.post("/trash/delete")
    def trash_delete(payload: TrashDelete):
        _ensure_write(read_only, "彻底删除回收站任务")
        result = task_trash.purge_trash(vault_dir, paths=payload.paths,
                                        all_items=payload.all)
        _audit(database, "task_trash_purged", {"count": result["deleted"]})
        return result

    @router.post("/trash/restore")
    def trash_restore(payload: TrashRestore):
        _ensure_write(read_only, "恢复回收站任务")
        result = task_trash.restore_trash(vault_dir, paths=payload.paths)
        restored_lists = restore_trash_list_definitions(
            vault_dir, result.get("list_definitions") or [], database, read_only=read_only)
        result["lists_restored"] = restored_lists
        _audit(database, "task_trash_restored", {"count": result["restored"],
                                                  "lists": restored_lists})
        return result

    @router.post("/lists")
    def lists_create(payload: ListCreate):
        return create_list(vault_dir, payload, database, read_only=read_only)

    @router.post("/lists/update")
    def lists_update(payload: ListUpdate):
        return update_list(vault_dir, payload, database, read_only=read_only)

    @router.post("/lists/reorder")
    def lists_reorder(payload: ListReorder):
        return reorder_list(vault_dir, payload, database, read_only=read_only)

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
