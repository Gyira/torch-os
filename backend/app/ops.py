"""生活运营接口：体重/账目月度表只读（2026-10-26 起写入在 Obsidian）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query

from . import structured_notes


def create_ops_router(vault_dir: Path | str, *, read_only: bool = False,
                      prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=f"{prefix}/ops", tags=["ops"])

    @router.get("/{dataset}/months/{month}")
    def read_month(dataset: str, month: str):
        return structured_notes.read_month(vault_dir, dataset, month)

    @router.get("/{dataset}/summary")
    def read_summary(dataset: str, months: int = Query(default=12, ge=1, le=60)):
        return structured_notes.summary(vault_dir, dataset, months)

    return router


__all__ = ["create_ops_router"]
