"""Conservative, read-only reference audit. Never rewrites notes or opens databases."""
from __future__ import annotations

import os
import posixpath
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

from .storage_layout import checked_path
from .vault_layout import PILLAR_DIRS
from .vault_links import scan_links

PROTECTED = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1[^\n]*$|`[^`\n]*`|<!--.*?-->")


def audit_references(source):
    root = checked_path(source)
    if not root.is_dir():
        raise ValueError("源库不存在")
    inventory, blocked = set(), []
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs):
            path = Path(directory) / name
            if path.is_symlink() or path.is_junction():
                dirs.remove(name)
                blocked.append({"path": path.relative_to(root).as_posix(), "reason": "linked_directory"})
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                blocked.append({"path": relative, "reason": "linked_file"})
            else:
                inventory.add(relative)
    basenames = defaultdict(list)
    for relative in sorted(inventory):
        basenames[Path(relative).name].append(relative)
        if relative.endswith(".md"):
            basenames[Path(relative).stem].append(relative)
    results, scanned = [], 0
    for relative in sorted(inventory):
        parts = relative.split("/")
        if parts[0] not in PILLAR_DIRS.values() or any(p.startswith(".") or p == "_系统" for p in parts):
            continue
        if relative.endswith(".canvas"):
            blocked.append({"path": relative, "reason": "canvas_requires_separate_audit"})
            continue
        if not relative.endswith(".md"):
            continue
        path = root / relative
        if path.stat().st_size > 1048576:
            blocked.append({"path": relative, "reason": "note_exceeds_budget"})
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (UnicodeError, OSError):
            blocked.append({"path": relative, "reason": "unreadable_note"})
            continue
        scanned += 1
        visible = PROTECTED.sub("", text)
        for link in scan_links(visible, relative):
            target = unquote(link["target"])
            if not target:  # anchors, or targets not understood by the conservative parser
                continue
            candidates = set()
            choices = [target]
            if link["kind"] == "wiki":
                choices.append(posixpath.normpath(posixpath.join(posixpath.dirname(relative), target)))
            for choice in choices:
                for key in (choice, choice + ".md"):
                    if key in inventory:
                        candidates.add(key)
            if not candidates and link["kind"] == "wiki" and "/" not in target:
                candidates.update(basenames.get(target, []))
            matches = sorted(candidates)
            status = "resolved" if len(matches) == 1 else "ambiguous" if matches else "unresolved"
            results.append({"source": relative, "target": target, "kind": link["kind"],
                            "status": status, "matches": matches})
    return {"dry_run": True, "scanned_notes": scanned, "references": results, "blocked": blocked,
            "safe_to_migrate": False,
            "limitations": ["不输出正文，不改文件，不读取业务数据库。", "仅覆盖普通 Markdown/wiki 链接；HTML、引用式链接、复杂括号、Canvas 和数据库路径需另行核对。",
                            "已解析到原件不代表该原件可以迁出；必须与候选拆分目标交叉核对。"]}
