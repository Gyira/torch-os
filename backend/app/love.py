"""恋爱系统（原生模块）。

把原来的「恋爱分析助手」重做成薪火OS 的一部分：
- 直接读写它已有的 SQLite 数据库（默认在 %LOCALAPPDATA%\\love-analysis-assistant），
  不启动、也不依赖原来那个 FastAPI/Electron 进程；
- 保留原有表结构、数据格式和 DeepSeek 分析口径，已有项目/日志/画像可以继续使用；
- 只通过本机工作台的会话鉴权暴露 API，所有聊天内容都留在本机；
  AI 分析前按原有规则脱敏姓名、电话、证件、邮箱、链接等。

文件故意保持为「一个功能一个文件」：交路表/排班表在 ``love_schedule.py``。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any, Iterator, Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, StrictBool
from starlette.concurrency import run_in_threadpool

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_IMPORT_BYTES = 40 * 1024 * 1024
MAX_PROJECT_NAME = 200
MAX_TRAIT_KEY = 120
MAX_JOURNAL_CONTENT = 5000
ANALYSIS_MAX_MESSAGES = 8000

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    partner_a VARCHAR(100) NOT NULL,
    partner_b VARCHAR(100) NOT NULL,
    message_count INTEGER,
    date_range_start DATETIME,
    date_range_end DATETIME,
    created_at DATETIME,
    updated_at DATETIME
);
CREATE TABLE IF NOT EXISTS messages (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    timestamp DATETIME NOT NULL,
    sender VARCHAR(100) NOT NULL,
    content TEXT NOT NULL,
    is_system BOOLEAN,
    content_hash VARCHAR(64) NOT NULL,
    created_at DATETIME,
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_messages_project_id ON messages(project_id);
CREATE INDEX IF NOT EXISTS ix_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_project_hash ON messages(project_id, content_hash);
CREATE TABLE IF NOT EXISTS partner_profiles (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    partner_name VARCHAR(100) NOT NULL,
    project_id VARCHAR(36),
    traits TEXT,
    is_locked BOOLEAN,
    created_at DATETIME,
    updated_at DATETIME,
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_partner_profiles_partner_name ON partner_profiles(partner_name);
CREATE TABLE IF NOT EXISTS analysis_logs (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    profile_id VARCHAR(36),
    trigger_type VARCHAR(20),
    analysis_type VARCHAR(20),
    model VARCHAR(50),
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    result_json TEXT,
    risk_flags TEXT,
    status VARCHAR(20),
    error_message TEXT,
    created_at DATETIME,
    completed_at DATETIME,
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
    FOREIGN KEY(profile_id) REFERENCES partner_profiles (id)
);
CREATE INDEX IF NOT EXISTS ix_analysis_logs_project_id ON analysis_logs(project_id);
CREATE TABLE IF NOT EXISTS journal_entries (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    date DATE NOT NULL,
    content TEXT NOT NULL,
    mood VARCHAR(20),
    created_at DATETIME,
    updated_at DATETIME,
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_journal_entries_project_id ON journal_entries(project_id);
CREATE INDEX IF NOT EXISTS ix_journal_entries_date ON journal_entries(date);
CREATE TABLE IF NOT EXISTS settings (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    key VARCHAR(100) NOT NULL,
    value TEXT,
    updated_at DATETIME
);
CREATE INDEX IF NOT EXISTS ix_settings_key ON settings(key);
"""


# ── 小工具 ──────────────────────────────────────────────────────────

def _now_text(with_micro: bool = True) -> str:
    fmt = "%Y-%m-%d %H:%M:%S.%f" if with_micro else "%Y-%m-%d %H:%M:%S"
    return datetime.now().strftime(fmt)


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    return None


def _iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    parsed = _parse_dt(value)
    return parsed.isoformat() if parsed else str(value)


def _date_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _limit_text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


@dataclass(frozen=True)
class LoveSettings:
    """恋爱系统的显式配置。

    故意不在模块顶层 import ``backend.app.config``：QA 的启动器禁止
    在隔离进程里触碰用户 .env/私有路径。个人模式由 application.create_app
    把 config 里的值显式传进来；独立测试则可以直接构造这个对象。
    """
    db_path: Path
    app_dir: Path
    stopwords_path: Path
    excel_dir: Path
    timetable_path: Path
    api_base_url: str
    api_model: str
    key_file: Path | None = None
    # 排班对象姓名只从本机配置读取，不写进仓库；空串表示自动识别。
    target_name: str = ""

    @classmethod
    def from_env(cls, *, db_path: Path | None = None,
                 key_file: Path | None = None) -> "LoveSettings":
        def path_value(name: str, default: Path) -> Path:
            raw = (os.getenv(name) or "").strip()
            if not raw:
                return default
            value = Path(raw)
            return value if value.is_absolute() else PROJECT_ROOT / value

        local_appdata = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        database = db_path or path_value(
            "LOVE_DB_PATH", local_appdata / "love-analysis-assistant" / "love_analysis.db"
        )
        app_dir = path_value("LOVE_APP_DIR", PROJECT_ROOT)
        return cls(
            db_path=Path(database),
            app_dir=app_dir,
            stopwords_path=path_value(
                "LOVE_STOPWORDS_PATH", app_dir / "resources" / "stopwords" / "cn_stopwords.txt"
            ),
            excel_dir=path_value(
                "LOVE_EXCEL_DIR", PROJECT_ROOT / "data" / "love" / "xlsx"
            ),
            timetable_path=path_value(
                "LOVE_TIMETABLE_PATH", app_dir / "backend" / "data" / "train_timetable.json"
            ),
            api_base_url=(os.getenv("LOVE_API_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/"),
            api_model=os.getenv("LOVE_API_MODEL") or "deepseek-chat",
            key_file=Path(key_file) if key_file else Path(database).parent / ".api_key.key",
            target_name=(os.getenv("LOVE_TARGET_NAME") or "").strip(),
        )


class LoveStore:
    """原有恋爱数据库的薄封装：每次请求独立连接，打开外键，异常不留半写状态。"""

    def __init__(self, db_path: Path | str, *, key_file: Path | str | None = None,
                 settings: LoveSettings | None = None):
        self.db_path = Path(db_path)
        self.key_file = Path(key_file) if key_file else self.db_path.parent / ".api_key.key"
        self.settings = settings
        self._schema_ready = False

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        try:
            con = sqlite3.connect(str(self.db_path), timeout=15)
        except sqlite3.Error as exc:
            raise RuntimeError(f"无法打开恋爱数据库：{exc}") from exc
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA_SQL)
        self._schema_ready = True

    def status(self) -> dict[str, Any]:
        settings = self.settings or LoveSettings.from_env(db_path=self.db_path, key_file=self.key_file)
        exists = self.db_path.exists()
        info: dict[str, Any] = {
            "available": False,
            "db_path": str(self.db_path),
            "db_exists": exists,
            "db_size": self.db_path.stat().st_size if exists else 0,
            "project_count": 0,
            "message_count": 0,
            "journal_count": 0,
            "api_key_configured": False,
            "api_base_url": settings.api_base_url,
            "api_model": settings.api_model,
            "app_dir": str(settings.app_dir),
            "app_dir_exists": settings.app_dir.is_dir(),
            "schedule_excel_dir": str(settings.excel_dir),
            "schedule_timetable": str(settings.timetable_path),
            "error": "",
        }
        try:
            self.ensure_schema()
            with self.connect() as con:
                info["project_count"] = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
                info["message_count"] = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                info["journal_count"] = con.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]
                info["api_key_configured"] = bool(_stored_api_key_row(con))
            info["available"] = True
        except Exception as exc:  # 路径/权限/损坏的库：把原因显示给用户，不吞掉
            info["error"] = f"恋爱数据库不可用：{exc}"
        return info


def default_love_store(settings: LoveSettings | None = None) -> LoveStore:
    settings = settings or LoveSettings.from_env()
    return LoveStore(settings.db_path, key_file=settings.key_file, settings=settings)


def _stored_api_key_row(con: sqlite3.Connection) -> sqlite3.Row | None:
    return con.execute(
        "SELECT id, value, updated_at FROM settings WHERE key = 'api_key' "
        "ORDER BY updated_at DESC, rowid DESC LIMIT 1"
    ).fetchone()


# ── 聊天记录解析（兼容原有导出格式）────────────────────────────────

@dataclass
class ParsedMessage:
    timestamp: datetime
    sender: str
    content: str
    is_system: bool = False


@dataclass
class ParsedChat:
    messages: list[ParsedMessage] = field(default_factory=list)
    senders: list[str] = field(default_factory=list)
    sender_counts: dict[str, int] = field(default_factory=dict)
    date_range_start: datetime | None = None
    date_range_end: datetime | None = None
    encoding: str = ""
    line_count: int = 0


