"""设置页「库结构与迁移」接口（2026-10-21）。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.concurrency import run_in_threadpool

from .db import Database
from .migration_apply import apply_migration, migration_status, rollback_migration
from .migration_plan import plan_markdown, plan_migration
from .vault_connection import ensure_vault_writable

MAX_REASON = 500


class MigrationApply(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    plan_hash: str = Field(min_length=1, max_length=32)
    confirm: StrictBool = False


class MigrationRollback(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1, max_length=64)
    confirm: StrictBool = False


def create_migration_router(database: Database, vault_dir: Path | str,
                            data_lock: asyncio.Lock, *, read_only: bool = False,
                            prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=f"{prefix}/migration", tags=["migration"])

    @router.get("/preview")
    async def preview():
        plan = await run_in_threadpool(plan_migration, vault_dir)
        payload = plan.as_dict()
        payload["markdown"] = plan_markdown(plan)
        return payload

    @router.get("/status")
    async def status():
        return await run_in_threadpool(migration_status, vault_dir)

    @router.post("/apply")
    async def apply(payload: MigrationApply):
        if read_only:
            raise HTTPException(409, "当前是只读模式，已拒绝迁移")
        ensure_vault_writable(read_only, "迁移库结构")
        if not payload.confirm:
            raise HTTPException(422, "迁移需要显式确认")
        async with data_lock:
            try:
                result = await run_in_threadpool(
                    apply_migration, vault_dir, database,
                    expected_hash=payload.plan_hash, confirm=True,
                )
            except (ValueError, RuntimeError, FileNotFoundError) as error:
                raise HTTPException(409, str(error)) from None
        return result

    @router.post("/rollback")
    async def rollback(payload: MigrationRollback):
        if read_only:
            raise HTTPException(409, "当前是只读模式，已拒绝回滚")
        ensure_vault_writable(read_only, "回滚库结构")
        if not payload.confirm:
            raise HTTPException(422, "回滚需要显式确认")
        async with data_lock:
            try:
                result = await run_in_threadpool(
                    rollback_migration, vault_dir, payload.run_id, database)
            except (RuntimeError, FileNotFoundError) as error:
                raise HTTPException(409, str(error)) from None
        return result

    return router


__all__ = ["create_migration_router", "MigrationApply", "MigrationRollback"]
