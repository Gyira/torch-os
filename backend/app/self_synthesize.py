"""人生导师「更新自我档案」的全量整理编排（2026-11-01）。

一次调用只产生：

- 现状画像（写 00_画像.md，旧版备份）；
- 档案补丁（写待确认卡，不直接改档案）；
- 记忆时间线/核心记忆更新；
- 动态题库问题；
- gaps（资料不足的本子）。

模型输出不合法或 AI 失败时不写任何半成品。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from . import mentor_memory, mentor_pending, mentor_questions, self_archive
from .ai_schemas import SynthesisResult, extract_json

SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_SYNTH_CHARS = 60_000
RANGE_DAYS = (7, 14, 30)


class SynthesizeError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _persist_patches(vault: Path, patches: list[dict[str, Any]], *,
                     read_only: bool) -> tuple[list[dict[str, Any]], list[str]]:
    created: list[dict[str, Any]] = []
    errors: list[str] = []
    for raw in patches[:30]:
        if not isinstance(raw, dict):
            continue
        section = str(raw.get("section") or "").strip()
        if section not in self_archive.BOOK_BY_KEY:
            errors.append(f"未知本子：{section}")
            continue
        new_text = str(raw.get("new_text") or "").strip()
        if not new_text:
            continue
        try:
            card = mentor_pending.create_pending(vault, {
                "section": section,
                "action": raw.get("action") or "create",
                "entry_id": raw.get("entry_id") or "",
                "entry_type": raw.get("entry_type") or "inference",
                "confidence": raw.get("confidence") or "medium",
                "title": raw.get("title") or "档案修改",
                "old_text": raw.get("old_text") or "",
                "new_text": new_text,
                "evidence": raw.get("evidence") or "",
                "counterexample": raw.get("counterexample") or "",
                "verify": raw.get("verify") or "",
                "reason": raw.get("reason") or "",
                "sources": raw.get("sources") or [],
                "verdict": {"decision": "accept", "source": "synthesize"},
            }, read_only=read_only)
        except Exception as error:  # noqa: BLE001
            errors.append(f"{section}：{error}")
            continue
        created.append(card)
    return created, errors


def _persist_memory(vault: Path, updates: list[Any], *, read_only: bool) -> int:
    timeline: list[dict[str, Any]] = []
    core_text = ""
    for item in updates[:30]:
        kind = str(getattr(item, "kind", "") or "")
        text = str(getattr(item, "text", "") or "").strip()
        sources = list(getattr(item, "sources", []) or [])
        if not text:
            continue
        if kind == "core":
            core_text = text
            continue
        timeline.append({"kind": "state" if kind in ("timeline", "state") else kind,
                         "text": text, "sources": sources})
    count = 0
    if timeline:
        try:
            count += len(mentor_memory.append_timeline(vault, timeline, read_only=read_only))
        except Exception:
            pass
    if core_text:
        try:
            mentor_memory.write_core(vault, core_text[:3_000], read_only=read_only)
            count += 1
        except Exception:
            pass
    return count


def synthesize(role_service: Any, vault_dir: Path | str, *, range_days: int = 14,
               read_only: bool = False) -> dict[str, Any]:
    if role_service is None:
        raise SynthesizeError("AI 不可用：请先在 dsh 里配置模型", 503)
    days = max(7, min(int(range_days or 14), 30))
    vault = Path(vault_dir).resolve()
    mentor_memory.ensure_memory(vault)
    try:
        mentor_questions.ensure_bank(vault, read_only=read_only)
    except Exception:
        pass
    prompt = json.dumps({
        "task": "全量整理自我档案",
        "range_days": days,
        "输出要求": "只输出一个 JSON 对象；结构见角色说明。没有证据的写进 gaps，不要补脑。",
    }, ensure_ascii=False)
    try:
        result = role_service.run_once(
            "mentor", "synthesize", prompt, entry="self.archive",
            selectors=[
                {"entry": "identity"},
                {"entry": "self_archive"},
                {"entry": "mentor_memory"},
                {"entry": "journals_recent", "days": days},
                {"entry": "ops_summary"},
                {"entry": "recent_notes", "days": days},
            ],
            max_chars=MAX_SYNTH_CHARS,
        )
    except Exception as error:  # noqa: BLE001
        raise SynthesizeError(f"整理失败：{error}", 502) from error
    data = result.get("structured")
    if not isinstance(data, dict):
        data = extract_json(result.get("text") or "") or {}
    try:
        parsed = SynthesisResult.model_validate(data)
    except Exception as error:  # noqa: BLE001
        raise SynthesizeError("模型没有返回可用的结构化结果；没有写入任何文件", 502) from error

    portrait_written = False
    if parsed.portrait.strip():
        try:
            self_archive.write_portrait(
                vault, parsed.portrait, sources=[],
                confidence=parsed.portrait_confidence, read_only=read_only)
            portrait_written = True
        except Exception as error:  # noqa: BLE001
            raise SynthesizeError(f"画像写入失败：{error}", 500) from error

    patches, patch_errors = _persist_patches(
        vault, parsed.archive_patches, read_only=read_only)
    memory_count = _persist_memory(vault, parsed.memory_updates, read_only=read_only)
    questions: list[dict[str, Any]] = []
    dynamic_rows = []
    for item in parsed.question_candidates[:12]:
        section = str(item.target_section or item.section or "").strip()
        if section not in self_archive.BOOK_BY_KEY or not item.prompt.strip():
            continue
        dynamic_rows.append({
            "section": section, "prompt": item.prompt,
            "entry_type": item.entry_type, "priority": item.priority,
            "why": item.why, "sources": item.sources,
        })
    if dynamic_rows:
        try:
            questions = mentor_questions.append_dynamic_questions(
                vault, dynamic_rows, read_only=read_only)
        except Exception:
            questions = []
    return {
        "portrait": parsed.portrait.strip(),
        "portrait_written": portrait_written,
        "patches": patches,
        "patch_errors": patch_errors,
        "memory_updates": memory_count,
        "questions": questions,
        "gaps": [str(item) for item in parsed.gaps],
        "sources": result.get("sources") or [],
        "truncated": bool(result.get("truncated")),
        "seconds": result.get("seconds"),
    }


__all__ = ["SynthesizeError", "synthesize", "MAX_SYNTH_CHARS", "RANGE_DAYS"]
