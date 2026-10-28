"""Same-origin HTTP/WebSocket proxy for the managed dsh web UI.

The browser only talks to torch's origin.  This matters twice:

* dsh's auth cookie is ``SameSite=Strict``; inside Obsidian the top-level site
  is ``app://obsidian.md`` even when the iframe itself is same-origin with
  torch.  The proxy therefore keeps the dsh cookie server-side and injects it
  into every upstream request.
* dsh expects its own ``Host``/``Origin`` authority and a WebSocket upgrade;
  both are forwarded explicitly.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from .dsh_web import DshError, DshLaunch, DshWebRuntime

_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "content-length",
}
_REQUEST_DROP = {
    "host", "cookie", "authorization", "x-api-token", "proxy-authorization",
    "connection", "keep-alive", "proxy-authenticate", "te", "trailer",
    "transfer-encoding", "upgrade", "expect", "content-length",
}
_HTML_CSP = (
    "frame-ancestors 'self' http://127.0.0.1:* http://localhost:* app:"
)
_LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "testclient"}


def _is_loopback_client(host: str | None) -> bool:
    value = (host or "").strip().strip("[]").lower()
    return value in _LOOPBACK_CLIENTS or value.startswith("127.")


def _authority(request_or_ws: Any) -> str:
    return str(request_or_ws.headers.get("host") or "").strip()


def _expected_origin(scheme: str, authority: str) -> str:
    return f"{scheme}://{authority}".rstrip("/")


def _parse_cookie(raw: str) -> tuple[str, str] | None:
    first = str(raw or "").split(";", 1)[0].strip()
    if "=" not in first:
        return None
    name, value = first.split("=", 1)
    name = name.strip()
    return (name, value) if name else None


def _merge_cookies(jar: dict[str, str], headers: Iterable[str]) -> None:
    for raw in headers:
        parsed = _parse_cookie(raw)
        if parsed is not None:
            jar[parsed[0]] = parsed[1]


def _cookie_header(jar: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in jar.items())


def _rewrite_location(location: str, launch: DshLaunch, authority: str) -> str:
    value = str(location or "")
    if not value:
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        if parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port == launch.port:
            path = parsed.path or "/"
            return "/harness" + (path if path.startswith("/") else "/" + path) + (
                f"?{parsed.query}" if parsed.query else "")
        return value
    if value.startswith("/") and not value.startswith("/harness"):
        return "/harness" + value
    return value


class DshProxy:
    def __init__(self, runtime: DshWebRuntime, *, client: httpx.AsyncClient | None = None):
        self.runtime = runtime
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=None, write=None, pool=None),
            follow_redirects=False, trust_env=False,
        )
        self._own_client = client is None
        self._cookie_lock = asyncio.Lock()
        self._cookies: dict[tuple[int, str], dict[str, str]] = {}

    async def close(self) -> None:
        if self._own_client:
            await self._client.aclose()

    def invalidate(self) -> None:
        self._cookies.clear()

    async def _cookie_for(self, authority: str) -> str:
        launch = self.runtime.upstream()
        key = (self.runtime.generation, authority)
        async with self._cookie_lock:
            jar = self._cookies.get(key)
            if jar is None:
                url = f"http://127.0.0.1:{launch.port}/?token={quote(launch.token, safe='')}"
                try:
                    response = await self._client.get(
                        url, headers={"Host": authority}, follow_redirects=False,
                        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
                    )
                except httpx.HTTPError as exc:
                    raise DshError(f"无法通过本地 dsh 认证：{exc}") from exc
                jar = {}
                _merge_cookies(jar, response.headers.get_list("set-cookie"))
                if not jar:
                    raise DshError("本地 dsh 没有返回登录 cookie")
                self._cookies[key] = jar
            return _cookie_header(jar)

    def _request_headers(self, request: Request, authority: str, cookie: str) -> dict[str, str]:
        headers: dict[str, str] = {}
        for name, value in request.headers.items():
            lower = name.lower()
            if (lower in _REQUEST_DROP or lower.startswith("sec-websocket-")
                    or lower == "sec-fetch-site"):
                continue
            if lower == "origin" and value.strip().lower() == "null":
                continue
            headers[name] = value
        headers["Host"] = authority
        headers["Cookie"] = cookie
        # dsh rejects the literal cross-site value.  The proxy has already
        # validated Host/Origin and is the only path to dsh.
        headers["Sec-Fetch-Site"] = "same-origin"
        return headers

    def _fence(self, request: Request, authority: str) -> str | None:
        if not _is_loopback_client(request.client.host if request.client else None):
            return "Harness 只允许本机访问"
        if not authority:
            return "缺少 Host"
        origin = request.headers.get("origin")
        if origin and origin != "null" and origin.rstrip("/") != _expected_origin(
                request.url.scheme, authority):
            return "拒绝跨来源 Harness 请求"
        return None

    async def handle_http(self, request: Request, subpath: str) -> Response:
        authority = _authority(request)
        refusal = self._fence(request, authority)
        if refusal:
            return JSONResponse({"detail": refusal}, status_code=403)
        if request.url.path == "/harness":
            return RedirectResponse("/harness/", status_code=307)
        try:
            launch = self.runtime.upstream()
        except DshError as exc:
            return Response(
                f"DeepSeek Harness 尚未就绪：{exc}",
                status_code=503, media_type="text/plain; charset=utf-8",
                headers={"Retry-After": "1"},
            )
        target_path = "/" + subpath if subpath else "/"
        query = request.url.query
        target = f"http://127.0.0.1:{launch.port}{target_path}" + (f"?{query}" if query else "")
        body = await request.body()
        upstream: httpx.Response | None = None
        cookie = ""
        for attempt in range(2):
            try:
                cookie = await self._cookie_for(authority)
            except DshError as exc:
                return Response(str(exc), status_code=502,
                                media_type="text/plain; charset=utf-8")
            headers = self._request_headers(request, authority, cookie)
            if body:
                headers["Content-Length"] = str(len(body))
            request_headers = headers
            upstream = await self._client.send(
                self._client.build_request(request.method, target, headers=request_headers,
                                           content=body),
                stream=True,
            )
            if upstream.status_code == 401 and attempt == 0:
                await upstream.aclose()
                self.invalidate()
                continue
            break
        if upstream is None:
            return Response("dsh 没有响应", status_code=502, media_type="text/plain; charset=utf-8")
        # Keep dsh's refreshed cookies server-side; never hand them to the page.
        key = (self.runtime.generation, authority)
        jar = self._cookies.setdefault(key, {})
        _merge_cookies(jar, upstream.headers.get_list("set-cookie"))

        out_headers: list[tuple[str, str]] = []
        content_type = ""
        for name, value in upstream.headers.multi_items():
            lower = name.lower()
            if lower in _HOP_HEADERS or lower == "set-cookie":
                continue
            if lower == "location":
                value = _rewrite_location(value, launch, authority)
            if lower == "content-type":
                content_type = value.lower()
            out_headers.append((name, value))
        if "text/html" in content_type:
            out_headers.append(("Content-Security-Policy", _HTML_CSP))

        async def body_stream():
            try:
                if upstream.is_stream_consumed:
                    # Buffered responses (including test transports) have no
                    # live byte iterator left; return their captured body.
                    if upstream.content:
                        yield upstream.content
                else:
                    async for chunk in upstream.aiter_raw():
                        yield chunk
            finally:
                await upstream.aclose()

        response = StreamingResponse(body_stream(), status_code=upstream.status_code)
        response.raw_headers = [
            (name.encode("latin-1"), value.encode("latin-1"))
            for name, value in out_headers
        ]
        return response

    async def handle_websocket(self, websocket: WebSocket, subpath: str) -> None:
        authority = _authority(websocket)
        client_host = websocket.client.host if websocket.client else None
        if not _is_loopback_client(client_host):
            await websocket.close(code=1008)
            return
        browser_scheme = "https" if websocket.url.scheme == "wss" else "http"
        expected = _expected_origin(browser_scheme, authority)
        origin = websocket.headers.get("origin")
        if origin and origin != "null" and origin.rstrip("/") != expected:
            await websocket.close(code=1008)
            return
        try:
            launch = self.runtime.upstream()
            cookie = await self._cookie_for(authority)
        except DshError:
            await websocket.close(code=1011)
            return
        path = "/" + subpath if subpath else "/"
        query = websocket.url.query
        # The URI supplies the browser-facing Host header; host/port override
        # only the TCP destination, so dsh authenticates against the torch
        # authority instead of its private upstream port.
        target = f"ws://{authority}{path}" + (f"?{query}" if query else "")
        headers = {
            "Cookie": cookie,
            "Origin": origin if origin and origin != "null" else expected,
            "Sec-Fetch-Site": "same-origin",
        }
        subprotocols = websocket.scope.get("subprotocols") or None
        try:
            upstream = await websockets.connect(
                target, host="127.0.0.1", port=launch.port, proxy=None,
                additional_headers=headers, subprotocols=subprotocols,
                compression=None, max_size=16 * 1024 * 1024, open_timeout=10,
            )
        except Exception:
            await websocket.close(code=1011)
            return
        await websocket.accept(subprotocol=upstream.subprotocol)

        async def client_to_upstream() -> None:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                if message.get("text") is not None:
                    await upstream.send(message["text"])
                elif message.get("bytes") is not None:
                    await upstream.send(message["bytes"])

        async def upstream_to_client() -> None:
            try:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)
            finally:
                code = getattr(upstream, "close_code", None) or 1000
                reason = getattr(upstream, "close_reason", "") or ""
                with contextlib.suppress(Exception):
                    await websocket.close(code=code, reason=reason)

        tasks = [asyncio.create_task(client_to_upstream()),
                 asyncio.create_task(upstream_to_client())]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    task.result()
        finally:
            for task in tasks:
                task.cancel()
            with contextlib.suppress(Exception):
                await upstream.close()


def create_dsh_router(runtime: DshWebRuntime, proxy: DshProxy) -> APIRouter:
    router = APIRouter()

    @router.get("/api/dsh/status")
    def dsh_status():
        return runtime.status()

    @router.post("/api/dsh/start")
    async def dsh_start():
        runtime.request_start()
        return runtime.status()

    @router.post("/api/dsh/restart")
    async def dsh_restart():
        proxy.invalidate()
        try:
            runtime.request_restart()
        except DshError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return runtime.status()

    @router.get("/harness")
    async def harness_root(request: Request):
        return await proxy.handle_http(request, "")

    @router.api_route("/harness/", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def harness_index(request: Request):
        return await proxy.handle_http(request, "")

    @router.api_route("/harness/{subpath:path}",
                      methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def harness_http(request: Request, subpath: str):
        return await proxy.handle_http(request, subpath)

    @router.websocket("/harness/{subpath:path}")
    async def harness_ws(websocket: WebSocket, subpath: str):
        await proxy.handle_websocket(websocket, subpath)

    return router
