"""自定义人生路本子（2026-10-30）。

本子 = `00_人生路/<本子名>/` 文件夹；不另存一份元数据，Obsidian 里看到的目录
就是唯一事实。创建只建空文件夹并记一条 `notebook_created` 审计；笔记仍然通过
`/api/writing/notes` 写入（chapter 使用 `custom:<本子名>`）。

只允许人生路；知识库本子仍是固定章节。删除/改名本轮不做，避免误删目录。
"""
from __future__ import annotations

import stat
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import pillar_notes, vault_layout
from .db import Database
from .vault_connection import VaultReadOnlyError, ensure_vault_writable


class NotebookCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pillar: Literal["life"] = "life"
    name: str = Field(min_length=1, max_length=vault_layout.CUSTOM_NOTEBOOK_MAX_NAME)


def _is_reparse(info: Any) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def create_notebook(database: Database, vault_dir: Path | str, name: str, *,
                    read_only: bool = False) -> dict[str, Any]:
    try:
        clean = vault_layout.validate_notebook_name(name)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    try:
        ensure_vault_writable(read_only, "新建本子")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error

    relative = vault_layout.custom_notebook_path(clean)
    vault = Path(vault_dir).resolve()
    target = vault / relative
    try:
        if target.exists() or target.is_symlink():
            raise HTTPException(409, "这个本子已经存在")
        target.parent.mkdir(parents=True, exist_ok=True)
        # 再确认一次父目录/目标是普通目录，不跟随符号链接写出库外。
        info = target.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            raise HTTPException(409, "人生路目录不可写")
        target.mkdir()
        info = target.lstat()
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            raise HTTPException(409, "无法创建本子文件夹")
    except FileExistsError as error:
        raise HTTPException(409, "这个本子已经存在") from error
    except HTTPException:
        raise
    except OSError as error:
        raise HTTPException(500, f"创建本子失败：{error}") from error

    database.audit("notebook_created", {
        "pillar": "life", "name": clean, "path": relative,
    })
    return {
        "key": vault_layout.custom_notebook_key(clean),
        "title": clean,
        "path": relative,
        "pillar": "life",
        "count": 0,
        "latest": None,
        "custom": True,
    }


def list_notebooks(vault_dir: Path | str) -> dict[str, Any]:
    notes = pillar_notes.list_notes(vault_dir, pillar="life", limit=500)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        name = vault_layout.custom_notebook_name_from_relpath(str(note.get("path") or ""))
        if name:
            grouped.setdefault(name, []).append(note)
    items = []
    for name in vault_layout.list_custom_notebooks(vault_dir):
        mine = grouped.get(name, [])
        items.append({
            "key": vault_layout.custom_notebook_key(name),
            "title": name,
            "path": vault_layout.custom_notebook_path(name),
            "pillar": "life",
            "count": len(mine),
            "latest": mine[0] if mine else None,
            "custom": True,
        })
    return {"pillar": "life", "items": items}


def create_notebook_router(database: Database, vault_dir: Path | str, *,
                           read_only: bool = False, prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["notebooks"])

    @router.get("/notebooks")
    def notebooks(pillar: str = Query(default="life")):
        if pillar != "life":
            raise HTTPException(422, "目前只有人生路支持自定义本子")
        return list_notebooks(vault_dir)

    @router.post("/notebooks")
    def create(payload: NotebookCreateRequest):
        if payload.pillar != "life":
            raise HTTPException(422, "目前只有人生路支持自定义本子")
        return create_notebook(database, vault_dir, payload.name, read_only=read_only)

    return router


__all__ = ["NotebookCreateRequest", "create_notebook", "list_notebooks",
           "create_notebook_router"]
