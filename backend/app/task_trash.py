"""任务回收站：列出/恢复/彻底删除 `02_生活运营/00_待办事项/回收站` 里的条目。

新回收站按批次存放：

```
回收站/<batch>/manifest.json
回收站/<batch>/files/<清单>/<任务>.md
回收站/<batch>/folders/<被删清单>/
回收站/<batch>/_系统/任务（tasks）.md
```

旧 `.torch-os/task-trash` 批次仍然可读、可恢复、可彻底删除；启动迁移会把
它们搬进新回收站。
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import HTTPException

from . import task_notes, task_trash_store, vault_layout

MANIFEST_NAME = task_trash_store.MANIFEST_NAME
CONFIG_NAMES = ("任务（tasks）.md", "任务.md")


def _vault_root(vault_dir: Path | str) -> Path:
    return Path(vault_dir).resolve()


def _entry_path(vault_dir: Path | str, batch: Path, relative: Any) -> Path:
    """manifest 里存的是库相对路径；校验它确实在批次目录里。"""
    text = _posix(relative)
    pure = PurePosixPath(text)
    if not text or pure.is_absolute() or ".." in pure.parts:
        raise ValueError("回收站路径不合法")
    path = (_vault_root(vault_dir) / Path(*pure.parts)).resolve()
    try:
        path.relative_to(batch.resolve())
    except ValueError:
        raise ValueError("路径不在回收站批次里") from None
    return path


def trash_root(vault_dir: Path | str) -> Path:
    return task_trash_store.trash_root(vault_dir)


def legacy_trash_root(vault_dir: Path | str) -> Path:
    return task_trash_store.legacy_trash_root(vault_dir)


def _posix(value: Any) -> str:
    return re.sub(r"/+", "/", str(value or "").replace("\\", "/")).strip("/")


def canonical_original_relpath(original: Any) -> str:
    """旧库/旧批次路径 → 新任务根下的规范路径。"""
    text = _posix(original)
    root = vault_layout.TASK_ROOT
    prefixes = (
        (root + "/", ""),
        ("00_人生路/08_任务与待办/任务/", ""),
        ("00_人生路/07_任务与待办/任务/", ""),
        ("00_人生路/08_任务与待办/", ""),
        ("00-System/任务/", ""),
        ("00_人生路/07_任务与待办/", ""),
    )
    rest = text
    for prefix, _ in prefixes:
        if text.startswith(prefix):
            rest = text[len(prefix):]
            break
    parts = [part for part in rest.split("/") if part]
    if parts and parts[0] == task_notes.LEGACY_INNER_DIR_NAME:
        parts = parts[1:]
    if not parts:
        return root
    if len(parts) == 1:
        return f"{root}/{task_notes.INBOX}/{parts[0]}"
    return f"{root}/{'/'.join(parts)}"


def _original_relative(original: Any) -> str:
    canonical = canonical_original_relpath(original)
    return canonical[len(vault_layout.TASK_ROOT) + 1:] if canonical.startswith(vault_layout.TASK_ROOT + "/") else canonical


def _list_from_original(original: Any, fallback: str = "") -> str:
    relative = _original_relative(original)
    parts = relative.split("/")
    if len(parts) <= 1:
        return task_notes.INBOX
    parent = parts[-2]
    if parent in {task_notes.INBOX, task_notes.LEGACY_INNER_DIR_NAME, ""}:
        return task_notes.INBOX
    if parent == task_notes.DONE_DIR_NAME:
        return fallback or task_notes.INBOX
    return parent


def _is_config_path(path: Path) -> bool:
    return path.suffix.lower() == ".md" and path.name in CONFIG_NAMES and "_系统" in path.parts


def _task_item(vault_dir: Path | str, path: Path, *, batch: Path, base: Path,
               original: str, entry: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".md":
        return None
    if _is_config_path(path):
        return None
    entry = entry or {}
    list_name = str(entry.get("list_name") or "").strip() or _list_from_original(original)
    note = None
    try:
        note = task_notes.inspect_task_file(path, list_hint=list_name)
    except Exception:
        note = None
    title = str(entry.get("title") or (note["title"] if note else path.stem))
    done = bool(entry.get("done", note["done"] if note else False))
    dida_id = str(entry.get("dida_id") or (note.get("dida_id") if note else "") or "")
    if not list_name:
        list_name = str(note.get("list_name") if note else "") or task_notes.INBOX
    size = 0
    mtime = ""
    try:
        stat = path.stat()
        size = stat.st_size
        mtime = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    except OSError:
        pass
    try:
        trash_rel = path.relative_to(_vault_root(vault_dir)).as_posix()
    except ValueError:
        return None
    return {
        "id": task_trash_store.entry_id(trash_rel),
        "kind": "file",
        "trash_path": trash_rel,
        "batch": batch.name,
        "original_path": canonical_original_relpath(original),
        "title": title,
        "list_name": list_name,
        "deleted_at": str(entry.get("deleted_at") or task_trash_store.batch_time(batch.name) or mtime),
        "bytes": size,
        "done": done,
        "dida_id": dida_id or None,
        "dida_ids": [],
        "count": None,
        "restorable": True,
        "legacy": base == legacy_trash_root(vault_dir),
    }


def _folder_item(vault_dir: Path | str, path: Path, *, batch: Path, base: Path,
                 original: str, entry: dict[str, Any]) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_dir():
        return None
    files: list[Path] = []
    try:
        files = [item for item in path.rglob("*.md") if item.is_file() and not item.is_symlink()]
    except OSError:
        pass
    dida_ids: list[str] = list(entry.get("dida_ids") or [])
    size = 0
    for file in files:
        try:
            size += file.stat().st_size
        except OSError:
            pass
        if dida_ids:
            continue
        try:
            note = task_notes.inspect_task_file(file)
        except Exception:
            note = None
        if note and note.get("dida_id"):
            dida_ids.append(note["dida_id"])
    try:
        trash_rel = path.relative_to(_vault_root(vault_dir)).as_posix()
    except ValueError:
        return None
    name = str(entry.get("title") or path.name)
    return {
        "id": task_trash_store.entry_id(trash_rel),
        "kind": "folder",
        "trash_path": trash_rel,
        "batch": batch.name,
        "original_path": canonical_original_relpath(original),
        "title": name,
        "list_name": str(entry.get("list_name") or _list_from_original(original)),
        "deleted_at": str(entry.get("deleted_at") or task_trash_store.batch_time(batch.name)),
        "bytes": size,
        "done": False,
        "dida_id": None,
        "dida_ids": dida_ids,
        "count": int(entry.get("count") or len(files)),
        "restorable": True,
        "legacy": base == legacy_trash_root(vault_dir),
    }


def _infer_batch_items(vault_dir: Path | str, batch: Path, base: Path) -> list[tuple[Path, dict[str, Any]]]:
    """没有 manifest 时按目录结构推断条目。"""
    found: list[tuple[Path, dict[str, Any]]] = []
    files_dir = batch / "files"
    folders_dir = batch / "folders"
    if files_dir.is_dir() or folders_dir.is_dir():
        if files_dir.is_dir():
            for path in sorted(files_dir.rglob("*.md")):
                if path.is_file() and not path.is_symlink() and not _is_config_path(path):
                    relative = path.relative_to(files_dir).as_posix()
                    found.append((path, {
                        "kind": "file", "original_relpath": f"{vault_layout.TASK_ROOT}/{relative}",
                        "trash_relpath": path.relative_to(_vault_root(vault_dir)).as_posix(),
                    }))
        if folders_dir.is_dir():
            for path in sorted(folders_dir.iterdir()):
                if path.is_dir() and not path.is_symlink():
                    found.append((path, {
                        "kind": "folder", "original_relpath": f"{vault_layout.TASK_ROOT}/{path.name}",
                        "trash_relpath": path.relative_to(_vault_root(vault_dir)).as_posix(),
                        "title": path.name, "list_name": path.name,
                    }))
        return found
    for path in sorted(batch.rglob("*.md")):
        if not path.is_file() or path.is_symlink() or _is_config_path(path):
            continue
        relative_parts = path.relative_to(batch).parts
        if any(part in {task_notes.SYSTEM_DIR_NAME, "_旧版"} for part in relative_parts):
            continue
        relative = path.relative_to(batch).as_posix()
        found.append((path, {
            "kind": "file", "original_relpath": canonical_original_relpath(relative),
            "trash_relpath": path.relative_to(_vault_root(vault_dir)).as_posix(),
        }))
    return found


def list_trash(vault_dir: Path | str) -> list[dict[str, Any]]:
    """列出所有可恢复/可彻底删除的回收站条目（文件与文件夹）。"""
    vault = _vault_root(vault_dir)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for batch, base in task_trash_store.iter_batches(vault):
        manifest = task_trash_store.read_manifest(batch)
        entries: list[tuple[Path, dict[str, Any]]] = []
        if manifest and manifest.get("entries"):
            for entry in manifest["entries"]:
                if not isinstance(entry, dict):
                    continue
                try:
                    path = _entry_path(vault, batch, entry.get("trash_relpath"))
                except ValueError:
                    continue
                entries.append((path, entry))
        else:
            entries = _infer_batch_items(vault, batch, base)
        for path, entry in entries:
            kind = str(entry.get("kind") or ("folder" if path.is_dir() else "file"))
            if kind == "config" or not entry.get("restorable", True):
                continue
            original = str(entry.get("original_relpath") or "")
            if kind == "folder":
                item = _folder_item(vault, path, batch=batch, base=base,
                                    original=original, entry=entry)
            else:
                item = _task_item(vault, path, batch=batch, base=base,
                                  original=original, entry=entry)
            if item is None or item["trash_path"] in seen:
                continue
            seen.add(item["trash_path"])
            items.append(item)
    items.sort(key=lambda item: (item["deleted_at"], item["title"]), reverse=True)
    return items


def _batch_for_path(vault_dir: Path | str, path: Path) -> tuple[Path, Path] | None:
    resolved = path.resolve()
    for base in (trash_root(vault_dir), legacy_trash_root(vault_dir)):
        if not base.is_dir():
            continue
        try:
            relative = resolved.relative_to(base.resolve())
        except ValueError:
            continue
        if not relative.parts:
            return None
        return base, base / relative.parts[0]
    return None


def _entry_for(vault_dir: Path | str, path: Path) -> tuple[Path, Path, dict[str, Any]] | None:
    batch_info = _batch_for_path(vault_dir, path)
    if batch_info is None:
        return None
    base, batch = batch_info
    manifest = task_trash_store.read_manifest(batch)
    if manifest and manifest.get("entries"):
        trash_rel = path.relative_to(_vault_root(vault_dir)).as_posix()
        for entry in manifest["entries"]:
            if isinstance(entry, dict) and str(entry.get("trash_relpath") or "") == trash_rel:
                return base, batch, entry
        return None
    for inferred_path, entry in _infer_batch_items(vault_dir, batch, base):
        if inferred_path.resolve() == path.resolve():
            return base, batch, entry
    return None


def _merge_into(source: Path, target: Path) -> int:
    """把 source 里的内容合并进 target，返回移动的文件数。"""
    moved = 0
    for entry in list(source.iterdir()):
        destination = target / entry.name
        if entry.is_dir() and not entry.is_symlink():
            if destination.exists() and destination.is_dir():
                moved += _merge_into(entry, destination)
                try:
                    entry.rmdir()
                except OSError:
                    pass
            elif not destination.exists():
                os.replace(entry, destination)
                moved += 1
            continue
        if destination.exists():
            continue
        try:
            os.replace(entry, destination)
            moved += 1
        except OSError:
            continue
    return moved


def _restore_file(vault_dir: Path | str, source: Path, *,
                  list_name: str, done: bool, filename: str | None = None) -> Path:
    target_dir = task_notes.destination_folder(vault_dir, list_name, done, create=True)
    target = target_dir / (filename or source.name)
    if target.exists():
        raise FileExistsError("原位置已有同名文件")
    os.replace(source, target)
    return target


def _refresh_folder_entry(batch: Path, entry: dict[str, Any], vault_dir: Path | str) -> None:
    try:
        path = _entry_path(vault_dir, batch, entry.get("trash_relpath"))
    except ValueError:
        return
    files = [item for item in path.rglob("*.md") if item.is_file() and not item.is_symlink()] if path.is_dir() else []
    dida_ids: list[str] = []
    for file in files:
        try:
            note = task_notes.inspect_task_file(file)
        except Exception:
            note = None
        if note and note.get("dida_id"):
            dida_ids.append(note["dida_id"])
    entry["count"] = len(files)
    entry["dida_ids"] = dida_ids
    manifest = task_trash_store.read_manifest(batch) or {"schema": 1, "batch": batch.name,
                                                          "created_at": "", "entries": []}
    manifest["entries"] = [
        entry if isinstance(item, dict)
        and str(item.get("trash_relpath") or "") == str(entry.get("trash_relpath") or "") else item
        for item in manifest.get("entries", [])
    ]
    task_trash_store.write_manifest(batch, manifest)


def restore_trash(vault_dir: Path | str, *, paths: list[str] | None = None) -> dict[str, Any]:
    """把回收站条目放回原位置；文件按 done/清单路由，文件夹整体恢复。"""
    vault = _vault_root(vault_dir)
    selected = [str(path) for path in (paths or []) if str(path or "").strip()]
    restored = 0
    restored_paths: list[str] = []
    restored_folders: list[str] = []
    list_definitions: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for rel in selected:
        raw = Path(rel)
        if raw.is_absolute() or ".." in raw.parts:
            skipped.append({"path": rel, "reason": "路径不在回收站里"})
            continue
        try:
            source = (vault / raw).resolve()
        except OSError:
            skipped.append({"path": rel, "reason": "路径无效"})
            continue
        info = _entry_for(vault_dir, source)
        if info is None:
            skipped.append({"path": rel, "reason": "回收站条目不存在"})
            continue
        _base, batch, entry = info
        kind = str(entry.get("kind") or ("folder" if source.is_dir() else "file"))
        original = str(entry.get("original_relpath") or "")
        if kind == "folder":
            name = str(entry.get("title") or source.name)
            target = task_notes.list_folder(vault_dir, name)
            folder_removed = False
            try:
                if target.exists():
                    moved = _merge_into(source, target)
                    if moved:
                        restored += 1
                        restored_paths.append(task_notes.list_folder(vault_dir, name).relative_to(vault).as_posix())
                    _refresh_folder_entry(batch, entry, vault_dir)
                    if not any(source.iterdir()):
                        try:
                            source.rmdir()
                            folder_removed = True
                        except OSError:
                            pass
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(source, target)
                    restored += 1
                    restored_folders.append(target.relative_to(vault).as_posix())
                    folder_removed = True
                definition = entry.get("list_definition")
                if isinstance(definition, dict) and definition:
                    list_definitions.append(definition)
                if folder_removed:
                    task_trash_store.remove_entry(batch, str(entry.get("trash_relpath") or ""))
                # 同一批次里属于这个清单的已完成任务也一起恢复。
                manifest = task_trash_store.read_manifest(batch) or {}
                for related in list(manifest.get("entries") or []):
                    if not isinstance(related, dict):
                        continue
                    if (str(related.get("kind") or "") != "file"
                            or str(related.get("reason") or "") != "list_delete_done"
                            or str(related.get("list_name") or "").casefold() != name.casefold()):
                        continue
                    try:
                        related_path = _entry_path(vault_dir, batch, related.get("trash_relpath"))
                    except ValueError:
                        continue
                    try:
                        related_note = task_notes.inspect_task_file(related_path, list_hint=name)
                    except Exception:
                        related_note = None
                    related_done = bool((related_note or {}).get("done", True))
                    try:
                        target_done = _restore_file(
                            vault_dir, related_path, list_name=name, done=related_done)
                    except (FileExistsError, OSError):
                        continue
                    task_trash_store.remove_entry(
                        batch, str(related.get("trash_relpath") or ""))
                    restored += 1
                    restored_paths.append(target_done.relative_to(vault).as_posix())
                task_trash_store.cleanup_empty_batch(batch, vault_dir)
            except OSError as error:
                errors.append({"path": rel, "reason": type(error).__name__})
            continue
        # 文件
        list_name = str(entry.get("list_name") or "").strip()
        done = bool(entry.get("done"))
        note = None
        try:
            note = task_notes.inspect_task_file(
                source, list_hint=list_name or _list_from_original(original))
        except Exception:
            note = None
        if note is not None:
            list_name = str(note.get("list_name") or list_name or task_notes.INBOX)
            done = bool(note.get("done"))
        list_name = list_name or _list_from_original(original)
        try:
            target = _restore_file(vault_dir, source, list_name=list_name, done=done)
        except FileExistsError:
            skipped.append({"path": rel, "reason": "原位置已有同名文件"})
            continue
        except OSError as error:
            errors.append({"path": rel, "reason": type(error).__name__})
            continue
        task_trash_store.remove_entry(batch, str(entry.get("trash_relpath") or ""))
        task_trash_store.cleanup_empty_batch(batch, vault_dir)
        restored += 1
        restored_paths.append(target.relative_to(vault).as_posix())
    return {
        "restored": restored,
        "restored_paths": restored_paths,
        "restored_folders": restored_folders,
        "list_definitions": list_definitions,
        "skipped": skipped,
        "errors": errors,
    }


def restore_by_dida_id(vault_dir: Path | str, dida_id: Any) -> dict[str, Any] | None:
    """按滴答任务 ID 从回收站恢复笔记；文件夹条目里的单个任务也会被取出。"""
    wanted = str(dida_id or "").strip()
    if not wanted:
        return None
    vault = _vault_root(vault_dir)
    for batch, base in task_trash_store.iter_batches(vault):
        manifest = task_trash_store.read_manifest(batch)
        candidates: list[tuple[Path, dict[str, Any], bool]] = []
        if manifest and manifest.get("entries"):
            for entry in manifest["entries"]:
                if not isinstance(entry, dict):
                    continue
                kind = str(entry.get("kind") or "")
                if kind not in {"file", "folder"}:
                    continue
                try:
                    path = _entry_path(vault, batch, entry.get("trash_relpath"))
                except ValueError:
                    continue
                candidates.append((path, entry, kind == "folder"))
        else:
            for path, entry in _infer_batch_items(vault, batch, base):
                candidates.append((path, entry, path.is_dir()))
        for path, entry, is_folder in candidates:
            files = [path] if not is_folder else [
                item for item in path.rglob("*.md")
                if item.is_file() and not item.is_symlink()
            ]
            for file in files:
                hint = (str(entry.get("list_name") or "")
                        or _list_from_original(entry.get("original_relpath") or ""))
                try:
                    note = task_notes.inspect_task_file(file, list_hint=hint)
                except Exception:
                    note = None
                if note is None or str(note.get("dida_id") or "") != wanted:
                    continue
                list_name = str(entry.get("list_name") or note.get("list_name")
                                or hint or task_notes.INBOX)
                done = bool(entry.get("done", note.get("done")))
                try:
                    target = _restore_file(vault_dir, file, list_name=list_name, done=done)
                except (FileExistsError, OSError):
                    continue
                if is_folder:
                    _refresh_folder_entry(batch, entry, vault_dir)
                    if not any(path.iterdir()):
                        try:
                            path.rmdir()
                        except OSError:
                            pass
                        task_trash_store.remove_entry(
                            batch, str(entry.get("trash_relpath") or ""))
                    task_trash_store.cleanup_empty_batch(batch, vault_dir)
                else:
                    task_trash_store.remove_entry(batch, str(entry.get("trash_relpath") or ""))
                    task_trash_store.cleanup_empty_batch(batch, vault_dir)
                try:
                    fresh = task_notes.read_task_note(vault_dir, target)
                except Exception:
                    fresh = None
                return {
                    "item": entry,
                    "path": target,
                    "relpath": target.relative_to(vault).as_posix(),
                    "note": fresh,
                }
    return None


def purge_trash(vault_dir: Path | str, *, paths: list[str] | None = None,
                all_items: bool = False) -> dict[str, Any]:
    """真正删除回收站里的文件/文件夹；绝不触碰任务目录本身。"""
    vault = _vault_root(vault_dir)
    deleted = 0
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    if all_items:
        for base in (trash_root(vault_dir), legacy_trash_root(vault_dir)):
            if not base.is_dir():
                continue
            for batch in sorted(base.iterdir(), key=lambda item: item.name):
                if not batch.is_dir() or batch.is_symlink():
                    continue
                try:
                    count = sum(1 for item in batch.rglob("*") if item.is_file())
                    shutil.rmtree(batch)
                    deleted += max(1, count)
                except OSError as error:
                    errors.append({"path": batch.relative_to(vault).as_posix(),
                                   "reason": type(error).__name__})
        return {"deleted": deleted, "skipped": skipped, "errors": errors}

    for rel in [str(path) for path in (paths or []) if str(path or "").strip()]:
        raw = Path(rel)
        if raw.is_absolute() or ".." in raw.parts:
            skipped.append({"path": rel, "reason": "路径不在回收站里"})
            continue
        try:
            target = (vault / raw).resolve()
        except OSError:
            skipped.append({"path": rel, "reason": "路径无效"})
            continue
        info = _entry_for(vault_dir, target)
        if info is None:
            skipped.append({"path": rel, "reason": "回收站条目不存在"})
            continue
        _base, batch, entry = info
        try:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.is_file():
                target.unlink()
            else:
                skipped.append({"path": rel, "reason": "条目已经不存在"})
                continue
        except OSError as error:
            errors.append({"path": rel, "reason": type(error).__name__})
            continue
        deleted += 1
        task_trash_store.remove_entry(batch, str(entry.get("trash_relpath") or ""))
        task_trash_store.cleanup_empty_batch(batch, vault_dir)
    return {"deleted": deleted, "skipped": skipped, "errors": errors}


__all__ = [
    "MANIFEST_NAME", "canonical_original_relpath", "trash_root", "legacy_trash_root",
    "list_trash", "restore_trash", "restore_by_dida_id", "purge_trash",
]
