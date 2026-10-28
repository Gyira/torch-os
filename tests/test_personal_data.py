"""个性化数据仓库：<个人数据库>/.torch-os/personal-data.json 的读写、校验与只读保护。"""

import json

from fastapi.testclient import TestClient

from backend.app.application import create_app, vault_identity
from backend.app.db import Database


def make_client(tmp_path, **kwargs):
    app = create_app(
        Database(tmp_path / "personal.sqlite3"), tmp_path / "vault",
        security_environ={}, env_file=tmp_path / ".env", **kwargs)
    client = TestClient(app)
    client.get("/")
    return client


def data_path(tmp_path):
    return tmp_path / "vault" / ".torch-os" / "personal-data.json"


def test_missing_store_reads_as_empty(tmp_path):
    client = make_client(tmp_path)
    payload = client.get("/api/personal-data").json()
    assert payload["exists"] is False
    assert payload["values"] == {}
    assert payload["path"] == ".torch-os/personal-data.json"
    assert payload["vault_id"] == vault_identity(tmp_path / "vault")


def test_write_merges_deletes_and_survives_restart(tmp_path):
    client = make_client(tmp_path)
    saved = client.put("/api/personal-data", json={"values": {
        "torch-os:weather-cities": '[{"name":"上海"}]',
        "torch-os:workbench-widget-layout": '{"left":["weather"]}',
    }})
    assert saved.status_code == 200, saved.text
    assert saved.json()["keys"] == 2
    assert set(client.get("/api/personal-data").json()["values"]) == {
        "torch-os:weather-cities", "torch-os:workbench-widget-layout"}

    merged = client.put("/api/personal-data", json={"values": {
        "torch.welcome.v1.dismissed": "1",
        "torch-os:weather-cities": None,
    }})
    assert merged.status_code == 200 and merged.json()["keys"] == 2
    values = client.get("/api/personal-data").json()["values"]
    assert "torch-os:weather-cities" not in values
    assert values["torch.welcome.v1.dismissed"] == "1"
    assert json.loads(data_path(tmp_path).read_text(encoding="utf-8"))["version"] == 1

    restarted = make_client(tmp_path)
    assert restarted.get("/api/personal-data").json()["values"] == values


def test_invalid_keys_and_oversized_values_are_rejected(tmp_path, monkeypatch):
    from backend.app import personal_data

    client = make_client(tmp_path)
    for key in ("other-app:value", "torch-os:", "torch-os:天气 城市", ""):
        response = client.put("/api/personal-data", json={"values": {key: "x"}})
        assert response.status_code == 422, key

    monkeypatch.setattr(personal_data, "MAX_VALUE_BYTES", 8)
    too_large = client.put("/api/personal-data", json={"values": {"torch-os:big": "x" * 9}})
    assert too_large.status_code == 422
    assert not data_path(tmp_path).exists()


def test_value_limit_fits_a_widget_local_image_background(tmp_path):
    """倒计时/纪念日的本机图片以 data URL 存在同一个键里，上限必须装得下。"""
    client = make_client(tmp_path)
    value = "data:image/png;base64," + "A" * 1_500_000
    saved = client.put("/api/personal-data", json={
        "values": {"torch-os:countdown:v1": value}})
    assert saved.status_code == 200, saved.text
    assert client.get("/api/personal-data").json()["values"]["torch-os:countdown:v1"] == value


def test_corrupt_file_is_ignored_and_rewritten(tmp_path):
    client = make_client(tmp_path)
    path = data_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert client.get("/api/personal-data").json()["values"] == {}
    saved = client.put("/api/personal-data", json={"values": {"torch-os:time-view": "week"}})
    assert saved.status_code == 200 and saved.json()["keys"] == 1
    assert client.get("/api/personal-data").json()["values"] == {"torch-os:time-view": "week"}


def test_read_only_database_refuses_changes(tmp_path):
    make_client(tmp_path).put("/api/personal-data", json={
        "values": {"torch-os:time-view": "month"}})
    read_only_app = create_app(
        Database(tmp_path / "ro.sqlite3"), tmp_path / "vault",
        security_environ={"VAULT_READ_ONLY": "1"}, env_file=tmp_path / ".env")
    client = TestClient(read_only_app)
    client.get("/")
    assert client.get("/api/personal-data").json()["values"] == {"torch-os:time-view": "month"}
    blocked = client.put("/api/personal-data", json={"values": {"torch-os:time-view": "day"}})
    assert blocked.status_code == 409
    assert make_client(tmp_path).get("/api/personal-data").json()["values"]["torch-os:time-view"] == "month"
