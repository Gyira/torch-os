"""Read-only local tools for agents: today, memories, goals, habits, journal,
profile, vault, body, documents, imports, vault_audit, identity.

Every tool is a GET, returns structure and (where noted) the user's own text, and
never writes anything except one count-only audit row per call. No write tool is
exposed here on purpose: an agent may look, but changes still go through the
review surfaces the user controls.

Goals, tasks and body records are read from Markdown (2026-10-22); the retired
SQLite goals/learning/body backends are no longer consulted.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from . import body_source, goal_notes, journal_note, task_reports
from .attachment_plan import ATTACHMENT_DIRECTORY
from .db import Database
from .habits import HabitService
from .identity import read_identity
from .vault import parse_text
from .vault_connection import describe_vault, read_only_enabled
from .vault_audit import run_audit
from .wolai_import import HISTORY_DIRECTORY, IMPORT_DIRECTORY

SHANGHAI = ZoneInfo('Asia/Shanghai')
MAX_TOOL_LIMIT = 10


def shanghai_today() -> date:
    from datetime import datetime
    return datetime.now(SHANGHAI).date()


def tool_catalog() -> dict[str, Any]:
    tools = [
        {'name': 'today', 'method': 'GET', 'path': '/api/tools/today',
         'summary': '某一天的结构化概况：日记字段、Markdown 任务计数与到期标题、习惯状态、身体记录标记、草稿数',
         'reads_text': True, 'parameters': ['day?(YYYY-MM-DD，默认今天)']},
        {'name': 'memories', 'method': 'GET', 'path': '/api/tools/memories?q=&limit=',
         'summary': '按问题检索“已确认且来源有效”的记忆，含来源路径与低置信标记；候选/拒绝/归档不会出现',
         'reads_text': True, 'parameters': ['q(必填)', 'limit(1–10，默认 6)']},
        {'name': 'goals', 'method': 'GET', 'path': '/api/tools/goals',
         'summary': '人生路目标笔记与未完成 Markdown 任务（标题、截止日期、关联目标路径）',
         'reads_text': True, 'parameters': ['limit(1–10，默认 5)']},
        {'name': 'habits', 'method': 'GET', 'path': '/api/tools/habits?days=',
         'summary': '每个启用习惯最近 N 天（7–90）的完成/跳过/未记录计数，不含备注',
         'reads_text': False, 'parameters': ['days(7–90，默认 30)']},
        {'name': 'journal', 'method': 'GET', 'path': '/api/tools/journal?from=&to=',
         'summary': '一段日期里哪些天有记录、是否写了下一步（不含日记正文）',
         'reads_text': False, 'parameters': ['from(YYYY-MM-DD)', 'to(YYYY-MM-DD，最多 90 天)']},
        {'name': 'profile', 'method': 'GET', 'path': '/api/tools/profile',
         'summary': '认识我的计数快照：可用/待确认记忆、资料数、人生路进行中目标、未完成 Markdown 任务',
         'reads_text': False, 'parameters': []},
        {'name': 'vault', 'method': 'GET', 'path': '/api/tools/vault',
         'summary': 'Vault 现状：路径、是否 Obsidian 库、Markdown 篇数、跳过的目录、是否只读模式',
         'reads_text': False, 'parameters': []},
        {'name': 'body', 'method': 'GET', 'path': '/api/tools/body?days=',
         'summary': '来自健康月度 Markdown 表的最近 N 天（7–90）汇总：有记录天数、睡眠与运动分钟合计（不含体重数值、不做健康评价）',
         'reads_text': False, 'parameters': ['days(7–90，默认 7)']},
        {'name': 'documents', 'method': 'GET', 'path': '/api/tools/documents?limit=',
         'summary': '已索引资料摘要：总数、最近索引的几篇标题与路径、是否有未索引的变化',
         'reads_text': False, 'parameters': ['limit(1–10，默认 5)']},
        {'name': 'imports', 'method': 'GET', 'path': '/api/tools/imports',
         'summary': '资料导入健康：导入包数量、已导入笔记、历史版本、索引待刷新笔记与本地附件计数（不含文件名与正文）',
         'reads_text': False, 'parameters': []},
        {'name': 'vault_audit', 'method': 'GET', 'path': '/api/tools/vault_audit',
         'summary': 'Vault 只读体检：检查篇数与各类问题计数（不返回具体路径）',
         'reads_text': False, 'parameters': []},
        {'name': 'identity', 'method': 'GET', 'path': '/api/tools/identity',
         'summary': '我是谁：姓名、座右铭、三观与自由补充（来自 Vault 的 01-Identity/身份（identity）.md，是用户自己写下的内容）',
         'reads_text': True, 'parameters': []},
    ]
    return {
        'schema': 1,
        'generated_at': shanghai_today().isoformat(),
        'count': len(tools),
        'read_only': True,
        'note': '全部工具只读且只在本机回环地址 + 本地会话下可用；写操作（确认记忆、建任务、写日记）永远不会通过这些接口开放。',
        'tools': tools,
    }


class ToolService:
    def __init__(self, database: Database, vault_dir: Path):
        self.database = database
        self.vault_dir = Path(vault_dir)
        self._habits = HabitService(database, vault_dir)

    def _day(self, value: str | None) -> date:
        if not value:
            return shanghai_today()
        try:
            return date.fromisoformat(str(value))
        except ValueError as error:
            raise HTTPException(422, 'day 必须是 YYYY-MM-DD') from error

    def identity(self) -> dict[str, Any]:
        """The user's own words about who they are (Vault-backed, never invented)."""
        payload = read_identity(self.vault_dir)
        return {
            'name': payload.get('name') or '',
            'motto': payload.get('motto') or '',
            'worldview': (payload.get('worldview') or '')[:500],
            'lifeview': (payload.get('lifeview') or '')[:500],
            'values': (payload.get('values') or '')[:500],
            'notes': (payload.get('notes') or '')[:800],
            'path': payload.get('path'),
            'updated_at': payload.get('updated_at'),
            'note': '空字段表示用户还没写；不要替用户编造三观或座右铭。',
        }

    def today(self, day: str | None = None) -> dict[str, Any]:
        target = self._day(day)
        stamp = target.isoformat()
        note = journal_note.read_note(self.vault_dir, stamp)
        try:
            _metadata, journal_body = parse_text(note.get("content") or "")
        except ValueError:
            journal_body = note.get("content") or ""
        next_action = journal_note.next_action_text(note.get("content") or "")
        open_tasks = task_reports.open_tasks(self.vault_dir)
        due_today = [task for task in open_tasks if task.get('date') == stamp]
        overdue = [task for task in open_tasks if task.get('date') and task['date'] < stamp]
        habit_rows = []
        for habit in self._habits.list('active'):
            summary = self._habits.summary(habit['id'], stamp, stamp)
            entry = (summary.get('entries') or [{}])[0]
            habit_rows.append({'title': habit.get('title'), 'status': entry.get('status'),
                               'scheduled': entry.get('scheduled')})
        body = body_source.day_entry(self.vault_dir, stamp)
        with self.database.connect() as connection:
            drafts = connection.execute(
                'SELECT COUNT(*) AS total FROM inbox_items WHERE source_date=? AND read_at IS NULL AND dismissed_at IS NULL',
                (stamp,),
            ).fetchone()['total']
        return {
            'date': stamp,
            'journal': {
                'recorded': bool(note.get('exists')),
                'updated_at': note.get('updated_at'),
                'content': journal_body,
                'highlights': '',
                'obstacles': '',
                'next_action': next_action,
                'mood': '',
                'diet_summary': '',
            },
            'tasks': {
                'open': len(open_tasks), 'due_today': len(due_today), 'overdue': len(overdue),
                'due_titles': [task.get('title') for task in (due_today + overdue)][:5],
            },
            'habits': habit_rows,
            'body': {'recorded': bool(body.get('recorded')),
                     'sleep_minutes': body.get('sleep_minutes'),
                     'exercise_minutes': body.get('exercise_minutes')},
            'inbox_drafts': drafts,
            'note': '只返回这一天的内容；数值为空表示未记录，不代表当天没有发生。',
        }

    def memories(self, query: str, limit: int = 6) -> dict[str, Any]:
        text = str(query or '').strip()
        if not text:
            raise HTTPException(422, 'q 不能为空')
        size = max(1, min(MAX_TOOL_LIMIT, int(limit)))
        rows = self.database.search_memories(text, size)
        return {
            'query': text,
            'count': len(rows),
            'memories': [
                {'id': row.get('id'), 'title': row.get('title'), 'content': row.get('content'),
                 'source_path': row.get('source_path'), 'source_date': row.get('source_date'),
                 'low_confidence': bool(row.get('low_confidence'))}
                for row in rows
            ],
            'note': '只包含已确认且来源有效的记忆；候选、拒绝与归档不会出现。',
        }

    def goals(self, limit: int = 5) -> dict[str, Any]:
        size = max(1, min(MAX_TOOL_LIMIT, int(limit)))
        goals = goal_notes.active_goals(self.vault_dir, limit=size)
        open_tasks = task_reports.open_tasks(self.vault_dir)
        next_tasks = task_reports.sorted_due(open_tasks, size)
        return {
            'goals': [
                {'id': goal.get('id'), 'title': goal.get('title'), 'category': goal.get('chapter'),
                 'target_date': goal.get('date') or ''}
                for goal in goals
            ],
            'open_task_count': len(open_tasks),
            'next_tasks': [
                {'title': task.get('title'), 'due_date': task.get('date') or None,
                 'goal': task.get('goal') or ''}
                for task in next_tasks
            ],
            'note': '目标来自人生路目标笔记，任务来自 Markdown 任务笔记；只列未完成项。',
        }

    def habits(self, days: int = 30) -> dict[str, Any]:
        window = max(7, min(90, int(days)))
        last = shanghai_today()
        first = last - timedelta(days=window - 1)
        rows = []
        for habit in self._habits.list('active'):
            summary = self._habits.summary(habit['id'], first.isoformat(), last.isoformat())
            rows.append({'title': habit.get('title'), 'scheduled_days': summary.get('scheduled_days'),
                         'done': summary.get('done'), 'skipped': summary.get('skipped'),
                         'unrecorded': summary.get('unrecorded')})
        return {'from_date': first.isoformat(), 'to_date': last.isoformat(), 'habits': rows,
                'note': '未记录不等于失败；这里没有备注与原因。'}

    def journal_range(self, from_date: str, to_date: str) -> dict[str, Any]:
        first = self._day(from_date)
        last = self._day(to_date)
        if last < first:
            raise HTTPException(422, '日期顺序应为 from ≤ to')
        if (last - first).days >= 90:
            raise HTTPException(422, '一次最多查询 90 天')
        days: list[dict[str, Any]] = []
        current = last
        while current >= first:
            stamp = current.isoformat()
            note = journal_note.read_note(self.vault_dir, stamp)
            if note.get('exists'):
                try:
                    _metadata, body = parse_text(note.get('content') or "")
                except ValueError:
                    body = note.get('content') or ""
                days.append({
                    'date': stamp, 'recorded': True,
                    'has_next_step': bool(journal_note.next_action_text(note.get('content') or '')),
                    'has_body': bool(body.strip()),
                })
            current -= timedelta(days=1)
        return {
            'from_date': first.isoformat(), 'to_date': last.isoformat(),
            'days': days,
            'note': '只列出是否记录与是否写了下一步，不包含日记正文。',
        }

    def profile(self) -> dict[str, Any]:
        memories = self.database.list_memories()
        active = [item for item in memories if item.get('status') == 'active']
        candidate = [item for item in memories if item.get('status') == 'candidate']
        return {
            'confirmed_memories': len(active),
            'pending_candidates': len(candidate),
            'documents': len(self.database.list_documents()),
            'active_goals': goal_notes.count_active(self.vault_dir),
            'open_tasks': len(task_reports.open_tasks(self.vault_dir)),
            'note': '只有已确认且来源有效的记忆会参与问答；候选需要你逐条确认。',
        }

    def vault(self) -> dict[str, Any]:
        payload = describe_vault(self.vault_dir, read_only=read_only_enabled())
        payload['sample_paths'] = payload.get('sample_paths', [])[:3]
        return payload

    def body_summary(self, days: int = 7) -> dict[str, Any]:
        window = max(7, min(90, int(days)))
        last = shanghai_today()
        first = last - timedelta(days=window - 1)
        rows = body_source.entries_between(self.vault_dir, first, last)
        return {
            'from_date': first.isoformat(), 'to_date': last.isoformat(),
            'recorded_days': len(rows),
            'sleep_minutes_total': sum(int(row.get('sleep_minutes') or 0) for row in rows),
            'exercise_minutes_total': sum(int(row.get('exercise_minutes') or 0) for row in rows),
            'note': '来自健康系统每日记录（旧库回退到健康月度表）；只统计记录过的天数与分钟合计，不含体重数值，也不做健康评价。',
        }

    def documents_summary(self, limit: int = 5) -> dict[str, Any]:
        size = max(1, min(MAX_TOOL_LIMIT, int(limit)))
        rows = self.database.list_documents()
        recent = [{'title': row.get('title'), 'path': row.get('path'), 'indexed_at': row.get('indexed_at')}
                  for row in rows[:size]]
        return {'documents': len(rows), 'recent': recent,
                'note': '这是索引摘要，不含正文；原文件从未被修改。'}

    def imports_summary(self) -> dict[str, Any]:
        """Count-only import health; never returns file names or note text."""
        imports_dir = self.database.path.parent / 'imports'
        packages = 0
        if imports_dir.exists():
            packages = sum(1 for path in imports_dir.rglob('*.zip')
                           if path.is_file() and not path.is_symlink())
        note_dir = self.vault_dir / IMPORT_DIRECTORY
        notes = [path for path in note_dir.glob('wolai_*.md') if path.is_file()] if note_dir.exists() else []
        digests = self.database.document_digests()
        stale = 0
        for path in notes:
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                relative = path.relative_to(self.vault_dir).as_posix()
            except (OSError, ValueError):
                stale += 1
                continue
            if digests.get(relative) != digest:
                stale += 1
        history_dir = self.vault_dir / HISTORY_DIRECTORY
        history_versions = 0
        if history_dir.exists():
            history_versions = sum(1 for path in history_dir.rglob('*.md') if path.is_file())
        attachment_dir = self.vault_dir / ATTACHMENT_DIRECTORY
        attachments = [path for path in attachment_dir.iterdir() if path.is_file()] if attachment_dir.exists() else []
        return {
            'packages': packages,
            'imported_notes': len(notes),
            'history_versions': history_versions,
            'notes_index_stale': stale,
            'attachment_files': len(attachments),
            'attachment_bytes': sum(path.stat().st_size for path in attachments),
            'generated_at': shanghai_today().isoformat(),
            'note': '只统计数量：不含导入包名、笔记正文或附件内容；写操作永远不通过这些接口开放。',
        }

    def vault_audit(self) -> dict[str, Any]:
        report = run_audit(self.vault_dir)
        issues = report.get('issues') or []
        by_code: dict[str, int] = {}
        for issue in issues:
            code = str(issue.get('code') or 'unknown')
            by_code[code] = by_code.get(code, 0) + 1
        return {
            'checked': int(report.get('checked_files') or 0),
            'ok_files': int(report.get('ok_files') or 0),
            'issues': len(issues),
            'by_code': by_code,
            'generated_at': report.get('generated_at'),
            'note': '只读体检：不返回具体路径，也不修改任何文件。',
        }


