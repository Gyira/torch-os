"""One read-only ZIP with every human-readable export.

This is a reading/migration bundle, not a restore package: restoring is the
job of the verified database backup. Each section reuses the same service the
individual export endpoints use, so counts and content stay consistent.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Response

from . import body_source
from .ai_export import AIExportService
from .ai_export import render_markdown as render_ai_markdown
from .conversation_export import ConversationExportService
from .conversation_export import render_markdown as render_conversations_markdown
from .db import Database
from .growth_export import GrowthExportService
from .growth_export import render_markdown as render_growth_markdown
from .journal_export import JournalExportService
from .journal_export import render_markdown as render_journal_markdown
from .memory_export import MemoryExportService
from .memory_export import render_markdown as render_memories_markdown
from .planning_export import PlanningExportService
from .planning_export import render_markdown as render_planning_markdown


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _body_csv(entries: list[dict[str, Any]]) -> str:
    lines = ["date,weight_kg,sleep_minutes,exercise_minutes"]
    for item in entries:
        lines.append(",".join([
            str(item.get("date") or ""),
            "" if item.get("weight_kg") is None else format(item["weight_kg"], "g"),
            "" if item.get("sleep_minutes") is None else str(item["sleep_minutes"]),
            "" if item.get("exercise_minutes") is None else str(item["exercise_minutes"]),
        ]))
    return "\n".join(lines) + "\n"


def build_bundle(database: Database, vault_dir) -> tuple[bytes, dict[str, Any]]:
    generated_at = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    memories = MemoryExportService(database).collect("all")
    planning = PlanningExportService(vault_dir).collect()
    growth = GrowthExportService(database, vault_dir).collect()
    conversations = ConversationExportService(database).collect()
    ai_roles = AIExportService(database).collect()
    journal = JournalExportService(database).collect()
    body_rows = body_source.all_entries(vault_dir)
    body = {
        "schema": 1,
        "exported_at": generated_at,
        "total": len(body_rows),
        "entries": body_rows,
        "note": "来自健康与体重月度 Markdown 表；每天多行时睡眠/运动相加、体重取最后一条。",
    }

    members: dict[str, str] = {
        "memories.md": render_memories_markdown(memories),
        "memories.json": json.dumps(memories, ensure_ascii=False, indent=2) + "\n",
        "planning.md": render_planning_markdown(planning),
        "planning.json": json.dumps(planning, ensure_ascii=False, indent=2) + "\n",
        "growth.md": render_growth_markdown(growth),
        "growth.json": json.dumps(growth, ensure_ascii=False, indent=2) + "\n",
        "conversations.md": render_conversations_markdown(conversations),
        "conversations.json": json.dumps(conversations, ensure_ascii=False, indent=2) + "\n",
        "ai.md": render_ai_markdown(ai_roles),
        "ai.json": json.dumps(ai_roles, ensure_ascii=False, indent=2) + "\n",
        "journal.md": render_journal_markdown(journal),
        "journal.json": json.dumps(journal, ensure_ascii=False, indent=2) + "\n",
        "body.csv": _body_csv(body_rows),
        "body.json": json.dumps(body, ensure_ascii=False, indent=2) + "\n",
    }
    counts = {
        "memories": memories["total"],
        "goals": planning["counts"]["goals"],
        "tasks": planning["counts"]["tasks"],
        "habits": growth["counts"]["habits"],
        "habit_entries": growth["counts"]["habit_entries"],
        "conversations": conversations["counts"]["conversations"],
        "messages": conversations["counts"]["messages"],
        "ai_threads": ai_roles["counts"]["threads"],
        "ai_turns": ai_roles["counts"]["turns"],
        "ai_proposals": ai_roles["counts"]["proposals"],
        "journal_days": journal["counts"]["days"],
        "body_days": len(body_rows),
    }
    readme = [
        "薪火OS 可读导出包",
        "",
        f"生成时间：{generated_at}（Asia/Shanghai）",
        "",
        "这是只读快照，用于阅读与迁移；它不是恢复包。恢复请使用“设置与状态”里的本地备份（含数据库与原始资料）。",
        "",
        "内容：",
        "- memories.md / memories.json：记忆（含待确认、已拒绝、已归档）",
        "- planning.md / planning.json：人生路目标笔记与 Markdown 任务",
        "- growth.md / growth.json：习惯与打卡",
        "- conversations.md / conversations.json：保存过的对话与引用",
        "- ai.md / ai.json：AI 角色抽屉的会话、回复、来源与提案状态",
        "- journal.md / journal.json：每日记录（含情绪与饮食摘要）",
        "- body.csv / body.json：身体记录（来自健康月度 Markdown 表）",
        "",
        "数量："
        + " · ".join(f"{key} {value}" for key, value in counts.items()),
        "",
    ]
    members["README.txt"] = "\n".join(readme)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(datetime.now(SHANGHAI).timetuple()[:6]))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, members[name].encode("utf-8"))
    return buffer.getvalue(), {"files": len(members), "counts": counts, "generated_at": generated_at}


def create_export_bundle_router(database: Database, vault_dir, prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["export-bundle"])

    @router.get("/export/all.zip")
    def export_all():
        payload, summary = build_bundle(database, vault_dir)
        database.audit("export_bundle_created", {"files": summary["files"]})
        stamp = datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")
        return Response(
            content=payload,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="torch-os-export-{stamp}.zip"'},
        )

    return router
