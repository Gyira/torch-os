"""恋爱系统 v1 → v2 数据迁移（2026-11-01）。

旧数据在 ``<个人数据库>/.torch-os/love/``，新数据在
``02_生活运营/01_恋爱系统/``。迁移原则：

- 先备份旧目录到 ``01_恋爱系统/_归档/legacy-love-<时间>/``，原文件不删除；
- 聊天项目只保留最近活跃的一个作为“我的对象”，其余项目写入归档索引；
- 日志转成 Markdown（恋爱日志本子），分析/画像转成恋爱大师历史；
- 排班表、值班备注、经期库、八字 JSON、WeFlow Token 分别搬到对应页面目录；
- 幂等：按内容哈希/标题/日期去重，重复启动不会产生重复笔记或重复记录。
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import love_bazi, love_chat, love_gallery, love_master, love_schedule, love_memory, pillar_notes, vault_layout
from .db import Database
from .love_credentials import CredentialError, save_credentials
from .love_layout import (
    ARCHIVE_DIR, BAZI_DB, CHAT_DB, CYCLE_DB, LEGACY_DB, LEGACY_KEY,
    LovePaths, ensure_love_layout, love_paths,
)
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

DAY = ZoneInfo("Asia/Shanghai")
SCHEMA = 2
MARKER = "migration-v2.json"
TZ_TEXT = "%Y-%m-%d %H:%M:%S"


def _now() -> str:
    return datetime.now(DAY).isoformat(timespec="seconds")


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _legacy_roots(database_path: Path, extra: list[Path] | None = None) -> list[Path]:
    roots: list[Path] = []
    explicit = (os.environ.get("LOVE_DATA_DIR") or "").strip()
    if explicit:
        roots.append(Path(explicit).expanduser())
    roots.append(Path(database_path).resolve().parent / "love")
    roots.extend(Path(item).expanduser() for item in (extra or []))
    unique: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _copy_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        return
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.is_file():
            shutil.copy2(item, target / item.name)
        elif item.is_dir() and not item.is_symlink():
            _copy_tree(item, target / item.name)


def _copy_sqlite(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(source)) as source_db, sqlite3.connect(str(target)) as target_db:
        source_db.backup(target_db)


def _backup_legacy(root: Path, paths: LovePaths) -> Path | None:
    if not root.is_dir():
        return None
    target = paths.archive / f"legacy-love-{datetime.now(DAY).strftime('%Y%m%d-%H%M%S')}"
    target.mkdir(parents=True, exist_ok=True)
    for name in (LEGACY_DB, LEGACY_KEY, "cycle.db", "bazi.json", "schedule-notes.json"):
        source = root / name
        if not source.is_file():
            continue
        if name in (LEGACY_DB, "cycle.db"):
            try:
                _copy_sqlite(source, target / name)
                continue
            except sqlite3.Error:
                pass
        shutil.copy2(source, target / name)
    _copy_tree(root / "xlsx", target / "xlsx")
    return target


def _decrypt_legacy(legacy: Path, value: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key_path = legacy / LEGACY_KEY
    if not key_path.is_file():
        raise CredentialError("旧版密钥文件不存在")
    key = key_path.read_bytes()
    if len(key) != 32:
        raise CredentialError("旧版密钥文件长度不正确")
    raw = base64.b64decode(value)
    return AESGCM(key).decrypt(raw[:12], raw[12:], None).decode("utf-8")


def _migrate_weflow_token(legacy: Path, paths: LovePaths, connection: sqlite3.Connection,
                          report: dict[str, Any]) -> None:
    if not _table_exists(connection, "settings"):
        return
    token_row = connection.execute(
        "SELECT value FROM settings WHERE key='weflow_token' ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    port_row = connection.execute(
        "SELECT value FROM settings WHERE key='weflow_port' ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    try:
        port = int(port_row["value"]) if port_row and port_row["value"] else 5031
    except (TypeError, ValueError):
        port = 5031
    if token_row and token_row["value"]:
        try:
            token = _decrypt_legacy(legacy, str(token_row["value"]))
            save_credentials(paths.chat_credentials, port=port, token=token,
                             keep_existing=False)
            report["weflow_token"] = "copied"
        except Exception as error:  # noqa: BLE001 - 迁移失败保留旧文件
            report["weflow_token"] = f"failed: {str(error)[:200]}"
    elif paths.chat_credentials.is_file():
        report["weflow_token"] = "kept"


def _ensure_legacy_message_columns(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "messages"):
        return
    existing = {str(row["name"]) for row in connection.execute("PRAGMA table_info(messages)")}
    columns = {
        "platform_message_id": "VARCHAR(64)",
        "sender_id": "VARCHAR(100)",
        "message_type": "INTEGER",
        "is_send": "BOOLEAN",
        "reply_to_message_id": "VARCHAR(64)",
        "media_path": "TEXT",
        "source": "VARCHAR(20)",
    }
    changed = False
    for name, sql_type in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE messages ADD COLUMN {name} {sql_type}")
            changed = True
    if changed:
        connection.commit()


def _retain_project(connection: sqlite3.Connection) -> sqlite3.Row | None:
    if not _table_exists(connection, "projects"):
        return None
    return connection.execute(
        "SELECT * FROM projects ORDER BY COALESCE(updated_at, created_at) DESC "
        "LIMIT 1").fetchone()


def _migrate_chat(legacy: Path, paths: LovePaths, connection: sqlite3.Connection,
                  report: dict[str, Any]) -> None:
    _ensure_legacy_message_columns(connection)
    project = _retain_project(connection)
    if project is None:
        return
    project_id = str(project["id"])
    source = None
    if _table_exists(connection, "project_sources"):
        source = connection.execute(
            "SELECT * FROM project_sources WHERE project_id=?", (project_id,)).fetchone()
    me_sender = str((source["me_sender"] if source is not None else "") or project["partner_a"] or "我")
    partner_name = str(project["partner_b"] or "对象")
    if source is not None and source["partner_sender"]:
        partner_name = str(source["partner_sender"])
    if me_sender == partner_name:
        partner_name = str(project["partner_a"] or "对象")
    # 纯文本导入没有 WeFlow 会话：留空，让微信聊天页提示重新绑定；
    # 不要伪造 `legacy:...` 会话 ID，否则同步会去请求一个不存在的会话。
    session_id = str((source["session_id"] if source is not None else "") or "")
    watermark = int((source["watermark"] if source is not None else 0) or 0)
    last_sync_at = str((source["last_sync_at"] if source is not None else "") or "")
    with love_chat._connect(paths.chat_db) as chat:
        existing = chat.execute("SELECT 1 FROM contact WHERE id=1").fetchone()
        if existing is None:
            if _table_exists(connection, "messages"):
                rows = connection.execute(
                    "SELECT * FROM messages WHERE project_id=? ORDER BY timestamp ASC",
                    (project_id,)).fetchall()
            else:
                rows = []
            inserted = 0
            earliest = ""
            max_ts = watermark
            for row in rows:
                timestamp = str(row["timestamp"] or "")[:19]
                try:
                    parsed = datetime.strptime(timestamp, TZ_TEXT).replace(tzinfo=DAY)
                except ValueError:
                    continue
                ts = int(parsed.timestamp())
                max_ts = max(max_ts, ts)
                day = parsed.date().isoformat()
                earliest = day if not earliest or day < earliest else earliest
                sender = str(row["sender"] or "")
                is_send = row["is_send"]
                if is_send is None:
                    is_send = 1 if sender == me_sender else 0
                digest = str(row["content_hash"] or "")
                if not digest:
                    import hashlib
                    digest = hashlib.sha256(
                        f"{parsed.replace(tzinfo=None).isoformat()}|{sender}|{row['content']}".encode("utf-8")
                    ).hexdigest()
                try:
                    chat.execute(
                        """INSERT OR IGNORE INTO messages
                        (platform_message_id, sender_id, sender, is_send, message_type,
                         content, day, ts, media_path, media_kind, reply_to_message_id,
                         content_hash, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(row["platform_message_id"] or ""), str(row["sender_id"] or ""),
                            sender[:100], int(bool(is_send)), int(row["message_type"] or 0),
                            str(row["content"] or "")[:20_000], day, ts,
                            str(row["media_path"] or ""),
                            love_chat._media_kind_from_path(str(row["media_path"] or "")),
                            str(row["reply_to_message_id"] or ""), digest, _now(),
                        ))
                    inserted += 1
                except sqlite3.Error:
                    continue
            last_sync_day = last_sync_at[:10] if last_sync_at else (earliest or "")
            now = _now()
            chat.execute(
                """INSERT INTO contact
                (id, session_id, display_name, me_sender, partner_sender, avatar,
                 watermark, history_start_day, last_sync_day, last_sync_at,
                 last_sync_status, last_error, created_at, updated_at)
                VALUES (1, ?, ?, ?, ?, '', ?, ?, ?, ?, 'migrated', '', ?, ?)""",
                (session_id, partner_name[:200], me_sender[:100], partner_name[:100],
                 max_ts, earliest, last_sync_day, last_sync_at[:19], now, now))
            chat.commit()
            report["chat"] = {"project": project["name"], "messages": inserted,
                              "partner": partner_name, "me": me_sender}
        else:
            report["chat"] = {"project": project["name"], "messages": 0, "kept": True}
        # 同步日志（可选）：保留旧项目最近 20 条，方便查看迁移前的同步状态。
        if _table_exists(connection, "weflow_sync_logs"):
            logs = connection.execute(
                "SELECT mode, status, fetched, inserted, matched, duplicates, error_message, "
                "started_at, finished_at FROM weflow_sync_logs WHERE project_id=? "
                "ORDER BY started_at DESC LIMIT 20", (project_id,)).fetchall()
            for row in reversed(logs):
                chat.execute(
                    """INSERT INTO sync_logs
                    (mode, status, fetched, inserted, matched, duplicates, error, started_at, finished_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row["mode"], row["status"], row["fetched"] or 0, row["inserted"] or 0,
                     row["matched"] or 0, row["duplicates"] or 0,
                     row["error_message"] or "", row["started_at"] or "", row["finished_at"] or ""))
            chat.commit()
    report["archive"] = _archive_other_projects(connection, paths, project_id)


def _archive_other_projects(connection: sqlite3.Connection, paths: LovePaths,
                            keep_id: str) -> dict[str, Any]:
    if not _table_exists(connection, "projects"):
        return {}
    rows = connection.execute(
        "SELECT id, name, partner_a, partner_b, message_count, date_range_start, "
        "date_range_end, created_at, updated_at FROM projects WHERE id != ?", (keep_id,)
    ).fetchall()
    if not rows:
        return {}
    target = paths.archive / "其他聊天项目"
    target.mkdir(parents=True, exist_ok=True)
    index = []
    for row in rows:
        item = dict(row)
        index.append(item)
    (target / "项目索引.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {"other_projects": len(index), "index": str(target / "项目索引.json")}


def _migrate_journals(legacy: Path, paths: LovePaths, database: Database,
                      connection: sqlite3.Connection, report: dict[str, Any]) -> None:
    if not _table_exists(connection, "journal_entries"):
        return
    rows = connection.execute(
        "SELECT date, content, mood, created_at, updated_at FROM journal_entries "
        "ORDER BY date ASC, created_at ASC").fetchall()
    existing = pillar_notes.list_notes(
        str(paths.root.parent.parent), pillar=vault_layout.LOVE_PILLAR,
        chapter=vault_layout.LOVE_JOURNAL_CHAPTER, limit=500)
    titles = {str(item.get("title") or "") for item in existing}
    created = 0
    for row in rows:
        day = str(row["date"] or "")[:10]
        content = str(row["content"] or "").strip()
        if not day or not content:
            continue
        base_title = f"恋爱日志 {day}"
        title = base_title
        suffix = 2
        while title in titles:
            title = f"{base_title}（{suffix}）"
            suffix += 1
        try:
            note = pillar_notes.create_note(
                str(paths.root.parent.parent), pillar=vault_layout.LOVE_PILLAR,
                chapter=vault_layout.LOVE_JOURNAL_CHAPTER, title=title,
                body=content, note_type="love_memory",
                tags=["旧恋爱日志"], date=day,
                extra_metadata={"mood": str(row["mood"] or ""), "generated_by": "legacy"},
            )
            titles.add(title)
            database.audit("love_journal_migrated", {"path": note.get("path", ""), "date": day})
            created += 1
        except Exception as error:  # noqa: BLE001 - 单篇失败继续
            report.setdefault("errors", []).append(f"恋爱日志 {day}: {str(error)[:200]}")
    report["journal"] = {"created": created, "source": len(rows)}


def _migrate_profiles(legacy: Path, paths: LovePaths, connection: sqlite3.Connection,
                      report: dict[str, Any]) -> None:
    if not _table_exists(connection, "partner_profiles"):
        return
    rows = connection.execute(
        "SELECT * FROM partner_profiles ORDER BY updated_at DESC").fetchall()
    if not rows:
        return
    row = rows[0]
    try:
        traits = json.loads(row["traits"] or "[]")
    except ValueError:
        traits = []
    structured = {
        "summary": f"从旧版迁移的{row['partner_name']}画像。",
        "traits": traits if isinstance(traits, list) else [],
    }
    with love_master._connect(paths.master_db) as master:
        exists = master.execute(
            "SELECT 1 FROM runs WHERE skill='portrait' AND result_json LIKE ? LIMIT 1",
            ("%旧版迁移%",)).fetchone()
        if exists is None:
            master.execute(
                """INSERT INTO runs
                (skill, status, params_json, result_json, created_at, completed_at)
                VALUES ('portrait', 'completed', '{}', ?, ?, ?)""",
                (json.dumps({"structured": structured, "text": structured["summary"],
                             "migrated": True}, ensure_ascii=False),
                 str(row["created_at"] or _now()), str(row["updated_at"] or _now())))
            master.commit()
            report["portrait"] = {"traits": len(structured["traits"])}
        else:
            report["portrait"] = {"kept": True}


def _migrate_analyses(legacy: Path, paths: LovePaths, connection: sqlite3.Connection,
                      report: dict[str, Any]) -> None:
    if not _table_exists(connection, "analysis_logs"):
        return
    rows = connection.execute(
        "SELECT * FROM analysis_logs WHERE status='completed' AND result_json != '' "
        "ORDER BY created_at DESC LIMIT 50").fetchall()
    count = 0
    with love_master._connect(paths.master_db) as master:
        for row in rows:
            try:
                result = json.loads(row["result_json"] or "{}")
            except ValueError:
                continue
            marker = str(row["id"] or "")
            exists = master.execute(
                "SELECT 1 FROM runs WHERE skill='deep_analysis' AND params_json LIKE ? LIMIT 1",
                (f"%{marker}%",)).fetchone()
            if exists is not None:
                continue
            master.execute(
                """INSERT INTO runs
                (skill, status, params_json, result_json, created_at, completed_at)
                VALUES ('deep_analysis', 'completed', ?, ?, ?, ?)""",
                (json.dumps({"legacy_id": marker}, ensure_ascii=False),
                 json.dumps({"structured": result, "text": "", "migrated": True},
                            ensure_ascii=False),
                 str(row["created_at"] or _now()), str(row["completed_at"] or _now())))
            count += 1
        master.commit()
    report["analyses"] = {"created": count, "source": len(rows)}


def _migrate_schedule(legacy: Path, paths: LovePaths, report: dict[str, Any]) -> None:
    excel = legacy / "xlsx"
    copied = 0
    if excel.is_dir():
        paths.schedule_excel.mkdir(parents=True, exist_ok=True)
        for item in excel.iterdir():
            if item.is_file() and item.suffix.lower() in (".xlsx", ".xlsm"):
                target = paths.schedule_excel / item.name
                if not target.exists():
                    shutil.copy2(item, target)
                    copied += 1
    notes = legacy / love_schedule.NOTES_NAME
    if notes.is_file() and not paths.schedule_notes.exists():
        shutil.copy2(notes, paths.schedule_notes)
    if copied or paths.schedule_notes.exists():
        report["schedule"] = {"excel_copied": copied,
                              "notes": paths.schedule_notes.exists()}


def _migrate_cycle(legacy: Path, paths: LovePaths, report: dict[str, Any]) -> None:
    source = legacy / "cycle.db"
    if source.is_file() and not paths.cycle_db.exists():
        try:
            _copy_sqlite(source, paths.cycle_db)
        except sqlite3.Error:
            shutil.copy2(source, paths.cycle_db)
        report["cycle"] = "copied"
    elif paths.cycle_db.exists():
        report["cycle"] = "kept"


def _migrate_bazi(legacy: Path, paths: LovePaths, report: dict[str, Any]) -> None:
    source = legacy / "bazi.json"
    if not source.is_file():
        return
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        report.setdefault("errors", []).append(f"八字 JSON 读取失败：{str(error)[:200]}")
        return
    if not isinstance(payload, dict):
        return
    with love_bazi._connect(paths.bazi_db) as con:
        exists = con.execute(
            "SELECT 1 FROM reports WHERE input_json LIKE '%bazi.json%' LIMIT 1").fetchone()
        if exists is not None:
            report["bazi"] = "kept"
            return
        hehun = payload.get("hehun") if isinstance(payload.get("hehun"), dict) else {}
        title = "旧版八字合婚"
        chart = {"him": payload.get("him") or {}, "her": payload.get("her") or {},
                 "hehun": hehun}
        con.execute(
            """INSERT INTO reports
            (title, input_json, chart_json, result_json, status, engine, error, created_at, completed_at)
            VALUES (?, ?, ?, ?, 'completed', 'legacy-bazi.json', '', ?, ?)""",
            (title, json.dumps({"source": "bazi.json"}, ensure_ascii=False),
             json.dumps(chart, ensure_ascii=False),
             json.dumps({**chart}, ensure_ascii=False), _now(), _now()))
        con.commit()
    report["bazi"] = "copied"


def _write_marker(paths: LovePaths, report: dict[str, Any]) -> None:
    paths.archive.mkdir(parents=True, exist_ok=True)
    (paths.archive / MARKER).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")


def migrate_love_v2(vault_dir: Path | str, database: Database, *,
                    database_path: Path | str | None = None,
                    extra_legacy_roots: list[Path] | None = None,
                    read_only: bool = False) -> dict[str, Any]:
    """执行一次幂等迁移；返回可展示的迁移报告。"""
    root = Path(vault_dir).resolve()
    db_path = Path(database_path) if database_path else database.path
    paths = love_paths(root)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "vault": str(root),
        "status": "skipped",
        "started_at": _now(),
        "source": "",
        "backup": "",
        "steps": {},
        "errors": [],
    }
    if read_only:
        report["reason"] = "read_only"
        return report
    try:
        ensure_vault_writable(read_only, "迁移恋爱系统数据")
    except VaultReadOnlyError:
        report["reason"] = "read_only"
        return report
    marker_path = paths.archive / MARKER
    if marker_path.is_file():
        try:
            previous = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        if isinstance(previous, dict) and previous.get("schema") == SCHEMA \
                and previous.get("status") == "completed":
            # 已经迁移过：不再重复备份/改文件；旧目录原样保留。
            previous["reused"] = True
            ensure_love_layout(root)
            return previous
    legacy_candidates = [
        root for root in _legacy_roots(db_path, extra_legacy_roots)
        if (root / LEGACY_DB).is_file()
    ]
    if not legacy_candidates:
        ensure_love_layout(root)
        report["status"] = "empty"
        report["reason"] = "没有旧版恋爱数据库"
        _write_marker(paths, report)
        return report
    legacy = legacy_candidates[0]
    report["source"] = str(legacy)
    report["status"] = "running"
    ensure_love_layout(root)
    # 新库表先建好，迁移写入不会因为缺表失败。
    love_chat.ensure_chat_schema(paths.chat_db)
    love_gallery.ensure_gallery_schema(paths)
    love_bazi.ensure_bazi_schema(paths)
    love_master.ensure_master_schema(paths)
    try:
        backup = _backup_legacy(legacy, paths)
        if backup is not None:
            report["backup"] = str(backup)
        connection = _connect(legacy / LEGACY_DB)
        try:
            _migrate_weflow_token(legacy, paths, connection, report["steps"])
            _migrate_chat(legacy, paths, connection, report["steps"])
            _migrate_journals(legacy, paths, database, connection, report["steps"])
            _migrate_profiles(legacy, paths, connection, report["steps"])
            _migrate_analyses(legacy, paths, connection, report["steps"])
        finally:
            connection.close()
        _migrate_schedule(legacy, paths, report["steps"])
        _migrate_cycle(legacy, paths, report["steps"])
        _migrate_bazi(legacy, paths, report["steps"])
        report["status"] = "completed"
    except Exception as error:  # noqa: BLE001 - 迁移失败不能让应用起不来
        report["status"] = "failed"
        report["errors"].append(str(error)[:500])
    report["finished_at"] = _now()
    _write_marker(paths, report)
    return report


__all__ = ["migrate_love_v2", "SCHEMA"]
