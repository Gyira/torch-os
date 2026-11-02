"""自我档案 · 人生导师 HTTP 接口（2026-11-01）。

只读端：总览、本子、条目、画像、记忆、今日三问、待确认。
写入端：迁移预览/执行、采纳/驳回补丁、回答/跳过三问、全量整理。
所有写入都要求 ``confirm=true``（三问跳过除外），并受 VAULT_READ_ONLY 保护。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import (mentor_memory, mentor_pending, mentor_questions, self_archive,
               self_bazi, self_migration, self_synthesize, vault_layout)


class SynthesizePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    range_days: int = Field(default=14, ge=7, le=30)


class PendingAcceptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    confirm: bool = False
    edited_text: str = Field(default="", max_length=8_000)


class PendingRejectPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(default="", max_length=2_000)


class QuestionAnswerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    confirm: bool = False
    text: str = Field(default="", max_length=4_000)
    accept_suggested: bool = False


class QuestionSkipPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forever: bool = False


class MigrationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False


class ArchiveRecoveryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False


class BaziProfilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(default="", max_length=60)
    sex: str = Field(default="", max_length=10)
    birth_date: str = Field(default="", max_length=10)
    birth_time: str = Field(default="", max_length=5)
    birth_place: str = Field(default="", max_length=120)
    pillars_manual: str = Field(default="", max_length=40)
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    revision: str = Field(default="", max_length=64)


class BaziGeneratePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    confirm: bool = False
    topic: str = Field(default="综合", max_length=20)


def _guard(action):
    try:
        return action()
    except HTTPException:
        raise
    except (self_archive.SelfArchiveError, mentor_pending.PendingError,
            mentor_questions.QuestionError, mentor_memory.MentorMemoryError,
            self_synthesize.SynthesizeError, self_bazi.BaziError) as error:
        raise HTTPException(error.status_code, str(error)) from error
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


def _today_payload(vault: Path, *, read_only: bool) -> dict[str, Any]:
    """只读库没有题库时不要让整个导师页 409；把问题区域降级为空。"""
    try:
        return mentor_questions.ensure_today(vault, read_only=read_only)
    except Exception as error:  # noqa: BLE001
        return {"date": "", "items": [], "saved": False, "remaining": 0,
                "error": str(error)}


def create_self_router(vault_dir: Path | str, *, read_only: bool = False,
                       role_service: Any = None, prefix: str = "/api",
                       autonomous_archive: bool = True) -> APIRouter:
    vault = Path(vault_dir)
    router = APIRouter(prefix=prefix, tags=["self"])

    # ----- 自我档案 -------------------------------------------------------

    @router.get("/self/overview")
    def self_overview():
        archive = self_archive.overview(vault)
        today = _guard(lambda: _today_payload(vault, read_only=read_only))
        return {
            **archive,
            "vault_name": Path(vault_dir).name,
            "pending_count": mentor_pending.count_pending(vault),
            "questions": {
                "date": today["date"],
                "remaining": today["remaining"],
                "items": today["items"],
            },
            "migration": self_migration.preview(vault),
            "archive_operations": self_archive.recovery_status(vault),
            "paths": {
                "archive": vault_layout.SELF_ARCHIVE_ROOT,
                "portrait": vault_layout.SELF_PORTRAIT_RELATIVE,
                "identity": vault_layout.SELF_IDENTITY_RELATIVE,
            },
        }

    @router.get("/self/books/{book_key}")
    def self_book(book_key: str, history: bool = Query(default=False)):
        return _guard(lambda: {
            "book": self_archive.book_meta(book_key),
            "vault_name": Path(vault_dir).name,
            "entries": self_archive.list_entries(
                vault, book_key, include_history=history),
        })

    @router.get("/self/entries/{entry_id}")
    def self_entry(entry_id: str):
        item = self_archive.find_entry(vault, entry_id, include_history=True)
        if item is None:
            raise HTTPException(404, "档案条目不存在")
        return item

    @router.get("/self/migration/preview")
    def migration_preview():
        return self_migration.preview(vault)

    @router.post("/self/migration/apply")
    def migration_apply(payload: MigrationPayload):
        if payload.confirm is not True:
            raise HTTPException(422, "请明确确认迁移自我档案")
        return _guard(lambda: self_migration.apply(
            vault, confirm=True, read_only=read_only))

    @router.get("/self/archive/operations")
    def archive_operations():
        return self_archive.recovery_status(vault)

    @router.post("/self/archive/operations/recover")
    def archive_operations_recover(payload: ArchiveRecoveryPayload):
        if payload.confirm is not True:
            raise HTTPException(422, "请明确确认检查未完成的档案操作")
        return _guard(lambda: self_archive.recover_incomplete_operations(
            vault, read_only=read_only))

    @router.get("/self/pending")
    def self_pending():
        return {"items": mentor_pending.list_pending(vault),
                "count": mentor_pending.count_pending(vault)}

    @router.post("/self/pending/{pending_id}/accept")
    def pending_accept(pending_id: str, payload: PendingAcceptPayload):
        return _guard(lambda: mentor_pending.accept_pending(
            vault, pending_id, edited_text=payload.edited_text,
            confirm=payload.confirm, read_only=read_only))

    @router.post("/self/pending/{pending_id}/reject")
    def pending_reject(pending_id: str, payload: PendingRejectPayload):
        return _guard(lambda: mentor_pending.reject_pending(
            vault, pending_id, reason=payload.reason, read_only=read_only))

    @router.post("/self/synthesize")
    def self_synthesize_route(payload: SynthesizePayload):
        if payload.confirm is not True:
            raise HTTPException(422, "请明确确认更新自我档案")
        return _guard(lambda: self_synthesize.synthesize(
            role_service, vault, range_days=payload.range_days,
            read_only=read_only, autonomous_archive=autonomous_archive))

    # ----- 人生导师 -------------------------------------------------------

    @router.get("/mentor/overview")
    def mentor_overview():
        today = _guard(lambda: _today_payload(vault, read_only=read_only))
        pending = mentor_pending.list_pending(vault)
        threads = role_service.threads(limit=20, role_id="mentor") if role_service else []
        legacy = role_service.threads(limit=10, role_id="yiling") if role_service else []
        return {
            "vault_name": Path(vault_dir).name,
            "memory": mentor_memory.overview(vault),
            "memory_qa": mentor_memory.read_qa(vault),
            "pending": pending,
            "pending_count": len(pending),
            "questions": today,
            "portrait": self_archive.read_portrait(vault),
            "autonomous_archive": bool(autonomous_archive),
            "archive_operations": self_archive.recovery_status(vault),
            "threads": threads,
            "legacy_threads": legacy,
            "paths": {
                "memory": vault_layout.SELF_MENTOR_MEMORY_DIR,
                "pending": vault_layout.SELF_MENTOR_PENDING_DIR,
                "questions": vault_layout.SELF_MENTOR_QUESTIONS_DIR,
                "archive": vault_layout.SELF_ARCHIVE_ROOT,
            },
        }

    @router.get("/mentor/questions/today")
    def questions_today():
        return _guard(lambda: _today_payload(vault, read_only=read_only))

    @router.post("/mentor/questions/draft")
    def questions_draft():
        return _guard(lambda: mentor_questions.draft_today(
            role_service, vault, read_only=read_only))

    @router.post("/mentor/questions/{qid}/answer")
    def questions_answer(qid: str, payload: QuestionAnswerPayload):
        if payload.confirm is not True:
            raise HTTPException(422, "请明确确认回答")
        return _guard(lambda: mentor_questions.answer(
            vault, qid, payload.text, accept_suggested=payload.accept_suggested,
            read_only=read_only))

    @router.post("/mentor/questions/{qid}/skip")
    def questions_skip(qid: str, payload: QuestionSkipPayload):
        return _guard(lambda: mentor_questions.skip(
            vault, qid, forever=payload.forever, read_only=read_only))

    @router.get("/mentor/memory")
    def mentor_memory_route():
        return {**mentor_memory.overview(vault), "qa": mentor_memory.read_qa(vault)}

    @router.get("/mentor/pending")
    def mentor_pending_route():
        return {"items": mentor_pending.list_pending(vault),
                "count": mentor_pending.count_pending(vault)}

    @router.get("/mentor/threads")
    def mentor_threads(limit: int = Query(default=30, ge=1, le=200)):
        if role_service is None:
            return {"items": [], "legacy": []}
        return {
            "items": role_service.threads(limit=limit, role_id="mentor"),
            "legacy": role_service.threads(limit=limit, role_id="yiling"),
        }

    # ----- 八字系统 -------------------------------------------------------

    @router.get("/self/bazi/overview")
    def bazi_overview():
        return _guard(lambda: self_bazi.overview(vault))

    @router.get("/self/bazi/profile")
    def bazi_profile():
        return _guard(lambda: self_bazi.profile_view(vault))

    @router.put("/self/bazi/profile")
    def bazi_profile_write(payload: BaziProfilePayload):
        return _guard(lambda: self_bazi.write_profile(
            vault, payload.model_dump(), read_only=read_only))

    @router.post("/self/bazi/chart")
    def bazi_chart(payload: BaziProfilePayload):
        return _guard(lambda: self_bazi.chart_from_payload(payload.model_dump()))

    @router.post("/self/bazi/generate")
    def bazi_generate(payload: BaziGeneratePayload):
        if payload.confirm is not True:
            raise HTTPException(422, "请明确确认生成八字解读")
        return _guard(lambda: self_bazi.generate_reading(
            role_service, vault, topic=payload.topic, read_only=read_only))

    @router.get("/self/bazi/readings")
    def bazi_readings():
        return {"items": self_bazi.list_readings(vault),
                "topics": list(self_bazi.TOPICS)}

    @router.get("/self/bazi/readings/{name}")
    def bazi_reading(name: str):
        return _guard(lambda: self_bazi.read_reading(vault, name))

    @router.delete("/self/bazi/readings/{name}")
    def bazi_reading_delete(name: str, confirm: bool = Query(default=False)):
        if confirm is not True:
            raise HTTPException(422, "请明确确认删除这篇解读")
        return _guard(lambda: self_bazi.delete_reading(
            vault, name, read_only=read_only))

    return router


__all__ = ["create_self_router"]
