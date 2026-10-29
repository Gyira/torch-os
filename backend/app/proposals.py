"""Generic AI proposals: the only bridge between a role's suggestion and a write.

A role never writes the Vault.  It returns a proposal (new note, task, habit,
identity patch, report).  Torch stores it as ``pending``; the user must click
采纳 (``apply`` with ``confirm=true``); then a registered handler performs the
write through the existing module (identity/pillar_notes/tasks/habits).

P0 ships the state machine and HTTP surface.  Real handlers are registered by
the phase that owns each kind, so an unimplemented kind fails closed with a
clear 409 instead of silently doing nothing.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .db import Database, utc_now

PROPOSAL_KINDS = frozenset({
    "identity_patch", "new_note", "archive_note", "new_task", "new_habit",
    "health_report", "finance_report", "life_advice", "memory_note",
})
STATUSES = frozenset({"pending", "applying", "applied", "rejected", "expired", "failed"})
MAX_TITLE = 200
MAX_SUMMARY = 2000
MAX_REASON = 2000
MAX_SOURCES = 20
DEFAULT_TTL_DAYS = 7

ProposalHandler = Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]


class ProposalError(RuntimeError):
    """A proposal action failed for a user-visible reason."""

    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expiry() -> str:
    return (_now() + timedelta(days=DEFAULT_TTL_DAYS)).isoformat()


def _clean_text(value: Any, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _clean_sources(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()[:500]
        if text and text not in result:
            result.append(text)
    return result[:MAX_SOURCES]


def _loads(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    payload = _loads(row["payload_json"], {})
    sources = _loads(row["sources_json"], [])
    verdict = _loads(row["verdict_json"], {})
    result = _loads(row["result_json"], {})
    requires_dialogue = bool(verdict.get("requires_dialogue"))
    return {
        "id": row["id"],
        "kind": row["kind"],
        "role_id": row["role_id"],
        "thread_id": row["thread_id"],
        "title": row["title"],
        "summary": row["summary"],
        "reason": row["reason"],
        "payload": payload,
        "sources": sources,
        "base_revision": row["base_revision"],
        "verdict": verdict,
        "requires_dialogue": requires_dialogue,
        "verdict_ready": bool(row["verdict_token"]) if requires_dialogue else True,
        "status": row["status"],
        "result": result,
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
        "expires_at": row["expires_at"],
        "can_apply": row["status"] == "pending" and (not requires_dialogue or bool(row["verdict_token"])),
        "stale": False,
    }


class ProposalService:
    """SQLite-backed proposal state machine; handlers are injected per phase."""

    def __init__(self, database: Database, *, handlers: Mapping[str, ProposalHandler] | None = None):
        self.database = database
        self.handlers: dict[str, ProposalHandler] = dict(handlers or {})

    def register(self, kind: str, handler: ProposalHandler) -> None:
        if kind not in PROPOSAL_KINDS:
            raise ValueError(f"未知提案类型：{kind}")
        self.handlers[kind] = handler

    def create(self, *, kind: str, title: str, payload: Mapping[str, Any],
               summary: str = "", reason: str = "", sources: Any = None,
               role_id: str = "", thread_id: str = "", base_revision: str = "",
               verdict: Mapping[str, Any] | None = None,
               verdict_token: str = "", dedupe_key: str = "") -> dict[str, Any]:
        kind = str(kind or "").strip()
        if kind not in PROPOSAL_KINDS:
            raise ProposalError("未知的提案类型", 422)
        title = _clean_text(title, limit=MAX_TITLE)
        if not title:
            raise ProposalError("提案标题不能为空", 422)
        if not isinstance(payload, Mapping):
            raise ProposalError("提案内容必须是对象", 422)
        payload_json = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        if len(payload_json) > 200_000:
            raise ProposalError("提案内容太长", 422)
        sources_list = _clean_sources(sources)
        verdict_dict = dict(verdict or {})
        key = str(dedupe_key or "").strip()
        if not key:
            seed = json.dumps({"kind": kind, "title": title, "payload": payload,
                               "role_id": role_id}, ensure_ascii=False, sort_keys=True)
            key = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40]
        proposal_id = "prop_" + uuid.uuid4().hex[:24]
        now = utc_now()
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """INSERT INTO ai_proposals
                       (id, kind, role_id, thread_id, title, summary, reason, payload_json,
                        sources_json, base_revision, verdict_json, verdict_token, dedupe_key,
                        status, result_json, created_at, decided_at, expires_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        proposal_id, kind, str(role_id or ""), str(thread_id or ""),
                        title, _clean_text(summary, limit=MAX_SUMMARY),
                        _clean_text(reason, limit=MAX_REASON), payload_json,
                        json.dumps(sources_list, ensure_ascii=False),
                        str(base_revision or ""), json.dumps(verdict_dict, ensure_ascii=False),
                        str(verdict_token or ""), key, "pending", "{}", now, None, _expiry(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ProposalError("这个提案已经存在，可能刚被创建过", 409) from exc
        self.database.audit("proposal_created", {
            "proposal_id": proposal_id, "kind": kind, "role_id": str(role_id or ""),
            "sources": len(sources_list),
        })
        return self.get(proposal_id)

    def get(self, proposal_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_proposals WHERE id=?", (str(proposal_id or "").strip(),)
            ).fetchone()
        if row is None:
            raise ProposalError("提案不存在或已过期", 404)
        return _row_to_dict(row)

    def list(self, *, status: str = "pending", kind: str = "", limit: int = 100) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if status:
            if status not in STATUSES:
                raise ProposalError("未知的提案状态", 422)
            clauses.append("status=?")
            params.append(status)
        if kind:
            if kind not in PROPOSAL_KINDS:
                raise ProposalError("未知的提案类型", 422)
            clauses.append("kind=?")
            params.append(kind)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(int(limit), 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM ai_proposals{where} ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def apply(self, proposal_id: str, *, confirm: bool = False,
              edits: Mapping[str, Any] | None = None,
              verdict_token: str = "") -> dict[str, Any]:
        if confirm is not True:
            raise ProposalError("请明确确认采纳这个提案", 422)
        existing = self.get(proposal_id)
        if existing["status"] == "applied":
            return existing
        if existing["status"] != "pending":
            raise ProposalError("这个提案已经处理过了", 409)
        expires_at = str(existing.get("expires_at") or "")
        if expires_at and expires_at < utc_now():
            self._set_status(proposal_id, "expired")
            raise ProposalError("这个提案已过期，请让 AI 重新生成", 409)
        if existing["requires_dialogue"]:
            stored = self._token(proposal_id)
            if not stored or str(verdict_token or "") != stored:
                raise ProposalError("这个身份修改需要先在苏格拉底对话里说服 AI", 409)
        handler = self.handlers.get(existing["kind"])
        if handler is None:
            raise ProposalError("这个提案类型还没有启用落盘处理器", 409)
        self._set_status(proposal_id, "applying")
        try:
            result = handler(existing, dict(edits) if edits else None)
        except ProposalError:
            self._set_status(proposal_id, "failed")
            raise
        except Exception as exc:  # keep the proposal visible instead of half-writing
            self._set_status(proposal_id, "failed")
            raise ProposalError(f"应用提案失败：{exc}", 500) from exc
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE ai_proposals SET status='applied', result_json=?, decided_at=? WHERE id=?",
                (json.dumps(result or {}, ensure_ascii=False), utc_now(), proposal_id),
            )
        self.database.audit("proposal_applied", {
            "proposal_id": proposal_id, "kind": existing["kind"],
            "role_id": existing["role_id"],
        })
        return self.get(proposal_id)

    def reject(self, proposal_id: str, *, reason: str = "") -> dict[str, Any]:
        existing = self.get(proposal_id)
        if existing["status"] == "rejected":
            return existing
        if existing["status"] not in {"pending", "failed"}:
            raise ProposalError("这个提案已经处理过了", 409)
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE ai_proposals SET status='rejected', result_json=?, decided_at=? WHERE id=?",
                (json.dumps({"reason": _clean_text(reason, limit=MAX_REASON)}, ensure_ascii=False),
                 utc_now(), proposal_id),
            )
        self.database.audit("proposal_rejected", {
            "proposal_id": proposal_id, "kind": existing["kind"],
        })
        return self.get(proposal_id)

    def _token(self, proposal_id: str) -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT verdict_token FROM ai_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
        return str(row["verdict_token"] or "") if row else ""

    def _set_status(self, proposal_id: str, status: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE ai_proposals SET status=?, decided_at=? WHERE id=?",
                (status, utc_now() if status in {"expired", "failed"} else None, proposal_id),
            )


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    confirm: bool = Field(strict=True)
    edits: dict[str, Any] | None = None
    verdict_token: str = Field(default="", max_length=200)


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(default="", max_length=MAX_REASON)


def create_proposals_router(service: ProposalService, prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["proposals"])

    def _guard(action):
        try:
            return action()
        except ProposalError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc

    @router.get("/proposals")
    def proposals(status: str = Query(default="pending"), kind: str = Query(default=""),
                  limit: int = Query(default=100, ge=1, le=500)):
        return {"items": _guard(lambda: service.list(status=status, kind=kind, limit=limit))}

    @router.get("/proposals/{proposal_id}")
    def proposal(proposal_id: str):
        return _guard(lambda: service.get(proposal_id))

    @router.post("/proposals/{proposal_id}/apply")
    def apply_proposal(proposal_id: str, payload: ApplyRequest):
        return _guard(lambda: service.apply(
            proposal_id, confirm=payload.confirm, edits=payload.edits,
            verdict_token=payload.verdict_token,
        ))

    @router.post("/proposals/{proposal_id}/reject")
    def reject_proposal(proposal_id: str, payload: RejectRequest):
        return _guard(lambda: service.reject(proposal_id, reason=payload.reason))

    return router


__all__ = [
    "PROPOSAL_KINDS", "STATUSES", "ProposalError", "ProposalService",
    "create_proposals_router",
]
