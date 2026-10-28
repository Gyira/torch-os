"""今天的笔记：日记文件化（Vault 的 `00_人生路/01_日记/YYYY-MM-DD.md`，文件为准）。

设计见 `docs/薪火OS-重设计方案-v2.0.md`（用户定案：不做内置编辑器，Obsidian 就是编辑器）：

- 目录按四库约定：日记在 `00_人生路/01_日记/`，模板在 `00_人生路/01_日记/_模板/`；
- 四库从 2026-10-26 起只读：本模块只读取当天笔记，新建/追加都在 Obsidian 里完成；
- 2026-10-28 起日记正文由 `journal_review.compose()` 生成；`render_skeleton` 只作兜底薄骨架，
  `note_abspath` 仍供读取、盘点与横幅统计复用。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from . import vault_layout
from .db import Database
from .journal_shared import validate_day
from .vault import managed_note_path

SHANGHAI = ZoneInfo("Asia/Shanghai")
JOURNAL_DIR = Path("00_人生路", "01_日记")
TEMPLATE_REL = Path("00_人生路", "01_日记", "_模板", "日记模板（journal-template）.md")
TEMPLATE_LEGACY_REL = Path("00_人生路", "01_日记", "_模板", "日记模板.md")
TEMPLATE_OLD_SYSTEM_REL = Path("00-System", "templates", "日记模板（journal-template）.md")
TEMPLATE_OLD_SYSTEM_LEGACY_REL = Path("00-System", "templates", "日记模板.md")
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 薄骨架：只有标题。日记正文由 `journal_review.compose()` 生成——**模板是提问清单，不是文档骨架**，
# 所以这里不再预置「反思/每日三问/____」这些段落，避免再长出空壳（2026-10-28）。
DEFAULT_TEMPLATE = """# {{date_dots}}（第{{week}}周，{{weekday}}）
"""


def _relative(day: str) -> Path:
    return JOURNAL_DIR / f"{day}.md"


def note_abspath(vault_dir: Path | str, day: str) -> Path:
    return managed_note_path(
        vault_dir, vault_layout.journal_path(day),
        vault_layout.legacy_journal_path(day),
    )


def read_note(vault_dir: Path | str, day: str) -> dict[str, Any]:
    """Read the day's note. Missing file is a normal state, not an error."""
    path = note_abspath(vault_dir, day)
    try:
        relpath = path.relative_to(Path(vault_dir).resolve()).as_posix()
    except ValueError:
        relpath = _relative(day).as_posix()
    meta = {
        "day": day,
        "relpath": relpath,
        "path": str(path),
        "vault_name": Path(vault_dir).name,
    }
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {**meta, "exists": False, "content": "", "updated_at": None}
    except OSError as exc:
        raise HTTPException(500, f"无法读取今天的笔记：{exc}") from exc
    updated = datetime.fromtimestamp(path.stat().st_mtime, SHANGHAI).isoformat(timespec="seconds")
    return {**meta, "exists": True, "content": content, "updated_at": updated}


def next_action_text(content: str) -> str:
    """从日记正文提取「下一步」；没有就返回空串。"""
    body = _strip_front_matter(str(content or ""))
    match = re.search(r"(?m)^\s*(?:[-*]\s*)?下一步[:：]\s*(.+?)\s*$", body)
    if match:
        return match.group(1).strip()
    section = re.search(r"(?ms)^#{1,6}\s*下一步[^\n]*\n+(.+?)(?:\n#{1,6}\s|\Z)", body)
    if section:
        lines = [line.strip("-* \t") for line in section.group(1).splitlines() if line.strip()]
        return lines[0] if lines else ""
    return ""


def _strip_front_matter(text: str) -> str:
    """模板文件可能自带 front matter（它自己的元数据）——笔记不继承，统一用 journal 的。"""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].lstrip("\n")
    return text


def render_skeleton(vault_dir: Path | str, day: str) -> str:
    """模板文件优先（{{date}}/{{weekday}} 占位替换），缺失时用内置骨架；
    模板自己的 front matter 一律剥掉，笔记固定用 ``type: journal`` + ``date``。"""
    template = DEFAULT_TEMPLATE
    for primary, legacy in (
        (TEMPLATE_REL, TEMPLATE_LEGACY_REL),
        (TEMPLATE_OLD_SYSTEM_REL, TEMPLATE_OLD_SYSTEM_LEGACY_REL),
    ):
        try:
            template = managed_note_path(
                vault_dir, primary.as_posix(), legacy.as_posix()
            ).read_text(encoding="utf-8")
            break
        except OSError:
            continue
    parsed = date.fromisoformat(day)
    weekday = WEEKDAYS[parsed.weekday()]
    body = _strip_front_matter(template).strip("\n")
    filled = (body.replace("{{date}}", day)
                  .replace("{{date_dots}}", day.replace("-", "."))
                  .replace("{{week}}", str(parsed.isocalendar()[1]))
                  .replace("{{weekday}}", weekday))
    return f"---\ntype: journal\ndate: {day}\n---\n\n{filled}\n"


def create_journal_note_router(
    database: Database, vault_dir: Path, prefix: str = "/api", read_only: bool = False,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["journal"])

    @router.get("/journal/note")
    def get_note(day: str = Query()):
        validate_day(day)
        return read_note(vault_dir, day)

    return router
