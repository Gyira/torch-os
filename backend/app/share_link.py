"""Read-only fetch + rendering of DeepSeek web share links (v1.0 P2).

Only ``chat.deepseek.com/share/<id>`` is accepted.  The public content endpoint
returns a conversation whose fragments include REQUEST / RESPONSE / THINK /
TOOL_*; we keep only the user's question and the final answer.  Nothing here
writes the Vault or executes retrieved text.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

SHANGHAI = ZoneInfo("Asia/Shanghai")
SHARE_API = "https://chat.deepseek.com/api/v0/share/content"
SHARE_URL = "https://chat.deepseek.com/share/{share_id}"
SHARE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
SHARE_PATH_PATTERN = re.compile(r"/share/([A-Za-z0-9_-]{4,64})")
TITLE_LIMIT = 40
MAX_CHARS = 400_000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class ShareLinkError(RuntimeError):
    """The link is malformed, deleted, rate-limited, or the endpoint changed."""


def parse_share_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ShareLinkError("没有拿到分享链接。")
    text = text.strip("<>()（）[]【】 \t\r\n,，。;；'\"")
    if "://" in text:
        host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text, flags=re.IGNORECASE)
        host = host.split("/")[0].split(":")[0].lower()
        if host not in {"chat.deepseek.com", "www.chat.deepseek.com"}:
            raise ShareLinkError("只接受 chat.deepseek.com 的分享链接，其他站点不会去抓。")
    match = re.search(r"[?&]share_id=([A-Za-z0-9_-]{4,64})", text)
    if match:
        return match.group(1)
    match = SHARE_PATH_PATTERN.search(text)
    if match:
        return match.group(1)
    if SHARE_ID_PATTERN.match(text):
        return text
    raise ShareLinkError("看不懂这个链接：只接受 https://chat.deepseek.com/share/<id> 形式的分享链接。")


def fetch_payload(share_id: str, *, timeout: float = 20.0,
                  client: httpx.Client | None = None) -> dict[str, Any]:
    if not SHARE_ID_PATTERN.match(str(share_id or "")):
        raise ShareLinkError("share_id 不合法。")
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://chat.deepseek.com/",
        "Accept": "application/json, text/plain, */*",
    }
    try:
        if client is not None:
            response = client.get(SHARE_API, params={"share_id": share_id}, headers=headers)
        else:
            with httpx.Client(timeout=timeout, follow_redirects=True) as own:
                response = own.get(SHARE_API, params={"share_id": share_id}, headers=headers)
    except httpx.HTTPError as error:
        raise ShareLinkError(f"网络请求失败：{error}") from error
    if response.status_code == 404:
        raise ShareLinkError("分享不存在或已被删除。")
    if response.status_code != 200:
        raise ShareLinkError(f"接口返回 HTTP {response.status_code}。")
    if "html" in (response.headers.get("content-type") or "").lower():
        raise ShareLinkError("接口返回的是网页而不是数据（可能被风控或接口改版）。")
    try:
        payload = response.json()
    except ValueError as error:
        raise ShareLinkError("接口返回的不是 JSON。") from error
    if not isinstance(payload, dict):
        raise ShareLinkError("接口返回格式不对。")
    if payload.get("code") not in (0, "0"):
        message = str(payload.get("msg") or payload.get("message") or "").strip()
        raise ShareLinkError(f"分享不可用{('：' + message) if message else '。'}")
    data = payload.get("data")
    biz = data.get("biz_data") if isinstance(data, dict) else None
    if not isinstance(biz, dict) or not isinstance(biz.get("messages"), list):
        raise ShareLinkError("分享里没有对话内容。")
    return payload


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _message_time(value: Any) -> datetime | None:
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(stamp, SHANGHAI)
    except (OverflowError, OSError, ValueError):
        return None


def extract_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    biz = data.get("biz_data") if isinstance(data, dict) else None
    raw_messages = biz.get("messages") if isinstance(biz, dict) else None
    messages: list[dict[str, Any]] = []
    for item in raw_messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").upper()
        if role == "USER":
            wanted, out_role = "REQUEST", "user"
        elif role == "ASSISTANT":
            wanted, out_role = "RESPONSE", "assistant"
        else:
            continue
        parts = []
        for fragment in item.get("fragments") or []:
            if not isinstance(fragment, dict) or fragment.get("type") != wanted:
                continue
            content = fragment.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content)
        text = _clean_text("\n\n".join(parts))
        if text:
            messages.append({"role": out_role, "text": text,
                             "at": _message_time(item.get("inserted_at"))})
    return messages


def conversation_title(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") != "user":
            continue
        text = re.sub(r"[#>*_`~\[\]()]", "", str(message.get("text") or ""))
        text = re.sub(r"\s+", " ", text).strip(" ：:·-—")
        if text:
            return text[:TITLE_LIMIT] + ("…" if len(text) > TITLE_LIMIT else "")
    return "DeepSeek 网页版对话"


def _turn_count(messages: list[dict[str, Any]]) -> int:
    return sum(1 for message in messages if message.get("role") == "user")


def group_rounds(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rounds: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "user":
            rounds.append({"user": message, "answers": [], "at": message.get("at")})
        elif rounds:
            rounds[-1]["answers"].append(message)
    return rounds


def parse_round_spec(spec: str, total: int) -> list[int]:
    text = str(spec or "").strip()
    if not text:
        return list(range(1, total + 1))
    picked: list[int] = []
    for part in re.split(r"[,，\s]+", text):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:\s*[-–~]\s*(\d+))?", part)
        if not match:
            raise ShareLinkError(f"看不懂轮次「{part}」，写法是 1,3-5。")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start or end > total:
            raise ShareLinkError(f"轮次 {part} 超出范围（这份分享共 {total} 轮）。")
        picked.extend(range(start, end + 1))
    return sorted(set(picked)) or list(range(1, total + 1))


def select_rounds(messages: list[dict[str, Any]], spec: str = "") -> tuple[list[dict[str, Any]], list[int], int]:
    rounds = group_rounds(messages)
    picked = parse_round_spec(spec, len(rounds))
    chosen: list[dict[str, Any]] = []
    for index, group in enumerate(rounds, 1):
        if index not in picked:
            continue
        user = dict(group["user"])
        user["round"] = index
        chosen.append(user)
        for answer in group["answers"]:
            item = dict(answer)
            item["round"] = index
            chosen.append(item)
    return chosen, picked, len(rounds)


def render_markdown(messages: list[dict[str, Any]], share_id: str, *,
                    fetched_at: datetime | None = None, rounds_note: str = "") -> str:
    stamp = (fetched_at or datetime.now(SHANGHAI)).strftime("%Y-%m-%d %H:%M")
    turns = _turn_count(messages)
    header = [
        f"> 来源：{SHARE_URL.format(share_id=share_id)}",
        f"> 取回：{stamp}（Asia/Shanghai）· {turns} 轮问答{rounds_note}",
    ]
    blocks: list[str] = []
    if turns <= 1 and not rounds_note:
        for message in messages:
            heading = "## 我问" if message["role"] == "user" else "## 回答"
            blocks.append(f"{heading}\n\n{message['text']}")
    else:
        round_index = 0
        for message in messages:
            if message["role"] == "user":
                round_index = int(message.get("round") or (round_index + 1))
                heading = f"## 第 {round_index} 轮 · 我问"
            else:
                heading = f"## 第 {round_index or 1} 轮 · 回答"
            blocks.append(f"{heading}\n\n{message['text']}")
    return "\n".join(header) + "\n\n" + "\n\n".join(blocks) + "\n"


def summarize(messages: list[dict[str, Any]], share_id: str) -> dict[str, Any]:
    chars = sum(len(message["text"]) for message in messages)
    return {
        "share_id": share_id,
        "share_url": SHARE_URL.format(share_id=share_id),
        "title": conversation_title(messages),
        "turns": _turn_count(messages),
        "chars": chars,
        "messages": [
            {
                "role": message["role"],
                "chars": len(message["text"]),
                "at": message["at"].isoformat(timespec="seconds") if message.get("at") else None,
                "preview": message["text"][:120].replace("\n", " "),
            }
            for message in messages
        ],
    }


def suggest_chapter(messages: list[dict[str, Any]]) -> str:
    text = "\n".join(str(message.get("text") or "") for message in messages).casefold()
    if any(word in text for word in ("读书", "这本书", "作者", "章节", "读后感")):
        return "reading"
    if any(word in text for word in ("课程", "课件", "作业", "老师")):
        return "course"
    if any(word in text for word in ("方法", "步骤", "流程", "怎么做")):
        return "method_general"
    if any(word in text for word in ("概念", "定义", "术语", "是什么")):
        return "concept"
    return "topic"


def preview(value: str, *, rounds: str = "", client: httpx.Client | None = None,
            max_chars: int = MAX_CHARS) -> dict[str, Any]:
    """Fetch a share link and return a UI-ready preview; never writes."""
    share_id = parse_share_id(value)
    messages = extract_messages(fetch_payload(share_id, client=client))
    if not messages:
        raise ShareLinkError("分享里只有思考/工具片段，没有可归档的问答。")
    selected, picked, total = select_rounds(messages, rounds)
    rounds_note = ""
    if picked and len(picked) < total:
        rounds_note = f"（只取第 {'、'.join(str(item) for item in picked)} 轮，原文共 {total} 轮）"
    markdown = render_markdown(selected, share_id, rounds_note=rounds_note)
    summary = summarize(selected, share_id)
    summary.update({
        "total_rounds": total,
        "picked_rounds": picked,
        "markdown": markdown,
        "chars_markdown": len(markdown),
        "too_long": len(markdown) > max(1, int(max_chars)),
        "suggested_chapter": suggest_chapter(selected),
    })
    return summary


__all__ = [
    "SHARE_URL", "MAX_CHARS", "ShareLinkError", "parse_share_id", "fetch_payload",
    "extract_messages", "conversation_title", "group_rounds", "parse_round_spec",
    "select_rounds", "render_markdown", "summarize", "suggest_chapter", "preview",
]
