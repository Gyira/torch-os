"""Local daily journal, deterministic reviews, and explicit immutable exports.

SQLite is the journal authority. Exported Markdown is a user-confirmed snapshot;
neither saving nor reviewing a journal invokes a model or activates a memory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .db import Database, utc_now
from .goals import TaskCreate, _goal_row, _task_row
from .vault_connection import ensure_vault_writable
from .vault import MAX_NOTE_BYTES, safe_note_path


SHANGHAI = ZoneInfo("Asia/Shanghai")
EXTRACTION_VERSION = "journal-fields-v1"
MAX_SEGMENT_CHARACTERS = 2000
TEXT_FIELDS = ("content", "highlights", "obstacles", "notes", "reflection", "diet_summary", "mood", "next_action")
# 天气与地址只是当天的情境，不参与记忆候选（所以不放进 TEXT_FIELDS）。
CONTEXT_FIELDS = ("weather", "location")
WRITE_FIELDS = (*TEXT_FIELDS, *CONTEXT_FIELDS)
FIELD_LABELS = {"content": "每日记录", "highlights": "完成与收获", "obstacles": "遇到的阻碍",
                "notes": "值得注意的事", "reflection": "应该反思的",
                "diet_summary": "饮食摘要", "mood": "情绪", "next_action": "下一步行动",
                "weather": "天气", "location": "地址"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_entries (
    date TEXT PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    highlights TEXT NOT NULL DEFAULT '',
    obstacles TEXT NOT NULL DEFAULT '',
    diet_summary TEXT NOT NULL DEFAULT '',
    mood TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    reflection TEXT NOT NULL DEFAULT '',
    weather TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    goal_id INTEGER,
    revision INTEGER NOT NULL CHECK(revision > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS journal_actions (
    date TEXT NOT NULL,
    revision INTEGER NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('task', 'export')),
    entity_id INTEGER NOT NULL,
    source_path TEXT,
    source_hash TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(date, revision, action),
    FOREIGN KEY(date) REFERENCES journal_entries(date)
);
"""


class JournalWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    revision: StrictInt = Field(ge=0)
    content: str = Field(default="", max_length=12_000)
    highlights: str = Field(default="", max_length=4_000)
    obstacles: str = Field(default="", max_length=4_000)
    notes: str = Field(default="", max_length=4_000)
    reflection: str = Field(default="", max_length=4_000)
    weather: str = Field(default="", max_length=60)
    location: str = Field(default="", max_length=120)
    diet_summary: str = Field(default="", max_length=300)
    mood: str = Field(default="", max_length=100)
    next_action: str = Field(default="", max_length=500)
    goal_id: StrictInt | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def reject_empty_record(self):
        if not any(getattr(self, field) for field in TEXT_FIELDS):
            raise ValueError("至少填写一项记录或复盘内容；空白不会标记为已记录")
        return self


class JournalAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: StrictInt = Field(ge=1)
    confirm: bool = Field(strict=True)

    @model_validator(mode="after")
    def require_confirmation(self):
        if self.confirm is not True:
            raise ValueError("请明确确认此操作")
        return self


class MonthReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    month: str = Field(min_length=7, max_length=7, pattern=r"[0-9]{4}-[0-9]{2}")


def shanghai_today() -> date:
    return datetime.now(SHANGHAI).date()


def validate_day(value: str) -> date:
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError
        result = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "日期必须是有效的 YYYY-MM-DD") from exc
    if result > shanghai_today():
        raise HTTPException(422, "不能为未来日期记录已经发生的生活；请在任务中安排计划")
    return result


def _entry(row: Any, day: str) -> dict[str, Any]:
    if row:
        return {**dict(row), "recorded": True}
    return {"date": day, **{field: "" for field in WRITE_FIELDS}, "goal_id": None,
            "revision": 0, "recorded": False, "created_at": None, "updated_at": None}


def _audit(connection, event_type: str, details: dict[str, Any]) -> None:
    # Keep audit events in the same transaction instead of opening a second writer.
    connection.execute(
        "INSERT INTO audit_events(event_type, detail_json, created_at) VALUES (?, ?, ?)",
        (event_type, json.dumps(details, ensure_ascii=False), utc_now()),
    )


