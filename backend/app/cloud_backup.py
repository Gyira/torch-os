"""完整个人数据库快照与网盘同步备份（2026-11-02）。

目标不是替代 ``backup.py`` 的“Markdown + 主库”恢复包，而是回答：
“如果这台电脑整机丢失，能不能把整个个人数据库原样拿回来？”

因此本模块会：
- 复制当前个人数据库里的全部普通文件（四库、恋爱系统媒体、``.obsidian`` 等）；
- 对每个 SQLite 数据库（主库、微信 chat.db、相册、八字、经期、恋爱大师）用
  SQLite 在线备份 API 做一致快照，避免 WAL 正在写入时拿到半份数据库；
- 排除日志、旧备份、恢复副本与临时目录，避免快照互相套娃；
- 生成一个带 manifest、逐文件 sha256 的 ZIP；
- 可选用密码做 AES-256-GCM 分块加密，再写进用户选择的网盘同步文件夹。

网盘客户端只看到“已经完成”的 ZIP（或 ``.zip.enc``），不会看到正在写入的半成品。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import struct
import tempfile
import threading
import time
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .db import Database
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

FORMAT = "torch-os-full-backup"
FORMAT_VERSION = 1
CONFIG_RELATIVE = ".torch-os/cloud-backup.json"
SECRET_RELATIVE = ".torch-os/cloud-backup.secret"
STAGING_RELATIVE = ".torch-os/cloud-backup-staging"
SNAPSHOT_PREFIX = "torch-os-full-"
SNAPSHOT_PATTERN = re.compile(
    r"^torch-os-full-\d{8}T\d{6,}Z-[0-9a-f]{8}\.(?:zip|zip\.enc)$")
SNAPSHOT_SUFFIXES = (".zip", ".zip.enc")

MAX_FILES = 200_000
MAX_TOTAL_BYTES = 50 * 1024 ** 3
MAX_FILE_BYTES = 8 * 1024 ** 3
CHUNK_BYTES = 4 * 1024 * 1024
COPY_ATTEMPTS = 3
PASSWORD_MIN_LENGTH = 8

# 加密容器：magic(8) + version(1) + flags(1) + reserved(2) + salt(16)
#           + nonce_prefix(8) + chunk_size(4) + plaintext_size(8)
MAGIC = b"TORCHFB1"
HEADER = struct.Struct(">8sBBH16s8sIQ")
HEADER_SIZE = HEADER.size
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1


class CloudBackupError(ValueError):
    """可直接展示给界面的备份失败；不包含密码或文件正文。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_text() -> str:
    return _now().isoformat(timespec="seconds")


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _parse_iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as incoming:
        while chunk := incoming.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _safe_vault(vault_dir: Path | str) -> Path:
    vault = Path(vault_dir).expanduser().resolve()
    if not vault.is_dir():
        raise CloudBackupError("个人数据库目录不存在")
    return vault


def _config_path(vault: Path) -> Path:
    return vault / CONFIG_RELATIVE


def _secret_path(vault: Path) -> Path:
    return vault / SECRET_RELATIVE


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _default_config() -> dict[str, Any]:
    return {
        "version": 1,
        "directory": "",
        "frequency": "manual",
        "keep": 7,
        "encrypt": True,
        "last_attempt_at": "",
        "last_run_at": "",
        "last_status": "",
        "last_file": "",
        "last_error": "",
        "last_bytes": 0,
        "last_files": 0,
    }


