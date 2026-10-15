"""Read and restore managed Wolai import history.

Listing never reads history bodies. Restoring always archives the current
file first (content-addressed), so no version can be lost by a restore.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .attachment_localize import _publish_new_file
from .db import Database
from .vault import MAX_NOTE_BYTES, parse_text
from .wolai_import import (
    HISTORY_DIRECTORY,
    IMPORT_DIRECTORY,
    MAX_IMPORT_NOTES,
    WolaiImportError,
    _local_path,
    _open_archive,
    _render,
    _source_id,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
_VERSION_NAME = re.compile(r"[0-9a-f]{64}\.md")
_SHA256 = re.compile(r"[0-9a-f]{64}")
CLEANUP_KEEP_NEWEST = 3
CLEANUP_MIN_AGE_DAYS = 30


def _validate_selection(selected_paths: list[str]) -> None:
    if not isinstance(selected_paths, list) or not 1 <= len(selected_paths) <= MAX_IMPORT_NOTES or any(
        not isinstance(path, str) or path.casefold() in {"all", "*"} for path in selected_paths
    ):
        raise WolaiImportError("必须明确选择 1 至 20 篇 Markdown")
    if len(set(selected_paths)) != len(selected_paths):
        raise WolaiImportError("选择列表包含重复路径")


def _require_member(records: list[dict[str, Any]], member: str) -> None:
    by_path = {record["path"].rstrip("/"): record for record in records}
    record = by_path.get(member)
    if not record or record["type"] != "markdown":
        raise WolaiImportError("选中路径不存在或不是 Markdown")
    if record["sensitive"]:
        raise WolaiImportError("敏感目录的 Markdown 不允许读取导入历史")
    if not record["import_allowed"]:
        raise WolaiImportError("选中文档超过大小限制")


def _note_state(target: Path, member: str) -> str:
    if not target.is_file():
        return "missing"
    try:
        if target.stat().st_size > MAX_NOTE_BYTES:
            return "unmanaged"
        raw = target.read_bytes()
        metadata, body = parse_text(raw.decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        return "unmanaged"
    if metadata.get("source_ref") != member:
        return "unmanaged"
    if metadata.get("content_sha256") != hashlib.sha256(body.encode("utf-8")).hexdigest():
        return "unmanaged"
    try:
        return "managed" if _render(metadata, body) == raw else "unmanaged"
    except (TypeError, ValueError):
        return "unmanaged"


def _versions(history_dir: Path, current_sha: str | None) -> list[dict[str, Any]]:
    versions: list[dict[str, Any]] = []
    if history_dir.is_dir():
        for path in history_dir.iterdir():
            if not path.is_file() or not _VERSION_NAME.fullmatch(path.name):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            sha = path.stem
            versions.append({
                "sha256": sha, "bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, SHANGHAI).isoformat(),
                "current": sha == current_sha,
            })
    versions.sort(key=lambda item: item["modified_at"], reverse=True)
    return versions


def list_wolai_import_history(
    archive_path: Path, selected_paths: list[str], vault_dir: Path, database: Database,
) -> dict[str, Any]:
    """Read-only history inventory for the selected notes (no note bodies)."""
    _validate_selection(selected_paths)
    vault_dir = _local_path(Path(vault_dir))
    digests = database.document_digests()
    notes: list[dict[str, Any]] = []
    with _open_archive(archive_path) as (path, _archive, records):
        for member in selected_paths:
            _require_member(records, member)
        for member in selected_paths:
            source_id = _source_id(member)
            target = _local_path(vault_dir / IMPORT_DIRECTORY / f"{source_id}.md")
            current_sha = None
            if target.is_file():
                try:
                    current_sha = hashlib.sha256(target.read_bytes()).hexdigest()
                except OSError:
                    current_sha = None
            history_dir = _local_path(vault_dir / HISTORY_DIRECTORY / source_id)
            vault_path = f"{IMPORT_DIRECTORY}/{source_id}.md"
            if not target.is_file():
                index_state = "missing"
            elif current_sha and digests.get(vault_path) == current_sha:
                index_state = "current"
            else:
                index_state = "stale"
            notes.append({
                "source": member,
                "vault_path": vault_path,
                "state": _note_state(target, member),
                "index_state": index_state,
                "current_sha256": current_sha,
                "versions": _versions(history_dir, current_sha),
            })
        result = {
            "format": "torch-os-wolai-import-history", "version": 1,
            "archive_filename": path.name, "selected_paths": list(selected_paths),
            "notes": notes,
            "summary": {
                "notes": len(notes),
                "versions": sum(len(note["versions"]) for note in notes),
                "notes_missing": sum(note["state"] == "missing" for note in notes),
                "notes_unmanaged": sum(note["state"] == "unmanaged" for note in notes),
                "notes_stale": sum(note["index_state"] == "stale" for note in notes),
            },
            "writes": False,
            "warnings": [
                "只读取 .wolai-import-history/ 的文件名、大小与修改时间，不展示历史正文，不改任何文件。",
                "恢复某个版本前，当前文件会先按内容哈希存进同一历史目录，因此不会丢内容。",
                "index_state=stale 表示本地索引里的内容与当前文件不一致：扫描后才会刷新候选（旧候选归档、新候选待审核）。",
            ],
        }
    return result


def restore_wolai_import_history(
    archive_path: Path, source_path: str, sha256: str, vault_dir: Path, database: Database,
) -> dict[str, Any]:
    """Restore one managed history version; the current file is archived first."""
    if not isinstance(source_path, str) or not source_path or source_path.casefold() in {"all", "*"}:
        raise WolaiImportError("必须明确指定一篇 Markdown")
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise WolaiImportError("版本哈希必须是 64 位小写十六进制")
    vault_dir = _local_path(Path(vault_dir))
    with _open_archive(archive_path) as (path, _archive, records):
        _require_member(records, source_path)
        archive_name = path.name
    source_id = _source_id(source_path)
    target = _local_path(vault_dir / IMPORT_DIRECTORY / f"{source_id}.md")
    history_dir = _local_path(vault_dir / HISTORY_DIRECTORY / source_id)
    version_path = _local_path(history_dir / f"{sha256}.md")
    if not version_path.is_file() or version_path.stat().st_size > MAX_NOTE_BYTES:
        raise WolaiImportError("这个历史版本不存在或超过大小限制")
    raw = version_path.read_bytes()
    try:
        metadata, _body = parse_text(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise WolaiImportError("历史版本格式不兼容，未恢复") from exc
    if metadata.get("id") != source_id or metadata.get("source_ref") != source_path:
        raise WolaiImportError("历史版本不属于这篇来源，未恢复")
    state_before = _note_state(target, source_path)
    previous_sha = None
    archived_path = None
    if target.is_file():
        current = target.read_bytes()
        previous_sha = hashlib.sha256(current).hexdigest()
        if previous_sha == sha256:
            raise WolaiImportError("当前文件就是这个历史版本，无需恢复")
        if _publish_new_file(current, _local_path(history_dir / f"{previous_sha}.md")):
            archived_path = f"{HISTORY_DIRECTORY}/{source_id}/{previous_sha}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".wolai-history-", dir=target.parent) as temporary:
        staged = Path(temporary) / "note.md"
        staged.write_bytes(raw)
        staged.replace(target)
    database.audit("wolai_history_restored", {
        "source_id": source_id, "previous_sha256": previous_sha, "restored_sha256": sha256,
    })
    return {
        "format": "torch-os-wolai-history-restore", "version": 1,
        "archive_filename": archive_name, "source": source_path,
        "vault_path": f"{IMPORT_DIRECTORY}/{source_id}.md", "state_before": state_before,
        "previous_sha256": previous_sha, "restored_sha256": sha256,
        "previous_archived_path": archived_path, "writes": True,
        "warning": "恢复只改这篇受管笔记；当前文件已先存入历史。下次扫描会按“来源变化”重新生成候选，旧候选归档。",
    }


def preview_wolai_history_cleanup(
    archive_path: Path, selected_paths: list[str], vault_dir: Path,
    *, keep_newest: int = CLEANUP_KEEP_NEWEST, min_age_days: int = CLEANUP_MIN_AGE_DAYS,
) -> dict[str, Any]:
    """Read-only retention preview: newest N and young versions are kept."""
    _validate_selection(selected_paths)
    keep = max(1, int(keep_newest))
    young = max(1, int(min_age_days))
    vault_dir = _local_path(Path(vault_dir))
    now = datetime.now(SHANGHAI)
    notes: list[dict[str, Any]] = []
    totals = {"versions": 0, "bytes": 0, "cleanable": 0, "cleanable_bytes": 0}
    with _open_archive(archive_path) as (path, _archive, records):
        for member in selected_paths:
            _require_member(records, member)
        for member in selected_paths:
            source_id = _source_id(member)
            history_dir = _local_path(vault_dir / HISTORY_DIRECTORY / source_id)
            versions = _versions(history_dir, None)
            items = []
            note_cleanable = 0
            note_cleanable_bytes = 0
            for index, version in enumerate(versions):
                modified = datetime.fromisoformat(str(version["modified_at"]))
                age_days = max(0, (now - modified).days)
                if index < keep:
                    protected, reason = True, f"最近 {keep} 个版本之一，保留"
                elif age_days < young:
                    protected, reason = True, f"{young} 天内，保留"
                else:
                    protected, reason = False, "超过保留窗口且不是最近版本，可清理（预览）"
                    note_cleanable += 1
                    note_cleanable_bytes += int(version["bytes"])
                items.append({**version, "age_days": age_days, "keep": protected, "reason": reason})
            totals["versions"] += len(items)
            totals["bytes"] += sum(int(item["bytes"]) for item in items)
            totals["cleanable"] += note_cleanable
            totals["cleanable_bytes"] += note_cleanable_bytes
            notes.append({
                "source": member,
                "vault_path": f"{IMPORT_DIRECTORY}/{source_id}.md",
                "versions": len(items), "bytes": sum(int(item["bytes"]) for item in items),
                "cleanable": note_cleanable, "cleanable_bytes": note_cleanable_bytes,
                "items": items,
            })
        result = {
            "format": "torch-os-wolai-history-cleanup-preview", "version": 1,
            "archive_filename": path.name, "selected_paths": list(selected_paths),
            "policy": {"keep_newest": keep, "min_age_days": young},
            "notes": notes,
            "summary": {"notes": len(notes), **totals},
            "writes": False,
            "note": "这只是预览：不会删除任何文件；是否执行清理仍等你决定。",
            "warnings": [
                f"保留策略：每篇笔记最近 {keep} 个版本、以及 {young} 天内修改过的版本一律保留。",
                "清理只会删除 .wolai-import-history/ 里的旧副本，不影响当前笔记、原 ZIP 或候选记忆。",
            ],
        }
    return result
