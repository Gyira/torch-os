import asyncio
import builtins
import json
import traceback

import httpx
import pytest

from backend.app.model_gateway import ModelGateway, ModelGatewayError


FAKE_API_KEY = "test-only-key-never-a-real-credential"
EVIDENCE = [{"path": "profile.md", "content": "用户希望逐步学习软件开发。"}]


def make_gateway(handler=None, **overrides):
    def no_network(request):
        raise AssertionError("Unexpected cloud call")

    options = {
        "provider": "deepseek", "base_url": "https://api.example.test/v1",
        "model": "test-chat", "api_key": FAKE_API_KEY, "timeout": 45,
        "transport": httpx.MockTransport(handler or no_network),
    }
    options.update(overrides)
    return ModelGateway(**options)


def completion(content="事实：[资料 1] 用户希望学习软件开发。", **overrides):
    data = {
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    }
    data.update(overrides)
    return data


def test_success_has_bounded_request_and_public_usage():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == f"Bearer {FAKE_API_KEY}"
        assert request.url == "https://api.example.test/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 800
        assert payload["stream"] is False
        assert "tools" not in payload
        assert FAKE_API_KEY not in request.content.decode()
        assert "不是用户授权" in payload["messages"][0]["content"]
        assert "不得编造" in payload["messages"][0]["content"]
        assert all(value == 45 for value in request.extensions["timeout"].values())
        return httpx.Response(200, json=completion())

    gateway = make_gateway(handler)
    result = asyncio.run(gateway.generate("我希望学习什么？", EVIDENCE))
    assert result == {
        "answer": "事实：[资料 1] 用户希望学习软件开发。", "mode": "cloud",
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        "model": "test-chat",
    }
    assert len(requests) == 1
    assert gateway.configured
    assert FAKE_API_KEY not in json.dumps(gateway.public_status())
    assert "base_url" not in gateway.public_status()


def test_answer_retains_string_interface():
    gateway = make_gateway(lambda request: httpx.Response(200, json=completion("  已确认的事实。  ")))
    assert asyncio.run(gateway.answer("问题", EVIDENCE)) == "已确认的事实。"


@pytest.mark.parametrize("evidence", [[], [{"path": "empty.md", "content": "  "}], [{"path": "bad.md"}]])
def test_without_sources_does_not_call_cloud(evidence):
    result = asyncio.run(make_gateway().generate("我是谁，我住哪里？", evidence))
    assert result == {"answer": None, "mode": "local", "usage": None, "model": None}


@pytest.mark.parametrize("overrides", [{"provider": "local"}, {"api_key": ""}, {"model": ""}, {"base_url": ""}])
def test_unconfigured_gateway_returns_local_fallback(overrides):
    gateway = make_gateway(**overrides)
    assert not gateway.configured
    assert asyncio.run(gateway.answer("问题", EVIDENCE)) is None


def test_injected_constructor_does_not_import_configuration(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        assert not (name.endswith("config") or "config" in fromlist)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert make_gateway().configured


@pytest.mark.parametrize(
    ("base_url", "expected_url"),
    [
        ("https://api.deepseek.com", "https://api.deepseek.com/chat/completions"),
        ("https://api.deepseek.com/v1/", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.example.test/api", "https://api.example.test/api/chat/completions"),
        ("https://api.example.test/v1/chat/completions", "https://api.example.test/v1/chat/completions"),
        ("http://127.0.0.1:8000/v1", "http://127.0.0.1:8000/v1/chat/completions"),
        ("http://localhost:8000", "http://localhost:8000/chat/completions"),
        ("http://[::1]:8000", "http://[::1]:8000/chat/completions"),
    ],
)
def test_endpoint_normalization_uses_mock_transport(base_url, expected_url):
    def handler(request):
        assert str(request.url) == expected_url
        return httpx.Response(200, json=completion())

    assert asyncio.run(make_gateway(handler, base_url=base_url).generate("问题", EVIDENCE))["mode"] == "cloud"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.example.test/v1", "http://192.168.1.5/v1", "file:///tmp/model",
        "ftp://api.example.test/v1", "https://username:password@api.example.test/v1",
        f"https://api.example.test/v1?api_key={FAKE_API_KEY}",
        "https://api.example.test/v1#secret", "https://api.example.test/\nsecret",
        "https://api.example.test\\@other.example/v1", "not-an-api-url",
    ],
)
def test_unsafe_endpoints_are_never_requested(base_url):
    gateway = make_gateway(base_url=base_url)
    assert not gateway.configured
    with pytest.raises(ModelGatewayError) as caught:
        asyncio.run(gateway.generate("问题", EVIDENCE))
    assert caught.value.code == "model_configuration"
    assert FAKE_API_KEY not in str(caught.value)
    assert base_url not in json.dumps(gateway.public_status())


