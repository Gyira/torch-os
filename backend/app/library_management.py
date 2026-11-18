"""Library titles and file actions; notebook identity and links stay stable."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import pillar_notes, vault_layout, note_editor
from .memory_store import operation_lock
from .storage_layout import workspace_path
from .vault_connection import ensure_vault_writable, VaultReadOnlyError


class NotebookTitleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    pillar: Literal["life", "knowledge"]
    key: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=80)


class LibraryNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: Literal["title", "delete", "move"]
    path: str = Field(min_length=1, max_length=2048)
    expected_revision: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=200)
    target_chapter: str = Field(default="", max_length=120)


def notebook_titles(vault_dir, pillar):
    path = workspace_path(Path(vault_dir).resolve(), ".torch-os/notebook-titles.json")
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get(pillar, {})
        if not isinstance(rows, dict):
            raise ValueError("invalid titles")
        return {key: value for key, value in rows.items() if isinstance(value, str)}
    except (OSError, ValueError, AttributeError) as error:
        raise HTTPException(409, "本子标题配置无法读取，请恢复配置后再试") from error


def _writable(read_only, action):
    try:
        ensure_vault_writable(read_only, action)
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error


def _save_titles(vault_dir, rows):
    path = workspace_path(Path(vault_dir).resolve(), ".torch-os/notebook-titles.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def clear_notebook_title(vault_dir, pillar, key):
    """A newly created folder must not inherit a previous deleted notebook's title."""
    with operation_lock(f"notebook-recycle|{Path(vault_dir).resolve()}"):
        rows = {p: notebook_titles(vault_dir, p) for p in ("life", "knowledge")}
        if key in rows.get(pillar, {}):
            del rows[pillar][key]
            _save_titles(vault_dir, rows)


def restore_notebook_title(vault_dir, manifest):
    """Caller holds the notebook operation lock; paths still come from the original key."""
    pillar, key, title = manifest.get("pillar"), manifest.get("key"), manifest.get("display_title")
    if pillar not in {"life", "knowledge"} or not isinstance(title, str) or not title:
        return
    try:
        rows = {p: notebook_titles(vault_dir, p) for p in ("life", "knowledge")}
    except HTTPException as error:
        raise ValueError(error.detail) from error
    rows[pillar][key] = title
    _save_titles(vault_dir, rows)


def set_notebook_title(database, vault_dir, payload, *, read_only=False):
    _writable(read_only, "更改本子标题")
    with operation_lock(f"notebook-recycle|{Path(vault_dir).resolve()}"):
        key, pillar = payload.key, payload.pillar
        exists = key in vault_layout.BUILTIN_NOTEBOOKS.get(pillar, {})
        if exists:
            exists = key not in vault_layout.deleted_notebook_keys(vault_dir).get(pillar, set())
        else:
            name = vault_layout.custom_notebook_name(key)
            exists = (vault_layout.custom_notebook_pillar(key) == pillar
                      and name in vault_layout.list_custom_notebooks(vault_dir, pillar))
        if not exists:
            raise HTTPException(404, "本子不存在")
        if any(ord(char) < 32 for char in payload.title):
            raise HTTPException(422, "标题不能包含控制字符")
        rows = {p: notebook_titles(vault_dir, p) for p in ("life", "knowledge")}
        rows[pillar][key] = payload.title
        _save_titles(vault_dir, rows)
        database.audit("notebook_title_changed", {"pillar": pillar, "key": key, "title": payload.title})
        return {"key": key, "title": payload.title, "pillar": pillar}


def manage_note(database, vault_dir, payload, *, read_only=False):
    _writable(read_only, "管理文档")
    with operation_lock(f"notebook-recycle|{Path(vault_dir).resolve()}"):
        note = pillar_notes.read_note(vault_dir, payload.path)
        chapter = note.get("chapter", "")
        if (vault_layout.pillar_of(note["path"]) not in {"life", "knowledge"}
                or not (note_editor.is_writable_chapter(chapter) or chapter == "journal")):
            raise HTTPException(422, "只支持人生路和知识库的文档；打卡等系统记录请在对应模块管理")
        if note["revision"] != payload.expected_revision:
            raise HTTPException(409, "文档已在别处修改，请刷新后重试")
        kwargs = {"expected_revision": payload.expected_revision}
        if payload.action == "title":
            if not payload.title:
                raise HTTPException(422, "标题不能为空")
            result = pillar_notes.update_note(vault_dir, note["path"], title=payload.title, **kwargs)
        elif payload.action == "delete":
            result = pillar_notes.trash_note(vault_dir, note["path"], **kwargs)
        else:
            target = note_editor._require_writable_chapter(payload.target_chapter, vault_dir)
            note_editor._ensure_custom_notebook_exists(vault_dir, target)
            if note_editor.chapter_pillar(target) != vault_layout.pillar_of(note["path"]):
                raise HTTPException(422, "只能在同一个库内移动文档")
            if chapter != "journal":
                return note_editor.move_note(database, vault_dir, path=note["path"],
                                             target_chapter=target, **kwargs)
            result = pillar_notes.move_note(vault_dir, note["path"], target, **kwargs)
            try:
                database.relocate_document(note["path"], result["path"], title=result.get("title", ""),
                                           content=result.get("body", ""))
            except Exception:
                # The file move succeeded; a stale search projection is repaired by the next scan.
                result["index_pending"] = True
        database.audit(f"library_note_{payload.action}", {"path": note["path"], "result_path": result.get("path", "")})
        return result
