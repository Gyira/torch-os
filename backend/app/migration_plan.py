"""只读迁移预览（2026-10-26）：旧布局 / 人生路 v1 → 四库 v2 的逐文件计划。

不写任何文件。每个条目带源/目标/动作/理由/哈希，冲突单独列出；
apply 阶段必须携带这里生成的 plan_hash，避免“预览后文件又变了”却照旧执行。
v1 章节搬迁只认 `vault_layout.V1_CHAPTER_MOVES`：日记、任务、习惯等未移动目录保持 keep_v1。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import vault_layout
from .vault import parse_text

SKIP_TOP = {".obsidian", ".git", ".trash", ".claude", ".claudian", ".torch-os"}
MAX_FILE_BYTES = 64 * 1024 * 1024


@dataclass
class PlanItem:
    source: str
    target: str
    action: str          # move / keep_v1 / keep_system / keep_foreign / keep_legacy
    reason: str
    kind: str            # markdown / file
    sha256: str
    size: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MigrationPlan:
    vault: str
    layout_version: str
    items: list[PlanItem]
    conflicts: list[dict[str, str]]
    plan_hash: str

    def moves(self) -> list[PlanItem]:
        return [item for item in self.items if item.action == "move"]

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.action] = counts.get(item.action, 0) + 1
        counts["conflicts"] = len(self.conflicts)
        counts["total"] = len(self.items)
        counts["moves"] = len(self.moves())
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "vault": self.vault,
            "layout_version": self.layout_version,
            "plan_hash": self.plan_hash,
            "stats": self.stats(),
            "items": [asdict(item) for item in self.items],
            "conflicts": self.conflicts,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_metadata(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".md":
        return {}
    try:
        raw = path.read_bytes()
        if len(raw) > 1_048_576:
            return {}
        metadata, _body = parse_text(raw.decode("utf-8-sig"))
        return metadata if isinstance(metadata, dict) else {}
    except (OSError, ValueError, yaml.YAMLError, UnicodeDecodeError):
        return {}


def _is_foreign(relative: str, metadata: dict[str, Any]) -> bool:
    return vault_layout.is_foreign(relative, metadata)


def _plan_target(relative: str, metadata: dict[str, Any]) -> tuple[str, str] | None:
    """返回 (target, reason) 或 None；None 表示原地保留。"""
    text = relative.replace("\\", "/")
    lowered = text.casefold()
    if _is_foreign(text, metadata):
        return None
    if str(metadata.get("type") or "").casefold() == "plan":
        name = text.rsplit("/", 1)[-1]
        return (f"{vault_layout.chapter_dir('project_plan')}/{name}", "plan")
    if lowered.startswith("02-life/journal/"):
        return (f"00_人生路/01_日记/{text.split('/', 2)[2]}", "journal")
    if lowered.startswith("01-identity/"):
        rest = text.split("/", 1)[1]
        if rest.casefold() in {"identity.md"}:
            rest = "身份（identity）.md"
        return (f"01_自我档案/01_内核与三观/{rest}", "identity")
    if lowered.startswith("90-inbox/"):
        return (f"03_知识库/00_收件箱/{text.split('/', 1)[1]}", "inbox")
    if lowered.startswith("80-wolai/"):
        return (f"03_知识库/00_收件箱/_导入/80-Wolai/{text.split('/', 1)[1]}", "wolai-import")
    if lowered.startswith("提示词/"):
        return (f"03_知识库/00_收件箱/{text.split('/', 1)[1]}", "prompt")
    if lowered.startswith("03-learning/"):
        return (f"03_知识库/00_收件箱/_导入/03-Learning/{text.split('/', 1)[1]}", "learning")
    return None


def _walk_files(root: Path) -> list[str]:
    found: list[str] = []
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        subdirs[:] = sorted(
            name for name in subdirs
            if name not in SKIP_TOP and not name.startswith(".")
            and not (Path(directory) / name).is_symlink()
        )
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            path = Path(directory) / filename
            if path.is_symlink():
                continue
            found.append(path.relative_to(root).as_posix())
    return found


def plan_migration(vault_dir: Path | str) -> MigrationPlan:
    root = Path(vault_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Vault 不存在：{root}")
    items: list[PlanItem] = []
    conflicts: list[dict[str, str]] = []
    targets: dict[str, str] = {}
    for relative in _walk_files(root):
        path = root / relative
        try:
            size = path.stat().st_size
            digest = _sha256(path)
        except OSError as error:
            conflicts.append({"source": relative, "reason": f"无法读取：{error}"})
            continue
        metadata = _safe_metadata(path)
        kind = "markdown" if relative.casefold().endswith(".md") else "file"
        lowered = relative.casefold()
        # 人生路 v2：v1 章节目录里的文件按相对子路径搬到新章节；其余四库文件原地保留。
        planned_v1 = vault_layout.v1_chapter_move(relative)
        if planned_v1 is not None:
            target, reason = planned_v1
            reason = f"layout-v2:{reason}"
        elif vault_layout.pillar_of(relative):
            items.append(PlanItem(relative, relative, "keep_v1", "已在四库",
                                  kind, digest, size, metadata))
            continue
        # 计划笔记不是任务：即使放在 00-System/任务 里也归入人生路。
        elif str(metadata.get("type") or "").casefold() == "plan":
            name = relative.rsplit("/", 1)[-1]
            target = f"{vault_layout.chapter_dir('project_plan')}/{name}"
            items.append(PlanItem(relative, target, "move", "plan", kind,
                                  digest, size, metadata))
            targets[target] = relative
            continue
        elif lowered.startswith("00-system/"):
            items.append(PlanItem(relative, relative, "keep_system", "工作台系统区",
                                  kind, digest, size, metadata))
            continue
        elif _is_foreign(relative, metadata):
            items.append(PlanItem(relative, relative, "keep_foreign", "外部资产（Apex Dashboard）",
                                  kind, digest, size, metadata))
            continue
        elif lowered.startswith("20-journal/"):
            items.append(PlanItem(relative, relative, "keep_legacy", "旧日记导出快照（只读）",
                                  kind, digest, size, metadata))
            continue
        else:
            planned = _plan_target(relative, metadata)
            if planned is None:
                if "/" not in relative and kind == "markdown":
                    target, reason = f"03_知识库/00_收件箱/{relative}", "root-note"
                elif "/" not in relative:
                    target, reason = f"03_知识库/00_收件箱/{relative}", "root-file"
                else:
                    items.append(PlanItem(relative, relative, "keep_legacy",
                                          "未识别的旧目录，暂不移动",
                                          kind, digest, size, metadata))
                    continue
            else:
                target, reason = planned
        target = vault_layout.normalize_relpath(target)
        try:
            vault_layout.validate_target(target)
        except ValueError as error:
            conflicts.append({"source": relative, "reason": str(error)})
            continue
        if target in targets:
            conflicts.append({"source": relative, "target": target,
                              "reason": f"目标与 {targets[target]} 冲突"})
            continue
        target_path = root / target
        if target_path.exists():
            try:
                same = target_path.is_file() and _sha256(target_path) == digest
            except OSError:
                same = False
            if not same:
                conflicts.append({"source": relative, "target": target,
                                  "reason": "目标已存在且内容不同"})
                continue
        targets[target] = relative
        items.append(PlanItem(relative, target, "move", reason, kind, digest, size, metadata))
    material = json.dumps(
        [[item.source, item.target, item.sha256] for item in items if item.action == "move"],
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")
    plan_hash = hashlib.sha256(material).hexdigest()[:16]
    return MigrationPlan(str(root), vault_layout.LAYOUT_VERSION, items, conflicts, plan_hash)


def plan_markdown(plan: MigrationPlan) -> str:
    lines = [
        f"# 迁移预览 `{plan.plan_hash}`",
        "",
        f"- 库：`{plan.vault}`",
        f"- 布局：`{plan.layout_version}`",
        f"- 统计：{json.dumps(plan.stats(), ensure_ascii=False)}",
        "",
        "| 源 | 目标 | 动作 | 理由 |",
        "|---|---|---|---|",
    ]
    for item in plan.items:
        if item.action == "move":
            lines.append(f"| `{item.source}` | `{item.target}` | 移动 | {item.reason} |")
    keep = [item for item in plan.items if item.action != "move"]
    if keep:
        lines.extend(["", "## 原地保留", "", "| 路径 | 原因 |", "|---|---|"])
        for item in keep:
            lines.append(f"| `{item.source}` | {item.reason} |")
    if plan.conflicts:
        lines.extend(["", "## 冲突（必须先处理）", ""])
        for conflict in plan.conflicts:
            lines.append(f"- `{conflict.get('source')}` → `{conflict.get('target', '')}`：{conflict.get('reason')}")
    lines.append("")
    return "\n".join(lines)


__all__ = ["PlanItem", "MigrationPlan", "plan_migration", "plan_markdown"]
