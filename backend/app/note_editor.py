"""In-app writing for 人生路 / 知识库 (v1.0 P2).

Policy change: until v0.99 the four pillars were read-only in the web UI and all
edits went to Obsidian.  v1.0 opens exactly two writing areas:

- 人生路: reflections, insights, essays, reviews, goals/plans, project notes;
- 知识库: topic/reading/course/concept/method/output/inbox notes.

Everything else stays managed by its own module: the daily journal has its own
closed-loop API, tasks/habits have their board APIs, identity has the review
pipeline, and 生活运营 reports are written by dedicated services.

All writes here are allow-list checked, revision guarded, audited, and refused
when the vault is read-only.  Frontmatter unknown to Torch is preserved by
``pillar_notes``.
"""
from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import journal_inbox, pillar_notes, vault_layout
from .db import Database
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

LIFE_CHAPTERS = frozenset({
    "reflection", "insight_life", "insight_work", "insight_study",
    "review_week", "review_month",
    "goal_year", "goal_quarter", "goal_month",
    "project_plan", "work_review_project", "work_review_meeting",
    "work_review_pitfall", "essay",
})
KNOWLEDGE_CHAPTERS = frozenset({
    "inbox", "topic", "reading", "course", "concept",
    "method_work", "method_general", "output",
})
WRITABLE_CHAPTERS = LIFE_CHAPTERS | KNOWLEDGE_CHAPTERS
CHAPTER_LABELS = {
    "reflection": "反思", "insight_life": "生活心得", "insight_work": "工作心得",
    "insight_study": "学习心得", "review_week": "周复盘", "review_month": "月复盘",
    "goal_year": "年度目标", "goal_quarter": "季度目标", "goal_month": "月度目标",
    "project_plan": "项目计划", "work_review_project": "项目复盘",
    "work_review_meeting": "会议收获", "work_review_pitfall": "踩坑记录",
    "essay": "随笔",
    "inbox": "收件箱", "topic": "主题笔记", "reading": "读书笔记",
    "course": "课程笔记", "concept": "概念卡片",
    "method_work": "工作方法", "method_general": "通用方法", "output": "输出与作品",
}
DEFAULT_LIFE_CHAPTER = "reflection"
DEFAULT_KNOWLEDGE_CHAPTER = "topic"


def chapter_pillar(chapter: str) -> str:
    directory = vault_layout.chapter_dir(str(chapter or "").strip())
    return vault_layout.pillar_of(directory) if directory else ""


def is_writable_chapter(chapter: str) -> bool:
    value = str(chapter or "").strip()
    return value in WRITABLE_CHAPTERS or vault_layout.is_custom_notebook_key(value)


def _require_writable_chapter(chapter: str, vault_dir: Path | str) -> str:
    value = str(chapter or "").strip()
    if vault_layout.is_chapter_hidden(vault_dir, value):
        raise HTTPException(422, "这个本子已被删除，不能写入")
    if value not in WRITABLE_CHAPTERS and not vault_layout.is_custom_notebook_key(value):
        raise HTTPException(422, "这个章节不在应用内写作范围里")
    return value


def _require_writable_path(relative: str, vault_dir: Path | str) -> str:
    chapter = vault_layout.chapter_key_of(relative)
    if vault_layout.is_chapter_hidden(vault_dir, chapter):
        raise HTTPException(422, "这个本子已被删除，不能写入")
    if chapter not in WRITABLE_CHAPTERS and not vault_layout.is_custom_notebook_key(chapter):
        raise HTTPException(422, "这篇笔记不在应用内写作范围里")
    return chapter


def _ensure_custom_notebook_exists(vault_dir: Path | str, chapter: str) -> None:
    if not vault_layout.is_custom_notebook_key(chapter):
        return
    directory = vault_layout.chapter_dir(chapter)
    target = Path(vault_dir) / directory if directory else None
    if target is None or not target.is_dir():
        raise HTTPException(422, "这个本子不存在，请先在人生路新建本子")
    try:
        info = target.lstat()
    except OSError as error:
        raise HTTPException(422, "本子文件夹不可读") from error
    if stat.S_ISLNK(info.st_mode) or getattr(
            info, "st_file_attributes", 0) & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise HTTPException(422, "本子文件夹是链接，不能写入")


