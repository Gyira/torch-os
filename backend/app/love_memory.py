"""恋爱记忆（2026-11-01）：Markdown 本子与导出。

数据是 ``02_生活运营/01_恋爱系统/01_恋爱记忆/`` 下的普通 Markdown：
- 固定本子：``恋爱日志``（默认、不可删）与 ``AI 草稿``（AI 批量补日记先落这里）；
- 自定义本子：同一目录下的文件夹，key 为 ``custom:love:<名字>``。

笔记的增删改查复用 ``note_editor`` / ``/api/writing``；本模块只提供恋爱首页需要的
聚合、导出和“把 AI 草稿移入恋爱日志”的批量动作，避免把本子逻辑复制一份。
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from . import note_editor, pillar_notes, vault_layout
from .db import Database
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

FIXED_NOTEBOOKS = (
    {
        "key": vault_layout.LOVE_JOURNAL_CHAPTER,
        "title": "恋爱日志",
        "description": "默认本子，记录每天想留下的片段。",
        "protected": True,
        "icon": "恋",
        "tone": "rose",
    },
    {
        "key": vault_layout.LOVE_DRAFT_CHAPTER,
        "title": "AI 草稿",
        "description": "AI 批量补日记先放这里，你确认后再移入正式本子。",
        "protected": True,
        "icon": "AI",
        "tone": "plum",
    },
)


def _note_book_key(note: dict[str, Any]) -> str:
    chapter = str(note.get("chapter") or "")
    if chapter in (vault_layout.LOVE_JOURNAL_CHAPTER, vault_layout.LOVE_DRAFT_CHAPTER):
        return chapter
    custom = vault_layout.custom_notebook_name_from_relpath(str(note.get("path") or ""))
    if custom:
        return vault_layout.custom_notebook_key(custom, vault_layout.LOVE_PILLAR)
    return chapter


def _note_date(note: dict[str, Any]) -> str:
    value = str(note.get("date") or "").strip()
    if value:
        return value[:10]
    try:
        return datetime.fromtimestamp(float(note.get("mtime") or 0)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _fixed_rows(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        grouped.setdefault(_note_book_key(note), []).append(note)
    rows: list[dict[str, Any]] = []
    for item in FIXED_NOTEBOOKS:
        mine = grouped.get(item["key"], [])
        rows.append({
            **item,
            "count": len(mine),
            "latest": mine[0] if mine else None,
            "path": vault_layout.chapter_dir(item["key"]),
            "custom": False,
        })
    return rows


def _custom_rows(vault_dir: Path | str, notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        custom = vault_layout.custom_notebook_name_from_relpath(str(note.get("path") or ""))
        if custom:
            grouped.setdefault(custom, []).append(note)
    rows: list[dict[str, Any]] = []
    for name in vault_layout.list_custom_notebooks(vault_dir, vault_layout.LOVE_PILLAR):
        mine = grouped.get(name, [])
        rows.append({
            "key": vault_layout.custom_notebook_key(name, vault_layout.LOVE_PILLAR),
            "title": name,
            "description": "自定义本子",
            "custom": True,
            "icon": "本",
            "tone": "slate",
            "count": len(mine),
            "latest": mine[0] if mine else None,
            "path": vault_layout.custom_notebook_path(name, vault_layout.LOVE_PILLAR),
            "protected": vault_layout.is_protected_notebook(
                vault_layout.LOVE_PILLAR,
                vault_layout.custom_notebook_key(name, vault_layout.LOVE_PILLAR)),
        })
    return rows


def _export_text(vault_dir: Path | str, notes: list[dict[str, Any]]) -> str:
    vault = Path(vault_dir)
    blocks: list[str] = []
    for note in notes:
        title = str(note.get("title") or "未命名")
        day = _note_date(note)
        try:
            body = pillar_notes.read_note(vault, str(note.get("path") or "")).get("body") or ""
        except HTTPException:
            body = ""
        header = f"# {title}"
        if day:
            header += f"（{day}）"
        blocks.append(f"{header}\n\n{body.strip()}\n")
    return "\n---\n\n".join(blocks) if blocks else "还没有记忆。\n"


def create_memory_router(vault_dir: Path | str, database: Database, *,
                         read_only: bool = False,
                         prefix: str = "/api/love/memory") -> APIRouter:
    vault = Path(vault_dir).resolve()
    router = APIRouter(prefix=prefix, tags=["love-memory"])

    def all_notes(limit: int = 500) -> list[dict[str, Any]]:
        return pillar_notes.list_notes(
            vault, pillar=vault_layout.LOVE_PILLAR, limit=max(1, min(500, limit)))

    @router.get("/overview")
    def overview(limit: int = Query(default=12, ge=1, le=60)):
        notes = all_notes(500)
        notebooks = _fixed_rows(notes)
        notebooks.extend(_custom_rows(vault, notes))
        dates: dict[str, int] = {}
        month_counts: dict[str, int] = {}
        for note in notes:
            day = _note_date(note)
            if day:
                dates[day] = dates.get(day, 0) + 1
                month_counts[day[:7]] = month_counts.get(day[:7], 0) + 1
        return {
            "notebooks": notebooks,
            "recent": notes[:limit],
            "total": len(notes),
            "dates": dict(sorted(dates.items())),
            "months": dict(sorted(month_counts.items(), reverse=True)),
            "journal_count": sum(1 for note in notes
                                 if note.get("chapter") == vault_layout.LOVE_JOURNAL_CHAPTER),
            "draft_count": sum(1 for note in notes
                               if note.get("chapter") == vault_layout.LOVE_DRAFT_CHAPTER),
            "custom_count": sum(1 for note in notebooks if note.get("custom")),
        }

    @router.get("/notes")
    def notes(notebook: str = Query(default=""),
              q: str = Query(default=""), limit: int = Query(default=100, ge=1, le=500)):
        chapter = str(notebook or "").strip()
        if chapter and not (
            chapter in (vault_layout.LOVE_JOURNAL_CHAPTER, vault_layout.LOVE_DRAFT_CHAPTER)
            or vault_layout.is_custom_notebook_key(chapter)
        ):
            raise HTTPException(422, "未知的恋爱记忆本子")
        items = pillar_notes.list_notes(
            vault, pillar=vault_layout.LOVE_PILLAR, chapter=chapter,
            query=q, limit=limit,
        )
        return {"notebook": chapter or "all", "items": items}

    @router.get("/export")
    def export(notebook: str = Query(default="")):
        chapter = str(notebook or "").strip()
        if chapter and not (
            chapter in (vault_layout.LOVE_JOURNAL_CHAPTER, vault_layout.LOVE_DRAFT_CHAPTER)
            or vault_layout.is_custom_notebook_key(chapter)
        ):
            raise HTTPException(422, "未知的恋爱记忆本子")
        notes = pillar_notes.list_notes(vault, pillar=vault_layout.LOVE_PILLAR,
                                         chapter=chapter, limit=500)
        label = "恋爱记忆"
        if chapter:
            key = chapter
            for item in FIXED_NOTEBOOKS:
                if item["key"] == chapter:
                    label = str(item["title"])
                    break
            else:
                name = vault_layout.custom_notebook_name(chapter)
                if name:
                    label = name
        text = _export_text(vault, notes)
        filename = quote(f"{label}.txt")
        return PlainTextResponse(
            text, media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        )

    @router.post("/drafts/promote")
    def promote_drafts(limit: int = Query(default=200, ge=1, le=500)):
        try:
            ensure_vault_writable(read_only, "把 AI 草稿移入恋爱日志")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        drafts = pillar_notes.list_notes(
            vault, pillar=vault_layout.LOVE_PILLAR,
            chapter=vault_layout.LOVE_DRAFT_CHAPTER, limit=limit,
        )
        moved: list[str] = []
        failed: list[dict[str, str]] = []
        for note in drafts:
            try:
                note_editor.move_note(
                    database, vault, path=str(note.get("path") or ""),
                    target_chapter=vault_layout.LOVE_JOURNAL_CHAPTER,
                    read_only=read_only,
                )
                moved.append(str(note.get("path") or ""))
            except Exception as error:  # noqa: BLE001 - 单篇失败不阻断其余草稿
                failed.append({"path": str(note.get("path") or ""),
                               "error": str(error)[:300]})
        return {"moved": len(moved), "failed": failed, "paths": moved}

    return router


__all__ = ["FIXED_NOTEBOOKS", "create_memory_router"]