def create_tools_router(database: Database, vault_dir: Path, prefix: str = '/api') -> APIRouter:
    service = ToolService(database, vault_dir)
    router = APIRouter(prefix=prefix, tags=['tools'])

    def run(name: str, action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        result = action()
        items = result.get('count')
        if items is None and name == 'imports':
            items = result.get('imported_notes')
        if items is None:
            for key in ('memories', 'goals', 'habits', 'days'):
                value = result.get(key)
                if isinstance(value, list):
                    items = len(value)
                    break
        database.audit('tool_called', {'tool': name, 'items': items if isinstance(items, int) else None})
        return result

    @router.get('/tools')
    def catalog():
        return tool_catalog()

    @router.get('/tools/today')
    def today(day: str | None = Query(default=None)):
        return run('today', lambda: service.today(day))

    @router.get('/tools/memories')
    def memories(q: str = Query(min_length=1, max_length=200), limit: int = Query(default=6, ge=1, le=MAX_TOOL_LIMIT)):
        return run('memories', lambda: service.memories(q, limit))

    @router.get('/tools/goals')
    def goals(limit: int = Query(default=5, ge=1, le=MAX_TOOL_LIMIT)):
        return run('goals', lambda: service.goals(limit))

    @router.get('/tools/habits')
    def habits(days: int = Query(default=30, ge=7, le=90)):
        return run('habits', lambda: service.habits(days))

    @router.get('/tools/journal')
    def journal(from_date: str = Query(alias='from'), to_date: str = Query(alias='to')):
        return run('journal', lambda: service.journal_range(from_date, to_date))

    @router.get('/tools/profile')
    def profile():
        return run('profile', service.profile)

    @router.get('/tools/vault')
    def vault():
        return run('vault', service.vault)

    @router.get('/tools/body')
    def body(days: int = Query(default=7, ge=7, le=90)):
        return run('body', lambda: service.body_summary(days))

    @router.get('/tools/documents')
    def documents(limit: int = Query(default=5, ge=1, le=MAX_TOOL_LIMIT)):
        return run('documents', lambda: service.documents_summary(limit))

    @router.get('/tools/imports')
    def imports():
        return run('imports', service.imports_summary)

    @router.get('/tools/vault_audit')
    def vault_audit():
        return run('vault_audit', service.vault_audit)

    @router.get('/tools/identity')
    def identity():
        return run('identity', service.identity)

    return router
