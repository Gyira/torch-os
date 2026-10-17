"""工作区（用户）：一个人 = 一个文件夹。

文件夹就是他的 Obsidian 库根目录；应用自己的 SQLite 索引/记忆/审计、备份、恢复副本与导入
都放在 `<文件夹>/.torch-os/`（隐藏目录，Obsidian 不显示、扫描器按点目录跳过）。
因此「换用户」就是换文件夹：拷贝整个文件夹 = 拷贝这个人的全部数据。

切换工作区会把 `DATA_DIR` / `VAULT_DIR` / `VAULT_READ_ONLY` / `WORKSPACE_NAME` 写进本地
`.env`，**重启服务后生效**——与现有「Obsidian 库连接」一致。运行中的进程不会原地换库：
那会让两个人的索引、记忆与审计串在一起。

注册表固定放在仓库的 `data/workspaces.json`（`data/` 已被 Git 忽略），只记录最近用过的文件夹；
即使注册表丢了，`.env` 里的当前配置仍会作为一个「当前工作区」列出来。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .db import Database
from .envfile import write_env_value
from .vault_connection import normalize_vault_path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "data" / "workspaces.json"
DEFAULT_NAME = "个人"
REGISTRY_VERSION = 2
APP_DIR_NAME = ".torch-os"
INVALID_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class WorkspaceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=40)
    folder: str = Field(min_length=1, max_length=1000)
    read_only: bool = False
    original_name: str = Field(default="", max_length=40)


class WorkspaceName(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=40)


def data_dir_for(folder: str | Path) -> Path:
    """应用数据目录：用户文件夹下的隐藏目录，扫描与备份都会跳过。"""
    return Path(folder) / APP_DIR_NAME


def load_registry(path: Path = REGISTRY_PATH) -> list[dict[str, Any]]:
    """读取注册表；缺失或损坏时返回空表（当前 .env 配置仍会作为工作区显示）。

    兼容早期 v1 条目（vault/data）：统一折叠成 folder；v1 的自定义数据目录不再保留
    （本功能刚落地，迁移由切换/激活流程完成）。
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = payload.get("workspaces") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        folder = str(entry.get("folder") or entry.get("vault") or "").strip()
        if not name or not folder:
            continue
        cleaned.append({"name": name, "folder": folder,
                        "read_only": bool(entry.get("read_only", False))})
    return cleaned


