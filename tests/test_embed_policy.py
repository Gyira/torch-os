"""Only the local dsh web UI and the local Obsidian app may frame the workbench."""

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def test_only_local_dsh_and_obsidian_origins_may_frame_the_workbench(tmp_path):
    database = Database(tmp_path / "embed.sqlite3")
    app = create_app(
        database, tmp_path / "vault",
        ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1),
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    response = client.get("/")
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'self' http://127.0.0.1:* http://localhost:* app:" in csp
    assert "frame-ancestors 'none'" not in csp
    assert "x-frame-options" not in response.headers
    # 其它来源仍然没有机会：没有通配 *、没有 https:
    assert "frame-ancestors *" not in csp and "https:" not in csp
