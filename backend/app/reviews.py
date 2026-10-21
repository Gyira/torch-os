"""确定性周/月复盘骨架（2026-10-21）：只读日记与笔记，生成可编辑的 Markdown。"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from fastapi import HTTPException

from . import journal_note, vault_layout
from .db import utc_now
from .markdown_store import load_note
from .vault import parse_text, safe_note_path

SHANGHAI = ZoneInfo("Asia/Shanghai")
EMOTIONS = ("开心", "平静", "焦虑", "疲惫", "充实", "烦躁", "失落", "期待", "满足",
            "压力", "放松", "专注", "迷茫", "感动", "生气", "担心", "踏实", "兴奋")
_WORD = re.compile(r"[一-鿿]{2,6}")


def _week_label(day: date) -> str:
    year, week, _weekday = day.isocalendar()
    return f"{year}-W{week:02d}"


def _week_range(day: date) -> tuple[date, date]:
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def _read_journal(vault: Path, day: date) -> tuple[bool, str]:
    note = journal_note.read_note(vault, day.isoformat())
    if not note.get("exists"):
        return False, ""
    try:
        _metadata, body = parse_text(note.get("content") or "")
    except ValueError:
        body = note.get("content") or ""
    return True, body


def _keywords(texts: list[str], limit: int = 6) -> list[str]:
    counter: Counter[str] = Counter()
    for text in texts:
        for word in _WORD.findall(text):
            if word in {"今天", "昨天", "明天", "一个", "自己", "觉得", "然后", "还是", "没有", "就是"}:
                continue
            counter[word] += 1
    return [word for word, count in counter.most_common(limit) if count > 1]


def _emotions(texts: list[str]) -> list[str]:
    found: Counter[str] = Counter()
    for text in texts:
        for word in EMOTIONS:
            found[word] += text.count(word)
    return [word for word, count in found.most_common(5) if count]


def _source_days(vault: Path, start: date, end: date) -> list[dict[str, Any]]:
    days: list[dict[str, Any]] = []
    current = start
    while current <= end:
        exists, body = _read_journal(vault, current)
        if exists:
            days.append({"date": current.isoformat(), "body": body})
        current += timedelta(days=1)
    return days


def _build(vault: Path, *, title: str, date_value: str, chapter: str,
           body: str) -> dict[str, Any]:
    directory = vault_layout.chapter_dir(chapter)
    if not directory:
        raise HTTPException(422, "未知复盘章节")
    target = safe_note_path(vault, f"{directory}/{title}.md")
    if target.is_file():
        loaded = load_note(target)
        _metadata, existing = parse_text(loaded["raw"].decode("utf-8-sig"))
        return {"created": False, "path": target.relative_to(vault).as_posix(),
                "body": existing, "revision": loaded["revision"]}
    target.parent.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    metadata = {
        "id": __import__("uuid").uuid4().hex,
        "type": "weekly_review" if chapter == "review_week" else "monthly_review",
        "title": title, "date": date_value, "created": now, "updated": now,
        "pillar": "life", "chapter": chapter, "status": "active",
    }
    lines = ["---", *yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip("\n").split("\n"),
             "---", "", *body.split("\n"), ""]
    target.write_text("\n".join(lines), encoding="utf-8", newline="")
    return {"created": True, "path": target.relative_to(vault).as_posix(),
            "body": body, "revision": ""}


def weekly_skeleton(vault_dir: Path | str, day: str = "") -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    try:
        target = date.fromisoformat(day) if day else datetime.now(SHANGHAI).date()
    except ValueError:
        raise HTTPException(422, "日期必须是 YYYY-MM-DD") from None
    start, end = _week_range(target)
    days = _source_days(vault, start, end)
    texts = [item["body"] for item in days]
    label = _week_label(target)
    keywords = _keywords(texts)
    emotions = _emotions(texts)
    body = f"""# {label} 周复盘

> 这是本地生成的骨架，不调用模型；在 Obsidian 里直接补全。

## 本周输入
- 日记 {len(days)} 篇（{start.isoformat()} ~ {end.isoformat()}）
- 关键词：{'、'.join(keywords) if keywords else '（本周记录较少，暂不提炼）'}

## 本周行动
- 目标/项目推进：
- 完成但没写进日记的事：

## 情绪关键词
{chr(10).join(f'- {word}' for word in emotions) if emotions else '- （本周日记里没有明显情绪词）'}

## 值得留下的东西
- 哪一件事值得沉淀成原则？

## 待提炼
- 从本周的反思、心得里挑 1–3 条，考虑放进「自我档案 / 沉淀与创作」。

## 下周重点
-
"""
    return _build(vault, title=label, date_value=end.isoformat(),
                  chapter="review_week", body=body)


def monthly_skeleton(vault_dir: Path | str, month: str = "") -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    try:
        first = date.fromisoformat(f"{month}-01") if month else datetime.now(SHANGHAI).date().replace(day=1)
    except ValueError:
        raise HTTPException(422, "月份格式应为 YYYY-MM") from None
    last_day = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    days = _source_days(vault, first, last_day)
    texts = [item["body"] for item in days]
    keywords = _keywords(texts, 8)
    emotions = _emotions(texts)
    label = first.strftime("%Y-%m")
    body = f"""# {label} 月复盘

> 这是本地生成的骨架，不调用模型；在 Obsidian 里直接补全。

## 本月输入
- 日记 {len(days)} 篇
- 关键词：{'、'.join(keywords) if keywords else '（本月记录较少）'}

## 本月行动
- 目标完成情况：
- 项目进展：

## 情绪与状态
{chr(10).join(f'- {word}' for word in emotions) if emotions else '- （没有明显情绪词）'}

## 本月沉淀
- 哪 1–3 件事值得写进自我档案？

## 下月重点
-
"""
    return _build(vault, title=label, date_value=last_day.isoformat(),
                  chapter="review_month", body=body)


__all__ = ["weekly_skeleton", "monthly_skeleton"]
