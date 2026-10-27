"""DeepSeek 余额代理：Key 来源、加密保存、出站解析与错误映射。"""

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.deepseek_balance import (
    DeepSeekBalanceError,
    DeepSeekBalanceService,
    create_deepseek_balance_router,
    http_fetch_balance,
)

KEY = "sk-test-balance-1234567890"
KEY2 = "sk-test-balance-0987654321"


def raw_balance(currency="CNY", total="34.74", topped="34.74", granted="0.00", available=True):
    return {
        "is_available": available,
        "balance_infos": [{
            "currency": currency,
            "total_balance": total,
            "topped_up_balance": topped,
            "granted_balance": granted,
        }],
    }


def make_service(tmp_path, *, fetcher=None, environ=None, dsh_home=None,
                 enabled=True, clock=None):
    captured = {"keys": []}

    def default_fetcher(key):
        captured["keys"].append(key)
        return raw_balance()

    return DeepSeekBalanceService(
        tmp_path / "data",
        enabled=enabled,
        fetcher=fetcher or default_fetcher,
        environ={} if environ is None else environ,
        dsh_home=tmp_path / "dsh" if dsh_home is None else dsh_home,
        clock=clock or (lambda: 1000.0),
    ), captured


def write_dsh_key(home: Path, key: str, quoted=True):
    home.mkdir(parents=True, exist_ok=True)
    value = f'"{key}"' if quoted else key
    (home / ".credentials.yaml").write_text(
        "version: 1\n"
        "refs:\n"
        f"  DEEPSEEK_API_KEY: {value}\n"
        "  OTHER_KEY: unused\n",
        encoding="utf-8",
    )


def test_without_any_key_returns_setup_state(tmp_path):
    service, captured = make_service(tmp_path)
    result = service.balance()
    assert result["ok"] is False
    assert result["code"] == "NO_KEY"
    assert result["configured"] is False
    assert result["sources"] == {"saved": False, "env": False, "dsh": False}
    assert captured["keys"] == []
    assert service.describe()["source"] == ""
    assert KEY not in json.dumps(result, ensure_ascii=False)


def test_saved_key_is_encrypted_and_preferred(tmp_path):
    service, captured = make_service(tmp_path)
    service.save_key(KEY)
    result = service.balance()
    assert result["ok"] is True
    assert result["source"] == "saved"
    assert result["currency"] == "CNY"
    assert result["total_balance"] == "34.74"
    assert result["topped_up_balance"] == "34.74"
    assert result["granted_balance"] == "0.00"
    assert captured["keys"] == [KEY]
    assert KEY not in json.dumps(result, ensure_ascii=False)

    settings = (tmp_path / "data" / "deepseek-balance.json").read_text(encoding="utf-8")
    assert KEY not in settings
    assert (tmp_path / "data" / "deepseek-balance.key").stat().st_size == 32

    restarted, restarted_captured = make_service(tmp_path)
    assert restarted.describe()["saved"] is True
    assert restarted.balance()["ok"] is True
    assert restarted_captured["keys"] == [KEY]


def test_env_and_dsh_are_fallbacks_and_saved_wins(tmp_path):
    dsh_home = tmp_path / "dsh"
    write_dsh_key(dsh_home, "sk-dsh-test-123456")
    service, captured = make_service(
        tmp_path, environ={"DEEPSEEK_API_KEY": "sk-env-test-123456"}, dsh_home=dsh_home)
    assert service.describe() == {
        "enabled": True, "saved": False, "env": True, "dsh": True, "source": "env",
    }
    assert service.balance()["source"] == "env"
    assert captured["keys"] == ["sk-env-test-123456"]

    service.save_key(KEY)
    assert service.balance()["source"] == "saved"
    assert captured["keys"][-1] == KEY

    service.clear_key()
    assert service.balance()["source"] == "env"

    only_dsh, dsh_captured = make_service(tmp_path / "dsh-only", dsh_home=dsh_home)
    assert only_dsh.describe()["source"] == "dsh"
    assert only_dsh.balance()["source"] == "dsh"
    assert dsh_captured["keys"] == ["sk-dsh-test-123456"]


def test_dsh_credentials_accept_quoted_and_unquoted_keys(tmp_path):
    quoted = tmp_path / "quoted"
    write_dsh_key(quoted, "sk-dsh-quoted-123456", quoted=True)
    service, _ = make_service(tmp_path / "a", dsh_home=quoted)
    assert service.describe()["dsh"] is True

    unquoted = tmp_path / "unquoted"
    write_dsh_key(unquoted, "sk-dsh-unquoted-123456", quoted=False)
    service, _ = make_service(tmp_path / "b", dsh_home=unquoted)
    assert service.describe()["dsh"] is True


def test_qa_mode_never_reads_keys_or_calls_the_api(tmp_path):
    dsh_home = tmp_path / "dsh"
    write_dsh_key(dsh_home, "sk-dsh-should-not-be-read")

    def forbidden(_key):
        raise AssertionError("QA 模式不应请求余额接口")

    service, _ = make_service(
        tmp_path, fetcher=forbidden, dsh_home=dsh_home, enabled=False)
    assert service.describe() == {
        "enabled": False, "saved": False, "env": False, "dsh": False, "source": "",
    }
    result = service.balance()
    assert result["ok"] is False
    assert result["enabled"] is False
    assert result["code"] == "DISABLED"
    with pytest.raises(ValueError, match="不允许保存"):
        service.save_key(KEY)


