"""Read-only audit summary: counts of what happened, never any content.

The transparency panel answers "how many exports/backups happened?" and shows
scheduler/dsh errors using event types and counts only — no note text, question
text, titles or paths. AI conversations are managed by DeepSeek Harness.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query

from .db import Database


SHANGHAI = ZoneInfo("Asia/Shanghai")
ERROR_TYPES = ("scheduler_loop_error", "dsh_web_error")
EXPORT_SUFFIX = "_exported"
MAX_TYPES = 40


def collect_summary(database: Database, days: int) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    placeholders = ",".join("?" for _ in ERROR_TYPES)
    with database.connect() as connection:
        type_rows = [dict(row) for row in connection.execute(
            """SELECT event_type, COUNT(*) AS n, MAX(created_at) AS last_at
               FROM audit_events WHERE created_at>=?
               GROUP BY event_type ORDER BY n DESC, event_type""",
            (since,),
        ).fetchall()]
        error_rows = connection.execute(
            f"""SELECT event_type, created_at FROM audit_events
                WHERE event_type IN ({placeholders}) AND created_at>=?
                ORDER BY created_at DESC, id DESC""",
            (*ERROR_TYPES, since),
        ).fetchall()

    by_type = [
        {"event_type": row["event_type"], "count": int(row["n"]), "last_at": row["last_at"]}
        for row in type_rows
    ]
    exports = sum(item["count"] for item in by_type if item["event_type"].endswith(EXPORT_SUFFIX))
    backups = sum(item["count"] for item in by_type if item["event_type"] == "backup_created")
    errors = sum(item["count"] for item in by_type if item["event_type"] in ERROR_TYPES)
    latest_error = None
    if error_rows:
        latest_error = {"event_type": error_rows[0]["event_type"], "created_at": error_rows[0]["created_at"]}
    return {
        "generated_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "window_days": days,
        "total_events": sum(item["count"] for item in by_type),
        "exports": exports,
        "backups": backups,
        "errors": errors,
        "latest_error": latest_error,
        "by_type": by_type[:MAX_TYPES],
        "note": "只读统计：只有事件类型与数量，不含正文、问题、标题或路径。",
    }


def create_audit_summary_router(database: Database, prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["audit-summary"])

    @router.get("/audit/summary")
    def summary(days: int = Query(default=30, ge=1, le=365)):
        return collect_summary(database, days)

    return router
