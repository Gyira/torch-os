"""四库只读接口与快速捕获（2026-10-21）。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import journal_note, pillar_notes, reviews, steward_policy, vault_layout
from .db import Database
from .habits import HabitService, shanghai_today
from .identity import read_identity
from .vault_connection import ensure_vault_writable


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=200_000)
    pillar: str = Field(default="knowledge", max_length=32)
    chapter: str = Field(default="inbox", max_length=32)
    note_type: str = Field(default="", max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=24)
    source: str = Field(default="", max_length=500)
    date: str = Field(default="", max_length=10)


class NoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    body: str | None = Field(default=None, max_length=200_000)
    title: str = Field(default="", max_length=200)
    tags: list[str] | None = Field(default=None, max_length=24)
    status: str = Field(default="", max_length=32)


class NoteMove(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1, max_length=500)
    chapter: str = Field(min_length=1, max_length=32)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    day: str = Field(default="", max_length=10)
    month: str = Field(default="", max_length=7)


class SuggestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=50_000)
    source: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=24)


def _pillar_or_404(pillar: str) -> str:
    key = str(pillar or "").strip()
    if key not in vault_layout.PILLAR_DIRS:
        raise HTTPException(404, "未知库")
    return key


def _overview(vault_dir: Path | str, database: Database, pillar: str) -> dict[str, Any]:
    key = _pillar_or_404(pillar)
    notes = pillar_notes.list_notes(vault_dir, pillar=key, limit=500)
    chapters: list[dict[str, Any]] = []
    for chapter in vault_layout.chapter_manifest()["pillars"]:
        if chapter["key"] != key:
            continue
        for item in chapter["chapters"]:
            mine = [note for note in notes if note["chapter"] == item["key"]]
            chapters.append({
                **item,
                "count": len(mine),
                "latest": mine[0] if mine else None,
            })
    payload: dict[str, Any] = {
        "pillar": key,
        "title": vault_layout.PILLAR_TITLES[key],
        "dir": vault_layout.PILLAR_DIRS[key],
        "vault_name": Path(vault_dir).name,
        "chapters": chapters,
        "recent": notes[:8],
        "count": len(notes),
    }
    if key == "life":
        today = shanghai_today().isoformat()
        journal = journal_note.read_note(vault_dir, today)
        habits = HabitService(database, vault_dir).list(status="active")
        payload["today"] = {
            "date": today,
            "journal_written": bool(journal.get("exists")),
            "journal_updated_at": journal.get("updated_at"),
            "habit_count": len(habits),
        }
    if key == "self":
        identity = read_identity(vault_dir)
        payload["identity"] = {
            "exists": bool(identity.get("exists")),
            "path": identity.get("path"),
            "name": identity.get("name") or "",
            "motto": identity.get("motto") or "",
            "worldview": identity.get("worldview") or "",
            "lifeview": identity.get("lifeview") or "",
            "values": identity.get("values") or "",
            "updated_at": identity.get("updated_at"),
        }
    if key == "knowledge":
        inbox = [note for note in notes if note["chapter"] == "inbox"]
        payload["inbox_count"] = len(inbox)
    return payload


def create_pillars_router(database: Database, vault_dir: Path | str, *,
                          read_only: bool = False, prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["pillars"])

    @router.get("/pillars")
    def pillars_manifest():
        return vault_layout.chapter_manifest()

    @router.get("/pillars/{pillar}/overview")
    def pillar_overview(pillar: str):
        return _overview(vault_dir, database, pillar)

    @router.get("/pillars/{pillar}/notes")
    def pillar_notes_list(pillar: str, chapter: str = Query(default=""),
                          q: str = Query(default=""), limit: int = Query(default=100, ge=1, le=500)):
        key = _pillar_or_404(pillar)
        if chapter and vault_layout.pillar_of(vault_layout.chapter_dir(chapter)) != key:
            raise HTTPException(422, "章节不属于这个库")
        return {"pillar": key, "items": pillar_notes.list_notes(
            vault_dir, pillar=key, chapter=chapter, query=q, limit=limit)}

    @router.get("/notes")
    def note_read(path: str = Query(min_length=1)):
        return pillar_notes.read_note(vault_dir, path)

    @router.get("/notes/backlinks")
    def note_backlinks(path: str = Query(min_length=1)):
        return {"items": pillar_notes.backlinks(vault_dir, path)}

    @router.post("/notes/update")
    def note_update(payload: NoteUpdate, path: str = Query(min_length=1)):
        if read_only:
            raise HTTPException(409, "当前是只读模式")
        ensure_vault_writable(read_only, "更新笔记")
        return pillar_notes.update_note(
            vault_dir, path, body=payload.body, title=payload.title,
            tags=payload.tags, status=payload.status)

    @router.post("/notes/move")
    def note_move(payload: NoteMove):
        if read_only:
            raise HTTPException(409, "当前是只读模式")
        ensure_vault_writable(read_only, "移动笔记")
        return pillar_notes.move_note(vault_dir, payload.path, payload.chapter)

    @router.post("/capture")
    def capture(payload: CaptureRequest):
        if read_only:
            raise HTTPException(409, "当前是只读模式")
        ensure_vault_writable(read_only, "快速捕获")
        return pillar_notes.create_note(
            vault_dir, pillar=payload.pillar, chapter=payload.chapter,
            title=payload.title, body=payload.body, note_type=payload.note_type,
            tags=payload.tags, source=payload.source, date=payload.date)

    @router.get("/search")
    def search(q: str = Query(min_length=1), pillar: str = Query(default=""),
               limit: int = Query(default=40, ge=1, le=200)):
        if pillar:
            _pillar_or_404(pillar)
        return {"query": q, "items": pillar_notes.list_notes(
            vault_dir, pillar=pillar, query=q, limit=limit)}

    @router.get("/steward/inbox-suggestions")
    def inbox_suggestions(limit: int = Query(default=50, ge=1, le=200)):
        notes = pillar_notes.list_notes(vault_dir, pillar="knowledge",
                                        chapter="inbox", limit=limit)
        items = [steward_policy.suggestion_for_note(note) for note in notes]
        return {"items": items, "count": len(items)}

    @router.post("/steward/suggest")
    def suggest_target(payload: SuggestRequest):
        result = steward_policy.suggest(payload.title, payload.body,
                                        payload.source, payload.tags)
        result["chapter_title"] = steward_policy.CHAPTER_TITLES.get(
            result["chapter"], result["chapter"])
        result["pillar_title"] = vault_layout.PILLAR_TITLES.get(
            result["pillar"], result["pillar"])
        return result

    @router.post("/reviews/weekly")
    def weekly_review(payload: ReviewRequest | None = None):
        if read_only:
            raise HTTPException(409, "当前是只读模式")
        ensure_vault_writable(read_only, "生成周复盘")
        return reviews.weekly_skeleton(vault_dir, (payload.day if payload else ""))

    @router.post("/reviews/monthly")
    def monthly_review(payload: ReviewRequest | None = None):
        if read_only:
            raise HTTPException(409, "当前是只读模式")
        ensure_vault_writable(read_only, "生成月复盘")
        return reviews.monthly_skeleton(vault_dir, (payload.month if payload else ""))

    return router


__all__ = ["create_pillars_router"]
