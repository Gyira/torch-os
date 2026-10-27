"""Daily fortune check-in: the gate that unlocks today's almanac.

The almanac itself is rendered in the browser from a vendored lunar library.
This module owns only the daily state: one row per Shanghai day, written
idempotently. It is DB-only state (like habits), so Markdown read-only mode
does not block check-in.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from .db import Database, utc_now

SHANGHAI = ZoneInfo("Asia/Shanghai")
DAY_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS fortune_checkins (
    day TEXT PRIMARY KEY CHECK(length(day) = 10 AND date(day) IS NOT NULL),
    checked_at TEXT NOT NULL
);
"""


def shanghai_today() -> date:
    return datetime.now(SHANGHAI).date()


def _parse_day(value: str, *, allow_future: bool = False) -> date:
    try:
        if not isinstance(value, str) or not DAY_PATTERN.fullmatch(value):
            raise ValueError
        result = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "日期必须是有效的 YYYY-MM-DD") from exc
    if not allow_future and result > shanghai_today():
        raise HTTPException(422, "不能查看未来日期")
    return result


def _status(day: str, row: Any = None) -> dict[str, Any]:
    return {
        "day": day,
        "checked_in": row is not None,
        "checked_at": row["checked_at"] if row is not None else None,
    }


def _audit(connection, event_type: str, details: dict[str, Any]) -> None:
    # Identifiers only: the day is already a validated date string.
    connection.execute(
        "INSERT INTO audit_events(event_type, detail_json, created_at) VALUES (?,?,?)",
        (event_type, json.dumps(details, ensure_ascii=False), utc_now()),
    )


class FortuneService:
    def __init__(self, database: Database):
        self.database = database
        self.database.initialize()
        with self.database.connect() as connection:
            connection.executescript(SCHEMA)

    def status(self, day: str) -> dict[str, Any]:
        target = _parse_day(day, allow_future=True).isoformat()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT day, checked_at FROM fortune_checkins WHERE day = ?", (target,)
            ).fetchone()
        return _status(target, row)

    def check_in(self, day: str) -> dict[str, Any]:
        target = _parse_day(day).isoformat()
        if target != shanghai_today().isoformat():
            raise HTTPException(422, "只能签到今天")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                "INSERT OR IGNORE INTO fortune_checkins(day, checked_at) VALUES (?, ?)",
                (target, utc_now()),
            ).rowcount
            row = connection.execute(
                "SELECT day, checked_at FROM fortune_checkins WHERE day = ?", (target,)
            ).fetchone()
            if inserted:
                _audit(connection, "fortune_checkin", {"day": target})
        return _status(target, row)


def create_fortune_router(database: Database, prefix: str = "/api") -> APIRouter:
    service = FortuneService(database)
    router = APIRouter(prefix=prefix, tags=["fortune"])

    @router.get("/fortune/checkins/{day}")
    def get_checkin(day: str):
        return service.status(day)

    @router.post("/fortune/checkins/{day}")
    def check_in(day: str):
        return service.check_in(day)

    return router
