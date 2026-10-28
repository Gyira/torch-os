"""恋爱系统 · 八字合婚页：只读读取 `.torch-os/love/bazi.json`。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.love import LoveStore, create_love_router


def build_client(tmp_path: Path) -> tuple[TestClient, Path]:
    store = LoveStore(tmp_path / "love.db", key_file=tmp_path / ".api_key.key")
    store.ensure_schema()
    app = FastAPI()
    app.include_router(create_love_router(store))
    return TestClient(app), store.db_path.parent


def test_bazi_returns_local_payload(tmp_path: Path) -> None:
    client, data_dir = build_client(tmp_path)
    payload = {
        "version": 1,
        "her": {"name": "胡丽洁", "day_master": "乙木"},
        "hehun": {"verdict": "良配"},
    }
    (data_dir / "bazi.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    response = client.get("/api/love/bazi")

    assert response.status_code == 200, response.text
    assert response.json()["her"]["name"] == "胡丽洁"
    assert response.json()["hehun"]["verdict"] == "良配"


def test_bazi_missing_returns_404(tmp_path: Path) -> None:
    client, _data_dir = build_client(tmp_path)
    response = client.get("/api/love/bazi")
    assert response.status_code == 404
    assert "bazi.json" in response.json()["detail"]


def test_bazi_broken_json_returns_500(tmp_path: Path) -> None:
    client, data_dir = build_client(tmp_path)
    (data_dir / "bazi.json").write_text("{not json", encoding="utf-8")
    response = client.get("/api/love/bazi")
    assert response.status_code == 500
    assert "八字资料读取失败" in response.json()["detail"]


def test_bazi_non_object_returns_500(tmp_path: Path) -> None:
    client, data_dir = build_client(tmp_path)
    (data_dir / "bazi.json").write_text("[]", encoding="utf-8")
    response = client.get("/api/love/bazi")
    assert response.status_code == 500
    assert "格式不正确" in response.json()["detail"]
