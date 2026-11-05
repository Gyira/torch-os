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
import json
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
from .memory_store import operation_lock, read_document, update_document
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
# Minimal write-ahead metadata for crash recovery; never a second copy of the
# archive body. Kept under <Vault>/.torch-os as required by the top-level rules.
OPS_RELATIVE = ".torch-os/self-archive-ops"
OP_SCHEMA = 1
OP_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
OP_OPEN_STATUSES = ("preparing", "new_written")

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
_BOOK_TITLE_TO_KEY = {item[1]: item[0] for item in BOOKS}
# 模型经常把 section 写成 "motivation" / "动机" / "动机与需求"；这些都是同一个本子。
_BOOK_ALIASES = {
    "fact": "self_facts", "facts": "self_facts",
    "body": "self_body", "energy": "self_body",
    "behavior": "self_behavior", "behaviour": "self_behavior", "behaviors": "self_behavior",
    "emotion": "self_emotion", "emotions": "self_emotion", "stress": "self_emotion",
    "motivation": "self_motivation", "motivations": "self_motivation", "needs": "self_motivation",
    "value": "self_values", "values": "self_values", "worldview": "self_values",
    "relationship": "self_relationship", "relationships": "self_relationship",
    "boundaries": "self_relationship",
    "narrative": "self_narrative", "story": "self_narrative",
    "paradox": "self_paradox", "contradiction": "self_paradox",
    "blind_spot": "self_paradox", "blind_spots": "self_paradox",
    "timeline": "self_timeline", "life_timeline": "self_timeline",
    "experiment": "self_experiments", "experiments": "self_experiments",
    "verification": "self_experiments",
    "基础事实": "self_facts", "身体": "self_body", "身体与能量": "self_body",
    "行为": "self_behavior", "行为与习惯": "self_behavior",
    "情绪": "self_emotion", "情绪与压力": "self_emotion",
    "动机": "self_motivation", "需求": "self_motivation", "动机与需求": "self_motivation",
    "三观": "self_values", "价值观": "self_values", "三观与决策": "self_values",
    "关系": "self_relationship", "关系与边界": "self_relationship",
    "叙事": "self_narrative", "自我叙事": "self_narrative",
    "矛盾": "self_paradox", "盲区": "self_paradox", "矛盾与盲区": "self_paradox",
    "时间线": "self_timeline", "人生时间线": "self_timeline",
    "验证": "self_experiments", "验证与更新": "self_experiments",
}


def normalize_book_key(value: Any) -> str:
    """Accept canonical keys, short English names, and Chinese book titles."""
    text = str(value or "").strip()
    if not text:
        return ""
    if text in BOOK_BY_KEY:
        return text
    lowered = text.lower().replace("-", "_").replace(" ", "_")
    if lowered in BOOK_BY_KEY:
        return lowered
    if f"self_{lowered}" in BOOK_BY_KEY:
        return f"self_{lowered}"
    return (_BOOK_TITLE_TO_KEY.get(text) or _BOOK_ALIASES.get(lowered)
            or _BOOK_ALIASES.get(text) or "")

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
        handle.flush()
        os.fsync(handle.fileno())
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
    item = BOOK_BY_KEY.get(normalize_book_key(book_key))
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
    section = normalize_book_key(metadata.get("section"))
    if not section:
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


def _split_current_entries(vault: Path, items: list[dict[str, Any]], *,
                           include_history: bool) -> list[dict[str, Any]]:
    """Hide a new version whose interrupted supersede still leaves the old one active.

    This keeps a single current version visible even before recovery runs. The
    new file stays on disk and remains visible in history with a marker.
    """
    pending = _incomplete_supersede_ids(vault)
    active_ids = {item["entry_id"] for item in items if item["status"] == "active"}
    result: list[dict[str, Any]] = []
    for item in items:
        limbo = (item["status"] == "active" and item["entry_id"] in pending
                 and item.get("supersedes") in active_ids)
        if limbo:
            item["operation_pending"] = True
            if not include_history:
                continue
        result.append(item)
    return result


_HEADING_LINE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_HR_LINE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def _compact_text(value: Any) -> str:
    """Normalize text for display-time dedupe (not for storage)."""
    text = str(value or "")
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s*|>\s*)", "", text)
    text = re.sub(r"[*_`]", "", text)
    return re.sub(r"\s+", "", text).strip()


