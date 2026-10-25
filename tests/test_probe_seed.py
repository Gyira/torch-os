"""The QA browser probe may seed disposable data, but never a real vault."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import browser_pillars_probe as probe  # noqa: E402

SHANGHAI = ZoneInfo("Asia/Shanghai")


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeClient:
    """按 URL 分派的假客户端：只记录调用，不碰网络。"""

    def __init__(self, health: dict, vault: Path | None = None):
        self.health = health
        self.vault = vault
        self.calls: list[tuple[str, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, url: str, params: dict | None = None):
        self.calls.append(("GET", url))
        if url == "/api/health":
            return FakeResponse(200, self.health)
        if url == "/api/vault/connection":
            return FakeResponse(200, {"path": str(self.vault or "")})
        if url == "/api/time/tasks":
            return FakeResponse(200, {"tasks": [], "revision": "qa-rev"})
        return FakeResponse(200, {})

    def post(self, url: str, json: dict | None = None):
        self.calls.append(("POST", url))
        return FakeResponse(200, {})


def install(monkeypatch, client: FakeClient):
    monkeypatch.setattr(probe.httpx, "Client", lambda **_kwargs: client)
    return client


def test_real_vault_is_never_written(monkeypatch):
    client = install(monkeypatch, FakeClient({"runtime_label": "personal"}))
    result = probe.seed_qa_workspace("http://127.0.0.1:8765")
    assert result["skipped"] == "not-qa"
    assert [call[0] for call in client.calls] == ["GET", "GET"]
    assert not any(call[0] == "POST" for call in client.calls)


def test_qa_workspace_without_marker_is_skipped(monkeypatch, tmp_path):
    workspace = tmp_path / "qa-no-marker"
    vault = workspace / "vault"
    vault.mkdir(parents=True)
    client = install(monkeypatch, FakeClient({"runtime_label": "qa"}, vault=vault))
    result = probe.seed_qa_workspace("http://127.0.0.1:8766")
    assert result["skipped"] == "not-a-disposable-qa-vault"
    assert list(tmp_path.rglob("*.md")) == []
    assert not any(call[0] == "POST" for call in client.calls)


def test_disposable_qa_workspace_gets_readonly_fixtures(monkeypatch, tmp_path):
    workspace = tmp_path / "qa-probe"
    vault = workspace / "vault"
    vault.mkdir(parents=True)
    (workspace / probe.WORKSPACE_MARKER).write_text(json.dumps({
        "schema": 1, "environment": "qa", "workspace": workspace.name,
    }), encoding="utf-8")
    client = install(monkeypatch, FakeClient({"runtime_label": "qa"}, vault=vault))

    result = probe.seed_qa_workspace("http://127.0.0.1:8766")

    assert result["seeded"] is True
    assert result["scan"] == 200
    day = datetime.now(SHANGHAI).date()
    written = sorted(path.relative_to(vault).as_posix() for path in vault.rglob("*.md"))
    assert f"00_人生路/01_日记/{day.isoformat()}.md" in written
    assert "03_知识库/00_收件箱/QA 整理收件箱五步法.md" in written
    assert "03_知识库/01_主题笔记/考研规划.md" in written
    assert f"02_生活运营/01_健康与体重/{day.strftime('%Y-%m')}.md" in written
    assert len(written) == 6
    posted = [call[1] for call in client.calls if call[0] == "POST"]
    assert posted[0] == "/api/vault/scan"
    # 旧的 /api/capture 已经下线，种子不应再调用它。
    assert "/api/capture" not in posted
