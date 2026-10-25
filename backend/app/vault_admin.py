"""创建新的个人数据库（Vault）：严格按四库布局一次成型。

2026-10-26 新原则：四库在插件里只读，手动写入都在 Obsidian。为了让新库一打开就能用，
这里按 `vault_layout` 的章节表创建同一套目录，并预设 `.obsidian`、torch 插件和
`.torch-os/vault-layout.json`（章节目录的 1:1 映射清单）。

模板的唯一来源是 `vault_layout.CHAPTERS`：设置页只展示、不复制路径表；要改模板就改
`vault_layout.py`，创建与读取会同时跟着变。创建过程先写进同级临时目录，成功后再改名，
不会覆盖已有文件夹，也不会动当前库。
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import vault_layout
from .db import Database

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_SOURCE = PROJECT_ROOT / "obsidian-plugin"
PLUGIN_FILES = ("main.js", "manifest.json", "styles.css")
PLUGIN_DIR_RELATIVE = ".obsidian/plugins/torch"
MANIFEST_RELATIVE = ".torch-os/vault-layout.json"
TASK_NOTES_RELATIVE = "00_人生路/08_任务与待办/任务"
TASK_SYSTEM_RELATIVE = "00_人生路/08_任务与待办/_系统"
CORE_PLUGINS = {
    "file-explorer": True, "global-search": True, "switcher": True, "graph": True,
    "backlink": True, "canvas": True, "outgoing-link": True, "tag-pane": True,
    "properties": True, "page-preview": True, "daily-notes": True, "templates": True,
    "note-composer": True, "command-palette": True, "editor-status": True,
    "bookmarks": True, "outline": True, "word-count": True, "file-recovery": True,
}
INVALID_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
IDENTITY_SEED = """---
id: {identity_id}
type: identity
title: 身份（identity）
created: {stamp}
updated: {stamp}
name: ""
motto: ""
worldview: ""
lifeview: ""
values: ""
---

# 我是谁

## 三观

## 我要去哪里