def test_balance_picks_cny_and_keeps_optional_fields(tmp_path):
    payload = {
        "is_available": False,
        "balance_infos": [
            {"currency": "USD", "total_balance": "1.00", "topped_up_balance": "1.00"},
            {"currency": "CNY", "total_balance": "34.74", "topped_up_balance": "30.00", "granted_balance": "4.74"},
        ],
    }
    service, _ = make_service(tmp_path, fetcher=lambda _key: payload)
    service.save_key(KEY)
    result = service.balance()
    assert result["ok"] is True
    assert result["is_available"] is False
    assert result["currency"] == "CNY"
    assert result["total_balance"] == "34.74"
    assert result["topped_up_balance"] == "30.00"
    assert result["granted_balance"] == "4.74"

    missing = make_service(tmp_path / "missing", fetcher=lambda _key: {"balance_infos": []})[0]
    missing.save_key(KEY)
    assert missing.balance()["code"] == "SHAPE"

    bad_total = make_service(
        tmp_path / "bad", fetcher=lambda _key: {"balance_infos": [{"currency": "CNY", "total_balance": "n/a"}]})[0]
    bad_total.save_key(KEY)
    assert bad_total.balance()["code"] == "SHAPE"


def raise_balance_error(code, message):
    def fetcher(_key):
        raise DeepSeekBalanceError(code, message)
    return fetcher


def test_auth_and_network_errors_are_mapped_without_the_key(tmp_path):
    service, _ = make_service(tmp_path, fetcher=raise_balance_error("AUTH", "Key 无效"))
    service.save_key(KEY)
    result = service.balance()
    assert result["ok"] is False
    assert result["code"] == "AUTH"
    assert result["configured"] is True
    assert result["source"] == "saved"
    assert KEY not in json.dumps(result, ensure_ascii=False)

    network, _ = make_service(tmp_path / "network", fetcher=raise_balance_error("NETWORK", "断网"))
    network.save_key(KEY)
    assert network.balance()["code"] == "NETWORK"


def test_success_is_cached_and_refresh_bypasses_the_cache(tmp_path):
    calls = {"n": 0}
    now = {"value": 1000.0}

    def fetcher(_key):
        calls["n"] += 1
        return raw_balance()

    service, _ = make_service(tmp_path, fetcher=fetcher, clock=lambda: now["value"])
    service.save_key(KEY)
    first = service.balance()
    second = service.balance()
    assert first["cached"] is False
    assert second["cached"] is True
    assert calls["n"] == 1
    now["value"] += 31
    assert service.balance()["cached"] is False
    assert calls["n"] == 2
    service.balance(refresh=True)
    assert calls["n"] == 3


def test_router_exposes_state_without_leaking_the_key(tmp_path):
    service, _ = make_service(tmp_path)
    app = FastAPI()
    app.include_router(create_deepseek_balance_router(service))
    client = TestClient(app)

    no_key = client.get("/api/deepseek/balance").json()
    assert no_key["code"] == "NO_KEY"
    assert KEY not in json.dumps(no_key, ensure_ascii=False)

    saved = client.post("/api/deepseek/balance/key", json={"key": KEY})
    assert saved.status_code == 200 and saved.json()["saved"] is True
    settings = client.get("/api/deepseek/balance/settings").json()
    assert settings["saved"] is True and settings["source"] == "saved"
    assert KEY not in json.dumps(settings, ensure_ascii=False)

    balance = client.get("/api/deepseek/balance", params={"refresh": 1}).json()
    assert balance["ok"] is True and balance["source"] == "saved"
    assert KEY not in json.dumps(balance, ensure_ascii=False)

    assert client.post("/api/deepseek/balance/key", json={"key": "short"}).status_code == 422
    cleared = client.delete("/api/deepseek/balance/key")
    assert cleared.status_code == 200 and cleared.json()["saved"] is False


def test_http_fetcher_sends_only_bearer_auth_and_parses_json():
    def ok(request):
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.headers["accept"] == "application/json"
        return httpx.Response(200, json=raw_balance())

    data = http_fetch_balance(KEY, transport=httpx.MockTransport(ok))
    assert data["balance_infos"][0]["total_balance"] == "34.74"


def test_http_fetcher_maps_auth_errors_and_invalid_json():
    with pytest.raises(DeepSeekBalanceError) as auth:
        http_fetch_balance(KEY, transport=httpx.MockTransport(lambda _request: httpx.Response(401)))
    assert auth.value.code == "AUTH"

    with pytest.raises(DeepSeekBalanceError) as shape:
        http_fetch_balance(
            KEY, transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"not json")))
    assert shape.value.code == "SHAPE"

    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(DeepSeekBalanceError) as network:
        http_fetch_balance(KEY, transport=httpx.MockTransport(offline))
    assert network.value.code == "NETWORK"

    with pytest.raises(DeepSeekBalanceError) as server:
        http_fetch_balance(KEY, transport=httpx.MockTransport(lambda _request: httpx.Response(503)))
    assert server.value.code == "SERVER"


def test_application_advertises_balance_only_in_personal_mode(tmp_path):
    personal = create_app(
        Database(tmp_path / "personal.sqlite3"), tmp_path / "personal-vault",
        security_environ={}, env_file=tmp_path / ".env")
    qa = create_app(
        Database(tmp_path / "qa.sqlite3"), tmp_path / "qa-vault",
        security_environ={}, env_file=tmp_path / ".env", runtime_label="qa")
    personal_client = TestClient(personal)
    personal_client.get("/")
    assert personal_client.get("/api/config/status").json()["capabilities"]["deepseek_balance"] is True
    qa_client = TestClient(qa)
    qa_client.get("/")
    assert qa_client.get("/api/config/status").json()["capabilities"]["deepseek_balance"] is False
