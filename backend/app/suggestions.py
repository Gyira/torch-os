"""Proactive suggestion cards: candidates to think about, never actions to run.

Every card is generated from local data by deterministic rules and carries the
numbers it came from. Nothing here changes tasks, habits, memories, journal or
anything else; the only writes are the card itself plus its dismiss state.
"""
from __future__ import annotations

import json
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .db import Database, utc_now
from .goals import GoalTaskService, TaskCreate
from .habits import HabitService
from .learning import LearningService
from .vault_connection import describe_vault, read_only_enabled
from .wolai_import import IMPORT_DIRECTORY

SHANGHAI = ZoneInfo('Asia/Shanghai')
CANDIDATE_BACKLOG_THRESHOLD = 20
STREAK_GAP_DAYS = (1, 2)
COLD_SUBJECT_DAYS = 14
SCHEMA = """
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    action_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    dismissed_at TEXT,
    snoozed_until TEXT,
    done_at TEXT,
    link_view TEXT,
    link_label TEXT,
    task_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_suggestions_open ON suggestions(dismissed_at, created_at DESC);
"""


def shanghai_today() -> date:
    return datetime.now(SHANGHAI).date()


class SnoozeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    days: int = Field(default=1, ge=1, le=30)


def _row(row) -> dict[str, Any]:
    item = dict(row)
    try:
        item['evidence'] = json.loads(item.pop('evidence_json'))
    except (TypeError, ValueError):
        item['evidence'] = {}
        item.pop('evidence_json', None)
    return item


