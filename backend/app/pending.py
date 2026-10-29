"""待确认中枢：把散落各处、各自一套状态机的「等我拍板」，收成一份清单。

这不是第五套状态机，而是**只读适配层 + 动作转发**：

| kind | 来源 | 动作 |
|---|---|---|
| `draft` | `inbox_items`（早报/晚报/周报） | 标记已读 / 丢弃（只打标记） |
| `fact` | `memories.status='candidate'` + `.torch-os/身份待确认/` | 采纳（写四库）/ 拒绝（不动四库） |
| `triage` | `03_知识库/00_收件箱` | 按建议或自选章节，真的移过去 |
| `love` | `.torch-os/love/love_analysis.db` 的 `memory_drafts` | 写入四库 / 丢弃（由恋爱系统的接口执行） |

统一结构固定为 `{kind, id, title, preview, lines, source, created, actions}`，
前端只认这一个结构——否则中枢会变成四种列表拼接，比现在更难用。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import identity_inbox, pillar_notes, steward_policy, vault_layout
from .db import Database
from .love_storage import DB_NAME as LOVE_DB_NAME
from .scheduler import SchedulerService

KINDS = ("draft", "fact", "triage", "love")
KIND_TITLES = {"draft": "草稿", "fact": "事实", "triage": "收件箱", "love": "恋爱记忆"}
MAX_LINES = 6
# 任务/日记/习惯由各自的工作台管，收件箱不能往那儿搬。
TRIAGE_BLOCKED = {"task", "journal", "habit"}


def _draft_lines(kind: str, body: dict[str, Any]) -> tuple[str, list[str]]:
    """把三种草稿的 body 压成「一行预览 + 几行细节」，前端不解析业务字段。"""
    lines: list[str] = []
    if kind == "morning":
        for goal in body.get("goals") or []:
            lines.append(f"目标 · {goal.get('title') or ''}".strip())
        for task in body.get("open_tasks") or []:
            due = task.get("due_date") or "无日期"
            lines.append(f"任务 · {task.get('title') or ''}（{due}）")
        if body.get("journal_yesterday_recorded") is False:
            lines.append("昨天日记没写")
    elif kind == "evening":
        lines.append(f"完成 {int(body.get('completed_tasks') or 0)} 件 · "
                     f"逾期 {int(body.get('open_overdue_tasks') or 0)} 件")
        habits = body.get("habits") or {}
        if habits:
            lines.append(f"习惯 完成 {habits.get('done', 0)} · 跳过 {habits.get('skipped', 0)} · "
                         f"未记 {habits.get('unrecorded', 0)}")
        next_action = str((body.get("journal") or {}).get("next_action") or "").strip()
        if next_action:
            lines.append(f"昨天的下一步：{next_action}")
    elif kind == "weekly":
        lines.append(f"{body.get('week_start') or ''} ~ {body.get('week_end') or ''}".strip())
        lines.append(f"完成 {int(body.get('completed_tasks') or 0)} 件 · "
                     f"逾期 {int(body.get('open_overdue_tasks') or 0)} 件")
        journal_days = body.get("journal_days")
        days = len(journal_days) if isinstance(journal_days, list) else int(journal_days or 0)
        lines.append(f"日记 {days} 天 · 身体记录 {int(body.get('body_record_days') or 0)} 天")
        for title in (body.get("completed_examples") or [])[:3]:
            lines.append(f"完成 · {title}")
    preview = " · ".join(lines[:2]) if lines else "（没有内容）"
    return preview, lines[:MAX_LINES]


def _timestamp(value: str) -> float:
    """四处来源的时间戳时区各不相同；按字符串排会把今天的事实排到昨天的草稿后面。"""
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def drafts(service: SchedulerService, limit: int = 50) -> list[dict[str, Any]]:
    """只列「未读且未丢弃」的草稿——读过的不再是待确认。"""
    items: list[dict[str, Any]] = []
    for row in service.list_inbox(limit=max(1, min(limit, 200))):
        if not row.get("unread"):
            continue
        kind = str(row.get("kind") or "")
        preview, lines = _draft_lines(kind, row.get("body") or {})
        if not lines:
            lines = ["今天没有到期任务，也没有进行中的目标" if kind == "morning" else "（这批草稿没有可读内容）"]
            preview = lines[0]
        items.append({
            "kind": "draft",
            "id": str(row.get("id")),
            "title": str(row.get("title") or "草稿"),
            "preview": preview,
            "lines": lines,
            "source": f"{row.get('source_date') or ''} · 定时草稿".strip(" ·"),
            "created": str(row.get("created_at") or ""),
            "unread": True,
            "actions": ["read", "dismiss"],
        })
    return items


def facts(database: Database, vault_dir: Path | str, limit: int = 50) -> list[dict[str, Any]]:
    """记忆候选 + 身份候选：一个来自索引器，一个来自 dsh，都还没有统一的落脚点。"""
    items: list[dict[str, Any]] = []
    for row in database.list_memories("candidate"):
        items.append({
            "kind": "fact",
            "id": f"memory:{row.get('id')}",
            "title": str(row.get("title") or "记忆候选"),
            "preview": str(row.get("content") or "")[:120],
            "lines": [f"原话：{row['source_quote']}"] if row.get("source_quote") else [],
            "source": f"记忆候选 · {row.get('source_path') or ''}".strip(" ·"),
            "created": str(row.get("created_at") or row.get("updated_at") or ""),
            "actions": ["accept", "reject"],
        })
    for draft in identity_inbox.list_drafts(vault_dir):
        target = draft["field_label"] if draft["kind"] == "field" else "记忆与自传"
        items.append({
            "kind": "fact",
            "id": f"identity:{draft['name']}",
            "title": draft["text"],
            "preview": f"交给忆灵复核：{target}",
            "lines": [f"原话：{draft['evidence']}"] if draft["evidence"] else [],
            "source": "身份候选 · dsh 对话",
            "created": draft["created"],
            "actions": ["accept", "reject"],
        })
    items.sort(key=lambda item: _timestamp(item["created"]), reverse=True)
    return items[:max(1, min(limit, 200))]


def triage_chapters() -> list[dict[str, str]]:
    """可以搬进去的章节（排除工作台托管的任务/日记/习惯）。"""
    return [
        {"key": key, "title": title, "pillar": vault_layout.PILLAR_TITLES[pillar]}
        for key, pillar, title, _directory, _type in vault_layout.CHAPTERS
        if key not in TRIAGE_BLOCKED
    ]


def triage(vault_dir: Path | str, limit: int = 50) -> list[dict[str, Any]]:
    """收件箱待分流：带上本地规则的章节建议，一键真的搬过去。"""
    items: list[dict[str, Any]] = []
    for note in pillar_notes.list_notes(vault_dir, chapter="inbox", limit=max(1, min(limit, 200))):
        suggestion = steward_policy.suggestion_for_note(note)
        chapter = suggestion["chapter"]
        confidence = {"high": "高置信", "medium": "可参考"}.get(str(suggestion.get("confidence")), "低置信")
        lines = [f"建议：{suggestion['chapter_title']}（{confidence}）"]
        if suggestion.get("reason"):
            lines.append(str(suggestion["reason"]))
        items.append({
            "kind": "triage",
            "id": str(note.get("path") or ""),
            "title": str(note.get("title") or "未命名"),
            "preview": str(note.get("excerpt") or "")[:120],
            "lines": lines,
            "source": "03_知识库/00_收件箱",
            "created": str(note.get("date") or ""),
            "actions": ["move"],
            "suggest": {
                "chapter": chapter,
                "title": suggestion["chapter_title"],
                "confidence": str(suggestion.get("confidence") or ""),
                "reason": str(suggestion.get("reason") or ""),
            },
        })
    return items


def _love_db_paths(vault_dir: Path | str) -> list[Path]:
    paths = [Path(vault_dir) / ".torch-os" / "love" / LOVE_DB_NAME]
    try:
        from .config import LOVE_DATA_DIR

        paths.append(Path(LOVE_DATA_DIR) / LOVE_DB_NAME)
    except Exception:  # 配置缺失时只用库内默认位置
        pass
    return paths


def _love_preview(markdown: str) -> tuple[str, list[str]]:
    lines: list[str] = []
    for line in str(markdown or "").splitlines():
        text = line.strip()
        if not text or text.startswith(("---", "#", ">", "|", "```")):
            continue
        lines.append(text[:80])
        if len(lines) >= 3:
            break
    return (" · ".join(lines[:2]) if lines else "（草稿还没有正文）"), lines


def love_drafts(vault_dir: Path | str, limit: int = 50) -> list[dict[str, Any]]:
    """恋爱系统的「每日记忆」草稿：只读它自己的库，写回仍走恋爱系统的接口。"""
    for db_path in _love_db_paths(vault_dir):
        if not db_path.is_file():
            continue
        connection = None
        try:
            connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT id, day, status, markdown FROM memory_drafts "
                "WHERE status='draft' ORDER BY day DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            if connection is not None:
                connection.close()
        items: list[dict[str, Any]] = []
        for row in rows:
            preview, lines = _love_preview(row["markdown"])
            items.append({
                "kind": "love",
                "id": str(row["id"]),
                "title": f"{row['day']} · 每日记忆",
                "preview": preview,
                "lines": lines,
                "source": "每日记忆 · 恋爱系统",
                "created": f"{row['day']}T00:00:00",
                "actions": ["write", "discard"],
            })
        return items
    return []


def items(database: Database, vault_dir: Path | str, service: SchedulerService,
          kind: str = "", limit: int = 50) -> list[dict[str, Any]]:
    key = str(kind or "").strip()
    if key and key not in KINDS:
        raise ValueError("未知的待确认类型")
    if key == "draft":
        return drafts(service, limit)
    if key == "fact":
        return facts(database, vault_dir, limit)
    if key == "triage":
        return triage(vault_dir, limit)
    if key == "love":
        return love_drafts(vault_dir, limit)
    merged = (drafts(service, limit) + facts(database, vault_dir, limit)
              + triage(vault_dir, limit) + love_drafts(vault_dir, limit))
    merged.sort(key=lambda item: _timestamp(item["created"]), reverse=True)
    return merged[:max(1, min(limit, 200))]


def summary(database: Database, vault_dir: Path | str, service: SchedulerService) -> dict[str, Any]:
    """计数是这套东西最值钱的部分：一个常驻数字就能防记忆腐烂。"""
    counts = {
        "draft": len(drafts(service, 200)),
        "fact": len(facts(database, vault_dir, 200)),
        "triage": len(triage(vault_dir, 200)),
        "love": len(love_drafts(vault_dir, 200)),
    }
    return {"total": sum(counts.values()), **counts, "titles": KIND_TITLES}


def move_triage(vault_dir: Path | str, path: str, chapter: str) -> dict[str, Any]:
    """把收件箱里的一条搬到章节；重名由 move_note 自己加 -2，不覆盖。"""
    key = str(chapter or "").strip()
    if key not in vault_layout.CHAPTER_BY_KEY:
        raise ValueError("未知章节")
    if key in TRIAGE_BLOCKED:
        raise ValueError("任务、日记、习惯由工作台托管，不能作为归档目标")
    return pillar_notes.move_note(vault_dir, str(path or "").strip(), key)


class DraftAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1)
    action: Literal["read", "dismiss"]


class FactAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=3, max_length=300)
    action: Literal["accept", "reject"]


class TriageAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    chapter: str = Field(min_length=1, max_length=40)


def apply_draft(service: SchedulerService, payload: DraftAction) -> dict[str, Any]:
    if payload.action == "read":
        return {"ok": True, "item": service.mark_read(payload.id)}
    return {"ok": True, "item": service.dismiss(payload.id)}


def apply_fact(database: Database, vault_dir: Path | str, payload: FactAction,
               *, read_only: bool = False) -> dict[str, Any]:
    prefix, _, value = payload.id.partition(":")
    if prefix == "identity":
        if payload.action == "accept":
            return {"ok": True, "result": identity_inbox.accept_draft(vault_dir, value, read_only=read_only)}
        return {"ok": True, "result": identity_inbox.reject_draft(vault_dir, value)}
    if prefix == "memory":
        if not value.isdigit():
            raise ValueError("记忆候选编号不合法")
        result = database.update_memory_status(int(value), "active" if payload.action == "accept" else "rejected")
        if result is None:
            raise LookupError("记忆候选不存在")
        return {"ok": True, "result": {key: result.get(key) for key in ("id", "status", "title")}}
    raise ValueError("未知的待确认编号")


def create_pending_router(database: Database, vault_dir: Path | str, *,
                          read_only: bool = False, prefix: str = "/api") -> APIRouter:
    service = SchedulerService(database, vault_dir)
    router = APIRouter(prefix=prefix, tags=["pending"])

    @router.get("/pending/summary")
    def pending_summary():
        return summary(database, vault_dir, service)

    @router.get("/pending/items")
    def pending_items(kind: str = Query(default=""), limit: int = Query(default=50, ge=1, le=200)):
        try:
            payload: dict[str, Any] = {"items": items(database, vault_dir, service, kind, limit)}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if str(kind or "").strip() in {"", "triage"}:
            payload["chapters"] = triage_chapters()
        return payload

    @router.post("/pending/drafts")
    def pending_draft_action(payload: DraftAction):
        try:
            return apply_draft(service, payload)
        except HTTPException:
            raise
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/pending/facts")
    def pending_fact_action(payload: FactAction):
        try:
            return apply_fact(database, vault_dir, payload, read_only=read_only)
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/pending/triage")
    def pending_triage_action(payload: TriageAction):
        try:
            return {"ok": True, "note": move_triage(vault_dir, payload.path, payload.chapter)}
        except HTTPException:
            raise
        except FileNotFoundError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    return router


__all__ = ["KINDS", "KIND_TITLES", "create_pending_router", "summary", "items",
           "drafts", "facts", "triage", "love_drafts", "triage_chapters", "move_triage",
           "apply_draft", "apply_fact"]
