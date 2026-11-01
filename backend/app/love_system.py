"""恋爱系统总状态（2026-11-01）：设置抽屉与首屏使用的轻量只读信息。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from .love_layout import LovePaths
from .vault_connection import read_only_enabled


def create_system_router(paths: LovePaths, *, migration: dict[str, Any] | None = None,
                         role_service: Any = None,
                         prefix: str = "/api/love") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["love-system"])

    @router.get("/status")
    def status():
        readiness = {"available": False, "reason": "AI 角色服务未接入"}
        if role_service is not None:
            try:
                available, reason = role_service.readiness()
                readiness = {"available": bool(available), "reason": reason or ""}
            except Exception as error:  # noqa: BLE001 - 状态检查不能阻断开页
                readiness = {"available": False, "reason": str(error)[:500]}
        return {
            "paths": paths.as_dict(),
            "migration": migration or {},
            "read_only": read_only_enabled(),
            "ai": readiness,
            "exists": {
                "memory": paths.memory.is_dir(),
                "chat": paths.chat_db.is_file(),
                "schedule": paths.schedule_excel.is_dir(),
                "cycle": paths.cycle_db.is_file(),
                "gallery": paths.gallery_db.is_file(),
                "bazi": paths.bazi_db.is_file(),
                "master": paths.master_db.is_file(),
            },
        }

    return router


__all__ = ["create_system_router"]
