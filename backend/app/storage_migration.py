"""Copy-only preparation for split storage. Never switches services or deletes source data."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from dataclasses import replace

from .storage_layout import StorageLayout, checked_path, initialize_empty_workspace, legacy_identity, workspace_identity
from .storage_audit import audit_references
from .storage_bundle import digest
from .cloud_backup import _is_sqlite, _snapshot_sqlite
from .vault_layout import LEGACY_LOVE_DIRS, LOVE_PAGE_DIRS

MEDIA = {
    LOVE_PAGE_DIRS[1] + "/media": "archive", LOVE_PAGE_DIRS[1] + "/exports": "archive",
    LOVE_PAGE_DIRS[2] + "/xlsx": "archive", LOVE_PAGE_DIRS[4] + "/照片": "archive",
    LOVE_PAGE_DIRS[4] + "/视频": "archive", LOVE_PAGE_DIRS[4] + "/thumbnails": "cache",
}


def prepare_split(source, layout: StorageLayout, *, database_path: Path, writers_stopped=False):
    source, database_path = checked_path(source), checked_path(database_path)
    existing_identity = workspace_identity(source)
    if existing_identity:
        layout = replace(layout, workspace_id=existing_identity)
    if not writers_stopped:
        raise ValueError("复制快照前必须安排所有写入方停写；本工具不会关闭用户程序")
    if not source.is_dir() or not database_path.is_file() or not _is_sqlite(database_path):
        raise ValueError("需要有效源库与明确指定的权威 SQLite 数据库")
    reserved_main = source / ".torch-os/torch_os.sqlite3"
    if reserved_main.exists() and reserved_main.resolve() != database_path:
        raise ValueError("源库中存在另一份同名数据库，需人工确认权威源")
    for key in ("notes", "state", "archive", "cache", "exports"):
        root = checked_path(getattr(layout, key))
        if root.is_relative_to(source) or source.is_relative_to(root) or root.exists():
            raise ValueError("目标必须是不存在且与源库分离的新目录")
    report = audit_references(source)
    referenced = {match for item in report["references"] if item["status"] == "resolved" for match in item["matches"]}
    operations = []
    attachment_bytes = 0
    for directory, dirs, names in os.walk(source, followlinks=False):
        for name in dirs:
            checked_path(Path(directory) / name)
        for name in sorted(names):
            path = checked_path(Path(directory) / name)
            if not path.is_file() or name.endswith(("-wal", "-shm")):
                continue
            rel = path.relative_to(source).as_posix()
            destinations = []
            media = next((p for p in MEDIA if rel.startswith(p + "/")), None)
            if media:
                page = Path(media).parent.name
                suffix = rel[len(media) + 1:]
                destinations.append((MEDIA[media], f"love/{page}/{Path(media).name}/{suffix}" if MEDIA[media] != "cache" else f"love/thumbnails/{suffix}"))
            elif any(rel.startswith(p + "/") for p in (*LOVE_PAGE_DIRS[1:], *LEGACY_LOVE_DIRS)) and not rel.endswith(".md"):
                page = next(p for p in (*LOVE_PAGE_DIRS[1:], *LEGACY_LOVE_DIRS) if rel.startswith(p + "/"))
                destinations.append(("state", f"love/{Path(page).name}/{rel[len(page) + 1:]}"))
            elif rel.startswith(".torch-os/"):
                remainder = rel[len(".torch-os/"):]
                destinations.append(("state", "vault-state/" + remainder))
            elif rel.endswith(".md") and (rel.startswith(("人生路/", "自我档案/", "生活运营/", "知识库/", "00_", "01_", "02_", "03_")) or rel.startswith(("_Torch/收件/", "_Torch/回执/"))):
                destinations.append(("notes", rel))
            else:
                destinations.append(("archive", "legacy-vault/" + rel))
            if rel in referenced and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}:
                if path.stat().st_size > 1048576:
                    raise ValueError("笔记引用了超过首版移动预算的附件，需先生成附件说明或选择迁移策略")
                attachment_bytes += path.stat().st_size
                if attachment_bytes > 50 * 1048576:
                    raise ValueError("必要附件超过 50 MiB，请先调整同步范围")
                destinations.append(("notes", rel))
            operations.append((path, list(dict.fromkeys(destinations))))
    legacy_layout = StorageLayout.legacy(source, database_path)
    old_capture = checked_path(legacy_layout.share_database)
    if old_capture.is_file():
        if not _is_sqlite(old_capture):
            raise ValueError("原分享台账不是有效 SQLite，请先核对")
        operations.append((old_capture, [("state", "share-captures.sqlite3")]))
    old_exports = checked_path(legacy_layout.context_exports)
    if old_exports.is_dir():
        for directory, dirs, names in os.walk(old_exports, followlinks=False):
            for name in dirs:
                checked_path(Path(directory) / name)
            for name in names:
                path = checked_path(Path(directory) / name)
                operations.append((path, [("exports", "context-packs/" + path.relative_to(old_exports).as_posix())]))
    bytes_required = sum(p.stat().st_size * len(destinations) for p, destinations in operations) + database_path.stat().st_size
    ancestor = layout.notes.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    if shutil.disk_usage(ancestor).free < bytes_required * 2 + 64 * 1048576:
        raise ValueError("空间不足以容纳目标副本及后续恢复演练")
    # The marker is created only after planning checks. Failure never removes source files.
    initialize_empty_workspace(layout)
    for key in ("state", "archive", "cache", "exports"):
        getattr(layout, key).mkdir(parents=True, exist_ok=False)
    copied = []
    for path, destinations in operations:
        before = (path.stat().st_size, path.stat().st_mtime_ns, digest(path))
        for role, relative in destinations:
            target = checked_path(getattr(layout, role) / relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError("迁移目标发生路径碰撞，已停止并保留源库")
            if _is_sqlite(path):
                _snapshot_sqlite(path, target)
            else:
                with path.open("rb") as inp, target.open("xb") as out:
                    shutil.copyfileobj(inp, out, 1048576)
                if digest(target) != before[2]:
                    raise ValueError("复制校验失败")
            copied.append({"source": path.relative_to(source).as_posix() if path.is_relative_to(source) else str(path), "role": role, "path": relative, "sha256": digest(target), "bytes": target.stat().st_size})
        if before != (path.stat().st_size, path.stat().st_mtime_ns, digest(path)):
            raise ValueError("源库仍在写入，已停止；不得激活未完成目标")
    main = layout.state / "vault-state/torch_os.sqlite3"
    main.parent.mkdir(parents=True, exist_ok=True)
    if not main.exists():
        _snapshot_sqlite(database_path, main)
    elif (source / ".torch-os/torch_os.sqlite3").resolve() != database_path:
        raise ValueError("源库中存在另一份同名数据库，需人工确认权威源")
    from .device_registry import DeviceRegistry
    registry = DeviceRegistry(layout.state / "device")
    registry.device_id()
    registry.bind(layout, legacy_paths=(source,))
    config = {"version": 1, "workspace_id": layout.workspace_id, **{key: str(getattr(layout, key)) for key in ("notes", "state", "archive", "cache", "exports")}}
    config_path = layout.exports / "device-storage.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"version": 1, "status": "prepared_not_active", "workspace_id": layout.workspace_id, "legacy_alias": legacy_identity(source),
              "source": str(source), "device_config": str(config_path), "copied": copied, "reference_audit": report,
              "main_database": {"source": str(database_path), "destination": str(main), "sha256": digest(main)},
              "compatibility": "旧 .torch-os 逻辑路径由解析器映射到 state/vault-state；原 Obsidian 配置保留在 archive/legacy-vault，不自动启用旧插件。",
              "cutover_requirements": ["独立多根备份和新目录恢复验证", "桌面页面及业务回归", "安卓与百度独立测试库验收", "停旧后端后再启新后端"]}
    (layout.exports / "MIGRATION.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