def load_config(vault_dir: Path | str) -> dict[str, Any]:
    vault = _safe_vault(vault_dir)
    config = _default_config()
    path = _config_path(vault)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return config
    except (OSError, ValueError, TypeError):
        config["last_status"] = "failed"
        config["last_error"] = "云端备份配置文件无法读取，请重新保存设置"
        return config
    if not isinstance(raw, dict):
        return config
    for key in ("directory", "frequency", "last_attempt_at", "last_run_at",
                "last_status", "last_file", "last_error"):
        value = raw.get(key)
        if isinstance(value, str):
            config[key] = value.strip() if key in {"directory", "frequency"} else value
    if config["frequency"] not in {"manual", "daily", "weekly"}:
        config["frequency"] = "manual"
    for key in ("keep", "last_bytes", "last_files"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            config[key] = value
    if not 1 <= config["keep"] <= 30:
        config["keep"] = 7
    config["encrypt"] = bool(raw.get("encrypt", True))
    return config


def save_config(vault_dir: Path | str, config: dict[str, Any]) -> None:
    vault = _safe_vault(vault_dir)
    payload = _default_config()
    payload.update({key: config[key] for key in payload if key in config})
    _atomic_write(
        _config_path(vault),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


# ── 密码保存：Windows 用当前用户 DPAPI；其他平台退回本机 AES 密钥 ────────


def _local_key(vault: Path) -> bytes:
    path = vault / ".torch-os" / "cloud-backup.key"
    try:
        raw = path.read_bytes()
        if len(raw) == 32:
            return raw
    except OSError:
        pass
    key = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def _protect_password(password: str) -> bytes:
    value = password.encode("utf-8")
    if os.name == "nt":
        try:
            from .love_credentials import _dpapi_encrypt
            return b"DPAPI1" + _dpapi_encrypt(value)
        except Exception as error:  # noqa: BLE001 - DPAPI 不可用时退回 AES
            raise CloudBackupError(f"无法用 Windows 凭据保护备份密码：{error}") from error
    return b"AES1"


def set_password(vault_dir: Path | str, password: str) -> None:
    vault = _safe_vault(vault_dir)
    if len(password) < PASSWORD_MIN_LENGTH:
        raise CloudBackupError(f"备份密码至少需要 {PASSWORD_MIN_LENGTH} 位")
    protected = _protect_password(password)
    if protected == b"AES1":
        key = _local_key(vault)
        nonce = secrets.token_bytes(12)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        cipher = AESGCM(key).encrypt(nonce, password.encode("utf-8"), b"torch-cloud-backup")
        protected = b"AES1" + nonce + cipher
    path = _secret_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(protected)
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def get_password(vault_dir: Path | str) -> str | None:
    vault = _safe_vault(vault_dir)
    path = _secret_path(vault)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise CloudBackupError(f"无法读取已保存的备份密码：{error}") from error
    if raw.startswith(b"DPAPI1"):
        if os.name != "nt":
            raise CloudBackupError("备份密码由 Windows 凭据保护，当前平台无法读取")
        try:
            from .love_credentials import _dpapi_decrypt
            return _dpapi_decrypt(raw[6:]).decode("utf-8")
        except Exception as error:  # noqa: BLE001
            raise CloudBackupError("无法解密已保存的备份密码，请重新设置") from error
    if raw.startswith(b"AES1"):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            key = _local_key(vault)
            return AESGCM(key).decrypt(raw[4:16], raw[16:], b"torch-cloud-backup").decode("utf-8")
        except Exception as error:  # noqa: BLE001
            raise CloudBackupError("无法解密已保存的备份密码，请重新设置") from error
    raise CloudBackupError("备份密码文件格式不正确，请重新设置")


def password_is_set(vault_dir: Path | str) -> bool:
    return _secret_path(_safe_vault(vault_dir)).is_file()


# ── 分块 AES-256-GCM ───────────────────────────────────────────────────


def _derive_key(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(
        password.encode("utf-8"))


def encrypt_file(source: Path, destination: Path, password: str) -> None:
    """把 source 加密到 destination；destination 必须不存在。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    size = source.stat().st_size
    salt = secrets.token_bytes(16)
    nonce_prefix = secrets.token_bytes(8)
    header = HEADER.pack(MAGIC, 1, 0, 0, salt, nonce_prefix, CHUNK_BYTES, size)
    aes = AESGCM(_derive_key(password, salt))
    try:
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            outgoing.write(header)
            counter = 0
            while chunk := incoming.read(CHUNK_BYTES):
                nonce = nonce_prefix + counter.to_bytes(4, "big")
                outgoing.write(aes.encrypt(nonce, chunk, header))
                counter += 1
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def decrypt_file(source: Path, destination: Path, password: str) -> None:
    """解密 encrypt_file 生成的文件；密码错误或文件被改动时抛 CloudBackupError。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    with source.open("rb") as incoming:
        header = incoming.read(HEADER_SIZE)
        if len(header) != HEADER_SIZE:
            raise CloudBackupError("加密备份文件头不完整")
        try:
            magic, version, _flags, _reserved, salt, nonce_prefix, chunk_size, size = \
                HEADER.unpack(header)
        except struct.error as error:
            raise CloudBackupError("加密备份文件头不正确") from error
        if magic != MAGIC or version != 1 or chunk_size != CHUNK_BYTES:
            raise CloudBackupError("不支持的加密备份格式")
        aes = AESGCM(_derive_key(password, salt))
        try:
            with destination.open("xb") as outgoing:
                remaining = size
                counter = 0
                while remaining > 0:
                    take = min(chunk_size, remaining)
                    payload = incoming.read(take + 16)
                    if len(payload) != take + 16:
                        raise CloudBackupError("加密备份文件提前结束")
                    nonce = nonce_prefix + counter.to_bytes(4, "big")
                    outgoing.write(aes.decrypt(nonce, payload, header))
                    remaining -= take
                    counter += 1
                if incoming.read(1):
                    raise CloudBackupError("加密备份文件有多余数据")
        except CloudBackupError:
            destination.unlink(missing_ok=True)
            raise
        except Exception as error:  # noqa: BLE001 - InvalidTag 等统一成可展示错误
            destination.unlink(missing_ok=True)
            raise CloudBackupError("密码不正确，或加密备份文件已损坏") from error


# ── 扫描与快照 ─────────────────────────────────────────────────────────


def _excluded_relative(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return False
    folded = [part.casefold() for part in parts]
    if any(part in {".git", "__pycache__", "node_modules"} for part in folded):
        return True
    if folded[0] == ".torch-os" and len(parts) >= 2:
        if folded[1] in {
            "backups", "restored", "logs", "diagnostics", "cloud-backup-staging",
        }:
            return True
    return False


def _db_sidecar(name: str) -> bool:
    folded = name.casefold()
    return folded.endswith(("-wal", "-shm", "-journal"))


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as incoming:
            return incoming.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        uri = source.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as incoming, \
                closing(sqlite3.connect(destination)) as outgoing:
            incoming.execute("PRAGMA busy_timeout = 10000")
            incoming.backup(outgoing)
    except sqlite3.Error as error:
        destination.unlink(missing_ok=True)
        raise CloudBackupError(f"数据库 {source.name} 备份失败：{error}") from error


def _copy_stable(source: Path, destination: Path, relative: str) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for _attempt in range(COPY_ATTEMPTS):
        try:
            before = source.stat()
        except OSError as error:
            raise CloudBackupError(f"无法读取 {relative}：{error}") from error
        if not stat.S_ISREG(before.st_mode):
            raise CloudBackupError(f"{relative} 不是普通文件")
        if before.st_size > MAX_FILE_BYTES:
            raise CloudBackupError(f"{relative} 超过单文件 8GB 上限")
        digest = hashlib.sha256()
        copied = 0
        try:
            with source.open("rb") as incoming, destination.open("wb") as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    outgoing.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
            after = source.stat()
        except OSError as error:
            destination.unlink(missing_ok=True)
            raise CloudBackupError(f"无法复制 {relative}：{error}") from error
        unchanged = (
            copied == before.st_size
            and after.st_size == before.st_size
            and after.st_mtime_ns == before.st_mtime_ns
        )
        if unchanged:
            return copied, digest.hexdigest()
        destination.unlink(missing_ok=True)
    raise CloudBackupError(f"{relative} 正在写入，备份已暂停；请稍后重试")


def _stage_vault(vault: Path, staging: Path,
                 progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    staged_root = staging / "vault"
    files: list[dict[str, Any]] = []
    warnings: list[str] = []
    total_bytes = 0
    total_files = 0

    def report(message: str) -> None:
        if progress:
            progress(message)

    for current, dirs, names in os.walk(vault, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(vault)
        kept_dirs: list[str] = []
        for name in sorted(dirs):
            child = current_path / name
            try:
                info = child.lstat()
            except OSError as error:
                raise CloudBackupError(f"无法读取目录 {child.name}：{error}") from error
            if _is_link(info):
                raise CloudBackupError(f"个人数据库里有符号链接/快捷方式：{(relative_dir / name).as_posix()}")
            if _excluded_relative(relative_dir / name):
                warnings.append(f"已跳过目录 {((relative_dir / name).as_posix())}")
                continue
            kept_dirs.append(name)
        dirs[:] = kept_dirs

        for name in sorted(names):
            source = current_path / name
            relative = (relative_dir / name).as_posix()
            try:
                info = source.lstat()
            except OSError as error:
                raise CloudBackupError(f"无法读取 {relative}：{error}") from error
            if _is_link(info):
                raise CloudBackupError(f"个人数据库里有符号链接/快捷方式：{relative}")
            if not stat.S_ISREG(info.st_mode):
                warnings.append(f"已跳过非普通文件 {relative}")
                continue
            if _excluded_relative(relative_dir / name) or _db_sidecar(name):
                continue
            total_files += 1
            if total_files > MAX_FILES:
                raise CloudBackupError("文件数量超过 20 万，已停止备份")
            destination = staged_root / relative
            kind = "file"
            if name.casefold().endswith((".db", ".sqlite3")) and _is_sqlite(source):
                report(f"正在快照数据库 {relative}")
                _snapshot_sqlite(source, destination)
                size = destination.stat().st_size
                digest = _sha256(destination)
                kind = "sqlite"
            else:
                report(f"正在复制 {relative}")
                size, digest = _copy_stable(source, destination, relative)
            total_bytes += size
            if total_bytes > MAX_TOTAL_BYTES:
                raise CloudBackupError("个人数据库超过 50GB 备份上限，已停止备份")
            files.append({
                "path": f"vault/{relative}",
                "size": size,
                "sha256": digest,
                "kind": kind,
            })

    manifest = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "created_at": _now_text(),
        "vault_name": vault.name,
        "encrypted": False,
        "files": files,
        "warnings": warnings,
        "totals": {"files": len(files), "bytes": total_bytes},
    }
    _atomic_write(staging / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def _write_zip(staging: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_STORED,
                         allowZip64=True) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_dir():
                continue
            archive.write(path, path.relative_to(staging).as_posix())
    with zipfile.ZipFile(destination) as archive:
        bad = archive.testzip()
        if bad:
            raise CloudBackupError(f"备份 ZIP 校验失败：{bad}")


def _snapshot_files(directory: Path) -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    for path in entries:
        if (not path.is_file() or path.is_symlink()
                or not SNAPSHOT_PATTERN.fullmatch(path.name)):
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        rows.append({
            "filename": path.name,
            "bytes": info.st_size,
            "modified_at": datetime.fromtimestamp(
                info.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "encrypted": path.name.endswith(".zip.enc"),
        })
    rows.sort(key=lambda item: item["filename"], reverse=True)
    return rows


def _prune(directory: Path, keep: int) -> list[str]:
    rows = _snapshot_files(directory)
    removed: list[str] = []
    for row in rows[max(1, keep):]:
        path = directory / row["filename"]
        try:
            path.unlink()
            removed.append(row["filename"])
        except OSError:
            continue
    return removed


def _validate_cloud_dir(vault: Path, value: str) -> Path:
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        raise CloudBackupError("请先填写网盘同步文件夹")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise CloudBackupError("网盘同步文件夹需要填写完整绝对路径")
    try:
        path = path.resolve()
    except OSError as error:
        raise CloudBackupError(f"无法解析网盘同步文件夹：{error}") from error
    if path == vault or path.is_relative_to(vault):
        raise CloudBackupError("网盘同步文件夹不能放在个人数据库里面")
    if not path.is_dir():
        raise CloudBackupError("网盘同步文件夹不存在，请先在网盘客户端里创建并同步")
    try:
        with tempfile.NamedTemporaryFile(prefix=".torch-backup-write-", dir=path,
                                         delete=True):
            pass
    except OSError as error:
        raise CloudBackupError(f"网盘同步文件夹不可写：{error}") from error
    return path


def create_snapshot(vault_dir: Path | str, directory: Path | str, *,
                    password: str | None = None, keep: int = 7,
                    progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """创建一个完整快照并原子发布到 directory。返回不含密码的结果摘要。"""
    vault = _safe_vault(vault_dir)
    cloud = _validate_cloud_dir(vault, str(directory))
    keep = max(1, min(30, int(keep)))
    staging_root = vault / STAGING_RELATIVE
    staging_root.mkdir(parents=True, exist_ok=True)
    # 上次进程崩溃可能留下临时目录；超过一天的直接清理，不影响正在进行的快照。
    now_ts = time.time()
    for child in staging_root.iterdir():
        try:
            if child.is_dir() and now_ts - child.stat().st_mtime > 86400:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue
    staging = Path(tempfile.mkdtemp(prefix="snapshot-", dir=staging_root))
    stamp = _now().strftime("%Y%m%dT%H%M%S%fZ")
    name = f"{SNAPSHOT_PREFIX}{stamp}-{secrets.token_hex(4)}"
    temp_zip = cloud / f".{name}.zip.partial"
    temp_enc = cloud / f".{name}.zip.enc.partial"
    started = time.monotonic()
    try:
        if progress:
            progress("正在扫描个人数据库")
        manifest = _stage_vault(vault, staging, progress)
        manifest["encrypted"] = bool(password)
        manifest["filename"] = f"{name}.zip.enc" if password else f"{name}.zip"
        _atomic_write(staging / "manifest.json",
                      json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        if progress:
            progress("正在打包快照")
        _write_zip(staging, temp_zip)
        if password:
            if progress:
                progress("正在加密快照")
            encrypt_file(temp_zip, temp_enc, password)
            temp_zip.unlink(missing_ok=True)
            final = cloud / f"{name}.zip.enc"
            os.replace(temp_enc, final)
        else:
            final = cloud / f"{name}.zip"
            os.replace(temp_zip, final)
        manifest["encrypted"] = bool(password)
        manifest["filename"] = final.name
        removed = _prune(cloud, keep)
        if progress:
            progress("完整备份完成")
        return {
            "filename": final.name,
            "bytes": final.stat().st_size,
            "files": len(manifest["files"]),
            "encrypted": bool(password),
            "warnings": manifest["warnings"][:50],
            "removed": removed,
            "seconds": round(time.monotonic() - started, 2),
            "created_at": manifest["created_at"],
            "path": str(final),
        }
    except CloudBackupError:
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        raise CloudBackupError(f"完整备份失败：{error}") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        temp_zip.unlink(missing_ok=True)
        temp_enc.unlink(missing_ok=True)


# ── 后台服务 ───────────────────────────────────────────────────────────


class CloudBackupService:
    def __init__(self, database: Database, vault_dir: Path | str, *,
                 read_only: bool = False):
        self.database = database
        self.vault_dir = _safe_vault(vault_dir)
        self.read_only = read_only
        self._lock = threading.Lock()
        self._job: dict[str, Any] = {
            "status": "idle", "progress": "", "started_at": "", "finished_at": "",
            "error": "", "filename": "",
        }

    # 配置
    def settings(self) -> dict[str, Any]:
        config = load_config(self.vault_dir)
        return {
            **config,
            "password_set": password_is_set(self.vault_dir),
            "read_only": self.read_only,
        }

    def update_settings(self, *, directory: str, frequency: str, keep: int,
                        encrypt: bool, password: str = "") -> dict[str, Any]:
        try:
            ensure_vault_writable(self.read_only, "保存云端备份设置")
        except VaultReadOnlyError as error:
            raise CloudBackupError(str(error)) from error
        if self._job_active():
            raise CloudBackupError("完整备份正在进行，完成后再修改设置")
        cloud = _validate_cloud_dir(self.vault_dir, directory)
        if frequency not in {"manual", "daily", "weekly"}:
            raise CloudBackupError("自动备份频率不正确")
        if not 1 <= int(keep) <= 30:
            raise CloudBackupError("保留份数需要是 1–30")
        if password and len(password) < PASSWORD_MIN_LENGTH:
            raise CloudBackupError(f"备份密码至少需要 {PASSWORD_MIN_LENGTH} 位")
        if encrypt and not password and not password_is_set(self.vault_dir):
            raise CloudBackupError("请先设置至少 8 位的备份密码，或关闭密码加密")
        if password:
            set_password(self.vault_dir, password)
        config = load_config(self.vault_dir)
        config.update({
            "directory": str(cloud),
            "frequency": frequency,
            "keep": int(keep),
            "encrypt": bool(encrypt),
        })
        save_config(self.vault_dir, config)
        return self.status()

    def status(self) -> dict[str, Any]:
        config = self.settings()
        snapshots = _snapshot_files(Path(config["directory"])) if config["directory"] else []
        with self._lock:
            job = dict(self._job)
        return {"settings": config, "job": job, "snapshots": snapshots}

    def _job_active(self) -> bool:
        with self._lock:
            return self._job["status"] in {"running", "queued"}

    def start(self) -> dict[str, Any]:
        try:
            ensure_vault_writable(self.read_only, "创建完整备份")
        except VaultReadOnlyError as error:
            raise CloudBackupError(str(error)) from error
        config = load_config(self.vault_dir)
        cloud = _validate_cloud_dir(self.vault_dir, config["directory"])
        password = None
        if config["encrypt"]:
            password = get_password(self.vault_dir)
            if not password:
                raise CloudBackupError("还没有设置备份密码；请先在设置里填写至少 8 位密码")
        with self._lock:
            if self._job["status"] in {"running", "queued"}:
                raise CloudBackupError("完整备份正在进行，请等待完成")
            self._job = {
                "status": "running", "progress": "正在准备", "started_at": _now_text(),
                "finished_at": "", "error": "", "filename": "",
            }
        thread = threading.Thread(
            target=self._run, args=(cloud, password, config), daemon=True,
            name="torch-cloud-backup")
        thread.start()
        return self.status()

    def _run(self, cloud: Path, password: str | None, config: dict[str, Any]) -> None:
        try:
            result = create_snapshot(
                self.vault_dir, cloud, password=password, keep=int(config["keep"]),
                progress=self._progress)
            self._record_success(result)
            with self._lock:
                self._job.update({
                    "status": "completed", "progress": "完成",
                    "finished_at": _now_text(), "filename": result["filename"],
                })
            self.database.audit("cloud_backup_created", {
                "filename": result["filename"],
                "bytes": result["bytes"],
                "files": result["files"],
                "encrypted": result["encrypted"],
            })
        except Exception as error:  # noqa: BLE001 - 后台任务必须把失败写回状态
            message = str(error)[:500] or "完整备份失败"
            self._record_failure(message)
            with self._lock:
                self._job.update({
                    "status": "failed", "progress": "", "finished_at": _now_text(),
                    "error": message,
                })

    def _progress(self, message: str) -> None:
        with self._lock:
            self._job["progress"] = message

    def _record_success(self, result: dict[str, Any]) -> None:
        config = load_config(self.vault_dir)
        config.update({
            "last_attempt_at": _now_text(),
            "last_run_at": _now_text(),
            "last_status": "completed",
            "last_file": result["filename"],
            "last_error": "",
            "last_bytes": result["bytes"],
            "last_files": result["files"],
        })
        save_config(self.vault_dir, config)

    def _record_failure(self, message: str) -> None:
        config = load_config(self.vault_dir)
        config.update({
            "last_attempt_at": _now_text(),
            "last_status": "failed",
            "last_error": message,
        })
        save_config(self.vault_dir, config)
        self.database.audit("cloud_backup_failed", {"detail": message})

    def maybe_run_due(self) -> None:
        if self.read_only or self._job_active():
            return
        config = load_config(self.vault_dir)
        if config["frequency"] not in {"daily", "weekly"} or not config["directory"]:
            return
        interval = timedelta(days=1 if config["frequency"] == "daily" else 7)
        if config.get("last_status") == "failed":
            interval = timedelta(hours=1)
        last = _parse_iso(config.get("last_attempt_at"))
        if last and _now() - last < interval:
            return
        try:
            self.start()
        except Exception as error:  # noqa: BLE001 - 定时任务失败也要落到状态里
            self._record_failure(str(error)[:500] or "自动备份启动失败")

    def delete_snapshot(self, filename: str) -> dict[str, Any]:
        try:
            ensure_vault_writable(self.read_only, "删除完整备份")
        except VaultReadOnlyError as error:
            raise CloudBackupError(str(error)) from error
        if not SNAPSHOT_PATTERN.fullmatch(str(filename or "")):
            raise CloudBackupError("备份文件名不正确")
        config = load_config(self.vault_dir)
        cloud = _validate_cloud_dir(self.vault_dir, config["directory"])
        path = cloud / filename
        if path.is_symlink() or not path.is_file():
            raise CloudBackupError("备份文件不存在")
        path.unlink()
        self.database.audit("cloud_backup_deleted", {"filename": filename})
        return self.status()


class CloudBackupSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    directory: str = Field(default="", max_length=1000)
    frequency: Literal["manual", "daily", "weekly"] = "manual"
    keep: int = Field(default=7, ge=1, le=30)
    encrypt: bool = True
    password: str = Field(default="", max_length=200)


class CloudBackupRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False


def create_cloud_backup_router(service: CloudBackupService, *,
                               prefix: str = "/api/cloud-backup") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["cloud-backup"])

    @router.get("")
    def overview():
        try:
            return service.status()
        except CloudBackupError as error:
            raise HTTPException(409, str(error)) from error

    @router.put("/settings")
    def update(payload: CloudBackupSettingsRequest):
        try:
            return service.update_settings(
                directory=payload.directory, frequency=payload.frequency,
                keep=payload.keep, encrypt=payload.encrypt, password=payload.password)
        except CloudBackupError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/run")
    def run(payload: CloudBackupRunRequest):
        if payload.confirm is not True:
            raise HTTPException(422, "请明确确认创建完整备份")
        try:
            return service.start()
        except CloudBackupError as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/status")
    def status():
        try:
            return service.status()
        except CloudBackupError as error:
            raise HTTPException(409, str(error)) from error

    @router.delete("/files/{filename}")
    def delete(filename: str):
        try:
            return service.delete_snapshot(filename)
        except CloudBackupError as error:
            raise HTTPException(422, str(error)) from error

    return router


__all__ = [
    "CloudBackupError", "CloudBackupService", "create_snapshot", "encrypt_file",
    "decrypt_file", "load_config", "save_config", "set_password", "get_password",
    "password_is_set", "create_cloud_backup_router",
]
