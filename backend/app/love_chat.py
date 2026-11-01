"""微信聊天（2026-11-01）：单联系人同步、日历看板、按日查看与导出。

数据落在 ``02_生活运营/01_恋爱系统/02_微信聊天/``：

- ``chat.db``：联系人、消息、同步日志；
- ``.credentials.json``：WeFlow 端口与加密 Token；
- ``media/``：从 WeFlow 本地 API 导出的图片/视频/语音/表情；
- ``exports/``：暂时保留导出目录（导出默认直接下载，不落盘）。

只访问 WeFlow 的 ``127.0.0.1`` 本地 API，不读微信数据库、不保存微信密码。
一个 chat.db 只绑定一个联系人；更换联系人必须显式确认，旧聊天会先备份再清空。
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .love_credentials import (
    CredentialError, clear_token, load_credentials, public_status, save_credentials,
)
from .love_layout import LovePaths
from .vault_connection import VaultReadOnlyError, ensure_vault_writable
from .weflow import (
    TZ, WeFlowClient, WeFlowError, WeFlowMessage, content_hash, iter_pages,
)

DAY = ZoneInfo("Asia/Shanghai")
MAX_MEDIA_BYTES = 64 * 1024 * 1024
MAX_MEDIA_PER_SYNC = 2_000
MEDIA_TYPES = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image",
    ".webp": "image", ".bmp": "image", ".heic": "image", ".avif": "image",
    ".mp4": "video", ".mov": "video", ".m4v": "video", ".webm": "video",
    ".mp3": "voice", ".wav": "voice", ".m4a": "voice", ".amr": "voice",
    ".silk": "voice", ".ogg": "voice",
}
SYNC_LOCK = threading.Lock()
_SYNC_STARTED = object()


class ChatError(RuntimeError):
    pass


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contact (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    session_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    me_sender TEXT NOT NULL DEFAULT '',
    partner_sender TEXT NOT NULL DEFAULT '',
    avatar TEXT NOT NULL DEFAULT '',
    watermark INTEGER NOT NULL DEFAULT 0,
    history_start_day TEXT NOT NULL DEFAULT '',
    last_sync_day TEXT NOT NULL DEFAULT '',
    last_sync_at TEXT NOT NULL DEFAULT '',
    last_sync_status TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform_message_id TEXT NOT NULL DEFAULT '',
    sender_id TEXT NOT NULL DEFAULT '',
    sender TEXT NOT NULL DEFAULT '',
    is_send INTEGER NOT NULL DEFAULT 0,
    message_type INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL DEFAULT '',
    day TEXT NOT NULL,
    ts INTEGER NOT NULL,
    media_path TEXT NOT NULL DEFAULT '',
    media_kind TEXT NOT NULL DEFAULT '',
    reply_to_message_id TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_love_chat_day ON messages(day);
CREATE INDEX IF NOT EXISTS ix_love_chat_ts ON messages(ts);
CREATE TABLE IF NOT EXISTS sync_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    total INTEGER NOT NULL DEFAULT 0,
    fetched INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    matched INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    media_failed INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    warnings TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT ''
);
"""

# 旧库补齐同步进度字段；total=0 表示未知总量（前端显示不确定进度条）。
_SYNC_LOG_COLUMNS = {
    "total": "INTEGER NOT NULL DEFAULT 0",
}


