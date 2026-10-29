"""Read-only export of v1.0 role-drawer threads, turns and proposals.

The export bundle is for reading and migration, not restore.  This service only
reads ``ai_threads``/``ai_turns``/``ai_proposals`` and never calls a model, never
changes proposal state, and never exposes the dsh session id or verdict token.
The bundle router records one count-only audit row.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .db import Database

SHANGHAI = ZoneInfo("Asia/Shanghai")
ROLE_LABELS = {"user": "你", "assistant": "助手"}


def _loads(value: Any, fallback: Any) -> Any:
    try:
        data = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return fallback
    return data if isinstance(data, type(fallback)) else fallback


class AIExportService:
    def __init__(self, database: Database):
        self.database = database

    def collect(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            thread_rows = [dict(row) for row in connection.execute(
                "SELECT id,role_id,mode,title,created_at,updated_at,archived_at "
                "FROM ai_threads ORDER BY updated_at DESC, id DESC"
            ).fetchall()]
            turn_rows = [dict(row) for row in connection.execute(
                "SELECT thread_id,turn_index,role,content,sources_json,"
                "proposals_json,structured_json,seconds,created_at "
                "FROM ai_turns ORDER BY thread_id, turn_index ASC"
            ).fetchall()]
            proposal_rows = [dict(row) for row in connection.execute(
                "SELECT id,kind,role_id,thread_id,title,summary,reason,"
                "payload_json,sources_json,status,created_at,decided_at "
                "FROM ai_proposals ORDER BY created_at DESC, id DESC"
            ).fetchall()]

        by_thread: dict[str, list[dict[str, Any]]] = {}
        turns = 0
        for turn in turn_rows:
            thread_id = str(turn.pop("thread_id"))
            turn["sources"] = _loads(turn.pop("sources_json"), [])
            turn["proposals"] = _loads(turn.pop("proposals_json"), [])
            turn["structured"] = _loads(turn.pop("structured_json"), {})
            by_thread.setdefault(thread_id, []).append(turn)
            turns += 1

        threads = [{**row, "turns": by_thread.get(str(row["id"]), [])}
                   for row in thread_rows]
        proposals = []
        for row in proposal_rows:
            row["payload"] = _loads(row.pop("payload_json"), {})
            row["sources"] = _loads(row.pop("sources_json"), [])
            proposals.append(row)
        return {
            "schema": 1,
            "exported_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "counts": {
                "threads": len(threads),
                "turns": turns,
                "proposals": len(proposals),
            },
            "threads": threads,
            "proposals": proposals,
        }


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _time(value: Any) -> str:
    return str(value or "未记录")


def render_markdown(payload: dict[str, Any]) -> str:
    counts = payload["counts"]
    lines = [
        "# 薪火OS AI 角色导出",
        "",
        f"- 导出时间：{payload['exported_at']}（Asia/Shanghai）",
        f"- 会话 {counts['threads']} · 轮次 {counts['turns']} · 提案 {counts['proposals']}",
        "- 这是本地只读快照：包含页面角色抽屉的会话、回复与提案状态；不包含 dsh 会话 ID、",
        "  verdict token、模型 Key 或主对话 iframe 的历史。",
        "",
    ]
    if not payload["threads"]:
        lines.append("（还没有 AI 角色会话）")
    else:
        for thread in payload["threads"]:
            title = _one_line(thread.get("title") or "未命名会话")
            lines.append(f"## 会话：{title}（{thread.get('role_id')} / {thread.get('mode')}）")
            lines.append(
                f"- 创建：{_time(thread.get('created_at'))}"
                f" · 更新：{_time(thread.get('updated_at'))}"
                f" · thread_id：{thread.get('id')}"
            )
            if thread.get("archived_at"):
                lines.append(f"- 归档：{_time(thread.get('archived_at'))}")
            lines.append("")
            for turn in thread["turns"]:
                role = ROLE_LABELS.get(turn.get("role"), turn.get("role") or "消息")
                seconds = turn.get("seconds")
                extra = f" · {seconds}s" if isinstance(seconds, (int, float)) else ""
                lines.append(f"### {role}（{_time(turn.get('created_at'))}{extra}）")
                lines.append("")
                lines.append(str(turn.get("content") or "").rstrip())
                lines.append("")
                if turn.get("sources"):
                    names = [str(item.get("path") or item.get("label") or "")
                             for item in turn["sources"] if isinstance(item, dict)]
                    names = [name for name in names if name]
                    if names:
                        lines.append("来源：" + "、".join(names[:20]))
                        lines.append("")

    lines.append("## 提案")
    lines.append("")
    if not payload["proposals"]:
        lines.append("（还没有 AI 提案）")
    else:
        for proposal in payload["proposals"]:
            lines.append(
                f"- [{proposal.get('status')}] {_one_line(proposal.get('title'))}"
                f"（{proposal.get('kind')} · {proposal.get('role_id') or '系统'}）"
            )
            summary = _one_line(proposal.get("summary"))
            if summary:
                lines.append(f"  - 摘要：{summary}")
            reason = _one_line(proposal.get("reason"))
            if reason:
                lines.append(f"  - 理由：{reason}")
            if proposal.get("decided_at"):
                lines.append(f"  - 处理时间：{_time(proposal.get('decided_at'))}")
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["AIExportService", "render_markdown"]