def _strip_title_heading(text: str, title: str) -> str:
    """Drop a leading Markdown heading that only repeats the entry title."""
    lines = str(text or "").strip().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines:
        match = _HEADING_LINE.match(lines[0].strip())
        wanted = _compact_text(title)
        if match and wanted and _compact_text(match.group(1)) == wanted:
            lines.pop(0)
    return "\n".join(lines).strip()


def _is_stub_entry(item: Mapping[str, Any]) -> bool:
    """An entry whose body is only its own title heading (e.g. migration leftovers)."""
    has_support = bool(item.get("evidence") or item.get("counterexample") or item.get("verify"))
    text = str(item.get("text") or "").strip()
    if not text:
        return not has_support
    if has_support:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return True
    title = _compact_text(item.get("title"))
    for line in lines:
        if _HR_LINE.match(line):
            continue
        match = _HEADING_LINE.match(line)
        if match and title and _compact_text(match.group(1)) == title:
            continue
        return False
    return True


def _entry_rank(item: Mapping[str, Any]) -> tuple:
    """Prefer curated entries over migration copies, then completeness, then recency."""
    return (
        1 if item.get("origin") != "migration" else 0,
        _CONFIDENCE_RANK.get(str(item.get("confidence") or ""), 2),
        1 if item.get("evidence") else 0,
        1 if item.get("verify") else 0,
        min(len(item.get("sources") or []), 5),
        str(item.get("created") or ""),
        str(item.get("updated") or ""),
        str(item.get("entry_id") or ""),
    )


def _merge_entry_details(target: dict[str, Any], extra: Mapping[str, Any]) -> None:
    merged = list(target.get("sources") or [])
    for source in extra.get("sources") or []:
        if source not in merged:
            merged.append(source)
    target["sources"] = merged[:MAX_SOURCES]
    for field in ("evidence", "counterexample", "verify"):
        if not target.get(field) and extra.get(field):
            target[field] = extra[field]


