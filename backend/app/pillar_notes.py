"""四库笔记的读写（2026-10-21）。

所有写入都走 markdown_store 的乐观锁协议；路径只允许落在四库章节内。
新笔记统一带 id/type/title/created/pillar/chapter/status，旧笔记缺字段也能读。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from fastapi import HTTPException

from . import vault_layout, vault_links
from .db import utc_now
from .markdown_store import guarded_write, load_note
from .vault import clean_fragment, parse_text, safe_note_path

MAX_BODY = 200_000


def _relative(vault: Path, path: Path) -> str:
    return path.resolve().relative_to(vault.resolve()).as_posix()


def _clean_tags(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        name = str(value or "").strip().lstrip("#")
        if name and name not in result:
            result.append(name[:32])
    return result[:24]


def _metadata(relative: str, metadata: dict[str, Any], path: Path) -> dict[str, Any]:
    pillar = vault_layout.pillar_of(relative)
    chapter = vault_layout.chapter_key_of(relative)
    return {
        "path": relative,
        "pillar": str(metadata.get("pillar") or pillar),
        "chapter": str(metadata.get("chapter") or chapter),
        "type": str(metadata.get("type") or vault_layout.chapter_default_type(chapter)),
        "title": str(metadata.get("title") or path.stem),
        "id": str(metadata.get("id") or ""),
        "date": str(metadata.get("date") or metadata.get("created") or ""),
        "status": str(metadata.get("status") or ("inbox" if chapter == "inbox" else "active")),
        "tags": [str(item) for item in (metadata.get("tags") or []) if str(item).strip()],
        "source": metadata.get("source") or "",
        "updated": str(metadata.get("updated") or ""),
        "aliases": [str(item) for item in (metadata.get("aliases") or []) if str(item).strip()],
    }


def list_notes(vault_dir: Path | str, *, pillar: str = "", chapter: str = "",
               query: str = "", limit: int = 100) -> list[dict[str, Any]]:
    vault = Path(vault_dir).resolve()
    roots: list[Path] = []
    if chapter:
        directory = vault_layout.chapter_dir(chapter)
        if not directory:
            raise HTTPException(422, "未知章节")
        roots = [vault / directory]
    elif pillar:
        directory = vault_layout.PILLAR_DIRS.get(pillar)
        if not directory:
            raise HTTPException(422, "未知库")
        roots = [vault / directory]
    else:
        roots = [vault / directory for directory in vault_layout.PILLAR_DIRS.values()]
    text = str(query or "").strip().casefold()
    found: list[dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            if any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            relative = _relative(vault, path)
            if not relative or vault_layout.is_skipped(relative):
                continue
            try:
                loaded = load_note(path)
                metadata, body = parse_text(
                    loaded["raw"].decode("utf-8-sig") if loaded["raw"] else "")
            except Exception:
                continue
            item = _metadata(relative, metadata, path)
            if text and text not in item["title"].casefold() and text not in body.casefold():
                continue
            item["excerpt"] = clean_fragment(body)[:160]
            item["mtime"] = path.stat().st_mtime
            found.append(item)
    found.sort(key=lambda item: (item.get("date") or "", item["mtime"]), reverse=True)
    return found[:max(1, min(500, limit))]


def read_note(vault_dir: Path | str, relative: str) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    vault_layout.validate_target(relative)
    path = safe_note_path(vault, relative)
    loaded = load_note(path)
    if not loaded["exists"]:
        raise HTTPException(404, "笔记不存在")
    metadata, body = parse_text(loaded["raw"].decode("utf-8-sig"))
    item = _metadata(relative, metadata, path)
    item.update({
        "body": body,
        "revision": loaded["revision"],
        "raw_metadata": {key: value for key, value in metadata.items()},
        "mtime": path.stat().st_mtime,
    })
    return item


def _unique_path(directory: Path, stem: str) -> Path:
    candidate = directory / f"{stem}.md"
    index = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{index}.md"
        index += 1
    return candidate


def create_note(vault_dir: Path | str, *, pillar: str = "knowledge", chapter: str = "inbox",
                title: str, body: str = "", note_type: str = "", tags: list[str] | None = None,
                source: str = "", date: str = "") -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    directory = vault_layout.chapter_dir(chapter)
    if not directory or vault_layout.pillar_of(directory) != pillar:
        raise HTTPException(422, "章节与库不匹配")
    clean_title = str(title or "").strip()
    if not clean_title:
        raise HTTPException(422, "标题不能为空")
    if len(clean_title) > 200:
        raise HTTPException(422, "标题不能超过 200 字")
    text = str(body or "").strip()
    if len(text.encode("utf-8")) > MAX_BODY:
        raise HTTPException(422, "正文过大")
    target_dir = vault / directory
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = vault_layout.safe_note_stem(clean_title)
    path = _unique_path(target_dir, stem)
    timestamp = utc_now()
    metadata: dict[str, Any] = {
        "id": uuid4().hex,
        "type": note_type or vault_layout.chapter_default_type(chapter),
        "title": clean_title,
        "created": timestamp,
        "updated": timestamp,
        "pillar": pillar,
        "chapter": chapter,
        "status": "inbox" if chapter == "inbox" else "active",
    }
    if date:
        metadata["date"] = date
    if tags:
        metadata["tags"] = _clean_tags(tags)
    if source:
        metadata["source"] = source
    lines = ["---", *yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip("\n").split("\n"),
             "---", "", *text.split("\n"), ""]
    path.write_text("\n".join(lines), encoding="utf-8", newline="")
    relative = _relative(vault, path)
    return read_note(vault, relative)


def update_note(vault_dir: Path | str, relative: str, *, body: str | None = None,
                title: str = "", tags: list[str] | None = None,
                status: str = "") -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    vault_layout.validate_target(relative)
    path = safe_note_path(vault, relative)
    loaded = load_note(path)
    if not loaded["exists"]:
        raise HTTPException(404, "笔记不存在")
    metadata, current_body = parse_text(loaded["raw"].decode("utf-8-sig"))
    if title:
        metadata["title"] = str(title).strip()[:200]
    if tags is not None:
        metadata["tags"] = _clean_tags(tags)
    if status:
        metadata["status"] = str(status)[:32]
    metadata["updated"] = utc_now()
    text = current_body if body is None else str(body)
    lines = ["---", *yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip("\n").split("\n"),
             "---", "", *text.split("\n"), ""]
    guarded_write(loaded, lines)
    return read_note(vault, relative)


def move_note(vault_dir: Path | str, relative: str, target_chapter: str) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    vault_layout.validate_target(relative)
    directory = vault_layout.chapter_dir(target_chapter)
    if not directory:
        raise HTTPException(422, "未知章节")
    source = safe_note_path(vault, relative)
    if not source.is_file():
        raise HTTPException(404, "笔记不存在")
    target_dir = vault / directory
    target_dir.mkdir(parents=True, exist_ok=True)
    target = _unique_path(target_dir, source.stem)
    os.replace(source, target)
    new_relative = _relative(vault, target)
    loaded = load_note(target)
    metadata, body = parse_text(loaded["raw"].decode("utf-8-sig"))
    metadata["pillar"] = vault_layout.pillar_of(new_relative)
    metadata["chapter"] = target_chapter
    metadata["updated"] = utc_now()
    lines = ["---", *yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip("\n").split("\n"),
             "---", "", *body.split("\n")]
    guarded_write(loaded, lines)
    return read_note(vault, new_relative)


def backlinks(vault_dir: Path | str, target: str, limit: int = 50) -> list[dict[str, Any]]:
    """在四库笔记里找指向 target 的链接（wiki 全路径/文件名、相对 Markdown 链接）。"""
    vault = Path(vault_dir).resolve()
    wanted = vault_layout.normalize_relpath(target).removesuffix(".md")
    wanted_name = wanted.rsplit("/", 1)[-1]
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for directory in vault_layout.PILLAR_DIRS.values():
        root = vault / directory
        if not root.is_dir():
            continue
        for path in root.rglob("*.md"):
            if any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            relative = _relative(vault, path)
            if relative == target or relative in seen:
                continue
            try:
                loaded = load_note(path)
                metadata, body = parse_text(loaded["raw"].decode("utf-8-sig"))
            except Exception:
                continue
            for link in vault_links.scan_links(body, relative):
                candidate = vault_layout.normalize_relpath(link["target"]).removesuffix(".md")
                candidate_name = candidate.rsplit("/", 1)[-1]
                if candidate == wanted or candidate_name == wanted_name:
                    seen.add(relative)
                    found.append({
                        "path": relative,
                        "title": str(metadata.get("title") or path.stem),
                        "pillar": vault_layout.pillar_of(relative),
                        "chapter": vault_layout.chapter_key_of(relative),
                    })
                    break
            if len(found) >= limit:
                return found
    return found


__all__ = ["list_notes", "read_note", "create_note", "update_note", "move_note", "backlinks"]
