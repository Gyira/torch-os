"""Habit definitions and check-ins (2026-10-28).

Markdown in the Vault is the authoritative store:

- ``习惯/<title>.md`` — one note per habit (frontmatter + user body).
- ``打卡/YYYY-MM.md`` — shared monthly check-in tables.
- ``习惯定义.md`` — generated read-only index.

SQLite is a derived cache that provides stable integer ids, optimistic-lock
revisions, fast statistics, and the existing downstream query shapes.  Every
public read/write calls :meth:`HabitService.sync`, which rebuilds the cache when
the Markdown file set or a file's mtime/size changed.  Writes go to Markdown
first and then re-sync, so Obsidian edits are read back and app writes never
silently overwrite a concurrent external edit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Path as PathParam, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from . import habit_markdown, habit_schedule, habit_stats
from .db import Database, utc_now
from .vault_connection import read_only_enabled

SHANGHAI = ZoneInfo("Asia/Shanghai")
HabitStatus = Literal["active", "archived"]
EntryStatus = Literal["done", "skipped", "unrecorded"]
Priority = Literal["high", "medium", "low", "none"]
HabitId = Annotated[int, PathParam(ge=1, le=9_223_372_036_854_775_807)]
PRIORITIES = ("high", "medium", "low", "none")
DONE_STATES = ("done", "skipped")
MAX_RANGE_DAYS = 93
DEFAULT_ICON = "🌱"
DEFAULT_GROUP = "默认"
SCHEMA_VERSION = "2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS habit_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    cue TEXT NOT NULL DEFAULT '',
    minimum_action TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL,
    weekdays_json TEXT NOT NULL DEFAULT '[]',
    frequency_json TEXT NOT NULL DEFAULT '{"kind":"daily"}',
    goal TEXT NOT NULL DEFAULT 'binary',
    target_days INTEGER,
    icon TEXT NOT NULL DEFAULT '🌱',
    color TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '默认',
    reminder TEXT NOT NULL DEFAULT '',
    auto_log INTEGER NOT NULL DEFAULT 0,
    priority TEXT NOT NULL DEFAULT 'medium',
    sort_order REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    source_path TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    missing INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS habit_entries (
    habit_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(habit_id, date),
    FOREIGN KEY(habit_id) REFERENCES habit_definitions(id)
);
CREATE INDEX IF NOT EXISTS habit_entries_date ON habit_entries(date);
CREATE TABLE IF NOT EXISTS habit_cache_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_SNAPSHOTS: dict[tuple[str, str], tuple[Any, ...]] = {}
_WARNINGS: dict[tuple[str, str], list[dict[str, str]]] = {}


def shanghai_today() -> date:
    return datetime.now(SHANGHAI).date()


def shanghai_now_iso() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def validate_day(value: str, *, today: date | None = None, allow_future: bool = False) -> date:
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", str(value or "")):
            raise ValueError
        result = date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "日期必须是有效的 YYYY-MM-DD") from exc
    if not allow_future and result > (today if today is not None else shanghai_today()):
        raise HTTPException(422, "习惯记录和统计不能使用未来日期")
    return result


def validate_month(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", text):
        raise HTTPException(422, "月份必须是 YYYY-MM")
    try:
        year, month = int(text[:4]), int(text[5:])
        date(year, month, 1)
    except ValueError as exc:
        raise HTTPException(422, "月份必须是有效的 YYYY-MM") from exc
    return text


def _date_range(from_date: str, to_date: str) -> tuple[date, date]:
    today = shanghai_today()
    first, last = validate_day(from_date, today=today), validate_day(to_date, today=today)
    if not 0 <= (last - first).days < MAX_RANGE_DAYS:
        raise HTTPException(422, "查询范围必须按先后顺序，且包含首尾日期最多 93 天")
    return first, last


def _month_bounds(month: str) -> tuple[date, date]:
    year, month_number = int(month[:4]), int(month[5:])
    start = date(year, month_number, 1)
    end = date(year + (1 if month_number == 12 else 0),
               1 if month_number == 12 else month_number + 1, 1) - timedelta(days=1)
    return start, end


def _new_uid() -> str:
    # Prefix with a letter so YAML never resolves it as an integer.
    return "h" + uuid.uuid4().hex


def _ensure_uid(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        digest = hashlib.sha256(f"habit\0{fallback}".encode("utf-8")).hexdigest()
        text = "h" + digest[:31]
    if text[0].isdigit():
        text = "h" + text
    return text[:80]


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _bool_int(value: Any) -> int:
    if isinstance(value, str):
        return 1 if value.strip().lower() in ("1", "true", "yes", "on") else 0
    return 1 if value else 0


def _priority(value: Any) -> str:
    text = str(value or "medium").strip().lower()
    return text if text in PRIORITIES else "medium"


def _validate_color(value: str) -> str:
    text = str(value or "").strip()
    if text and not re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        raise HTTPException(422, "颜色必须是 #RRGGBB 或空字符串")
    return text.lower()


def _validate_reminder(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", text):
        raise HTTPException(422, "提醒时间必须是 HH:MM")
    return text


def _validate_target_days(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise HTTPException(422, "坚持天数必须是正整数或 null")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "坚持天数必须是正整数或 null") from exc
    if number <= 0:
        return None
    if number > habit_schedule.TARGET_DAYS_MAX:
        raise HTTPException(422, "坚持天数不能超过 3650 天")
    return number


def _semantic_definition(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": data["title"],
        "cue": data["cue"],
        "minimum_action": data["minimum_action"],
        "start_date": data["start_date"],
        "frequency": data["frequency"],
        "goal": "binary",
        "target_days": data["target_days"],
        "icon": data["icon"],
        "color": data["color"],
        "group": data["group"],
        "reminder": data["reminder"],
        "auto_log": bool(data["auto_log"]),
        "priority": data["priority"],
        "sort_order": float(data["sort_order"]),
        "status": data["status"],
    }


def _entry_semantic(row: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({
        "status": row.get("status") or "unrecorded",
        "note": row.get("note") or "",
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _table_columns(connection, table: str) -> set[str]:
    try:
        return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return set()


def _meta_get(connection, key: str, default: str = "") -> str:
    row = connection.execute("SELECT value FROM habit_cache_meta WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else default


def _meta_set(connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO habit_cache_meta(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def _bump_generation(connection) -> None:
    generation = int(_meta_get(connection, "generation", "0") or 0)
    _meta_set(connection, "generation", str(generation + 1))


def _habit_revision(connection) -> str:
    return f"{_meta_get(connection, 'cache_id', '')}:{_meta_get(connection, 'generation', '0')}"


def _seed_meta(connection) -> None:
    connection.execute("INSERT OR IGNORE INTO habit_cache_meta(key,value) VALUES ('schema_version',?)",
                       (SCHEMA_VERSION,))
    if not _meta_get(connection, "cache_id"):
        _meta_set(connection, "cache_id", uuid.uuid4().hex)
    if not _meta_get(connection, "generation"):
        _meta_set(connection, "generation", "1")


def _rebuild_schema(connection) -> None:
    old_defs = [dict(row) for row in connection.execute("SELECT * FROM habit_definitions").fetchall()]
    try:
        old_entries = [dict(row) for row in connection.execute("SELECT * FROM habit_entries").fetchall()]
    except sqlite3.DatabaseError:
        old_entries = []
    connection.execute("PRAGMA foreign_keys = OFF")
    if _table_columns(connection, "habit_entries"):
        connection.executescript(
            "ALTER TABLE habit_entries RENAME TO habit_entries_v1;")
    connection.executescript(
        "ALTER TABLE habit_definitions RENAME TO habit_definitions_v1;")
    connection.executescript(SCHEMA)
    now = utc_now()
    for row in old_defs:
        try:
            weekdays = json.loads(row.get("weekdays_json") or "[]")
        except (TypeError, ValueError):
            weekdays = list(range(7))
        try:
            frequency = habit_schedule.normalize_frequency(None, weekdays)
        except ValueError:
            frequency = {"kind": "daily"}
        uid = "h" + hashlib.sha256(
            f"legacy-habit\0{row.get('id')}".encode("utf-8")).hexdigest()[:31]
        connection.execute(
            """INSERT INTO habit_definitions
            (id,uid,title,cue,minimum_action,start_date,weekdays_json,frequency_json,goal,
             target_days,icon,color,group_name,reminder,auto_log,priority,sort_order,status,
             source_path,content_hash,missing,deleted,revision,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(row.get("id") or 0), uid, row.get("title") or "未命名习惯",
                row.get("cue") or "", row.get("minimum_action") or "",
                row.get("start_date") or shanghai_today().isoformat(),
                json.dumps(habit_schedule.weekdays_for(frequency), ensure_ascii=False),
                json.dumps(frequency, ensure_ascii=False), "binary", None,
                DEFAULT_ICON, "", DEFAULT_GROUP, "", 0, "medium",
                float((row.get("id") or 0) * 1000),
                row.get("status") or "active", "", "", 0, 0,
                int(row.get("revision") or 1),
                row.get("created_at") or now, row.get("updated_at") or now,
            ),
        )
    for row in old_entries:
        try:
            connection.execute(
                """INSERT INTO habit_entries(habit_id,date,status,note,revision,updated_at)
                VALUES (?,?,?,?,?,?)""",
                (int(row["habit_id"]), row["date"], row.get("status") or "unrecorded",
                 row.get("note") or "", int(row.get("revision") or 1),
                 row.get("updated_at") or now),
            )
        except (sqlite3.IntegrityError, KeyError, TypeError, ValueError):
            continue
    connection.executescript(
        "DROP TABLE IF EXISTS habit_entries_v1; DROP TABLE IF EXISTS habit_definitions_v1;")
    connection.execute("PRAGMA foreign_keys = ON")
    _seed_meta(connection)


