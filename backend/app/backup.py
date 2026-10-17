"""Bounded, verified local plaintext backups. Never reads model credentials."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import time
import uuid
import zipfile
import zlib
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from .db import Database


FORMAT = "torch-os-backup"
FORMAT_VERSION = 1
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_FILES = 10_000
MAX_COMPRESSION_RATIO = 200
BACKUP_TIMEOUT_SECONDS = 60
SUPPORTED_SQLITE_USER_VERSIONS = {0}
RESTORED_DATABASE_NAME = "torch_os.sqlite3"
RESTORED_NAME_PATTERN = re.compile(r"[0-9a-f]{32}")
SHANGHAI = ZoneInfo("Asia/Shanghai")
_CHUNK = 1024 * 1024
# `.torch-os` = 工作区的应用数据目录（索引/备份/诊断），和 `.obsidian` 一样不进备份快照。
_EXCLUDED_DIRS = {".git", ".obsidian", "__pycache__", "logs", ".torch-os"}
RELATIONSHIP_DIRS = {"relationship", "relationships", "relationship-vault", "relationship vault"}
_DEVICE_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_REQUIRED_COLUMNS = {
    "documents": {"id", "path", "title", "content", "sha256", "metadata_json", "indexed_at"},
    "memories": {"id", "document_id", "kind", "title", "content", "status", "source_quote"},
    "conversations": {"id", "title", "created_at", "updated_at"},
    "messages": {"id", "conversation_id", "role", "content", "citations_json"},
    "audit_events": {"id", "event_type", "detail_json", "created_at"},
    "goals": {"id", "title", "status", "progress"},
    "tasks": {"id", "goal_id", "title", "status"},
}


class BackupError(ValueError):
    """A backup is unsafe, unsupported, inconsistent, or exceeds a limit."""


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _safe_path(path: Path) -> Path:
    path = Path(path)
    if "\x00" in str(path) or ".." in path.parts:
        raise BackupError("Parent traversal is not allowed")
    absolute = path.absolute()
    for part in [*reversed(absolute.parents), absolute]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BackupError("Could not inspect backup path") from exc
        if _is_link(info):
            raise BackupError("Symbolic links and junctions are not allowed")
    return absolute


def _regular_file(path: Path) -> os.stat_result:
    path = _safe_path(path)
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise BackupError("Required file does not exist") from exc
    except OSError as exc:
        raise BackupError("Could not inspect backup file") from exc
    if not stat.S_ISREG(info.st_mode):
        raise BackupError("Expected a regular file")
    return info


def _fingerprint(info: os.stat_result) -> tuple[int, ...]:
    identity = info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    # Windows stat/fstat disagree on deprecated st_ctime (birth time versus write time).
    return identity if os.name == "nt" else (*identity, info.st_ctime_ns)


def _safe_member(name: str) -> PurePosixPath:
    if not name or "\\" in name or ":" in name or any(ord(char) < 32 for char in name):
        raise BackupError("Invalid archive member path")
    path = PurePosixPath(name)
    if path.is_absolute() or str(path) != name or len(name) > 1024:
        raise BackupError("Invalid archive member path")
    if any(part in {"", ".", ".."} or part.endswith((".", " ")) or part.split(".")[0].casefold() in _DEVICE_NAMES for part in path.parts):
        raise BackupError("Unsafe archive member path")
    if name not in {"database.sqlite3", "manifest.json"}:
        if len(path.parts) < 2 or path.parts[0] != "vault" or path.suffix.casefold() != ".md":
            raise BackupError("Unexpected archive member")
        if any(part.casefold() in _EXCLUDED_DIRS for part in path.parts[1:-1]) or _excluded_file(path.name):
            raise BackupError("Excluded file cannot be restored")
    return path


def _excluded_file(name: str) -> bool:
    folded = name.casefold()
    return folded.startswith(".env") or folded.split(".")[0] in {"apikey", "api_key", "api-key", "credentials", "secrets"}


def _scan_vault(vault_dir: Path) -> dict[str, tuple[int, ...]]:
    root = _safe_path(vault_dir)
    if not root.is_dir():
        raise BackupError("Vault directory does not exist")
    inventory: dict[str, tuple[int, ...]] = {}
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        _safe_path(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if _is_link(info):
                    raise BackupError("Vault contains a symbolic link or junction")
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.casefold() not in _EXCLUDED_DIRS:
                        pending.append(Path(entry.path))
                    continue
                if Path(entry.name).suffix.casefold() != ".md" or _excluded_file(entry.name):
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise BackupError("Vault contains a non-regular Markdown file")
                name = "vault/" + Path(entry.path).relative_to(root).as_posix()
                _safe_member(name)
                if info.st_size > MAX_FILE_BYTES:
                    raise BackupError("File exceeds backup size limit")
                # DirEntry.stat() reports zero inode/device on Windows; use a fresh path stat.
                inventory[name] = _fingerprint(_regular_file(Path(entry.path)))
                total += info.st_size
                if len(inventory) + 1 > MAX_FILES or total > MAX_TOTAL_BYTES:
                    raise BackupError("Vault exceeds backup limits")
    folded = [name.casefold() for name in inventory]
    if len(set(folded)) != len(folded):
        raise BackupError("Vault has case-colliding paths")
    return inventory


def _copy_stable_file(source: Path, destination: Path, expected: tuple[int, ...]) -> None:
    _regular_file(source)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(source, flags), "rb") as incoming:
        if _fingerprint(os.fstat(incoming.fileno())) != expected:
            raise BackupError("Vault changed during backup; retry when writing finishes")
        destination.parent.mkdir(parents=True, exist_ok=True)
        copied = 0
        with destination.open("xb") as outgoing:
            while chunk := incoming.read(_CHUNK):
                copied += len(chunk)
                if copied > MAX_FILE_BYTES or copied > expected[2]:
                    raise BackupError("Vault changed during backup; retry when writing finishes")
                outgoing.write(chunk)
        if _fingerprint(os.fstat(incoming.fileno())) != expected or _fingerprint(_regular_file(source)) != expected:
            raise BackupError("Vault changed during backup; retry when writing finishes")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as incoming:
        while chunk := incoming.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _check_database(path: Path) -> int:
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA query_only = ON")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in SUPPORTED_SQLITE_USER_VERSIONS:
                raise BackupError("Unsupported SQLite schema version")
            rows = connection.execute("PRAGMA integrity_check").fetchmany(2)
            if rows != [("ok",)]:
                raise BackupError("SQLite integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise BackupError("SQLite foreign key check failed")
            for table, required in _REQUIRED_COLUMNS.items():
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                if not required.issubset(columns):
                    raise BackupError("Backup does not contain a compatible application database")
            return version
    except sqlite3.Error as exc:
        raise BackupError("SQLite validation failed") from exc


def _snapshot_database(database: Database, destination: Path) -> int:
    source_path = _safe_path(database.path)
    if _regular_file(source_path).st_size > MAX_FILE_BYTES:
        raise BackupError("Database exceeds backup size limit")
    started = time.monotonic()
    # Read-only mode also prevents a disappearing source from being silently recreated.
    with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(destination)) as target:
        page_size = source.execute("PRAGMA page_size").fetchone()[0]

        def progress(status: int, remaining: int, total: int) -> None:
            if total * page_size > MAX_FILE_BYTES:
                raise BackupError("Database exceeds backup size limit")
            if time.monotonic() - started > BACKUP_TIMEOUT_SECONDS:
                raise BackupError("Database backup timed out; retry when writing finishes")

        source.backup(target, pages=128, progress=progress, sleep=0.05)
    return _check_database(destination)


def _summary(manifest: dict[str, Any], archive_path: Path) -> dict[str, Any]:
    return {
        "path": str(archive_path),
        "filename": archive_path.name,
        "created_at": manifest["created_at"],
        "format_version": manifest["version"],
        "encrypted": False,
        "sqlite_user_version": manifest["sqlite_user_version"],
        "vault_files": sum(entry["path"].startswith("vault/") for entry in manifest["files"]),
        "relationship_files": sum(
            1 for entry in manifest["files"]
            if any(part.casefold() in RELATIONSHIP_DIRS
                   for part in PurePosixPath(entry["path"]).parts[1:-1])
        ),
        "file_count": len(manifest["files"]),
        "total_bytes": sum(entry["size"] for entry in manifest["files"]),
        "archive_bytes": archive_path.stat().st_size,
        "verified": True,
    }


def plan_retention(backup_dir: Path, keep: int) -> dict[str, Any]:
    """Read-only preview of which archives a "keep the newest N" rule touches.

    Nothing is deleted or moved; the caller decides what to do afterwards.
    """
    folder = Path(backup_dir)
    archives = []
    if folder.exists():
        archives = [
            path for path in folder.glob("*.zip")
            if path.is_file() and not path.is_symlink()
        ]
    archives.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    records = [
        {
            "filename": path.name,
            "bytes": int(path.stat().st_size),
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, SHANGHAI).isoformat(timespec="seconds"),
        }
        for path in archives
    ]
    keep_list = records[:keep]
    remove_list = records[keep:]
    return {
        "generated_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "keep": keep,
        "total": len(records),
        "total_bytes": sum(item["bytes"] for item in records),
        "keep_bytes": sum(item["bytes"] for item in keep_list),
        "remove_bytes": sum(item["bytes"] for item in remove_list),
        "keep_list": keep_list,
        "remove_list": remove_list,
        "note": "只读预览：不会删除或移动任何备份文件；删除需要你之后明确操作。",
    }


def _tree_stats(root: Path) -> tuple[int, int]:
    """Count regular files and their total size without reading any content."""
    files = 0
    total = 0
    if not root.is_dir() or root.is_symlink():
        return files, total
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
        for name in names:
            path = Path(current) / name
            try:
                info = path.stat()
            except OSError:
                continue
            if _is_link(info) or not stat.S_ISREG(info.st_mode):
                continue
            files += 1
            total += int(info.st_size)
    return files, total


def list_restored_copies(restored_dir: Path) -> dict[str, Any]:
    """Read-only inventory of restored verification copies. Never reads record content."""
    folder = Path(restored_dir)
    copies: list[dict[str, Any]] = []
    if folder.is_dir() and not folder.is_symlink():
        for path in folder.iterdir():
            try:
                if not path.is_dir() or path.is_symlink():
                    continue
                info = path.stat()
            except OSError:
                continue
            files, total = _tree_stats(path)
            vault_files, _ = _tree_stats(path / "vault")
            copies.append({
                "name": path.name,
                "modified_at": datetime.fromtimestamp(info.st_mtime, SHANGHAI).isoformat(timespec="seconds"),
                "file_count": files,
                "total_bytes": total,
                "vault_files": vault_files,
                "has_database": (path / RESTORED_DATABASE_NAME).is_file()
                and not (path / RESTORED_DATABASE_NAME).is_symlink(),
            })
    copies.sort(key=lambda item: (item["modified_at"], item["name"]), reverse=True)
    return {
        "generated_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "total": len(copies),
        "total_bytes": sum(item["total_bytes"] for item in copies),
        "copies": copies,
        "note": "只读列表：只统计文件数量与大小，不读取正文，不删除、不移动、不切换任何数据。",
    }


def _restored_target(restored_dir: Path, name: str) -> Path:
    if not isinstance(name, str) or not RESTORED_NAME_PATTERN.fullmatch(name):
        raise BackupError("恢复副本名无效")
    folder = _safe_path(Path(restored_dir))
    target = _safe_path(folder / name)
    if target == folder or not target.is_relative_to(folder):
        raise BackupError("恢复副本名无效")
    if not target.is_dir() or target.is_symlink():
        raise BackupError("恢复副本不存在")
    return target


def verify_restored_copy(restored_dir: Path, name: str) -> dict[str, Any]:
    """Check one restored copy read-only: SQLite integrity plus file inventory."""
    target = _restored_target(restored_dir, name)
    database_path = target / RESTORED_DATABASE_NAME
    _regular_file(database_path)
    version = _check_database(database_path)
    files, total = _tree_stats(target)
    vault_files, vault_bytes = _tree_stats(target / "vault")
    return {
        "name": name,
        "checked_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "ok": True,
        "sqlite_integrity": "ok",
        "sqlite_user_version": version,
        "file_count": files,
        "total_bytes": total,
        "vault_files": vault_files,
        "vault_bytes": vault_bytes,
        "note": "只读验证：直接读取该副本自己的数据库，不改动副本、当前数据或备份文件。",
    }


def render_restore_drill_report(backup: dict[str, Any], copy: dict[str, Any],
                               verification: dict[str, Any]) -> str:
    """Markdown record of one drill: what was backed up, where it was restored, what the check said."""
    lines = [
        "# 恢复演练报告（一键流程）",
        "",
        f"- 生成时间：{datetime.now(SHANGHAI).isoformat(timespec='seconds')}",
        f"- 备份文件：`{backup.get('filename')}`（verified: {str(bool(backup.get('verified'))).lower()}，"
        f"{backup.get('file_count')} 个文件、{backup.get('archive_bytes')} 字节）",
        f"- 恢复副本：`{Path(str(copy.get('target_dir'))).name}`",
        f"- 只读验证：ok={str(bool(verification.get('ok'))).lower()}、"
        f"SQLite integrity={verification.get('sqlite_integrity')}、"
        f"{verification.get('file_count')} 个文件（其中 Vault Markdown {verification.get('vault_files')} 篇、"
        f"{verification.get('vault_bytes')} 字节）",
        f"- 验证时间：{verification.get('checked_at')}",
        "",
        "## 本次演练做了什么",
        "",
        "1. 生成一份新的备份 ZIP（路径穿越、哈希清单与 SQLite 完整性在生成时已校验）。",
        "2. 把它恢复到 `data/restored/<32 位十六进制>/` 的独立目录。",
        "3. 只读打开副本自己的数据库，检查完整性、外键与必需表，并统计文件清单。",
        "",
        "## 边界",
        "",
        "- 演练不覆盖当前库、不删除任何备份、不切换正在使用的数据。",
        f"- {verification.get('note') or '只读验证。'}",
        "- “把副本切换为当前库”仍未实现，需要你明确决定后再设计。",
        "",
    ]
    return "\n".join(lines)


def create_backup(database: Database, vault_dir: Path, backup_dir: Path) -> dict[str, Any]:
    """Snapshot SQLite and stable Markdown into a new verified local plaintext ZIP."""
    try:
        vault_dir = _safe_path(vault_dir)
        backup_dir = _safe_path(backup_dir)
        if backup_dir.is_relative_to(vault_dir):
            # 工作区模型把应用数据放在 <Vault>/.torch-os；这个隐藏目录不进扫描与备份，
            # 所以允许备份放在它里面。其它库内路径仍然拒绝。
            relative = backup_dir.relative_to(vault_dir)
            if not relative.parts or relative.parts[0].casefold() != ".torch-os":
                raise BackupError("Backup directory must be outside the Vault")
        inventory = _scan_vault(vault_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        _safe_path(backup_dir)
    except OSError as exc:
        raise BackupError("Could not read Vault or create backup directory") from exc
    now = datetime.now(timezone.utc)
    filename = f"torch-os-{now:%Y%m%dT%H%M%S%fZ}-{uuid.uuid4().hex[:8]}.zip"
    final_path = backup_dir / filename
    try:
        with tempfile.TemporaryDirectory(prefix=".backup-", dir=backup_dir) as temp:
            stage = Path(temp)
            version = _snapshot_database(database, stage / "database.sqlite3")
            for name, fingerprint in sorted(inventory.items()):
                _copy_stable_file(vault_dir / PurePosixPath(name).relative_to("vault"), stage / name, fingerprint)
            if _scan_vault(vault_dir) != inventory:
                raise BackupError("Vault changed during backup; retry when writing finishes")
            names = ["database.sqlite3", *sorted(inventory)]
            files = [{"path": name, "size": (stage / name).stat().st_size, "sha256": _hash_file(stage / name)} for name in names]
            if sum(entry["size"] for entry in files) > MAX_TOTAL_BYTES:
                raise BackupError("Backup exceeds total size limit")
            manifest = {"format": FORMAT, "version": FORMAT_VERSION, "created_at": now.isoformat(), "sqlite_user_version": version, "encrypted": False, "files": files}
            package = stage / "backup.zip"
            # Stored entries keep size predictable and do not produce self-rejected high-ratio ZIPs.
            with zipfile.ZipFile(package, "x", compression=zipfile.ZIP_STORED) as archive:
                for name in names:
                    archive.write(stage / name, name)
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
            verify_backup(package)
            if _scan_vault(vault_dir) != inventory:
                raise BackupError("Vault changed during backup; retry when writing finishes")
            _safe_path(backup_dir)
            # A hard link publishes a complete file atomically without replacing any existing backup.
            os.link(package, final_path)
        return _summary(manifest, final_path)
    except BackupError:
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile, RuntimeError) as exc:
        raise BackupError("Could not create a complete backup") from exc


def _read_manifest(raw: bytes) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise BackupError("Duplicate manifest property")
            result[key] = value
        return result

    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise BackupError("Invalid backup manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or type(manifest.get("version")) is not int or manifest["version"] != FORMAT_VERSION:
        raise BackupError("Unsupported backup format or version")
    if manifest.get("encrypted") is not False or type(manifest.get("sqlite_user_version")) is not int:
        raise BackupError("Invalid backup metadata")
    if not isinstance(manifest.get("created_at"), str):
        raise BackupError("Invalid backup timestamp")
    try:
        if datetime.fromisoformat(manifest["created_at"]).tzinfo is None:
            raise ValueError("timezone missing")
    except ValueError as exc:
        raise BackupError("Invalid backup timestamp") from exc
    if not isinstance(manifest.get("files"), list) or not 1 <= len(manifest["files"]) <= MAX_FILES:
        raise BackupError("Invalid backup file list")
    return manifest


def _extract_and_validate(archive_path: Path, stage: Path) -> dict[str, Any]:
    initial = _regular_file(archive_path)
    if initial.st_size > MAX_ARCHIVE_BYTES:
        raise BackupError("Archive exceeds size limit")
    with archive_path.open("rb") as incoming, zipfile.ZipFile(incoming, "r") as archive:
        if _fingerprint(os.fstat(incoming.fileno())) != _fingerprint(initial):
            raise BackupError("Archive changed while opening")
        infos = archive.infolist()
        if not 2 <= len(infos) <= MAX_FILES + 1:
            raise BackupError("Archive file count exceeds limits")
        names: dict[str, zipfile.ZipInfo] = {}
        folded: set[str] = set()
        total = 0
        for info in infos:
            if info.orig_filename != info.filename:
                raise BackupError("Invalid archive member path")
            _safe_member(info.filename)
            key = info.filename.casefold()
            mode = info.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if info.is_dir() or file_type not in {0, stat.S_IFREG} or info.flag_bits & 1:
                raise BackupError("Archive contains a link, special file, directory, or encrypted entry")
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise BackupError("Unsupported archive compression")
            if key in folded:
                raise BackupError("Archive contains duplicate or case-colliding paths")
            folded.add(key)
            names[info.filename] = info
            limit = MAX_MANIFEST_BYTES if info.filename == "manifest.json" else MAX_FILE_BYTES
            if info.file_size > limit or info.file_size > max(info.compress_size, 1) * MAX_COMPRESSION_RATIO:
                raise BackupError("Archive entry exceeds size or compression ratio limit")
            total += info.file_size
        if total > MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES or "manifest.json" not in names or "database.sqlite3" not in names:
            raise BackupError("Archive is incomplete or exceeds total size limit")
        # A file cannot also be a directory, including on case-insensitive filesystems.
        for name in names:
            if any(parent.as_posix().casefold() in folded for parent in PurePosixPath(name).parents if str(parent) != "."):
                raise BackupError("Archive contains conflicting file paths")
        manifest = _read_manifest(archive.read("manifest.json"))
        expected: dict[str, dict[str, Any]] = {}
        for entry in manifest["files"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise BackupError("Invalid manifest file entry")
            name = entry["path"]
            _safe_member(name)
            if name == "manifest.json" or name in expected or name not in names:
                raise BackupError("Manifest file list does not match archive")
            if type(entry.get("size")) is not int or entry["size"] < 0 or entry["size"] != names[name].file_size:
                raise BackupError("Manifest file size does not match archive")
            if not isinstance(entry.get("sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None:
                raise BackupError("Invalid manifest checksum")
            expected[name] = entry
        if set(expected) != set(names) - {"manifest.json"} or sum(item["size"] for item in expected.values()) > MAX_TOTAL_BYTES:
            raise BackupError("Manifest file list does not match archive or size limit")
        for name, entry in expected.items():
            destination = stage.joinpath(*PurePosixPath(name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            copied = 0
            with archive.open(name) as source, destination.open("xb") as target:
                while chunk := source.read(_CHUNK):
                    copied += len(chunk)
                    if copied > entry["size"]:
                        raise BackupError("Expanded archive exceeds declared size")
                    digest.update(chunk)
                    target.write(chunk)
            if copied != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise BackupError("Backup checksum validation failed")
        version = _check_database(stage / "database.sqlite3")
        if version != manifest["sqlite_user_version"]:
            raise BackupError("Manifest SQLite version does not match database")
        if _fingerprint(os.fstat(incoming.fileno())) != _fingerprint(initial) or _fingerprint(_regular_file(archive_path)) != _fingerprint(initial):
            raise BackupError("Archive changed during verification")
        return manifest


def verify_backup(archive_path: Path) -> dict[str, Any]:
    """Verify all paths, limits, hashes, schema version, and SQLite integrity."""
    archive_path = _safe_path(archive_path)
    try:
        with tempfile.TemporaryDirectory(prefix=".verify-", dir=archive_path.parent) as temp:
            manifest = _extract_and_validate(archive_path, Path(temp))
        return _summary(manifest, archive_path)
    except BackupError:
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile, RuntimeError, EOFError, NotImplementedError, zlib.error) as exc:
        raise BackupError("Backup verification failed") from exc


def restore_backup(archive_path: Path, target_dir: Path, *, allowed_parent: Path) -> dict[str, Any]:
    """Restore into a new/empty child directory; never changes the running database.

    ``allowed_parent`` must be supplied by trusted server configuration, not a client.
    The target's immediate parent must already exist under ``allowed_parent``.
    """
    archive_path = _safe_path(archive_path)
    allowed_parent = _safe_path(allowed_parent)
    target_dir = _safe_path(target_dir)
    if target_dir == allowed_parent or not target_dir.is_relative_to(allowed_parent):
        raise BackupError("Restore target must be below the configured data directory")
    if not allowed_parent.is_dir() or not target_dir.parent.is_dir():
        raise BackupError("Restore parent directory must already exist")
    if target_dir.exists() and (not target_dir.is_dir() or any(target_dir.iterdir())):
        raise BackupError("Restore target must be a new or empty directory")
    original = target_dir.stat() if target_dir.exists() else None
    removed_empty_target = False
    try:
        with tempfile.TemporaryDirectory(prefix=".restore-", dir=target_dir.parent) as temp:
            stage = Path(temp) / "content"
            stage.mkdir()
            manifest = _extract_and_validate(archive_path, stage)
            (stage / "database.sqlite3").rename(stage / RESTORED_DATABASE_NAME)
            (stage / "vault").mkdir(exist_ok=True)
            _safe_path(target_dir)
            if original is not None:
                if not target_dir.exists() or _fingerprint(target_dir.stat()) != _fingerprint(original) or any(target_dir.iterdir()):
                    raise BackupError("Restore target changed during validation")
                target_dir.rmdir()
                removed_empty_target = True
            elif target_dir.exists():
                raise BackupError("Restore target appeared during validation")
            stage.rename(target_dir)
        result = _summary(manifest, archive_path)
        result.update({"target_dir": str(target_dir), "database_path": str(target_dir / RESTORED_DATABASE_NAME), "vault_dir": str(target_dir / "vault")})
        return result
    except BackupError:
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile, RuntimeError, EOFError, NotImplementedError, zlib.error) as exc:
        raise BackupError("Backup restoration failed; existing data was not replaced") from exc
    finally:
        if removed_empty_target and not target_dir.exists():
            target_dir.mkdir(exist_ok=True)
