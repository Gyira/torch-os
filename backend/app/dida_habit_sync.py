"""习惯 ↔ 滴答清单双向同步引擎。

设计要点：

- Markdown 是习惯与打卡的权威源；本引擎通过 ``HabitService`` 读写。
- 定义冲突用「映射字段快照」判定，不用 revision：本地改备注/图标/分组
  不应产生冲突或写入滴答。
- 删除/归档是保守的：滴答删除习惯只归档本地；滴答列表为空时不动本地；
  本地整月日志丢失时用批量删除保护拦住远端打卡删除。
- 新建远端对象先落 ``pending_create`` / ``pending_add`` 链接行，网络超时
  后重试复用同一个客户端 ID，避免重复对象。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import secrets
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from . import habit_markdown
from . import habits as habits_module
from .dida_client import DidaError, RemoteHabit, RemoteHabitCheckin, habit_from_payload
from .dida_habit_mapping import (
    ABSENT_CHECKIN_HASH, MATERIAL_KEYS, LOCAL_CHECKIN_STATUS, canonical_title,
    build_remote_checkin_payload, build_remote_habit_payload,
    first_link_matches, local_habit_material, local_material_json,
    map_remote_habit, material_diff, material_from_json, material_hash,
    parse_checkin_stamp, remote_checkin_hash,
)
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

if TYPE_CHECKING:  # pragma: no cover - 仅类型提示
    from .dida_sync import DidaSyncService

SOURCE = "dida_web"
MAX_CHECKIN_DELETES = 20
MASS_DELETE_RATIO = 0.5
BATCH_SIZE = 200
CHECKIN_HISTORY_STAMP = 19700101

HABIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS dida_habit_links (
    source TEXT NOT NULL DEFAULT 'dida_web',
    external_id TEXT NOT NULL,
    uid TEXT NOT NULL DEFAULT '',
    relpath TEXT NOT NULL DEFAULT '',
    last_title TEXT NOT NULL DEFAULT '',
    last_local_revision INTEGER NOT NULL DEFAULT 0,
    last_local_mapped_json TEXT NOT NULL DEFAULT '',
    last_remote_hash TEXT NOT NULL DEFAULT '',
    last_remote_material_json TEXT NOT NULL DEFAULT '',
    last_remote_status INTEGER,
    last_remote_etag TEXT NOT NULL DEFAULT '',
    last_remote_modified TEXT NOT NULL DEFAULT '',
    remote_payload_json TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'synced'
        CHECK(status IN ('synced','pending_create','conflict','local_missing','remote_deleted')),
    conflict_reason TEXT NOT NULL DEFAULT '',
    conflict_at TEXT,
    pending_remote_title TEXT NOT NULL DEFAULT '',
    pending_remote_payload_json TEXT NOT NULL DEFAULT '',
    pending_remote_hash TEXT NOT NULL DEFAULT '',
    pending_remote_material_json TEXT NOT NULL DEFAULT '',
    pending_remote_etag TEXT NOT NULL DEFAULT '',
    pending_remote_modified TEXT NOT NULL DEFAULT '',
    pending_checkins_json TEXT NOT NULL DEFAULT '{}',
    local_archived_by_sync INTEGER NOT NULL DEFAULT 0
        CHECK(local_archived_by_sync IN (0,1)),
    last_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_dida_habit_links_status ON dida_habit_links(status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_dida_habit_links_uid
    ON dida_habit_links(source, uid) WHERE uid <> '';

CREATE TABLE IF NOT EXISTS dida_habit_checkin_links (
    source TEXT NOT NULL DEFAULT 'dida_web',
    external_id TEXT NOT NULL,
    date TEXT NOT NULL,
    remote_checkin_id TEXT NOT NULL DEFAULT '',
    last_local_revision INTEGER NOT NULL DEFAULT 0,
    last_local_status TEXT NOT NULL DEFAULT '',
    last_remote_hash TEXT NOT NULL DEFAULT '',
    last_remote_status INTEGER,
    remote_payload_json TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'synced'
        CHECK(status IN ('synced','pending_add','conflict')),
    conflict_reason TEXT NOT NULL DEFAULT '',
    conflict_at TEXT,
    pending_remote_checkin_id TEXT NOT NULL DEFAULT '',
    pending_remote_payload_json TEXT NOT NULL DEFAULT '',
    pending_remote_hash TEXT NOT NULL DEFAULT '',
    pending_remote_status INTEGER,
    last_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(source, external_id, date)
);
CREATE INDEX IF NOT EXISTS idx_dida_habit_checkin_links_status
    ON dida_habit_checkin_links(status);
CREATE INDEX IF NOT EXISTS idx_dida_habit_checkin_links_date
    ON dida_habit_checkin_links(date);
"""

LINK_COLUMNS = (
    "source", "external_id", "uid", "relpath", "last_title",
    "last_local_revision", "last_local_mapped_json", "last_remote_hash",
    "last_remote_material_json", "last_remote_status", "last_remote_etag",
    "last_remote_modified", "remote_payload_json", "status", "conflict_reason",
    "conflict_at", "pending_remote_title", "pending_remote_payload_json",
    "pending_remote_hash", "pending_remote_material_json", "pending_remote_etag",
    "pending_remote_modified", "pending_checkins_json", "local_archived_by_sync",
    "last_synced_at", "created_at", "updated_at",
)

