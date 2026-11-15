"""Verified multi-root recovery bundles. Restore always creates a new directory."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import re
import unicodedata
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .storage_layout import StorageLayout, checked_path
from .cloud_backup import _snapshot_sqlite, _is_sqlite, encrypt_file, decrypt_file

FORMAT = "torch-multi-root-v1"
MAX_BYTES = 50 * 1024 ** 3
MAX_FILES = 200000


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1048576), b""):
            result.update(part)
    return result.hexdigest()


def _relative(value):
    if not isinstance(value, str) or value != unicodedata.normalize("NFC", value):
        raise ValueError("恢复包路径类型或 Unicode 格式无效")
    path = PurePosixPath(value)
    if ("\\" in value or path.is_absolute() or ".." in path.parts
            or len(path.parts) < 2 or path.parts[0] not in {"notes", "state", "archive"}
            or any(not p or p in {".", ".."} or p.endswith((" ", ".")) or re.search(r'[\\:\x00-\x1f<>"|?*]', p) or re.match(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", p, re.I) for p in path.parts)):
        raise ValueError("恢复包路径无效")
    return path


def create_bundle(layout: StorageLayout, destination: Path, *, password=""):
    destination = checked_path(destination)
    if destination.exists():
        raise ValueError("备份目标已存在，拒绝覆盖")
    roots = {key: getattr(layout, key) for key in ("notes", "state", "archive")}
    for key in ("notes", "state", "archive", "cache", "exports"):
        if destination.is_relative_to(getattr(layout, key)):
            raise ValueError("备份目标必须在五个活动存储根之外")
    for root in roots.values():
        if destination.is_relative_to(root):
            raise ValueError("备份不能写入正在备份的数据根")
        checked_path(root)
        if not root.is_dir():
            raise ValueError("备份源根目录缺失")
    if password and len(password) < 8:
        raise ValueError("恢复包密码至少 8 位（兼容既有完整备份设置）")
    destination.parent.mkdir(parents=True, exist_ok=True)
    files, total = [], 0
    staging = checked_path(layout.cache / "backup-staging")
    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="torch-bundle-", dir=staging) as temp:
        temp = Path(temp)
        package = temp / "snapshot.zip"
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for role, root in roots.items():
                for directory, dirs, names in os.walk(root, followlinks=False):
                    for name in dirs:
                        checked_path(Path(directory) / name)
                    dirs[:] = sorted(d for d in dirs if d not in {".git", "__pycache__", "node_modules"})
                    for name in sorted(names):
                        path = checked_path(Path(directory) / name)
                        if not path.is_file() or name.endswith(("-wal", "-shm", ".tmp")):
                            continue
                        relative = f"{role}/{path.relative_to(root).as_posix()}"
                        _relative(relative)
                        if len(files) >= MAX_FILES:
                            raise ValueError("备份文件数超出上限")
                        source = path
                        kind = "file"
                        if _is_sqlite(path):
                            source = temp / "databases" / uuid4().hex
                            source.parent.mkdir(exist_ok=True)
                            _snapshot_sqlite(path, source)
                            kind = "sqlite"
                        before = source.stat()
                        size = before.st_size
                        total += size
                        if total > MAX_BYTES:
                            raise ValueError("备份超过 50 GiB 上限")
                        checksum = digest(source)
                        archive.write(source, relative)
                        after = source.stat()
                        if (before.st_size, before.st_mtime_ns, checksum) != (after.st_size, after.st_mtime_ns, digest(source)):
                            raise ValueError("备份期间源文件变化，请停止写入后重试")
                        files.append({"path": relative, "bytes": size, "sha256": checksum, "kind": kind})
            manifest = {"format": FORMAT, "workspace_id": layout.workspace_id, "files": files, "bytes": total,
                        "credentials": "包含恢复所需的本机配置；按私人资料保管，cache 未包含", "cross_database_consistency": "需由调用方安排停写窗口"}
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        verify_bundle(package)
        published = package
        if password:
            published = temp / "snapshot.enc"
            encrypt_file(package, published, password)
        # Plain ZIP/credentials stay in local cache when encryption is enabled.
        # Only the finished (possibly encrypted) package reaches the backup folder.
        publishing = destination.parent / (".torch-publish-" + uuid4().hex + ".partial")
        try:
            with published.open("rb") as inp, publishing.open("xb") as out:
                shutil.copyfileobj(inp, out, 1048576); out.flush(); os.fsync(out.fileno())
            if digest(publishing) != digest(published):
                raise ValueError("完整恢复包发布校验失败")
            os.link(publishing, destination)  # exclusive final publication, never overwrite
        finally:
            publishing.unlink(missing_ok=True)
    return {"file": str(destination), "files": len(files), "bytes": total, "encrypted": bool(password)}


def verify_bundle(package: Path):
    with zipfile.ZipFile(package) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES + 1 or len({i.filename.casefold() for i in infos}) != len(infos):
            raise ValueError("备份条目重复或超出上限")
        info = archive.getinfo("manifest.json")
        if info.file_size > 32 * 1048576:
            raise ValueError("备份清单过大")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != FORMAT or not isinstance(manifest.get("files"), list):
            raise ValueError("备份格式不支持")
        if set(archive.namelist()) != {"manifest.json", *(item["path"] for item in manifest["files"])}:
            raise ValueError("清单与备份不一致")
        total = 0
        for item in manifest["files"]:
            _relative(item["path"])
            info = archive.getinfo(item["path"])
            if info.file_size != item["bytes"] or info.external_attr >> 16 & 0o170000 == 0o120000:
                raise ValueError("备份大小或文件类型不匹配")
            total += info.file_size
            if total > MAX_BYTES:
                raise ValueError("恢复超过容量上限")
            sha = hashlib.sha256()
            with archive.open(info) as stream:
                for part in iter(lambda: stream.read(1048576), b""):
                    sha.update(part)
            if sha.hexdigest() != item["sha256"]:
                raise ValueError("备份内容哈希不匹配")
        return manifest


def restore_bundle(package: Path, destination: Path, *, password=""):
    destination = checked_path(destination)
    if destination.exists():
        raise ValueError("只能恢复到全新目录")
    with tempfile.TemporaryDirectory(prefix="torch-restore-") as scratch:
        source = Path(package)
        if password:
            source = Path(scratch) / "decrypted.zip"
            decrypt_file(Path(package), source, password)
        manifest = verify_bundle(source)
        destination.mkdir(parents=True, exist_ok=False)
        # On failure keep the incomplete independent directory for diagnostics; never replace current data.
        with zipfile.ZipFile(source) as archive:
            for item in manifest["files"]:
                target = checked_path(destination / Path(*_relative(item["path"]).parts))
                if not target.is_relative_to(destination):
                    raise ValueError("恢复路径越界")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as out, archive.open(item["path"]) as inp:
                    shutil.copyfileobj(inp, out, 1048576)
                if digest(target) != item["sha256"]:
                    raise ValueError("恢复后的文件校验失败")
                if item["kind"] == "sqlite":
                    with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as con:
                        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise ValueError("恢复数据库完整性校验失败")
        (destination / "RESTORED.json").write_text(json.dumps({"format": FORMAT, "workspace_id": manifest["workspace_id"], "status": "verified_copy_not_active"}), encoding="utf-8")
        return {"destination": str(destination), "files": len(manifest["files"]), "active": False}
