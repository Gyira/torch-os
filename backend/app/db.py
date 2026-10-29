from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from uuid import uuid4


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    indexed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    confidence TEXT NOT NULL DEFAULT 'explicit',
    source_quote TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '新对话',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    citations_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
-- Deprecated (2026-10-25): old ACP session map; kept dormant for historical rows.
CREATE TABLE IF NOT EXISTS harness_sessions (
    conversation_id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    cwd TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_usages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    conversation_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(message_id, memory_id),
    FOREIGN KEY(memory_id) REFERENCES memories(id),
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_memory_usages_memory ON memory_usages(memory_id, created_at DESC);
CREATE TABLE IF NOT EXISTS memory_deletions (
    document_id INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    PRIMARY KEY(document_id, content_hash),
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(memory_id) REFERENCES memories(id)
);
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
-- v1.0 AI 角色层：一个角色会话 = 一个 dsh session；对话正文属于过程数据，放 SQLite。
CREATE TABLE IF NOT EXISTS ai_threads (
    id TEXT PRIMARY KEY,
    role_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    dsh_session_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_threads_updated ON ai_threads(updated_at DESC);
CREATE TABLE IF NOT EXISTS ai_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    proposals_json TEXT NOT NULL DEFAULT '[]',
    structured_json TEXT NOT NULL DEFAULT '{}',
    seconds REAL,
    created_at TEXT NOT NULL,
    UNIQUE(thread_id, turn_index),
    FOREIGN KEY(thread_id) REFERENCES ai_threads(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ai_turns_thread ON ai_turns(thread_id, turn_index);
-- v1.0 通用提案：AI 只提议，用户确认后由后端 apply；身份补丁额外带 verdict_token。
CREATE TABLE IF NOT EXISTS ai_proposals (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    role_id TEXT NOT NULL DEFAULT '',
    thread_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    base_revision TEXT NOT NULL DEFAULT '',
    verdict_json TEXT NOT NULL DEFAULT '{}',
    verdict_token TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    decided_at TEXT,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_proposals_status ON ai_proposals(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_proposals_kind ON ai_proposals(kind, status);
-- v1.0 记忆中枢：角色/主 dsh/用户投递的“值得记住的事”，以及每次综合的运行记录。
CREATE TABLE IF NOT EXISTS memory_hub_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    source_ref TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    dedupe_key TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_memory_hub_events_status ON memory_hub_events(status, created_at DESC);
CREATE TABLE IF NOT EXISTS memory_hub_runs (
    id TEXT PRIMARY KEY,
    trigger TEXT NOT NULL,
    range_days INTEGER NOT NULL,
    status TEXT NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 0,
    card_count INTEGER NOT NULL DEFAULT 0,
    snapshot_path TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_hub_runs_started ON memory_hub_runs(started_at DESC);
-- Deprecated (2026-10-25): old vector cache; lexical retrieval only, no writers.
CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    dim INTEGER NOT NULL CHECK(dim > 0),
    vector_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
);
-- Deprecated (2026-10-22): retained for historical rows only. Goal notes under
-- 00_人生路/04_目标 and Markdown task notes are the live authorities.
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    target_date TEXT,
    progress INTEGER NOT NULL DEFAULT 0 CHECK(progress >= 0 AND progress <= 100),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goals_status_updated ON goals(status, updated_at DESC);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo',
    priority TEXT NOT NULL DEFAULT 'medium',
    due_date TEXT,
    completed_at TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_goal_status ON tasks(goal_id, status, sort_order, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date, status);
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title,
    content,
    path,
    content='documents',
    content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, title, content, path)
    VALUES (new.id, new.title, new.content, new.path);
END;
CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, content, path)
    VALUES ('delete', old.id, old.title, old.content, old.path);
END;
CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, content, path)
    VALUES ('delete', old.id, old.title, old.content, old.path);
    INSERT INTO documents_fts(rowid, title, content, path)
    VALUES (new.id, new.title, new.content, new.path);
END;
"""

# Deprecated since 0.99: these tables belonged to the retired 八字 / 建议 / 旧日记
# modules. Their DDL is kept here so existing databases keep their historical rows and
# new databases keep the same schema shape for one release. They have no writers and
# must not be dropped by this project; removal requires an explicit migration.
LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS bazi_profiles (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    birth_date TEXT NOT NULL CHECK(length(birth_date) = 10),
    birth_time TEXT NOT NULL CHECK(length(birth_time) = 5),
    longitude REAL CHECK(longitude IS NULL OR (longitude >= -180 AND longitude <= 180)),
    use_true_solar INTEGER NOT NULL DEFAULT 0 CHECK(use_true_solar IN (0, 1)),
    updated_at TEXT NOT NULL
);
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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _explicitly_expired(value: Any, now: datetime) -> bool:
    """Only explicit ISO validity bounds expire memories; source age never does."""
    if not isinstance(value, str) or not value.strip():
        return False
    value = value.strip()
    local_timezone = timezone(timedelta(hours=8))
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            # A date-only bound remains valid throughout that local calendar day.
            return now.astimezone(local_timezone).date() > date.fromisoformat(value)
        cutoff = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=local_timezone)
        return now >= cutoff
    except ValueError:
        return False


_DATE_IN_PATH = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")


def _record_date(path: str | None, metadata_json: str | None) -> str | None:
    """Best-effort origin date of a note: front-matter metadata first, then the path.

    Only a full ``YYYY-MM-DD`` counts; anything else is honestly left unknown
    instead of guessing from an extraction timestamp.
    """
    data: dict[str, Any] = {}
    if metadata_json:
        try:
            parsed = json.loads(metadata_json)
            if isinstance(parsed, dict):
                data = parsed
        except (TypeError, ValueError):
            data = {}
    for key in ("event_date", "record_date", "date", "created_at", "recorded_at"):
        value = data.get(key)
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value[:10]):
            try:
                return date.fromisoformat(value[:10]).isoformat()
            except ValueError:
                continue
    if path:
        match = _DATE_IN_PATH.search(path)
        if match:
            try:
                return date.fromisoformat(match.group(1)).isoformat()
            except ValueError:
                return None
    return None


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.executescript(LEGACY_SCHEMA)
            # Additive migration preserves the existing demo database and decisions.
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(documents)')}
            for name, declaration in {
                'source_key': 'TEXT', 'available': 'INTEGER NOT NULL DEFAULT 1',
                'privacy': "TEXT NOT NULL DEFAULT 'personal'", 'revision': 'INTEGER NOT NULL DEFAULT 1',
                'last_known_path': 'TEXT',
                'coverage_summary_json': 'TEXT',
            }.items():
                if name not in columns:
                    connection.execute(f'ALTER TABLE documents ADD COLUMN {name} {declaration}')
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(memories)')}
            for name, declaration in {
                'source_revision': 'INTEGER NOT NULL DEFAULT 1', 'revision': 'INTEGER NOT NULL DEFAULT 1',
                'source_start_line': 'INTEGER', 'source_end_line': 'INTEGER',
                'source_start_offset': 'INTEGER', 'source_end_offset': 'INTEGER',
                'source_location_basis': 'TEXT',
            }.items():
                if name not in columns:
                    connection.execute(f'ALTER TABLE memories ADD COLUMN {name} {declaration}')
            connection.execute('INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (2, ?)', (utc_now(),))
            connection.execute('INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (3, ?)', (utc_now(),))
            # 4: memory_usages (which stored answers cited which confirmed memories).
            connection.execute('INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (4, ?)', (utc_now(),))
            # 5: memory_deletions (hard-deleted memories stay deleted across re-extraction).
            connection.execute('INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (5, ?)', (utc_now(),))
            # 6: AI role threads/turns (v1.0 role layer).
            connection.execute('INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (6, ?)', (utc_now(),))
            # 7: AI proposals (identity_patch/new_note/new_task/new_habit/…).
            connection.execute('INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (7, ?)', (utc_now(),))
            # 8: memory hub events (what roles tell 忆灵).
            connection.execute('INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (8, ?)', (utc_now(),))
            # 9: memory hub synthesis runs.
            connection.execute('INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (9, ?)', (utc_now(),))

    def audit(self, event_type: str, detail: dict[str, Any] | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO audit_events(event_type, detail_json, created_at) VALUES (?, ?, ?)",
                (event_type, json.dumps(detail or {}, ensure_ascii=False), utc_now()),
            )

    def upsert_document(
        self,
        path: str,
        title: str,
        content: str,
        sha256: str,
        metadata: dict[str, Any],
    ) -> tuple[int, bool]:
        with self.connect() as connection:
            source_key = str(metadata.get('id') or '') or None
            path_match = connection.execute('SELECT * FROM documents WHERE path=?', (path,)).fetchone()
            if source_key:
                matches = connection.execute('SELECT * FROM documents WHERE source_key=?', (source_key,)).fetchall()
                if len(matches) > 1:
                    raise ValueError('来源 ID 在旧索引中存在歧义，请使用新的唯一 ID')
                existing = matches[0] if matches else None
                if existing is None and path_match and not path_match['source_key']:
                    existing = path_match
                if path_match and (existing is None or path_match['id'] != existing['id']):
                    self._retire_source_path(connection, path_match)
            else:
                existing = path_match
            metadata_json = json.dumps(metadata, ensure_ascii=False, default=str)
            if existing and existing["sha256"] == sha256 and existing['available']:
                connection.execute('UPDATE documents SET path=?, source_key=?, last_known_path=NULL WHERE id=?', (path, source_key, existing['id']))
                return int(existing["id"]), False
            if existing:
                self._archive_source(connection, existing['id'], 'source_changed')
                connection.execute(
                    "UPDATE documents SET path=?, title=?, content=?, sha256=?, metadata_json=?, indexed_at=?, source_key=?, available=1, privacy='personal', revision=revision+1, last_known_path=NULL, coverage_summary_json=NULL WHERE id=?",
                    (path, title, content, sha256, metadata_json, utc_now(), source_key, existing["id"]),
                )
                return int(existing["id"]), True
            cursor = connection.execute(
                "INSERT INTO documents(path, title, content, sha256, metadata_json, indexed_at, source_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (path, title, content, sha256, metadata_json, utc_now(), source_key),
            )
            return int(cursor.lastrowid), True

    def _retire_source_path(self, connection, row) -> None:
        """Free a replaced path without reassigning the old document identity."""
        self._archive_source(connection, row['id'], 'source_replaced')
        connection.execute(
            'UPDATE documents SET path=?,last_known_path=?,available=0 WHERE id=?',
            (f'.source-history/{row["id"]}-{uuid4().hex}.md',
             row['last_known_path'] or row['path'], row['id']),
        )

    def prepare_document_paths(self, sources: dict[str, str | None]) -> set[str]:
        """Resolve the whole scan before upserts, preserving identities in rename cycles.

        Temporary paths exist only inside this transaction. Replaced documents
        keep a private database-only path and their original readable source path.
        Ambiguous historic IDs are returned to the scanner for fail-closed handling.
        """
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute('SELECT * FROM documents').fetchall()
            by_path = {row['path']: row for row in rows}
            by_id = {row['id']: row for row in rows}
            by_source: dict[str, list] = {}
            for row in rows:
                if row['source_key']:
                    by_source.setdefault(row['source_key'], []).append(row)
            ambiguous = {path for path, key in sources.items() if key and len(by_source.get(key, [])) > 1}
            assignments: dict[int, str] = {}
            # Stable IDs claim their rows before any path-only fallback can use them.
            for path, key in sources.items():
                matches = by_source.get(key, []) if key else []
                if path not in ambiguous and matches:
                    assignments[matches[0]['id']] = path
            for path, key in sources.items():
                if path in ambiguous or (key and by_source.get(key)):
                    continue
                existing = by_path.get(path)
                if existing and existing['id'] not in assignments and (not key or not existing['source_key']):
                    assignments[existing['id']] = path

            displaced = {document_id for document_id, path in assignments.items()
                         if by_id[document_id]['path'] != path}
            for path in sources.keys() - ambiguous:
                occupant = by_path.get(path)
                if occupant and assignments.get(occupant['id']) != path:
                    displaced.add(occupant['id'])
            for document_id in displaced:
                row = by_id[document_id]
                if document_id not in assignments:
                    self._retire_source_path(connection, row)
                else:
                    connection.execute('UPDATE documents SET path=? WHERE id=?',
                                       (f'.source-history/{document_id}-{uuid4().hex}.md', document_id))
            for document_id, path in assignments.items():
                if document_id in displaced:
                    connection.execute('UPDATE documents SET path=?,last_known_path=NULL WHERE id=?', (path, document_id))
            return ambiguous

    def replace_document_memories(
        self, document_id: int, memories: list[dict[str, Any]],
        coverage_summary: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            self._archive_source(connection, document_id, 'source_reindexed')
            document = connection.execute('SELECT revision,title FROM documents WHERE id=?', (document_id,)).fetchone()
            coverage = coverage_summary if coverage_summary is not None else getattr(memories, 'coverage_summary', None)
            connection.execute('UPDATE documents SET coverage_summary_json=? WHERE id=?',
                               (json.dumps(coverage, ensure_ascii=False) if coverage is not None else None, document_id))
            # One stamp for the whole batch: rows saved together must not be
            # ordered by which insert happened to cross a clock tick.
            batch_stamp = utc_now()
            for memory in memories:
                content_hash = self._memory_content_hash(memory["content"])
                # Deletions recorded before 0.43.0 hashed title+content (with the
                # document title of that era); check those too so suppression
                # survives extraction upgrades.
                legacy_hashes = {
                    self._legacy_memory_hash(memory["title"], memory["content"]),
                    self._legacy_memory_hash(document["title"] or '', memory["content"]),
                }
                markers = ','.join('?' for _ in legacy_hashes)
                suppressed = connection.execute(
                    f"SELECT 1 FROM memory_deletions WHERE document_id=? AND content_hash IN (?,{markers})",
                    (document_id, content_hash, *legacy_hashes),
                ).fetchone()
                if suppressed:
                    continue
                cursor = connection.execute(
                    """INSERT INTO memories
                    (document_id, kind, title, content, status, confidence, source_quote, created_at, updated_at, source_revision,
                     source_start_line, source_end_line, source_start_offset, source_end_offset, source_location_basis)
                    VALUES (?, ?, ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        document_id,
                        memory["kind"],
                        memory["title"],
                        memory["content"],
                        memory.get("confidence", "explicit"),
                        memory["source_quote"],
                        batch_stamp,
                        batch_stamp,
                        document['revision'],
                        memory.get('source_start_line'),
                        memory.get('source_end_line'),
                        memory.get('source_start_offset'),
                        memory.get('source_end_offset'),
                        memory.get('source_location_basis'),
                    ),
                )
                row = connection.execute('SELECT * FROM memories WHERE id=?', (cursor.lastrowid,)).fetchone()
                self._event(connection, row['id'], 'created', {}, dict(row))

    @staticmethod
    def _event(connection, memory_id: int, event_type: str, before: dict, after: dict) -> None:
        connection.execute(
            'INSERT INTO memory_events(memory_id,event_type,before_json,after_json,created_at) VALUES (?,?,?,?,?)',
            (memory_id, event_type, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), utc_now()),
        )

    def _archive_source(self, connection, document_id: int, reason: str) -> None:
        rows = connection.execute("SELECT * FROM memories WHERE document_id=? AND status IN ('active','candidate')", (document_id,)).fetchall()
        for row in rows:
            after = dict(row)
            after.update(status='archived', updated_at=utc_now(), revision=row['revision']+1)
            connection.execute('UPDATE memories SET status=?, updated_at=?, revision=? WHERE id=?', ('archived', after['updated_at'], after['revision'], row['id']))
            self._event(connection, row['id'], reason, dict(row), after)

    def invalidate_documents(self, available_paths: set[str]) -> int:
        with self.connect() as connection:
            rows = connection.execute('SELECT id,path FROM documents WHERE available=1').fetchall()
            missing = [row for row in rows if row['path'] not in available_paths]
            for row in missing:
                self._archive_source(connection, row['id'], 'source_unavailable')
                connection.execute('UPDATE documents SET available=0 WHERE id=?', (row['id'],))
            return len(missing)

    def get_document(self, document_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM documents WHERE id=? AND available=1', (document_id,)).fetchone()
            return self._document_record(row) if row else None

    def find_document_by_source_key(self, source_key: str) -> dict[str, Any] | None:
        """Look up the stable identity of a generated note that is rewritten in place."""
        with self.connect() as connection:
            row = connection.execute('SELECT id,path,title,revision,available FROM documents WHERE source_key=?', (source_key,)).fetchone()
        return dict(row) if row else None

    def document_digests(self) -> dict[str, str]:
        """Path → sha256 of every available document; used by the read-only scan preview."""
        with self.connect() as connection:
            rows = connection.execute('SELECT COALESCE(last_known_path, path) AS path, sha256, available FROM documents').fetchall()
        return {row['path']: row['sha256'] for row in rows if row['available']}

    @staticmethod
    def _document_record(row) -> dict[str, Any]:
        item = dict(row)
        try:
            coverage = json.loads(item.get('coverage_summary_json') or 'null')
        except (TypeError, ValueError):
            coverage = None
        if not isinstance(coverage, dict):
            coverage = {
                'extraction_version': 'legacy', 'candidate_count': None,
                'total_candidate_segments': None, 'omitted_segments': None,
                'truncated': None, 'coverage_complete': None,
                'source_location_basis': None,
                'scope_note': '候选提取范围尚未记录，不代表语义事实覆盖率。',
                'warnings': ['旧版本资料未记录候选提取范围与来源行号；升级和重扫不会重置既有确认或拒绝决定。'],
            }
        item['coverage_summary'] = coverage
        return item

    def search_documents(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Compatibility entry point: only confirmed evidence may be retrieved."""
        return self.search_memories(query, limit)

    def list_documents(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [self._document_record(row) for row in connection.execute(
                "SELECT id, path, title, sha256, indexed_at, metadata_json, revision, available, coverage_summary_json FROM documents WHERE available=1 ORDER BY indexed_at DESC"
            ).fetchall()]

    def list_memories(self, status: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            query = """SELECT m.*, COALESCE(d.last_known_path,d.path) AS source_path,
                d.metadata_json AS source_metadata_json
                FROM memories m JOIN documents d ON d.id=m.document_id"""
            if status:
                rows = connection.execute(
                    query + " WHERE m.status=? ORDER BY m.updated_at DESC, m.id ASC", (status,)
                ).fetchall()
            else:
                rows = connection.execute(
                    # Rows touched in the same clock tick tie on updated_at; the
                    # id keeps the order stable instead of letting SQLite return
                    # an arbitrary one.
                    query + " ORDER BY m.updated_at DESC, m.id ASC"
                ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                # Metadata stays server-side; only the derived origin date is exposed.
                record_date = _record_date(item.get('source_path'), item.pop('source_metadata_json', None))
                item['source_date'] = record_date
                item['record_month'] = record_date[:7] if record_date else None
                items.append(item)
            return items

    def update_memory_status(self, memory_id: int, status: str) -> dict[str, Any] | None:
        if status not in {'candidate', 'active', 'rejected', 'archived'}:
            raise ValueError('不支持的记忆状态')
        with self.connect() as connection:
            before = connection.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
            if not before:
                return None
            document = connection.execute('SELECT * FROM documents WHERE id=?', (before['document_id'],)).fetchone()
            if status in {'active', 'candidate'} and (not document['available'] or before['source_revision'] != document['revision']):
                raise ValueError('来源已变更或不可用，请确认新版本候选')
            connection.execute(
                "UPDATE memories SET status=?, updated_at=?, revision=revision+1 WHERE id=?",
                (status, utc_now(), memory_id),
            )
            row = connection.execute(
                "SELECT m.*, COALESCE(d.last_known_path,d.path) AS source_path FROM memories m JOIN documents d ON d.id=m.document_id WHERE m.id=?",
                (memory_id,),
            ).fetchone()
            if before['status'] != status:
                self._event(connection, memory_id, 'status_changed', dict(before), dict(row))
            return dict(row) if row else None

    def batch_update_memory_status(self, memory_ids: list[int], status: str) -> dict[str, Any]:
        """Apply one status to many memories in a single transaction.

        Each item reports its own skip reason; nothing is silently changed.
        """
        if status not in {'active', 'rejected'}:
            raise ValueError('批量操作只支持确认或拒绝')
        updated_ids: list[int] = []
        skipped: list[dict[str, Any]] = []
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            for memory_id in memory_ids:
                before = connection.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
                if before is None:
                    skipped.append({'memory_id': memory_id, 'reason': 'not_found'})
                    continue
                if before['status'] == status:
                    skipped.append({'memory_id': memory_id, 'reason': 'already'})
                    continue
                document = connection.execute('SELECT * FROM documents WHERE id=?', (before['document_id'],)).fetchone()
                if status == 'active' and (not document['available'] or before['source_revision'] != document['revision']):
                    skipped.append({'memory_id': memory_id, 'reason': 'source_changed'})
                    continue
                connection.execute(
                    "UPDATE memories SET status=?, updated_at=?, revision=revision+1 WHERE id=?",
                    (status, utc_now(), memory_id),
                )
                row = connection.execute(
                    """SELECT m.*, COALESCE(d.last_known_path,d.path) AS source_path
                    FROM memories m JOIN documents d ON d.id=m.document_id WHERE m.id=?""",
                    (memory_id,),
                ).fetchone()
                self._event(connection, memory_id, 'status_changed', dict(before), dict(row))
                updated_ids.append(memory_id)
        return {'status': status, 'requested': len(memory_ids), 'updated': len(updated_ids),
                'updated_ids': updated_ids, 'skipped': skipped}

    def tidy_candidate_memories(self, *, dry_run: bool = True) -> dict[str, Any]:
        """Rewrite candidate titles/content into readable statements.

        Only title/content are touched; ``source_quote`` keeps the raw excerpt and
        every changed row records a ``tidied`` event. Rows that still do not read
        like a statement are reported instead of guessed at.
        """
        from .vault import _is_memory_like, readable_statement

        updated = unchanged = skipped = 0
        samples: list[dict[str, Any]] = []
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,title,content,source_quote FROM memories WHERE status='candidate' ORDER BY id"
            ).fetchall()
            if not dry_run:
                connection.execute('BEGIN IMMEDIATE')
            for row in rows:
                raw = row['source_quote'] or row['content'] or ''
                readable = readable_statement(raw)
                if not _is_memory_like(readable):
                    skipped += 1
                    continue
                title = readable if len(readable) <= 24 else readable[:24] + '…'
                if readable == row['content'] and title == row['title']:
                    unchanged += 1
                    continue
                updated += 1
                if len(samples) < 3:
                    samples.append({'memory_id': row['id'], 'title': title, 'content': readable})
                if not dry_run:
                    connection.execute(
                        "UPDATE memories SET title=?, content=?, revision=revision+1, updated_at=? WHERE id=?",
                        (title, readable, utc_now(), row['id']),
                    )
                    self._event(connection, row['id'], 'tidied',
                                {'title': row['title'], 'content': row['content']},
                                {'title': title, 'content': readable})
        return {'dry_run': dry_run, 'total': len(rows), 'updated': updated,
                'unchanged': unchanged, 'not_memory_like': skipped, 'samples': samples}

    def delete_memory(self, memory_id: int) -> dict[str, Any] | None:
        """Hard-delete one memory and its derived links; suppress re-extraction.

        The source Markdown stays untouched, and existing exports or backups
        are not rewritten. Citations stored inside saved answers are scrubbed so
        the deleted content no longer appears there either.
        """
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT id,document_id,title,content,status FROM memories WHERE id=?', (memory_id,)
            ).fetchone()
            if row is None:
                return None
            # Content-only so the same statement stays suppressed even when the
            # title/extraction rules change later.
            content_hash = self._memory_content_hash(row['content'])
            usages_removed = int(connection.execute(
                'SELECT COUNT(*) AS n FROM memory_usages WHERE memory_id=?', (memory_id,)
            ).fetchone()['n'])
            events_removed = int(connection.execute(
                'SELECT COUNT(*) AS n FROM memory_events WHERE memory_id=?', (memory_id,)
            ).fetchone()['n'])
            citations_removed = 0
            candidates = connection.execute(
                """SELECT id,citations_json FROM messages
                   WHERE citations_json LIKE ? OR citations_json LIKE ?""",
                (f'%"memory_id": {memory_id},%', f'%"memory_id": {memory_id}}}%'),
            ).fetchall()
            for message in candidates:
                try:
                    citations = json.loads(message['citations_json'] or '[]')
                except (TypeError, ValueError):
                    continue
                if not isinstance(citations, list):
                    continue
                kept = []
                for item in citations:
                    try:
                        entry_id = int(item.get('memory_id')) if isinstance(item, dict) else None
                    except (TypeError, ValueError):
                        entry_id = None
                    if entry_id != memory_id:
                        kept.append(item)
                if len(kept) != len(citations):
                    citations_removed += len(citations) - len(kept)
                    connection.execute(
                        'UPDATE messages SET citations_json=? WHERE id=?',
                        (json.dumps(kept, ensure_ascii=False), message['id']),
                    )
            connection.execute('DELETE FROM memory_usages WHERE memory_id=?', (memory_id,))
            connection.execute('DELETE FROM memory_events WHERE memory_id=?', (memory_id,))
            connection.execute(
                'INSERT OR REPLACE INTO memory_deletions(document_id,content_hash,deleted_at) VALUES (?,?,?)',
                (row['document_id'], content_hash, utc_now()),
            )
            connection.execute('DELETE FROM memories WHERE id=?', (memory_id,))
        return {
            'deleted': True,
            'memory_id': memory_id,
            'document_id': int(row['document_id']),
            'status_before': row['status'],
            'usages_removed': usages_removed,
            'events_removed': events_removed,
            'citations_removed': citations_removed,
        }

    def edit_memory(self, memory_id: int, content: str, title: str | None = None) -> dict[str, Any] | None:
        with self.connect() as connection:
            before = connection.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
            if not before:
                return None
            document = connection.execute('SELECT * FROM documents WHERE id=?', (before['document_id'],)).fetchone()
            if not document['available'] or before['source_revision'] != document['revision']:
                raise ValueError('来源已变更，请编辑新版本候选')
            if title is None:
                # Candidate titles are the statement itself; keep them in sync when
                # the user edits only the content.
                from .vault import clean_fragment

                readable = clean_fragment(content)
                title = readable if len(readable) <= 24 else readable[:24] + '…'
            connection.execute(
                "UPDATE memories SET content=?,title=?,status='candidate',confidence='user_corrected',revision=revision+1,updated_at=? WHERE id=?",
                (content, title or before['title'], utc_now(), memory_id),
            )
            after = connection.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
            self._event(connection, memory_id, 'user_corrected', dict(before), dict(after))
            return dict(after)

    def memory_history(self, memory_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute('SELECT * FROM memory_events WHERE memory_id=? ORDER BY id DESC', (memory_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['before'] = json.loads(item.pop('before_json'))
            item['after'] = json.loads(item.pop('after_json'))
            result.append(item)
        return result

    def search_memories(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        """Retrieve confirmed, current evidence; no arbitrary FTS expressions or single-character fallback."""
        return self._retrieval(query, limit)['selected']

    @staticmethod
    def _memory_content_hash(content: str) -> str:
        """Content-only hash: stable across title and extraction-version changes."""
        return hashlib.sha256(f"content\n{content}".encode("utf-8")).hexdigest()

    @staticmethod
    def _legacy_memory_hash(title: str, content: str) -> str:
        """Pre-0.43.0 hash kept so existing deletion records still suppress."""
        return hashlib.sha256(f"{title}\n{content}".encode("utf-8")).hexdigest()

    def _retrieval(self, query: str, limit: int = 6) -> dict[str, Any]:
        """Shared read-only scoring for search and previews; never writes audit or state."""
        normalized = query.casefold().strip()
        intent_priority: list[str] = []
        if any(term in normalized for term in ('我是谁', '关于我', '个人资料', '介绍我', 'who am i')):
            intent_priority.extend(('fact', 'preference'))
        if any(term in normalized for term in ('我的目标', '当前目标', '目前目标', 'current goal')):
            intent_priority.append('goal')
        # Deterministic priority: when several memories only qualify through the
        # intent boost, the earlier kind wins the tie instead of the newest id.
        intent_kinds = {kind: index for index, kind in enumerate(dict.fromkeys(intent_priority))}
        words = set(re.findall(r'[a-z0-9_]{2,}', normalized))
        for segment in re.findall(r'[\u4e00-\u9fff]+', normalized):
            words.update(segment[i:i+2] for i in range(len(segment)-1))
        words -= {'我是','我的','我想','是什么','什么','是什','么呢','现在','一下','请问','能否','是否','最近','怎么','如何','可以','的目'}
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT m.*, d.path, d.sha256 AS source_hash, d.revision AS document_revision,
                          d.metadata_json AS source_metadata_json
                FROM memories m JOIN documents d ON d.id=m.document_id
                WHERE m.status='active' AND d.available=1 AND d.privacy='personal'
                  AND m.source_revision=d.revision ORDER BY m.id DESC"""
            ).fetchall()
        scored = []
        expired = 0
        now = datetime.now(timezone.utc)
        for row in rows:
            try:
                metadata = json.loads(row['source_metadata_json'])
            except (TypeError, ValueError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            if _explicitly_expired(metadata.get('valid_until'), now):
                expired += 1
                continue
            haystack = (row['title'] + ' ' + row['content']).casefold()
            hits = {word for word in words if word in haystack}
            score = sum(3 if len(word)>2 else 1 for word in hits)
            if row['kind'] in intent_kinds:
                score += 20
            intent_rank = intent_kinds.get(row['kind'], len(intent_kinds))
            if score > 0:
                item = dict(row)
                item.pop('source_metadata_json', None)
                item['source_date'] = metadata.get('event_date') or metadata.get('created_at')
                item['valid_from'] = metadata.get('valid_from')
                item['valid_until'] = metadata.get('valid_until')
                item['memory_id'] = item['id']
                item['source_path'] = item['path']
                item['snippet'] = item['content']
                item['low_confidence'] = False
                item['lexical_score'] = score
                item['score'] = score
                scored.append({'lex': score, 'intent': intent_rank, 'item': item})
        scored.sort(key=lambda entry: (-entry['lex'], entry['intent'], -entry['item']['id']))
        usable_total = len(rows) - expired
        matched_total = len(scored)
        return {
            'selected': [entry['item'] for entry in scored[:limit]],
            'usable_total': usable_total,
            'expired': expired,
            'matched_total': matched_total,
            'not_matching': usable_total - matched_total,
            'over_limit': max(0, matched_total - limit),
            'semantic': False,
        }

    def _blocked_active_counts(self, connection) -> dict[str, int]:
        """Active memories excluded by source availability, privacy or revision, highest priority wins."""
        row = connection.execute(
            """SELECT
               SUM(CASE WHEN d.available=0 THEN 1 ELSE 0 END) AS source_unavailable,
               SUM(CASE WHEN d.available=1 AND d.privacy!='personal' THEN 1 ELSE 0 END) AS not_personal,
               SUM(CASE WHEN d.available=1 AND d.privacy='personal' AND m.source_revision!=d.revision THEN 1 ELSE 0 END) AS stale_source
               FROM memories m JOIN documents d ON d.id=m.document_id WHERE m.status='active'"""
        ).fetchone()
        return {key: int(row[key] or 0) for key in ('source_unavailable', 'not_personal', 'stale_source')}

    def preview_memories(self, query: str, limit: int = 6) -> dict[str, Any]:
        """Read-only evidence preview identical to search_memories, plus exclusion counts."""
        retrieval = self._retrieval(query, limit)
        with self.connect() as connection:
            status_counts = {row['status']: int(row['n']) for row in connection.execute(
                'SELECT status, COUNT(*) AS n FROM memories GROUP BY status'
            ).fetchall()}
            blocked = self._blocked_active_counts(connection)
        excluded = {
            'candidate': status_counts.get('candidate', 0),
            'rejected': status_counts.get('rejected', 0),
            'archived': status_counts.get('archived', 0),
            **blocked,
            'expired': retrieval['expired'],
            'not_matching': retrieval['not_matching'],
            'over_limit': retrieval['over_limit'],
        }
        return {
            'evidence': retrieval['selected'],
            'counts': {
                'selected': len(retrieval['selected']),
                'max_limit': limit,
                'active_total': status_counts.get('active', 0),
                'usable_total': retrieval['usable_total'],
                'matched_total': retrieval['matched_total'],
            },
            'excluded': excluded,
        }

    def active_memory_overview(self, limit: int = 100) -> dict[str, Any]:
        """Read-only inventory of confirmed memories for the 'know me' summary."""
        with self.connect() as connection:
            status_counts = {row['status']: int(row['n']) for row in connection.execute(
                'SELECT status, COUNT(*) AS n FROM memories GROUP BY status'
            ).fetchall()}
            blocked = self._blocked_active_counts(connection)
            rows = connection.execute(
                """SELECT m.*, d.path, d.metadata_json AS source_metadata_json, d.revision AS document_revision
                FROM memories m JOIN documents d ON d.id=m.document_id
                WHERE m.status='active' AND d.available=1 AND d.privacy='personal'
                  AND m.source_revision=d.revision ORDER BY m.updated_at DESC, m.id DESC"""
            ).fetchall()
        now = datetime.now(timezone.utc)
        items: list[dict[str, Any]] = []
        by_kind: dict[str, int] = {}
        expired = 0
        for row in rows:
            try:
                metadata = json.loads(row['source_metadata_json'])
            except (TypeError, ValueError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            if _explicitly_expired(metadata.get('valid_until'), now):
                expired += 1
                continue
            by_kind[row['kind']] = by_kind.get(row['kind'], 0) + 1
            if len(items) >= limit:
                continue
            item = dict(row)
            item.pop('source_metadata_json', None)
            item['source_date'] = metadata.get('event_date') or metadata.get('created_at')
            item['valid_from'] = metadata.get('valid_from')
            item['valid_until'] = metadata.get('valid_until')
            item['memory_id'] = item['id']
            item['source_path'] = item['path']
            items.append(item)
        return {
            'items': items,
            'usable_total': len(rows) - expired,
            'expired': expired,
            'blocked': blocked,
            'status_counts': status_counts,
            'by_kind': by_kind,
        }

    def list_conversations(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute('SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 100')]

    def get_conversation(self, conversation_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM conversations WHERE id=?', (conversation_id,)).fetchone()
            if not row:
                return None
            messages = [dict(msg) for msg in connection.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id', (conversation_id,))]
        for message in messages:
            message['citations'] = json.loads(message.pop('citations_json'))
        return {'conversation': dict(row), 'messages': messages}

    def create_conversation(self, title: str = "新对话") -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO conversations(title, created_at, updated_at) VALUES (?, ?, ?)",
                (title, utc_now(), utc_now()),
            )
            return int(cursor.lastrowid)

    def add_message(self, conversation_id: int, role: str, content: str, citations: list[dict[str, Any]]) -> int:
        """Store one message and, for stored answers, the memories they cited.

        Usage rows are written only here, so previews, drafts and local retrieval
        probes never fabricate a reverse reference. The write shares the answer's
        transaction.
        """
        created_at = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO messages(conversation_id, role, content, citations_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, role, content, json.dumps(citations, ensure_ascii=False), created_at),
            )
            message_id = int(cursor.lastrowid)
            connection.execute("UPDATE conversations SET updated_at=? WHERE id=?", (created_at, conversation_id))
            if role == 'assistant':
                self._record_memory_usages(connection, message_id, conversation_id, citations, created_at)
        return message_id

    @staticmethod
    def _record_memory_usages(connection, message_id: int, conversation_id: int,
                              citations: list[dict[str, Any]], created_at: str) -> int:
        """Insert one usage row per distinct citation; skip citations without a live memory."""
        seen: list[int] = []
        for citation in citations or []:
            if not isinstance(citation, dict):
                continue
            try:
                memory_id = int(citation.get('memory_id'))
            except (TypeError, ValueError):
                continue
            if memory_id not in seen:
                seen.append(memory_id)
        inserted = 0
        for memory_id in seen:
            if connection.execute('SELECT 1 FROM memories WHERE id=?', (memory_id,)).fetchone() is None:
                continue
            cursor = connection.execute(
                """INSERT OR IGNORE INTO memory_usages(memory_id, message_id, conversation_id, created_at)
                   VALUES (?, ?, ?, ?)""",
                (memory_id, message_id, conversation_id, created_at),
            )
            inserted += max(0, cursor.rowcount or 0)
        return inserted

    def memory_usage_stats(self, days: int = 30) -> dict[int, dict[str, Any]]:
        """Per-memory usage counts inside a rolling window; read-only."""
        since = (datetime.now(timezone.utc) - timedelta(days=max(0, days))).isoformat()
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT memory_id, COUNT(*) AS uses, MAX(created_at) AS last_used_at
                   FROM memory_usages WHERE created_at>=? GROUP BY memory_id""",
                (since,),
            ).fetchall()
        return {
            int(row['memory_id']): {'uses': int(row['uses']), 'last_used_at': row['last_used_at']}
            for row in rows
        }

    def recent_memory_usage(self, limit: int = 8) -> list[dict[str, Any]]:
        """Newest answer citations with the question that triggered each stored answer."""
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT u.memory_id, u.message_id, u.conversation_id, u.created_at AS used_at,
                          m.title, m.kind, m.status, c.title AS conversation_title
                   FROM memory_usages u
                   JOIN memories m ON m.id=u.memory_id
                   JOIN messages a ON a.id=u.message_id
                   JOIN conversations c ON c.id=u.conversation_id
                   ORDER BY u.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item['memory_id'] = int(item['memory_id'])
                question = connection.execute(
                    """SELECT content FROM messages
                       WHERE conversation_id=? AND role='user' AND id<? ORDER BY id DESC LIMIT 1""",
                    (row['conversation_id'], row['message_id']),
                ).fetchone()
                item['question'] = question['content'] if question else None
                items.append(item)
        return items

    def memory_usage_detail(self, memory_id: int, limit: int = 20) -> dict[str, Any] | None:
        """Reverse references for one memory, including memories that are no longer active."""
        with self.connect() as connection:
            memory = connection.execute(
                """SELECT m.id, m.title, m.kind, m.status, m.confidence, m.revision,
                          m.updated_at, m.document_id, d.path
                   FROM memories m JOIN documents d ON d.id=m.document_id WHERE m.id=?""",
                (memory_id,),
            ).fetchone()
            if memory is None:
                return None
            total = int(connection.execute(
                'SELECT COUNT(*) AS n FROM memory_usages WHERE memory_id=?', (memory_id,)
            ).fetchone()['n'])
            rows = connection.execute(
                """SELECT u.message_id, u.conversation_id, u.created_at AS used_at,
                          c.title AS conversation_title
                   FROM memory_usages u
                   JOIN messages a ON a.id=u.message_id
                   JOIN conversations c ON c.id=u.conversation_id
                   WHERE u.memory_id=? ORDER BY u.id DESC LIMIT ?""",
                (memory_id, limit),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item['message_id'] = int(item['message_id'])
                item['conversation_id'] = int(item['conversation_id'])
                question = connection.execute(
                    """SELECT content FROM messages
                       WHERE conversation_id=? AND role='user' AND id<? ORDER BY id DESC LIMIT 1""",
                    (row['conversation_id'], row['message_id']),
                ).fetchone()
                item['question'] = question['content'] if question else None
                items.append(item)
        record = dict(memory)
        record.pop('id', None)
        record['memory_id'] = memory_id
        return {'memory': record, 'total': total, 'items': items}

    def candidate_pool_distribution(self, sources: int = 6, months: int = 6) -> dict[str, Any]:
        """Read-only shape of the un-reviewed pool by source document and extraction month.

        Months come from ``memories.created_at`` (when the candidate was extracted),
        not from the note's own date; the payload labels this explicitly.
        """
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT m.created_at, d.path, d.metadata_json
                   FROM memories m JOIN documents d ON d.id=m.document_id
                   WHERE m.status='candidate'"""
            ).fetchall()
            progress_rows = connection.execute(
                """SELECT d.path, m.status, COUNT(*) AS n
                   FROM memories m JOIN documents d ON d.id=m.document_id
                   GROUP BY d.path, m.status"""
            ).fetchall()
        by_source: dict[str, int] = {}
        by_month: dict[str, int] = {}
        by_record_month: dict[str, int] = {}
        record_dated = 0
        record_unknown = 0
        stamps = [row['created_at'] for row in rows if row['created_at']]
        for row in rows:
            path = row['path'] or '未标注'
            by_source[path] = by_source.get(path, 0) + 1
            month = (row['created_at'] or '')[:7]
            if len(month) == 7:
                by_month[month] = by_month.get(month, 0) + 1
            record_date = _record_date(row['path'], row['metadata_json'])
            if record_date:
                record_dated += 1
                record_month = record_date[:7]
                by_record_month[record_month] = by_record_month.get(record_month, 0) + 1
            else:
                record_unknown += 1
        progress: dict[str, dict[str, int]] = {}
        for row in progress_rows:
            path = row['path'] or '未标注'
            item = progress.setdefault(path, {'candidate': 0, 'reviewed': 0, 'total': 0})
            count = int(row['n'])
            item['total'] += count
            if row['status'] == 'candidate':
                item['candidate'] += count
            else:
                item['reviewed'] += count
        return {
            'total': len(rows),
            'by_source': [
                {'path': path, 'count': count}
                for path, count in sorted(by_source.items(), key=lambda item: (-item[1], item[0]))[:sources]
            ],
            'by_month': [
                {'month': month, 'count': count}
                for month, count in sorted(by_month.items(), reverse=True)[:months]
            ],
            'by_source_progress': [
                {'path': path, **values}
                for path, values in sorted(
                    progress.items(),
                    key=lambda item: (-item[1]['candidate'], -item[1]['total'], item[0]),
                )[:sources]
            ],
            'by_record_month': [
                {'month': month, 'count': count}
                for month, count in sorted(by_record_month.items(), reverse=True)[:months]
            ],
            'record_dated_total': record_dated,
            'record_unknown_total': record_unknown,
            'record_basis': '按来源元数据日期或文件名/路径中的 YYYY-MM-DD 解析；解析不到的计入未标注',
            'oldest_extracted_at': min(stamps) if stamps else None,
            'newest_extracted_at': max(stamps) if stamps else None,
            'basis': '按提取时间统计，非原始记录日期',
        }
