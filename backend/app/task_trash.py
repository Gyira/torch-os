"""任务回收站：列出 `.torch-os/task-trash` 里的任务笔记，并支持彻底删除。

任务删除（应用内删除、滴答远端删除）都走 `task_notes.trash_note` 软删除；
只有这里的 purge 才会真正 unlink 磁盘上的 .md 文件。
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import HTTPException

from . import task_notes

TASK_PREFIXES = (
    task_notes.TASK_DIR_REL.as_posix() + "/",
    task_notes.LEGACY_TASK_DIR_REL.as_posix() + "/",
)
_BATCH_STAMP = re.compile(r"^(\d{8}T\d{6}\d+Z)-", re.IGNORECASE)


def _vault_root(vault_dir: Path | str) -> Path:
    return Path(vault_dir).resolve()


def trash_root(vault_dir: Path | str) -> Path:
    return _vault_root(vault_dir) / task_notes.TRASH_DIR_REL


def _batch_time(name: str) -> str:
    match = _BATCH_STAMP.match(name)
    if match is None:
        return ""
    try:
        stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S%fZ")
    except ValueError:
        return ""
    return stamp.replace(tzinfo=timezone.utc).isoformat()


def _is_task_note(original: str) -> bool:
    if not any(original.startswith(prefix) for prefix in TASK_PREFIXES) or not original.endswith(".md"):
        return False
    parts = PurePosixPath(original).parts
    return ".." not in parts and bool(parts)


def _original_relpath(path: Path, batch: Path) -> str:
    return path.relative_to(batch).as_posix()


def _list_name(original: str) -> str:
    parent = PurePosixPath(original).parent
    if parent.name in {"", task_notes.TASK_DIR_REL.name}:
        return task_notes.INBOX
    return parent.name


def list_trash(vault_dir: Path | str) -> list[dict[str, Any]]:
    """列出所有可恢复/可彻底删除的任务笔记（按删除时间倒序）。"""
    root = _vault_root(vault_dir)
    base = root / task_notes.TRASH_DIR_REL
    if not base.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for batch in base.iterdir():
        if not batch.is_dir() or batch.is_symlink():
            continue
        for path in batch.rglob("*.md"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                original = _original_relpath(path, batch)
            except ValueError:
                continue
            if not _is_task_note(original):
                continue
            title = path.stem
            done = False
            dida_id = ""
            try:
                note = task_notes.read_task_note(vault_dir, path)
                if note is not None:
                    title = note["title"]
                    done = bool(note["done"])
                    dida_id = str(note.get("dida_id") or "")
            except Exception:
                # frontmatter 坏了也仍然允许列出和彻底删除，不能把回收站卡死。
                pass
            size = 0
            mtime_iso = ""
            try:
                stat = path.stat()
                size = stat.st_size
                mtime_iso = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
            except OSError:
                pass
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            items.append({
                "id": hashlib.sha256(rel.encode("utf-8")).hexdigest()[:16],
                "trash_path": rel,
                "batch": batch.name,
                "original_path": original,
                "title": title,
                "list_name": _list_name(original),
                "deleted_at": _batch_time(batch.name) or mtime_iso,
                "bytes": size,
                "done": done,
                "dida_id": dida_id or None,
            })
    items.sort(key=lambda item: (item["deleted_at"], item["title"]), reverse=True)
    return items


def _validated_path(vault_dir: Path | str, rel: Any) -> Path:
    text = str(rel or "").strip()
    if not text or "\x00" in text:
        raise ValueError("回收站路径为空")
    raw = Path(text)
    if raw.is_absolute():
        raise ValueError("回收站路径必须是相对路径")
    root = _vault_root(vault_dir).resolve()
    base = (root / task_notes.TRASH_DIR_REL).resolve()
    candidate = (root / raw).resolve()
    try:
        relative = candidate.relative_to(base)
    except ValueError:
        raise ValueError("路径不在回收站里") from None
    if candidate.is_symlink() or not candidate.is_file() or candidate.suffix.lower() != ".md":
        raise ValueError("回收站条目不存在或不是任务笔记")
    parts = relative.parts
    if len(parts) < 2:
        raise ValueError("回收站条目层级不完整")
    original = PurePosixPath(*parts[1:]).as_posix()
    if not _is_task_note(original):
        raise ValueError("只允许彻底删除任务目录里的笔记")
    return candidate


def _cleanup_batch(path: Path, vault_dir: Path | str) -> None:
    base = (_vault_root(vault_dir) / task_notes.TRASH_DIR_REL).resolve()
    parent = path.parent
    while parent != base:
        try:
            parent.relative_to(base)
        except ValueError:
            break
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def _validated_entry(vault_dir: Path | str, rel: Any) -> tuple[Path, str]:
    """返回（回收站文件, 原始相对路径）；校验逻辑与彻底删除一致。"""
    candidate = _validated_path(vault_dir, rel)
    root = _vault_root(vault_dir).resolve()
    relative = candidate.relative_to((root / task_notes.TRASH_DIR_REL).resolve())
    original = PurePosixPath(*relative.parts[1:]).as_posix()
    return candidate, original


def restore_trash(vault_dir: Path | str, *, paths: list[str] | None = None) -> dict[str, Any]:
    """把回收站里的任务笔记放回原路径；原位置已有同名文件时跳过。"""
    root = _vault_root(vault_dir).resolve()
    selected = [str(path) for path in (paths or []) if str(path or "").strip()]
    restored = 0
    restored_paths: list[str] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for rel in selected:
        try:
            source, original = _validated_entry(vault_dir, rel)
        except ValueError as error:
            skipped.append({"path": rel, "reason": str(error)})
            continue
        target = (root / original).resolve()
        if target.exists():
            skipped.append({"path": rel, "reason": "原位置已有同名文件"})
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
        except OSError as error:
            errors.append({"path": rel, "reason": type(error).__name__})
            continue
        restored += 1
        restored_paths.append(original)
        _cleanup_batch(source, vault_dir)
    return {"restored": restored, "restored_paths": restored_paths,
            "skipped": skipped, "errors": errors}


def restore_by_dida_id(vault_dir: Path | str, dida_id: Any) -> dict[str, Any] | None:
    """按滴答任务 ID 从回收站恢复笔记；没有或恢复不了时返回 None。"""
    wanted = str(dida_id or "").strip()
    if not wanted:
        return None
    for item in list_trash(vault_dir):
        if str(item.get("dida_id") or "") != wanted:
            continue
        result = restore_trash(vault_dir, paths=[item["trash_path"]])
        if not result["restored"]:
            return None
        original = result["restored_paths"][0]
        path = (_vault_root(vault_dir) / original).resolve()
        note = None
        try:
            note = task_notes.read_task_note(vault_dir, path)
        except Exception:
            note = None
        return {"item": item, "path": path, "relpath": original, "note": note}
    return None


def purge_trash(vault_dir: Path | str, *, paths: list[str] | None = None,
                all_items: bool = False) -> dict[str, Any]:
    """真正删除回收站里的 .md 文件；绝不触碰任务目录本身。"""
    base = trash_root(vault_dir)
    selected: list[str] = []
    if all_items:
        selected = [item["trash_path"] for item in list_trash(vault_dir)]
    else:
        selected = [str(path) for path in (paths or []) if str(path or "").strip()]
    if not base.is_dir():
        return {
            "deleted": 0,
            "skipped": [{"path": path, "reason": "回收站不存在"} for path in selected],
            "errors": [],
        }
    deleted = 0
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for rel in selected:
        try:
            target = _validated_path(vault_dir, rel)
        except ValueError as error:
            skipped.append({"path": rel, "reason": str(error)})
            continue
        try:
            target.unlink()
            deleted += 1
            _cleanup_batch(target, vault_dir)
        except OSError as error:
            errors.append({"path": rel, "reason": type(error).__name__})
    return {"deleted": deleted, "skipped": skipped, "errors": errors}


__all__ = ["list_trash", "purge_trash", "restore_by_dida_id", "restore_trash", "trash_root"]
