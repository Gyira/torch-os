"""生活运营接口：体重/账目月度表 + 习惯 Markdown 镜像（2026-10-21）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import structured_notes
from .vault_connection import ensure_vault_writable


class OpsRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    row_id: str = Field(default="", max_length=24)
    date: str = Field(min_length=1, max_length=10)
    weight_kg: str = Field(default="", max_length=32)
    sleep_minutes: str = Field(default="", max_length=32)
    exercise_minutes: str = Field(default="", max_length=32)
    note: str = Field(default="", max_length=500)
    kind: str = Field(default="", max_length=16)
    amount: str = Field(default="", max_length=32)
    category: str = Field(default="", max_length=80)
    account: str = Field(default="", max_length=80)


def create_ops_router(vault_dir: Path | str, *, read_only: bool = False,
                      prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=f"{prefix}/ops", tags=["ops"])

    @router.get("/{dataset}/months/{month}")
    def read_month(dataset: str, month: str):
        return structured_notes.read_month(vault_dir, dataset, month)

    @router.get("/{dataset}/summary")
    def read_summary(dataset: str, months: int = Query(default=12, ge=1, le=60)):
        return structured_notes.summary(vault_dir, dataset, months)

    @router.post("/{dataset}/months/{month}/rows")
    def create_row(dataset: str, month: str, payload: OpsRow):
        if read_only:
            raise HTTPException(409, "当前是只读模式")
        ensure_vault_writable(read_only, "记录生活数据")
        return structured_notes.upsert_row(
            vault_dir, dataset, month, payload.model_dump(exclude_none=True))

    @router.post("/{dataset}/months/{month}/rows/{row_id}")
    def update_row(dataset: str, month: str, row_id: str, payload: OpsRow):
        if read_only:
            raise HTTPException(409, "当前是只读模式")
        ensure_vault_writable(read_only, "更新生活数据")
        return structured_notes.upsert_row(
            vault_dir, dataset, month, payload.model_dump(exclude_none=True), row_id=row_id)

    @router.delete("/{dataset}/months/{month}/rows/{row_id}")
    def delete_row(dataset: str, month: str, row_id: str):
        if read_only:
            raise HTTPException(409, "当前是只读模式")
        ensure_vault_writable(read_only, "删除生活数据")
        return structured_notes.delete_row(vault_dir, dataset, month, row_id)

    @router.post("/{dataset}/months/{month}/rows/{row_id}/restore")
    def restore_row(dataset: str, month: str, row_id: str, payload: OpsRow,
                    position: int | None = Query(default=None, ge=0)):
        if read_only:
            raise HTTPException(409, "当前是只读模式")
        ensure_vault_writable(read_only, "恢复生活数据")
        return structured_notes.restore_row(
            vault_dir, dataset, month, payload.model_dump(exclude_none=True),
            row_id, position)

    return router


__all__ = ["create_ops_router"]
