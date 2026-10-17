"""滴答清单远程任务 → torch-os 任务笔记字段的纯映射。

这里不访问网络、不读写文件，方便单测；所有输出都只包含普通 Python 值。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .dida_client import RemoteChecklistItem, RemoteTask

SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_TITLE = 200
MAX_TAGS = 12
MAX_TAG_NAME = 24
MAX_BODY_CHARS = 50_000
MAX_LIST_CHARS = 40
_FORBIDDEN_LIST = re.compile(r'[<>:"/\\|?*#^\[\]\x00-\x1f]')
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


@dataclass(frozen=True)
class MappedTask:
    """一条滴答任务映射成 torch-os 任务后的全部字段。"""

    dida_id: str
    project_id: str
    title: str
    list_name: str
    values: dict[str, Any]
    body: str
    remote_hash: str
    content_hash: str
    etag: str
    modified: str
    warnings: tuple[str, ...] = ()

    def material(self) -> dict[str, Any]:
        """用于和本地笔记比较的可见字段（不含正文）。"""
        return {
            "title": self.title,
            "list_name": self.list_name,
            "date": self.values.get("date", ""),
            "time": self.values.get("time", ""),
            "priority": self.values.get("priority", "none"),
            "tags": list(self.values.get("tags", [])),
            "done": bool(self.values.get("done")),
            "done_on": self.values.get("done_on", ""),
            "created": self.values.get("created", ""),
        }


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def clean_title(value: Any) -> str:
    text = _CONTROL.sub("", normalize_text(value))
    if not text:
        return "未命名任务"
    return text[:MAX_TITLE].rstrip() or "未命名任务"


def sanitize_list_name(value: Any, *, fallback: str = "滴答清单") -> str:
    """把滴答项目名变成能直接当任务文件夹的清单名。"""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = _FORBIDDEN_LIST.sub("-", text)
    text = re.sub(r"\s+", " ", text).strip(" .-")
    text = text[:MAX_LIST_CHARS].strip(" .-")
    if not text:
        text = fallback
    if text.split(".", 1)[0].upper() in _RESERVED:
        text = f"任务-{text}"
    return text


def priority_text(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "none"
    if number >= 5:
        return "high"
    if number >= 3:
        return "medium"
    if number >= 1:
        return "low"
    return "none"


def clean_tags(value: Any) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    tags: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            item = item.get("name") or item.get("title") or ""
        name = re.sub(r"\s+", "", str(item or "")).lstrip("#").strip()
        if not name or name in tags:
            continue
        tags.append(name[:MAX_TAG_NAME])
        if len(tags) >= MAX_TAGS:
            break
    return tags


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "").strip()) if name else SHANGHAI
    except (ZoneInfoNotFoundError, ValueError):
        return SHANGHAI


def _parse_datetime(value: Any, default_tz: ZoneInfo) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    # 滴答常见 +0800，fromisoformat 需要 +08:00。
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed


def _parse_wall(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None


def _time_label(value: datetime) -> str:
    return value.strftime("%H:%M")


def map_time_fields(task: RemoteTask, target_tz: ZoneInfo) -> tuple[str, str, list[str]]:
    """返回 (日期, 时间, warnings)；时间可能是 HH:MM 或 HH:MM-HH:MM。"""
    warnings: list[str] = []
    start_raw = task.start_date
    due_raw = task.due_date
    if not start_raw and not due_raw:
        return "", "", warnings

    if task.is_all_day:
        literal = due_raw or start_raw
        return str(literal)[:10] if literal else "", "", warnings

    if task.is_floating:
        start = _parse_wall(start_raw)
        due = _parse_wall(due_raw)
        chosen = due or start
        if chosen is None:
            return "", "", warnings + ["invalid_date"]
        if start and due and start.date() == due.date() and start < due:
            return chosen.date().isoformat(), f"{_time_label(start)}-{_time_label(due)}", warnings
        return chosen.date().isoformat(), _time_label(chosen), warnings

    task_tz = _zone(task.time_zone)
    start = _parse_datetime(start_raw, task_tz)
    due = _parse_datetime(due_raw, task_tz)
    if start is not None:
        start = start.astimezone(target_tz)
    if due is not None:
        due = due.astimezone(target_tz)
    chosen = due or start
    if chosen is None:
        return "", "", warnings + ["invalid_date"]
    if start and due and start.date() == due.date() and start < due:
        return chosen.date().isoformat(), f"{_time_label(start)}-{_time_label(due)}", warnings
    if start and due and start.date() != due.date():
        warnings.append("multi_day_collapsed")
    return chosen.date().isoformat(), _time_label(chosen), warnings


def _date_of(value: Any, target_tz: ZoneInfo) -> str:
    parsed = _parse_datetime(value, target_tz)
    if parsed is None:
        text = str(value or "").strip()
        return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else ""
    return parsed.astimezone(target_tz).date().isoformat()


class _HTMLToMarkdown(HTMLParser):
    """够用即可的 HTML → 纯文本/Markdown，不保留脚本、样式和危险链接。"""

    _BLOCK = {"p", "div", "section", "article", "header", "footer", "main",
              "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "tr"}
    _SKIP = {"script", "style", "template", "svg", "canvas"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._link_stack: list[str] = []
        self._list_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "br":
            self.parts.append("\n")
        elif tag == "img":
            alt = dict(attrs).get("alt") or ""
            if alt:
                self.parts.append(str(alt))
        elif tag == "a":
            href = str(dict(attrs).get("href") or "")
            self._link_stack.append(href if href.lower().startswith("https://") else "")
        elif tag in {"ul", "ol"}:
            self._list_depth += 1
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n" + "  " * max(0, self._list_depth - 1) + "- ")
        elif tag == "blockquote":
            self.parts.append("\n> ")
        elif tag == "pre":
            self.parts.append("\n```\n")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "a":
            href = self._link_stack.pop() if self._link_stack else ""
            if href:
                self.parts.append(f" ({href})")
        elif tag in {"ul", "ol"}:
            self._list_depth = max(0, self._list_depth - 1)
            self.parts.append("\n")
        elif tag == "pre":
            self.parts.append("\n```\n")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts).replace("\r\n", "\n").replace("\r", "\n")
        raw = re.sub(r"[ \t]+\n", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def html_to_markdown(value: Any) -> str:
    text = str(value or "")
    if not text.strip():
        return ""
    if "<" not in text:
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    parser = _HTMLToMarkdown()
    try:
        parser.feed(text)
        parser.close()
        return parser.text()
    except Exception:
        # 解析失败时退回纯文本，绝不因为一段 HTML 让整次同步失败。
        return re.sub(r"<[^>]*>", " ", text).strip()


def _truncate_body(text: str) -> tuple[str, list[str]]:
    if len(text) <= MAX_BODY_CHARS:
        return text, []
    return text[:MAX_BODY_CHARS].rstrip(), ["body_truncated"]


def build_body(content: Any, items: Iterable[RemoteChecklistItem]) -> tuple[str, list[str]]:
    text = html_to_markdown(content)
    item_lines: list[str] = []
    for item in items:
        title = clean_title(item.title)
        checked = "x" if int(item.status or 0) != 0 else " "
        item_lines.append(f"- [{checked}] {title}")
    if item_lines:
        section = "## 子任务\n\n" + "\n".join(item_lines)
        text = f"{text}\n\n{section}".strip() if text else section
    return _truncate_body(text)


def remote_hash(task: RemoteTask) -> str:
    payload = {
        "id": task.id,
        "project_id": task.project_id,
        "title": task.title,
        "content": task.content,
        "due": task.due_date,
        "start": task.start_date,
        "all_day": task.is_all_day,
        "floating": task.is_floating,
        "time_zone": task.time_zone,
        "priority": task.priority,
        "status": task.status,
        "completed": task.completed_time,
        "created": task.created_time,
        "tags": list(task.tags),
        "items": [{"id": item.id, "title": item.title, "status": item.status} for item in task.items],
        "kind": task.kind,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def content_hash(task: RemoteTask) -> str:
    payload = {
        "content": task.content,
        "items": [{"id": item.id, "title": item.title, "status": item.status} for item in task.items],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def map_task(task: RemoteTask, *, target_tz: ZoneInfo | str, list_name: str) -> MappedTask:
    """把一条远程任务映射成笔记字段。"""
    tz = target_tz if isinstance(target_tz, ZoneInfo) else _zone(target_tz)
    warnings: list[str] = []
    date_text, time_text, time_warnings = map_time_fields(task, tz)
    warnings.extend(time_warnings)
    done = int(task.status or 0) in (2, -1)
    if int(task.status or 0) == -1:
        warnings.append("abandoned_as_done")
    done_on = _date_of(task.completed_time, tz) if done else ""
    created = _date_of(task.created_time, tz)
    body, body_warnings = build_body(task.content, task.items)
    warnings.extend(body_warnings)
    if task.repeat_flag:
        warnings.append("repeat_ignored")
    if task.reminders:
        warnings.append("reminders_ignored")
    if task.attachments:
        warnings.append("attachments_ignored")
    if int(task.comment_count or 0) > 0:
        warnings.append("comments_ignored")
    values = {
        "date": date_text,
        "time": time_text,
        "priority": priority_text(task.priority),
        "tags": clean_tags(task.tags),
        "done": done,
        "done_on": done_on,
        "pinned": False,
        "created": created,
        "dida_id": str(task.id or "").strip(),
    }
    return MappedTask(
        dida_id=str(task.id or "").strip(),
        project_id=str(task.project_id or "").strip(),
        title=clean_title(task.title),
        list_name=list_name,
        values=values,
        body=body,
        remote_hash=remote_hash(task),
        content_hash=content_hash(task),
        etag=str(task.etag or ""),
        modified=str(task.modified_time or ""),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def material_matches(note: dict[str, Any], mapped: MappedTask, *, compare_body: bool = False,
                     note_body: str = "") -> bool:
    """比较本地笔记与远程映射；正文只在 compare_body=True 时比较。"""
    for key, expected in mapped.material().items():
        actual = note.get(key)
        if key == "tags":
            actual = list(actual or [])
        elif key in {"done"}:
            actual = bool(actual)
        else:
            actual = actual or ""
        if actual != expected:
            return False
    if compare_body:
        return (note_body or "").strip() == (mapped.body or "").strip()
    return True


def now_date(tz: ZoneInfo | str = SHANGHAI) -> str:
    zone = tz if isinstance(tz, ZoneInfo) else _zone(tz)
    return datetime.now(zone).date().isoformat()


__all__ = [
    "MappedTask", "SHANGHAI", "clean_tags", "clean_title", "content_hash",
    "html_to_markdown", "build_body", "map_task", "map_time_fields",
    "material_matches", "normalize_text", "priority_text", "remote_hash",
    "sanitize_list_name",
]
