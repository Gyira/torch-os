"""Obsidian iframe（app:// 顶层）的会话通路（0.99.0）。

嵌在 Obsidian 里的工作台本质是“跨站上下文里的同源应用”：应用自己的 fetch 带
`Sec-Fetch-Site: cross-site` + `Sec-Fetch-Mode: same-origin`，而真正的跨站网页
只能发 cors/no-cors 请求。会话 Cookie 也按上下文签发：普通访问 Strict，
内嵌跨站访问 None+Secure（127.0.0.1 是可信来源，浏览器允许 http 回环上的 Secure Cookie）。
"""

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway

CROSS = {"sec-fetch-site": "cross-site"}
EMBEDDED_FETCH = {"sec-fetch-site": "cross-site", "sec-fetch-mode": "same-origin"}


def make_client(tmp_path):
    app = create_app(
        Database(tmp_path / "embed-session.sqlite3"), tmp_path / "vault",
        ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1),
        security_environ={}, env_file=tmp_path / ".env")
    return TestClient(app)


def test_plain_visit_keeps_the_strict_cookie(tmp_path):
    client = make_client(tmp_path)
    cookie = client.get("/").headers["set-cookie"].lower()
    assert "samesite=strict" in cookie
    assert "secure" not in cookie


def test_embedded_visit_gets_a_none_secure_cookie_and_can_call_the_api(tmp_path):
    client = make_client(tmp_path)
    response = client.get("/", headers=CROSS)
    cookie = response.headers["set-cookie"].lower()
    assert "samesite=none" in cookie and "secure" in cookie

    # httpx 按 RFC 只会在 https 上发送 Secure Cookie（浏览器把 127.0.0.1 视为可信来源、
    # 会照常发送），所以测试里手动带上这个 Cookie 模拟浏览器的行为。
    cookie_header = response.headers["set-cookie"].split(";", 1)[0]
    ok = client.get("/api/config/status", headers={**EMBEDDED_FETCH, "Cookie": cookie_header})
    assert ok.status_code == 200

    # 没有会话的同源模式跨站请求仍然是 401（例外不绕过会话）。
    fresh = make_client(tmp_path)
    denied = fresh.get("/api/config/status", headers=EMBEDDED_FETCH)
    assert denied.status_code == 401


def test_real_cross_site_pages_are_still_rejected(tmp_path):
    client = make_client(tmp_path)
    client.get("/", headers=CROSS)  # 即便先拿到了会话 Cookie
    assert client.get("/api/config/status", headers={**CROSS, "sec-fetch-mode": "cors"}).status_code == 403
    assert client.get("/api/config/status", headers=CROSS).status_code == 403