def _require_current(connection, day: str, revision: int):
    row = connection.execute("SELECT * FROM journal_entries WHERE date=?", (day,)).fetchone()
    if not row:
        raise HTTPException(404, "此日期尚未保存记录")
    if row["revision"] != revision:
        raise HTTPException(409, "记录已发生变化，请保留草稿并刷新后重试")
    return row


def _completion_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Existing application writes UTC; tolerate older UTC values without offset.
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(SHANGHAI).date()
    except (TypeError, ValueError, OverflowError):
        return None


class JournalService:
    def __init__(self, database: Database, vault_dir: Path, read_only: bool = False):
        self.database = database
        self.vault_dir = Path(vault_dir)
        self.read_only = bool(read_only)
        self.initialize()

    def initialize(self) -> None:
        self.database.initialize()
        with self.database.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(journal_entries)")}
            for name in ("diet_summary", "mood", "notes", "reflection", "weather", "location"):
                if name not in columns:
                    connection.execute(f"ALTER TABLE journal_entries ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")

    def get(self, day: str) -> dict[str, Any]:
        validate_day(day)
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM journal_entries WHERE date=?", (day,)).fetchone()
        return _entry(row, day)

    def list(self, from_date: str, to_date: str) -> list[dict[str, Any]]:
        first, last = validate_day(from_date), validate_day(to_date)
        if not 0 <= (last - first).days < 31:
            raise HTTPException(422, "查询范围必须按先后顺序，且包含首尾日期最多 31 天")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM journal_entries WHERE date>=? AND date<=? ORDER BY date DESC",
                (from_date, to_date),
            ).fetchall()
        return [_entry(row, row["date"]) for row in rows]

    def save(self, day: str, payload: JournalWrite) -> dict[str, Any]:
        validate_day(day)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM journal_entries WHERE date=?", (day,)).fetchone()
            if payload.revision != (current["revision"] if current else 0):
                raise HTTPException(409, "记录已发生变化，请保留草稿并刷新后重试")
            if payload.goal_id is not None and not connection.execute(
                "SELECT 1 FROM goals WHERE id=?", (payload.goal_id,)
            ).fetchone():
                raise HTTPException(404, "关联目标不存在，请重新选择")
            values = payload.model_dump(exclude={"revision"})
            if current and all(current[key] == value for key, value in values.items()):
                return _entry(current, day)
            now = utc_now()
            if current:
                connection.execute(
                    """UPDATE journal_entries SET content=?,highlights=?,obstacles=?,notes=?,reflection=?,
                    diet_summary=?,mood=?,next_action=?,weather=?,location=?,
                    goal_id=?,revision=revision+1,updated_at=? WHERE date=?""",
                    (*(values[field] for field in WRITE_FIELDS), payload.goal_id, now, day),
                )
            else:
                connection.execute(
                    """INSERT INTO journal_entries
                    (date,content,highlights,obstacles,notes,reflection,diet_summary,mood,next_action,
                    weather,location,goal_id,revision,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                    (day, *(values[field] for field in WRITE_FIELDS), payload.goal_id, now, now),
                )
            result = connection.execute("SELECT * FROM journal_entries WHERE date=?", (day,)).fetchone()
            _audit(connection, "journal_saved", {"date": day, "revision": result["revision"]})
            return _entry(result, day)

    def review(self, day: str) -> dict[str, Any]:
        target = validate_day(day)
        with self.database.connect() as connection:
            # One read snapshot keeps the diary, goal and task facts internally consistent.
            connection.execute("BEGIN")
            record = _entry(connection.execute("SELECT * FROM journal_entries WHERE date=?", (day,)).fetchone(), day)
            completed = [
                _task_row(row) for row in connection.execute(
                    "SELECT * FROM tasks WHERE status='completed' AND completed_at IS NOT NULL ORDER BY completed_at,id"
                ).fetchall() if _completion_day(row["completed_at"]) == target
            ]
            open_tasks = [_task_row(row) for row in connection.execute(
                """SELECT * FROM tasks WHERE status IN ('todo','in_progress')
                AND due_date IS NOT NULL AND due_date<=? ORDER BY due_date,sort_order,id""", (day,)
            ).fetchall() if (created_day := _completion_day(row["created_at"])) is not None and created_day <= target]
            goal_row = connection.execute("SELECT * FROM goals WHERE id=?", (record["goal_id"],)).fetchone() if record["goal_id"] else None
        summary = ("已保存当天记录。" if record["recorded"] else "当天尚未保存记录，未记录不代表没有进展。")
        summary += f"系统当前记录中，有 {len(completed)} 项任务在这一天完成，{len(open_tasks)} 项已到期任务仍未完成。"
        if record["highlights"]:
            summary += "你记录的收获：" + record["highlights"]
        return {
            "date": day, "recorded": record["recorded"], "summary": summary,
            "completed_tasks": completed, "open_tasks": open_tasks,
            "goal": _goal_row(goal_row) if goal_row else None,
            "reflection_prompts": ["今天有哪些进展值得保留？", "遇到了哪些具体阻碍，需要什么帮助？", "下一步最小且可执行的行动是什么？"],
            "next_action": record["next_action"],
            "warning": "这是本地草稿，不推测未记录的生活。任务列表按当前状态整理，不是历史状态快照；待办仅包含创建日不晚于所选日期、仍未完成且截止不晚于所选日期的任务。补记仍按原日期归属。",
        }

    def monthly_review(self, month: str) -> dict[str, Any]:
        """Deterministic month draft over recorded facts only; nothing is written."""
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month or ""):
            raise HTTPException(422, "月份必须是 YYYY-MM 格式")
        year, number = int(month[:4]), int(month[5:7])
        if not 1 <= number <= 12:
            raise HTTPException(422, "月份必须在 01–12 之间")
        today = shanghai_today()
        start = date(year, number, 1)
        if start > today:
            raise HTTPException(422, "不能为未来月份生成复盘")
        next_month = date(year + 1, 1, 1) if number == 12 else date(year, number + 1, 1)
        month_end = next_month - timedelta(days=1)
        until = min(month_end, today)
        start_text, until_text = start.isoformat(), until.isoformat()
        with self.database.connect() as connection:
            connection.execute("BEGIN")

            def table_exists(name: str) -> bool:
                return connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
                ).fetchone() is not None

            journal_days = int(connection.execute(
                "SELECT COUNT(*) FROM journal_entries WHERE date>=? AND date<=?",
                (start_text, until_text),
            ).fetchone()[0] or 0)
            completed_tasks = 0
            completed_examples: list[str] = []
            overdue = 0
            overdue_examples: list[dict[str, Any]] = []
            if table_exists("tasks"):
                for row in connection.execute(
                    """SELECT title,completed_at FROM tasks WHERE status='completed'
                    AND completed_at IS NOT NULL ORDER BY completed_at DESC,id DESC"""
                ).fetchall():
                    finished = _completion_day(row["completed_at"])
                    if finished is None or not (start <= finished <= until):
                        continue
                    completed_tasks += 1
                    if len(completed_examples) < 3:
                        completed_examples.append(row["title"])
                overdue = int(connection.execute(
                    """SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress')
                    AND due_date IS NOT NULL AND due_date<=?""",
                    (until_text,),
                ).fetchone()[0] or 0)
                overdue_examples = [dict(row) for row in connection.execute(
                    """SELECT title,priority,due_date FROM tasks
                    WHERE status IN ('todo','in_progress') AND due_date IS NOT NULL AND due_date<=?
                    ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                    due_date, id LIMIT 5""",
                    (until_text,),
                ).fetchall()]
            study_minutes = study_days = 0
            study_subjects: list[dict[str, Any]] = []
            if table_exists("study_sessions"):
                study_minutes = int(connection.execute(
                    "SELECT COALESCE(SUM(minutes),0) FROM study_sessions WHERE study_date>=? AND study_date<=?",
                    (start_text, until_text),
                ).fetchone()[0] or 0)
                study_days = int(connection.execute(
                    "SELECT COUNT(DISTINCT study_date) FROM study_sessions WHERE study_date>=? AND study_date<=?",
                    (start_text, until_text),
                ).fetchone()[0] or 0)
                if table_exists("study_subjects"):
                    study_subjects = [dict(row) for row in connection.execute(
                        """SELECT sub.title AS title, SUM(s.minutes) AS minutes
                        FROM study_sessions s JOIN study_subjects sub ON sub.id=s.subject_id
                        WHERE s.study_date>=? AND s.study_date<=?
                        GROUP BY sub.id ORDER BY minutes DESC, sub.id LIMIT 5""",
                        (start_text, until_text),
                    ).fetchall()]
            habits = {"scheduled": 0, "done": 0, "skipped": 0, "unrecorded": 0}
            if table_exists("habit_definitions") and table_exists("habit_entries"):
                marks = {(row["habit_id"], row["date"]): row["status"] for row in connection.execute(
                    "SELECT habit_id,date,status FROM habit_entries WHERE date>=? AND date<=?",
                    (start_text, until_text),
                ).fetchall()}
                for row in connection.execute(
                    "SELECT id,weekdays_json,start_date FROM habit_definitions WHERE status='active'"
                ).fetchall():
                    try:
                        weekdays = json.loads(row["weekdays_json"])
                    except (TypeError, ValueError):
                        continue
                    target = start
                    while target <= until:
                        if target.isoformat() >= row["start_date"] and target.weekday() in weekdays:
                            habits["scheduled"] += 1
                            status = marks.get((row["id"], target.isoformat()), "unrecorded")
                            if status == "done":
                                habits["done"] += 1
                            elif status == "skipped":
                                habits["skipped"] += 1
                            else:
                                habits["unrecorded"] += 1
                        target += timedelta(days=1)
            body_days = 0
            if table_exists("body_entries"):
                body_days = int(connection.execute(
                    "SELECT COUNT(*) FROM body_entries WHERE date>=? AND date<=?",
                    (start_text, until_text),
                ).fetchone()[0] or 0)
        period = f"{start_text} 至 {until_text}" + ("（本月至今）" if until < month_end else "（整月）")
        summary = (
            f"{period}：已记录日记 {journal_days} 天；完成 {completed_tasks} 项任务；"
            f"学习 {study_minutes} 分钟（{study_days} 天）；习惯完成 {habits['done']} 次、明确跳过 {habits['skipped']} 次；"
            f"身体记录 {body_days} 天。"
        )
        focus: list[dict[str, Any]] = []
        if overdue:
            focus.append({"kind": "tasks", "text": f"先清掉 {overdue} 项到期未完成任务，再开新目标。",
                          "evidence": {"open_overdue": overdue}})
        if study_subjects:
            top = study_subjects[0]
            focus.append({"kind": "study",
                          "text": f"继续给「{top['title']}」留固定时段（本月 {int(top['minutes'] or 0)} 分钟）。",
                          "evidence": {"subject": top["title"], "minutes": int(top["minutes"] or 0)}})
        elif study_minutes == 0:
            focus.append({"kind": "study", "text": "给学习安排一个最小起点（例如每周 2 次、每次 25 分钟）。",
                          "evidence": {"minutes": 0}})
        unrecorded = int(habits.get("unrecorded") or 0)
        if unrecorded >= 3 and int(habits.get("done") or 0) == 0:
            focus.append({"kind": "habits",
                          "text": f"把习惯的最小动作再调小一点（本月 {unrecorded} 天未记录、完成 0 次）。",
                          "evidence": {"unrecorded": unrecorded, "done": 0}})
        if journal_days:
            focus.append({"kind": "journal", "text": f"下个月保持每周至少记录 1 天（本月 {journal_days} 天）。",
                          "evidence": {"days_recorded": journal_days}})
        return {
            "month": month,
            "range": {"start": start_text, "end": until_text, "month_end": month_end.isoformat(),
                      "partial": until < month_end},
            "summary": summary,
            "journal": {"days_recorded": journal_days},
            "tasks": {"completed": completed_tasks, "completed_examples": completed_examples,
                      "open_overdue": overdue, "overdue_examples": overdue_examples},
            "study": {"minutes": study_minutes, "days_recorded": study_days, "subjects": study_subjects},
            "habits": habits,
            "body": {"days_recorded": body_days},
            "reflection_prompts": ["这个月哪些行动值得保留？", "哪些计划没有落地，真实阻碍是什么？", "下个月最小的一步是什么？"],
            "next_month_focus": focus[:3],
            "next_month_note": "关注点只是根据本月已保存记录整理的草稿：不会自动创建任务、不会写记忆、也不做评分。",
            "warning": "本地草稿：只汇总已保存记录与生成时的任务状态；未记录不代表没有发生，也不做评分。"
                       "习惯只统计当前仍启用的定义；任务按完成时间归属；未完成任务是生成时刻的当前状态，不是历史快照。",
        }

    def next_task(self, day: str, payload: JournalAction) -> dict[str, Any]:
        validate_day(day)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = _require_current(connection, day, payload.revision)
            if not record["next_action"]:
                raise HTTPException(422, "请先填写并保存下一步行动")
            previous = connection.execute(
                "SELECT * FROM journal_actions WHERE date=? AND revision=? AND action='task'", (day, payload.revision)
            ).fetchone()
            if previous:
                task = connection.execute("SELECT * FROM tasks WHERE id=?", (previous["entity_id"],)).fetchone()
                if not task:
                    raise HTTPException(409, "此版本曾创建的任务已被删除，不会自动重建")
                return {"date": day, "revision": payload.revision, "created": False, "idempotent": True, "task": _task_row(task)}
            task_input = TaskCreate(title=record["next_action"][:200], description=record["next_action"],
                                    goal_id=record["goal_id"], tags=["journal"])
            now = utc_now()
            cursor = connection.execute(
                """INSERT INTO tasks
                (goal_id,title,description,status,priority,tags_json,created_at,updated_at)
                VALUES (?,?,?,'todo','medium',?,?,?)""",
                (task_input.goal_id, task_input.title, task_input.description, json.dumps(task_input.tags), now, now),
            )
            task_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO journal_actions(date,revision,action,entity_id,created_at) VALUES (?,?,'task',?,?)",
                (day, payload.revision, task_id, now),
            )
            _audit(connection, "task_created", {"task_id": task_id, "goal_id": task_input.goal_id})
            _audit(connection, "journal_task_created", {"date": day, "revision": payload.revision, "task_id": task_id})
            task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return {"date": day, "revision": payload.revision, "created": True, "idempotent": False, "task": _task_row(task)}

    @staticmethod
    def _snapshot_body(record) -> tuple[str, list[tuple[str, int, int]]]:
        sections: list[str] = []
        spans: list[tuple[str, int, int]] = []
        offset = 0
        for field in TEXT_FIELDS:
            content = record[field]
            if not content:
                continue
            if sections:
                offset += 2  # The two newlines joining adjacent field sections.
            prefix = f"## {FIELD_LABELS[field]}\n\n"
            start = offset + len(prefix)
            end = start + len(content)
            sections.append(prefix + content)
            spans.append((field, start, end))
            offset = end
        return "\n\n".join(sections), spans

    @staticmethod
    def _snapshot(record) -> tuple[str, str, dict[str, Any], bytes]:
        day, revision = record["date"], record["revision"]
        title = f"{day} 每日记录"
        relative = f"20-Journal/{day}-r{revision}.md"
        body, _ = JournalService._snapshot_body(record)
        # Stable bytes make a file left by an interrupted transaction recoverable.
        metadata = {"id": f"journal-{day}-r{revision}", "type": "experience", "title": title,
                    "event_date": day, "created_at": record["created_at"], "updated_at": record["updated_at"],
                    "source_kind": "user-authored", "source_system": "torch-os-journal",
                    "visibility": "private", "journal_revision": revision}
        raw = ("---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n\n" + body + "\n").encode("utf-8")
        return relative, body, metadata, raw

    def export(self, day: str, payload: JournalAction) -> dict[str, Any]:
        validate_day(day)
        ensure_vault_writable(self.read_only, '把每日记录导出为记忆候选')
        # No call to create_note here: that helper owns additional transactions and
        # cannot preserve event_date. Use its guarded paths and the same index schema.
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = _require_current(connection, day, payload.revision)
            relative, body, metadata, raw = self._snapshot(record)
            if len(raw) > MAX_NOTE_BYTES:
                raise HTTPException(422, "导出内容超出单篇资料大小限制")
            digest = hashlib.sha256(raw).hexdigest()
            try:
                path = safe_note_path(self.vault_dir, relative)
            except ValueError as exc:
                raise HTTPException(409, "导出路径不安全，请检查 Vault 目录") from exc
            previous = connection.execute(
                "SELECT * FROM journal_actions WHERE date=? AND revision=? AND action='export'", (day, payload.revision)
            ).fetchone()
            if previous:
                document = connection.execute("SELECT * FROM documents WHERE id=?", (previous["entity_id"],)).fetchone()
                try:
                    unchanged = path.is_file() and path.stat().st_size <= MAX_NOTE_BYTES and hashlib.sha256(path.read_bytes()).hexdigest() == previous["source_hash"]
                except OSError:
                    unchanged = False
                if (not document or not document["available"] or document["path"] != relative
                        or document["revision"] != 1 or document["sha256"] != digest
                        or previous["source_hash"] != digest or not unchanged):
                    raise HTTPException(409, "已导出的来源已修改、移动或不可用；不会覆盖，请保存新版本再导出")
                return self._export_result(connection, day, payload.revision, document, False)
            collisions = connection.execute(
                "SELECT * FROM documents WHERE path=? OR source_key=?", (relative, metadata["id"])
            ).fetchall()
            if collisions:
                # A vault scan can index a fully published file after the original
                # SQL transaction failed. Recover only that exact source identity;
                # never recreate its candidates or reset subsequent user decisions.
                document = collisions[0] if len(collisions) == 1 else None
                try:
                    unchanged = (path.is_file() and path.stat().st_size <= MAX_NOTE_BYTES
                                 and path.read_bytes() == raw)
                    indexed_metadata = json.loads(document["metadata_json"]) if document else None
                    metadata_matches = json.dumps(indexed_metadata, sort_keys=True, ensure_ascii=False) == json.dumps(metadata, sort_keys=True, ensure_ascii=False)
                except (OSError, TypeError, ValueError):
                    unchanged = metadata_matches = False
                if (not document or not unchanged or not metadata_matches
                        or document["path"] != relative or document["source_key"] != metadata["id"]
                        or document["sha256"] != digest or document["content"] != body
                        or document["title"] != metadata["title"] or document["revision"] != 1
                        or not document["available"] or document["privacy"] != "personal"
                        or document["last_known_path"] is not None):
                    raise HTTPException(409, "导出路径或来源 ID 已被其他内容使用，不会覆盖已有资料")
                connection.execute(
                    """INSERT INTO journal_actions(date,revision,action,entity_id,source_path,source_hash,created_at)
                    VALUES (?,?,'export',?,?,?,?)""",
                    (day, payload.revision, document["id"], relative, digest, utc_now()),
                )
                _audit(connection, "journal_exported", {"date": day, "revision": payload.revision,
                                                        "document_id": document["id"], "recovered": True})
                return self._export_result(connection, day, payload.revision, document, False, recovered=True)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    # Publish a complete file without replacing a destination. The
                    # scanner skips hidden staging directories, including on Windows.
                    with tempfile.TemporaryDirectory(prefix=".journal-export-", dir=path.parent) as temporary:
                        staged = Path(temporary) / "snapshot.md"
                        with staged.open("xb") as handle:
                            handle.write(raw)
                            handle.flush()
                            os.fsync(handle.fileno())
                        safe_note_path(self.vault_dir, relative)
                        os.link(staged, path)
                except FileExistsError:
                    # A previous attempt may have written the file before a crash.
                    if path.stat().st_size > MAX_NOTE_BYTES or path.read_bytes() != raw:
                        raise HTTPException(409, "导出文件已存在且内容不同，不会覆盖")
            except (OSError, ValueError) as exc:
                raise HTTPException(409, "无法安全写入导出目录，请检查目录权限、同名文件或硬链接支持") from exc
            now = utc_now()
            cursor = connection.execute(
                """INSERT INTO documents(path,title,content,sha256,metadata_json,indexed_at,source_key)
                VALUES (?,?,?,?,?,?,?)""",
                (relative, metadata["title"], body, digest, json.dumps(metadata, ensure_ascii=False), now, metadata["id"]),
            )
            document_id = int(cursor.lastrowid)
            # Preserve every authored character, including long paragraphs and headings.
            # Reuse the same builder as the snapshot: repeated text never causes an
            # ambiguous str.find match, and whitespace at chunk boundaries is kept.
            _, spans = self._snapshot_body(record)
            candidate_count = 0
            for field, field_start, field_end in spans:
                for start in range(field_start, field_end, MAX_SEGMENT_CHARACTERS):
                    end = min(start + MAX_SEGMENT_CHARACTERS, field_end)
                    quote = body[start:end]
                    memory = connection.execute(
                        """INSERT INTO memories
                        (document_id,kind,title,content,status,confidence,source_quote,created_at,updated_at,source_revision,
                         source_start_line,source_end_line,source_start_offset,source_end_offset,source_location_basis)
                        VALUES (?,'experience',?,?,'candidate','explicit',?,?,?,1,?,?,?,?,'document_body')""",
                        (document_id, f"{day} · {FIELD_LABELS[field]}", quote, quote, now, now,
                         body.count("\n", 0, start) + 1, body.count("\n", 0, end - 1) + 1, start, end),
                    )
                    candidate_count += 1
                    memory_row = connection.execute("SELECT * FROM memories WHERE id=?", (memory.lastrowid,)).fetchone()
                    self.database._event(connection, int(memory.lastrowid), "created", {}, dict(memory_row))
            coverage = {
                "extraction_version": EXTRACTION_VERSION,
                "candidate_count": candidate_count, "total_candidate_segments": candidate_count,
                "omitted_segments": 0, "truncated": False, "coverage_complete": True,
                "candidate_limit": None, "segment_char_limit": MAX_SEGMENT_CHARACTERS,
                "excluded_heading_lines": len(spans), "excluded_code_blocks": 0, "excluded_code_lines": 0,
                "source_location_basis": "document_body",
                "location_note": "行号从已移除 YAML 元数据的快照正文第 1 行计起；Unicode 字符位置从 0 开始，结束位置不含在内。",
                "scope_note": "每日记录的四个表单字段按字符分块完整进入候选，保留字段原文中的标题、代码和空白；仅排除系统生成的字段标题。完整仅表示原文字符已进入候选，不代表 AI 已理解、语义事实覆盖或记忆已确认。",
                "warnings": [],
            }
            connection.execute("UPDATE documents SET coverage_summary_json=? WHERE id=?",
                               (json.dumps(coverage, ensure_ascii=False), document_id))
            connection.execute(
                """INSERT INTO journal_actions(date,revision,action,entity_id,source_path,source_hash,created_at)
                VALUES (?,?,'export',?,?,?,?)""", (day, payload.revision, document_id, relative, digest, now),
            )
            _audit(connection, "note_created", {"document_id": document_id})
            _audit(connection, "journal_exported", {"date": day, "revision": payload.revision, "document_id": document_id})
            document = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            return self._export_result(connection, day, payload.revision, document, True)

    @staticmethod
    def _export_result(connection, day, revision, document, created, recovered=False):
        memories = [dict(row) for row in connection.execute(
            """SELECT m.*,d.path AS source_path FROM memories m JOIN documents d ON d.id=m.document_id
            WHERE m.document_id=? ORDER BY m.id""", (document["id"],)
        ).fetchall()]
        return {"date": day, "revision": revision, "created": created, "idempotent": not created, "recovered": recovered,
                "document": Database._document_record(document), "memories": memories}


def create_journal_router(database: Database, vault_dir: Path, prefix: str = "/api", read_only: bool = False) -> APIRouter:
    service = JournalService(database, vault_dir, read_only=read_only)
    router = APIRouter(prefix=prefix, tags=["journal"])

    @router.get("/journal")
    def list_journal(from_date: str = Query(), to_date: str = Query()):
        return service.list(from_date, to_date)

    @router.get("/journal/{day}")
    def get_journal(day: str):
        return service.get(day)

    @router.put("/journal/{day}")
    def save_journal(day: str, payload: JournalWrite):
        return service.save(day, payload)

    @router.post("/journal/{day}/review")
    def review_journal(day: str):
        return service.review(day)

    @router.post("/journal/{day}/next-task")
    def next_task(day: str, payload: JournalAction):
        return service.next_task(day, payload)

    @router.post("/journal/{day}/export")
    def export_journal(day: str, payload: JournalAction):
        return service.export(day, payload)

    @router.post("/journal/monthly-review")
    def monthly_review(payload: MonthReviewRequest):
        """Read-only deterministic month draft; no audit event, no writes."""
        return service.monthly_review(payload.month)

    return router
