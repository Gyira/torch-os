"""Markdown storage for habits (2026-10-28).

Habit definitions and check-ins are user content, so Markdown is the authority:

- ``<habit chapter>/习惯/<title>.md`` — one note per habit; frontmatter holds
  the definition, the body is preserved verbatim.
- ``<habit chapter>/打卡/YYYY-MM.md`` — one shared monthly table per month.
- ``<habit chapter>/习惯定义.md`` — generated read-only index for humans.

The old one-way mirror (``习惯定义.md`` table + ``YYYY-MM_打卡.md``) is only
parsed during the first migration; it is never moved or deleted.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable

import yaml

from . import markdown_store, vault_layout

HABIT_TYPE = "habit"
LOG_TYPE = "habit_log"
INDEX_TYPE = "habit_index"
NOTES_DIR = "习惯"
LOGS_DIR = "打卡"
INDEX_NAME = "习惯定义.md"
STATUSES = ("done", "skipped", "unrecorded")
STATUS_ALIASES = {
    "done": "done", "完成": "done", "已完成": "done",
    "skipped": "skipped", "skip": "skipped", "主动休息": "skipped", "跳过": "skipped",
    "unrecorded": "unrecorded", "未记录": "unrecorded",
}
STANDARD_LOG_HEADER = ["日期", "习惯uid", "习惯", "状态", "备注", "记录时间"]
REQUIRED_LOG_HEADERS = ("日期",)


def chapter_dir(vault_dir: Path | str) -> Path:
    return Path(vault_dir) / vault_layout.chapter_dir("habit")


def notes_dir(vault_dir: Path | str) -> Path:
    return chapter_dir(vault_dir) / NOTES_DIR


def logs_dir(vault_dir: Path | str) -> Path:
    return chapter_dir(vault_dir) / LOGS_DIR


def index_path(vault_dir: Path | str) -> Path:
    return chapter_dir(vault_dir) / INDEX_NAME


def legacy_log_path(vault_dir: Path | str, month: str) -> Path:
    return chapter_dir(vault_dir) / f"{month}_打卡.md"


def ensure_dirs(vault_dir: Path | str) -> None:
    notes_dir(vault_dir).mkdir(parents=True, exist_ok=True)
    logs_dir(vault_dir).mkdir(parents=True, exist_ok=True)


def safe_habit_stem(title: str) -> str:
    return vault_layout.safe_note_stem(title, fallback="未命名习惯")


def unique_note_path(vault_dir: Path | str, title: str, *,
                     existing: Path | None = None, old_title: str = "") -> Path:
    """Pick a note path for ``title`` without overwriting another habit.

    When ``existing`` is supplied and its filename still follows the old title,
    a rename returns the new filename.  If the user renamed the file themselves,
    the existing path is kept (frontmatter title remains authoritative).
    """
    folder = notes_dir(vault_dir)
    base = safe_habit_stem(title)
    if existing is not None:
        existing = Path(existing)
        if existing.parent == folder:
            old_base = safe_habit_stem(old_title) if old_title else ""
            if not old_base or existing.stem == old_base:
                candidate = folder / f"{base}.md"
                if candidate == existing or not candidate.exists():
                    return candidate
    candidate = folder / f"{base}.md"
    if existing is not None and candidate == Path(existing):
        return candidate
    if not candidate.exists():
        return candidate
    for index in range(2, 1000):
        candidate = folder / f"{base}-{index}.md"
        if not candidate.exists() or (existing is not None and candidate == Path(existing)):
            return candidate
    raise OSError("同名习惯文件过多，请先整理 习惯/ 目录")


def _string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return str(value)


def _parse_frontmatter(lines: list[str], body_start: int) -> dict[str, Any]:
    if body_start <= 0 or not lines or lines[0].strip() != "---":
        return {}
    text = "\n".join(lines[1:body_start - 1])
    try:
        metadata = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter 不是合法 YAML：{exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError("frontmatter 必须是键值对象")
    return metadata


def _dump_frontmatter(metadata: dict[str, Any]) -> list[str]:
    text = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False,
                          default_flow_style=False).rstrip("\n")
    return ["---", *text.split("\n"), "---"]


def _read_loaded(path: Path) -> dict[str, Any]:
    return markdown_store.load_note(path)


def read_note(path: Path) -> dict[str, Any]:
    loaded = _read_loaded(path)
    if not loaded["exists"]:
        return {"loaded": loaded, "metadata": {}, "body_lines": [], "exists": False}
    lines: list[str] = loaded["lines"]
    metadata = _parse_frontmatter(lines, loaded["body_start"])
    body_lines = lines[loaded["body_start"]:] if loaded["body_start"] else list(lines)
    return {"loaded": loaded, "metadata": metadata, "body_lines": body_lines, "exists": True}


def render_note(metadata: dict[str, Any], body_lines: Iterable[str]) -> list[str]:
    lines = _dump_frontmatter(metadata)
    body = list(body_lines)
    if not body:
        return [*lines, ""]
    return [*lines, *body]


def write_note(path: Path, metadata: dict[str, Any], body_lines: Iterable[str], *,
               loaded: dict[str, Any] | None = None) -> bytes:
    lines = render_note(metadata, body_lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    if loaded and loaded.get("exists"):
        return markdown_store.guarded_write(loaded, lines)
    text = "\n".join(lines)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    os.replace(temporary, path)
    return text.encode("utf-8")


def _normalize_header(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("ID", "id")


def _is_separator(line: str) -> bool:
    text = str(line or "").strip()
    if not text.startswith("|"):
        return False
    return bool(re.fullmatch(r"\|?[\s:|-]+\|?", text)) and "-" in text


def split_row(line: str) -> list[str]:
    text = str(line or "").strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    sentinel = "\u0000PIPE\u0000"
    text = text.replace("\\|", sentinel)
    return [cell.replace(sentinel, "|").strip() for cell in text.split("|")]


def _clean_cell(value: Any, *, max_length: int | None = None) -> str:
    text = str(value or "").replace("\\|", "|").replace("|", "／")
    text = re.sub(r"\s+", " ", text.replace("\r", " ").replace("\n", " ")).strip()
    if max_length is not None:
        text = text[:max_length]
    return text


def _join_row(cells: Iterable[Any]) -> str:
    return "| " + " | ".join(_clean_cell(cell) for cell in cells) + " |"


def _table_start(lines: list[str], body_start: int) -> int:
    for index in range(body_start, len(lines) - 1):
        line = lines[index].strip()
        if not line.startswith("|"):
            continue
        headers = [_normalize_header(cell) for cell in split_row(line)]
        if not all(any(required == header for header in headers)
                   for required in REQUIRED_LOG_HEADERS):
            continue
        if _is_separator(lines[index + 1]):
            return index
    return -1


def read_log(path: Path) -> dict[str, Any]:
    loaded = _read_loaded(path)
    if not loaded["exists"]:
        return {"loaded": loaded, "metadata": {}, "preamble_lines": [], "suffix_lines": [],
                "header": list(STANDARD_LOG_HEADER), "rows": [], "exists": False,
                "table_start": -1, "table_end": -1}
    lines: list[str] = loaded["lines"]
    metadata = _parse_frontmatter(lines, loaded["body_start"])
    start = _table_start(lines, loaded["body_start"])
    if start < 0:
        return {"loaded": loaded, "metadata": metadata,
                "preamble_lines": list(lines[loaded["body_start"]:]),
                "suffix_lines": [], "header": list(STANDARD_LOG_HEADER), "rows": [],
                "exists": True, "table_start": -1, "table_end": -1}
    end = start + 2
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    raw_header = split_row(lines[start])
    header = [_normalize_header(cell) for cell in raw_header]
    rows: list[dict[str, str]] = []
    for line in lines[start + 2:end]:
        cells = split_row(line)
        if not any(cells):
            continue
        row = {}
        for index, name in enumerate(header):
            if not name:
                continue
            row[name] = cells[index] if index < len(cells) else ""
        rows.append(row)
    return {
        "loaded": loaded, "metadata": metadata,
        "preamble_lines": list(lines[loaded["body_start"]:start]),
        "suffix_lines": list(lines[end:]),
        "header": header, "raw_header": raw_header, "rows": rows,
        "exists": True, "table_start": start, "table_end": end,
    }


def _row_value(row: dict[str, Any], names: Iterable[str]) -> str:
    for name in names:
        for key in (name, _normalize_header(name)):
            if key in row and str(row[key]).strip():
                return str(row[key]).strip()
    return ""


def normalize_log_row(row: dict[str, Any]) -> dict[str, str] | None:
    day = _row_value(row, ("日期", "date"))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return None
    try:
        date.fromisoformat(day)
    except ValueError:
        return None
    status_text = _row_value(row, ("状态", "status"))
    status = STATUS_ALIASES.get(status_text.casefold(), STATUS_ALIASES.get(status_text))
    if status is None:
        status = "unrecorded"
    return {
        "date": day,
        "uid": _row_value(row, ("习惯uid", "habit_uid", "习惯id", "habit_id", "uid")),
        "title": _row_value(row, ("习惯", "title")),
        "status": status,
        "note": _row_value(row, ("备注", "note")),
        "updated": _row_value(row, ("记录时间", "更新时间", "updated", "updated_at")),
    }


def render_log(metadata: dict[str, Any], header: list[str],
               rows: list[dict[str, Any]], preamble: list[str],
               suffix: list[str]) -> list[str]:
    body = list(preamble)
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    tail = list(suffix)
    while tail and not tail[0].strip():
        tail.pop(0)
    while tail and not tail[-1].strip():
        tail.pop()

    lines = _dump_frontmatter(metadata)
    lines.append("")
    if body:
        lines.extend(body)
        lines.append("")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    ordered = sorted(rows, key=lambda row: (str(row.get("date") or ""), str(row.get("uid") or "")))
    for row in ordered:
        cells = []
        for name in header:
            normalized = _normalize_header(name)
            if normalized == "日期":
                cells.append(row.get("date", ""))
            elif normalized in ("习惯uid", "habit_uid", "习惯id", "habit_id", "uid"):
                cells.append(row.get("uid", ""))
            elif normalized in ("习惯", "title"):
                cells.append(row.get("title", ""))
            elif normalized in ("状态", "status"):
                cells.append(row.get("status", ""))
            elif normalized in ("备注", "note"):
                cells.append(row.get("note", ""))
            elif normalized in ("记录时间", "更新时间", "updated", "updated_at"):
                cells.append(row.get("updated", ""))
            else:
                cells.append(row.get(name, ""))
        lines.append(_join_row(cells))
    if tail:
        lines.append("")
        lines.extend(tail)
    return lines


def write_log(path: Path, metadata: dict[str, Any], rows: list[dict[str, Any]], *,
              header: list[str] | None = None, preamble: list[str] | None = None,
              suffix: list[str] | None = None, loaded: dict[str, Any] | None = None) -> bytes:
    use_header = list(header or STANDARD_LOG_HEADER)
    for required in ("日期", "状态"):
        if not any(_normalize_header(cell) == _normalize_header(required) for cell in use_header):
            use_header.append(required)
    if not any(_normalize_header(cell) in ("习惯uid", "习惯id", "uid") for cell in use_header):
        use_header.insert(1, "习惯uid")
    lines = render_log(metadata, use_header, rows,
                       [line for line in (preamble or []) if line is not None],
                       [line for line in (suffix or []) if line is not None])
    path.parent.mkdir(parents=True, exist_ok=True)
    if loaded and loaded.get("exists"):
        return markdown_store.guarded_write(loaded, lines)
    text = "\n".join(lines)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    os.replace(temporary, path)
    return text.encode("utf-8")


def _write_if_changed(path: Path, text: str) -> bool:
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    os.replace(temporary, path)
    return True


def _cleaned(value: Any) -> str:
    return _clean_cell(value)


def write_index(vault_dir: Path | str, habits: list[dict[str, Any]],
                warnings: list[dict[str, Any]] | None = None) -> bool:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    metadata = {
        "type": INDEX_TYPE,
        "title": "习惯定义",
        "pillar": "life",
        "chapter": "habit",
        "readonly": True,
        "updated": now,
    }
    lines = _dump_frontmatter(metadata)
    lines.extend([
        "",
        "> 本文件由薪火OS自动生成，修改会被覆盖。请编辑 `习惯/` 下的单篇习惯笔记；打卡记录在 `打卡/`。",
        "",
        "| uid | id | 名称 | 图标 | 分组 | 频率 | 目标 | 开始日期 | 坚持天数 | 提醒 | 优先级 | 状态 | 笔记 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for habit in habits:
        lines.append(_join_row([
            habit.get("uid", ""), habit.get("id", ""), habit.get("title", ""),
            habit.get("icon", ""), habit.get("group", ""), habit.get("frequency_label", ""),
            "当天完成打卡", habit.get("start_date", ""),
            "永远" if not habit.get("target_days") else f"{habit.get('target_days')}天",
            habit.get("reminder", ""), habit.get("priority", ""),
            "已归档" if habit.get("status") == "archived" else "进行中",
            habit.get("path", ""),
        ]))
    if warnings:
        lines.extend(["", "## 读取警告", ""])
        for item in warnings:
            lines.append(f"- {_cleaned(item.get('path') or '')}：{_cleaned(item.get('reason') or '')}")
    return _write_if_changed(index_path(vault_dir), "\n".join(lines) + "\n")


def read_table(path: Path) -> dict[str, Any]:
    """Read the first Markdown table in a file (used only for legacy mirrors)."""
    if not path.is_file():
        return {"header": [], "rows": []}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {"header": [], "rows": []}
    lines = text.replace("\r\n", "\n").split("\n")
    start = -1
    for index in range(len(lines) - 1):
        if lines[index].strip().startswith("|") and _is_separator(lines[index + 1]):
            start = index
            break
    if start < 0:
        return {"header": [], "rows": []}
    header = [_normalize_header(cell) for cell in split_row(lines[start])]
    rows = []
    for line in lines[start + 2:]:
        if not line.strip().startswith("|"):
            break
        cells = split_row(line)
        if not any(cells):
            continue
        rows.append({name: cells[index] if index < len(cells) else ""
                     for index, name in enumerate(header) if name})
    return {"header": header, "rows": rows}


def _weekdays_from_label(value: str) -> list[int]:
    text = str(value or "").strip()
    if "每天" in text or "每日" in text:
        return list(range(7))
    days = []
    for index, label in enumerate("一二三四五六日"):
        if re.search(rf"周\s*{label}", text):
            days.append(index)
    return sorted(set(days))


def parse_legacy_index(vault_dir: Path | str) -> list[dict[str, Any]]:
    table = read_table(index_path(vault_dir))
    habits: list[dict[str, Any]] = []
    for row in table["rows"]:
        title = _row_value(row, ("名称", "title"))
        if not title:
            continue
        raw_id = _row_value(row, ("id",))
        try:
            habit_id = int(raw_id)
        except ValueError:
            habit_id = 0
        weekdays = _weekdays_from_label(_row_value(row, ("计划日", "weekdays")))
        status = _row_value(row, ("状态", "status"))
        habits.append({
            "id": habit_id,
            "title": title,
            "cue": _row_value(row, ("触发提示", "cue")),
            "minimum_action": _row_value(row, ("最低行动", "minimum_action")),
            "start_date": _row_value(row, ("开始日期", "start_date")),
            "weekdays": weekdays or list(range(7)),
            "status": "archived" if "归档" in status else "active",
        })
    return habits


def parse_legacy_log(path: Path) -> list[dict[str, Any]]:
    table = read_table(path)
    rows = []
    for row in table["rows"]:
        normalized = normalize_log_row(row)
        if not normalized:
            continue
        try:
            normalized["habit_id"] = int(normalized.get("uid") or 0)
        except ValueError:
            continue
        rows.append(normalized)
    return rows


def legacy_months(vault_dir: Path | str) -> list[str]:
    folder = chapter_dir(vault_dir)
    if not folder.is_dir():
        return []
    months = []
    for path in folder.glob("*_打卡.md"):
        match = re.match(r"(\d{4}-\d{2})_打卡\.md$", path.name)
        if match:
            months.append(match.group(1))
    return sorted(months)


def content_hash(value: Any) -> str:
    text = yaml.safe_dump(value, allow_unicode=True, sort_keys=True, default_flow_style=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "HABIT_TYPE", "LOG_TYPE", "INDEX_TYPE", "NOTES_DIR", "LOGS_DIR", "INDEX_NAME",
    "STATUSES", "STATUS_ALIASES", "STANDARD_LOG_HEADER", "chapter_dir", "notes_dir",
    "logs_dir", "index_path", "legacy_log_path", "ensure_dirs", "safe_habit_stem",
    "unique_note_path", "read_note", "render_note", "write_note", "split_row",
    "read_log", "normalize_log_row", "render_log", "write_log", "write_index",
    "read_table", "parse_legacy_index", "parse_legacy_log", "legacy_months",
    "content_hash",
]
