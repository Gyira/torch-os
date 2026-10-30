"""四库只读接口（2026-10-26 起写入统一在 Obsidian 完成）。"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from . import goal_notes, identity_inbox, journal_note, pillar_notes, steward, steward_policy, vault_layout
from .db import Database
from .habits import HabitService, shanghai_today
from .identity import read_identity
from .vault import parse_text, safe_note_path


def _first_line_summary(vault_dir: Path | str, relative: str) -> str:
    """档案时间线的一句话：取正文第一行（`> 摘要` 约定），不要 excerpt 那种整段拼接。"""
    try:
        raw = (Path(vault_dir) / relative).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    _meta, body = parse_text(raw)
    for line in body.splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith(">"):
            text = text.lstrip("> ").strip()
        return text[:90]
    return ""

# 四库阅读视图只展示位图附件；SVG 不在这里直出，避免同源脚本风险。
ASSET_TYPES = {
    '.avif': 'image/avif',
    '.bmp': 'image/bmp',
    '.gif': 'image/gif',
    '.jpeg': 'image/jpeg',
    '.jpg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
}


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


def _custom_notebook_rows(vault_dir: Path | str, notes: list[dict[str, Any]],
                          pillar: str) -> list[dict[str, Any]]:
    """自定义本子 = 库根目录下的直属文件夹；返回前端本子卡片需要的计数/最新一篇。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        name = vault_layout.custom_notebook_name_from_relpath(str(note.get("path") or ""))
        if name:
            grouped.setdefault(name, []).append(note)
    rows: list[dict[str, Any]] = []
    for name in vault_layout.list_custom_notebooks(vault_dir, pillar):
        mine = grouped.get(name, [])
        rows.append({
            "key": vault_layout.custom_notebook_key(name, pillar),
            "title": name,
            "path": vault_layout.custom_notebook_path(name, pillar),
            "count": len(mine),
            "latest": mine[0] if mine else None,
            "custom": True,
        })
    return rows


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
    if key in vault_layout.CUSTOM_NOTEBOOK_PILLARS:
        payload["custom_notebooks"] = _custom_notebook_rows(vault_dir, notes, key)
    if key == "life":
        today_date = shanghai_today()
        today = today_date.isoformat()
        journal = journal_note.read_note(vault_dir, today)
        habits = HabitService(database, vault_dir).list(status="active")
        week: list[dict[str, Any]] = []
        for offset in range(6, -1, -1):
            day = (today_date - timedelta(days=offset)).isoformat()
            entry = journal_note.read_note(vault_dir, day) if day != today else journal
            relpath = str(entry.get("relpath") or "") if entry.get("exists") else ""
            week.append({
                "date": day,
                "weekday": journal_note.WEEKDAYS[date.fromisoformat(day).weekday()],
                "exists": bool(entry.get("exists")),
                "path": relpath if relpath.startswith("00_人生路/") else "",
                "legacy": bool(entry.get("exists")) and not relpath.startswith("00_人生路/"),
            })
        payload["today"] = {
            "date": today,
            "journal_written": bool(journal.get("exists")),
            "journal_updated_at": journal.get("updated_at"),
            "habit_count": len(habits),
            "week": week,
        }
    if key == "self":
        identity = read_identity(vault_dir)
        drafts = identity_inbox.list_drafts(vault_dir)
        payload["identity"] = {
            "exists": bool(identity.get("exists")),
            "path": identity.get("path"),
            "name": identity.get("name") or "",
            "motto": identity.get("motto") or "",
            "worldview": identity.get("worldview") or "",
            "lifeview": identity.get("lifeview") or "",
            "values": identity.get("values") or "",
            "notes": identity.get("notes") or "",
            "mottos": identity.get("mottos") or [],
            "updated_at": identity.get("updated_at"),
        }
        payload["drafts"] = drafts
        payload["draft_count"] = len(drafts)
        # 档案首屏的自传时间线：只取「记忆与自传」，按日期倒序，带一句摘要。
        payload["timeline"] = [
            {
                "path": note["path"],
                "title": note["title"],
                "date": str(note.get("date") or "")[:10],
                "summary": _first_line_summary(vault_dir, note["path"]) or (note.get("excerpt") or ""),
            }
            for note in pillar_notes.list_notes(vault_dir, chapter="memory", limit=12)
        ]
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

    @router.get("/goals")
    def goals(limit: int = Query(default=100, ge=1, le=500)):
        """目标选择器：只读人生路的年度/季度/月度目标笔记。"""
        return goal_notes.list_goals(vault_dir, limit=limit)

    @router.get("/notes")
    def note_read(path: str = Query(min_length=1)):
        return pillar_notes.read_note(vault_dir, path)

    @router.get("/notes/backlinks")
    def note_backlinks(path: str = Query(min_length=1)):
        return {"items": pillar_notes.backlinks(vault_dir, path)}

    @router.get("/notes/asset")
    def note_asset(path: str = Query(min_length=1)):
        """四库阅读视图的本地图片：只允许库内位图，路径与符号链接沿用 vault 的同一套校验。"""
        try:
            target = safe_note_path(Path(vault_dir), path)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        media_type = ASSET_TYPES.get(target.suffix.lower())
        if not media_type:
            raise HTTPException(415, "只支持 png/jpg/jpeg/gif/webp/avif/bmp 图片")
        if not target.is_file():
            raise HTTPException(404, "图片不存在")
        return FileResponse(target, media_type=media_type,
                            headers={"Cache-Control": "no-store"})

    @router.get("/search")
    def search(q: str = Query(min_length=1), pillar: str = Query(default=""),
               limit: int = Query(default=40, ge=1, le=200)):
        if pillar:
            _pillar_or_404(pillar)
        return {"query": q, "items": pillar_notes.list_notes(
            vault_dir, pillar=pillar, query=q, limit=limit)}

    @router.get("/steward/inbox-suggestions")
    def inbox_suggestions(limit: int = Query(default=50, ge=1, le=200)):
        # 直接复用管家提案，确保界面上的 id 与只读提案一致。
        items = []
        for proposal in steward.inbox_proposals(vault_dir):
            items.append({
                "proposal_id": proposal["id"],
                "path": proposal["path"],
                "title": proposal["title"],
                "chapter": proposal["target_chapter"],
                "pillar": proposal["target_pillar"],
                "reason": proposal["reason"],
                "confidence": proposal["confidence"],
                "matched": "",
                "chapter_title": proposal["target_title"],
                "pillar_title": proposal["target_pillar_title"],
            })
            if len(items) >= limit:
                break
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

    return router


__all__ = ["create_pillars_router"]
