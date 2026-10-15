"""Read-only 'know me' summary and evidence preview endpoints.

Nothing in this module writes conversations, audit events or memory decisions.
Preview runs the exact same retrieval as /api/chat so the user can inspect the
data boundary before any cloud call.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .db import Database
from .vault import scan_vault

SHANGHAI = ZoneInfo('Asia/Shanghai')
EVIDENCE_LIMIT = 6

USAGE_RULES = [
    '只有已确认且来源有效的记忆会参与回答。',
    '每次回答最多使用 6 条最相关的证据，来源与片段随回答展示。',
    '候选、已拒绝、已归档记忆不会进入云端上下文。',
    '每次保存的回答会记录它引用了哪些已确认记忆，便于你回看使用记录。',
    '云端调用默认关闭；关闭时回答在本地生成。',
]

USAGE_WINDOW_DAYS = 30
RECENT_USAGE_LIMIT = 8
ACTIVITY_DEFAULT_DAYS = 30
ACTIVITY_MIN_DAYS = 7
ACTIVITY_MAX_DAYS = 90

ACTIVITY_NOTES = [
    '只统计你在这段时间实际填写过的记录，空白日期不代表没有发生。',
    '习惯只计入明确保存的完成或跳过记录；未记录不计为失败。',
    '学习只累加你记录的学习时长，不代表学习效果。',
    '身体记录只统计填写天数，不读取体重、睡眠与运动数值。',
    '这里不评价、不打分、不推断人格，也不作为任何自动决定。',
]

PREVIEW_NOTES = [
    '预览不调用模型，不创建对话，也不改变记忆审核状态。',
    '开启云端后，以上片段才会随问题发送；候选记忆不会发送。',
]


def shanghai_today() -> date:
    return datetime.now(SHANGHAI).date()


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=4000)


def _table_exists(connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _count(connection, table: str, sql: str, params: tuple = ()) -> int:
    if not _table_exists(connection, table):
        return 0
    return int(connection.execute(sql, params).fetchone()['n'] or 0)


def _evidence_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same citation shape /api/chat returns after an answer."""
    return [{
        'memory_id': row['id'],
        'document_id': row['document_id'],
        'path': row['path'],
        'title': row['title'],
        'quote': row['content'],
        'source_quote': row.get('source_quote'),
        'confidence': row['confidence'],
        'revision': row['revision'],
        'source_date': row.get('source_date'),
        'valid_from': row.get('valid_from'),
        'valid_until': row.get('valid_until'),
        'low_confidence': bool(row.get('low_confidence')),
        'semantic_score': row.get('semantic_score'),
        'lexical_score': row.get('lexical_score'),
        **{key: row.get(key) for key in (
            'source_start_line', 'source_end_line',
            'source_start_offset', 'source_end_offset', 'source_location_basis',
        )},
    } for row in rows]