def _ensure_write(read_only: bool, action: str) -> None:
    try:
        ensure_vault_writable(read_only, action)
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error


def create_note(database: Database, vault_dir: Path | str, *, chapter: str, title: str,
                body: str = "", note_type: str = "", tags: list[str] | None = None,
                source: str = "", date: str = "", generated_by: str = "",
                read_only: bool = False) -> dict[str, Any]:
    target = _require_writable_chapter(chapter, vault_dir)
    _ensure_custom_notebook_exists(vault_dir, target)
    _ensure_write(read_only, "新建笔记")
    pillar = chapter_pillar(target)
    extra = {"generated_by": generated_by} if generated_by else None
    note = pillar_notes.create_note(
        vault_dir, pillar=pillar, chapter=target, title=title, body=body,
        note_type=note_type, tags=tags, source=source, date=date,
        extra_metadata=extra,
    )
    database.audit("note_created", {"path": note.get("path", ""), "chapter": target})
    return note


def update_note(database: Database, vault_dir: Path | str, *, path: str,
                body: str | None = None, title: str = "", tags: list[str] | None = None,
                expected_revision: str = "", read_only: bool = False) -> dict[str, Any]:
    _require_writable_path(path, vault_dir)
    _ensure_write(read_only, "编辑笔记")
    note = pillar_notes.update_note(
        vault_dir, path, body=body, title=title, tags=tags,
        expected_revision=expected_revision,
    )
    database.audit("note_updated", {"path": note.get("path", path),
                                    "chapter": note.get("chapter", "")})
    return note


def move_note(database: Database, vault_dir: Path | str, *, path: str,
              target_chapter: str, expected_revision: str = "",
              read_only: bool = False) -> dict[str, Any]:
    source_chapter = _require_writable_path(path, vault_dir)
    target = _require_writable_chapter(target_chapter, vault_dir)
    _ensure_custom_notebook_exists(vault_dir, target)
    if chapter_pillar(source_chapter) != chapter_pillar(target):
        raise HTTPException(422, "应用内只支持在同一个库里移动笔记")
    _ensure_write(read_only, "移动笔记")
    note = pillar_notes.move_note(
        vault_dir, path, target, expected_revision=expected_revision)
    # 物理文件已经移动；如果这篇笔记已经被「扫描 Vault」进 documents 索引，
    # 立即同步索引路径/正文哈希，避免 资料/搜索/计数 还指向旧文件夹。
    new_path = str(note.get("path") or "")
    index_updated = False
    if new_path:
        try:
            raw = (Path(vault_dir) / new_path).read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
        except OSError:
            digest = ""
        try:
            index_updated = database.relocate_document(
                path, new_path, title=str(note.get("title") or ""),
                content=str(note.get("body") or ""), sha256=digest,
                metadata=(note.get("raw_metadata")
                          if isinstance(note.get("raw_metadata"), dict) else {}),
            )
        except Exception:
            # 文件已经移动成功；索引同步失败不能让用户误以为移动失败。
            # 下一次「扫描 Vault」会按新路径重新核对并修复。
            index_updated = False
    database.audit("note_moved", {"path": new_path, "from": source_chapter,
                                  "to": target, "index_updated": index_updated})
    return note


def delete_note(database: Database, vault_dir: Path | str, *, path: str,
                expected_revision: str = "", read_only: bool = False) -> dict[str, Any]:
    """软删除：只允许应用内可写章节；文件移进 `.torch-os/笔记回收站`。"""
    chapter = _require_writable_path(path, vault_dir)
    _ensure_custom_notebook_exists(vault_dir, chapter)
    _ensure_write(read_only, "删除笔记")
    result = pillar_notes.trash_note(
        vault_dir, path, expected_revision=expected_revision)
    database.audit("note_deleted", {"path": result.get("path", path),
                                    "trash": result.get("trash_path", ""),
                                    "chapter": chapter})
    return result


# ── AI 判类 / 润色 ────────────────────────────────────────────────────────

class NoteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    chapter: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=200_000)
    note_type: str = Field(default="", max_length=40)
    tags: list[str] = Field(default_factory=list, max_length=24)
    source: str = Field(default="", max_length=500)
    date: str = Field(default="", max_length=10)


class NoteUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1, max_length=2048)
    expected_revision: str = Field(default="", max_length=64)
    body: str | None = Field(default=None, max_length=200_000)
    title: str = Field(default="", max_length=200)
    tags: list[str] | None = Field(default=None, max_length=24)


class NoteMoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1, max_length=2048)
    target_chapter: str = Field(min_length=1, max_length=120)
    expected_revision: str = Field(default="", max_length=64)


class NoteDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1, max_length=2048)
    expected_revision: str = Field(default="", max_length=64)


class ClassifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=20_000)
    day: str = Field(default="", max_length=10)
    context: Literal["life", "knowledge"] = "life"


class PolishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=20_000)
    path: str = Field(default="", max_length=2048)
    context: Literal["life", "knowledge"] = "life"


def _fallback_cards(text: str, day: str, context: str) -> list[dict[str, Any]]:
    if context == "life":
        cards = journal_inbox.classify(text, day or journal_inbox.shanghai_today().isoformat())
        for card in cards:
            card["chapter"] = "journal" if card["kind"] == "diary" else journal_inbox.KIND_CHAPTER.get(card["kind"], DEFAULT_LIFE_CHAPTER)
        return cards
    title = journal_inbox._title(text)
    chapter = DEFAULT_KNOWLEDGE_CHAPTER
    return [{
        "kind": "note", "label": "知识笔记", "title": title, "body": text,
        "chapter": chapter, "target": vault_layout.chapter_dir(chapter),
        "confidence": 0.5, "reason": "没有命中规则，先放主题笔记",
    }]


