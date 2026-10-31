"""回收站批次与 manifest 的底层存储（2026-10-30）。

任务回收站是用户可见目录：`02_生活运营/00_待办事项/回收站/`。
每次删除创建一个批次目录，里面用 `manifest.json` 记录条目，文件本体放在
`files/...`，被删除的清单文件夹放在 `folders/...`。

旧版 `.torch-os/task-trash` 仍可读取；迁移时把旧批次搬进新回收站。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from . import vault_layout

MANIFEST_NAME = "manifest.json"
TRASH_DIR_NAME = "回收站"
LEGACY_TRASH_DIR = Path(".torch-os", "task-trash")
_BATCH_STAMP = re.compile(r"^(\d{8}T\d{6}\d+Z)-", re.IGNORECASE)


def _vault_root(vault_dir: Path | str) -> Path:
    return Path(vault_dir).resolve()


def trash_root(vault_dir: Path | str) -> Path:
    return _vault_root(vault_dir) / vault_layout.TASK_TRASH_DIR


def legacy_trash_root(vault_dir: Path | str) -> Path:
    return _vault_root(vault_dir) / LEGACY_TRASH_DIR


def batch_time(name: str) -> str:
    match = _BATCH_STAMP.match(str(name or ""))
    if match is None:
        return ""
    try:
        stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S%fZ")
    except ValueError:
        return ""
    return stamp.replace(tzinfo=timezone.utc).isoformat()


def new_batch(vault_dir: Path | str) -> Path:
    root = trash_root(vault_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    batch = root / f"{stamp}-{secrets.token_hex(4)}"
    try:
        batch.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        raise OSError(f"建不了任务回收站：{error}") from error
    write_manifest(batch, {
        "schema": 1,
        "batch": batch.name,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "entries": [],
    })
    return batch


def read_manifest(batch: Path) -> dict[str, Any] | None:
    path = batch / MANIFEST_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    entries = payload.get("entries")
    if not isinstance(entries, list):
        payload["entries"] = []
    return payload


def write_manifest(batch: Path, manifest: dict[str, Any]) -> None:
    batch.mkdir(parents=True, exist_ok=True)
    target = batch / MANIFEST_NAME
    temporary = batch / f".{MANIFEST_NAME}.tmp-{secrets.token_hex(4)}"
    text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, target)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def entry_id(trash_relpath: str) -> str:
    return hashlib.sha256(str(trash_relpath or "").encode("utf-8")).hexdigest()[:16]


def append_entry(batch: Path, entry: dict[str, Any]) -> None:
    manifest = read_manifest(batch) or {
        "schema": 1, "batch": batch.name, "created_at": "", "entries": [],
    }
    entries = [item for item in manifest.get("entries", [])
               if isinstance(item, dict)
               and str(item.get("trash_relpath") or "") != str(entry.get("trash_relpath") or "")]
    entries.append(entry)
    manifest["entries"] = entries
    write_manifest(batch, manifest)


def remove_entry(batch: Path, trash_relpath: str) -> bool:
    manifest = read_manifest(batch)
    if manifest is None:
        return False
    before = len(manifest.get("entries", []))
    manifest["entries"] = [
        item for item in manifest.get("entries", [])
        if not isinstance(item, dict) or str(item.get("trash_relpath") or "") != str(trash_relpath)
    ]
    if len(manifest["entries"]) == before:
        return False
    write_manifest(batch, manifest)
    return True


def iter_batches(vault_dir: Path | str) -> list[tuple[Path, Path]]:
    """返回 [(batch, base)]；新回收站优先，旧 .torch-os 批次也列出来。"""
    found: list[tuple[Path, Path]] = []
    for base in (trash_root(vault_dir), legacy_trash_root(vault_dir)):
        if not base.is_dir():
            continue
        try:
            children = sorted(base.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for entry in children:
            if not entry.is_dir() or entry.is_symlink():
                continue
            found.append((entry, base))
    return found


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def safe_member(batch: Path, relpath: str) -> Path:
    text = str(relpath or "").replace("\\", "/").strip()
    if not text or "\x00" in text or text.startswith("/"):
        raise ValueError("回收站路径为空")
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("回收站路径不合法")
    base = batch.resolve()
    candidate = (base / Path(*pure.parts)).resolve()
    if not _is_relative_to(candidate, base) or candidate == base:
        raise ValueError("路径不在回收站批次里")
    if candidate.is_symlink():
        raise ValueError("回收站里不能有链接文件")
    return candidate


def trash_relative(path: Path, vault_dir: Path | str) -> str:
    return path.resolve().relative_to(_vault_root(vault_dir)).as_posix()


def cleanup_empty_batch(batch: Path, vault_dir: Path | str) -> None:
    """条目清空后删掉空批次；有文件或 manifest 条目时保持不动。"""
    manifest = read_manifest(batch)
    if manifest is not None and manifest.get("entries"):
        return
    try:
        leftovers = [item for item in batch.iterdir() if item.name != MANIFEST_NAME]
    except OSError:
        return
    for item in leftovers:
        if item.is_file():
            return
        try:
            if any(child.is_file() for child in item.rglob("*")):
                return
        except OSError:
            return
    if manifest is not None:
        try:
            (batch / MANIFEST_NAME).unlink()
        except OSError:
            return
    for item in sorted(leftovers, key=lambda node: len(node.parts), reverse=True):
        try:
            item.rmdir()
        except OSError:
            return
    try:
        batch.rmdir()
    except OSError:
        pass


__all__ = [
    "MANIFEST_NAME", "LEGACY_TRASH_DIR", "trash_root", "legacy_trash_root",
    "batch_time", "new_batch", "read_manifest", "write_manifest", "entry_id",
    "append_entry", "remove_entry", "iter_batches", "safe_member",
    "trash_relative", "cleanup_empty_batch",
]
