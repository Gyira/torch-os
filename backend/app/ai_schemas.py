"""Tolerant structured-output models for AI roles.

The role prompts ask for a small JSON object, but models occasionally wrap it in
prose or a ```json fence.  We prefer the last complete object, validate it with
Pydantic, and fall back to plain text.  A malformed structure must never become
a proposal or an identity write.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Finding(_Loose):
    kind: str = "observation"
    text: str = Field(default="", max_length=4000)
    confidence: str = Field(default="medium", max_length=20)
    sources: list[str] = Field(default_factory=list, max_length=20)


class ProposalDraft(_Loose):
    kind: str = Field(min_length=1, max_length=40)
    title: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=2000)
    reason: str = Field(default="", max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list, max_length=20)
    confidence: str = Field(default="medium", max_length=20)


class IdentityVerdict(_Loose):
    decision: Literal["accept", "reject", "undecided"] = "undecided"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=2000)
    changes: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class HubEvent(_Loose):
    kind: str = Field(default="observation", max_length=40)
    summary: str = Field(default="", max_length=1000)
    source_ref: str = Field(default="", max_length=500)


class ArchiveVerdict(_Loose):
    """人生导师对“档案这条是否要改”的裁决；只有 accept 才会生成待确认卡片。"""

    decision: Literal["accept", "reject", "undecided"] = "undecided"
    section: str = Field(default="", max_length=60)
    entry_id: str = Field(default="", max_length=80)
    action: Literal["create", "supersede"] = "create"
    entry_type: str = Field(default="inference", max_length=20)
    confidence: str = Field(default="medium", max_length=20)
    title: str = Field(default="", max_length=120)
    old_text: str = Field(default="", max_length=4000)
    new_text: str = Field(default="", max_length=8000)
    evidence: str = Field(default="", max_length=2000)
    counterexample: str = Field(default="", max_length=2000)
    verify: str = Field(default="", max_length=2000)
    reason: str = Field(default="", max_length=2000)
    sources: list[str] = Field(default_factory=list, max_length=20)


class MemoryNote(_Loose):
    kind: str = Field(default="observation", max_length=40)
    text: str = Field(default="", max_length=300)
    sources: list[str] = Field(default_factory=list, max_length=20)


class QuestionDraft(_Loose):
    prompt: str = Field(default="", max_length=300)
    target_section: str = Field(default="", max_length=60)
    section: str = Field(default="", max_length=60)
    entry_type: str = Field(default="narrative", max_length=20)
    priority: int = Field(default=50, ge=0, le=100)
    why: str = Field(default="", max_length=500)
    sources: list[str] = Field(default_factory=list, max_length=20)


class SynthesisResult(_Loose):
    portrait: str = Field(default="", max_length=20_000)
    portrait_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    archive_patches: list[dict[str, Any]] = Field(default_factory=list, max_length=30)
    memory_updates: list[MemoryNote] = Field(default_factory=list, max_length=30)
    question_candidates: list[QuestionDraft] = Field(default_factory=list, max_length=30)
    gaps: list[str] = Field(default_factory=list, max_length=20)


class Reply(_Loose):
    reply: str = Field(default="", max_length=20_000)
    findings: list[Finding] = Field(default_factory=list, max_length=50)
    proposals: list[ProposalDraft] = Field(default_factory=list, max_length=20)
    identity_verdict: IdentityVerdict | None = None
    archive_verdict: ArchiveVerdict | None = None
    memory_notes: list[MemoryNote] = Field(default_factory=list, max_length=20)
    questions: list[QuestionDraft] = Field(default_factory=list, max_length=10)
    hub_events: list[HubEvent] = Field(default_factory=list, max_length=20)


def _candidates(text: str) -> list[str]:
    """Fenced JSON blocks first, then the raw text."""
    result: list[str] = []
    for match in re.finditer(r"```(?:json)?\s*", text or ""):
        start = match.end()
        end = text.find("```", start)
        if end > start:
            result.append(text[start:end])
    result.append(text or "")
    return result


def extract_json(text: str) -> dict[str, Any] | None:
    """Find the first complete JSON object in a role reply."""
    decoder = json.JSONDecoder()
    for candidate in _candidates(text):
        for match in re.finditer(r"\{", candidate):
            try:
                value, _end = decoder.raw_decode(candidate[match.start():])
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    return None


def parse_reply(text: str) -> tuple[Reply, bool]:
    """Return ``(validated_reply, structured)``; never raise on model output."""
    raw = str(text or "").strip()
    data = extract_json(raw)
    if data is None:
        return Reply(reply=raw), False
    try:
        reply = Reply.model_validate(data)
    except Exception:
        return Reply(reply=raw), False
    if not reply.reply and not (reply.proposals or reply.findings
                                or reply.identity_verdict or reply.archive_verdict
                                or reply.memory_notes or reply.questions):
        reply.reply = raw
    return reply, True


__all__ = ["Finding", "ProposalDraft", "IdentityVerdict", "HubEvent", "Reply",
           "ArchiveVerdict", "MemoryNote", "QuestionDraft", "SynthesisResult",
           "extract_json", "parse_reply"]
