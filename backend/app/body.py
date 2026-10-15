"""Local body records (weight, sleep, exercise) without inference or model calls.

One optional row per Shanghai date. Values are user-entered facts only; they
never become memories, tasks or cloud context on their own. Clearing every
field removes the row instead of keeping an empty one.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .db import Database, utc_now


SHANGHAI = ZoneInfo("Asia/Shanghai")
VALUE_FIELDS = ("weight_kg", "sleep_minutes", "exercise_minutes")
SCHEMA = """
CREATE TABLE IF NOT EXISTS body_entries (
    date TEXT PRIMARY KEY CHECK(length(date) = 10 AND date(date) IS NOT NULL),
    weight_kg REAL CHECK(weight_kg IS NULL OR (weight_kg >= 20.0 AND weight_kg <= 300.0)),
    sleep_minutes INTEGER CHECK(sleep_minutes IS NULL OR (sleep_minutes BETWEEN 0 AND 1440)),
    exercise_minutes INTEGER CHECK(exercise_minutes IS NULL OR (exercise_minutes BETWEEN 0 AND 1440)),
    revision INTEGER NOT NULL CHECK(revision > 0),
    updated_at TEXT NOT NULL
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
        raise HTTPException(422, "身体记录不能使用未来日期")
    return result


def _date_range(from_date: str, to_date: str) -> tuple[date, date]:
    today = shanghai_today()
    first, last = validate_day(from_date, today=today), validate_day(to_date, today=today)
    if not 0 <= (last - first).days < 93:
        raise HTTPException(422, "查询范围必须按先后顺序，且包含首尾日期最多 93 天")
    return first, last


class BodyEntryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weight_kg: float | None = None
    sleep_minutes: StrictInt | None = None
    exercise_minutes: StrictInt | None = None
    revision: StrictInt = Field(ge=0)

    @field_validator("weight_kg", mode="before")
    @classmethod
    def weight_is_plain_number(cls, value):
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("体重必须是数字（单位 kg）")
        return float(value)

    @field_validator("weight_kg")
    @classmethod
    def weight_range_and_precision(cls, value):
        if value is None:
            return None
        if not 20.0 <= value <= 300.0:
            raise ValueError("体重需在 20–300 kg 之间")
        if abs(value * 10 - round(value * 10)) > 1e-9:
            raise ValueError("体重最多保留一位小数")
        return value

    @field_validator("sleep_minutes")
    @classmethod
    def sleep_range(cls, value):
        if value is not None and not 0 <= value <= 1440:
            raise ValueError("睡眠时长需为 0–1440 分钟的整数")
        return value

    @field_validator("exercise_minutes")
    @classmethod
    def exercise_range(cls, value):
        if value is not None and not 0 <= value <= 1440:
            raise ValueError("运动时长需为 0–1440 分钟的整数")
        return value

    @model_validator(mode="after")
    def require_value_field(self):
        # Explicit null is meaningful (clear that field), so presence — not a
        # non-null value — is what makes a payload valid.
        if not (self.model_fields_set & set(VALUE_FIELDS)):
            raise ValueError("请至少提供一个身体记录字段：体重、睡眠或运动时长")
        return self


def _record(day: str, row: Any) -> dict[str, Any]:
    if row is None:
        return {"date": day, "recorded": False, "weight_kg": None, "sleep_minutes": None,
                "exercise_minutes": None, "revision": 0, "updated_at": None}
    return {"date": row["date"], "recorded": True, "weight_kg": row["weight_kg"],
            "sleep_minutes": row["sleep_minutes"], "exercise_minutes": row["exercise_minutes"],
            "revision": row["revision"], "updated_at": row["updated_at"]}


def _audit(connection, event_type: str, details: dict[str, Any]) -> None:
    # Identifiers and field names only; measured values never enter the audit log.
    connection.execute(
        "INSERT INTO audit_events(event_type, detail_json, created_at) VALUES (?,?,?)",
        (event_type, json.dumps(details, ensure_ascii=False), utc_now()),
    )


SUMMARY_PERIODS = ("week", "month")


def summary_window(period: str, anchor: str | None) -> tuple[str, str, bool]:
    if period not in SUMMARY_PERIODS:
        raise HTTPException(422, "周期必须是 week 或 month")
    today = shanghai_today()
    target = validate_day(anchor) if anchor else today
    if period == "week":
        start = target - timedelta(days=target.weekday())
        end = start + timedelta(days=6)
    else:
        start = target.replace(day=1)
        next_month = date(target.year + 1, 1, 1) if target.month == 12 else date(target.year, target.month + 1, 1)
        end = next_month - timedelta(days=1)
    return start.isoformat(), min(end, today).isoformat(), end > today


def build_summary(period: str, from_date: str, to_date: str, partial: bool,
                  entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Fact-only aggregate: recorded days and minute sums. Weight values never leave rows."""
    days_weight = sum(item["weight_kg"] is not None for item in entries)
    days_sleep = sum(item["sleep_minutes"] is not None for item in entries)
    days_exercise = sum(item["exercise_minutes"] is not None for item in entries)
    return {
        "schema": 1,
        "exported_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "period": period,
        "from_date": from_date,
        "to_date": to_date,
        "partial": partial,
        "days_recorded": len(entries),
        "days_weight": days_weight,
        "days_sleep": days_sleep,
        "days_exercise": days_exercise,
        "totals": {
            "sleep_minutes": sum(item["sleep_minutes"] or 0 for item in entries),
            "exercise_minutes": sum(item["exercise_minutes"] or 0 for item in entries),
        },
        "warning": "只汇总填写天数与分钟合计，不含体重数值、不做健康评价；未记录不代表 0 或异常。",
    }


def summary_markdown(summary: dict[str, Any]) -> str:
    scope = "至今" if summary["partial"] else "完整周期"
    totals = summary["totals"]
    return "\n".join([
        f"# 薪火OS 身体记录汇总（{summary['period']}）",
        "",
        f"- 周期：{summary['from_date']} 至 {summary['to_date']}（{scope}）",
        f"- 有记录天数：{summary['days_recorded']}",
        f"- 体重记录天数：{summary['days_weight']}（不含数值）",
        f"- 睡眠记录天数：{summary['days_sleep']}；合计 {totals['sleep_minutes']} 分钟",
        f"- 运动记录天数：{summary['days_exercise']}；合计 {totals['exercise_minutes']} 分钟",
        f"- 导出时间：{summary['exported_at']}",
        "",
        f"> {summary['warning']}",
        "",
    ])


class BodyRecordService:
    def __init__(self, database: Database):
        self.database = database
        self.database.initialize()
        with self.database.connect() as connection:
            connection.executescript(SCHEMA)

    def get(self, day: str) -> dict[str, Any]:
        validate_day(day)
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM body_entries WHERE date=?", (day,)).fetchone()
        return _record(day, row)

    def entries(self, from_date: str, to_date: str) -> dict[str, Any]:
        _date_range(from_date, to_date)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM body_entries WHERE date>=? AND date<=? ORDER BY date",
                (from_date, to_date),
            ).fetchall()
        return {"from_date": from_date, "to_date": to_date,
                "entries": [_record(row["date"], row) for row in rows],
                "warning": "只列出有记录的日期；未记录不代表当天没有发生。"}

    def save(self, day: str, payload: BodyEntryWrite) -> dict[str, Any]:
        validate_day(day)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM body_entries WHERE date=?", (day,)).fetchone()
            if payload.revision != (current["revision"] if current else 0):
                raise HTTPException(409, "身体记录已发生变化，请保留草稿并刷新后重试")
            changes = payload.model_dump(exclude_unset=True, exclude={"revision"})
            merged = {field: (changes[field] if field in changes else
                              (current[field] if current else None)) for field in VALUE_FIELDS}
            if current is not None and all(current[field] == merged[field] for field in VALUE_FIELDS):
                return _record(day, current)
            if all(merged[field] is None for field in VALUE_FIELDS):
                if current is None:
                    return _record(day, None)
                connection.execute("DELETE FROM body_entries WHERE date=?", (day,))
                _audit(connection, "body_entry_cleared",
                       {"date": day, "previous_revision": current["revision"]})
                return _record(day, None)
            now = utc_now()
            values = [merged[field] for field in VALUE_FIELDS]
            if current is None:
                connection.execute(
                    """INSERT INTO body_entries(date,weight_kg,sleep_minutes,exercise_minutes,revision,updated_at)
                    VALUES (?,?,?,?,1,?)""", (day, *values, now),
                )
            else:
                connection.execute(
                    """UPDATE body_entries SET weight_kg=?,sleep_minutes=?,exercise_minutes=?,
                    revision=revision+1,updated_at=? WHERE date=?""", (*values, now, day),
                )
            row = connection.execute("SELECT * FROM body_entries WHERE date=?", (day,)).fetchone()
            _audit(connection, "body_entry_saved", {
                "date": day, "previous_revision": current["revision"] if current else 0,
                "revision": row["revision"], "changed_fields": sorted(changes),
                "cleared_fields": sorted(field for field in changes if merged[field] is None),
            })
            return _record(day, row)


def create_body_router(database: Database, prefix: str = "/api") -> APIRouter:
    service = BodyRecordService(database)
    router = APIRouter(prefix=prefix, tags=["body"])

    def window(from_date: str | None, to_date: str | None) -> tuple[str, str]:
        today = shanghai_today()
        return (
            from_date or (today - timedelta(days=89)).isoformat(),
            to_date or today.isoformat(),
        )

    def stamp() -> str:
        return datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")

    def record_export(export_format: str, result: dict[str, Any]) -> None:
        # Counts only: measured values never enter the audit log.
        database.audit("body_exported", {
            "format": export_format,
            "from_date": result["from_date"], "to_date": result["to_date"],
            "total": len(result["entries"]),
        })

    @router.get("/body/entries")
    def list_entries(from_date: str = Query(), to_date: str = Query()):
        return service.entries(from_date, to_date)

    @router.get("/body/entries/{day}")
    def get_entry(day: str):
        return service.get(day)

    @router.put("/body/entries/{day}")
    def save_entry(day: str, payload: BodyEntryWrite):
        return service.save(day, payload)

    @router.get("/body/export.json")
    def export_json(from_date: str | None = Query(default=None), to_date: str | None = Query(default=None)):
        start, end = window(from_date, to_date)
        result = service.entries(start, end)
        record_export("json", result)
        payload = {
            "schema": 1,
            "exported_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "from_date": result["from_date"], "to_date": result["to_date"],
            "total": len(result["entries"]), "warning": result["warning"],
            "entries": result["entries"],
        }
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="torch-os-body-{stamp()}.json"'},
        )

    @router.get("/body/export.csv")
    def export_csv(from_date: str | None = Query(default=None), to_date: str | None = Query(default=None)):
        start, end = window(from_date, to_date)
        result = service.entries(start, end)
        record_export("csv", result)
        lines = ["date,weight_kg,sleep_minutes,exercise_minutes"]
        for item in result["entries"]:
            lines.append(",".join([
                item["date"],
                "" if item["weight_kg"] is None else format(item["weight_kg"], "g"),
                "" if item["sleep_minutes"] is None else str(item["sleep_minutes"]),
                "" if item["exercise_minutes"] is None else str(item["exercise_minutes"]),
            ]))
        return Response(
            content="\n".join(lines) + "\n",
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="torch-os-body-{stamp()}.csv"'},
        )

    def summary_result(export_format: str, period: str, anchor: str | None) -> dict[str, Any]:
        start, end, partial = summary_window(period, anchor)
        result = service.entries(start, end)
        payload = build_summary(period, start, end, partial, result["entries"])
        # Counts only: period boundaries and recorded days, never measured values.
        database.audit("body_summary_exported", {
            "format": export_format, "period": period,
            "from_date": start, "to_date": end, "days_recorded": payload["days_recorded"],
        })
        return payload

    @router.get("/body/summary.json")
    def summary_json(period: str = Query(default="month"), anchor: str | None = Query(default=None)):
        payload = summary_result("json", period, anchor)
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="torch-os-body-summary-{payload["period"]}-{stamp()}.json"'},
        )

    @router.get("/body/summary.md")
    def summary_md(period: str = Query(default="month"), anchor: str | None = Query(default=None)):
        payload = summary_result("md", period, anchor)
        return Response(
            content=summary_markdown(payload),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="torch-os-body-summary-{payload["period"]}-{stamp()}.md"'},
        )

    return router