class ChatParser:
    ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16")

    TIME_PATTERNS = (
        re.compile(r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}:\d{2})\s+([^\s:]+?):\s*(.+)$"),
        re.compile(r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2})\s+([^\s:]+?):\s*(.+)$"),
        re.compile(r"^(\d{4}年\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{2})\s+([^(]+?)\([^)]*\):\s*(.+)$"),
        re.compile(r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}:\d{2})\s+(\S+)\s+(.+)$"),
    )
    MULTILINE_HEADER = re.compile(
        r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}:\d{2})\s+'([^']+)'(?:\s+(.+))?$"
    )
    SYSTEM_KEYWORDS = (
        "加入了群聊", "退出了群聊", "修改群名为", "修改群公告",
        "邀请", "移出了群聊", "开启了好友验证", "关闭了好友验证",
        "撤回了一条消息", "修改备注", "拍了拍", "发送了",
        "对方正在输入", "消息已发出，但被对方拒收了",
    )

    def _timestamp(self, text: str) -> datetime | None:
        normalized = (
            text.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
        )
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(normalized.strip(), fmt)
            except ValueError:
                continue
        return None

    def _system(self, sender: str, content: str) -> bool:
        return (
            any(word in content for word in self.SYSTEM_KEYWORDS)
            or any(word in sender for word in ("系统", "System"))
        )

    def _line(self, line: str) -> ParsedMessage | None:
        if not line:
            return None
        for pattern in self.TIME_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            timestamp = self._timestamp(match.group(1))
            if timestamp is None:
                continue
            sender = match.group(2).strip()
            content = match.group(3).strip()
            if not sender or not content:
                continue
            return ParsedMessage(timestamp, sender, content, self._system(sender, content))
        return None

    def parse(self, text: str, *, encoding: str = "") -> ParsedChat:
        lines = text.splitlines()
        result = ParsedChat(encoding=encoding, line_count=len(lines))
        index = 0
        while index < len(lines):
            line = lines[index].strip()
            multi = self.MULTILINE_HEADER.match(line)
            if multi:
                timestamp = self._timestamp(multi.group(1))
                sender = multi.group(2).strip()
                inline = multi.group(3)
                if inline:
                    content = inline.strip()
                else:
                    index += 1
                    while index < len(lines) and not lines[index].strip():
                        index += 1
                    content = lines[index].strip() if index < len(lines) else ""
                if timestamp and sender and content:
                    result.messages.append(
                        ParsedMessage(timestamp, sender, content, self._system(sender, content))
                    )
                index += 1
                continue
            message = self._line(line)
            if message:
                result.messages.append(message)
            index += 1

        result.messages.sort(key=lambda item: item.timestamp)
        counts: dict[str, int] = {}
        for message in result.messages:
            if message.is_system:
                continue
            counts[message.sender] = counts.get(message.sender, 0) + 1
        ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        result.senders = [name for name, _ in ranked]
        result.sender_counts = dict(ranked)
        if result.messages:
            result.date_range_start = result.messages[0].timestamp
            result.date_range_end = result.messages[-1].timestamp
        return result

    def decode_and_parse(self, raw: bytes) -> ParsedChat:
        """原工具有 GBK/UTF-8 混合导出；按解析成功率选编码，避免把乱码当正文入库。"""
        if not raw:
            raise ValueError("文件内容为空")
        detected = ""
        try:
            import chardet

            guess = chardet.detect(raw[:65536]) or {}
            candidate = str(guess.get("encoding") or "")
            if candidate:
                detected = candidate.lower().replace("gb2312", "gb18030")
        except Exception:
            detected = ""
        order: list[str] = []
        for name in (detected, *self.ENCODINGS):
            if name and name not in order:
                order.append(name)
        best: ParsedChat | None = None
        best_enc = ""
        for encoding in order[:4]:
            try:
                text = raw.decode(encoding, errors="strict")
            except (UnicodeDecodeError, LookupError):
                continue
            parsed = self.parse(text, encoding=encoding)
            if best is None or len(parsed.messages) > len(best.messages):
                best, best_enc = parsed, encoding
            if len(parsed.messages) >= 2 and parsed.senders:
                # 编码正确时的成功率足够高；继续尝试只会白白多解析几遍大文件。
                if encoding in (detected, "utf-8-sig", "utf-8") and len(parsed.messages) >= 1:
                    return parsed
        if best is None:
            raise ValueError("无法识别文件编码，请使用 UTF-8 或 GBK 重新导出聊天记录")
        best.encoding = best_enc
        return best

    def preview(self, parsed: ParsedChat, raw_lines: list[str]) -> dict[str, Any]:
        return {
            "lines": raw_lines[:30],
            "encoding": parsed.encoding,
            "line_count": parsed.line_count,
            "message_count": len(parsed.messages),
            "senders": parsed.senders,
            "sender_counts": parsed.sender_counts,
            "date_range_start": _iso(parsed.date_range_start),
            "date_range_end": _iso(parsed.date_range_end),
        }


_PARSER = ChatParser()


def _choose_project_name(parsed: ParsedChat, partner_a: str, partner_b: str,
                         requested: str = "", file_name: str = "") -> str:
    name = (requested or "").strip()
    if not name and file_name:
        name = Path(file_name).name[:120]
    if not name:
        name = f"{partner_a} & {partner_b} 的聊天"
    if parsed.date_range_start:
        name += f" ({parsed.date_range_start.strftime('%Y-%m-%d')})"
    return name[:MAX_PROJECT_NAME]


def _insert_import(store: LoveStore, parsed: ParsedChat, *, project_name: str = "",
                   file_name: str = "") -> dict[str, Any]:
    if not parsed.messages:
        raise HTTPException(400, "未能从文件中解析到任何消息，请检查文件格式")
    if len(parsed.senders) < 2:
        raise HTTPException(
            400, f"仅检测到 {len(parsed.senders)} 位对话者，请确认文件格式正确"
        )
    partner_a = parsed.senders[0]
    partner_b = parsed.senders[1]
    project_id = str(uuid.uuid4())
    now = _now_text()
    name = _choose_project_name(parsed, partner_a, partner_b, project_name, file_name)
    rows: list[tuple[Any, ...]] = []
    seen: set[str] = set()
    for message in parsed.messages:
        digest = hashlib.sha256(
            f"{message.timestamp.isoformat()}|{message.sender}|{message.content}".encode("utf-8")
        ).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        rows.append((
            str(uuid.uuid4()), project_id, message.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),
            message.sender, message.content, 1 if message.is_system else 0, digest, now,
        ))
    with store.connect() as con:
        con.execute(
            "INSERT INTO projects (id, name, partner_a, partner_b, message_count, "
            "date_range_start, date_range_end, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                project_id, name, partner_a, partner_b, len(rows),
                parsed.date_range_start.strftime("%Y-%m-%d %H:%M:%S.%f") if parsed.date_range_start else None,
                parsed.date_range_end.strftime("%Y-%m-%d %H:%M:%S.%f") if parsed.date_range_end else None,
                now, now,
            ),
        )
        con.executemany(
            "INSERT INTO messages (id, project_id, timestamp, sender, content, is_system, "
            "content_hash, created_at) VALUES (?,?,?,?,?,?,?,?)", rows,
        )
    return {
        "project_id": project_id,
        "project_name": name,
        "message_count": len(rows),
        "total_parsed": len(parsed.messages),
        "duplicate_count": len(parsed.messages) - len(rows),
        "sender_a": partner_a,
        "sender_b": partner_b,
        "date_range_start": _iso(parsed.date_range_start),
        "date_range_end": _iso(parsed.date_range_end),
        "encoding": parsed.encoding,
    }


# ── 统计 ────────────────────────────────────────────────────────────

def _project(con: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "项目不存在")
    return row


def _project_json(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "partner_a": row["partner_a"],
        "partner_b": row["partner_b"],
        "message_count": row["message_count"] or 0,
        "date_range_start": _iso(row["date_range_start"]),
        "date_range_end": _iso(row["date_range_end"]),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def _latest_analysis(con: sqlite3.Connection, project_id: str,
                     *, completed_only: bool = False) -> sqlite3.Row | None:
    where = "AND status = 'completed'" if completed_only else ""
    return con.execute(
        "SELECT * FROM analysis_logs WHERE project_id = ? " + where +
        " ORDER BY created_at DESC LIMIT 1", (project_id,)
    ).fetchone()


def _overview(con: sqlite3.Connection, project: sqlite3.Row) -> dict[str, Any]:
    project_id = project["id"]
    row = con.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN sender = ? THEN 1 ELSE 0 END) AS a_count, "
        "SUM(CASE WHEN sender = ? THEN 1 ELSE 0 END) AS b_count, "
        "MIN(timestamp) AS first, MAX(timestamp) AS last "
        "FROM messages WHERE project_id = ? AND COALESCE(is_system, 0) = 0",
        (project["partner_a"], project["partner_b"], project_id),
    ).fetchone()
    total = int(row["total"] or 0)
    a_count = int(row["a_count"] or 0)
    b_count = int(row["b_count"] or 0)
    denominator = a_count + b_count
    start = _parse_dt(row["first"])
    end = _parse_dt(row["last"])
    days = max((end.date() - start.date()).days + 1, 1) if start and end else 0
    return {
        "total_messages": total,
        "sender_a": {
            "name": project["partner_a"], "messages": a_count,
            "ratio": round(a_count / denominator, 4) if denominator else 0,
        },
        "sender_b": {
            "name": project["partner_b"], "messages": b_count,
            "ratio": round(b_count / denominator, 4) if denominator else 0,
        },
        "date_range_start": _iso(start),
        "date_range_end": _iso(end),
        "total_days": days,
        "avg_daily_messages": round(total / days, 1) if days else 0,
    }