def _ensure_schema(database: Database) -> None:
    with database.connect() as connection:
        columns = _table_columns(connection, "habit_definitions")
        if not columns:
            connection.executescript(SCHEMA)
        elif "uid" not in columns or "frequency_json" not in columns:
            _rebuild_schema(connection)
        else:
            connection.executescript(SCHEMA)
        _seed_meta(connection)


def _lock_for(key: tuple[str, str]) -> threading.RLock:
    lock = _LOCKS.get(key)
    if lock is None:
        lock = threading.RLock()
        _LOCKS[key] = lock
    return lock


# ===== request models =====


class HabitInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, values):
        if isinstance(values, dict):
            for key, value in values.items():
                if value is None and key != "target_days":
                    raise ValueError(f"{key} 不能为 null")
        return values


class HabitCreate(HabitInput):
    title: str = Field(min_length=1, max_length=120)
    cue: str = Field(default="", max_length=500)
    minimum_action: str = Field(default="", max_length=500)
    start_date: str = Field(default_factory=lambda: shanghai_today().isoformat())
    frequency: dict[str, Any] | None = None
    weekdays: list[StrictInt] | None = None
    goal: Literal["binary"] = "binary"
    target_days: StrictInt | None = Field(default=None, ge=1, le=habit_schedule.TARGET_DAYS_MAX)
    icon: str = Field(default=DEFAULT_ICON, max_length=16)
    color: str = Field(default="", max_length=7)
    group: str = Field(default=DEFAULT_GROUP, max_length=40)
    reminder: str = Field(default="", max_length=5)
    auto_log: bool = False
    priority: Priority = "medium"
    sort_order: float | None = None

    @model_validator(mode="after")
    def normalize_frequency(self):
        if self.frequency is not None and self.weekdays is not None:
            raise ValueError("frequency 和 weekdays 不能同时提供")
        try:
            self.frequency = habit_schedule.normalize_frequency(
                self.frequency, self.weekdays)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        self.color = _validate_color(self.color)
        self.reminder = _validate_reminder(self.reminder)
        if not self.group:
            self.group = DEFAULT_GROUP
        return self


class HabitUpdate(HabitInput):
    revision: StrictInt = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    cue: str | None = Field(default=None, max_length=500)
    minimum_action: str | None = Field(default=None, max_length=500)
    start_date: str | None = None
    frequency: dict[str, Any] | None = None
    weekdays: list[StrictInt] | None = None
    goal: Literal["binary"] | None = None
    target_days: StrictInt | None = Field(default=None, ge=1, le=habit_schedule.TARGET_DAYS_MAX)
    icon: str | None = Field(default=None, max_length=16)
    color: str | None = Field(default=None, max_length=7)
    group: str | None = Field(default=None, max_length=40)
    reminder: str | None = Field(default=None, max_length=5)
    auto_log: bool | None = None
    priority: Priority | None = None
    sort_order: float | None = None
    status: HabitStatus | None = None

    @model_validator(mode="after")
    def normalize_frequency(self):
        provided = self.model_fields_set
        if "frequency" in provided and "weekdays" in provided:
            raise ValueError("frequency 和 weekdays 不能同时提供")
        if "frequency" in provided or "weekdays" in provided:
            try:
                self.frequency = habit_schedule.normalize_frequency(
                    self.frequency if "frequency" in provided else None,
                    self.weekdays if "weekdays" in provided else None)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
        if self.color is not None:
            self.color = _validate_color(self.color)
        if self.reminder is not None:
            self.reminder = _validate_reminder(self.reminder)
        if self.group is not None and not self.group:
            self.group = DEFAULT_GROUP
        return self


class HabitEntryWrite(HabitInput):
    revision: StrictInt = Field(ge=0)
    status: EntryStatus
    note: str = Field(default="", max_length=2000)


class HabitToggle(HabitInput):
    revision: StrictInt = Field(default=0, ge=0)
    day: str = ""
    done: bool


class HabitDelete(HabitInput):
    revision: StrictInt = Field(ge=1)


class HabitReorder(HabitInput):
    moved_id: StrictInt = Field(ge=1)
    before_id: StrictInt = Field(default=0, ge=0)
    revision: str = ""


# ===== service =====