class SuggestionService:
    def __init__(self, database: Database, vault_dir: Path | None = None):
        self.database = database
        self.vault_dir = Path(vault_dir) if vault_dir else None
        self.database.initialize()
        with self.database.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)
        self.goals = GoalTaskService(database)
        self.habits = HabitService(database)
        self.learning = LearningService(database)

    @staticmethod
    def _migrate(connection) -> None:
        """Additive migration for 0.53.0 databases that predate snooze/done."""
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(suggestions)')}
        for name, declaration in (('snoozed_until', 'TEXT'), ('done_at', 'TEXT'),
                                  ('link_view', 'TEXT'), ('link_label', 'TEXT'), ('task_id', 'INTEGER')):
            if name not in columns:
                connection.execute(f'ALTER TABLE suggestions ADD COLUMN {name} {declaration}')

    # ---- generation rules -------------------------------------------------

    def _study_gap(self, today: date) -> dict[str, Any] | None:
        last_start = today - timedelta(days=6)
        prev_start = today - timedelta(days=13)
        prev_end = today - timedelta(days=7)
        recent = self.learning.list_sessions(last_start.isoformat(), today.isoformat())
        previous = self.learning.list_sessions(prev_start.isoformat(), prev_end.isoformat())
        recent_minutes = sum(int(item.get('minutes') or 0) for item in recent)
        previous_minutes = sum(int(item.get('minutes') or 0) for item in previous)
        if previous_minutes <= 0 or recent_minutes >= previous_minutes:
            return None
        by_subject: dict[str, int] = {}
        subjects = {item['id']: item.get('title') for item in self.learning.list_subjects()}
        for item in recent:
            name = subjects.get(item.get('subject_id')) or '未命名科目'
            by_subject[name] = by_subject.get(name, 0) + int(item.get('minutes') or 0)
        weakest = min(by_subject, key=by_subject.get) if by_subject else None
        return {
            'dedupe_key': f'study-gap-{today.isoformat()}',
            'kind': 'study',
            'title': '学习时长比上一周少',
            'body': f"最近 7 天学习 {recent_minutes} 分钟，前 7 天是 {previous_minutes} 分钟。",
            'evidence': {'recent_7d_minutes': recent_minutes, 'previous_7d_minutes': previous_minutes,
                         'by_subject': by_subject},
            'action_text': (f"要不要给“{weakest}”先安排一个 25 分钟的时段？只是建议，不会自动加任务。"
                            if weakest else '要不要挑一个科目先安排 25 分钟？只是建议，不会自动加任务。'),
        }

    def _due_tasks(self, today: date) -> dict[str, Any] | None:
        rows = [task for task in self.goals.list_tasks()
                if task.get('status') in ('todo', 'in_progress') and task.get('due_date') and task['due_date'] <= today.isoformat()]
        if not rows:
            return None
        overdue = [task for task in rows if task['due_date'] < today.isoformat()]
        titles = [task.get('title') for task in rows[:3]]
        return {
            'dedupe_key': f'due-tasks-{today.isoformat()}',
            'kind': 'task',
            'title': f"今天到期或已逾期 {len(rows)} 项",
            'body': f"其中已逾期 {len(overdue)} 项；包括：{'、'.join(title for title in titles if title) or '（无标题）'}。",
            'evidence': {'due_or_overdue': len(rows), 'overdue': len(overdue), 'titles': titles},
            'action_text': '要不要挑一项今天先推进 15 分钟，其余的改到明天？只是建议，不会自动改任务。',
        }

    def _habit_gap(self, today: date) -> list[dict[str, Any]]:
        first = today - timedelta(days=6)
        cards = []
        for habit in self.habits.list('active'):
            summary = self.habits.summary(habit['id'], first.isoformat(), today.isoformat())
            if summary.get('done') or summary.get('unrecorded', 0) < 4:
                continue
            cards.append({
                'dedupe_key': f"habit-gap-{habit['id']}-{today.isoformat()}",
                'kind': 'habit',
                'title': f"习惯“{habit.get('title')}”最近一周没记录",
                'body': f"最近 7 天：完成 {summary.get('done', 0)} 次、明确跳过 {summary.get('skipped', 0)} 次、未记录 {summary.get('unrecorded', 0)} 次。",
                'evidence': {'habit_id': habit['id'], 'done': summary.get('done', 0),
                             'skipped': summary.get('skipped', 0), 'unrecorded': summary.get('unrecorded', 0)},
                'action_text': (f"要不要今天只做最小动作：{habit.get('minimum_action')}？只是建议，不会自动打卡。"
                                if habit.get('minimum_action') else '要不要今天先记一笔？只是建议，不会自动打卡。'),
            })
        return cards

    def _candidate_backlog(self, today: date) -> dict[str, Any] | None:
        candidates = self.database.list_memories('candidate')
        if len(candidates) < CANDIDATE_BACKLOG_THRESHOLD:
            return None
        return {
            'dedupe_key': f'candidate-backlog-{today.isoformat()}',
            'kind': 'memory',
            'title': f"待确认记忆累计 {len(candidates)} 条",
            'body': '候选只有确认后才参与问答；积累太多会让审核越来越重。',
            'evidence': {'candidates': len(candidates)},
            'action_text': '要不要去记忆页按来源筛一遍，先确认 5 条？只是建议，不会自动确认。',
        }

    def _journal_gap(self, today: date) -> dict[str, Any] | None:
        if not self._has_table('journal_entries'):
            return None
        with self.database.connect() as connection:
            row = connection.execute('SELECT MAX(date) AS latest FROM journal_entries').fetchone()
        latest = row['latest'] if row else None
        gap_days = 3
        if latest and (today - date.fromisoformat(latest)).days < gap_days:
            return None
        return {
            'dedupe_key': f'journal-gap-{today.isoformat()}',
            'kind': 'journal',
            'title': f"最近 {gap_days} 天还没有每日记录",
            'body': f"上一次记录是 {latest}。" if latest else '还没有任何每日记录。',
            'evidence': {'latest_journal_date': latest, 'gap_days': gap_days},
            'action_text': '要不要写三句话：发生了什么、卡在哪、下一步是什么？只是建议，不会自动写入。',
        }

    def _journal_interrupted(self, today: date) -> dict[str, Any] | None:
        """A run of ≥3 recorded days that just stopped 1–2 days ago."""
        if not self._has_table('journal_entries'):
            return None
        with self.database.connect() as connection:
            rows = connection.execute(
                'SELECT date FROM journal_entries WHERE date<=? ORDER BY date DESC LIMIT 30', (today.isoformat(),)
            ).fetchall()
        days = {date.fromisoformat(row['date']) for row in rows}
        if not days:
            return None
        latest = max(days)
        gap = (today - latest).days
        streak = 1
        cursor = latest
        while True:
            cursor -= timedelta(days=1)
            if cursor not in days:
                break
            streak += 1
        if streak < 3 or gap not in STREAK_GAP_DAYS:
            return None
        return {
            'dedupe_key': f'journal-interrupted-{today.isoformat()}',
            'kind': 'journal',
            'title': f"连续记录了 {streak} 天后中断了 {gap} 天",
            'body': f"截止今天，最近一次记录在 {gap} 天前；在此之前是连续 {streak} 天。",
            'evidence': {'streak_days': streak, 'gap_days': gap},
            'action_text': '要不要今天只写一句话，把连续记录接上？只是建议，不会自动写入。',
        }

    def _subject_cold(self, today: date) -> list[dict[str, Any]]:
        """Active subjects that have history but nothing recorded for a while."""
        subjects = self.learning.list_subjects()
        if not subjects:
            return []
        first = today - timedelta(days=COLD_SUBJECT_DAYS - 1)
        recent = {item.get('subject_id') for item in self.learning.list_sessions(first.isoformat(), today.isoformat())}
        week = today.isocalendar()
        bucket = f'{week[0]}-W{week[1]:02d}'
        cards = []
        for subject in subjects:
            if subject.get('status') != 'active' or subject['id'] in recent:
                continue
            with self.database.connect() as connection:
                row = connection.execute(
                    'SELECT MAX(study_date) AS latest, COUNT(*) AS total FROM study_sessions WHERE subject_id=?',
                    (subject['id'],),
                ).fetchone()
            latest = row['latest'] if row else None
            if not latest or not int(row['total'] or 0):
                continue
            days_since = (today - date.fromisoformat(latest)).days
            if days_since < COLD_SUBJECT_DAYS:
                continue
            cards.append({
                'dedupe_key': f"subject-cold-{subject['id']}-{bucket}",
                'kind': 'study',
                'title': f"科目“{subject['title']}”已经 {days_since} 天没有记录",
                'body': f"最近一次学习记录在 {latest}；这期间没有新的时长。",
                'evidence': {'subject_id': subject['id'], 'days_since': days_since, 'last_date': latest},
                'action_text': '要不要安排一个 25 分钟的时段？只是建议，不会自动加任务或记录。',
            })
        return cards

    def _has_table(self, name: str) -> bool:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone() is not None

    def _vault_empty(self, today: date) -> dict[str, Any] | None:
        if self.vault_dir is None:
            return None
        payload = describe_vault(self.vault_dir, read_only=read_only_enabled())
        if not payload.get('exists') or payload.get('markdown_files'):
            return None
        return {
            'dedupe_key': f'vault-empty-{today.isoformat()}',
            'kind': 'vault',
            'title': 'Vault 里还没有 Markdown',
            'body': '可以把 Obsidian 库接进来（建议先保持只读），或用“记录与资料”写第一条记录。',
            'evidence': {'markdown_files': 0, 'read_only': payload.get('read_only')},
            'action_text': '要不要先去“设置 → Obsidian 库”做一次只读演练报告？只是建议，不会动你的库。',
        }

    def _vault_issues(self, today: date) -> dict[str, Any] | None:
        """Turn the read-only vault audit into one actionable card per day."""
        from .vault_audit import run_audit

        if self.vault_dir is None:
            return None
        report = run_audit(self.vault_dir)
        issues = report.get('issues') or []
        if not issues:
            return None
        counts = {code: int(value) for code, value in (report.get('counts') or {}).items() if value}
        labels = {
            'too_large': '超大文件', 'unreadable': '无法读取', 'decode_failed': '编码失败',
            'invalid_frontmatter': 'front-matter 无效', 'missing_frontmatter': '缺 front-matter',
            'missing_fields': '字段缺失', 'duplicate_id': 'id 重复', 'empty_body': '空正文',
        }
        top = sorted(counts.items(), key=lambda item: -item[1])[:3]
        summary = '、'.join(f"{labels.get(code, code)} {value}" for code, value in top)
        paths = [str(issue.get('path')) for issue in issues[:3]]
        return {
            'dedupe_key': f'vault-issues-{today.isoformat()}',
            'kind': 'vault',
            'title': f"资料体检发现 {len(issues)} 个问题",
            'body': f"主要是：{summary}。问题只影响这些文件被索引的方式，原文没有被改动。",
            'evidence': {'issues': len(issues), 'by_code': counts, 'examples': paths},
            'action_text': '要不要去“记录与资料”页看一下体检报告并修正格式？只是建议，不会自动改文件。',
            'link_view': 'documents',
            'link_label': '去资料页处理',
        }

    def _import_drift(self, today: date) -> dict[str, Any] | None:
        """Imported Markdown that changed on disk after it was indexed."""
        if self.vault_dir is None:
            return None
        drifted: list[dict[str, Any]] = []
        for document in self.database.list_documents():
            path = str(document.get('path') or '')
            if not path.startswith(f'{IMPORT_DIRECTORY}/'):
                continue
            target = self.vault_dir / path
            try:
                if not target.is_file() or target.is_symlink():
                    continue
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
            except OSError:
                continue
            if digest != document.get('sha256'):
                drifted.append({'title': document.get('title'), 'path': path})
        if not drifted:
            return None
        return {
            'dedupe_key': f'import-drift-{today.isoformat()}',
            'kind': 'vault',
            'title': f"有 {len(drifted)} 篇导入资料在本地被改过",
            'body': '这些文件的当前内容和索引里保存的版本不一致；记忆仍指向旧版本，重新提取后旧候选会归档、新候选需要重新审核。',
            'evidence': {'drifted': len(drifted), 'examples': [item['path'] for item in drifted[:3]]},
            'action_text': '要不要去“记录与资料”页看一下并重新提取？只是建议，不会自动改动或自动确认。',
            'link_view': 'documents',
            'link_label': '去资料页处理',
        }

    def build_cards(self, today: date | None = None) -> list[dict[str, Any]]:
        day = today or shanghai_today()
        cards: list[dict[str, Any]] = []
        for rule in (self._study_gap, self._due_tasks, self._candidate_backlog,
                     self._journal_interrupted, self._journal_gap, self._vault_empty,
                     self._vault_issues, self._import_drift):
            card = rule(day)
            if card:
                cards.append(card)
        cards.extend(self._habit_gap(day))
        cards.extend(self._subject_cold(day))
        top = self._top_pick(day, cards)
        if top:
            cards.append(top)
        return cards

    def _top_pick(self, today: date, cards: list[dict[str, Any]]) -> dict[str, Any] | None:
        """One daily pick: the single thing most worth doing first, with a reason."""
        priority_order = {'urgent': 0, 'high': 1, 'medium': 2, 'low': 3}
        due = [task for task in self.goals.list_tasks()
               if task.get('status') in ('todo', 'in_progress')
               and task.get('due_date') and task['due_date'] <= today.isoformat()]
        if due:
            due.sort(key=lambda task: (priority_order.get(task.get('priority'), 4),
                                       task.get('due_date') or '9999', task.get('id') or 0))
            task = due[0]
            return {
                'dedupe_key': f'top-pick-{today.isoformat()}',
                'kind': 'top-pick',
                'title': f"今天先做：{(task.get('title') or '')[:40]}",
                'body': f"截止 {task.get('due_date')}，优先级 {task.get('priority') or 'medium'}；这是当前最值得先推进的一件事。",
                'evidence': {'reason': 'due_task', 'task_id': task.get('id'),
                             'due_date': task.get('due_date'), 'priority': task.get('priority')},
                'action_text': '要不要先推进 15 分钟？只是建议，不会自动改任务或标记完成。',
            }
        for kind in ('task', 'habit', 'study', 'journal', 'memory', 'vault'):
            candidate = next((card for card in cards if card.get('kind') == kind), None)
            if not candidate:
                continue
            return {
                'dedupe_key': f'top-pick-{today.isoformat()}',
                'kind': 'top-pick',
                'title': f"今天先看：{(candidate.get('title') or '')[:40]}",
                'body': (candidate.get('body') or '')[:120],
                'evidence': {'reason': 'suggestion', 'from_kind': kind,
                             'from_title': candidate.get('title')},
                'action_text': '要不要先处理这一条？点“变成任务”可以生成一条任务草稿，不会自动执行。',
            }
        return None

    def generate(self, today: date | None = None) -> dict[str, Any]:
        """Insert missing cards (dedupe key includes the day bucket) and report counts."""
        cards = self.build_cards(today)
        created = 0
        skipped = 0
        with self.database.connect() as connection:
            for card in cards:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO suggestions
                    (dedupe_key,kind,title,body,evidence_json,action_text,created_at,link_view,link_label)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (card['dedupe_key'], card['kind'], card['title'], card['body'],
                     json.dumps(card['evidence'], ensure_ascii=False), card['action_text'], utc_now(),
                     card.get('link_view'), card.get('link_label')),
                )
                created += 1 if cursor.rowcount else 0
                skipped += 0 if cursor.rowcount else 1
        return {'generated': len(cards), 'created': created, 'skipped': skipped, 'open': self.open_count()}

    def open_count(self) -> int:
        with self.database.connect() as connection:
            return connection.execute(
                'SELECT COUNT(*) AS total FROM suggestions WHERE dismissed_at IS NULL AND done_at IS NULL'
                ' AND (snoozed_until IS NULL OR snoozed_until <= ?)', (shanghai_today().isoformat(),)
            ).fetchone()['total']

    def list_open(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute(
                'SELECT * FROM suggestions WHERE dismissed_at IS NULL AND done_at IS NULL'
                ' AND (snoozed_until IS NULL OR snoozed_until <= ?) ORDER BY created_at DESC, id DESC LIMIT 50',
                (shanghai_today().isoformat(),),
            ).fetchall()
        return {
            'count': len(rows),
            'suggestions': [_row(row) for row in rows],
            'note': '建议只是候选：不会自动改任务、习惯、记忆、日记或任何数据；收起、稍后再说、已经做了都只记录你的选择。',
        }

    def snooze(self, suggestion_id: int, days: int = 1) -> dict[str, Any]:
        window = max(1, min(30, int(days)))
        until = (shanghai_today() + timedelta(days=window)).isoformat()
        with self.database.connect() as connection:
            row = connection.execute('SELECT id FROM suggestions WHERE id=?', (suggestion_id,)).fetchone()
            if not row:
                return {'snoozed': False}
            connection.execute('UPDATE suggestions SET snoozed_until=?, done_at=NULL WHERE id=?', (until, suggestion_id))
        return {'snoozed': True, 'until': until, 'days': window, 'open': self.open_count()}

    def list_archive(self) -> dict[str, Any]:
        """Cards the user dismissed, snoozed or acknowledged — restorable at any time."""
        today = shanghai_today().isoformat()
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM suggestions
                WHERE dismissed_at IS NOT NULL OR done_at IS NOT NULL
                   OR (snoozed_until IS NOT NULL AND snoozed_until > ?)
                ORDER BY COALESCE(done_at, dismissed_at, snoozed_until) DESC, id DESC LIMIT 50""",
                (today,),
            ).fetchall()
        items = []
        for row in rows:
            item = _row(row)
            if item.get('done_at'):
                item['state'] = 'done'
            elif item.get('dismissed_at'):
                item['state'] = 'dismissed'
            else:
                item['state'] = 'snoozed'
            items.append(item)
        return {
            'count': len(items),
            'suggestions': items,
            'note': '这里只记录你的处理方式；恢复后卡片回到“待看”，任务、习惯、记忆与日记从未被改动。',
        }

    def mark_done(self, suggestion_id: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute('SELECT id FROM suggestions WHERE id=?', (suggestion_id,)).fetchone()
            if not row:
                return {'done': False}
            connection.execute('UPDATE suggestions SET done_at=?, snoozed_until=NULL WHERE id=?', (utc_now(), suggestion_id))
        return {'done': True, 'open': self.open_count()}

    @staticmethod
    def task_draft_text(card: dict[str, Any]) -> str:
        """Human title for the task created from a card (kept short and actionable)."""
        evidence = card.get('evidence') or {}
        kind = card.get('kind')
        if kind == 'study' and evidence.get('subject_id'):
            return f"安排 25 分钟学习（{card.get('title', '')[:24]}）"
        if kind == 'study':
            return '安排 25 分钟学习'
        if kind == 'task':
            titles = evidence.get('titles') or []
            return f"先推进：{titles[0]}" if titles else '推进一项到期任务'
        if kind == 'habit':
            return f"完成习惯：{card.get('title', '')[:24]}"
        if kind == 'journal':
            return '写三句话的每日记录'
        if kind == 'memory':
            return '确认 5 条候选记忆'
        if kind == 'vault':
            return '处理资料体检问题'
        return (card.get('title') or '处理一条建议')[:60]

    def create_task(self, suggestion_id: int, today: date | None = None) -> dict[str, Any]:
        """Turn one card into a normal task draft. The click itself is the confirmation."""
        day = (today or shanghai_today()).isoformat()
        with self.database.connect() as connection:
            row = connection.execute('SELECT * FROM suggestions WHERE id=?', (suggestion_id,)).fetchone()
            if not row:
                return {'created': False}
            card = _row(row)
        if card.get('task_id'):
            existing = self.goals.get_task(int(card['task_id']))
            if existing:
                return {'created': False, 'already': True, 'task': existing,
                        'note': '这张建议卡之前已经生成过任务，没有重复创建。'}
        task = self.goals.create_task(TaskCreate(
            title=self.task_draft_text(card),
            description=f"{card.get('body') or ''}\n来源：建议卡片 #{suggestion_id}（{card.get('kind')}）",
            due_date=date.fromisoformat(day),
            priority='medium',
        ))
        with self.database.connect() as connection:
            connection.execute('UPDATE suggestions SET task_id=?, done_at=?, snoozed_until=NULL WHERE id=?',
                               (task['id'], utc_now(), suggestion_id))
        return {'created': True, 'task': task, 'open': self.open_count(),
                'note': '只创建了一条普通任务草稿；没有自动完成、没有改动其他数据。'}

    def restore(self, suggestion_id: int) -> dict[str, Any]:
        """Undo a dismissal, a snooze or a done回执 (used by tests and future UI)."""
        with self.database.connect() as connection:
            row = connection.execute('SELECT id FROM suggestions WHERE id=?', (suggestion_id,)).fetchone()
            if not row:
                return {'restored': False}
            connection.execute(
                'UPDATE suggestions SET dismissed_at=NULL, snoozed_until=NULL, done_at=NULL WHERE id=?',
                (suggestion_id,),
            )
        return {'restored': True, 'open': self.open_count()}

    def dismiss(self, suggestion_id: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute('SELECT id FROM suggestions WHERE id=?', (suggestion_id,)).fetchone()
            if not row:
                return {'dismissed': False}
            connection.execute('UPDATE suggestions SET dismissed_at=?, read_at=COALESCE(read_at, ?) WHERE id=?',
                               (utc_now(), utc_now(), suggestion_id))
        return {'dismissed': True}


def create_suggestions_router(database: Database, vault_dir: Path, prefix: str = '/api') -> APIRouter:
    service = SuggestionService(database, vault_dir)
    router = APIRouter(prefix=prefix, tags=['suggestions'])

    @router.get('/suggestions')
    def list_suggestions():
        return service.list_open()

    @router.get('/suggestions/archive')
    def list_archive():
        return service.list_archive()

    @router.post('/suggestions/refresh')
    def refresh_suggestions():
        result = service.generate()
        database.audit('suggestions_generated', {'created': result['created'], 'skipped': result['skipped'], 'open': result['open']})
        return {**result, **service.list_open()}

    @router.post('/suggestions/{suggestion_id}/dismiss')
    def dismiss_suggestion(suggestion_id: int):
        result = service.dismiss(suggestion_id)
        if not result['dismissed']:
            raise HTTPException(404, '建议不存在')
        database.audit('suggestion_dismissed', {'suggestion_id': suggestion_id, 'open': service.open_count()})
        return {'dismissed': True, 'open': service.open_count()}

    @router.post('/suggestions/{suggestion_id}/snooze')
    def snooze_suggestion(suggestion_id: int, payload: SnoozeRequest):
        result = service.snooze(suggestion_id, payload.days)
        if not result['snoozed']:
            raise HTTPException(404, '建议不存在')
        database.audit('suggestion_snoozed', {'suggestion_id': suggestion_id, 'days': result['days'], 'open': result['open']})
        return result

    @router.post('/suggestions/{suggestion_id}/done')
    def done_suggestion(suggestion_id: int):
        result = service.mark_done(suggestion_id)
        if not result['done']:
            raise HTTPException(404, '建议不存在')
        database.audit('suggestion_done', {'suggestion_id': suggestion_id, 'open': result['open']})
        return result

    @router.post('/suggestions/{suggestion_id}/restore')
    def restore_suggestion(suggestion_id: int):
        result = service.restore(suggestion_id)
        if not result['restored']:
            raise HTTPException(404, '建议不存在')
        database.audit('suggestion_restored', {'suggestion_id': suggestion_id, 'open': result['open']})
        return result

    @router.post('/suggestions/{suggestion_id}/task-draft', status_code=201)
    def suggestion_task_draft(suggestion_id: int):
        """Explicit, user-clicked write: create one normal task draft from this card."""
        result = service.create_task(suggestion_id)
        if not result['created'] and not result.get('already'):
            raise HTTPException(404, '建议不存在')
        if result['created']:
            database.audit('suggestion_task_drafted', {'suggestion_id': suggestion_id, 'task_id': result['task']['id']})
            return result
        return result

    return router
