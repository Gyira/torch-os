"""恋爱系统本机凭据（WeFlow Token）。

凭据保存在 ``02_微信聊天/.credentials.json``，Token 不落明文：
- Windows 优先用 DPAPI（当前用户/机器绑定，不需要额外密钥文件）；
- 其他平台或 DPAPI 不可用时，退回 AES-GCM，并在同目录保存一个仅当前用户可读的
  ``.credential.key``。

接口只返回 ``token_configured``，不回显 Token。旧版 ``<.torch-os/love>`` 里的
``weflow_token`` 由迁移代码解密后重新写入这里。
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import secrets
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CREDENTIALS_VERSION = 1
KEY_NAME = ".credential.key"
MAX_TOKEN = 400


class CredentialError(RuntimeError):
    """凭据读写失败；消息可直接展示。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Windows DPAPI ──────────────────────────────────────────────────────

class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _dpapi_encrypt(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, _in_buffer = _blob(data)
    out_blob = _DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob))
    if not ok:
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_decrypt(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, _in_buffer = _blob(data)
    out_blob = _DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob))
    if not ok:
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _aes_key(path: Path) -> bytes:
    key_path = path.parent / KEY_NAME
    try:
        raw = key_path.read_bytes()
        if len(raw) == 32:
            return raw
    except OSError:
        pass
    key = secrets.token_bytes(32)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return key


def _aes_encrypt(path: Path, data: bytes) -> tuple[str, str]:
    key = _aes_key(path)
    nonce = secrets.token_bytes(12)
    cipher = AESGCM(key).encrypt(nonce, data, b"torch-love-credentials")
    return base64.b64encode(nonce + cipher).decode("ascii"), "aesgcm"


def _aes_decrypt(path: Path, payload: str) -> bytes:
    key = _aes_key(path)
    raw = base64.b64decode(payload.encode("ascii"))
    return AESGCM(key).decrypt(raw[:12], raw[12:], b"torch-love-credentials")


# ── 文件读写 ───────────────────────────────────────────────────────────

def _read(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise CredentialError(f"凭据文件读取失败：{error}") from error
    return payload if isinstance(payload, dict) else {}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(3)}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def load_credentials(path: Path | str) -> dict:
    """返回 ``{port, token, provider}``；没有 Token 时 token 为空串。"""
    target = Path(path)
    payload = _read(target)
    port = payload.get("port")
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 5031
    if not 1 <= port <= 65535:
        port = 5031
    token = ""
    encrypted = str(payload.get("token") or "").strip()
    provider = str(payload.get("provider") or "").strip()
    if encrypted:
        try:
            if provider == "dpapi":
                token = _dpapi_decrypt(base64.b64decode(encrypted)).decode("utf-8")
            elif provider == "aesgcm":
                token = _aes_decrypt(target, encrypted).decode("utf-8")
            else:
                # 未知 provider：不猜测明文，按“未配置”处理。
                token = ""
        except Exception as error:  # noqa: BLE001 - 凭据损坏时按未配置处理
            token = ""
            raise CredentialError(f"凭据解密失败：{error}") from error
    return {"port": port, "token": token.strip(), "provider": provider}


def public_status(path: Path | str) -> dict:
    try:
        credentials = load_credentials(path)
    except CredentialError:
        return {"port": 5031, "token_configured": False, "error": "凭据文件损坏，请重新填写 Token"}
    return {
        "port": credentials["port"],
        "token_configured": bool(credentials["token"]),
    }


def save_credentials(path: Path | str, *, port: int, token: str = "",
                     keep_existing: bool = True) -> dict:
    target = Path(path)
    try:
        current = load_credentials(target)
    except CredentialError:
        current = {"port": 5031, "token": ""}
    value = str(token or "").strip()
    if not value and keep_existing:
        value = current.get("token", "")
    if len(value) > MAX_TOKEN:
        raise CredentialError("WeFlow Token 过长")
    if not 1 <= int(port) <= 65535:
        raise CredentialError("WeFlow 端口应在 1-65535 之间")
    encrypted = ""
    provider = ""
    if value:
        try:
            encrypted = base64.b64encode(_dpapi_encrypt(value.encode("utf-8"))).decode("ascii")
            provider = "dpapi"
        except Exception:  # noqa: BLE001 - 非 Windows 或 DPAPI 不可用
            encrypted, provider = _aes_encrypt(target, value.encode("utf-8"))
    _write(target, {
        "version": CREDENTIALS_VERSION,
        "port": int(port),
        "token": encrypted,
        "provider": provider,
        "updated_at": _now(),
    })
    return public_status(target)


def clear_token(path: Path | str) -> dict:
    target = Path(path)
    try:
        current = load_credentials(target)
    except CredentialError:
        current = {"port": 5031}
    _write(target, {
        "version": CREDENTIALS_VERSION,
        "port": current.get("port", 5031),
        "token": "",
        "provider": "",
        "updated_at": _now(),
    })
    return public_status(target)


__all__ = ["CredentialError", "load_credentials", "public_status",
           "save_credentials", "clear_token"]
