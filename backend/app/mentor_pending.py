"""人生导师的待确认卡片（2026-11-01）。

卡片是 Markdown，落在 ``01_自我档案/02_人生导师/待确认/档案补丁/``。
导师只生成卡片；用户确认后才会通过 self_archive 写入档案。
采纳/驳回后卡片移入同级的 ``已采纳/`` 或 ``已驳回/``，不删除内容。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import yaml

from . import self_archive, vault_layout
from .memory_store import operation_lock
from .vault import parse_text, safe_note_path
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SHANGHAI = ZoneInfo("Asia/Shanghai")
PENDING_TYPE = "mentor_pending"
KINDS = ("archive_patch",)
STATUSES = ("pending", "accepted", "rejected", "superseded")
MAX_NEW_TEXT = 8_000


class PendingError(RuntimeError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", delete=False,
        dir=str(path.parent), prefix=".pending-", suffix=".tmp",
    )
    try:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, path)
    except OSError:
        Path(handle.name).unlink(missing_ok=True)
        raise


def _ensure_write(read_only: bool, action: str) -> None:
    try:
        ensure_vault_writable(read_only, action)
    except VaultReadOnlyError as error:
        raise PendingError(str(error), 409) from error


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


def _payload_text(metadata: Mapping[str, Any]) -> str:
    lines = [
        "---",
        *yaml.safe_dump(dict(metadata), allow_unicode=True, sort_keys=False)
        .rstrip("\n").split("\n"),
        "---",
        "",
        str(metadata.get("new_text") or "").strip(),
        "",
    ]
    return "\n".join(lines)


def _dedupe_key(metadata: Mapping[str, Any]) -> str:
    seed = json.dumps({
        "kind": metadata.get("kind"),
        "section": metadata.get("section"),
        "action": metadata.get("action"),
        "entry_id": metadata.get("entry_id"),
        "title": metadata.get("title"),
        "new_text": metadata.get("new_text"),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40]


def _card_dir(vault: Path, relative: str) -> Path:
    return safe_note_path(vault, relative)


def _read_card(vault: Path, path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_bytes()
        metadata, body = parse_text(raw.decode("utf-8-sig"))
    except (OSError, ValueError, yaml.YAMLError):
        return None
    relative = path.resolve().relative_to(vault.resolve()).as_posix()
    kind = str(metadata.get("kind") or "archive_patch").strip()
    if kind not in KINDS:
        kind = "archive_patch"
    status = str(metadata.get("status") or "pending").strip()
    if status not in STATUSES:
        status = "pending"
    return {
        "type": PENDING_TYPE,
        "pending_id": str(metadata.get("pending_id") or path.stem),
        "kind": kind,
        "status": status,
        "created": str(metadata.get("created") or ""),
        "section": str(metadata.get("section") or ""),
        "section_title": str(metadata.get("section_title") or ""),
        "action": str(metadata.get("action") or "create"),
        "entry_id": str(metadata.get("entry_id") or ""),
        "entry_type": str(metadata.get("entry_type") or "inference"),
        "confidence": str(metadata.get("confidence") or "medium"),
        "title": str(metadata.get("title") or ""),
        "old_text": str(metadata.get("old_text") or ""),
        "new_text": str(metadata.get("new_text") or body or "").strip(),
        "evidence": str(metadata.get("evidence") or ""),
        "counterexample": str(metadata.get("counterexample") or ""),
        "verify": str(metadata.get("verify") or ""),
        "reason": str(metadata.get("reason") or ""),
        "sources": _sources(metadata.get("sources")),
        "base_revision": str(metadata.get("base_revision") or ""),
        "verdict": metadata.get("verdict") if isinstance(metadata.get("verdict"), dict) else {},
        "result_entry_id": str(metadata.get("result_entry_id") or ""),
        "path": relative,
        "revision": hashlib.sha256(raw).hexdigest()[:16],
    }


def create_pending(vault_dir: Path | str, payload: Mapping[str, Any], *,
                   read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "写入人生导师待确认")
    vault = Path(vault_dir).resolve()
    section = self_archive.normalize_book_key(payload.get("section"))
    if not section:
        raise PendingError(self_archive.unknown_book_message(payload.get("section")), 422)
    self_archive.book_meta(section)  # validate
    new_text = str(payload.get("new_text") or "").strip()
    if not new_text:
        raise PendingError("补丁正文不能为空", 422)
    if len(new_text) > MAX_NEW_TEXT:
        raise PendingError("补丁正文过长", 422)
    action = str(payload.get("action") or "create").strip()
    if action not in ("create", "supersede"):
        raise PendingError("补丁动作只能是 create/supersede", 422)
    entry_id = str(payload.get("entry_id") or "").strip()
    base_revision = str(payload.get("base_revision") or "").strip()
    if action == "supersede":
        old = self_archive.find_entry(vault, entry_id, include_history=False)
        if old is None:
            raise PendingError("要修正的条目不存在或已被取代", 404)
        if old["section"] != section:
            raise PendingError("替代条目的本子必须与原条目一致", 422)
        if not base_revision:
            base_revision = old["revision"]
    meta = self_archive.book_meta(section)
    entry_type = str(payload.get("entry_type") or "inference").strip().lower()
    if entry_type not in self_archive.ENTRY_TYPES:
        entry_type = "inference"
    confidence = str(payload.get("confidence") or "medium").strip().lower()
    if confidence not in self_archive.CONFIDENCES:
        confidence = "medium"
    clean_meta = {
        "type": PENDING_TYPE,
        "pending_id": "pend_" + uuid.uuid4().hex[:12],
        "kind": "archive_patch",
        "status": "pending",
        "created": _now(),
        "section": section,
        "section_title": meta["title"],
        "action": action,
        "entry_id": entry_id,
        "entry_type": entry_type,
        "confidence": confidence,
        "title": _clean(payload.get("title") or "档案修改", 120),
        "old_text": _clean(payload.get("old_text") or "", 4_000),
        "new_text": new_text,
        "evidence": _clean(payload.get("evidence") or "", 2_000),
        "counterexample": _clean(payload.get("counterexample") or "", 2_000),
        "verify": _clean(payload.get("verify") or "", 2_000),
        "reason": _clean(payload.get("reason") or "", 2_000),
        "sources": _sources(payload.get("sources")),
        "base_revision": base_revision,
        "verdict": payload.get("verdict") if isinstance(payload.get("verdict"), dict) else {},
        "dedupe_key": _dedupe_key({
            "kind": "archive_patch", "section": section, "action": action,
            "entry_id": entry_id, "title": payload.get("title") or "档案修改",
            "new_text": new_text,
        }),
    }
    directory = _card_dir(vault, vault_layout.SELF_MENTOR_PENDING_PATCHES)
    directory.mkdir(parents=True, exist_ok=True)
    for existing in directory.glob("*.md"):
        item = _read_card(vault, existing)
        if item is None or item["status"] != "pending":
            continue
        try:
            existing_meta, _ = parse_text(existing.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, yaml.YAMLError):
            continue
        if str(existing_meta.get("dedupe_key") or "") == clean_meta["dedupe_key"]:
            return item
    path = directory / f"{_stamp()}-{clean_meta['pending_id'][-8:]}.md"
    _atomic_write(path, _payload_text(clean_meta))
    item = _read_card(vault, path)
    if item is None:
        raise PendingError("待确认卡片写入后无法读回", 500)
    return item


def list_pending(vault_dir: Path | str) -> list[dict[str, Any]]:
    vault = Path(vault_dir).resolve()
    directory = _card_dir(vault, vault_layout.SELF_MENTOR_PENDING_PATCHES)
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        item = _read_card(vault, path)
        if item is not None and item["status"] == "pending":
            items.append(item)
    items.sort(key=lambda item: item.get("created", ""), reverse=True)
    return items


def count_pending(vault_dir: Path | str) -> int:
    return len(list_pending(vault_dir))


def _find(vault: Path, pending_id: str, *, include_archived: bool = False) -> Path:
    wanted = str(pending_id or "").strip()
    relatives = [vault_layout.SELF_MENTOR_PENDING_PATCHES]
    if include_archived:
        relatives += [vault_layout.SELF_MENTOR_PENDING_ACCEPTED,
                      vault_layout.SELF_MENTOR_PENDING_REJECTED]
    for relative in relatives:
        directory = _card_dir(vault, relative)
        for path in directory.glob("*.md") if directory.is_dir() else []:
            item = _read_card(vault, path)
            if item is not None and item["pending_id"] == wanted:
                return path
    raise PendingError("待确认卡片不存在或已处理", 404)


def _move(vault: Path, path: Path, target_dir: str) -> str:
    target = _card_dir(vault, target_dir)
    target.mkdir(parents=True, exist_ok=True)
    destination = target / path.name
    index = 2
    while destination.exists():
        destination = target / f"{path.stem}-{index}.md"
        index += 1
    os.replace(path, destination)
    return destination.resolve().relative_to(vault.resolve()).as_posix()


def _relative(vault: Path, path: Path) -> str:
    return path.resolve().relative_to(vault.resolve()).as_posix()


def accept_pending(vault_dir: Path | str, pending_id: str, *,
                   edited_text: str = "", confirm: bool = False,
                   read_only: bool = False) -> dict[str, Any]:
    if confirm is not True:
        raise PendingError("请明确确认采纳这条档案修改", 422)
    _ensure_write(read_only, "采纳人生导师的档案修改")
    vault = Path(vault_dir).resolve()
    wanted = str(pending_id or "").strip()
    # The same card can be submitted twice (double click / retry). Serialize on
    # the card id and make the archive write idempotent through its operation id.
    with operation_lock(f"pending|{vault}|{wanted}"):
        path = _find(vault, wanted, include_archived=True)
        item = _read_card(vault, path)
        if item is None:
            raise PendingError("待确认卡片损坏", 409)
        if item["status"] != "pending":
            if item["status"] == "accepted" and item.get("result_entry_id"):
                entry = self_archive.find_entry(
                    vault, item["result_entry_id"], include_history=True)
                if entry is None:
                    raise PendingError(
                        "这张卡片已采纳，但对应档案条目不存在；请检查档案", 409)
                return {"accepted": True, "pending_id": item["pending_id"],
                        "entry": entry, "archived_as": _relative(vault, path),
                        "duplicate": True}
            raise PendingError("这张卡片已经处理过", 409)
        text = str(edited_text or "").strip() or item["new_text"]
        if len(text) > MAX_NEW_TEXT:
            raise PendingError("补丁正文过长", 422)
        operation_id = f"pending:{item['pending_id']}"
        if item["action"] == "supersede":
            entry = self_archive.supersede_entry(
                vault, item["entry_id"],
                entry_type=item["entry_type"], confidence=item["confidence"],
                title=item["title"], text=text, evidence=item["evidence"],
                counterexample=item["counterexample"], verify=item["verify"],
                sources=item["sources"], origin="mentor",
                base_revision=item["base_revision"], read_only=read_only,
                operation_id=operation_id,
            )
        else:
            entry = self_archive.create_entry(
                vault, book_key=item["section"], entry_type=item["entry_type"],
                confidence=item["confidence"], title=item["title"], text=text,
                evidence=item["evidence"], counterexample=item["counterexample"],
                verify=item["verify"], sources=item["sources"], origin="mentor",
                read_only=read_only, operation_id=operation_id,
            )
        try:
            existing_meta, _ = parse_text(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, yaml.YAMLError):
            existing_meta = {}
        metadata = dict(existing_meta)
        metadata["status"] = "accepted"
        metadata["result_entry_id"] = entry["entry_id"]
        metadata["decided_at"] = _now()
        if edited_text.strip():
            metadata["new_text"] = text
        _atomic_write(path, _payload_text(metadata))
        archived = _move(vault, path, vault_layout.SELF_MENTOR_PENDING_ACCEPTED)
        return {"accepted": True, "pending_id": item["pending_id"],
                "entry": entry, "archived_as": archived}


def reject_pending(vault_dir: Path | str, pending_id: str, *,
                   reason: str = "", read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "驳回人生导师的档案修改")
    vault = Path(vault_dir).resolve()
    wanted = str(pending_id or "").strip()
    with operation_lock(f"pending|{vault}|{wanted}"):
        path = _find(vault, wanted, include_archived=True)
        item = _read_card(vault, path)
        if item is None:
            raise PendingError("待确认卡片损坏", 409)
        if item["status"] != "pending":
            if item["status"] == "rejected":
                return {"rejected": True, "pending_id": item["pending_id"],
                        "archived_as": _relative(vault, path), "duplicate": True}
            raise PendingError("这张卡片已经处理过", 409)
        try:
            existing_meta, _ = parse_text(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, yaml.YAMLError):
            existing_meta = {}
        metadata = dict(existing_meta)
        metadata["status"] = "rejected"
        metadata["reason"] = _clean(reason, 2_000) or metadata.get("reason", "")
        metadata["decided_at"] = _now()
        _atomic_write(path, _payload_text(metadata))
        archived = _move(vault, path, vault_layout.SELF_MENTOR_PENDING_REJECTED)
        return {"rejected": True, "pending_id": item["pending_id"],
                "archived_as": archived}


__all__ = [
    "PendingError", "create_pending", "list_pending", "count_pending",
    "accept_pending", "reject_pending",
]