class ProfileService:
    """Aggregates the current personal-model inventory without side effects."""

    def __init__(self, database: Database):
        self.database = database

    def _rhythm(self, today) -> dict[str, int]:
        since = (today - timedelta(days=6)).isoformat()
        until = today.isoformat()
        with self.database.connect() as connection:
            return {
                'journal_days_last_7': _count(
                    connection, 'journal_entries',
                    'SELECT COUNT(*) AS n FROM journal_entries WHERE date>=? AND date<=?', (since, until)),
                'habits_active': _count(
                    connection, 'habit_definitions',
                    "SELECT COUNT(*) AS n FROM habit_definitions WHERE status='active'"),
                'habit_done_last_7': _count(
                    connection, 'habit_entries',
                    "SELECT COUNT(*) AS n FROM habit_entries WHERE status='done' AND date>=? AND date<=?", (since, until)),
                'study_minutes_last_7': _count(
                    connection, 'study_sessions',
                    'SELECT COALESCE(SUM(minutes),0) AS n FROM study_sessions WHERE study_date>=? AND study_date<=?', (since, until)),
                'study_subjects_active': _count(
                    connection, 'study_subjects',
                    "SELECT COUNT(*) AS n FROM study_subjects WHERE status='active'"),
                'body_record_days_last_7': _count(
                    connection, 'body_entries',
                    'SELECT COUNT(*) AS n FROM body_entries WHERE date>=? AND date<=?', (since, until)),
            }

    def activity(self, days: int) -> dict[str, Any]:
        """Recorded-day coverage for one window. Counts only; no scores, no inference."""
        today = shanghai_today()
        since = (today - timedelta(days=days - 1)).isoformat()
        until = today.isoformat()
        window = (since, until)
        with self.database.connect() as connection:
            journal_days = _count(
                connection, 'journal_entries',
                'SELECT COUNT(DISTINCT date) AS n FROM journal_entries WHERE date>=? AND date<=?', window)
            body_days = _count(
                connection, 'body_entries',
                'SELECT COUNT(DISTINCT date) AS n FROM body_entries WHERE date>=? AND date<=?', window)
            habit_days = _count(
                connection, 'habit_entries',
                'SELECT COUNT(DISTINCT date) AS n FROM habit_entries WHERE date>=? AND date<=?', window)
            habit_done = _count(
                connection, 'habit_entries',
                "SELECT COUNT(*) AS n FROM habit_entries WHERE status='done' AND date>=? AND date<=?", window)
            habit_skipped = _count(
                connection, 'habit_entries',
                "SELECT COUNT(*) AS n FROM habit_entries WHERE status='skipped' AND date>=? AND date<=?", window)
            habits_active = _count(
                connection, 'habit_definitions',
                "SELECT COUNT(*) AS n FROM habit_definitions WHERE status='active'")
            study_days = _count(
                connection, 'study_sessions',
                'SELECT COUNT(DISTINCT study_date) AS n FROM study_sessions WHERE study_date>=? AND study_date<=?', window)
            study_minutes = _count(
                connection, 'study_sessions',
                'SELECT COALESCE(SUM(minutes),0) AS n FROM study_sessions WHERE study_date>=? AND study_date<=?', window)
            study_subjects = _count(
                connection, 'study_sessions',
                'SELECT COUNT(DISTINCT subject_id) AS n FROM study_sessions WHERE study_date>=? AND study_date<=?', window)
        return {
            'generated_at': datetime.now(SHANGHAI).isoformat(),
            'window': {'days': days, 'start': since, 'end': until},
            'journal': {'days_recorded': journal_days},
            'body': {'days_recorded': body_days},
            'habits': {
                'days_recorded': habit_days,
                'done': habit_done,
                'skipped': habit_skipped,
                'active_definitions': habits_active,
            },
            'study': {
                'days_recorded': study_days,
                'minutes': study_minutes,
                'subjects_recorded': study_subjects,
            },
            'notes': list(ACTIVITY_NOTES),
        }

    def summary(self, version: str, vault_dir: Path | None = None) -> dict[str, Any]:
        overview = self.database.active_memory_overview()
        usage_stats = self.database.memory_usage_stats(USAGE_WINDOW_DAYS)
        active_memories = []
        used_last_30 = 0
        for item in overview['items']:
            usage = usage_stats.get(int(item['id']), {'uses': 0, 'last_used_at': None})
            if usage['uses']:
                used_last_30 += 1
            active_memories.append({**item, 'usage': usage})
        with self.database.connect() as connection:
            documents_available = _count(connection, 'documents', 'SELECT COUNT(*) AS n FROM documents WHERE available=1')
            documents_personal = _count(connection, 'documents', "SELECT COUNT(*) AS n FROM documents WHERE available=1 AND privacy='personal'")
            goal_counts = {row['status']: int(row['n']) for row in connection.execute(
                'SELECT status, COUNT(*) AS n FROM goals GROUP BY status').fetchall()}
            task_counts = {row['status']: int(row['n']) for row in connection.execute(
                'SELECT status, COUNT(*) AS n FROM tasks GROUP BY status').fetchall()}
            goal_items = [dict(row) for row in connection.execute(
                "SELECT id, title, status, progress, updated_at FROM goals WHERE status='active' ORDER BY updated_at DESC, id DESC LIMIT 10"
            ).fetchall()]
        status_counts = overview['status_counts']
        payload = {
            'generated_at': datetime.now(SHANGHAI).isoformat(),
            'version': version,
            'documents': {
                'available': documents_available,
                'personal': documents_personal,
                'other_privacy': documents_available - documents_personal,
            },
            'memories': {
                'active': status_counts.get('active', 0),
                'candidate': status_counts.get('candidate', 0),
                'rejected': status_counts.get('rejected', 0),
                'archived': status_counts.get('archived', 0),
                'active_usable': overview['usable_total'],
                'active_blocked': {**overview['blocked'], 'expired': overview['expired']},
                'by_kind': overview['by_kind'],
            },
            'active_memories': active_memories,
            'usage': {
                'window_days': USAGE_WINDOW_DAYS,
                'memories_used': used_last_30,
                'recent': self.database.recent_memory_usage(RECENT_USAGE_LIMIT),
            },
            'candidate_pool': self.database.candidate_pool_distribution(),
            'goals': {
                'active': goal_counts.get('active', 0),
                'paused': goal_counts.get('paused', 0),
                'completed': goal_counts.get('completed', 0),
                'open_tasks': task_counts.get('todo', 0) + task_counts.get('in_progress', 0),
                'completed_tasks': task_counts.get('completed', 0),
                'items': goal_items,
            },
            'rhythm': self._rhythm(shanghai_today()),
            'usage_rules': list(USAGE_RULES),
            'excluded': {
                'candidate': status_counts.get('candidate', 0),
                'rejected': status_counts.get('rejected', 0),
                'archived': status_counts.get('archived', 0),
            },
            'relationship_vault': {'connected': False, 'note': '关系数据分区尚未接入。'},
        }
        if vault_dir is not None:
            payload['library_health'] = self.library_health(vault_dir)
        return payload

    def library_health(self, vault_dir: Path) -> dict[str, Any]:
        """Read-only digest of the vault and the index: counts only, no paths or text."""
        from .vault_audit import run_audit
        from .vault_connection import describe_vault, read_only_enabled

        described = describe_vault(vault_dir, read_only=read_only_enabled())
        documents = self.database.list_documents()
        report = run_audit(vault_dir)
        counts = report.get('counts') or {}
        by_code = {code: int(value) for code, value in counts.items() if value}
        return {
            'documents': len(documents),
            'recent': [
                {'title': row.get('title'), 'indexed_at': row.get('indexed_at')}
                for row in documents[:3]
            ],
            'vault': {
                'exists': described.get('exists'),
                'looks_like_obsidian': described.get('looks_like_obsidian'),
                'markdown_files': described.get('markdown_files'),
                'read_only': described.get('read_only'),
                'skipped_directories': described.get('skipped_directories'),
            },
            'audit': {
                'checked': int(report.get('checked_files') or 0),
                'ok': int(report.get('ok_files') or 0),
                'issues': len(report.get('issues') or []),
                'truncated': int(report.get('truncated') or 0),
                'by_code': by_code,
                'generated_at': report.get('generated_at'),
            },
            'note': '只读摘要：不含正文、不含具体问题路径；体检不会修改或修复任何文件。',
        }


