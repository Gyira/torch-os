"""Apply the attachment-localization plan: copy attachments and rewrite links.

Only already imported, unmodified notes are processed. Every write happens
after a full validation pass; failures restore the previous bytes and remove
files created by this run. The source ZIP is never modified.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .attachment_plan import ATTACHMENT_DIRECTORY, plan_wolai_attachment_localization, _rewrite_line
from .db import Database
from .vault import MAX_NOTE_BYTES, parse_text
from .wolai_import import (
    HISTORY_DIRECTORY,
    WolaiImportError,
    _local_path,
    _open_archive,
    _render,
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _publish_new_file(raw: bytes, target: Path) -> bool:
    """Create ``target`` exclusively; identical content is reused, different content fails."""
    _local_path(target)
    if target.exists():
        if not target.is_file() or target.read_bytes() != raw:
            raise WolaiImportError("目标文件已存在且内容不同，未覆盖")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".wolai-localize-", dir=target.parent) as temporary:
        staged = Path(temporary) / "payload"
        staged.write_bytes(raw)
        try:
            os.link(staged, target)
        except FileExistsError:
            _local_path(target)
            if not target.is_file() or target.read_bytes() != raw:
                raise WolaiImportError("并发写入的目标内容不同，未覆盖")
            return False
    return True


def _replace_note(target: Path, expected: bytes, new: bytes) -> None:
    _local_path(target)
    if not target.is_file() or target.read_bytes() != expected:
        raise WolaiImportError("笔记在操作期间发生变化，未覆盖")
    with tempfile.TemporaryDirectory(prefix=".wolai-localize-", dir=target.parent) as temporary:
        staged = Path(temporary) / "note.md"
        staged.write_bytes(new)
        staged.replace(target)


def localize_wolai_attachments(
    archive_path: Path, selected_paths: list[str], vault_dir: Path, database: Database,
) -> dict[str, Any]:
    """Copy referenced attachments and rewrite links for the plannable notes."""
    plan = plan_wolai_attachment_localization(archive_path, selected_paths, vault_dir)
    plannable = [note for note in plan["notes"] if note["plannable"]]
    skipped = [note for note in plan["notes"] if not note["plannable"]]
    if not plannable:
        raise WolaiImportError("所选记录都没有可本地化的附件：请先在导入页导入，或重新导入后再试")
    vault_dir = _local_path(Path(vault_dir))
    specs: dict[str, dict[str, Any]] = {}
    for note in plannable:
        for item in note["items"]:
            if item["supported"]:
                specs.setdefault(item["resolved_path"], {
                    "copy_to": item["copy_to"],
                    "stored_name": PurePosixPath(item["copy_to"]).name,
                    "size": int(item["size"]),
                })
    created: list[Path] = []
    replaced: list[tuple[Path, bytes]] = []
    attachment_results: list[dict[str, Any]] = []
    note_results: list[dict[str, Any]] = []
    try:
        payloads: dict[str, bytes] = {}
        with _open_archive(archive_path) as (_path, archive, records):
            by_path = {record["path"].rstrip("/"): record for record in records}
            for resolved, spec in specs.items():
                record = by_path.get(resolved)
                if not record or record["type"] != "attachment":
                    raise WolaiImportError("附件在导出包中不存在或类型不符，请重新生成计划")
                with archive.open(resolved) as stream:
                    payload = stream.read(int(record["size"]) + 1)
                if len(payload) != int(record["size"]) or len(payload) != spec["size"]:
                    raise WolaiImportError("附件大小与预览不一致，请重新生成计划")
                if _sha256(payload)[:12] != spec["stored_name"][:12]:
                    raise WolaiImportError("附件内容与预览不一致，请重新生成计划")
                payloads[resolved] = payload
        copied_bytes = 0
        for resolved, spec in specs.items():
            target = _local_path(vault_dir / spec["copy_to"])
            was_created = _publish_new_file(payloads[resolved], target)
            if was_created:
                created.append(target)
                copied_bytes += len(payloads[resolved])
            attachment_results.append({
                "resolved_path": resolved, "copy_to": spec["copy_to"],
                "bytes": len(payloads[resolved]), "created": was_created,
            })
        for note in plannable:
            target = _local_path(vault_dir / note["vault_path"])
            if not target.is_file():
                raise WolaiImportError("笔记已不存在，请重新生成计划")
            current_raw = target.read_bytes()
            try:
                metadata, body = parse_text(current_raw.decode("utf-8-sig"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise WolaiImportError("笔记格式不兼容，未修改") from exc
            if metadata.get("content_sha256") != _sha256(body.encode("utf-8")):
                raise WolaiImportError("笔记在预览后发生变化，请重新生成计划")
            lines = body.splitlines()
            item_results = []
            for item in note["items"]:
                if not item["supported"]:
                    continue
                index = int(item["line"]) - 1
                if not 0 <= index < len(lines):
                    raise WolaiImportError("链接行号超出范围，请重新生成计划")
                rewritten = _rewrite_line(lines[index], item["target"], item["new_target"])
                if rewritten is None:
                    raise WolaiImportError("链接写法不再受支持，请重新生成计划")
                lines[index] = rewritten
                item_results.append({"line": item["line"], "target": item["target"], "new_target": item["new_target"]})
            new_body = "\n".join(lines)
            metadata["content_sha256"] = _sha256(new_body.encode("utf-8"))
            new_raw = _render(metadata, new_body)
            if len(new_raw) > MAX_NOTE_BYTES:
                raise WolaiImportError("改写后笔记超过 1 MiB，未修改")
            source_id = PurePosixPath(note["vault_path"]).stem
            history_target = _local_path(
                vault_dir / HISTORY_DIRECTORY / source_id / f"{_sha256(current_raw)}.md")
            if _publish_new_file(current_raw, history_target):
                created.append(history_target)
            _replace_note(target, current_raw, new_raw)
            replaced.append((target, current_raw))
            note_results.append({
                "source": note["source"], "vault_path": note["vault_path"], "status": "localized",
                "history_path": f"{HISTORY_DIRECTORY}/{source_id}/{_sha256(current_raw)}.md",
                "items": item_results,
            })
    except Exception as exc:
        recovery_failed = False
        for path, previous in reversed(replaced):
            try:
                _local_path(path)
                path.write_bytes(previous)
            except (OSError, WolaiImportError):
                recovery_failed = True
        if not recovery_failed:
            for path in reversed(created):
                try:
                    _local_path(path).unlink()
                except FileNotFoundError:
                    pass
                except (OSError, WolaiImportError):
                    recovery_failed = True
        if recovery_failed:
            raise WolaiImportError("本地化失败且回滚不完整；请检查 80-Wolai 与 .wolai-import-history") from exc
        if isinstance(exc, WolaiImportError):
            raise WolaiImportError(f"{exc}；本次本地化已回滚，未保留任何修改") from exc
        raise WolaiImportError("附件本地化失败，本次修改已回滚") from exc
    database.audit("wolai_attachments_localized", {
        "archive_sha256": plan["archive_sha256"], "notes": len(note_results),
        "attachments": len(specs), "bytes": sum(item["bytes"] for item in attachment_results),
    })
    return {
        "format": "torch-os-wolai-attachment-localization", "version": 1,
        "archive_filename": plan["archive_filename"], "archive_sha256": plan["archive_sha256"],
        "notes": note_results,
        "skipped_notes": [
            {"source": note["source"], "state": note["state"], "state_reason": note["state_reason"]}
            for note in skipped
        ],
        "attachments": attachment_results,
        "counts": {
            "notes_localized": len(note_results), "notes_skipped": len(skipped),
            "attachments_referenced": len(specs),
            "attachments_copied": sum(item["created"] for item in attachment_results),
            "attachments_reused": sum(not item["created"] for item in attachment_results),
            "bytes_copied": sum(item["bytes"] for item in attachment_results if item["created"]),
        },
        "writes": True,
        "warnings": [
            "原 ZIP、Wolai 原空间与其它文件未改动；旧笔记保存在 .wolai-import-history/ 下。",
            "下次扫描会按“来源变化”重新生成候选：旧候选归档，新候选需要你重新审核。",
            "重新导入同一篇记录会用导出包里的原始链接替换本地化版本（旧版本仍在历史里）。",
        ],
    }
