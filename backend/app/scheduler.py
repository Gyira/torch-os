"""Local morning/evening draft reports delivered to a private inbox.

Deterministic and offline: no model calls, no task or memory writes, and the
only side effects are a new inbox row plus one audit event per generation.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pathlib import Path as FilePath

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, field_validator

from . import journal_note
from .db import Database, utc_now


SHANGHAI = ZoneInfo("Asia/Shanghai")
ReportKind = Literal["morning", "evening", "weekly"]
InboxId = Annotated[int, Path(ge=1, le=9_223_372_036_854_775_807)]
TIME_PATTERN = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
DISCLAIMER = "这是本地生成的草稿，不会自动创建任务，也没有调用任何模型。"
WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduler_settings (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    morning_enabled INTEGER NOT NULL DEFAULT 1 CHECK(morning_enabled IN (0, 1)),
    morning_time TEXT NOT NULL DEFAULT '08:00',
    evening_enabled INTEGER NOT NULL DEFAULT 1 CHECK(evening_enabled IN (0, 1)),
    evening_time TEXT NOT NULL DEFAULT '21:30',
    weekly_enabled INTEGER NOT NULL DEFAULT 1 CHECK(weekly_enabled IN (0, 1)),
    weekly_day INTEGER NOT NULL DEFAULT 6 CHECK(weekly_day BETWEEN 0 AND 6),
    weekly_time TEXT NOT NULL DEFAULT '20:00',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inbox_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('morning', 'evening', 'weekly')),
    source_date TEXT NOT NULL,
    title TEXT NOT NULL,
    body_json TEXT NOT NULL,
    manual INTEGER NOT NULL DEFAULT 0 CHECK(manual IN (0, 1)),
    catch_up INTEGER NOT NULL DEFAULT 0 CHECK(catch_up IN (0, 1)),
    created_at TEXT NOT NULL,
    read_at TEXT,
    dismissed_at TEXT,
    UNIQUE(kind, source_date)
);
CREATE INDEX IF NOT EXISTS idx_inbox_items_created ON inbox_items(created_at DESC, id DESC);
"""


class ScheduleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    morning_enabled: StrictBool
    morning_time: str
    evening_enabled: StrictBool
    evening_time: str
    weekly_enabled: StrictBool
    weekly_day: StrictInt
    weekly_time: str

    @field_validator("morning_time", "evening_time", "weekly_time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        if not isinstance(value, str) or not TIME_PATTERN.fullmatch(value):
            raise ValueError("时间必须是 HH:MM（00:00–23:59）")
        return value

    @field_validator("weekly_day")
    @classmethod
    def valid_weekday(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 6:
            raise ValueError("周报日期必须是 0（周一）到 6（周日）")
        return value


class RunNowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ReportKind


def _settings(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["morning_enabled"] = bool(item["morning_enabled"])
    item["evening_enabled"] = bool(item["evening_enabled"])
    item["weekly_enabled"] = bool(item["weekly_enabled"])
    return item


def _item(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["body"] = json.loads(result.pop("body_json"))
    result["manual"] = bool(result["manual"])
    result["catch_up"] = bool(result["catch_up"])
    result["unread"] = result["read_at"] is None and result["dismissed_at"] is None
    return result


def _audit(connection, details: dict[str, Any]) -> None:
    # Only identifiers and flags, never the generated body text.
    connection.execute(
        "INSERT INTO audit_events(event_type, detail_json, created_at) VALUES (?,?,?)",
        ("scheduler_report_created", json.dumps(details, ensure_ascii=False), utc_now()),
    )


class SchedulerService:
    def __init__(self, database: Database, vault_dir: FilePath | str | None = None):
        self.database = database
        self.vault_dir = FilePath(vault_dir) if vault_dir else None
        self.initialize()

    def initialize(self) -> None:
        with self.database.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)
            connection.execute(
                "INSERT OR IGNORE INTO scheduler_settings(id, updated_at) VALUES (1, ?)",
                (utc_now(),),
            )

    @staticmethod
    def _migrate(connection) -> None:
        """Additive migration for 0.5.1 databases; 0.8 adds the weekly digest."""
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(scheduler_settings)")}
        for name, declaration in (
            ("weekly_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("weekly_day", "INTEGER NOT NULL DEFAULT 6"),
            ("weekly_time", "TEXT NOT NULL DEFAULT '20:00'"),
        ):
            if name not in columns:
                connection.execute(f"ALTER TABLE scheduler_settings ADD COLUMN {name} {declaration}")
        table = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='inbox_items'"
        ).fetchone()
        if table and table["sql"] and "'weekly'" not in table["sql"]:
            # SQLite cannot widen a CHECK constraint; rebuild while keeping ids,
            # read/dismiss timestamps and the unique (kind, source_date) rule.
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE inbox_items_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL CHECK(kind IN ('morning', 'evening', 'weekly')),
                    source_date TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    manual INTEGER NOT NULL DEFAULT 0 CHECK(manual IN (0, 1)),
                    catch_up INTEGER NOT NULL DEFAULT 0 CHECK(catch_up IN (0, 1)),
                    created_at TEXT NOT NULL,
                    read_at TEXT,
                    dismissed_at TEXT,
                    UNIQUE(kind, source_date)
                );
                INSERT INTO inbox_items_new(id,kind,source_date,title,body_json,manual,catch_up,created_at,read_at,dismissed_at)
                    SELECT id,kind,source_date,title,body_json,manual,catch_up,created_at,read_at,dismissed_at FROM inbox_items;
                DROP TABLE inbox_items;
                ALTER TABLE inbox_items_new RENAME TO inbox_items;
                CREATE INDEX IF NOT EXISTS idx_inbox_items_created ON inbox_items(created_at DESC, id DESC);
                COMMIT;
                """
            )

    def get_settings(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM scheduler_settings WHERE id=1").fetchone()
        return _settings(row)

    def update_settings(self, payload: ScheduleSettings) -> dict[str, Any]:
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE scheduler_settings SET morning_enabled=?, morning_time=?,
                evening_enabled=?, evening_time=?, weekly_enabled=?, weekly_day=?,
                weekly_time=?, updated_at=? WHERE id=1""",
                (
                    int(payload.morning_enabled), payload.morning_time,
                    int(payload.evening_enabled), payload.evening_time,
                    int(payload.weekly_enabled), payload.weekly_day, payload.weekly_time,
                    utc_now(),
                ),
            )
            row = connection.execute("SELECT * FROM scheduler_settings WHERE id=1").fetchone()
        return _settings(row)

    @staticmethod
    def _has_table(connection, name: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    def _morning_body(self, local: datetime) -> dict[str, Any]:
        today = local.date()
        yesterday = (today - timedelta(days=1)).isoformat()
        week_start = (today - timedelta(days=today.weekday())).isoformat()
        week_end = (today - timedelta(days=today.weekday() - 6)).isoformat()
        day = today.isoformat()
        with self.database.connect() as connection:
            goals = []
            if self._has_table(connection, "goals"):
                goals = [dict(row) for row in connection.execute(
                    "SELECT id,title,status,progress FROM goals WHERE status='active' ORDER BY updated_at DESC, id LIMIT 3"
                ).fetchall()]
            open_tasks = []
            if self._has_table(connection, "tasks"):
                open_tasks = [dict(row) for row in connection.execute(
                    """SELECT title,priority,due_date FROM tasks
                    WHERE status IN ('todo','in_progress') AND due_date IS NOT NULL AND due_date<=?
                    ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                    due_date, id LIMIT 5""",
                    (day,),
                ).fetchall()]
            journal_yesterday = False
            if self.vault_dir is not None:
                journal_yesterday = journal_note.note_abspath(
                    self.vault_dir, yesterday
                ).is_file()
            elif self._has_table(connection, "journal_entries"):
                journal_yesterday = connection.execute(
                    "SELECT 1 FROM journal_entries WHERE date=?", (yesterday,)
                ).fetchone() is not None
            minutes = target = 0
            if self._has_table(connection, "study_sessions") and self._has_table(connection, "study_subjects"):
                minutes = connection.execute(
                    "SELECT COALESCE(SUM(minutes),0) FROM study_sessions WHERE study_date>=? AND study_date<=?",
                    (week_start, week_end),
                ).fetchone()[0]
                target = connection.execute(
                    "SELECT COALESCE(SUM(weekly_target_minutes),0) FROM study_subjects WHERE status='active'"
                ).fetchone()[0]
        return {
            "date": day,
            "goals": goals,
            "open_tasks": open_tasks,
            "journal_yesterday_recorded": journal_yesterday,
            "study_week": {"week_start": week_start, "minutes": int(minutes), "target_minutes": int(target)},
            "disclaimer": DISCLAIMER,
        }

    def _evening_body(self, local: datetime) -> dict[str, Any]:
        today = local.date()
        day = today.isoformat()
        with self.database.connect() as connection:
            completed = 0
            completed_rows = []
            overdue = 0
            if self._has_table(connection, "tasks"):
                completed_rows = connection.execute(
                    "SELECT completed_at FROM tasks WHERE status='completed' AND completed_at IS NOT NULL"
                ).fetchall()
                overdue = connection.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress') AND due_date IS NOT NULL AND due_date<=?",
                    (day,),
                ).fetchone()[0]
            for row in completed_rows:
                try:
                    if datetime.fromisoformat(row["completed_at"]).astimezone(SHANGHAI).date() == today:
                        completed += 1
                except (TypeError, ValueError):
                    continue
            study_minutes = 0
            study_subjects: list[dict[str, Any]] = []
            if self._has_table(connection, "study_sessions"):
                study_minutes = connection.execute(
                    "SELECT COALESCE(SUM(minutes),0) FROM study_sessions WHERE study_date=?", (day,)
                ).fetchone()[0]
                if self._has_table(connection, "study_subjects"):
                    study_subjects = [dict(row) for row in connection.execute(
                        """SELECT sub.title AS title, SUM(s.minutes) AS minutes
                        FROM study_sessions s JOIN study_subjects sub ON sub.id=s.subject_id
                        WHERE s.study_date=? GROUP BY sub.id ORDER BY minutes DESC, sub.id""",
                        (day,),
                    ).fetchall()]
            habits = {"done": 0, "skipped": 0, "unrecorded": 0}
            if self._has_table(connection, "habit_definitions") and self._has_table(connection, "habit_entries"):
                scheduled = []
                for row in connection.execute(
                    "SELECT id,weekdays_json,start_date FROM habit_definitions WHERE status='active'"
                ).fetchall():
                    try:
                        weekdays = json.loads(row["weekdays_json"])
                    except (TypeError, ValueError):
                        continue
                    if row["start_date"] <= day and today.weekday() in weekdays:
                        scheduled.append(row["id"])
                if scheduled:
                    marks = {row["habit_id"]: row["status"] for row in connection.execute(
                        "SELECT habit_id,status FROM habit_entries WHERE date=?", (day,)
                    ).fetchall()}
                    for habit_id in scheduled:
                        status = marks.get(habit_id, "unrecorded")
                        if status == "done":
                            habits["done"] += 1
                        elif status == "skipped":
                            habits["skipped"] += 1
                        else:
                            habits["unrecorded"] += 1
            journal_recorded = False
            next_action = ""
            if self.vault_dir is not None:
                note = journal_note.read_note(self.vault_dir, day)
                journal_recorded = bool(note.get("exists"))
                next_action = journal_note.next_action_text(note.get("content") or "")
            elif self._has_table(connection, "journal_entries"):
                row = connection.execute(
                    "SELECT next_action FROM journal_entries WHERE date=?", (day,)
                ).fetchone()
                journal_recorded = row is not None
                next_action = (row["next_action"] or "") if row else ""
            body_records = None
            if self._has_table(connection, "body_entries"):
                row = connection.execute(
                    "SELECT weight_kg,sleep_minutes,exercise_minutes FROM body_entries WHERE date=?", (day,)
                ).fetchone()
                if row is not None:
                    body_records = {
                        "weight_kg": row["weight_kg"],
                        "sleep_minutes": row["sleep_minutes"],
                        "exercise_minutes": row["exercise_minutes"],
                    }
        return {
            "date": day,
            "completed_tasks": int(completed),
            "open_overdue_tasks": int(overdue),
            "study_today": {"minutes": int(study_minutes), "subjects": study_subjects},
            "habits": habits,
            "journal": {"recorded": journal_recorded, "next_action": next_action},
            "body_records": body_records,
            "reflection_prompts": ["今天有哪些进展值得保留？", "遇到了哪些具体阻碍，需要什么帮助？", "明天最小且可执行的行动是什么？"],
            "disclaimer": DISCLAIMER,
        }

    def _weekly_body(self, week_end: date) -> dict[str, Any]:
        """Rolling seven days ending on the scheduled day (no scoring)."""
        week_start = week_end - timedelta(days=6)
        start, end = week_start.isoformat(), week_end.isoformat()
        with self.database.connect() as connection:
            completed_tasks = 0
            completed_examples: list[str] = []
            overdue = 0
            overdue_tasks: list[dict[str, Any]] = []
            if self._has_table(connection, "tasks"):
                for row in connection.execute(
                    """SELECT title,completed_at FROM tasks WHERE status='completed'
                    AND completed_at IS NOT NULL ORDER BY completed_at DESC, id DESC"""
                ).fetchall():
                    try:
                        finished = datetime.fromisoformat(row["completed_at"]).astimezone(SHANGHAI).date()
                    except (TypeError, ValueError):
                        continue
                    if week_start <= finished <= week_end:
                        completed_tasks += 1
                        if len(completed_examples) < 3:
                            completed_examples.append(row["title"])
                overdue = connection.execute(
                    """SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress')
                    AND due_date IS NOT NULL AND due_date<=?""",
                    (end,),
                ).fetchone()[0]
                overdue_tasks = [dict(row) for row in connection.execute(
                    """SELECT title,priority,due_date FROM tasks
                    WHERE status IN ('todo','in_progress') AND due_date IS NOT NULL AND due_date<=?
                    ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                    due_date, id LIMIT 5""",
                    (end,),
                ).fetchall()]
            study_minutes = 0
            study_subjects: list[dict[str, Any]] = []
            if self._has_table(connection, "study_sessions"):
                study_minutes = connection.execute(
                    "SELECT COALESCE(SUM(minutes),0) FROM study_sessions WHERE study_date>=? AND study_date<=?",
                    (start, end),
                ).fetchone()[0]
                if self._has_table(connection, "study_subjects"):
                    study_subjects = [dict(row) for row in connection.execute(
                        """SELECT sub.title AS title, SUM(s.minutes) AS minutes
                        FROM study_sessions s JOIN study_subjects sub ON sub.id=s.subject_id
                        WHERE s.study_date>=? AND s.study_date<=? GROUP BY sub.id
                        ORDER BY minutes DESC, sub.id LIMIT 3""",
                        (start, end),
                    ).fetchall()]
            habits = {"done": 0, "skipped": 0, "unrecorded": 0}
            if self._has_table(connection, "habit_definitions") and self._has_table(connection, "habit_entries"):
                marks = {(row["habit_id"], row["date"]): row["status"] for row in connection.execute(
                    "SELECT habit_id,date,status FROM habit_entries WHERE date>=? AND date<=?",
                    (start, end),
                ).fetchall()}
                for row in connection.execute(
                    "SELECT id,weekdays_json,start_date FROM habit_definitions WHERE status='active'"
                ).fetchall():
                    try:
                        weekdays = json.loads(row["weekdays_json"])
                    except (TypeError, ValueError):
                        continue
                    for offset in range(7):
                        day = week_start + timedelta(days=offset)
                        if day.isoformat() < row["start_date"] or day.weekday() not in weekdays:
                            continue
                        status = marks.get((row["id"], day.isoformat()), "unrecorded")
                        if status == "done":
                            habits["done"] += 1
                        elif status == "skipped":
                            habits["skipped"] += 1
                        else:
                            habits["unrecorded"] += 1
            journal_days = 0
            if self.vault_dir is not None:
                for offset in range(7):
                    if journal_note.note_abspath(
                            self.vault_dir, (week_start + timedelta(days=offset)).isoformat()
                    ).is_file():
                        journal_days += 1
            elif self._has_table(connection, "journal_entries"):
                journal_days = connection.execute(
                    "SELECT COUNT(*) FROM journal_entries WHERE date>=? AND date<=?",
                    (start, end),
                ).fetchone()[0]
            body_days = 0
            if self._has_table(connection, "body_entries"):
                body_days = connection.execute(
                    "SELECT COUNT(*) FROM body_entries WHERE date>=? AND date<=?",
                    (start, end),
                ).fetchone()[0]
        return {
            "week_start": start,
            "week_end": end,
            "completed_tasks": int(completed_tasks),
            "completed_examples": completed_examples,
            "open_overdue_tasks": int(overdue),
            "overdue_tasks": overdue_tasks,
            "study_week": {"minutes": int(study_minutes), "subjects": study_subjects},
            "habits": habits,
            "journal_days": int(journal_days),
            "body_record_days": int(body_days),
            "reflection_prompts": ["这一周哪些行动值得保留？", "哪些计划没有落地，真实阻碍是什么？", "下一周最小的一步是什么？"],
            "disclaimer": DISCLAIMER,
        }

    @staticmethod
    def _weekly_due(local: datetime, settings: dict[str, Any]) -> datetime | None:
        """Scheduled time today, or ``None`` when today is not the weekly day.

        Like the morning/evening drafts, a missed whole day is not back-filled;
        only the same scheduled day catches up.
        """
        scheduled_day = int(settings["weekly_day"])
        scheduled = time.fromisoformat(settings["weekly_time"])
        if local.weekday() != scheduled_day or local.time() < scheduled:
            return None
        return local.replace(hour=scheduled.hour, minute=scheduled.minute, second=0, microsecond=0)

    def run_due(self, now: datetime | None = None, force_kind: ReportKind | None = None) -> dict[str, Any]:
        local = now or datetime.now(SHANGHAI)
        if local.tzinfo is None:
            local = local.replace(tzinfo=SHANGHAI)
        local = local.astimezone(SHANGHAI)
        day = local.date().isoformat()
        settings = self.get_settings()
        created: list[dict[str, Any]] = []
        existing: list[dict[str, Any]] = []
        kinds: list[str] = [force_kind] if force_kind else [
            kind for kind in ("morning", "evening", "weekly") if settings[f"{kind}_enabled"]
        ]
        for kind in kinds:
            catch_up = 0
            manual = 0
            if kind == "weekly":
                if force_kind:
                    manual = 1
                    source = local.date().isoformat()
                    body = self._weekly_body(local.date())
                else:
                    scheduled_at = self._weekly_due(local, settings)
                    if scheduled_at is None:
                        continue
                    catch_up = int(local - scheduled_at > timedelta(minutes=5))
                    source = local.date().isoformat()
                    body = self._weekly_body(local.date())
                title = "本周回顾草稿"
            else:
                if force_kind:
                    manual = 1
                else:
                    scheduled = time.fromisoformat(settings[f"{kind}_time"])
                    if local.time() < scheduled:
                        continue
                    scheduled_at = local.replace(hour=scheduled.hour, minute=scheduled.minute, second=0, microsecond=0)
                    catch_up = int(local - scheduled_at > timedelta(minutes=5))
                source = day
                body = self._morning_body(local) if kind == "morning" else self._evening_body(local)
                title = "今日规划草稿" if kind == "morning" else "今日复盘草稿"
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                previous = connection.execute(
                    "SELECT * FROM inbox_items WHERE kind=? AND source_date=?", (kind, source)
                ).fetchone()
                if previous:
                    existing.append(_item(previous))
                    continue
                cursor = connection.execute(
                    """INSERT INTO inbox_items(kind,source_date,title,body_json,manual,catch_up,created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (kind, source, title, json.dumps(body, ensure_ascii=False), manual, catch_up, utc_now()),
                )
                _audit(connection, {"kind": kind, "source_date": source, "manual": manual, "catch_up": catch_up})
                row = connection.execute("SELECT * FROM inbox_items WHERE id=?", (cursor.lastrowid,)).fetchone()
            created.append(_item(row))
        return {"created": created, "existing": existing}

    def today_runs(self, now: datetime | None = None) -> list[dict[str, Any]]:
        local = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT kind,created_at,manual,catch_up FROM inbox_items WHERE source_date=? ORDER BY id",
                (local.date().isoformat(),),
            ).fetchall()
        return [{**dict(row), "manual": bool(row["manual"]), "catch_up": bool(row["catch_up"])} for row in rows]

    def list_inbox(self, limit: int = 50, include_dismissed: bool = False) -> list[dict[str, Any]]:
        where = "" if include_dismissed else "WHERE dismissed_at IS NULL"
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM inbox_items {where} ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_item(row) for row in rows]

    def _touch(self, item_id: int, column: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM inbox_items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "收件箱条目不存在")
            if row[column] is None:
                connection.execute(
                    f"UPDATE inbox_items SET {column}=? WHERE id=?", (utc_now(), item_id)
                )
                row = connection.execute("SELECT * FROM inbox_items WHERE id=?", (item_id,)).fetchone()
        return _item(row)

    def mark_read(self, item_id: int) -> dict[str, Any]:
        return self._touch(item_id, "read_at")

    def dismiss(self, item_id: int) -> dict[str, Any]:
        return self._touch(item_id, "dismissed_at")


def create_scheduler_router(database: Database, prefix: str = "/api", status_provider=None,
                            vault_dir: FilePath | str | None = None) -> APIRouter:
    service = SchedulerService(database, vault_dir)
    router = APIRouter(prefix=prefix, tags=["scheduler"])

    @router.get("/schedule")
    def get_schedule():
        payload = {"settings": service.get_settings(), "today_runs": service.today_runs()}
        payload["runtime"] = status_provider() if status_provider else None
        return payload

    @router.put("/schedule")
    def put_schedule(payload: ScheduleSettings):
        return service.update_settings(payload)

    @router.post("/schedule/run-now")
    def run_now(payload: RunNowRequest):
        result = service.run_due(force_kind=payload.kind)
        if result["created"]:
            return {"created": True, "item": result["created"][0]}
        return {"created": False, "item": result["existing"][0]}

    @router.get("/inbox")
    def get_inbox(limit: int = Query(default=50, ge=1, le=200)):
        return service.list_inbox(limit)

    @router.post("/inbox/{item_id}/read")
    def read_item(item_id: InboxId):
        return service.mark_read(item_id)

    @router.post("/inbox/{item_id}/dismiss")
    def dismiss_item(item_id: InboxId):
        return service.dismiss(item_id)

    return router
