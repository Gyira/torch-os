"""今天的笔记：日记文件化（Vault 的 `00_人生路/01_日记/YYYY-MM-DD.md`，文件为准）。

设计见 `docs/薪火OS-重设计方案-v2.0.md`（用户定案：不做内置编辑器，Obsidian 就是编辑器）：

- 目录按四库约定：日记在 `00_人生路/01_日记/`，模板在 `00-System/templates/`；
- 「一键生成」**只在文件不存在时落笔**：模板骨架（可用模板文件覆盖）＋当天事实汇总；
- 文件已存在＝**文件优先**：生成只返回可追加的汇总预览，追加必须再显式点一次；
- 只读安全模式的 Vault 一律拒绝写入；每次真正的写入只留一条不含正文的计数审计。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from . import vault_layout, workbench
from .activity import list_day_activity
from .db import Database, utc_now
from .journal_shared import validate_day
from .vault import managed_note_path

SHANGHAI = ZoneInfo("Asia/Shanghai")
JOURNAL_DIR = Path("00_人生路", "01_日记")
TEMPLATE_REL = Path("00-System", "templates", "日记模板（journal-template）.md")
TEMPLATE_LEGACY_REL = Path("00-System", "templates", "日记模板.md")
MAX_NOTE_BYTES = 200_000
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
STATE_LABELS = {"done": "已完成", "skipped": "主动休息", "unrecorded": "未记录"}

# 与 `00-System/templates/日记模板（journal-template）.md` 同形的内置骨架（Vault 里没有模板文件时的兜底）。
# 结构参照用户 wolai 的「2026.10.05（第41周，周一）」页，做了精简（去掉影视娱乐/卫生细格）。
DEFAULT_TEMPLATE = """---
type: journal
date: {{date}}
---

# {{date_dots}}（第{{week}}周，{{weekday}}）

> 每日三问：今天你学习了吗？今天你反思了吗？今天你保持卫生了吗？

**就寝**：____ 　**起床**：____ 　**天气**：____ 　**心情**：____ 　**地址**：____

## 今日格言
>

## 今日回顾

### 学习
-

### 工作
-

## 反思

### 学习反思
-

### 工作反思
-

### 饮食运动健康
- 饮食：____ 　运动：____

### 家庭与交际
-

### 其他反思
-

## 今日纠偏与改进（Act）

**今天取得了哪些进步？**
-

