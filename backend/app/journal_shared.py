"""Shared journal fields and day validation for the Markdown-era workbench.

The old SQLite journal router was retired in 0.99. These helpers are still used
by the live diary note, todo note, activity and export modules.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException

SHANGHAI = ZoneInfo("Asia/Shanghai")
TEXT_FIELDS = ("content", "highlights", "obstacles", "notes", "reflection",
               "diet_summary", "mood", "next_action")
FIELD_LABELS = {
    "content": "每日记录", "highlights": "完成与收获", "obstacles": "遇到的阻碍",
    "notes": "值得注意的事", "reflection": "应该反思的",
    "diet_summary": "饮食摘要", "mood": "情绪", "next_action": "下一步行动",
    "weather": "天气", "location": "地址",
}


def shanghai_today() -> date:
    return datetime.now(SHANGHAI).date()


def validate_day(value: str) -> date:
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError
        result = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "日期必须是有效的 YYYY-MM-DD") from exc
    if result > shanghai_today():
        raise HTTPException(422, "不能为未来日期记录已经发生的生活；请在任务中安排计划")
    return result
