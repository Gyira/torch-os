"""自定义本子（2026-10-30）。

本子 = 某个库根目录下的一个文件夹（人生路 `00_人生路/<名字>/`，知识库
`03_知识库/<名字>/`）；不另存元数据，Obsidian 里看到的目录就是唯一事实。
创建只建空文件夹并记一条 `notebook_created` 审计；笔记仍通过
`/api/writing/notes` 写入（人生路 `custom:<名字>`，知识库 `custom:knowledge:<名字>`）。

删除本子走软删除：整个文件夹移进 `<库>/.torch-os/本子回收站/<批次>/`，保留
manifest 供手动找回。固定章节（如「收件箱」「主题笔记」）不是自定义本子，不能删除。
"""
from __future__ import annotations

import json
import os
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import pillar_notes, vault_layout
from .db import Database
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

NOTEBOOK_TRASH_DIR = ".torch-os/本子回收站"
NotebookPillar = Literal["life", "knowledge"]


class NotebookCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pillar: NotebookPillar = "life"
    name: str = Field(min_length=1, max_length=vault_layout.CUSTOM_NOTEBOOK_MAX_NAME)


class NotebookDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pillar: NotebookPillar = "life"
    key: str = Field(min_length=1, max_length=200)
    confirm: bool = False


def _is_reparse(info: Any) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _require_pillar(pillar: str) -> str:
    value = str(pillar or "").strip()
    if value not in vault_layout.CUSTOM_NOTEBOOK_PILLARS:
        raise HTTPException(422, "只有人生路和知识库支持自定义本子")
    return value


def create_notebook(database: Database, vault_dir: Path | str, name: str, *,
                    pillar: str = "life", read_only: bool = False) -> dict[str, Any]:
    key_pillar = _require_pillar(pillar)
    try:
        clean = vault_layout.validate_notebook_name(name)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    try:
        ensure_vault_writable(read_only, "新建本子")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error

    relative = vault_layout.custom_notebook_path(clean, key_pillar)
    vault = Path(vault_dir).resolve()
    target = vault / relative
    try:
        if target.exists() or target.is_symlink():
            raise HTTPException(409, "这个本子已经存在")
        target.parent.mkdir(parents=True, exist_ok=True)
        # 再确认一次父目录/目标是普通目录，不跟随符号链接写出库外。
        info = target.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            raise HTTPException(409, "库目录不可写")
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
        "pillar": key_pillar, "name": clean, "path": relative,
    })
    return {
        "key": vault_layout.custom_notebook_key(clean, key_pillar),
        "title": clean,
        "path": relative,
        "pillar": key_pillar,
        "count": 0,
        "latest": None,
        "custom": True,
    }


def list_notebooks(vault_dir: Path | str, pillar: str = "life") -> dict[str, Any]:
    key_pillar = _require_pillar(pillar)
    notes = pillar_notes.list_notes(vault_dir, pillar=key_pillar, limit=500)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        name = vault_layout.custom_notebook_name_from_relpath(str(note.get("path") or ""))
        if name:
            grouped.setdefault(name, []).append(note)
    items = []
    for name in vault_layout.list_custom_notebooks(vault_dir, key_pillar):
        mine = grouped.get(name, [])
        items.append({
            "key": vault_layout.custom_notebook_key(name, key_pillar),
            "title": name,
            "path": vault_layout.custom_notebook_path(name, key_pillar),
            "pillar": key_pillar,
            "count": len(mine),
            "latest": mine[0] if mine else None,
            "custom": True,
        })
    return {"pillar": key_pillar, "items": items}


def _count_markdown(root: Path) -> int:
    total = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [
            name for name in dirs
            if not (Path(current) / name).is_symlink()
        ]
        total += sum(1 for name in files if name.lower().endswith(".md"))
    return total


