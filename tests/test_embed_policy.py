"""Only the local dsh web UI and the local Obsidian app may frame the workbench."""

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database


def test_only_local_dsh_and_obsidian_origins_may_frame_the_workbench(tmp_path):
    database = Database(tmp_path / "embed.sqlite3")
    app = create_app(
        database, tmp_path / "vault",
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    response = client.get("/")
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'self' http://127.0.0.1:* http://localhost:* app:" in csp
    assert "frame-ancestors 'none'" not in csp
    assert "x-frame-options" not in response.headers
    # 仅图片允许用户自定义的 https 背景；脚本/连接仍限定 self。
    assert "img-src 'self' data: https:" in csp
    assert "script-src 'self'" in csp and "connect-src 'self'" in csp
    # 其它来源仍然没有机会：frame-ancestors 里没有任意来源 *、没有 https:
    frame_ancestors = csp.split("frame-ancestors", 1)[1].split(";", 1)[0]
    assert "https:" not in frame_ancestors
    assert "frame-ancestors *" not in csp
