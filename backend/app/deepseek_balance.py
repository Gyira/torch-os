"""DeepSeek API 余额：官方 /user/balance 的本地代理与本机 Key 管理。

边界（和天气一样只做白名单出站）：
- 只请求 `https://api.deepseek.com/user/balance`（HTTPS、固定 host、超时、大小上限、无重试）；
- Key 只发到 DeepSeek 官方余额接口，不写日志、不返回给前端；
- 本机保存的 Key 用 AES-GCM 加密在 DATA_DIR，不进 Obsidian 库、备份、导出或 Markdown；
- Key 来源优先级：本机保存 > DEEPSEEK_API_KEY 环境变量 > DSH 凭据里的同名 Key（只读，不复制）；
- QA/离线模式 `enabled=False`：不读 Key、不联网，只返回禁用状态。
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

SHANGHAI = ZoneInfo("Asia/Shanghai")
BALANCE_URL = "https://api.deepseek.com/user/balance"
TOP_UP_URL = "https://platform.deepseek.com/top_up"
TIMEOUT_SECONDS = 8.0
MAX_RESPONSE_BYTES = 256_000
CACHE_SECONDS = 30.0
SETTINGS_FILE = "deepseek-balance.json"
KEY_FILE = "deepseek-balance.key"
MAX_KEY_LENGTH = 300

_DSH_KEY = re.compile(r'(?m)^\s*DEEPSEEK_API_KEY\s*:\s*["\']?(sk-[^\s"\']+)["\']?\s*(?:#.*)?$')


class DeepSeekBalanceError(RuntimeError):
    """可安全展示给界面的余额读取失败；message 不能包含 Key。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class BalanceKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(min_length=8, max_length=MAX_KEY_LENGTH)


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:  # pragma: no cover - dependency should be installed
        raise ValueError("缺少 cryptography 依赖，无法保存 DeepSeek Key") from exc
    return AESGCM


def _money(value: Any) -> str | None:
    """保留接口给的小数文本；取不到就是 None（不臆造 0）。"""
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return format(number, "f")


