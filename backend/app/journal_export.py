"""Explicit, read-only export of every saved daily record.

Body measurements live in their own export; this snapshot contains the
structured journal fields (content, highlights, obstacles, diet, mood,
next step) exactly as saved. Audit carries counts only.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Response

from .db import Database
from .journal import FIELD_LABELS, TEXT_FIELDS


SHANGHAI = ZoneInfo("Asia/Shanghai")


class JournalExportService:
    def __init__(self, database: Database):
        self.database = database

    def collect(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM journal_entries ORDER BY date DESC"
            ).fetchall()]
        return {
            "schema": 1,
            "exported_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "counts": {
                "days": len(rows),
                "first_date": rows[-1]["date"] if rows else None,
                "last_date": rows[0]["date"] if rows else None,
            },
            "entries": rows,
        }


def render_markdown(payload: dict[str, Any]) -> str:
    counts = payload["counts"]
    span = (
        f"（{counts['first_date']} 至 {counts['last_date']}）"
        if counts["days"] else ""
    )
    lines = [
        "# 薪火OS 每日记录导出",
        "",
        f"- 导出时间：{payload['exported_at']}（Asia/Shanghai）",
        f"- 已保存 {counts['days']} 天记录{span}",
        "- 本地只读快照；身体记录在单独的“身体记录导出”里，不会重复出现在这里。",
        "",
    ]
    if not payload["entries"]:
        lines.append("（还没有保存的每日记录）")
        return "\n".join(lines).rstrip() + "\n"
    for entry in payload["entries"]:
        lines.append(f"## {entry['date']}")
        lines.append("")
        if entry.get("goal_id"):
            lines.append(f"- 关联目标：{entry['goal_id']}")
            lines.append("")
        for field in TEXT_FIELDS:
            value = str(entry.get(field) or "").strip()
            if not value:
                continue
            lines.append(f"### {FIELD_LABELS.get(field, field)}")
            lines.append("")
            lines.append(value)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def create_journal_export_router(database: Database, prefix: str = "/api") -> APIRouter:
    service = JournalExportService(database)
    router = APIRouter(prefix=prefix, tags=["journal-export"])

    def _stamp() -> str:
        return datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")

    def _record(payload: dict[str, Any], export_format: str) -> None:
        database.audit("journal_bulk_exported", {
            "format": export_format,
            "days": payload["counts"]["days"],
        })

    @router.get("/export/journal.md")
    def export_markdown():
        payload = service.collect()
        _record(payload, "markdown")
        return Response(
            content=render_markdown(payload),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="torch-os-journal-{_stamp()}.md"'},
        )

    @router.get("/export/journal.json")
    def export_json():
        payload = service.collect()
        _record(payload, "json")
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="torch-os-journal-{_stamp()}.json"'},
        )

    return router