def create_profile_router(database: Database, vault_dir: Path, data_lock, version: str,
                          prefix: str = '/api') -> APIRouter:
    router = APIRouter(prefix=prefix)
    service = ProfileService(database)

    @router.get('/profile')
    def profile_summary():
        return service.summary(version, vault_dir)

    @router.get('/profile/activity')
    def profile_activity(days: int = Query(default=ACTIVITY_DEFAULT_DAYS, ge=ACTIVITY_MIN_DAYS, le=ACTIVITY_MAX_DAYS)):
        """Coverage of explicitly recorded days in the recent window; strictly read-only."""
        return service.activity(days)

    @router.get('/memories/{memory_id}/usage')
    def memory_usage(memory_id: int):
        """Reverse references for one memory; includes withdrawn candidates on purpose."""
        detail = database.memory_usage_detail(memory_id)
        if detail is None:
            raise HTTPException(404, '记忆不存在')
        return detail

    @router.post('/chat/preview')
    async def chat_preview(payload: PreviewRequest):
        # Same scan + retrieval as /api/chat, but strictly read-only.
        async with data_lock:
            await run_in_threadpool(scan_vault, vault_dir, database)
            result = database.preview_memories(payload.message, limit=EVIDENCE_LIMIT)
        return {
            'query': payload.message,
            'evidence': _evidence_payload(result['evidence']),
            'counts': result['counts'],
            'excluded': result['excluded'],
            'notes': list(PREVIEW_NOTES),
        }

    return router
