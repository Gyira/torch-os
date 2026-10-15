"""User-recorded study subjects, sessions and local weekly minute summaries.

Saving a study session never creates a task, approves a memory or calls a model.
The first create request remains identifiable even after a session is corrected.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .db import Database, utc_now


SHANGHAI = ZoneInfo("Asia/Shanghai")
SQLITE_MAX_INTEGER = 9_223_372_036_854_775_807
LearningId = Annotated[int, Path(ge=1, le=SQLITE_MAX_INTEGER)]
SCHEMA = """
CREATE TABLE IF NOT EXISTS study_subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 120),
    goal_id INTEGER,
    weekly_target_minutes INTEGER NOT NULL
        CHECK(typeof(weekly_target_minutes)='integer' AND weekly_target_minutes BETWEEN 1 AND 10080),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(typeof(revision)='integer' AND revision > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS study_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL,
    study_date TEXT NOT NULL CHECK(
        study_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
        AND substr(study_date,1,4) >= '0001'
        AND date(study_date,'+0 days') IS NOT NULL
        AND date(study_date,'+0 days')=study_date),
    minutes INTEGER NOT NULL CHECK(typeof(minutes)='integer' AND minutes BETWEEN 1 AND 1440),
    content TEXT NOT NULL DEFAULT '' CHECK(length(content) <= 4000),
    next_step TEXT NOT NULL DEFAULT '' CHECK(length(next_step) <= 500),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(typeof(revision)='integer' AND revision > 0),
    request_key TEXT NOT NULL UNIQUE CHECK(length(request_key)=36),
    request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
    goal_id_at_recording INTEGER CHECK(goal_id_at_recording IS NULL OR goal_id_at_recording > 0),
    goal_title_at_recording TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(subject_id) REFERENCES study_subjects(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_study_sessions_date_subject ON study_sessions(study_date,subject_id);
CREATE TRIGGER IF NOT EXISTS study_sessions_daily_limit_insert
BEFORE INSERT ON study_sessions
WHEN NEW.minutes + COALESCE((SELECT SUM(minutes) FROM study_sessions WHERE study_date=NEW.study_date),0) > 1440
BEGIN SELECT RAISE(ABORT,'study daily total exceeds 1440 minutes'); END;
CREATE TRIGGER IF NOT EXISTS study_sessions_daily_limit_update
BEFORE UPDATE OF study_date,minutes ON study_sessions
WHEN NEW.minutes + COALESCE((SELECT SUM(minutes) FROM study_sessions WHERE study_date=NEW.study_date AND id<>OLD.id),0) > 1440
BEGIN SELECT RAISE(ABORT,'study daily total exceeds 1440 minutes'); END;
"""


class LearningInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, values):
        if isinstance(values, dict):
            for field, value in values.items():
                if value is None and field != "goal_id":
                    raise ValueError(f"{field} 不能为 null")
            if "study_date" in values and (
                not isinstance(values["study_date"], str)
                or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", values["study_date"])
            ):
                raise ValueError("日期必须严格使用 YYYY-MM-DD")
        return values


class SubjectCreate(LearningInput):
    title: str = Field(min_length=1, max_length=120)
    goal_id: StrictInt | None = Field(default=None, gt=0, le=SQLITE_MAX_INTEGER)
    weekly_target_minutes: StrictInt = Field(ge=1, le=10080)


class SubjectUpdate(LearningInput):
    revision: StrictInt = Field(ge=1, le=SQLITE_MAX_INTEGER)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    goal_id: StrictInt | None = Field(default=None, gt=0, le=SQLITE_MAX_INTEGER)
    weekly_target_minutes: StrictInt | None = Field(default=None, ge=1, le=10080)
    status: Literal["active", "archived"] | None = None


class SessionCreate(LearningInput):
    subject_id: StrictInt = Field(gt=0, le=SQLITE_MAX_INTEGER)
    study_date: str
    minutes: StrictInt = Field(ge=1, le=1440)
    content: str = Field(default="", max_length=4000)
    next_step: str = Field(default="", max_length=500)
    request_key: str

    @field_validator("request_key")
    @classmethod
    def canonical_request_key(cls, value: str) -> str:
        # Accept standard UUID text only; normalize case for stable retries.
        if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value):
            raise ValueError("request_key 必须是标准 UUID")
        return str(UUID(value))


class SessionUpdate(LearningInput):
    revision: StrictInt = Field(ge=1, le=SQLITE_MAX_INTEGER)
    study_date: str | None = None
    minutes: StrictInt | None = Field(default=None, ge=1, le=1440)
    content: str | None = Field(default=None, max_length=4000)
    next_step: str | None = Field(default=None, max_length=500)


def shanghai_today() -> date:
    return datetime.now(SHANGHAI).date()


def validate_day(value: str, *, allow_future: bool = False) -> date:
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError
        result = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "日期必须是有效的 YYYY-MM-DD") from exc
    if not allow_future and result > shanghai_today():
        raise HTTPException(422, "学习记录只能填写已经发生的日期；未来安排请保存在任务中")
    return result


def _audit(connection, event_type: str, detail: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO audit_events(event_type,detail_json,created_at) VALUES (?,?,?)",
        (event_type, json.dumps(detail, ensure_ascii=False), utc_now()),
    )


def _progress(minutes: int, target: int) -> float | None:
    return round(minutes * 100 / target, 1) if target else None


SESSION_SELECT = """SELECT s.*,sub.title AS subject_title,sub.goal_id AS goal_id,
    sub.status AS subject_status FROM study_sessions s
    JOIN study_subjects sub ON sub.id=s.subject_id"""


def _session_row(row) -> dict[str, Any]:
    result = dict(row)
    result.pop("request_hash", None)
    return result


class LearningService:
    def __init__(self, database: Database):
        self.database = database
        self.database.initialize()
        with self.database.connect() as connection:
            connection.executescript(SCHEMA)

    @staticmethod
    def _goal(connection, goal_id):
        if goal_id is None:
            return None
        row = connection.execute("SELECT id,title FROM goals WHERE id=?", (goal_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "关联目标不存在，请重新选择")
        return row

    @staticmethod
    def _current(connection, table: Literal["study_subjects", "study_sessions"], entity_id: int, revision: int):
        row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "学习科目或记录不存在")
        if row["revision"] != revision:
            raise HTTPException(409, "记录已被修改，请保留草稿并刷新后重试")
        return row

    @staticmethod
    def _check_daily_total(connection, day: str, minutes: int, exclude_id: int = 0) -> None:
        recorded = connection.execute(
            "SELECT COALESCE(SUM(minutes),0) FROM study_sessions WHERE study_date=? AND id<>?", (day, exclude_id)
        ).fetchone()[0]
        if recorded + minutes > 1440:
            raise HTTPException(422, "同一天所有科目的学习记录总时长不能超过 1440 分钟，请核对重复记录")

    def list_subjects(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM study_subjects ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,id"
            )]

    def get_subject(self, subject_id: int) -> dict[str, Any]:
        """Return the current subject using the same shape as ``list_subjects``."""
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM study_subjects WHERE id=?", (subject_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "学习科目不存在")
        return dict(row)

    def create_subject(self, payload: SubjectCreate) -> dict[str, Any]:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._goal(connection, payload.goal_id)
            now = utc_now()
            cursor = connection.execute(
                """INSERT INTO study_subjects(title,goal_id,weekly_target_minutes,created_at,updated_at)
                VALUES (?,?,?,?,?)""", (payload.title, payload.goal_id, payload.weekly_target_minutes, now, now)
            )
            row = dict(connection.execute("SELECT * FROM study_subjects WHERE id=?", (cursor.lastrowid,)).fetchone())
            _audit(connection, "study_subject_created", {"subject_id": row["id"], "goal_id": row["goal_id"], "revision": 1})
            return row

    def update_subject(self, subject_id: int, payload: SubjectUpdate) -> dict[str, Any]:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._current(connection, "study_subjects", subject_id, payload.revision)
            changes = payload.model_dump(exclude_unset=True, exclude={"revision"})
            if "goal_id" in changes:
                self._goal(connection, changes["goal_id"])
            if not changes or all(current[key] == value for key, value in changes.items()):
                return dict(current)
            assignments = ",".join(f"{key}=?" for key in changes)
            connection.execute(
                f"UPDATE study_subjects SET {assignments},revision=revision+1,updated_at=? WHERE id=?",
                (*changes.values(), utc_now(), subject_id),
            )
            row = dict(connection.execute("SELECT * FROM study_subjects WHERE id=?", (subject_id,)).fetchone())
            _audit(connection, "study_subject_updated", {
                "subject_id": subject_id, "revision": row["revision"], "changed_fields": list(changes),
                "previous_goal_id": current["goal_id"], "goal_id": row["goal_id"],
                "previous_target_minutes": current["weekly_target_minutes"], "target_minutes": row["weekly_target_minutes"],
                "previous_status": current["status"], "status": row["status"],
            })
            return row

    def list_sessions(self, from_date: str, to_date: str, subject_id: int | None = None) -> list[dict[str, Any]]:
        first = validate_day(from_date, allow_future=True)
        last = validate_day(to_date, allow_future=True)
        if not 0 <= (last - first).days < 93:
            raise HTTPException(422, "查询范围必须按先后顺序，且包含首尾日期最多 93 天")
        with self.database.connect() as connection:
            if subject_id is not None and not connection.execute("SELECT 1 FROM study_subjects WHERE id=?", (subject_id,)).fetchone():
                raise HTTPException(404, "学习科目不存在")
            params: list[Any] = [from_date, to_date]
            where = " WHERE s.study_date>=? AND s.study_date<=?"
            if subject_id is not None:
                where += " AND s.subject_id=?"
                params.append(subject_id)
            return [_session_row(row) for row in connection.execute(
                SESSION_SELECT + where + " ORDER BY s.study_date DESC,s.id DESC", params
            )]

    def get_session(self, session_id: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(SESSION_SELECT + " WHERE s.id=?", (session_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "学习记录不存在")
        return _session_row(row)

    def create_session(self, payload: SessionCreate) -> dict[str, Any]:
        validate_day(payload.study_date)
        request = payload.model_dump(exclude={"request_key"})
        digest = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(SESSION_SELECT + " WHERE s.request_key=?", (payload.request_key,)).fetchone()
            if previous:
                if previous["request_hash"] != digest:
                    raise HTTPException(409, "此保存标识已用于其他内容；请保留草稿并为新记录生成新的 request_key")
                return {"created": False, "idempotent": True, "session": _session_row(previous)}
            subject = connection.execute("SELECT * FROM study_subjects WHERE id=?", (payload.subject_id,)).fetchone()
            if subject is None:
                raise HTTPException(404, "学习科目不存在")
            if subject["status"] != "active":
                raise HTTPException(409, "此科目已归档，请恢复科目后再添加记录；已有记录仍可查看和修正")
            self._check_daily_total(connection, payload.study_date, payload.minutes)
            goal = self._goal(connection, subject["goal_id"])
            now = utc_now()
            cursor = connection.execute(
                """INSERT INTO study_sessions
                (subject_id,study_date,minutes,content,next_step,request_key,request_hash,
                 goal_id_at_recording,goal_title_at_recording,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (payload.subject_id, payload.study_date, payload.minutes, payload.content, payload.next_step,
                 payload.request_key, digest, goal["id"] if goal else None, goal["title"] if goal else None, now, now),
            )
            row = connection.execute(SESSION_SELECT + " WHERE s.id=?", (cursor.lastrowid,)).fetchone()
            _audit(connection, "study_session_created", {
                "session_id": row["id"], "subject_id": row["subject_id"], "study_date": row["study_date"],
                "minutes": row["minutes"], "revision": 1, "goal_id_at_recording": row["goal_id_at_recording"],
            })
            return {"created": True, "idempotent": False, "session": _session_row(row)}

    def update_session(self, session_id: int, payload: SessionUpdate) -> dict[str, Any]:
        if "study_date" in payload.model_fields_set:
            validate_day(payload.study_date)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._current(connection, "study_sessions", session_id, payload.revision)
            changes = payload.model_dump(exclude_unset=True, exclude={"revision"})
            if changes and any(current[key] != value for key, value in changes.items()):
                self._check_daily_total(connection, changes.get("study_date", current["study_date"]),
                                        changes.get("minutes", current["minutes"]), session_id)
                assignments = ",".join(f"{key}=?" for key in changes)
                connection.execute(
                    f"UPDATE study_sessions SET {assignments},revision=revision+1,updated_at=? WHERE id=?",
                    (*changes.values(), utc_now(), session_id),
                )
                _audit(connection, "study_session_updated", {
                    "session_id": session_id, "subject_id": current["subject_id"], "revision": current["revision"] + 1,
                    "changed_fields": list(changes), "previous_study_date": current["study_date"],
                    "study_date": changes.get("study_date", current["study_date"]),
                    "previous_minutes": current["minutes"], "minutes": changes.get("minutes", current["minutes"]),
                })
            row = connection.execute(SESSION_SELECT + " WHERE s.id=?", (session_id,)).fetchone()
            return _session_row(row)

    def monthly(self, month: str | None = None) -> dict[str, Any]:
        today = shanghai_today()
        if month is None:
            month = today.strftime("%Y-%m")
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
            raise HTTPException(422, "月份必须是 YYYY-MM 格式")
        year, number = int(month[:4]), int(month[5:7])
        if not 1 <= number <= 12:
            raise HTTPException(422, "月份必须在 01–12 之间")
        first = date(year, number, 1)
        if first > today:
            raise HTTPException(422, "不能查看未来月份")
        next_month = date(year + 1, 1, 1) if number == 12 else date(year, number + 1, 1)
        last = next_month - timedelta(days=1)

        def snapshot(connection, start: date, end: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            rows = connection.execute(
                """SELECT sub.id AS subject_id,sub.title,sub.status,
                    COALESCE(SUM(s.minutes),0) AS studied_minutes,COUNT(s.id) AS sessions_count
                FROM study_subjects sub LEFT JOIN study_sessions s
                    ON s.subject_id=sub.id AND s.study_date>=? AND s.study_date<=?
                GROUP BY sub.id HAVING sub.status='active' OR COUNT(s.id)>0
                ORDER BY CASE sub.status WHEN 'active' THEN 0 ELSE 1 END,sub.id""",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
            subjects = [dict(row) for row in rows]
            days_studied = connection.execute(
                "SELECT COUNT(DISTINCT study_date) FROM study_sessions WHERE study_date>=? AND study_date<=?",
                (start.isoformat(), end.isoformat()),
            ).fetchone()[0]
            totals = {
                "studied_minutes": sum(item["studied_minutes"] for item in subjects),
                "sessions_count": sum(item["sessions_count"] for item in subjects),
                "active_studied_minutes": sum(item["studied_minutes"] for item in subjects if item["status"] == "active"),
                "days_studied": days_studied,
            }
            return subjects, totals

        with self.database.connect() as connection:
            subjects, totals = snapshot(connection, first, last)
            prev_first = date(year - 1, 12, 1) if number == 1 else date(year, number - 1, 1)
            prev_last = first - timedelta(days=1)
            prev_subjects, prev_totals = snapshot(connection, prev_first, prev_last)
        previous = {
            "month": prev_first.strftime("%Y-%m"),
            "subjects": prev_subjects,
            "totals": prev_totals,
        }
        return {
            "month": month,
            "month_start": first.isoformat(),
            "month_end": last.isoformat(),
            "today": today.isoformat(),
            "subjects": subjects,
            "totals": totals,
            "previous_month": previous,
            "warning": "月汇总只统计你填写的学习记录分钟数与记录天数，不代表知识掌握程度；没有记录不代表没有学习。归档科目在当月有记录时保留其时长，不再计入活跃科目合计。对比上月同样只反映记录时长，不是效果评价。",
        }

    def weekly(self, week_start: str | None = None) -> dict[str, Any]:
        today = shanghai_today()
        first = validate_day(week_start) if week_start is not None else today - timedelta(days=today.weekday())
        if first.weekday() != 0:
            raise HTTPException(422, "week_start 必须是周一")
        last = first + timedelta(days=6)
        with self.database.connect() as connection:
            # A single SELECT yields a coherent snapshot of subjects and minutes.
            rows = connection.execute(
                """SELECT sub.id AS subject_id,sub.title,sub.goal_id,sub.status,sub.weekly_target_minutes,
                    COALESCE(SUM(s.minutes),0) AS studied_minutes,COUNT(s.id) AS sessions_count,
                    last.last_studied_date
                FROM study_subjects sub LEFT JOIN study_sessions s
                    ON s.subject_id=sub.id AND s.study_date>=? AND s.study_date<=?
                LEFT JOIN (SELECT subject_id,MAX(study_date) AS last_studied_date
                           FROM study_sessions GROUP BY subject_id) last
                    ON last.subject_id=sub.id
                GROUP BY sub.id HAVING sub.status='active' OR COUNT(s.id)>0
                ORDER BY CASE sub.status WHEN 'active' THEN 0 ELSE 1 END,sub.id""", (first.isoformat(), last.isoformat())
            ).fetchall()
        subjects = []
        for row in rows:
            item = dict(row)
            item["target_minutes"] = item["weekly_target_minutes"] if item["status"] == "active" else 0
            item["progress_percent"] = _progress(item["studied_minutes"], item["target_minutes"])
            last_studied = item["last_studied_date"]
            item["days_since_last"] = None if last_studied is None else max(0, (today - date.fromisoformat(last_studied)).days)
            subjects.append(item)
        totals = {key: sum(item[key] for item in subjects) for key in ("target_minutes", "studied_minutes", "sessions_count")}
        totals["active_studied_minutes"] = sum(item["studied_minutes"] for item in subjects if item["status"] == "active")
        totals["progress_percent"] = _progress(totals["active_studied_minutes"], totals["target_minutes"])
        return {
            "week_start": first.isoformat(), "week_end": last.isoformat(), "today": today.isoformat(),
            "subjects": subjects, "totals": totals,
            "warning": "只汇总你填写的学习分钟数，不代表知识掌握程度；没有记录不代表没有学习。目标与科目归属使用当前设置，不是历史快照；归档科目保留已记时长，不再计入目标。周目标进度按当前活跃科目计算。每条记录另保留初次保存时的目标关联。最近一次记录按全部历史记录计算，不受所选周限制。",
        }


def create_learning_router(database: Database, prefix: str = "/api") -> APIRouter:
    service = LearningService(database)
    router = APIRouter(prefix=prefix + "/learning", tags=["learning"])

    @router.get("/subjects")
    def list_subjects():
        return service.list_subjects()

    @router.get("/subjects/{subject_id}")
    def get_subject(subject_id: LearningId):
        return service.get_subject(subject_id)

    @router.post("/subjects")
    def create_subject(payload: SubjectCreate):
        return service.create_subject(payload)

    @router.patch("/subjects/{subject_id}")
    def update_subject(subject_id: LearningId, payload: SubjectUpdate):
        return service.update_subject(subject_id, payload)

    @router.get("/sessions")
    def list_sessions(from_date: str = Query(), to_date: str = Query(),
                      subject_id: int | None = Query(default=None, gt=0, le=SQLITE_MAX_INTEGER)):
        return service.list_sessions(from_date, to_date, subject_id)

    @router.post("/sessions")
    def create_session(payload: SessionCreate):
        return service.create_session(payload)

    @router.get("/sessions/{session_id}")
    def get_session(session_id: LearningId):
        return service.get_session(session_id)

    @router.patch("/sessions/{session_id}")
    def update_session(session_id: LearningId, payload: SessionUpdate):
        return service.update_session(session_id, payload)

    @router.get("/weekly")
    def weekly(week_start: str | None = Query(default=None)):
        return service.weekly(week_start)

    @router.get("/monthly")
    def monthly(month: str | None = Query(default=None)):
        """Read-only monthly minute summary with previous-month comparison."""
        return service.monthly(month)

    return router
