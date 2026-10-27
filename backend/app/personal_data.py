"""顶层个性化数据：`<个人数据库>/.torch-os/personal-data.json`。

四库放 Markdown 正文；系统设置、小组件偏好、排序、城市列表这类“只属于这台
设备/这个库”的数据统一放 `.torch-os/`。浏览器 localStorage 只作为本窗口的
即时缓存：启动时由这里灌入，修改后由前端防抖镜像回来。

文件格式保持简单（键值都是字符串，null 表示删除），这样 Obsidian 库整个复制
到另一台机器时，个性化数据也跟着走，不依赖浏览器 profile。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .db import utc_now
from .vault import safe_note_path
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

PERSONAL_DATA_RELATIVE = ".torch-os/personal-data.json"
PERSONAL_DATA_VERSION = 1
# 倒计时/纪念日允许本机图片背景（前端上限 1.2MB，base64 后约 1.6MB），
# 所以单值上限要能装下一张图片；合计仍限制在 8MB，避免无限膨胀。
MAX_VALUE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_KEYS = 500

# 只允许 torch-os 自己的命名空间，避免把 localStorage 里的第三方键也吸进来。
KEY_PATTERN = re.compile(r"^(?:torch-os:|torch:|torch\.)[A-Za-z0-9:._-]{1,200}$")


def _data_path(vault_dir: Path | str) -> Path:
    return safe_note_path(Path(vault_dir), PERSONAL_DATA_RELATIVE)


def _valid_key(key: Any) -> bool:
    return isinstance(key, str) and bool(KEY_PATTERN.fullmatch(key))


def _load_values(path: Path) -> tuple[dict[str, str], str | None]:
    if not path.is_file():
        return {}, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}, None
    if not isinstance(payload, dict):
        return {}, None
    raw_values = payload.get("values")
    if not isinstance(raw_values, dict):
        return {}, payload.get("updated_at") if isinstance(payload.get("updated_at"), str) else None
    values = {
        key: value for key, value in raw_values.items()
        if _valid_key(key) and isinstance(value, str)
    }
    updated_at = payload.get("updated_at")
    return values, updated_at if isinstance(updated_at, str) else None


def read_personal_data(vault_dir: Path | str, *, vault_id: str = "") -> dict[str, Any]:
    path = _data_path(vault_dir)
    values, updated_at = _load_values(path)
    return {
        "path": PERSONAL_DATA_RELATIVE,
        "exists": path.is_file(),
        "updated_at": updated_at,
        "vault_id": vault_id,
        "values": values,
    }


class PersonalDataWrite(BaseModel):
    """局部更新：只覆盖传入的键，值为 null 表示删除。"""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, str | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_values(self):
        if len(self.values) > MAX_KEYS:
            raise ValueError(f"一次最多写入 {MAX_KEYS} 个个性化键")
        total = 0
        for key, value in self.values.items():
            if not _valid_key(key):
                raise ValueError(f"个性化键名不合法：{key}")
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"{key} 的值必须是字符串")
            size = len(value.encode("utf-8"))
            if size > MAX_VALUE_BYTES:
                raise ValueError(f"{key} 的值超过 {MAX_VALUE_BYTES // 1024}KB 上限")
            total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError(f"个性化数据合计超过 {MAX_TOTAL_BYTES // 1024 // 1024}MB 上限")
        return self


def write_personal_data(vault_dir: Path | str, payload: PersonalDataWrite, *,
                        read_only: bool = False) -> dict[str, Any]:
    try:
        ensure_vault_writable(read_only, "保存个性化设置")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    path = _data_path(vault_dir)
    values, _ = _load_values(path)
    for key, value in payload.values.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    total = sum(len(value.encode("utf-8")) for value in values.values())
    if total > MAX_TOTAL_BYTES:
        raise HTTPException(413, f"个性化数据合计超过 {MAX_TOTAL_BYTES // 1024 // 1024}MB 上限")
    document = {
        "version": PERSONAL_DATA_VERSION,
        "updated_at": utc_now(),
        "values": values,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    # 不把整份数据回传：小组件可能存了 1MB+ 的图片 data URL，写一次就回显一次太浪费。
    return {
        "ok": True,
        "path": PERSONAL_DATA_RELATIVE,
        "updated_at": document["updated_at"],
        "keys": len(values),
    }


def create_personal_data_router(vault_dir: Path | str, *, read_only: bool = False,
                                prefix: str = "/api", vault_id: str = "") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["personal-data"])

    @router.get("/personal-data")
    def personal_data_read():
        return read_personal_data(vault_dir, vault_id=vault_id)

    @router.put("/personal-data")
    def personal_data_write(payload: PersonalDataWrite):
        return write_personal_data(vault_dir, payload, read_only=read_only)

    return router
