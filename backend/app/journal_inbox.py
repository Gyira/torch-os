"""一个输入口：写一段话，判成日记 / 反思 / 心得，确认后各归各位。

对应 `docs/人生路重设计-v3-2026-10-28.md` 第 3、4 节。三条硬规矩：

- **规则先行**：判类不依赖模型，离线可用；模型以后只做边界兜底；
- **分类可改**：这里只给「建议」，落盘要前端带 `confirm`；
- **只写人生路**：日记进当天文件，反思/心得在人生路章节里新建，不碰其他三库。

任务暂不判类——等任务搬进 `.torch` 之后再加，避免出现两套任务。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import journal_review, vault_layout
from .journal_shared import validate_day
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SHANGHAI = ZoneInfo("Asia/Shanghai")
BACKUP_REL = Path(".torch-os", "backups", "inbox")
MAX_TEXT = 8000
MAX_CARDS = 3

DIARY_MARKS = (
    "今天", "上午", "下午", "中午", "晚上", "凌晨", "刚才", "刚刚", "完成", "做了",
    "去了", "见了", "吃了", "开了", "改完", "写完", "读完", "动了", "跑了", "上了",
)
REFLECTION_MARKS = (
    "不该", "下次", "其实", "发现", "教训", "后悔", "改进", "不够好", "不够",
    "应该", "反思", "复盘", "问题在于", "没做到", "做得不好", "硬扛",
)
INSIGHT_MARKS = (
    "明白", "原来", "意识到", "学到", "想通", "体会", "感悟", "懂了", "搞懂", "看清",
)
KIND_LABELS = {"diary": "日记", "reflection": "反思", "insight": "心得"}
KIND_CHAPTER = {"reflection": "reflection", "insight": "insight_life"}
KIND_TYPE = {"reflection": "reflection", "insight": "insight"}


def shanghai_today() -> date:
    return datetime.now(SHANGHAI).date()


def _clean_day(day: str | None) -> str:
    if not day:
        return shanghai_today().isoformat()
    return validate_day(day).isoformat()


def _split_units(text: str) -> list[str]:
    """按句读切，保留句子本身；空行也切。"""
    pieces = re.split(r"(?<=[。！？!?；;])|\n+", str(text or ""))
    return [piece.strip() for piece in pieces if piece and piece.strip()]


def _score(unit: str) -> tuple[str, list[str]]:
    marks = {
        "diary": [mark for mark in DIARY_MARKS if mark in unit],
        "reflection": [mark for mark in REFLECTION_MARKS if mark in unit],
        "insight": [mark for mark in INSIGHT_MARKS if mark in unit],
    }
    order = {"reflection": 3, "insight": 2, "diary": 1}
    best = max(marks, key=lambda kind: (len(marks[kind]), order[kind]))
    return (best, marks[best]) if marks[best] else ("", [])


def _scalar(value: str) -> str:
    """YAML 能裸写就裸写；含冒号/井号/特殊开头时加引号。"""
    text = str(value)
    plain = (
        bool(text)
        and text == text.strip()
        and ": " not in text
        and " #" not in text
        and not text.startswith(("-", "?", ":", "[", "{", "&", "*", "!", "|", ">", "@", "`"))
    )
    return text if plain else json.dumps(text, ensure_ascii=False)


def _title(text: str, limit: int = 18) -> str:
    head = re.split(r"[。！？!?；;，,]", text.strip())[0]
    title = (head or text)[:limit]
    for bad in '\\/:*?"<>|':
        title = title.replace(bad, "")
    return title.strip() or "未命名"


def classify(text: str, day: str) -> list[dict[str, Any]]:
    """一段话 → 最多 3 张卡。混着写会拆开，相邻同类会合并。"""
    units = _split_units(text)[:60]
    if not units:
        return []
    cards: list[dict[str, Any]] = []
    for unit in units:
        kind, marks = _score(unit)
        if not kind:
            kind = cards[-1]["kind"] if cards else "diary"
        if cards and cards[-1]["kind"] == kind and len(unit) + len(cards[-1]["body"]) < 600:
            cards[-1]["body"] = f"{cards[-1]['body']}{unit}"
            cards[-1]["marks"].extend(marks)
            continue
        cards.append({"kind": kind, "body": unit, "marks": list(marks)})
    del cards[MAX_CARDS:]

    result = []
    for card in cards:
        kind = card["kind"]
        result.append({
            "kind": kind,
            "label": KIND_LABELS[kind],
            "title": "" if kind == "diary" else _title(card["body"]),
            "body": card["body"].strip(),
            "confidence": round(min(0.9, 0.5 + 0.12 * len(card["marks"])), 2),
            "reason": "、".join(dict.fromkeys(card["marks"])) or "没命中关键词，按日记处理",
            "target": _target(kind, day),
        })
    return result


def _target(kind: str, day: str) -> str:
    if kind == "diary":
        return vault_layout.journal_path(day)
    return vault_layout.chapter_dir(KIND_CHAPTER[kind])


# ── 落盘 ──────────────────────────────────────────────────────────────────

def _backup(vault_dir: Path | str, path: Path) -> str:
    folder = Path(vault_dir) / BACKUP_REL
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(SHANGHAI).strftime("%Y%m%dT%H%M%S")
    target = folder / f"{path.stem}-{stamp}.md"
    counter = 1
    while target.exists():
        target = folder / f"{path.stem}-{stamp}-{counter}.md"
        counter += 1
    target.write_bytes(path.read_bytes())
    return str(target)


def _atomic_write(path: Path, text: str) -> bytes:
    payload = (text.rstrip("\n") + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "wb", delete=False, dir=str(path.parent), prefix=".inbox-", suffix=".md")
    try:
        handle.write(payload)
        handle.close()
        os.replace(handle.name, path)
    except OSError:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return payload


def _append_diary_section(content: str, text: str) -> str:
    """把这段话并进「## 我想说的」；没有这一节就补一个。"""
    lines = content.rstrip("\n").splitlines()
    start = next((index for index, line in enumerate(lines)
                  if re.match(r"^##\s+我想说的\s*$", line)), -1)
    if start < 0:
        lines += ["", "## 我想说的", "", text.strip()]
        return "\n".join(lines)
    end = next((index for index in range(start + 1, len(lines))
                if re.match(r"^##\s+", lines[index])), len(lines))
    body = [line for line in lines[start + 1:end] if line.strip()]
    body.append(text.strip())
    return "\n".join(lines[:start + 1] + [""] + body + [""] + lines[end:])


