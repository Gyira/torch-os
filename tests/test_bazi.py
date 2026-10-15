"""Local four-pillars reference: temporary databases only, no network, no model."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import bazi
from backend.app.bazi import BaziService, create_bazi_router
from backend.app.db import Database


@pytest.fixture
def setup(tmp_path):
    database = Database(tmp_path / "bazi.sqlite3")
    app = FastAPI()
    app.include_router(create_bazi_router(database))
    return database, TestClient(app)


def audit_details(database):
    with database.connect() as connection:
        rows = connection.execute("SELECT event_type, detail_json FROM audit_events ORDER BY id").fetchall()
    return [(row["event_type"], row["detail_json"]) for row in rows]


def test_profile_is_empty_until_saved_and_chart_needs_a_profile(setup):
    _, client = setup
    payload = client.get("/api/bazi/profile").json()
    assert payload == {"profile": None, "chart": None}
    missing = client.get("/api/bazi/chart")
    assert missing.status_code == 404
    assert "出生资料" in missing.json()["detail"]


def test_saved_chart_matches_the_reference_four_pillars(setup):
    _, client = setup
    response = client.put("/api/bazi/profile", json={
        "birth_date": "1990-01-01", "birth_time": "12:00",
        "longitude": None, "use_true_solar": False,
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    chart = payload["chart"]
    assert [pillar["ganzhi"] for pillar in chart["pillars"]] == ["己巳", "丙子", "丙寅", "甲午"]
    assert [pillar["name"] for pillar in chart["pillars"]] == ["年柱", "月柱", "日柱", "时柱"]
    assert chart["pillars"][2]["na_yin"] == "炉中火"
    assert chart["pillars"][2]["shi_shen_gan"] == "日主"
    assert chart["day_master"] == "丙"
    assert chart["five_elements"] == {"金": 0, "木": 2, "水": 1, "火": 4, "土": 1}
    assert chart["lunar"]["text"] == "一九八九年腊月初五"
    assert chart["lunar"]["zodiac"] == "蛇"
    assert chart["true_solar"]["applied"] is False
    assert chart["extraction_version"] == "bazi-v1"
    assert any("传统文化参考" in note for note in chart["notes"])

    again = client.get("/api/bazi/chart").json()
    assert [pillar["ganzhi"] for pillar in again["pillars"]] == ["己巳", "丙子", "丙寅", "甲午"]


def test_true_solar_correction_can_move_a_pillar_across_the_solar_term(setup):
    _, client = setup
    # 2024-02-04 立春 ~16:27 Beijing time. With true solar time for 116.4°E
    # (about -28 minutes) the same clock time still belongs to the 癸卯 year.
    corrected = client.put("/api/bazi/profile", json={
        "birth_date": "2024-02-04", "birth_time": "16:20",
        "longitude": 116.4, "use_true_solar": True,
    }).json()
    assert corrected["chart"]["true_solar"]["applied"] is True
    assert corrected["chart"]["chart_time"] == "2024-02-04T15:51"
    assert corrected["chart"]["true_solar"]["total_minutes"] == pytest.approx(-28.48, abs=0.01)
    assert corrected["chart"]["pillars"][0]["ganzhi"] == "癸卯"

    plain = client.put("/api/bazi/profile", json={
        "birth_date": "2024-02-04", "birth_time": "16:40",
        "longitude": None, "use_true_solar": False,
    }).json()
    assert plain["chart"]["pillars"][0]["ganzhi"] == "甲辰"


def test_validation_rejects_unsafe_or_incomplete_input(setup):
    _, client = setup
    base = {"birth_date": "1990-01-01", "birth_time": "12:00", "longitude": None, "use_true_solar": False}
    cases = [
        {**base, "birth_date": "2199-01-01"},
        {**base, "birth_date": "1899-12-31"},
        {**base, "birth_date": "1990-02-30"},
        {**base, "birth_time": "24:00"},
        {**base, "birth_time": "12-00"},
        {**base, "longitude": 200, "use_true_solar": True},
        {**base, "use_true_solar": True},
        {**base, "birth_place": "北京"},
    ]
    for payload in cases:
        response = client.put("/api/bazi/profile", json=payload)
        assert response.status_code == 422, f"{payload} -> {response.status_code}"
    future = client.put("/api/bazi/profile", json={**base, "birth_date": "2999-01-01"})
    assert future.status_code == 422


def test_clearing_requires_explicit_confirmation_and_removes_the_profile(setup):
    database, client = setup
    client.put("/api/bazi/profile", json={
        "birth_date": "1990-01-01", "birth_time": "12:00", "longitude": None, "use_true_solar": False,
    })
    refused = client.post("/api/bazi/profile/clear", json={"confirm": False})
    assert refused.status_code == 422
    assert client.get("/api/bazi/profile").json()["profile"] is not None
    assert BaziService(database).get_profile() is not None

    cleared = client.post("/api/bazi/profile/clear", json={"confirm": True})
    assert cleared.status_code == 200
    assert cleared.json() == {"cleared": True}
    assert client.get("/api/bazi/profile").json() == {"profile": None, "chart": None}
    assert client.post("/api/bazi/profile/clear", json={"confirm": True}).json() == {"cleared": False}


def test_audit_events_never_store_birth_values(setup):
    database, client = setup
    client.put("/api/bazi/profile", json={
        "birth_date": "1990-01-01", "birth_time": "12:00", "longitude": 116.4, "use_true_solar": True,
    })
    client.post("/api/bazi/profile/clear", json={"confirm": True})
    events = audit_details(database)
    assert [event for event, _ in events] == ["bazi_profile_saved", "bazi_profile_cleared"]
    for _, detail in events:
        assert "1990-01-01" not in detail
        assert "12:00" not in detail
        assert "116.4" not in detail
        assert json.loads(detail) in ({}, {"has_longitude": True})


def test_bazi_module_stays_offline():
    from pathlib import Path

    text = Path(bazi.__file__).read_text(encoding="utf-8")
    for forbidden in ("httpx", "requests", "urllib", "socket"):
        assert forbidden not in text


def test_missing_calendar_library_returns_a_clear_503(setup, monkeypatch):
    _, client = setup
    client.put("/api/bazi/profile", json={
        "birth_date": "1990-01-01", "birth_time": "12:00", "longitude": None, "use_true_solar": False,
    })

    def missing(profile):
        raise RuntimeError(bazi.MISSING_DEPENDENCY)

    monkeypatch.setattr(bazi, "build_chart", missing)
    chart = client.get("/api/bazi/chart")
    assert chart.status_code == 503
    assert "lunar-python" in chart.json()["detail"]
    profile = client.get("/api/bazi/profile")
    assert profile.status_code == 503
    assert bazi.dependency_available() is True


def test_health_reports_the_bazi_capability(tmp_path, monkeypatch):
    from datetime import date

    from backend.app.application import APP_VERSION, create_app
    from backend.app.model_gateway import ModelGateway

    monkeypatch.setattr('backend.app.habits.shanghai_today', lambda: date(2026, 10, 4))
    monkeypatch.setattr('backend.app.learning.shanghai_today', lambda: date(2026, 10, 4))
    database = Database(tmp_path / 'torch_os.sqlite3')
    gateway = ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    client = TestClient(create_app(database, tmp_path / 'vault', gateway, security_environ={}))
    client.get('/')
    assert client.get('/api/config/status').json()['capabilities']['bazi'] is True
    assert client.get('/api/health').json()['version'] == APP_VERSION
