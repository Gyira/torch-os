"""自定义本子（2026-10-30，2026-11-22 支持子本子）。

本子 = 某个库根目录下的一个文件夹（人生路 `人生路/<名字>/`，知识库
`知识库/<名字>/`）；子本子 = 本子文件夹下的子文件夹，路径用 `/` 分层
（`人生路/<父>/<子>/`，key 为 `custom:<父>/<子>`）。不另存元数据，
Obsidian 里看到的目录就是唯一事实。创建只建空文件夹并记一条
`notebook_created` 审计；笔记仍通过 `/api/writing/notes` 写入。

删除本子走软删除：自建本子整夹移进 `<库>/.torch-os/本子回收站/<批次>/`；内置本子
按章节集合整体移走并记录隐藏标记。人生路只保留「随笔」、知识库只保留「收件箱」、
生活运营只保留「待办事项」这些默认内置本子，其他内置本子都可删除。

生活运营的「待办事项」是受保护的内置本子，只展示不创建/不删除；自定义本子仍只在
人生路与知识库开放。
"""
from __future__ import annotations
from .storage_layout import is_workspace_path, workspace_relative
from .storage_layout import workspace_path

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import pillar_notes, vault_layout
from .library_management import NotebookTitleRequest, LibraryNoteRequest, set_notebook_title, manage_note, notebook_titles, clear_notebook_title
from .db import Database
from .memory_store import operation_lock
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

NOTEBOOK_TRASH_DIR = ".torch-os/本子回收站"
NotebookPillar = Literal["life", "knowledge", "love"]


class NotebookCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pillar: NotebookPillar = "life"
    name: str = Field(min_length=1, max_length=vault_layout.CUSTOM_NOTEBOOK_MAX_NAME)
    # 子本子：父本子的相对路径（如 "读书/小说"）；留空表示在库根下新建顶层本子。
    parent: str = Field(default="", max_length=240)
    # 内置本子（日记/随笔/收件箱…）的子本子：所属固定章节 key；为空表示自建本子。
    parent_chapter: str = Field(default="", max_length=80)


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
        raise HTTPException(422, "只有人生路、知识库和恋爱记忆支持自定义本子")
    return value


def _assert_directory_chain(vault: Path, path: Path) -> None:
    """本子目录及其上级都必须是库内普通目录，不跟随链接写出库外。"""
    try:
        relative = path.relative_to(vault)
    except ValueError as error:
        raise HTTPException(409, "库目录不可写") from error
    current = vault
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError as error:
            raise HTTPException(409, "库目录不可写") from error
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            raise HTTPException(409, "库目录不可写")


def _note_notebook_name(note: dict[str, Any]) -> str:
    return vault_layout.custom_notebook_name_from_relpath(str(note.get("path") or ""))


def _custom_notebook_item(vault_dir: Path | str, pillar: str, name: str,
                          notes: list[dict[str, Any]]) -> dict[str, Any]:
    """一个本子（含子本子）的列表项；计数包含它下面所有层级的笔记。"""
    prefix = f"{name}/"
    mine = [
        note for note in notes
        if (note_name := _note_notebook_name(note))
        and (note_name == name or note_name.startswith(prefix))
    ]
    parent = vault_layout.notebook_parent_name(name)
    key = vault_layout.custom_notebook_key(name, pillar)
    return {
        "key": key,
        "title": vault_layout.notebook_leaf_name(name),
        "name": name,
        "path": vault_layout.custom_notebook_path(name, pillar),
        "parent": (vault_layout.custom_notebook_key(parent, pillar)
                   if parent else ""),
        "depth": name.count("/"),
        "pillar": pillar,
        "count": len(mine),
        "latest": mine[0] if mine else None,
        "custom": True,
        "protected": vault_layout.is_protected_notebook(pillar, key),
    }