CHECKIN_COLUMNS = (
    "source", "external_id", "date", "remote_checkin_id", "last_local_revision",
    "last_local_status", "last_remote_hash", "last_remote_status",
    "remote_payload_json", "status", "conflict_reason", "conflict_at",
    "pending_remote_checkin_id", "pending_remote_payload_json",
    "pending_remote_hash", "pending_remote_status", "last_synced_at",
    "created_at", "updated_at",
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(text: Any, default: Any) -> Any:
    if isinstance(text, (dict, list)):
        return text
    raw = str(text or "").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def _new_external_id() -> str:
    return secrets.token_hex(12)


def _disabled(reason: str = "disabled") -> dict[str, Any]:
    return {"enabled": False, "counts": {}, "warnings": [], "errors": [],
            "skipped": reason}


class DidaHabitSync:
    def __init__(self, service: "DidaSyncService"):
        self.service = service
        self.database = service.database
        self.vault_dir = Path(service.vault_dir)
        self.read_only = bool(service.read_only)
        self.supported = bool(service.supported)
        self._change_guard = asyncio.Lock()
        self._delete_guard = asyncio.Lock()
        self._change_backoff_until: datetime | None = None
        self._delete_backoff_until: datetime | None = None

    # ---------- 初始化 / 状态 ----------

    def initialize(self) -> None:
        with self.database.connect() as connection:
            connection.executescript(HABIT_SCHEMA)
            connection.commit()

    def enabled(self) -> bool:
        if not self.supported:
            return False
        try:
            return bool(self.service._row().get("habits_enabled", 1))
        except Exception:
            return False

    def _habit_service(self) -> habits_module.HabitService:
        return habits_module.HabitService(
            self.database, self.vault_dir, read_only=self.read_only)

    def link_counts(self) -> dict[str, int]:
        counts = {"links": 0, "synced": 0, "pending_create": 0, "conflict": 0,
                  "local_missing": 0, "remote_deleted": 0,
                  "checkin_links": 0, "checkin_conflicts": 0, "checkin_pending": 0}
        with self.database.connect() as connection:
            for row in connection.execute(
                    "SELECT status, COUNT(*) AS total FROM dida_habit_links "
                    "WHERE source = ? GROUP BY status", (SOURCE,)):
                status = str(row["status"] or "")
                total = int(row["total"] or 0)
                counts["links"] += total
                if status in counts:
                    counts[status] = total
            for row in connection.execute(
                    "SELECT status, COUNT(*) AS total FROM dida_habit_checkin_links "
                    "WHERE source = ? GROUP BY status", (SOURCE,)):
                status = str(row["status"] or "")
                total = int(row["total"] or 0)
                counts["checkin_links"] += total
                if status == "conflict":
                    counts["checkin_conflicts"] = total
                elif status == "pending_add":
                    counts["checkin_pending"] = total
        return counts

    def link_map(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT external_id, uid, last_title, relpath, status,
                          conflict_reason, last_synced_at
                   FROM dida_habit_links WHERE source = ? ORDER BY last_title""",
                (SOURCE,),
            ).fetchall()
        result = [dict(row) for row in rows]
        return [row for row in result if str(row.get("uid") or "")]

    def conflicts(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 200))
        result: list[dict[str, Any]] = []
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM dida_habit_links
                   WHERE source = ? AND status = 'conflict'
                   ORDER BY conflict_at DESC, external_id ASC LIMIT ?""",
                (SOURCE, limit),
            ).fetchall()
            for row in rows:
                link = dict(row)
                pending = _load_json(link.get("pending_checkins_json"), {})
                result.append({
                    "kind": "habit",
                    "external_id": link["external_id"],
                    "uid": link.get("uid") or "",
                    "last_title": link.get("last_title") or "",
                    "pending_remote_title": link.get("pending_remote_title") or "",
                    "relpath": link.get("relpath") or "",
                    "conflict_reason": link.get("conflict_reason") or "",
                    "conflict_at": link.get("conflict_at"),
                    "pending_dates": sorted(pending) if isinstance(pending, dict) else [],
                })
            checkin_rows = connection.execute(
                """SELECT c.*, h.last_title
                   FROM dida_habit_checkin_links c
                   LEFT JOIN dida_habit_links h
                     ON h.source = c.source AND h.external_id = c.external_id
                   WHERE c.source = ? AND c.status = 'conflict'
                   ORDER BY c.conflict_at DESC, c.date ASC LIMIT ?""",
                (SOURCE, limit),
            ).fetchall()
            for row in checkin_rows:
                item = dict(row)
                result.append({
                    "kind": "habit_checkin",
                    "external_id": item["external_id"],
                    "date": item.get("date") or "",
                    "last_title": item.get("last_title") or "",
                    "pending_remote_title": item.get("last_title") or "",
                    "conflict_reason": item.get("conflict_reason") or "",
                    "conflict_at": item.get("conflict_at"),
                    "local_status": item.get("last_local_status") or "",
                    "pending_remote_status": item.get("pending_remote_status"),
                })
        return result[:limit]

    def keep_local(self, external_id: str) -> dict[str, Any]:
        external = str(external_id or "").strip()
        if not external:
            raise HTTPException(404, "没有这条习惯同步记录")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM dida_habit_links WHERE source = ? AND external_id = ?",
                (SOURCE, external),
            ).fetchone()
        if row is None:
            raise HTTPException(404, "没有这条习惯同步记录")
        link = dict(row)
        pending = _load_json(link.get("pending_checkins_json"), {})
        pending_material = _load_json(link.get("pending_remote_material_json"), {})
        deleted_remote = (
            str(link.get("conflict_reason") or "") == "remote_deleted_local_changed"
            and not link.get("pending_remote_payload_json")
        )
        if deleted_remote:
            self._delete_link(external)
            self.database.audit("dida_habit_keep_local", {"external_id": external})
            return {"ok": True, "external_id": external, "status": "synced"}
        definition_pending = bool(
            link.get("pending_remote_payload_json")
            or link.get("pending_remote_material_json"))
        pending_status = None
        if isinstance(pending_material, dict) and pending_material:
            pending_status = 2 if pending_material.get("status") == "archived" else 0
        with self.database.connect() as connection:
            if definition_pending:
                connection.execute(
                    """UPDATE dida_habit_links SET
                       status = 'synced', conflict_reason = '', conflict_at = NULL,
                       last_local_revision = -1, last_local_mapped_json = '',
                       last_remote_hash = pending_remote_hash,
                       last_remote_material_json = pending_remote_material_json,
                       last_remote_status = COALESCE(?, last_remote_status),
                       last_remote_etag = CASE WHEN pending_remote_etag != ''
                           THEN pending_remote_etag ELSE last_remote_etag END,
                       last_remote_modified = CASE WHEN pending_remote_modified != ''
                           THEN pending_remote_modified ELSE last_remote_modified END,
                       remote_payload_json = CASE
                           WHEN pending_remote_payload_json != ''
                           THEN pending_remote_payload_json ELSE remote_payload_json END,
                       pending_remote_title = '', pending_remote_payload_json = '',
                       pending_remote_hash = '', pending_remote_material_json = '',
                       pending_remote_etag = '', pending_remote_modified = '',
                       pending_checkins_json = '{}',
                       local_archived_by_sync = 0,
                       last_synced_at = ?, updated_at = ?
                       WHERE source = ? AND external_id = ?""",
                    (pending_status, _iso_now(), _iso_now(), SOURCE, external),
                )
            else:
                connection.execute(
                    """UPDATE dida_habit_links SET
                       status = 'synced', conflict_reason = '', conflict_at = NULL,
                       pending_checkins_json = '{}',
                       local_archived_by_sync = 0,
                       last_synced_at = ?, updated_at = ?
                       WHERE source = ? AND external_id = ?""",
                    (_iso_now(), _iso_now(), SOURCE, external),
                )
            connection.commit()
        if isinstance(pending, dict):
            for day, item in pending.items():
                if not isinstance(item, dict):
                    continue
                if item.get("deleted"):
                    self._delete_checkin_link(external, str(day))
                    continue
                status = item.get("status")
                try:
                    remote_status = int(status) if status is not None else None
                except (TypeError, ValueError):
                    remote_status = None
                self._upsert_checkin_link(
                    external_id=external, day=str(day),
                    remote_id=str(item.get("id") or ""),
                    local_revision=-1, local_status="",
                    remote_hash=str(item.get("hash") or ""),
                    remote_status=remote_status,
                    payload=item.get("payload") if isinstance(item.get("payload"), dict) else {},
                )
        self.database.audit("dida_habit_keep_local", {"external_id": external})
        return {"ok": True, "external_id": external, "status": "synced"}

    # ---------- 链接表 helpers ----------

    def _load_links(self) -> dict[str, dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM dida_habit_links WHERE source = ?", (SOURCE,)
            ).fetchall()
        return {str(row["external_id"]): dict(row) for row in rows}

    def _load_checkin_links(self) -> dict[tuple[str, str], dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM dida_habit_checkin_links WHERE source = ?", (SOURCE,)
            ).fetchall()
        return {(str(row["external_id"]), str(row["date"])): dict(row) for row in rows}

    def _upsert_link(self, connection, values: dict[str, Any]) -> None:
        now = _iso_now()
        defaults: dict[str, Any] = {key: "" for key in LINK_COLUMNS}
        defaults.update({"source": SOURCE, "last_remote_status": None,
                         "conflict_at": None, "local_archived_by_sync": 0})
        values = {**defaults, **values}
        values["source"] = SOURCE
        values["updated_at"] = now
        if not values.get("created_at"):
            values["created_at"] = now
        current = connection.execute(
            "SELECT created_at FROM dida_habit_links WHERE source = ? AND external_id = ?",
            (SOURCE, values["external_id"]),
        ).fetchone()
        if current is not None and current["created_at"]:
            values["created_at"] = str(current["created_at"])
        columns = ", ".join(LINK_COLUMNS)
        placeholders = ", ".join("?" for _ in LINK_COLUMNS)
        updates = ", ".join(
            f"{name} = excluded.{name}" for name in LINK_COLUMNS
            if name not in {"source", "external_id", "created_at"})
        connection.execute(
            f"INSERT INTO dida_habit_links ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(source, external_id) DO UPDATE SET {updates}",
            tuple(values.get(name) for name in LINK_COLUMNS),
        )

    def _save_synced_link(self, connection, *, external_id: str, habit: dict[str, Any],
                          remote: RemoteHabit, remote_material: dict[str, Any] | None = None,
                          remote_hash: str | None = None, payload: dict[str, Any] | None = None,
                          local_archived_by_sync: int = 0) -> None:
        material = local_habit_material(habit)
        remote_values = remote_material or material
        self._upsert_link(connection, {
            "external_id": external_id,
            "uid": str(habit.get("uid") or ""),
            "relpath": str(habit.get("path") or ""),
            "last_title": str(habit.get("title") or ""),
            "last_local_revision": int(habit.get("revision") or 0),
            "last_local_mapped_json": _json(material),
            "last_remote_hash": remote_hash or material_hash(remote_values),
            "last_remote_material_json": _json(remote_values),
            "last_remote_status": int(remote.status),
            "last_remote_etag": remote.etag,
            "last_remote_modified": remote.modified_time,
            "remote_payload_json": _json(payload if payload is not None else remote.raw),
            "status": "synced",
            "local_archived_by_sync": int(local_archived_by_sync),
            "last_synced_at": _iso_now(),
        })

    def _save_conflict_link(self, connection, *, external_id: str, habit: dict[str, Any],
                            remote: RemoteHabit, mapped, reason: str,
                            pending_checkins: dict[str, Any] | None = None) -> None:
        self._upsert_link(connection, {
            "external_id": external_id,
            "uid": str(habit.get("uid") or ""),
            "relpath": str(habit.get("path") or ""),
            "last_title": str(habit.get("title") or ""),
            "last_local_revision": int(habit.get("revision") or 0),
            "last_local_mapped_json": local_material_json(habit),
            "last_remote_hash": mapped.remote_hash if mapped else "",
            "last_remote_material_json": _json(mapped.remote_material) if mapped else "",
            "last_remote_status": int(remote.status) if remote else None,
            "last_remote_etag": remote.etag if remote else "",
            "last_remote_modified": remote.modified_time if remote else "",
            "remote_payload_json": _json(remote.raw) if remote else "",
            "status": "conflict",
            "conflict_reason": reason,
            "conflict_at": _iso_now(),
            "pending_remote_title": mapped.title if mapped else "",
            "pending_remote_payload_json": _json(remote.raw) if remote else "",
            "pending_remote_hash": mapped.remote_hash if mapped else "",
            "pending_remote_material_json": _json(mapped.remote_material) if mapped else "",
            "pending_remote_etag": remote.etag if remote else "",
            "pending_remote_modified": remote.modified_time if remote else "",
            "pending_checkins_json": _json(pending_checkins or {}),
        })

    def _save_remote_deleted_conflict(self, external_id: str,
                                      habit: dict[str, Any]) -> None:
        with self.database.connect() as connection:
            self._upsert_link(connection, {
                "external_id": external_id,
                "uid": str(habit.get("uid") or ""),
                "relpath": str(habit.get("path") or ""),
                "last_title": str(habit.get("title") or ""),
                "last_local_revision": int(habit.get("revision") or 0),
                "last_local_mapped_json": local_material_json(habit),
                "status": "conflict",
                "conflict_reason": "remote_deleted_local_changed",
                "conflict_at": _iso_now(),
                "pending_remote_title": "",
                "pending_remote_payload_json": "",
                "pending_remote_hash": "",
                "pending_remote_material_json": "",
                "pending_remote_etag": "",
                "pending_remote_modified": "",
            })

    def _mark_link(self, external_id: str, status: str, reason: str = "",
                   *, archived_by_sync: int | None = None) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE dida_habit_links SET status = ?, conflict_reason = ?,
                   conflict_at = CASE WHEN ? = 'conflict' THEN ? ELSE conflict_at END,
                   local_archived_by_sync = COALESCE(?, local_archived_by_sync),
                   updated_at = ? WHERE source = ? AND external_id = ?""",
                (status, reason, status, _iso_now(), archived_by_sync,
                 _iso_now(), SOURCE, external_id),
            )
            connection.commit()

    def _delete_link(self, external_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM dida_habit_checkin_links WHERE source = ? AND external_id = ?",
                (SOURCE, external_id),
            )
            connection.execute(
                "DELETE FROM dida_habit_links WHERE source = ? AND external_id = ?",
                (SOURCE, external_id),
            )
            connection.commit()

    def _upsert_checkin_link(self, *, external_id: str, day: str, remote_id: str,
                             local_revision: int, local_status: str,
                             remote_hash: str, remote_status: int | None,
                             payload: dict[str, Any] | None = None,
                             status: str = "synced") -> None:
        now = _iso_now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO dida_habit_checkin_links
                   (source, external_id, date, remote_checkin_id, last_local_revision,
                    last_local_status, last_remote_hash, last_remote_status,
                    remote_payload_json, status, conflict_reason, conflict_at,
                    pending_remote_checkin_id, pending_remote_payload_json,
                    pending_remote_hash, pending_remote_status, last_synced_at,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'',NULL,'','','',NULL,?,?,?)
                   ON CONFLICT(source, external_id, date) DO UPDATE SET
                     remote_checkin_id = excluded.remote_checkin_id,
                     last_local_revision = excluded.last_local_revision,
                     last_local_status = excluded.last_local_status,
                     last_remote_hash = excluded.last_remote_hash,
                     last_remote_status = excluded.last_remote_status,
                     remote_payload_json = excluded.remote_payload_json,
                     status = excluded.status, conflict_reason = '',
                     conflict_at = NULL, pending_remote_checkin_id = '',
                     pending_remote_payload_json = '', pending_remote_hash = '',
                     pending_remote_status = NULL,
                     last_synced_at = excluded.last_synced_at,
                     updated_at = excluded.updated_at""",
                (SOURCE, external_id, day, remote_id, local_revision, local_status,
                 remote_hash, remote_status, _json(payload or {}), status,
                 now, now, now),
            )
            connection.commit()

    def _save_checkin_conflict(self, external_id: str, day: str, *,
                               local_revision: int, local_status: str,
                               remote_id: str, remote_hash: str,
                               remote_status: int | None,
                               payload: dict[str, Any] | None = None,
                               reason: str = "checkin_both_changed") -> None:
        now = _iso_now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO dida_habit_checkin_links
                   (source, external_id, date, remote_checkin_id, last_local_revision,
                    last_local_status, last_remote_hash, last_remote_status,
                    remote_payload_json, status, conflict_reason, conflict_at,
                    pending_remote_checkin_id, pending_remote_payload_json,
                    pending_remote_hash, pending_remote_status, last_synced_at,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,'conflict',?,?,?,?,?,?,NULL,?,?)
                   ON CONFLICT(source, external_id, date) DO UPDATE SET
                     status = 'conflict', conflict_reason = excluded.conflict_reason,
                     conflict_at = excluded.conflict_at,
                     last_local_revision = excluded.last_local_revision,
                     last_local_status = excluded.last_local_status,
                     pending_remote_checkin_id = excluded.pending_remote_checkin_id,
                     pending_remote_payload_json = excluded.pending_remote_payload_json,
                     pending_remote_hash = excluded.pending_remote_hash,
                     pending_remote_status = excluded.pending_remote_status,
                     updated_at = excluded.updated_at""",
                (SOURCE, external_id, day, remote_id, local_revision, local_status,
                 remote_hash, remote_status, _json(payload or {}), reason, now,
                 remote_id, _json(payload or {}), remote_hash, remote_status,
                 now, now),
            )
            connection.commit()

    def _delete_checkin_link(self, external_id: str, day: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM dida_habit_checkin_links "
                "WHERE source = ? AND external_id = ? AND date = ?",
                (SOURCE, external_id, day),
            )
            connection.commit()

    # ---------- 本地读取 ----------

    def _local_snapshot(self, service=None) -> tuple[Any, list[dict[str, Any]], dict[tuple[int, str], dict[str, Any]]]:
        habit_service = service or self._habit_service()
        payload = habit_service.export_payload()
        habits = list(payload.get("habits") or [])
        entries = {
            (int(row["habit_id"]), str(row["date"])): dict(row)
            for row in payload.get("entries") or []
        }
        return habit_service, habits, entries

    def _note_external_ids(self, habits: list[dict[str, Any]]) -> dict[str, str]:
        result: dict[str, str] = {}
        if self.vault_dir is None:
            return result
        for habit in habits:
            source = str(habit.get("path") or "")
            if not source:
                continue
            try:
                metadata = habit_markdown.read_note(self.vault_dir / source)["metadata"]
            except Exception:
                continue
            external = str(metadata.get("dida_id") or "").strip()
            if external:
                result[str(habit["uid"])] = external
        return result

    def _remote_checkin_dates(self, checkins: dict[str, tuple[RemoteHabitCheckin, ...]]) -> dict[str, dict[str, RemoteHabitCheckin]]:
        """按习惯/日期取一条记录；同一天多条时完成优先、opTime 最新。"""
        result: dict[str, dict[str, RemoteHabitCheckin]] = {}
        for habit_id, rows in (checkins or {}).items():
            by_day: dict[str, RemoteHabitCheckin] = {}
            for row in rows:
                day = parse_checkin_stamp(row.checkin_stamp)
                if not day:
                    continue
                current = by_day.get(day)
                if current is None:
                    by_day[day] = row
                    continue
                if int(row.status) == 2 and int(current.status) != 2:
                    by_day[day] = row
                elif int(row.status) == int(current.status) and str(row.op_time or "") > str(current.op_time or ""):
                    by_day[day] = row
            result[str(habit_id)] = by_day
        return result

    # ---------- 拉取 ----------

    def apply_remote(self, remote_habits: tuple[RemoteHabit, ...] | list[RemoteHabit],
                     checkins: dict[str, tuple[RemoteHabitCheckin, ...]] | None, *,
                     dry_run: bool = False, force: bool = False,
                     trigger: str = "manual") -> dict[str, Any]:
        if not self.enabled():
            return _disabled("habits_disabled")
        if self.read_only and not dry_run:
            raise VaultReadOnlyError("当前是 Obsidian 只读模式，已拒绝从滴答同步习惯")
        if not dry_run:
            ensure_vault_writable(self.read_only, "从滴答同步习惯")
        counts: Counter = Counter()
        warnings: Counter = Counter()
        errors: list[dict[str, str]] = []
        remote_list = list(remote_habits or ())
        remote_by_id = {str(item.id): item for item in remote_list if item.id}
        try:
            service, local_habits, _entries = self._local_snapshot()
        except Exception as error:
            return {"enabled": True, "counts": {}, "warnings": ["habit_local_read_failed"],
                    "errors": [{"code": type(error).__name__, "message": str(error)}]}
        local_by_uid = {str(item["uid"]): item for item in local_habits}
        active_local = [item for item in local_habits if not item.get("missing")]
        links = self._load_links()
        links_by_uid = {
            str(link.get("uid")): link for link in links.values()
            if str(link.get("uid") or "")
        }
        note_ids = self._note_external_ids(active_local)
        checkins_available = checkins is not None
        checkin_dates = self._remote_checkin_dates(checkins or {})

        # ----- 配对 -----
        pairs: dict[str, tuple[dict[str, Any], dict[str, Any] | None, RemoteHabit]] = {}
        claimed: set[str] = set()
        for uid, habit in local_by_uid.items():
            link = links_by_uid.get(uid)
            if not link:
                continue
            remote = remote_by_id.get(str(link.get("external_id") or ""))
            if remote is not None:
                pairs[uid] = (habit, link, remote)
                claimed.add(remote.id)
        for habit in active_local:
            uid = str(habit["uid"])
            if uid in pairs:
                continue
            external = note_ids.get(uid)
            if not external or external not in remote_by_id or external in claimed:
                continue
            link = links.get(external)
            if link is not None and str(link.get("uid") or "") not in ("", uid):
                continue
            pairs[uid] = (habit, link, remote_by_id[external])
            claimed.add(external)
        # 唯一同名配对（只匹配没有链接、没有 dida_id 的本地习惯）
        candidates_by_title: dict[str, list[RemoteHabit]] = {}
        for remote in remote_list:
            if remote.id in claimed:
                continue
            candidates_by_title.setdefault(canonical_title(remote.name), []).append(remote)
        local_by_title: dict[str, list[dict[str, Any]]] = {}
        for habit in active_local:
            uid = str(habit["uid"])
            if uid in pairs or uid in links_by_uid or note_ids.get(uid):
                continue
            local_by_title.setdefault(canonical_title(habit["title"]), []).append(habit)
        for title, locals_ in local_by_title.items():
            remotes = candidates_by_title.get(title) or []
            if not remotes:
                continue
            if len(locals_) == 1 and len(remotes) == 1:
                habit = locals_[0]
                remote = remotes[0]
                pairs[str(habit["uid"])] = (habit, links.get(remote.id), remote)
                claimed.add(remote.id)
            else:
                warnings["habit_title_ambiguous"] += 1

        # ----- 空列表守卫 / 远端删除 -----
        remote_ids = set(remote_by_id)
        if not remote_list and links:
            # 空列表仍然按“远端确实没有习惯”处理，但留一条 warning；本地只归档、
            # 不删笔记和打卡，远端恢复后会自动取消归档。
            warnings["habit_list_empty_guard"] += 1
        for external, link in links.items():
            if external in remote_ids:
                continue
            if str(link.get("status") or "") == "pending_create":
                # 远端新建还没确认，不能把“列表里没有”当成远端删除。
                continue
            if link.get("last_remote_status") == 2:
                warnings["habit_archived_not_listed"] += 1
                continue
            uid = str(link.get("uid") or "")
            habit = local_by_uid.get(uid)
            if habit is None:
                counts["habits_local_missing"] += 1
                continue
            if habit.get("missing"):
                counts["habits_remote_deleted"] += 1
                if not dry_run:
                    self._mark_link(external, "remote_deleted", "remote_deleted")
                continue
            baseline = material_from_json(link.get("last_local_mapped_json"))
            local_changed = material_diff(local_habit_material(habit), baseline)
            if local_changed and not force:
                counts["habit_conflicts"] += 1
                if not dry_run:
                    self._save_remote_deleted_conflict(external, habit)
                continue
            counts["habits_archived"] += 1
            if dry_run:
                continue
            try:
                service.update(
                    int(habit["id"]),
                    habits_module.HabitUpdate(revision=int(habit["revision"]), status="archived"))
            except HTTPException as error:
                errors.append({"code": f"habit_archive_http_{error.status_code}",
                               "message": str(error.detail)})
                warnings["habit_archive_error"] += 1
                continue
            self._mark_link(external, "remote_deleted", "remote_deleted",
                            archived_by_sync=1)

        # ----- 远端独有 → 本地新建 -----
        # 已有链接但本地习惯被删除时，远端习惯不能重新拉回来；留给
        # push_deletions 按 push_deletes 决定是否删除远端。
        known_external = set(links)
        for remote in remote_list:
            if remote.id in claimed or remote.id in known_external:
                continue
            counts["habits_created"] += 1
            if dry_run:
                continue
            try:
                created, mapped = self._create_local_habit(remote, service,
                                                           checkin_dates.get(remote.id, {}))
            except Exception as error:
                counts["habits_created"] -= 1
                counts["habits_errors"] += 1
                errors.append({"code": getattr(error, "code", type(error).__name__),
                               "message": str(getattr(error, "message", error))})
                warnings["habit_create_error"] += 1
                continue
            claimed.add(remote.id)
            counts["habits_linked"] += 1

        # ----- 常规 reconcile -----
        for uid, (habit, link, remote) in pairs.items():
            if habit.get("missing"):
                counts["habits_local_missing"] += 1
                if link and not dry_run:
                    self._mark_link(str(link["external_id"]), "local_missing",
                                    "local_note_missing")
                continue
            if link is not None and str(link.get("status") or "") == "remote_deleted":
                # 远端重新出现：只有同步自动归档的习惯才自动取消归档。
                if (int(link.get("local_archived_by_sync") or 0) == 1
                        and str(habit.get("status") or "") == "archived"):
                    counts["habits_restored"] += 1
                    if not dry_run:
                        try:
                            habit = service.update(
                                int(habit["id"]),
                                habits_module.HabitUpdate(
                                    revision=int(habit["revision"]), status="active"))
                        except HTTPException:
                            warnings["habit_restore_error"] += 1
                            continue
                link = None
            if link is not None and str(link.get("status") or "") == "conflict" and not force:
                counts["habit_conflicts"] += 1
                continue
            if dry_run:
                counts["habits_unchanged"] += 1
                continue
            try:
                outcome = self._reconcile_habit(
                    service, habit, link, remote, force=force)
            except HTTPException as error:
                counts["habits_errors"] += 1
                errors.append({"code": f"habit_http_{error.status_code}",
                               "message": str(error.detail)})
                warnings["habit_update_error"] += 1
                continue
            for key, value in (outcome.get("counts") or {}).items():
                counts[key] += int(value or 0)
            for code in outcome.get("warnings") or ():
                warnings[code] += 1

        # ----- 打卡 -----
        if not dry_run and checkins_available:
            service, local_habits, entries = self._local_snapshot(service)
            local_by_uid = {str(item["uid"]): item for item in local_habits}
            links = self._load_links()
            links_by_uid = {
                str(link.get("uid")): link for link in links.values()
                if str(link.get("uid") or "")
            }
            checkin_links = self._load_checkin_links()
            checkin_outcome = self._apply_remote_checkins(
                service, local_by_uid, entries, links_by_uid,
                checkin_links, checkin_dates, force=force)
            for key, value in (checkin_outcome.get("counts") or {}).items():
                counts[key] += int(value or 0)
            for code in checkin_outcome.get("warnings") or ():
                warnings[code] += 1
            errors.extend(checkin_outcome.get("errors") or [])
        elif checkins_available:
            # dry-run 只估算：有远端打卡的习惯数量。
            counts["habit_checkins_pulled"] = sum(
                len(rows) for rows in checkin_dates.values())

        summary = {
            "enabled": True,
            "dry_run": dry_run,
            "force": force,
            "trigger": trigger,
            "counts": {key: int(value) for key, value in counts.items() if value},
            "warnings": sorted(warnings),
            "warning_counts": {key: int(value) for key, value in warnings.items()},
            "errors": errors,
        }
        if not dry_run:
            self.database.audit("dida_habit_pull_completed", {
                "counts": summary["counts"], "warnings": summary["warnings"],
                "trigger": trigger,
            })
        return summary

    def _create_local_habit(self, remote: RemoteHabit, service,
                            checkins_by_day: dict[str, RemoteHabitCheckin]):
        fallback = ""
        if checkins_by_day:
            fallback = min(checkins_by_day)
        mapped = map_remote_habit(remote, fallback_start=fallback)
        start_date = mapped.values["start_date"] or habits_module.shanghai_today().isoformat()
        payload = habits_module.HabitCreate(
            title=mapped.title,
            cue="",
            minimum_action="",
            start_date=start_date,
            frequency=mapped.values["frequency"] or {"kind": "daily"},
            goal="binary",
            target_days=mapped.values.get("target_days"),
            icon=habits_module.DEFAULT_ICON,
            color=mapped.values.get("color") or "",
            group=habits_module.DEFAULT_GROUP,
            reminder=mapped.values.get("reminder") or "",
            auto_log=False,
            priority="medium",
        )
        created = service.create(payload)
        if mapped.values.get("status") == "archived":
            created = service.update(
                int(created["id"]),
                habits_module.HabitUpdate(revision=int(created["revision"]), status="archived"))
        if self.vault_dir is not None:
            service.set_dida_links({str(created["uid"]): remote.id})
        with self.database.connect() as connection:
            self._save_synced_link(
                connection, external_id=remote.id, habit=created, remote=remote,
                remote_material=mapped.remote_material, remote_hash=mapped.remote_hash,
                payload=remote.raw)
        return created, mapped

    def _reconcile_habit(self, service, habit: dict[str, Any],
                         link: dict[str, Any] | None, remote: RemoteHabit,
                         *, force: bool) -> dict[str, Any]:
        counts: Counter = Counter()
        warnings: Counter = Counter()
        mapped = map_remote_habit(remote, fallback_start=str(habit.get("start_date") or ""))
        local_material = local_habit_material(habit)
        if link is None:
            same = first_link_matches(
                local_material, mapped.remote_material, mapped.present_keys)
            if same or force:
                # 首次配对时用远端补齐本地默认值（颜色/提醒/开始日期等）；
                # 真正的设置差异已经由 first_link_matches 判为冲突。
                updated = self._apply_remote_definition(
                    service, habit, mapped, set(mapped.present_keys), warnings)
                habit = updated or habit
                with self.database.connect() as connection:
                    self._save_synced_link(
                        connection, external_id=remote.id, habit=habit, remote=remote,
                        remote_material=mapped.remote_material,
                        remote_hash=mapped.remote_hash, payload=remote.raw)
                counts["habits_linked" if same else "habits_updated"] += 1
            else:
                with self.database.connect() as connection:
                    self._save_conflict_link(
                        connection, external_id=remote.id, habit=habit, remote=remote,
                        mapped=mapped, reason="first_link_differs")
                counts["habit_conflicts"] += 1
            return {"counts": counts, "warnings": warnings}
        baseline = material_from_json(link.get("last_local_mapped_json"))
        remote_baseline = material_from_json(link.get("last_remote_material_json"))
        local_changed = material_diff(local_material, baseline, MATERIAL_KEYS)
        if force:
            remote_changed_keys = set(mapped.present_keys)
        else:
            remote_changed_keys = set(
                material_diff(mapped.remote_material, remote_baseline, mapped.present_keys))
        if not local_changed and not remote_changed_keys:
            if (str(link.get("status") or "") != "synced"
                    or link.get("last_remote_etag") != remote.etag
                    or link.get("remote_payload_json", "") == ""):
                with self.database.connect() as connection:
                    self._save_synced_link(
                        connection, external_id=remote.id, habit=habit, remote=remote,
                        remote_material=mapped.remote_material,
                        remote_hash=mapped.remote_hash, payload=remote.raw,
                        local_archived_by_sync=int(link.get("local_archived_by_sync") or 0))
            counts["habits_unchanged"] += 1
            return {"counts": counts, "warnings": warnings}
        if remote_changed_keys:
            overlap = set(local_changed) & set(remote_changed_keys)
            if overlap and not force:
                with self.database.connect() as connection:
                    self._save_conflict_link(
                        connection, external_id=remote.id, habit=habit, remote=remote,
                        mapped=mapped, reason="local_and_remote_changed")
                counts["habit_conflicts"] += 1
                return {"counts": counts, "warnings": warnings}
            apply_keys = set(remote_changed_keys)
            updated = self._apply_remote_definition(
                service, habit, mapped, apply_keys, warnings)
            if updated is None:
                counts["habit_conflicts"] += 1
                return {"counts": counts, "warnings": warnings}
            habit = updated
            # 已应用远端值的键写入基线；本地其他未同步修改仍保留在基线的旧值上，
            # 下一次 push 会继续发送。
            new_baseline = dict(baseline)
            for key in apply_keys:
                if key in mapped.remote_material:
                    new_baseline[key] = mapped.remote_material[key]
            if force:
                new_baseline = local_habit_material(habit)
            with self.database.connect() as connection:
                self._upsert_link(connection, {
                    "external_id": remote.id,
                    "uid": str(habit.get("uid") or ""),
                    "relpath": str(habit.get("path") or ""),
                    "last_title": str(habit.get("title") or ""),
                    "last_local_revision": int(habit.get("revision") or 0),
                    "last_local_mapped_json": _json(new_baseline),
                    "last_remote_hash": mapped.remote_hash,
                    "last_remote_material_json": _json(mapped.remote_material),
                    "last_remote_status": int(remote.status),
                    "last_remote_etag": remote.etag,
                    "last_remote_modified": remote.modified_time,
                    "remote_payload_json": _json(remote.raw),
                    "status": "synced",
                    "local_archived_by_sync": int(link.get("local_archived_by_sync") or 0),
                    "last_synced_at": _iso_now(),
                })
            counts["habits_updated"] += 1
            return {"counts": counts, "warnings": warnings}
        counts["habits_local_edits"] += 1
        return {"counts": counts, "warnings": warnings}

    def _apply_remote_definition(self, service, habit: dict[str, Any], mapped,
                                 keys: set[str], warnings: Counter) -> dict[str, Any] | None:
        changes: dict[str, Any] = {}
        for key in keys:
            if key not in mapped.present_keys:
                continue
            value = mapped.remote_material.get(key)
            if key == "title":
                changes["title"] = value
            elif key == "color":
                changes["color"] = value or ""
            elif key == "frequency":
                changes["frequency"] = value
            elif key == "start_date":
                changes["start_date"] = value
            elif key == "target_days":
                changes["target_days"] = value
            elif key == "reminder":
                changes["reminder"] = value or ""
            elif key == "status":
                changes["status"] = value
        if not changes:
            return habit
        payload = habits_module.HabitUpdate(
            revision=int(habit["revision"]), **changes)
        try:
            return service.update(int(habit["id"]), payload)
        except HTTPException as error:
            if error.status_code == 422 and "target_days" in changes:
                warnings["habit_target_days_rejected"] += 1
                retry = dict(changes)
                retry.pop("target_days", None)
                if retry:
                    try:
                        return service.update(
                            int(habit["id"]),
                            habits_module.HabitUpdate(
                                revision=int(habit["revision"]), **retry))
                    except HTTPException as retry_error:
                        warnings["habit_update_rejected"] += 1
                        del retry_error
                        return None
                return habit
            if error.status_code == 409:
                warnings["local_note_changed_during_sync"] += 1
                return None
            raise

    def _apply_remote_checkins(self, service, local_by_uid, entries, links_by_uid,
                               checkin_links, checkin_dates, *, force: bool) -> dict[str, Any]:
        counts: Counter = Counter()
        warnings: Counter = Counter()
        errors: list[dict[str, str]] = []
        updates: list[dict[str, Any]] = []
        planned: list[dict[str, Any]] = []
        skipped_keys: set[tuple[int, str]] = set()
        conflicts: dict[str, dict[str, Any]] = {}
        for uid, habit in local_by_uid.items():
            if habit.get("missing"):
                continue
            link = links_by_uid.get(uid)
            if link is None or str(link.get("status") or "") != "synced":
                continue
            external = str(link["external_id"])
            remote_by_day = checkin_dates.get(external, {})
            habit_id = int(habit["id"])
            days = set(remote_by_day)
            days.update(day for (habit_external, day) in checkin_links
                        if habit_external == external)
            local_days = {
                day for (entry_habit_id, day) in entries if entry_habit_id == habit_id
            }
            days.update(local_days)
            for day in sorted(days):
                local_entry = entries.get((habit_id, day))
                local_status = str(local_entry.get("status") or "unrecorded") if local_entry else "unrecorded"
                local_revision = int(local_entry.get("revision") or 0) if local_entry else 0
                remote = remote_by_day.get(day)
                remote_status = int(remote.status) if remote else None
                remote_hash = remote_checkin_hash(remote) if remote else ABSENT_CHECKIN_HASH
                target = LOCAL_CHECKIN_STATUS.get(remote_status) if remote_status in (1, 2) else None
                baseline = checkin_links.get((external, day))
                baseline_status = str(baseline.get("last_local_status") or "") if baseline else ""
                baseline_hash = str(baseline.get("last_remote_hash") or "") if baseline else ""
                local_changed = (
                    local_status != baseline_status if baseline
                    else local_status in ("done", "failed", "skipped")
                )
                remote_changed = (
                    remote_hash != baseline_hash if baseline
                    else target is not None
                )
                if remote is None and baseline is not None:
                    if str(baseline.get("status") or "") == "pending_add":
                        # 新建打卡还没确认，留给 push 复用同一 ID 重试。
                        continue
                    if local_status == "skipped":
                        # 主动休息是本地专属状态；远端记录消失时保留休息。
                        self._delete_checkin_link(external, day)
                        counts["checkins_local_edits"] += 1
                        continue
                    # 远端打卡被删除：本地没改就同步取消，本地也改过则冲突。
                    if local_changed:
                        self._save_checkin_conflict(
                            external, day, local_revision=local_revision,
                            local_status=local_status, remote_id="",
                            remote_hash=ABSENT_CHECKIN_HASH, remote_status=None,
                            payload={}, reason="checkin_remote_deleted")
                        conflicts.setdefault(external, {})[day] = {
                            "deleted": True, "status": None, "id": "",
                            "hash": ABSENT_CHECKIN_HASH, "payload": {},
                        }
                        counts["checkins_conflicts"] += 1
                    else:
                        updates.append({
                            "habit_id": habit_id, "date": day,
                            "status": "unrecorded", "note": None,
                            "expected_status": local_status,
                            "external": external, "uncheck": True,
                        })
                        planned.append({
                            "external": external, "day": day,
                            "target": "unrecorded", "remote": None,
                            "remote_hash": ABSENT_CHECKIN_HASH,
                            "remote_status": None, "uncheck": True,
                        })
                        counts["checkins_unchecked"] += 1
                    continue
                if local_status == "skipped":
                    # 本地主动休息不会被远端的完成/失败覆盖。
                    if baseline is None and target is not None:
                        self._save_checkin_conflict(
                            external, day, local_revision=local_revision,
                            local_status=local_status,
                            remote_id=str(remote.id) if remote else "",
                            remote_hash=remote_hash, remote_status=remote_status,
                            payload=remote.raw if remote else {},
                            reason="first_checkin_differs")
                        conflicts.setdefault(external, {})[day] = {
                            "status": remote_status,
                            "id": str(remote.id) if remote else "",
                            "hash": remote_hash,
                            "payload": remote.raw if remote else {},
                        }
                        counts["checkins_conflicts"] += 1
                    elif baseline is not None and remote_changed:
                        self._save_checkin_conflict(
                            external, day, local_revision=local_revision,
                            local_status=local_status,
                            remote_id=str(remote.id) if remote else "",
                            remote_hash=remote_hash, remote_status=remote_status,
                            payload=remote.raw if remote else {},
                            reason="checkin_remote_changed_local_skipped")
                        conflicts.setdefault(external, {})[day] = {
                            "status": remote_status,
                            "id": str(remote.id) if remote else "",
                            "hash": remote_hash,
                            "payload": remote.raw if remote else {},
                        }
                        counts["checkins_conflicts"] += 1
                    else:
                        # 远端没有新变化：留给 push 删除远端记录。
                        counts["checkins_local_edits"] += 1
                    continue
                if target is None:
                    continue
                if local_status == target:
                    if baseline is None or baseline_hash != remote_hash:
                        self._upsert_checkin_link(
                            external_id=external, day=day,
                            remote_id=str(remote.id) if remote else "",
                            local_revision=local_revision, local_status=local_status,
                            remote_hash=remote_hash, remote_status=remote_status,
                            payload=remote.raw if remote else None)
                        counts["checkins_unchanged"] += 1
                    continue
                if remote_changed and not local_changed:
                    updates.append({
                        "habit_id": habit_id,
                        "date": day,
                        "status": target,
                        "note": None,
                        "expected_status": local_status,
                    })
                    planned.append({
                        "external": external, "day": day, "target": target,
                        "remote": remote, "remote_hash": remote_hash,
                        "remote_status": remote_status,
                    })
                    counts["checkins_updated"] += 1
                elif local_changed and not remote_changed:
                    counts["checkins_local_edits"] += 1
                elif local_changed and remote_changed:
                    if local_status == target:
                        self._upsert_checkin_link(
                            external_id=external, day=day,
                            remote_id=str(remote.id) if remote else "",
                            local_revision=local_revision, local_status=local_status,
                            remote_hash=remote_hash, remote_status=remote_status,
                            payload=remote.raw if remote else None)
                        counts["checkins_unchanged"] += 1
                    else:
                        self._save_checkin_conflict(
                            external, day, local_revision=local_revision,
                            local_status=local_status,
                            remote_id=str(remote.id) if remote else "",
                            remote_hash=remote_hash, remote_status=remote_status,
                            payload=remote.raw if remote else None,
                            reason="checkin_both_changed")
                        conflicts.setdefault(external, {})[day] = {
                            "status": remote_status, "id": str(remote.id) if remote else "",
                            "hash": remote_hash, "payload": remote.raw if remote else {},
                        }
                        counts["checkins_conflicts"] += 1
        # 拉取方向的批量取消保护：远端整段缺失时不要把本地历史全部清空。
        uncheck_groups: Counter = Counter(
            item["external"] for item in planned if item.get("uncheck"))
        if uncheck_groups:
            baseline_counts: Counter = Counter(
                external for (external, _day) in checkin_links)
            blocked = set()
            for external, total in uncheck_groups.items():
                baseline_count = int(baseline_counts.get(external, 0))
                if total > MAX_CHECKIN_DELETES or (
                        baseline_count >= 5
                        and total / max(baseline_count, 1) > MASS_DELETE_RATIO):
                    blocked.add(external)
                    warnings["checkin_mass_uncheck_guard"] += 1
            if blocked:
                updates = [
                    item for item in updates
                    if not (item.get("uncheck") and item.get("external") in blocked)
                ]
                planned = [
                    item for item in planned
                    if not (item.get("uncheck") and item.get("external") in blocked)
                ]
                counts["checkins_unchecked"] = max(
                    0, int(counts.get("checkins_unchecked", 0)) - sum(
                        total for external, total in uncheck_groups.items()
                        if external in blocked))
        if updates:
            try:
                outcome = service.apply_remote_entries(updates)
            except Exception as error:
                errors.append({"code": type(error).__name__, "message": str(error)})
                warnings["checkin_import_error"] += 1
                return {"counts": counts, "warnings": warnings, "errors": errors}
            for item in outcome.get("skipped") or []:
                key = (int(item.get("habit_id") or 0), str(item.get("date") or ""))
                skipped_keys.add(key)
                warnings["checkin_import_skipped"] += 1
            _service, _habits, fresh_entries = self._local_snapshot(service)
            planned_by_key = {}
            for item in planned:
                planned_by_key[(item["external"], item["day"])] = item
            for (external, day), item in planned_by_key.items():
                habit = next(
                    (h for h in local_by_uid.values()
                     if str(links_by_uid.get(str(h["uid"]), {}).get("external_id") or "") == external),
                    None)
                if habit is None:
                    continue
                habit_id = int(habit["id"])
                if (habit_id, day) in skipped_keys:
                    local_entry = entries.get((habit_id, day))
                    local_revision = int(local_entry.get("revision") or 0) if local_entry else 0
                    local_status = (str(local_entry.get("status") or "unrecorded")
                                    if local_entry else "unrecorded")
                    if item["target"] == "unrecorded":
                        self._save_checkin_conflict(
                            external, day, local_revision=local_revision,
                            local_status=local_status, remote_id="",
                            remote_hash=ABSENT_CHECKIN_HASH, remote_status=None,
                            payload={}, reason="checkin_remote_deleted")
                        conflicts.setdefault(external, {})[day] = {
                            "deleted": True, "status": None, "id": "",
                            "hash": ABSENT_CHECKIN_HASH, "payload": {},
                        }
                    else:
                        remote = item.get("remote")
                        self._save_checkin_conflict(
                            external, day, local_revision=local_revision,
                            local_status=local_status,
                            remote_id=str(remote.id) if remote else "",
                            remote_hash=item["remote_hash"],
                            remote_status=item["remote_status"],
                            payload=remote.raw if remote else {},
                            reason="checkin_import_skipped_local_changed")
                        conflicts.setdefault(external, {})[day] = {
                            "status": item["remote_status"],
                            "id": str(remote.id) if remote else "",
                            "hash": item["remote_hash"],
                            "payload": remote.raw if remote else {},
                        }
                    counts["checkins_conflicts"] += 1
                    continue
                if item["target"] == "unrecorded":
                    self._delete_checkin_link(external, day)
                    continue
                fresh = fresh_entries.get((habit_id, day))
                self._upsert_checkin_link(
                    external_id=external, day=day,
                    remote_id=str(item["remote"].id) if item["remote"] else "",
                    local_revision=int(fresh.get("revision") or 0) if fresh else 0,
                    local_status=item["target"],
                    remote_hash=item["remote_hash"],
                    remote_status=item["remote_status"],
                    payload=item["remote"].raw if item["remote"] else None)
        if conflicts:
            for external, pending in conflicts.items():
                with self.database.connect() as connection:
                    row = connection.execute(
                        "SELECT pending_checkins_json FROM dida_habit_links "
                        "WHERE source = ? AND external_id = ?", (SOURCE, external),
                    ).fetchone()
                    merged = _load_json(row["pending_checkins_json"] if row else "", {})
                    if isinstance(merged, dict):
                        merged.update(pending)
                    else:
                        merged = pending
                    connection.execute(
                        """UPDATE dida_habit_links SET
                           pending_checkins_json = ?, status = 'conflict',
                           conflict_reason = CASE WHEN conflict_reason = ''
                               THEN 'checkin_conflict' ELSE conflict_reason END,
                           conflict_at = COALESCE(conflict_at, ?), updated_at = ?
                           WHERE source = ? AND external_id = ?""",
                        (_json(merged), _iso_now(), _iso_now(), SOURCE, external),
                    )
                    connection.commit()
        return {"counts": counts, "warnings": warnings, "errors": errors}

    # ---------- 推送 ----------

    def _local_change_candidates(self) -> dict[str, Any]:
        service, habits, entries = self._local_snapshot()
        links = self._load_links()
        checkin_links = self._load_checkin_links()
        links_by_uid = {
            str(link.get("uid")): link for link in links.values()
            if str(link.get("uid") or "")
        }
        local_by_uid = {str(habit["uid"]): habit for habit in habits}
        note_ids = self._note_external_ids(habits)
        habit_candidates: list[dict[str, Any]] = []
        for habit in habits:
            if habit.get("missing"):
                continue
            link = links_by_uid.get(str(habit["uid"]))
            if link is None:
                habit_candidates.append({
                    "habit": habit, "link": None, "reason": "new",
                    "dida_id": note_ids.get(str(habit["uid"]), ""),
                })
                continue
            status = str(link.get("status") or "")
            if status == "conflict":
                continue
            if status == "remote_deleted":
                if str(habit.get("status") or "") == "active":
                    habit_candidates.append({"habit": habit, "link": link,
                                             "reason": "recreate"})
                continue
            if status == "pending_create":
                habit_candidates.append({"habit": habit, "link": link,
                                         "reason": "pending"})
                continue
            baseline = material_from_json(link.get("last_local_mapped_json"))
            if material_diff(local_habit_material(habit), baseline, MATERIAL_KEYS):
                habit_candidates.append({"habit": habit, "link": link,
                                         "reason": "update"})
        checkin_candidates: list[dict[str, Any]] = []
        for habit in habits:
            if habit.get("missing"):
                continue
            link = links_by_uid.get(str(habit["uid"]))
            if link is None or str(link.get("status") or "") != "synced":
                continue
            external = str(link["external_id"])
            for (habit_id, day), entry in entries.items():
                if int(habit_id) != int(habit["id"]):
                    continue
                status = str(entry.get("status") or "unrecorded")
                baseline = checkin_links.get((external, day))
                if status in ("done", "failed"):
                    baseline_status = str(baseline.get("last_local_status") or "") if baseline else ""
                    remote_status = baseline.get("last_remote_status") if baseline else None
                    remote_local = LOCAL_CHECKIN_STATUS.get(remote_status)
                    if (baseline is None or baseline_status != status
                            or remote_local != status):
                        checkin_candidates.append({
                            "habit": habit, "external": external, "day": day,
                            "status": status, "baseline": baseline, "entry": entry,
                        })
                elif status == "skipped":
                    # 主动休息是本地专属状态：如果滴答曾有一天记录，推送时删除它。
                    if baseline is not None:
                        checkin_candidates.append({
                            "habit": habit, "external": external, "day": day,
                            "status": status, "baseline": baseline, "entry": entry,
                        })
                elif status == "unrecorded" and baseline is not None:
                    checkin_candidates.append({
                        "habit": habit, "external": external, "day": day,
                        "status": status, "baseline": baseline, "entry": entry,
                    })
            for (external_id, day), baseline in checkin_links.items():
                if external_id != external:
                    continue
                if (int(habit["id"]), day) in entries:
                    continue
                if (int(baseline.get("last_local_revision") or 0) == 0
                        and str(baseline.get("status") or "") != "pending_add"):
                    continue
                checkin_candidates.append({
                    "habit": habit, "external": external, "day": day,
                    "status": "unrecorded", "baseline": baseline, "entry": None,
                })
        pending_checkins = [
            row for row in checkin_links.values()
            if str(row.get("status") or "") == "pending_add"
        ]
        return {
            "service": service,
            "habits": habits,
            "entries": entries,
            "links": links,
            "links_by_uid": links_by_uid,
            "local_by_uid": local_by_uid,
            "habit_candidates": habit_candidates,
            "checkin_candidates": checkin_candidates,
            "pending_checkins": pending_checkins,
        }

    async def push_changes(self, *, force: bool = False) -> dict[str, Any]:
        if not self.enabled():
            return _disabled("habits_disabled")
        row = self.service._row()
        if self.read_only or not self.supported:
            return _disabled("read_only_or_unsupported")
        if not row.get("push_changes"):
            return _disabled("push_changes_disabled")
        now = datetime.now(timezone.utc)
        if not force and self._change_backoff_until and now < self._change_backoff_until:
            return _disabled("backoff")
        if self._change_guard.locked():
            return _disabled("running")
        scan = await run_in_threadpool(self._local_change_candidates)
        if not (scan["habit_candidates"] or scan["checkin_candidates"]
                or scan["pending_checkins"]):
            return {"enabled": True, "counts": {}, "warnings": [], "errors": []}
        _cookie, csrf = self.service.credentials.load()
        if not csrf:
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE dida_sync_settings SET last_habit_push_at = ?, "
                    "last_habit_push_error_code = 'csrf_required', updated_at = ? WHERE id = 1",
                    (_iso_now(), _iso_now()),
                )
                connection.commit()
            self._change_backoff_until = now + timedelta(minutes=5)
            return {"enabled": True, "counts": {}, "warnings": ["habit_push_csrf_required"],
                    "errors": [{"code": "csrf_required",
                                "message": "需要 _csrf_token 才能回写滴答习惯"}]}
        async with self._change_guard:
            provider = None
            counts: Counter = Counter()
            warnings: Counter = Counter()
            errors: list[dict[str, str]] = []
            try:
                provider = self.service.build_provider()
                remote_list = list(await provider.fetch_habits())
                remote_by_id = {str(item.id): item for item in remote_list}
                remote_ids = list(remote_by_id)
                remote_checkins = await provider.query_habit_checkins(
                    remote_ids, after_stamp=CHECKIN_HISTORY_STAMP) if remote_ids else {}
                remote_by_day = self._remote_checkin_dates(remote_checkins)
                await self._push_habit_links_and_updates(
                    provider, remote_by_id, remote_by_day, scan, counts, warnings, errors)
                await self._push_checkins(
                    provider, remote_by_id, remote_by_day, counts, warnings, errors)
            except DidaError as error:
                self._change_backoff_until = now + timedelta(minutes=5)
                with self.database.connect() as connection:
                    connection.execute(
                        "UPDATE dida_sync_settings SET last_habit_push_at = ?, "
                        "last_habit_push_error_code = ?, updated_at = ? WHERE id = 1",
                        (_iso_now(), error.code, _iso_now()),
                    )
                    connection.commit()
                return {"enabled": True, "counts": dict(counts),
                        "warnings": sorted(warnings),
                        "errors": [{"code": error.code, "message": error.message}]}
            except Exception as error:
                self._change_backoff_until = now + timedelta(minutes=5)
                warnings["habit_push_error"] += 1
                errors.append({"code": type(error).__name__, "message": str(error)})
            finally:
                if provider is not None:
                    await provider.aclose()
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE dida_sync_settings SET last_habit_push_at = ?, "
                    "last_habit_push_error_code = ?, updated_at = ? WHERE id = 1",
                    (_iso_now(), errors[0]["code"] if errors else "", _iso_now()),
                )
                connection.commit()
            self._change_backoff_until = (
                datetime.now(timezone.utc) + timedelta(minutes=5) if errors else None)
            if counts:
                self.database.audit("dida_habit_push_completed", {
                    "counts": dict(counts), "warnings": sorted(warnings)})
            return {"enabled": True, "counts": dict(counts),
                    "warnings": sorted(warnings), "errors": errors}

    async def _push_habit_links_and_updates(self, provider, remote_by_id, remote_by_day,
                                            scan, counts, warnings, errors) -> None:
        service = scan["service"]
        local_by_uid = scan["local_by_uid"]
        links_by_uid = scan["links_by_uid"]
        create_updates: list[dict[str, Any]] = []
        update_updates: list[dict[str, Any]] = []
        pending_rows: list[dict[str, Any]] = []
        recreate: list[dict[str, Any]] = []
        claimed_remote = {
            str(link.get("external_id") or "") for link in scan["links"].values()
            if str(link.get("external_id") or "")
        }
        # 已有链接的定义更新
        for candidate in scan["habit_candidates"]:
            habit = candidate["habit"]
            link = candidate["link"]
            if link is None:
                continue
            status = str(link.get("status") or "")
            if status == "pending_create":
                pending_rows.append(candidate)
                continue
            if status == "remote_deleted":
                recreate.append(candidate)
                continue
            base = _load_json(link.get("remote_payload_json"), {})
            if not isinstance(base, dict) or not base:
                warnings["habit_push_needs_pull"] += 1
                continue
            baseline = material_from_json(link.get("last_local_mapped_json"))
            changed_keys = material_diff(local_habit_material(habit), baseline, MATERIAL_KEYS)
            if not changed_keys:
                if status == "local_missing":
                    remote = remote_by_id.get(str(link.get("external_id") or ""))
                    if remote is not None:
                        mapped = map_remote_habit(
                            remote, fallback_start=str(habit.get("start_date") or ""))
                        with self.database.connect() as connection:
                            self._save_synced_link(
                                connection, external_id=remote.id, habit=habit,
                                remote=remote, remote_material=mapped.remote_material,
                                remote_hash=mapped.remote_hash, payload=remote.raw)
                        counts["habits_unchanged"] += 1
                continue
            payload = build_remote_habit_payload(
                habit, habit_id=str(link["external_id"]), base=base,
                changed_keys=set(changed_keys))
            update_updates.append({
                "payload": payload, "habit": habit, "link": link,
                "changed_keys": changed_keys,
            })
        # 未链接 / 重新创建
        for candidate in [item for item in scan["habit_candidates"] if item["link"] is None] + recreate:
            habit = candidate["habit"]
            link = candidate["link"]
            if link is not None:
                # 本地重新 active：删除旧链接与打卡基线，按新建处理。
                await run_in_threadpool(self._delete_link, str(link["external_id"]))
                claimed_remote.discard(str(link["external_id"]))
            if str(habit.get("status") or "") == "archived":
                warnings["archived_habit_not_pushed"] += 1
                continue
            note_id = str(candidate.get("dida_id") or "")
            remote = remote_by_id.get(note_id) if note_id else None
            if remote is not None and note_id not in claimed_remote:
                mapped = map_remote_habit(
                    remote, fallback_start=str(habit.get("start_date") or ""))
                same = first_link_matches(
                    local_habit_material(habit), mapped.remote_material,
                    mapped.present_keys)
                with self.database.connect() as connection:
                    if same:
                        self._save_synced_link(
                            connection, external_id=remote.id, habit=habit,
                            remote=remote, remote_material=mapped.remote_material,
                            remote_hash=mapped.remote_hash, payload=remote.raw)
                    else:
                        self._save_conflict_link(
                            connection, external_id=remote.id, habit=habit,
                            remote=remote, mapped=mapped,
                            reason="first_link_differs")
                claimed_remote.add(remote.id)
                counts["habits_created_pushed" if same else "habit_conflicts"] += 1
                continue
            # 按唯一同名且映射字段相同认领，避免重复创建。
            title = canonical_title(habit.get("title"))
            matches = [
                item for item in remote_by_id.values()
                if item.id not in claimed_remote
                and canonical_title(item.name) == title
            ]
            if len(matches) == 1:
                mapped = map_remote_habit(
                    matches[0], fallback_start=str(habit.get("start_date") or ""))
                if first_link_matches(
                        local_habit_material(habit), mapped.remote_material,
                        mapped.present_keys):
                    with self.database.connect() as connection:
                        self._save_synced_link(
                            connection, external_id=matches[0].id,
                            habit=habit, remote=matches[0],
                            remote_material=mapped.remote_material,
                            remote_hash=mapped.remote_hash,
                            payload=matches[0].raw)
                    if self.vault_dir is not None:
                        service.set_dida_links({str(habit["uid"]): matches[0].id})
                    claimed_remote.add(matches[0].id)
                    counts["habits_created_pushed"] += 1
                    continue
                with self.database.connect() as connection:
                    self._save_conflict_link(
                        connection, external_id=matches[0].id, habit=habit,
                        remote=matches[0], mapped=mapped,
                        reason="first_link_differs")
                claimed_remote.add(matches[0].id)
                counts["habit_conflicts"] += 1
                continue
            external = _new_external_id()
            payload = build_remote_habit_payload(habit, habit_id=external)
            with self.database.connect() as connection:
                self._upsert_link(connection, {
                    "external_id": external,
                    "uid": str(habit["uid"]),
                    "relpath": str(habit.get("path") or ""),
                    "last_title": str(habit.get("title") or ""),
                    "last_local_revision": int(habit.get("revision") or 0),
                    "last_local_mapped_json": local_material_json(habit),
                    "remote_payload_json": _json(payload),
                    "status": "pending_create",
                    "pending_remote_title": str(habit.get("title") or ""),
                    "pending_remote_payload_json": _json(payload),
                    "last_synced_at": _iso_now(),
                })
                connection.commit()
            claimed_remote.add(external)
            create_updates.append({
                "payload": payload, "habit": habit, "external": external,
            })
        # 新建
        for chunk in _chunks(create_updates, 100):
            result = await provider.add_habits([item["payload"] for item in chunk])
            etags = result.get("etags") or {}
            errors_map = result.get("errors") or {}
            for item in chunk:
                external = item["external"]
                code = str(errors_map.get(external) or "")
                if code:
                    counts["habits_errors"] += 1
                    errors.append({"code": code, "external_id": external})
                    continue
                habit = item["habit"]
                payload = item["payload"]
                remote = habit_from_payload(payload)
                mapped = map_remote_habit(
                    remote, fallback_start=str(habit.get("start_date") or ""))
                remote = dataclasses.replace(
                    remote, etag=str(etags.get(external) or remote.etag))
                payload = {**payload, "etag": remote.etag}
                with self.database.connect() as connection:
                    self._save_synced_link(
                        connection, external_id=external, habit=habit, remote=remote,
                        remote_material=mapped.remote_material,
                        remote_hash=mapped.remote_hash, payload=payload)
                if self.vault_dir is not None:
                    service.set_dida_links({str(habit["uid"]): external})
                counts["habits_created_pushed"] += 1
        # 更新
        for chunk in _chunks(update_updates, 100):
            result = await provider.update_habits([item["payload"] for item in chunk])
            etags = result.get("etags") or {}
            errors_map = result.get("errors") or {}
            for item in chunk:
                external = str(item["link"]["external_id"])
                code = str(errors_map.get(external) or "")
                if code:
                    counts["habits_errors"] += 1
                    errors.append({"code": code, "external_id": external})
                    continue
                habit = item["habit"]
                payload = item["payload"]
                remote = habit_from_payload(payload)
                mapped = map_remote_habit(
                    remote, fallback_start=str(habit.get("start_date") or ""))
                remote = dataclasses.replace(
                    remote, etag=str(etags.get(external) or remote.etag))
                payload = {**payload, "etag": remote.etag}
                with self.database.connect() as connection:
                    self._save_synced_link(
                        connection, external_id=external, habit=habit, remote=remote,
                        remote_material=mapped.remote_material,
                        remote_hash=mapped.remote_hash, payload=payload)
                counts["habits_updated_pushed"] += 1
        # pending_create：远端已有同 ID 时对账，否则复用同一 ID 重试。
        for candidate in pending_rows:
            habit = candidate["habit"]
            link = candidate["link"]
            external = str(link.get("external_id") or "")
            if not external:
                continue
            if external in remote_by_id:
                remote = remote_by_id[external]
                mapped = map_remote_habit(
                    remote, fallback_start=str(habit.get("start_date") or ""))
                same = first_link_matches(
                    local_habit_material(habit), mapped.remote_material,
                    mapped.present_keys)
                with self.database.connect() as connection:
                    if same:
                        self._save_synced_link(
                            connection, external_id=external, habit=habit,
                            remote=remote, remote_material=mapped.remote_material,
                            remote_hash=mapped.remote_hash, payload=remote.raw)
                    else:
                        self._save_conflict_link(
                            connection, external_id=external, habit=habit,
                            remote=remote, mapped=mapped,
                            reason="first_link_differs")
                if same and self.vault_dir is not None:
                    service.set_dida_links({str(habit["uid"]): external})
                counts["habits_created_pushed" if same else "habit_conflicts"] += 1
                continue
            payload = _load_json(link.get("pending_remote_payload_json"), {})
            if not isinstance(payload, dict) or not payload:
                continue
            result = await provider.add_habits([payload])
            if not (result.get("errors") or {}).get(external):
                remote = habit_from_payload(payload)
                remote = dataclasses.replace(
                    remote,
                    etag=str((result.get("etags") or {}).get(external) or remote.etag))
                payload = {**payload, "etag": remote.etag}
                mapped = map_remote_habit(
                    remote, fallback_start=str(habit.get("start_date") or ""))
                with self.database.connect() as connection:
                    self._save_synced_link(
                        connection, external_id=external, habit=habit,
                        remote=remote, remote_material=mapped.remote_material,
                        remote_hash=mapped.remote_hash, payload=payload)
                if self.vault_dir is not None:
                    service.set_dida_links({str(habit["uid"]): external})
                counts["habits_created_pushed"] += 1

    async def _push_checkins(self, provider, remote_by_id, remote_by_day,
                             counts, warnings, errors) -> None:
        scan = await run_in_threadpool(self._local_change_candidates)
        checkin_links = await run_in_threadpool(self._load_checkin_links)
        adds: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        deletes: list[dict[str, Any]] = []
        per_habit_deletes: Counter = Counter()
        per_habit_baseline: Counter = Counter()
        for (external, _day) in checkin_links:
            per_habit_baseline[external] += 1
        seen: set[tuple[str, str]] = set()
        for candidate in scan["checkin_candidates"]:
            external = candidate["external"]
            day = candidate["day"]
            if (external, day) in seen:
                continue
            seen.add((external, day))
            local_status = candidate["status"]
            baseline = candidate["baseline"]
            entry = candidate.get("entry") or {}
            entry_revision = int(entry.get("revision") or 0)
            remote = (remote_by_day.get(external) or {}).get(day)
            if local_status in ("done", "failed"):
                target_status = 2 if local_status == "done" else 1
                target_hash = remote_checkin_hash({"status": target_status})
                if remote is not None:
                    if int(remote.status) == target_status:
                        self._upsert_checkin_link(
                            external_id=external, day=day,
                            remote_id=str(remote.id), local_revision=entry_revision,
                            local_status=local_status, remote_hash=target_hash,
                            remote_status=target_status, payload=remote.raw)
                        counts["checkins_unchanged"] += 1
                    else:
                        updates.append({
                            "external": external, "day": day,
                            "status": local_status, "remote": remote,
                            "local_revision": entry_revision,
                        })
                else:
                    checkin_id = (
                        str(baseline.get("remote_checkin_id") or "")
                        if baseline else "") or _new_external_id()
                    payload = build_remote_checkin_payload(
                        {"status": local_status}, checkin_id=checkin_id,
                        habit_id=external, date_text=day)
                    self._upsert_checkin_link(
                        external_id=external, day=day, remote_id=checkin_id,
                        local_revision=entry_revision, local_status=local_status,
                        remote_hash="", remote_status=None, payload=payload,
                        status="pending_add")
                    adds.append({
                        "external": external, "day": day, "status": local_status,
                        "payload": payload, "local_revision": entry_revision,
                    })
            else:
                # skipped / unrecorded 都不在滴答保留打卡记录。
                if remote is not None:
                    per_habit_deletes[external] += 1
                    deletes.append({
                        "external": external, "day": day,
                        "checkin_id": str(remote.id),
                    })
                elif baseline is not None:
                    self._delete_checkin_link(external, day)
                else:
                    counts["checkins_skipped"] += 1
        # 批量删除保护：整月日志丢失时不能把远端历史一起删掉。
        safe_deletes: list[dict[str, Any]] = []
        for external, total in per_habit_deletes.items():
            baseline_count = max(int(per_habit_baseline.get(external, 0)), int(total))
            if total > MAX_CHECKIN_DELETES or (
                    baseline_count >= 5 and total / baseline_count > MASS_DELETE_RATIO):
                warnings["checkin_mass_delete_guard"] += 1
                continue
            safe_deletes.extend(item for item in deletes if item["external"] == external)
        deletes = safe_deletes
        for chunk in _chunks(adds, BATCH_SIZE):
            result = await provider.add_habit_checkins(
                [item["payload"] for item in chunk])
            errors_map = result.get("errors") or {}
            for item in chunk:
                checkin_id = str(item["payload"]["id"])
                if errors_map.get(checkin_id):
                    counts["checkins_errors"] += 1
                    errors.append({"code": str(errors_map[checkin_id]),
                                   "external_id": item["external"],
                                   "date": item["day"]})
                    continue
                self._upsert_checkin_link(
                    external_id=item["external"], day=item["day"],
                    remote_id=checkin_id,
                    local_revision=int(item.get("local_revision") or 0),
                    local_status=item["status"],
                    remote_hash=remote_checkin_hash(
                        {"status": 2 if item["status"] == "done" else 1}),
                    remote_status=2 if item["status"] == "done" else 1,
                    payload=item["payload"])
                counts["checkins_created_pushed"] += 1
        for chunk in _chunks(updates, BATCH_SIZE):
            payloads = []
            for item in chunk:
                payload = build_remote_checkin_payload(
                    {"status": item["status"]}, checkin_id=str(item["remote"].id),
                    habit_id=item["external"], date_text=item["day"],
                    base=item["remote"].raw)
                payloads.append((item, payload))
            result = await provider.update_habit_checkins([payload for _, payload in payloads])
            errors_map = result.get("errors") or {}
            for item, payload in payloads:
                if errors_map.get(str(item["remote"].id)):
                    counts["checkins_errors"] += 1
                    errors.append({"code": str(errors_map[str(item["remote"].id)]),
                                   "external_id": item["external"],
                                   "date": item["day"]})
                    continue
                self._upsert_checkin_link(
                    external_id=item["external"], day=item["day"],
                    remote_id=str(item["remote"].id),
                    local_revision=int(item.get("local_revision") or 0),
                    local_status=item["status"],
                    remote_hash=remote_checkin_hash(
                        {"status": 2 if item["status"] == "done" else 1}),
                    remote_status=2 if item["status"] == "done" else 1,
                    payload=payload)
                counts["checkins_updated_pushed"] += 1
        for chunk in _chunks(deletes, BATCH_SIZE):
            result = await provider.delete_habit_checkins(
                [item["checkin_id"] for item in chunk])
            errors_map = result.get("errors") or {}
            for item in chunk:
                if errors_map.get(item["checkin_id"]):
                    counts["checkins_errors"] += 1
                    errors.append({"code": str(errors_map[item["checkin_id"]]),
                                   "external_id": item["external"],
                                   "date": item["day"]})
                    continue
                self._delete_checkin_link(item["external"], item["day"])
                counts["checkins_deleted_pushed"] += 1

    async def push_deletions(self, *, force: bool = False) -> dict[str, Any]:
        if not self.enabled():
            return _disabled("habits_disabled")
        row = self.service._row()
        if self.read_only or not self.supported:
            return _disabled("read_only_or_unsupported")
        now = datetime.now(timezone.utc)
        if not force and self._delete_backoff_until and now < self._delete_backoff_until:
            return _disabled("backoff")
        if self._delete_guard.locked():
            return _disabled("running")
        scan = await run_in_threadpool(self._local_snapshot)
        _service, habits, _entries = scan
        local_uids = {str(habit["uid"]) for habit in habits}
        links = await run_in_threadpool(self._load_links)
        candidates = []
        for external, link in links.items():
            uid = str(link.get("uid") or "")
            if not uid or uid in local_uids:
                continue
            candidates.append({"external": external, "link": link})
        if not candidates:
            return {"enabled": True, "counts": {}, "warnings": [], "errors": []}
        if not row.get("push_deletes"):
            for item in candidates:
                await run_in_threadpool(
                    self._mark_link, item["external"], "local_missing",
                    "local_habit_deleted")
            return {"enabled": True, "counts": {}, "warnings": ["habit_local_missing"],
                    "errors": []}
        _cookie, csrf = self.service.credentials.load()
        if not csrf:
            return {"enabled": True, "counts": {}, "warnings": ["habit_push_csrf_required"],
                    "errors": [{"code": "csrf_required",
                                "message": "需要 _csrf_token 才能删除滴答习惯"}]}
        async with self._delete_guard:
            provider = None
            counts: Counter = Counter()
            warnings: Counter = Counter()
            errors: list[dict[str, str]] = []
            try:
                provider = self.service.build_provider()
                remote_ids = {str(item.id) for item in await provider.fetch_habits()}
                for item in candidates:
                    external = item["external"]
                    if external not in remote_ids:
                        await run_in_threadpool(self._delete_link, external)
                        counts["habits_deleted_pushed"] += 1
                        continue
                    result = await provider.delete_habits([external])
                    if (result.get("errors") or {}).get(external):
                        counts["habits_errors"] += 1
                        errors.append({"code": str(result["errors"][external]),
                                       "external_id": external})
                        continue
                    await run_in_threadpool(self._delete_link, external)
                    counts["habits_deleted_pushed"] += 1
            except DidaError as error:
                self._delete_backoff_until = now + timedelta(minutes=5)
                return {"enabled": True, "counts": dict(counts),
                        "warnings": sorted(warnings),
                        "errors": [{"code": error.code, "message": error.message}]}
            finally:
                if provider is not None:
                    await provider.aclose()
            self._delete_backoff_until = (
                datetime.now(timezone.utc) + timedelta(minutes=5) if errors else None)
            if counts:
                self.database.audit("dida_habit_delete_pushed", {"count": counts["habits_deleted_pushed"]})
            return {"enabled": True, "counts": dict(counts),
                    "warnings": sorted(warnings), "errors": errors}


def _chunks(items: list[dict[str, Any]], size: int):
    for index in range(0, len(items), size):
        yield items[index:index + size]


__all__ = ["DidaHabitSync", "HABIT_SCHEMA"]
