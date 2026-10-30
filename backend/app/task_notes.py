"""一任务一笔记的存储层（2026-10-30 任务状态文件夹版）。

任务数据层根目录是 `00_人生路/07_任务与待办`，直接包含：

- `收集箱/`：还没有放进清单的未完成任务；
- `已完成/`：所有 done 任务（平铺，逻辑清单记在 frontmatter `清单`）；
- `回收站/`：软删除的任务文件与清单文件夹；
- `<清单>/`：每个自定义清单一个文件夹；
- `_系统/任务（tasks）.md`：清单配置与旧复选框任务。

规则：

- 未完成任务：父文件夹就是逻辑清单；`收集箱` = 收集箱。
- 已完成任务：统一放在 `已完成/`，用 frontmatter `清单` 记住原清单；取消完成时移回。
- 标签 / 今天 / 日历 / 四象限都只是前端筛选，没有对应文件夹。
- 删除任务或清单走 `task_trash_store` 的批次 manifest，可恢复。
- 旧 `08_任务与待办/任务`、`07_任务与待办/任务`、`00-System/任务` 仍可读，
  启动迁移会把它们展开成新布局。
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from . import task_trash_store, vault_layout
from .markdown_store import guarded_write, load_note

INBOX = "收集箱"
DONE_DIR_NAME = "已完成"
TRASH_DIR_NAME = "回收站"
SYSTEM_DIR_NAME = "_系统"
LEGACY_INNER_DIR_NAME = "任务"
LEGACY_ARCHIVE_DIR_NAME = "_旧版"

TASK_ROOT_REL = Path(vault_layout.TASK_ROOT)
TASK_ROOT_POSIX = vault_layout.TASK_ROOT
# 兼容旧名字：现在表示整个任务根，而不是根下的“任务/”子目录。
TASK_DIR_REL = TASK_ROOT_REL
TASK_DIR_POSIX = TASK_ROOT_POSIX

LEGACY_TASK_ROOT_REL = Path("00_人生路", "08_任务与待办")
LEGACY_TASK_DIR_REL = LEGACY_TASK_ROOT_REL / LEGACY_INNER_DIR_NAME
LEGACY_TASK_DIR_POSIX = LEGACY_TASK_DIR_REL.as_posix()
LEGACY_TASK_ROOT_POSIX = LEGACY_TASK_ROOT_REL.as_posix()
SYSTEM_TASK_DIR_REL = Path("00-System", "任务")
SYSTEM_TASK_DIR_POSIX = SYSTEM_TASK_DIR_REL.as_posix()

TRASH_DIR_REL = TASK_ROOT_REL / TRASH_DIR_NAME
LEGACY_TRASH_DIR_REL = task_trash_store.LEGACY_TRASH_DIR

RESERVED_FOLDER_NAMES = frozenset({
    INBOX, DONE_DIR_NAME, TRASH_DIR_NAME, SYSTEM_DIR_NAME,
    LEGACY_INNER_DIR_NAME, LEGACY_ARCHIVE_DIR_NAME,
})

MAX_STEM_CHARS = 96
MAX_STEM_BYTES = 180
MAX_TAGS = 12
MAX_TAG_NAME = 24

PRIORITIES = ("none", "low", "medium", "high")
PRIORITY_MARKS = {"high": "⏫", "medium": "🔼", "low": "🔽"}
MARK_PRIORITIES = {mark: name for name, mark in PRIORITY_MARKS.items()}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(?:-([01]?\d|2[0-3]):([0-5]\d))?$")
FILENAME_FORBIDDEN = re.compile(r'[<>:"/\\|?*#^\[\]\x00-\x1f]')
QUOTE_AS_STRING = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}(?:-\d{1,2}:\d{2})?)$")
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                  *(f"COM{index}" for index in range(1, 10)),
                  *(f"LPT{index}" for index in range(1, 10))}
KNOWN_KEYS = ("type", "标题", "日期", "时间", "优先级", "标签", "完成", "完成于",
              "置顶", "创建", "排序", "dida_id", "目标", "导入键", "清单")


def order_value(value: Any) -> float | int | None:
    """把 frontmatter 的「排序」解析成有限数字；无法解析时返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number) if number.is_integer() else number


class TaskNoteFormatError(ValueError):
    """任务笔记的 frontmatter 无法安全解析；读时跳过、写时拒绝。"""


# ===== 布局定位 =====


def _vault_root(vault_dir: Path | str) -> Path:
    return Path(vault_dir).resolve()


def task_root(vault_dir: Path | str) -> Path:
    """现行任务根优先；旧 08 / 00-System 布局继续只读/迁移兜底。"""
    root = _vault_root(vault_dir)
    canonical = root / TASK_ROOT_REL
    if canonical.is_dir():
        return canonical
    legacy = root / LEGACY_TASK_DIR_REL
    if legacy.is_dir():
        return legacy
    system = root / SYSTEM_TASK_DIR_REL
    if system.is_dir():
        return system
    return canonical


def task_root_relative(vault_dir: Path | str) -> str:
    root = _vault_root(vault_dir)
    try:
        return task_root(vault_dir).relative_to(root).as_posix()
    except ValueError:
        return TASK_ROOT_POSIX