def _trends(con: sqlite3.Connection, project: sqlite3.Row,
            granularity: str = "day") -> dict[str, Any]:
    fmt = {"week": "%Y-%W", "month": "%Y-%m"}.get(granularity, "%Y-%m-%d")
    rows = con.execute(
        "SELECT strftime(?, timestamp) AS period, COUNT(*) AS total, "
        "SUM(CASE WHEN sender = ? THEN 1 ELSE 0 END) AS a_count, "
        "SUM(CASE WHEN sender = ? THEN 1 ELSE 0 END) AS b_count "
        "FROM messages WHERE project_id = ? AND COALESCE(is_system, 0) = 0 "
        "GROUP BY period ORDER BY period",
        (fmt, project["partner_a"], project["partner_b"], project["id"]),
    ).fetchall()
    return {
        "granularity": granularity,
        "senders": {"a": project["partner_a"], "b": project["partner_b"]},
        "data": [
            {
                "date": row["period"], "total": int(row["total"] or 0),
                "sender_a": int(row["a_count"] or 0), "sender_b": int(row["b_count"] or 0),
            }
            for row in rows
        ],
    }


def _timing(con: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    rows = con.execute(
        "SELECT timestamp, sender FROM messages WHERE project_id = ? "
        "AND COALESCE(is_system, 0) = 0 ORDER BY timestamp ASC", (project_id,)
    ).fetchall()
    parsed = [(_parse_dt(row["timestamp"]), row["sender"]) for row in rows]
    parsed = [(stamp, sender) for stamp, sender in parsed if stamp is not None]
    result: dict[str, Any] = {
        "avg_reply_interval_seconds": 0.0,
        "median_reply_interval_seconds": 0.0,
        "longest_silence_seconds": 0.0,
        "longest_silence_start": None,
        "longest_silence_end": None,
        "reply_distribution": {},
    }
    if len(parsed) < 2:
        return result
    intervals: list[float] = []
    longest = 0.0
    longest_start = longest_end = None
    for index in range(1, len(parsed)):
        previous_time, previous_sender = parsed[index - 1]
        current_time, current_sender = parsed[index]
        gap = (current_time - previous_time).total_seconds()
        if gap < 0:
            continue
        if previous_sender != current_sender:
            intervals.append(gap)
        if gap > longest:
            longest = gap
            longest_start = previous_time.strftime("%Y-%m-%d %H:%M:%S")
            longest_end = current_time.strftime("%Y-%m-%d %H:%M:%S")
    distribution = {"<1分钟": 0, "1-5分钟": 0, "5-30分钟": 0,
                    "30分钟-2小时": 0, "2-12小时": 0, ">12小时": 0}
    for gap in intervals:
        if gap < 60:
            distribution["<1分钟"] += 1
        elif gap < 300:
            distribution["1-5分钟"] += 1
        elif gap < 1800:
            distribution["5-30分钟"] += 1
        elif gap < 7200:
            distribution["30分钟-2小时"] += 1
        elif gap < 43200:
            distribution["2-12小时"] += 1
        else:
            distribution[">12小时"] += 1
    if intervals:
        ordered = sorted(intervals)
        result["avg_reply_interval_seconds"] = round(sum(ordered) / len(ordered), 1)
        result["median_reply_interval_seconds"] = round(ordered[len(ordered) // 2], 1)
    result["longest_silence_seconds"] = round(longest, 1)
    result["longest_silence_start"] = longest_start
    result["longest_silence_end"] = longest_end
    result["reply_distribution"] = distribution
    return result


_STICKER_MARKERS = (
    "[表情包]", "[表情包：", "[表情]", "[动画表情]", "[Sticker]", "/表情",
    "[emoji", "<msg>", "sticker", "Sticker", "[偷笑]", "[呲牙]", "[流泪]",
    "[玫瑰]", "[社会社会]", "[得意]", "[大哭]", "[生气]", "[害羞]", "[亲亲]",
    "[拥抱]", "[爱心]", "[笑哭]", "[色]", "[惊讶]", "[白眼]", "[抓狂]", "[奋斗]",
)
_IMAGE_MARKERS = (
    "[图片]", "[照片]", "[Image]", "[Photo]", "../images/", "../emojis/",
    ".jpg", ".png", ".jpeg", ".gif", ".webp", "img", "IMG", "Screenshot",
)


def _media(con: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    contents = con.execute(
        "SELECT content FROM messages WHERE project_id = ? AND COALESCE(is_system, 0) = 0",
        (project_id,),
    ).fetchall()
    text_count = sticker_count = image_count = 0
    for row in contents:
        content = row["content"] or ""
        if any(marker in content for marker in _STICKER_MARKERS):
            sticker_count += 1
        elif any(marker in content for marker in _IMAGE_MARKERS):
            image_count += 1
        else:
            text_count += 1
    total = len(contents)
    return {
        "text_count": text_count,
        "sticker_count": sticker_count,
        "image_count": image_count,
        "sticker_ratio": round(sticker_count / total, 4) if total else 0,
        "image_ratio": round(image_count / total, 4) if total else 0,
    }


_WECHAT_NOISE = (
    r"\[表情包\]", r"\[表情包：[^\]]+\]", r"\[表情\]", r"\[动画表情\]",
    r"\[Sticker\]", r"\[emoji[^\]]*\]", r"\[[^\]]{1,8}\]",
    r"\[文件\]", r"\[视频通话\]", r"\[语音消息[^\]]*\]", r"\[语音\]",
    r"\[分享\]", r"\[链接\]", r"\[小程序\]", r"\[红包\]", r"\[转账\]",
    r"\.\./images/[^\s]+", r"\.\./emojis/[^\s]+", r"\.jpg\b", r"\.png\b",
    r"\.gif\b", r"\.jpeg\b", r"\.webp\b", r"\.bmp\b",
    r"语音消息.*转文字失败.*", r"Silk\s*解码失败",
)
_DEFAULT_STOPWORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "什么", "怎么", "如何", "还是", "可以", "因为", "所以", "但是", "然而",
    "如果", "虽然", "而且", "或者", "不过", "只是", "已经", "这个", "那个",
    "哪个", "这样", "那样", "怎样", "为什么", "没", "被", "把", "让", "向",
    "从", "对", "与", "及", "或", "啊", "吧", "呢", "吗", "哦", "嗯", "哈",
    "呀", "啦", "哇", "呵", "嘿", "喂", "唉", "咦", "哟", "噢", "嘛", "呗",
}
_STOPWORDS: dict[Path, set[str]] = {}


def _stopwords(path: Path) -> set[str]:
    cached = _STOPWORDS.get(path)
    if cached is not None:
        return cached
    words = set(_DEFAULT_STOPWORDS)
    try:
        if path.is_file():
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    word = line.strip()
                    if word:
                        words.add(word)
    except OSError:
        pass
    _STOPWORDS[path] = words
    return words


def _keywords(con: sqlite3.Connection, project_id: str, top_n: int,
              settings: LoveSettings) -> dict[str, Any]:
    rows = con.execute(
        "SELECT content FROM messages WHERE project_id = ? AND COALESCE(is_system, 0) = 0",
        (project_id,),
    ).fetchall()
    texts = [row["content"] or "" for row in rows]
    if not texts:
        return {"words": [], "total_unique_words": 0, "engine": "none"}
    cleaned: list[str] = []
    for text in texts:
        for pattern in _WECHAT_NOISE:
            text = re.sub(pattern, " ", text)
        cleaned.append(text)
    stopwords = _stopwords(settings.stopwords_path)
    counter: dict[str, int] = {}
    engine = "jieba"
    try:
        import jieba

        jieba.setLogLevel(logging.ERROR)
        for word in ("恋爱", "分手", "表白", "约会", "前任", "现任", "男友", "女友"):
            jieba.add_word(word, freq=100)
        tokens = jieba.cut(" ".join(cleaned))
    except Exception:
        engine = "fallback"
        tokens = re.findall(r"[\u4e00-\u9fff]{2,4}|[A-Za-z]{2,}", " ".join(cleaned))
    for token in tokens:
        word = token.strip()
        if len(word) < 2 or word in stopwords or word.isdigit():
            continue
        if all(not char.isalnum() and not ("\u4e00" <= char <= "\u9fff") for char in word):
            continue
        counter[word] = counter.get(word, 0) + 1
    ranked = sorted(counter.items(), key=lambda item: item[1], reverse=True)[:max(1, min(top_n, 300))]
    top = ranked[0][1] if ranked else 1
    return {
        "words": [
            {"word": word, "count": count, "weight": round(count / top, 4)}
            for word, count in ranked
        ],
        "total_unique_words": len(counter),
        "engine": engine,
    }


# ── 画像 ────────────────────────────────────────────────────────────

def _traits(row: sqlite3.Row | None) -> list[dict[str, Any]]:
    if row is None:
        return []
    try:
        data = json.loads(row["traits"] or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("trait")]


def _profile_json(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {"id": None, "partner_name": "", "traits": [], "updated_at": None}
    return {
        "id": row["id"],
        "partner_name": row["partner_name"],
        "project_id": row["project_id"],
        "traits": _traits(row),
        "updated_at": _iso(row["updated_at"]),
    }


def _profile_row(con: sqlite3.Connection, partner_name: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM partner_profiles WHERE partner_name = ? "
        "ORDER BY updated_at DESC LIMIT 1", (partner_name,)
    ).fetchone()


def _save_traits(con: sqlite3.Connection, profile_id: str,
                 traits: list[dict[str, Any]]) -> None:
    con.execute(
        "UPDATE partner_profiles SET traits = ?, updated_at = ?, is_locked = ? WHERE id = ?",
        (
            json.dumps(traits, ensure_ascii=False),
            _now_text(),
            1 if traits and all(trait.get("locked") for trait in traits) else 0,
            profile_id,
        ),
    )


def _merge_traits(store: LoveStore, partner_name: str, project_id: str,
                  new_traits: list[dict[str, Any]], log_id: str) -> None:
    with store.connect() as con:
        profile = _profile_row(con, partner_name)
        if profile is None:
            profile_id = str(uuid.uuid4())
            now = _now_text()
            con.execute(
                "INSERT INTO partner_profiles (id, partner_name, project_id, traits, "
                "is_locked, created_at, updated_at) VALUES (?,?,?,?,0,?,?)",
                (profile_id, partner_name, project_id, "[]", now, now),
            )
            existing: list[dict[str, Any]] = []
        else:
            profile_id = profile["id"]
            existing = _traits(profile)
        by_key = {str(item.get("trait")): item for item in existing}
        for item in new_traits:
            if not isinstance(item, dict):
                continue
            key = str(item.get("trait") or "").strip()[:MAX_TRAIT_KEY]
            if not key:
                continue
            value = str(item.get("value") or "")[:2000]
            try:
                confidence = float(item.get("confidence") or 0)
            except (TypeError, ValueError):
                confidence = 0.5
            confidence = max(0.0, min(1.0, confidence))
            evidence = str(item.get("evidence") or "")[:2000]
            current = by_key.get(key)
            if current is not None and current.get("locked"):
                current.setdefault("alternate_values", []).append({
                    "value": value, "confidence": confidence, "log_id": log_id,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                })
                current["update_count"] = int(current.get("update_count") or 0) + 1
                continue
            if current is None:
                item_row = {
                    "trait": key, "value": value, "confidence": confidence,
                    "evidence": evidence, "source_log_id": log_id, "locked": False,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "update_count": 0,
                }
                existing.append(item_row)
                by_key[key] = item_row
            elif confidence >= _safe_float(current.get("confidence")):
                current.update({
                    "value": value, "confidence": confidence, "evidence": evidence,
                    "source_log_id": log_id,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                })
                current["update_count"] = int(current.get("update_count") or 0) + 1
        _save_traits(con, profile_id, existing)
        con.execute(
            "UPDATE analysis_logs SET profile_id = ? WHERE project_id = ? AND id = ?",
            (profile_id, project_id, log_id),
        )


# ── API Key（沿用原应用的 AES-GCM 文件）────────────────────────────

class LoveKeyError(RuntimeError):
    pass


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:  # pragma: no cover - dependency should be installed
        raise LoveKeyError("缺少 cryptography 依赖，无法读取/保存 API Key") from exc
    return AESGCM


def _load_key(store: LoveStore, *, create: bool = False) -> bytes | None:
    path = store.key_file
    if path.is_file():
        try:
            key = path.read_bytes()
        except OSError as exc:
            raise LoveKeyError(f"无法读取 API Key 密钥文件：{exc}") from exc
        if len(key) == 32:
            return key
        raise LoveKeyError("API Key 密钥文件长度不正确")
    if not create:
        return None
    key = os.urandom(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key)
    except OSError as exc:
        raise LoveKeyError(f"无法保存 API Key 密钥文件：{exc}") from exc
    return key


def _decrypt_key(store: LoveStore, value: str) -> str:
    try:
        raw = base64.b64decode(value)
    except Exception as exc:
        raise LoveKeyError("API Key 密文格式不正确") from exc
    key = _load_key(store)
    if key is None:
        raise LoveKeyError("找不到 API Key 密钥文件；请在设置里重新保存一次 API Key")
    try:
        aes = _aesgcm()(key)
        return aes.decrypt(raw[:12], raw[12:], None).decode("utf-8")
    except Exception as exc:
        raise LoveKeyError("API Key 解密失败；请在设置里重新保存一次") from exc


def _encrypt_key(store: LoveStore, plaintext: str) -> str:
    key = _load_key(store, create=True)
    if key is None:
        raise LoveKeyError("无法创建 API Key 密钥")
    nonce = os.urandom(12)
    ciphertext = _aesgcm()(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def _api_key(store: LoveStore) -> str:
    with store.connect() as con:
        row = _stored_api_key_row(con)
    if row is None or not row["value"]:
        raise HTTPException(400, "还没有配置 DeepSeek API Key，请先到「恋爱系统 → 设置」里配置")
    try:
        return _decrypt_key(store, row["value"])
    except LoveKeyError as exc:
        raise HTTPException(400, str(exc)) from exc


# ── AI 分析 ─────────────────────────────────────────────────────────

ANALYSIS_SYSTEM_PROMPT = """你是专业的恋爱关系分析顾问。你的任务是基于微信聊天记录，客观分析两人的关系状态。

分析时请遵守以下原则：
1. 保持客观中立，不偏袒任何一方
2. 基于事实和对话模式做分析，不主观臆断
3. 给出具体、可执行的建议，而非泛泛而谈
4. 识别沟通中的健康模式和不健康模式
5. 特别注意情绪波动和潜在风险信号
6. 如果有历史档案信息，优先参考已确认的信息

请输出以下 JSON 格式（不要包含 markdown 标记）：
{
  "relationship_stage": "认识初期|暧昧期|热恋期|稳定期|磨合期|冷淡期|分手边缘|其他",
  "emotional_tone": {
    "overall": "整体情绪基调（如：温馨甜蜜 / 平淡日常 / 紧张不安 / 混合复杂）",
    "fluctuation": 0.0-1.0之间的数值,
    "trend": "情绪趋势（上升/稳定/下降/波动大）"
  },
  "communication_patterns": {
    "initiative_balance": "主动程度平衡性描述",
    "response_style": "回应模式描述",
    "key_topics": ["主要话题1", "主要话题2", "主要话题3"]
  },
  "key_events": [
    {"date": "事件日期或大致时间段", "description": "关键事件描述", "impact": "positive|negative|neutral", "significance": "high|medium|low"}
  ],
  "risk_warnings": [
    {"severity": "low|medium|high", "aspect": "风险方面", "detail": "具体描述和依据"}
  ],
  "personalized_advice": ["具体建议1", "具体建议2", "具体建议3"],
  "partner_traits": [
    {"trait": "特质名称", "value": "具体描述", "confidence": 0.0, "evidence": "支撑依据"}
  ]
}
"""

JOURNAL_SYSTEM_PROMPT = """你是一个贴心的恋爱日记助手。请基于以下聊天记录，用第一人称「我」的视角，写一段 100-200 字的恋爱日记。

要求：
1. 语气温馨自然，像自己写的日记
2. 概括当天最有趣的互动、最甜蜜的瞬间、或最重要的事情
3. 如果聊天很少或很平淡，就简单记录"今天比较忙，没怎么聊天"之类
4. 不要出现"根据聊天记录"、"AI分析"等暴露AI身份的词汇
5. 可以适当加入一些心情描述（开心、感动、有点小摩擦等）
6. 只输出日记正文，不要加任何标题、前缀或说明"""

# 中文紧贴数字时 \b 不会成立（「是138…」两边都是 \w）；用数字前后断言替代 \b。
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://[^\s,，。！？\n]+")
_BANK_CARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")


def _desensitize(text: str, names: dict[str, str]) -> str:
    value = text or ""
    value = _PHONE.sub("[电话号码已隐藏]", value)
    value = _ID_CARD.sub("[身份证号已隐藏]", value)
    value = _EMAIL.sub("[邮箱地址已隐藏]", value)
    value = _URL.sub("[链接已隐藏]", value)
    value = _BANK_CARD.sub("[银行卡号已隐藏]", value)
    for real, alias in names.items():
        if real and len(real) >= 2:
            value = value.replace(real, alias)
    return value


def _build_prompt(project: sqlite3.Row, messages: list[sqlite3.Row],
                  traits: list[dict[str, Any]]) -> list[dict[str, str]]:
    names = {project["partner_a"]: "用户A", project["partner_b"]: "用户B"}
    if len(messages) <= ANALYSIS_MAX_MESSAGES:
        selected = messages
    else:
        recent_count = int(ANALYSIS_MAX_MESSAGES * 0.6)
        older = messages[:-recent_count]
        sample_count = ANALYSIS_MAX_MESSAGES - recent_count
        step = max(1, len(older) // sample_count)
        selected = older[::step][:sample_count] + messages[-recent_count:]
    lines = []
    for message in selected:
        stamp = _parse_dt(message["timestamp"])
        label = stamp.strftime("%Y-%m-%d %H:%M") if stamp else str(message["timestamp"])[:16]
        lines.append(f"[{label}] {names.get(message['sender'], message['sender'])}: "
                     f"{_desensitize(message['content'], names)}")
    conversation = "\n".join(lines)

    sender_counts: dict[str, int] = {}
    for message in messages:
        sender_counts[message["sender"]] = sender_counts.get(message["sender"], 0) + 1
    first = _parse_dt(messages[0]["timestamp"])
    last = _parse_dt(messages[-1]["timestamp"])
    stats = [
        f"总消息数: {len(messages)}",
        f"时间范围: {first.date() if first else '?'} 至 {last.date() if last else '?'}",
    ]
    for sender, count in sender_counts.items():
        stats.append(f"{names.get(sender, sender)}: {count} 条 ({count / len(messages) * 100:.1f}%)")

    locked = [item for item in traits if item.get("locked")]
    unconfirmed = [item for item in traits if not item.get("locked")]
    profile_lines = ["## 分析对象档案"]
    if locked:
        profile_lines.append("\n### 已确认信息")
        for item in locked:
            profile_lines.append(f"- [✓] {item.get('trait')}: {item.get('value')}")
    if unconfirmed:
        profile_lines.append("\n### AI 推测信息（请根据最新对话验证）")
        for item in unconfirmed:
            confidence = float(item.get("confidence") or 0)
            profile_lines.append(
                f"- [?] {item.get('trait')}: {item.get('value')} (置信度: {confidence:.0%})"
            )
    profile_context = "\n".join(profile_lines) if traits else "暂无历史画像"

    user_prompt = "\n\n".join([
        f"【历史档案】\n{profile_context}",
        "【基础数据】\n" + "\n".join(stats),
        f"【聊天记录（已脱敏）】\n{conversation}",
        "\n【分析目标说明】\n"
        "对话中「用户A」是你自己，「用户B」是你的恋爱对象。\n"
        "请站在 用户A 的视角分析这段关系，重点关注以下内容：\n"
        "- partner_traits 字段请分析 用户B（你的恋爱对象）的个人特质，而非 用户A 的\n"
        "- 所有分析和建议应面向 用户A（你自己），帮助用户A 更好地理解和经营这段关系\n"
        "请基于以上信息进行深度分析，输出 JSON 格式结果。",
    ])
    return [
        {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _parse_ai_json(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return json.loads(text.replace("\n", "\\n").replace("\r", "\\r"))
        except json.JSONDecodeError as exc:
            return {
                "relationship_stage": "解析失败",
                "emotional_tone": {"overall": "分析结果解析异常", "fluctuation": 0, "trend": "未知"},
                "communication_patterns": {"initiative_balance": "", "response_style": "", "key_topics": []},
                "key_events": [], "risk_warnings": [], "personalized_advice": [], "partner_traits": [],
                "_raw_response": raw_text, "_parse_error": str(exc),
            }


def _call_deepseek(settings: LoveSettings, api_key: str,
                   messages: list[dict[str, str]], *,
                   max_tokens: int = 4096, temperature: float = 0.7) -> tuple[str, dict[str, int]]:
    import time

    last_error = ""
    for attempt in range(3):
        try:
            with httpx.Client(timeout=httpx.Timeout(120.0)) as client:
                response = client.post(
                    f"{settings.api_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": settings.api_model, "messages": messages,
                        "temperature": temperature, "max_tokens": max_tokens, "stream": False,
                    },
                )
            if response.status_code == 200:
                data = response.json()
                usage = data.get("usage") or {}
                return (
                    data["choices"][0]["message"]["content"],
                    {
                        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                        "completion_tokens": int(usage.get("completion_tokens") or 0),
                    },
                )
            if response.status_code == 401:
                raise ValueError("API Key 无效，请到设置里重新配置")
            if response.status_code == 429:
                last_error = "API 调用频率过高，请稍后再试"
                if attempt < 2:
                    time.sleep((attempt + 1) * 3)
                    continue
                raise ValueError(last_error)
            detail = response.text[:300]
            raise ValueError(f"API 请求失败 ({response.status_code}): {detail}")
        except httpx.TimeoutException:
            last_error = "API 请求超时，请检查网络连接"
            if attempt < 2:
                continue
            raise ValueError(last_error)
        except httpx.RequestError as exc:
            raise ValueError(f"网络请求失败: {exc}") from exc
    raise ValueError(last_error or "API 请求失败")


def _run_analysis_job(store: LoveStore, settings: LoveSettings,
                      project_id: str, log_id: str) -> None:
    """后台任务：读取 → 脱敏 → 画像 → DeepSeek → 保存结果/合并画像。"""
    try:
        with store.connect() as con:
            project = _project(con, project_id)
            messages = con.execute(
                "SELECT timestamp, sender, content FROM messages WHERE project_id = ? "
                "AND COALESCE(is_system, 0) = 0 ORDER BY timestamp ASC", (project_id,)
            ).fetchall()
            if not messages:
                raise ValueError("项目中没有消息，请先导入聊天记录")
            profile = _profile_row(con, project["partner_b"])
            traits = _traits(profile)
            prompt = _build_prompt(project, messages, traits)
        api_key = _api_key(store)
        content, usage = _call_deepseek(settings, api_key, prompt)
        parsed = _parse_ai_json(content)
        if not isinstance(parsed, dict):
            parsed = {"_raw_response": content, "_parse_error": "AI 返回的不是 JSON 对象"}
        with store.connect() as con:
            con.execute(
                "UPDATE analysis_logs SET status = 'completed', result_json = ?, "
                "prompt_tokens = ?, completion_tokens = ?, completed_at = ?, error_message = NULL "
                "WHERE id = ? AND project_id = ?",
                (json.dumps(parsed, ensure_ascii=False), usage["prompt_tokens"],
                 usage["completion_tokens"], _now_text(), log_id, project_id),
            )
            partner = con.execute(
                "SELECT partner_b FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if partner and isinstance(parsed.get("partner_traits"), list):
            _merge_traits(store, partner["partner_b"], project_id,
                          parsed["partner_traits"], log_id)
    except Exception as exc:
        message = str(exc)[:1000] or "分析失败"
        try:
            with store.connect() as con:
                con.execute(
                    "UPDATE analysis_logs SET status = 'failed', error_message = ?, "
                    "completed_at = ? WHERE id = ?", (message, _now_text(), log_id),
                )
        except Exception:
            pass


def _journal_for_date(settings: LoveSettings, api_key: str, project: sqlite3.Row,
                      messages: list[sqlite3.Row], target: str) -> str:
    if not messages:
        raise HTTPException(400, f"{target} 当天没有聊天记录")
    lines = []
    for message in messages[:200]:
        stamp = _parse_dt(message["timestamp"])
        time_text = stamp.strftime("%H:%M") if stamp else ""
        sender = "我" if message["sender"] == project["partner_a"] else project["partner_b"]
        lines.append(f"[{time_text}] {sender}: {message['content']}")
    content, _ = _call_deepseek(
        settings, api_key,
        [
            {"role": "system", "content": JOURNAL_SYSTEM_PROMPT},
            {"role": "user", "content": f"日期：{target}\n\n聊天记录：\n" + "\n".join(lines)},
        ],
        max_tokens=512, temperature=0.8,
    )
    return content.strip()


# ── 报告（原生 HTML，不依赖 Jinja/weasyprint）──────────────────────

def _report_html(project: sqlite3.Row, overview: dict[str, Any],
                 analysis: dict[str, Any] | None, traits: list[dict[str, Any]]) -> str:
    def block(title: str, body: str) -> str:
        return f'<section><h2>{escape(title)}</h2>{body}</section>'

    # AI 结果是本地 JSON，可能来自旧版本或被手动改过；渲染前按宽容类型处理。
    if not isinstance(analysis, dict):
        analysis = None
    stage = escape(str((analysis or {}).get("relationship_stage") or "暂无 AI 分析"))
    tone = (analysis or {}).get("emotional_tone")
    tone = tone if isinstance(tone, dict) else {}
    patterns = (analysis or {}).get("communication_patterns")
    patterns = patterns if isinstance(patterns, dict) else {}
    topics = [str(item) for item in _list_value(patterns.get("key_topics"))]
    event_rows = "".join(
        "<li><b>{}</b><span>{}</span><em>{}</em></li>".format(
            escape(str(item.get("date") or "")),
            escape(str(item.get("description") or "")),
            escape(str(item.get("impact") or "")),
        )
        for item in _list_value((analysis or {}).get("key_events")) if isinstance(item, dict)
    )
    risk_rows = "".join(
        '<div class="risk risk-{0}"><b>{1}</b><p>{2}</p></div>'.format(
            escape(str(item.get("severity") or "low")),
            escape(str(item.get("aspect") or "")),
            escape(str(item.get("detail") or "")),
        )
        for item in _list_value((analysis or {}).get("risk_warnings")) if isinstance(item, dict)
    )
    advice_rows = "".join(
        f"<li>{escape(str(item))}</li>"
        for item in _list_value((analysis or {}).get("personalized_advice"))
    )
    trait_rows = "".join(
        '<li class="{0}"><span>{1}</span><small>{2}</small></li>'.format(
            "locked" if item.get("locked") else "",
            escape(str(item.get("trait") or "")) + "：" + escape(str(item.get("value") or "")),
            "已确认" if item.get("locked") else f"置信度 {_safe_float(item.get('confidence')):.0%}",
        )
        for item in traits if isinstance(item, dict)
    )
    analysis_html = ""
    if analysis:
        analysis_html = block("AI 深度分析", f"""
<p class="stage">{stage}</p>
<div class="grid">
  <div><small>整体情绪</small><strong>{escape(str(tone.get("overall") or "—"))}</strong></div>
  <div><small>情绪波动</small><strong>{_safe_float(tone.get("fluctuation")):.0%}</strong></div>
  <div><small>情绪趋势</small><strong>{escape(str(tone.get("trend") or "—"))}</strong></div>
</div>
<h3>沟通模式</h3>
<p><b>主动度：</b>{escape(str(patterns.get("initiative_balance") or "—"))}</p>
<p><b>回应风格：</b>{escape(str(patterns.get("response_style") or "—"))}</p>
<p><b>主要话题：</b>{escape("、".join(topics))}</p>
{('<h3>关键事件</h3><ul class="events">' + event_rows + '</ul>') if event_rows else ''}
{('<h3>风险预警</h3>' + risk_rows) if risk_rows else ''}
{('<h3>个性化建议</h3><ol>' + advice_rows + '</ol>') if advice_rows else ''}
""")
    traits_html = block(f"{project['partner_b']} 的画像", f'<ul class="traits">{trait_rows}</ul>') if trait_rows else ""
    generated = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>恋爱分析报告 — {escape(project['name'])}</title>
<style>
  :root{{--ink:#263342;--muted:#5b6a7b;--accent:#a44d2c;--line:#e2e7ed;--soft:#f9f4ef;--surface:#fff;--ok:#226443;--err:#b3261e;--warn:#9a6700}}
  *{{box-sizing:border-box}}body{{margin:0;background:#f3f5f7;color:var(--ink);font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;line-height:1.75}}
  main{{max-width:820px;margin:0 auto;padding:40px 22px}}article{{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:38px 42px;box-shadow:0 10px 40px rgba(16,27,43,.06)}}
  .cover{{text-align:center;padding:20px 0 30px;border-bottom:1px solid var(--line);margin-bottom:12px}}.cover h1{{color:var(--accent);margin:0 0 8px;font-size:28px}}.cover p{{margin:4px 0;color:var(--muted);font-size:13px}}
  section{{margin:30px 0}}h2{{font-size:19px;border-bottom:2px solid var(--accent);padding-bottom:7px}}h3{{font-size:15px;margin:20px 0 8px}}p{{margin:8px 0}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:14px 0}}.grid>div{{background:var(--soft);border-radius:10px;padding:13px}}.grid small{{display:block;color:var(--muted);font-size:12px}}.grid strong{{display:block;font-size:17px;margin-top:3px}}
  .stage{{font-size:18px;color:var(--accent);font-weight:700}}.events{{list-style:none;padding:0}}.events li{{display:flex;gap:12px;flex-wrap:wrap;border-bottom:1px dashed var(--line);padding:9px 0}}.events em{{margin-left:auto;color:var(--muted);font-style:normal;font-size:12px}}
  .risk{{border-left:4px solid var(--warn);background:#fff8e8;border-radius:8px;padding:10px 14px;margin:9px 0}}.risk-high{{border-color:var(--err);background:#fdecea}}.risk p{{margin:4px 0;font-size:13px}}
  .traits{{list-style:none;padding:0}}.traits li{{display:flex;justify-content:space-between;gap:12px;background:var(--soft);border-radius:9px;padding:10px 13px;margin:7px 0}}.traits li.locked{{background:#eef7f1;border-left:4px solid var(--ok)}}.traits small{{color:var(--muted);white-space:nowrap}}
  footer{{margin-top:30px;padding-top:14px;border-top:1px solid var(--line);color:var(--muted);font-size:12px;text-align:center}}
  @media print{{body{{background:#fff}}main{{padding:0}}article{{border:0;box-shadow:none;padding:0}}}}
</style></head><body><main><article>
<div class="cover"><h1>❤️ 恋爱分析报告</h1><p>{escape(project['name'])}</p>
<p>{escape(project['partner_a'])} 与 {escape(project['partner_b'])} 的聊天记录分析</p>
<p>生成时间：{generated}</p><p>消息时间：{escape(str(overview.get('date_range_start') or '—'))} ~ {escape(str(overview.get('date_range_end') or '—'))}</p></div>
{block("数据概览", f'''<div class="grid">
<div><small>总消息数</small><strong>{overview['total_messages']}</strong></div>
<div><small>时间跨度</small><strong>{overview['total_days']} 天</strong></div>
<div><small>{escape(str(overview['sender_a']['name']))}</small><strong>{overview['sender_a']['messages']} 条 / {overview['sender_a']['ratio']:.0%}</strong></div>
<div><small>{escape(str(overview['sender_b']['name']))}</small><strong>{overview['sender_b']['messages']} 条 / {overview['sender_b']['ratio']:.0%}</strong></div>
</div>''')}
{analysis_html}
{traits_html}
<footer>由薪火OS「恋爱系统」在本机生成 · 数据只存储在本机 · {generated}</footer>
</article></main></body></html>"""


# ── 请求模型 ────────────────────────────────────────────────────────

class ProjectUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_PROJECT_NAME)


class ConfirmRequest(BaseModel):
    confirm: StrictBool


class TextImportRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_IMPORT_BYTES)
    file_name: str = Field(default="", max_length=200)
    project_name: str = Field(default="", max_length=MAX_PROJECT_NAME)


class AnalysisStartRequest(BaseModel):
    analysis_type: Literal["deep", "basic"] = "deep"


class TraitCreateRequest(BaseModel):
    trait: str = Field(min_length=1, max_length=MAX_TRAIT_KEY)
    value: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(default=1.0, ge=0, le=1)
    evidence: str = Field(default="手动添加", max_length=2000)


class TraitUpdateRequest(BaseModel):
    value: str = Field(min_length=1, max_length=2000)
    evidence: str | None = Field(default=None, max_length=2000)
    locked: bool | None = None


class TraitLockRequest(BaseModel):
    locked: StrictBool


class JournalCreateRequest(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    content: str = Field(min_length=1, max_length=MAX_JOURNAL_CONTENT)
    mood: str | None = Field(default=None, max_length=20)


class JournalUpdateRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=MAX_JOURNAL_CONTENT)
    mood: str | None = Field(default=None, max_length=20)


class JournalGenerateRequest(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class JournalBatchRequest(BaseModel):
    max_days: int = Field(default=10, ge=1, le=60)


class ApiKeyRequest(BaseModel):
    key: str = Field(min_length=8, max_length=300)


# ── 路由 ────────────────────────────────────────────────────────────

def create_love_router(store: LoveStore | None = None,
                       settings: LoveSettings | None = None) -> APIRouter:
    settings = settings or (store.settings if store else None) or LoveSettings.from_env()
    if store is None:
        store = LoveStore(settings.db_path, key_file=settings.key_file, settings=settings)
    elif store.settings is None:
        store.settings = settings
    router = APIRouter(prefix="/api/love", tags=["love"])

    def connection():
        try:
            store.ensure_schema()
            return store.connect()
        except Exception as exc:
            raise HTTPException(503, f"恋爱数据库不可用：{exc}") from exc

    @router.get("/status")
    def love_status():
        return store.status()

    @router.get("/settings")
    def get_settings():
        with connection() as con:
            row = _stored_api_key_row(con)
        return {
            "api_key_configured": bool(row),
            "api_base_url": settings.api_base_url,
            "api_model": settings.api_model,
            "data_dir": str(store.db_path.parent),
            "database": store.db_path.name,
        }

    @router.post("/settings/api-key")
    def save_api_key(payload: ApiKeyRequest):
        key = payload.key.strip()
        try:
            encrypted = _encrypt_key(store, key)
        except LoveKeyError as exc:
            raise HTTPException(500, str(exc)) from exc
        with connection() as con:
            con.execute("DELETE FROM settings WHERE key = 'api_key'")
            con.execute(
                "INSERT INTO settings (id, key, value, updated_at) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), "api_key", encrypted, _now_text()),
            )
        return {"status": "ok", "api_key_configured": True}

    @router.delete("/settings/api-key")
    def delete_api_key():
        with connection() as con:
            con.execute("DELETE FROM settings WHERE key = 'api_key'")
        return {"status": "ok", "api_key_configured": False}

    @router.get("/projects")
    def list_projects():
        with connection() as con:
            rows = con.execute(
                "SELECT p.*, "
                "(SELECT status FROM analysis_logs a WHERE a.project_id = p.id "
                " ORDER BY a.created_at DESC LIMIT 1) AS latest_analysis_status, "
                "(SELECT COUNT(*) FROM journal_entries j WHERE j.project_id = p.id) AS journal_count "
                "FROM projects p ORDER BY p.created_at DESC"
            ).fetchall()
        return {
            "projects": [
                {
                    **_project_json(row),
                    "latest_analysis_status": row["latest_analysis_status"],
                    "journal_count": int(row["journal_count"] or 0),
                }
                for row in rows
            ]
        }

    @router.get("/projects/{project_id}")
    def get_project(project_id: str):
        with connection() as con:
            project = _project(con, project_id)
            latest = _latest_analysis(con, project_id)
        data = _project_json(project)
        data["latest_analysis"] = _analysis_log_json(latest, include_result=False) if latest else None
        return data

    @router.put("/projects/{project_id}")
    def rename_project(project_id: str, payload: ProjectUpdate):
        with connection() as con:
            _project(con, project_id)
            con.execute(
                "UPDATE projects SET name = ?, updated_at = ? WHERE id = ?",
                (payload.name.strip(), _now_text(), project_id),
            )
        return {"status": "ok"}

    @router.delete("/projects/{project_id}")
    def delete_project(project_id: str, payload: ConfirmRequest):
        if payload.confirm is not True:
            raise HTTPException(422, "请明确确认删除项目")
        with connection() as con:
            _project(con, project_id)
            con.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return {"status": "deleted"}

    @router.post("/import/preview")
    async def preview_import(file: UploadFile = File(...)):
        raw = await _read_upload(file)
        try:
            parsed = await run_in_threadpool(_PARSER.decode_and_parse, raw)
            text = raw.decode(parsed.encoding, errors="replace")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _PARSER.preview(parsed, text.splitlines())

    @router.post("/import")
    async def import_chat(file: UploadFile = File(...),
                          project_name: str = Form(default="")):
        raw = await _read_upload(file)
        try:
            parsed = await run_in_threadpool(_PARSER.decode_and_parse, raw)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return await run_in_threadpool(
            _insert_import, store, parsed,
            project_name=project_name, file_name=file.filename or "",
        )

    @router.post("/import/text")
    def import_text(payload: TextImportRequest):
        parsed = _PARSER.parse(payload.content, encoding="utf-8")
        return _insert_import(store, parsed, project_name=payload.project_name,
                              file_name=payload.file_name)

    @router.get("/projects/{project_id}/messages")
    def list_messages(project_id: str, page: int = Query(default=1, ge=1),
                      page_size: int = Query(default=50, ge=1, le=200),
                      sender: str | None = None):
        with connection() as con:
            _project(con, project_id)
            where = "WHERE project_id = ?" + (" AND sender = ?" if sender else "")
            params: list[Any] = [project_id] + ([sender] if sender else [])
            total = con.execute(
                "SELECT COUNT(*) FROM messages " + where, params
            ).fetchone()[0]
            rows = con.execute(
                "SELECT id, timestamp, sender, content, COALESCE(is_system,0) AS is_system "
                "FROM messages " + where + " ORDER BY timestamp ASC LIMIT ? OFFSET ?",
                params + [page_size, (page - 1) * page_size],
            ).fetchall()
        return {
            "total": int(total), "page": page, "page_size": page_size,
            "messages": [
                {
                    "id": row["id"], "timestamp": _iso(row["timestamp"]),
                    "sender": row["sender"], "content": row["content"],
                    "is_system": bool(row["is_system"]),
                }
                for row in rows
            ],
        }

    @router.delete("/projects/{project_id}/messages")
    def clear_messages(project_id: str, payload: ConfirmRequest):
        if payload.confirm is not True:
            raise HTTPException(422, "请明确确认清空消息")
        with connection() as con:
            _project(con, project_id)
            con.execute("DELETE FROM messages WHERE project_id = ?", (project_id,))
            con.execute(
                "UPDATE projects SET message_count = 0, updated_at = ? WHERE id = ?",
                (_now_text(), project_id),
            )
        return {"status": "cleared"}

    @router.get("/projects/{project_id}/stats/overview")
    def stats_overview(project_id: str):
        with connection() as con:
            return _overview(con, _project(con, project_id))

    @router.get("/projects/{project_id}/stats/trends")
    def stats_trends(project_id: str,
                     granularity: str = Query(default="day", pattern="^(day|week|month)$")):
        with connection() as con:
            return _trends(con, _project(con, project_id), granularity)

    @router.get("/projects/{project_id}/stats/timing")
    def stats_timing(project_id: str):
        with connection() as con:
            _project(con, project_id)
            return _timing(con, project_id)

    @router.get("/projects/{project_id}/stats/media")
    def stats_media(project_id: str):
        with connection() as con:
            _project(con, project_id)
            return _media(con, project_id)

    @router.get("/projects/{project_id}/stats/keywords")
    def stats_keywords(project_id: str,
                       top_n: int = Query(default=100, ge=10, le=300)):
        with connection() as con:
            _project(con, project_id)
            return _keywords(con, project_id, top_n, settings)

    @router.post("/projects/{project_id}/analysis")
    def start_analysis(project_id: str, background: BackgroundTasks,
                       payload: AnalysisStartRequest = AnalysisStartRequest()):
        _api_key(store)  # 先验证 Key，再创建日志，界面上能立刻给出明确原因
        log_id = str(uuid.uuid4())
        now = _now_text()
        with connection() as con:
            _project(con, project_id)
            con.execute(
                "INSERT INTO analysis_logs (id, project_id, trigger_type, analysis_type, "
                "model, prompt_tokens, completion_tokens, status, created_at) "
                "VALUES (?,?,?,?,?,0,0,'running',?)",
                (log_id, project_id, "manual", payload.analysis_type, settings.api_model, now),
            )
        background.add_task(_run_analysis_job, store, settings, project_id, log_id)
        return {"log_id": log_id, "status": "running"}

    @router.get("/projects/{project_id}/analysis/logs")
    def analysis_logs(project_id: str):
        with connection() as con:
            _project(con, project_id)
            rows = con.execute(
                "SELECT * FROM analysis_logs WHERE project_id = ? "
                "ORDER BY created_at DESC LIMIT 20", (project_id,)
            ).fetchall()
        return {"logs": [_analysis_log_json(row, include_result=True) for row in rows]}

    @router.get("/projects/{project_id}/analysis/latest")
    def analysis_latest(project_id: str):
        with connection() as con:
            _project(con, project_id)
            row = _latest_analysis(con, project_id)
        return _analysis_log_json(row, include_result=True) if row else {"status": "none"}

    @router.get("/projects/{project_id}/analysis/status/{log_id}")
    def analysis_status(project_id: str, log_id: str):
        with connection() as con:
            _project(con, project_id)
            row = con.execute(
                "SELECT * FROM analysis_logs WHERE id = ? AND project_id = ?",
                (log_id, project_id),
            ).fetchone()
        if row is None:
            raise HTTPException(404, "分析记录不存在")
        return _analysis_log_json(row, include_result=True)

    @router.get("/projects/{project_id}/profile")
    def get_profile(project_id: str):
        with connection() as con:
            project = _project(con, project_id)
            profile = _profile_row(con, project["partner_b"])
        data = _profile_json(profile)
        data["partner_name"] = data["partner_name"] or project["partner_b"]
        return data

    @router.post("/projects/{project_id}/profile/traits")
    def add_trait(project_id: str, payload: TraitCreateRequest):
        with connection() as con:
            project = _project(con, project_id)
            profile = _profile_row(con, project["partner_b"])
            if profile is None:
                profile_id = str(uuid.uuid4())
                now = _now_text()
                con.execute(
                    "INSERT INTO partner_profiles (id, partner_name, project_id, traits, "
                    "is_locked, created_at, updated_at) VALUES (?,?,?,?,0,?,?)",
                    (profile_id, project["partner_b"], project_id, "[]", now, now),
                )
                traits: list[dict[str, Any]] = []
            else:
                profile_id = profile["id"]
                traits = _traits(profile)
            key = payload.trait.strip()
            existing = next((item for item in traits if item.get("trait") == key), None)
            if existing:
                existing.update({
                    "value": payload.value.strip(), "confidence": payload.confidence,
                    "evidence": payload.evidence.strip() or "手动添加",
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                })
            else:
                traits.append({
                    "trait": key, "value": payload.value.strip(),
                    "confidence": payload.confidence,
                    "evidence": payload.evidence.strip() or "手动添加",
                    "source_log_id": "manual", "locked": False,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "update_count": 0,
                })
            _save_traits(con, profile_id, traits)
            updated = con.execute(
                "SELECT * FROM partner_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
        return _profile_json(updated)

    @router.put("/projects/{project_id}/profile/traits/{trait_key}")
    def update_trait(project_id: str, trait_key: str, payload: TraitUpdateRequest):
        with connection() as con:
            project = _project(con, project_id)
            profile = _profile_row(con, project["partner_b"])
            if profile is None:
                raise HTTPException(404, "画像不存在")
            traits = _traits(profile)
            matched = next((item for item in traits if item.get("trait") == trait_key), None)
            if matched is None:
                raise HTTPException(404, "特质不存在")
            matched["value"] = payload.value.strip()
            matched["updated_at"] = datetime.now().isoformat(timespec="seconds")
            if payload.evidence is not None:
                matched["evidence"] = payload.evidence.strip()
            if payload.locked is not None:
                matched["locked"] = bool(payload.locked)
            _save_traits(con, profile["id"], traits)
            updated = _profile_row(con, project["partner_b"])
        return _profile_json(updated)

    @router.put("/projects/{project_id}/profile/traits/{trait_key}/lock")
    def lock_trait(project_id: str, trait_key: str, payload: TraitLockRequest):
        with connection() as con:
            project = _project(con, project_id)
            profile = _profile_row(con, project["partner_b"])
            if profile is None:
                raise HTTPException(404, "画像不存在")
            traits = _traits(profile)
            matched = next((item for item in traits if item.get("trait") == trait_key), None)
            if matched is None:
                raise HTTPException(404, "特质不存在")
            matched["locked"] = bool(payload.locked)
            matched["locked_at"] = (
                datetime.now().isoformat(timespec="seconds") if payload.locked else None
            )
            _save_traits(con, profile["id"], traits)
            updated = _profile_row(con, project["partner_b"])
        return _profile_json(updated)

    @router.delete("/projects/{project_id}/profile/traits/{trait_key}")
    def delete_trait(project_id: str, trait_key: str):
        with connection() as con:
            project = _project(con, project_id)
            profile = _profile_row(con, project["partner_b"])
            if profile is None:
                raise HTTPException(404, "画像不存在")
            traits = [item for item in _traits(profile) if item.get("trait") != trait_key]
            _save_traits(con, profile["id"], traits)
            updated = _profile_row(con, project["partner_b"])
        return _profile_json(updated)

    @router.get("/projects/{project_id}/journal")
    def list_journal(project_id: str, year: int | None = Query(default=None),
                     month: int | None = Query(default=None, ge=1, le=12)):
        with connection() as con:
            _project(con, project_id)
            sql = "SELECT * FROM journal_entries WHERE project_id = ?"
            params: list[Any] = [project_id]
            if year:
                sql += " AND strftime('%Y', date) = ?"
                params.append(str(year))
                if month:
                    sql += " AND strftime('%m', date) = ?"
                    params.append(f"{month:02d}")
            sql += " ORDER BY date DESC, created_at DESC"
            rows = con.execute(sql, params).fetchall()
        return {"entries": [_journal_json(row) for row in rows]}

    @router.post("/projects/{project_id}/journal")
    def create_journal(project_id: str, payload: JournalCreateRequest):
        _validate_day(payload.date)
        with connection() as con:
            _project(con, project_id)
            entry_id = str(uuid.uuid4())
            now = _now_text()
            con.execute(
                "INSERT INTO journal_entries (id, project_id, date, content, mood, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (entry_id, project_id, payload.date, payload.content.strip(),
                 payload.mood, now, now),
            )
            row = con.execute(
                "SELECT * FROM journal_entries WHERE id = ?", (entry_id,)
            ).fetchone()
        return _journal_json(row)

    @router.put("/projects/{project_id}/journal/{entry_id}")
    def update_journal(project_id: str, entry_id: str, payload: JournalUpdateRequest):
        with connection() as con:
            _project(con, project_id)
            row = con.execute(
                "SELECT * FROM journal_entries WHERE id = ? AND project_id = ?",
                (entry_id, project_id),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "日志不存在")
            content = payload.content.strip() if payload.content is not None else row["content"]
            mood = payload.mood if payload.mood is not None else row["mood"]
            con.execute(
                "UPDATE journal_entries SET content = ?, mood = ?, updated_at = ? WHERE id = ?",
                (content, mood, _now_text(), entry_id),
            )
            updated = con.execute(
                "SELECT * FROM journal_entries WHERE id = ?", (entry_id,)
            ).fetchone()
        return _journal_json(updated)

    @router.delete("/projects/{project_id}/journal/{entry_id}")
    def delete_journal(project_id: str, entry_id: str):
        with connection() as con:
            _project(con, project_id)
            deleted = con.execute(
                "DELETE FROM journal_entries WHERE id = ? AND project_id = ?",
                (entry_id, project_id),
            ).rowcount
        if not deleted:
            raise HTTPException(404, "日志不存在")
        return {"status": "deleted"}

    @router.post("/projects/{project_id}/journal/generate")
    def generate_journal(project_id: str, payload: JournalGenerateRequest):
        _validate_day(payload.date)
        api_key = _api_key(store)
        with connection() as con:
            project = _project(con, project_id)
            messages = con.execute(
                "SELECT timestamp, sender, content FROM messages WHERE project_id = ? "
                "AND strftime('%Y-%m-%d', timestamp) = ? AND COALESCE(is_system,0) = 0 "
                "ORDER BY timestamp ASC", (project_id, payload.date),
            ).fetchall()
        try:
            content = _journal_for_date(settings, api_key, project, messages, payload.date)
        except ValueError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"content": content, "date": payload.date}

    @router.post("/projects/{project_id}/journal/generate-all")
    def generate_journal_batch(project_id: str, payload: JournalBatchRequest):
        api_key = _api_key(store)
        with connection() as con:
            project = _project(con, project_id)
            date_rows = con.execute(
                "SELECT DISTINCT strftime('%Y-%m-%d', timestamp) AS day FROM messages "
                "WHERE project_id = ? AND COALESCE(is_system,0) = 0 ORDER BY day",
                (project_id,),
            ).fetchall()
            existing = {
                row["date"] for row in con.execute(
                    "SELECT date FROM journal_entries WHERE project_id = ?", (project_id,)
                ).fetchall()
            }
        pending = [row["day"] for row in date_rows if row["day"] not in existing][:payload.max_days]
        if not pending:
            return {"generated": 0, "failed": 0, "message": "所有聊天日期都已有日志"}
        generated = failed = 0
        generated_dates: list[str] = []
        failures: list[str] = []
        for day in pending:
            # 网络请求期间不持有数据库连接；每天只读一次、写一次，避免长时间锁库。
            with connection() as con:
                messages = con.execute(
                    "SELECT timestamp, sender, content FROM messages WHERE project_id = ? "
                    "AND strftime('%Y-%m-%d', timestamp) = ? AND COALESCE(is_system,0) = 0 "
                    "ORDER BY timestamp ASC", (project_id, day),
                ).fetchall()
            if not messages:
                continue
            try:
                content = _journal_for_date(settings, api_key, project, messages, day)
            except Exception as exc:
                failed += 1
                failures.append(f"{day}: {str(exc)[:120]}")
                continue
            with connection() as con:
                now = _now_text()
                con.execute(
                    "INSERT INTO journal_entries (id, project_id, date, content, mood, "
                    "created_at, updated_at) VALUES (?,?,?,?,NULL,?,?)",
                    (str(uuid.uuid4()), project_id, day, content, now, now),
                )
            generated += 1
            generated_dates.append(day)
        return {
            "generated": generated, "failed": failed,
            "generated_dates": generated_dates, "failures": failures[:10],
            "message": f"生成 {generated} 篇，失败 {failed} 篇",
        }

    @router.get("/projects/{project_id}/journal/export")
    def export_journal(project_id: str):
        from fastapi.responses import Response

        with connection() as con:
            project = _project(con, project_id)
            rows = con.execute(
                "SELECT * FROM journal_entries WHERE project_id = ? ORDER BY date ASC",
                (project_id,),
            ).fetchall()
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        moods = {"happy": "😊", "sad": "😢", "angry": "😠", "neutral": "😐",
                 "excited": "🎉", "love": "❤️", "miss": "💭"}
        lines = ["=" * 50, f"  恋爱日志 — {project['name']}"]
        if rows:
            lines.append(f"  时间范围: {rows[0]['date']} ~ {rows[-1]['date']}")
        lines += ["=" * 50, ""]
        for row in rows:
            try:
                weekday = weekdays[date.fromisoformat(str(row["date"])[:10]).weekday()]
            except ValueError:
                weekday = ""
            lines.append(f"{row['date']} ({weekday}) {moods.get(row['mood'] or '', '')}".strip())
            lines.append(row["content"])
            lines.append("")
        payload = "\n".join(lines).encode("utf-8-sig")
        name = str(project["name"]).replace("/", "_").replace("\\", "_").replace(" ", "_")
        from urllib.parse import quote

        filename = f"{name}_恋爱日志.txt"
        return Response(
            content=payload, media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition":
                    f"attachment; filename=\"journal.txt\"; "
                    f"filename*=UTF-8''{quote(filename)}"
            },
        )

    def report_payload(project_id: str) -> dict[str, Any]:
        with connection() as con:
            project = _project(con, project_id)
            overview = _overview(con, project)
            latest = _latest_analysis(con, project_id, completed_only=True)
            profile = _profile_row(con, project["partner_b"])
        analysis = None
        if latest and latest["result_json"]:
            try:
                analysis = json.loads(latest["result_json"])
            except json.JSONDecodeError:
                analysis = None
        return {
            "html": _report_html(project, overview, analysis, _traits(profile)),
            "project_name": project["name"],
            "has_analysis": analysis is not None,
        }

    @router.post("/projects/{project_id}/report")
    def generate_report(project_id: str):
        return report_payload(project_id)

    # 报告预览用独立文档承载：报告自带 <style>，而工作台的 CSP 默认禁止内联样式；
    # 这个路由在 application.py 里拿到只允许样式、不允许脚本的窄 CSP，供 iframe/新标签打印。
    @router.get("/projects/{project_id}/report/preview", include_in_schema=False)
    def preview_report(project_id: str):
        result = report_payload(project_id)
        return HTMLResponse(result["html"])

    return router


def _validate_day(value: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, "日期格式应为 YYYY-MM-DD") from exc


async def _read_upload(file: UploadFile) -> bytes:
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(413, f"文件超过 {MAX_IMPORT_BYTES // 1024 // 1024}MB 上限")
    if not raw:
        raise HTTPException(400, "文件内容为空")
    return raw


def _journal_json(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "date": _date_text(row["date"]),
        "content": row["content"],
        "mood": row["mood"],
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def _analysis_log_json(row: sqlite3.Row | None,
                       *, include_result: bool = False) -> dict[str, Any]:
    if row is None:
        return {"status": "none"}
    data: dict[str, Any] = {
        "id": row["id"],
        "project_id": row["project_id"],
        "status": row["status"],
        "analysis_type": row["analysis_type"],
        "model": row["model"],
        "trigger_type": row["trigger_type"],
        "prompt_tokens": int(row["prompt_tokens"] or 0),
        "completion_tokens": int(row["completion_tokens"] or 0),
        "created_at": _iso(row["created_at"]),
        "completed_at": _iso(row["completed_at"]),
        "error": row["error_message"] or "",
    }
    if include_result and row["result_json"]:
        try:
            data["result"] = json.loads(row["result_json"])
        except json.JSONDecodeError:
            data["result"] = {"raw": row["result_json"]}
    return data
