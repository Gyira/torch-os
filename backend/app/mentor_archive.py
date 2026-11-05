"""Validated mentor archive updates (B4b).

One place turns model output and user answers into the existing self-archive
entry model. It never creates a second memory store: facts/inferences still
live as Markdown entries, supersede still uses the B4a operation ledger.

The caller decides whether an update is committed autonomously (mentor chat /
synthesis) or kept as an old-style pending card (compatibility switch).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from . import mentor_pending, self_archive
from .role_policy import RolePolicy


def _field(raw: Any, name: str, default: Any = "") -> Any:
    if isinstance(raw, Mapping):
        return raw.get(name, default)
    return getattr(raw, name, default)


def _clean(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _sources(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()[:500]
        if text and text not in result:
            result.append(text)
    return result[:20]


def normalize_candidate(raw: Any, *, vault_dir: Path | str, policy: RolePolicy,
                        bindings: list[dict[str, Any]] | None = None,
                        thread_id: str = "", origin: str = "mentor",
                        require_fact_source: bool = True) -> tuple[dict[str, Any] | None, str]:
    """Validate one proposed entry; return ``(candidate, error)``.

    Facts must cite a source that resolves inside the mentor's read scope.
    Model guesses may still be stored, but only as ``inference`` (or another
    non-fact type) with the server keeping the mentor origin.
    """
    raw_section = str(_field(raw, "section") or "").strip()
    section = self_archive.normalize_book_key(raw_section)
    if not section:
        return None, self_archive.unknown_book_message(raw_section)
    action = str(_field(raw, "action") or "create").strip()
    if action not in ("create", "supersede"):
        return None, "档案动作只能是 create/supersede"
    entry_type = str(_field(raw, "entry_type") or "inference").strip().lower()
    if entry_type not in self_archive.ENTRY_TYPES:
        entry_type = "inference"
    confidence = str(_field(raw, "confidence") or "medium").strip().lower()
    if confidence not in self_archive.CONFIDENCES:
        confidence = "medium"
    title = _clean(_field(raw, "title") or "", self_archive.MAX_TITLE)
    # Both the new archive_updates item and the legacy archive_verdict are
    # accepted; the latter names the body ``new_text``.
    text = str(_field(raw, "text") or _field(raw, "new_text") or "").strip()
    if not title:
        return None, "条目标题不能为空"
    if not text:
        return None, "条目正文不能为空"
    if len(text) > self_archive.MAX_ENTRY_CHARS:
        return None, f"条目正文最多 {self_archive.MAX_ENTRY_CHARS} 字"
    resolved: list[str] = []
    trusted_source = False
    binding_ids = {str(item.get("id") or "") for item in (bindings or [])}
    for value in _sources(_field(raw, "sources")):
        try:
            reference = policy.reference(
                value, bindings=bindings or [], thread_id=thread_id)
        except Exception as error:  # includes RoleScopeError / HTTPException
            detail = getattr(error, "detail", None) or str(error)
            return None, f"来源未通过校验：{detail}"
        # A server-issued S-number or context source is already known to have
        # been assembled for this turn. A literal local path must still exist;
        # otherwise a model could invent a plausible-looking citation.
        if value.strip("[]") in binding_ids or reference.startswith("context:"):
            trusted_source = True
        elif reference.startswith(("http://", "https://")):
            # URLs are metadata only: the server never fetches them.
            pass
        else:
            try:
                target = policy.check_path(reference, note=True)
            except Exception as error:  # noqa: BLE001
                detail = getattr(error, "detail", None) or str(error)
                return None, f"来源未通过校验：{detail}"
            if not target.is_file():
                return None, f"来源文件不存在：{reference}"
            trusted_source = True
        resolved.append(reference)
    if entry_type == "fact" and require_fact_source and not trusted_source:
        return None, "事实条目必须有库内或本轮资料的明确来源，不能把模型推断写成事实"
    candidate = {
        "action": action,
        "section": section,
        "entry_id": str(_field(raw, "entry_id") or "").strip()[:80],
        "entry_type": entry_type,
        "confidence": confidence,
        "title": title,
        "text": text,
        "evidence": _clean(_field(raw, "evidence") or "", 2_000),
        "counterexample": _clean(_field(raw, "counterexample") or "", 2_000),
        "verify": _clean(_field(raw, "verify") or "", 2_000),
        "sources": resolved,
        "origin": origin if origin in self_archive.ORIGINS else "mentor",
        "old_text": "",
        "base_revision": "",
        "previous_entry_id": "",
        "previous_path": "",
    }
    if action == "supersede":
        if not candidate["entry_id"]:
            return None, "替代条目缺少 entry_id"
        old = self_archive.find_entry(
            vault_dir, candidate["entry_id"], include_history=False)
        if old is None:
            return None, "要修正的条目不存在或已被取代"
        if old["section"] != section:
            return None, "替代条目的本子必须与原条目一致"
        candidate["old_text"] = str(old.get("text") or "")[:4_000]
        candidate["base_revision"] = str(old.get("revision") or "")
        candidate["previous_entry_id"] = old["entry_id"]
        candidate["previous_path"] = old["path"]
        candidate["previous_text"] = str(old.get("text") or "")[:4_000]
    return candidate, ""


def fingerprint(candidate: Mapping[str, Any]) -> str:
    seed = json.dumps({
        "section": candidate.get("section"),
        "action": candidate.get("action"),
        "entry_id": candidate.get("entry_id"),
        "entry_type": candidate.get("entry_type"),
        "title": candidate.get("title"),
        "text": candidate.get("text"),
        "evidence": candidate.get("evidence"),
        "sources": candidate.get("sources"),
        "origin": candidate.get("origin"),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40]


def operation_id_for(prefix: str, candidate: Mapping[str, Any]) -> str:
    return f"{prefix}:{fingerprint(candidate)}"


def failure_result(raw: Any, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "action": str(_field(raw, "action") or "create"),
        "section": str(_field(raw, "section") or ""),
        "section_title": self_archive.BOOK_BY_KEY.get(
            str(_field(raw, "section") or ""), ("", "未分类", "", ()))[1]
        if str(_field(raw, "section") or "") in self_archive.BOOK_BY_KEY else "未分类",
        "entry_type": str(_field(raw, "entry_type") or "inference"),
        "entry_type_label": self_archive.ENTRY_TYPE_LABELS.get(
            str(_field(raw, "entry_type") or "inference"), "推断"),
        "confidence": str(_field(raw, "confidence") or "medium"),
        "confidence_label": self_archive.CONFIDENCE_LABELS.get(
            str(_field(raw, "confidence") or "medium"), "中"),
        "title": str(_field(raw, "title") or "")[:120],
        "text": "", "evidence": "", "sources": [],
        "entry_id": "", "previous_entry_id": "", "previous_path": "",
        "path": "", "error": error,
    }


def apply_candidate(vault_dir: Path | str, candidate: Mapping[str, Any], *,
                    operation_id: str, read_only: bool = False) -> dict[str, Any]:
    """Commit one normalized candidate through the existing archive primitives."""
    try:
        if candidate["action"] == "supersede":
            entry = self_archive.supersede_entry(
                vault_dir, candidate["entry_id"],
                entry_type=candidate["entry_type"],
                confidence=candidate["confidence"],
                title=candidate["title"], text=candidate["text"],
                evidence=candidate["evidence"],
                counterexample=candidate["counterexample"],
                verify=candidate["verify"], sources=candidate["sources"],
                origin=candidate["origin"],
                base_revision=candidate["base_revision"],
                read_only=read_only, operation_id=operation_id,
            )
        else:
            entry = self_archive.create_entry(
                vault_dir, book_key=candidate["section"],
                entry_type=candidate["entry_type"],
                confidence=candidate["confidence"],
                title=candidate["title"], text=candidate["text"],
                evidence=candidate["evidence"],
                counterexample=candidate["counterexample"],
                verify=candidate["verify"], sources=candidate["sources"],
                origin=candidate["origin"], read_only=read_only,
                operation_id=operation_id,
            )
    except Exception as error:  # noqa: BLE001 - per-item failure is user-visible
        result = failure_result(candidate, str(error))
        result["operation_id"] = operation_id
        return result
    return {
        "ok": True,
        "operation_id": operation_id,
        "action": candidate["action"],
        "section": candidate["section"],
        "section_title": self_archive.BOOK_BY_KEY[candidate["section"]][1],
        "entry_type": candidate["entry_type"],
        "entry_type_label": self_archive.ENTRY_TYPE_LABELS[candidate["entry_type"]],
        "confidence": candidate["confidence"],
        "confidence_label": self_archive.CONFIDENCE_LABELS[candidate["confidence"]],
        "title": entry["title"],
        "text": entry["text"],
        "evidence": entry["evidence"],
        "sources": list(entry.get("sources") or []),
        "entry_id": entry["entry_id"],
        "path": entry["path"],
        "previous_entry_id": candidate.get("previous_entry_id") or "",
        "previous_path": candidate.get("previous_path") or "",
        "previous_text": candidate.get("previous_text") or "",
        "error": "",
    }


def to_pending(vault_dir: Path | str, candidates: list[Mapping[str, Any]], *,
               read_only: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    """Compatibility path: keep old-style confirmation cards instead of writing."""
    cards: list[dict[str, Any]] = []
    errors: list[str] = []
    for candidate in candidates:
        try:
            cards.append(mentor_pending.create_pending(vault_dir, {
                "section": candidate["section"],
                "action": candidate["action"],
                "entry_id": candidate["entry_id"],
                "entry_type": candidate["entry_type"],
                "confidence": candidate["confidence"],
                "title": candidate["title"],
                "old_text": candidate.get("old_text") or "",
                "new_text": candidate["text"],
                "evidence": candidate["evidence"],
                "counterexample": candidate["counterexample"],
                "verify": candidate["verify"],
                "reason": "",
                "sources": candidate["sources"],
                "base_revision": candidate["base_revision"],
                "verdict": {"decision": "accept", "source": "mentor_autonomous_off"},
            }, read_only=read_only))
        except Exception as error:  # noqa: BLE001
            errors.append(str(error))
    return cards, errors


__all__ = ["normalize_candidate", "fingerprint", "operation_id_for", "failure_result",
           "apply_candidate", "to_pending"]
