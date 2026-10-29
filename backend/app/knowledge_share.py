"""Knowledge-base share-link APIs (v1.0 P2).

The user opens chat.deepseek.com in a normal browser (no iframe, no cookies),
copies a public share link, and pastes it here.  This router only fetches that
public link, shows a preview, and can archive it as a Markdown note.  The AI
digest endpoint creates a ``new_note`` proposal; the user still confirms it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import note_editor, pillar_notes, share_link, vault_layout
from .db import Database
from .proposals import ProposalError

MAX_PREVIEW_MARKDOWN = 200_000


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=2048)
    rounds: str = Field(default="", max_length=80)


class ArchiveRequest(PreviewRequest):
    confirm: bool = Field(strict=True)
    chapter: str = Field(default="", max_length=40)
    title: str = Field(default="", max_length=200)


class DigestRequest(PreviewRequest):
    chapter: str = Field(default="", max_length=40)


def _preview_or_422(url: str, rounds: str = "") -> dict[str, Any]:
    try:
        return share_link.preview(url, rounds=rounds)
    except share_link.ShareLinkError as error:
        raise HTTPException(422, str(error)) from error


def _knowledge_chapter(value: str, fallback: str) -> str:
    chapter = str(value or "").strip() or fallback
    if chapter not in note_editor.KNOWLEDGE_CHAPTERS:
        raise HTTPException(422, "分享只能归档到知识库章节")
    return chapter


def create_knowledge_share_router(database: Database, vault_dir: Path | str, *,
                                   role_service: Any = None, proposals: Any = None,
                                   read_only: bool = False, prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["knowledge-share"])

    @router.post("/knowledge/share/preview")
    def preview(payload: PreviewRequest):
        result = _preview_or_422(payload.url, payload.rounds)
        if result["chars_markdown"] > MAX_PREVIEW_MARKDOWN:
            result["markdown"] = result["markdown"][:MAX_PREVIEW_MARKDOWN] + "\n\n…（预览已截断，归档时按原文）"
            result["preview_truncated"] = True
        return result

    @router.post("/knowledge/share/archive")
    def archive(payload: ArchiveRequest):
        if payload.confirm is not True:
            raise HTTPException(422, "请明确确认把分享归档到知识库")
        result = _preview_or_422(payload.url, payload.rounds)
        if len(result["markdown"].encode("utf-8")) > pillar_notes.MAX_BODY:
            raise HTTPException(413, "这篇分享太长，请用轮次缩小范围后再归档")
        chapter = _knowledge_chapter(payload.chapter, result["suggested_chapter"])
        note = note_editor.create_note(
            database, vault_dir, chapter=chapter,
            title=payload.title or result["title"], body=result["markdown"],
            note_type=vault_layout.chapter_default_type(chapter), source=result["share_url"],
            generated_by="share-link", read_only=read_only,
        )
        return {"note": note, "chapter": chapter, "source": result["share_url"],
                "turns": result["turns"], "chars": result["chars_markdown"]}

    @router.post("/knowledge/share/digest")
    def digest(payload: DigestRequest):
        if role_service is None or proposals is None:
            raise HTTPException(409, "AI 导师未启用")
        result = _preview_or_422(payload.url, payload.rounds)
        if len(result["markdown"].encode("utf-8")) > pillar_notes.MAX_BODY:
            raise HTTPException(413, "这篇分享太长，请用轮次缩小范围后再整理")
        try:
            generated = role_service.run_once(
                "academic_mentor", "share_digest", result["markdown"],
                entry="knowledge.qa", selectors=[])
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error
        except Exception as error:
            status = getattr(error, "status_code", 503)
            raise HTTPException(status, str(error)) from error
        structured = generated.get("structured") or {}
        note_data = structured.get("note") if isinstance(structured, dict) else None
        if not isinstance(note_data, dict):
            note_data = {}
        chapter = _knowledge_chapter(
            str(note_data.get("chapter") or payload.chapter or ""),
            result["suggested_chapter"])
        title = str(note_data.get("title") or result["title"])[:200]
        markdown = str(note_data.get("markdown") or "").strip() or result["markdown"]
        try:
            proposal = proposals.create(
                kind="new_note", title=title,
                summary=str(structured.get("summary") or "")[:2000],
                reason="由学术导师整理的分享链接",
                payload={"chapter": chapter, "title": title, "body": markdown,
                         "source": result["share_url"], "generated_by": "share-link"},
                sources=[result["share_url"]], role_id="academic_mentor",
            )
        except ProposalError as error:
            raise HTTPException(error.status_code, str(error)) from error
        return {"proposal": proposal, "preview": {
            "title": result["title"], "turns": result["turns"],
            "chars": result["chars_markdown"], "suggested_chapter": chapter}}

    return router


__all__ = ["create_knowledge_share_router", "PreviewRequest", "ArchiveRequest",
           "DigestRequest"]
