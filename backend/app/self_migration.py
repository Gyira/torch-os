"""自我档案 v1 → v2 迁移（2026-11-01）。

旧的 01_自我档案 章节（内核与三观 / 记忆与自传 / 想法与灵感 / 情感与关系 /
意义与目标 / 沉淀与创作 / 我的八字参考 / 忆灵观察）在 v2 中不再写入。
迁移是「预览 → 确认 → 备份 → 逐文件转换」：

- 先备份整棵 01_自我档案 到 .torch-os/backups/self-archive/99_重构备份-YYYYMMDD-HHMMSS/；
- 旧笔记转换成 01_档案 下的条目 Markdown；
- 旧八字笔记转换成 03_八字系统/命盘.md + 解读/；
- 旧忆灵观察与 .torch-os/忆灵 数据复制到 02_人生导师/历史/；
- 原始旧文件移到备份的 _迁移前原始文件/ 下，不删除备份；
- 写入 .torch-os/self-archive-layout.json，幂等。
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import yaml

from . import self_archive, vault_layout
from .vault import parse_text, safe_note_path
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SHANGHAI = ZoneInfo("Asia/Shanghai")

# 旧章节 key → (目标本子, 条目类型)
OLD_CHAPTER_MAP: dict[str, tuple[str, str]] = {
    "core_values": ("self_values", "narrative"),
    "memory": ("self_timeline", "narrative"),
    "idea": ("self_paradox", "hypothesis"),
    "relationship": ("self_relationship", "narrative"),
    "meaning": ("self_motivation", "narrative"),
    "distill_work": ("self_behavior", "behavior"),
    "distill_career": ("self_values", "narrative"),
}
OLD_DIRS = tuple(vault_layout.CHAPTER_BY_KEY[key][3]
                 for key in OLD_CHAPTER_MAP if key in vault_layout.CHAPTER_BY_KEY)
BAZI_OLD_DIR = "01_自我档案/07_我的八字参考"
YILING_OLD_DIR = "01_自我档案/08_忆灵观察"
IDENTITY_OLD_DIR = "01_自我档案/01_内核与三观"


def _now() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")


def _marker_path(vault: Path) -> Path:
    return vault / vault_layout.SELF_ARCHIVE_LAYOUT_MARKER


def _read_marker(vault: Path) -> dict[str, Any]:
    try:
        data = json.loads(_marker_path(vault).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_marker(vault: Path, payload: dict[str, Any]) -> None:
    path = _marker_path(vault)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    except OSError:
        pass


def _has_old_files(vault: Path) -> bool:
    for relative in OLD_DIRS:
        directory = vault / relative
        if directory.is_dir() and any(directory.rglob("*.md")):
            return True
    for relative in (BAZI_OLD_DIR, YILING_OLD_DIR):
        directory = vault / relative
        if directory.is_dir() and any(directory.rglob("*.md")):
            return True
    return False


def _old_files(vault: Path) -> list[str]:
    result: list[str] = []
    for relative in (*OLD_DIRS, BAZI_OLD_DIR, YILING_OLD_DIR):
        directory = vault / relative
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.md")):
            if any(part.startswith(".") for part in path.relative_to(directory).parts):
                continue
            if path.parent == vault / IDENTITY_OLD_DIR and path.name in (
                    "身份（identity）.md", "identity.md"):
                # 身份文件由 _migrate_identity 单独处理，不当作普通档案条目。
                continue
            result.append(path.relative_to(vault).as_posix())
    return result


def preview(vault_dir: Path | str) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    marker = _read_marker(vault)
    files = _old_files(vault)
    plan: list[dict[str, str]] = []
    for relative in files:
        if relative.startswith(BAZI_OLD_DIR + "/"):
            target = (vault_layout.SELF_BAZI_ROOT + "/命盘.md"
                      if relative.endswith("我的八字参考.md")
                      else vault_layout.SELF_BAZI_READINGS_DIR + "/" + Path(relative).name)
            plan.append({"source": relative, "target": target, "kind": "bazi"})
        elif relative.startswith(YILING_OLD_DIR + "/"):
            plan.append({
                "source": relative,
                "target": vault_layout.SELF_MENTOR_OLD_OBSERVATIONS + "/" + Path(relative).name,
                "kind": "yiling_history",
            })
        else:
            chapter_key = ""
            for key, item in vault_layout.CHAPTER_BY_KEY.items():
                if item[3] and relative.startswith(item[3] + "/"):
                    chapter_key = key
                    break
            mapping = OLD_CHAPTER_MAP.get(chapter_key)
            if mapping:
                target = f"{vault_layout.SELF_ARCHIVE_ROOT}/{self_archive.BOOK_BY_KEY[mapping[0]][2]}/{Path(relative).stem}.md"
                plan.append({"source": relative, "target": target, "kind": "entry"})
            else:
                plan.append({"source": relative, "target": "", "kind": "unmapped"})
    conflicts = [row for row in plan if row["target"] and (vault / row["target"]).exists()]
    return {
        "schema": vault_layout.SELF_ARCHIVE_LAYOUT_SCHEMA,
        "needed": bool(files) and (
            marker.get("schema") != vault_layout.SELF_ARCHIVE_LAYOUT_SCHEMA
            or marker.get("status") != "done"),
        "marker": marker,
        "files": len(files),
        "plan": plan,
        "conflicts": conflicts,
        "backup_root": vault_layout.SELF_BACKUP_PREFIX + _stamp(),
    }


def _backup(vault: Path) -> str:
    source = vault / vault_layout.SELF_ROOT
    stamp = _stamp()
    target = vault / (vault_layout.SELF_BACKUP_PREFIX + stamp)
    index = 2
    while target.exists():
        target = vault / (vault_layout.SELF_BACKUP_PREFIX + f"{stamp}-{index}")
        index += 1
    target.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        return target.relative_to(vault).as_posix()
    for child in source.iterdir():
        if child.name.startswith("99_重构备份-"):
            continue
        destination = target / child.name
        try:
            if child.is_dir():
                shutil.copytree(child, destination, dirs_exist_ok=True)
            elif child.is_file():
                shutil.copy2(child, destination)
        except OSError:
            continue
    return target.relative_to(vault).as_posix()


def _move_to_backup(vault: Path, source: Path, backup_rel: str) -> None:
    target = vault / backup_rel / "_迁移前原始文件" / source.relative_to(vault)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, target)
    except OSError:
        # 移动失败不阻断：新条目已写入，旧文件留在原处也不会被新 UI 读取。
        pass


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _clean_title(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return (text or fallback)[:120]


def _migrate_entry(vault: Path, relative: str, *, read_only: bool) -> dict[str, Any]:
    source = safe_note_path(vault, relative)
    raw = source.read_bytes()
    metadata, body = parse_text(raw.decode("utf-8-sig", errors="replace"))
    chapter_key = ""
    for key, item in vault_layout.CHAPTER_BY_KEY.items():
        if item[3] and relative.startswith(item[3] + "/"):
            chapter_key = key
            break
    mapping = OLD_CHAPTER_MAP.get(chapter_key)
    if not mapping:
        raise ValueError("没有迁移映射")
    book_key, entry_type = mapping
    title = _clean_title(metadata.get("title"), source.stem)
    text = body.strip() or title
    evidence = str(metadata.get("evidence") or "").strip()
    sources = [f"迁移:{relative}"]
    created = str(metadata.get("date") or metadata.get("created") or "").strip()
    if created:
        sources.append(f"原日期:{created[:10]}")
    return self_archive.create_entry(
        vault, book_key=book_key, entry_type=entry_type,
        confidence="medium", title=title, text=text,
        evidence=evidence, sources=sources, origin="migration",
        read_only=read_only,
    )


def _migrate_identity(vault: Path, *, read_only: bool) -> dict[str, Any]:
    old_dir = vault / IDENTITY_OLD_DIR
    if not old_dir.is_dir():
        return {"moved": 0, "entries": 0}
    moved = 0
    entries = 0
    for name in ("身份（identity）.md", "identity.md"):
        source = old_dir / name
        if not source.is_file():
            continue
        target = vault / vault_layout.SELF_IDENTITY_RELATIVE
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            if target.exists():
                target = _unique_path(target)
            os.replace(source, target)
            moved += 1
        raw = target.read_bytes()
        metadata, body = parse_text(raw.decode("utf-8-sig", errors="replace"))
        for field, title, book, entry_type in (
            ("worldview", "世界观", "self_values", "narrative"),
            ("lifeview", "人生观", "self_values", "narrative"),
            ("values", "价值观", "self_values", "narrative"),
            ("notes", "自述", "self_narrative", "narrative"),
        ):
            value = str(metadata.get(field) or "").strip()
            if field == "notes" and not value:
                value = body.strip()
            if not value:
                continue
            self_archive.create_entry(
                vault, book_key=book, entry_type=entry_type,
                confidence="medium", title=title, text=value,
                sources=["迁移:旧身份卡"], origin="migration",
                read_only=read_only,
            )
            entries += 1
        break
    return {"moved": moved, "entries": entries}


def _bazi_profile(vault: Path, source: Path) -> None:
    raw = source.read_bytes()
    metadata, body = parse_text(raw.decode("utf-8-sig", errors="replace"))
    pillars = str(metadata.get("八字") or "").strip()
    name = ""
    sex = ""
    match = re.search(r"\*\*([^*\n]{1,20})\*\*\s*·\s*(男|女)", body)
    if match:
        name, sex = match.group(1).strip(), "male" if match.group(2) == "男" else "female"
    profile = {
        "type": "bazi_profile",
        "name": name,
        "sex": sex,
        "birth_date": "",
        "birth_time": "",
        "birth_place": "",
        "timezone": "Asia/Shanghai",
        "pillars_manual": pillars,
        "created": _now(),
        "updated": _now(),
    }
    target = vault / vault_layout.SELF_BAZI_ROOT / "命盘.md"
    if target.exists():
        target = _unique_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = ("---\n" + yaml.safe_dump(profile, allow_unicode=True, sort_keys=False)
            + "---\n\n# 我的命盘\n\n> 从旧版「我的八字参考」迁移；出生日期/时间可在八字系统里补全，"
              "四柱已直接写入。\n")
    target.write_text(text, encoding="utf-8", newline="\n")


def _copy_tree(source: Path, target: Path) -> None:
    if not source.exists():
        return
    try:
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    except OSError:
        pass


def apply(vault_dir: Path | str, *, confirm: bool = False,
          read_only: bool = False) -> dict[str, Any]:
    if confirm is not True:
        raise ValueError("请明确确认迁移自我档案")
    try:
        ensure_vault_writable(read_only, "迁移自我档案")
    except VaultReadOnlyError as error:
        raise ValueError(str(error)) from error
    vault = Path(vault_dir).resolve()
    plan = preview(vault)
    if not plan["needed"]:
        return {**plan, "status": "already_done", "moved": 0, "backup": ""}
    backup = _backup(vault)
    vault_layout.ensure_self_layout(vault)
    moved = 0
    conflicts: list[str] = []
    entries = 0
    identity_result = _migrate_identity(vault, read_only=read_only)
    entries += identity_result["entries"]
    for row in plan["plan"]:
        source = vault / row["source"]
        if not source.is_file():
            continue
        try:
            if row["kind"] == "entry":
                item = _migrate_entry(vault, row["source"], read_only=read_only)
                if item:
                    entries += 1
                _move_to_backup(vault, source, backup)
                moved += 1
            elif row["kind"] == "yiling_history":
                target = _unique_path(vault / row["target"])
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
                moved += 1
            elif row["kind"] == "bazi":
                target_rel = row["target"]
                if source.name == "我的八字参考.md":
                    _bazi_profile(vault, source)
                    # 命盘.md 是生成物；原文本身保留为一篇可读解读。
                    target_rel = f"{vault_layout.SELF_BAZI_READINGS_DIR}/我的八字参考.md"
                target = _unique_path(vault / target_rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
                moved += 1
            else:
                conflicts.append(row["source"] + "：没有迁移映射，保留原处")
        except Exception as error:  # noqa: BLE001 - 迁移失败要记录而不是中断全部
            conflicts.append(f"{row['source']}：{error}")
    # .torch-os/忆灵 与身份候选：复制到历史，不移动原数据。
    history = vault / vault_layout.SELF_MENTOR_HISTORY_DIR
    _copy_tree(vault / ".torch-os/忆灵/画像.json",
               history / "旧忆灵画像.json")
    _copy_tree(vault / ".torch-os/忆灵/复盘卡", history / "旧忆灵复盘卡")
    _copy_tree(vault / ".torch-os/忆灵/画像历史", history / "旧忆灵画像历史")
    _copy_tree(vault / ".torch-os/身份待确认", history / "旧身份候选")
    payload = {
        "schema": vault_layout.SELF_ARCHIVE_LAYOUT_SCHEMA,
        "status": "done" if not conflicts else "partial",
        "finished": _now(),
        "moved": moved,
        "entries": entries,
        "conflicts": conflicts,
        "backup": backup,
    }
    _write_marker(vault, payload)
    return payload


__all__ = ["preview", "apply", "OLD_CHAPTER_MAP"]
