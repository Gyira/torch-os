"""滴答清单 → torch-os 单向同步（Phase 1）。

- 后端拉取滴答私有 V2 Web API 的全量快照与最近完成的任务；
- 把项目映射成清单、任务映射成 `type: task` 的 Markdown 笔记；
- 本地在上次同步后改过的笔记不覆盖，冲突可查看/保留本地；
- 凭据单独存放在当前库的 `.torch-os/dida-credentials.json`，不进 SQLite/备份；
- 默认拉取；可选把本地删除回写滴答（需要 CSRF）；滴答远端删除会把本地笔记移入任务回收站。
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

from . import task_notes, task_trash, tasks
from .db import Database, utc_now
from .dida_client import (
    DidaCredentials, DidaError, DidaProvider, PrivateDidaProvider, RemoteProject,
    RemoteSnapshot, RemoteTask, parse_cookie_input, task_from_payload,
)
from .dida_mapping import (
    MappedTask, build_remote_task_payload, legacy_all_day_date, map_task,
    material_matches, sanitize_list_name,
)
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SOURCE = "dida_web"
CREDENTIALS_FILE = "dida-credentials.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_TRUE_VALUES = {"1", "true", "yes", "on"}
# 滴答删除接口返回这些错误码时，等价于“已经不存在”，按成功处理。
_DELETE_OK_CODES = {"NOT_EXISTED", "DELETED", "NOT_EXIST", "NOT_FOUND"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS dida_sync_settings (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
    region TEXT NOT NULL DEFAULT 'dida365' CHECK(region IN ('dida365', 'ticktick')),
    interval_minutes INTEGER NOT NULL DEFAULT 15 CHECK(interval_minutes BETWEEN 5 AND 1440),
    completed_days INTEGER NOT NULL DEFAULT 30 CHECK(completed_days BETWEEN 0 AND 3650),
    push_deletes INTEGER NOT NULL DEFAULT 1 CHECK(push_deletes IN (0, 1)),
    push_changes INTEGER NOT NULL DEFAULT 1 CHECK(push_changes IN (0, 1)),
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
    last_push_at TEXT,
    last_push_error_code TEXT NOT NULL DEFAULT '',
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
    remote_payload_json TEXT NOT NULL DEFAULT '',
    pending_remote_payload_json TEXT NOT NULL DEFAULT '',
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
    synced_name TEXT NOT NULL DEFAULT '',
    remote_seen INTEGER NOT NULL DEFAULT 1,
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
    push_deletes: StrictBool = True
    push_changes: StrictBool = True
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


class DidaRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_ids: list[str] = Field(min_length=1, max_length=500)


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
    projects_deleted: int = 0
    projects_created_pushed: int = 0
    projects_renamed_pushed: int = 0
    projects_deleted_pushed: int = 0
    created: int = 0
    created_pushed: int = 0
    restored: int = 0
    updated: int = 0
    updated_pushed: int = 0
    moved_pushed: int = 0
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
        self._push_guard = asyncio.Lock()
        self._push_backoff_until: datetime | None = None
        self._local_push_backoff_until: datetime | None = None
        # 清单名 → 待用于远端新建的项目 ID；重试时复用，避免瞬时失败产生重复清单。
        self._pending_projects: dict[str, str] = {}
        self.initialize()

    # ---------- 初始化 / 设置 ----------

    def initialize(self) -> None:
        with self.database.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute(
                "PRAGMA table_info(dida_sync_settings)"
            )}
            for name, declaration in (
                ("push_deletes", "INTEGER NOT NULL DEFAULT 1"),
                ("push_changes", "INTEGER NOT NULL DEFAULT 1"),
                ("last_push_at", "TEXT"),
                ("last_push_error_code", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE dida_sync_settings ADD COLUMN {name} {declaration}"
                    )
            task_columns = {row["name"] for row in connection.execute(
                "PRAGMA table_info(dida_task_links)"
            )}
            for name, declaration in (
                ("remote_payload_json", "TEXT NOT NULL DEFAULT ''"),
                ("pending_remote_payload_json", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in task_columns:
                    connection.execute(
                        f"ALTER TABLE dida_task_links ADD COLUMN {name} {declaration}"
                    )
            project_columns = {row["name"] for row in connection.execute(
                "PRAGMA table_info(dida_project_links)"
            )}
            added_synced_name = "synced_name" not in project_columns
            for name, declaration in (
                ("synced_name", "TEXT NOT NULL DEFAULT ''"),
                ("remote_seen", "INTEGER NOT NULL DEFAULT 1"),
            ):
                if name not in project_columns:
                    connection.execute(
                        f"ALTER TABLE dida_project_links ADD COLUMN {name} {declaration}"
                    )
            if added_synced_name:
                # 升级前的链接以当前本地清单名作为基线，避免把历史改名误当成待回写。
                connection.execute(
                    "UPDATE dida_project_links SET synced_name = list_name "
                    "WHERE source = ? AND synced_name = ''",
                    (SOURCE,),
                )
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
            "push_deletes": bool(row.get("push_deletes", 1)),
            "push_changes": bool(row.get("push_changes", 1)),
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
            "last_push_at": row.get("last_push_at"),
            "last_push_error_code": row.get("last_push_error_code") or "",
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
                   push_deletes = ?, push_changes = ?, timezone = ?, device_version = ?,
                   device_id = ?, updated_at = ? WHERE id = 1""",
                (1 if payload.enabled else 0, payload.region, payload.interval_minutes,
                 payload.completed_days, 1 if payload.push_deletes else 0,
                 1 if payload.push_changes else 0,
                 payload.timezone, payload.device_version, device_id, _iso_now()),
            )
            connection.commit()
        self.database.audit("dida_sync_settings_updated", {
            "enabled": bool(payload.enabled),
            "region": payload.region,
            "interval_minutes": payload.interval_minutes,
            "credentials_cleared": bool(payload.clear_credentials),
        })
        self._push_backoff_until = None
        self._local_push_backoff_until = None
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

    def _pending_delete_entries(self) -> list[dict[str, Any]]:
        """已同步、且还没回写删除的本地删除任务。

        回收站是主要来源；如果任务已被“彻底删除”或回收站被清空，只要同步链接还在、
        原笔记也不在任务目录里，仍然补发删除——否则先清理回收站就永远删不掉远端任务。
        """
        if not self.supported or self.read_only:
            return []
        row = self._row()
        if not row.get("push_deletes"):
            return []
        cookie, _csrf = self.credentials.load()
        if not cookie:
            return []
        items = task_trash.list_trash(self.vault_dir)
        with self.database.connect() as connection:
            links = self._load_links(connection)
            project_links = self._load_project_links(connection)
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(dida_id: str, link: dict[str, Any], item: dict[str, Any]) -> None:
            if not dida_id or dida_id in seen:
                return
            if str(link.get("status") or "") == "remote_deleted":
                return
            project_id = str(link.get("project_id") or "")
            # 远端清单已经被删除时，任务多半随清单一起消失；不再逐条回写。
            if not project_id or project_id not in project_links:
                return
            seen.add(dida_id)
            entries.append({"taskId": dida_id, "projectId": project_id, "item": item})

        for item in items:
            dida_id = str(item.get("dida_id") or "")
            link = links.get(dida_id)
            if link is not None:
                add(dida_id, link, item)

        # 回收站里找不到、但本地已不存在的已同步任务（例如已被彻底清理）。
        # 先扫描现有任务笔记，避免把“在任务目录里换了位置”的笔记误判成删除。
        root = Path(self.vault_dir)
        candidates: list[tuple[str, dict[str, Any]]] = []
        for dida_id, link in links.items():
            if dida_id in seen or str(link.get("status") or "") == "remote_deleted":
                continue
            relpath = str(link.get("relpath") or "")
            if not task_notes.is_task_relpath(relpath):
                continue
            try:
                if (root / relpath).exists():
                    continue
            except OSError:
                continue
            candidates.append((dida_id, link))
        if candidates:
            notes, _warnings = task_notes.scan_task_notes(self.vault_dir)
            existing = {str(note.get("dida_id") or "") for note in notes}
            for dida_id, link in candidates:
                if dida_id in existing:
                    continue
                add(dida_id, link, {"dida_id": dida_id,
                                    "trash_path": str(link.get("relpath") or "")})
        return entries

    def _local_list_delete_candidates(self) -> list[dict[str, Any]]:
        """本地已删除、但仍与滴答项目保持映射的清单；只读，不发请求。"""
        if not self.supported or self.read_only:
            return []
        row = self._row()
        if not row.get("push_deletes"):
            return []
        state = tasks.read_tasks(self.vault_dir, read_only=self.read_only)
        linked_ids = {
            str(entry.get("dida_project_id") or "").strip()
            for entry in state.get("lists", [])
            if str(entry.get("dida_project_id") or "").strip()
        }
        with self.database.connect() as connection:
            project_links = self._load_project_links(connection)
        candidates: list[dict[str, Any]] = []
        for project_id, link in project_links.items():
            name = str(link.get("list_name") or "").strip()
            if not project_id or project_id in linked_ids:
                continue
            if not name or name.casefold() == task_notes.INBOX.casefold():
                continue
            if project_id.lower().startswith("inbox"):
                continue
            try:
                if task_notes.task_folder(self.vault_dir, name).is_dir():
                    continue
            except Exception:  # 名字无法安全转成目录时宁可不动
                continue
            candidates.append({"project_id": project_id, "list_name": name})
        return candidates

    async def _push_local_list_deletions(self, provider: DidaProvider,
                                         items: list[dict[str, Any]],
                                         counts: SyncCounts,
                                         warnings: Counter) -> str:
        try:
            result = await provider.delete_projects(
                [item["project_id"] for item in items]
            )
        except DidaError as error:
            counts.errors += len(items)
            warnings[f"project_push_error_{error.code}"] += len(items)
            return error.code
        errors = result.get("errors") if isinstance(result, dict) else {}
        first_error = ""
        for item in items:
            project_id = item["project_id"]
            code = str((errors or {}).get(project_id) or "")
            if code and code not in _DELETE_OK_CODES:
                counts.errors += 1
                warnings[f"project_push_error_{code}"] += 1
                first_error = first_error or code
                continue
            with self.database.connect() as connection:
                for external_id, link in self._load_links(connection).items():
                    if str(link.get("project_id") or "") != project_id:
                        continue
                    if str(link.get("status") or "") == "remote_deleted":
                        continue
                    self._mark_link_status(connection, external_id,
                                           "remote_deleted", "deleted_with_project")
                self._delete_project_link(connection, project_id)
            counts.projects_deleted_pushed += 1
        return first_error

    async def push_deletions(self, *, force: bool = False) -> dict[str, Any]:
        """把本地删除的清单和已同步任务回写滴答（需要 CSRF）。"""
        row = self._row()
        if not self.supported:
            return {"enabled": False, "deleted": 0, "projects_deleted": 0,
                    "counts": {}, "warnings": [], "errors": []}
        if self.read_only:
            return {"enabled": False, "deleted": 0, "projects_deleted": 0,
                    "counts": {}, "warnings": [], "errors": []}
        if not row.get("push_deletes"):
            return {"enabled": False, "deleted": 0, "projects_deleted": 0,
                    "counts": {}, "warnings": [], "errors": []}
        now = datetime.now(timezone.utc)
        if not force and self._push_backoff_until and now < self._push_backoff_until:
            return {"enabled": True, "deleted": 0, "projects_deleted": 0,
                    "skipped": "backoff", "counts": {}, "warnings": [], "errors": []}
        if self._push_guard.locked():
            return {"enabled": True, "deleted": 0, "projects_deleted": 0,
                    "skipped": "running", "counts": {}, "warnings": [], "errors": []}
        async with self._push_guard:
            list_items = await run_in_threadpool(self._local_list_delete_candidates)
            entries = await run_in_threadpool(self._pending_delete_entries)
            if not list_items and not entries:
                return {"enabled": True, "deleted": 0, "projects_deleted": 0,
                        "counts": {}, "warnings": [], "errors": []}
            _cookie, csrf = self.credentials.load()
            if not csrf:
                with self.database.connect() as connection:
                    connection.execute(
                        "UPDATE dida_sync_settings SET last_push_at = ?, "
                        "last_push_error_code = 'csrf_required', updated_at = ? WHERE id = 1",
                        (_iso_now(), _iso_now()),
                    )
                    connection.commit()
                self._push_backoff_until = datetime.now(timezone.utc) + timedelta(minutes=5)
                return {"enabled": True, "deleted": 0, "projects_deleted": 0,
                        "counts": {}, "warnings": [],
                        "errors": [{"code": "csrf_required",
                                    "message": "需要 _csrf_token 才能回写滴答"}]}
            counts = SyncCounts()
            warnings: Counter = Counter()
            errors: list[dict[str, str]] = []
            success_ids: set[str] = set()
            first_error = ""
            provider: DidaProvider | None = None
            try:
                provider = self.build_provider()
                if list_items:
                    first_error = await self._push_local_list_deletions(
                        provider, list_items, counts, warnings
                    ) or first_error
                    # 项目已整体删除：属于它的任务不必再逐条发送删除。
                    entries = await run_in_threadpool(self._pending_delete_entries)
                if entries:
                    result = await provider.delete_tasks([
                        {"taskId": entry["taskId"], "projectId": entry["projectId"]}
                        for entry in entries
                    ])
                    raw_errors = result.get("errors") if isinstance(result, dict) else {}
                    for entry in entries:
                        code = str((raw_errors or {}).get(entry["taskId"]) or "")
                        if code and code not in _DELETE_OK_CODES:
                            errors.append({"taskId": entry["taskId"], "code": code})
                        else:
                            success_ids.add(entry["taskId"])
            except DidaError as error:
                self._push_backoff_until = now + timedelta(minutes=5)
                with self.database.connect() as connection:
                    connection.execute(
                        "UPDATE dida_sync_settings SET last_push_at = ?, "
                        "last_push_error_code = ?, updated_at = ? WHERE id = 1",
                        (_iso_now(), error.code, _iso_now()),
                    )
                    connection.commit()
                return {"enabled": True, "deleted": 0,
                        "projects_deleted": counts.projects_deleted_pushed,
                        "counts": counts.as_dict(), "warnings": sorted(warnings),
                        "errors": [{"code": error.code, "message": error.message}]}
            finally:
                if provider is not None:
                    await provider.aclose()
            with self.database.connect() as connection:
                for dida_id in success_ids:
                    self._mark_link_status(connection, dida_id, "remote_deleted",
                                           "deleted_by_local")
                connection.execute(
                    "UPDATE dida_sync_settings SET last_push_at = ?, "
                    "last_push_error_code = ?, updated_at = ? WHERE id = 1",
                    (_iso_now(), errors[0]["code"] if errors else first_error,
                     _iso_now()),
                )
                connection.commit()
            self._push_backoff_until = (
                datetime.now(timezone.utc) + timedelta(minutes=5)
                if (errors or first_error) else None
            )
            if success_ids:
                self.database.audit("dida_sync_delete_pushed", {"count": len(success_ids)})
            return {"enabled": True, "deleted": len(success_ids),
                    "projects_deleted": counts.projects_deleted_pushed,
                    "counts": counts.as_dict(), "warnings": sorted(warnings),
                    "errors": errors}

    def _list_project_map(self) -> dict[str, str]:
        """本地清单名 → 滴答项目 ID（收集箱也通过 project_links 映射）。"""
        mapping: dict[str, str] = {}
        with self.database.connect() as connection:
            for row in connection.execute(
                "SELECT list_name, project_id FROM dida_project_links WHERE source = ?",
                (SOURCE,),
            ):
                name = str(row["list_name"] or "").strip()
                project_id = str(row["project_id"] or "").strip()
                if name and project_id:
                    mapping[name] = project_id
        state = tasks.read_tasks(self.vault_dir, read_only=self.read_only)
        for entry in state.get("lists", []):
            name = str(entry.get("name") or "").strip()
            project_id = str(entry.get("dida_project_id") or "").strip()
            if name and project_id:
                mapping[name] = project_id
        return mapping

    def _pending_project_id(self, name: str) -> str:
        return self._pending_projects.setdefault(name, uuid.uuid4().hex[:24])

    def _local_list_push_candidates(self) -> tuple[list[dict[str, Any]],
                                                   list[dict[str, Any]], Counter]:
        """扫描本地清单，找出需要在滴答新建/改名的项目；只读，不发请求。"""
        warnings: Counter = Counter()
        state = tasks.read_tasks(self.vault_dir, read_only=self.read_only)
        with self.database.connect() as connection:
            links = self._load_project_links(connection)
        creates: list[dict[str, Any]] = []
        renames: list[dict[str, Any]] = []
        for entry in state.get("lists", []):
            name = str(entry.get("name") or "").strip()
            if not name or entry.get("synthetic"):
                continue
            if name.casefold() == task_notes.INBOX.casefold():
                continue
            project_id = str(entry.get("dida_project_id") or "").strip()
            color = _valid_color(entry.get("color")) or "#3b82f6"
            if not project_id:
                creates.append({
                    "name": name,
                    "color": color,
                    "project_id": self._pending_project_id(name),
                })
                continue
            link = links.get(project_id)
            if link is None:
                warnings["project_link_missing"] += 1
                continue
            synced_name = str(link.get("synced_name") or "").strip()
            if synced_name and synced_name != name:
                renames.append({
                    "project_id": project_id,
                    "name": name,
                    "color": color,
                })
        return creates, renames, warnings

    def _local_push_candidates(self) -> tuple[list[dict[str, Any]],
                                              list[dict[str, Any]], Counter]:
        """扫描本地任务，找出需要回写的新建/修改；只读，不发请求。"""
        warnings: Counter = Counter()
        notes, scan_warnings = task_notes.scan_task_notes(self.vault_dir)
        for _warning in scan_warnings:
            warnings["scan_warning"] += 1
        with self.database.connect() as connection:
            links = self._load_links(connection)
        project_by_list = self._list_project_map()
        creates: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        for note in notes:
            list_name = str(note.get("list_name") or task_notes.INBOX)
            project_id = project_by_list.get(list_name, "")
            dida_id = str(note.get("dida_id") or "").strip()
            if not dida_id:
                if not project_id:
                    warnings["local_push_unmapped_list"] += 1
                    continue
                creates.append({
                    "note": note,
                    "project_id": project_id,
                    "task_id": uuid.uuid4().hex[:24],
                })
                continue
            link = links.get(dida_id)
            if link is None:
                continue
            if str(link.get("status") or "") != "synced":
                continue
            local_changed = (
                note["revision"] != str(link.get("last_local_revision") or "")
                or note["relpath"] != str(link.get("relpath") or "")
            )
            if not local_changed:
                continue
            if not project_id:
                project_id = str(link.get("project_id") or "")
            if not project_id:
                warnings["local_push_no_project"] += 1
                continue
            if not str(link.get("remote_payload_json") or ""):
                # 旧链接还没有远端原始对象：先让下一次拉取补全，避免回写时丢掉
                # 远端正文/清单项等未映射字段。
                warnings["push_needs_pull"] += 1
                continue
            item = {"note": note, "link": link, "project_id": project_id}
            old_project = str(link.get("project_id") or "")
            if project_id and old_project and project_id != old_project:
                item["move"] = {"taskId": dida_id, "fromProjectId": old_project,
                                "toProjectId": project_id}
            updates.append(item)
        return creates, updates, warnings

    def _apply_push_results(self, items: list[dict[str, Any]], result: dict[str, Any],
                            counts: SyncCounts, warnings: Counter,
                            target_tz: ZoneInfo, *, create: bool) -> str:
        etags = result.get("etags") if isinstance(result, dict) else {}
        errors = result.get("errors") if isinstance(result, dict) else {}
        first_error = ""
        with self.database.connect() as connection:
            for item in items:
                payload = item["payload"]
                task_id = str(payload.get("id") or "")
                code = str((errors or {}).get(task_id) or "")
                if code:
                    first_error = first_error or code
                    counts.errors += 1
                    warnings[f"push_error_{code}"] += 1
                    continue
                etag = str((etags or {}).get(task_id) or "")
                if etag:
                    payload["etag"] = etag
                note = item["note"]
                try:
                    if create:
                        values = {
                            "date": note.get("date", ""),
                            "time": note.get("time", ""),
                            "priority": note.get("priority", "none"),
                            "tags": note.get("tags", []),
                            "done": note.get("done", False),
                            "done_on": note.get("done_on", ""),
                            "pinned": note.get("pinned", False),
                            "created": note.get("created", ""),
                            "dida_id": task_id,
                        }
                        fresh = task_notes.rewrite_task_note(
                            self.vault_dir, note, folder=note.get("folder", ""),
                            title=note.get("title", ""), values=values,
                        )
                        mapped = map_task(task_from_payload(payload), target_tz=target_tz,
                                          list_name=note.get("list_name", task_notes.INBOX))
                        self._save_link_synced(connection, mapped, fresh, payload)
                        counts.created_pushed += 1
                    else:
                        mapped = map_task(task_from_payload(payload), target_tz=target_tz,
                                          list_name=note.get("list_name", task_notes.INBOX))
                        self._save_link_synced(connection, mapped, note, payload)
                        counts.updated_pushed += 1
                except Exception as error:  # 单条失败不中断其余回写
                    counts.errors += 1
                    warnings[f"push_apply_error_{type(error).__name__}"] += 1
                    continue
            connection.commit()
        return first_error

    async def _push_local_list_creates(self, provider: DidaProvider,
                                       items: list[dict[str, Any]],
                                       counts: SyncCounts,
                                       warnings: Counter) -> str:
        payloads = [{
            "id": item["project_id"],
            "name": item["name"],
            "color": item["color"],
            "kind": "TASK",
            "viewMode": "list",
        } for item in items]
        try:
            result = await provider.add_projects(payloads)
        except DidaError as error:
            counts.errors += len(items)
            warnings[f"project_push_error_{error.code}"] += len(items)
            return error.code
        etags = result.get("etags") if isinstance(result, dict) else {}
        errors = result.get("errors") if isinstance(result, dict) else {}
        first_error = ""
        for item in items:
            project_id = item["project_id"]
            code = str((errors or {}).get(project_id) or "")
            if code:
                counts.errors += 1
                warnings[f"project_push_error_{code}"] += 1
                first_error = first_error or code
                continue
            try:
                attached = await run_in_threadpool(
                    tasks.attach_list_dida_project,
                    self.vault_dir, item["name"], project_id, self.database,
                )
            except (HTTPException, VaultReadOnlyError, OSError):
                attached = False
            if not attached:
                # 扫描与落库之间清单被改名/删除：远端项目成了孤儿，尽力清理。
                warnings["project_create_list_gone"] += 1
                try:
                    cleanup = await provider.delete_projects([project_id])
                    cleanup_errors = (cleanup.get("errors")
                                      if isinstance(cleanup, dict) else {})
                    cleanup_code = str((cleanup_errors or {}).get(project_id) or "")
                    if cleanup_code and cleanup_code not in _DELETE_OK_CODES:
                        warnings["project_create_cleanup_failed"] += 1
                    else:
                        self._pending_projects.pop(item["name"], None)
                except Exception:
                    warnings["project_create_cleanup_failed"] += 1
                continue
            self._pending_projects.pop(item["name"], None)
            with self.database.connect() as connection:
                self._upsert_local_project_link(
                    connection, project_id, item["name"], item["color"]
                )
            counts.projects_created_pushed += 1
            if not str((etags or {}).get(project_id) or ""):
                warnings["project_create_unconfirmed"] += 1
        return first_error

    async def _push_local_list_renames(self, provider: DidaProvider,
                                       items: list[dict[str, Any]],
                                       counts: SyncCounts,
                                       warnings: Counter) -> str:
        try:
            result = await provider.update_projects([{
                "id": item["project_id"],
                "name": item["name"],
                "color": item["color"],
            } for item in items])
        except DidaError as error:
            counts.errors += len(items)
            warnings[f"project_push_error_{error.code}"] += len(items)
            return error.code
        etags = result.get("etags") if isinstance(result, dict) else {}
        errors = result.get("errors") if isinstance(result, dict) else {}
        first_error = ""
        now = _iso_now()
        with self.database.connect() as connection:
            for item in items:
                project_id = item["project_id"]
                code = str((errors or {}).get(project_id) or "")
                if code:
                    counts.errors += 1
                    warnings[f"project_push_error_{code}"] += 1
                    first_error = first_error or code
                    continue
                counts.projects_renamed_pushed += 1
                if not str((etags or {}).get(project_id) or ""):
                    warnings["project_rename_unconfirmed"] += 1
                connection.execute(
                    """UPDATE dida_project_links
                       SET synced_name = ?, list_name = ?, color = ?, last_seen_at = ?
                       WHERE source = ? AND project_id = ?""",
                    (item["name"], item["name"], item["color"], now,
                     SOURCE, project_id),
                )
            connection.commit()
        return first_error

    async def push_local_changes(self, *, force: bool = False) -> dict[str, Any]:
        """把本地新建/修改的任务和清单写回滴答（需要 CSRF）。"""
        if not self.supported or self.read_only:
            return {"enabled": False, "counts": {}, "warnings": []}
        row = self._row()
        if not row.get("push_changes"):
            return {"enabled": False, "counts": {}, "warnings": []}
        cookie, csrf = self.credentials.load()
        if not cookie:
            return {"enabled": False, "counts": {}, "warnings": []}
        if not csrf:
            return {"enabled": True, "counts": {}, "warnings": ["push_changes_csrf_required"],
                    "errors": [{"code": "csrf_required",
                                "message": "需要 _csrf_token 才能回写滴答"}]}
        if self._push_guard.locked():
            return {"enabled": True, "counts": {}, "warnings": [], "skipped": "running"}
        now = datetime.now(timezone.utc)
        if (not force and self._local_push_backoff_until
                and now < self._local_push_backoff_until):
            return {"enabled": True, "counts": {}, "warnings": [], "skipped": "backoff"}
        async with self._push_guard:
            list_creates, list_renames, warnings = await run_in_threadpool(
                self._local_list_push_candidates
            )
            task_creates: list[dict[str, Any]] = []
            task_updates: list[dict[str, Any]] = []
            if not list_creates and not list_renames:
                task_creates, task_updates, task_warnings = await run_in_threadpool(
                    self._local_push_candidates
                )
                warnings.update(task_warnings)
            if (not list_creates and not list_renames
                    and not task_creates and not task_updates):
                return {"enabled": True, "counts": {}, "warnings": sorted(warnings)}
            target_tz = _zone(str(row.get("timezone") or "Asia/Shanghai"))
            counts = SyncCounts()
            first_error = ""
            creates, updates = task_creates, task_updates
            provider: DidaProvider | None = None
            try:
                provider = self.build_provider()
                if list_creates:
                    first_error = await self._push_local_list_creates(
                        provider, list_creates, counts, warnings
                    ) or first_error
                if list_renames:
                    first_error = await self._push_local_list_renames(
                        provider, list_renames, counts, warnings
                    ) or first_error
                if list_creates:
                    # 新清单的 dida_project_id 已落盘：同一轮把其中的任务也推上去。
                    task_creates, task_updates, task_warnings = await run_in_threadpool(
                        self._local_push_candidates
                    )
                    warnings.update(task_warnings)
                creates, updates = task_creates, task_updates
                if creates:
                    payloads = []
                    for item in creates:
                        item["payload"] = build_remote_task_payload(
                            item["note"], task_id=item["task_id"],
                            project_id=item["project_id"], target_tz=target_tz,
                            body=task_notes.note_body(item["note"]),
                        )
                        payloads.append(item["payload"])
                    result = await provider.add_tasks(payloads)
                    first_error = self._apply_push_results(
                        creates, result, counts, warnings, target_tz, create=True
                    ) or first_error
                move_items = [item for item in updates if item.get("move")]
                if move_items:
                    move_result = await provider.move_tasks(
                        [item["move"] for item in move_items]
                    )
                    move_errors = move_result.get("errors") if isinstance(move_result, dict) else {}
                    failed_moves: set[str] = set()
                    for item in move_items:
                        code = str((move_errors or {}).get(item["move"]["taskId"]) or "")
                        if code:
                            failed_moves.add(item["move"]["taskId"])
                            counts.errors += 1
                            warnings[f"push_move_error_{code}"] += 1
                    if failed_moves:
                        first_error = first_error or "move_error"
                        updates = [item for item in updates
                                   if item.get("move", {}).get("taskId") not in failed_moves]
                    counts.moved_pushed += len(move_items) - len(failed_moves)
                if updates:
                    payloads = []
                    for item in updates:
                        base: dict[str, Any] = {}
                        raw = str(item["link"].get("remote_payload_json") or "")
                        if raw:
                            try:
                                loaded = json.loads(raw)
                                if isinstance(loaded, dict):
                                    base = loaded
                            except ValueError:
                                base = {}
                        if not base:
                            base = {"etag": str(item["link"].get("last_remote_etag") or "")}
                        payload = build_remote_task_payload(
                            item["note"], task_id=str(item["note"].get("dida_id") or ""),
                            project_id=item["project_id"], target_tz=target_tz,
                            base=base,
                        )
                        etag = str(item["link"].get("last_remote_etag") or "")
                        if etag:
                            payload["etag"] = etag
                        item["payload"] = payload
                        payloads.append(payload)
                    result = await provider.update_tasks(payloads)
                    first_error = self._apply_push_results(
                        updates, result, counts, warnings, target_tz, create=False
                    ) or first_error
            except DidaError as error:
                first_error = first_error or error.code
                counts.errors += (len(creates) + len(updates)
                                  + len(list_creates) + len(list_renames))
                warnings[f"push_error_{error.code}"] += 1
            except Exception as error:  # 回写失败不应拖垮整次拉取
                first_error = first_error or type(error).__name__
                counts.errors += (len(creates) + len(updates)
                                  + len(list_creates) + len(list_renames))
                warnings[f"push_error_{type(error).__name__}"] += 1
            finally:
                if provider is not None:
                    await provider.aclose()
            self._local_push_backoff_until = (
                datetime.now(timezone.utc) + timedelta(minutes=5)
                if first_error else None
            )
            with self.database.connect() as connection:
                connection.execute(
                    """UPDATE dida_sync_settings SET last_push_at = ?,
                       last_push_error_code = ?, updated_at = ? WHERE id = 1""",
                    (_iso_now(), first_error, _iso_now()),
                )
                connection.commit()
            self.database.audit("dida_local_push_completed", {
                "counts": counts.as_dict(), "error": first_error,
            })
            return {"enabled": True, "counts": counts.as_dict(),
                    "warnings": sorted(warnings), "error": first_error}

    async def restore_tasks(self, external_ids: list[str]) -> dict[str, Any]:
        """本地恢复任务后，把滴答回收站里对应的任务也恢复。"""
        if not self.supported:
            raise DidaError("unsupported", "当前运行环境不支持滴答同步")
        if self.read_only:
            raise VaultReadOnlyError("当前是 Obsidian 只读模式，已拒绝恢复滴答任务")
        wanted = [str(item).strip() for item in external_ids if str(item or "").strip()]
        if not wanted:
            return {"ok": True, "restored": 0, "errors": [], "skipped": []}
        with self.database.connect() as connection:
            links = self._load_links(connection)
        entries: list[dict[str, str]] = []
        skipped: list[str] = []
        for external_id in wanted:
            link = links.get(external_id)
            # 只恢复“已经回写删除、正在滴答回收站里”的任务；普通任务无需远端恢复。
            if link is None or str(link.get("status") or "") != "remote_deleted":
                skipped.append(external_id)
                continue
            project_id = str(link.get("project_id") or "")
            if not project_id:
                skipped.append(external_id)
                continue
            entries.append({"taskId": external_id, "projectId": project_id})
        if not entries:
            return {"ok": True, "restored": 0, "errors": [], "skipped": skipped}
        provider: DidaProvider | None = None
        try:
            provider = self.build_provider()
            result = await provider.restore_tasks(entries)
        except DidaError as error:
            return {
                "ok": False, "restored": 0, "skipped": skipped,
                "errors": [{"taskId": entry["taskId"], "code": error.code,
                            "message": error.message} for entry in entries],
            }
        finally:
            if provider is not None:
                await provider.aclose()
        raw_errors = result.get("errors") if isinstance(result, dict) else {}
        errors: list[dict[str, str]] = []
        success_ids: set[str] = set()
        for entry in entries:
            code = str((raw_errors or {}).get(entry["taskId"]) or "")
            if code and code not in {"NOT_EXISTED", "DELETED", "NOT_EXIST", "NOT_FOUND"}:
                errors.append({"taskId": entry["taskId"], "code": code})
            else:
                success_ids.add(entry["taskId"])
        if success_ids:
            with self.database.connect() as connection:
                for dida_id in success_ids:
                    self._mark_link_status(connection, dida_id, "synced", "")
                connection.commit()
            self.database.audit("dida_sync_delete_restored", {"count": len(success_ids)})
        return {"ok": not errors, "restored": len(success_ids),
                "errors": errors, "skipped": skipped}

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
               (source, project_id, list_name, color, closed, synced_name,
                remote_seen, last_seen_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(source, project_id) DO UPDATE SET
                 list_name = excluded.list_name, color = excluded.color,
                 closed = excluded.closed, last_seen_at = excluded.last_seen_at,
                 remote_seen = 1""",
            (SOURCE, project.id, list_name, _valid_color(project.color),
             1 if project.closed else 0, list_name, now, now),
        )
        connection.commit()

    def _upsert_local_project_link(self, connection, project_id: str,
                                   list_name: str, color: str) -> None:
        """本地新建清单成功后建链：synced_name 已对齐，等待快照确认 remote_seen。"""
        now = _iso_now()
        connection.execute(
            """INSERT INTO dida_project_links
               (source, project_id, list_name, color, closed, synced_name,
                remote_seen, last_seen_at, created_at)
               VALUES (?, ?, ?, ?, 0, ?, 0, ?, ?)
               ON CONFLICT(source, project_id) DO UPDATE SET
                 list_name = excluded.list_name, color = excluded.color,
                 synced_name = excluded.synced_name, remote_seen = 0,
                 last_seen_at = excluded.last_seen_at""",
            (SOURCE, project_id, list_name, _valid_color(color),
             list_name, now, now),
        )
        connection.commit()

    def _mark_projects_seen(self, connection,
                            project_links: dict[str, dict[str, Any]],
                            remote_ids: set[str]) -> None:
        """完整快照里出现的本地新建项目：解除旧快照保护。"""
        now = _iso_now()
        changed = False
        for project_id, link in project_links.items():
            if project_id not in remote_ids:
                continue
            if int(link.get("remote_seen") or 0) != 0:
                continue
            connection.execute(
                """UPDATE dida_project_links
                   SET remote_seen = 1, last_seen_at = ?
                   WHERE source = ? AND project_id = ?""",
                (now, SOURCE, project_id),
            )
            link["remote_seen"] = 1
            changed = True
        if changed:
            connection.commit()

    def _delete_project_link(self, connection, project_id: str) -> None:
        connection.execute(
            "DELETE FROM dida_project_links WHERE source = ? AND project_id = ?",
            (SOURCE, project_id),
        )
        connection.commit()

    def _save_link_synced(self, connection, mapped: MappedTask, note: dict[str, Any],
                          remote_payload: dict[str, Any] | None = None) -> None:
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
        payload = remote_payload if remote_payload is not None else mapped.remote_raw
        if payload is not None:
            connection.execute(
                """UPDATE dida_task_links SET remote_payload_json = ?,
                   pending_remote_payload_json = ''
                   WHERE source = ? AND external_id = ?""",
                (json.dumps(payload, ensure_ascii=False), SOURCE, mapped.dida_id),
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
        if mapped.remote_raw is not None:
            connection.execute(
                """UPDATE dida_task_links SET pending_remote_payload_json = ?
                   WHERE source = ? AND external_id = ?""",
                (json.dumps(mapped.remote_raw, ensure_ascii=False), SOURCE, mapped.dida_id),
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

    def _apply_remote_project_deletions(self, connection,
                                        snapshot: RemoteSnapshot,
                                        projects: dict[str, RemoteProject],
                                        project_links: dict[str, dict[str, Any]],
                                        task_links: dict[str, dict[str, Any]],
                                        counts: SyncCounts, warnings: Counter,
                                        dry_run: bool) -> None:
        """滴答删除了清单：本地清单连同任务移入回收站。

        只有确认本次返回包含完整项目列表（projects_complete）时才做差集判断，
        避免把增量响应里缺失的 projectProfiles 误当成远端删除。
        """
        # 空的项目列表既可能是“真的删光了”，也可能是异常响应；宁可不下手，
        # 也要避免一次坏响应把所有本地清单移进回收站。
        if not snapshot.projects_complete or not snapshot.projects:
            return
        remote_ids = set(projects)
        if snapshot.inbox_id:
            remote_ids.add(snapshot.inbox_id)
        if not dry_run:
            # 本次快照包含这些本地新建项目：解除“等待远端确认”的保护。
            self._mark_projects_seen(connection, project_links, remote_ids)
        protected_ids = {
            project_id for project_id, link in project_links.items()
            if int(link.get("remote_seen") or 0) == 0
        }
        current = tasks.read_tasks(self.vault_dir, read_only=self.read_only)
        candidates: dict[str, str] = {}
        for entry in current.get("lists", []):
            project_id = str(entry.get("dida_project_id") or "").strip()
            name = str(entry.get("name") or "").strip()
            if project_id and name:
                candidates[project_id] = name
        for project_id, link in project_links.items():
            name = str(link.get("list_name") or "").strip()
            if project_id and name and project_id not in candidates:
                candidates[project_id] = name
        # 清单已经不在了：它的所有任务都随清单消失，包括本地已经缺失的任务链接，
        # 不能让它们继续留在“本地缺失”里等待回写。
        if not dry_run:
            for external_id, link in task_links.items():
                project_id = str(link.get("project_id") or "")
                if not project_id or project_id in remote_ids:
                    continue
                if project_id in protected_ids:
                    continue
                if str(link.get("status") or "") == "remote_deleted":
                    continue
                self._mark_link_status(connection, external_id, "remote_deleted",
                                       "remote_deleted")
        for project_id in sorted(candidates):
            if project_id in remote_ids:
                continue
            if project_id in protected_ids:
                # 本地刚建、远端快照还没看到：先别当作远端删除。
                warnings["project_seen_pending"] += 1
                continue
            name = candidates[project_id]
            if dry_run:
                counts.projects_deleted += 1
                continue
            try:
                latest = tasks.read_tasks(self.vault_dir, read_only=False)
                entry = next((item for item in latest.get("lists", [])
                              if str(item.get("name") or "") == name), None)
                if entry is not None and not entry.get("synthetic"):
                    result = tasks.delete_list(
                        self.vault_dir,
                        tasks.NamedPayload(name=name, revision=latest["revision"]),
                        self.database, read_only=False,
                    )
                    removed = int(result.get("tasks") or 0)
                else:
                    result = task_notes.trash_list_folder(self.vault_dir, name)
                    removed = int(result.get("count") or 0)
                if entry is not None:
                    counts.projects_deleted += 1
                counts.remote_deleted += removed
                # 这些任务是被远端清单删除带走的（包括本地已经缺失的）：
                # 标记为已远端删除，避免下一次“本地删除回写”再发一次删除请求。
                for external_id, link in task_links.items():
                    if str(link.get("project_id") or "") != project_id:
                        continue
                    if str(link.get("status") or "") == "remote_deleted":
                        continue
                    self._mark_link_status(connection, external_id, "remote_deleted",
                                           "remote_deleted")
                self._delete_project_link(connection, project_id)
            except Exception as error:  # 单个清单失败不中断整次同步
                counts.errors += 1
                warnings[f"project_delete_error_{type(error).__name__}"] += 1

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
                    # 本地刚建、完整快照里暂时缺席的项目：沿用本地清单，别把任务
                    # 挪进收集箱（等下次快照确认后再恢复正常处理）。
                    local_name = next(
                        (name for name, entry in lists_by_name.items()
                         if str(entry.get("dida_project_id") or "") == project_id),
                        None,
                    )
                    link = project_links.get(project_id)
                    if (local_name and link is not None
                            and int(link.get("remote_seen") or 0) == 0):
                        project_to_list[project_id] = local_name
                        continue
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
                            if str(link.get("status") or "") == "remote_deleted":
                                # 任务重新出现在滴答：用户从滴答回收站恢复了它。
                                if dry_run:
                                    counts.restored += 1
                                    continue
                                folder = "" if list_name == task_notes.INBOX else list_name
                                restored = task_trash.restore_by_dida_id(self.vault_dir, task.id)
                                if restored is not None:
                                    if restored.get("note") is None:
                                        counts.errors += 1
                                        warnings["restored_note_unreadable"] += 1
                                        continue
                                    fresh = task_notes.rewrite_task_note(
                                        self.vault_dir, restored["note"], folder=folder,
                                        title=mapped.title, values=mapped.values,
                                        body=mapped.body,
                                    )
                                else:
                                    # 本地回收站里没有副本（可能已彻底删除）：按远端重建。
                                    fresh = task_notes.create_task_note(
                                        self.vault_dir, folder, mapped.title, mapped.values,
                                        body=mapped.body,
                                    )
                                notes_by_dida[task.id] = fresh
                                self._save_link_synced(connection, mapped, fresh)
                                links[task.id] = {"external_id": task.id}
                                counts.restored += 1
                                if mapped.values["done"]:
                                    counts.completed += 1
                                continue
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

                    if link is not None and str(link.get("status") or "") == "remote_deleted":
                        # 本地已恢复（或本来就在）且远端任务也回来了：重建同步基线。
                        if not dry_run:
                            self._save_link_synced(connection, mapped, note)
                            links[task.id] = {"external_id": task.id}
                        counts.restored += 1
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

                    # 旧版全日任务日期差一天：即使本地还有其它未回写修改（比如勾选完成），
                    # 也只修日期、保留其余本地字段，随后照常走本地改动回写。
                    legacy_date = legacy_all_day_date(task)
                    if (not dry_run and link is not None
                            and str(link.get("status") or "") in {"synced", "local_missing"}
                            and legacy_date and note["date"] == legacy_date
                            and mapped.values["date"] != legacy_date):
                        values = {
                            "date": mapped.values["date"],
                            "time": note.get("time", ""),
                            "priority": note.get("priority", "none"),
                            "tags": note.get("tags", []),
                            "done": note.get("done", False),
                            "done_on": note.get("done_on", ""),
                            "pinned": note.get("pinned", False),
                            "created": note.get("created", ""),
                            "dida_id": note.get("dida_id", ""),
                        }
                        fresh = task_notes.rewrite_task_note(
                            self.vault_dir, note, folder=note.get("folder", ""),
                            title=note.get("title", ""), values=values,
                        )
                        notes_by_dida[task.id] = fresh
                        note = fresh
                    local_changed = (
                        note["revision"] != str(link.get("last_local_revision") or "")
                        or note["relpath"] != str(link.get("relpath") or "")
                    )
                    remote_changed = mapped.remote_hash != str(link.get("last_remote_hash") or "")
                    material_differs = not fields_match or (content_changed and not body_matches)

                    if not remote_changed and not local_changed:
                        # 两边都没动：即使字段有差异（例如用户点了“保留本地”），也不覆盖。
                        # 旧链接还没有远端原始对象时顺手补上，回写更新时可以保留未映射字段。
                        if (not dry_run and mapped.remote_raw is not None
                                and not str(link.get("remote_payload_json") or "")):
                            self._save_link_synced(connection, mapped, note)
                        # 例外：旧版全日任务把 UTC 日期直接截断，会差一天；如果本地日期
                        # 正好是那个旧值，说明是映射 bug 而不是用户编辑，顺手修正。
                        legacy_date = legacy_all_day_date(task)
                        if (str(link.get("status") or "") == "synced"
                                and legacy_date and note["date"] == legacy_date
                                and mapped.values["date"] != legacy_date):
                            if not dry_run:
                                folder = "" if list_name == task_notes.INBOX else list_name
                                fresh = task_notes.rewrite_task_note(
                                    self.vault_dir, note, folder=folder,
                                    title=mapped.title, values=mapped.values,
                                )
                                notes_by_dida[task.id] = fresh
                                self._save_link_synced(connection, mapped, fresh)
                                links[task.id] = {"external_id": task.id}
                            counts.updated += 1
                            continue
                        # 如果之前是本地缺失、现在笔记又回来了，说明用户从本地回收站恢复，
                        # 需要把链接从 local_missing 改回 synced。
                        if str(link.get("status") or "") == "local_missing":
                            if not dry_run:
                                self._save_link_synced(connection, mapped, note)
                                links[task.id] = {"external_id": task.id}
                            counts.restored += 1
                        else:
                            counts.unchanged += 1
                        continue
                    if not remote_changed and local_changed:
                        # 旧链接缺远端原始对象时先补上（不推进本地基线），
                        # 这样后台回写才能保留正文/清单项等未映射字段。
                        if (not dry_run and mapped.remote_raw is not None
                                and not str(link.get("remote_payload_json") or "")):
                            connection.execute(
                                """UPDATE dida_task_links SET remote_payload_json = ?
                                   WHERE source = ? AND external_id = ?""",
                                (json.dumps(mapped.remote_raw, ensure_ascii=False),
                                 SOURCE, task.id),
                            )
                            connection.commit()
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
                # 先释放本连接上的读事务，随后删除清单会另开连接写配置/审计。
                connection.commit()
            self._apply_remote_project_deletions(
                connection, snapshot, projects, project_links, links,
                counts, warnings, dry_run,
            )
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
                   remote_payload_json = CASE
                     WHEN pending_remote_payload_json != '' THEN pending_remote_payload_json
                     ELSE remote_payload_json END,
                   pending_remote_title = '', pending_remote_hash = '',
                   pending_remote_content_hash = '', pending_remote_etag = '',
                   pending_remote_modified = '', pending_remote_payload_json = '',
                   last_synced_at = ?, updated_at = ?
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
                # 先回写本地新建/修改，再回写本地删除；两者都基于已合并的远端快照。
                local_push = await service.push_local_changes(force=True)
                for key, value in (local_push.get("counts") or {}).items():
                    summary["counts"][key] = int(summary["counts"].get(key, 0)) + int(value or 0)
                for warning in local_push.get("warnings") or []:
                    summary["warnings"] = sorted(set([*summary.get("warnings", []), warning]))
                    warning_counts = summary.setdefault("warning_counts", {})
                    warning_counts[warning] = int(warning_counts.get(warning, 0)) + 1
                push = await service.push_deletions(force=True)
                summary["counts"]["deleted_pushed"] = int(push.get("deleted") or 0)
                for key, value in (push.get("counts") or {}).items():
                    summary["counts"][key] = (
                        int(summary["counts"].get(key, 0)) + int(value or 0)
                    )
                for warning in push.get("warnings") or []:
                    summary["warnings"] = sorted(set([*summary.get("warnings", []), warning]))
                    warning_counts = summary.setdefault("warning_counts", {})
                    warning_counts[warning] = int(warning_counts.get(warning, 0)) + 1
                if push.get("errors"):
                    summary["warnings"] = sorted(set([*summary.get("warnings", []), "push_delete_error"]))
                    summary.setdefault("warning_counts", {})["push_delete_error"] = len(push["errors"])
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
    """每 15 秒检查本地回写/删除；拉取间隔仍按设置里的同步间隔计算。"""
    while True:
        try:
            if not service._running:
                await service.push_local_changes()
                await service.push_deletions()
            if service.due():
                await run_dida_sync(service, data_lock, trigger="scheduled")
        except asyncio.CancelledError:
            raise
        except Exception:
            # run_dida_sync 已记录 last_error；循环不重复刷审计。
            pass
        await asyncio.sleep(15)


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
            "csrf_required": 409,
            "write_forbidden": 409,
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

    @router.post("/restore")
    async def restore(payload: DidaRestoreRequest):
        try:
            return await service.restore_tasks(payload.external_ids)
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
    "CredentialStore", "DidaRestoreRequest", "DidaSyncService", "DidaSyncSettingsUpdate",
    "DidaRunRequest", "create_dida_sync_router", "dida_sync_loop", "run_dida_sync",
]
