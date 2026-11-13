"""Portable capture contract. Public pages only; unknown formats fail closed."""
from __future__ import annotations

import hashlib
import json
import re
import socket
import ipaddress
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import getproxies

import httpx

from . import share_link as legacy

MAX_RESPONSE = 8 * 1024 * 1024


def identify(value: str) -> tuple[str, str, str]:
    value = str(value or "").strip().strip("<>()（）[]【】 \t\r\n,，。;；'\"")
    if legacy.SHARE_ID_PATTERN.fullmatch(value):
        return "deepseek", value, legacy.SHARE_URL.format(share_id=value)
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise legacy.ShareLinkError("只接受 HTTPS 公开分享链接。")
    host = (parsed.hostname or "").lower()
    provider = {"chatgpt.com": "chatgpt", "chat.openai.com": "chatgpt",
                "chat.deepseek.com": "deepseek", "www.chat.deepseek.com": "deepseek"}.get(host)
    match = re.fullmatch(r"/share/([A-Za-z0-9_-]{4,128})/?", parsed.path)
    if not provider or not match:
        raise legacy.ShareLinkError("只接受 ChatGPT / DeepSeek 的公开分享页面。")
    sid = match[1]
    canonical = ("https://chatgpt.com/share/" + sid if provider == "chatgpt"
                 else legacy.SHARE_URL.format(share_id=sid))
    return provider, sid, canonical


def get_public(url: str, *, client: httpx.Client | None = None, timeout: float = 20) -> bytes:
    """No redirect, cookie, login or unbounded response. Hosts are fixed upstreams."""
    if client is None:
        host = urlsplit(url).hostname
        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
                raise legacy.ShareLinkError("分享站点解析到非公网地址，已停止请求。")
        except OSError as exc:
            raise legacy.ShareLinkError("分享站点 DNS 解析失败。") from exc
        # Respect the user's configured HTTPS proxy (including Windows settings).
        # Targets still come exclusively from identify()/the fixed DeepSeek endpoint.
        with httpx.Client(timeout=timeout, follow_redirects=False,
                          proxy=getproxies().get("https"), trust_env=False) as own:
            return get_public(url, client=own)
    try:
        with client.stream("GET", url, headers={"User-Agent": legacy.USER_AGENT}, follow_redirects=False) as response:
            if response.status_code == 404:
                raise legacy.ShareLinkError("分享不存在或已删除。")
            if response.status_code in (301, 302, 303, 307, 308, 401, 403):
                raise legacy.ShareLinkError("分享需要访问权限、跳转或被限制；请粘贴正文。")
            if response.status_code != 200:
                raise legacy.ShareLinkError(f"分享站点返回 HTTP {response.status_code}。")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE:
                    raise legacy.ShareLinkError("分享响应过大，已停止读取。")
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError as exc:
        raise legacy.ShareLinkError("网络请求失败，请稍后重试或粘贴正文。") from exc


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.active = tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__"

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def parse_chatgpt(page: bytes) -> tuple[str, list[dict]]:
    """Recognize explicit conversation trees only. Stream encodings need a new adapter."""
    parser = _Page()
    parser.feed(page.decode("utf-8", errors="replace"))
    try:
        data = json.loads("".join(parser.parts))
        shared = data["props"]["pageProps"]["serverResponse"]["data"]
        nodes, current = shared["mapping"], shared["current_node"]
    except (ValueError, KeyError, TypeError):
        raise legacy.ShareLinkError("未识别到完整 ChatGPT 对话结构（可能页面改版）；请粘贴正文。")
    if not isinstance(nodes, dict) or not isinstance(current, str):
        raise legacy.ShareLinkError("分享对话索引格式变化，请粘贴正文。")
    messages, visited = [], set()
    while current:
        if current in visited or current not in nodes:
            raise legacy.ShareLinkError("分享对话链不完整，拒绝作为完整原文导入。")
        visited.add(current)
        node = nodes[current]
        if not isinstance(node, dict):
            raise legacy.ShareLinkError("分享对话节点格式变化，请粘贴正文。")
        message = node.get("message") or {}
        if not isinstance(message, dict):
            raise legacy.ShareLinkError("分享消息格式变化，请粘贴正文。")
        author, metadata = message.get("author") or {}, message.get("metadata") or {}
        if not isinstance(author, dict) or not isinstance(metadata, dict):
            raise legacy.ShareLinkError("分享消息元数据格式变化，请粘贴正文。")
        role = author.get("role")
        content = message.get("content") or {}
        if (message.get("channel") not in (None, "final") or
                metadata.get("is_visually_hidden_from_conversation")):
            current = node.get("parent")
            continue
        if role in ("user", "assistant"):
            if not isinstance(content, dict) or not isinstance(content.get("parts", []), list):
                raise legacy.ShareLinkError("分享正文格式变化，请粘贴正文。")
            if message.get("status") not in (None, "finished_successfully"):
                raise legacy.ShareLinkError("分享包含未完成回复，请粘贴核对后的正文。")
            if content.get("content_type") != "text" or any(not isinstance(p, str) for p in content.get("parts", [])):
                raise legacy.ShareLinkError("分享含暂不支持的媒体内容，请粘贴需要的正文。")
            text = "\n\n".join(content.get("parts", [])).strip()
            if text:
                messages.append({"role": role, "text": text, "at": None})
        current = node.get("parent")
        if current is not None and not isinstance(current, str):
            raise legacy.ShareLinkError("分享父节点格式变化，请粘贴正文。")
    messages.reverse()
    if not messages:
        raise legacy.ShareLinkError("分享没有可归档的正文。")
    return str(shared.get("title") or "ChatGPT 对话"), messages


