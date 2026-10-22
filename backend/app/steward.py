"""薪火管家（二期）：提案、健康检查与带引用问答（2026-10-21）。

边界：
- 管家只生成提案，不自动移动、不自动新建；执行接口必须收到 confirm=true。
- 健康检查只读四库散文笔记；任务/待办章节与 Apex dashboard、附件不在检查范围。
- 问答默认只用本地检索；只有用户显式勾选“使用云端模型”才会把相关笔记片段
  发送给已配置的模型，模型不可用时退回本地摘录。
- 审计只记录提案 id、动作、路径与结果，不记录正文。
"""

from __future__ import annotations

import re
import threading
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import pillar_notes, steward_policy, vault_layout, vault_links
from .db import Database, utc_now
from .model_gateway import ModelGateway, ModelGatewayError
from .vault_connection import ensure_vault_writable

MAX_NOTES = 500
STALE_INBOX_DAYS = 14
MAX_HEALTH_ITEMS = 200
MAX_ANSWER_CITATIONS = 6

_DISTILL_CHAPTERS = {
    "reflection", "insight_life", "insight_work", "insight_study",
    "work_review_project", "work_review_meeting", "work_review_pitfall",
}
_CUES = ("我学到", "我意识到", "教训", "原则", "下次", "以后", "启发", "明白",
         "总结", "复盘", "值得记住")


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=1000)
    use_cloud: bool = Field(default=False, strict=True)


