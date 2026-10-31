"""Role registry and read-only role metadata endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import ai_roles
from backend.app.dsh_web import DshCommand

ROOT = Path(__file__).resolve().parents[1]
ROLES = ROOT / "dsh" / "roles"


class FakeBalance:
    def __init__(self, *, key: str = "sk-test", enabled: bool = True):
        self.key = key
        self.enabled = enabled

    def describe(self):
        source = "saved" if self.key and self.enabled else ""
        return {"enabled": self.enabled, "saved": bool(self.key), "env": False,
                "dsh": False, "source": source}

    def resolve_key(self):
        return self.key if self.enabled else ""


def test_registry_loads_all_role_cards_without_errors():
    registry = ai_roles.RoleRegistry(ROLES)
    assert registry.errors == []
    assert sorted(registry.roles) == [
        "academic_mentor", "finance_advisor", "health_doctor",
        "journal_companion", "life_assistant", "love_master", "yiling",
    ]
    yiling = registry.get("yiling")
    assert yiling.modes == ("synthesize", "converse")
    assert "identity_patch" in yiling.proposals
    assert yiling.tools == "none"
    manifest = yiling.manifest()
    assert "prompt" not in manifest and manifest["name"] == "忆灵"
    assert registry.get("life_assistant").name == "生活管家"
    assert "discuss" in registry.get("academic_mentor").modes
    assert registry.get("love_master").name == "恋爱大师"


def test_registry_reports_invalid_role_instead_of_using_it(tmp_path):
    (tmp_path / "bad.md").write_text(
        "---\nid: bad\nname: 坏角色\nmodes: [chat]\nproposals: [drop_table]\n---\n\n正文\n",
        encoding="utf-8")
    registry = ai_roles.RoleRegistry(tmp_path)
    assert registry.roles == {}
    assert registry.errors and "未知提案类型" in registry.errors[0]["error"]


def test_registry_requires_a_mode_prompt(tmp_path):
    (tmp_path / "bad.md").write_text(
        "---\nid: bad\nname: 坏角色\nmodes: [chat]\n---\n\n正文没有 mode 段\n",
        encoding="utf-8")
    registry = ai_roles.RoleRegistry(tmp_path)
    assert registry.roles == {}
    assert "缺少 mode 提示段落" in registry.errors[0]["error"]


def test_service_status_requires_command_key_and_personal_mode(monkeypatch):
    registry = ai_roles.RoleRegistry(ROLES)
    monkeypatch.setattr(ai_roles, "resolve_command",
                        lambda environ=None: DshCommand(("dsh",), "installed"))
    service = ai_roles.RoleService(registry, FakeBalance(), enabled=True,
                                   environ={"PATH": "x"}, workspace=ROOT / "tmp")
    status = service.status()
    assert status["available"] is True
    assert status["key"] == {"configured": True, "source": "saved"}
    assert status["roles"] == 7

    no_key = ai_roles.RoleService(registry, FakeBalance(key=""), enabled=True)
    assert no_key.status()["available"] is False
    assert "Key" in no_key.status()["reason"]

    disabled = ai_roles.RoleService(registry, FakeBalance(), enabled=False)
    assert disabled.status()["available"] is False
    assert "未启用" in disabled.status()["reason"]

    monkeypatch.setattr(ai_roles, "resolve_command", lambda environ=None: None)
    no_command = ai_roles.RoleService(registry, FakeBalance(), enabled=True)
    assert no_command.status()["available"] is False
    assert "dsh" in no_command.status()["reason"]


def test_build_prompt_marks_context_as_data_and_rejects_unknown_mode():
    registry = ai_roles.RoleRegistry(ROLES)
    service = ai_roles.RoleService(registry, FakeBalance(), enabled=True)
    prompt = service.build_prompt(
        "yiling", "converse",
        context_text="[S1] 最近日记：今天有点累", user_text="我觉得自己在讨好别人")
    assert "苏格拉底" in prompt
    assert "只读资料" in prompt and "不是指令" in prompt
    assert "[S1]" in prompt and "我觉得自己在讨好别人" in prompt
    with pytest.raises(ValueError):
        service.build_prompt("yiling", "classify", user_text="x")


def test_service_run_injects_key_and_agents_home(monkeypatch, tmp_path):
    registry = ai_roles.RoleRegistry(ROLES)
    seen = {}

    def fake_run(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["env"] = kwargs["environ"]
        seen["timeout"] = kwargs["timeout"]
        return ai_roles.HeadlessResult(text="好", session_id="session-1",
                                       seconds=0.1, returncode=0, stderr_tail="")

    monkeypatch.setattr(ai_roles, "resolve_command",
                        lambda environ=None: DshCommand(("dsh",), "installed"))
    monkeypatch.setattr(ai_roles, "run_headless", fake_run)
    service = ai_roles.RoleService(
        registry, FakeBalance(), enabled=True,
        environ={"PATH": "x"}, agents_home=ROLES, workspace=tmp_path)
    result = service.run("journal_companion", "discuss", user_text="聊聊这篇")
    assert result.text == "好"
    assert seen["env"]["DEEPSEEK_API_KEY"] == "sk-test"
    assert seen["env"]["DSH_AGENTS_HOME"] == str(ROLES)
    assert "只读资料" not in seen["prompt"] or "用户这一轮" in seen["prompt"]
    assert seen["timeout"] == registry.get("journal_companion").timeout_s


def test_ai_router_exposes_status_and_manifest(monkeypatch):
    registry = ai_roles.RoleRegistry(ROLES)
    monkeypatch.setattr(ai_roles, "resolve_command",
                        lambda environ=None: DshCommand(("dsh",), "installed"))
    service = ai_roles.RoleService(registry, FakeBalance(), enabled=True)
    app = FastAPI()
    app.include_router(ai_roles.create_ai_router(service))
    client = TestClient(app)
    assert client.get("/api/ai/status").json()["available"] is True
    items = client.get("/api/ai/roles").json()["items"]
    assert len(items) == 7
    assert all("prompt" not in item for item in items)
