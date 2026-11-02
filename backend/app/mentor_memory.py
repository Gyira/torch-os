"""人生导师的分层记忆（2026-11-01）。

三份 Markdown，全部在 ``01_自我档案/02_人生导师/记忆/``：

- ``核心记忆.md``   每次对话都注入的精简长期记忆（<=3000 字）；
- ``记忆时间线.md`` 按日期追加的事件/决定/关系/目标变化；
- ``问答记忆.md``   每日三问与对话追问的问答记录。

原始对话轮次仍存 SQLite；这里只放“值得长期记住”的结论，且都可在 Obsidian 编辑。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import yaml

from . import vault_layout
from .memory_store import DocumentUpdate, read_document, update_document
from .vault import parse_text, safe_note_path
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SHANGHAI = ZoneInfo("Asia/Shanghai")
CORE_TYPE = "mentor_core_memory"
TIMELINE_TYPE = "mentor_timeline"
QA_TYPE = "mentor_qa"
MAX_CORE_CHARS = 3_000
MAX_TIMELINE_TEXT = 300
MAX_QA_ANSWER = 800
TIMELINE_KINDS = ("observation", "decision", "relationship", "goal_change",
                  "correction", "state")
KIND_LABELS = {
    "observation": "观察",
    "decision": "决定",
    "relationship": "关系",
    "goal_change": "目标变化",
    "correction": "纠正",
    "state": "状态",
}


class MentorMemoryError(RuntimeError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")


def _today() -> str:
    return datetime.now(SHANGHAI).date().isoformat()


def _path(vault: Path, name: str) -> Path:
    # Only construct here; the shared reader/writer validates under its lock.
    return vault / vault_layout.SELF_MENTOR_MEMORY_DIR / name


def _ensure_write(read_only: bool, action: str) -> None:
    try:
        ensure_vault_writable(read_only, action)
    except VaultReadOnlyError as error:
        raise MentorMemoryError(str(error), 409) from error


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
    return result[:20]


def _read(path: Path, vault: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = read_document(path, vault_dir=vault)
    except OSError:
        return {}, ""
    try:
        return parse_text(raw.decode("utf-8-sig")) if raw is not None else ({}, "")
    except (ValueError, yaml.YAMLError):
        return {}, ""


def _write_doc(update: DocumentUpdate, metadata: Mapping[str, Any], body: str) -> None:
    lines = [
        "---",
        *yaml.safe_dump(dict(metadata), allow_unicode=True, sort_keys=False)
        .rstrip("\n").split("\n"),
        "---",
        "",
        str(body or "").strip(),
        "",
    ]
    update.write("\n".join(lines))


def _read_update(update: DocumentUpdate) -> tuple[dict[str, Any], str]:
    if update.original is None:
        return {}, ""
    try:
        return parse_text(update.original.decode("utf-8-sig"))
    except (UnicodeError, ValueError, yaml.YAMLError) as error:
        raise MentorMemoryError("记忆文件无法解析，本次未覆盖；请先修复原文件", 409) from error


def ensure_memory(vault_dir: Path | str, *, read_only: bool = False) -> None:
    _ensure_write(read_only, "初始化人生导师记忆")
    vault = Path(vault_dir).resolve()
    for name, kind, body in (
        ("核心记忆.md", CORE_TYPE, "# 核心记忆\n\n（还没有核心记忆；全量整理后由人生导师写入。）"),
        ("记忆时间线.md", TIMELINE_TYPE, "# 记忆时间线\n"),
        ("问答记忆.md", QA_TYPE, "# 问答记忆\n"),
    ):
        with update_document(_path(vault, name), MentorMemoryError, vault_dir=vault) as update:
            if update.original is None:
                metadata = {"type": kind, "updated_at": _now()}
                if kind == CORE_TYPE:
                    metadata["revision"] = 1
                _write_doc(update, metadata, body)


def read_core(vault_dir: Path | str) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    metadata, body = _read(_path(vault, "核心记忆.md"), vault)
    return {
        "path": f"{vault_layout.SELF_MENTOR_MEMORY_DIR}/核心记忆.md",
        "exists": bool(body),
        "text": body.strip(),
        "updated_at": str(metadata.get("updated_at") or ""),
    }


def write_core(vault_dir: Path | str, text: str, *, read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "更新人生导师核心记忆")
    clean = str(text or "").strip()
    if not clean:
        raise MentorMemoryError("核心记忆不能为空", 422)
    if len(clean) > MAX_CORE_CHARS:
        raise MentorMemoryError(f"核心记忆最多 {MAX_CORE_CHARS} 字", 422)
    vault = Path(vault_dir).resolve()
    path = _path(vault, "核心记忆.md")
    with update_document(path, MentorMemoryError, vault_dir=vault) as update:
        backup = update.backup(safe_note_path(vault, f"{vault_layout.SELF_MENTOR_HISTORY_DIR}/核心记忆"),
                               f"核心记忆-{_stamp()}")
        updated_at = _now()
        _write_doc(update, {"type": CORE_TYPE, "updated_at": updated_at}, clean)
        return {"path": f"{vault_layout.SELF_MENTOR_MEMORY_DIR}/核心记忆.md",
                "exists": True, "text": clean.replace("\r\n", "\n"), "updated_at": updated_at,
                "history_path": backup.relative_to(vault).as_posix() if backup else ""}


def _timeline_entries(body: str) -> list[dict[str, str]]:
    """解析记忆时间线：`## 日期` + `- [类型] 文本（来源：...）`。"""
    result: list[dict[str, str]] = []
    date = ""
    for line in str(body or "").splitlines():
        text = line.strip()
        if text.startswith("## "):
            date = text[3:].strip()
            continue
        if not text.startswith("- ["):
            continue
        try:
            kind_label, rest = text[3:].split("]", 1)
        except ValueError:
            continue
        kind = next((key for key, label in KIND_LABELS.items()
                     if label == kind_label.strip()), "observation")
        text = re.sub(r"（来源：[^）]*）\s*$", "", rest.strip()).strip()
        result.append({"date": date, "kind": kind, "text": text})
    return result


def append_timeline(vault_dir: Path | str, entries: Iterable[Mapping[str, Any]], *,
                    read_only: bool = False) -> list[dict[str, str]]:
    _ensure_write(read_only, "更新人生导师记忆时间线")
    vault = Path(vault_dir).resolve()
    path = _path(vault, "记忆时间线.md")
    with update_document(path, MentorMemoryError, vault_dir=vault) as update:
        metadata, body = _read_update(update)
        if not body.strip():
            body = "# 记忆时间线\n"
        existing = _timeline_entries(body)
        seen = {(item["date"], item["kind"], item["text"].casefold()) for item in existing}
        today = _today()
        added: list[dict[str, str]] = []
        lines = [body.rstrip()]
        for raw in entries:
            kind = str(raw.get("kind") or "observation").strip()
            if kind not in TIMELINE_KINDS:
                kind = "observation"
            text = _clean(raw.get("text"), MAX_TIMELINE_TEXT)
            if not text:
                continue
            sources = _sources(raw.get("sources"))
            line = f"- [{KIND_LABELS[kind]}] {text}"
            if sources:
                line += "（来源：" + "、".join(sources) + "）"
            key = (today, kind, text.casefold())
            if key in seen:
                continue
            seen.add(key)
            if f"## {today}" not in "\n".join(lines):
                lines.extend(["", f"## {today}"])
            lines.append(line)
            added.append({"date": today, "kind": kind, "text": text})
        if not added:
            return []
        _write_doc(update, {**metadata, "type": TIMELINE_TYPE, "updated_at": _now()},
                   "\n".join(lines).strip())
        return added


def _qa_block(identifier: str, *, question: str, answer: str, section: str,
              sources: list[str], entry_id: str) -> list[str]:
    block = [
        f"## {identifier}" + (f" · {section}" if section else ""),
        "",
        f"- 问题：{question}",
        f"- 回答：{answer}",
        f"- 日期：{_today()}",
    ]
    if sources:
        block.append("- 来源：" + "、".join(sources))
    block.append(f"- 已整理进档案：{entry_id or '否'}")
    return block


def _replace_qa_block(body: str, identifier: str, block: list[str]) -> str | None:
    """Replace the block with the same qid; returns None when it is absent."""
    lines = body.rstrip().splitlines()
    start = -1
    prefix = f"## {identifier}"
    for index, line in enumerate(lines):
        if line.strip() == prefix or line.strip().startswith(prefix + " ·"):
            start = index
            break
    if start < 0:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].strip().startswith("## "):
            end = index
            break
    known = ("- 问题：", "- 回答：", "- 日期：", "- 来源：", "- 已整理进档案：")
    extra = [line.strip() for line in lines[start + 1:end]
             if line.strip() and not line.strip().startswith(known)]
    merged = [*block[:-1], *extra, block[-1]]
    return "\n".join([*lines[:start], *merged, *lines[end:]]).rstrip()


def append_qa(vault_dir: Path | str, *, question: str, answer: str,
              qid: str = "", section: str = "", sources: Any = None,
              entry_id: str = "", read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "更新人生导师问答记忆")
    question = _clean(question, 500)
    answer = _clean(answer, MAX_QA_ANSWER)
    if not question or not answer:
        raise MentorMemoryError("问题和回答都不能为空", 422)
    vault = Path(vault_dir).resolve()
    path = _path(vault, "问答记忆.md")
    with update_document(path, MentorMemoryError, vault_dir=vault) as update:
        metadata, body = _read_update(update)
        if not body.strip():
            body = "# 问答记忆\n"
        identifier = str(qid or _stamp()).strip()
        block = _qa_block(identifier, question=question, answer=answer,
                          section=section, sources=_sources(sources),
                          entry_id=entry_id)
        replaced = _replace_qa_block(body, identifier, block)
        content = replaced if replaced is not None else body.rstrip() + "\n\n" + "\n".join(block)
        _write_doc(update, {**metadata, "type": QA_TYPE, "updated_at": _now()}, content)
        return {"qid": identifier, "section": section, "answer": answer,
                "path": f"{vault_layout.SELF_MENTOR_MEMORY_DIR}/问答记忆.md",
                "updated_existing": replaced is not None}


def link_qa_entry(vault_dir: Path | str, qid: str, entry_id: str, *,
                  read_only: bool = False) -> bool:
    """Best-effort: record the archive entry id on an already-saved answer."""
    _ensure_write(read_only, "关联人生导师问答记忆")
    identifier = str(qid or "").strip()
    if not identifier or not entry_id:
        return False
    vault = Path(vault_dir).resolve()
    path = _path(vault, "问答记忆.md")
    with update_document(path, MentorMemoryError, vault_dir=vault) as update:
        metadata, body = _read_update(update)
        if not body.strip():
            return False
        lines = body.rstrip().splitlines()
        prefix = f"## {identifier}"
        start = next((index for index, line in enumerate(lines)
                      if line.strip() == prefix or line.strip().startswith(prefix + " ·")), -1)
        if start < 0:
            return False
        end = next((index for index in range(start + 1, len(lines))
                    if lines[index].strip().startswith("## ")), len(lines))
        changed = False
        for index in range(start + 1, end):
            if lines[index].startswith("- 已整理进档案："):
                lines[index] = f"- 已整理进档案：{entry_id}"
                changed = True
                break
        if not changed:
            lines.insert(end, f"- 已整理进档案：{entry_id}")
        _write_doc(update, {**metadata, "type": QA_TYPE, "updated_at": _now()},
                   "\n".join(lines))
        return True


def read_timeline(vault_dir: Path | str, *, limit: int = 200) -> list[dict[str, str]]:
    vault = Path(vault_dir).resolve()
    _metadata, body = _read(_path(vault, "记忆时间线.md"), vault)
    return _timeline_entries(body)[-max(1, int(limit)):]


def read_qa(vault_dir: Path | str, *, max_chars: int = 8_000) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    metadata, body = _read(_path(vault, "问答记忆.md"), vault)
    text = body.strip()
    if len(text) > max_chars:
        text = "…" + text[-max_chars:]
    return {
        "path": f"{vault_layout.SELF_MENTOR_MEMORY_DIR}/问答记忆.md",
        "text": text,
        "updated_at": str(metadata.get("updated_at") or ""),
    }


def context_text(vault_dir: Path | str, *, max_chars: int = 13_000) -> str:
    """核心 + 最近时间线 + 最近问答；给 AI 上下文用。"""
    core = read_core(vault_dir)
    timeline = read_timeline(vault_dir, limit=60)
    vault = Path(vault_dir).resolve()
    _qa_meta, qa_body = _read(_path(vault, "问答记忆.md"), vault)
    blocks: list[str] = []
    if core["text"]:
        blocks.append("## 核心记忆\n\n" + core["text"][:3_000])
    if timeline:
        lines = ["## 记忆时间线（最近）"]
        for item in timeline[-60:]:
            lines.append(f"- {item['date']} [{KIND_LABELS.get(item['kind'], item['kind'])}] {item['text']}")
        blocks.append("\n".join(lines))
    qa = qa_body.strip()
    if qa:
        blocks.append("## 问答记忆（最近）\n\n" + qa[-6_000:])
    text = "\n\n".join(blocks).strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def overview(vault_dir: Path | str) -> dict[str, Any]:
    core = read_core(vault_dir)
    timeline = read_timeline(vault_dir, limit=200)
    return {
        "core": core,
        "timeline_count": len(timeline),
        "timeline_recent": timeline[-8:],
        "paths": {
            "core": f"{vault_layout.SELF_MENTOR_MEMORY_DIR}/核心记忆.md",
            "timeline": f"{vault_layout.SELF_MENTOR_MEMORY_DIR}/记忆时间线.md",
            "qa": f"{vault_layout.SELF_MENTOR_MEMORY_DIR}/问答记忆.md",
        },
    }


__all__ = [
    "MentorMemoryError", "KIND_LABELS", "ensure_memory", "read_core", "write_core",
    "append_timeline", "append_qa", "link_qa_entry", "read_timeline", "read_qa",
    "context_text", "overview",
]
