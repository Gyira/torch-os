"""Local, explicit habit records without inferred failures or model calls."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from . import habit_markdown
from .db import Database, utc_now


SHANGHAI = ZoneInfo("Asia/Shanghai")
HabitStatus = Literal["active", "archived"]
EntryStatus = Literal["done", "skipped", "unrecorded"]
HabitId = Annotated[int, Path(ge=1, le=9_223_372_036_854_775_807)]
SCHEMA = """
CREATE TABLE IF NOT EXISTS habit_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 120),
    cue TEXT NOT NULL DEFAULT '' CHECK(length(cue) <= 500),
    minimum_action TEXT NOT NULL CHECK(length(trim(minimum_action)) BETWEEN 1 AND 500),
    start_date TEXT NOT NULL CHECK(length(start_date) = 10 AND date(start_date) IS NOT NULL),
    weekdays_json TEXT NOT NULL CHECK(json_valid(weekdays_json)
        AND json_type(weekdays_json) = 'array' AND json_array_length(weekdays_json) BETWEEN 1 AND 7),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived')),
    revision INTEGER NOT NULL CHECK(revision > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS habit_entries (
    habit_id INTEGER NOT NULL,
    date TEXT NOT NULL CHECK(length(date) = 10 AND date(date) IS NOT NULL),
    status TEXT NOT NULL CHECK(status IN ('done', 'skipped', 'unrecorded')),
    note TEXT NOT NULL DEFAULT '' CHECK(length(note) <= 2000),
    revision INTEGER NOT NULL CHECK(revision > 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(habit_id, date),
    FOREIGN KEY(habit_id) REFERENCES habit_definitions(id)
);
"""


def shanghai_today() -> date:
    return datetime.now(SHANGHAI).date()


def validate_day(value: str, *, today: date | None = None) -> date:
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError
        result = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "日期必须是有效的 YYYY-MM-DD") from exc
    if result > (today if today is not None else shanghai_today()):
        raise HTTPException(422, "习惯记录和统计不能使用未来日期")
    return result


def _date_range(from_date: str, to_date: str) -> tuple[date, date]:
    today = shanghai_today()
    first, last = validate_day(from_date, today=today), validate_day(to_date, today=today)
    if not 0 <= (last - first).days < 93:
        raise HTTPException(422, "查询范围必须按先后顺序，且包含首尾日期最多 93 天")
    return first, last


class HabitInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, values):
        if isinstance(values, dict) and any(value is None for value in values.values()):
            raise ValueError("习惯字段不能为 null；清空提示或备注请使用空字符串")
        return values


class HabitCreate(HabitInput):
    title: str = Field(min_length=1, max_length=120)
    cue: str = Field(default="", max_length=500)
    minimum_action: str = Field(min_length=1, max_length=500)
    start_date: str = Field(default_factory=lambda: shanghai_today().isoformat())
    weekdays: list[StrictInt] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, values):
        if len(set(values)) != len(values) or any(day < 0 or day > 6 for day in values):
            raise ValueError("每周日期必须是互不重复的 0–6 整数，0 为周一、6 为周日")
        return sorted(values)


class HabitUpdate(HabitInput):
    revision: StrictInt = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    cue: str | None = Field(default=None, max_length=500)
    minimum_action: str | None = Field(default=None, min_length=1, max_length=500)
    status: HabitStatus | None = None


class HabitEntryWrite(HabitInput):
    revision: StrictInt = Field(ge=0)
    status: EntryStatus
    note: str = Field(default="", max_length=2000)


def _habit(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["weekdays"] = json.loads(result.pop("weekdays_json"))
    return result


def _require_habit(connection, habit_id: int):
    row = connection.execute("SELECT * FROM habit_definitions WHERE id=?", (habit_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "习惯不存在")
    return row


def _audit(connection, event_type: str, details: dict[str, Any]) -> None:
    # Audit only identifiers and transitions, not personal habit text or notes.
    connection.execute(
        "INSERT INTO audit_events(event_type, detail_json, created_at) VALUES (?,?,?)",
        (event_type, json.dumps(details, ensure_ascii=False), utc_now()),
    )


def _entry(row: Any, habit_id: int, day: str, scheduled: bool) -> dict[str, Any]:
    if row is not None:
        return {**dict(row), "scheduled": scheduled}
    return {"habit_id": habit_id, "date": day, "scheduled": scheduled, "status": "unrecorded",
            "note": "", "revision": 0, "updated_at": None}


class HabitService:
    def __init__(self, database: Database, vault_dir: Any = None):
        self.database = database
        self.vault_dir = vault_dir
        self.database.initialize()
        with self.database.connect() as connection:
            connection.executescript(SCHEMA)

    def _mirror(self) -> None:
        """写入后把定义和当月打卡镜像到人生路的 Markdown（失败不影响主流程）。"""
        if not self.vault_dir:
            return
        try:
            month = shanghai_today().strftime("%Y-%m")
            with self.database.connect() as connection:
                habits = [dict(row) for row in connection.execute(
                    "SELECT * FROM habit_definitions ORDER BY id").fetchall()]
                entries = [dict(row) for row in connection.execute(
                    "SELECT * FROM habit_entries WHERE date LIKE ? ORDER BY date, habit_id",
                    (f"{month}%",)).fetchall()]
            habit_markdown.mirror(self.vault_dir, habits, entries, month)
        except (OSError, ValueError):
            pass

    def list(self, status: HabitStatus | None = None) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM habit_definitions WHERE (? IS NULL OR status=?) ORDER BY id", (status, status),
            ).fetchall()
        return [_habit(row) for row in rows]

    def get(self, habit_id: int) -> dict[str, Any]:
        """Return the current definition using the same shape as ``list``.

        Reads deliberately go through the same row normalizer as the collection
        endpoint, so a client can refetch after a revision conflict without
        having to merge two response shapes.
        """
        with self.database.connect() as connection:
            return _habit(_require_habit(connection, habit_id))

    def create(self, payload: HabitCreate) -> dict[str, Any]:
        validate_day(payload.start_date)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = utc_now()
            cursor = connection.execute(
                """INSERT INTO habit_definitions
                (title,cue,minimum_action,start_date,weekdays_json,status,revision,created_at,updated_at)
                VALUES (?,?,?,?,?,'active',1,?,?)""",
                (payload.title, payload.cue, payload.minimum_action, payload.start_date,
                 json.dumps(payload.weekdays), now, now),
            )
            habit_id = int(cursor.lastrowid)
            _audit(connection, "habit_created", {"habit_id": habit_id, "revision": 1})
            result = _habit(_require_habit(connection, habit_id))
        self._mirror()
        return result

    def update(self, habit_id: int, payload: HabitUpdate) -> dict[str, Any]:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = _require_habit(connection, habit_id)
            if current["revision"] != payload.revision:
                raise HTTPException(409, "习惯已发生变化，请保留草稿并刷新后重试")
            changes = payload.model_dump(exclude_unset=True, exclude={"revision"})
            if all(current[key] == value for key, value in changes.items()):
                return _habit(current)
            values = {key: changes.get(key, current[key]) for key in ("title", "cue", "minimum_action", "status")}
            connection.execute(
                """UPDATE habit_definitions SET title=?,cue=?,minimum_action=?,status=?,revision=revision+1,
                updated_at=? WHERE id=?""", (*values.values(), utc_now(), habit_id),
            )
            _audit(connection, "habit_updated", {
                "habit_id": habit_id, "previous_revision": current["revision"], "revision": current["revision"] + 1,
                "changed_fields": sorted(changes), "previous_status": current["status"], "status": values["status"],
            })
            result = _habit(_require_habit(connection, habit_id))
        self._mirror()
        return result

    @staticmethod
    def _scheduled(habit, target: date) -> bool:
        return target.isoformat() >= habit["start_date"] and target.weekday() in json.loads(habit["weekdays_json"])

    def save_entry(self, habit_id: int, day: str, payload: HabitEntryWrite) -> dict[str, Any]:
        target = validate_day(day)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            habit = _require_habit(connection, habit_id)
            if not self._scheduled(habit, target):
                raise HTTPException(422, "此日期早于开始日期或不在每周计划内，无需打卡")
            current = connection.execute(
                "SELECT * FROM habit_entries WHERE habit_id=? AND date=?", (habit_id, day),
            ).fetchone()
            if payload.revision != (current["revision"] if current else 0):
                raise HTTPException(409, "当天记录已发生变化，请保留草稿并刷新后重试")
            if habit["status"] == "archived" and current is None:
                raise HTTPException(409, "习惯已归档，不能新增记录；已有历史记录仍可纠正")
            if current and current["status"] == payload.status and current["note"] == payload.note:
                return _entry(current, habit_id, day, True)
            if current is None and payload.status == "unrecorded" and not payload.note:
                return _entry(None, habit_id, day, True)
            now = utc_now()
            if current:
                connection.execute(
                    """UPDATE habit_entries SET status=?,note=?,revision=revision+1,updated_at=?
                    WHERE habit_id=? AND date=?""", (payload.status, payload.note, now, habit_id, day),
                )
            else:
                connection.execute(
                    """INSERT INTO habit_entries(habit_id,date,status,note,revision,updated_at)
                    VALUES (?,?,?,?,1,?)""", (habit_id, day, payload.status, payload.note, now),
                )
            row = connection.execute(
                "SELECT * FROM habit_entries WHERE habit_id=? AND date=?", (habit_id, day),
            ).fetchone()
            _audit(connection, "habit_entry_saved", {
                "habit_id": habit_id, "date": day,
                "previous_revision": current["revision"] if current else 0, "revision": row["revision"],
                "previous_status": current["status"] if current else "unrecorded", "status": row["status"],
            })
            result = _entry(row, habit_id, day, True)
        self._mirror()
        return result

    def _entries_from_rows(self, habit, first: date, last: date, rows) -> list[dict[str, Any]]:
        return [
            _entry(rows.get(target.isoformat()), habit["id"], target.isoformat(), self._scheduled(habit, target))
            for offset in range((last - first).days + 1) for target in [first + timedelta(days=offset)]
        ]

    @staticmethod
    def _entry_rows(connection, habit_id: int, first: date, last: date) -> dict[str, Any]:
        return {row["date"]: row for row in connection.execute(
            "SELECT * FROM habit_entries WHERE habit_id=? AND date>=? AND date<=? ORDER BY date",
            (habit_id, first.isoformat(), last.isoformat()),
        ).fetchall()}

    def entries(self, habit_id: int, from_date: str, to_date: str) -> list[dict[str, Any]]:
        first, last = _date_range(from_date, to_date)
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            habit = _require_habit(connection, habit_id)
            rows = self._entry_rows(connection, habit_id, first, last)
        return self._entries_from_rows(habit, first, last, rows)

    def summary(self, habit_id: int, from_date: str, to_date: str) -> dict[str, Any]:
        first, last = _date_range(from_date, to_date)
        week_start = last - timedelta(days=last.weekday())
        week_end = week_start + timedelta(days=6)
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            habit = _require_habit(connection, habit_id)
            rows = self._entry_rows(connection, habit_id, min(first, week_start), max(last, week_end))
            entries = self._entries_from_rows(habit, first, last, rows)
            week_entries = self._entries_from_rows(habit, week_start, week_end, rows)
        scheduled = [entry for entry in entries if entry["scheduled"]]
        counts = {status: sum(entry["status"] == status for entry in scheduled)
                  for status in ("done", "skipped", "unrecorded")}
        denominator = len(scheduled) - counts["skipped"]
        week_scheduled = [entry for entry in week_entries if entry["scheduled"]]
        week_counts = {status: sum(entry["status"] == status for entry in week_scheduled)
                       for status in ("done", "skipped", "unrecorded")}
        week = {
            "start": week_start.isoformat(), "end": week_end.isoformat(),
            "scheduled": len(week_scheduled), **week_counts,
            "remaining": sum(1 for entry in week_scheduled
                             if entry["date"] >= last.isoformat() and entry["status"] == "unrecorded"),
            "note": "“还剩”指从所选日期起到本周日之间仍未记录的计划日；未记录不等于必须完成。",
        }
        return {
            "habit_id": habit_id, "from_date": from_date, "to_date": to_date,
            "scheduled_days": len(scheduled), **counts,
            "completion_rate": counts["done"] / denominator if denominator else None,
            "entries": entries,
            "week": week,
            "warning": "未记录不代表失败。完成率为已完成÷（计划天数−明确跳过），分母为 0 时不计算。"
                       "计划天数按固定开始日期和每周周期计算；归档仅停止新增记录，不改写计划或历史。",
        }


def create_habits_router(database: Database, prefix: str = "/api",
                         vault_dir: Any = None) -> APIRouter:
    service = HabitService(database, vault_dir)
    router = APIRouter(prefix=prefix, tags=["habits"])

    @router.get("/habits")
    def list_habits(status: HabitStatus | None = Query(default=None)):
        return service.list(status)

    @router.get("/habits/{habit_id}")
    def get_habit(habit_id: HabitId):
        return service.get(habit_id)

    @router.post("/habits", status_code=201)
    def create_habit(payload: HabitCreate):
        return service.create(payload)

    @router.patch("/habits/{habit_id}")
    def update_habit(habit_id: HabitId, payload: HabitUpdate):
        return service.update(habit_id, payload)

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
