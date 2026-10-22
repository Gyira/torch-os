"""记录模块：今天在保险库里动过哪些文件（按真实 mtime）——日记自动汇总与预填的原料。

- 纯读：走保险库 `.md`，用 `vault.restricted_path` 排除系统目录（00-System 等）与隐藏目录；
- 按上海时区把 mtime 归到"天"，只取目标日期的文件，按修改时间倒序；
- 已索引的文件用 `documents` 表里的标题，未索引的用文件名去后缀；
- 说明：`documents.indexed_at` 是"扫描时发现变化"的时间，不能替代真实 mtime，所以这里
  必须现场 stat 文件——这层扫描也正是"前置记录模块"的数据来源。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query

from .db import Database
from .journal_shared import validate_day
from .vault import restricted_path

SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_ACTIVITY = 80
MAX_FILES_SCANNED = 20_000


def list_day_activity(database: Database, vault_dir: Path | str, day: str) -> list[dict[str, Any]]:
    root = Path(vault_dir)
    if not root.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    scanned = 0
    for path in root.rglob("*.md"):
        scanned += 1
        if scanned > MAX_FILES_SCANNED:
            break
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if restricted_path(relative) or path.is_symlink():
            continue
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        moment = datetime.fromtimestamp(stat.st_mtime, SHANGHAI)
        if moment.date().isoformat() != day:
            continue
        entries.append({
            "path": relative.as_posix(),
            "mtime": moment.isoformat(timespec="seconds"),
            "epoch": stat.st_mtime,
            "size": stat.st_size,
        })
    entries.sort(key=lambda item: item["epoch"], reverse=True)
    del entries[MAX_ACTIVITY:]

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT path, title FROM documents WHERE available=1").fetchall()
    titles = {row["path"]: row["title"] for row in rows}
    for entry in entries:
        entry["title"] = titles.get(entry["path"]) or Path(entry["path"]).stem
    return entries


def count_activity_days(vault_dir: Path | str, end_day: str,
                        days: int = 14) -> dict[str, int]:
    """最近 N 天里每天动过几个文件（一次 walk 分桶）——横幅热力图的原料。"""
    root = Path(vault_dir)
    end = datetime.fromisoformat(end_day).date()
    start = end - timedelta(days=days - 1)
    buckets: dict[str, int] = {}
    if not root.is_dir():
        return {(start + timedelta(days=offset)).isoformat(): 0 for offset in range(days)}
    scanned = 0
    for path in root.rglob("*.md"):
        scanned += 1
        if scanned > MAX_FILES_SCANNED:
            break
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if restricted_path(relative) or path.is_symlink():
            continue
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        moment = datetime.fromtimestamp(stat.st_mtime, SHANGHAI).date()
        if start <= moment <= end:
            key = moment.isoformat()
            buckets[key] = buckets.get(key, 0) + 1
    return {(start + timedelta(days=offset)).isoformat(): buckets.get(
        (start + timedelta(days=offset)).isoformat(), 0) for offset in range(days)}


def create_activity_router(database: Database, vault_dir: Path | str,
                           prefix: str = "/api") -> APIRouter:
    """挂载时套用应用级数据锁（与扫描/日记写入同一把），避免并发 walk。"""
    router = APIRouter(prefix=prefix, tags=["activity"])

    @router.get("/vault/activity")
    def vault_activity(day: str = Query(default="")):
        target = validate_day(day).isoformat() if day else datetime.now(SHANGHAI).date().isoformat()
        entries = list_day_activity(database, vault_dir, target)
        return {"day": target, "count": len(entries), "files": entries}

    return router
