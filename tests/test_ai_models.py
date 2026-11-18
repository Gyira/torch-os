import json
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest

from backend.app.ai_models import ModelSettings, ModelSettingsRequest
from backend.app.ai_gateway import AIUnavailable, AIProtocolError, HeadlessResult
from backend.app.ai_roles import RoleService, RoleRegistry, HandoffRequest
from backend.app import ai_context
from backend.app.db import Database
from backend.app.role_policy import RoleScopeError

KEY = "sk-synthetic-test-key-only"


def configured(tmp_path, handler):
    models = ModelSettings(tmp_path / "model", transport=httpx.MockTransport(handler))
    models.save(ModelSettingsRequest(provider="openai", model="gpt-test", api_key=KEY))
    return models


def response(text="回答"):
    return httpx.Response(200, json={"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": text}]}]})


def test_keys_encrypted_and_settings_never_return_secret(tmp_path):
    models = configured(tmp_path, lambda req: response())
    assert models.keys.resolve_key() == KEY
    assert KEY not in json.dumps(models.describe())
    assert all(KEY.encode() not in file.read_bytes() for file in models.directory.iterdir())
    models.save(ModelSettingsRequest(provider="deepseek"))
    assert models.keys.resolve_key() == KEY
    models.save(ModelSettingsRequest(clear_key=True))
    assert not models.keys.resolve_key()


def test_snapshot_pins_provider_until_next_request(tmp_path):
    models = configured(tmp_path, lambda req: response())
    with models.scope():
        models.save(ModelSettingsRequest(provider="deepseek"))
        assert models.config()["provider"] == "openai"
    assert models.config()["provider"] == "deepseek"


def test_openai_request_has_no_tools_or_server_conversation(tmp_path):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        return response()
    models = configured(tmp_path, handler)
    result = models.execute("合成背景", images=[{"media_type":"image/png", "image_bytes": b"fake-png"}])
    assert result.text == "回答" and result.session_id == ""
    assert calls[0]["store"] is False and calls[0]["tools"] == []
    assert "previous_response_id" not in calls[0]
    assert calls[0]["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize("status", [301, 400, 401, 403, 429, 500])
def test_upstream_errors_do_not_leak_secrets_or_retry(tmp_path, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=KEY)
    models = configured(tmp_path, handler)
    with pytest.raises(AIUnavailable) as error:
        models.execute("private text")
    assert KEY not in str(error.value) and len(calls) == 1


@pytest.mark.parametrize("payload", [{"status":"incomplete"}, {"status":"completed","output":None},
                                    {"status":"completed","output":[{"type":"message","content":None}]}])
def test_incomplete_or_malformed_output_is_not_actionable(tmp_path, payload):
    models = configured(tmp_path, lambda req: httpx.Response(200, json=payload))
    with pytest.raises(AIProtocolError):
        models.execute("test")


def test_disabled_runtime_never_reads_key_or_calls_model(tmp_path):
    models = ModelSettings(tmp_path, enabled=False, environ={"OPENAI_API_KEY": KEY})
    assert not models.describe()["key_configured"]
    with pytest.raises(AIUnavailable):
        models.execute("test")


def service(tmp_path, models=None):
    vault = tmp_path / "vault"; vault.mkdir()
    db = Database(tmp_path / "state.sqlite"); db.initialize()
    registry = RoleRegistry(Path(__file__).resolve().parents[1] / "dsh" / "roles")
    return RoleService(registry, database=db, vault_dir=vault, models=models)


def test_switching_provider_keeps_local_history_and_role_scope(tmp_path):
    prompts = []
    models = configured(tmp_path, lambda req: (prompts.append(json.loads(req.content)) or response("GPT 回复")))
    svc = service(tmp_path, models)
    first = svc.chat("academic_mentor", "discuss", "我正在研究合成测试问题")
    models.save(ModelSettingsRequest(provider="deepseek"))
    with patch.object(svc, "readiness", return_value=(True,"")), patch("backend.app.ai_roles.run_headless", return_value=HeadlessResult("DeepSeek 回复", "new", 0, 0, "")) as run:
        svc.workspace = tmp_path / "runtime"
        second = svc.chat("academic_mentor", "discuss", "请继续", thread_id=first["thread_id"])
        assert "合成测试问题" in run.call_args.args[0]
        assert run.call_args.kwargs["session_id"] == ""
    assert second["thread_id"] == first["thread_id"]
    assert second["model"]["provider"] == "deepseek"
    with pytest.raises(RoleScopeError):
        svc.chat("academic_mentor", "discuss", "read", selectors=[{"entry":"identity"}])


def test_handoff_is_read_only_and_checks_scope_without_model(tmp_path):
    svc = service(tmp_path)
    svc._execute = Mock(side_effect=AssertionError("must not call model"))
    result = svc.handoff(HandoffRequest(role_id="academic_mentor", mode="discuss", message="讲解概念"))
    assert result["uploaded"] is False and result["written"] is False
    assert "讲解概念" in result["text"] and svc.threads() == []
    with pytest.raises(RoleScopeError):
        svc.handoff(HandoffRequest(role_id="academic_mentor",mode="discuss",message="x",selectors=[{"entry":"identity"}]))


def test_context_reserves_room_for_later_sources_and_reports_real_size(tmp_path):
    svc = service(tmp_path)
    def build(db, vault, selector, source_id, **kwargs):
        return {"id":source_id,"chars":20000}, f"[{source_id}]\n" + "字"*20000, False
    with patch.object(ai_context, "_build_one", side_effect=build):
        bundle = ai_context.assemble(svc.database, svc.vault_dir, role_id="mentor",
            selectors=[{"entry":"identity"},{"entry":"self_archive"},{"entry":"tasks"}], max_chars=3000)
    assert len(bundle.text) <= 3000 and len(bundle.sources) == 3
    assert all(s["truncated"] and s["chars"] < 20000 for s in bundle.sources)
