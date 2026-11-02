"""自我档案 v2：固定本子、条目与画像（2026-11-01）。

数据目录与前端左栏一一对应：

- ``01_自我档案/01_档案/<本子文件夹>/*.md``  每个条目一篇 Markdown；
- ``01_自我档案/01_档案/00_画像.md``         缩略版现状画像；
- 身份文件 ``01_自我档案/01_档案/身份（identity）.md`` 仍由 identity.py 管理。

写入权只有人生导师：本模块提供 create / supersede / portrait 的原语，
真正的“用户确认”在 mentor_pending 与 API 层。Torch 界面不提供手动编辑。
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import yaml

from . import vault_layout
from .memory_store import update_document
from .markdown_store import revision as _revision
from .vault import parse_text, safe_note_path
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SHANGHAI = ZoneInfo("Asia/Shanghai")
ENTRY_TYPE = "self_archive_entry"
PORTRAIT_TYPE = "self_portrait"
ENTRY_TYPES = ("fact", "behavior", "inference", "narrative", "hypothesis")
ENTRY_TYPE_LABELS = {
    "fact": "事实",
    "behavior": "行为证据",
    "inference": "推断",
    "narrative": "自我叙事",
    "hypothesis": "待验证假设",
}
CONFIDENCES = ("high", "medium", "low")
CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}
STATUSES = ("active", "superseded", "archived")
ORIGINS = ("mentor", "question", "migration", "user-answer")
MAX_ENTRY_CHARS = 8_000
MAX_TITLE = 120
MAX_PORTRAIT_CHARS = 1_200
MAX_SOURCES = 20

# key, 标题, 文件夹, 默认条目类型（顺序就是左栏顺序）
BOOKS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("self_facts", "基础事实", "01_基础事实", ("fact", "behavior")),
    ("self_body", "身体与能量", "02_身体与能量", ("fact", "behavior", "inference")),
    ("self_behavior", "行为与习惯", "03_行为与习惯", ("behavior", "fact")),
    ("self_emotion", "情绪与压力", "04_情绪与压力", ("behavior", "inference", "hypothesis")),
    ("self_motivation", "动机与需求", "05_动机与需求", ("inference", "narrative", "fact")),
    ("self_values", "三观与决策", "06_三观与决策", ("narrative", "fact", "inference")),
    ("self_relationship", "关系与边界", "07_关系与边界", ("behavior", "inference", "fact")),
    ("self_narrative", "自我叙事", "08_自我叙事", ("narrative",)),
    ("self_paradox", "矛盾与盲区", "09_矛盾与盲区", ("inference", "hypothesis")),
    ("self_timeline", "人生时间线", "10_人生时间线", ("fact", "narrative")),
    ("self_experiments", "验证与更新", "11_验证与更新", ("hypothesis", "behavior", "fact")),
)
BOOK_BY_KEY = {item[0]: item for item in BOOKS}
BOOK_ORDER = tuple(item[0] for item in BOOKS)

_FILENAME_UNSAFE = re.compile(r'[<>:"/\\|?*#^\[\]\x00-\x1f]')


class SelfArchiveError(RuntimeError):
    """A user-visible archive error; API turns it into 4xx/5xx."""

    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class SelfArchiveConflict(SelfArchiveError):
    def __init__(self, message: str = "条目已在别处修改：请刷新后再确认"):
        super().__init__(message, 409)


def _now() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", delete=False,
        dir=str(path.parent), prefix=".self-", suffix=".tmp",
    )
    try:
        handle.write(text)
        handle.close()
        os.replace(handle.name, path)
    except OSError:
        Path(handle.name).unlink(missing_ok=True)
        raise


def _clean(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _sources(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()[:500]
        if text and text not in result:
            result.append(text)
    return result[:MAX_SOURCES]


def _ensure_write(read_only: bool, action: str) -> None:
    try:
        ensure_vault_writable(read_only, action)
    except VaultReadOnlyError as error:
        raise SelfArchiveError(str(error), 409) from error


def book_meta(book_key: str) -> dict[str, Any]:
    item = BOOK_BY_KEY.get(str(book_key or "").strip())
    if item is None:
        raise SelfArchiveError("未知的档案本子", 404)
    key, title, folder, types = item
    return {
        "key": key,
        "title": title,
        "folder": folder,
        "path": f"{vault_layout.SELF_ARCHIVE_ROOT}/{folder}",
        "entry_types": list(types),
        "protected": True,
    }


def book_dir(vault_dir: Path | str, book_key: str) -> Path:
    meta = book_meta(book_key)
    return safe_note_path(Path(vault_dir), meta["path"])


def _entry_relative(vault: Path, path: Path) -> str:
    return path.resolve().relative_to(vault.resolve()).as_posix()


def _entry_id(path: Path, metadata: Mapping[str, Any]) -> str:
    value = str(metadata.get("entry_id") or "").strip()
    if value:
        return value
    return "ent_" + hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:12]


def _parse_entry(vault: Path, path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_bytes()
        metadata, body = parse_text(raw.decode("utf-8-sig"))
    except (OSError, ValueError, yaml.YAMLError):
        return None
    relative = _entry_relative(vault, path)
    section = str(metadata.get("section") or "").strip()
    if section not in BOOK_BY_KEY:
        section = _section_from_relative(relative)
    if section not in BOOK_BY_KEY:
        section = ""
    entry_type = str(metadata.get("entry_type") or "").strip().lower()
    if entry_type not in ENTRY_TYPES:
        entry_type = "inference"
    confidence = str(metadata.get("confidence") or "").strip().lower()
    if confidence not in CONFIDENCES:
        confidence = "medium"
    status = str(metadata.get("status") or "").strip().lower()
    if status not in STATUSES:
        status = "active"
    return {
        "type": str(metadata.get("type") or ENTRY_TYPE),
        "entry_id": _entry_id(path, metadata),
        "section": section,
        "section_title": BOOK_BY_KEY[section][1] if section else "未分类",
        "entry_type": entry_type,
        "entry_type_label": ENTRY_TYPE_LABELS[entry_type],
        "confidence": confidence,
        "confidence_label": CONFIDENCE_LABELS[confidence],
        "status": status,
        "title": str(metadata.get("title") or path.stem)[:MAX_TITLE],
        "text": body.strip(),
        "evidence": str(metadata.get("evidence") or "").strip(),
        "counterexample": str(metadata.get("counterexample") or "").strip(),
        "verify": str(metadata.get("verify") or "").strip(),
        "sources": _sources(metadata.get("sources")),
        "origin": str(metadata.get("origin") or "").strip(),
        "supersedes": str(metadata.get("supersedes") or "").strip(),
        "created": str(metadata.get("created") or ""),
        "updated": str(metadata.get("updated") or ""),
        "path": relative,
        "revision": _revision(raw),
    }


def _section_from_relative(relative: str) -> str:
    prefix = vault_layout.SELF_ARCHIVE_ROOT + "/"
    if not relative.startswith(prefix):
        return ""
    folder = relative[len(prefix):].split("/", 1)[0]
    for key, _title, item_folder, _types in BOOKS:
        if item_folder == folder:
            return key
    return ""


def list_entries(vault_dir: Path | str, book_key: str, *,
                 include_history: bool = False) -> list[dict[str, Any]]:
    vault = Path(vault_dir).resolve()
    directory = book_dir(vault, book_key)
    if not directory.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        item = _parse_entry(vault, path)
        if item is None:
            continue
        if not include_history and item["status"] != "active":
            continue
        entries.append(item)
    entries.sort(key=lambda item: (item.get("created") or "", item["path"]), reverse=True)
    return entries


def all_entries(vault_dir: Path | str, *, include_history: bool = False) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key in BOOK_ORDER:
        result.extend(list_entries(vault_dir, key, include_history=include_history))
    return result


def find_entry(vault_dir: Path | str, entry_id: str, *,
               include_history: bool = True) -> dict[str, Any] | None:
    wanted = str(entry_id or "").strip()
    if not wanted:
        return None
    for item in all_entries(vault_dir, include_history=include_history):
        if item["entry_id"] == wanted:
            return item
    return None


def _entry_text(metadata: Mapping[str, Any], body: str) -> str:
    lines = [
        "---",
        *yaml.safe_dump(dict(metadata), allow_unicode=True, sort_keys=False)
        .rstrip("\n").split("\n"),
        "---",
        "",
        str(body or "").strip(),
        "",
    ]
    return "\n".join(lines)


def create_entry(vault_dir: Path | str, *, book_key: str, entry_type: str,
                 confidence: str, title: str, text: str,
                 evidence: str = "", counterexample: str = "", verify: str = "",
                 sources: Any = None, origin: str = "mentor",
                 supersedes: str = "", read_only: bool = False) -> dict[str, Any]:
    """写入一个新条目；本函数不做“用户确认”，确认逻辑在 API/待确认层。"""
    _ensure_write(read_only, "写入自我档案")
    meta = book_meta(book_key)
    entry_type = str(entry_type or "").strip().lower()
    if entry_type not in ENTRY_TYPES:
        raise SelfArchiveError("条目类型不合法", 422)
    confidence = str(confidence or "").strip().lower()
    if confidence not in CONFIDENCES:
        confidence = "medium"
    clean_title = _clean(title, MAX_TITLE)
    if not clean_title:
        raise SelfArchiveError("条目标题不能为空", 422)
    clean_text = str(text or "").strip()
    if not clean_text:
        raise SelfArchiveError("条目正文不能为空", 422)
    if len(clean_text) > MAX_ENTRY_CHARS:
        raise SelfArchiveError(f"条目正文最多 {MAX_ENTRY_CHARS} 字", 422)
    origin = str(origin or "mentor").strip()
    if origin not in ORIGINS:
        origin = "mentor"
    now = _now()
    entry_id = "ent_" + uuid.uuid4().hex[:12]
    metadata = {
        "type": ENTRY_TYPE,
        "entry_id": entry_id,
        "section": meta["key"],
        "section_title": meta["title"],
        "entry_type": entry_type,
        "confidence": confidence,
        "status": "active",
        "title": clean_title,
        "evidence": _clean(evidence, 2_000),
        "counterexample": _clean(counterexample, 2_000),
        "verify": _clean(verify, 2_000),
        "sources": _sources(sources),
        "origin": origin,
        "supersedes": str(supersedes or "").strip(),
        "created": now,
        "updated": now,
    }
    directory = book_dir(vault_dir, meta["key"])
    stem = "条目-" + _stamp() + "-" + entry_id[-6:]
    stem = _FILENAME_UNSAFE.sub("-", stem)
    path = directory / f"{stem}.md"
    index = 2
    while path.exists():
        path = directory / f"{stem}-{index}.md"
        index += 1
    _atomic_write(path, _entry_text(metadata, clean_text))
    item = _parse_entry(Path(vault_dir).resolve(), path)
    if item is None:
        raise SelfArchiveError("条目写入后无法读回", 500)
    return item


def _rewrite_entry(path: Path, item: Mapping[str, Any], body: str,
                   *, status: str | None = None) -> None:
    metadata = {
        "type": item.get("type") or ENTRY_TYPE,
        "entry_id": item.get("entry_id") or "",
        "section": item.get("section") or "",
        "section_title": item.get("section_title") or "",
        "entry_type": item.get("entry_type") or "inference",
        "confidence": item.get("confidence") or "medium",
        "status": status or item.get("status") or "active",
        "title": item.get("title") or "",
        "evidence": item.get("evidence") or "",
        "counterexample": item.get("counterexample") or "",
        "verify": item.get("verify") or "",
        "sources": list(item.get("sources") or []),
        "origin": item.get("origin") or "",
        "supersedes": item.get("supersedes") or "",
        "created": item.get("created") or "",
        "updated": _now(),
    }
    _atomic_write(path, _entry_text(metadata, body))


def supersede_entry(vault_dir: Path | str, old_entry_id: str, *,
                    entry_type: str, confidence: str, title: str, text: str,
                    evidence: str = "", counterexample: str = "", verify: str = "",
                    sources: Any = None, origin: str = "mentor",
                    base_revision: str = "", read_only: bool = False) -> dict[str, Any]:
    """用新条目取代旧条目；旧条目保留为 superseded，不删除。"""
    _ensure_write(read_only, "修正自我档案")
    old = find_entry(vault_dir, old_entry_id, include_history=False)
    if old is None:
        raise SelfArchiveError("要修正的条目不存在或已被取代", 404)
    if old["status"] != "active":
        raise SelfArchiveError("要修正的条目已不在当前版本", 409)
    if base_revision and str(base_revision) != old["revision"]:
        raise SelfArchiveConflict()
    vault = Path(vault_dir).resolve()
    old_path = safe_note_path(vault, old["path"])
    # 先写新条目；旧条目更新失败时回滚新文件，避免出现两个 active。
    new_entry = create_entry(
        vault_dir, book_key=old["section"], entry_type=entry_type,
        confidence=confidence, title=title, text=text, evidence=evidence,
        counterexample=counterexample, verify=verify, sources=sources,
        origin=origin, supersedes=old["entry_id"], read_only=read_only,
    )
    new_path = safe_note_path(vault, new_entry["path"])
    try:
        _rewrite_entry(old_path, old, old["text"], status="superseded")
    except Exception:
        new_path.unlink(missing_ok=True)
        raise
    return new_entry


def read_portrait(vault_dir: Path | str) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    path = safe_note_path(vault, vault_layout.SELF_PORTRAIT_RELATIVE)
    default = {
        "exists": False, "path": vault_layout.SELF_PORTRAIT_RELATIVE,
        "revision": "", "updated_at": "", "sources": [], "confidence": 0,
        "text": "", "body": "",
    }
    if not path.is_file():
        return default
    try:
        raw = path.read_bytes()
        metadata, body = parse_text(raw.decode("utf-8-sig"))
    except (OSError, ValueError, yaml.YAMLError):
        return default
    return {
        "exists": True,
        "path": vault_layout.SELF_PORTRAIT_RELATIVE,
        "revision": _revision(raw),
        "updated_at": str(metadata.get("updated_at") or ""),
        "sources": _sources(metadata.get("sources")),
        "confidence": metadata.get("confidence", 0),
        "text": body.strip(),
        "body": body.strip(),
    }


def write_portrait(vault_dir: Path | str, text: str, *, sources: Any = None,
                   confidence: Any = 0, read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "更新自我档案画像")
    clean = str(text or "").strip()
    if not clean:
        raise SelfArchiveError("画像不能为空", 422)
    if len(clean) > MAX_PORTRAIT_CHARS:
        clean = clean[: MAX_PORTRAIT_CHARS - 1].rstrip() + "…"
    vault = Path(vault_dir).resolve()
    path = safe_note_path(vault, vault_layout.SELF_PORTRAIT_RELATIVE)
    try:
        value = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        value = 0.0
    metadata = {
        "type": PORTRAIT_TYPE,
        "updated_at": _now(),
        "sources": _sources(sources),
        "confidence": round(value, 2),
    }
    with update_document(path, SelfArchiveError) as update:
        backup = update.backup(safe_note_path(vault, vault_layout.SELF_MENTOR_PORTRAIT_HISTORY),
                               f"画像-{_stamp()}")
        update.write(_entry_text(metadata, clean))
        result = read_portrait(vault)
        result["history_path"] = backup.relative_to(vault).as_posix() if backup else ""
        return result


def context_text(vault_dir: Path | str, *, max_chars: int = 12_000) -> str:
    """给人生导师的档案摘要：只放 active 条目，按本子分组，控制总长度。"""
    blocks: list[str] = []
    used = 0
    for key in BOOK_ORDER:
        meta = book_meta(key)
        rows = list_entries(vault_dir, key)
        if not rows:
            continue
        lines = [f"## {meta['title']}（{len(rows)} 条）"]
        for item in rows:
            text = _clean(item["text"], 360).replace("\n", " ")
            evidence = _clean(item["evidence"], 160).replace("\n", " ")
            line = (f"- [{item['entry_type_label']}|{item['confidence_label']}] "
                    f"{item['title']}：{text}")
            if evidence:
                line += f"（证据：{evidence}）"
            if item["sources"]:
                line += "（来源：" + "、".join(item["sources"]) + "）"
            lines.append(line)
        block = "\n".join(lines)
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(block) > remaining:
            block = block[: max(0, remaining - 1)].rstrip() + "…"
        blocks.append(block)
        used += len(block) + 2
    return "\n\n".join(blocks).strip()


def overview(vault_dir: Path | str) -> dict[str, Any]:
    books: list[dict[str, Any]] = []
    total = 0
    for key in BOOK_ORDER:
        meta = book_meta(key)
        entries = list_entries(vault_dir, key)
        total += len(entries)
        latest = entries[0] if entries else None
        books.append({
            **meta,
            "count": len(entries),
            "latest": latest,
        })
    return {
        "books": books,
        "count": total,
        "portrait": read_portrait(vault_dir),
        "archive_root": vault_layout.SELF_ARCHIVE_ROOT,
    }


__all__ = [
    "BOOKS", "BOOK_BY_KEY", "BOOK_ORDER", "ENTRY_TYPES", "ENTRY_TYPE_LABELS",
    "CONFIDENCES", "CONFIDENCE_LABELS", "STATUSES", "ORIGINS",
    "SelfArchiveError", "SelfArchiveConflict",
    "book_meta", "book_dir", "list_entries", "all_entries", "find_entry",
    "create_entry", "supersede_entry", "read_portrait", "write_portrait",
    "context_text", "overview",
]
