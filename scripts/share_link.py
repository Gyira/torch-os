"""把 DeepSeek 网页版的分享链接取回成 Markdown（2026-10-28）。

工作流：我在工作台右侧书签栏点开 chat.deepseek.com，问完在网页里「分享」拿到
`https://chat.deepseek.com/share/<id>`，把链接贴给 dsh；这里只负责把问答原文取回来，
不写库、不改写内容。

网页本身是 JS 渲染，直接抓 HTML 只有空壳；公开分享内容有一个只读接口：

    GET https://chat.deepseek.com/api/v0/share/content?share_id=<id>

返回 `data.biz_data.messages[]`，每条消息的 `fragments` 里：
    REQUEST  = 我的提问（role=USER）
    RESPONSE = 最终回答（role=ASSISTANT）
    THINK / TOOL_* = 思考与工具过程，不写进笔记（默认丢弃）

用法：
    python scripts/share_link.py "<链接或 share_id>" [--json] [--out 草稿.md]

只读；联网只发往 chat.deepseek.com。取不到就报错退出，不猜内容。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
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
    """链接不可用：格式不对、分享不存在、接口改版或被限制。"""


def parse_share_id(value: Any) -> str:
    """从链接、API 链接或裸 id 里取出 share_id；不是 chat.deepseek.com 的一律拒绝。"""
    text = str(value or "").strip()
    if not text:
        raise ShareLinkError("没有拿到分享链接。")
    # 句末标点、全角括号这些是聊天里粘链接时常见的尾巴。
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
    raise ShareLinkError(
        "看不懂这个链接：只接受 https://chat.deepseek.com/share/<id> 形式的分享链接。")


def fetch_payload(share_id: str, *, timeout: float = 20.0,
                  client: httpx.Client | None = None) -> dict[str, Any]:
    """调用公开分享接口；非 0 code、空数据、HTML 都按失败处理。"""
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
    content_type = (response.headers.get("content-type") or "").lower()
    if "html" in content_type:
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
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
    """只保留「我的提问」和「最终回答」；思考过程与工具调用一律不进笔记。"""
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
        if not text:
            continue
        messages.append({"role": out_role, "text": text, "at": _message_time(item.get("inserted_at"))})
    return messages


def conversation_title(messages: list[dict[str, Any]]) -> str:
    """用第一句提问当标题建议：去掉 Markdown 记号和换行，最多 40 字。"""
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
    """按「一问 + 随后所有回答」切轮；分享里的分支/重新生成会留下多条回答。"""
    rounds: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "user":
            rounds.append({"user": message, "answers": [], "at": message.get("at")})
        elif rounds:
            rounds[-1]["answers"].append(message)
    return rounds


def parse_round_spec(spec: str, total: int) -> list[int]:
    """`1,3-5` → [1,3,4,5]；空串表示全部；越界报错（宁可不写，不错写）。"""
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
    """按轮次筛选消息；返回（消息、选中的轮号、原总轮数）。"""
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
    """把问答渲染成笔记正文（frontmatter 由 archive_note.py 负责，这里不写）。"""
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
                "preview": message["text"][:80].replace("\n", " "),
            }
            for message in messages
        ],
    }


def _configure_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - 老解释器/被重定向
        pass


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = argparse.ArgumentParser(
        description="取回 DeepSeek 网页版分享链接里的问答，输出 Markdown 或 JSON 摘要。")
    parser.add_argument("link", help="分享链接、API 链接或 share_id")
    parser.add_argument("--json", action="store_true", help="只输出结构化摘要（不打印全文）")
    parser.add_argument("--out", type=Path, help="把 Markdown 写到这个文件（UTF-8，不覆盖已有内容）")
    parser.add_argument("--timeout", type=float, default=20.0, help="网络超时秒数，默认 20")
    parser.add_argument("--rounds", default="", help="只取第几轮，如 1,3-5（从 1 开始；默认全部）")
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS,
                        help=f"整篇 Markdown 的上限，默认 {MAX_CHARS}；超了只给 --json 摘要")
    args = parser.parse_args(argv)

    try:
        share_id = parse_share_id(args.link)
    except ShareLinkError as error:
        print(f"链接不可用：{error}", file=sys.stderr)
        return 2

    try:
        payload = fetch_payload(share_id, timeout=args.timeout)
        messages = extract_messages(payload)
        messages, picked, total_rounds = select_rounds(messages, args.rounds)
    except ShareLinkError as error:
        print(f"取不到分享内容：{error}", file=sys.stderr)
        return 3

    if not messages:
        print("分享里只有思考/工具片段，没有可归档的问答。", file=sys.stderr)
        return 3

    rounds_note = ""
    if picked and len(picked) < total_rounds:
        rounds_note = f"（只取第 {'、'.join(str(item) for item in picked)} 轮，原文共 {total_rounds} 轮）"
    markdown = render_markdown(messages, share_id, rounds_note=rounds_note)
    too_long = len(markdown) > max(1, args.max_chars)
    if too_long and (args.out or not args.json):
        print(f"问答太长（{len(markdown)} 字，上限 {args.max_chars}），拒绝整篇输出；"
              f"这份分享共 {total_rounds} 轮，用 --rounds 1-3 只取需要的轮次。", file=sys.stderr)
        return 3

    if args.out:
        target = args.out.expanduser()
        if target.exists():
            print(f"目标文件已存在，不覆盖：{target}", file=sys.stderr)
            return 4
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown, encoding="utf-8", newline="\n")
        except OSError as error:
            print(f"写不进 {target}：{error}", file=sys.stderr)
            return 4
        print(f"已写入 {target}（{len(markdown)} 字）")

    if args.json:
        summary = summarize(messages, share_id)
        summary["total_rounds"] = total_rounds
        summary["picked_rounds"] = picked
        summary["chars_markdown"] = len(markdown)
        summary["too_long"] = too_long
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif not args.out:
        print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