def _create_sub_notebook(database: Database, vault: Path, pillar: str,
                         clean: str, parent_name: str, chapter: str, *,
                         read_only: bool, audit: bool) -> dict[str, Any]:
    """在内置本子的章节目录下创建已登记的子本子。"""
    if chapter not in vault_layout.SUB_NOTEBOOK_CHAPTERS:
        raise HTTPException(422, "这个本子不支持子本子")
    if vault_layout.CHAPTER_BY_KEY[chapter][1] != pillar:
        raise HTTPException(422, "子本子必须和本子在同一个库里")
    if vault_layout.is_chapter_hidden(vault, chapter):
        raise HTTPException(422, "这个本子已被删除，不能建子本子")
    full_name = f"{parent_name}/{clean}" if parent_name else clean
    try:
        vault_layout.validate_notebook_path(full_name)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if parent_name and not vault_layout.sub_notebook_exists(vault, chapter, parent_name):
        raise HTTPException(422, "父本子不存在，请先创建父本子")
    chapter_relative = vault_layout.chapter_dir(chapter)
    chapter_path = workspace_path(vault, chapter_relative)
    try:
        chapter_path.mkdir(parents=True, exist_ok=True)
        _assert_directory_chain(vault, chapter_path)
    except FileExistsError as error:
        raise HTTPException(409, "这个本子的文件夹被同名文件占用") from error
    except HTTPException:
        raise
    except OSError as error:
        raise HTTPException(500, f"无法准备本子文件夹：{error}") from error
    parent_relative = (f"{chapter_relative}/{parent_name}"
                       if parent_name else chapter_relative)
    relative = vault_layout.sub_notebook_path(full_name, chapter)
    target = workspace_path(vault, relative)
    try:
        if target.exists() or target.is_symlink():
            raise HTTPException(409, "这个子本子已经存在")
        parent_target = workspace_path(vault, parent_relative)
        parent_target.mkdir(parents=True, exist_ok=True)
        _assert_directory_chain(vault, parent_target)
        target.mkdir()
        _assert_directory_chain(vault, target)
    except FileExistsError as error:
        raise HTTPException(409, "这个子本子已经存在") from error
    except HTTPException:
        raise
    except OSError as error:
        raise HTTPException(500, f"创建子本子失败：{error}") from error

    key = vault_layout.sub_notebook_key(chapter, full_name)
    try:
        vault_layout.register_notebook(vault, key)
        clear_notebook_title(vault, pillar, key)
    except Exception as error:
        try:
            vault_layout.unregister_notebook(vault, key, include_descendants=True)
        except Exception:
            pass
        target.rmdir()
        raise HTTPException(500, f"创建子本子失败：{error}") from error
    if audit:
        database.audit("notebook_created", {
            "pillar": pillar, "name": full_name, "parent": parent_name,
            "parent_chapter": chapter, "path": relative, "sub": True,
        })
    return {
        "key": key,
        "title": clean,
        "name": full_name,
        "parent": (vault_layout.sub_notebook_key(chapter, parent_name)
                   if parent_name else ""),
        "parent_chapter": chapter,
        "depth": full_name.count("/"),
        "path": relative,
        "pillar": pillar,
        "count": 0,
        "latest": None,
        "custom": False,
        "sub": True,
    }


