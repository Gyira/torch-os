"""Vault 内部链接扫描与迁移改写（2026-10-21）。

只处理 Obsidian wiki link 与相对 Markdown 链接；跳过代码围栏、行内代码、
HTML 注释、URL、锚点和外部协议。迁移时先移动文件，再用 old→new 映射统一改写，
因此改写函数同时接收源文件的旧路径与新路径，用来重算相对链接。
"""

from __future__ import annotations

import posixpath
import re
from typing import Any

_WIKI_LINK = re.compile(r"(!?)\[\[([^\]\n]+)\]\]")
_MD_LINK = re.compile(r"(!?\[[^\]\n]*\]\()([^)\s]+)([^)]*\))")
_PROTECTED = re.compile(
    r"(?ms)(?:^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1[^\n]*$)"
    r"|(?:`[^`\n]*`)"
    r"|(?:<!--.*?-->)"
)
_EXTERNAL = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|#|//)")


def _normalize(path: str) -> str:
    text = str(path or "").replace("\\", "/").strip().lstrip("/")
    if not text:
        return ""
    text = posixpath.normpath(text)
    return "" if text == "." or text.startswith("../") else text


def _lookup(mapping: dict[str, str], path: str) -> str | None:
    clean = _normalize(path)
    if not clean:
        return None
    for key in (clean, clean[:-3] if clean.endswith(".md") else clean + ".md"):
        if key in mapping:
            return mapping[key]
    return None


def _split_wiki(inner: str) -> tuple[str, str, str]:
    target, _, alias = inner.partition("|")
    path, has_heading, heading = target.partition("#")
    return path.strip(), heading if has_heading else "", alias


def _rewrite_wiki(segment: str, mapping: dict[str, str]) -> tuple[str, int]:
    changes = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changes
        bang, inner = match.group(1), match.group(2)
        path, heading, alias = _split_wiki(inner)
        new_path = _lookup(mapping, path)
        if new_path is None:
            return match.group(0)
        suffix = f"#{heading}" if heading else ""
        alias_part = f"|{alias}" if alias else ""
        changes += 1
        return f"{bang}[[{new_path}{suffix}{alias_part}]]"

    return _WIKI_LINK.sub(replace, segment), changes


def _rewrite_markdown(segment: str, mapping: dict[str, str],
                      source_old: str, source_new: str) -> tuple[str, int]:
    changes = 0
    old_dir = posixpath.dirname(_normalize(source_old))
    new_dir = posixpath.dirname(_normalize(source_new))

    def replace(match: re.Match[str]) -> str:
        nonlocal changes
        prefix, target, suffix = match.group(1), match.group(2), match.group(3)
        if _EXTERNAL.match(target):
            return match.group(0)
        path, hash_mark, anchor = target.partition("#")
        resolved = _normalize(posixpath.join(old_dir, path))
        new_path = _lookup(mapping, resolved)
        if new_path is None:
            return match.group(0)
        relative = posixpath.relpath(new_path, new_dir or ".")
        rewritten = relative + (f"#{anchor}" if hash_mark else "")
        changes += 1
        return f"{prefix}{rewritten}{suffix}"

    return _MD_LINK.sub(replace, segment), changes


def rewrite_links(text: str, mapping: dict[str, str],
                  source_old: str = "", source_new: str = "") -> tuple[str, list[dict[str, str]]]:
    """按 old→new 映射改写正文链接；返回新正文与变更列表。"""
    if not mapping:
        return text, []
    outputs: list[str] = []
    changes: list[dict[str, str]] = []
    cursor = 0
    for match in _PROTECTED.finditer(text):
        before = text[cursor:match.start()]
        rewritten, count = _rewrite_wiki(before, mapping)
        rewritten, md_count = _rewrite_markdown(
            rewritten, mapping, source_old, source_new)
        outputs.append(rewritten)
        if count + md_count:
            changes.append({"source": source_old, "links": str(count + md_count)})
        outputs.append(match.group(0))
        cursor = match.end()
    tail, count = _rewrite_wiki(text[cursor:], mapping)
    tail, md_count = _rewrite_markdown(tail, mapping, source_old, source_new)
    outputs.append(tail)
    if count + md_count:
        changes.append({"source": source_old, "links": str(count + md_count)})
    return "".join(outputs), changes


def scan_links(text: str, source: str) -> list[dict[str, Any]]:
    """列出正文中的内部链接（只读，用于迁移报告/健康检查）。"""
    found: list[dict[str, Any]] = []
    for match in _WIKI_LINK.finditer(text):
        path, _heading, _alias = _split_wiki(match.group(2))
        found.append({"kind": "wiki", "source": source, "target": path})
    old_dir = posixpath.dirname(_normalize(source))
    for match in _MD_LINK.finditer(text):
        target = match.group(2)
        if _EXTERNAL.match(target):
            continue
        path = target.partition("#")[0]
        found.append({"kind": "markdown", "source": source,
                      "target": _normalize(posixpath.join(old_dir, path))})
    return found


__all__ = ["rewrite_links", "scan_links"]