def save_cards(database: Any, vault_dir: Path | str, day: str, cards: list[dict[str, Any]], *,
               confirm: bool = False, read_only: bool = False) -> dict[str, Any]:
    if not confirm:
        raise HTTPException(400, "落盘需要 confirm=true")
    target_day = validate_day(day)
    if target_day != shanghai_today():
        raise HTTPException(409, "只能写今天的内容；过去的请在 Obsidian 里补。")
    try:
        ensure_vault_writable(read_only, "写下今天的内容")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    if not cards:
        raise HTTPException(400, "没有可写的内容")

    written: list[dict[str, Any]] = []
    diary_text = "\n".join(card["body"].strip() for card in cards if card["kind"] == "diary").strip()
    if diary_text:
        written.append(_save_diary(database, vault_dir, day, diary_text))
    for card in cards:
        kind = card.get("kind")
        if kind in KIND_CHAPTER:
            written.append(_save_note(vault_dir, day, kind, card))
        elif kind not in ("diary",) and kind not in KIND_CHAPTER:
            raise HTTPException(400, f"不认识的类型：{kind}")
    return {"day": day, "written": written}


def _save_diary(database: Any, vault_dir: Path | str, day: str, text: str) -> dict[str, Any]:
    path = Path(vault_dir) / vault_layout.journal_path(day)
    if path.is_file():
        original = path.read_text(encoding="utf-8", errors="replace")
        _backup(vault_dir, path)
        content = _append_diary_section(original, text)
        created = False
    else:
        digest = journal_review.build_digest(database, vault_dir, day)
        content = journal_review.compose(day, digest, free_text=text)
        created = True
    payload = _atomic_write(path, content)
    return {"kind": "diary", "path": vault_layout.journal_path(day),
            "created": created, "sha256": hashlib.sha256(payload).hexdigest()}


def _unique_path(folder: Path, stem: str) -> Path:
    path = folder / f"{stem}.md"
    counter = 2
    while path.exists():
        path = folder / f"{stem}-{counter}.md"
        counter += 1
    return path


def _save_note(vault_dir: Path | str, day: str, kind: str, card: dict[str, Any]) -> dict[str, Any]:
    chapter = KIND_CHAPTER[kind]
    folder = Path(vault_dir) / vault_layout.chapter_dir(chapter)
    title = (card.get("title") or "").strip() or _title(card.get("body") or "")
    body = (card.get("body") or "").strip()
    if not body:
        raise HTTPException(400, "内容为空")
    note_type = KIND_TYPE[kind]
    front = (
        "---\n"
        f"type: {note_type}\n"
        f"title: {_scalar(title)}\n"
        f"created: {day}\n"
        "来源: torch 一个输入口\n"
        "tags: [日记回流]\n"
        "---\n"
    )
    content = f"{front}\n> {title}\n\n{body}\n"
    path = _unique_path(folder, f"{day} {title}")
    payload = _atomic_write(path, content)
    return {"kind": kind, "path": path.resolve().relative_to(Path(vault_dir).resolve()).as_posix(),
            "created": True, "sha256": hashlib.sha256(payload).hexdigest()}


# ── API ───────────────────────────────────────────────────────────────────

class CardIn(BaseModel):
    # 前端会把 classify 返回的整张卡（label/confidence/target…）原样回传，多出来的字段直接忽略：
    # 落盘只认 kind/title/body，target 一律由服务端按类型重算，避免被客户端指定路径。
    model_config = ConfigDict(extra="ignore")
    kind: str = Field(max_length=12)
    title: str = Field(default="", max_length=80)
    body: str = Field(min_length=1, max_length=2000)


class ClassifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    day: str = ""


class SaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: str = ""
    cards: list[CardIn] = Field(min_length=1, max_length=MAX_CARDS)
    confirm: bool = False


def create_journal_inbox_router(database: Any, vault_dir: Path | str, prefix: str = "/api",
                                read_only: bool = False) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["journal-inbox"])

    @router.post("/journal/inbox/classify")
    def classify_text(payload: ClassifyRequest):
        target = _clean_day(payload.day)
        cards = classify(payload.text, target)
        return {"day": target, "cards": cards}

    @router.post("/journal/inbox/save")
    def save(payload: SaveRequest):
        target = _clean_day(payload.day)
        return save_cards(database, vault_dir, target,
                          [card.model_dump() for card in payload.cards],
                          confirm=payload.confirm, read_only=read_only)

    return router