class ProposalApply(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    proposal_id: str = Field(min_length=1, max_length=64)
    confirm: bool = Field(strict=True)

    @model_validator(mode="after")
    def require_confirmation(self):
        if self.confirm is not True:
            raise ValueError("请明确确认执行这条提案")
        return self


class ProposalBatchApply(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    proposal_ids: list[str] = Field(min_length=1, max_length=50)
    confirm: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_batch(self):
        if self.confirm is not True:
            raise ValueError("请明确确认执行这批提案")
        if len(set(self.proposal_ids)) != len(self.proposal_ids):
            raise ValueError("提案不能重复")
        if any(not item.strip() or len(item) > 64 for item in self.proposal_ids):
            raise ValueError("提案 id 格式无效")
        return self


def _parse_day(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _notes(vault_dir: Path | str) -> list[dict[str, Any]]:
    vault = Path(vault_dir).resolve()
    items: list[dict[str, Any]] = []
    for summary in pillar_notes.list_notes(vault, limit=MAX_NOTES):
        try:
            note = pillar_notes.read_note(vault, summary["path"])
        except Exception:
            continue
        # 路径是结构事实来源：即使 frontmatter 过期，提案也按实际目录判断。
        note["pillar"] = vault_layout.pillar_of(note["path"]) or note.get("pillar", "")
        note["chapter"] = vault_layout.chapter_key_of(note["path"]) or note.get("chapter", "")
        items.append(note)
    return items


def _health_item(kind: str, severity: str, path: str, title: str,
                 message: str, suggestion: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "severity": severity,
        "path": path,
        "title": title,
        "message": message,
        "suggestion": suggestion,
    }


def _link_names(vault: Path) -> set[str]:
    names: set[str] = set()
    try:
        candidates = vault.rglob("*.md")
        for path in candidates:
            try:
                relative = path.relative_to(vault)
            except ValueError:
                continue
            if any(part.startswith(".") for part in relative.parts):
                continue
            relative_text = relative.as_posix().casefold()
            names.add(relative_text.removesuffix(".md"))
            names.add(path.stem.casefold())
    except OSError:
        pass
    return names


def _broken_links(note: dict[str, Any], names: set[str], vault: Path) -> list[dict[str, str]]:
    broken: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        links = vault_links.scan_links(str(note.get("body") or ""), note.get("path", ""))
    except Exception:
        return broken
    for link in links:
        raw = str(link.get("target") or "").strip()
        if not raw:
            continue
        clean = vault_layout.normalize_relpath(raw)
        if not clean:
            continue
        note_dir = Path(note["path"]).parent
        key_path = clean.removesuffix(".md").casefold()
        name = key_path.rsplit("/", 1)[-1]
        if key_path in names or name in names:
            continue
        if (vault / clean).is_file() or (vault / f"{clean}.md").is_file():
            continue
        suffix = Path(clean).suffix.casefold()
        if suffix and suffix != ".md":
            if not (vault / note_dir / clean).is_file():
                key = f"{note['path']}::{clean}"
                if key not in seen:
                    seen.add(key)
                    broken.append({"target": clean, "kind": "附件不存在"})
            continue
        marker = f"{note['path']}::{clean}"
        if marker in seen:
            continue
        seen.add(marker)
        broken.append({"target": clean, "kind": "链接目标不存在"})
    return broken


def health_check(vault_dir: Path | str, *, limit: int = MAX_HEALTH_ITEMS) -> dict[str, Any]:
    """扫描四库的元数据、空笔记、收件箱积压、重复标题与断链。"""
    vault = Path(vault_dir).resolve()
    notes = _notes(vault)
    names = _link_names(vault)
    items: list[dict[str, Any]] = []
    title_map: dict[str, list[dict[str, Any]]] = {}
    today = _today()

    for note in notes:
        path = note["path"]
        # 任务与待办由工作台维护：空正文是正常的，不按散文笔记做健康检查。
        if note.get("chapter") == "task":
            continue
        title = note.get("title", "")
        raw = note.get("raw_metadata") or {}
        body = str(note.get("body") or "").strip()
        if not vault_layout.chapter_key_of(path):
            items.append(_health_item(
                "outside_chapter", "warning", path, title,
                "这篇笔记不在任何章节目录内，四库页面可能不会把它列出来。",
                "手动把它移到目标章节，或先移到知识库收件箱。"))
        if not raw:
            items.append(_health_item(
                "frontmatter", "warning", path, title,
                "缺少 frontmatter，标题/类型/状态只能从路径推断。",
                "在 Obsidian 里补上 type、title、status；新笔记会自动带上这些字段。"))
        if raw.get("chapter") and note.get("chapter") and str(raw["chapter"]) != note["chapter"]:
            items.append(_health_item(
                "chapter_mismatch", "warning", path, title,
                f"frontmatter 写着章节「{raw['chapter']}」，实际位于「{note['chapter']}」。",
                "以实际目录为准，修正或删除 frontmatter 里的 chapter。"))
        if raw.get("pillar") and note.get("pillar") and str(raw["pillar"]) != note["pillar"]:
            items.append(_health_item(
                "pillar_mismatch", "warning", path, title,
                f"frontmatter 写着库「{raw['pillar']}」，实际位于「{note['pillar']}」。",
                "以实际目录为准，修正或删除 frontmatter 里的 pillar。"))
        if not body:
            items.append(_health_item(
                "empty", "warning", path, title,
                "这是一篇空笔记。", "补一句内容，或手动归档/删除它。"))
        if not str(raw.get("id") or "").strip():
            items.append(_health_item(
                "missing_id", "info", path, title,
                "旧笔记没有稳定 id；移动后仍可按路径读取。",
                "无需立即处理；新笔记会自动生成 id。"))
        if note.get("chapter") == "inbox":
            day = _parse_day(note.get("date") or raw.get("created"))
            if day and (today - day).days >= STALE_INBOX_DAYS:
                items.append(_health_item(
                    "inbox_stale", "warning", path, title,
                    f"在收件箱里放了 {(today - day).days} 天，还没有分流。",
                    "看看提案页的分流建议，或先留在收件箱。"))
        for link in _broken_links(note, names, vault):
            items.append(_health_item(
                "broken_link", "warning", path, title,
                f"{link['kind']}：{link['target']}",
                "修正链接目标，或先确认目标笔记是否还没创建。"))
        title_map.setdefault(str(title).strip().casefold(), []).append(note)

    for title, group in title_map.items():
        if title and len(group) > 1:
            paths = "、".join(item["path"].rsplit("/", 1)[-1] for item in group[:4])
            for note in group:
                items.append(_health_item(
                    "duplicate_title", "info", note["path"], note.get("title", ""),
                    f"有 {len(group)} 篇同名笔记：{paths}。",
                    "如果它们是同一主题，考虑合并或加日期区分。"))
        if len(items) >= limit:
            break

    severity_order = {"error": 0, "warning": 1, "info": 2}
    items.sort(key=lambda item: severity_order.get(item["severity"], 3))
    total_items = len(items)
    items = items[:limit]
    counts = {"error": 0, "warning": 0, "info": 0}
    for item in items:
        counts[item["severity"]] = counts.get(item["severity"], 0) + 1
    return {
        "ok": counts["error"] == 0 and counts["warning"] == 0,
        "checked": len(notes),
        "counts": counts,
        "items": items,
        "truncated": total_items > len(items),
        "generated_at": utc_now(),
        "note": "只检查四库散文笔记的元数据、空笔记、收件箱积压、重复标题与断链；任务与待办由工作台单独维护。",
    }


def _proposal_id(kind: str, path: str, action: str, target: str) -> str:
    digest = sha256(f"{kind}|{path}|{action}|{target}".encode("utf-8")).hexdigest()
    return "st-" + digest[:12]


def _distilled_sources(vault: Path) -> set[str]:
    """已经提炼过的来源路径；每次生成提案只扫一遍自我档案。"""
    try:
        candidates = pillar_notes.list_notes(vault, pillar="self", limit=MAX_NOTES)
    except Exception:
        return set()
    return {str(item.get("source") or "").strip() for item in candidates
            if str(item.get("source") or "").strip()}


def _distill_cue(note: dict[str, Any]) -> str:
    text = " ".join([str(note.get("title") or ""), str(note.get("body") or "")]).casefold()
    for cue in _CUES:
        if cue.casefold() in text:
            return cue
    return ""


def _distill_target(note: dict[str, Any]) -> str:
    text = " ".join([str(note.get("title") or ""), str(note.get("body") or "")]).casefold()
    if any(word in text for word in ("职业", "职场", "职业选择", "职业原则")):
        return "distill_career"
    return "distill_work"


def _inbox_proposals(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for note in notes:
        if note.get("chapter") != "inbox":
            continue
        suggestion = steward_policy.suggestion_for_note(note)
        target = suggestion["chapter"]
        if target == "inbox":
            continue
        items.append({
            "id": _proposal_id("inbox_triage", note["path"], "move", target),
            "kind": "inbox_triage",
            "kind_title": "收件箱分流",
            "action": "move",
            "path": note["path"],
            "title": note.get("title", ""),
            "target_chapter": target,
            "target_title": suggestion.get("chapter_title", target),
            "target_pillar": suggestion.get("pillar", vault_layout.pillar_of(
                vault_layout.chapter_dir(target))),
            "target_pillar_title": suggestion.get("pillar_title", ""),
            "reason": suggestion.get("reason", ""),
            "confidence": suggestion.get("confidence", "low"),
            "requires_confirmation": True,
        })
    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda item: (order.get(item["confidence"], 3), item["path"]))
    return items


def inbox_proposals(vault_dir: Path | str) -> list[dict[str, Any]]:
    """只看收件箱的轻量提案，用于知识库整理卡。"""
    vault = Path(vault_dir).resolve()
    notes: list[dict[str, Any]] = []
    for summary in pillar_notes.list_notes(vault, chapter="inbox", limit=MAX_NOTES):
        try:
            note = pillar_notes.read_note(vault, summary["path"])
        except Exception:
            continue
        note["pillar"] = "knowledge"
        note["chapter"] = "inbox"
        notes.append(note)
    return _inbox_proposals(notes)


def proposals(vault_dir: Path | str) -> list[dict[str, Any]]:
    """确定性提案：收件箱分流 + 从反思/心得提炼原则。"""
    vault = Path(vault_dir).resolve()
    notes = _notes(vault)
    items = _inbox_proposals(notes)
    distilled = _distilled_sources(vault)

    for note in notes:
        if note.get("pillar") != "life":
            continue
        raw = note.get("raw_metadata") or {}
        chapter = note.get("chapter", "")
        explicit_memory = raw.get("memory") is True
        if chapter not in _DISTILL_CHAPTERS and not explicit_memory:
            continue
        cue = _distill_cue(note)
        if not cue:
            continue
        if note["path"] in distilled:
            continue
        target = _distill_target(note)
        items.append({
            "id": _proposal_id("distill", note["path"], "distill", target),
            "kind": "distill",
            "kind_title": "提炼原则",
            "action": "distill",
            "path": note["path"],
            "title": note.get("title", ""),
            "target_chapter": target,
            "target_title": steward_policy.CHAPTER_TITLES.get(target, target),
            "target_pillar": "self",
            "target_pillar_title": vault_layout.PILLAR_TITLES["self"],
            "reason": f"这条记录出现了「{cue}」，可以提炼成一条可复用的原则；原文会以双链保留。",
            "confidence": "high" if explicit_memory else "medium",
            "requires_confirmation": True,
        })

    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda item: (order.get(item["confidence"], 3), item["kind"], item["path"]))
    return items


def _principle_body(source: dict[str, Any]) -> str:
    excerpt = str(source.get("body") or "").strip()[:2000]
    return ("\n".join([
        f"> 来源：[[{source['path']}|{source.get('title') or source['path']}]]",
        "",
        "## 我提炼的原则",
        "",
        "（用一句话写下可以反复使用的原则）",
        "",
        "## 依据",
        "",
        excerpt or "（原文没有正文，请在这里补充依据）",
        "",
    ]))


_APPLY_LOCK = threading.Lock()


def apply_proposal(vault_dir: Path | str, database: Database, proposal_id: str, *,
                   confirm: bool) -> dict[str, Any]:
    """执行一条仍然有效的提案；提案失效或确认缺失时拒绝。"""
    if confirm is not True:
        raise ValueError("请明确确认执行这条提案")
    # 两个标签页同时点“确认应用”时至少不会都通过旧提案；进程内串行足够。
    with _APPLY_LOCK:
        return _apply_proposal_locked(vault_dir, database, proposal_id)


def _apply_proposal_locked(vault_dir: Path | str, database: Database,
                           proposal_id: str) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    current = proposals(vault)
    proposal = next((item for item in current if item["id"] == proposal_id), None)
    if proposal is None:
        raise ValueError("提案已失效：原笔记可能被移动、修改或已处理，请刷新后再看")
    return _execute_proposal(vault, database, proposal)


def _execute_proposal(vault: Path, database: Database,
                      proposal: dict[str, Any]) -> dict[str, Any]:
    if proposal["action"] == "move":
        note = pillar_notes.move_note(vault, proposal["path"], proposal["target_chapter"])
    elif proposal["action"] == "distill":
        if proposal["path"] in _distilled_sources(vault):
            raise ValueError("这条记录已经提炼过，请刷新提案")
        source = pillar_notes.read_note(vault, proposal["path"])
        note = pillar_notes.create_note(
            vault,
            pillar="self",
            chapter=proposal["target_chapter"],
            title=source.get("title") or "提炼的原则",
            body=_principle_body(source),
            note_type="principle",
            tags=list(source.get("tags") or []),
            source=source["path"],
        )
    else:
        raise ValueError("不支持的提案动作")

    database.audit("steward_proposal_applied", {
        "proposal_id": proposal["id"],
        "kind": proposal["kind"],
        "action": proposal["action"],
        "target_chapter": proposal["target_chapter"],
        "source_path": proposal["path"],
        "result_path": note["path"],
    })
    return {"proposal": proposal, "note": note}


def _terms(question: str) -> set[str]:
    text = str(question or "").casefold()
    terms = set(re.findall(r"[a-z0-9_]{2,}", text))
    for segment in re.findall(r"[一-鿿]+", text):
        if len(segment) <= 4:
            terms.add(segment)
        else:
            terms.update(segment[index:index + 2] for index in range(len(segment) - 1))
    return {term for term in terms if term.strip()}


def _excerpt(body: str, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(body or "")).strip()[:limit]


def search_notes(vault_dir: Path | str, question: str,
                 limit: int = MAX_ANSWER_CITATIONS) -> list[dict[str, Any]]:
    """朴素词项检索：标题权重最高，其次标签，最后正文。"""
    vault = Path(vault_dir).resolve()
    text = str(question or "").strip().casefold()
    terms = _terms(text)
    if not terms:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for note in _notes(vault):
        title = str(note.get("title") or "").casefold()
        body = str(note.get("body") or "").casefold()
        tags = " ".join(str(tag) for tag in (note.get("tags") or [])).casefold()
        score = 0
        for term in terms:
            if term in title:
                score += 5
            if term in tags:
                score += 3
            if term in body:
                score += 1
        if text and text in title:
            score += 6
        if score:
            scored.append((score, note))
    scored.sort(key=lambda pair: (pair[0], str(pair[1].get("date") or ""),
                                  pair[1].get("mtime") or 0), reverse=True)
    return [note for _score, note in scored[:max(1, min(12, limit))]]


def _citation(note: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": note["path"],
        "title": note.get("title", ""),
        "pillar": note.get("pillar", ""),
        "chapter": note.get("chapter", ""),
        "date": note.get("date", ""),
        "excerpt": _excerpt(note.get("body", "")),
    }


def _local_answer(citations: list[dict[str, Any]], warning: str | None) -> str:
    if not citations:
        return "没有在四库笔记里找到与这个问题直接相关的记录。可以换一个更具体的词，或先把资料捕获到收件箱。"
    lines = ["以下是四库笔记里的直接摘录（本地检索）："]
    if warning:
        lines.append(f"\n提示：{warning}")
    for index, item in enumerate(citations[:3], start=1):
        lines.append(f"\n[{index}] {item['title']} · {item['path']}\n{item['excerpt']}")
    return "\n".join(lines)


async def answer_question(vault_dir: Path | str, question: str, gateway: ModelGateway, *,
                          use_cloud: bool = False,
                          limit: int = MAX_ANSWER_CITATIONS) -> dict[str, Any]:
    """带引用的问答；只有 use_cloud=true 且模型已配置才外发笔记片段。"""
    hits = search_notes(vault_dir, question, limit=limit)
    citations = [_citation(note) for note in hits]
    warning: str | None = None
    mode = "local"
    model = None
    usage = None
    answer = None

    if use_cloud:
        if not gateway.configured:
            warning = "模型尚未配置，已使用本地检索。"
        else:
            evidence = [{
                "path": note["path"],
                "title": note.get("title", ""),
                "content": str(note.get("body") or "")[:1800],
            } for note in hits]
            try:
                result = await gateway.generate(str(question).strip(), evidence)
                if result.get("answer"):
                    answer = result["answer"]
                    mode = str(result.get("mode") or "cloud")
                    model = result.get("model")
                    usage = result.get("usage")
                else:
                    warning = "模型没有生成可用回答，已使用本地检索。"
            except ModelGatewayError as error:
                warning = f"{error.message} 已使用本地检索。"
            except Exception:
                warning = "云端调用未完成，已使用本地检索。请检查模型连接或额度。"

    if answer is None:
        answer = _local_answer(citations, warning)

    return {
        "answer": answer,
        "mode": mode,
        "model": model,
        "usage": usage,
        "warning": warning,
        "citations": citations,
        "principles": [
            "只依据列出的笔记摘录回答；没有证据时明确说不知道。",
            "默认不调用云端模型；勾选后只发送与问题相关的笔记片段。",
        ],
    }


def create_steward_router(database: Database, vault_dir: Path | str, gateway: ModelGateway, *,
                          read_only: bool = False, prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["steward"])

    @router.get("/steward/status")
    def steward_status():
        return {
            "model_configured": gateway.configured,
            "default_mode": "local",
            "note": "管家只提议，不自动执行；问答默认本地检索，勾选后才使用云端模型。",
        }

    @router.get("/steward/health")
    def steward_health():
        return health_check(vault_dir)

    @router.get("/steward/proposals")
    def steward_proposals(limit: int = Query(default=100, ge=1, le=500)):
        all_items = proposals(vault_dir)
        items = all_items[:limit]
        return {"items": items, "count": len(items), "total": len(all_items),
                "truncated": len(all_items) > len(items),
                "note": "提案是确定性的，不会自动执行；应用前请核对源路径与目标章节。"}

    @router.post("/steward/proposals/apply")
    def steward_apply(payload: ProposalApply):
        if read_only:
            raise HTTPException(409, "当前是只读模式")
        ensure_vault_writable(read_only, "执行管家提案")
        try:
            return apply_proposal(vault_dir, database, payload.proposal_id,
                                  confirm=payload.confirm)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(409, "原笔记已不存在，请刷新提案") from error

    @router.post("/steward/proposals/apply-batch")
    def steward_apply_batch(payload: ProposalBatchApply):
        """按顺序执行选中的提案；单条失败不会回滚已成功的条目。"""
        if read_only:
            raise HTTPException(409, "当前是只读模式")
        ensure_vault_writable(read_only, "批量执行管家提案")
        applied: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []
        with _APPLY_LOCK:
            vault = Path(vault_dir).resolve()
            current = {item["id"]: item for item in proposals(vault)}
            for proposal_id in payload.proposal_ids:
                proposal = current.get(proposal_id)
                if proposal is None:
                    failed.append({"proposal_id": proposal_id,
                                   "error": "提案已失效，请刷新后重试"})
                    continue
                try:
                    result = _execute_proposal(vault, database, proposal)
                except HTTPException as error:
                    failed.append({"proposal_id": proposal_id, "error": str(error.detail)})
                except (ValueError, FileNotFoundError, OSError) as error:
                    failed.append({"proposal_id": proposal_id, "error": str(error)})
                else:
                    applied.append({
                        "proposal_id": proposal_id,
                        "path": result["proposal"]["path"],
                        "target_chapter": result["proposal"]["target_chapter"],
                    })
        database.audit("steward_batch_applied", {
            "requested": len(payload.proposal_ids),
            "applied": len(applied),
            "failed": len(failed),
        })
        return {
            "applied": applied,
            "failed": failed,
            "applied_count": len(applied),
            "failed_count": len(failed),
            "note": "逐条执行；失败项保留在原处，可刷新后重试。",
        }

    @router.post("/steward/ask")
    async def steward_ask(payload: AskRequest):
        return await answer_question(vault_dir, payload.question, gateway,
                                     use_cloud=payload.use_cloud)

    return router


__all__ = [
    "health_check", "proposals", "apply_proposal", "search_notes",
    "answer_question", "create_steward_router",
]
