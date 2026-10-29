"""身份候选：dsh 从对话里提炼的「关于我」，先落队列，交给忆灵复核。

分工写死在这里：

- dsh 只往 `<库>/.torch-os/身份待确认/` 投递 Markdown 卡片，不碰四库、不碰数据库；
- v1.0 P3 起，采纳候选**只写一篇记忆笔记**（`01_自我档案/02_记忆与自传`），
  不再直接写身份字段；忆灵综合时看到这篇笔记，才会提出待审身份补丁；
- 丢弃只是把卡片挪进 `已丢弃/`，四库一个字都不动。

身份只有两条写入路径：忆灵审核的补丁、苏格拉底 verdict 后的补丁。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from . import pillar_notes
from .identity import FIELD_LABELS, IdentityWrite, _identity_path, read_identity, write_identity
from .vault import parse_text
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

INBOX_DIR = ".torch-os/身份待确认"
ACCEPTED_DIR = f"{INBOX_DIR}/已采纳"
REJECTED_DIR = f"{INBOX_DIR}/已丢弃"
MAX_TEXT = 200


def inbox_dir(vault_dir: Path | str) -> Path:
    return Path(vault_dir) / INBOX_DIR


def _safe_name(name: str) -> str:
    text = str(name or "").strip()
    if not text.endswith(".md") or Path(text).name != text or text in {".", ".."}:
        raise ValueError("候选文件名不合法")
    return text


def read_draft(path: Path) -> dict[str, Any]:
    """一张候选卡：kind=field 指向身份字段，kind=memory 指向一篇记忆笔记。"""
    meta, _body = parse_text(path.read_text(encoding="utf-8", errors="replace"))
    kind = "field" if str(meta.get("kind") or "").strip() == "field" else "memory"
    field = str(meta.get("field") or "").strip()
    return {
        "name": path.name,
        "kind": kind,
        "field": field if field in FIELD_LABELS else "",
        "field_label": FIELD_LABELS.get(field, ""),
        "text": str(meta.get("text") or "").strip(),
        "evidence": str(meta.get("evidence") or "").strip(),
        "created": str(meta.get("created") or ""),
    }


def list_drafts(vault_dir: Path | str) -> list[dict[str, Any]]:
    directory = inbox_dir(vault_dir)
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        try:
            item = read_draft(path)
        except OSError:
            continue
        if item["text"]:
            items.append(item)
    items.sort(key=lambda item: item["created"], reverse=True)
    return items


def _archive(path: Path, target_dir: Path) -> str:
    """卡片归档（采纳/丢弃都保留，方便回头查是哪次对话说的）。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    index = 2
    while target.exists():
        target = target_dir / f"{path.stem}-{index}.md"
        index += 1
    shutil.move(str(path), str(target))
    return target.name


def _pending(vault_dir: Path | str, name: str) -> Path:
    path = inbox_dir(vault_dir) / _safe_name(name)
    if not path.is_file():
        raise LookupError("候选不存在，可能已经在别处处理过")
    return path


def accept_draft(vault_dir: Path | str, name: str, *, read_only: bool = False) -> dict[str, Any]:
    """采纳候选：只留一篇记忆笔记，不直接改身份；身份由忆灵/苏格拉底提议。"""
    ensure_vault_writable(read_only, "采纳身份候选")
    path = _pending(vault_dir, name)
    draft = read_draft(path)
    text = draft["text"][:MAX_TEXT]
    if not text:
        raise ValueError("候选内容为空")
    field = draft.get("field") or ""
    field_label = FIELD_LABELS.get(field, "")
    body = f"> {text}\n"
    if field_label:
        body += f"\n## 候选字段\n\n{field_label}\n"
    if draft["evidence"]:
        body += f"\n## 来自对话\n\n> {draft['evidence']}\n"
    note = pillar_notes.create_note(
        vault_dir, pillar="self", chapter="memory", title=text[:60], body=body,
        tags=["dsh", "身份候选"], source="dsh",
    )
    result = {
        "target": "memory", "field": field, "field_label": field_label,
        "path": note.get("path") or "", "title": note.get("title") or "",
    }
    archived = _archive(path, Path(vault_dir) / ACCEPTED_DIR)
    return {**result, "kind": draft["kind"], "text": text, "archived_as": archived}


def reject_draft(vault_dir: Path | str, name: str) -> dict[str, Any]:
    path = _pending(vault_dir, name)
    archived = _archive(path, Path(vault_dir) / REJECTED_DIR)
    return {"rejected": True, "name": name, "archived_as": archived}


__all__ = ["INBOX_DIR", "ACCEPTED_DIR", "REJECTED_DIR", "inbox_dir", "list_drafts",
           "read_draft", "accept_draft", "reject_draft"]