def _now() -> str:
    return datetime.now(DAY).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(DAY).date().isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def ensure_chat_schema(path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _connect(target) as connection:
        connection.executescript(SCHEMA_SQL)
        existing = {str(row["name"])
                    for row in connection.execute("PRAGMA table_info(sync_logs)")}
        for name, sql_type in _SYNC_LOG_COLUMNS.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE sync_logs ADD COLUMN {name} {sql_type}")
    return target


def _row_json(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _contact(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM contact WHERE id = 1").fetchone()


def _contact_json(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "session_id": row["session_id"],
        "display_name": row["display_name"],
        "me_sender": row["me_sender"],
        "partner_sender": row["partner_sender"],
        "avatar": row["avatar"],
        "watermark": int(row["watermark"] or 0),
        "history_start_day": row["history_start_day"],
        "last_sync_day": row["last_sync_day"],
        "last_sync_at": row["last_sync_at"],
        "last_sync_status": row["last_sync_status"],
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _message_day(message: WeFlowMessage) -> str:
    return message.timestamp.date().isoformat()


def _message_ts(message: WeFlowMessage) -> int:
    return int(message.timestamp.replace(tzinfo=TZ).timestamp())


def _media_ext(media_path: str) -> str:
    suffix = Path(str(media_path or "")).suffix.lower()
    return suffix if suffix in MEDIA_TYPES else ""


def _media_kind_from_path(media_path: str) -> str:
    ext = _media_ext(media_path)
    if ext:
        return MEDIA_TYPES[ext]
    lowered = str(media_path or "").lower()
    if "/images/" in lowered:
        return "image"
    if "/videos/" in lowered:
        return "video"
    if "/voices/" in lowered:
        return "voice"
    if "/emojis/" in lowered:
        return "emoji"
    return "file"


def _media_kind(message: WeFlowMessage) -> str:
    ext = _media_ext(message.media_path)
    if ext:
        return MEDIA_TYPES[ext]
    if message.message_type == 1:
        return "image"
    if message.message_type == 3:
        return "video"
    if message.message_type == 2:
        return "voice"
    if message.message_type == 5:
        return "emoji"
    return _media_kind_from_path(message.media_path)


def _placeholder(message: WeFlowMessage) -> str:
    return {
        1: "[图片]", 2: "[语音]", 3: "[视频]", 5: "[表情]",
        7: "[链接/文件]", 8: "[位置]", 23: "[通话]", 27: "[名片]",
        80: "[系统消息]", 99: "[其他消息]",
    }.get(int(message.message_type or 0), "[消息]")


def _media_available(media_path: str) -> bool:
    value = str(media_path or "").strip()
    if not value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def _message_json(row: sqlite3.Row) -> dict[str, Any]:
    available = _media_available(row["media_path"])
    return {
        "id": int(row["id"]),
        "sender": row["sender"],
        "is_send": bool(row["is_send"]),
        "type": int(row["message_type"] or 0),
        "content": row["content"],
        "date": row["day"],
        "timestamp": int(row["ts"] or 0),
        "time": datetime.fromtimestamp(int(row["ts"] or 0), DAY).strftime("%H:%M"),
        "media_kind": row["media_kind"] if available else "",
        "has_media": available,
        "media_url": (f"/api/love/chat/media/{int(row['id'])}" if available else ""),
        "reply_to_message_id": row["reply_to_message_id"],
    }


def _insert_message(connection: sqlite3.Connection, message: WeFlowMessage, *,
                    media_relative: str = "") -> str:
    digest = content_hash(message)
    exists = connection.execute(
        "SELECT 1 FROM messages WHERE content_hash = ? LIMIT 1", (digest,)
    ).fetchone()
    if exists:
        return "duplicate"
    platform_id = str(message.platform_message_id or "").strip()
    if platform_id:
        matched = connection.execute(
            "SELECT id FROM messages WHERE platform_message_id = ? LIMIT 1", (platform_id,)
        ).fetchone()
        if matched:
            return "matched"
    content = str(message.content or "").strip() or _placeholder(message)
    connection.execute(
        """INSERT INTO messages
        (platform_message_id, sender_id, sender, is_send, message_type, content,
         day, ts, media_path, media_kind, reply_to_message_id, content_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            platform_id, str(message.sender_id or "")[:100], str(message.sender or "")[:100],
            1 if message.is_send else 0, int(message.message_type or 0), content[:20_000],
            _message_day(message), _message_ts(message), media_relative,
            _media_kind(message) if media_relative else "",
            str(message.reply_to_message_id or "")[:64], digest, _now(),
        ),
    )
    return "inserted"


def _find_session(client: WeFlowClient, session_id: str) -> dict[str, Any] | None:
    try:
        sessions = client.sessions(keyword="", limit=1000, include_groups=False)
    except WeFlowError:
        return None
    for item in sessions:
        if item.id == session_id:
            return {
                "id": item.id, "name": item.name, "type": item.type,
                "message_count": item.message_count, "last_message_at": item.last_message_at,
            }
    return None


def _backup_chat_db(paths: LovePaths, label: str) -> Path | None:
    if not paths.chat_db.is_file():
        return None
    target = paths.archive / "聊天备份" / f"{label}-{datetime.now(DAY).strftime('%Y%m%d-%H%M%S')}.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    with _connect(paths.chat_db) as source, _connect(target) as destination:
        source.backup(destination)
    return target


def _save_media(client: WeFlowClient, message: WeFlowMessage, media_dir: Path,
                *, day: str) -> tuple[str, str | None]:
    """下载一条媒体，返回 ``(相对路径, 错误)``。

    失败不抛出：消息仍会入库，页面显示占位；错误累计到同步日志。
    """
    relative_source = str(message.media_path or "").strip()
    if not relative_source:
        return "", None
    ext = _media_ext(relative_source) or ".bin"
    name = hashlib.sha256(
        f"{message.platform_message_id}|{message.timestamp.isoformat()}|{relative_source}".encode("utf-8")
    ).hexdigest()[:32] + ext
    target_dir = media_dir / day[:4] / day[5:7]
    target = target_dir / name
    if target.is_file():
        return target.relative_to(media_dir).as_posix(), None
    try:
        data = client.download_media(relative_source, max_bytes=MAX_MEDIA_BYTES)
    except (WeFlowError, OSError) as error:
        return "", f"媒体下载失败（{relative_source[:80]}）：{error}"[:300]
    if not data:
        return "", f"媒体为空（{relative_source[:80]}）"
    target_dir.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    temporary.write_bytes(data)
    os.replace(temporary, target)
    return target.relative_to(media_dir).as_posix(), None


def _update_log(connection: sqlite3.Connection, log_id: int, **values: Any) -> None:
    if not values:
        return
    columns = ", ".join(f"{key} = ?" for key in values)
    connection.execute(
        f"UPDATE sync_logs SET {columns} WHERE id = ?",
        (*[values[key] for key in values], log_id),
    )
    connection.commit()


def _run_sync(paths: LovePaths, log_id: int, mode: str, full: bool) -> None:
    """线程入口：无论成功、失败还是提前返回，都释放同步锁。"""
    try:
        _run_sync_body(paths, log_id, mode, full)
    finally:
        try:
            SYNC_LOCK.release()
        except RuntimeError:
            pass


def _remote_message_total(client: WeFlowClient, session_id: str) -> int:
    """WeFlow 会话列表里的 messageCount；拿不到就当未知（0）。"""
    try:
        sessions = client.sessions(limit=1000)
    except (WeFlowError, OSError, ValueError):
        return 0
    for session in sessions:
        if session.id == session_id:
            try:
                return max(0, int(session.message_count or 0))
            except (TypeError, ValueError):
                return 0
    return 0


def _run_sync_body(paths: LovePaths, log_id: int, mode: str, full: bool) -> None:
    try:
        credentials = load_credentials(paths.chat_credentials)
    except CredentialError as error:
        with _connect(paths.chat_db) as connection:
            _update_log(connection, log_id, status="failed", finished_at=_now(),
                        error=str(error)[:500])
        return
    if not credentials.get("token"):
        with _connect(paths.chat_db) as connection:
            _update_log(connection, log_id, status="failed", finished_at=_now(),
                        error="还没有配置 WeFlow Access Token")
        return
    with _connect(paths.chat_db) as connection:
        contact = _contact(connection)
        if contact is None:
            _update_log(connection, log_id, status="failed", finished_at=_now(),
                        error="还没有绑定微信联系人")
            return
        session_id = str(contact["session_id"] or "")
        watermark = int(contact["watermark"] or 0)
        me_sender = str(contact["me_sender"] or "")
        history_start = str(contact["history_start_day"] or "")
    if not session_id:
        with _connect(paths.chat_db) as connection:
            _update_log(connection, log_id, status="failed", finished_at=_now(),
                        error="还没有绑定微信联系人")
        return

    full_scan = bool(full) or watermark <= 0
    since = 0 if full_scan else max(0, watermark - 120)
    inserted = matched = duplicates = fetched = media_failed = 0
    warnings: list[str] = []
    media_saved = 0
    max_ts = watermark
    earliest_day = ""
    client: WeFlowClient | None = None
    try:
        client = WeFlowClient(credentials["token"], credentials["port"])
        client.health()
        remote_total = _remote_message_total(client, session_id)
        with _connect(paths.chat_db) as connection:
            local_total = int(connection.execute(
                "SELECT COUNT(*) AS count FROM messages").fetchone()["count"] or 0)
            if remote_total > 0:
                expected_total = (remote_total if full_scan
                                  else max(remote_total - local_total, 0))
            elif full_scan and history_start:
                # WeFlow 有些版本不返回 messageCount。已经完成过一次全量扫描时，
                # 本地消息数就是下一轮全量扫描的可靠估算；首次同步则保持未知。
                expected_total = local_total
            else:
                expected_total = 0
            _update_log(connection, log_id, total=expected_total)
        for page in iter_pages(client, session_id, since=since, retry_empty=True):
            fetched += len(page.messages)
            with _connect(paths.chat_db) as connection:
                for message in page.messages:
                    max_ts = max(max_ts, _message_ts(message))
                    day = _message_day(message)
                    if not earliest_day or day < earliest_day:
                        earliest_day = day
                    media_relative = ""
                    if mode == "apply" and message.media_path and media_saved < MAX_MEDIA_PER_SYNC:
                        media_relative, media_error = _save_media(
                            client, message, paths.chat_media, day=day)
                        if media_relative:
                            media_saved += 1
                        elif media_error:
                            media_failed += 1
                            if len(warnings) < 50:
                                warnings.append(media_error)
                    if mode != "apply":
                        # 预览只读：不写消息，但按内容哈希判断会新增还是会重复。
                        digest = content_hash(message)
                        exists = connection.execute(
                            "SELECT 1 FROM messages WHERE content_hash = ? LIMIT 1", (digest,)
                        ).fetchone()
                        if exists:
                            duplicates += 1
                        else:
                            inserted += 1
                        continue
                    outcome = _insert_message(connection, message, media_relative=media_relative)
                    if outcome == "inserted":
                        inserted += 1
                    elif outcome == "matched":
                        matched += 1
                    else:
                        duplicates += 1
                _update_log(
                    connection, log_id, fetched=fetched, inserted=inserted,
                    matched=matched, duplicates=duplicates, media_failed=media_failed,
                    warnings="\n".join(warnings)[:4000],
                )
        if mode == "apply":
            today = _today()
            with _connect(paths.chat_db) as connection:
                contact = _contact(connection)
                if contact is not None:
                    history_start = str(contact["history_start_day"] or "")
                    if full or not history_start:
                        history_start = earliest_day or today
                    connection.execute(
                        """UPDATE contact SET watermark = ?, history_start_day = ?,
                        last_sync_day = ?, last_sync_at = ?, last_sync_status = 'ok',
                        last_error = '', updated_at = ? WHERE id = 1""",
                        (max_ts, history_start, today, _now(), _now()),
                    )
                    connection.commit()
        with _connect(paths.chat_db) as connection:
            _update_log(
                connection, log_id, status="completed", finished_at=_now(),
                fetched=fetched, inserted=inserted, matched=matched,
                duplicates=duplicates, media_failed=media_failed,
                warnings="\n".join(warnings)[:4000],
            )
    except (WeFlowError, OSError, sqlite3.Error, ValueError) as error:
        with _connect(paths.chat_db) as connection:
            _update_log(connection, log_id, status="failed", finished_at=_now(),
                        error=str(error)[:1000], fetched=fetched, inserted=inserted,
                        matched=matched, duplicates=duplicates,
                        media_failed=media_failed, warnings="\n".join(warnings)[:4000])
            if mode == "apply":
                connection.execute(
                    "UPDATE contact SET last_sync_status = 'failed', last_error = ?, "
                    "updated_at = ? WHERE id = 1", (str(error)[:1000], _now()))
                connection.commit()
    finally:
        if client is not None:
            client.close()


def _month_days(month: str) -> list[date]:
    year, month_number = (int(part) for part in month.split("-"))
    first = date(year, month_number, 1)
    if month_number == 12:
        following = date(year + 1, 1, 1)
    else:
        following = date(year, month_number + 1, 1)
    return [first + timedelta(days=offset) for offset in range((following - first).days)]


def _calendar_payload(connection: sqlite3.Connection, month: str) -> dict[str, Any]:
    contact = _contact(connection)
    info = _contact_json(contact)
    counts = {
        row["day"]: int(row["count"])
        for row in connection.execute(
            "SELECT day, COUNT(*) AS count FROM messages GROUP BY day")
    }
    today = _today()
    last_sync_day = str(info.get("last_sync_day") or "")
    history_start = str(info.get("history_start_day") or "")
    days: list[dict[str, Any]] = []
    for current in _month_days(month):
        key = current.isoformat()
        count = counts.get(key, 0)
        if key > today:
            state = "future"
        elif not last_sync_day:
            state = "no_chat" if count == 0 else "synced"
        elif key > last_sync_day:
            state = "unsynced"
        elif history_start and key < history_start:
            state = "unsynced"
        elif count:
            state = "synced"
        else:
            state = "no_chat"
        if key == today and info.get("last_sync_status") == "failed" and state == "unsynced":
            state = "unsynced"
        days.append({
            "date": key,
            "day": current.day,
            "weekday": ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[current.weekday()],
            "count": count,
            "state": state,
            "is_today": key == today,
        })
    return {
        "month": month,
        "today": today,
        "contact": info,
        "days": days,
        "total_days": sum(1 for day in days if day["state"] == "synced"),
        "unsynced_days": sum(1 for day in days if day["state"] == "unsynced"),
        "no_chat_days": sum(1 for day in days if day["state"] == "no_chat"),
    }


def _parse_log_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=DAY)


def _sync_log_json(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    payload = dict(row)
    payload["warnings"] = [line for line in str(payload.get("warnings") or "").splitlines() if line]
    status = str(payload.get("status") or "")
    total = int(payload.get("total") or 0)
    fetched = int(payload.get("fetched") or 0)
    started = _parse_log_time(payload.get("started_at"))
    finished = _parse_log_time(payload.get("finished_at"))
    payload["progress_percent"] = None
    payload["rate_per_second"] = None
    payload["eta_seconds"] = None
    if status == "completed":
        payload["progress_percent"] = 100.0
        if started is not None:
            elapsed = max((finished or datetime.now(DAY)) - started, timedelta(0)).total_seconds()
            if elapsed > 0:
                payload["rate_per_second"] = round(fetched / elapsed, 1)
        return payload
    if status != "running" or started is None:
        return payload
    elapsed = max((datetime.now(DAY) - started).total_seconds(), 0.0)
    rate = fetched / elapsed if elapsed > 0 and fetched > 0 else 0.0
    if rate > 0:
        payload["rate_per_second"] = round(rate, 1)
    if total > 0:
        payload["progress_percent"] = round(min(99.0, fetched / total * 100), 1)
        if rate > 0:
            payload["eta_seconds"] = max(0, int(round(max(total - fetched, 0) / rate)))
    return payload


def _export_messages(connection: sqlite3.Connection, *, day: str = "",
                     fmt: str = "txt") -> tuple[str, str, str]:
    if day:
        rows = connection.execute(
            "SELECT * FROM messages WHERE day = ? ORDER BY ts ASC, id ASC", (day,)
        ).fetchall()
        label = day
    else:
        rows = connection.execute(
            "SELECT * FROM messages ORDER BY ts ASC, id ASC").fetchall()
        label = "全部"
    if fmt == "json":
        payload = [
            {
                "platform_message_id": row["platform_message_id"],
                "sender": row["sender"],
                "is_send": bool(row["is_send"]),
                "type": int(row["message_type"] or 0),
                "content": row["content"],
                "timestamp": int(row["ts"] or 0),
                "time": datetime.fromtimestamp(int(row["ts"] or 0), DAY).isoformat(timespec="seconds"),
                "media_kind": row["media_kind"],
                "reply_to_message_id": row["reply_to_message_id"],
            }
            for row in rows
        ]
        return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                "application/json; charset=utf-8", f"微信聊天-{label}.json")
    lines: list[str] = []
    current_day = ""
    for row in rows:
        if row["day"] != current_day:
            current_day = row["day"]
            lines.append(f"\n===== {current_day} =====\n")
        stamp = datetime.fromtimestamp(int(row["ts"] or 0), DAY).strftime("%H:%M:%S")
        lines.append(f"[{stamp}] {row['sender']}: {row['content']}")
    text = "\n".join(lines).strip() + "\n" if lines else "还没有聊天记录。\n"
    return text, "text/plain; charset=utf-8", f"微信聊天-{label}.txt"


class SettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    port: int = Field(default=5031, ge=1, le=65535)
    token: str = Field(default="", max_length=400)


class BindPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    session_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(default="", max_length=200)
    me_sender: str = Field(default="我", max_length=100)
    replace: bool = False


class SyncPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: str = Field(default="apply", pattern=r"^(preview|apply)$")
    full: bool = False


def create_chat_router(paths: LovePaths, *, read_only: bool = False,
                       prefix: str = "/api/love/chat") -> APIRouter:
    if not read_only:
        ensure_chat_schema(paths.chat_db)
    router = APIRouter(prefix=prefix, tags=["love-chat"])

    def connection() -> sqlite3.Connection:
        if not paths.chat_db.is_file():
            if read_only:
                raise HTTPException(404, "还没有聊天数据")
            ensure_chat_schema(paths.chat_db)
        return _connect(paths.chat_db)

    def client() -> tuple[WeFlowClient, dict[str, Any]]:
        try:
            credentials = load_credentials(paths.chat_credentials)
        except CredentialError as error:
            raise HTTPException(409, str(error)) from error
        if not credentials.get("token"):
            raise HTTPException(409, "还没有配置 WeFlow Access Token")
        return WeFlowClient(credentials["token"], credentials["port"]), credentials

    @router.get("/status")
    def status():
        credentials = public_status(paths.chat_credentials)
        with connection() as con:
            contact = _contact(con)
            counts = con.execute(
                "SELECT COUNT(*) AS total, MIN(day) AS first_day, MAX(day) AS last_day "
                "FROM messages").fetchone()
            last_log = con.execute(
                "SELECT * FROM sync_logs ORDER BY id DESC LIMIT 1").fetchone()
            log = _sync_log_json(last_log)
        payload = {
            "contact": _contact_json(contact),
            "total": int(counts["total"] or 0),
            "first_day": counts["first_day"] or "",
            "last_day": counts["last_day"] or "",
            "last_log": log,
            **credentials,
            "paths": {"db": str(paths.chat_db), "media": str(paths.chat_media)},
        }
        if credentials.get("token_configured"):
            probe: WeFlowClient | None = None
            try:
                probe = WeFlowClient(
                    load_credentials(paths.chat_credentials)["token"], credentials["port"],
                    timeout=5.0)
                probe.health()
                payload["weflow_ok"] = True
            except Exception as error:  # noqa: BLE001 - 状态检查失败要显示原因
                payload["weflow_ok"] = False
                payload["weflow_error"] = str(error)[:500]
            finally:
                if probe is not None:
                    probe.close()
        return payload

    @router.put("/settings")
    def settings(payload: SettingsPayload):
        try:
            result = save_credentials(
                paths.chat_credentials, port=payload.port, token=payload.token)
        except CredentialError as error:
            raise HTTPException(422, str(error)) from error
        if result.get("token_configured"):
            probe: WeFlowClient | None = None
            try:
                credentials = load_credentials(paths.chat_credentials)
                probe = WeFlowClient(credentials["token"], credentials["port"], timeout=10.0)
                probe.health()
            except (WeFlowError, CredentialError) as error:
                raise HTTPException(409, f"WeFlow 连接失败：{error}") from error
            finally:
                if probe is not None:
                    probe.close()
        return {**result, "weflow_ok": bool(result.get("token_configured"))}

    @router.delete("/token")
    def remove_token():
        try:
            return clear_token(paths.chat_credentials)
        except CredentialError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/sessions")
    def sessions(keyword: str = Query(default="", max_length=100)):
        flow, _credentials = client()
        try:
            items = flow.sessions(keyword=keyword, limit=1000, include_groups=False)
        except WeFlowError as error:
            raise HTTPException(503, str(error)) from error
        finally:
            flow.close()
        return {"items": [
            {"id": item.id, "name": item.name, "type": item.type,
             "message_count": item.message_count, "last_message_at": item.last_message_at}
            for item in items
        ]}

    @router.post("/bind")
    def bind(payload: BindPayload):
        try:
            ensure_vault_writable(read_only, "绑定微信联系人")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        flow, _credentials = client()
        try:
            found = _find_session(flow, payload.session_id)
        except WeFlowError as error:
            raise HTTPException(503, str(error)) from error
        finally:
            flow.close()
        display_name = payload.display_name.strip() or (found or {}).get("name") or payload.session_id
        with connection() as con:
            current = _contact(con)
            current_session = str(current["session_id"] or "").strip() if current is not None else ""
            if current is not None and current_session and current_session != payload.session_id:
                if not payload.replace:
                    raise HTTPException(
                        409, "已经绑定了一个联系人；换人需要先确认，旧聊天会先备份再清空")
                backup = _backup_chat_db(paths, "换绑前")
                con.execute("DELETE FROM messages")
                con.execute("DELETE FROM contact")
                con.commit()
                if backup is not None:
                    try:
                        backup_note = backup.relative_to(paths.root.parent.parent)
                    except ValueError:
                        backup_note = backup
                else:
                    backup_note = ""
            else:
                backup_note = ""
            now = _now()
            con.execute(
                """INSERT INTO contact
                (id, session_id, display_name, me_sender, partner_sender, avatar,
                 watermark, history_start_day, last_sync_day, last_sync_at,
                 last_sync_status, last_error, created_at, updated_at)
                VALUES (1, ?, ?, ?, ?, '', 0, '', '', '', '', '', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  session_id = excluded.session_id,
                  display_name = excluded.display_name,
                  me_sender = excluded.me_sender,
                  partner_sender = excluded.partner_sender,
                  updated_at = excluded.updated_at""",
                (payload.session_id, display_name[:200], payload.me_sender[:100],
                 display_name[:100], now, now),
            )
            con.commit()
            contact = _contact(con)
        return {"contact": _contact_json(contact), "backup": str(backup_note)}

    @router.delete("/bind")
    def unbind():
        with connection() as con:
            con.execute("DELETE FROM contact")
            con.commit()
        return {"unbound": True}

    @router.post("/sync")
    def sync(payload: SyncPayload):
        try:
            ensure_vault_writable(read_only, "同步微信聊天")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        with connection() as con:
            contact = _contact(con)
            if contact is None:
                raise HTTPException(409, "还没有绑定微信联系人")
            if not SYNC_LOCK.acquire(blocking=False):
                raise HTTPException(409, "已有同步任务在进行，请等它完成")
            now = _now()
            cursor = con.execute(
                "INSERT INTO sync_logs (mode, status, started_at) VALUES (?, 'running', ?)",
                (payload.mode, now),
            )
            log_id = int(cursor.lastrowid)
            con.commit()
        try:
            thread = threading.Thread(
                target=_run_sync, args=(paths, log_id, payload.mode, payload.full),
                daemon=True, name=f"love-chat-sync-{log_id}",
            )
            thread.start()
        except Exception:
            SYNC_LOCK.release()
            raise
        return {"log_id": log_id, "mode": payload.mode, "status": "running"}

    @router.get("/sync/{log_id}")
    def sync_status(log_id: int):
        with connection() as con:
            row = con.execute("SELECT * FROM sync_logs WHERE id = ?", (log_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "同步日志不存在")
            return _sync_log_json(row)

    @router.get("/calendar")
    def calendar(month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
        with connection() as con:
            return _calendar_payload(con, month)

    @router.get("/day")
    def day(target_date: str = Query(alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
            limit: int = Query(default=5000, ge=1, le=20_000)):
        with connection() as con:
            rows = con.execute(
                "SELECT * FROM messages WHERE day = ? ORDER BY ts ASC, id ASC LIMIT ?",
                (target_date, limit)).fetchall()
            return {
                "date": target_date,
                "count": len(rows),
                "messages": [_message_json(row) for row in rows],
            }

    @router.get("/media/{message_id}")
    def media(message_id: int):
        with connection() as con:
            row = con.execute(
                "SELECT media_path, media_kind FROM messages WHERE id = ?",
                (message_id,)).fetchone()
            if row is None or not row["media_path"]:
                raise HTTPException(404, "这条消息没有媒体")
            relative = Path(str(row["media_path"]))
            if relative.is_absolute() or ".." in relative.parts:
                raise HTTPException(409, "媒体路径不安全")
            target = (paths.chat_media / relative).resolve()
            if not target.is_relative_to(paths.chat_media.resolve()):
                raise HTTPException(409, "媒体路径不在聊天目录内")
            if not target.is_file():
                raise HTTPException(404, "媒体文件不存在")
            media_type, _ = mimetypes.guess_type(target.name)
            return FileResponse(target, media_type=media_type or "application/octet-stream",
                                headers={"Cache-Control": "private, max-age=86400"})

    @router.get("/export")
    def export(day_filter: str = Query(default="all", alias="date"),
               fmt: str = Query(default="txt", pattern=r"^(txt|json)$")):
        day_value = "" if day_filter == "all" else day_filter
        if day_value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_value):
            raise HTTPException(422, "日期格式应为 YYYY-MM-DD 或 all")
        with connection() as con:
            text, media_type, filename = _export_messages(con, day=day_value, fmt=fmt)
        return PlainTextResponse(
            text, media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )

    return router


__all__ = ["ensure_chat_schema", "create_chat_router", "SCHEMA_SQL"]
