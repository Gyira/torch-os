"""Explicit, read-only export of saved conversations as Markdown or JSON.

The snapshot keeps the stored answer, its citations and timestamps. It never
calls a model, never creates a message and never changes review state; the
audit event carries counts only.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Response

from .db import Database


SHANGHAI = ZoneInfo("Asia/Shanghai")
ROLE_LABELS = {"user": "你", "assistant": "回答"}


class ConversationExportService:
    def __init__(self, database: Database):
        self.database = database

    def collect(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            conversation_rows = [dict(row) for row in connection.execute(
                """SELECT id,title,created_at,updated_at FROM conversations
                ORDER BY updated_at DESC, id DESC"""
            ).fetchall()]
            message_rows = [dict(row) for row in connection.execute(
                """SELECT id,conversation_id,role,content,citations_json,created_at
                FROM messages ORDER BY conversation_id, id"""
            ).fetchall()]
        by_conversation: dict[int, list[dict[str, Any]]] = {}
        counts = {"conversations": len(conversation_rows), "messages": 0, "user_messages": 0,
                  "assistant_messages": 0, "citations": 0}
        for message in message_rows:
            try:
                citations = json.loads(message.pop("citations_json") or "[]")
            except (TypeError, ValueError):
                citations = []
            if not isinstance(citations, list):
                citations = []
            message["citations"] = citations
            counts["messages"] += 1
            counts["citations"] += len(citations)
            if message.get("role") == "user":
                counts["user_messages"] += 1
            elif message.get("role") == "assistant":
                counts["assistant_messages"] += 1
            by_conversation.setdefault(int(message["conversation_id"]), []).append(message)
        conversations = [
            {**row, "messages": by_conversation.get(int(row["id"]), [])}
            for row in conversation_rows
        ]
        return {
            "schema": 1,
            "exported_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "counts": counts,
            "conversations": conversations,
        }


def _stamp() -> str:
    return datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")


def _citation_lines(citations: list[Any]) -> list[str]:
    lines: list[str] = []
    for index, citation in enumerate(citations, start=1):
        if not isinstance(citation, dict):
            continue
        title = " ".join(str(citation.get("title") or "未命名记忆").split())
        lines.append(f"  - 引用 {index}：{title}")
        quote = str(citation.get("quote") or "").strip()
        if quote:
            lines.append("    > " + " ".join(quote.split()))
        path = str(citation.get("path") or "").strip()
        if path:
            lines.append(f"    来源：{path}")
    return lines


def render_markdown(payload: dict[str, Any]) -> str:
    counts = payload["counts"]
    lines = [
        "# 薪火OS 对话导出",
        "",
        f"- 导出时间：{payload['exported_at']}（Asia/Shanghai）",
        f"- 对话 {counts['conversations']} · 消息 {counts['messages']}"
        f"（你 {counts['user_messages']} · 回答 {counts['assistant_messages']}）· 引用 {counts['citations']}",
        "- 这是本地只读快照：包含当时保存的回答与引用；撤回记忆不会改写这里的历史，也不会调用模型。",
        "",
    ]
    if not payload["conversations"]:
        lines.append("（还没有保存的对话）")
        return "\n".join(lines).rstrip() + "\n"
    for conversation in payload["conversations"]:
        title = " ".join(str(conversation.get("title") or "未命名对话").split())
        lines.append(f"## 对话：{title}")
        lines.append(
            f"- 创建：{conversation.get('created_at') or '未记录'}"
            f" · 更新：{conversation.get('updated_at') or '未记录'}"
        )
        lines.append("")
        for message in conversation["messages"]:
            role = ROLE_LABELS.get(message.get("role"), message.get("role") or "消息")
            lines.append(f"### {role}（{message.get('created_at') or '未记录'}）")
            lines.append("")
            lines.append(str(message.get("content") or "").rstrip())
            lines.append("")
            citation_lines = _citation_lines(message["citations"])
            if citation_lines:
                lines.extend(citation_lines)
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def create_conversation_export_router(database: Database, prefix: str = "/api") -> APIRouter:
    service = ConversationExportService(database)
    router = APIRouter(prefix=prefix, tags=["conversation-export"])

    def _record(payload: dict[str, Any], export_format: str) -> None:
        counts = payload["counts"]
        database.audit("conversations_exported", {
            "format": export_format,
            "conversations": counts["conversations"],
            "messages": counts["messages"],
            "citations": counts["citations"],
        })

    @router.get("/export/conversations.md")
    def export_markdown():
        payload = service.collect()
        _record(payload, "markdown")
        return Response(
            content=render_markdown(payload),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="torch-os-conversations-{_stamp()}.md"'},
        )

    @router.get("/export/conversations.json")
    def export_json():
        payload = service.collect()
        _record(payload, "json")
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="torch-os-conversations-{_stamp()}.json"'},
        )

    return router
