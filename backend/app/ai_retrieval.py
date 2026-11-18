"""Local lexical retrieval. No model calls, embeddings or private-data uploads."""
from __future__ import annotations

import os
import re
from pathlib import Path
import yaml

from . import vault_layout
from .role_policy import guard_note_read
from .vault import parse_text

_STOP = set("我想 想要 之前 现在 什么 怎么 如何 可以 这个 那个 我的 我们 你们 觉得 帮我 一下 还有 是否 为了 已经 但是 因为 所以".split())


def terms(query: str) -> set[str]:
    result = set(re.findall(r"[a-z0-9_]{2,}", str(query).casefold()[:2000]))
    for run in re.findall(r"[\u3400-\u9fff]+", str(query)[:2000]):
        result.update(run[i:i + 2] for i in range(len(run) - 1))
    return result - _STOP


def score(text: str, query: str) -> int:
    lower = text.casefold()
    return sum(len(term) for term in terms(query) if term in lower)


def excerpt(text: str, query: str, limit: int = 1000) -> str:
    """Take a window around the densest match, not always the document opening."""
    if len(text) <= limit:
        return text
    tokens = terms(query)
    lower = text.casefold()
    positions = {0}
    for token in tokens:
        start = 0
        for _ in range(20):
            position = lower.find(token, start)
            if position < 0:
                break
            positions.add(max(0, position - limit // 3))
            start = position + len(token)
    start = max(sorted(positions), key=lambda p: score(text[p:p + limit], query))
    return ("…" if start else "") + text[start:start + limit] + ("…" if start + limit < len(text) else "")


def related_notes(vault_dir: Path | str, query: str, *, max_files: int = 400,
                  max_bytes: int = 4_000_000) -> tuple[list[dict], bool]:
    """Bounded knowledge-only scan; authorize every file before opening it.

    The returned flag means coverage was limited, never that no other match exists.
    Symlinks, system/history directories and hidden chapters are pruned up front.
    """
    vault = Path(vault_dir).resolve()
    root = vault / vault_layout.PILLAR_DIRS["knowledge"]
    hidden = vault_layout.hidden_chapters(vault)
    found: list[dict] = []
    count = used = 0
    limited = False
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if not name.startswith(".")
                         and name not in {"_系统", "_备份", "backups", "logs"}
                         and not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            if name.startswith(".") or not name.lower().endswith(".md"):
                continue
            path = Path(directory) / name
            relative = path.relative_to(vault).as_posix()
            if vault_layout.is_skipped(relative) or vault_layout.chapter_key_of(relative) in hidden:
                continue
            guard_note_read(vault, relative)
            if count >= max_files or used >= max_bytes:
                return sorted(found, key=lambda row: (-row["score"], row["path"]))[:6], True
            count += 1
            try:
                with path.open("rb") as stream:
                    raw = stream.read(min(256_001, max_bytes - used + 1))
                used += len(raw)
                if len(raw) > 256_000 or used > max_bytes:
                    limited = True
                    continue
                metadata, body = parse_text(raw.decode("utf-8-sig"))
            except (OSError, UnicodeError, ValueError, yaml.YAMLError):
                limited = True
                continue
            if vault_layout.is_foreign(relative, metadata) or str(metadata.get("status")) in {"archived", "superseded"}:
                continue
            title = str(metadata.get("title") or path.stem)
            relevance = score(body, query) + 3 * score(title, query)
            if relevance:
                found.append({"title": title, "path": relative, "score": relevance,
                              "excerpt": excerpt(body, query), "date": str(metadata.get("date") or metadata.get("created") or "")})
    return sorted(found, key=lambda row: (-row["score"], row["path"]))[:6], limited