"""


class VaultCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    parent_dir: str = Field(min_length=1, max_length=1000)
    name: str = Field(min_length=1, max_length=40)
    register_workspace: bool = True


def plugin_version(plugin_dir: Path = PLUGIN_SOURCE) -> str:
    try:
        payload = json.loads((plugin_dir / "manifest.json").read_text(encoding="utf-8-sig"))
        return str(payload.get("version") or "")
    except (OSError, ValueError):
        return ""


def _chapter_rows() -> list[dict[str, str]]:
    return [
        {"key": item[0], "pillar": item[1], "title": item[2],
         "path": item[3], "default_type": item[4]}
        for item in vault_layout.CHAPTERS
    ]


def template_payload(*, vault_dir: Path, runtime_label: str, vault_scoped: bool = False,
                     plugin_dir: Path = PLUGIN_SOURCE) -> dict[str, Any]:
    """设置页展示的模板：目录、种子文件与 Obsidian 预设，全部来自后端布局。"""
    return {
        "layout_version": vault_layout.LAYOUT_VERSION,
        "pillars": vault_layout.chapter_manifest()["pillars"],
        "chapters": _chapter_rows(),
        "directories": sorted({item[3] for item in vault_layout.CHAPTERS}),
        "extra_directories": [TASK_NOTES_RELATIVE, TASK_SYSTEM_RELATIVE,
                              PLUGIN_DIR_RELATIVE, ".torch-os"],
        "seed_files": [vault_layout.identity_paths()[0]],
        "manifest_file": MANIFEST_RELATIVE,
        "obsidian": {
            "app.json": {},
            "appearance.json": {},
            "core-plugins.json": CORE_PLUGINS,
            "community-plugins.json": ["torch"],
            "plugin": {"id": "torch", "version": plugin_version(plugin_dir),
                       "files": list(PLUGIN_FILES) + ["data.json"]},
        },
        "current": {
            "path": str(vault_dir),
            "runtime_label": runtime_label,
            "vault_scoped": bool(vault_scoped),
        },
        "template_source": "backend/app/vault_layout.py（唯一来源；改模板即改这里）",
        "note": "新库的章节目录与四库映射一一对应；创建只写新文件夹，不碰当前库。",
    }


def _validate_name(name: str) -> str:
    value = name.strip()
    if not value or len(value) > 40:
        raise HTTPException(422, "名字要 1–40 个字符")
    if INVALID_NAME.search(value) or value in {".", ".."}:
        raise HTTPException(422, '名字不能包含 \\ / : * ? " < > |')
    if value.endswith((" ", ".")):
        raise HTTPException(422, "名字不能以空格或句点结尾")
    if value.upper() in RESERVED_NAMES:
        raise HTTPException(422, "这是 Windows 保留名，换一个")
    return value


def _resolve_parent(raw: str) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise HTTPException(422, "父文件夹要写完整路径，例如 C:\\Users\\你\\Desktop")
    try:
        parent = candidate.resolve()
    except OSError as error:
        raise HTTPException(422, f"父文件夹路径无效：{error}") from error
    if not parent.is_dir():
        raise HTTPException(422, "父文件夹不存在，或不是一个文件夹")
    return parent


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _build_vault(root: Path, *, plugin_dir: Path) -> dict[str, Any]:
    """在 root 里写入完整模板；返回统计。root 必须是不存在的临时目录。"""
    created = vault_layout.ensure_layout(root)
    for relative in (TASK_NOTES_RELATIVE, TASK_SYSTEM_RELATIVE, PLUGIN_DIR_RELATIVE, ".torch-os"):
        path = root / relative
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(relative)

    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    identity_path = root / vault_layout.identity_paths()[0]
    _write_text(identity_path, IDENTITY_SEED.format(identity_id=uuid4().hex, stamp=stamp))

    obsidian = root / ".obsidian"
    _write_json(obsidian / "app.json", {})
    _write_json(obsidian / "appearance.json", {})
    _write_json(obsidian / "core-plugins.json", CORE_PLUGINS)
    _write_json(obsidian / "community-plugins.json", ["torch"])
    plugin_target = root / PLUGIN_DIR_RELATIVE
    for name in PLUGIN_FILES:
        source = plugin_dir / name
        if not source.is_file():
            raise HTTPException(500, f"仓库里缺少插件文件：{source.name}")
        shutil.copy2(source, plugin_target / name)
    _write_json(plugin_target / "data.json", {
        "baseUrl": "http://127.0.0.1:8765",
        "repoRoot": PROJECT_ROOT.as_posix(),
        "pythonPath": "",
        "autoStart": True,
        "instancePort": 0,
    })

    manifest = {
        "schema": 1,
        "layout_version": vault_layout.LAYOUT_VERSION,
        "created_at": stamp,
        "source": "backend/app/vault_layout.py",
        "plugin_version": plugin_version(plugin_dir),
        "pillars": vault_layout.chapter_manifest()["pillars"],
    }
    _write_json(root / MANIFEST_RELATIVE, manifest)

    return {
        "folders": created,
        "folder_count": len(created),
        "file_count": sum(1 for path in root.rglob("*") if path.is_file()),
        "layout_version": vault_layout.LAYOUT_VERSION,
        "plugin_version": plugin_version(plugin_dir),
    }


def create_vault_admin_router(*, database: Database, vault_dir: Path,
                              runtime_label: str, vault_scoped: bool = False,
                              plugin_dir: Path = PLUGIN_SOURCE,
                              registry_path: Path | None = None) -> APIRouter:
    from .workspaces import REGISTRY_PATH, load_registry, save_registry  # 延迟导入避免环

    registry = Path(registry_path) if registry_path is not None else REGISTRY_PATH
    router = APIRouter(prefix="/api", tags=["vault-admin"])

    def guard() -> None:
        if runtime_label != "personal":
            raise HTTPException(409, "隔离验收环境不能创建或切换个人数据库")

    @router.get("/vault/template")
    def vault_template():
        guard()
        return template_payload(vault_dir=Path(vault_dir), runtime_label=runtime_label,
                                vault_scoped=vault_scoped, plugin_dir=plugin_dir)

    @router.post("/vault/create", status_code=201)
    def create_vault(payload: VaultCreateRequest):
        guard()
        name = _validate_name(payload.name)
        parent = _resolve_parent(payload.parent_dir)
        target = (parent / name).resolve()
        if target.parent != parent:
            raise HTTPException(422, "目标路径越界了，请换一个名字")

        current = Path(vault_dir).resolve()
        if target == current or target in current.parents:
            raise HTTPException(409, "不能覆盖当前正在使用的数据库")
        if current in target.parents:
            raise HTTPException(409, "新数据库不能建在当前库里面；换一个父文件夹")

        if target.exists():
            if not target.is_dir():
                raise HTTPException(409, "同名文件已存在，换一个名字")
            try:
                if any(target.iterdir()):
                    raise HTTPException(409, "这个文件夹里已经有东西了；换个名字或换个空文件夹")
            except OSError as error:
                raise HTTPException(409, f"读不了目标文件夹：{error}") from error

        entries = load_registry(registry)
        if payload.register_workspace and any(item["name"].casefold() == name.casefold()
                                    and Path(item["folder"]) != target for item in entries):
            raise HTTPException(409, f"已经有叫“{name}”的数据库了；换一个名字")

        temporary = parent / f".torch-new-{uuid4().hex[:10]}"
        try:
            temporary.mkdir()
        except OSError as error:
            raise HTTPException(409, f"父文件夹不可写：{error}") from error
        try:
            stats = _build_vault(temporary, plugin_dir=plugin_dir)
            if target.exists():
                target.rmdir()  # 之前确认过是空目录
            temporary.rename(target)
        except HTTPException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        except OSError as error:
            shutil.rmtree(temporary, ignore_errors=True)
            raise HTTPException(409, f"创建失败：{error}") from error

        registered = False
        warning = ""
        if payload.register_workspace:
            entries = [item for item in entries if Path(item["folder"]) != target]
            entries.append({"name": name, "folder": str(target), "read_only": False})
            try:
                save_registry(entries, registry)
                registered = True
            except OSError as error:
                warning = f"文件夹已创建，但没能写进工作区列表：{error}"
        database.audit("vault_created", {
            "name_length": len(name),
            "folder_count": stats["folder_count"],
            "plugin_version": stats["plugin_version"],
            "registered": registered,
        })
        return {
            "created": True,
            "name": name,
            "path": str(target),
            "folders": stats["folder_count"],
            "files": stats["file_count"],
            "layout_version": stats["layout_version"],
            "plugin": {"version": stats["plugin_version"],
                       "path": str(target / PLUGIN_DIR_RELATIVE)},
            "registered": registered,
            "warning": warning,
            "obsidian_uri": f"obsidian://open?vault={quote(name)}",
            "next_steps": [
                "打开 Obsidian →「打开文件夹作为库」，选中上面的路径（第一次要手动选一次）。",
                "在插件列表里启用「薪火OS」；它会为这个库启动自己的后端（端口从 9168 起自动挑）。",
                "回到本页刷新，就能在数据库列表里看到它；切换库 = 在 Obsidian 里打开另一个库。",
            ],
            "note": "创建不会改动当前库；每个库的索引、备份与审计都在自己的 .torch-os 里。",
        }

    return router