class HabitService:
    def __init__(self, database: Database, vault_dir: Any = None, *, read_only: bool = False):
        self.database = database
        self.vault_dir = Path(vault_dir) if vault_dir else None
        # All HabitService callers (workbench/tools/pillars/scheduler/exports)
        # must respect the global read-only switch, not only the habits router.
        self.read_only = bool(read_only) or read_only_enabled()
        self._warnings: list[dict[str, str]] = []
        self.database.initialize()
        _ensure_schema(self.database)
        if self.vault_dir is not None and not self.read_only:
            try:
                self._migrate_to_markdown()
            except (OSError, ValueError, sqlite3.DatabaseError) as exc:
                self._warnings.append({"path": "习惯迁移", "reason": str(exc)})

    # ----- paths / snapshot -----

    @property
    def _key(self) -> tuple[str, str]:
        return (str(self.database.path), str(self.vault_dir or ""))

    def _snapshot(self) -> tuple[Any, ...]:
        if self.vault_dir is None:
            return ()
        entries: list[tuple[str, int, int]] = []
        for folder in (habit_markdown.notes_dir(self.vault_dir),
                       habit_markdown.logs_dir(self.vault_dir)):
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.md")):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                entries.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(entries)

    def _invalidate_snapshot(self) -> None:
        _SNAPSHOTS.pop(self._key, None)

    def _reset_after_write(self) -> None:
        self._invalidate_snapshot()

    def _require_write(self, action: str) -> None:
        if self.read_only:
            raise HTTPException(409, f"当前为只读模式，不能{action}")

    def _audit(self, event_type: str, details: dict[str, Any]) -> None:
        try:
            self.database.audit(event_type, details)
        except Exception as exc:  # audit must not make a saved Markdown change look lost
            self._warnings.append({"path": "审计", "reason": str(exc)})

    # ----- Markdown -> cache -----

    def _migrate_to_markdown(self) -> None:
        if self.vault_dir is None or self.read_only:
            return
        key = self._key
        with _lock_for(key):
            with self.database.connect() as connection:
                if _meta_get(connection, "migrated_at"):
                    return
            # Do not create 习惯/ or 打卡/ on an empty vault: read-only previews
            # and other features must not gain files just because the app started.
            notes = list(habit_markdown.notes_dir(self.vault_dir).glob("*.md"))
            if notes:
                self.sync(force=True)
            else:
                with self.database.connect() as connection:
                    definitions = [dict(row) for row in connection.execute(
                        "SELECT * FROM habit_definitions WHERE deleted=0 ORDER BY id").fetchall()]
                if definitions:
                    self._write_markdown_from_cache(definitions)
                    self.sync(force=True)
                elif habit_markdown.index_path(self.vault_dir).is_file():
                    legacy = habit_markdown.parse_legacy_index(self.vault_dir)
                    if legacy:
                        self._write_markdown_from_legacy(legacy)
                        self.sync(force=True)
            with self.database.connect() as connection:
                _meta_set(connection, "migrated_at", shanghai_now_iso())

    def _write_markdown_from_cache(self, definitions: list[dict[str, Any]]) -> None:
        assert self.vault_dir is not None
        with self.database.connect() as connection:
            entries = [dict(row) for row in connection.execute(
                "SELECT * FROM habit_entries ORDER BY date, habit_id").fetchall()]
        by_habit: dict[int, list[dict[str, Any]]] = {}
        for entry in entries:
            by_habit.setdefault(int(entry["habit_id"]), []).append(entry)
        id_to_habit = {int(row["id"]): self._row_to_habit(row) for row in definitions}
        all_rows: list[dict[str, Any]] = []
        for habit_id, habit in id_to_habit.items():
            path = habit_markdown.unique_note_path(self.vault_dir, habit["title"])
            habit_markdown.write_note(path, self._definition_metadata(habit), [])
            all_rows.extend(self._entry_log_row(habit, entry)
                            for entry in by_habit.get(habit_id, []))
        self._write_logs_for_rows(all_rows)
        habit_markdown.write_index(self.vault_dir, list(id_to_habit.values()))

    def _write_markdown_from_legacy(self, legacy: list[dict[str, Any]]) -> None:
        assert self.vault_dir is not None
        id_to_uid: dict[int, str] = {}
        all_rows: list[dict[str, Any]] = []
        for item in legacy:
            uid = _new_uid()
            old_id = int(item.get("id") or 0)
            if old_id:
                id_to_uid[old_id] = uid
            frequency = habit_schedule.normalize_frequency(None, item.get("weekdays"))
            habit = {
                "id": old_id or 0, "uid": uid, "title": item.get("title") or "未命名习惯",
                "cue": item.get("cue") or "", "minimum_action": item.get("minimum_action") or "",
                "start_date": item.get("start_date") or shanghai_today().isoformat(),
                "frequency": frequency, "goal": "binary", "target_days": None,
                "icon": DEFAULT_ICON, "color": "", "group": DEFAULT_GROUP,
                "reminder": "", "auto_log": False, "priority": "medium", "sort_order": 1000,
                "status": item.get("status") or "active", "path": "",
                "created_at": utc_now(), "updated_at": utc_now(),
            }
            path = habit_markdown.unique_note_path(self.vault_dir, habit["title"])
            habit_markdown.write_note(path, self._definition_metadata(habit), [])
            for month in habit_markdown.legacy_months(self.vault_dir):
                for row in habit_markdown.parse_legacy_log(
                        habit_markdown.legacy_log_path(self.vault_dir, month)):
                    if int(row.get("habit_id") or 0) != old_id:
                        continue
                    all_rows.append({
                        "date": row["date"], "uid": uid, "title": habit["title"],
                        "status": row["status"], "note": row.get("note") or "",
                        "updated": row.get("updated") or shanghai_now_iso(),
                    })
        if all_rows:
            self._write_logs_for_rows(all_rows)

    def _read_note_definitions(self) -> tuple[list[dict[str, Any]], list[dict[str, str]], set[str]]:
        assert self.vault_dir is not None
        folder = habit_markdown.notes_dir(self.vault_dir)
        parsed: list[dict[str, Any]] = []
        warnings: list[dict[str, str]] = []
        failed: set[str] = set()
        seen: set[str] = set()
        if not folder.is_dir():
            return parsed, warnings, failed
        for path in sorted(folder.glob("*.md")):
            rel = path.relative_to(self.vault_dir).as_posix()
            try:
                note = habit_markdown.read_note(path)
                metadata = note["metadata"]
                if not isinstance(metadata, dict) or (
                        str(metadata.get("type") or "") != habit_markdown.HABIT_TYPE
                        and not metadata.get("uid")):
                    # A file in 习惯/ that is not a habit note no longer defines
                    # this habit.  Do not add it to failed_notes: the cache row
                    # should become missing (history stays, habit hides).
                    warnings.append({"path": rel, "reason": "不是习惯笔记（缺少 type: habit / uid）"})
                    continue
                data = self._parse_definition(metadata, rel)
            except (OSError, ValueError, HTTPException) as exc:
                failed.add(rel)
                warnings.append({"path": rel, "reason": str(getattr(exc, "detail", exc))})
                continue
            if data["uid"] in seen:
                failed.add(rel)
                warnings.append({"path": rel, "reason": f"uid 重复：{data['uid']}"})
                continue
            seen.add(data["uid"])
            parsed.append(data)
        return parsed, warnings, failed

    def _parse_definition(self, metadata: dict[str, Any], relpath: str) -> dict[str, Any]:
        uid = _ensure_uid(metadata.get("uid"), relpath)
        title = _coerce_text(metadata.get("title")).strip() or Path(relpath).stem
        if len(title) > 120:
            raise ValueError("标题最多 120 字")
        try:
            frequency = habit_schedule.normalize_frequency(
                metadata.get("frequency"), metadata.get("weekdays"))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        start_date = _coerce_text(metadata.get("start_date")).strip()
        start = validate_day(start_date, allow_future=True) if start_date else shanghai_today()
        target_days = _validate_target_days(metadata.get("target_days"))
        status_text = str(metadata.get("status") or "").strip().lower()
        status = "archived" if status_text in ("archived", "已归档", "归档") else "active"
        color = _validate_color(_coerce_text(metadata.get("color")))
        reminder = _validate_reminder(_coerce_text(metadata.get("reminder")))
        icon = _coerce_text(metadata.get("icon")).strip()[:16] or DEFAULT_ICON
        group = _coerce_text(metadata.get("group")).strip()[:40] or DEFAULT_GROUP
        raw_id = metadata.get("id")
        try:
            habit_id = int(raw_id) if raw_id not in (None, "") else 0
        except (TypeError, ValueError):
            habit_id = 0
        try:
            sort_order = float(metadata.get("sort_order"))
        except (TypeError, ValueError):
            sort_order = 0.0
        data = {
            "uid": uid,
            "id": habit_id if habit_id > 0 else None,
            "title": title,
            "cue": _coerce_text(metadata.get("cue"))[:500],
            "minimum_action": _coerce_text(metadata.get("minimum_action"))[:500],
            "start_date": start.isoformat(),
            "frequency": frequency,
            "weekdays": habit_schedule.weekdays_for(frequency),
            "goal": "binary",
            "target_days": target_days,
            "icon": icon,
            "color": color,
            "group": group,
            "reminder": reminder,
            "auto_log": bool(_bool_int(metadata.get("auto_log"))),
            "priority": _priority(metadata.get("priority")),
            "sort_order": sort_order,
            "status": status,
            "path": relpath,
            "created_at": _coerce_text(metadata.get("created")) or utc_now(),
            "updated_at": _coerce_text(metadata.get("updated")) or utc_now(),
        }
        data["content_hash"] = habit_markdown.content_hash(_semantic_definition(data))
        return data

    def _read_log_entries(self, definitions: list[dict[str, Any]]):
        assert self.vault_dir is not None
        folder = habit_markdown.logs_dir(self.vault_dir)
        by_uid = {item["uid"]: item for item in definitions}
        by_id = {item["id"]: item for item in definitions if item.get("id")}
        parsed: dict[tuple[str, str], dict[str, Any]] = {}
        months: set[str] = set()
        unreadable_months: set[str] = set()
        warnings: list[dict[str, str]] = []
        failed = False
        if not folder.is_dir():
            return parsed, months, unreadable_months, warnings, failed
        for path in sorted(folder.glob("*.md")):
            match = re.fullmatch(r"(\d{4}-\d{2})\.md", path.name)
            if not match:
                warnings.append({"path": path.name, "reason": "不是 YYYY-MM.md 月度打卡表"})
                continue
            month = match.group(1)
            try:
                log = habit_markdown.read_log(path)
            except (OSError, ValueError, HTTPException) as exc:
                warnings.append({"path": path.name, "reason": str(getattr(exc, "detail", exc))})
                unreadable_months.add(month)
                failed = True
                continue
            if log["table_start"] < 0:
                unreadable_months.add(month)
                continue
            months.add(month)
            for raw in log["rows"]:
                row = habit_markdown.normalize_log_row(raw)
                if not row:
                    warnings.append({"path": path.name, "reason": "表格行缺少有效日期"})
                    unreadable_months.add(month)
                    continue
                uid = row["uid"]
                if uid in by_uid:
                    pass
                elif uid.isdigit() and int(uid) in by_id:
                    uid = by_id[int(uid)]["uid"]
                else:
                    warnings.append({"path": path.name,
                                     "reason": f"未知习惯：{uid or '空'}"})
                    continue
                if not row["date"].startswith(month):
                    warnings.append({"path": path.name,
                                     "reason": f"记录日期 {row['date']} 不在 {month}"})
                    unreadable_months.add(month)
                    continue
                key = (uid, row["date"])
                if key in parsed:
                    warnings.append({"path": path.name,
                                     "reason": f"重复记录 {row['date']} / {uid}"})
                parsed[key] = {**row, "uid": uid}
        return parsed, months, unreadable_months, warnings, failed

    def sync(self, *, force: bool = False) -> None:
        if self.vault_dir is None:
            return
        key = self._key
        signature = self._snapshot()
        if not force and _SNAPSHOTS.get(key) == signature:
            self._warnings = list(_WARNINGS.get(key, []))
            return
        with _lock_for(key):
            signature = self._snapshot()
            if not force and _SNAPSHOTS.get(key) == signature:
                self._warnings = list(_WARNINGS.get(key, []))
                return
            try:
                definitions, warnings, failed_notes = self._read_note_definitions()
                parsed_entries, months, unreadable, log_warnings, log_failed = \
                    self._read_log_entries(definitions)
            except (OSError, ValueError) as exc:
                self._warnings = [{"path": "习惯", "reason": str(exc)}]
                _WARNINGS[key] = list(self._warnings)
                return
            warnings.extend(log_warnings)
            notes_folder = habit_markdown.notes_dir(self.vault_dir)
            note_files = list(notes_folder.glob("*.md")) if notes_folder.is_dir() else []
            if note_files and not definitions:
                # Every note failed to parse: keep the last good cache instead of
                # marking every habit missing.  Editing the file changes its stat
                # and triggers another attempt.
                self._warnings = warnings
                _WARNINGS[key] = list(warnings)
                _SNAPSHOTS[key] = signature
                return
            self._warnings = warnings
            _WARNINGS[key] = list(warnings)
            self._apply_cache(definitions, parsed_entries, failed_notes,
                              unreadable, log_failed)
            _SNAPSHOTS[key] = signature

    def _apply_cache(self, definitions: list[dict[str, Any]],
                     entries: dict[tuple[str, str], dict[str, Any]],
                     failed_notes: set[str], unreadable_months: set[str],
                     log_failed: bool) -> None:
        now = utc_now()
        changed = False
        definitions_changed = False
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = {row["uid"]: dict(row) for row in connection.execute(
                "SELECT * FROM habit_definitions").fetchall()}
            used_ids = {int(row["id"]) for row in existing.values()}
            parsed_uids = {item["uid"] for item in definitions}
            for item in definitions:
                old = existing.get(item["uid"])
                semantic = habit_markdown.content_hash(_semantic_definition(item))
                if old is None:
                    habit_id = item.get("id")
                    if not isinstance(habit_id, int) or habit_id <= 0 or habit_id in used_ids:
                        habit_id = None
                    values = (
                        item["uid"], item["title"], item["cue"], item["minimum_action"],
                        item["start_date"], json.dumps(item["weekdays"], ensure_ascii=False),
                        json.dumps(item["frequency"], ensure_ascii=False), "binary",
                        item["target_days"], item["icon"], item["color"], item["group"],
                        item["reminder"], _bool_int(item["auto_log"]), item["priority"],
                        float(item["sort_order"]), item["status"], item["path"], semantic,
                        0, 0, 1, item["created_at"], now,
                    )
                    if habit_id is None:
                        cursor = connection.execute(
                            """INSERT INTO habit_definitions
                            (uid,title,cue,minimum_action,start_date,weekdays_json,frequency_json,
                             goal,target_days,icon,color,group_name,reminder,auto_log,priority,
                             sort_order,status,source_path,content_hash,missing,deleted,revision,
                             created_at,updated_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
                        habit_id = int(cursor.lastrowid)
                    else:
                        connection.execute(
                            """INSERT INTO habit_definitions
                            (id,uid,title,cue,minimum_action,start_date,weekdays_json,
                             frequency_json,goal,target_days,icon,color,group_name,reminder,
                             auto_log,priority,sort_order,status,source_path,content_hash,
                             missing,deleted,revision,created_at,updated_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (habit_id, *values))
                    used_ids.add(habit_id)
                    changed = definitions_changed = True
                    continue
                changed_now = str(old.get("content_hash") or "") != semantic
                state_changed = bool(old.get("missing") or old.get("deleted"))
                path_changed = str(old.get("source_path") or "") != item["path"]
                if changed_now or state_changed or path_changed:
                    connection.execute(
                        """UPDATE habit_definitions SET title=?,cue=?,minimum_action=?,
                        start_date=?,weekdays_json=?,frequency_json=?,goal=?,target_days=?,
                        icon=?,color=?,group_name=?,reminder=?,auto_log=?,priority=?,
                        sort_order=?,status=?,source_path=?,content_hash=?,missing=0,deleted=0,
                        revision=revision+?,updated_at=? WHERE id=?""",
                        (
                            item["title"], item["cue"], item["minimum_action"], item["start_date"],
                            json.dumps(item["weekdays"], ensure_ascii=False),
                            json.dumps(item["frequency"], ensure_ascii=False), "binary",
                            item["target_days"], item["icon"], item["color"], item["group"],
                            item["reminder"], _bool_int(item["auto_log"]), item["priority"],
                            float(item["sort_order"]), item["status"], item["path"], semantic,
                            1 if (changed_now or state_changed) else 0,
                            now if (changed_now or state_changed) else old.get("updated_at") or now,
                            int(old["id"]),
                        ),
                    )
                    if changed_now or state_changed or path_changed:
                        changed = True
                    definitions_changed = definitions_changed or changed_now or state_changed or path_changed
            # Definitions whose note disappeared are kept as missing so history survives.
            notes_dir_exists = (habit_markdown.notes_dir(self.vault_dir).is_dir()
                                if self.vault_dir is not None else False)
            if notes_dir_exists:
                for uid, old in existing.items():
                    if uid in parsed_uids or old.get("deleted"):
                        continue
                    if str(old.get("source_path") or "") in failed_notes:
                        continue
                    source = str(old.get("source_path") or "")
                    if not source:
                        # Cache-only habit (no vault notes yet): never hide it.
                        continue
                    # A source note that still exists but no longer parses as this
                    # uid (habit removed, type changed, or uid edited) is no longer
                    # authoritative for this habit; keep history but hide the habit.
                    if not old.get("missing"):
                        connection.execute(
                            "UPDATE habit_definitions SET missing=1,revision=revision+1,updated_at=? WHERE id=?",
                            (now, int(old["id"])))
                        changed = definitions_changed = True
            uid_to_id = {row["uid"]: int(row["id"]) for row in connection.execute(
                "SELECT id,uid FROM habit_definitions WHERE deleted=0").fetchall()}
            existing_entries = {(int(row["habit_id"]), row["date"]): dict(row)
                                for row in connection.execute("SELECT * FROM habit_entries").fetchall()}
            desired: set[tuple[int, str]] = set()
            for (uid, day), row in entries.items():
                habit_id = uid_to_id.get(uid)
                if habit_id is None:
                    continue
                key = (habit_id, day)
                desired.add(key)
                semantic = _entry_semantic(row)
                old = existing_entries.get(key)
                updated = row.get("updated") or now
                if old is None:
                    connection.execute(
                        """INSERT INTO habit_entries(habit_id,date,status,note,revision,updated_at)
                        VALUES (?,?,?,?,1,?)""",
                        (habit_id, day, row["status"], row["note"], updated))
                    changed = True
                elif _entry_semantic(old) != semantic:
                    connection.execute(
                        """UPDATE habit_entries SET status=?,note=?,revision=revision+1,updated_at=?
                        WHERE habit_id=? AND date=?""",
                        (row["status"], row["note"], updated, habit_id, day))
                    changed = True
            if not log_failed and definitions:
                missing_ids = {int(row["id"]) for row in connection.execute(
                    "SELECT id FROM habit_definitions WHERE missing=1 OR deleted=1").fetchall()}
                for key in existing_entries:
                    if key in desired:
                        continue
                    habit_id, day = key
                    if habit_id in missing_ids:
                        continue
                    if day[:7] in unreadable_months:
                        continue
                    connection.execute(
                        "DELETE FROM habit_entries WHERE habit_id=? AND date=?", key)
                    changed = True
            if changed:
                _bump_generation(connection)
            if definitions_changed:
                rows = [dict(row) for row in connection.execute(
                    "SELECT * FROM habit_definitions WHERE deleted=0 AND missing=0 "
                    "ORDER BY sort_order,id").fetchall()]
            else:
                rows = []
        if rows and self.vault_dir is not None:
            habit_markdown.write_index(self.vault_dir, [self._row_to_habit(row) for row in rows],
                                       self._warnings)

    # ----- row / API shapes -----

    def _row_to_habit(self, row: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(row, dict):
            row = dict(row)
        try:
            frequency = habit_schedule.normalize_frequency(
                json.loads(row.get("frequency_json") or '{"kind":"daily"}'))
        except (TypeError, ValueError, json.JSONDecodeError):
            frequency = {"kind": "daily"}
        start = str(row.get("start_date") or "")
        target_days = row.get("target_days")
        end = habit_schedule.target_end_date(start, target_days)
        today = shanghai_today()
        return {
            "id": int(row["id"]),
            "uid": str(row["uid"]),
            "title": str(row.get("title") or ""),
            "cue": str(row.get("cue") or ""),
            "minimum_action": str(row.get("minimum_action") or ""),
            "start_date": start,
            "weekdays": habit_schedule.weekdays_for(frequency),
            "frequency": frequency,
            "frequency_label": habit_schedule.frequency_label(frequency),
            "goal": "binary",
            "target_days": int(target_days) if target_days not in (None, "") else None,
            "end_date": end.isoformat() if end else None,
            "ended": bool(end and today > end),
            "icon": str(row.get("icon") or DEFAULT_ICON),
            "color": str(row.get("color") or ""),
            "group": str(row.get("group_name") or DEFAULT_GROUP),
            "reminder": str(row.get("reminder") or ""),
            "auto_log": bool(row.get("auto_log")),
            "priority": _priority(row.get("priority")),
            "sort_order": float(row.get("sort_order") or 0),
            "status": str(row.get("status") or "active"),
            "path": str(row.get("source_path") or ""),
            "revision": int(row.get("revision") or 1),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }

    def _definition_metadata(self, habit: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": habit_markdown.HABIT_TYPE,
            "uid": habit["uid"],
            "id": int(habit["id"]) if habit.get("id") else None,
            "title": habit["title"],
            "icon": habit["icon"],
            "color": habit["color"],
            "group": habit["group"],
            "frequency": habit["frequency"],
            "goal": "binary",
            "start_date": habit["start_date"],
            "target_days": habit["target_days"],
            "reminder": habit["reminder"],
            "auto_log": bool(habit["auto_log"]),
            "priority": habit["priority"],
            "status": habit["status"],
            "cue": habit["cue"],
            "minimum_action": habit["minimum_action"],
            "sort_order": float(habit["sort_order"]),
            "created": habit.get("created_at") or shanghai_now_iso(),
            "updated": shanghai_now_iso(),
        }

    def _entry_dict(self, row: dict[str, Any] | None, habit_id: int, day: str,
                    scheduled: bool) -> dict[str, Any]:
        if row is None:
            return {"habit_id": habit_id, "date": day, "scheduled": scheduled,
                    "status": "unrecorded", "note": "", "revision": 0, "updated_at": None}
        return {"habit_id": habit_id, "date": day, "scheduled": scheduled,
                "status": str(row.get("status") or "unrecorded"),
                "note": str(row.get("note") or ""),
                "revision": int(row.get("revision") or 0),
                "updated_at": row.get("updated_at")}

    def _entry_log_row(self, habit: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "date": str(entry.get("date") or ""),
            "uid": habit["uid"],
            "title": habit["title"],
            "status": str(entry.get("status") or "unrecorded"),
            "note": str(entry.get("note") or ""),
            "updated": str(entry.get("updated_at") or shanghai_now_iso()),
        }

    def _write_logs_for_rows(self, rows: list[dict[str, Any]]) -> None:
        assert self.vault_dir is not None
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            month = str(row.get("date") or "")[:7]
            if re.fullmatch(r"\d{4}-\d{2}", month):
                grouped.setdefault(month, []).append(row)
        for month, month_rows in grouped.items():
            self._write_log_file(month, month_rows)

    def _write_log_file(self, month: str, rows: list[dict[str, Any]], *,
                        loaded: dict[str, Any] | None = None) -> None:
        assert self.vault_dir is not None
        path = habit_markdown.logs_dir(self.vault_dir) / f"{month}.md"
        if loaded is None:
            log = habit_markdown.read_log(path)
        else:
            log = loaded
        metadata = dict(log.get("metadata") or {})
        metadata.update({
            "type": habit_markdown.LOG_TYPE,
            "month": month,
            "pillar": "life",
            "chapter": "habit",
            "updated": shanghai_now_iso(),
        })
        if not metadata.get("title"):
            metadata["title"] = f"{month} 习惯打卡"
        habit_markdown.write_log(
            path, metadata, rows, header=log.get("header"),
            preamble=log.get("preamble_lines") or [],
            suffix=log.get("suffix_lines") or [],
            loaded=log.get("loaded") if log else None)

    def _remove_entry_row(self, habit: dict[str, Any], day: str) -> None:
        assert self.vault_dir is not None
        month = day[:7]
        path = habit_markdown.logs_dir(self.vault_dir) / f"{month}.md"
        log = habit_markdown.read_log(path)
        rows = []
        for raw in log["rows"]:
            row = habit_markdown.normalize_log_row(raw)
            if not row or (row.get("uid") == habit["uid"] and row.get("date") == day):
                continue
            rows.append(row)
        self._write_log_file(month, rows, loaded=log)

    # ----- queries -----

    def _require_row(self, habit_id: int, *, include_missing: bool = False) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM habit_definitions WHERE id=? AND deleted=0", (habit_id,)).fetchone()
        if row is None or (not include_missing and row["missing"]):
            raise HTTPException(404, "习惯不存在")
        return dict(row)

    def _find_uid(self, uid: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM habit_definitions WHERE uid=? AND deleted=0", (uid,)).fetchone()
        return dict(row) if row else None

    def _all_entries(self, habit_ids: list[int]) -> dict[int, dict[str, dict[str, Any]]]:
        result: dict[int, dict[str, dict[str, Any]]] = {habit_id: {} for habit_id in habit_ids}
        if not habit_ids:
            return result
        placeholders = ",".join("?" for _ in habit_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM habit_entries WHERE habit_id IN ({placeholders})",
                tuple(habit_ids)).fetchall()
        for row in rows:
            habit_id = int(row["habit_id"])
            result.setdefault(habit_id, {})[str(row["date"])] = dict(row)
        return result

    def list(self, status: HabitStatus | None = None) -> list[dict[str, Any]]:
        self.sync()
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM habit_definitions
                WHERE deleted=0 AND missing=0 AND (? IS NULL OR status=?)
                ORDER BY sort_order,id""", (status, status)).fetchall()
        return [self._row_to_habit(row) for row in rows]

    def get(self, habit_id: int) -> dict[str, Any]:
        self.sync()
        return self._row_to_habit(self._require_row(habit_id))

    def _build_habit(self, payload: HabitCreate, *, uid: str, habit_id: int,
                     sort_order: float) -> dict[str, Any]:
        frequency = payload.frequency or {"kind": "daily"}
        now = shanghai_now_iso()
        return {
            "id": habit_id, "uid": uid, "title": payload.title,
            "cue": payload.cue, "minimum_action": payload.minimum_action,
            "start_date": validate_day(payload.start_date, allow_future=True).isoformat(),
            "frequency": frequency, "weekdays": habit_schedule.weekdays_for(frequency),
            "goal": "binary", "target_days": _validate_target_days(payload.target_days),
            "icon": payload.icon or DEFAULT_ICON, "color": payload.color,
            "group": payload.group or DEFAULT_GROUP, "reminder": payload.reminder,
            "auto_log": bool(payload.auto_log), "priority": payload.priority,
            "sort_order": sort_order, "status": "active", "path": "",
            "created_at": now, "updated_at": now,
        }

    def create(self, payload: HabitCreate) -> dict[str, Any]:
        self._require_write("新增习惯")
        self.sync()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            next_id = int(connection.execute(
                "SELECT COALESCE(MAX(id),0)+1 FROM habit_definitions").fetchone()[0])
            max_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order),0) FROM habit_definitions").fetchone()[0]
        sort_order = float(payload.sort_order) if payload.sort_order is not None else float(max_order) + 1000
        habit = self._build_habit(payload, uid=_new_uid(), habit_id=next_id, sort_order=sort_order)
        if self.vault_dir is not None:
            path = habit_markdown.unique_note_path(self.vault_dir, habit["title"])
            habit_markdown.write_note(path, self._definition_metadata(habit), [])
            self._invalidate_snapshot()
            self.sync(force=True)
            row = self._find_uid(habit["uid"])
            if row is None:
                raise HTTPException(500, "习惯已写入 Markdown，但缓存同步失败，请刷新后重试")
            result = self._row_to_habit(row)
        else:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT INTO habit_definitions
                    (id,uid,title,cue,minimum_action,start_date,weekdays_json,frequency_json,
                     goal,target_days,icon,color,group_name,reminder,auto_log,priority,
                     sort_order,status,source_path,content_hash,missing,deleted,revision,
                     created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,1,?,?)""",
                    (habit["id"], habit["uid"], habit["title"], habit["cue"],
                     habit["minimum_action"], habit["start_date"],
                     json.dumps(habit["weekdays"], ensure_ascii=False),
                     json.dumps(habit["frequency"], ensure_ascii=False), "binary",
                     habit["target_days"], habit["icon"], habit["color"], habit["group"],
                     habit["reminder"], _bool_int(habit["auto_log"]), habit["priority"],
                     habit["sort_order"], habit["status"], "", "", utc_now(), utc_now()))
            result = self.get(next_id)
        self._audit("habit_created", {"habit_id": result["id"], "uid": result["uid"]})
        return result

    def _merge_update(self, current: dict[str, Any], payload: HabitUpdate) -> dict[str, Any]:
        changes = payload.model_dump(exclude_unset=True, exclude={"revision"})
        frequency = current["frequency"]
        if "frequency" in changes or "weekdays" in changes:
            frequency = payload.frequency or habit_schedule.normalize_frequency(
                None, changes.get("weekdays"))
            changes["frequency"] = frequency
        changes.pop("weekdays", None)
        result = dict(current)
        for key in ("title", "cue", "minimum_action", "icon", "color", "group",
                    "reminder", "auto_log", "priority", "status", "sort_order"):
            if key in changes:
                result[key] = changes[key]
        if "start_date" in changes:
            result["start_date"] = validate_day(changes["start_date"], allow_future=True).isoformat()
        if "frequency" in changes:
            result["frequency"] = frequency
            result["weekdays"] = habit_schedule.weekdays_for(frequency)
        if "target_days" in changes:
            result["target_days"] = _validate_target_days(changes["target_days"])
        if not result.get("icon"):
            result["icon"] = DEFAULT_ICON
        if not result.get("group"):
            result["group"] = DEFAULT_GROUP
        if result["status"] not in ("active", "archived"):
            result["status"] = "active"
        return result

    def _write_cached_update(self, habit_id: int, habit: dict[str, Any], *,
                             revision_delta: int = 1) -> None:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE habit_definitions SET title=?,cue=?,minimum_action=?,start_date=?,
                weekdays_json=?,frequency_json=?,goal=?,target_days=?,icon=?,color=?,
                group_name=?,reminder=?,auto_log=?,priority=?,sort_order=?,status=?,
                content_hash=?,revision=revision+?,updated_at=? WHERE id=?""",
                (habit["title"], habit["cue"], habit["minimum_action"], habit["start_date"],
                 json.dumps(habit["weekdays"], ensure_ascii=False),
                 json.dumps(habit["frequency"], ensure_ascii=False), "binary",
                 habit["target_days"], habit["icon"], habit["color"], habit["group"],
                 habit["reminder"], _bool_int(habit["auto_log"]), habit["priority"],
                 float(habit["sort_order"]), habit["status"],
                 habit_markdown.content_hash(_semantic_definition(habit)),
                 revision_delta, utc_now(), habit_id))
            _bump_generation(connection)

    def update(self, habit_id: int, payload: HabitUpdate) -> dict[str, Any]:
        self._require_write("修改习惯")
        self.sync()
        current_row = self._require_row(habit_id, include_missing=True)
        if current_row["missing"]:
            raise HTTPException(409, "习惯笔记不存在，不能修改；请先在 Obsidian 恢复笔记")
        current = self._row_to_habit(current_row)
        if current["revision"] != payload.revision:
            raise HTTPException(409, "习惯已发生变化，请保留草稿并刷新后重试")
        merged = self._merge_update(current, payload)
        if habit_markdown.content_hash(_semantic_definition(merged)) == current_row["content_hash"]:
            return current
        new_target = merged["target_days"]
        if new_target:
            end = habit_schedule.target_end_date(merged["start_date"], new_target)
            with self.database.connect() as connection:
                latest = connection.execute(
                    "SELECT MAX(date) FROM habit_entries WHERE habit_id=?", (habit_id,)).fetchone()[0]
            if end and latest and str(latest) > end.isoformat():
                raise HTTPException(422, "新的坚持天数早于已有打卡记录，请先导出或归档该习惯")
        if self.vault_dir is not None:
            source = current.get("path") or ""
            path = self.vault_dir / source
            if not source or not path.is_file():
                raise HTTPException(409, "习惯笔记不存在，不能修改；请先在 Obsidian 恢复笔记")
            note = habit_markdown.read_note(path)
            metadata = dict(note["metadata"])
            metadata.pop("weekdays", None)
            metadata.update(self._definition_metadata(merged))
            habit_markdown.write_note(path, metadata, note["body_lines"], loaded=note["loaded"])
            if merged["title"] != current["title"]:
                new_path = habit_markdown.unique_note_path(
                    self.vault_dir, merged["title"], existing=path, old_title=current["title"])
                if new_path != path:
                    os.replace(path, new_path)
            self._invalidate_snapshot()
            self.sync(force=True)
            result = self._row_to_habit(self._require_row(habit_id))
        else:
            self._write_cached_update(habit_id, merged)
            result = self._row_to_habit(self._require_row(habit_id))
        self._audit("habit_updated", {"habit_id": habit_id, "revision": result["revision"]})
        return result

    def delete(self, habit_id: int, payload: HabitDelete) -> dict[str, Any]:
        self._require_write("删除习惯")
        self.sync()
        row = self._require_row(habit_id, include_missing=True)
        current = self._row_to_habit(row)
        if current["revision"] != payload.revision:
            raise HTTPException(409, "习惯已发生变化，请刷新后重试")
        with self.database.connect() as connection:
            entries = [dict(item) for item in connection.execute(
                "SELECT * FROM habit_entries WHERE habit_id=? ORDER BY date", (habit_id,)).fetchall()]
        trash_path = ""
        if self.vault_dir is not None:
            stamp = datetime.now(SHANGHAI).strftime("%Y%m%dT%H%M%S")
            trash = self.vault_dir / ".torch-os" / "habit-trash" / f"{stamp}-{current['uid']}"
            trash.mkdir(parents=True, exist_ok=True)
            (trash / "entries.json").write_text(
                json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
            (trash / "manifest.json").write_text(json.dumps({
                "habit": current, "deleted_at": shanghai_now_iso(),
                "entries": len(entries),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            source = current.get("path") or ""
            note_path = self.vault_dir / source if source else None
            if note_path and note_path.is_file():
                shutil.copy2(note_path, trash / "habit.md")
                note_path.unlink()
            months = {str(entry.get("date") or "")[:7] for entry in entries}
            for month in sorted(month for month in months if re.fullmatch(r"\d{4}-\d{2}", month)):
                log_path = habit_markdown.logs_dir(self.vault_dir) / f"{month}.md"
                if log_path.is_file():
                    shutil.copy2(log_path, trash / f"{month}.md")
                log = habit_markdown.read_log(log_path)
                rows = []
                for raw in log["rows"]:
                    normalized = habit_markdown.normalize_log_row(raw)
                    if not normalized or normalized.get("uid") == current["uid"]:
                        continue
                    rows.append(normalized)
                self._write_log_file(month, rows, loaded=log)
            trash_path = str(trash.relative_to(self.vault_dir).as_posix())
            self._invalidate_snapshot()
            self.sync(force=True)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM habit_entries WHERE habit_id=?", (habit_id,))
            connection.execute("DELETE FROM habit_definitions WHERE id=?", (habit_id,))
            _bump_generation(connection)
        self._audit("habit_deleted", {"habit_id": habit_id, "entries": len(entries)})
        return {"deleted": True, "trash_path": trash_path, "entries": len(entries)}

    def reorder(self, payload: HabitReorder) -> dict[str, Any]:
        self._require_write("调整习惯顺序")
        self.sync()
        with self.database.connect() as connection:
            if payload.revision:
                current_revision = _habit_revision(connection)
                if payload.revision != current_revision:
                    raise HTTPException(409, "习惯列表已发生变化，请刷新后重试")
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM habit_definitions WHERE deleted=0 AND missing=0 "
                "AND status='active' ORDER BY sort_order,id").fetchall()]
        by_id = {int(row["id"]): row for row in rows}
        moved = by_id.get(payload.moved_id)
        if moved is None:
            raise HTTPException(404, "习惯不存在或已归档")
        if payload.before_id and payload.before_id not in by_id:
            raise HTTPException(404, "目标习惯不存在")
        ordered = [row for row in rows if int(row["id"]) != payload.moved_id]
        if payload.before_id:
            index = next((index for index, row in enumerate(ordered)
                          if int(row["id"]) == payload.before_id), len(ordered))
        else:
            index = len(ordered)
        previous_order = float(ordered[index - 1]["sort_order"]) if index > 0 else None
        next_order = float(ordered[index]["sort_order"]) if index < len(ordered) else None
        if previous_order is None and next_order is None:
            new_order = 1000.0
        elif previous_order is None:
            new_order = next_order - 1000.0
        elif next_order is None:
            new_order = previous_order + 1000.0
        elif next_order - previous_order > 1:
            new_order = (previous_order + next_order) / 2
        else:
            new_order = None
        changes: dict[int, float] = {}
        if new_order is None:
            for offset, row in enumerate([*ordered[:index], moved, *ordered[index:]]):
                changes[int(row["id"])] = float((offset + 1) * 1000)
        else:
            changes[payload.moved_id] = new_order
        for habit_id, sort_order in changes.items():
            habit = self._row_to_habit(by_id.get(habit_id) or moved)
            habit["sort_order"] = sort_order
            if self.vault_dir is not None:
                source = habit.get("path") or ""
                path = self.vault_dir / source
                if path.is_file():
                    note = habit_markdown.read_note(path)
                    metadata = dict(note["metadata"])
                    metadata.pop("weekdays", None)
                    metadata.update(self._definition_metadata(habit))
                    habit_markdown.write_note(path, metadata, note["body_lines"], loaded=note["loaded"])
            else:
                self._write_cached_update(habit_id, habit, revision_delta=1)
        if self.vault_dir is not None:
            self._invalidate_snapshot()
            self.sync(force=True)
        result = self.get(payload.moved_id)
        with self.database.connect() as connection:
            revision = _habit_revision(connection)
        self._audit("habit_reordered", {"habit_id": payload.moved_id,
                                        "before_id": payload.before_id})
        return {"reordered": True, "moved_id": payload.moved_id,
                "sort_order": result["sort_order"], "habits_revision": revision}

    # ----- entries -----

    def _entries_rows(self, habit_id: int, first: date, last: date) -> dict[str, dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM habit_entries WHERE habit_id=? AND date>=? AND date<=?",
                (habit_id, first.isoformat(), last.isoformat())).fetchall()
        return {str(row["date"]): dict(row) for row in rows}

    def entries(self, habit_id: int, from_date: str, to_date: str) -> list[dict[str, Any]]:
        first, last = _date_range(from_date, to_date)
        self.sync()
        row = self._require_row(habit_id)
        habit = self._row_to_habit(row)
        rows = self._entries_rows(habit_id, first, last)
        return [
            self._entry_dict(rows.get(day.isoformat()), habit_id, day.isoformat(),
                             habit_schedule.scheduled_on(habit, day))
            for day in (first + timedelta(days=offset)
                        for offset in range((last - first).days + 1))
        ]

    def summary(self, habit_id: int, from_date: str, to_date: str) -> dict[str, Any]:
        first, last = _date_range(from_date, to_date)
        self.sync()
        row = self._require_row(habit_id)
        habit = self._row_to_habit(row)
        rows = self._entries_rows(habit_id, first, last)
        all_entries = self._all_entries([habit_id]).get(habit_id, {})
        today = shanghai_today()
        stats = habit_stats.range_stats(habit, rows, first, last, today)
        week_start = last - timedelta(days=last.weekday())
        week_end = week_start + timedelta(days=6)
        week_rows = self._entries_rows(habit_id, min(first, week_start), max(last, week_end))
        week_stats = habit_stats.range_stats(habit, week_rows, week_start, week_end, today)
        remaining = sum(
            1 for day in (week_start + timedelta(days=offset) for offset in range(7))
            if day >= last and habit_schedule.scheduled_on(habit, day)
            and habit_stats._status(week_rows.get(day.isoformat())) == "unrecorded")
        entries = [
            self._entry_dict(rows.get(day.isoformat()), habit_id, day.isoformat(),
                             habit_schedule.scheduled_on(habit, day))
            for day in (first + timedelta(days=offset)
                        for offset in range((last - first).days + 1))
        ]
        streak = habit_stats.streak_stats(habit, all_entries, today)
        return {
            "habit_id": habit_id, "from_date": from_date, "to_date": to_date,
            "scheduled_days": stats["scheduled_days"],
            "done": stats["done"], "skipped": stats["skipped"],
            "unrecorded": stats["unrecorded"], "extra_done": stats["extra_done"],
            "completion_rate": stats["completion_rate"],
            "total_done": habit_stats.habit_total_done(all_entries),
            **streak,
            "entries": entries,
            "week": {
                "start": week_start.isoformat(), "end": week_end.isoformat(),
                "scheduled": week_stats["scheduled_days"],
                "done": week_stats["done"], "skipped": week_stats["skipped"],
                "unrecorded": week_stats["unrecorded"], "remaining": remaining,
                "note": "“还剩”指从所选日期起到本周日之间仍未记录的计划日；未记录不等于必须完成。",
            },
            "warning": "未记录不代表失败。完成率为已完成÷（计划天数−明确跳过），分母为 0 时不计算。",
        }

    def _entry_for(self, habit_id: int, day: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM habit_entries WHERE habit_id=? AND date=?",
                (habit_id, day)).fetchone()
        return dict(row) if row else None

    def _write_entry(self, habit_id: int, day: str, payload: HabitEntryWrite) -> dict[str, Any]:
        target = validate_day(day)
        self.sync()
        row = self._require_row(habit_id)
        habit = self._row_to_habit(row)
        scheduled = habit_schedule.scheduled_on(habit, target)
        current = self._entry_for(habit_id, day)
        if current is None and not scheduled:
            raise HTTPException(422, "此日期不在习惯计划内，无需打卡")
        if current is None and habit["status"] == "archived":
            raise HTTPException(409, "习惯已归档，不能新增记录；已有历史记录仍可纠正")
        if payload.revision != (int(current["revision"]) if current else 0):
            raise HTTPException(409, "当天记录已发生变化，请保留草稿并刷新后重试")
        note = payload.note or ""
        status = payload.status
        if current and str(current["status"]) == status and str(current["note"] or "") == note:
            return self._entry_dict(current, habit_id, day, scheduled)
        if status == "unrecorded" and not note and current is None:
            return self._entry_dict(None, habit_id, day, scheduled)
        # An explicit undo keeps a revision-guarded unrecorded row.  Deleting the
        # row would reset its revision to 0 and let a stale client silently
        # re-check the day; keeping the row preserves optimistic locking.
        if self.vault_dir is not None:
            path = habit_markdown.logs_dir(self.vault_dir) / f"{target.strftime('%Y-%m')}.md"
            log = habit_markdown.read_log(path)
            rows = []
            for raw in log["rows"]:
                normalized = habit_markdown.normalize_log_row(raw)
                if not normalized or (normalized.get("uid") == habit["uid"]
                                       and normalized.get("date") == day):
                    continue
                rows.append(normalized)
            rows.append({
                "date": day, "uid": habit["uid"], "title": habit["title"],
                "status": status, "note": note, "updated": shanghai_now_iso(),
            })
            self._write_log_file(target.strftime("%Y-%m"), rows, loaded=log)
            self._invalidate_snapshot()
            self.sync(force=True)
        else:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if current:
                    connection.execute(
                        """UPDATE habit_entries SET status=?,note=?,revision=revision+1,
                        updated_at=? WHERE habit_id=? AND date=?""",
                        (status, note, shanghai_now_iso(), habit_id, day))
                else:
                    connection.execute(
                        """INSERT INTO habit_entries(habit_id,date,status,note,revision,updated_at)
                        VALUES (?,?,?,?,1,?)""",
                        (habit_id, day, status, note, shanghai_now_iso()))
                _bump_generation(connection)
        result = self._entry_dict(self._entry_for(habit_id, day), habit_id, day, scheduled)
        self._audit("habit_entry_saved", {
            "habit_id": habit_id, "date": day,
            "previous_revision": int(current["revision"]) if current else 0,
            "revision": result["revision"],
            "previous_status": str(current["status"]) if current else "unrecorded",
            "status": result["status"],
        })
        return result

    def toggle_entry(self, habit_id: int, payload: HabitToggle) -> dict[str, Any]:
        self._require_write("打卡")
        with _lock_for(self._key):
            day = payload.day or shanghai_today().isoformat()
            target = validate_day(day)
            self.sync()
            habit = self._row_to_habit(self._require_row(habit_id))
            current = self._entry_for(habit_id, day)
            revision = int(current["revision"]) if current else 0
            if payload.revision != revision:
                raise HTTPException(409, "当天记录已发生变化，请刷新后重试")
            if payload.done:
                status: EntryStatus = "done"
                note = str(current.get("note") or "") if current else ""
            else:
                status = "unrecorded"
                note = str(current.get("note") or "") if current else ""
                if not note and current is None:
                    return self._entry_dict(None, habit_id, day,
                                            habit_schedule.scheduled_on(habit, target))
            return self._write_entry(habit_id, day, HabitEntryWrite(
                revision=revision, status=status, note=note))

    def save_entry(self, habit_id: int, day: str, payload: HabitEntryWrite) -> dict[str, Any]:
        self._require_write("保存打卡记录")
        with _lock_for(self._key):
            return self._write_entry(habit_id, day, payload)

    # ----- overview / board -----

    def overview(self, month: str, *, include_archived: bool = False) -> dict[str, Any]:
        month = validate_month(month)
        self.sync()
        today = shanghai_today()
        month_start, month_end = _month_bounds(month)
        with self.database.connect() as connection:
            statuses = ("active", "archived") if include_archived else ("active",)
            rows = connection.execute(
                f"""SELECT * FROM habit_definitions WHERE deleted=0 AND missing=0
                AND status IN ({','.join('?' for _ in statuses)})
                ORDER BY sort_order,id""", statuses).fetchall()
            revision = _habit_revision(connection)
        habits = [self._row_to_habit(row) for row in rows]
        entries_by_habit = self._all_entries([habit["id"] for habit in habits])
        groups: dict[str, int] = {}
        for habit in habits:
            groups[habit["group"]] = groups.get(habit["group"], 0) + 1
        today_day = today
        week_start = today_day - timedelta(days=6)
        week = habit_stats.week_strip(habits, entries_by_habit, week_start, today_day, today_day)
        result_habits = []
        logs: list[dict[str, Any]] = []
        for habit in habits:
            entries_all = entries_by_habit.get(habit["id"], {})
            month_entries = []
            for day in (month_start + timedelta(days=offset)
                        for offset in range((month_end - month_start).days + 1)):
                entry = entries_all.get(day.isoformat())
                month_entries.append(self._entry_dict(
                    entry, habit["id"], day.isoformat(),
                    habit_schedule.scheduled_on(habit, day)))
            entry_today = self._entry_dict(
                entries_all.get(today.isoformat()), habit["id"], today.isoformat(),
                habit_schedule.scheduled_on(habit, today))
            stats = {
                **habit_stats.month_stats(habit, entries_all, month, today),
                "total_done": habit_stats.habit_total_done(entries_all),
                **habit_stats.streak_stats(habit, entries_all, today),
                **habit_stats.target_stats(habit, today),
            }
            result_habits.append({
                **habit,
                "scheduled_today": bool(
                    habit["status"] == "active" and not habit["ended"]
                    and habit_schedule.scheduled_on(habit, today)),
                "entry_today": entry_today,
                "stats": stats,
                "entries": month_entries,
            })
            for day in (month_start + timedelta(days=offset)
                        for offset in range((month_end - month_start).days + 1)):
                entry = entries_all.get(day.isoformat())
                if not entry:
                    continue
                status = str(entry.get("status") or "unrecorded")
                note = str(entry.get("note") or "")
                if status not in ("done", "skipped") and not note:
                    continue
                logs.append({
                    "habit_id": habit["id"], "habit_uid": habit["uid"],
                    "habit_title": habit["title"], "icon": habit["icon"],
                    "date": day.isoformat(),
                    "scheduled": habit_schedule.scheduled_on(habit, day),
                    "status": status, "note": note,
                    "revision": int(entry.get("revision") or 0),
                    "updated_at": entry.get("updated_at"),
                    "sort_order": habit["sort_order"],
                })
        logs.sort(key=lambda item: (item["date"], -float(item["sort_order"])), reverse=True)
        return {
            "today": today.isoformat(),
            "month": month,
            "month_start": month_start.isoformat(),
            "month_end": month_end.isoformat(),
            "vault_name": self.vault_dir.name if self.vault_dir else "",
            "habits_revision": revision,
            "groups": [{"name": name, "count": count} for name, count in sorted(groups.items())],
            "week": week,
            "habits": result_habits,
            "logs": logs,
            "warnings": list(self._warnings),
        }

    def board_day(self, day: str) -> dict[str, Any]:
        target = validate_day(day)
        self.sync()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM habit_definitions WHERE deleted=0 AND missing=0 "
                "AND status='active' ORDER BY sort_order,id").fetchall()
            revision = _habit_revision(connection)
        habits = [self._row_to_habit(row) for row in rows]
        entries_by_habit = self._all_entries([habit["id"] for habit in habits])
        tasks = []
        for habit in habits:
            if not habit_schedule.scheduled_on(habit, target):
                continue
            entry = entries_by_habit.get(habit["id"], {}).get(target.isoformat())
            status = str(entry.get("status") or "unrecorded") if entry else "unrecorded"
            streak = habit_stats.streak_stats(
                habit, entries_by_habit.get(habit["id"], {}), target)
            tasks.append({
                "key": f"habit:{habit['uid']}:{target.isoformat()}",
                "path": habit["path"],
                "legacy": False,
                "kind": "habit",
                "virtual": True,
                "habit_id": habit["id"],
                "habit_uid": habit["uid"],
                "title": habit["title"],
                "icon": habit["icon"],
                "color": habit["color"],
                "list_name": HABIT_LIST_NAME,
                "done": status in DONE_STATES,
                "entry_status": status,
                "done_on": target.isoformat() if status == "done" else "",
                "date": target.isoformat(),
                "time": "",
                "priority": habit["priority"],
                "tags": [],
                "pinned": False,
                "created": habit["start_date"],
                "definition_revision": habit["revision"],
                "entry_revision": int(entry.get("revision") or 0) if entry else 0,
                "entry_note": str(entry.get("note") or "") if entry else "",
                "frequency_label": habit["frequency_label"],
                "streak": streak["current_streak"],
                "auto_log": habit["auto_log"],
            })
        return {
            "day": target.isoformat(),
            "revision": f"{revision}:{target.isoformat()}",
            "tasks": tasks,
            "warnings": list(self._warnings),
        }

    def day_rows(self, day: str) -> list[dict[str, Any]]:
        """Active habits scheduled on ``day`` with their entry state (workbench use)."""
        target = validate_day(day)
        self.sync()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM habit_definitions WHERE deleted=0 AND missing=0 "
                "AND status='active' ORDER BY sort_order,id").fetchall()
        habits = [self._row_to_habit(row) for row in rows]
        entries_by_habit = self._all_entries([habit["id"] for habit in habits])
        result = []
        for habit in habits:
            if not habit_schedule.scheduled_on(habit, target):
                continue
            entry = entries_by_habit.get(habit["id"], {}).get(target.isoformat())
            status = str(entry.get("status") or "unrecorded") if entry else "unrecorded"
            result.append({
                "id": habit["id"],
                "title": habit["title"],
                "minimum_action": habit["minimum_action"],
                "icon": habit["icon"],
                "priority": habit["priority"],
                "frequency_label": habit["frequency_label"],
                "revision": int(entry.get("revision") or 0) if entry else 0,
                "planned": True,
                "state": status,
                "done": status in DONE_STATES,
                "note": str(entry.get("note") or "") if entry else "",
            })
        return result

    def range_counts(self, first: date, last: date) -> dict[str, int]:
        self.sync()
        with self.database.connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM habit_definitions WHERE deleted=0 AND missing=0 "
                "AND status='active'").fetchall()]
        habits = [self._row_to_habit(row) for row in rows]
        entries_by_habit = self._all_entries([habit["id"] for habit in habits])
        counts = {"done": 0, "skipped": 0, "unrecorded": 0}
        for habit in habits:
            entries = entries_by_habit.get(habit["id"], {})
            for day in (first + timedelta(days=offset)
                        for offset in range((last - first).days + 1)):
                if not habit_schedule.scheduled_on(habit, day):
                    continue
                status = habit_stats._status(entries.get(day.isoformat()))
                counts[status] += 1
        return counts

    def export_payload(self) -> dict[str, Any]:
        self.sync()
        with self.database.connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM habit_definitions WHERE deleted=0 ORDER BY id").fetchall()]
            entry_rows = [dict(row) for row in connection.execute(
                "SELECT * FROM habit_entries ORDER BY habit_id,date").fetchall()]
        entries_by_habit: dict[int, list[dict[str, Any]]] = {}
        for entry in entry_rows:
            entries_by_habit.setdefault(int(entry["habit_id"]), []).append(entry)
        habits = []
        for row in rows:
            habit = self._row_to_habit(row)
            habits.append({**habit, "entries": entries_by_habit.get(habit["id"], [])})
        return {"habits": habits, "entries": entry_rows}


HABIT_LIST_NAME = "习惯打卡"


def create_habits_router(database: Database, prefix: str = "/api",
                         vault_dir: Any = None, read_only: bool = False) -> APIRouter:
    service = HabitService(database, vault_dir, read_only=read_only)
    router = APIRouter(prefix=prefix, tags=["habits"])

    @router.get("/habits/overview")
    def overview(month: str = Query(default=""), include_archived: bool = Query(default=False)):
        target = month or shanghai_today().strftime("%Y-%m")
        return service.overview(target, include_archived=include_archived)

    @router.get("/habits")
    def list_habits(status: HabitStatus | None = Query(default=None)):
        return service.list(status)

    @router.post("/habits", status_code=201)
    def create_habit(payload: HabitCreate):
        return service.create(payload)

    @router.post("/habits/reorder")
    def reorder_habits(payload: HabitReorder):
        return service.reorder(payload)

    @router.get("/habits/{habit_id}")
    def get_habit(habit_id: HabitId):
        return service.get(habit_id)

    @router.patch("/habits/{habit_id}")
    def update_habit(habit_id: HabitId, payload: HabitUpdate):
        return service.update(habit_id, payload)

    @router.post("/habits/{habit_id}/toggle")
    def toggle_habit(habit_id: HabitId, payload: HabitToggle):
        return service.toggle_entry(habit_id, payload)

    @router.post("/habits/{habit_id}/delete")
    def delete_habit(habit_id: HabitId, payload: HabitDelete):
        return service.delete(habit_id, payload)

    @router.get("/habits/{habit_id}/entries")
    def get_entries(habit_id: HabitId, from_date: str = Query(), to_date: str = Query()):
        return service.entries(habit_id, from_date, to_date)

    @router.put("/habits/{habit_id}/entries/{day}")
    def save_entry(habit_id: HabitId, day: str, payload: HabitEntryWrite):
        return service.save_entry(habit_id, day, payload)

    @router.get("/habits/{habit_id}/summary")
    def summarize(habit_id: HabitId, from_date: str = Query(), to_date: str = Query()):
        return service.summary(habit_id, from_date, to_date)

    return router


__all__ = [
    "HabitService", "HabitCreate", "HabitUpdate", "HabitEntryWrite", "HabitToggle",
    "HabitDelete", "HabitReorder", "create_habits_router", "shanghai_today",
    "HabitStatus", "EntryStatus", "HABIT_LIST_NAME",
]
