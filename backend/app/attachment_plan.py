"""Read-only plan for localizing Wolai attachments (preview only; no writes).

The plan reads the explicitly selected Markdown bodies and the referenced
attachment bytes (bounded, for SHA-256 naming only). It never writes files,
never rewrites notes, never opens links, and never contacts a model or network.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .link_audit import _links, _resolve
from .vault import MAX_NOTE_BYTES
from .wolai_import import (
    IMPORT_DIRECTORY,
    MAX_IMPORT_NOTES,
    WolaiImportError,
    _hash_file,
    _local_path,
    _open_archive,
    _prepare_note,
    _read_owned_target,
)


MAX_PLAN_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_PLAN_TOTAL_BYTES = 100 * 1024 * 1024
ATTACHMENT_DIRECTORY = f"{IMPORT_DIRECTORY}/attachments"
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WARNINGS = [
    "这只是预览：不复制附件、不修改笔记、不改原 ZIP、不联网、不调用模型。",
    "附件按内容哈希命名并去重；执行时会复制到 <Vault>/80-Wolai/attachments/，并把笔记里的对应链接改写为本地相对路径。",
    "第一批只支持单行内联链接/图片（含可选标题与尖括号形式）；转义、跨行与引用式链接会跳过并在计划里注明。",
    "敏感目录里的附件不参与本地化；原 ZIP 与原始导出文件始终不动。",
    "执行时只处理已导入且未被修改的笔记；未导入或已被修改的笔记需要先重新导入。",
]


def _safe_basename(resolved_path: str) -> str:
    name = PurePosixPath(resolved_path).name
    cleaned = _UNSAFE_NAME.sub("_", name).strip(" .")
    if not cleaned:
        cleaned = "attachment"
    stem, dot, suffix = cleaned.rpartition(".")
    if dot and len(suffix) <= 12:
        return (stem[:60] or "attachment") + "." + suffix
    return cleaned[:64]


def _rewrite_line(raw_line: str, target: str, new_target: str) -> str | None:
    """Return the rewritten line for the supported inline forms, else ``None``."""
    for prefix, suffix in (("](", ")"), ("](<", ">)")):
        marker = prefix + target + suffix
        if marker in raw_line:
            return raw_line.replace(marker, prefix + new_target + suffix, 1)
    for quote in ('"', "'"):
        if "](" + target + " " + quote in raw_line:
            return raw_line.replace("](" + target, "](" + new_target, 1)
        if "](<" + target + "> " + quote in raw_line:
            return raw_line.replace("](<" + target + ">", "](<" + new_target + ">", 1)
    return None


def plan_wolai_attachment_localization(
    archive_path: Path, selected_paths: list[str], vault_dir: Path,
) -> dict[str, Any]:
    """Return the bounded, read-only localization plan for 1–20 selected notes."""
    if not isinstance(selected_paths, list) or not 1 <= len(selected_paths) <= MAX_IMPORT_NOTES or any(
        not isinstance(path, str) or path.casefold() in {"all", "*"} for path in selected_paths
    ):
        raise WolaiImportError("必须明确选择 1 至 20 篇 Markdown")
    if len(set(selected_paths)) != len(selected_paths):
        raise WolaiImportError("选择列表包含重复路径")
    vault_dir = _local_path(vault_dir)
    notes: list[dict[str, Any]] = []
    unique_sizes: dict[str, int] = {}
    stored_names: dict[str, str] = {}
    unique_bytes: dict[str, int] = {}
    with _open_archive(archive_path) as (path, archive, records):
        by_path = {record["path"].rstrip("/"): record for record in records}
        for member in selected_paths:
            record = by_path.get(member)
            if not record or record["type"] != "markdown":
                raise WolaiImportError("选中路径不存在或不是 Markdown")
            if record["sensitive"]:
                raise WolaiImportError("敏感目录的 Markdown 不允许读取正文进行附件预览")
            if not record["import_allowed"]:
                raise WolaiImportError("选中文档超过大小限制")
        for member in selected_paths:
            with archive.open(member) as stream:
                source = stream.read(MAX_NOTE_BYTES + 1)
            note = _prepare_note(member, source)
            target = _local_path(vault_dir / note["relative_path"])
            if not target.exists():
                state, state_reason = "not-imported", "先在导入页导入这篇记录，再执行附件本地化。"
            else:
                try:
                    owned = _read_owned_target(target, note)
                except WolaiImportError:
                    state, state_reason = "user-edited", "这篇导入记录被修改过或元信息不匹配；先重新导入再本地化。"
                else:
                    if owned == note["raw"]:
                        state, state_reason = "ready", None
                    else:
                        state, state_reason = "modified", "本地文件与导出包版本不同（可能已本地化或修改过）；先重新导入再本地化。"
            lines = note["body"].splitlines()
            items: list[dict[str, Any]] = []
            for link in _links(note["body"])[0]:
                classification, resolved, _warnings = _resolve(member, link["target"], by_path)
                if not link["parsed"] and classification != "unsafe":
                    classification, resolved = "manual_review", None
                if classification != "resolved_attachment" or not resolved:
                    continue
                record = by_path.get(resolved)
                size = int(record["size"]) if record else 0
                item = {
                    "line": link["line"], "target": link["target"], "resolved_path": resolved,
                    "size": size, "supported": False, "skipped_reason": None,
                    "copy_to": None, "new_target": None,
                }
                if size > MAX_PLAN_ATTACHMENT_BYTES:
                    item["skipped_reason"] = "单个附件超过 25 MiB，第一批不本地化。"
                elif sum(unique_sizes.values()) + (0 if resolved in unique_sizes else size) > MAX_PLAN_TOTAL_BYTES:
                    item["skipped_reason"] = "本批附件合计超过 100 MiB，第一批不本地化。"
                else:
                    raw_line = lines[link["line"] - 1] if 1 <= link["line"] <= len(lines) else ""
                    with archive.open(resolved) as stream:
                        payload = stream.read(size + 1)
                    if len(payload) != size:
                        raise WolaiImportError("附件读取结果与清单不一致")
                    digest = hashlib.sha256(payload).hexdigest()
                    stored_name = stored_names.get(digest)
                    if stored_name is None:
                        stored_name = f"{digest[:12]}-{_safe_basename(resolved)}"
                        stored_names[digest] = stored_name
                    unique_bytes[digest] = size
                    new_target = f"attachments/{stored_name}"
                    rewritten = _rewrite_line(raw_line, link["target"], new_target)
                    if rewritten is None:
                        item["skipped_reason"] = "链接写法不在第一批支持范围（转义/跨行/引用式），未规划改写。"
                    else:
                        item.update({
                            "supported": True, "copy_to": f"{ATTACHMENT_DIRECTORY}/{stored_name}",
                            "new_target": new_target,
                        })
                        unique_sizes.setdefault(resolved, size)
                items.append(item)
            supported = sum(1 for item in items if item["supported"])
            skipped = sum(1 for item in items if not item["supported"])
            notes.append({
                "source": member, "vault_path": note["relative_path"], "state": state, "state_reason": state_reason,
                "plannable": state == "ready" and skipped == 0,
                "attachments": len(items), "supported": supported, "skipped": skipped,
                "items": items,
            })
        result = {
            "format": "torch-os-wolai-attachment-plan", "version": 1,
            "archive_filename": path.name, "archive_sha256": _hash_file(path),
            "selected_paths": list(selected_paths), "notes": notes,
            "summary": {
                "notes": len(notes),
                "attachments": sum(note["attachments"] for note in notes),
                "unique_attachments": len(unique_bytes),
                "copy_bytes": sum(unique_bytes.values()),
                "supported": sum(note["supported"] for note in notes),
                "skipped": sum(note["skipped"] for note in notes),
                "notes_ready": sum(note["state"] == "ready" for note in notes),
                "notes_needing_import": sum(note["state"] == "not-imported" for note in notes),
                "notes_blocked": sum(note["state"] in {"user-edited", "modified"} for note in notes),
            },
            "writes": False,
            "note": "预览读取附件字节只为计算 SHA-256，不解析、不展示、不写出附件内容。",
            "warnings": list(_WARNINGS),
        }
    return result
