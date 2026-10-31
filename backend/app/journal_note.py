"""今天的笔记：日记文件化（Vault 的 `00_人生路/01_日记/YYYY-MM-DD.md`，文件为准）。

- 目录按四库约定：日记在 `00_人生路/01_日记/`，模板在 `00_人生路/01_日记/_模板/`；
- 2026-10-30 起，**今天**这一篇可以在应用内全屏编辑器里直接写（走 `/api/journal/write`）；
  过去的日记保持只读，回 Obsidian 改；
- `read_note` 同时返回原始字节的 `sha256`，供编辑器保存前做冲突校验；
- 2026-10-28 起日记正文由 `journal_review.compose()` 生成；`render_skeleton` 只作兜底薄骨架，
  `note_abspath` 仍供读取、盘点与横幅统计复用。
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import pillar_notes, vault_layout
from .db import Database
from .journal_shared import validate_day
from .vault import managed_note_path
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

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
        raw = path.read_bytes()
        # 保持旧 read_text() 的通用换行行为，API 仍返回 \n；sha 用原始字节，供保存冲突校验。
        content = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    except FileNotFoundError:
        return {**meta, "exists": False, "content": "", "sha256": "", "updated_at": None}
    except OSError as exc:
        raise HTTPException(500, f"无法读取今天的笔记：{exc}") from exc
    updated = datetime.fromtimestamp(path.stat().st_mtime, SHANGHAI).isoformat(timespec="seconds")
    return {
        **meta, "exists": True, "content": content,
        # 应用内日记编辑器用它做保存前冲突校验；与 /api/journal/write 返回的 sha 同协议。
        "sha256": hashlib.sha256(raw).hexdigest(),
        "updated_at": updated,
    }


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


_HEAT_CN = re.compile(r"[\u4e00-\u9fff]")
_HEAT_WORD = re.compile(r"[A-Za-z0-9_]+")
_HEAT_DATE_HEADING = re.compile(r"^#\s+\d{4}[-.]\d{2}[-.]\d{2}(.*)$")
HEATMAP_WEEKS = 26


def journal_word_count(text: str) -> int:
    """与前端 editor-model.wordCount 同口径：汉字算 1 字，英文/数字按词。"""
    value = str(text or "")
    chinese = len(_HEAT_CN.findall(value))
    words = len(_HEAT_WORD.findall(_HEAT_CN.sub(" ", value)))
    return chinese + words


def _journal_heat_words(content: str) -> int:
    """日记正文的实际字数；只有当天日期骨架标题的薄模板不算写过。"""
    body = _strip_front_matter(str(content or ""))
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return 0
    if len(lines) == 1:
        match = _HEAT_DATE_HEADING.match(lines[0])
        if match:
            rest = match.group(1).strip()
            if not rest or re.match(r"^[（(]第\d+周", rest):
                return 0
    return journal_word_count(body)


def journal_has_content(content: str) -> bool:
    """正文有实际内容（只有日期骨架标题的薄模板返回 False）。"""
    return _journal_heat_words(content) > 0


def journal_heatmap(vault_dir: Path | str, today: date | str,
                    weeks: int = HEATMAP_WEEKS) -> dict[str, Any]:
    """最近 N 周（含本周，周一起）的写作热力图数据；只返回有正文的日子。

    与前台「今日日记」一样以 Markdown 文件为准；不返回正文，只返回每天的字数
    和一个可打开的路径。薄模板（只有 `# 2026.10.31（第N周…）`）不计入。
    """
    if isinstance(today, datetime):
        current = today.date()
    elif isinstance(today, date):
        current = today
    else:
        current = date.fromisoformat(str(today))
    span = max(1, min(53, int(weeks or HEATMAP_WEEKS)))
    start = current - timedelta(days=current.weekday() + 7 * (span - 1))
    days: dict[str, dict[str, Any]] = {}
    for offset in range(span * 7):
        day = start + timedelta(days=offset)
        if day > current:
            break
        stamp = day.isoformat()
        try:
            entry = read_note(vault_dir, stamp)
        except Exception:
            # 单篇日记读不了不该拖垮整个首页；它最多缺席一格。
            continue
        if not entry.get("exists"):
            continue
        words = _journal_heat_words(str(entry.get("content") or ""))
        if words <= 0:
            continue
        relpath = str(entry.get("relpath") or "")
        standard = relpath.startswith("00_人生路/")
        days[stamp] = {
            "entries": 1,
            "words": words,
            "path": relpath if standard else "",
            "legacy": bool(relpath) and not standard,
        }
    return {
        "start": start.isoformat(),
        "weeks": span,
        "today": current.isoformat(),
        "first_weekday": 1,
        "written_days": len(days),
        "days": days,
    }


def delete_note(database: Database, vault_dir: Path | str, *, path: str,
                expected_revision: str = "", read_only: bool = False) -> dict[str, Any]:
    """软删除一篇日记：只接受四库日记目录里的 `YYYY-MM-DD.md`。

    过去的日记在应用内只读，但仍允许在阅读器里删除；文件移进
    `.torch-os/笔记回收站`，保留原相对路径，之后可在文件管理器里找回。
    """
    relative = vault_layout.normalize_relpath(path)
    match = re.fullmatch(r"00_人生路/01_日记/(\d{4}-\d{2}-\d{2})\.md", relative)
    if not match:
        raise HTTPException(422, "只能删除四库日记目录里的单篇日记")
    validate_day(match.group(1))
    try:
        ensure_vault_writable(read_only, "删除日记")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    result = pillar_notes.trash_note(
        vault_dir, relative, expected_revision=expected_revision)
    database.audit("journal_deleted", {
        "path": relative, "trash": result.get("trash_path", ""),
    })
    return result


class JournalDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1, max_length=2048)
    expected_revision: str = Field(default="", max_length=64)


def create_journal_note_router(
    database: Database, vault_dir: Path, prefix: str = "/api", read_only: bool = False,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["journal"])

    @router.get("/journal/note")
    def get_note(day: str = Query()):
        validate_day(day)
        return read_note(vault_dir, day)

    @router.post("/journal/delete")
    def delete(payload: JournalDeleteRequest):
        return delete_note(
            database, vault_dir, path=payload.path,
            expected_revision=payload.expected_revision, read_only=read_only,
        )

    return router