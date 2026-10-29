"""生活运营 AI（v1.0 P4）：健康/理财/恋爱/生活助理的只读上下文与报告。

- 只读来源：`02_生活运营` 的月度表、恋爱系统自己的 SQLite（只读摘要，不发原始聊天）、
  工作台未完成任务/今日习惯；
- 报告落盘：健康 → `06_健康报告`，财务 → `07_财务报告`，生活/恋爱建议 → `08_生活建议`；
- AI 只提议：建议里的 new_task/new_habit 走 `ai_proposals`，用户采纳后才创建；
- 不修改健康/财务月度表，不替恋爱系统发消息，不控制任何家庭设备。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import pillar_notes, structured_notes, task_reports, vault_layout, workbench
from .ai_schemas import extract_json
from .db import Database
from .proposals import ProposalError
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SHANGHAI = ZoneInfo("Asia/Shanghai")
DOMAINS = {"health", "finance", "love", "life"}
REPORT_CHAPTER = {
    "health": "health_report",
    "finance": "finance_report",
    "love": "life_advice",
    "life": "life_advice",
}
REPORT_TITLE = {
    "health": "健康报告",
    "finance": "财务报告",
    "love": "恋爱大师建议",
    "life": "生活助理建议",
}
ROLE = {
    "health": ("health_doctor", "report"),
    "finance": ("finance_advisor", "report"),
    "love": ("love_master", "advice"),
    "life": ("life_assistant", "suggest"),
}
MAX_CONTEXT = 14_000


class LifeAIError(RuntimeError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _trim(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _money(value: Any) -> float | None:
    try:
        return float(str(value or "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _month_rows(vault_dir: Path | str, dataset: str, months: int = 6) -> list[dict[str, Any]]:
    try:
        summary = structured_notes.summary(vault_dir, dataset, months)
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for month in summary.get("months") or []:
        for row in month.get("rows") or []:
            rows.append({"month": month.get("month", ""), **row})
    return rows


def health_context(vault_dir: Path | str, months: int = 6) -> tuple[str, dict[str, Any]]:
    rows = _month_rows(vault_dir, "health", months)
    lines: list[str] = []
    weights = [(_money(row.get("weight_kg")), row.get("date")) for row in rows]
    weights = [(value, day) for value, day in weights if value is not None]
    sleeps = [value for value in (_money(row.get("sleep_minutes")) for row in rows) if value is not None]
    exercises = [value for value in (_money(row.get("exercise_minutes")) for row in rows) if value is not None]
    if weights:
        latest_value, latest_day = max(weights, key=lambda item: item[1] or "")
        lines.append(f"- 最近体重：{latest_value:g} kg（{latest_day}）")
    if sleeps:
        lines.append(f"- 平均睡眠：{sum(sleeps) / len(sleeps):.0f} 分钟/天（{len(sleeps)} 条）")
    if exercises:
        lines.append(f"- 平均运动：{sum(exercises) / len(exercises):.0f} 分钟/天（{len(exercises)} 条）")
    for row in rows[:20]:
        parts = [str(row.get("date") or row.get("month") or "")]
        if row.get("weight_kg"):
            parts.append(f"体重 {row['weight_kg']}kg")
        if row.get("sleep_minutes"):
            parts.append(f"睡眠 {row['sleep_minutes']}min")
        if row.get("exercise_minutes"):
            parts.append(f"运动 {row['exercise_minutes']}min")
        if row.get("note"):
            parts.append(str(row["note"]))
        lines.append("- " + " · ".join(parts))
    return "\n".join(lines) or "还没有健康月度数据。", {"rows": len(rows), "weights": len(weights),
                                                         "sleeps": len(sleeps),
                                                         "exercises": len(exercises)}


def finance_context(vault_dir: Path | str, months: int = 6) -> tuple[str, dict[str, Any]]:
    rows = _month_rows(vault_dir, "finance", months)
    income = expense = 0.0
    by_category: dict[str, float] = {}
    for row in rows:
        amount = _money(row.get("amount"))
        if amount is None:
            continue
        kind = str(row.get("kind") or "").strip()
        if any(word in kind for word in ("收入", "入账", "工资", "报销")):
            income += amount
        else:
            expense += amount
        category = str(row.get("category") or "未分类").strip()
        by_category[category] = by_category.get(category, 0.0) + amount
    categories = sorted(by_category.items(), key=lambda item: item[1], reverse=True)[:8]
    lines = [
        f"- 记录条数：{len(rows)}；收入 {income:.2f}；支出 {expense:.2f}；结余 {income - expense:.2f}",
    ]
    if categories:
        lines.append("- 主要分类：" + "、".join(f"{name} {total:.2f}" for name, total in categories))
    for row in rows[:15]:
        lines.append(f"- {row.get('date') or row.get('month') or ''} · {row.get('kind') or ''} "
                     f"{row.get('amount') or ''} · {row.get('category') or ''} · {row.get('note') or ''}")
    return "\n".join(lines), {"rows": len(rows), "income": round(income, 2),
                              "expense": round(expense, 2)}


def love_context(love_settings: Any) -> tuple[str, dict[str, Any]]:
    db_path = Path(getattr(love_settings, "db_path", "") or "")
    if not db_path.is_file():
        return "还没有恋爱分析数据。", {"projects": 0, "analyses": 0}
    try:
        connection = sqlite3.connect(str(db_path), timeout=5)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as error:
        return f"恋爱数据暂时读不到：{error}", {"projects": 0, "analyses": 0}
    try:
        projects = connection.execute(
            "SELECT id, name, partner_a, partner_b, message_count FROM projects ORDER BY updated_at DESC LIMIT 3"
        ).fetchall()
        lines: list[str] = []
        analyses = 0
        for project in projects:
            line = f"- 项目：{project['name']}（{project['partner_a']} / {project['partner_b']}，{project['message_count']} 条消息）"
            row = connection.execute(
                "SELECT result_json FROM analysis_logs WHERE project_id=? AND status='completed' "
                "ORDER BY created_at DESC LIMIT 1", (project["id"],)
            ).fetchone()
            if row and row["result_json"]:
                analyses += 1
                try:
                    result = json.loads(row["result_json"])
                except ValueError:
                    result = {"raw": str(row["result_json"])}
                line += "\n  最近分析摘要：" + _trim(json.dumps(result, ensure_ascii=False), 1_500)
            else:
                line += "\n  还没有完成的 AI 分析。"
            lines.append(line)
        return "\n".join(lines) or "还没有恋爱项目。", {"projects": len(projects), "analyses": analyses}
    except sqlite3.Error as error:
        return f"恋爱数据暂时读不到：{error}", {"projects": 0, "analyses": 0}
    finally:
        connection.close()


def life_context(database: Database, vault_dir: Path | str, love_settings: Any = None) -> tuple[str, dict[str, Any]]:
    health_text, health_stats = health_context(vault_dir)
    finance_text, finance_stats = finance_context(vault_dir)
    love_text, love_stats = love_context(love_settings) if love_settings is not None else ("未启用恋爱数据。", {"projects": 0, "analyses": 0})
    try:
        tasks = task_reports.open_tasks(vault_dir)
        task_lines = [f"- {note.get('title')}（{note.get('date') or '未排期'}）" for note in tasks[:20]]
    except (OSError, ValueError):
        task_lines = []
    try:
        day = datetime.now(SHANGHAI).date().isoformat()
        payload = workbench.collect_day(database, vault_dir, day)
        habits = payload.get("habits") or []
        habit_line = f"今日习惯：{sum(1 for habit in habits if habit.get('done'))}/{len(habits)} 完成。"
    except Exception:
        habit_line = "今日习惯：暂时读不到。"
    text = "\n\n".join([
        "## 健康\n" + health_text,
        "## 财务\n" + finance_text,
        "## 恋爱\n" + love_text,
        "## 未完成任务\n" + ("\n".join(task_lines) or "没有未完成任务。"),
        "## 今天\n" + habit_line,
    ])
    return _trim(text, MAX_CONTEXT), {
        "health": health_stats, "finance": finance_stats, "love": love_stats,
        "open_tasks": len(task_lines),
    }


def _create_action_proposals(proposals: Any, structured: dict[str, Any], *,
                             role_id: str, sources: list[str]) -> list[dict[str, Any]]:
    if proposals is None:
        return []
    created: list[dict[str, Any]] = []
    for draft in list(structured.get("proposals") or [])[:5]:
        if not isinstance(draft, dict):
            continue
        kind = str(draft.get("kind") or "")
        if kind not in {"new_task", "new_habit", "new_note"}:
            continue
        payload = draft.get("payload") if isinstance(draft.get("payload"), dict) else {}
        try:
            item = proposals.create(
                kind=kind, title=str(draft.get("title") or "生活建议")[:200],
                summary=str(draft.get("summary") or "")[:2000],
                reason=str(draft.get("reason") or "")[:2000],
                payload=payload, sources=sources, role_id=role_id,
            )
        except ProposalError:
            continue
        created.append(item)
    return created


def report_note_title(domain: str) -> str:
    stamp = datetime.now(SHANGHAI).strftime("%Y-%m-%d")
    return f"{REPORT_TITLE.get(domain, '生活建议')}-{stamp}"


def generate_report(database: Database, vault_dir: Path | str, role_service: Any,
                    proposals: Any, domain: str, *, love_settings: Any = None,
                    read_only: bool = False) -> dict[str, Any]:
    domain = str(domain or "").strip()
    if domain not in DOMAINS:
        raise LifeAIError("未知的生活运营报告类型", 422)
    if role_service is None:
        raise LifeAIError("AI 不可用：没有配置 dsh 或 DeepSeek Key", 409)
    try:
        ensure_vault_writable(read_only, "生成生活运营报告")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    if domain == "life":
        context, stats = life_context(database, vault_dir, love_settings)
    elif domain == "health":
        context, stats = health_context(vault_dir)
    elif domain == "finance":
        context, stats = finance_context(vault_dir)
    else:
        context, stats = love_context(love_settings)
    role_id, mode = ROLE[domain]
    user_text = {
        "health": "请按私人医生模式评估最近 6 个月的健康数据，输出约定的报告 JSON。",
        "finance": "请按理财专家模式评估最近 6 个月的收支数据，输出约定的报告 JSON。",
        "love": "请按恋爱大师模式给出关系建议；只基于摘要，不要假装读过原始聊天。",
        "life": "请按生活助理模式汇总健康/财务/恋爱与任务，给最多三条可执行建议，并输出约定 JSON。",
    }[domain]
    try:
        result = role_service.run(role_id, mode, context_text=context, user_text=user_text)
    except RuntimeError as error:
        raise LifeAIError(str(error), 409) from error
    except Exception as error:
        status = getattr(error, "status_code", 503)
        raise LifeAIError(str(error), status) from error
    structured = extract_json(result.text) or {}
    note_data = structured.get("note") if isinstance(structured, dict) else None
    if not isinstance(note_data, dict):
        note_data = {}
    chapter = str(note_data.get("chapter") or REPORT_CHAPTER[domain])
    if chapter != REPORT_CHAPTER[domain]:
        chapter = REPORT_CHAPTER[domain]
    title = str(note_data.get("title") or report_note_title(domain))[:200]
    body = str(note_data.get("markdown") or "").strip() or result.text
    note = pillar_notes.create_note(
        vault_dir, pillar="ops", chapter=chapter, title=title, body=body,
        note_type="report", tags=["AI", domain], source="AI",
        extra_metadata={"memory": False, "generated_by": role_id},
    )
    action_proposals = _create_action_proposals(
        proposals, structured if isinstance(structured, dict) else {},
        role_id=role_id, sources=[note.get("path", "")])
    database.audit("ops_ai_report", {"domain": domain, "role": role_id,
                                     "path": note.get("path", ""),
                                     "proposals": len(action_proposals)})
    return {
        "domain": domain, "role": role_id, "text": result.text,
        "structured": structured if isinstance(structured, dict) else {},
        "note": note, "proposals": action_proposals, "stats": stats,
        "seconds": result.seconds,
    }


def _report_notes(vault_dir: Path | str, limit: int = 5) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for chapter in ("health_report", "finance_report", "life_advice"):
        try:
            notes = pillar_notes.list_notes(vault_dir, chapter=chapter, limit=limit)
        except Exception:
            notes = []
        items.extend({
            "chapter": chapter, "title": note.get("title", ""),
            "path": note.get("path", ""), "date": note.get("date", ""),
        } for note in notes)
    return items[: max(1, limit * 3)]


def overview(database: Database, vault_dir: Path | str, *, love_settings: Any = None) -> dict[str, Any]:
    health_text, health_stats = health_context(vault_dir)
    finance_text, finance_stats = finance_context(vault_dir)
    love_text, love_stats = love_context(love_settings) if love_settings is not None else ("未启用恋爱数据。", {"projects": 0, "analyses": 0})
    try:
        open_tasks = len(task_reports.open_tasks(vault_dir))
    except (OSError, ValueError):
        open_tasks = 0
    return {
        "health": {"text": health_text, "stats": health_stats},
        "finance": {"text": finance_text, "stats": finance_stats},
        "love": {"text": love_text, "stats": love_stats},
        "open_tasks": open_tasks,
        "reports": _report_notes(vault_dir),
    }


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    domain: str = Field(min_length=1, max_length=20)
    confirm: bool = Field(strict=True)


def create_life_ai_router(database: Database, vault_dir: Path | str, *,
                           role_service: Any = None, proposals: Any = None,
                           love_settings: Any = None, read_only: bool = False,
                           prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["life-ai"])

    @router.get("/ops/ai/overview")
    def ops_ai_overview():
        return overview(database, vault_dir, love_settings=love_settings)

    @router.post("/ops/ai/report")
    def ops_ai_report(payload: ReportRequest):
        if payload.confirm is not True:
            raise HTTPException(422, "请明确确认生成并保存报告")
        try:
            return generate_report(database, vault_dir, role_service, proposals,
                                   payload.domain, love_settings=love_settings,
                                   read_only=read_only)
        except LifeAIError as error:
            raise HTTPException(error.status_code, error.message) from error

    return router


__all__ = ["DOMAINS", "REPORT_CHAPTER", "health_context", "finance_context",
           "love_context", "life_context", "generate_report", "overview",
           "create_life_ai_router"]
