"""一任务一笔记的存储层（2026-10-17）。

产品托管的任务现在是 `00-System/任务/` 下的一篇篇 Markdown：

- `00-System/任务/<标题>.md` = 收集箱任务；
- `00-System/任务/<清单>/<标题>.md` = 自定义清单任务。

规格（与 `tasks.py` 的分工）：

- 只有 frontmatter `type: task` 的 `.md` 才是任务；标题默认取文件名，清单取父目录；
- 文件名不能安全表达标题时（禁用字符/过长/重名编号），frontmatter 写完整 `标题`；
- 正文与未知 frontmatter 字段逐字保留；更新只重写 frontmatter；
- 清单目录/文件删除走 `.torch-os/task-trash`（软删除），扫描永不进入那里；
- 读写复用 `markdown_store` 的修订号与 409 冲突协议。

旧式复选框任务仍在 `00-System/任务（tasks）.md`，由 `tasks.py` 继续解析为只读任务；
本模块不迁移、不改写它们。
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

from .markdown_store import guarded_write, load_note

INBOX = "收集箱"
TASK_DIR_REL = Path("00-System", "任务")
TASK_DIR_POSIX = TASK_DIR_REL.as_posix()
TRASH_DIR_REL = Path(".torch-os", "task-trash")

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
KNOWN_KEYS = ("type", "标题", "日期", "时间", "优先级", "标签", "完成", "完成于", "置顶", "创建", "dida_id")


class TaskNoteFormatError(ValueError):
    """任务笔记的 frontmatter 无法安全解析；读时跳过、写时拒绝。"""


def task_root(vault_dir: Path | str) -> Path:
    return Path(vault_dir).resolve() / TASK_DIR_REL


def task_relative(path: Path, vault_dir: Path | str) -> str:
    return path.resolve().relative_to(Path(vault_dir).resolve()).as_posix()


def task_key(relpath: str) -> str:
    return hashlib.sha256(relpath.encode("utf-8")).hexdigest()


def normalize_title(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def safe_task_stem(title: Any) -> str:
    """把标题变成安全、稳定的文件名 stem（不含 `.md`）。"""
    text = normalize_title(title)
    text = FILENAME_FORBIDDEN.sub("-", text)
    text = re.sub(r"-{2,}", "-", text).strip(" .-")
    if text.lower().endswith(".md"):
        text = text[:-3].rstrip(" .-")
    if not text:
        text = "未命名任务"
    # 截断时按字符数与 UTF-8 字节数双上限，避免 Windows/路径长度问题。
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


def _clean_folder_name(folder: Any) -> str:
    name = normalize_title(folder)
    if not name or name in {".", ".."} or name.startswith("."):
        raise HTTPException(422, "清单名不能为空、不能以点开头")
    if FILENAME_FORBIDDEN.search(name):
        raise HTTPException(422, "清单名不能包含 \\ / : * ? \" < > | # ^ [ ] 等文件夹/链接特殊字符")
    if name.rstrip(" .") != name:
        raise HTTPException(422, "清单名不能以空格或点结尾")
    if name.split(".", 1)[0].upper() in RESERVED_NAMES:
        raise HTTPException(422, "这个清单名是操作系统保留名，请换一个")
    return name


def task_folder(vault_dir: Path | str, folder: Any, *, create: bool = False) -> Path:
    """清单名 → 任务目录；收集箱/空名 = 任务根目录。"""
    root = task_root(vault_dir)
    name = normalize_title(folder)
    target = root if not name or name == INBOX else root / _clean_folder_name(name)
    if create:
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise HTTPException(409, f"建不了任务文件夹：{error}") from error
    return target


def clean_list_name(value: Any) -> str:
    """校验清单名能直接当文件夹名；不能时抛 422。"""
    return _clean_folder_name(value)


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


def read_task_note(vault_dir: Path | str, path: Path) -> dict[str, Any] | None:
    """读一篇任务笔记；不存在/不是任务返回 None，坏 YAML 抛 TaskNoteFormatError。"""
    loaded = load_note(path, too_large="任务笔记过大，已停止编辑",
                       bad_encoding="任务笔记不是 UTF-8 编码，暂不能编辑")
    if not loaded["exists"]:
        return None
    metadata = _metadata(loaded)
    if not is_task_metadata(metadata):
        return None
    title = normalize_title(metadata.get("标题")) or path.stem
    done = _bool_text(metadata.get("完成"))
    done_on = _date_text(metadata.get("完成于")) if done else ""
    folder = ""
    try:
        relative_parent = path.parent.resolve().relative_to(task_root(vault_dir))
        if relative_parent.parts:
            folder = relative_parent.parts[0]
    except (OSError, ValueError):
        folder = ""
    relpath = task_relative(path, vault_dir)
    return {
        "key": task_key(relpath),
        "path": path,
        "relpath": relpath,
        "folder": folder,
        "list_name": folder or INBOX,
        "title": title,
        "done": done,
        "done_on": done_on,
        "date": _date_text(metadata.get("日期")),
        "time": _time_text(metadata.get("时间")),
        "priority": _priority_text(metadata.get("优先级")),
        "tags": _tags_value(metadata.get("标签")),
        "pinned": _bool_text(metadata.get("置顶")),
        "created": _date_text(metadata.get("创建")),
        "dida_id": _coerce_text(metadata.get("dida_id")),
        "metadata": metadata,
        "exists": True,
        "raw": loaded["raw"],
        "revision": loaded["revision"],
        "newline": loaded["newline"],
        "lines": loaded["lines"],
        "body_start": loaded["body_start"],
        "legacy": False,
    }


def _warning_path(path: Path, vault_dir: Path | str) -> str:
    try:
        return path.resolve().relative_to(task_root(vault_dir)).as_posix()
    except (OSError, ValueError):
        return path.name


def _scan_file(vault_dir: Path | str, path: Path, warnings: list[dict[str, str]]) -> dict[str, Any] | None:
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


def scan_task_notes(vault_dir: Path | str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """扫描任务根 + 一层清单目录；返回任务与读取警告。"""
    root = task_root(vault_dir)
    if not root.is_dir():
        return [], []
    notes: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    for entry in _folder_entries(root):
        if entry.name.startswith(".") or _is_link(entry):
            continue
        if entry.is_dir():
            for child in _folder_entries(entry):
                if child.name.startswith(".") or _is_link(child):
                    continue
                if child.is_dir():
                    warnings.append({"path": _warning_path(child, vault_dir) + "/",
                                     "reason": "嵌套目录不扫描（任务只支持根目录与一层清单文件夹）"})
                    continue
                if child.suffix.lower() != ".md":
                    continue
                note = _scan_file(vault_dir, child, warnings)
                if note is not None:
                    notes.append(note)
        elif entry.suffix.lower() == ".md":
            note = _scan_file(vault_dir, entry, warnings)
            if note is not None:
                notes.append(note)
    notes.sort(key=lambda item: item["relpath"])
    return notes, warnings


def _as_body_lines(note: dict[str, Any]) -> list[str]:
    return list(note["lines"][note["body_start"]:])


def note_body(note: dict[str, Any]) -> str:
    """任务笔记的正文（保留内部换行，去掉文件末尾由 split 产生的空行）。"""
    lines = _as_body_lines(note)
    return "\n".join(lines)


def _build_metadata(existing: dict[str, Any], title: str, stem: str, fields: dict[str, Any]) -> dict[str, Any]:
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
    dida_id = str(fields.get("dida_id") or existing.get("dida_id") or "").strip()
    if dida_id:
        metadata["dida_id"] = dida_id
    for key, value in existing.items():
        if key not in KNOWN_KEYS:
            metadata[key] = value
    return metadata


class _TaskDumper(yaml.SafeDumper):
    pass


def _represent_string(dumper: yaml.SafeDumper, data: str):
    # 日期/时间必须按字符串写回，避免被 YAML 读成 date / sexagesimal。
    style = '"' if QUOTE_AS_STRING.match(data) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_TaskDumper.add_representer(str, _represent_string)


def _compose(metadata: dict[str, Any], body_lines: list[str]) -> list[str]:
    block = yaml.dump(metadata, Dumper=_TaskDumper, allow_unicode=True,
                      sort_keys=False, default_flow_style=False, width=1000)
    return ["---", *block.rstrip("\n").split("\n"), "---", *body_lines]


def _body_lines(body: str) -> list[str]:
    """正文 → 行列表；空正文写一个空行，非空正文补齐末尾换行。"""
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
        "dida_id": _coerce_text(values.get("dida_id")),
    }


def create_task_note(vault_dir: Path | str, folder: Any, title: str,
                     values: dict[str, Any], *, body: str = "") -> dict[str, Any]:
    """创建一篇任务笔记；重名自动编号，标题无法落进文件名时写 `标题`。"""
    cleaned = normalize_title(title)
    if not cleaned or len(cleaned) > 200 or any(ord(character) < 32 for character in cleaned):
        raise HTTPException(422, "任务标题需要 1–200 个字符，且不能包含换行")
    target_dir = task_folder(vault_dir, folder, create=True)
    fields = _fields_from(values)
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
    """把源文件移动到目标名；跨目录、只改大小写都走临时名，绝不覆盖。"""
    # WindowsPath 的 == 不区分大小写，必须按字符串判断“只改大小写”这种情况。
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
                      folder: Any, title: str, values: dict[str, Any],
                      body: str | None = None) -> dict[str, Any]:
    """更新/改名/移动一篇任务笔记；正文默认保留，传入 body 时替换。"""
    cleaned = normalize_title(title)
    if not cleaned or len(cleaned) > 200 or any(ord(character) < 32 for character in cleaned):
        raise HTTPException(422, "任务标题需要 1–200 个字符，且不能包含换行")
    target_dir = task_folder(vault_dir, folder, create=True)
    fields = _fields_from(values)
    title_changed = cleaned != normalize_title(note["title"])
    folder_changed = target_dir.resolve() != note["path"].parent.resolve()
    if title_changed:
        base = safe_task_stem(cleaned)
    else:
        base = note["path"].stem
    if title_changed or folder_changed:
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
        # 先写内容（不丢数据），再挪文件名；只改大小写也走临时名。
        _move_note_file(note["path"], target)
    fresh = read_task_note(vault_dir, target)
    if fresh is None:
        raise HTTPException(409, "任务笔记更新后无法识别，请刷新后重试")
    return fresh


def new_trash_batch(vault_dir: Path | str) -> Path:
    root = Path(vault_dir).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    batch = root / TRASH_DIR_REL / f"{stamp}-{secrets.token_hex(4)}"
    try:
        batch.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        raise HTTPException(409, f"建不了任务回收站：{error}") from error
    return batch


def _trash_target(batch: Path, relpath: str) -> Path:
    target = batch / Path(relpath)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def copy_to_trash(vault_dir: Path | str, path: Path, batch: Path) -> str:
    """复制一份文件进回收批次（删除清单前备份配置用）。"""
    target = _trash_target(batch, task_relative(path, vault_dir))
    try:
        shutil.copy2(path, target)
    except OSError as error:
        raise HTTPException(409, f"备份到任务回收站失败：{error}") from error
    return task_relative(target, vault_dir)


def trash_note(vault_dir: Path | str, note: dict[str, Any],
               batch: Path | None = None) -> str:
    source = note["path"]
    try:
        if source.read_bytes() != note["raw"]:
            raise HTTPException(409, "任务笔记已在别处修改，请刷新后重试")
    except OSError as error:
        raise HTTPException(409, "任务笔记已经不在原处，请刷新后重试") from error
    batch = batch or new_trash_batch(vault_dir)
    target = _trash_target(batch, note["relpath"])
    try:
        os.replace(source, target)
    except OSError as error:
        raise HTTPException(409, f"把任务移进回收站失败：{error}") from error
    return task_relative(batch, vault_dir)


def _folder_notes(vault_dir: Path | str, folder: Any) -> list[dict[str, Any]]:
    directory = task_folder(vault_dir, folder)
    notes: list[dict[str, Any]] = []
    for entry in _folder_entries(directory):
        if entry.name.startswith(".") or _is_link(entry):
            raise HTTPException(409, "清单目录里有隐藏/链接文件，不能删除这个清单")
        if entry.is_dir() or entry.suffix.lower() != ".md":
            raise HTTPException(409, "清单目录里有非任务文件，先在 Obsidian 里处理后再删除清单")
        try:
            note = read_task_note(vault_dir, entry)
        except (TaskNoteFormatError, HTTPException) as error:
            raise HTTPException(409, "清单目录里有无法识别的笔记，不能删除这个清单") from error
        if note is None:
            raise HTTPException(409, "清单目录里有不是任务的文件，不能删除这个清单")
        notes.append(note)
    return notes


def trash_list_folder(vault_dir: Path | str, folder: Any, *,
                      config_path: Path | None = None,
                      batch: Path | None = None) -> dict[str, Any]:
    """删除清单目录：先检查没有非任务文件，再把配置和任务笔记放进同一个回收批次。"""
    if normalize_title(folder) in {"", INBOX}:
        raise HTTPException(422, "收集箱不能删除")
    directory = task_folder(vault_dir, folder)
    if not directory.exists():
        return {"batch": "", "count": 0}
    notes = _folder_notes(vault_dir, folder)
    batch = batch or new_trash_batch(vault_dir)
    if config_path is not None and config_path.is_file():
        copy_to_trash(vault_dir, config_path, batch)
    for note in notes:
        target = _trash_target(batch, note["relpath"])
        try:
            os.replace(note["path"], target)
        except OSError as error:
            raise HTTPException(409, f"把清单任务移进回收站失败：{error}") from error
    try:
        directory.rmdir()
    except OSError:
        # 还有其它文件（理论上前面已拦截）：保留目录，不阻止清单定义删除。
        pass
    return {"batch": task_relative(batch, vault_dir), "count": len(notes)}


def rename_list_folder(vault_dir: Path | str, old_name: Any, new_name: Any) -> bool:
    """清单改名时同步改磁盘文件夹；只改大小写也安全。"""
    old_dir = task_folder(vault_dir, old_name)
    new_dir = task_folder(vault_dir, new_name)
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
    """配置 + 全部任务笔记的复合修订号（前端乐观锁）。"""
    digest = hashlib.sha256()
    digest.update(b"task-board-v1\0")
    digest.update((config_revision or "missing").encode("utf-8"))
    for note in sorted(notes, key=lambda item: item["relpath"]):
        digest.update(b"\0" + note["relpath"].encode("utf-8") + b"\0" + note["revision"].encode("utf-8"))
    return digest.hexdigest()[:16]


__all__ = [
    "INBOX", "TASK_DIR_REL", "TASK_DIR_POSIX", "MAX_TAGS", "MAX_TAG_NAME",
    "PRIORITIES", "TaskNoteFormatError", "task_root", "task_relative", "task_key",
    "normalize_title", "safe_task_stem", "unique_stem", "task_folder",
    "clean_list_name", "read_task_note", "scan_task_notes", "create_task_note",
    "rewrite_task_note", "note_body", "new_trash_batch", "copy_to_trash",
    "trash_note", "trash_list_folder", "rename_list_folder", "board_revision",
]
