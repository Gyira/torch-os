"""忆灵（Memory Hub）：事件 → 综合 → 画像 → 复盘卡 → 身份补丁。

Data layout
-----------
- `.torch-os/忆灵/画像.json`        canonical portrait (system state)
- `.torch-os/忆灵/画像历史/`        previous portrait revisions
- `.torch-os/忆灵/复盘卡/`          pending review cards (Markdown queue)
- `.torch-os/忆灵/复盘卡/已采纳|已忽略/`
- `.torch-os/忆灵/事件/`            main-dsh/script dropped events (P3; optional)
- `.torch-os/忆灵/state.json`       local synthesis state
- `01_自我档案/08_忆灵观察/`        append-only portrait/observation snapshots (user-readable)

Identity has exactly two write paths: a reviewed memory-hub patch (no dialogue token
needed) or a Socratic patch that carries the AI's verdict token.  Both end in
``apply_identity_patch`` and are revision guarded; this module never silently
overwrites identity.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import identity, identity_inbox, journal_note, pillar_notes, questions, task_reports, vault_layout, workbench
from .ai_schemas import extract_json
from .db import Database, utc_now
from .journal_shared import shanghai_today
from .proposals import ProposalError
from .vault import parse_text, safe_note_path
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SHANGHAI = ZoneInfo("Asia/Shanghai")
HUB_REL = ".torch-os/忆灵"
PORTRAIT_REL = f"{HUB_REL}/画像.json"
HISTORY_REL = f"{HUB_REL}/画像历史"
CARDS_REL = f"{HUB_REL}/复盘卡"
CARDS_KEPT = f"{CARDS_REL}/已采纳"
CARDS_DISMISSED = f"{CARDS_REL}/已忽略"
EVENTS_REL = f"{HUB_REL}/事件"
EVENTS_DONE = f"{EVENTS_REL}/已读"
STATE_REL = f"{HUB_REL}/state.json"
OBSERVATION_CHAPTER = "yiling"
IDENTITY_BACKUP_REL = ".torch-os/身份备份"
MAX_CONTEXT = 16_000
MAX_CARDS = 8
MAX_PORTRAIT_CHARS = 12_000
MAX_CARD_CHARS = 4_000
RUN_RANGES = (7, 14, 30)
CARD_KINDS = {"observation", "question", "correction", "suggestion"}


class MemoryHubError(RuntimeError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _path(vault_dir: Path | str, relative: str) -> Path:
    root = Path(vault_dir)
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise MemoryHubError("忆灵路径越界", 422)
    return path


def _now() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                         delete=False, dir=str(path.parent),
                                         prefix=".hub-", suffix=".tmp")
    try:
        handle.write(text)
        handle.close()
        os.replace(handle.name, path)
    except OSError:
        Path(handle.name).unlink(missing_ok=True)
        raise


def _trim(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def ensure_hub(vault_dir: Path | str) -> None:
    for relative in (HUB_REL, HISTORY_REL, CARDS_REL, CARDS_KEPT, CARDS_DISMISSED,
                     EVENTS_REL, EVENTS_DONE):
        _path(vault_dir, relative).mkdir(parents=True, exist_ok=True)


def _ensure_write(read_only: bool, action: str) -> None:
    try:
        ensure_vault_writable(read_only, action)
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error


# ── portrait ─────────────────────────────────────────────────────────────

def read_portrait(vault_dir: Path | str) -> dict[str, Any]:
    path = _path(vault_dir, PORTRAIT_REL)
    default = {"revision": "", "portrait": "", "sections": {}, "sources": [],
               "confidence": 0, "updated_at": "", "path": PORTRAIT_REL}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    if not isinstance(data, dict):
        return default
    return {
        **default,
        "revision": str(data.get("revision") or ""),
        "portrait": str(data.get("portrait") or ""),
        "sections": data.get("sections") if isinstance(data.get("sections"), dict) else {},
        "sources": [str(item) for item in (data.get("sources") or [])][:40],
        "confidence": data.get("confidence", 0),
        "updated_at": str(data.get("updated_at") or ""),
    }


def write_portrait(vault_dir: Path | str, *, portrait: str, sections: dict[str, Any] | None = None,
                   sources: list[str] | None = None, confidence: Any = 0) -> dict[str, Any]:
    ensure_hub(vault_dir)
    old = read_portrait(vault_dir)
    path = _path(vault_dir, PORTRAIT_REL)
    if old["revision"] and path.is_file():
        backup = _path(vault_dir, f"{HISTORY_REL}/{old['revision']}.json")
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(path.read_bytes())
    payload = {
        "version": 1,
        "portrait": _trim(portrait, MAX_PORTRAIT_CHARS),
        "sections": sections if isinstance(sections, dict) else {},
        "sources": [str(item) for item in (sources or [])][:40],
        "confidence": confidence,
        "updated_at": _now(),
    }
    payload["revision"] = _hash(payload)[:16]
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return {**payload, "path": PORTRAIT_REL}


def portrait_history(vault_dir: Path | str) -> list[dict[str, Any]]:
    try:
        notes = pillar_notes.list_notes(vault_dir, chapter=OBSERVATION_CHAPTER, limit=100)
    except Exception:
        return []
    return [
        {"title": note.get("title", ""), "path": note.get("path", ""),
         "type": note.get("type", ""), "date": note.get("date", "")}
        for note in notes
        if note.get("type") in {"portrait_snapshot", "observation"}
    ]


def _identity_text(vault_dir: Path | str) -> str:
    data = identity.read_identity(vault_dir)
    labels = {"name": "称呼", "motto": "座右铭", "worldview": "世界观",
              "lifeview": "人生观", "values": "价值观", "notes": "补充"}
    lines = [f"{labels[key]}：{data.get(key)}" for key in identity.TEXT_FIELDS
             if str(data.get(key) or "").strip()]
    return "\n".join(lines) or "身份笔记还是空的。"


# ── cards ────────────────────────────────────────────────────────────────

def _card_filename(card_id: str, kind: str) -> str:
    return f"{_stamp()}-{kind}-{card_id[-8:]}.md"


def _write_card(vault_dir: Path | str, *, kind: str, title: str, text: str,
                confidence: Any = "medium", sources: list[str] | None = None,
                proposal_id: str = "") -> dict[str, Any]:
    ensure_hub(vault_dir)
    card_id = "card_" + uuid.uuid4().hex[:24]
    metadata = {
        "id": card_id,
        "kind": kind if kind in CARD_KINDS else "observation",
        "title": _trim(title or "忆灵观察", 120),
        "text": _trim(text, MAX_CARD_CHARS),
        "confidence": confidence,
        "sources": [str(item) for item in (sources or [])][:20],
        "created": _now(),
        "status": "pending",
    }
    if proposal_id:
        metadata["proposal_id"] = proposal_id
    body = ("---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip("\n")
            + "\n---\n\n" + metadata["text"] + "\n")
    path = _path(vault_dir, f"{CARDS_REL}/{_card_filename(card_id, metadata['kind'])}")
    _atomic_write(path, body)
    return _read_card(path)


def _read_card(path: Path) -> dict[str, Any]:
    metadata, body = parse_text(path.read_text(encoding="utf-8", errors="replace"))
    return {
        "id": str(metadata.get("id") or path.stem),
        "kind": str(metadata.get("kind") or "observation"),
        "title": str(metadata.get("title") or path.stem),
        "text": str(metadata.get("text") or body).strip(),
        "confidence": metadata.get("confidence", "medium"),
        "sources": [str(item) for item in (metadata.get("sources") or [])][:20],
        "created": str(metadata.get("created") or ""),
        "proposal_id": str(metadata.get("proposal_id") or ""),
        "path": path.as_posix(),
    }


def list_cards(vault_dir: Path | str) -> list[dict[str, Any]]:
    directory = _path(vault_dir, CARDS_REL)
    if not directory.is_dir():
        return []
    cards: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        try:
            cards.append(_read_card(path))
        except OSError:
            continue
    cards.sort(key=lambda item: item.get("created", ""), reverse=True)
    return cards


def _find_card(vault_dir: Path | str, card_id: str) -> Path:
    directory = _path(vault_dir, CARDS_REL)
    for path in directory.glob("*.md") if directory.is_dir() else []:
        try:
            if _read_card(path)["id"] == card_id:
                return path
        except OSError:
            continue
    raise MemoryHubError("复盘卡不存在或已处理", 404)


def _move_card(vault_dir: Path | str, path: Path, target_rel: str) -> str:
    target_dir = _path(vault_dir, target_rel)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    index = 2
    while target.exists():
        target = target_dir / f"{path.stem}-{index}{path.suffix}"
        index += 1
    shutil.move(str(path), str(target))
    return target.as_posix()


def keep_card(database: Database, vault_dir: Path | str, card_id: str, *,
              target: str = "observation", title: str = "", body: str = "",
              read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "采纳忆灵复盘卡")
    path = _find_card(vault_dir, card_id)
    card = _read_card(path)
    chapter = "memory" if target == "memory" else OBSERVATION_CHAPTER
    note_type = "memory" if target == "memory" else "observation"
    note_body = (body or card["text"]).strip()
    if card.get("sources"):
        note_body += "\n\n## 来源\n\n" + "\n".join(f"- {item}" for item in card["sources"])
    note = pillar_notes.create_note(
        vault_dir, pillar="self", chapter=chapter,
        title=title or card["title"], body=note_body, note_type=note_type,
        tags=["忆灵"], source="忆灵",
        extra_metadata={"generated_by": "yiling", "memory": False if target != "memory" else True},
    )
    archived = _move_card(vault_dir, path, CARDS_KEPT)
    database.audit("memory_hub_card_kept", {
        "card_id": card_id, "target": target, "path": note.get("path", ""),
    })
    return {"card": card, "note": note, "archived_as": archived}


def dismiss_card(database: Database, vault_dir: Path | str, card_id: str, *,
                 reason: str = "") -> dict[str, Any]:
    path = _find_card(vault_dir, card_id)
    card = _read_card(path)
    archived = _move_card(vault_dir, path, CARDS_DISMISSED)
    database.audit("memory_hub_card_dismissed", {"card_id": card_id})
    return {"card": card, "archived_as": archived, "reason": _trim(reason, 500)}


# ── events ───────────────────────────────────────────────────────────────

def ingest_event(database: Database, *, kind: str, summary: str,
                 source_ref: str = "", source_kind: str = "",
                 detail: dict[str, Any] | None = None) -> dict[str, Any]:
    text = _trim(summary, 1000)
    if not text:
        raise MemoryHubError("事件摘要不能为空", 422)
    key = hashlib.sha256(
        f"{kind}|{text}|{source_ref}|{shanghai_today().isoformat()}".encode("utf-8")
    ).hexdigest()[:40]
    with database.connect() as connection:
        connection.execute(
            """INSERT OR IGNORE INTO memory_hub_events
               (kind, summary, detail_json, source_ref, source_kind,
                occurred_at, created_at, status, dedupe_key)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (str(kind or "observation")[:40], text,
             json.dumps(detail or {}, ensure_ascii=False),
             str(source_ref or "")[:500], str(source_kind or "")[:60],
             _now(), utc_now(), "new", key),
        )
    return {"ok": True, "summary": text}


