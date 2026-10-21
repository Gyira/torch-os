"""习惯的 Markdown 镜像（2026-10-21）。

工作台继续使用现有 `/api/habits` 接口与 SQLite 操作层；每次写入后把定义和当月
打卡表镜像到 `00_人生路/06_目标与计划/04_习惯追踪/`。Markdown 可读可改，
SQLite 仍是当前写入路径；后续可在这个镜像上做双向同步。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from . import vault_layout
from .db import utc_now

WEEKDAY_LABELS = "一二三四五六日"


def definition_path(vault_dir: Path | str) -> Path:
    return Path(vault_dir) / vault_layout.chapter_dir("habit") / "习惯定义.md"


def log_path(vault_dir: Path | str, month: str) -> Path:
    return Path(vault_dir) / vault_layout.chapter_dir("habit") / f"{month}_打卡.md"


def _weekdays(value: str) -> str:
    try:
        days = json.loads(value)
    except (TypeError, ValueError):
        days = []
    days = sorted({int(day) for day in days if isinstance(day, int) and 0 <= day <= 6})
    if len(days) == 7:
        return "每天"
    return "周" + "、周".join(WEEKDAY_LABELS[day] for day in days) if days else ""


def _write_if_changed(path: Path, lines: list[str]) -> bool:
    text = "\n".join(lines)
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    temporary.replace(path)
    return True


def mirror(vault_dir: Path | str, habits: list[dict[str, Any]],
           entries: list[dict[str, Any]], month: str) -> list[str]:
    written: list[str] = []
    now = utc_now()
    definition_meta = {
        "type": "habit_definition", "title": "习惯定义", "pillar": "life",
        "chapter": "habit", "status": "active", "updated": now,
    }
    lines = ["---", *yaml.safe_dump(definition_meta, allow_unicode=True,
                                    sort_keys=False).rstrip("\n").split("\n"), "---", "",
             "| id | 名称 | 触发提示 | 最低行动 | 开始日期 | 计划日 | 状态 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for habit in habits:
        lines.append("| " + " | ".join([
            str(habit.get("id", "")), str(habit.get("title", "")),
            str(habit.get("cue", "")), str(habit.get("minimum_action", "")),
            str(habit.get("start_date", "")), _weekdays(str(habit.get("weekdays_json", "[]"))),
            str(habit.get("status", "active")),
        ]).replace("|", "／") + " |")
    if _write_if_changed(definition_path(vault_dir), lines):
        written.append(str(definition_path(vault_dir)))

    log_meta = {
        "type": "habit_log", "title": f"{month} 习惯打卡", "month": month,
        "pillar": "life", "chapter": "habit", "status": "active", "updated": now,
    }
    log_lines = ["---", *yaml.safe_dump(log_meta, allow_unicode=True,
                                        sort_keys=False).rstrip("\n").split("\n"), "---", "",
                 "| 日期 | 习惯id | 状态 | 备注 |", "| --- | --- | --- | --- |"]
    for entry in entries:
        if not str(entry.get("date", "")).startswith(month):
            continue
        log_lines.append("| " + " | ".join([
            str(entry.get("date", "")), str(entry.get("habit_id", "")),
            str(entry.get("status", "")), str(entry.get("note", "")),
        ]).replace("|", "／") + " |")
    if _write_if_changed(log_path(vault_dir, month), log_lines):
        written.append(str(log_path(vault_dir, month)))
    return written


__all__ = ["definition_path", "log_path", "mirror"]
