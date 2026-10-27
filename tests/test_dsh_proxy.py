"""Same-origin dsh proxy: cookie injection, authority, streaming and fences."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from backend.app.dsh_proxy import DshProxy, _rewrite_location, create_dsh_router
from backend.app.dsh_web import DshError, DshLaunch


class FakeRuntime:
    enabled = True
    generation = 1

    def __init__(self):
        self.launch = DshLaunch(port=54321, token="launch-token",
                                url="http://127.0.0.1:54321/?token=launch-token")
        self.started = False

    def upstream(self):
        return self.launch

    def status(self):
        return {"state": "ready", "generation": self.generation}

    def request_start(self):
        self.started = True

    def request_restart(self):
        self.started = True


def _app(proxy):
    app = FastAPI()
    app.include_router(create_dsh_router(FakeRuntime(), proxy))
    return app


def test_cookie_injection_preserves_host_and_strips_browser_cookies():
    seen = []

    async def upstream(request: httpx.Request):
        seen.append(request)
        path = request.url.path
        cookie = request.headers.get("cookie", "")
        if path == "/" and request.url.params.get("token"):
            assert request.headers.get("host") == "127.0.0.1:8765"
            return httpx.Response(303, headers={
                "location": "./",
                "set-cookie": "dsh-auth-test=secret; Path=/; HttpOnly; SameSite=Strict",
            })
        if path == "/" and "dsh-auth-test=secret" in cookie:
            return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                                  text="<html><base href='./'>harness</html>")
        if path == "/api/echo":
            return httpx.Response(200, json={
                "host": request.headers.get("host"),
                "cookie": cookie,
                "origin": request.headers.get("origin"),
                "site": request.headers.get("sec-fetch-site"),
                "xff": request.headers.get("x-forwarded-for"),
            })
        return httpx.Response(401, text="unauthorized")

    async def scenario():
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        proxy = DshProxy(FakeRuntime(), client=upstream_client)
        app = _app(proxy)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
            index = await client.get("/harness/", headers={"cookie": "torch_session_x=leak"})
            assert index.status_code == 200
            assert "harness" in index.text
            assert "set-cookie" not in index.headers
            assert "frame-ancestors" in index.headers["content-security-policy"]
            echoed = await client.get("/harness/api/echo", headers={
                "cookie": "torch_session_x=leak",
                "origin": "http://127.0.0.1:8765",
                "sec-fetch-site": "cross-site",
            })
            payload = echoed.json()
            assert payload["host"] == "127.0.0.1:8765"
            assert "dsh-auth-test=secret" in payload["cookie"]
            assert "torch_session_x" not in payload["cookie"]
            assert payload["origin"] == "http://127.0.0.1:8765"
            assert payload["site"] == "same-origin"
            assert payload["xff"] is None
        await upstream_client.aclose()

    asyncio.run(scenario())


def test_root_redirect_and_location_rewrite():
    proxy = DshProxy(FakeRuntime(), client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200))))

    async def scenario():
        app = _app(proxy)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
            response = await client.get("/harness", follow_redirects=False)
            assert response.status_code == 307
            assert response.headers["location"] == "/harness/"
        await proxy.close()

    asyncio.run(scenario())
    launch = DshLaunch(port=54321, token="t", url="")
    assert _rewrite_location("http://127.0.0.1:54321/api/x?a=1", launch, "127.0.0.1:8765") == "/harness/api/x?a=1"
    assert _rewrite_location("./", launch, "127.0.0.1:8765") == "./"
    assert _rewrite_location("/api/x", launch, "127.0.0.1:8765") == "/harness/api/x"


def test_cross_origin_is_rejected_and_non_loopback_is_rejected():
    async def upstream(request: httpx.Request):
        return httpx.Response(200, text="should not be called")

    async def scenario():
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        proxy = DshProxy(FakeRuntime(), client=upstream_client)
        app = _app(proxy)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
            response = await client.get("/harness/", headers={"origin": "https://evil.example"})
            assert response.status_code == 403
        await upstream_client.aclose()

    asyncio.run(scenario())


def test_restart_is_refused_while_a_shared_dsh_has_another_owner():
    class _SharedRuntime(FakeRuntime):
        def request_restart(self):
            raise DshError("另一个 torch 入口正在使用这个 dsh；先关掉那个入口，再重启。")

    async def scenario():
        runtime = _SharedRuntime()
        proxy = DshProxy(runtime, client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200))))
        app = FastAPI()
        app.include_router(create_dsh_router(runtime, proxy))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
            response = await client.post("/api/dsh/restart")
            assert response.status_code == 409
            assert "另一个 torch 入口" in response.json()["detail"]
        await proxy.close()

    asyncio.run(scenario())
