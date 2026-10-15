"""Explicit, read-only memory export as Markdown or JSON.

The user owns their memories (project principle: view, edit, disable, delete
and export). This router renders a portable snapshot of the current review
states. It never writes notes, never calls a model and records only one audit
event with counts — never memory content.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Response

from .db import Database


SHANGHAI = ZoneInfo("Asia/Shanghai")
ExportFilter = Literal["all", "candidate", "active", "rejected", "archived"]
STATUS_LABELS = {"active": "已确认", "candidate": "待确认", "rejected": "已拒绝", "archived": "已归档"}
STATUS_ORDER = ("active", "candidate", "rejected", "archived")
KIND_LABELS = {"fact": "事实", "experience": "经历", "belief": "信念", "goal": "目标", "preference": "偏好", "note": "记录"}


def _source_location(row: dict[str, Any]) -> str:
    start = row.get("source_start_line")
    end = row.get("source_end_line")
    positioned = (
        row.get("source_location_basis") == "document_body"
        and isinstance(start, int) and start > 0
        and isinstance(end, int) and end >= start
    )
    if not positioned:
        return "来源未记录正文行号"
    span = f"{start}" if end == start else f"{start}–{end}"
    return f"资料正文第 {span} 行（已去除文首元数据，非原始文件行号）"


class MemoryExportService:
    def __init__(self, database: Database):
        self.database = database

    def collect(self, status: ExportFilter) -> dict[str, Any]:
        memories = self.database.list_memories(None if status == "all" else status)
        usage = self.database.memory_usage_stats(30)
        counts = {key: 0 for key in STATUS_ORDER}
        rows: list[dict[str, Any]] = []
        for memory in memories:
            memory_status = str(memory.get("status") or "")
            if memory_status in counts:
                counts[memory_status] += 1
            stats = usage.get(int(memory["id"])) or {}
            rows.append({
                "id": int(memory["id"]),
                "kind": memory.get("kind"),
                "title": memory.get("title") or "",
                "content": memory.get("content") or "",
                "status": memory.get("status"),
                "confidence": memory.get("confidence"),
                "revision": memory.get("revision"),
                "source_path": memory.get("source_path"),
                "source_quote": memory.get("source_quote"),
                "source_location": _source_location(memory),
                "created_at": memory.get("created_at"),
                "updated_at": memory.get("updated_at"),
                "usage_recent": int(stats.get("uses") or 0),
            })
        return {
            "schema": 1,
            "exported_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "filter": status,
            "total": len(rows),
            "counts": counts,
            "memories": rows,
        }


def render_markdown(payload: dict[str, Any]) -> str:
    counts = payload["counts"]
    summary = " · ".join(f"{STATUS_LABELS[key]} {counts.get(key, 0)}" for key in STATUS_ORDER if counts.get(key))
    lines = [
        "# 薪火OS 记忆导出",
        "",
        f"- 导出时间：{payload['exported_at']}（Asia/Shanghai）",
        f"- 筛选：{'全部状态' if payload['filter'] == 'all' else STATUS_LABELS.get(payload['filter'], payload['filter'])}",
        f"- 条目：{payload['total']}" + (f"（{summary}）" if summary else ""),
        "- 这是本地导出，不改变任何审核状态；来源路径与行号指向本地原始资料。",
        "",
    ]
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in STATUS_ORDER}
    for row in payload["memories"]:
        grouped.setdefault(str(row["status"]), []).append(row)
    for status in STATUS_ORDER:
        rows = grouped.get(status) or []
        if not rows:
            continue
        lines.append(f"## {STATUS_LABELS[status]}（{len(rows)}）")
        lines.append("")
        for row in rows:
            title = " ".join(str(row["title"]).split()) or "未命名记忆"
            lines.append(f"### {title}")
            meta = [f"类型：{KIND_LABELS.get(row['kind'], row['kind'] or '未标注')}", f"版本：{row['revision']}"]
            if row["usage_recent"]:
                meta.append(f"近 30 天被回答引用 {row['usage_recent']} 次")
            lines.append("- " + " · ".join(meta))
            lines.append(f"- 来源：{row['source_path'] or '未标注'} · {row['source_location']}")
            if row["source_quote"]:
                quote = " ".join(str(row["source_quote"]).split())
                lines.append(f"- 来源片段：{quote}")
            lines.append("")
            lines.append(str(row["content"]).rstrip())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _attachment(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


def create_memory_export_router(database: Database, prefix: str = "/api") -> APIRouter:
    service = MemoryExportService(database)
    router = APIRouter(prefix=prefix, tags=["memory-export"])

    def _stamp() -> str:
        return datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")

    def _record(payload: dict[str, Any], export_format: str) -> None:
        database.audit("memory_exported", {
            "format": export_format, "filter": payload["filter"],
            "total": payload["total"], "counts": payload["counts"],
        })

    @router.get("/export/memories.md")
    def export_markdown(status: ExportFilter = Query(default="all")):
        payload = service.collect(status)
        _record(payload, "markdown")
        return Response(
            content=render_markdown(payload),
            media_type="text/markdown",
            headers=_attachment(f"torch-os-memories-{_stamp()}.md"),
        )

    @router.get("/export/memories.json")
    def export_json(status: ExportFilter = Query(default="all")):
        payload = service.collect(status)
        _record(payload, "json")
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            media_type="application/json",
            headers=_attachment(f"torch-os-memories-{_stamp()}.json"),
        )

    return router
