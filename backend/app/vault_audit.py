"""Read-only Vault audit: front matter, ids, bodies and encodings.

It never writes, never fixes and never reads restricted partitions (the same
``restricted_path`` rule the scanner uses). The report carries relative paths
and issue codes only — no note content.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from fastapi import APIRouter

from .vault import MAX_NOTE_BYTES, parse_text, restricted_path


SHANGHAI = ZoneInfo("Asia/Shanghai")
REQUIRED_FIELDS = ("id", "type", "title")
MAX_ISSUES = 200
ISSUE_CODES = (
    "too_large", "unreadable", "decode_failed", "invalid_frontmatter",
    "missing_frontmatter", "missing_fields", "duplicate_id", "empty_body",
)


def run_audit(vault_dir: Path) -> dict[str, Any]:
    root = Path(vault_dir)
    files: list[Path] = []
    if root.exists():
        for path in sorted(root.rglob("*.md")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if restricted_path(relative):
                continue
            files.append(path)

    counts = {code: 0 for code in ISSUE_CODES}
    issues: list[dict[str, Any]] = []
    parsed: list[tuple[str, dict[str, Any], str, bool]] = []

    for path in files:
        relative = path.relative_to(root).as_posix()
        def flag(code: str, detail: str) -> None:
            counts[code] += 1
            issues.append({"path": relative, "code": code, "detail": detail})

        try:
            if path.stat().st_size > MAX_NOTE_BYTES:
                flag("too_large", "超过 1 MiB 单篇上限")
                continue
            raw = path.read_bytes()
        except OSError as error:
            flag("unreadable", str(error))
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            flag("decode_failed", "不是 UTF-8 文本")
            continue
        has_frontmatter = text.replace("\r\n", "\n").split("\n", 1)[0].strip() == "---"
        try:
            metadata, body = parse_text(text)
        except (ValueError, yaml.YAMLError) as error:
            flag("invalid_frontmatter", str(error))
            continue
        parsed.append((relative, metadata, body, has_frontmatter))

    seen_ids: dict[str, str] = {}
    duplicate_first: set[str] = set()
    for relative, metadata, body, has_frontmatter in parsed:
        if not has_frontmatter:
            counts["missing_frontmatter"] += 1
            issues.append({"path": relative, "code": "missing_frontmatter", "detail": "文件开头没有 front-matter"})
        missing = [field for field in REQUIRED_FIELDS if not str(metadata.get(field) or "").strip()] if has_frontmatter else []
        if missing:
            counts["missing_fields"] += 1
            issues.append({"path": relative, "code": "missing_fields", "detail": "缺少 " + "、".join(missing)})
        memory_id = str(metadata.get("id") or "").strip()
        if memory_id:
            if memory_id in seen_ids:
                counts["duplicate_id"] += 1
                issues.append({"path": relative, "code": "duplicate_id", "detail": f"与 {seen_ids[memory_id]} 使用了相同 id"})
                if memory_id not in duplicate_first:
                    duplicate_first.add(memory_id)
                    counts["duplicate_id"] += 1
                    issues.append({"path": seen_ids[memory_id], "code": "duplicate_id", "detail": f"与 {relative} 使用了相同 id"})
            else:
                seen_ids[memory_id] = relative
        if not body.strip():
            counts["empty_body"] += 1
            issues.append({"path": relative, "code": "empty_body", "detail": "front-matter 之外没有正文"})

    bad_paths = {item["path"] for item in issues}
    return {
        "generated_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "checked_files": len(files),
        "ok_files": len(files) - len(bad_paths),
        "counts": counts,
        "issues": issues[:MAX_ISSUES],
        "truncated": max(0, len(issues) - MAX_ISSUES),
        "note": "只读报告：只包含相对路径与问题说明，不包含任何正文内容；体检不会修改或修复文件。",
    }


def create_vault_audit_router(vault_dir: Path, prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["vault-audit"])

    @router.get("/vault/audit")
    def audit():
        return run_audit(vault_dir)

    return router
