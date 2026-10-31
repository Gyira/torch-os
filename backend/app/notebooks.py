"""自定义本子（2026-10-30）。

本子 = 某个库根目录下的一个文件夹（人生路 `00_人生路/<名字>/`，知识库
`03_知识库/<名字>/`）；不另存元数据，Obsidian 里看到的目录就是唯一事实。
创建只建空文件夹并记一条 `notebook_created` 审计；笔记仍通过
`/api/writing/notes` 写入（人生路 `custom:<名字>`，知识库 `custom:knowledge:<名字>`）。

删除本子走软删除：自建本子整夹移进 `<库>/.torch-os/本子回收站/<批次>/`；内置本子
按章节集合整体移走并记录隐藏标记。人生路只保留「随笔」、知识库只保留「收件箱」、
生活运营只保留「待办事项」这些默认内置本子，其他内置本子都可删除。

生活运营的「待办事项」是受保护的内置本子，只展示不创建/不删除；自定义本子仍只在
人生路与知识库开放。
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


def _notebook_trash_root(vault: Path) -> Path:
    root = vault / NOTEBOOK_TRASH_DIR
    root.mkdir(parents=True, exist_ok=True)
    if not root.resolve().is_relative_to(vault):
        raise HTTPException(409, "本子回收站目录不在库内，已停止删除")
    return root


def _new_notebook_batch(vault: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    batch = _notebook_trash_root(vault) / f"{stamp}-{secrets.token_hex(4)}"
    batch.mkdir(parents=True, exist_ok=False)
    return batch


def _rollback_moves(moved: list[tuple[Path, Path]]) -> None:
    for original, moved_target in reversed(moved):
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            os.replace(moved_target, original)
        except OSError:
            # 已经尽量回滚；保留可读的失败原文在回收批次里。
            pass


def _delete_custom_notebook(database: Database, vault: Path, pillar: str,
                            key: str) -> dict[str, Any]:
    name = vault_layout.custom_notebook_name(key)
    if not name:
        raise HTTPException(422, "只能删除当前库的本子")
    relative = vault_layout.custom_notebook_path(name, pillar)
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
        expected_parent = (vault / vault_layout.PILLAR_DIRS[pillar]).resolve()
        if parent != expected_parent or not target.resolve().is_relative_to(vault):
            raise HTTPException(409, "本子目录不在库内，已停止删除")
        note_count = _count_markdown(target)
    except HTTPException:
        raise
    except OSError as error:
        raise HTTPException(409, f"本子文件夹不可读：{error}") from error

    try:
        batch = _new_notebook_batch(vault)
        trash_target = batch / "notebooks" / pillar / name
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
        "pillar": pillar,
        "name": name,
        "key": key,
        "builtin": False,
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
        "pillar": pillar, "name": name, "path": relative,
        "trash": trash_path, "count": note_count, "builtin": False,
    })
    return {
        "deleted": True,
        "key": key,
        "title": name,
        "builtin": False,
        "path": relative,
        "paths": [relative],
        "trash_path": trash_path,
        "trash_batch": batch_path,
        "count": note_count,
    }


def _delete_builtin_notebook(database: Database, vault: Path, pillar: str,
                             key: str) -> dict[str, Any]:
    title = vault_layout.builtin_notebook_title(pillar, key)
    chapters = vault_layout.builtin_notebook_chapters(pillar, key)
    if not title or not chapters:
        raise HTTPException(422, "只能删除人生路/知识库的内置本子或自建本子")
    sources: list[tuple[str, Path, str]] = []
    note_count = 0
    for chapter in chapters:
        relative = vault_layout.chapter_dir(chapter)
        if not relative:
            continue
        source = vault / relative
        if not source.exists() and not source.is_symlink():
            continue
        try:
            info = source.lstat()
        except OSError as error:
            raise HTTPException(409, f"本子文件夹不可读：{error}") from error
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            raise HTTPException(409, "本子文件夹不是普通目录，已停止删除")
        if not source.resolve().is_relative_to(vault):
            raise HTTPException(409, "本子目录不在库内，已停止删除")
        note_count += _count_markdown(source)
        sources.append((chapter, source, relative))

    try:
        batch = _new_notebook_batch(vault)
    except HTTPException:
        raise
    except OSError as error:
        raise HTTPException(409, f"建不了本子回收站：{error}") from error

    moved: list[tuple[Path, Path]] = []
    trash_paths: list[str] = []
    try:
        for _chapter, source, relative in sources:
            inner = Path(*relative.split("/")[1:])
            target = batch / "notebooks" / pillar / key / inner
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            moved.append((source, target))
            trash_paths.append(target.relative_to(vault).as_posix())
    except OSError as error:
        _rollback_moves(moved)
        raise HTTPException(409, f"移入本子回收站失败：{error}") from error

    try:
        vault_layout.mark_notebook_deleted(vault, pillar, key)
    except (OSError, ValueError) as error:
        _rollback_moves(moved)
        raise HTTPException(409, f"记录本子删除状态失败：{error}") from error

    batch_path = batch.relative_to(vault).as_posix()
    trash_path = (batch / "notebooks" / pillar / key).relative_to(vault).as_posix()
    manifest = {
        "schema": 1,
        "deleted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pillar": pillar,
        "name": title,
        "key": key,
        "builtin": True,
        "chapters": list(chapters),
        "original_paths": [relative for _chapter, _source, relative in sources],
        "trash_paths": trash_paths,
        "trash_path": trash_path,
        "note_count": note_count,
    }
    try:
        (batch / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n")
    except OSError:
        pass
    database.audit("notebook_deleted", {
        "pillar": pillar, "name": title, "key": key,
        "path": ",".join(relative for _chapter, _source, relative in sources),
        "trash": trash_path, "count": note_count, "builtin": True,
    })
    return {
        "deleted": True,
        "key": key,
        "title": title,
        "builtin": True,
        "path": sources[0][2] if sources else "",
        "paths": [relative for _chapter, _source, relative in sources],
        "trash_path": trash_path,
        "trash_batch": batch_path,
        "count": note_count,
    }


def delete_notebook(database: Database, vault_dir: Path | str, *, pillar: str,
                    key: str, confirm: bool = False,
                    read_only: bool = False) -> dict[str, Any]:
    """软删除内置或自建本子；随笔/收件箱和任务/习惯等非本子路径拒绝删除。"""
    key_pillar = _require_pillar(pillar)
    if not confirm:
        raise HTTPException(422, "删除本子需要二次确认")
    try:
        ensure_vault_writable(read_only, "删除本子")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error

    notebook_key = str(key or "").strip()
    vault = Path(vault_dir).resolve()
    if notebook_key in vault_layout.BUILTIN_NOTEBOOKS.get(key_pillar, {}):
        if vault_layout.is_protected_notebook(key_pillar, notebook_key):
            title = vault_layout.builtin_notebook_title(key_pillar, notebook_key)
            raise HTTPException(422, f"「{title}」是默认本子，不能删除")
        return _delete_builtin_notebook(database, vault, key_pillar, notebook_key)

    custom_pillar = vault_layout.custom_notebook_pillar(notebook_key)
    if custom_pillar == key_pillar:
        return _delete_custom_notebook(database, vault, key_pillar, notebook_key)

    raise HTTPException(422, "只能删除当前库的本子")


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
