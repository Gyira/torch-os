"""Preview Wolai ZIP metadata and import explicitly selected, ordinary notes.

Archive content is source material only. This module does not execute source
instructions, contact a model, fetch links, or extract attachments.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import threading
import zipfile
import zlib
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .backup import BackupError, _fingerprint, _safe_path
from .db import Database
from .vault import MAX_NOTE_BYTES, _memory_from_document, parse_text


MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 5_000
MAX_COMPRESSION_RATIO = 200
MAX_IMPORT_NOTES = 20
IMPORT_DIRECTORY = "80-Wolai"
HISTORY_DIRECTORY = ".wolai-import-history"
_CHUNK = 1024 * 1024
_IMPORT_LOCK = threading.Lock()
_SENSITIVITY = {
    "relationship": ("恋爱", "另一半", "爱情", "伴侣", "婚姻", "亲密关系", "relationship", "romance", "romantic", "partner", "spouse", "intimate", "love"),
    "family": ("家人", "家庭", "family", "families"),
    "health": ("健康", "身体", "医疗", "病历", "体检", "health", "medical", "medicine", "therapy", "body", "wellbeing"),
}
_INSTRUCTION_NAMES = ("ai宪法", "ai 宪法", "宪法", "系统提示", "系统指令", "提示词", "constitution", "system prompt", "system_prompt", "instruction")
_WARNINGS = [
    "目录分类只是隐私建议，不代表已审查正文；普通目录也可能含敏感资料。",
    "预览不解读 Markdown 正文，不打开附件，不访问外部链接，不向模型发送内容。",
    "AI 宪法、提示词和其他文档指令始终作为不可信来源资料，不构成系统授权。",
]


class WolaiImportError(ValueError):
    """The archive or requested import does not meet the local import contract."""


def _local_path(path: Path) -> Path:
    try:
        return _safe_path(path)
    except (BackupError, OSError) as exc:
        raise WolaiImportError("不接受符号链接、junction 或越界路径") from exc


def _member_path(name: str, is_directory: bool) -> PurePosixPath:
    if not name or "\\" in name or ":" in name or any(ord(char) < 32 for char in name):
        raise WolaiImportError("ZIP 中存在不安全路径")
    text = name[:-1] if is_directory and name.endswith("/") else name
    path = PurePosixPath(text)
    if not text or path.is_absolute() or str(path) != text or len(text) > 2048:
        raise WolaiImportError("ZIP 中存在绝对路径或非规范路径")
    if any(part in {".", ".."} or part.endswith((".", " ")) for part in path.parts):
        raise WolaiImportError("ZIP 中存在越界路径")
    return path


def _categories(name: str) -> list[str]:
    folded = name.casefold()
    return [category for category, words in _SENSITIVITY.items() if any(word in folded for word in words)]


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_index(archive: zipfile.ZipFile) -> list[dict[str, Any]]:
    infos = archive.infolist()
    if not infos or len(infos) > MAX_ENTRIES:
        raise WolaiImportError("ZIP 文件数量为空或超过限制")
    seen: set[str] = set()
    file_paths: set[str] = set()
    records = []
    total = 0
    for info in infos:
        if info.filename != info.orig_filename:
            raise WolaiImportError("ZIP 文件名包含非法字符")
        directory = info.is_dir()
        path = _member_path(info.filename, directory)
        normalized = str(path).casefold()
        if normalized in seen:
            raise WolaiImportError("ZIP 存在重复路径或大小写冲突")
        seen.add(normalized)
        mode = stat.S_IFMT(info.external_attr >> 16)
        if mode not in {0, stat.S_IFDIR if directory else stat.S_IFREG} or info.flag_bits & 1:
            raise WolaiImportError("ZIP 包含链接、特殊文件或加密项")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise WolaiImportError("ZIP 压缩方式不受支持")
        if directory and info.file_size:
            raise WolaiImportError("ZIP 目录项包含异常数据")
        if info.file_size > MAX_MEMBER_BYTES or info.file_size > max(info.compress_size, 1) * MAX_COMPRESSION_RATIO:
            raise WolaiImportError("ZIP 展开大小或压缩比超过限制")
        total += info.file_size
        if total > MAX_EXPANDED_BYTES:
            raise WolaiImportError("ZIP 总展开大小超过限制")
        if not directory:
            file_paths.add(normalized)
        kind = "directory" if directory else "markdown" if path.suffix.casefold() == ".md" else "attachment"
        categories = _categories(info.filename)
        instruction_like = any(word in info.filename.casefold() for word in _INSTRUCTION_NAMES)
        warnings = []
        if categories:
            warnings.append("建议隔离保存；禁止直接进入普通 Vault")
        if instruction_like:
            warnings.append("指令类资料仅作为文档内容，不执行或授权")
        if kind == "markdown" and info.file_size > MAX_NOTE_BYTES:
            warnings.append("单篇 Markdown 超过 1 MiB，不能导入")
        records.append({
            "path": info.filename, "size": info.file_size, "compressed_size": info.compress_size,
            "type": kind, "sensitive": bool(categories), "sensitivity_categories": categories,
            "instruction_like": instruction_like,
            "import_allowed": kind == "markdown" and not categories and info.file_size <= MAX_NOTE_BYTES,
            "warnings": warnings,
        })
    for record in records:
        path = _member_path(record["path"], record["type"] == "directory")
        if any(str(parent).casefold() in file_paths for parent in path.parents if str(parent) != "."):
            raise WolaiImportError("ZIP 文件与目录路径冲突")
    return records


@contextmanager
def _open_archive(archive_path: Path):
    archive_path = _local_path(archive_path)
    try:
        initial = archive_path.stat()
        if not stat.S_ISREG(initial.st_mode) or initial.st_size > MAX_ARCHIVE_BYTES:
            raise WolaiImportError("ZIP 必须是大小未超限的普通文件")
        with archive_path.open("rb") as stream, zipfile.ZipFile(stream) as archive:
            if _fingerprint(os.fstat(stream.fileno())) != _fingerprint(initial):
                raise WolaiImportError("ZIP 在打开时发生变化")
            records = _archive_index(archive)
            yield archive_path, archive, records
            if _fingerprint(os.fstat(stream.fileno())) != _fingerprint(initial) or _fingerprint(_local_path(archive_path).stat()) != _fingerprint(initial):
                raise WolaiImportError("ZIP 在处理过程中发生变化")
    except WolaiImportError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError) as exc:
        raise WolaiImportError("无法安全读取 Wolai ZIP") from exc


def _report(path: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    attachments = [record["path"] for record in records if record["type"] == "attachment"]
    sensitive = [{"path": record["path"], "categories": record["sensitivity_categories"], "recommendation": "isolate_and_review"} for record in records if record["sensitive"] and record["type"] != "directory"]
    return {
        "format": "torch-os-wolai-inspection", "version": 1,
        "archive_filename": path.name, "archive_sha256": _hash_file(path),
        "archive_bytes": path.stat().st_size, "expanded_bytes": sum(record["size"] for record in records),
        "entry_count": len(records), "file_count": sum(record["type"] != "directory" for record in records),
        "markdown_count": sum(record["type"] == "markdown" for record in records),
        "attachment_count": len(attachments), "attachments": attachments,
        "sensitive_count": len(sensitive), "sensitive_suggestions": sensitive,
        "files": records, "content_verified": False, "warnings": list(_WARNINGS),
    }


def inspect_wolai_archive(archive_path: Path) -> dict[str, Any]:
    """Inspect the complete ZIP directory without reading/decompressing its bodies."""
    with _open_archive(archive_path) as (path, _archive, records):
        result = _report(path, records)
    return result


def preview_wolai_archive(archive_path: Path, output_dir: Path) -> dict[str, Any]:
    """Write a metadata-only manifest to a caller-configured local imports directory."""
    result = inspect_wolai_archive(archive_path)
    output_dir = _local_path(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        _local_path(output_dir)
        manifest_path = output_dir / f"manifest-{result['archive_sha256'][:16]}.json"
        result["preview_mode"] = "metadata_only"
        result["selected_notes"] = []
        raw = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if manifest_path.exists():
            _local_path(manifest_path)
            if manifest_path.read_bytes() != raw:
                raise WolaiImportError("同名预览清单已存在且内容不同，未覆盖")
        else:
            with tempfile.TemporaryDirectory(prefix=".wolai-preview-", dir=output_dir) as temporary:
                staged = Path(temporary) / "manifest.json"
                staged.write_bytes(raw)
                os.link(staged, manifest_path)
        return {**result, "manifest_path": str(manifest_path)}
    except WolaiImportError:
        raise
    except OSError as exc:
        raise WolaiImportError("无法保存 Wolai 预览清单") from exc


def _source_id(member_path: str) -> str:
    return "wolai_" + hashlib.sha256(member_path.encode("utf-8")).hexdigest()


def preview_import_states(archive_path: Path, selected_paths: list[str], vault_dir: Path) -> dict[str, Any]:
    """Read-only classification of the selection against the current vault (no writes)."""
    if not isinstance(selected_paths, list) or not 1 <= len(selected_paths) <= MAX_IMPORT_NOTES:
        raise WolaiImportError("必须明确选择 1 至 20 篇 Markdown")
    if len(set(selected_paths)) != len(selected_paths):
        raise WolaiImportError("选择列表包含重复路径")
    vault_dir = _local_path(vault_dir)
    counts = {"new": 0, "unchanged": 0, "updated": 0, "user-edited": 0}
    items: list[dict[str, Any]] = []
    with _open_archive(archive_path) as (path, archive, records):
        by_path = {record["path"]: record for record in records}
        for member in selected_paths:
            record = by_path.get(member)
            if not record or record["type"] != "markdown":
                raise WolaiImportError("选中路径不存在或不是 Markdown")
            if record["sensitive"]:
                raise WolaiImportError("恋爱、家庭或健康等敏感资料不能导入普通 Vault")
            if not record["import_allowed"]:
                raise WolaiImportError("选中文档超过导入大小限制")
            with archive.open(member) as stream:
                source = stream.read(MAX_NOTE_BYTES + 1)
            note = _prepare_note(member, source)
            target = _local_path(vault_dir / note["relative_path"])
            if not target.exists():
                state = "new"
            elif target.read_bytes() == note["raw"]:
                state = "unchanged"
            else:
                try:
                    _read_owned_target(target, note)
                except WolaiImportError:
                    # 本地被改过、元信息不匹配或格式不兼容：导入会拒绝覆盖。
                    state = "user-edited"
                else:
                    state = "updated"
            counts[state] += 1
            items.append({"path": member, "vault_path": note["relative_path"], "state": state})
    return {
        "selected_count": len(items),
        "counts": counts,
        "items": items,
        "note": "只读检查：没有写入任何文件。未变化=内容一致；已更新=旧版本会先存入导入历史再替换，新候选需要重新审核；已被你编辑=不会覆盖。",
    }


def _render(metadata: dict[str, Any], body: str) -> bytes:
    return ("---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n\n" + body + "\n").encode("utf-8")


def _prepare_note(member_path: str, source: bytes) -> dict[str, Any]:
    if len(source) > MAX_NOTE_BYTES:
        raise WolaiImportError("单篇 Markdown 超过 1 MiB")
    try:
        body = source.decode("utf-8-sig").replace("\r\n", "\n").strip()
    except UnicodeDecodeError as exc:
        raise WolaiImportError("仅支持 UTF-8 Markdown，请先在本地确认编码") from exc
    if not body or "\x00" in body:
        raise WolaiImportError("Markdown 为空或包含二进制字符")
    source_id = _source_id(member_path)
    # Original frontmatter stays inside the body. It cannot set identity, trust,
    # memory status, scope, roles, or any importer-owned metadata.
    metadata = {
        "id": source_id, "type": "note", "title": PurePosixPath(member_path).stem[:200],
        "source_kind": "imported", "source_ref": member_path,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "source_trust": "untrusted_document", "visibility": "private",
    }
    raw = _render(metadata, body)
    if len(raw) > MAX_NOTE_BYTES:
        raise WolaiImportError("Markdown 添加来源信息后超过 1 MiB")
    return {"member_path": member_path, "source_id": source_id, "relative_path": f"{IMPORT_DIRECTORY}/{source_id}.md", "metadata": metadata, "body": body, "raw": raw}


def _read_owned_target(path: Path, note: dict[str, Any]) -> bytes | None:
    _local_path(path)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > MAX_NOTE_BYTES:
        raise WolaiImportError("导入目标已被其他文件占用")
    raw = path.read_bytes()
    try:
        metadata, body = parse_text(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError, yaml.YAMLError, RecursionError) as exc:
        raise WolaiImportError("已有导入文件被修改或格式不兼容，未覆盖") from exc
    expected = note["metadata"]
    stable_keys = {"id", "type", "title", "source_kind", "source_ref", "source_trust", "visibility"}
    if set(metadata) != set(expected) or any(metadata.get(key) != expected[key] for key in stable_keys):
        raise WolaiImportError("已有导入文件的来源或元信息被修改，未覆盖")
    if metadata.get("content_sha256") != hashlib.sha256(body.encode("utf-8")).hexdigest() or not re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("source_sha256", ""))) or _render(metadata, body) != raw:
        raise WolaiImportError("已有导入文件存在本地编辑，未覆盖")
    return raw


class _ImportTransaction(Database):
    """Reuse normal source revision/history operations in one outer transaction."""

    def __init__(self, database: Database, connection: sqlite3.Connection):
        super().__init__(database.path)
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection


def import_selected_notes(archive_path: Path, selected_paths: list[str], vault_dir: Path, database: Database) -> dict[str, Any]:
    """Import 1–20 explicitly selected ordinary Markdown notes as candidates only."""
    if not isinstance(selected_paths, list) or not 1 <= len(selected_paths) <= MAX_IMPORT_NOTES or any(not isinstance(path, str) or path.casefold() in {"all", "*"} for path in selected_paths):
        raise WolaiImportError("必须明确选择 1 至 20 篇 Markdown，不支持全部导入")
    if len(set(selected_paths)) != len(selected_paths):
        raise WolaiImportError("选择列表包含重复路径")
    notes = []
    with _open_archive(archive_path) as (path, archive, records):
        by_path = {record["path"]: record for record in records}
        for member in selected_paths:
            record = by_path.get(member)
            if not record or record["type"] != "markdown":
                raise WolaiImportError("选中路径不存在或不是 Markdown")
            if record["sensitive"]:
                raise WolaiImportError("恋爱、家庭或健康等敏感资料不能导入普通 Vault")
            if not record["import_allowed"]:
                raise WolaiImportError("选中文档超过导入大小限制")
        # Validate the whole selection before reading any selected body or writing files.
        for member in selected_paths:
            with archive.open(member) as stream:
                source = stream.read(MAX_NOTE_BYTES + 1)
            notes.append(_prepare_note(member, source))
        archive_digest = _hash_file(path)
    vault_dir = _local_path(vault_dir)
    published: list[tuple[Path, bytes | None, bytes]] = []
    history_created: list[Path] = []
    results = []
    committed = False
    with _IMPORT_LOCK:
        try:
            vault_dir.mkdir(parents=True, exist_ok=True)
            folder = _local_path(vault_dir / IMPORT_DIRECTORY)
            folder.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".wolai-import-", dir=vault_dir) as temporary:
                stage = Path(temporary)
                with database.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    transaction = _ImportTransaction(database, connection)
                    # Verify every destination before publishing the first selected file.
                    prior = {note["source_id"]: _read_owned_target(folder / f"{note['source_id']}.md", note) for note in notes}
                    for note in notes:
                        target = _local_path(vault_dir / note["relative_path"])
                        previous = prior[note["source_id"]]
                        if previous != note["raw"]:
                            if previous is not None:
                                previous_digest = hashlib.sha256(previous).hexdigest()
                                history = _local_path(vault_dir / HISTORY_DIRECTORY / note["source_id"] / f"{previous_digest}.md")
                                history.parent.mkdir(parents=True, exist_ok=True)
                                if history.exists():
                                    if history.read_bytes() != previous:
                                        raise WolaiImportError("导入历史副本冲突，未覆盖")
                                else:
                                    with history.open("xb") as stream:
                                        stream.write(previous)
                                    history_created.append(history)
                            staged = stage / f"{note['source_id']}.md"
                            staged.write_bytes(note["raw"])
                            # Recheck before replacement so ordinary edits are never silently lost.
                            if _read_owned_target(target, note) != previous:
                                raise WolaiImportError("导入目标在操作期间发生变化")
                            staged.replace(target)
                            published.append((target, previous, note["raw"]))
                        document_id, changed = transaction.upsert_document(note["relative_path"], note["metadata"]["title"], note["body"], hashlib.sha256(note["raw"]).hexdigest(), note["metadata"])
                        if changed:
                            transaction.replace_document_memories(document_id, _memory_from_document(note["metadata"], note["body"], target))
                        if not changed:
                            change = "unchanged"
                        elif previous is None:
                            change = "created"
                        else:
                            change = "updated"
                        history_path = None
                        if change == "updated":
                            history_path = f"{HISTORY_DIRECTORY}/{note['source_id']}"
                        results.append({"source_path": note["member_path"], "vault_path": note["relative_path"],
                                        "document_id": document_id,
                                        "status": "imported" if changed else "unchanged",
                                        "change": change, "history_path": history_path})
                    transaction.audit("wolai_notes_imported", {"archive_sha256": archive_digest, "selected_count": len(notes), "changed_count": sum(item["status"] == "imported" for item in results), "document_ids": [item["document_id"] for item in results]})
                committed = True
            return {
                "selected_count": len(notes),
                "imported": sum(item["status"] == "imported" for item in results),
                "unchanged": sum(item["status"] == "unchanged" for item in results),
                "created": sum(item["change"] == "created" for item in results),
                "updated": sum(item["change"] == "updated" for item in results),
                "notes": results,
                "warnings": ["导入内容仅生成待确认候选，不会自动成为已确认记忆。",
                             "本地历史副本会保留旧内容，撤回不会自动删除历史。",
                             "内容有变化的来源：旧候选已归档，新候选需要重新审核（不会自动确认）。"],
            }
        except Exception as exc:
            if committed:
                raise WolaiImportError("导入已提交，但暂存目录清理失败；请先检查结果，不要重复覆盖文件") from exc
            # Database.connect rolls back on exception; restore only files we published.
            recovery_failed = False
            for target, previous, new in reversed(published):
                try:
                    _local_path(target)
                    if not target.exists() or target.read_bytes() != new:
                        recovery_failed = True
                        continue
                    if previous is None:
                        target.unlink()
                    else:
                        target.write_bytes(previous)
                except (OSError, WolaiImportError):
                    recovery_failed = True
            if not recovery_failed:
                for history in history_created:
                    try:
                        _local_path(history).unlink()
                    except OSError:
                        pass
            if recovery_failed:
                raise WolaiImportError("导入失败且目标发生并发变化；数据库已回滚，请检查文件和导入历史副本") from exc
            if isinstance(exc, WolaiImportError):
                raise
            raise WolaiImportError("导入失败，数据库与本次文件修改已回滚") from exc
