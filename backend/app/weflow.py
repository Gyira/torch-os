"""WeFlow 本地 HTTP API 客户端（微信同步用）。

只访问 `http://127.0.0.1:<port>`，不接受用户填写的任意 URL；Token 只放在
Authorization 头里，不写日志、不回传前端。WeFlow 负责读取本机已登录微信的数据，
torch 只拉取用户选中的一个会话。

接口依据 WeFlow 的 ChatLab Pull：
- `GET /api/v1/sessions?format=chatlab`
- `GET /api/v1/sessions/:id/messages?since=&offset=&limit=`

WeFlow 的 API 可能随版本变化；失败时抛 `WeFlowError`，由上层转成可读提示。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_PORT = 5031
DEFAULT_LIMIT = 5000
MAX_RESPONSE_BYTES = 24 * 1024 * 1024
MAX_MESSAGES_PER_SYNC = 200_000


class WeFlowError(Exception):
    """WeFlow 连接/鉴权/数据格式错误；消息可直接展示给用户。"""


@dataclass(frozen=True)
class WeFlowSession:
    id: str
    name: str
    type: str
    platform: str
    message_count: int | None
    last_message_at: int | None


@dataclass(frozen=True)
class WeFlowMessage:
    platform_message_id: str
    sender_id: str
    sender: str
    timestamp: datetime
    message_type: int
    content: str
    is_send: bool
    is_system: bool
    reply_to_message_id: str = ""
    media_path: str = ""


@dataclass
class WeFlowPull:
    owner_id: str
    members: dict[str, str]
    messages: list[WeFlowMessage] = field(default_factory=list)
    has_more: bool = False
    next_since: int | None = None
    next_offset: int | None = None
    watermark: int | None = None


def _clean(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _message_placeholder(message_type: int) -> str:
    return {
        1: "[图片]", 2: "[语音]", 3: "[视频]", 5: "[表情]",
        7: "[链接/文件]", 8: "[位置]", 23: "[通话]", 27: "[名片]",
        80: "[系统消息]", 99: "[其他消息]",
    }.get(message_type, "[消息]")


class WeFlowClient:
    """本地 WeFlow API 的最小客户端；端口固定 loopback。"""

    def __init__(self, token: str, port: int = DEFAULT_PORT, *,
                 timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
        token = str(token or "").strip()
        if not token:
            raise WeFlowError("还没有配置 WeFlow Access Token")
        try:
            port = int(port)
        except (TypeError, ValueError) as exc:
            raise WeFlowError("WeFlow 端口不正确") from exc
        if not 1 <= port <= 65535:
            raise WeFlowError("WeFlow 端口应在 1-65535 之间")
        self.port = port
        self._token = token
        self._base = f"http://127.0.0.1:{port}"
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "WeFlowClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not path.startswith("/api/"):
            raise WeFlowError("WeFlow 接口路径不合法")
        try:
            response = self._client.get(f"{self._base}{path}", params=params)
        except httpx.ConnectError as exc:
            raise WeFlowError(
                f"连不上 WeFlow（127.0.0.1:{self.port}）。请确认 WeFlow 正在运行，"
                "并在设置 → API 服务里开启服务。"
            ) from exc
        except httpx.TimeoutException as exc:
            raise WeFlowError("WeFlow 响应超时，请稍后再试") from exc
        except httpx.HTTPError as exc:
            raise WeFlowError(f"WeFlow 请求失败：{exc}") from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise WeFlowError("WeFlow 返回内容超过大小上限")
        if response.status_code in (401, 403):
            raise WeFlowError("WeFlow Access Token 无效或已更换，请重新生成并填写")
        if response.status_code != 200:
            raise WeFlowError(f"WeFlow 返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise WeFlowError("WeFlow 返回了非 JSON 内容") from exc
        if not isinstance(payload, dict):
            raise WeFlowError("WeFlow 返回结构不正确")
        return payload

    def health(self) -> dict[str, Any]:
        return self._get("/api/v1/health")

    def sessions(self, keyword: str = "", *, limit: int = 200,
                 include_groups: bool = False) -> list[WeFlowSession]:
        limit = max(1, min(int(limit), 1000))
        params: dict[str, Any] = {"format": "chatlab", "limit": limit}
        keyword = str(keyword or "").strip()
        if keyword:
            params["keyword"] = keyword[:100]
        payload = self._get("/api/v1/sessions", params)
        raw_sessions = payload.get("sessions")
        if not isinstance(raw_sessions, list):
            raise WeFlowError("WeFlow 会话列表结构不正确")
        result: list[WeFlowSession] = []
        for item in raw_sessions:
            if not isinstance(item, dict):
                continue
            session_id = _clean(item.get("id"))
            if not session_id:
                continue
            session_type = _clean(item.get("type"), 20).lower() or "private"
            if not include_groups and session_type != "private":
                continue
            result.append(WeFlowSession(
                id=session_id,
                name=_clean(item.get("name")) or session_id,
                type=session_type,
                platform=_clean(item.get("platform"), 20) or "wechat",
                message_count=_int(item.get("messageCount")),
                last_message_at=_int(item.get("lastMessageAt")),
            ))
        return result

    def pull(self, session_id: str, *, since: int = 0, offset: int = 0,
             limit: int = DEFAULT_LIMIT) -> WeFlowPull:
        session_id = str(session_id or "").strip()
        if not session_id or len(session_id) > 200:
            raise WeFlowError("WeFlow 会话 ID 不正确")
        limit = max(1, min(int(limit), DEFAULT_LIMIT))
        payload = self._get(
            f"/api/v1/sessions/{quote(session_id, safe='')}/messages",
            {"since": max(0, int(since)), "offset": max(0, int(offset)), "limit": limit},
        )
        return parse_chatlab(payload)


def _message_type(value: Any) -> int:
    number = _int(value)
    if number is None:
        return 0
    return number if 0 <= number <= 10_000 else 0


def load_chatlab_export(raw: bytes) -> dict[str, Any]:
    """读取 WeFlow 导出的 ChatLab JSON 或 JSONL，返回统一的 payload。"""
    if not raw:
        raise WeFlowError("文件内容为空")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise WeFlowError("ChatLab 文件应为 UTF-8 编码") from exc
    stripped = text.lstrip()
    if not stripped:
        raise WeFlowError("文件内容为空")
    try:
        payload = json.loads(stripped)
        if isinstance(payload, list):
            return {"messages": payload}
        if not isinstance(payload, dict):
            raise WeFlowError("ChatLab 文件结构不正确")
        if isinstance(payload.get("messages"), list):
            return payload
    except json.JSONDecodeError:
        pass

    header: dict[str, Any] = {}
    members: list[Any] = []
    messages: list[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WeFlowError("文件不是有效的 ChatLab JSON 或 JSONL") from exc
        if not isinstance(item, dict):
            continue
        kind = str(item.get("_type") or "")
        if kind == "header":
            header = item
        elif kind == "member":
            members.append(item)
        elif kind == "message":
            messages.append(item)
        elif isinstance(item.get("messages"), list):
            return item
    if not messages:
        raise WeFlowError("文件里没有找到 ChatLab 消息")
    result: dict[str, Any] = {
        "meta": header.get("meta") if isinstance(header.get("meta"), dict) else {},
        "members": members,
        "messages": messages,
    }
    if header.get("chatlab"):
        result["chatlab"] = header["chatlab"]
    return result


def parse_chatlab(payload: dict[str, Any], *, me_sender: str = "") -> WeFlowPull:
    """把 ChatLab Pull/导出归一成消息对象。

    API 响应用 ``meta.ownerId`` 判断是否本人发送；导出文件通常没有 ownerId，
    这时由调用方传入「文件里哪个名字是你」，按显示名判断。
    """
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    owner_id = _clean(meta.get("ownerId"), 200)
    me_sender = _clean(me_sender, 100)
    members: dict[str, str] = {}
    for item in payload.get("members") or []:
        if not isinstance(item, dict):
            continue
        platform_id = _clean(item.get("platformId"))
        if platform_id:
            members[platform_id] = _clean(item.get("accountName")) or _clean(item.get("groupNickname")) or platform_id
    messages: list[WeFlowMessage] = []
    for raw in payload.get("messages") or []:
        if not isinstance(raw, dict):
            continue
        timestamp = _int(raw.get("timestamp"))
        if timestamp is None or timestamp <= 0:
            continue
        sender_id = _clean(raw.get("sender"), 200)
        sender = _clean(raw.get("accountName")) or members.get(sender_id) or sender_id or "未知"
        message_type = _message_type(raw.get("type"))
        content = str(raw.get("content") or "").strip()
        if not content:
            content = _message_placeholder(message_type)
        media_path = _clean(raw.get("mediaPath") or raw.get("media_path"), 500)
        is_send = bool(owner_id and sender_id == owner_id)
        if not owner_id and me_sender:
            is_send = sender == me_sender
        messages.append(WeFlowMessage(
            platform_message_id=_clean(raw.get("platformMessageId"), 64),
            sender_id=sender_id,
            sender=sender[:100],
            # 存成与旧导入一致的本地 naive 时间，content_hash 才能对上。
            timestamp=datetime.fromtimestamp(timestamp, TZ).replace(tzinfo=None),
            message_type=message_type,
            content=content[:20_000],
            is_send=is_send,
            is_system=message_type in (80,),
            reply_to_message_id=_clean(raw.get("replyToMessageId"), 64),
            media_path=media_path,
        ))
    sync = payload.get("sync") if isinstance(payload.get("sync"), dict) else {}
    return WeFlowPull(
        owner_id=owner_id,
        members=members,
        messages=messages,
        has_more=bool(sync.get("hasMore")),
        next_since=_int(sync.get("nextSince")),
        next_offset=_int(sync.get("nextOffset")),
        watermark=_int(sync.get("watermark")),
    )


def content_hash(message: WeFlowMessage) -> str:
    """与旧导入一致的哈希公式，便于旧消息去重。"""
    return hashlib.sha256(
        f"{message.timestamp.isoformat()}|{message.sender}|{message.content}".encode("utf-8")
    ).hexdigest()


def iter_pages(client: WeFlowClient, session_id: str, *, since: int = 0,
               limit: int = DEFAULT_LIMIT, retry_empty: bool = False) -> Iterator[WeFlowPull]:
    """按 offset 翻页；调用方负责累计/去重。

    WeFlow 刚启动或刚绑定会话时，第一次拉取偶尔返回空页；`retry_empty` 只在
    第一页为空时等 0.4 秒重试一次，避免把「还没准备好」误判成「没有消息」。
    """
    offset = 0
    total = 0
    first = True
    while total < MAX_MESSAGES_PER_SYNC:
        page = client.pull(session_id, since=since, offset=offset, limit=limit)
        if retry_empty and first and not page.messages:
            time.sleep(0.4)
            page = client.pull(session_id, since=since, offset=offset, limit=limit)
        first = False
        yield page
        total += len(page.messages)
        if not page.has_more or not page.messages:
            break
        next_offset = (page.next_offset if page.next_offset is not None
                       else offset + len(page.messages))
        if next_offset <= offset:
            break
        offset = next_offset
