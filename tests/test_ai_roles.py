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
        "academic_mentor", "finance_advisor", "health_doctor", "health_estimator",
        "journal_companion", "life_assistant", "love_master", "mentor", "yiling",
    ]
    yiling = registry.get("yiling")
    assert yiling.modes == ("synthesize", "converse")
    assert "identity_patch" in yiling.proposals
    assert yiling.tools == "none"
    assert yiling.manifest()["name"] == "忆灵"
    assert yiling.legacy is True
    estimator = registry.get("health_estimator")
    assert estimator.internal is True
    visible = registry.manifest()
    assert visible["legacy_count"] == 1
    assert visible["internal_count"] == 1
    assert len(visible["items"]) == 7
    visible_ids = [item["id"] for item in visible["items"]]
    assert "yiling" not in visible_ids
    assert "health_estimator" not in visible_ids
    mentor = registry.get("mentor")
    assert mentor.name == "人生导师"
    assert mentor.modes == ("converse", "synthesize", "bazi", "question_draft")
    assert mentor.tools == "none"
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
    assert status["legacy_roles"] == 1
    assert status["internal_roles"] == 1

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
    payload = client.get("/api/ai/roles").json()
    items = payload["items"]
    assert len(items) == 7
    assert payload["legacy_count"] == 1
    assert payload["internal_count"] == 1
    assert "yiling" not in [item["id"] for item in items]
    assert "health_estimator" not in [item["id"] for item in items]
    assert all("prompt" not in item for item in items)
    assert all("tagline" in item and "custom" in item for item in items)
    profile = client.get("/api/ai/roles/yiling/profile").json()
    assert profile["id"] == "yiling"
    assert "prompt" not in profile
    assert profile["modes"][0]["id"] == "synthesize"
    assert profile["revision"]


def test_profile_http_round_trip_and_revision_conflict(tmp_path):
    source = ROLES / "journal_companion.md"
    (tmp_path / source.name).write_bytes(source.read_bytes())
    registry = ai_roles.RoleRegistry(tmp_path)
    service = ai_roles.RoleService(registry, FakeBalance(), enabled=True)
    app = FastAPI()
    app.include_router(ai_roles.create_ai_router(service))
    client = TestClient(app)

    before = client.get("/api/ai/roles/journal_companion/profile").json()
    payload = {
        "name": "写作伙伴",
        "tagline": "陪你把话写清楚",
        "identity": "你熟悉我的表达习惯。",
        "style": "先接住，再追问。",
        "skills": [{
            "id": "skill_opening",
            "name": "开头诊断",
            "when": "我卡在第一段时",
            "instructions": "先指出开头在回避什么，再给两个可选的开法。",
        }],
        "expected_revision": before["revision"],
    }
    saved = client.put("/api/ai/roles/journal_companion/profile", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["saved"] is True
    profile = saved.json()["profile"]
    assert profile["tagline"] == "陪你把话写清楚"
    assert profile["skills"][0]["name"] == "开头诊断"
    assert profile["revision"] != before["revision"]

    stale = client.put("/api/ai/roles/journal_companion/profile", json=payload)
    assert stale.status_code == 409
    assert "别处修改" in stale.json()["detail"]

    manifest = client.get("/api/ai/roles").json()
    item = next(entry for entry in manifest["items"] if entry["id"] == "journal_companion")
    assert item["tagline"] == "陪你把话写清楚"
    assert item["custom"] == {"identity": True, "style": True, "skills": 1}


def test_save_role_profile_preserves_mode_prompts_and_custom_prompt_order(tmp_path):
    source = ROLES / "yiling.md"
    target = tmp_path / source.name
    target.write_bytes(source.read_bytes())
    registry = ai_roles.RoleRegistry(tmp_path)
    service = ai_roles.RoleService(registry, FakeBalance(), enabled=True)
    before = registry.profile("yiling")

    result = service.save_profile(
        "yiling",
        name="忆灵·测试",
        tagline="测试用的一句话定位",
        identity="你是一个测试身份。",
        style="短句，先给结论。",
        skills=[{
            "id": "skill_week_review",
            "name": "周回看",
            "when": "周日晚上",
            "instructions": "按三件事带我回看这一周。",
        }],
        expected_revision=before["revision"],
    )

    assert result["saved"] is True
    profile = result["profile"]
    assert profile["name"] == "忆灵·测试"
    assert profile["tagline"] == "测试用的一句话定位"
    assert profile["skills"][0]["name"] == "周回看"
    assert profile["revision"] != before["revision"]

    text = target.read_text(encoding="utf-8")
    assert "## mode: synthesize" in text
    assert "## mode: converse" in text
    assert "你是一个测试身份。" in text
    assert "name: 忆灵·测试" in text

    prompt = service.build_prompt("yiling", "converse", user_text="你好")
    assert prompt.index("苏格拉底") < prompt.index("角色身份（用户设定）")
    assert "你是一个测试身份。" in prompt
    assert "短句，先给结论。" in prompt
    assert "### 周回看" in prompt
    assert "周日晚上" in prompt
    assert "按三件事带我回看这一周。" in prompt


def test_save_role_profile_rejects_stale_revision_and_bad_skills(tmp_path):
    source = ROLES / "finance_advisor.md"
    (tmp_path / source.name).write_bytes(source.read_bytes())
    registry = ai_roles.RoleRegistry(tmp_path)
    service = ai_roles.RoleService(registry, FakeBalance(), enabled=True)
    revision = registry.profile("finance_advisor")["revision"]
    with pytest.raises(ai_roles.RoleConflictError):
        service.save_profile(
            "finance_advisor", name="理财专家", tagline="", identity="",
            style="", skills=[], expected_revision="stale")
    with pytest.raises(ValueError):
        service.save_profile(
            "finance_advisor", name="理财专家", tagline="", identity="",
            style="", skills=[{"name": "空技能", "instructions": ""}],
            expected_revision=revision)
    assert registry.profile("finance_advisor")["revision"] == revision


def test_registry_reports_invalid_skill_metadata(tmp_path):
    (tmp_path / "bad.md").write_text(
        "---\nid: bad\nname: 坏角色\nmodes: [chat]\n"
        "skills:\n  - id: ok_skill\n    name: 坏技能\n---\n\n"
        "## mode: chat\n\n你好。\n",
        encoding="utf-8")
    registry = ai_roles.RoleRegistry(tmp_path)
    assert registry.roles == {}
    assert registry.errors and "缺少做法" in registry.errors[0]["error"]
