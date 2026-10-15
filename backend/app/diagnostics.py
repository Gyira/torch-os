"""Read-only local self-check: integrity, table counts, vault files, latest backup.

It never reads note contents, never writes an audit event and never calls a
model. The payload contains counts and file numbers only, so it stays safe to
look at and safe to screenshot.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from .backup import BackupError, verify_backup
from .db import Database


SHANGHAI = ZoneInfo("Asia/Shanghai")
RELATIONSHIP_DIRS = {"relationships", "relationship", "关系"}
COUNT_TABLES = (
    ("documents_available", "documents", "SELECT COUNT(*) FROM documents WHERE available=1"),
    ("memories_active", "memories", "SELECT COUNT(*) FROM memories WHERE status='active'"),
    ("memories_candidate", "memories", "SELECT COUNT(*) FROM memories WHERE status='candidate'"),
    ("memories_rejected", "memories", "SELECT COUNT(*) FROM memories WHERE status='rejected'"),
    ("memories_archived", "memories", "SELECT COUNT(*) FROM memories WHERE status='archived'"),
    ("conversations", "conversations", "SELECT COUNT(*) FROM conversations"),
    ("messages", "messages", "SELECT COUNT(*) FROM messages"),
    ("goals", "goals", "SELECT COUNT(*) FROM goals"),
    ("tasks", "tasks", "SELECT COUNT(*) FROM tasks"),
    ("journal_entries", "journal_entries", "SELECT COUNT(*) FROM journal_entries"),
    ("habit_entries", "habit_entries", "SELECT COUNT(*) FROM habit_entries"),
    ("study_sessions", "study_sessions", "SELECT COUNT(*) FROM study_sessions"),
    ("body_entries", "body_entries", "SELECT COUNT(*) FROM body_entries"),
    ("inbox_items", "inbox_items", "SELECT COUNT(*) FROM inbox_items"),
    ("memory_usages", "memory_usages", "SELECT COUNT(*) FROM memory_usages"),
    ("audit_events", "audit_events", "SELECT COUNT(*) FROM audit_events"),
)


def _table_counts(connection) -> dict[str, int]:
    present = {
        row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return {
        label: int(connection.execute(sql).fetchone()[0]) if table in present else 0
        for label, table, sql in COUNT_TABLES
    }


def _vault_numbers(vault_dir: Path) -> dict[str, Any]:
    markdown_files = 0
    relationships_present = False
    root = Path(vault_dir)
    if root.exists():
        for path in root.rglob("*"):
            if path.is_symlink():
                continue
            if path.is_dir():
                if path.name.casefold() in RELATIONSHIP_DIRS:
                    relationships_present = True
                continue
            if any(part.casefold() in RELATIONSHIP_DIRS for part in path.relative_to(root).parts[:-1]):
                continue
            if path.suffix.casefold() == ".md":
                markdown_files += 1
    return {"markdown_files": markdown_files, "relationships_present": relationships_present}


def _latest_backup(database: Database) -> dict[str, Any]:
    folder = database.path.parent / "backups"
    archives = []
    if folder.exists():
        archives = [
            path for path in folder.glob("*.zip")
            if path.is_file() and not path.is_symlink()
        ]
    if not archives:
        return {"count": 0, "latest": None,
                "verification": {"checked": None, "ok": None, "error": None, "relationship_files": None}}
    archives.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    latest = archives[0]
    stat = latest.stat()
    return {
        "count": len(archives),
        "latest": {
            "filename": latest.name,
            "bytes": int(stat.st_size),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, SHANGHAI).isoformat(timespec="seconds"),
        },
        "verification": _verify_latest(latest),
    }


def _verify_latest(latest: Path) -> dict[str, Any]:
    """Re-verify the newest archive end to end; failures are reported, not raised."""
    try:
        summary = verify_backup(latest)
    except BackupError:
        return {"checked": latest.name, "ok": False, "error": "最近备份未通过完整校验", "relationship_files": None}
    except Exception:
        return {"checked": latest.name, "ok": False, "error": "最近备份校验时发生未知错误", "relationship_files": None}
    return {
        "checked": latest.name,
        "ok": True,
        "error": None,
        "relationship_files": int(summary.get("relationship_files") or 0),
    }


def collect_summary(database: Database, vault_dir: Path) -> dict[str, Any]:
    with database.connect() as connection:
        row = connection.execute("PRAGMA quick_check").fetchone()
        integrity = str(row[0]) if row else "unknown"
        counts = _table_counts(connection)
    return {
        "generated_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "integrity": integrity,
        "counts": counts,
        "vault": _vault_numbers(vault_dir),
        "backups": _latest_backup(database),
        "embedding_provider": getattr(database.embedding_provider, "name", None),
    }


def create_diagnostics_router(
    database: Database, vault_dir: Path, version: str, prefix: str = "/api"
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["diagnostics"])

    @router.get("/diagnostics/summary")
    def summary():
        payload = collect_summary(database, vault_dir)
        payload["version"] = version
        return payload

    return router
