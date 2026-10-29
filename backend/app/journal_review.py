"""日记闭环：盘点 → 提问 → 草稿 → 写入 → 封存。

设计见 `docs/日记闭环设计-2026-10-28.md`。三条硬规矩：

- **事实由程序写**：`build_digest` 只读任务笔记、待办、习惯、当日改动与 dsh 会话，不调模型；
- **空段落不生成**：`compose` 从结构化字段拼 Markdown，没写的段落整段消失；
- **只写当天**：`write_note` 只允许写 `00_人生路/01_日记/<今天>.md`，写前备份、带 sha 校验，
  只读库直接 409。

模型不在这里。改写与对话走 dsh（`/api/journal/chat` 的前端面板），
本模块保证**没有模型也能走完整条链路**。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import activity, journal_note, task_reports, vault_layout, workbench
from .journal_shared import validate_day
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SHANGHAI = ZoneInfo("Asia/Shanghai")
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
BACKUP_REL = Path(".torch-os", "backups", "journal")
MAX_ACTIVITY_LINES = 6
MAX_ACTIVITY_FILES = 40
MAX_ANSWER_CHARS = 4000
MAX_FREE_TEXT = 8000
MAX_NEXT_STEP = 400
MAX_QUESTIONS = 5
DEFAULT_QUESTIONS = 3

# 值得被问到的章节：改过这些笔记却没写日记时，问一句「是在调方向吗」。
MEANINGFUL_CHAPTERS = {
    "goal_year": "年度目标", "goal_quarter": "季度目标", "goal_month": "月度目标",
    "project_plan": "项目计划", "project_review": "项目复盘",
    "reflection": "反思", "insight_life": "生活心得", "insight_work": "工作心得",
    "insight_study": "学习心得", "identity": "身份",
}

SIGNAL_DONE = "done"
SIGNAL_OPEN = "open"
SIGNAL_MISS = "miss"


def shanghai_today() -> date:
    return datetime.now(SHANGHAI).date()


def _clean_day(day: str | None) -> str:
    if not day:
        return shanghai_today().isoformat()
    return validate_day(day).isoformat()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _plural(count: int, unit: str = "件") -> str:
    return f"{count} {unit}"


def _title_of(note: dict[str, Any]) -> str:
    return str(note.get("title") or Path(str(note.get("path") or "")).stem or "未命名")


def _rel(vault_dir: Path | str, value: Any) -> str:
    """任务笔记的 path 是绝对 Path；对外一律换成库内相对路径。"""
    if not value:
        return ""
    try:
        return Path(value).resolve().relative_to(Path(vault_dir).resolve()).as_posix()
    except (ValueError, OSError):
        return str(value)


# ── 盘点 ──────────────────────────────────────────────────────────────────

def build_digest(database: Any, vault_dir: Path | str, day: str) -> dict[str, Any]:
    """一天的事实清单。只读；任何异常都降级成空列表，不让日记整条链路挂掉。"""
    payload = workbench.collect_day(database, vault_dir, day)
    journal = journal_note.read_note(vault_dir, day)

    tasks = task_reports.tasks(vault_dir)
    completed = [note for note in tasks if note.get("done") and note.get("done_on") == day]
    overdue = sorted(
        (note for note in tasks
         if not note.get("done") and note.get("date") and str(note["date"]) < day),
        key=lambda note: (str(note.get("date")), _title_of(note)),
    )
    due_today = [note for note in tasks if not note.get("done") and note.get("date") == day]

    habits = payload.get("habits") or []
    habit_done = [habit for habit in habits if habit.get("done")]
    habit_missed = [habit for habit in habits if not habit.get("done")]

    todos = payload.get("todos") or []
    todo_done = [todo for todo in todos if todo.get("completed")]
    todo_open = [todo for todo in todos if not todo.get("completed")]

    daily = [item for item in (payload.get("daily") or []) if item.get("in_master")]
    daily_done = [item for item in daily if item.get("done")]
    daily_open = [item for item in daily if not item.get("done")]

    try:
        touched = activity.list_day_activity(database, vault_dir, day)[:MAX_ACTIVITY_FILES]
    except (OSError, ValueError):
        touched = []
    meaningful = [
        item for item in touched
        if vault_layout.chapter_key_of(item["path"]) in MEANINGFUL_CHAPTERS
    ]

    try:
        sessions = workbench.session_notes(vault_dir, day)
    except (OSError, ValueError):
        sessions = []

    facts = {
        "tasks": {
            "completed": [{"title": _title_of(note), "path": _rel(vault_dir, note.get("path"))}
                          for note in completed],
            "overdue": [{"title": _title_of(note), "due": note.get("date"),
                         "days": _days_late(str(note.get("date")), day),
                         "path": _rel(vault_dir, note.get("path"))} for note in overdue],
            "due_today": [{"title": _title_of(note), "due": note.get("date"),
                           "path": _rel(vault_dir, note.get("path"))} for note in due_today],
            "open": [{"title": _title_of(note), "due": note.get("date")}
                     for note in tasks if not note.get("done")],
        },
        "habits": {
            "done": [{"title": habit.get("title"), "minimum": habit.get("minimum_action")}
                     for habit in habit_done],
            "missed": [{"title": habit.get("title"), "minimum": habit.get("minimum_action")}
                       for habit in habit_missed],
        },
        "todos": {
            "done": [todo.get("title") for todo in todo_done],
            "open": [todo.get("title") for todo in todo_open],
        },
        "daily": {
            "done": [item.get("title") for item in daily_done],
            "open": [item.get("title") for item in daily_open],
        },
        "activity": [
            {
                "path": item["path"],
                "title": item.get("title") or Path(item["path"]).stem,
                "time": str(item.get("mtime") or "")[11:16],
                "chapter": vault_layout.chapter_key_of(item["path"]),
            }
            for item in touched
        ],
        "meaningful": [
            {
                "path": item["path"],
                "title": item.get("title") or Path(item["path"]).stem,
                "time": str(item.get("mtime") or "")[11:16],
                "chapter": MEANINGFUL_CHAPTERS.get(vault_layout.chapter_key_of(item["path"]), ""),
            }
            for item in meaningful
        ],
        "sessions": [{"path": item.get("path"), "text": item.get("text")} for item in sessions],
        "journal": {
            "exists": bool(journal.get("exists")),
            "relpath": journal.get("relpath"),
            "sealed": _is_sealed(journal.get("content") or ""),
            "mode": _front_matter_value(journal.get("content") or "", "mode") or "",
        },
        "progress": payload.get("progress") or {},
        "quiet_days": _quiet_days(vault_dir, day),
    }

    return {
        "day": day,
        "weekday": WEEKDAYS[date.fromisoformat(day).weekday()],
        "facts": facts,
        "signals": _signals(facts),
        "headline": _headline(facts),
    }


def _days_late(due: str, day: str) -> int:
    try:
        return max(0, (date.fromisoformat(day) - date.fromisoformat(due)).days)
    except ValueError:
        return 0


def _headline(facts: dict[str, Any]) -> str:
    """一句话盘点，给卡片用。"""
    done = len(facts["tasks"]["completed"]) + len(facts["todos"]["done"]) + len(facts["daily"]["done"])
    open_count = len(facts["tasks"]["overdue"]) + len(facts["tasks"]["due_today"])
    files = len(facts["activity"])
    bits = []
    if done:
        bits.append(f"完成 {done} 件")
    if open_count:
        bits.append(f"{open_count} 件还挂着")
    if files:
        bits.append(f"动过 {files} 篇笔记")
    if not bits:
        return "今天还没有留下痕迹"
    return "，".join(bits)


def _signals(facts: dict[str, Any]) -> list[str]:
    """人话线索：给面板和 dsh 开场用。每条都能指回具体记录。"""
    lines: list[str] = []
    tasks = facts["tasks"]
    if tasks["completed"]:
        names = "、".join(item["title"] for item in tasks["completed"][:5])
        lines.append(f"完成 {_plural(len(tasks['completed']))}：{names}")
    if tasks["overdue"]:
        names = "、".join(f"{item['title']}（{item['due']}）" for item in tasks["overdue"][:5])
        lines.append(f"还挂着 {_plural(len(tasks['overdue']))}：{names}")
    if tasks["due_today"]:
        names = "、".join(item["title"] for item in tasks["due_today"][:5])
        lines.append(f"今天到期：{names}")
    if facts["habits"]["done"] or facts["habits"]["missed"]:
        total = len(facts["habits"]["done"]) + len(facts["habits"]["missed"])
        lines.append(f"习惯 {len(facts['habits']['done'])}/{total}")
    if facts["todos"]["done"] or facts["daily"]["done"]:
        count = len(facts["todos"]["done"]) + len(facts["daily"]["done"])
        lines.append(f"待办勾掉 {count} 件")
    if facts["activity"]:
        names = "、".join(item["title"] for item in facts["activity"][:6])
        lines.append(f"动过 {len(facts['activity'])} 篇笔记：{names}")
    if facts["sessions"]:
        lines.append(f"有 {len(facts['sessions'])} 段 dsh 会话")
    return lines


def _front_matter_value(content: str, key: str) -> str:
    if not content.startswith("---"):
        return ""
    head = content.split("---", 2)
    if len(head) < 3:
        return ""
    for line in head[1].splitlines():
        name, _, value = line.partition(":")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def _is_sealed(content: str) -> bool:
    return _front_matter_value(content, "status") == "sealed"


def _quiet_days(vault_dir: Path | str, day: str, limit: int = 30) -> int:
    """往前数连续多少天没有日记文件（今天不算）。"""
    cursor = date.fromisoformat(day)
    count = 0
    for _ in range(limit):
        cursor -= timedelta(days=1)
        try:
            if journal_note.note_abspath(vault_dir, cursor.isoformat()).is_file():
                break
        except OSError:
            break
        count += 1
    return count


def _quiet_prompt(streak: int) -> str:
    if streak >= 14:
        return "有一阵子没写日记了，是太满，还是没话想说？"
    return f"最近 {streak} 天没写日记，是太满，还是没话想说？"


# ── 提问 ──────────────────────────────────────────────────────────────────

def suggest_questions(digest: dict[str, Any], limit: int = DEFAULT_QUESTIONS) -> list[dict[str, Any]]:
    """规则候选 → 排序 → 取前 N 条。每条都带证据，宁可少问，不瞎问。"""
    facts = digest["facts"]
    day = digest["day"]
    candidates: list[dict[str, Any]] = []

    for item in facts["tasks"]["overdue"]:
        days = item.get("days") or 0
        candidates.append({
            "kind": "overdue",
            "priority": 100 + min(days, 30),
            "prompt": f"「{item['title']}」从 {item['due']} 挂到现在，是还要做，还是可以拿掉？",
            "why": f"任务笔记里它的日期还是 {item['due']}，没有写完成于。",
            "evidence": item.get("path") or "",
        })
    for item in facts["habits"]["missed"]:
        candidates.append({
            "kind": "habit",
            "priority": 90,
            "prompt": f"「{item['title']}」今天没打勾——是没顾上，还是这个习惯现在不现实？",
            "why": "习惯今天在你的计划里，但没有打卡记录。",
            "evidence": item.get("minimum") or "",
        })
    for item in facts["meaningful"][:2]:
        candidates.append({
            "kind": "touched",
            "priority": 80,
            "prompt": f"你 {item['time'] or '今天'} 改了《{item['title']}》，是在调方向吗？",
            "why": f"今天动过{ item['chapter'] or '这篇笔记'}，但日记还是空的。",
            "evidence": item.get("path") or "",
        })
    if not facts["journal"]["exists"]:
        streak = int(facts.get("quiet_days") or 0)
        if streak >= 3:
            candidates.append({
                "kind": "silence",
                "priority": 70,
                "prompt": _quiet_prompt(streak),
                "why": "日记目录里连续几天没有文件。",
                "evidence": "00_人生路/01_日记/",
            })
    if facts["tasks"]["due_today"]:
        item = facts["tasks"]["due_today"][0]
        candidates.append({
            "kind": "due_today",
            "priority": 60,
            "prompt": f"「{item['title']}」今天到期，是排不进今天，还是不需要了？",
            "why": "任务定在今天，但还没有完成记录。",
            "evidence": item.get("path") or "",
        })
    if not any([facts["tasks"]["completed"], facts["todos"]["done"], facts["daily"]["done"]]):
        candidates.append({
            "kind": "empty",
            "priority": 40,
            "prompt": "今天什么都没有勾掉。是休息日，还是清单本来就不听话？",
            "why": "任务、待办、每日必做今天都没有完成记录。",
            "evidence": "",
        })

    candidates.sort(key=lambda item: item["priority"], reverse=True)
    return candidates[:max(1, min(limit, MAX_QUESTIONS))]


# ── 草稿 ──────────────────────────────────────────────────────────────────

def compose(
    day: str,
    digest: dict[str, Any],
    *,
    free_text: str = "",
    answers: list[dict[str, str]] | None = None,
    next_step: str = "",
    summary: str = "",
    mood: str = "",
    energy: int | None = None,
    weather: str = "",
    mode: str = "template",
    sealed: bool = False,
) -> str:
    """事实 + 你的话 → 一篇 Markdown。**没写的段落直接不出现。**"""
    facts = digest["facts"]
    day_value = date.fromisoformat(day)
    answers = [item for item in (answers or []) if (item.get("answer") or "").strip()]
    free_text = (free_text or "").strip()[:MAX_FREE_TEXT]
    next_step = (next_step or "").strip()[:MAX_NEXT_STEP]

    front = _front_matter(
        day=day, mode=mode, sealed=sealed, summary=summary.strip()[:120],
        mood=mood.strip()[:20], energy=energy, weather=weather.strip()[:20],
    )

    blocks: list[tuple[str, list[str]]] = []
    done_lines = _fact_lines(facts)
    if done_lines:
        blocks.append(("今天做了什么", done_lines))
    about_lines: list[str] = list(free_text.splitlines()) if free_text else []
    if answers:
        if about_lines:
            about_lines.append("")
        about_lines.extend(_answer_callout(answers))
    if about_lines:
        blocks.append(("我想说的", about_lines))
    if next_step:
        blocks.append(("下一步", [f"- {line.strip()}" for line in next_step.splitlines() if line.strip()]))

    body = [front, "", f"# {day_value.month}月{day_value.day}日 · {digest['weekday']}"]
    for heading, lines in blocks:
        body.append("")
        body.append(f"## {heading}")
        body.append("")
        body.extend(lines)

    text = "\n".join(body).rstrip("\n") + "\n"
    if not blocks:
        # 只有标题和 front matter 不算一篇日记。
        raise HTTPException(400, "还没有任何内容可写：至少写一句，或留下一个下一步。")
    return text


def _front_matter(*, day: str, mode: str, sealed: bool, summary: str,
                  mood: str, energy: int | None, weather: str) -> str:
    lines = ["---", "type: journal", f"date: {day}", f"mode: {_scalar(mode)}",
             f"status: {'sealed' if sealed else 'draft'}"]
    if mood:
        lines.append(f"mood: {_scalar(mood)}")
    if energy is not None:
        lines.append(f"energy: {int(energy)}")
    if weather:
        lines.append(f"weather: {_scalar(weather)}")
    if summary:
        lines.append(f"summary: {_scalar(summary)}")
    lines.extend(["tags: [日记]", "---"])
    return "\n".join(lines)


def _scalar(value: str) -> str:
    """YAML 能裸写就裸写，含冒号/井号/首尾空格时才加引号。"""
    text = str(value)
    plain = (
        bool(text)
        and text == text.strip()
        and "\n" not in text
        and ": " not in text
        and " #" not in text
        and not text.startswith(("-", "?", ":", "[", "{", "&", "*", "!", "|", ">", "@", "`"))
    )
    return text if plain else json.dumps(text, ensure_ascii=False)


def _fact_lines(facts: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    tasks = facts["tasks"]
    if tasks["completed"]:
        lines.append(f"- 完成：{'、'.join(item['title'] for item in tasks['completed'][:6])}")
    if tasks["overdue"]:
        lines.append("- 还挂着：" + "、".join(
            f"{item['title']}（{item['due']}）" for item in tasks["overdue"][:6]))
    if tasks["due_today"]:
        lines.append("- 今天到期：" + "、".join(item["title"] for item in tasks["due_today"][:6]))
    if facts["todos"]["done"]:
        lines.append("- 待办勾掉：" + "、".join(facts["todos"]["done"][:6]))
    if facts["daily"]["done"] or facts["daily"]["open"]:
        bits = []
        if facts["daily"]["done"]:
            bits.append("做完 " + "、".join(facts["daily"]["done"][:4]))
        if facts["daily"]["open"]:
            bits.append("还差 " + "、".join(facts["daily"]["open"][:4]))
        lines.append("- 每日必做：" + "；".join(bits))
    if facts["habits"]["done"] or facts["habits"]["missed"]:
        total = len(facts["habits"]["done"]) + len(facts["habits"]["missed"])
        lines.append(f"- 习惯：{len(facts['habits']['done'])}/{total}")
    files = facts["activity"][:MAX_ACTIVITY_LINES]
    if files:
        lines.append("- 动过：" + "、".join(
            f"[[{item['path']}|{item['title']}]]" for item in files))
    return lines


def _answer_callout(answers: list[dict[str, str]]) -> list[str]:
    lines = ["> [!question]- 今天问过的"]
    for item in answers:
        prompt = (item.get("prompt") or "").strip().replace("\n", " ")[:200]
        answer = (item.get("answer") or "").strip()[:MAX_ANSWER_CHARS]
        if not answer:
            continue
        if prompt:
            lines.append(f"> **{prompt}**")
        for line in answer.splitlines():
            lines.append(f"> {line}".rstrip())
        lines.append(">")
    if lines[-1] == ">":
        lines.pop()
    return lines


# ── 写入 ──────────────────────────────────────────────────────────────────

def write_note(vault_dir: Path | str, day: str, content: str, *,
               expected_sha: str = "", read_only: bool = False,
               confirm: bool = False) -> dict[str, Any]:
    """只写当天这一篇。写前备份、sha 校验、原子替换。"""
    if not confirm:
        raise HTTPException(400, "写入日记需要 confirm=true")
    target_day = validate_day(day)
    today = shanghai_today()
    if target_day != today:
        raise HTTPException(409, "只能写今天的日记；过去的日记请在 Obsidian 里改。")
    try:
        ensure_vault_writable(read_only, "写入今天日记")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error

    text = (content or "").strip("\n")
    if not text:
        raise HTTPException(400, "日记内容是空的")
    if "type: journal" not in text:
        raise HTTPException(400, "缺少 type: journal 的 front matter")

    relative = vault_layout.journal_path(day)
    path = Path(vault_dir) / relative
    old = path.read_bytes() if path.is_file() else b""
    if expected_sha and old and _sha256(old) != expected_sha:
        raise HTTPException(409, "这篇日记在预览之后被改过，请刷新再写。")

    backup = None
    if old:
        backup = _backup(vault_dir, path, day)

    payload = (text + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "wb", delete=False, dir=str(path.parent), prefix=".journal-", suffix=".md")
    try:
        handle.write(payload)
        handle.close()
        os.replace(handle.name, path)
    except OSError:
        Path(handle.name).unlink(missing_ok=True)
        raise

    return {
        "day": day,
        "path": relative,
        "sha256": _sha256(payload),
        "bytes": len(payload),
        "backup": str(backup) if backup else "",
        "created": not old,
    }


def _backup(vault_dir: Path | str, path: Path, day: str) -> Path:
    folder = Path(vault_dir) / BACKUP_REL
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(SHANGHAI).strftime("%Y%m%dT%H%M%S")
    target = folder / f"{day}-{stamp}.md"
    counter = 1
    while target.exists():
        target = folder / f"{day}-{stamp}-{counter}.md"
        counter += 1
    target.write_bytes(path.read_bytes())
    return target


def read_review(vault_dir: Path | str, day: str) -> dict[str, Any]:
    note = journal_note.read_note(vault_dir, day)
    path = Path(vault_dir) / vault_layout.journal_path(day)
    old = path.read_bytes() if path.is_file() else b""
    return {
        "day": day,
        "vault_name": Path(vault_dir).name,
        "exists": bool(note.get("exists")),
        "content": note.get("content") or "",
        "sha256": _sha256(old) if old else "",
        "sealed": _is_sealed(note.get("content") or ""),
    }


# ── 周复盘 ────────────────────────────────────────────────────────────────

def _strip_front(content: str) -> str:
    text = content or ""
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2].lstrip("\n") if len(parts) >= 3 else text


def section_text(content: str, heading: str) -> str:
    """取某个二级标题下的正文（与前端 journal-review-model.js 同口径）。"""
    out: list[str] = []
    inside = False
    for line in _strip_front(content).splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            inside = match.group(1) == heading
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def _first_paragraph(text: str, limit: int = 120) -> str:
    for block in re.split(r"\n\s*\n", text):
        line = " ".join(part.strip() for part in block.splitlines() if part.strip())
        if line and not line.startswith(("#", ">")):
            return line[:limit]
    return ""


def build_weekly(database: Any, vault_dir: Path | str, day: str, days: int = 7) -> dict[str, Any]:
    end = date.fromisoformat(day)
    rows = []
    for offset in range(days - 1, -1, -1):
        target = end - timedelta(days=offset)
        note = journal_note.read_note(vault_dir, target.isoformat())
        rows.append({
            "date": target.isoformat(),
            "weekday": WEEKDAYS[target.weekday()],
            "exists": bool(note.get("exists")),
            "excerpt": _first_paragraph(_strip_front(note.get("content") or "")),
            "next_step": journal_note.next_action_text(note.get("content") or ""),
        })
    completed = task_reports.completed_between(vault_dir, rows[0]["date"], day)
    written = sum(1 for row in rows if row["exists"])
    return {
        "day": day,
        "from": rows[0]["date"],
        "to": day,
        "days": rows,
        "written": written,
        "tasks_done": len(completed),
        "headline": f"7 天里写了 {written} 天，完成 {len(completed)} 件事",
    }


def weekly_markdown(payload: dict[str, Any]) -> str:
    start = date.fromisoformat(payload["from"])
    end = date.fromisoformat(payload["to"])
    week = end.isocalendar()[1]
    lines = [
        "---", "type: weekly_review",
        f"周: {end.isocalendar()[0]}-W{week:02d}",
        f"创建: {payload['day']}", "---", "",
        f"# 第 {week} 周复盘（{start.month}月{start.day}日 → {end.month}月{end.day}日）", "",
        f"> {payload['headline']}", "",
        "## 这周留下的",
    ]
    for row in payload["days"]:
        if row["exists"]:
            lines.append(f"- `{row['date']}` {row['weekday']}：{row['excerpt'] or '（没有正文）'}")
    if not payload["written"]:
        lines.append("- （这周没有日记）")
    lines += [
        "", "## 三个问题", "",
        "1. 这周哪件事最值得记下来？",
        "2. 哪件事其实可以不做？",
        "3. 下周只推一件大事，是哪件？", "",
        "## 下周一件大事", "- ", "",
        "## 我的复盘", "",
    ]
    return "\n".join(lines) + "\n"


# ── 反思回流 ──────────────────────────────────────────────────────────────

DISTILL_MARKS = {
    "reflection": ("反思", "复盘", "教训", "下次", "以后", "不该", "其实", "发现"),
    "insight": ("学到", "明白", "原来", "懂了", "意识到", "想通"),
}


def _classify(paragraph: str) -> str:
    for kind, marks in DISTILL_MARKS.items():
        if any(mark in paragraph for mark in marks):
            return kind
    return "reflection"


def _note_title(paragraph: str) -> str:
    head = re.split(r"[。！？!?；;]", paragraph)[0].strip()
    title = (head or paragraph)[:18]
    for bad in '\\/:*?"<>|':
        title = title.replace(bad, "")
    return title.strip() or "今日反思"


def distill_candidates(vault_dir: Path | str, day: str, limit: int = 2) -> list[dict[str, Any]]:
    """从今天的日记里挑 1–2 段像反思/心得的话，供你一键存进对应章节。"""
    note = journal_note.read_note(vault_dir, day)
    if not note.get("exists"):
        return []
    content = note.get("content") or ""
    body = section_text(content, "我想说的") or "\n\n".join(
        block for block in re.split(r"\n\s*\n", _strip_front(content))
        if block.strip() and not block.lstrip().startswith(("#", ">", "-", "*"))
    )
    candidates: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", body):
        paragraph = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if len(paragraph) < 12 or paragraph.startswith(("#", ">", "- ", "* ")):
            continue
        kind = _classify(paragraph)
        chapter = "reflection" if kind == "reflection" else "insight_life"
        title = _note_title(paragraph)
        candidates.append({
            "kind": kind,
            "chapter": chapter,
            "path": f"{vault_layout.chapter_dir(chapter)}/{title}.md",
            "title": title,
            "body": paragraph[:600],
            "markdown": distill_markdown(kind, title, day, paragraph[:600]),
        })
        if len(candidates) >= limit:
            break
    return candidates


def distill_markdown(kind: str, title: str, day: str, body: str) -> str:
    note_type = "reflection" if kind == "reflection" else "insight"
    return (
        f"---\ntype: {note_type}\ntitle: {_scalar(title)}\ncreated: {day}\n"
        f"来源: 日记 {day}\ntags: [日记回流]\n---\n\n"
        f"> {title}\n\n{body}\n"
    )


# ── API ───────────────────────────────────────────────────────────────────

class AnswerItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(default="", max_length=400)
    answer: str = Field(default="", max_length=MAX_ANSWER_CHARS)


class ComposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: str = ""
    free_text: str = Field(default="", max_length=MAX_FREE_TEXT)
    answers: list[AnswerItem] = Field(default_factory=list)
    next_step: str = Field(default="", max_length=MAX_NEXT_STEP)
    summary: str = Field(default="", max_length=120)
    mood: str = Field(default="", max_length=20)
    energy: int | None = Field(default=None, ge=0, le=5)
    weather: str = Field(default="", max_length=20)
    mode: str = Field(default="template", max_length=20)
    sealed: bool = False


class WriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: str = ""
    content: str = Field(min_length=1, max_length=60_000)
    expected_sha: str = Field(default="", max_length=64)
    confirm: bool = False


def create_journal_review_router(database: Any, vault_dir: Path | str, prefix: str = "/api",
                                 read_only: bool = False) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["journal-review"])

    @router.get("/journal/digest")
    def journal_digest(day: str = Query(default="")):
        target = _clean_day(day)
        return build_digest(database, vault_dir, target)

    @router.get("/journal/questions")
    def journal_questions(day: str = Query(default=""), limit: int = Query(default=DEFAULT_QUESTIONS)):
        target = _clean_day(day)
        digest = build_digest(database, vault_dir, target)
        return {"day": target, "questions": suggest_questions(digest, limit)}

    @router.get("/journal/review")
    def journal_review(day: str = Query(default="")):
        target = _clean_day(day)
        digest = build_digest(database, vault_dir, target)
        return {
            "digest": digest,
            "questions": suggest_questions(digest),
            "note": read_review(vault_dir, target),
        }

    @router.get("/journal/weekly")
    def journal_weekly(day: str = Query(default="")):
        target = _clean_day(day)
        payload = build_weekly(database, vault_dir, target)
        return {**payload, "markdown": weekly_markdown(payload)}

    @router.get("/journal/distill")
    def journal_distill(day: str = Query(default="")):
        target = _clean_day(day)
        return {"day": target, "candidates": distill_candidates(vault_dir, target)}

    @router.post("/journal/compose")
    def journal_compose(payload: ComposeRequest):
        target = _clean_day(payload.day)
        digest = build_digest(database, vault_dir, target)
        content = compose(
            target, digest,
            free_text=payload.free_text,
            answers=[answer.model_dump() for answer in payload.answers],
            next_step=payload.next_step,
            summary=payload.summary,
            mood=payload.mood,
            energy=payload.energy,
            weather=payload.weather,
            mode=payload.mode,
            sealed=payload.sealed,
        )
        return {"day": target, "content": content, "sha256": _sha256(content.encode("utf-8"))}

    @router.post("/journal/write")
    def journal_write(payload: WriteRequest):
        target = _clean_day(payload.day)
        result = write_note(vault_dir, target, payload.content,
                            expected_sha=payload.expected_sha,
                            read_only=read_only, confirm=payload.confirm)
        # 审计只记日期/字节数与是否新建，不记日记正文。
        database.audit("journal_saved", {
            "day": target, "bytes": result["bytes"], "created": result["created"],
        })
        return result

    return router