def task_system_dir(vault_dir: Path | str) -> Path:
    root = task_root(vault_dir)
    if root.name == LEGACY_INNER_DIR_NAME:
        return root.parent / SYSTEM_DIR_NAME
    return root / SYSTEM_DIR_NAME


def is_canonical_layout(vault_dir: Path | str) -> bool:
    return task_root(vault_dir) == (_vault_root(vault_dir) / TASK_ROOT_REL)


def is_task_relpath(relpath: str) -> bool:
    text = str(relpath or "").replace("\\", "/")
    if text.startswith(vault_layout.TASK_TRASH_DIR + "/"):
        return False
    if text.startswith(vault_layout.TASK_INBOX_DIR + "/"):
        return True
    return any(text.startswith(prefix + "/") for prefix in (
        TASK_ROOT_POSIX, LEGACY_TASK_DIR_POSIX, SYSTEM_TASK_DIR_POSIX,
    ))


def task_relative(path: Path, vault_dir: Path | str) -> str:
    return path.resolve().relative_to(_vault_root(vault_dir)).as_posix()


def task_key(relpath: str) -> str:
    return hashlib.sha256(relpath.encode("utf-8")).hexdigest()


def normalize_title(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _clean_folder_name(folder: Any) -> str:
    name = normalize_title(folder)
    if not name or name in {".", ".."} or name.startswith("."):
        raise HTTPException(422, "清单名不能为空、不能以点开头")
    if name.casefold() in {item.casefold() for item in RESERVED_FOLDER_NAMES}:
        raise HTTPException(
            422, "“收集箱/已完成/回收站/_系统/任务/_旧版”是保留文件夹名，不能作为清单名")
    if FILENAME_FORBIDDEN.search(name):
        raise HTTPException(422, "清单名不能包含 \\ / : * ? \" < > | # ^ [ ] 等文件夹/链接特殊字符")
    if name.rstrip(" .") != name:
        raise HTTPException(422, "清单名不能以空格或点结尾")
    if name.split(".", 1)[0].upper() in RESERVED_NAMES:
        raise HTTPException(422, "这个清单名是操作系统保留名，请换一个")
    return name


def clean_list_name(value: Any) -> str:
    """校验清单名能直接当文件夹名；不能时抛 422。"""
    return _clean_folder_name(value)


def list_folder(vault_dir: Path | str, list_name: Any, *, create: bool = False) -> Path:
    """逻辑清单 → 物理文件夹；收集箱 / 空名 = 收集箱文件夹。"""
    root = task_root(vault_dir)
    name = normalize_title(list_name)
    target = root / INBOX if not name or name.casefold() == INBOX.casefold() else root / _clean_folder_name(name)
    if create:
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise HTTPException(409, f"建不了任务文件夹：{error}") from error
    return target


def task_folder(vault_dir: Path | str, folder: Any, *, create: bool = False) -> Path:
    """旧名兼容：返回清单的物理文件夹（不是任务状态落点）。"""
    return list_folder(vault_dir, folder, create=create)


def done_folder(vault_dir: Path | str, *, create: bool = False) -> Path:
    target = task_root(vault_dir) / DONE_DIR_NAME
    if create:
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise HTTPException(409, f"建不了已完成文件夹：{error}") from error
    return target


def trash_folder(vault_dir: Path | str, *, create: bool = False) -> Path:
    target = _vault_root(vault_dir) / vault_layout.TASK_TRASH_DIR
    if create:
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise HTTPException(409, f"建不了任务回收站：{error}") from error
    return target


def destination_folder(vault_dir: Path | str, list_name: Any,
                       done: bool, *, create: bool = False) -> Path:
    """任务实际落点：已完成优先；否则清单文件夹 / 收集箱。"""
    if done:
        return done_folder(vault_dir, create=create)
    return list_folder(vault_dir, list_name, create=create)


# ===== 文件名 =====


def safe_task_stem(title: Any) -> str:
    """把标题变成安全、稳定的文件名 stem（不含 `.md`）。"""
    text = normalize_title(title)
    text = FILENAME_FORBIDDEN.sub("-", text)
    text = re.sub(r"-{2,}", "-", text).strip(" .-")
    if text.lower().endswith(".md"):
        text = text[:-3].rstrip(" .-")
    if not text:
        text = "未命名任务"
    kept: list[str] = []
    used = 0
    for character in text:
        if len(kept) >= MAX_STEM_CHARS:
            break
        size = len(character.encode("utf-8"))
        if used + size > MAX_STEM_BYTES:
            break
        kept.append(character)
        used += size
    stem = "".join(kept).strip(" .-") or "未命名任务"
    if stem.split(".", 1)[0].upper() in RESERVED_NAMES:
        stem = f"任务-{stem}"
    return stem


def _truncate_for_suffix(base: str, suffix: str) -> str:
    budget = MAX_STEM_CHARS - len(suffix)
    if budget <= 0:
        return suffix.strip()
    kept: list[str] = []
    used = 0
    byte_budget = MAX_STEM_BYTES - len(suffix.encode("utf-8"))
    for character in base:
        if len(kept) >= budget or used + len(character.encode("utf-8")) > byte_budget:
            break
        kept.append(character)
        used += len(character.encode("utf-8"))
    return ("".join(kept).strip(" .-") or "任务") + suffix


def _folder_entries(directory: Path) -> list[Path]:
    try:
        return list(directory.iterdir())
    except OSError:
        return []


def unique_stem(directory: Path, base: str, *, reserved: set[str] | None = None,
                ignore: Path | None = None) -> tuple[str, bool]:
    """在目录里找一个不冲突的 stem；返回 (stem, 是否加了编号)。"""
    used: set[str] = {name.casefold() for name in (reserved or set())}
    ignore_name = ignore.name.casefold() if ignore is not None else ""
    for entry in _folder_entries(directory):
        name = entry.name.casefold()
        if ignore is not None and name == ignore_name and entry == ignore:
            continue
        used.add(name)
    candidate = base
    if f"{candidate}.md".casefold() not in used:
        return candidate, False
    index = 2
    while True:
        candidate = _truncate_for_suffix(base, f" {index}")
        if f"{candidate}.md".casefold() not in used:
            return candidate, True
        index += 1


# ===== frontmatter =====


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _coerce_text(value)
    if not DATE_RE.match(text):
        return ""
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return ""
    return text


def _time_text(value: Any) -> str:
    text = re.sub(r"\s+", "", _coerce_text(value))
    match = TIME_RE.match(text)
    if match is None:
        return ""
    start = f"{int(match.group(1)):02d}:{match.group(2)}"
    if match.group(3) is None:
        return start
    end = f"{int(match.group(3)):02d}:{match.group(4)}"
    return f"{start}-{end}" if end > start else start


def _priority_text(value: Any) -> str:
    text = _coerce_text(value).lower()
    if text in PRIORITIES:
        return text
    return MARK_PRIORITIES.get(_coerce_text(value), "none")


def _bool_text(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on", "是", "完成", "done"}


def _tags_value(value: Any) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    tags: list[str] = []
    for item in raw:
        name = re.sub(r"\s+", "", str(item or "")).lstrip("#").strip()
        if not name or name in tags:
            continue
        tags.append(name)
    return tags


def _valid_list_name(value: Any) -> str:
    name = normalize_title(value)
    if not name:
        return ""
    if name.casefold() in {item.casefold() for item in RESERVED_FOLDER_NAMES}:
        return ""
    try:
        return _clean_folder_name(name)
    except HTTPException:
        return ""


def _metadata(loaded: dict[str, Any]) -> dict[str, Any]:
    if not loaded["exists"]:
        return {}
    if loaded["body_start"] == 0:
        if loaded["lines"] and loaded["lines"][0].strip() == "---":
            raise TaskNoteFormatError("frontmatter 缺少结束分隔行")
        return {}
    block = "\n".join(loaded["lines"][1:loaded["body_start"] - 1])
    try:
        data = yaml.safe_load(block) or {}
    except yaml.YAMLError as error:
        raise TaskNoteFormatError("frontmatter 不是合法 YAML") from error
    if not isinstance(data, dict):
        raise TaskNoteFormatError("frontmatter 必须是键值对象")
    return data


def is_task_metadata(metadata: dict[str, Any]) -> bool:
    return str(metadata.get("type") or "").strip().lower() == "task"


def _note_from_loaded(loaded: dict[str, Any], path: Path, metadata: dict[str, Any], *,
                      relpath: str, storage: str, list_name: str) -> dict[str, Any]:
    title = normalize_title(metadata.get("标题")) or path.stem
    list_name = normalize_title(list_name) or INBOX
    done = _bool_text(metadata.get("完成"))
    done_on = _date_text(metadata.get("完成于")) if done else ""
    return {
        "key": task_key(relpath),
        "path": path,
        "relpath": relpath,
        "folder": "" if list_name.casefold() == INBOX.casefold() else list_name,
        "list_name": INBOX if list_name.casefold() == INBOX.casefold() else list_name,
        "storage": storage,
        "title": title,
        "done": done,
        "done_on": done_on,
        "date": _date_text(metadata.get("日期")),
        "time": _time_text(metadata.get("时间")),
        "priority": _priority_text(metadata.get("优先级")),
        "tags": _tags_value(metadata.get("标签")),
        "pinned": _bool_text(metadata.get("置顶")),
        "created": _date_text(metadata.get("创建")),
        "sort_order": order_value(metadata.get("排序")),
        "dida_id": _coerce_text(metadata.get("dida_id")),
        "goal": _coerce_text(metadata.get("目标")),
        "import_key": _coerce_text(metadata.get("导入键")),
        "metadata": metadata,
        "exists": True,
        "raw": loaded["raw"],
        "revision": loaded["revision"],
        "newline": loaded["newline"],
        "lines": loaded["lines"],
        "body_start": loaded["body_start"],
        "legacy": False,
    }


def _storage_for(vault_dir: Path | str, path: Path) -> tuple[str, str]:
    """返回 (storage, 父文件夹名)。"""
    root = task_root(vault_dir)
    try:
        relative = path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return "outside", ""
    parts = relative.parts
    legacy_root = root.name == LEGACY_INNER_DIR_NAME or root.parent.name == "00-System"
    if len(parts) <= 1:
        return ("legacy-inbox" if legacy_root else "loose"), ""
    head = parts[0]
    if head == INBOX:
        return "inbox", INBOX
    if head == DONE_DIR_NAME:
        return "done", DONE_DIR_NAME
    if head == TRASH_DIR_NAME:
        return "trash", TRASH_DIR_NAME
    if head == LEGACY_INNER_DIR_NAME:
        if len(parts) == 2:
            return "legacy-inbox", ""
        return "legacy-list", parts[1]
    if head in {SYSTEM_DIR_NAME, LEGACY_ARCHIVE_DIR_NAME} or head.startswith("."):
        return "skip", head
    return ("legacy-list" if legacy_root else "list"), head


def read_task_note(vault_dir: Path | str, path: Path) -> dict[str, Any] | None:
    """读一篇任务笔记；不存在/不是任务返回 None，坏 YAML 抛 TaskNoteFormatError。"""
    loaded = load_note(path, too_large="任务笔记过大，已停止编辑",
                       bad_encoding="任务笔记不是 UTF-8 编码，暂不能编辑")
    if not loaded["exists"]:
        return None
    metadata = _metadata(loaded)
    if not is_task_metadata(metadata):
        return None
    storage, physical_parent = _storage_for(vault_dir, path)
    if storage == "skip":
        return None
    if storage in {"done", "trash", "loose", "legacy-inbox"}:
        list_name = _valid_list_name(metadata.get("清单")) or (
            physical_parent if storage == "legacy-list" else "") or INBOX
    else:
        list_name = physical_parent or INBOX
    relpath = task_relative(path, vault_dir)
    return _note_from_loaded(loaded, path, metadata, relpath=relpath,
                             storage=storage, list_name=list_name)


def inspect_task_file(path: Path, *, list_hint: str = "") -> dict[str, Any] | None:
    """读回收站/旧批次里的任务文件（不要求位于任务根内）。"""
    loaded = load_note(path, too_large="任务笔记过大，已停止编辑",
                       bad_encoding="任务笔记不是 UTF-8 编码，暂不能编辑")
    if not loaded["exists"]:
        return None
    metadata = _metadata(loaded)
    if not is_task_metadata(metadata):
        return None
    list_name = _valid_list_name(metadata.get("清单")) or normalize_title(list_hint) or INBOX
    return _note_from_loaded(loaded, path, metadata, relpath=path.name,
                             storage="trash", list_name=list_name)


def _warning_path(path: Path, vault_dir: Path | str) -> str:
    try:
        return path.resolve().relative_to(task_root(vault_dir)).as_posix()
    except (OSError, ValueError):
        return path.name


def _scan_file(vault_dir: Path | str, path: Path,
               warnings: list[dict[str, str]]) -> dict[str, Any] | None:
    try:
        return read_task_note(vault_dir, path)
    except TaskNoteFormatError as error:
        warnings.append({"path": _warning_path(path, vault_dir), "reason": str(error)})
    except HTTPException as error:
        warnings.append({"path": _warning_path(path, vault_dir), "reason": str(error.detail)})
    except OSError:
        warnings.append({"path": _warning_path(path, vault_dir), "reason": "文件无法读取"})
    return None


def _is_link(entry: Path) -> bool:
    return entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction())


def _is_sync_sidecar(entry: Path) -> bool:
    name = entry.name.casefold()
    return (not _is_link(entry) and entry.is_file()
            and ".baiduyun." in name and name.endswith(".cfg"))


def _scan_one_level(vault_dir: Path | str, directory: Path, warnings: list[dict[str, str]],
                    notes: list[dict[str, Any]]) -> None:
    for entry in _folder_entries(directory):
        if entry.name.startswith(".") or _is_link(entry) or _is_sync_sidecar(entry):
            continue
        if entry.is_dir():
            warnings.append({"path": _warning_path(entry, vault_dir) + "/",
                             "reason": "嵌套目录不扫描（任务只支持根目录与一层清单文件夹）"})
            continue
        if entry.suffix.lower() != ".md":
            continue
        note = _scan_file(vault_dir, entry, warnings)
        if note is not None:
            notes.append(note)


def scan_task_notes(vault_dir: Path | str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """扫描任务根、收集箱、已完成、各清单文件夹；回收站不进入看板。"""
    root = task_root(vault_dir)
    if not root.is_dir():
        return [], []
    notes: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    legacy_root = root.name == LEGACY_INNER_DIR_NAME or root.parent.name == "00-System"

    if legacy_root:
        for entry in _folder_entries(root):
            if entry.name.startswith(".") or _is_link(entry):
                continue
            if entry.is_dir():
                if entry.name in {TRASH_DIR_NAME, SYSTEM_DIR_NAME, LEGACY_ARCHIVE_DIR_NAME}:
                    continue
                _scan_one_level(vault_dir, entry, warnings, notes)
                continue
            if entry.suffix.lower() != ".md" or _is_sync_sidecar(entry):
                continue
            note = _scan_file(vault_dir, entry, warnings)
            if note is not None:
                notes.append(note)
    else:
        for entry in _folder_entries(root):
            if entry.name.startswith(".") or _is_link(entry):
                continue
            if entry.is_dir():
                if entry.name in {TRASH_DIR_NAME, SYSTEM_DIR_NAME, LEGACY_ARCHIVE_DIR_NAME}:
                    continue
                if entry.name == LEGACY_INNER_DIR_NAME:
                    warnings.append({
                        "path": LEGACY_INNER_DIR_NAME + "/",
                        "reason": "旧任务目录尚未迁移；应用启动迁移会展开到收集箱/清单/已完成",
                    })
                _scan_one_level(vault_dir, entry, warnings, notes)
                continue
            if entry.suffix.lower() != ".md" or _is_sync_sidecar(entry):
                continue
            note = _scan_file(vault_dir, entry, warnings)
            if note is not None:
                warnings.append({
                    "path": entry.name,
                    "reason": "任务散落在任务根目录；应用启动迁移会移进收集箱",
                })
                notes.append(note)

    seen: set[Path] = set()
    unique: list[dict[str, Any]] = []
    for note in notes:
        resolved = note["path"].resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(note)
    unique.sort(key=lambda item: item["relpath"])
    return unique, warnings


def _as_body_lines(note: dict[str, Any]) -> list[str]:
    return list(note["lines"][note["body_start"]:])


def note_body(note: dict[str, Any]) -> str:
    lines = _as_body_lines(note)
    return "\n".join(lines)


def _build_metadata(existing: dict[str, Any], title: str, stem: str,
                    fields: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"type": "task"}
    if title and title != stem:
        metadata["标题"] = title
    if fields.get("date"):
        metadata["日期"] = str(fields["date"])
    if fields.get("time"):
        metadata["时间"] = str(fields["time"])
    priority = str(fields.get("priority") or "none")
    if priority != "none":
        metadata["优先级"] = priority
    tags = _tags_value(fields.get("tags"))
    if tags:
        metadata["标签"] = tags[:MAX_TAGS]
    if fields.get("done"):
        metadata["完成"] = True
        if fields.get("done_on"):
            metadata["完成于"] = str(fields["done_on"])
    if fields.get("pinned"):
        metadata["置顶"] = True
    if fields.get("created"):
        metadata["创建"] = str(fields["created"])
    order = order_value(fields.get("sort_order"))
    if order is None:
        order = order_value(existing.get("排序"))
    if order is not None:
        metadata["排序"] = order
    dida_id = str(fields.get("dida_id") or existing.get("dida_id") or "").strip()
    if dida_id:
        metadata["dida_id"] = dida_id
    goal = str(fields.get("goal") or existing.get("目标") or "").strip()
    if goal:
        metadata["目标"] = goal[:500]
    import_key = str(fields.get("import_key") or existing.get("导入键") or "").strip()
    if import_key:
        metadata["导入键"] = import_key[:64]
    list_name = normalize_title(fields.get("list_name") or existing.get("清单"))
    list_name = _valid_list_name(list_name) or INBOX
    metadata["清单"] = list_name
    for key, value in existing.items():
        if key not in KNOWN_KEYS:
            metadata[key] = value
    return metadata


class _TaskDumper(yaml.SafeDumper):
    pass


def _represent_string(dumper: yaml.SafeDumper, data: str):
    style = '"' if QUOTE_AS_STRING.match(data) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_TaskDumper.add_representer(str, _represent_string)


def _compose(metadata: dict[str, Any], body_lines: list[str]) -> list[str]:
    block = yaml.dump(metadata, Dumper=_TaskDumper, allow_unicode=True,
                      sort_keys=False, default_flow_style=False, width=1000)
    return ["---", *block.rstrip("\n").split("\n"), "---", *body_lines]


def _body_lines(body: str) -> list[str]:
    text = str(body or "")
    lines = text.split("\n") if text else [""]
    if text and not text.endswith("\n"):
        lines.append("")
    return lines


def _write_new_note(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="") as handle:
            handle.write("\n".join(lines))
    except FileExistsError:
        raise
    except OSError as error:
        raise HTTPException(409, f"写不了任务笔记：{error}") from error


def _fields_from(values: dict[str, Any]) -> dict[str, Any]:
    done = bool(values.get("done"))
    return {
        "date": _date_text(values.get("date")),
        "time": _time_text(values.get("time")),
        "priority": _priority_text(values.get("priority")),
        "tags": _tags_value(values.get("tags"))[:MAX_TAGS],
        "done": done,
        "done_on": _date_text(values.get("done_on")) if done else "",
        "pinned": _bool_text(values.get("pinned")),
        "created": _date_text(values.get("created")),
        "sort_order": values.get("sort_order"),
        "dida_id": _coerce_text(values.get("dida_id")),
        "goal": _coerce_text(values.get("goal")),
        "import_key": _coerce_text(values.get("import_key")),
        "list_name": normalize_title(values.get("list_name")),
    }


def note_values(note: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """任务的完整 values，供迁移/批量操作复用。"""
    values = {
        "date": note["date"], "time": note["time"], "priority": note["priority"],
        "tags": list(note["tags"]), "done": note["done"], "done_on": note["done_on"],
        "pinned": note["pinned"], "created": note["created"],
        "sort_order": note.get("sort_order"),
        "dida_id": note.get("dida_id", ""),
        "goal": note.get("goal", ""),
        "import_key": note.get("import_key", ""),
        "list_name": note["list_name"],
    }
    values.update(changes)
    return values


def create_task_note(vault_dir: Path | str, folder: Any, title: str,
                     values: dict[str, Any], *, body: str = "") -> dict[str, Any]:
    """创建一篇任务笔记；按 done 自动落到 已完成 / 收集箱 / 清单文件夹。"""
    cleaned = normalize_title(title)
    if not cleaned or len(cleaned) > 200 or any(ord(character) < 32 for character in cleaned):
        raise HTTPException(422, "任务标题需要 1–200 个字符，且不能包含换行")
    list_name = normalize_title(folder) or INBOX
    if list_name.casefold() != INBOX.casefold():
        list_name = _clean_folder_name(list_name)
    payload = dict(values)
    payload["list_name"] = list_name
    fields = _fields_from(payload)
    target_dir = destination_folder(vault_dir, list_name, fields["done"], create=True)
    base = safe_task_stem(cleaned)
    stem, _numbered = unique_stem(target_dir, base)
    path = target_dir / f"{stem}.md"
    metadata = _build_metadata({}, cleaned, stem, fields)
    body_lines = _body_lines(body)
    lines = _compose(metadata, body_lines)
    for _attempt in range(50):
        try:
            _write_new_note(path, lines)
            note = read_task_note(vault_dir, path)
            if note is None:
                raise HTTPException(409, "新任务笔记写入后无法识别，请刷新后重试")
            return note
        except FileExistsError:
            stem, _numbered = unique_stem(target_dir, base)
            path = target_dir / f"{stem}.md"
            metadata = _build_metadata({}, cleaned, stem, fields)
            lines = _compose(metadata, body_lines)
    raise HTTPException(409, "同名任务笔记太多，无法写出新文件")


def _move_note_file(source: Path, target: Path) -> None:
    if source.parent.resolve() == target.parent.resolve() and source.name == target.name:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".torch-task-move-{secrets.token_hex(6)}.md"
    try:
        os.replace(source, temporary)
    except OSError as error:
        raise HTTPException(409, f"移动任务笔记失败：{error}") from error
    try:
        same_path_ignoring_case = os.name == "nt" and os.path.normcase(str(source)) == os.path.normcase(str(target))
        if target.exists() and not same_path_ignoring_case:
            raise HTTPException(409, "目标位置已经有同名文件，请刷新后重试")
        os.replace(temporary, target)
    except Exception:
        try:
            if temporary.exists() and not source.exists():
                os.replace(temporary, source)
        except OSError:
            pass
        raise


def rewrite_task_note(vault_dir: Path | str, note: dict[str, Any], *,
                      folder: Any | None, title: str, values: dict[str, Any],
                      body: str | None = None) -> dict[str, Any]:
    """更新/改名/换清单/勾选完成；done 状态决定最终物理文件夹。"""
    cleaned = normalize_title(title)
    if not cleaned or len(cleaned) > 200 or any(ord(character) < 32 for character in cleaned):
        raise HTTPException(422, "任务标题需要 1–200 个字符，且不能包含换行")
    list_name = normalize_title(folder) if folder is not None else note["list_name"]
    list_name = list_name or INBOX
    if list_name.casefold() != INBOX.casefold():
        list_name = _clean_folder_name(list_name)
    payload = dict(values)
    payload["list_name"] = list_name
    fields = _fields_from(payload)
    target_dir = destination_folder(vault_dir, list_name, fields["done"], create=True)
    title_changed = cleaned != normalize_title(note["title"])
    moved_dir = target_dir.resolve() != note["path"].parent.resolve()
    if title_changed:
        base = safe_task_stem(cleaned)
    else:
        base = note["path"].stem
    if title_changed or moved_dir:
        ignore = note["path"] if target_dir.resolve() == note["path"].parent.resolve() else None
        stem, _numbered = unique_stem(target_dir, base, ignore=ignore)
    else:
        stem = note["path"].stem
    target = target_dir / f"{stem}.md"
    metadata = _build_metadata(note["metadata"], cleaned, stem, fields)
    body_lines = _as_body_lines(note) if body is None else _body_lines(body)
    lines = _compose(metadata, body_lines)
    same_file = (target.parent.resolve() == note["path"].parent.resolve()
                 and target.name == note["path"].name)
    guarded_write(note, lines)
    if not same_file:
        _move_note_file(note["path"], target)
    fresh = read_task_note(vault_dir, target)
    if fresh is None:
        raise HTTPException(409, "任务笔记更新后无法识别，请刷新后重试")
    return fresh


# ===== 回收站写入 =====


def new_trash_batch(vault_dir: Path | str) -> Path:
    try:
        return task_trash_store.new_batch(vault_dir)
    except OSError as error:
        raise HTTPException(409, str(error)) from error


def _task_root_relative_path(vault_dir: Path | str, path: Path, note: dict[str, Any]) -> str:
    """任务文件 → 相对任务根的目标路径（旧“任务/”内层会展开）。"""
    try:
        relative = path.resolve().relative_to(task_root(vault_dir).resolve())
        parts = relative.parts
    except (OSError, ValueError):
        parts = tuple(str(note.get("relpath") or path.name).replace("\\", "/").split("/"))
    if len(parts) <= 1:
        return f"{INBOX}/{parts[-1] if parts else path.name}"
    if parts[0] == LEGACY_INNER_DIR_NAME:
        return "/".join([*parts[1:]])
    return "/".join(parts)


def copy_to_trash(vault_dir: Path | str, path: Path, batch: Path) -> str:
    """复制一份文件进回收批次（删除清单前备份配置/待办用）。"""
    name = path.name
    target = batch / SYSTEM_DIR_NAME / name
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(path, target)
    except OSError as error:
        raise HTTPException(409, f"备份到任务回收站失败：{error}") from error
    relative = task_trash_store.trash_relative(target, vault_dir)
    task_trash_store.append_entry(batch, {
        "id": task_trash_store.entry_id(relative),
        "kind": "config",
        "restorable": False,
        "original_relpath": task_relative(path, vault_dir),
        "trash_relpath": relative,
        "title": name,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    })
    return relative


def trash_note(vault_dir: Path | str, note: dict[str, Any],
               batch: Path | None = None) -> str:
    source = note["path"]
    try:
        if source.read_bytes() != note["raw"]:
            raise HTTPException(409, "任务笔记已在别处修改，请刷新后重试")
    except OSError as error:
        raise HTTPException(409, "任务笔记已经不在原处，请刷新后重试") from error
    batch = batch or new_trash_batch(vault_dir)
    relative = _task_root_relative_path(vault_dir, source, note)
    target = batch / "files" / Path(*relative.replace("\\", "/").split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _unique_trash_target(target)
    try:
        os.replace(source, target)
    except OSError as error:
        raise HTTPException(409, f"把任务移进回收站失败：{error}") from error
    trash_rel = task_trash_store.trash_relative(target, vault_dir)
    task_trash_store.append_entry(batch, {
        "id": task_trash_store.entry_id(trash_rel),
        "kind": "file",
        "restorable": True,
        "reason": "task_delete",
        "original_relpath": f"{vault_layout.TASK_ROOT}/{relative}",
        "trash_relpath": trash_rel,
        "list_name": note["list_name"],
        "title": note["title"],
        "done": bool(note["done"]),
        "done_on": note["done_on"],
        "dida_id": note.get("dida_id") or "",
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    })
    return task_trash_store.trash_relative(batch, vault_dir)


def _unique_trash_target(target: Path) -> Path:
    if not target.exists():
        return target
    base = target.stem
    suffix = target.suffix
    index = 2
    while True:
        candidate = target.with_name(f"{base} {index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def trash_list_folder(vault_dir: Path | str, folder: Any, *,
                      config_path: Path | None = None,
                      batch: Path | None = None,
                      list_definition: dict[str, Any] | None = None) -> dict[str, Any]:
    """删除清单：整个文件夹 + 该清单的已完成任务一起进同一个回收批次。"""
    name = normalize_title(folder)
    if not name or name.casefold() == INBOX.casefold():
        raise HTTPException(422, "收集箱不能删除")
    name = _clean_folder_name(name)
    directory = list_folder(vault_dir, name)
    batch = batch or new_trash_batch(vault_dir)
    if config_path is not None and config_path.is_file():
        copy_to_trash(vault_dir, config_path, batch)
    if not directory.exists():
        return {"batch": task_trash_store.trash_relative(batch, vault_dir),
                "count": 0, "done_count": 0, "folder": name,
                "trash": task_trash_store.trash_relative(batch, vault_dir)}
    if not directory.is_dir():
        raise HTTPException(409, f"任务目录里的“{name}”不是文件夹，不能按清单删除")

    open_notes: list[dict[str, Any]] = []
    for entry in _folder_entries(directory):
        if entry.name.startswith(".") or _is_link(entry) or _is_sync_sidecar(entry):
            continue
        if entry.is_dir() or entry.suffix.lower() != ".md":
            continue
        try:
            note = read_task_note(vault_dir, entry)
        except (TaskNoteFormatError, HTTPException, OSError):
            note = None
        if note is not None:
            open_notes.append(note)

    target = batch / "folders" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _unique_trash_target(target)
    try:
        os.replace(directory, target)
    except OSError as error:
        raise HTTPException(409, f"把清单文件夹移进回收站失败：{error}") from error
    folder_rel = task_trash_store.trash_relative(target, vault_dir)
    task_trash_store.append_entry(batch, {
        "id": task_trash_store.entry_id(folder_rel),
        "kind": "folder",
        "restorable": True,
        "reason": "list_delete",
        "original_relpath": f"{vault_layout.TASK_ROOT}/{name}",
        "trash_relpath": folder_rel,
        "list_name": name,
        "title": name,
        "count": len(open_notes),
        "dida_ids": [note.get("dida_id") for note in open_notes if note.get("dida_id")],
        "list_definition": dict(list_definition or {}),
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    })

    # 已完成目录里属于这个清单的任务也一起进回收站。
    done_count = 0
    done_dir = done_folder(vault_dir)
    for entry in _folder_entries(done_dir):
        if entry.name.startswith(".") or _is_link(entry) or entry.suffix.lower() != ".md":
            continue
        try:
            note = read_task_note(vault_dir, entry)
        except (TaskNoteFormatError, HTTPException, OSError):
            continue
        if note is None or note["list_name"].casefold() != name.casefold():
            continue
        target = batch / "files" / DONE_DIR_NAME / entry.name
        target = _unique_trash_target(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(entry, target)
        except OSError as error:
            raise HTTPException(409, f"把该清单的已完成任务移进回收站失败：{error}") from error
        trash_rel = task_trash_store.trash_relative(target, vault_dir)
        task_trash_store.append_entry(batch, {
            "id": task_trash_store.entry_id(trash_rel),
            "kind": "file",
            "restorable": True,
            "reason": "list_delete_done",
            "original_relpath": f"{vault_layout.TASK_ROOT}/{DONE_DIR_NAME}/{target.name}",
            "trash_relpath": trash_rel,
            "list_name": name,
            "title": note["title"],
            "done": True,
            "done_on": note["done_on"],
            "dida_id": note.get("dida_id") or "",
            "deleted_at": datetime.now(timezone.utc).isoformat(),
        })
        done_count += 1

    return {
        "batch": task_trash_store.trash_relative(batch, vault_dir),
        "count": len(open_notes) + done_count,
        "open_count": len(open_notes),
        "done_count": done_count,
        "folder": name,
        "trash": task_trash_store.trash_relative(batch, vault_dir),
    }


def rename_list_folder(vault_dir: Path | str, old_name: Any, new_name: Any) -> bool:
    old_dir = list_folder(vault_dir, old_name)
    new_dir = list_folder(vault_dir, new_name)
    if not old_dir.exists():
        return False
    if old_dir.name == new_dir.name:
        return False
    same_ignoring_case = (os.name == "nt"
                          and os.path.normcase(str(old_dir)) == os.path.normcase(str(new_dir)))
    if new_dir.exists() and not same_ignoring_case:
        raise HTTPException(409, f"任务目录里已经有“{new_dir.name}”这个文件夹了")
    temporary = old_dir.parent / f".torch-list-move-{secrets.token_hex(6)}"
    try:
        os.replace(old_dir, temporary)
        if new_dir.exists() and not same_ignoring_case:
            raise HTTPException(409, f"任务目录里已经有“{new_dir.name}”这个文件夹了")
        os.replace(temporary, new_dir)
    except Exception:
        try:
            if temporary.exists() and not old_dir.exists():
                os.replace(temporary, old_dir)
        except OSError:
            pass
        raise
    return True


def board_revision(config_revision: str, notes: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(b"task-board-v2\0")
    digest.update((config_revision or "missing").encode("utf-8"))
    for note in sorted(notes, key=lambda item: item["relpath"]):
        digest.update(b"\0" + note["relpath"].encode("utf-8") + b"\0" + note["revision"].encode("utf-8"))
    return digest.hexdigest()[:16]


__all__ = [
    "INBOX", "DONE_DIR_NAME", "TRASH_DIR_NAME", "SYSTEM_DIR_NAME",
    "LEGACY_INNER_DIR_NAME", "TASK_ROOT_REL", "TASK_ROOT_POSIX", "TASK_DIR_REL",
    "TASK_DIR_POSIX", "LEGACY_TASK_ROOT_REL", "LEGACY_TASK_DIR_REL",
    "LEGACY_TASK_DIR_POSIX", "LEGACY_TASK_ROOT_POSIX", "SYSTEM_TASK_DIR_REL",
    "SYSTEM_TASK_DIR_POSIX", "TRASH_DIR_REL", "LEGACY_TRASH_DIR_REL",
    "RESERVED_FOLDER_NAMES", "MAX_TAGS", "MAX_TAG_NAME", "PRIORITIES",
    "TaskNoteFormatError", "task_root", "task_root_relative", "task_system_dir",
    "is_canonical_layout", "is_task_relpath", "task_relative", "task_key",
    "normalize_title", "safe_task_stem", "unique_stem", "list_folder", "task_folder",
    "done_folder", "trash_folder", "destination_folder", "clean_list_name",
    "read_task_note", "inspect_task_file", "scan_task_notes", "create_task_note",
    "rewrite_task_note", "note_values", "order_value", "note_body",
    "new_trash_batch", "copy_to_trash", "trash_note", "trash_list_folder",
    "rename_list_folder", "board_revision",
]