def _normalize_cards(cards: Any, context: str, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = LIFE_CHAPTERS if context == "life" else KNOWLEDGE_CHAPTERS
    default = DEFAULT_LIFE_CHAPTER if context == "life" else DEFAULT_KNOWLEDGE_CHAPTER
    result: list[dict[str, Any]] = []
    for card in list(cards or [])[:3]:
        if not isinstance(card, dict):
            continue
        body = str(card.get("body") or "").strip()
        if not body:
            continue
        kind = str(card.get("kind") or "note").strip()[:20]
        chapter = str(card.get("chapter") or "").strip()
        if kind == "diary" and context == "life":
            chapter = "journal"
        if chapter not in allowed and chapter != "journal":
            chapter = default
        result.append({
            "kind": kind,
            "label": str(card.get("label") or journal_inbox.KIND_LABELS.get(kind, "笔记")),
            "title": str(card.get("title") or journal_inbox._title(body))[:80],
            "body": body,
            "chapter": chapter,
            "target": (vault_layout.chapter_dir("journal") if chapter == "journal"
                       else vault_layout.chapter_dir(chapter)),
            "confidence": card.get("confidence", "medium"),
            "reason": str(card.get("reason") or "AI 判类"),
            "_source": "ai",
        })
    return result or fallback


def classify_text(text: str, *, day: str, context: str, role_service: Any = None) -> dict[str, Any]:
    fallback = _fallback_cards(text, day, context)
    if role_service is None:
        return {"cards": fallback, "source": "rules"}
    role_id = "journal_companion" if context == "life" else "academic_mentor"
    mode = "classify"
    entry = "life.write" if context == "life" else "knowledge.editor"
    try:
        result = role_service.run_once(role_id, mode, text, entry=entry, selectors=[])
    except Exception:
        return {"cards": fallback, "source": "rules"}
    structured = result.get("structured") or {}
    cards = structured.get("cards") if isinstance(structured, dict) else None
    normalized = _normalize_cards(cards, context, fallback)
    source = "ai" if any(card.get("_source") == "ai" for card in normalized) else "rules"
    for card in normalized:
        card.pop("_source", None)
    return {"cards": normalized, "source": source, "truncated": result.get("truncated", False)}


def polish_text(text: str, *, path: str, context: str, role_service: Any = None) -> dict[str, Any]:
    if role_service is None:
        raise HTTPException(503, "AI 不可用：没有配置 dsh 或 DeepSeek Key")
    role_id = "journal_companion" if context == "life" else "academic_mentor"
    entry = "life.editor" if context == "life" else "knowledge.editor"
    selectors = [{"entry": "note", "path": path}] if path else []
    try:
        result = role_service.run_once(role_id, "polish", text, entry=entry,
                                       selectors=selectors)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    except Exception as error:  # AIError/HTTPException/...
        status = getattr(error, "status_code", 503)
        raise HTTPException(status, str(error)) from error
    structured = result.get("structured") or {}
    polished = str(structured.get("polished") or "").strip()
    if not polished:
        raise HTTPException(502, "AI 没有返回可用的润色结果，正文未改动")
    return {
        "original": text,
        "polished": polished,
        "changes": [str(item) for item in (structured.get("changes") or [])][:20],
        "seconds": result.get("seconds", 0),
    }


def create_writing_router(database: Database, vault_dir: Path | str, role_service: Any = None, *,
                          read_only: bool = False, prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["writing"])

    @router.get("/writing/chapters")
    def chapters():
        hidden = vault_layout.hidden_chapters(vault_dir)

        def rows(keys):
            return [{"key": key, "title": CHAPTER_LABELS.get(key, key),
                     "path": vault_layout.chapter_dir(key)}
                    for key in sorted(keys) if key not in hidden]
        result = {"life": rows(LIFE_CHAPTERS), "knowledge": rows(KNOWLEDGE_CHAPTERS)}
        # 自定义本子 = 各库根目录下的文件夹；编辑器里和固定章节一样可作为移动目标。
        for pillar in vault_layout.CUSTOM_NOTEBOOK_PILLARS:
            for name in vault_layout.list_custom_notebooks(vault_dir, pillar):
                key = vault_layout.custom_notebook_key(name, pillar)
                result[pillar].append({
                    "key": key, "title": name,
                    "path": vault_layout.chapter_dir(key), "custom": True,
                })
        return result

    @router.post("/writing/notes")
    def create(payload: NoteCreateRequest):
        return create_note(
            database, vault_dir, chapter=payload.chapter, title=payload.title,
            body=payload.body, note_type=payload.note_type, tags=payload.tags,
            source=payload.source, date=payload.date, read_only=read_only,
        )

    @router.put("/writing/notes")
    def update(payload: NoteUpdateRequest):
        return update_note(
            database, vault_dir, path=payload.path, body=payload.body,
            title=payload.title, tags=payload.tags,
            expected_revision=payload.expected_revision, read_only=read_only,
        )

    @router.post("/writing/notes/move")
    def move(payload: NoteMoveRequest):
        return move_note(
            database, vault_dir, path=payload.path,
            target_chapter=payload.target_chapter,
            expected_revision=payload.expected_revision, read_only=read_only,
        )

    @router.post("/writing/notes/delete")
    def delete(payload: NoteDeleteRequest):
        return delete_note(
            database, vault_dir, path=payload.path,
            expected_revision=payload.expected_revision, read_only=read_only,
        )

    @router.post("/writing/classify")
    def classify(payload: ClassifyRequest):
        result = classify_text(payload.text, day=payload.day, context=payload.context,
                               role_service=role_service)
        hidden = vault_layout.hidden_chapters(vault_dir)
        fallback = "essay" if payload.context == "life" else "inbox"
        for card in result.get("cards", []):
            chapter = str(card.get("chapter") or "")
            if chapter in hidden:
                card["chapter"] = fallback
                card["target"] = vault_layout.chapter_dir(fallback)
                card["reason"] = ((card.get("reason") or "") + "；原建议本子已删除，改到默认本子").strip("；")
        return result

    @router.post("/writing/polish")
    def polish(payload: PolishRequest):
        return polish_text(payload.text, path=payload.path, context=payload.context,
                           role_service=role_service)

    return router


__all__ = [
    "LIFE_CHAPTERS", "KNOWLEDGE_CHAPTERS", "WRITABLE_CHAPTERS", "CHAPTER_LABELS",
    "chapter_pillar", "is_writable_chapter", "create_note", "update_note", "move_note",
    "delete_note", "classify_text", "polish_text", "create_writing_router",
]
