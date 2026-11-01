"""自我档案 / 人生导师 / 八字 HTTP 接口（离线，不调用模型）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import mentor_pending, self_archive, self_api, vault_layout


def make_client(tmp_path: Path) -> tuple[TestClient, Path]:
    vault = tmp_path / "vault"
    vault_layout.ensure_layout(vault)
    app = FastAPI()
    app.include_router(self_api.create_self_router(vault, role_service=None))
    return TestClient(app), vault


def test_self_overview_and_books(tmp_path):
    client, vault = make_client(tmp_path)
    self_archive.create_entry(
        vault, book_key="self_values", entry_type="narrative",
        confidence="medium", title="世界观", text="世界更像情境决定。")
    overview = client.get("/api/self/overview")
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert len(body["books"]) == 11
    assert body["vault_name"] == "vault"
    assert body["questions"]["remaining"] == 3
    book = client.get("/api/self/books/self_values").json()
    assert book["book"]["title"] == "三观与决策"
    assert [item["title"] for item in book["entries"]] == ["世界观"]


def test_mentor_overview_questions_memory(tmp_path):
    client, _vault = make_client(tmp_path)
    overview = client.get("/api/mentor/overview")
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert len(body["questions"]["items"]) == 3
    assert body["memory"]["paths"]["core"].endswith("核心记忆.md")
    second = client.get("/api/mentor/questions/today").json()
    assert [item["id"] for item in second["items"]] == [
        item["id"] for item in body["questions"]["items"]]


def test_pending_accept_requires_confirmation_and_writes_entry(tmp_path):
    client, vault = make_client(tmp_path)
    card = mentor_pending.create_pending(vault, {
        "section": "self_behavior", "action": "create", "entry_type": "behavior",
        "confidence": "medium", "title": "压力动作", "new_text": "压力大时先刷手机。",
    })
    blocked = client.post(f"/api/self/pending/{card['pending_id']}/accept", json={})
    assert blocked.status_code == 422
    accepted = client.post(f"/api/self/pending/{card['pending_id']}/accept",
                           json={"confirm": True, "edited_text": "压力大时先刷手机，再散步。"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["entry"]["text"].endswith("再散步。")
    assert self_archive.list_entries(vault, "self_behavior")[0]["title"] == "压力动作"


def test_bazi_profile_and_chart_endpoints(tmp_path):
    client, _vault = make_client(tmp_path)
    empty = client.get("/api/self/bazi/profile").json()
    assert empty["exists"] is False
    saved = client.put("/api/self/bazi/profile", json={
        "name": "王衡岳", "sex": "male", "pillars_manual": "癸未 辛酉 丙戌 戊子",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["chart"]["day_master"] == "丙火"
    preview = client.post("/api/self/bazi/chart", json={
        "name": "王衡岳", "sex": "male", "birth_date": "2003-09-10",
        "birth_time": "00:00",
    })
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["pillars"]) == 4
    assert [row["gan"] + row["zhi"] for row in preview.json()["pillars"]] == [
        "癸未", "辛酉", "丙戌", "戊子"]


def test_read_only_blocks_writes(tmp_path):
    vault = tmp_path / "vault"
    vault_layout.ensure_layout(vault)
    app = FastAPI()
    app.include_router(self_api.create_self_router(vault, read_only=True, role_service=None))
    client = TestClient(app)
    response = client.put("/api/self/bazi/profile", json={
        "name": "王衡岳", "sex": "male", "pillars_manual": "癸未 辛酉 丙戌 戊子",
    })
    assert response.status_code == 409
