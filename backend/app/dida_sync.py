"""滴答清单 → torch-os 单向同步（Phase 1）。

- 后端拉取滴答私有 V2 Web API 的全量快照与最近完成的任务；
- 把项目映射成清单、任务映射成 `type: task` 的 Markdown 笔记；
- 本地在上次同步后改过的笔记不覆盖，冲突可查看/保留本地；
- 凭据单独存放在当前库的 `.torch-os/dida-credentials.json`，不进 SQLite/备份；
- 本阶段不向滴答写回；滴答远端删除会把本地笔记移入任务回收站。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.concurrency import run_in_threadpool

from . import task_notes, tasks
from .db import Database, utc_now
from .dida_client import (
    DidaCredentials, DidaError, DidaProvider, PrivateDidaProvider, RemoteProject,
    RemoteSnapshot, RemoteTask, parse_cookie_input,
)
from .dida_mapping import (
    MappedTask, map_task, material_matches, sanitize_list_name,
)
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SOURCE = "dida_web"
CREDENTIALS_FILE = "dida-credentials.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_TRUE_VALUES = {"1", "true", "yes", "on"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS dida_sync_settings (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
    region TEXT NOT NULL DEFAULT 'dida365' CHECK(region IN ('dida365', 'ticktick')),
    interval_minutes INTEGER NOT NULL DEFAULT 15 CHECK(interval_minutes BETWEEN 5 AND 1440),
    completed_days INTEGER NOT NULL DEFAULT 30 CHECK(completed_days BETWEEN 0 AND 3650),
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    device_id TEXT NOT NULL DEFAULT '',
    device_version INTEGER NOT NULL DEFAULT 8000,
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_error_code TEXT NOT NULL DEFAULT '',
    last_error_at TEXT,
    last_run_status TEXT NOT NULL DEFAULT '',
    last_run_trigger TEXT NOT NULL DEFAULT '',
    last_run_counts_json TEXT NOT NULL DEFAULT '{}',
    last_run_warnings_json TEXT NOT NULL DEFAULT '[]',
    last_run_started_at TEXT,
    last_run_finished_at TEXT,
    last_checkpoint INTEGER,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dida_task_links (
    source TEXT NOT NULL DEFAULT 'dida_web',
    external_id TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    relpath TEXT NOT NULL DEFAULT '',
    last_title TEXT NOT NULL DEFAULT '',
    last_list_name TEXT NOT NULL DEFAULT '',
    last_local_revision TEXT NOT NULL DEFAULT '',
    last_remote_hash TEXT NOT NULL DEFAULT '',
    last_remote_content_hash TEXT NOT NULL DEFAULT '',
    last_remote_etag TEXT NOT NULL DEFAULT '',
    last_remote_modified TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'synced'
        CHECK(status IN ('synced', 'conflict', 'local_missing', 'remote_deleted')),
    conflict_reason TEXT NOT NULL DEFAULT '',
    conflict_at TEXT,
    pending_remote_title TEXT NOT NULL DEFAULT '',
    pending_remote_hash TEXT NOT NULL DEFAULT '',
    pending_remote_content_hash TEXT NOT NULL DEFAULT '',
    pending_remote_etag TEXT NOT NULL DEFAULT '',
    pending_remote_modified TEXT NOT NULL DEFAULT '',
    last_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_dida_task_links_status ON dida_task_links(status);
CREATE INDEX IF NOT EXISTS idx_dida_task_links_relpath ON dida_task_links(relpath);
CREATE TABLE IF NOT EXISTS dida_project_links (
    source TEXT NOT NULL DEFAULT 'dida_web',
    project_id TEXT NOT NULL,
    list_name TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '',
    closed INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(source, project_id)
);
CREATE INDEX IF NOT EXISTS idx_dida_project_links_list ON dida_project_links(list_name);
"""


class CredentialStore:
    """当前库的滴答凭据文件；绝不写入 SQLite、备份或日志。"""

    def __init__(self, data_dir: Path | str):
        self.path = Path(data_dir) / CREDENTIALS_FILE

    def load(self) -> tuple[str, str]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "", ""
        if not isinstance(payload, dict):
            return "", ""
        cookie = payload.get("cookie")
        csrf = payload.get("csrf_token", "")
        if not isinstance(cookie, str) or not isinstance(csrf, str):
            return "", ""
        return cookie.strip(), csrf.strip()

    def save(self, cookie: str, csrf: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "cookie": cookie, "csrf_token": csrf},
                             ensure_ascii=False)
        descriptor, temporary = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".dida-credentials-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def configured(self) -> bool:
        return bool(self.load()[0])


class DidaSyncSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: StrictBool = False
    region: str = Field(default="dida365", max_length=16)
    interval_minutes: StrictInt = Field(default=15, ge=5, le=1440)
    completed_days: StrictInt = Field(default=30, ge=0, le=3650)
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    device_version: StrictInt = Field(default=8000, ge=1, le=99999)
    cookie: str | None = None
    csrf_token: str | None = None
    clear_credentials: StrictBool = False
    regenerate_device_id: StrictBool = False


class DidaRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: StrictBool = False
    force: StrictBool = False


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "").strip() or "Asia/Shanghai")
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "时区不是有效的 IANA 时区") from None


def _iso_now() -> str:
    return utc_now()


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _valid_color(value: Any) -> str:
    text = str(value or "").strip()
    return text if _COLOR.match(text) else "#3b82f6"


def _completed_recent(task: RemoteTask, cutoff: datetime | None) -> bool:
    if cutoff is None or not task.completed_time:
        return True
    parsed = _parse_iso(task.completed_time)
    if parsed is None:
        raw = task.completed_time.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", raw))
        except ValueError:
            return True
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed >= cutoff


@dataclass
class SyncCounts:
    projects_created: int = 0
    created: int = 0
    updated: int = 0
    completed: int = 0
    uncompleted: int = 0
    unchanged: int = 0
    local_edits: int = 0
    conflicts: int = 0
    remote_deleted: int = 0
    local_missing: int = 0
    skipped: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


class DidaSyncService:
    def __init__(self, database: Database, vault_dir: Path | str, *,
                 read_only: bool = False, supported: bool = True,
                 provider_factory: Callable[[DidaCredentials], DidaProvider] | None = None):
        self.database = database
        self.vault_dir = Path(vault_dir)
        self.read_only = bool(read_only)
        self.supported = bool(supported)
        self.provider_factory = provider_factory or PrivateDidaProvider
        self.credentials = CredentialStore(Path(database.path).parent)
        self._running = False
        self._run_guard = asyncio.Lock()
        self.initialize()

    # ---------- 初始化 / 设置 ----------

    def initialize(self) -> None:
        with self.database.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO dida_sync_settings(id, device_id, updated_at) VALUES (1, ?, ?)",
                (uuid.uuid4().hex, _iso_now()),
            )
            row = connection.execute(
                "SELECT device_id FROM dida_sync_settings WHERE id = 1"
            ).fetchone()
            if row is None or not str(row["device_id"] or "").strip():
                connection.execute(
                    "UPDATE dida_sync_settings SET device_id = ?, updated_at = ? WHERE id = 1",
                    (uuid.uuid4().hex, _iso_now()),
                )
            connection.commit()

    def _row(self, connection=None) -> dict[str, Any]:
        if connection is None:
            with self.database.connect() as own:
                row = own.execute("SELECT * FROM dida_sync_settings WHERE id = 1").fetchone()
        else:
            row = connection.execute("SELECT * FROM dida_sync_settings WHERE id = 1").fetchone()
        if row is None:
            raise HTTPException(500, "滴答同步设置未初始化")
        return dict(row)

    def _link_counts(self, connection=None) -> dict[str, int]:
        counts = {"conflict": 0, "local_missing": 0, "remote_deleted": 0}
        if connection is None:
            with self.database.connect() as own:
                rows = own.execute(
                    "SELECT status, COUNT(*) AS total FROM dida_task_links GROUP BY status"
                ).fetchall()
        else:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM dida_task_links GROUP BY status"
            ).fetchall()
        for row in rows:
            if row["status"] in counts:
                counts[row["status"]] = int(row["total"])
        return counts

    def _credentials(self) -> tuple[str, str]:
        return self.credentials.load()

    def _settings_public(self) -> dict[str, Any]:
        row = self._row()
        cookie, csrf = self._credentials()
        counts = self._link_counts()
        try:
            last_counts = json.loads(row.get("last_run_counts_json") or "{}")
        except ValueError:
            last_counts = {}
        try:
            warnings = json.loads(row.get("last_run_warnings_json") or "[]")
        except ValueError:
            warnings = []
        enabled = bool(row.get("enabled"))
        interval = int(row.get("interval_minutes") or 15)
        next_due = None
        last_attempt = _parse_iso(row.get("last_attempt_at"))
        if enabled and cookie and last_attempt is not None:
            next_due = (last_attempt + timedelta(minutes=interval)).isoformat()
        return {
            "supported": self.supported,
            "read_only": self.read_only,
            "running": self._running,
            "configured": bool(cookie),
            "cookie_set": bool(cookie),
            "csrf_set": bool(csrf),
            "enabled": enabled,
            "region": row.get("region") or "dida365",
            "interval_minutes": interval,
            "completed_days": int(row.get("completed_days") or 0),
            "timezone": row.get("timezone") or "Asia/Shanghai",
            "device_version": int(row.get("device_version") or 8000),
            "device_id_prefix": str(row.get("device_id") or "")[:6],
            "last_attempt_at": row.get("last_attempt_at"),
            "last_success_at": row.get("last_success_at"),
            "last_error_code": row.get("last_error_code") or "",
            "last_error_at": row.get("last_error_at"),
            "last_run_status": row.get("last_run_status") or "",
            "last_run_trigger": row.get("last_run_trigger") or "",
            "last_run_counts": last_counts if isinstance(last_counts, dict) else {},
            "last_run_warnings": warnings if isinstance(warnings, list) else [],
            "last_run_finished_at": row.get("last_run_finished_at"),
            "checkpoint": row.get("last_checkpoint"),
            "consecutive_failures": int(row.get("consecutive_failures") or 0),
            "conflicts": counts["conflict"],
            "local_missing": counts["local_missing"],
            "remote_deleted": counts["remote_deleted"],
            "next_due_at": next_due,
        }

    def status(self) -> dict[str, Any]:
        return self._settings_public()

    def update_settings(self, payload: DidaSyncSettingsUpdate) -> dict[str, Any]:
        if payload.region not in {"dida365", "ticktick"}:
            raise HTTPException(422, "区域只能是 dida365 或 ticktick")
        if payload.clear_credentials:
            self.credentials.clear()
        else:
            cookie, csrf = self.credentials.load()
            if payload.cookie is not None and payload.cookie.strip():
                try:
                    cookie, parsed_csrf = parse_cookie_input(payload.cookie)
                except DidaError as error:
                    raise HTTPException(422, error.message) from None
                if payload.csrf_token is not None and payload.csrf_token.strip():
                    csrf = payload.csrf_token.strip()
                elif parsed_csrf:
                    csrf = parsed_csrf
                self.credentials.save(cookie, csrf)
            elif payload.csrf_token is not None:
                csrf_value = payload.csrf_token.strip()
                if len(csrf_value) > 8192:
                    raise HTTPException(422, "CSRF token 过长")
                self.credentials.save(cookie, csrf_value)
        _zone(payload.timezone)
        cookie, csrf = self.credentials.load()
        if payload.enabled and not cookie:
            raise HTTPException(422, "启用定时同步前先保存滴答 cookie")
        row = self._row()
        device_id = row.get("device_id") or ""
        if payload.regenerate_device_id or not device_id:
            device_id = uuid.uuid4().hex
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE dida_sync_settings SET
                   enabled = ?, region = ?, interval_minutes = ?, completed_days = ?,
                   timezone = ?, device_version = ?, device_id = ?, updated_at = ?
                   WHERE id = 1""",
                (1 if payload.enabled else 0, payload.region, payload.interval_minutes,
                 payload.completed_days, payload.timezone, payload.device_version,
                 device_id, _iso_now()),
            )
            connection.commit()
        self.database.audit("dida_sync_settings_updated", {
            "enabled": bool(payload.enabled),
            "region": payload.region,
            "interval_minutes": payload.interval_minutes,
            "credentials_cleared": bool(payload.clear_credentials),
        })
        return self._settings_public()

    def clear_credentials(self) -> dict[str, Any]:
        self.credentials.clear()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE dida_sync_settings SET enabled = 0, updated_at = ? WHERE id = 1",
                (_iso_now(),),
            )
            connection.commit()
        self.database.audit("dida_sync_credentials_cleared", {})
        return self._settings_public()

    def build_provider(self) -> DidaProvider:
        cookie, csrf = self.credentials.load()
        if not cookie:
            raise DidaError("not_configured", "还没有保存滴答登录凭据")
        row = self._row()
        credentials = DidaCredentials(
            region=str(row.get("region") or "dida365"),
            cookie=cookie,
            csrf_token=csrf,
            device_id=str(row.get("device_id") or ""),
            device_version=int(row.get("device_version") or 8000),
            timezone=str(row.get("timezone") or "Asia/Shanghai"),
        )
        return self.provider_factory(credentials)

    async def test_connection(self) -> dict[str, Any]:
        if not self.supported:
            raise DidaError("unsupported", "当前运行环境不支持滴答同步")
        provider = self.build_provider()
        try:
            return await provider.test_connection()
        finally:
            await provider.aclose()

    def due(self, now: datetime | None = None) -> bool:
        if not self.supported or self.read_only or self._running:
            return False
        cookie, _ = self.credentials.load()
        if not cookie:
            return False
        row = self._row()
        if not row.get("enabled"):
            return False
        current = now or datetime.now(timezone.utc)
        last = _parse_iso(row.get("last_attempt_at"))
        if last is None:
            return True
        return current >= last + timedelta(minutes=int(row.get("interval_minutes") or 15))

    # ---------- 同步状态 ----------

    def mark_attempt(self, trigger: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE dida_sync_settings SET
                   last_attempt_at = ?, last_run_status = 'running', last_run_trigger = ?,
                   last_run_started_at = ?, last_run_finished_at = NULL, updated_at = ?
                   WHERE id = 1""",
                (_iso_now(), trigger, _iso_now(), _iso_now()),
            )
            connection.commit()

    def mark_failure(self, error: Exception) -> None:
        code = error.code if isinstance(error, DidaError) else type(error).__name__
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT consecutive_failures FROM dida_sync_settings WHERE id = 1"
            ).fetchone()
            failures = int(row["consecutive_failures"] or 0) + 1 if row else 1
            connection.execute(
                """UPDATE dida_sync_settings SET last_error_code = ?, last_error_at = ?,
                   last_run_status = 'error', last_run_finished_at = ?,
                   consecutive_failures = ?, updated_at = ? WHERE id = 1""",
                (code, _iso_now(), _iso_now(), failures, _iso_now()),
            )
            connection.commit()
        if failures == 1:
            self.database.audit("dida_sync_failed", {"code": code})

    def mark_success(self, summary: dict[str, Any]) -> None:
        counts = summary.get("counts") or {}
        warnings = summary.get("warnings") or []
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE dida_sync_settings SET last_success_at = ?, last_error_code = '',
                   last_run_status = 'success', last_run_counts_json = ?,
                   last_run_warnings_json = ?, last_run_finished_at = ?,
                   last_checkpoint = COALESCE(?, last_checkpoint),
                   consecutive_failures = 0, updated_at = ? WHERE id = 1""",
                (_iso_now(), json.dumps(counts, ensure_ascii=False),
                 json.dumps(warnings, ensure_ascii=False), _iso_now(),
                 summary.get("checkpoint"), _iso_now()),
            )
            connection.commit()
        self.database.audit("dida_sync_completed", {
            "trigger": summary.get("trigger"),
            "counts": counts,
            "warnings": warnings[:20],
        })

    # ---------- 拉取 / 应用 ----------

    def _load_links(self, connection) -> dict[str, dict[str, Any]]:
        rows = connection.execute(
            "SELECT * FROM dida_task_links WHERE source = ?", (SOURCE,)
        ).fetchall()
        return {str(row["external_id"]): dict(row) for row in rows}

    def _load_project_links(self, connection) -> dict[str, dict[str, Any]]:
        rows = connection.execute(
            "SELECT * FROM dida_project_links WHERE source = ?", (SOURCE,)
        ).fetchall()
        return {str(row["project_id"]): dict(row) for row in rows}

    def _upsert_project_link(self, connection, project: RemoteProject, list_name: str) -> None:
        now = _iso_now()
        connection.execute(
            """INSERT INTO dida_project_links
               (source, project_id, list_name, color, closed, last_seen_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source, project_id) DO UPDATE SET
                 list_name = excluded.list_name, color = excluded.color,
                 closed = excluded.closed, last_seen_at = excluded.last_seen_at""",
            (SOURCE, project.id, list_name, _valid_color(project.color),
             1 if project.closed else 0, now, now),
        )
        connection.commit()

    def _save_link_synced(self, connection, mapped: MappedTask, note: dict[str, Any]) -> None:
        now = _iso_now()
        connection.execute(
            """INSERT INTO dida_task_links
               (source, external_id, project_id, relpath, last_title, last_list_name,
                last_local_revision, last_remote_hash, last_remote_content_hash,
                last_remote_etag, last_remote_modified, status, conflict_reason,
                conflict_at, pending_remote_title, pending_remote_hash,
                pending_remote_content_hash, pending_remote_etag, pending_remote_modified,
                last_synced_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'synced', '', NULL,
                       '', '', '', '', '', ?, ?, ?)
               ON CONFLICT(source, external_id) DO UPDATE SET
                 project_id = excluded.project_id, relpath = excluded.relpath,
                 last_title = excluded.last_title, last_list_name = excluded.last_list_name,
                 last_local_revision = excluded.last_local_revision,
                 last_remote_hash = excluded.last_remote_hash,
                 last_remote_content_hash = excluded.last_remote_content_hash,
                 last_remote_etag = excluded.last_remote_etag,
                 last_remote_modified = excluded.last_remote_modified,
                 status = 'synced', conflict_reason = '', conflict_at = NULL,
                 pending_remote_title = '', pending_remote_hash = '',
                 pending_remote_content_hash = '', pending_remote_etag = '',
                 pending_remote_modified = '', last_synced_at = excluded.last_synced_at,
                 updated_at = excluded.updated_at""",
            (SOURCE, mapped.dida_id, mapped.project_id, note["relpath"],
             mapped.title, mapped.list_name, note["revision"], mapped.remote_hash,
             mapped.content_hash, mapped.etag, mapped.modified, now, now, now),
        )
        connection.commit()

    def _save_link_conflict(self, connection, mapped: MappedTask,
                            note: dict[str, Any] | None, reason: str) -> None:
        now = _iso_now()
        relpath = note["relpath"] if note else ""
        revision = note["revision"] if note else ""
        local_title = note["title"] if note else mapped.title
        local_list = note["list_name"] if note else mapped.list_name
        connection.execute(
            """INSERT INTO dida_task_links
               (source, external_id, project_id, relpath, last_title, last_list_name,
                last_local_revision, last_remote_hash, last_remote_content_hash,
                last_remote_etag, last_remote_modified, status, conflict_reason,
                conflict_at, pending_remote_title, pending_remote_hash,
                pending_remote_content_hash, pending_remote_etag, pending_remote_modified,
                last_synced_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, '', '', '', '', 'conflict', ?, ?,
                       ?, ?, ?, ?, ?, NULL, ?, ?)
               ON CONFLICT(source, external_id) DO UPDATE SET
                 project_id = excluded.project_id, relpath = excluded.relpath,
                 last_title = excluded.last_title, last_list_name = excluded.last_list_name,
                 status = 'conflict', conflict_reason = excluded.conflict_reason,
                 conflict_at = excluded.conflict_at,
                 pending_remote_title = excluded.pending_remote_title,
                 pending_remote_hash = excluded.pending_remote_hash,
                 pending_remote_content_hash = excluded.pending_remote_content_hash,
                 pending_remote_etag = excluded.pending_remote_etag,
                 pending_remote_modified = excluded.pending_remote_modified,
                 updated_at = excluded.updated_at""",
            (SOURCE, mapped.dida_id, mapped.project_id, relpath, local_title,
             local_list, revision, reason, now, mapped.title, mapped.remote_hash,
             mapped.content_hash, mapped.etag, mapped.modified, now, now),
        )
        connection.commit()

    def _apply_remote_deletion(self, connection, external_id: str,
                               note: dict[str, Any] | None,
                               link: dict[str, Any] | None,
                               counts: SyncCounts, warnings: Counter,
                               dry_run: bool) -> None:
        """滴答删除：第一次把本地笔记移入回收站；已处理过/已还原的不再重复。"""
        status = str(link.get("status") or "") if link else ""
        if link is not None and status == "remote_deleted":
            counts.unchanged += 1
            return
        if note is not None:
            if not dry_run:
                try:
                    task_notes.trash_note(self.vault_dir, note)
                except Exception as error:
                    counts.errors += 1
                    warnings[f"trash_error_{type(error).__name__}"] += 1
                    return
            counts.remote_deleted += 1
            if not dry_run and link is not None:
                self._mark_link_status(connection, external_id, "remote_deleted",
                                       "remote_deleted")
            return
        counts.remote_deleted += 1
        if not dry_run and link is not None:
            self._mark_link_status(connection, external_id, "remote_deleted",
                                   "remote_deleted")

    def _mark_link_status(self, connection, external_id: str, status: str, reason: str = "") -> None:
        connection.execute(
            """UPDATE dida_task_links SET status = ?, conflict_reason = ?,
               conflict_at = CASE WHEN ? = 'conflict' THEN ? ELSE conflict_at END,
               updated_at = ? WHERE source = ? AND external_id = ?""",
            (status, reason, status, _iso_now(), _iso_now(), SOURCE, external_id),
        )
        connection.commit()

    def _merge_tasks(self, snapshot: RemoteSnapshot, completed: tuple[RemoteTask, ...],
                     completed_days: int, counts: SyncCounts,
                     warnings: Counter) -> tuple[dict[str, RemoteTask], set[str]]:
        merged: dict[str, RemoteTask] = {
            task.id: task for task in snapshot.tasks if task.id
        }
        cutoff = None
        if completed_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=completed_days)
        completed_ids: set[str] = set()
        recent: list[RemoteTask] = []
        for task in completed:
            if not task.id:
                continue
            if not _completed_recent(task, cutoff):
                counts.skipped += 1
                warnings["completed_filtered"] += 1
                continue
            recent.append(task)
            completed_ids.add(task.id)
        if len(recent) > 2000:
            recent.sort(key=lambda item: item.completed_time or "", reverse=True)
            dropped = len(recent) - 2000
            recent = recent[:2000]
            counts.skipped += dropped
            warnings["completed_truncated"] += dropped
        for task in recent:
            merged[task.id] = task
        return merged, completed_ids

    def _resolve_list(self, project: RemoteProject, snapshot: RemoteSnapshot,
                      project_links: dict[str, dict[str, Any]],
                      lists_by_name: dict[str, dict[str, Any]],
                      counts: SyncCounts, warnings: Counter, dry_run: bool) -> str | None:
        if project.id and (project.id == snapshot.inbox_id or project.id.lower().startswith("inbox")):
            return task_notes.INBOX
        # 清单在 torch-os 里改名后，dida_project_id 仍在条目里；优先用它，
        # 避免把旧项目链接误判成“本地清单已删除”。
        for name, entry in lists_by_name.items():
            if str(entry.get("dida_project_id") or "") == project.id:
                return name
        link = project_links.get(project.id)
        if link:
            name = str(link.get("list_name") or "").strip()
            if name and (name in lists_by_name or
                         task_notes.task_folder(self.vault_dir, name).is_dir()):
                return name
            warnings["list_deleted"] += 1
            counts.skipped += 1
            return None
        if project.closed:
            warnings["closed_project_inbox"] += 1
            return task_notes.INBOX
        candidate = sanitize_list_name(project.name, fallback=f"滴答清单-{project.id[:6]}")
        if candidate.casefold() == task_notes.INBOX.casefold():
            candidate = f"{candidate}-{project.id[:6]}"
        if candidate in lists_by_name:
            mapped_to = str(lists_by_name[candidate].get("dida_project_id") or "")
            if not mapped_to or mapped_to == project.id:
                return candidate
            base = candidate[: max(1, 40 - len(project.id[:6]) - 2)]
            candidate = f"{base} {project.id[:6]}"
        if candidate in lists_by_name:
            return candidate
        if dry_run:
            counts.projects_created += 1
            lists_by_name[candidate] = {"name": candidate, "dida_project_id": project.id}
            return candidate
        try:
            state = tasks.read_tasks(self.vault_dir, read_only=False)
            tasks.create_list(
                self.vault_dir,
                tasks.ListCreate(name=candidate, color=_valid_color(project.color),
                                 dida_project_id=project.id,
                                 revision=state["revision"]),
                self.database,
                read_only=False,
            )
        except (HTTPException, VaultReadOnlyError, OSError):
            warnings["list_create_failed"] += 1
            return task_notes.INBOX
        counts.projects_created += 1
        lists_by_name[candidate] = {"name": candidate, "dida_project_id": project.id}
        return candidate

    def apply_snapshot(self, snapshot: RemoteSnapshot, completed: tuple[RemoteTask, ...], *,
                       dry_run: bool = False, force: bool = False,
                       trigger: str = "manual") -> dict[str, Any]:
        if not self.supported:
            raise DidaError("unsupported", "当前运行环境不支持滴答同步")
        if not dry_run:
            ensure_vault_writable(self.read_only, "从滴答清单同步任务")
        counts = SyncCounts()
        warnings: Counter = Counter()
        started = _iso_now()
        with self.database.connect() as connection:
            settings = self._row(connection)
            tz = _zone(str(settings.get("timezone") or "Asia/Shanghai"))
            notes, scan_warnings = task_notes.scan_task_notes(self.vault_dir)
            for warning in scan_warnings:
                warnings["scan_warning"] += 1
            notes_by_dida: dict[str, dict[str, Any]] = {}
            for note in notes:
                dida_id = str(note.get("dida_id") or "").strip()
                if not dida_id:
                    continue
                if dida_id in notes_by_dida:
                    warnings["duplicate_dida_id"] += 1
                    continue
                notes_by_dida[dida_id] = note
            links = self._load_links(connection)
            project_links = self._load_project_links(connection)
            list_state = tasks.read_tasks(self.vault_dir, read_only=self.read_only)
            lists_by_name: dict[str, dict[str, Any]] = {
                str(entry.get("name")): entry for entry in list_state.get("lists", [])
                if entry.get("name")
            }
            merged, _completed_ids = self._merge_tasks(
                snapshot, completed, int(settings.get("completed_days") or 0),
                counts, warnings,
            )
            projects = {project.id: project for project in snapshot.projects if project.id}
            if snapshot.inbox_id and snapshot.inbox_id not in projects:
                projects[snapshot.inbox_id] = RemoteProject(
                    id=snapshot.inbox_id, name=task_notes.INBOX, color="", sort_order=0,
                    closed=False, view_mode="", group_id="", etag="", modified_time="",
                )
            needed = {task.project_id or snapshot.inbox_id for task in merged.values()}
            needed.add(snapshot.inbox_id)
            # 先结束只读事务，避免随后 create_list 在另一条连接上写审计时被锁住。
            connection.commit()
            project_to_list: dict[str, str | None] = {}
            for project_id in needed:
                project = projects.get(project_id)
                if project is None:
                    project_to_list[project_id] = task_notes.INBOX
                    warnings["unknown_project"] += 1
                    continue
                list_name = self._resolve_list(
                    project, snapshot, project_links, lists_by_name, counts, warnings, dry_run
                )
                project_to_list[project_id] = list_name
                if list_name and not dry_run:
                    self._upsert_project_link(connection, project, list_name)
                    project_links[project.id] = {"project_id": project.id, "list_name": list_name}

            for deleted_id in snapshot.deleted_ids:
                if deleted_id in merged:
                    continue
                self._apply_remote_deletion(
                    connection, deleted_id, notes_by_dida.get(deleted_id),
                    links.get(deleted_id), counts, warnings, dry_run,
                )
            for task in sorted(merged.values(),
                               key=lambda item: (item.project_id, item.id)):
                if not task.id:
                    counts.skipped += 1
                    warnings["invalid_task"] += 1
                    continue
                if task.deleted or task.id in snapshot.deleted_ids:
                    self._apply_remote_deletion(
                        connection, task.id, notes_by_dida.get(task.id),
                        links.get(task.id), counts, warnings, dry_run,
                    )
                    continue
                project_id = task.project_id or snapshot.inbox_id
                list_name = project_to_list.get(project_id)
                if list_name is None:
                    counts.skipped += 1
                    continue
                mapped = map_task(task, target_tz=tz, list_name=list_name)
                for warning in mapped.warnings:
                    warnings[warning] += 1
                note = notes_by_dida.get(task.id)
                link = links.get(task.id)
                try:
                    if note is None:
                        if link is not None:
                            counts.local_missing += 1
                            if not dry_run:
                                self._mark_link_status(connection, task.id, "local_missing",
                                                       "local_note_missing")
                            continue
                        if dry_run:
                            counts.created += 1
                            counts.completed += 1 if mapped.values["done"] else 0
                            continue
                        folder = "" if list_name == task_notes.INBOX else list_name
                        fresh = task_notes.create_task_note(
                            self.vault_dir, folder, mapped.title, mapped.values,
                            body=mapped.body,
                        )
                        notes_by_dida[task.id] = fresh
                        self._save_link_synced(connection, mapped, fresh)
                        links[task.id] = {"external_id": task.id}
                        counts.created += 1
                        if mapped.values["done"]:
                            counts.completed += 1
                        continue

                    note_body = task_notes.note_body(note)
                    content_changed = (
                        link is None or mapped.content_hash != str(link.get("last_remote_content_hash") or "")
                    )
                    body_matches = note_body.strip() == mapped.body.strip()
                    fields_match = material_matches(note, mapped, compare_body=False)
                    if link is None:
                        if fields_match:
                            if not dry_run:
                                self._save_link_synced(connection, mapped, note)
                                links[task.id] = {"external_id": task.id}
                            counts.unchanged += 1
                        else:
                            counts.conflicts += 1
                            warnings["adopted_note_conflict"] += 1
                            if not dry_run:
                                self._save_link_conflict(connection, mapped, note,
                                                         "existing_note_differs")
                        continue

                    local_changed = (
                        note["revision"] != str(link.get("last_local_revision") or "")
                        or note["relpath"] != str(link.get("relpath") or "")
                    )
                    remote_changed = mapped.remote_hash != str(link.get("last_remote_hash") or "")
                    material_differs = not fields_match or (content_changed and not body_matches)

                    if not remote_changed and not local_changed:
                        # 两边都没动：即使字段有差异（例如用户点了“保留本地”），也不覆盖。
                        counts.unchanged += 1
                        continue
                    if not remote_changed and local_changed:
                        counts.local_edits += 1
                        continue
                    if remote_changed and local_changed and not force:
                        counts.conflicts += 1
                        if not dry_run:
                            self._save_link_conflict(connection, mapped, note,
                                                     "local_and_remote_changed")
                        continue
                    if not material_differs:
                        counts.unchanged += 1
                        if not dry_run:
                            self._save_link_synced(connection, mapped, note)
                        continue
                    if dry_run:
                        counts.updated += 1
                        if mapped.values["done"] and not note.get("done"):
                            counts.completed += 1
                        if not mapped.values["done"] and note.get("done"):
                            counts.uncompleted += 1
                        continue
                    folder = "" if list_name == task_notes.INBOX else list_name
                    fresh = task_notes.rewrite_task_note(
                        self.vault_dir, note, folder=folder, title=mapped.title,
                        values=mapped.values,
                        body=mapped.body if content_changed else None,
                    )
                    notes_by_dida[task.id] = fresh
                    self._save_link_synced(connection, mapped, fresh)
                    links[task.id] = {"external_id": task.id}
                    counts.updated += 1
                    if mapped.values["done"] and not note.get("done"):
                        counts.completed += 1
                    if not mapped.values["done"] and note.get("done"):
                        counts.uncompleted += 1
                except Exception as error:  # 单条任务失败不中断整次同步
                    counts.errors += 1
                    warnings[f"task_error_{type(error).__name__}"] += 1
                    continue
            if not dry_run:
                connection.commit()
        finished = _iso_now()
        return {
            "ok": True,
            "dry_run": bool(dry_run),
            "force": bool(force),
            "trigger": trigger,
            "started_at": started,
            "finished_at": finished,
            "checkpoint": snapshot.checkpoint,
            "counts": counts.as_dict(),
            "warnings": sorted(warnings),
            "warning_counts": dict(sorted(warnings.items())),
        }

    def conflicts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT external_id, relpath, last_title, last_list_name,
                          conflict_reason, conflict_at, pending_remote_title
                   FROM dida_task_links WHERE source = ? AND status = 'conflict'
                   ORDER BY conflict_at DESC, external_id ASC LIMIT ?""",
                (SOURCE, max(1, min(int(limit), 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def keep_local(self, external_id: str) -> dict[str, Any]:
        dida_id = str(external_id or "").strip()
        if not dida_id:
            raise HTTPException(404, "没有这条同步记录")
        notes, _ = task_notes.scan_task_notes(self.vault_dir)
        note = next((item for item in notes
                     if str(item.get("dida_id") or "").strip() == dida_id), None)
        if note is None:
            raise HTTPException(404, "本地已找不到对应任务笔记")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM dida_task_links WHERE source = ? AND external_id = ?",
                (SOURCE, dida_id),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "没有这条同步记录")
            connection.execute(
                """UPDATE dida_task_links SET status = 'synced', conflict_reason = '',
                   conflict_at = NULL, last_local_revision = ?, relpath = ?,
                   last_remote_hash = pending_remote_hash,
                   last_remote_content_hash = pending_remote_content_hash,
                   last_remote_etag = pending_remote_etag,
                   last_remote_modified = pending_remote_modified,
                   pending_remote_title = '', pending_remote_hash = '',
                   pending_remote_content_hash = '', pending_remote_etag = '',
                   pending_remote_modified = '', last_synced_at = ?, updated_at = ?
                   WHERE source = ? AND external_id = ?""",
                (note["revision"], note["relpath"], _iso_now(), _iso_now(),
                 SOURCE, dida_id),
            )
            connection.commit()
        self.database.audit("dida_sync_keep_local", {"external_id": dida_id})
        return {"ok": True, "external_id": dida_id, "status": "synced"}


async def run_dida_sync(service: DidaSyncService, data_lock: asyncio.Lock, *,
                        trigger: str = "manual", dry_run: bool = False,
                        force: bool = False) -> dict[str, Any]:
    if not service.supported:
        raise DidaError("unsupported", "当前运行环境不支持滴答同步")
    if service.read_only and not dry_run:
        raise VaultReadOnlyError("当前是 Obsidian 只读模式，已拒绝从滴答清单同步任务")
    if service._run_guard.locked():
        raise DidaError("sync_running", "已经有一次滴答同步在进行")
    async with service._run_guard:
        service._running = True
        if not dry_run:
            service.mark_attempt(trigger)
        provider: DidaProvider | None = None
        try:
            provider = service.build_provider()
            snapshot = await provider.fetch_snapshot(0)
            completed = await provider.fetch_completed()
            await provider.aclose()
            provider = None
            async with data_lock:
                summary = await run_in_threadpool(
                    service.apply_snapshot, snapshot, completed,
                    dry_run=dry_run, force=force, trigger=trigger,
                )
            if not dry_run:
                service.mark_success(summary)
            return summary
        except Exception as error:
            if not dry_run:
                service.mark_failure(error)
            raise
        finally:
            if provider is not None:
                await provider.aclose()
            service._running = False


async def dida_sync_loop(service: DidaSyncService, data_lock: asyncio.Lock) -> None:
    """每 60 秒检查一次；设置里的间隔按上次尝试时间计算。"""
    while True:
        try:
            if service.due():
                await run_dida_sync(service, data_lock, trigger="scheduled")
        except asyncio.CancelledError:
            raise
        except Exception:
            # run_dida_sync 已记录 last_error；循环不重复刷审计。
            pass
        await asyncio.sleep(60)


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, VaultReadOnlyError):
        return HTTPException(409, str(error))
    if isinstance(error, DidaError):
        status = {
            "not_configured": 409,
            "bad_cookie": 409,
            "invalid_credentials": 422,
            "invalid_region": 422,
            "sync_running": 409,
            "unsupported": 409,
            "rate_limited": 429,
            "network_error": 502,
            "invalid_response": 502,
        }.get(error.code, 502)
        headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
        return HTTPException(status, error.message, headers=headers)
    return HTTPException(502, "滴答同步失败，请查看服务状态后重试")


def create_dida_sync_router(service: DidaSyncService, data_lock: asyncio.Lock, *,
                            prefix: str = "/api/sync/dida") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["dida-sync"])

    @router.get("/status")
    def status():
        return service.status()

    @router.put("/settings")
    def settings_update(payload: DidaSyncSettingsUpdate):
        return service.update_settings(payload)

    @router.post("/test")
    async def test_connection():
        try:
            return await service.test_connection()
        except Exception as error:
            raise _http_error(error) from None

    @router.post("/run")
    async def run(payload: DidaRunRequest | None = None):
        request = payload or DidaRunRequest()
        try:
            return await run_dida_sync(service, data_lock, trigger="manual",
                                       dry_run=request.dry_run, force=request.force)
        except Exception as error:
            raise _http_error(error) from None

    @router.get("/conflicts")
    def conflicts(limit: int = Query(default=100, ge=1, le=200)):
        return {"conflicts": service.conflicts(limit)}

    @router.post("/conflicts/{external_id}/keep-local")
    async def keep_local(external_id: str):
        async with data_lock:
            return await run_in_threadpool(service.keep_local, external_id)

    @router.delete("/credentials")
    def clear_credentials():
        return service.clear_credentials()

    return router


__all__ = [
    "CredentialStore", "DidaSyncService", "DidaSyncSettingsUpdate", "DidaRunRequest",
    "create_dida_sync_router", "dida_sync_loop", "run_dida_sync",
]