def capture(url: str = "", *, rounds: str = "", pasted_text: str = "",
            answers_only: bool = False, client: httpx.Client | None = None, timeout: float = 20) -> dict:
    provider, sid, canonical = identify(url) if url else ("paste", "", "")
    method = "paste" if pasted_text.strip() else "public_page"
    if method == "paste":
        if len(pasted_text.encode("utf-8")) > legacy.MAX_CHARS:
            raise legacy.ShareLinkError("粘贴正文过大，请缩小范围。")
        if rounds or answers_only:
            raise legacy.ShareLinkError("粘贴正文不自动推断角色或轮次，请先选好需要的正文。")
        messages = [{"role": "assistant", "text": pasted_text.strip(), "at": None}]
        title = "粘贴的对话正文"
    elif provider == "chatgpt":
        title, messages = parse_chatgpt(get_public(canonical, client=client, timeout=timeout))
    elif provider == "deepseek":
        raw = get_public(legacy.SHARE_API + "?share_id=" + sid, client=client, timeout=timeout)
        try:
            payload = json.loads(raw)
            if payload.get("code") not in (0, "0"):
                raise ValueError()
            messages = legacy.extract_messages(payload)
        except (ValueError, AttributeError, TypeError):
            raise legacy.ShareLinkError("DeepSeek 分享接口不可用或数据格式变化。")
        title = legacy.conversation_title(messages)
    else:
        raise legacy.ShareLinkError("请粘贴分享链接或正文。")
    if not messages:
        raise legacy.ShareLinkError("没有可归档的正文。")
    if any(m["role"] == "user" for m in messages):
        selected, picked, total = legacy.select_rounds(messages, rounds)
    else:
        if rounds:
            raise legacy.ShareLinkError("此内容只有回答，没有可选择的问答轮次。")
        selected, picked, total = messages, [], 0
    if answers_only:
        selected = [m for m in selected if m["role"] == "assistant"]
    if not selected:
        raise legacy.ShareLinkError("所选范围没有正文。")
    fetched = datetime.now(timezone.utc).isoformat()
    fingerprint = json.dumps({"provider": provider, "source": canonical,
                              "messages": [(m["role"], m["text"]) for m in selected],
                              "method": method}, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()
    header = f"> 来源：{canonical or '用户粘贴'}\n> 获取：{fetched}\n> 获取方式：{method}；模型：unknown\n"
    if method == "paste":
        header += "> 用户提供正文，未与分享页核验角色或完整性。\n"
    markdown = header + "\n" + "\n\n".join(
        f"## {'我问' if m['role'] == 'user' else '回答'}\n\n{m['text']}" for m in selected) + "\n"
    return {"provider": provider, "share_id": sid, "share_url": canonical, "title": title[:200],
            "capture_method": method, "completeness": "user_provided" if method == "paste" else "complete",
            "parser_version": 1, "model": "unknown", "fetched_at": fetched,
            "content_hash": digest, "markdown_hash": hashlib.sha256(markdown.encode()).hexdigest(),
            "markdown": markdown, "chars_markdown": len(markdown), "too_long": len(markdown) > legacy.MAX_CHARS,
            "turns": sum(m["role"] == "user" for m in selected), "total_rounds": total,
            "picked_rounds": picked, "suggested_chapter": legacy.suggest_chapter(selected), "messages": [
                {"role": m["role"], "preview": m["text"][:120], "chars": len(m["text"])} for m in selected]}