def delete_notebook(database: Database, vault_dir: Path | str, *, pillar: str,
                    key: str, confirm: bool = False,
                    read_only: bool = False) -> dict[str, Any]:
    """软删除一个自定义本子文件夹；固定章节和非本子路径一律拒绝。"""
    key_pillar = _require_pillar(pillar)
    if not confirm:
        raise HTTPException(422, "删除本子需要二次确认")
    key_pillar_in_key = vault_layout.custom_notebook_pillar(key)
    name = vault_layout.custom_notebook_name(key)
    if not name or key_pillar_in_key != key_pillar:
        raise HTTPException(422, "只能删除当前库的自定义本子")
    try:
        ensure_vault_writable(read_only, "删除本子")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error

    vault = Path(vault_dir).resolve()
    relative = vault_layout.custom_notebook_path(name, key_pillar)
    target = vault / relative
    try:
        info = target.lstat()
    except FileNotFoundError as error:
        raise HTTPException(404, "这个本子已经不存在") from error
    except OSError as error:
        raise HTTPException(409, f"本子文件夹不可读：{error}") from error
    if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
        raise HTTPException(409, "本子文件夹不是普通目录，已停止删除")
    try:
        parent = target.parent.resolve()
        expected_parent = (vault / vault_layout.PILLAR_DIRS[key_pillar]).resolve()
        if parent != expected_parent or not target.resolve().is_relative_to(vault):
            raise HTTPException(409, "本子目录不在库内，已停止删除")
        note_count = _count_markdown(target)
    except HTTPException:
        raise
    except OSError as error:
        raise HTTPException(409, f"本子文件夹不可读：{error}") from error

    trash_root = vault / NOTEBOOK_TRASH_DIR
    try:
        trash_root.mkdir(parents=True, exist_ok=True)
        if not trash_root.resolve().is_relative_to(vault):
            raise HTTPException(409, "本子回收站目录不在库内，已停止删除")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        batch = trash_root / f"{stamp}-{secrets.token_hex(4)}"
        trash_target = batch / "notebooks" / key_pillar / name
        trash_target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(target, trash_target)
    except HTTPException:
        raise
    except OSError as error:
        raise HTTPException(409, f"移入本子回收站失败：{error}") from error

    trash_path = trash_target.relative_to(vault).as_posix()
    batch_path = batch.relative_to(vault).as_posix()
    manifest = {
        "schema": 1,
        "deleted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pillar": key_pillar,
        "name": name,
        "key": key,
        "original_path": relative,
        "trash_path": trash_path,
        "note_count": note_count,
    }
    try:
        (batch / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n")
    except OSError:
        # 文件夹已经安全移进回收站；manifest 只用于找回提示，不因它失败而谎报删除失败。
        pass
    database.audit("notebook_deleted", {
        "pillar": key_pillar, "name": name, "path": relative,
        "trash": trash_path, "count": note_count,
    })
    return {
        "deleted": True,
        "key": key,
        "title": name,
        "path": relative,
        "trash_path": trash_path,
        "trash_batch": batch_path,
        "count": note_count,
    }


def create_notebook_router(database: Database, vault_dir: Path | str, *,
                           read_only: bool = False, prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["notebooks"])

    @router.get("/notebooks")
    def notebooks(pillar: str = Query(default="life")):
        return list_notebooks(vault_dir, pillar)

    @router.post("/notebooks")
    def create(payload: NotebookCreateRequest):
        return create_notebook(database, vault_dir, payload.name,
                               pillar=payload.pillar, read_only=read_only)

    @router.post("/notebooks/delete")
    def delete(payload: NotebookDeleteRequest):
        return delete_notebook(
            database, vault_dir, pillar=payload.pillar, key=payload.key,
            confirm=payload.confirm, read_only=read_only,
        )

    return router


__all__ = ["NOTEBOOK_TRASH_DIR", "NotebookCreateRequest", "NotebookDeleteRequest",
           "create_notebook", "list_notebooks", "delete_notebook",
           "create_notebook_router"]
