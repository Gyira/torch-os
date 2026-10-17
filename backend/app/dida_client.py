"""滴答清单私有 V2 Web API 的只读客户端（Phase 1）。

只做拉取：全量快照 + 已完成任务。认证使用浏览器会话 cookie `t`；
写入所需的 `_csrf_token` 也一并保存，但本阶段不发送写请求。
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

HOSTS = {
    "dida365": "https://api.dida365.com",
    "ticktick": "https://api.ticktick.com",
}
DEFAULT_REGION = "dida365"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_DEVICE_VERSION = 8000
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_COOKIE_CHARS = 8192
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class DidaError(Exception):
    """稳定的错误码；message 只放安全文案，不回显 cookie/响应正文。"""

    def __init__(self, code: str, message: str, *, retry_after: int | None = None,
                 status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.status = status


@dataclass(frozen=True)
class DidaCredentials:
    region: str = DEFAULT_REGION
    cookie: str = field(default="", repr=False)
    csrf_token: str = field(default="", repr=False)
    device_id: str = ""
    device_version: int = DEFAULT_DEVICE_VERSION
    timezone: str = "Asia/Shanghai"


@dataclass(frozen=True)
class RemoteChecklistItem:
    id: str
    title: str
    status: int


@dataclass(frozen=True)
class RemoteTask:
    id: str
    project_id: str
    title: str
    content: str
    due_date: str
    start_date: str
    is_all_day: bool
    is_floating: bool
    time_zone: str
    priority: int
    status: int
    completed_time: str
    created_time: str
    tags: tuple[str, ...]
    items: tuple[RemoteChecklistItem, ...]
    kind: str
    etag: str
    modified_time: str
    deleted: int
    repeat_flag: str
    reminders: tuple[Any, ...]
    attachments: tuple[Any, ...]
    comment_count: int


@dataclass(frozen=True)
class RemoteProject:
    id: str
    name: str
    color: str
    sort_order: int
    closed: bool
    view_mode: str
    group_id: str
    etag: str
    modified_time: str


@dataclass(frozen=True)
class RemoteSnapshot:
    checkpoint: int
    inbox_id: str
    projects: tuple[RemoteProject, ...]
    tasks: tuple[RemoteTask, ...]
    deleted_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class DidaProvider(Protocol):
    async def fetch_snapshot(self, checkpoint: int = 0) -> RemoteSnapshot: ...
    async def fetch_completed(self) -> tuple[RemoteTask, ...]: ...
    async def test_connection(self) -> dict[str, Any]: ...
    async def aclose(self) -> None: ...


def _as_str(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return ""


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _check_cookie_text(value: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > MAX_COOKIE_CHARS or _CONTROL.search(text):
        raise DidaError("invalid_credentials", "登录凭据格式不正确，请重新粘贴")
    return text


def parse_cookie_input(value: str) -> tuple[str, str]:
    """接受裸 `t` 值或完整 Cookie 头；只保留 `t` 和 `_csrf_token`。"""
    text = _check_cookie_text(value)
    if "=" not in text:
        return text, ""
    pairs: dict[str, str] = {}
    for part in text.split(";"):
        if not part.strip() or "=" not in part:
            continue
        key, _, item = part.partition("=")
        key = key.strip()
        if key in {"t", "_csrf_token"} and item.strip():
            pairs[key] = item.strip()
    if not pairs:
        # 没有可识别的键名时，把整段当作裸 t 值（某些 base64 值带 =）。
        return text, ""
    cookie = pairs.get("t", "")
    if not cookie:
        raise DidaError("invalid_credentials", "Cookie 里缺少 t 值，请重新复制")
    return cookie, pairs.get("_csrf_token", "")


def _task_from_raw(raw: dict[str, Any]) -> RemoteTask:
    items = tuple(
        RemoteChecklistItem(
            id=_as_str(item.get("id")),
            title=_as_str(item.get("title") or item.get("name")),
            status=_as_int(item.get("status")),
        )
        for item in _as_list(raw.get("items"))
        if isinstance(item, dict)
    )
    tags = tuple(
        _as_str(item.get("name") if isinstance(item, dict) else item)
        for item in _as_list(raw.get("tags"))
        if _as_str(item.get("name") if isinstance(item, dict) else item)
    )
    reminders = tuple(_as_list(raw.get("reminders")))
    attachments = tuple(_as_list(raw.get("attachments")))
    return RemoteTask(
        id=_as_str(raw.get("id")),
        project_id=_as_str(raw.get("projectId")),
        title=_as_str(raw.get("title")),
        content=_as_str(raw.get("content")),
        due_date=_as_str(raw.get("dueDate")),
        start_date=_as_str(raw.get("startDate")),
        is_all_day=_as_bool(raw.get("isAllDay")),
        is_floating=_as_bool(raw.get("isFloating")),
        time_zone=_as_str(raw.get("timeZone")),
        priority=_as_int(raw.get("priority")),
        status=_as_int(raw.get("status")),
        completed_time=_as_str(raw.get("completedTime")),
        created_time=_as_str(raw.get("createdTime")),
        tags=tags,
        items=items,
        kind=_as_str(raw.get("kind")),
        etag=_as_str(raw.get("etag")),
        modified_time=_as_str(raw.get("modifiedTime")),
        deleted=_as_int(raw.get("deleted")),
        repeat_flag=_as_str(raw.get("repeatFlag")),
        reminders=reminders,
        attachments=attachments,
        comment_count=_as_int(raw.get("commentCount")),
    )


def _project_from_raw(raw: dict[str, Any]) -> RemoteProject:
    return RemoteProject(
        id=_as_str(raw.get("id")),
        name=_as_str(raw.get("name")),
        color=_as_str(raw.get("color")),
        sort_order=_as_int(raw.get("sortOrder")),
        closed=_as_bool(raw.get("closed")),
        view_mode=_as_str(raw.get("viewMode")),
        group_id=_as_str(raw.get("groupId")),
        etag=_as_str(raw.get("etag")),
        modified_time=_as_str(raw.get("modifiedTime")),
    )


class PrivateDidaProvider:
    """滴答私有 V2 API 的 httpx 实现。"""

    def __init__(self, credentials: DidaCredentials, *,
                 client: httpx.AsyncClient | None = None):
        if credentials.region not in HOSTS:
            raise DidaError("invalid_region", "只支持 dida365 或 ticktick")
        if not credentials.cookie.strip():
            raise DidaError("not_configured", "还没有保存滴答登录凭据")
        self.credentials = credentials
        self.host = HOSTS[credentials.region]
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
            follow_redirects=False,
            trust_env=False,
        )

    def _headers(self) -> dict[str, str]:
        device = {
            "platform": "web",
            "version": int(self.credentials.device_version or DEFAULT_DEVICE_VERSION),
            "id": self.credentials.device_id or "torch-os",
        }
        cookie = f"t={self.credentials.cookie}"
        headers = {
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json",
            "x-device": json.dumps(device, ensure_ascii=False, separators=(",", ":")),
            "hl": "zh_CN" if self.credentials.region == "dida365" else "en_US",
            "x-tz": self.credentials.timezone or "Asia/Shanghai",
            "traceid": secrets.token_hex(16),
        }
        if self.credentials.csrf_token:
            # 滴答网页端所有请求都带 CSRF；读请求带上也不影响，写回阶段直接复用。
            cookie += f"; _csrf_token={self.credentials.csrf_token}"
            headers["X-Csrftoken"] = self.credentials.csrf_token
        headers["Cookie"] = cookie
        return headers

    @staticmethod
    def _retry_after(response: httpx.Response) -> int | None:
        raw = response.headers.get("retry-after", "").strip()
        if raw.isdigit():
            return min(30, int(raw))
        return None

    @staticmethod
    def _backoff(attempt: int) -> float:
        # 1s、2s，加一点抖动，避免多实例同时重试。
        return min(30.0, (2 ** max(0, attempt - 1)) + (time.monotonic() % 0.4))

    async def _get_json(self, path: str, *, params: dict[str, Any] | None = None,
                        attempts: int = 3) -> Any:
        last: DidaError | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.get(
                    f"{self.host}{path}", params=params, headers=self._headers()
                )
            except (httpx.TimeoutException, httpx.TransportError) as error:
                last = DidaError("network_error", f"连接滴答失败：{type(error).__name__}")
                if attempt < attempts:
                    await asyncio.sleep(self._backoff(attempt))
                    continue
                raise last from None

            status = response.status_code
            if status in (401, 403):
                raise DidaError("bad_cookie", "滴答登录已失效，请重新粘贴 cookie", status=status)
            if status == 429:
                retry_after = self._retry_after(response)
                last = DidaError("rate_limited", "滴答限流，请稍后再试",
                                 retry_after=retry_after, status=status)
                if attempt < attempts:
                    await asyncio.sleep(retry_after or self._backoff(attempt))
                    continue
                raise last
            if status >= 500:
                last = DidaError(f"http_{status}", "滴答服务暂时不可用", status=status)
                if attempt < attempts:
                    await asyncio.sleep(self._backoff(attempt))
                    continue
                raise last
            if status >= 400:
                raise DidaError(f"http_{status}", f"滴答接口返回 {status}", status=status)
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise DidaError("invalid_response", "滴答返回内容过大")
            try:
                return response.json()
            except ValueError:
                raise DidaError("invalid_response", "滴答返回了无法解析的内容") from None
        raise last or DidaError("network_error", "连接滴答失败")

    async def _snapshot_raw(self, checkpoint: int) -> dict[str, Any]:
        path = f"/api/v3/batch/check/{int(checkpoint)}"
        try:
            raw = await self._get_json(path)
        except DidaError as error:
            if error.code not in {"http_404", "http_405"}:
                raise
            raw = await self._get_json(f"/api/v2/batch/check/{int(checkpoint)}")
        if not isinstance(raw, dict):
            raise DidaError("invalid_response", "滴答同步包格式不正确")
        if "checkPoint" not in raw and "checkpoint" not in raw:
            # v2 兼容端点可能不带游标；Phase 1 全量拉取不依赖它。
            raw["checkPoint"] = int(checkpoint)
        return raw

    async def fetch_snapshot(self, checkpoint: int = 0) -> RemoteSnapshot:
        raw = await self._snapshot_raw(checkpoint)
        bean = raw.get("syncTaskBean") if isinstance(raw.get("syncTaskBean"), dict) else {}
        tasks: dict[str, RemoteTask] = {}
        for group in ("add", "update"):
            for item in _as_list(bean.get(group)):
                if not isinstance(item, dict):
                    continue
                task = _task_from_raw(item)
                if task.id:
                    tasks[task.id] = task
        deleted_ids: list[str] = []
        for item in _as_list(bean.get("delete")):
            if isinstance(item, dict):
                item_id = _as_str(item.get("id"))
            else:
                item_id = _as_str(item)
            if item_id:
                deleted_ids.append(item_id)
        projects = tuple(
            _project_from_raw(item)
            for item in _as_list(raw.get("projectProfiles"))
            if isinstance(item, dict) and _as_str(item.get("id"))
        )
        checkpoint_value = _as_int(raw.get("checkPoint", raw.get("checkpoint")))
        return RemoteSnapshot(
            checkpoint=checkpoint_value,
            inbox_id=_as_str(raw.get("inboxId")),
            projects=projects,
            tasks=tuple(tasks.values()),
            deleted_ids=tuple(dict.fromkeys(deleted_ids)),
        )

    async def fetch_completed(self) -> tuple[RemoteTask, ...]:
        try:
            raw = await self._get_json("/api/v2/project/all/closed", params={"status": "Completed"})
        except DidaError as error:
            if error.code not in {"http_400", "http_404", "http_405"}:
                raise
            now_ms = int(time.time() * 1000)
            raw = await self._get_json(
                "/api/v2/project/all/completed/",
                params={"from": 0, "to": now_ms, "limit": 600},
            )
        rows = raw if isinstance(raw, list) else raw.get("tasks", []) if isinstance(raw, dict) else []
        result: list[RemoteTask] = []
        for item in _as_list(rows):
            if not isinstance(item, dict):
                continue
            task = _task_from_raw(item)
            if task.id:
                result.append(task)
        return tuple(result)

    async def test_connection(self) -> dict[str, Any]:
        started = time.monotonic()
        snapshot = await self.fetch_snapshot(0)
        return {
            "ok": True,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "task_count": len(snapshot.tasks),
            "project_count": len(snapshot.projects),
            "checkpoint": snapshot.checkpoint,
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def make_provider(credentials: DidaCredentials, *,
                  client: httpx.AsyncClient | None = None) -> PrivateDidaProvider:
    return PrivateDidaProvider(credentials, client=client)


__all__ = [
    "DEFAULT_DEVICE_VERSION", "DEFAULT_REGION", "DidaCredentials", "DidaError",
    "DidaProvider", "PrivateDidaProvider", "RemoteChecklistItem", "RemoteProject",
    "RemoteSnapshot", "RemoteTask", "make_provider", "parse_cookie_input",
]