**还有哪些问题需要处理？**
-
"""


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    day: str
    mode: str = "create"  # create | append
    use_model: bool = False  # 显式勾选才把本地汇总发给已配置的模型润色


def _audit(database: Database, event_type: str, details: dict[str, Any]) -> None:
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO audit_events(event_type, detail_json, created_at) VALUES (?, ?, ?)",
            (event_type, json.dumps(details, ensure_ascii=False), utc_now()),
        )


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
    try:
        template = managed_note_path(
            vault_dir, TEMPLATE_REL.as_posix(), TEMPLATE_LEGACY_REL.as_posix()
        ).read_text(encoding="utf-8")
    except OSError:
        template = DEFAULT_TEMPLATE
    parsed = date.fromisoformat(day)
    weekday = WEEKDAYS[parsed.weekday()]
    body = _strip_front_matter(template).strip("\n")
    filled = (body.replace("{{date}}", day)
                  .replace("{{date_dots}}", day.replace("-", "."))
                  .replace("{{week}}", str(parsed.isocalendar()[1]))
                  .replace("{{weekday}}", weekday))
    return f"---\ntype: journal\ndate: {day}\n---\n\n{filled}\n"


def facts_summary(database: Database, vault_dir: Path | str, day: str) -> str:
    """当天事实的“自动汇总”块（只读汇总，不调用模型）。"""
    payload = workbench.collect_day(database, vault_dir, day)
    stamp = datetime.now(SHANGHAI).strftime("%H:%M")
    lines = [f"## 自动汇总 · {stamp}"]
    todos = payload.get("todos") or []
    done = [task["title"] for task in todos if task.get("completed")]
    open_tasks = [task["title"] for task in todos if not task.get("completed")]
    if todos:
        lines.append(f"- 待办：完成 {len(done)} / {len(todos)} 项")
        if done:
            lines.append("  - 完成：" + "、".join(done))
        if open_tasks:
            lines.append("  - 未完成：" + "、".join(open_tasks))
    daily = payload.get("daily") or []
    if daily:
        done_daily = [item["title"] for item in daily if item.get("done")]
        lines.append(f"- 每日必做：完成 {len(done_daily)} / {len(daily)}")
        if done_daily:
            lines.append("  - 完成：" + "、".join(done_daily))
    habits = payload.get("habits") or []
    if habits:
        rendered = "、".join(
            f"{habit['title']}（{STATE_LABELS.get(habit.get('state') or '', habit.get('state') or '')}）"
            for habit in habits
        )
        lines.append(f"- 习惯：{rendered}")
    study = payload.get("study") or {}
    if study.get("minutes"):
        lines.append(f"- 学习：{study['minutes']} 分钟（{study.get('sessions') or 0} 条记录）")
    try:
        touched = list_day_activity(database, vault_dir, day)
    except (OSError, ValueError):
        touched = []
    if touched:
        lines.append(f"- 今天动过的文件（{len(touched)} 个）：")
        for item in touched[:8]:
            lines.append(f"  - {str(item.get('mtime') or '')[11:16]} {item.get('title') or ''}")
    progress = payload.get("progress") or {}
    if progress.get("total"):
        lines.append(f"- 今日完成度：{progress.get('done')} / {progress.get('total')}（{progress.get('percent')}%）")
    for note in workbench.session_notes(vault_dir, day):
        text = " ".join(str(note.get("text") or "").split())
        if text:
            lines.append(f"- dsh 摘录：{text[:200]}")
    if len(lines) == 1:
        lines.append("- （今天还没有可汇总的记录）")
    return "\n".join(lines)


def _write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def create_journal_note_router(
    database: Database, vault_dir: Path, prefix: str = "/api", read_only: bool = False,
    gateway: Any = None,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["journal"])

    @router.get("/journal/note")
    def get_note(day: str = Query()):
        validate_day(day)
        return read_note(vault_dir, day)

    async def polish_summary(day: str, summary: str) -> str | None:
        """显式勾选后才调用；失败/未配置一律交回本地汇总。"""
        if gateway is None or not getattr(gateway, "configured", False):
            return None
        question = (
            "把下面这段今天的本地记录，写成一段第一人称的日记初稿（有分节更好），"
            "语气平实、不要夸张、不要编造记录里没有的事；保留具体做了什么。"
        )
        evidence = [{"title": f"{day} 的本地记录", "quote": summary, "document_id": None,
                     "source_path": "journal-note", "source_quote": summary}]
        try:
            result = await gateway.generate(question, evidence)
        except Exception:  # noqa: BLE001 - 永不丢本地汇总
            return None
        answer = (result or {}).get("answer")
        return str(answer).strip() if answer else None

    @router.post("/journal/note/generate")
    async def generate_note(payload: GenerateRequest):
        day = validate_day(payload.day).isoformat()
        if payload.mode not in ("create", "append"):
            raise HTTPException(422, "mode 只能是 create 或 append")
        if read_only:
            raise HTTPException(409, "Obsidian 库处于只读安全模式，已拒绝写入；想生成笔记请先取消只读。")
        path = note_abspath(vault_dir, day)
        summary = facts_summary(database, vault_dir, day)
        if payload.use_model:
            polished = await polish_summary(day, summary)
            if polished:
                summary = "> AI 润色初稿（已显式发送给你配置的模型；事实以本地记录为准）\n\n" + polished
        skeleton = render_skeleton(vault_dir, day)

        if payload.mode == "create":
            try:
                _write_new(path, skeleton.rstrip("\n") + "\n\n---\n\n" + summary + "\n")
            except FileExistsError:
                # 文件优先：不覆盖已经存在的笔记，只把“可追加的汇总”交回界面。
                current = read_note(vault_dir, day)
                return {**current, "created": False, "append_preview": summary}
        else:  # append
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                size = 0
            if size > MAX_NOTE_BYTES:
                raise HTTPException(413, "笔记文件过大，已停止追加")
            if size == 0:
                _write_new(path, skeleton.rstrip("\n") + "\n\n---\n\n" + summary + "\n")
            else:
                with open(path, "a", encoding="utf-8", newline="\n") as handle:
                    handle.write("\n---\n\n" + summary + "\n")

        _audit(database, f"journal_note_{'created' if payload.mode == 'create' else 'appended'}", {"day": day})
        return {**read_note(vault_dir, day), "created": payload.mode == "create", "appended": payload.mode == "append"}

    return router
