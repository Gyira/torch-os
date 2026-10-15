"""DeepSeek share-link import: mocked fetcher, temporary vault, no real network."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.db import Database
from backend.app.share_import import (
    ShareImportError,
    ShareImportService,
    create_share_import_router,
    parse_share_reference,
    parse_shared_conversation,
)

SHARE_ID = "synthetic-share-1"


def share_payload(messages=None, title="虚构分享", code=0, model_type="default"):
    rows = messages if messages is not None else [
        {"message_id": 1, "role": "USER", "inserted_at": 1790000000.0,
         "fragments": [{"id": 1, "type": "REQUEST", "content": "我打算每周三晚上学习英语，先从二十分钟开始。"}]},
        {"message_id": 2, "role": "ASSISTANT", "inserted_at": 1790000001.0,
         "fragments": [{"id": 2, "type": "THINK", "content": "内部思考不应进入候选"},
                       {"id": 3, "type": "RESPONSE", "content": "助手的回答也不是关于你的事实。"}]},
        {"message_id": 3, "role": "USER", "inserted_at": 1790000002.0,
         "fragments": [{"id": 4, "type": "REQUEST", "content": "我该怎么办？"}]},
    ]
    return {"code": code, "msg": "ok", "data": {"biz_code": 0, "biz_data": {"title": title, "model_type": model_type, "messages": rows}}}


@pytest.fixture
def setup(tmp_path):
    database = Database(tmp_path / "share.sqlite3")
    database.initialize()
    vault = tmp_path / "vault"
    vault.mkdir()
    calls = []

    def fetcher(share_id):
        calls.append(share_id)
        return share_payload()

    service = ShareImportService(database, vault, fetcher=fetcher)
    app = FastAPI()
    app.include_router(create_share_import_router(database, vault, fetcher=fetcher))
    return database, vault, TestClient(app), calls, service


def audit_events(database):
    with database.connect() as connection:
        rows = connection.execute("SELECT event_type, detail_json FROM audit_events ORDER BY id").fetchall()
    return [(row["event_type"], json.loads(row["detail_json"])) for row in rows]


def test_share_reference_accepts_only_the_canonical_https_link():
    assert parse_share_reference("abc123DEF_-") == "abc123DEF_-"
    assert parse_share_reference("https://chat.deepseek.com/share/abc123DEF_-") == "abc123DEF_-"
    assert parse_share_reference("  https://chat.deepseek.com/share/abc123DEF_-/  ") == "abc123DEF_-"
    for bad in [
        "http://chat.deepseek.com/share/abc123",
        "https://evil.example/share/abc123",
        "https://chat.deepseek.com.evil.example/share/abc123",
        "https://chat.deepseek.com:8443/share/abc123",
        "https://user@chat.deepseek.com/share/abc123",
        "https://chat.deepseek.com/share/abc123?x=1",
        "https://chat.deepseek.com/chat/abc123",
        "https://chat.deepseek.com/share/ab",
        "file:///share/abc123",
        "",
    ]:
        with pytest.raises(ValueError):
            parse_share_reference(bad)


def test_parse_shared_conversation_keeps_only_user_requests():
    parsed = parse_shared_conversation(share_payload())
    assert parsed["title"] == "虚构分享"
    assert parsed["model_type"] == "default"
    assert [(message["role"], message["content"]) for message in parsed["messages"]] == [
        ("user", "我打算每周三晚上学习英语，先从二十分钟开始。"),
        ("user", "我该怎么办？"),
    ]
    assert parsed["created_at"].startswith("2026-")
    with pytest.raises(ShareImportError):
        parse_shared_conversation({"code": 1, "msg": "gone", "data": {}})


def test_preview_reports_counts_without_writing(setup):
    database, vault, client, calls, _ = setup
    response = client.post("/api/imports/share/preview", json={"url": f"https://chat.deepseek.com/share/{SHARE_ID}"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["share_id"] == SHARE_ID
    assert payload["kept"] == 1
    assert payload["skipped"]["question"] == 1
    assert payload["sentences"][0]["text"] == "我打算每周三晚上学习英语，先从二十分钟开始。"
    assert payload["existing_document_id"] is None
    assert calls == [SHARE_ID]
    assert not list(vault.rglob("*.md"))
    assert database.list_memories() == []


def test_apply_requires_confirmation_then_writes_note_and_candidates(setup):
    database, vault, client, calls, _ = setup
    url = f"https://chat.deepseek.com/share/{SHARE_ID}"
    refused = client.post("/api/imports/share/apply", json={"url": url})
    assert refused.status_code == 422
    assert not list(vault.rglob("*.md"))

    applied = client.post("/api/imports/share/apply", json={"url": url, "confirm": True})
    assert applied.status_code == 200, applied.text
    payload = applied.json()
    assert payload["written"] is True
    assert payload["memory_count"] == 1
    note = vault / payload["note_path"]
    text = note.read_text(encoding="utf-8")
    assert "source_kind: deepseek-share" in text
    assert f"share_id: {SHARE_ID}" in text
    assert "- 我打算每周三晚上学习英语，先从二十分钟开始。" in text
    assert "助手" not in text
    candidates = database.list_memories("candidate")
    assert [item["content"] for item in candidates] == ["我打算每周三晚上学习英语，先从二十分钟开始。"]
    assert candidates[0]["status"] == "candidate"
    assert audit_events(database) == [("share_imported", {"share_id": SHARE_ID, "document_id": payload["document_id"], "candidates": 1})]


def test_reimporting_the_same_share_updates_one_note(setup):
    database, vault, client, _, _ = setup
    url = f"https://chat.deepseek.com/share/{SHARE_ID}"
    first = client.post("/api/imports/share/apply", json={"url": url, "confirm": True}).json()
    again = client.post("/api/imports/share/apply", json={"url": url, "confirm": True}).json()
    assert again["document_id"] == first["document_id"]
    assert again["changed"] is False
    assert len(list(vault.rglob("*.md"))) == 1
    assert len(database.list_memories("candidate")) == 1
    assert len(audit_events(database)) == 2


def test_an_empty_share_writes_nothing(tmp_path):
    database = Database(tmp_path / "empty.sqlite3")
    database.initialize()
    vault = tmp_path / "vault"
    vault.mkdir()
    payload = share_payload(messages=[{"message_id": 1, "role": "USER", "inserted_at": 1790000000.0,
                                       "fragments": [{"id": 1, "type": "REQUEST", "content": "你好"}]}])
    service = ShareImportService(database, vault, fetcher=lambda share_id: payload)
    result = service.apply(SHARE_ID)
    assert result["written"] is False
    assert result["reason"] == "empty"
    assert not list(vault.rglob("*.md"))
    assert audit_events(database) == []


def test_upstream_failures_map_to_clear_statuses(tmp_path):
    database = Database(tmp_path / "fail.sqlite3")
    database.initialize()
    vault = tmp_path / "vault"
    vault.mkdir()

    def broken(share_id):
        raise ShareImportError("分享接口返回 500，请稍后再试")

    app = FastAPI()
    app.include_router(create_share_import_router(database, vault, fetcher=broken))
    client = TestClient(app)
    failed = client.post("/api/imports/share/preview", json={"url": SHARE_ID})
    assert failed.status_code == 502
    assert "分享接口返回 500" in failed.json()["detail"]

    def too_big(share_id):
        raise ShareImportError("分享内容超过大小上限，已停止抓取")

    big_app = FastAPI()
    big_app.include_router(create_share_import_router(database, vault, fetcher=too_big))
    assert TestClient(big_app).post("/api/imports/share/preview", json={"url": SHARE_ID}).status_code == 413

    assert client.post("/api/imports/share/preview", json={"url": "https://evil.example/share/abc123"}).status_code == 422
    assert client.post("/api/imports/share/preview", json={"url": SHARE_ID, "extra": 1}).status_code == 422
