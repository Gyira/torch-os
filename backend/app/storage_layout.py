"""Explicit storage boundaries. Construction/loading never migrates or creates data."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import yaml

MARKER = Path("_Torch/工作区.md")


def legacy_identity(notes: Path | str) -> str:
    value = Path(notes).resolve().as_posix()
    return hashlib.sha256((value.lower() if os.name == "nt" else value).encode()).hexdigest()[:16]


def checked_path(value: Path | str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("存储根必须是本机绝对路径")
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("存储根不能经过链接或目录联接")
    return path.resolve()


def workspace_identity(notes: Path | str) -> str | None:
    path = Path(notes) / MARKER
    checked_path(path)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 8192:
        raise ValueError("工作区身份文件无效")
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("工作区身份文件缺少元数据")
    try:
        data = yaml.safe_load(text.split("---", 2)[1])
        if data["storage_version"] != 1:
            raise ValueError("不支持的存储协议版本")
        return str(UUID(data["workspace_id"]))
    except (KeyError, TypeError, yaml.YAMLError, IndexError) as error:
        raise ValueError("工作区身份文件损坏，不能隐式新建身份") from error


@dataclass(frozen=True)
class StorageLayout:
    notes: Path
    state: Path
    archive: Path
    cache: Path
    exports: Path
    workspace_id: str | None
    mode: str = "legacy"

    @classmethod
    def legacy(cls, notes, database_path):
        root = checked_path(notes)
        state = checked_path(Path(database_path).parent)
        return cls(root, state, root, state / "cache", state / "context-packs",
                   workspace_identity(root), "legacy")

    @classmethod
    def split(cls, *, notes, state, archive, cache, exports, workspace_id):
        roots = [checked_path(p) for p in (notes, state, archive, cache, exports)]
        for i, left in enumerate(roots):
            for right in roots[i + 1:]:
                if left == right or left.is_relative_to(right) or right.is_relative_to(left):
                    raise ValueError("拆分模式的存储根不得重叠")
        identity = str(UUID(workspace_id))
        recorded = workspace_identity(roots[0])
        if recorded is not None and recorded != identity:
            raise ValueError("设备配置与笔记库身份不匹配")
        return cls(*roots, identity, "split")

    @property
    def scope(self):
        return self.workspace_id or legacy_identity(self.notes)

    @property
    def share_database(self):
        # Preserve the P1 on-disk name until an explicit split migration.
        if self.mode == "legacy":
            digest = hashlib.sha256(str(self.notes).encode()).hexdigest()[:16]
            return self.state / f"share-captures-{digest}.sqlite3"
        return self.state / "share-captures.sqlite3"

    @property
    def context_exports(self):
        if self.mode == "legacy":
            digest = hashlib.sha256(str(self.notes).encode()).hexdigest()[:16]
            return self.exports / digest
        return self.exports / "context-packs"

    @classmethod
    def load(cls, device_config):
        data = json.loads(checked_path(device_config).read_text(encoding="utf-8"))
        required = {"version", "workspace_id", "notes", "state", "archive", "cache", "exports"}
        if set(data) != required or data["version"] != 1:
            raise ValueError("设备存储配置格式无效")
        layout = cls.split(**{k: data[k] for k in required - {"version"}})
        if workspace_identity(layout.notes) != layout.workspace_id:
            raise ValueError("笔记库身份缺失；不能自动新建空库")
        return layout


def initialize_empty_workspace(layout: StorageLayout):
    """Explicit initialization only; never adopts an existing personal database."""
    if layout.mode != "split" or not layout.workspace_id:
        raise ValueError("新建工作区需要显式拆分配置")
    if layout.notes.exists() and any(layout.notes.iterdir()):
        raise ValueError("只能初始化空笔记目录；旧库需要独立迁移流程")
    checked_path(layout.notes)
    marker = layout.notes / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    content = "---\nstorage_version: 1\nworkspace_id: " + layout.workspace_id + "\n---\n\n# Torch 工作区\n"
    # Exclusive creation prevents a concurrent initialization from replacing identity.
    with marker.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def new_layout(base: Path | str) -> StorageLayout:
    base = checked_path(base)
    identity = str(uuid4())
    return StorageLayout.split(**{key: base / key for key in ("notes", "state", "archive", "cache", "exports")},
                               workspace_id=identity)


def migration_plan(source, layout: StorageLayout):
    """Metadata-only dry-run; every file is preserved or requires manual classification."""
    source = checked_path(source)
    if not source.is_dir() or layout.mode != "split":
        raise ValueError("需要有效源库和显式拆分布局")
    for target in (layout.notes, layout.state, layout.archive, layout.cache, layout.exports):
        if target.is_relative_to(source) or source.is_relative_to(target):
            raise ValueError("迁移目标不能与源库重叠")
        if target.exists() and any(target.iterdir()):
            raise ValueError("目标目录非空，拒绝生成可误用的迁移计划")
    files = []
    for directory, dirs, names in os.walk(source, followlinks=False):
        for name in list(dirs):
            path = Path(directory) / name
            if path.is_symlink() or path.is_junction():
                dirs.remove(name)
                files.append({"source": path.relative_to(source).as_posix(), "action": "blocked_link"})
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(source).as_posix()
            if path.is_symlink():
                files.append({"source": relative, "action": "blocked_link"})
                continue
            suffix = path.suffix.lower()
            if any(part in {".torch-os", ".obsidian"} for part in path.relative_to(source).parts):
                destination, action = "state/legacy-vault/" + relative, "review_runtime_or_device_config"
            elif suffix in {".db", ".sqlite", ".sqlite3", ".json", ".key", ".enc"} or name.endswith(("-wal", "-shm")):
                destination, action = "state/legacy-vault/" + relative, "review_business_state"
            elif suffix == ".md" and relative.startswith(("00_人生路/", "01_自我档案/", "02_生活运营/", "03_知识库/")):
                destination, action = "notes/" + relative, "candidate_note_requires_link_audit"
            else:
                destination, action = "archive/legacy-vault/" + relative, "review_attachment_or_unknown"
            files.append({"source": relative, "destination": destination, "bytes": path.stat().st_size, "action": action})
    return {"version": 1, "dry_run": True, "executable": False, "workspace_id": layout.workspace_id,
            "files": files, "note": "仅候选分类；引用、唯一业务状态、凭据、空间、备份和恢复尚需验证。无文件被复制或移动。"}