def _pick_balance_info(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise DeepSeekBalanceError("SHAPE", "余额接口返回结构异常")
    infos = data.get("balance_infos")
    if not isinstance(infos, list) or not infos:
        raise DeepSeekBalanceError("SHAPE", "余额接口没有返回 balance_infos")
    wallets = [item for item in infos if isinstance(item, dict)]
    if not wallets:
        raise DeepSeekBalanceError("SHAPE", "余额接口返回的钱包结构异常")

    def currency(item: dict[str, Any]) -> str:
        return str(item.get("currency") or "CNY").upper()

    def total(item: dict[str, Any]) -> Decimal | None:
        try:
            number = Decimal(str(item.get("total_balance")))
        except (InvalidOperation, ValueError):
            return None
        return number if number.is_finite() else None

    valid = [item for item in wallets if total(item) is not None]
    if not valid:
        raise DeepSeekBalanceError("SHAPE", "余额接口没有返回可用的 total_balance")
    positive = [item for item in valid if (total(item) or Decimal(0)) > 0]
    picked = (
        next((item for item in positive if currency(item) == "CNY"), None)
        or (positive[0] if positive else None)
        or next((item for item in valid if currency(item) == "CNY"), None)
        or valid[0]
    )
    return {
        "is_available": bool(data.get("is_available", True)),
        "currency": currency(picked),
        "total_balance": _money(picked.get("total_balance")),
        "topped_up_balance": _money(picked.get("topped_up_balance")),
        "granted_balance": _money(picked.get("granted_balance")),
    }


def http_fetch_balance(key: str, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """One allow-listed GET: https, fixed host, timeout, byte cap, no retry."""
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, trust_env=False, transport=transport) as client:
            response = client.get(
                BALANCE_URL,
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            )
    except httpx.TimeoutException as exc:
        raise DeepSeekBalanceError("NETWORK", "连接 DeepSeek 超时，请稍后重试") from exc
    except httpx.HTTPError as exc:
        raise DeepSeekBalanceError("NETWORK", "无法连接 DeepSeek，请检查网络后重试") from exc
    if response.status_code in {401, 403}:
        raise DeepSeekBalanceError("AUTH", "DeepSeek API Key 无效或已过期")
    if response.status_code == 429:
        raise DeepSeekBalanceError("RATE", "余额查询太频繁，请稍后再试")
    if response.status_code >= 500:
        raise DeepSeekBalanceError("SERVER", "DeepSeek 服务暂时不可用，请稍后再试")
    if response.status_code != 200:
        raise DeepSeekBalanceError("HTTP", f"余额接口返回 HTTP {response.status_code}")
    body = response.content
    if len(body) > MAX_RESPONSE_BYTES:
        raise DeepSeekBalanceError("SHAPE", "余额接口返回内容超过大小上限")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise DeepSeekBalanceError("SHAPE", "余额接口返回不是合法 JSON") from exc


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


class DeepSeekBalanceService:
    """余额读取与 Key 解析；测试可注入 fetcher / environ / dsh_home / clock。"""

    def __init__(self, data_dir: Path, *, enabled: bool = True,
                 fetcher: Callable[[str], dict[str, Any]] | None = None,
                 environ: dict[str, str] | None = None,
                 dsh_home: Path | None = None,
                 clock: Callable[[], float] | None = None):
        self.data_dir = Path(data_dir)
        self.enabled = bool(enabled)
        self.fetcher = fetcher or http_fetch_balance
        self.environ = dict(os.environ if environ is None else environ)
        self.dsh_home = Path(dsh_home) if dsh_home is not None else None
        self.clock = clock or (lambda: datetime.now().timestamp())
        self.settings_path = self.data_dir / SETTINGS_FILE
        self.key_path = self.data_dir / KEY_FILE
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0

    def _now(self) -> str:
        return datetime.fromtimestamp(float(self.clock()), SHANGHAI).isoformat(timespec="seconds")

    def _load_aes_key(self, *, create: bool = False) -> bytes:
        if self.key_path.is_file():
            try:
                raw = self.key_path.read_bytes()
            except OSError:
                raw = b""
            if len(raw) == 32:
                return raw
        if not create:
            return b""
        raw = os.urandom(32)
        _atomic_write(self.key_path, raw)
        return raw

    def _read_saved_key(self) -> str:
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                return ""
            blob = base64.b64decode(str(payload.get("api_key_enc") or ""))
            aes_key = self._load_aes_key()
            if not aes_key or len(blob) < 13:
                return ""
            raw = _aesgcm()(aes_key).decrypt(blob[:12], blob[12:], None)
            return raw.decode("utf-8").strip()
        except Exception:
            return ""

    def _env_key(self) -> str:
        return str(self.environ.get("DEEPSEEK_API_KEY") or "").strip()

    def _dsh_home(self) -> Path:
        if self.dsh_home is not None:
            return self.dsh_home
        configured = str(self.environ.get("DSH_HOME") or "").strip()
        return Path(configured) if configured else Path.home() / ".dsh"

    def _dsh_key(self) -> str:
        if not self.enabled:
            return ""
        path = self._dsh_home() / ".credentials.yaml"
        try:
            if not path.is_file() or path.stat().st_size > 1_000_000:
                return ""
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return ""
        match = _DSH_KEY.search(text)
        return match.group(1).strip() if match else ""

    def _resolve(self) -> tuple[str, str, dict[str, bool]]:
        saved = self._read_saved_key()
        env = self._env_key()
        dsh = self._dsh_key()
        sources = {"saved": bool(saved), "env": bool(env), "dsh": bool(dsh)}
        if saved:
            return saved, "saved", sources
        if env:
            return env, "env", sources
        if dsh:
            return dsh, "dsh", sources
        return "", "", sources

    def resolve_key(self) -> str:
        """当前可用的 Key（saved > env > dsh），只给子进程注入环境用，不写日志、不回显。"""
        if not self.enabled:
            return ""
        return self._resolve()[0]

    def describe(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "saved": False, "env": False, "dsh": False, "source": ""}
        _, source, sources = self._resolve()
        return {"enabled": True, **sources, "source": source}

    def save_key(self, key: str) -> dict[str, Any]:
        if not self.enabled:
            raise ValueError("当前运行模式不允许保存 DeepSeek API Key")
        value = str(key or "").strip()
        if not value:
            raise ValueError("DeepSeek API Key 不能为空")
        if len(value) > MAX_KEY_LENGTH:
            raise ValueError("DeepSeek API Key 过长")
        aes_key = self._load_aes_key(create=True)
        if not aes_key:
            raise ValueError("无法创建本机 Key 加密文件")
        nonce = os.urandom(12)
        ciphertext = _aesgcm()(aes_key).encrypt(nonce, value.encode("utf-8"), None)
        payload = {
            "version": 1,
            "api_key_enc": base64.b64encode(nonce + ciphertext).decode("ascii"),
            "updated_at": self._now(),
        }
        _atomic_write(self.settings_path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        self._cache = None
        return {"ok": True, "saved": True}

    def clear_key(self) -> dict[str, Any]:
        with contextlib.suppress(FileNotFoundError):
            self.settings_path.unlink()
        self._cache = None
        return {"ok": True, "saved": False}

    def _error(self, code: str, message: str, source: str, sources: dict[str, bool]) -> dict[str, Any]:
        return {
            "ok": False,
            "enabled": self.enabled,
            "configured": bool(source),
            "source": source,
            "sources": sources,
            "code": code,
            "error": message,
            "top_up_url": TOP_UP_URL,
            "fetched_at": self._now(),
        }

    def balance(self, *, refresh: bool = False) -> dict[str, Any]:
        if not self.enabled:
            return {
                "ok": False, "enabled": False, "configured": False, "source": "",
                "sources": {"saved": False, "env": False, "dsh": False},
                "code": "DISABLED", "error": "当前运行模式不允许读取 DeepSeek 余额",
                "top_up_url": TOP_UP_URL, "fetched_at": self._now(),
            }
        key, source, sources = self._resolve()
        if not key:
            return self._error("NO_KEY", "还没有配置 DeepSeek API Key", source, sources)
        now = float(self.clock())
        fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
        if (self._cache is not None and not refresh
                and self._cache.get("fingerprint") == fingerprint
                and now - self._cache_at < CACHE_SECONDS):
            cached = dict(self._cache["payload"])
            cached.update({"cached": True, "source": source, "sources": sources})
            return cached
        try:
            data = self.fetcher(key)
            info = _pick_balance_info(data)
        except DeepSeekBalanceError as error:
            return self._error(error.code, error.message, source, sources)
        except Exception:
            return self._error("NETWORK", "读取余额失败，请稍后重试", source, sources)
        payload = {
            "ok": True,
            "enabled": True,
            "configured": True,
            "source": source,
            "sources": sources,
            **info,
            "cached": False,
            "top_up_url": TOP_UP_URL,
            "fetched_at": self._now(),
        }
        self._cache = {"fingerprint": fingerprint, "payload": payload}
        self._cache_at = now
        return payload


def create_deepseek_balance_router(
    service: DeepSeekBalanceService, prefix: str = "/api/deepseek",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["deepseek-balance"])

    @router.get("/balance")
    def get_balance(refresh: int = Query(default=0, ge=0, le=1)):
        return service.balance(refresh=bool(refresh))

    @router.get("/balance/settings")
    def get_balance_settings():
        return service.describe()

    @router.post("/balance/key")
    def save_balance_key(payload: BalanceKeyRequest):
        try:
            return service.save_key(payload.key)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.delete("/balance/key")
    def clear_balance_key():
        return service.clear_key()

    return router