def save_registry(entries: list[dict[str, Any]], path: Path = REGISTRY_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": REGISTRY_VERSION, "workspaces": entries}
    temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex[:8]}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _resolved(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def validate_workspace(payload: WorkspaceWrite, *, entries: list[dict[str, Any]],
                       updating: str | None = None) -> dict[str, Any]:
    """校验并归一化一条工作区；不创建目录、不写注册表。"""
    name = payload.name.strip()
    if not name or len(name) > 40 or INVALID_NAME.search(name):
        raise HTTPException(422, "工作区名字要 1–40 个字符，且不能包含 \\ / : * ? \" < > |")
    for entry in entries:
        if entry["name"].casefold() == name.casefold() and entry["name"] != updating:
            raise HTTPException(422, f"已经有叫“{name}”的工作区了，请换一个名字")

    try:
        folder = normalize_vault_path(payload.folder)
    except ValueError as error:
        raise HTTPException(422, f"文件夹路径无效：{error}") from error

    return {"name": name, "folder": str(folder), "read_only": bool(payload.read_only)}


def describe_workspaces(entries: list[dict[str, Any]], *, data_dir: Path, vault_dir: Path,
                        read_only: bool, runtime_label: str) -> dict[str, Any]:
    """列出工作区并标出当前运行中的那一个；data 永远是 `<folder>/.torch-os`。"""
    active_data = _resolved(data_dir)
    active_vault = _resolved(vault_dir)
    listed: list[dict[str, Any]] = []
    active_name: str | None = None
    for entry in entries:
        try:
            folder = _resolved(entry["folder"])
            data = data_dir_for(folder)
            is_active = folder == active_vault and data == active_data
        except OSError:
            is_active = False
        item = {
            "name": entry["name"],
            "folder": entry["folder"],
            "data": str(data_dir_for(entry["folder"])),
            "read_only": entry["read_only"],
            "active": is_active,
            "registered": True,
        }
        if is_active:
            active_name = entry["name"]
        listed.append(item)
    if active_name is None:
        name = os.getenv("WORKSPACE_NAME", "").strip() or (
            DEFAULT_NAME if runtime_label == "personal" else "当前工作区")
        listed.insert(0, {
            "name": name,
            "folder": str(vault_dir),
            "data": str(data_dir),
            "read_only": bool(read_only),
            "active": True,
            "registered": False,
        })
        active_name = name
    if runtime_label != "personal":
        # QA 隔离环境只展示它自己，不暴露个人工作区列表。
        listed = [item for item in listed if item["active"]]
    return {
        "workspaces": listed,
        "active": active_name,
        "restart_required_for_changes": True,
        "note": "换工作区就是换文件夹；索引、记忆、备份放在该文件夹的 .torch-os 里，重启服务后生效。",
    }


def create_workspaces_router(*, data_dir: Path, vault_dir: Path, read_only: bool,
                             env_path: Path, database: Database, runtime_label: str,
                             vault_scoped: bool = False,
                             registry_path: Path = REGISTRY_PATH) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["workspaces"])

    def guard_writable() -> None:
        if vault_scoped:
            raise HTTPException(409, "当前实例由 Obsidian 库承载，不能切换工作区；"
                                     "在 Obsidian 里打开另一个库即可使用那里的工作区。")
        if runtime_label != "personal":
            raise HTTPException(409, "隔离验收环境不允许管理工作区")

    def entries() -> list[dict[str, Any]]:
        return load_registry(registry_path)

    @router.get("/workspaces")
    def list_workspaces():
        return describe_workspaces(entries(), data_dir=data_dir, vault_dir=vault_dir,
                                   read_only=read_only, runtime_label=runtime_label)

    @router.post("/workspaces")
    def save_workspace(payload: WorkspaceWrite):
        guard_writable()
        current = entries()
        original = payload.original_name.strip() or payload.name.strip()
        existing = next((item for item in current
                         if item["name"].casefold() == original.casefold()), None)
        entry = validate_workspace(payload, entries=current,
                                   updating=existing["name"] if existing else None)
        if existing:
            current = [entry if item["name"] == existing["name"] else item for item in current]
        else:
            current.append(entry)
        save_registry(current, registry_path)
        database.audit("workspace_saved", {
            "created": existing is None,
            "read_only": entry["read_only"],
            "path_length": len(entry["folder"]),
            "looks_like_obsidian": (Path(entry["folder"]) / ".obsidian").is_dir(),
        })
        return {"saved": True,
                "workspace": {**entry, "data": str(data_dir_for(entry["folder"])),
                              "active": False, "registered": True},
                "note": "已保存到工作区列表；点「切换到此工作区」并重启服务后生效。"}

    @router.post("/workspaces/activate")
    def activate_workspace(payload: WorkspaceName):
        guard_writable()
        current = entries()
        entry = next((item for item in current
                      if item["name"].casefold() == payload.name.strip().casefold()), None)
        if entry is None:
            raise HTTPException(404, "找不到这个工作区，请刷新后重试")
        target_folder = Path(entry["folder"])
        target_data = data_dir_for(target_folder)
        if not target_folder.is_dir():
            raise HTTPException(422, "这个工作区的文件夹不存在了，请先修改路径")
        try:
            target_data.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise HTTPException(409, f"建不了数据目录：{error}") from error
        try:
            write_env_value(env_path, "DATA_DIR", str(target_data))
            write_env_value(env_path, "VAULT_DIR", str(target_folder))
            write_env_value(env_path, "VAULT_READ_ONLY", "1" if entry["read_only"] else "0")
            write_env_value(env_path, "WORKSPACE_NAME", entry["name"])
        except OSError as error:
            raise HTTPException(409, "无法写入本地 .env；请检查文件权限后重试") from error
        database.audit("workspace_activated", {
            "read_only": entry["read_only"],
            "path_length": len(entry["folder"]),
            "looks_like_obsidian": (target_folder / ".obsidian").is_dir(),
        })
        return {**entry, "data": str(target_data), "active": False, "saved": True,
                "restart_required": True,
                "note": "已写入本地 .env；重启本地服务后切换到该文件夹。首次进入建议在「第二大脑」点一次扫描，建立这一套数据的索引。"}

    @router.post("/workspaces/delete")
    def delete_workspace(payload: WorkspaceName):
        guard_writable()
        current = entries()
        entry = next((item for item in current
                      if item["name"].casefold() == payload.name.strip().casefold()), None)
        if entry is None:
            raise HTTPException(404, "找不到这个工作区，请刷新后重试")
        active = describe_workspaces(current, data_dir=data_dir, vault_dir=vault_dir,
                                     read_only=read_only, runtime_label=runtime_label)
        if active["active"] and active["active"].casefold() == entry["name"].casefold():
            raise HTTPException(409, "正在使用的工作区不能删除；先切换到别的工作区再删")
        current = [item for item in current if item["name"] != entry["name"]]
        save_registry(current, registry_path)
        database.audit("workspace_removed", {"read_only": entry["read_only"]})
        return {"deleted": entry["name"], "note": "已从列表移除；磁盘上的文件夹没有删除。"}

    return router