def _dedupe_current_entries(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse identical current entries and hide empty migration leftovers.

    Files stay untouched; this only affects what the book/overview/mentor context
    sees. History keeps every version, and duplicate sources are merged into the
    visible entry so provenance is not lost.
    """
    kept: dict[str, dict[str, Any]] = {}
    for item in items:
        if _is_stub_entry(item):
            continue
        body = _compact_text(_strip_title_heading(
            str(item.get("text") or ""), str(item.get("title") or "")))
        if not body:
            body = _compact_text(item.get("evidence") or item.get("verify"))
        if not body:
            continue
        title = _compact_text(item.get("title"))
        key = body if len(body) >= 40 else f"{title}\x00{body}"
        current = kept.get(key)
        if current is None:
            kept[key] = item
            continue
        if _entry_rank(item) > _entry_rank(current):
            _merge_entry_details(item, current)
            kept[key] = item
        else:
            _merge_entry_details(current, item)
    return list(kept.values())


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
    entries = _split_current_entries(vault, entries, include_history=include_history)
    if not include_history:
        entries = _dedupe_current_entries(entries)
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


def _clean_op_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not OP_ID_RE.fullmatch(text):
        raise SelfArchiveError("操作标识格式无效", 422)
    return text


def _ops_dir(vault: Path) -> Path:
    return vault / OPS_RELATIVE


def _op_path(vault: Path, operation_id: str) -> Path:
    digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:40]
    return _ops_dir(vault) / f"{digest}.json"


def _load_op(vault: Path, operation_id: str) -> dict[str, Any] | None:
    path = _op_path(vault, operation_id)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("operation_id") != operation_id:
        return None
    return record


def _save_op(vault: Path, record: Mapping[str, Any]) -> None:
    value = dict(record)
    value["schema"] = OP_SCHEMA
    value["updated"] = _now()
    _atomic_write(_op_path(vault, str(value["operation_id"])),
                  json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _op_records(vault: Path) -> list[dict[str, Any]]:
    directory = _ops_dir(vault)
    if not directory.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("operation_id"):
            records.append(record)
    return records


def _entry_payload(*, book_key: str, entry_type: str, confidence: str,
                   title: str, text: str, evidence: str,
                   counterexample: str, verify: str, sources: Any,
                   origin: str, supersedes: str) -> dict[str, Any]:
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
    return {
        "meta": meta, "entry_type": entry_type, "confidence": confidence,
        "title": clean_title, "text": clean_text,
        "evidence": _clean(evidence, 2_000),
        "counterexample": _clean(counterexample, 2_000),
        "verify": _clean(verify, 2_000), "sources": _sources(sources),
        "origin": origin, "supersedes": str(supersedes or "").strip(),
    }


def _entry_metadata(payload: Mapping[str, Any], entry_id: str) -> dict[str, Any]:
    now = _now()
    return {
        "type": ENTRY_TYPE,
        "entry_id": entry_id,
        "section": payload["meta"]["key"],
        "section_title": payload["meta"]["title"],
        "entry_type": payload["entry_type"],
        "confidence": payload["confidence"],
        "status": "active",
        "title": payload["title"],
        "evidence": payload["evidence"],
        "counterexample": payload["counterexample"],
        "verify": payload["verify"],
        "sources": list(payload["sources"]),
        "origin": payload["origin"],
        "supersedes": payload["supersedes"],
        "created": now,
        "updated": now,
    }


def _entry_target(vault: Path, payload: Mapping[str, Any], entry_id: str) -> Path:
    directory = book_dir(vault, payload["meta"]["key"])
    stem = "条目-" + _stamp() + "-" + entry_id[-6:]
    stem = _FILENAME_UNSAFE.sub("-", stem)
    path = directory / f"{stem}.md"
    index = 2
    while path.exists():
        path = directory / f"{stem}-{index}.md"
        index += 1
    return path


def _write_entry_file(vault: Path, path: Path, metadata: Mapping[str, Any],
                      text: str) -> dict[str, Any]:
    _atomic_write(path, _entry_text(metadata, text))
    item = _parse_entry(vault, path)
    if item is None:
        raise SelfArchiveError("条目写入后无法读回", 500)
    return item


def _recover_create_operation(vault: Path, record: dict[str, Any], *,
                              read_only: bool) -> dict[str, Any]:
    entry_id = str(record.get("new_entry_id") or "")
    path_text = str(record.get("new_path") or "")
    if not entry_id or not path_text:
        return {"status": "failed", "error": "新建操作缺少条目信息"}
    try:
        path = safe_note_path(vault, path_text)
    except (OSError, ValueError):
        return {"status": "conflict", "error": "新建操作路径无效"}
    item = _parse_entry(vault, path) if path.is_file() else None
    if item is None:
        status = "failed"
    elif item["entry_id"] != entry_id:
        status = "conflict"
    else:
        status = "done"
    if status != "done":
        return {"status": status,
                "error": "新条目文件与操作记录不一致" if status == "conflict" else "新条目尚未写入"}
    if not read_only and record.get("status") != "done":
        record["status"] = "done"
        record["new_revision"] = item.get("revision") or ""
        try:
            _save_op(vault, record)
        except OSError:
            pass
    return {"status": "done", "entry": item}


def _create_entry_ledgered(vault: Path, payload: Mapping[str, Any],
                           operation_id: str) -> dict[str, Any]:
    existing = _load_op(vault, operation_id)
    if existing is not None:
        result = _recover_create_operation(vault, existing, read_only=False)
        if result["status"] == "done":
            return result["entry"]
        if result["status"] == "conflict":
            raise SelfArchiveConflict("新建操作与已有文件不一致；未覆盖任何文件")
        if existing.get("status") == "done":
            raise SelfArchiveError("已完成的新建操作找不到对应条目；请检查档案", 500)
    entry_id = "ent_" + uuid.uuid4().hex[:12]
    path = _entry_target(vault, payload, entry_id)
    metadata = _entry_metadata(payload, entry_id)
    record = {
        "schema": OP_SCHEMA, "operation_id": operation_id, "kind": "create",
        "status": "preparing", "created": _now(),
        "section": payload["meta"]["key"], "entry_type": payload["entry_type"],
        "title": payload["title"],
        "text_sha256": hashlib.sha256(payload["text"].encode("utf-8")).hexdigest(),
        "new_entry_id": entry_id,
        "new_path": path.relative_to(vault).as_posix(),
    }
    _save_op(vault, record)
    try:
        item = _write_entry_file(vault, path, metadata, payload["text"])
    except Exception:
        path.unlink(missing_ok=True)
        record.update(status="failed", error="条目写入失败")
        try:
            _save_op(vault, record)
        except OSError:
            pass
        raise
    record.update(status="done", new_revision=item.get("revision") or "")
    try:
        _save_op(vault, record)
    except OSError:
        # The entry is already durable; recovery will reconcile the op record.
        pass
    return item


def create_entry(vault_dir: Path | str, *, book_key: str, entry_type: str,
                 confidence: str, title: str, text: str,
                 evidence: str = "", counterexample: str = "", verify: str = "",
                 sources: Any = None, origin: str = "mentor",
                 supersedes: str = "", read_only: bool = False,
                 operation_id: str = "") -> dict[str, Any]:
    """写入一个新条目；本函数不做“用户确认”，确认逻辑在 API/待确认层。"""
    _ensure_write(read_only, "写入自我档案")
    payload = _entry_payload(
        book_key=book_key, entry_type=entry_type, confidence=confidence,
        title=title, text=text, evidence=evidence,
        counterexample=counterexample, verify=verify, sources=sources,
        origin=origin, supersedes=supersedes)
    vault = Path(vault_dir).resolve()
    op_id = _clean_op_id(operation_id)
    if op_id:
        with operation_lock(f"create|{vault}|{op_id}"):
            return _create_entry_ledgered(vault, payload, op_id)
    entry_id = "ent_" + uuid.uuid4().hex[:12]
    path = _entry_target(vault, payload, entry_id)
    return _write_entry_file(vault, path, _entry_metadata(payload, entry_id),
                             payload["text"])


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


def _read_entry_at(vault: Path, relative: str) -> tuple[Path | None, dict[str, Any] | None]:
    if not relative:
        return None, None
    try:
        path = safe_note_path(vault, relative)
    except (OSError, ValueError):
        return None, None
    if not path.is_file():
        return path, None
    return path, _parse_entry(vault, path)


def _save_op_quietly(vault: Path, record: Mapping[str, Any]) -> None:
    try:
        _save_op(vault, record)
    except OSError:
        pass


def _recover_supersede_operation(vault: Path, record: dict[str, Any], *,
                                 read_only: bool) -> dict[str, Any]:
    """Inspect both files; only complete when the old revision is unchanged.

    Never guesses: an externally edited old entry becomes a conflict and is
    left as the current version. A new entry written by an interrupted
    operation is kept on disk but hidden from the current view until resolved.
    """
    old_id = str(record.get("old_entry_id") or "")
    new_id = str(record.get("new_entry_id") or "")
    old_path, old = _read_entry_at(vault, str(record.get("old_path") or ""))
    new_path, new = _read_entry_at(vault, str(record.get("new_path") or ""))
    error = ""
    if new is not None and new.get("entry_id") != new_id:
        status = "conflict"
        error = "新条目文件与操作记录不一致"
    elif old is not None and old.get("entry_id") != old_id:
        status = "conflict"
        error = "旧条目文件与操作记录不一致"
    elif new is not None and old is not None and old.get("status") == "superseded":
        status = "done"
    elif new is not None and old is not None and old.get("status") == "active":
        if str(old.get("revision") or "") != str(record.get("old_revision") or ""):
            status = "conflict"
            error = "旧条目在中断后被外部修改；未覆盖"
        elif read_only:
            return {"status": "pending", "entry": None,
                    "error": "只读模式，未执行恢复写入"}
        else:
            # Re-read immediately before the replace; do not use the earlier copy.
            current_path, current = _read_entry_at(vault, str(record.get("old_path") or ""))
            if (current_path is None or current is None or current.get("entry_id") != old_id
                    or current.get("status") != "active"
                    or str(current.get("revision") or "") != str(record.get("old_revision") or "")):
                status = "conflict"
                error = "旧条目在恢复提交前被外部修改；未覆盖"
            else:
                _rewrite_entry(current_path, current, current["text"], status="superseded")
                status = "done"
    elif new is None and old is not None and old.get("status") == "active":
        status = "failed"
        error = "新条目尚未写入；旧条目保持当前版本"
    elif new is None and old is not None and old.get("status") == "superseded":
        status = "conflict"
        error = "旧条目已标记取代，但新条目缺失"
    else:
        status = "conflict"
        error = "新旧条目文件均不完整"
    if not read_only and record.get("status") != status:
        record["status"] = status
        record["error"] = error
        if status == "done" and new is not None:
            record["new_revision"] = new.get("revision") or ""
        _save_op_quietly(vault, record)
    return {"status": status, "entry": new if status == "done" else None,
            "error": error}


def _supersede_entry_locked(vault: Path, old_entry_id: str, operation_id: str, *,
                            entry_type: str, confidence: str, title: str, text: str,
                            evidence: str, counterexample: str, verify: str,
                            sources: Any, origin: str,
                            base_revision: str) -> dict[str, Any]:
    record = _load_op(vault, operation_id)
    if record is not None:
        result = _recover_supersede_operation(vault, record, read_only=False)
        if result["status"] == "done":
            return result["entry"]
        if result["status"] == "conflict":
            raise SelfArchiveConflict(result.get("error") or "这条档案修改存在未解决冲突")
        if record.get("status") == "done":
            raise SelfArchiveError("已完成的档案替代找不到对应新条目；请检查档案", 500)
    old = find_entry(vault, old_entry_id, include_history=False)
    if old is None:
        raise SelfArchiveError("要修正的条目不存在或已被取代", 404)
    if old["status"] != "active":
        raise SelfArchiveError("要修正的条目已不在当前版本", 409)
    if base_revision and str(base_revision) != old["revision"]:
        raise SelfArchiveConflict()
    old_path = safe_note_path(vault, old["path"])
    payload = _entry_payload(
        book_key=old["section"], entry_type=entry_type, confidence=confidence,
        title=title, text=text, evidence=evidence, counterexample=counterexample,
        verify=verify, sources=sources, origin=origin,
        supersedes=old["entry_id"],
    )
    new_id = "ent_" + uuid.uuid4().hex[:12]
    new_path = _entry_target(vault, payload, new_id)
    metadata = _entry_metadata(payload, new_id)
    record = {
        "schema": OP_SCHEMA, "operation_id": operation_id, "kind": "supersede",
        "status": "preparing", "created": _now(),
        "section": old["section"], "entry_type": payload["entry_type"],
        "title": payload["title"],
        "text_sha256": hashlib.sha256(payload["text"].encode("utf-8")).hexdigest(),
        "old_entry_id": old["entry_id"], "old_path": old["path"],
        "old_revision": old["revision"], "base_revision": str(base_revision or ""),
        "new_entry_id": new_id,
        "new_path": new_path.relative_to(vault).as_posix(),
    }
    _save_op(vault, record)
    try:
        new_entry = _write_entry_file(vault, new_path, metadata, payload["text"])
        record.update(status="new_written", new_revision=new_entry.get("revision") or "")
        _save_op_quietly(vault, record)
        # Commit against a fresh read; an external edit must not be overwritten.
        current_path, current = _read_entry_at(vault, old["path"])
        if (current_path is None or current is None
                or current.get("entry_id") != old["entry_id"]
                or current.get("status") != "active"
                or str(current.get("revision") or "") != str(old["revision"])):
            new_path.unlink(missing_ok=True)
            record.update(status="conflict", error="旧条目在提交前被外部修改；新版本已撤回")
            _save_op_quietly(vault, record)
            raise SelfArchiveConflict("旧条目在提交前被外部修改；本次未覆盖，请刷新后重试")
        _rewrite_entry(current_path, current, current["text"], status="superseded")
    except SelfArchiveConflict:
        raise
    except Exception:
        new_path.unlink(missing_ok=True)
        record.update(status="failed", error="替代写入失败；旧条目保持当前版本")
        _save_op_quietly(vault, record)
        raise
    record.update(status="done", new_revision=new_entry.get("revision") or "")
    _save_op_quietly(vault, record)
    return new_entry


def supersede_entry(vault_dir: Path | str, old_entry_id: str, *,
                    entry_type: str, confidence: str, title: str, text: str,
                    evidence: str = "", counterexample: str = "", verify: str = "",
                    sources: Any = None, origin: str = "mentor",
                    base_revision: str = "", read_only: bool = False,
                    operation_id: str = "") -> dict[str, Any]:
    """用新条目取代旧条目；旧条目保留为 superseded，不删除。

    ``operation_id`` is the idempotency key. Repeated calls with the same key
    return the already-written new entry instead of applying twice.
    """
    _ensure_write(read_only, "修正自我档案")
    vault = Path(vault_dir).resolve()
    op_id = _clean_op_id(operation_id) or ("supersede-" + uuid.uuid4().hex)
    with operation_lock(f"supersede|{vault}|{str(old_entry_id or '').strip()}"):
        return _supersede_entry_locked(
            vault, str(old_entry_id or "").strip(), op_id,
            entry_type=entry_type, confidence=confidence, title=title, text=text,
            evidence=evidence, counterexample=counterexample, verify=verify,
            sources=sources, origin=origin, base_revision=base_revision)


def recover_incomplete_operations(vault_dir: Path | str, *,
                                  read_only: bool = False) -> dict[str, Any]:
    """Resolve recorded archive operations left by an interrupted process.

    Only one completion is automatic: a ``supersede`` whose new file exists,
    whose old file is still active, and whose old revision is unchanged.
    Everything else is reported, not guessed or replayed. In read-only mode
    nothing is written; the current view still hides ambiguous new versions.
    """
    vault = Path(vault_dir).resolve()
    details: list[dict[str, Any]] = []
    for record in _op_records(vault):
        if record.get("status") == "done":
            continue
        kind = str(record.get("kind") or "")
        try:
            if kind == "supersede":
                with operation_lock(f"supersede|{vault}|{record.get('old_entry_id') or ''}"):
                    result = _recover_supersede_operation(vault, record, read_only=read_only)
            elif kind == "create":
                with operation_lock(f"create|{vault}|{record.get('operation_id')}"):
                    result = _recover_create_operation(vault, record, read_only=read_only)
            else:
                continue
        except Exception as error:  # noqa: BLE001 - one bad record must not stop the rest
            result = {"status": "error", "error": type(error).__name__}
        details.append({
            "operation_id": record.get("operation_id"),
            "kind": kind, "status": result.get("status", "error"),
            "old_entry_id": record.get("old_entry_id") or "",
            "new_entry_id": record.get("new_entry_id") or "",
            "error": result.get("error") or "",
        })
    counts = {"done": 0, "conflict": 0, "failed": 0, "pending": 0, "error": 0}
    for item in details:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"checked": len(details), **counts, "read_only": bool(read_only),
            "items": details}


def recovery_status(vault_dir: Path | str) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    items = [{
        "operation_id": record.get("operation_id"),
        "kind": record.get("kind"),
        "status": record.get("status"),
        "section": record.get("section") or "",
        "title": record.get("title") or "",
        "old_entry_id": record.get("old_entry_id") or "",
        "new_entry_id": record.get("new_entry_id") or "",
        "error": record.get("error") or "",
        "updated": record.get("updated") or "",
    } for record in _op_records(vault) if record.get("status") != "done"]
    return {"count": len(items), "items": items}


def _incomplete_supersede_ids(vault: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in _op_records(vault):
        if record.get("kind") != "supersede" or record.get("status") == "done":
            continue
        new_id = str(record.get("new_entry_id") or "")
        if new_id:
            result[new_id] = record
    return result


def read_portrait(vault_dir: Path | str) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    path = vault / vault_layout.SELF_PORTRAIT_RELATIVE
    try:
        raw = read_document(path, vault_dir=vault)
    except OSError:
        raw = None
    return _portrait_result(raw)


def _portrait_result(raw: bytes | None) -> dict[str, Any]:
    default = {
        "exists": False, "path": vault_layout.SELF_PORTRAIT_RELATIVE,
        "revision": "", "updated_at": "", "sources": [], "confidence": 0,
        "text": "", "body": "",
    }
    if raw is None:
        return default
    try:
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
    path = vault / vault_layout.SELF_PORTRAIT_RELATIVE
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
    with update_document(path, SelfArchiveError, vault_dir=vault) as update:
        backup = update.backup(safe_note_path(vault, vault_layout.SELF_MENTOR_PORTRAIT_HISTORY),
                               f"画像-{_stamp()}")
        content = _entry_text(metadata, clean)
        update.write(content)
        result = _portrait_result(content.encode("utf-8"))
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
    "context_text", "overview", "recover_incomplete_operations", "recovery_status",
    "OPS_RELATIVE",
]
