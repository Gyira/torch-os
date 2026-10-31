"""全局回收中心（2026-10-31）。

四库文档删除后物理上仍沿用各域的回收站：

- 笔记 / 日记：`.torch-os/笔记回收站/<批次>/files/<原相对路径>`；
- 待办事项：`02_生活运营/00_待办事项/回收站/<批次>/...`（待办事项模块的回收站
  和这里查看的是同一批文件）。

本模块只做统一读取、恢复和彻底删除，不搬动既有回收站目录，避免已删除数据在升级时
丢失。恢复会把文件放回原相对路径；彻底删除才真正移除文件。待办事项的恢复仍交给
``task_trash``，以保留按清单/已完成路由的语义。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import pillar_notes, task_trash, task_trash_store, vault_layout
from .db import Database
from .vault import safe_note_path
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

MANIFEST_NAME = "manifest.json"
NOTE_TRASH_DIR = pillar_notes.NOTE_TRASH_DIR
PILLAR_GROUPS: tuple[tuple[str, str], ...] = (
    ("life", "人生路"),
    ("self", "自我档案"),
    ("ops", "生活运营"),
    ("knowledge", "知识库"),
)


class _RecycleSkip(Exception):
    """单个条目无法恢复时的可读原因。"""


def _vault_root(vault_dir: Path | str) -> Path:
    return Path(vault_dir).resolve()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _note_trash_root(vault: Path) -> Path:
    return vault / NOTE_TRASH_DIR


def _iso_or_batch_time(batch: Path, value: Any) -> str:
    text = str(value or "").strip()
    if text:
        return text
    return task_trash_store.batch_time(batch.name)


def _mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(
            path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return ""


def _note_item(vault: Path, batch: Path, path: Path, *, original: str,
               entry: dict[str, Any], fallback_title: str = "") -> dict[str, Any] | None:
    """把一个笔记回收文件投影成回收中心条目。"""
    try:
        trash_rel = path.relative_to(vault).as_posix()
        batch_rel = batch.relative_to(vault).as_posix()
    except ValueError:
        return None
    original = vault_layout.normalize_relpath(original)
    if not original:
        return None
    pillar = str(entry.get("pillar") or vault_layout.pillar_of(original) or "")
    chapter = str(entry.get("chapter") or vault_layout.chapter_key_of(original) or "")
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {
        "source": "notes",
        "kind": "journal" if chapter == "journal" else "note",
        "title": str(entry.get("title") or fallback_title or path.stem),
        "excerpt": str(entry.get("excerpt") or ""),
        "original_path": original,
        "trash_path": trash_rel,
        "batch_path": batch_rel,
        "chapter": chapter,
        "pillar": pillar,
        "deleted_at": _iso_or_batch_time(batch, entry.get("deleted_at")),
        "bytes": size,
        "restorable": bool(pillar),
    }


def _note_items_from_manifest(vault: Path, batch: Path,
                              manifest: dict[str, Any]) -> list[dict[str, Any]]:
    entries = manifest.get("entries")
    if isinstance(entries, list) and entries:
        source = [item for item in entries if isinstance(item, dict)]
    elif manifest.get("trash_path") or manifest.get("original_path"):
        source = [manifest]
    else:
        source = []
    items: list[dict[str, Any]] = []
    for entry in source:
        raw = str(entry.get("trash_path") or "").replace("\\", "/").strip("/")
        if not raw or ".." in raw.split("/"):
            continue
        path = (vault / raw).resolve()
        try:
            path.relative_to(batch.resolve())
        except ValueError:
            continue
        if not path.is_file():
            continue
        original = str(entry.get("original_path") or "")
        if not original and "/files/" in raw:
            original = raw.split("/files/", 1)[1]
        item = _note_item(vault, batch, path, original=original, entry=entry)
        if item is not None:
            items.append(item)
    return items


def _infer_note_items(vault: Path, batch: Path) -> list[dict[str, Any]]:
    """旧批次没有 manifest 时按 files/ 目录结构推断原路径。"""
    files_root = batch / "files"
    root = files_root if files_root.is_dir() else batch
    items: list[dict[str, Any]] = []
    try:
        paths = sorted(root.rglob("*.md"))
    except OSError:
        return items
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            original = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if not vault_layout.pillar_of(original):
            continue
        entry = {"deleted_at": _mtime_iso(path)}
        item = _note_item(vault, batch, path, original=original, entry=entry)
        if item is not None:
            items.append(item)
    return items


def _note_items(vault: Path) -> list[dict[str, Any]]:
    root = _note_trash_root(vault)
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    try:
        batches = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return items
    for batch in batches:
        if not batch.is_dir() or batch.is_symlink():
            continue
        manifest = _read_json(batch / MANIFEST_NAME)
        if manifest is not None:
            items.extend(_note_items_from_manifest(vault, batch, manifest))
        else:
            items.extend(_infer_note_items(vault, batch))
    return items


def _task_items(vault: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in task_trash.list_trash(vault):
        trash_rel = str(row.get("trash_path") or "")
        if not trash_rel:
            continue
        items.append({
            "source": "tasks",
            "kind": "task_folder" if row.get("kind") == "folder" else "task",
            "title": str(row.get("title") or "未命名任务"),
            "excerpt": str(row.get("list_name") or ""),
            "original_path": str(row.get("original_path") or ""),
            "trash_path": trash_rel,
            "batch_path": "",
            "chapter": "task",
            "pillar": "ops",
            "deleted_at": str(row.get("deleted_at") or ""),
            "bytes": int(row.get("bytes") or 0),
            "restorable": bool(row.get("restorable", True)),
            "list_name": str(row.get("list_name") or ""),
            "done": bool(row.get("done")),
            "count": row.get("count"),
            "legacy": bool(row.get("legacy")),
            "dida_id": str(row.get("dida_id") or ""),
            "dida_ids": [str(item) for item in (row.get("dida_ids") or []) if str(item)],
        })
    return items


def _iter_items(vault: Path) -> Iterator[dict[str, Any]]:
    seen: set[str] = set()
    for item in [*_note_items(vault), *_task_items(vault)]:
        trash_rel = str(item.get("trash_path") or "")
        if not trash_rel or trash_rel in seen:
            continue
        seen.add(trash_rel)
        yield item


def list_recycle(vault_dir: Path | str) -> dict[str, Any]:
    """按四库分组列出所有可恢复/可彻底删除的文档。"""
    vault = _vault_root(vault_dir)
    groups = {
        key: {"key": key, "title": title, "count": 0, "items": []}
        for key, title in PILLAR_GROUPS
    }
    other = {"key": "other", "title": "其他", "count": 0, "items": []}
    for item in _iter_items(vault):
        bucket = groups.get(str(item.get("pillar") or ""), other)
        bucket["items"].append(item)
    for bucket in [*groups.values(), other]:
        bucket["items"].sort(
            key=lambda row: (str(row.get("deleted_at") or ""), str(row.get("title") or "")),
            reverse=True,
        )
        bucket["count"] = len(bucket["items"])
    ordered = [groups[key] for key, _title in PILLAR_GROUPS]
    if other["count"]:
        ordered.append(other)
    return {
        "total": sum(bucket["count"] for bucket in ordered),
        "groups": ordered,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _ensure_write(read_only: bool, action: str) -> None:
    try:
        ensure_vault_writable(read_only, action)
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error


def _items_by_path(vault: Path) -> dict[str, dict[str, Any]]:
    return {item["trash_path"]: item for item in _iter_items(vault)}


def _safe_trash_file(vault: Path, item: dict[str, Any]) -> Path:
    root = _note_trash_root(vault).resolve()
    batch_text = str(item.get("batch_path") or "")
    if not batch_text:
        raise _RecycleSkip("回收站批次不可用")
    batch = (vault / batch_text).resolve()
    try:
        batch.relative_to(root)
    except ValueError as error:
        raise _RecycleSkip("回收站路径不合法") from error
    source = (vault / str(item.get("trash_path") or "")).resolve()
    try:
        source.relative_to(batch)
    except ValueError as error:
        raise _RecycleSkip("回收站路径不合法") from error
    if not source.is_file():
        raise _RecycleSkip("回收站文件已经不存在")
    return source


def _drop_note_manifest(vault: Path, item: dict[str, Any]) -> None:
    batch = (vault / str(item.get("batch_path") or "")).resolve()
    manifest_path = batch / MANIFEST_NAME
    manifest = _read_json(manifest_path)
    if manifest is None:
        return
    entries = manifest.get("entries")
    if isinstance(entries, list):
        remaining = [
            entry for entry in entries
            if not (isinstance(entry, dict)
                    and str(entry.get("trash_path") or "") == item["trash_path"])
        ]
        if remaining:
            try:
                task_trash_store.write_manifest(batch, {**manifest, "entries": remaining})
            except OSError:
                pass
        else:
            try:
                manifest_path.unlink()
            except OSError:
                pass
    elif str(manifest.get("trash_path") or "") == item["trash_path"]:
        try:
            manifest_path.unlink()
        except OSError:
            pass


def _prune_batch(batch: Path) -> None:
    try:
        directories = sorted(
            (path for path in batch.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
    except OSError:
        return
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        batch.rmdir()
    except OSError:
        pass


def _cleanup_note_batch(vault: Path, item: dict[str, Any]) -> None:
    batch = (vault / str(item.get("batch_path") or "")).resolve()
    _drop_note_manifest(vault, item)
    _prune_batch(batch)


def _restore_note(vault: Path, item: dict[str, Any]) -> None:
    original = vault_layout.normalize_relpath(item.get("original_path"))
    if not original:
        raise _RecycleSkip("原路径不可用")
    try:
        vault_layout.validate_target(original)
        target = safe_note_path(vault, original)
    except (ValueError, HTTPException) as error:
        raise _RecycleSkip("原路径不在四库内") from error
    if target.exists():
        raise _RecycleSkip("原位置已有同名文件")
    source = _safe_trash_file(vault, item)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
    except OSError as error:
        raise _RecycleSkip(f"恢复失败：{error}") from error
    _cleanup_note_batch(vault, item)


def _purge_note(vault: Path, item: dict[str, Any]) -> None:
    source = _safe_trash_file(vault, item)
    try:
        source.unlink()
    except OSError as error:
        raise _RecycleSkip(f"删除失败：{error}") from error
    _cleanup_note_batch(vault, item)


def _restore_tasks(database: Database, vault: Path, paths: list[str], *,
                   read_only: bool) -> dict[str, Any]:
    result = task_trash.restore_trash(vault, paths=paths)
    restored_lists = 0
    definitions = result.get("list_definitions") or []
    if definitions:
        try:
            from .tasks import restore_trash_list_definitions
            restored_lists = restore_trash_list_definitions(
                vault, definitions, database, read_only=read_only)
        except Exception:
            # 任务文件已经恢复；清单定义恢复失败不应让整次操作失败。
            restored_lists = 0
    result["lists_restored"] = restored_lists
    return result


def restore_recycle(database: Database, vault_dir: Path | str, *, paths: list[str],
                    read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "恢复回收中心文档")
    vault = _vault_root(vault_dir)
    restored = 0
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    task_paths: list[str] = []
    known = _items_by_path(vault)
    for raw in paths:
        rel = str(raw or "").strip()
        if not rel:
            continue
        item = known.get(rel)
        if item is None:
            skipped.append({"path": rel, "reason": "回收站条目不存在"})
            continue
        if item["source"] == "tasks":
            task_paths.append(rel)
            continue
        try:
            _restore_note(vault, item)
            restored += 1
        except _RecycleSkip as reason:
            skipped.append({"path": rel, "reason": str(reason)})
        except OSError as error:
            errors.append({"path": rel, "reason": type(error).__name__})
    lists_restored = 0
    if task_paths:
        task_result = _restore_tasks(database, vault, task_paths, read_only=read_only)
        restored += int(task_result.get("restored") or 0)
        lists_restored = int(task_result.get("lists_restored") or 0)
        skipped.extend(task_result.get("skipped") or [])
        errors.extend(task_result.get("errors") or [])
    database.audit("recycle_restored", {
        "count": restored, "tasks": len(task_paths),
        "skipped": len(skipped), "errors": len(errors),
    })
    return {
        "restored": restored, "lists_restored": lists_restored,
        "skipped": skipped, "errors": errors,
    }


def purge_recycle(database: Database, vault_dir: Path | str, *, paths: list[str],
                  read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "彻底删除回收中心文档")
    vault = _vault_root(vault_dir)
    deleted = 0
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    task_paths: list[str] = []
    known = _items_by_path(vault)
    for raw in paths:
        rel = str(raw or "").strip()
        if not rel:
            continue
        item = known.get(rel)
        if item is None:
            skipped.append({"path": rel, "reason": "回收站条目不存在"})
            continue
        if item["source"] == "tasks":
            task_paths.append(rel)
            continue
        try:
            _purge_note(vault, item)
            deleted += 1
        except _RecycleSkip as reason:
            skipped.append({"path": rel, "reason": str(reason)})
        except OSError as error:
            errors.append({"path": rel, "reason": type(error).__name__})
    if task_paths:
        task_result = task_trash.purge_trash(vault, paths=task_paths)
        deleted += int(task_result.get("deleted") or 0)
        skipped.extend(task_result.get("skipped") or [])
        errors.extend(task_result.get("errors") or [])
    database.audit("recycle_purged", {
        "count": deleted, "tasks": len(task_paths),
        "skipped": len(skipped), "errors": len(errors),
    })
    return {"deleted": deleted, "skipped": skipped, "errors": errors}


class RecycleActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    paths: list[str] = Field(min_length=1, max_length=500)


def create_recycle_router(database: Database, vault_dir: Path | str, *,
                          read_only: bool = False, prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["recycle"])

    @router.get("/recycle")
    def recycle_list():
        return list_recycle(vault_dir)

    @router.post("/recycle/restore")
    def recycle_restore(payload: RecycleActionRequest):
        return restore_recycle(
            database, vault_dir, paths=payload.paths, read_only=read_only)

    @router.post("/recycle/purge")
    def recycle_purge(payload: RecycleActionRequest):
        return purge_recycle(
            database, vault_dir, paths=payload.paths, read_only=read_only)

    return router


__all__ = [
    "MANIFEST_NAME", "NOTE_TRASH_DIR", "PILLAR_GROUPS", "list_recycle",
    "restore_recycle", "purge_recycle", "create_recycle_router",
]