def test_evidence_is_limited_to_six_sources_1800_each_12000_total():
    def handler(request):
        payload = json.loads(request.content)
        content = payload["messages"][1]["content"].split("以下是引用资料：\n", 1)[1]
        assert len(content) <= 12000
        assert content.count("[资料 ") == 6
        assert "第七条不应发送" not in content
        for index in range(6):
            assert content.count(chr(0x4E00 + index)) <= 1800
        return httpx.Response(200, json=completion())

    evidence = [{"path": "x" * 500, "content": chr(0x4E00 + index) * 5000} for index in range(6)]
    evidence.append({"path": "seventh.md", "content": "第七条不应发送"})
    assert asyncio.run(make_gateway(handler).answer("问题", evidence))


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "model_unauthorized"), (403, "model_unauthorized"), (429, "model_rate_limited"),
     (500, "model_unavailable"), (400, "model_http_error"), (302, "model_http_error")],
)
def test_http_errors_are_safe_and_never_retried(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, text=f"private server error: {FAKE_API_KEY}",
            headers={"Location": "https://untrusted.example.test/steal"},
        )

    with pytest.raises(ModelGatewayError) as caught:
        asyncio.run(make_gateway(handler).generate("问题", EVIDENCE))
    assert caught.value.code == code
    assert len(calls) == 1
    assert FAKE_API_KEY not in "".join(traceback.format_exception(caught.value))
    assert "private server error" not in str(caught.value)


@pytest.mark.parametrize(("error_type", "code"), [(httpx.ReadTimeout, "model_timeout"), (httpx.ConnectError, "model_network")])
def test_transport_errors_are_safe_and_never_retried(error_type, code):
    calls = []

    def handler(request):
        calls.append(request)
        raise error_type(f"failed request using {FAKE_API_KEY}", request=request)

    with pytest.raises(ModelGatewayError) as caught:
        asyncio.run(make_gateway(handler).generate("问题", EVIDENCE))
    assert caught.value.code == code
    assert len(calls) == 1
    assert FAKE_API_KEY not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    "response_body",
    [None, [], {"choices": []}, {"choices": "invalid"}, {"choices": [None]},
     {"choices": [{"message": None}]}, {"choices": [{"message": {"content": ["not text"]}}]}],
)
def test_malformed_response_is_rejected(response_body):
    gateway = make_gateway(lambda request: httpx.Response(200, content=json.dumps(response_body)))
    with pytest.raises(ModelGatewayError) as caught:
        asyncio.run(gateway.generate("问题", EVIDENCE))
    assert caught.value.code == "model_invalid_response"


def test_non_json_response_does_not_leak_body():
    gateway = make_gateway(lambda request: httpx.Response(200, text=f"<html>{FAKE_API_KEY}</html>"))
    with pytest.raises(ModelGatewayError) as caught:
        asyncio.run(gateway.generate("问题", EVIDENCE))
    assert caught.value.code == "model_invalid_response"
    assert FAKE_API_KEY not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(("content", "finish_reason", "code"), [(" ", "stop", "model_empty_response"), ("partial", "length", "model_truncated"), ("blocked", "content_filter", "model_blocked")])
def test_empty_truncated_or_blocked_response_is_not_presented_as_success(content, finish_reason, code):
    data = completion(content)
    data["choices"][0]["finish_reason"] = finish_reason
    with pytest.raises(ModelGatewayError) as caught:
        asyncio.run(make_gateway(lambda request: httpx.Response(200, json=data)).generate("问题", EVIDENCE))
    assert caught.value.code == code


def test_tool_requests_are_not_executed_or_returned_as_answers():
    data = completion("running tool")
    data["choices"][0]["message"]["tool_calls"] = [{"function": {"name": "delete_files"}}]
    with pytest.raises(ModelGatewayError) as caught:
        asyncio.run(make_gateway(lambda request: httpx.Response(200, json=data)).generate("问题", EVIDENCE))
    assert caught.value.code == "model_invalid_response"


def test_malformed_usage_is_omitted():
    data = completion(usage={"prompt_tokens": FAKE_API_KEY, "completion_tokens": 10, "total_tokens": 30})
    result = asyncio.run(make_gateway(lambda request: httpx.Response(200, json=data)).generate("问题", EVIDENCE))
    assert result["usage"] is None
    assert FAKE_API_KEY not in json.dumps(result)


def test_timeout_is_capped_at_45_seconds():
    assert make_gateway(timeout=120).timeout_seconds == 45


def test_request_has_an_overall_deadline_even_with_slow_transport():
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.1)
        return httpx.Response(200, json=completion())

    with pytest.raises(ModelGatewayError) as caught:
        asyncio.run(make_gateway(handler, timeout=0.001).generate("问题", EVIDENCE))
    assert caught.value.code == "model_timeout"
    assert len(calls) == 1


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), "bad"])
def test_invalid_timeout_is_not_requested(timeout):
    gateway = make_gateway(timeout=timeout)
    with pytest.raises(ModelGatewayError, match="配置"):
        asyncio.run(gateway.generate("问题", EVIDENCE))


def test_general_conversation_without_sources_requires_the_explicit_flag():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completion("一般性回答。"))

    gateway = make_gateway(handler)
    assert asyncio.run(gateway.generate("介绍一下你自己", [], general_conversation=False))["mode"] == "local"
    assert calls == []
    result = asyncio.run(gateway.generate("介绍一下你自己", [], general_conversation=True))
    assert result["mode"] == "cloud" and result["answer"] == "一般性回答。"
    payload = json.loads(calls[0].content)
    assert "没有提供任何个人资料" in payload["messages"][0]["content"]
    assert "没有随附已确认记忆" in payload["messages"][1]["content"]
    assert "引用资料" not in payload["messages"][1]["content"]