def create_notebook(database: Database, vault_dir: Path | str, name: str, *,
                    pillar: str = "life", parent: str = "",
                    parent_chapter: str = "", read_only: bool = False,
                    audit: bool = True) -> dict[str, Any]:
    key_pillar = _require_pillar(pillar)
    try:
        clean = vault_layout.validate_notebook_name(name)
        parent_name = (vault_layout.validate_notebook_path(parent)
                       if str(parent or "").strip() else "")
        full_name = f"{parent_name}/{clean}" if parent_name else clean
        vault_layout.validate_notebook_path(full_name)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    try:
        ensure_vault_writable(read_only, "新建本子")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error

    vault = Path(vault_dir).resolve()
    chapter = str(parent_chapter or "").strip()
    if chapter:
        return _create_sub_notebook(
            database, vault, key_pillar, clean, parent_name, chapter,
            read_only=read_only, audit=audit)
    if parent_name and not vault_layout.custom_notebook_exists(
            vault, key_pillar, parent_name):
        raise HTTPException(422, "父本子不存在，请先创建父本子")
    root_relative = vault_layout.notebook_root(key_pillar)
    parent_relative = f"{root_relative}/{parent_name}" if parent_name else root_relative
    relative = vault_layout.custom_notebook_path(full_name, key_pillar)
    target = workspace_path(vault, relative)
    try:
        if target.exists() or target.is_symlink():
            raise HTTPException(409, "这个本子已经存在")
        parent_target = workspace_path(vault, parent_relative)
        parent_target.mkdir(parents=True, exist_ok=True)
        _assert_directory_chain(vault, parent_target)
        target.mkdir()
        _assert_directory_chain(vault, target)
    except FileExistsError as error:
        raise HTTPException(409, "这个本子已经存在") from error
    except HTTPException:
        raise
    except OSError as error:
        raise HTTPException(500, f"创建本子失败：{error}") from error

    key = vault_layout.custom_notebook_key(full_name, key_pillar)
    try:
        vault_layout.register_notebook(vault, key)
        if key_pillar in {"life", "knowledge"}:
            clear_notebook_title(vault, key_pillar, key)
    except Exception as error:
        try:
            vault_layout.unregister_notebook(vault, key, include_descendants=True)
        except Exception:
            pass
        target.rmdir()  # Only the empty folder this request just created.
        raise HTTPException(500, f"创建本子失败：{error}") from error
    if audit:
        database.audit("notebook_created", {
            "pillar": key_pillar, "name": full_name, "parent": parent_name,
            "path": relative,
        })
    return {
        "key": key,
        "title": clean,
        "name": full_name,
        "parent": (vault_layout.custom_notebook_key(parent_name, key_pillar)
                   if parent_name else ""),
        "depth": full_name.count("/"),
        "path": relative,
        "pillar": key_pillar,
        "count": 0,
        "latest": None,
        "custom": True,
    }


def list_notebooks(vault_dir: Path | str, pillar: str = "life") -> dict[str, Any]:
    key_pillar = _require_pillar(pillar)
    notes = pillar_notes.list_notes(vault_dir, pillar=key_pillar, limit=500)
    items = [
        _custom_notebook_item(vault_dir, key_pillar, name, notes)
        for name in vault_layout.list_all_custom_notebooks(vault_dir, key_pillar)
    ]
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
    root = workspace_path(vault, NOTEBOOK_TRASH_DIR)
    root.mkdir(parents=True, exist_ok=True)
    if not is_workspace_path(vault, root.resolve()):
        raise HTTPException(409, "本子回收站目录不在库内，已停止删除")
    return root


