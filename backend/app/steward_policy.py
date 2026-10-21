"""确定性归档建议（2026-10-21）。

这是设计书决策树的本地规则版：不调用模型，只根据标题、正文、来源与标签
给出候选章节和理由。AI 管家二期可以在同一接口后面加模型增强；所有建议都
必须由用户确认后才会移动。
"""

from __future__ import annotations

import re
from typing import Any

from . import vault_layout

CHAPTER_TITLES = {item[0]: item[2] for item in vault_layout.CHAPTERS}
CHAPTER_PILLARS = {item[0]: item[1] for item in vault_layout.CHAPTERS}

_METHOD = ("步骤", "步法", "第一步", "流程", "方法", "清单", "如何", "怎么", "SOP",
           "检查表", "操作")
_CONCEPT = ("概念", "定义", "是指", "原理", "本质", "区别", "模型")
_READING = ("读书", "这本书", "作者", "书评", "读完")
_COURSE = ("课程", "课里", "老师", "讲座", "上课", "作业")
_FEELING = ("我觉得", "我感到", "触动", "感受", "想起", "让我", "情绪")
_WORK = ("工作", "职场", "项目", "会议", "同事", "公司", "客户", "职业")
_LIFE = ("生活", "家人", "朋友", "身体", "睡眠", "运动", "饮食")


def _has(text: str, words: tuple[str, ...]) -> str:
    for word in words:
        if word.casefold() in text:
            return word
    return ""


def suggest(title: str, body: str = "", source: str = "",
            tags: list[str] | None = None) -> dict[str, Any]:
    """返回 {chapter, pillar, reason, confidence, matched}；不确定时留在收件箱。"""
    text = " ".join([str(title or ""), str(body or ""), str(source or ""),
                     " ".join(str(tag) for tag in (tags or []))]).casefold()
    if not text.strip():
        return {"chapter": "inbox", "pillar": "knowledge", "reason": "内容为空，先留在收件箱。",
                "confidence": "low", "matched": ""}
    work = _has(text, _WORK)
    method = _has(text, _METHOD)
    concept = _has(text, _CONCEPT)
    reading = _has(text, _READING)
    course = _has(text, _COURSE)
    feeling = _has(text, _FEELING)
    life = _has(text, _LIFE)
    if method:
        chapter = "method_work" if work else "method_general"
        return {"chapter": chapter, "pillar": "knowledge",
                "reason": f"出现“{method}”，更像可操作的方法/SOP。",
                "confidence": "high", "matched": method}
    if concept:
        return {"chapter": "concept", "pillar": "knowledge",
                "reason": f"出现“{concept}”，更像概念/原理卡片。",
                "confidence": "high", "matched": concept}
    if reading:
        return {"chapter": "reading", "pillar": "knowledge",
                "reason": f"出现“{reading}”，更像读书笔记。",
                "confidence": "high", "matched": reading}
    if course:
        return {"chapter": "course", "pillar": "knowledge",
                "reason": f"出现“{course}”，更像课程笔记。",
                "confidence": "high", "matched": course}
    if feeling and not method:
        return {"chapter": "idea", "pillar": "self",
                "reason": f"出现“{feeling}”，更像个人触动/想法；如果只是零散过程记录，也可以留在人生路。",
                "confidence": "medium", "matched": feeling}
    if life and not work:
        return {"chapter": "insight_life", "pillar": "life",
                "reason": f"出现“{life}”，更像生活心得。",
                "confidence": "medium", "matched": life}
    if work:
        return {"chapter": "insight_work", "pillar": "life",
                "reason": f"出现“{work}”，更像工作心得；成熟方法请改放到方法库。",
                "confidence": "medium", "matched": work}
    return {"chapter": "inbox", "pillar": "knowledge",
            "reason": "暂时判断不了；先留在收件箱，等有上下文再分流。",
            "confidence": "low", "matched": ""}


def suggestion_for_note(note: dict[str, Any]) -> dict[str, Any]:
    # 列表接口给 excerpt，详情接口给完整 body；两者都尽量用上，规则才看得见上下文。
    body = note.get("excerpt") or str(note.get("body") or "")[:400]
    result = suggest(note.get("title", ""), body,
                     str(note.get("source") or ""), note.get("tags") or [])
    result["path"] = note.get("path", "")
    result["title"] = note.get("title", "")
    result["chapter_title"] = CHAPTER_TITLES.get(result["chapter"], result["chapter"])
    result["pillar_title"] = vault_layout.PILLAR_TITLES.get(result["pillar"], result["pillar"])
    return result


__all__ = ["suggest", "suggestion_for_note", "CHAPTER_TITLES", "CHAPTER_PILLARS"]