def ingest_event_files(database: Database, vault_dir: Path | str) -> int:
    """Consume `.torch-os/忆灵/事件/*.md` dropped by the main dsh/scripts.

    This is the write path for agents that do not have the torch HTTP session:
    a small Markdown card in, an event row in the DB, then the file moves to
    ``已读/``.  Cards are data, never instructions.
    """
    directory = _path(vault_dir, EVENTS_REL)
    if not directory.is_dir():
        return 0
    consumed = 0
    for path in sorted(directory.glob("*.md")):
        try:
            metadata, body = parse_text(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        summary = str(metadata.get("summary") or metadata.get("text") or body or "").strip()
        if not summary:
            continue
        ingest_event(
            database,
            kind=str(metadata.get("kind") or "observation"),
            summary=summary,
            source_ref=str(metadata.get("source_ref") or metadata.get("source") or ""),
            source_kind=str(metadata.get("source_kind") or "dsh"),
            detail={"file": path.name},
        )
        target_dir = _path(vault_dir, EVENTS_DONE)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        index = 2
        while target.exists():
            target = target_dir / f"{path.stem}-{index}{path.suffix}"
            index += 1
        shutil.move(str(path), str(target))
        consumed += 1
    return consumed


def list_events(database: Database, *, status: str = "new", limit: int = 50) -> list[dict[str, Any]]:
    query = "SELECT * FROM memory_hub_events"
    params: list[Any] = []
    if status:
        query += " WHERE status=?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    with database.connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


# ── context / synthesis ─────────────────────────────────────────────────

def collect_context(database: Database, vault_dir: Path | str, *,
                    range_days: int = 14, focus: str = "") -> tuple[str, dict[str, int]]:
    try:
        ingest_event_files(database, vault_dir)
    except OSError:
        pass
    days = max(1, min(int(range_days), 90))
    sections: list[str] = []
    counts: dict[str, int] = {}

    sections.append("## 身份\n" + _identity_text(vault_dir))

    portrait = read_portrait(vault_dir)
    sections.append("## 当前画像（上一版）\n" + (_trim(portrait["portrait"], 3_000)
                                                or "还没有画像。"))
    counts["portrait_chars"] = len(portrait["portrait"])

    journal_lines: list[str] = []
    for offset in range(days):
        day = (datetime.now(SHANGHAI).date() - timedelta(days=offset)).isoformat()
        note = journal_note.read_note(vault_dir, day)
        if note.get("exists") and str(note.get("content") or "").strip():
            journal_lines.append(f"- {day}：{_trim(note['content'], 900)}")
    counts["journal_days"] = len(journal_lines)
    sections.append("## 最近日记\n" + ("\n".join(journal_lines) or "最近没有日记。"))

    try:
        tasks = task_reports.open_tasks(vault_dir)
        tasks.sort(key=lambda note: (note.get("date") or "9999-99-99", note.get("title") or ""))
        task_lines = [f"- {note.get('title')}（{note.get('date') or '未排期'}）"
                      for note in tasks[:25]]
    except (OSError, ValueError):
        task_lines = []
    counts["open_tasks"] = len(task_lines)
    sections.append("## 未完成任务\n" + ("\n".join(task_lines) or "没有未完成任务。"))

    try:
        active = database.list_memories("active")[:30]
        memory_lines = [f"- {_trim(item.get('title') or '', 60)}：{_trim(item.get('content') or '', 180)}"
                        for item in active]
    except Exception:
        memory_lines = []
    counts["active_memories"] = len(memory_lines)
    sections.append("## 已确认记忆\n" + ("\n".join(memory_lines) or "还没有已确认记忆。"))

    new_events = list_events(database, status="new", limit=40)
    event_lines = [f"- [{item['kind']}] {item['summary']}" for item in new_events]
    counts["new_events"] = len(event_lines)
    sections.append("## 角色投递的新事件\n" + ("\n".join(event_lines) or "没有新事件。"))

    try:
        drafts = identity_inbox.list_drafts(vault_dir)[:10]
        draft_lines = [
            f"- [{item.get('field_label') or item.get('kind')}] {item.get('text')}"
            + (f"（依据：{item.get('evidence')}）" if item.get("evidence") else "")
            for item in drafts
        ]
    except Exception:
        draft_lines = []
    counts["identity_drafts"] = len(draft_lines)
    sections.append("## dsh 投来的身份候选（未采纳）\n"
                    + ("\n".join(draft_lines) or "没有身份候选。"))

    try:
        queued = questions.load_queue(vault_dir)
        question_lines = [f"- {item.get('prompt')}（{item.get('why') or '好奇缺口'}）"
                          for item in queued[:10]]
    except Exception:
        question_lines = []
    counts["queued_questions"] = len(question_lines)
    sections.append("## 还没问出口的问题\n" + ("\n".join(question_lines) or "没有排队的提问。"))

    if focus:
        sections.append("## 这次想聚焦\n" + _trim(focus, 1_000))
    text = _trim("\n\n".join(sections), MAX_CONTEXT)
    return text, counts


def _record_run(database: Database, *, run_id: str, trigger: str, range_days: int,
                status: str, event_count: int = 0, card_count: int = 0,
                snapshot_path: str = "", error: str = "") -> None:
    with database.connect() as connection:
        connection.execute(
            """INSERT OR REPLACE INTO memory_hub_runs
               (id, trigger, range_days, status, event_count, card_count,
                snapshot_path, error, started_at, finished_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (run_id, trigger, range_days, status, event_count, card_count,
             snapshot_path, error, _now(), None if status == "running" else _now()),
        )


def _finish_run(database: Database, run_id: str, *, status: str, event_count: int,
                card_count: int, snapshot_path: str = "", error: str = "") -> None:
    with database.connect() as connection:
        connection.execute(
            """UPDATE memory_hub_runs
               SET status=?, event_count=?, card_count=?, snapshot_path=?, error=?, finished_at=?
               WHERE id=?""",
            (status, event_count, card_count, snapshot_path, error, _now(), run_id),
        )


def _snapshot_note(vault_dir: Path | str, *, title: str, body: str, note_type: str,
                   run_id: str, confidence: Any, sources: list[str]) -> dict[str, Any]:
    return pillar_notes.create_note(
        vault_dir, pillar="self", chapter=OBSERVATION_CHAPTER,
        title=title, body=body, note_type=note_type,
        tags=["忆灵", note_type], source="忆灵",
        extra_metadata={
            "memory": False,
            "generated_by": "yiling",
            "run_id": run_id,
            "confidence": confidence,
            "sources": sources[:20],
        },
    )


def _normalize_review_cards(value: Any) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for item in list(value or [])[:MAX_CARDS]:
        if isinstance(item, str):
            data = {"kind": "observation", "title": _trim(item, 60), "text": item}
        elif isinstance(item, dict):
            data = item
        else:
            continue
        text = _trim(data.get("text") or data.get("body") or "", MAX_CARD_CHARS)
        if not text:
            continue
        kind = str(data.get("kind") or "observation")
        cards.append({
            "kind": kind if kind in CARD_KINDS else "observation",
            "title": _trim(data.get("title") or text[:40], 120),
            "text": text,
            "confidence": data.get("confidence", "medium"),
            "sources": [str(item) for item in (data.get("sources") or [])][:20],
        })
    return cards


def _normalize_patches(value: Any) -> list[dict[str, Any]]:
    patches: list[dict[str, Any]] = []
    for item in list(value or [])[:10]:
        if not isinstance(item, dict):
            continue
        changes = item.get("changes")
        if not isinstance(changes, list):
            changes = [{
                "field": item.get("field"),
                "from": item.get("from", ""),
                "to": item.get("to", ""),
                "reason": item.get("reason", ""),
                "sources": item.get("sources", []),
            }]
        clean = []
        for change in changes:
            if not isinstance(change, dict):
                continue
            field = str(change.get("field") or "").strip()
            to = str(change.get("to") or "").strip()
            if field in identity.TEXT_FIELDS and to:
                clean.append({
                    "field": field, "from": str(change.get("from") or ""),
                    "to": _trim(to, identity.MAX_FIELD),
                    "reason": _trim(change.get("reason") or item.get("reason") or "", 2_000),
                    "sources": [str(source) for source in (change.get("sources") or item.get("sources") or [])][:20],
                })
        if clean:
            patches.append({
                "changes": clean,
                "confidence": item.get("confidence", 0),
                "reason": _trim(item.get("reason") or "", 2_000),
            })
    return patches


def synthesize(database: Database, vault_dir: Path | str, role_service: Any, proposals: Any, *,
               range_days: int = 14, focus: str = "", read_only: bool = False,
               trigger: str = "manual") -> dict[str, Any]:
    _ensure_write(read_only, "让忆灵整理画像")
    if role_service is None:
        raise MemoryHubError("AI 不可用：没有配置 dsh 或 DeepSeek Key", 409)
    if proposals is None:
        raise MemoryHubError("提案服务未配置", 409)
    days = 7 if int(range_days) not in RUN_RANGES else int(range_days)
    run_id = "run_" + uuid.uuid4().hex[:24]
    _record_run(database, run_id=run_id, trigger=trigger, range_days=days, status="running")
    try:
        context, counts = collect_context(database, vault_dir, range_days=days, focus=focus)
        user_text = (
            f"请基于以上只读资料，按忆灵 synthesize 模式整理最近 {days} 天的画像与复盘卡。"
            "只输出约定的 JSON；每条观察都要引用资料或明确写“资料不足”。"
        )
        result = role_service.run("yiling", "synthesize", context_text=context,
                                  user_text=user_text)
        structured = extract_json(result.text)
        if not isinstance(structured, dict):
            raise MemoryHubError("忆灵没有返回结构化结果，本次不更新画像", 502)
        portrait_text = structured.get("portrait")
        if isinstance(portrait_text, dict):
            sections = portrait_text
            portrait_text = "\n".join(f"## {key}\n{value}" for key, value in portrait_text.items())
        elif isinstance(portrait_text, str):
            sections = {}
        else:
            sections = {}
            portrait_text = ""
        portrait_text = _trim(portrait_text, MAX_PORTRAIT_CHARS)
        if not portrait_text:
            raise MemoryHubError("忆灵没有给出画像正文，本次不更新画像", 502)
        sources = [str(item) for item in (structured.get("sources") or [])][:40]
        previous = read_portrait(vault_dir)
        saved = write_portrait(vault_dir, portrait=portrait_text, sections=sections,
                               sources=sources,
                               confidence=structured.get("confidence", 0))
        portrait_note = _snapshot_note(
            vault_dir, title=f"画像-{_stamp()}", body=portrait_text,
            note_type="portrait_snapshot", run_id=run_id,
            confidence=structured.get("confidence", 0), sources=sources)
        observations = structured.get("observations") or structured.get("review_cards") or []
        observation_text = "\n\n".join(
            f"## {item.get('title') if isinstance(item, dict) else '观察'}\n"
            f"{item.get('text') if isinstance(item, dict) else item}"
            for item in observations
        ) if observations else portrait_text
        observation_note = _snapshot_note(
            vault_dir, title=f"观察-{_stamp()}", body=observation_text,
            note_type="observation", run_id=run_id,
            confidence=structured.get("confidence", 0), sources=sources)
        cards = []
        for card in _normalize_review_cards(structured.get("review_cards")):
            try:
                cards.append(_write_card(
                    vault_dir, kind=card["kind"], title=card["title"],
                    text=card["text"], confidence=card["confidence"],
                    sources=card["sources"]))
            except OSError:
                continue
        patches = []
        current_revision = str(identity.read_identity(vault_dir).get("revision") or "")
        for patch in _normalize_patches(structured.get("identity_patches")):
            title = f"忆灵建议：{', '.join(change['field'] for change in patch['changes'])}"
            try:
                created = proposals.create(
                    kind="identity_patch", title=title,
                    summary=patch["reason"], reason=patch["reason"],
                    payload={"changes": patch["changes"]},
                    sources=[source for change in patch["changes"] for source in change["sources"]],
                    role_id="yiling", base_revision=current_revision,
                    verdict={"decision": "accept", "requires_dialogue": False},
                )
            except ProposalError:
                continue
            patches.append(created)
        new_events = list_events(database, status="new", limit=200)
        if new_events:
            with database.connect() as connection:
                connection.execute(
                    "UPDATE memory_hub_events SET status='absorbed' "
                    "WHERE status='new' AND id IN (%s)" % ",".join("?" for _ in new_events),
                    [item["id"] for item in new_events],
                )
        _finish_run(database, run_id, status="ok", event_count=len(new_events),
                    card_count=len(cards), snapshot_path=portrait_note.get("path", ""))
        database.audit("memory_hub_synthesized", {
            "run_id": run_id, "range_days": days, "events": len(new_events),
            "cards": len(cards), "patches": len(patches),
        })
        return {
            "run_id": run_id, "status": "ok", "range_days": days,
            "portrait": {**saved, "changed": previous.get("portrait", "") != portrait_text},
            "cards": cards, "identity_patches": patches,
            "report": {"portrait": portrait_note.get("path", ""),
                       "observation": observation_note.get("path", "")},
            "events": len(new_events), "seconds": result.seconds,
        }
    except MemoryHubError:
        _finish_run(database, run_id, status="error", event_count=0, card_count=0,
                    error="synthesis failed")
        raise
    except Exception as error:
        _finish_run(database, run_id, status="error", event_count=0, card_count=0,
                    error=str(error)[:500])
        raise MemoryHubError(f"忆灵整理失败：{error}", 503) from error


# ── identity patch apply ────────────────────────────────────────────────

def apply_identity_patch(database: Database, vault_dir: Path | str,
                         proposal: dict[str, Any], *,
                         read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "采纳身份补丁")
    current = identity.read_identity(vault_dir)
    base = str(proposal.get("base_revision") or "")
    if base and str(current.get("revision") or "") != base:
        raise ProposalError("身份卡已在别处修改，补丁已过期；请让忆灵重新整理", 409)
    payload = proposal.get("payload") or {}
    changes = payload.get("changes") if isinstance(payload, dict) else None
    if not isinstance(changes, list) or not changes:
        raise ProposalError("身份补丁没有 changes", 422)
    values = {field: str(current.get(field) or "") for field in identity.TEXT_FIELDS}
    changed_fields: list[str] = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        field = str(change.get("field") or "").strip()
        to = str(change.get("to") or "").strip()
        before = str(change.get("from") or "").strip()
        if field not in identity.TEXT_FIELDS:
            raise ProposalError(f"不支持的身份证字段：{field}", 422)
        if not to:
            raise ProposalError(f"{field} 的新值不能为空", 422)
        if before and values[field].strip() != before:
            raise ProposalError(f"{field} 已经在别处变了，请重新整理", 409)
        if len(to) > identity.MAX_FIELD:
            raise ProposalError(f"{field} 超长", 422)
        values[field] = to
        changed_fields.append(field)
    if not changed_fields:
        raise ProposalError("身份补丁没有可应用的字段", 422)
    backup_dir = _path(vault_dir, IDENTITY_BACKUP_REL)
    source = Path(vault_dir) / vault_layout.identity_paths()[0]
    if source.is_file():
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"身份-{_stamp()}.md"
        index = 2
        while target.exists():
            target = backup_dir / f"身份-{_stamp()}-{index}.md"
            index += 1
        shutil.copy2(source, target)
    written = identity.write_identity(
        vault_dir, identity.IdentityWrite(**values, revision=str(current.get("revision") or "")),
        read_only=read_only)
    database.audit("identity_patch_applied", {
        "fields": changed_fields, "revision": written.get("revision", ""),
        "proposal_id": proposal.get("id", ""),
    })
    return {"fields": changed_fields, "path": written.get("path", ""),
            "revision": written.get("revision", "")}


# ── overview / API ──────────────────────────────────────────────────────

def overview(database: Database, vault_dir: Path | str, proposals: Any, *,
             limit: int = 5) -> dict[str, Any]:
    try:
        ingest_event_files(database, vault_dir)
    except OSError:
        pass
    portrait = read_portrait(vault_dir)
    cards = list_cards(vault_dir)[:limit]
    identity_patches: list[dict[str, Any]] = []
    if proposals is not None:
        try:
            identity_patches = [item for item in proposals.list(status="pending")
                                if item.get("kind") == "identity_patch"][:limit]
        except Exception:
            identity_patches = []
    try:
        observations = pillar_notes.list_notes(vault_dir, chapter=OBSERVATION_CHAPTER, limit=20)
    except Exception:
        observations = []
    try:
        candidates = database.list_memories("candidate")[:limit]
    except Exception:
        candidates = []
    try:
        identity_drafts = identity_inbox.list_drafts(vault_dir)[:limit]
    except Exception:
        identity_drafts = []
    try:
        with database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_hub_runs ORDER BY started_at DESC LIMIT 1").fetchone()
            pending_events = connection.execute(
                "SELECT COUNT(*) FROM memory_hub_events WHERE status='new'").fetchone()[0]
    except Exception:
        row = None
        pending_events = 0
    return {
        "portrait": portrait,
        "identity": {key: identity.read_identity(vault_dir).get(key) for key in identity.TEXT_FIELDS},
        "cards": cards,
        "identity_patches": identity_patches,
        "observations": [
            {"title": note.get("title", ""), "path": note.get("path", ""),
             "type": note.get("type", ""), "date": note.get("date", "")}
            for note in observations
            if note.get("type") in {"portrait_snapshot", "observation"}
        ],
        "memory_candidates": candidates,
        "identity_drafts": identity_drafts,
        "stats": {"pending_events": pending_events,
                  "last_run": dict(row) if row is not None else None},
    }


class SynthesizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    range_days: int = Field(default=14)
    focus: str = Field(default="", max_length=2_000)
    confirm: bool = Field(strict=True)


class EventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: str = Field(default="observation", max_length=40)
    summary: str = Field(min_length=1, max_length=1_000)
    source_ref: str = Field(default="", max_length=500)
    source_kind: str = Field(default="manual", max_length=60)
    confirm: bool = Field(strict=True)


class KeepCardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    confirm: bool = Field(strict=True)
    target: str = Field(default="observation", max_length=20)
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=8_000)


class DismissCardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(default="", max_length=500)


def create_memory_hub_router(database: Database, vault_dir: Path | str, *,
                             role_service: Any = None, proposals: Any = None,
                             read_only: bool = False, prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["memory-hub"])

    def _guard(action):
        try:
            return action()
        except MemoryHubError as error:
            raise HTTPException(error.status_code, error.message) from error
        except HTTPException:
            raise

    @router.get("/memory-hub/overview")
    def hub_overview():
        return _guard(lambda: overview(database, vault_dir, proposals))

    @router.get("/memory-hub/portrait")
    def hub_portrait():
        return read_portrait(vault_dir)

    @router.get("/memory-hub/portrait/history")
    def hub_history():
        return {"items": portrait_history(vault_dir)}

    @router.get("/memory-hub/cards")
    def hub_cards():
        return {"items": list_cards(vault_dir)}

    @router.get("/memory-hub/events")
    def hub_events(status: str = "new", limit: int = 50):
        return {"items": list_events(database, status=status, limit=limit)}

    @router.post("/memory-hub/events")
    def add_event(payload: EventRequest):
        if payload.confirm is not True:
            raise HTTPException(422, "请明确确认这条事件")
        return _guard(lambda: ingest_event(
            database, kind=payload.kind, summary=payload.summary,
            source_ref=payload.source_ref, source_kind=payload.source_kind))

    @router.post("/memory-hub/synthesize")
    def hub_synthesize(payload: SynthesizeRequest):
        if payload.confirm is not True:
            raise HTTPException(422, "请明确确认让忆灵整理画像")
        return _guard(lambda: synthesize(
            database, vault_dir, role_service, proposals,
            range_days=payload.range_days, focus=payload.focus, read_only=read_only))

    @router.post("/memory-hub/cards/{card_id}/keep")
    def hub_keep(card_id: str, payload: KeepCardRequest):
        if payload.confirm is not True:
            raise HTTPException(422, "请明确确认留下这张复盘卡")
        return _guard(lambda: keep_card(
            database, vault_dir, card_id, target=payload.target,
            title=payload.title, body=payload.body, read_only=read_only))

    @router.post("/memory-hub/cards/{card_id}/dismiss")
    def hub_dismiss(card_id: str, payload: DismissCardRequest):
        return _guard(lambda: dismiss_card(database, vault_dir, card_id,
                                           reason=payload.reason))

    return router


__all__ = [
    "MemoryHubError", "HUB_REL", "OBSERVATION_CHAPTER", "read_portrait", "write_portrait",
    "portrait_history", "list_cards", "keep_card", "dismiss_card", "ingest_event",
    "list_events", "collect_context", "synthesize", "apply_identity_patch", "overview",
    "create_memory_hub_router",
]