def _new_notebook_batch(vault: Path) -> Path:
    batch = _notebook_trash_root(vault) / vault_layout.new_recycle_batch_name()
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
    try:
        name = vault_layout.validate_notebook_path(name)
    except ValueError as error:
        raise HTTPException(422, "只能删除当前库的本子") from error
    display_title = (notebook_titles(vault, pillar).get(key)
                     or vault_layout.notebook_leaf_name(name))
    relative = vault_layout.custom_notebook_path(name, pillar)
    target = workspace_path(vault, relative)
    try:
        target.lstat()
    except FileNotFoundError as error:
        raise HTTPException(404, "这个本子已经不存在") from error
    except OSError as error:
        raise HTTPException(409, f"本子文件夹不可读：{error}") from error
    _assert_directory_chain(vault, target)
    try:
        if not is_workspace_path(vault, target.resolve()):
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

    trash_path = workspace_relative(vault, trash_target).as_posix()
    batch_path = workspace_relative(vault, batch).as_posix()
    manifest = {
        "schema": 1,
        "deleted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pillar": pillar,
        "name": name,
        "key": key,
        "builtin": False,
        "original_path": relative,
        "display_title": display_title,
        "trash_path": trash_path,
        "note_count": note_count,
    }
    try:
        (batch / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n")
    except OSError as error:
        _rollback_moves([(target, trash_target)])
        raise HTTPException(409, "回收清单保存失败，已尝试将本子恢复原位") from error
    database.audit("notebook_deleted", {
        "pillar": pillar, "name": name, "path": relative,
        "trash": trash_path, "count": note_count, "builtin": False,
    })
    return {
        "deleted": True,
        "key": key,
        "title": display_title,
        "builtin": False,
        "path": relative,
        "paths": [relative],
        "trash_path": trash_path,
        "trash_batch": batch_path,
        "count": note_count,
    }


def _delete_sub_notebook(database: Database, vault: Path, pillar: str,
                         key: str) -> dict[str, Any]:
    """删除内置本子里的子本子：只移动这个子文件夹，并撤下登记。"""
    parts = vault_layout.sub_notebook_parts(key)
    if not parts:
        raise HTTPException(422, "只能删除当前库的子本子")
    chapter, name = parts
    if vault_layout.CHAPTER_BY_KEY[chapter][1] != pillar:
        raise HTTPException(422, "子本子和本子不在同一个库里")
    display_title = (notebook_titles(vault, pillar).get(key)
                     or vault_layout.notebook_leaf_name(name))
    relative = vault_layout.sub_notebook_path(name, chapter)
    target = workspace_path(vault, relative)
    try:
        target.lstat()
    except FileNotFoundError as error:
        raise HTTPException(404, "这个子本子已经不存在") from error
    except OSError as error:
        raise HTTPException(409, f"子本子文件夹不可读：{error}") from error
    _assert_directory_chain(vault, target)
    try:
        if not is_workspace_path(vault, target.resolve()):
            raise HTTPException(409, "子本子目录不在库内，已停止删除")
        note_count = _count_markdown(target)
    except HTTPException:
        raise
    except OSError as error:
        raise HTTPException(409, f"子本子文件夹不可读：{error}") from error

    # 回收目录用 sub-<章节> 隔离，恢复时按清单写回原路径并重新登记。
    try:
        batch = _new_notebook_batch(vault)
        trash_target = batch / "notebooks" / pillar / f"sub-{chapter}" / name
        trash_target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(target, trash_target)
    except HTTPException:
        raise
    except OSError as error:
        raise HTTPException(409, f"移入本子回收站失败：{error}") from error

    trash_path = workspace_relative(vault, trash_target).as_posix()
    batch_path = workspace_relative(vault, batch).as_posix()
    manifest = {
        "schema": 1,
        "deleted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pillar": pillar,
        "name": name,
        "key": key,
        "builtin": False,
        "sub": True,
        "chapter": chapter,
        "original_path": relative,
        "display_title": display_title,
        "trash_path": trash_path,
        "note_count": note_count,
    }
    try:
        (batch / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n")
    except OSError as error:
        _rollback_moves([(target, trash_target)])
        raise HTTPException(409, "回收清单保存失败，已尝试将子本子恢复原位") from error
    try:
        # 只撤下当前这一层：下层登记保留，整夹恢复后子本子还能回来。
        vault_layout.unregister_notebook(vault, key)
    except OSError:
        # 登记文件写不进去时，列表会因文件夹已不存在而自动隐藏；恢复时再写回。
        pass
    database.audit("notebook_deleted", {
        "pillar": pillar, "name": name, "path": relative,
        "trash": trash_path, "count": note_count, "builtin": False, "sub": True,
    })
    return {
        "deleted": True,
        "key": key,
        "title": display_title,
        "builtin": False,
        "sub": True,
        "path": relative,
        "paths": [relative],
        "trash_path": trash_path,
        "trash_batch": batch_path,
        "count": note_count,
    }


def _delete_builtin_notebook(database: Database, vault: Path, pillar: str,
                             key: str) -> dict[str, Any]:
    title = vault_layout.builtin_notebook_title(pillar, key)
    display_title = notebook_titles(vault, pillar).get(key, title)
    chapters = vault_layout.builtin_notebook_chapters(pillar, key)
    if not title or not chapters:
        raise HTTPException(422, "只能删除人生路/知识库的内置本子或自建本子")
    directories = [vault_layout.chapter_dir(chapter) for chapter in chapters]
    common = os.path.commonpath(directories).replace("\\", "/")
    # Move the containing notebook too: covers attachments and leaves no ghost folder.
    if common != vault_layout.PILLAR_DIRS[pillar]:
        directories = [common]
    sources: list[tuple[str, Path, str]] = []
    note_count = 0
    for relative in directories:
        if not relative:
            continue
        source = workspace_path(vault, relative)
        if not source.exists() and not source.is_symlink():
            continue
        try:
            info = source.lstat()
        except OSError as error:
            raise HTTPException(409, f"本子文件夹不可读：{error}") from error
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            raise HTTPException(409, "本子文件夹不是普通目录，已停止删除")
        if not is_workspace_path(vault, source.resolve()):
            raise HTTPException(409, "本子目录不在库内，已停止删除")
        note_count += _count_markdown(source)
        sources.append(("", source, relative))

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
            trash_paths.append(workspace_relative(vault, target).as_posix())
    except OSError as error:
        _rollback_moves(moved)
        raise HTTPException(409, f"移入本子回收站失败：{error}") from error

    try:
        vault_layout.mark_notebook_deleted(vault, pillar, key)
    except (OSError, ValueError) as error:
        _rollback_moves(moved)
        raise HTTPException(409, f"记录本子删除状态失败：{error}") from error

    batch_path = workspace_relative(vault, batch).as_posix()
    trash_path = workspace_relative(vault, batch / "notebooks" / pillar / key).as_posix()
    manifest = {
        "schema": 1,
        "deleted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pillar": pillar,
        "name": title,
        "key": key,
        "builtin": True,
        "chapters": list(chapters),
        "display_title": display_title,
        "original_paths": [relative for _chapter, _source, relative in sources],
        "trash_paths": trash_paths,
        "trash_path": trash_path,
        "note_count": note_count,
    }
    try:
        (batch / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n")
    except OSError as error:
        _rollback_moves(moved)
        vault_layout.mark_notebook_deleted(vault, pillar, key, deleted=False)
        raise HTTPException(409, "回收清单保存失败，已尝试恢复原本子") from error
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


def _delete_notebook(database: Database, vault_dir: Path | str, *, pillar: str,
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
    if vault_layout.is_sub_notebook_key(notebook_key):
        return _delete_sub_notebook(database, vault, key_pillar, notebook_key)
    if notebook_key in vault_layout.BUILTIN_NOTEBOOKS.get(key_pillar, {}):
        if vault_layout.is_protected_notebook(key_pillar, notebook_key):
            title = vault_layout.builtin_notebook_title(key_pillar, notebook_key)
            raise HTTPException(422, f"「{title}」是默认本子，不能删除")
        return _delete_builtin_notebook(database, vault, key_pillar, notebook_key)

    custom_pillar = vault_layout.custom_notebook_pillar(notebook_key)
    if custom_pillar == key_pillar:
        if vault_layout.is_protected_notebook(key_pillar, notebook_key):
            name = vault_layout.custom_notebook_name(notebook_key) or "这个本子"
            raise HTTPException(422, f"「{name}」是默认本子，不能删除")
        return _delete_custom_notebook(database, vault, key_pillar, notebook_key)

    raise HTTPException(422, "只能删除当前库的本子")


def delete_notebook(database: Database, vault_dir: Path | str, *, pillar: str,
                    key: str, confirm: bool = False, read_only: bool = False) -> dict[str, Any]:
    with operation_lock(f"notebook-recycle|{Path(vault_dir).resolve()}"):
        return _delete_notebook(database, vault_dir, pillar=pillar, key=key,
                                confirm=confirm, read_only=read_only)


def create_notebook_router(database: Database, vault_dir: Path | str, *,
                           read_only: bool = False, prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["notebooks"])

    @router.get("/notebooks")
    def notebooks(pillar: str = Query(default="life")):
        return list_notebooks(vault_dir, pillar)

    @router.post("/notebooks/title")
    def title(payload: NotebookTitleRequest):
        return set_notebook_title(database, vault_dir, payload, read_only=read_only)

    @router.post("/library/notes/action")
    def note_action(payload: LibraryNoteRequest):
        return manage_note(database, vault_dir, payload, read_only=read_only)

    @router.post("/notebooks")
    def create(payload: NotebookCreateRequest):
        return create_notebook(
            database, vault_dir, payload.name,
            pillar=payload.pillar, parent=payload.parent,
            parent_chapter=payload.parent_chapter, read_only=read_only)

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
