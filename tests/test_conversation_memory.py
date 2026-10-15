"""Conversation → candidate memories: temporary vault and database only."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.conversation_memory import (
    ConversationMemoryService,
    collect_sentences,
    create_conversation_memory_router,
)
from backend.app.db import Database


@pytest.fixture
def setup(tmp_path):
    database = Database(tmp_path / "conversation.sqlite3")
    database.initialize()
    vault = tmp_path / "vault"
    vault.mkdir()
    app = FastAPI()
    app.include_router(create_conversation_memory_router(database, vault))
    return database, vault, TestClient(app)


def message(role, content, message_id=1):
    return {"id": message_id, "role": role, "content": content}


def seed_conversation(database, user_messages, assistant="我先记下这些。"):
    conversation_id = database.create_conversation("测试对话")
    for index, content in enumerate(user_messages):
        database.add_message(conversation_id, "user", content, [])
        database.add_message(conversation_id, "assistant", assistant, [])
    return conversation_id


def audit_events(database):
    with database.connect() as connection:
        rows = connection.execute("SELECT event_type, detail_json FROM audit_events ORDER BY id").fetchall()
    return [(row["event_type"], json.loads(row["detail_json"])) for row in rows]


def test_collect_sentences_keeps_statements_and_counts_skips():
    collected = collect_sentences([
        message("user", "我最近在准备考研，数学是重点。我的目标是 2028 年上岸。"),
        message("assistant", "我记住了这条对话里的目标。", 2),
        message("user", "我该怎么办？", 3),
        message("user", "辛苦了！", 4),
        message("user", "我最近在准备考研，数学是重点。", 5),
    ])
    assert [item["text"] for item in collected["sentences"]] == [
        "我最近在准备考研，数学是重点。",
        "我的目标是 2028 年上岸。",
    ]
    assert collected["skipped"]["question"] == 1
    assert collected["skipped"]["chatter"] == 1
    assert collected["skipped"]["duplicate"] == 1
    assert collected["sentences"][0]["message_id"] == 1


def test_collect_sentences_only_reads_the_user_and_caps_the_list():
    messages = [message("assistant", "我计划明天早起。")]
    messages += [message("user", f"我在坚持第 {index} 个习惯。", index + 1) for index in range(30)]
    collected = collect_sentences(messages)
    assert len(collected["sentences"]) == 20
    assert collected["skipped"]["over_limit"] == 10


def test_preview_lists_sentences_without_writing_anything(setup):
    database, vault, client = setup
    conversation_id = seed_conversation(database, ["我每周三晚上学习英语。", "我该怎么办？"])
    response = client.post(f"/api/conversations/{conversation_id}/extract-candidates", json={"dry_run": True})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["kept"] == 1
    assert payload["sentences"][0]["text"] == "我每周三晚上学习英语。"
    assert payload["skipped"]["question"] == 1
    assert payload["existing_document_id"] is None
    assert payload["notice"].startswith("只提炼你说的话")
    assert not list(vault.rglob("*.md"))
    assert database.list_memories() == []
    assert client.post("/api/conversations/9999/extract-candidates", json={"dry_run": True}).status_code == 404


def test_apply_needs_confirmation_and_writes_a_note_plus_candidates(setup):
    database, vault, client = setup
    conversation_id = seed_conversation(database, ["我计划每周跑三次步，先从两公里开始。"])
    refused = client.post(
        f"/api/conversations/{conversation_id}/extract-candidates", json={"dry_run": False}
    )
    assert refused.status_code == 422
    assert not list(vault.rglob("*.md"))

    applied = client.post(
        f"/api/conversations/{conversation_id}/extract-candidates",
        json={"dry_run": False, "confirm": True},
    )
    assert applied.status_code == 200, applied.text
    payload = applied.json()
    assert payload["written"] is True
    assert payload["changed"] is True
    assert payload["memory_count"] == 1
    note = Path(vault) / payload["note_path"]
    assert note.is_file()
    text = note.read_text(encoding="utf-8")
    assert "source_kind: conversation-extract" in text
    assert f"conversation_id: {conversation_id}" in text
    assert "- 我计划每周跑三次步，先从两公里开始。" in text

    candidates = database.list_memories("candidate")
    assert [item["content"] for item in candidates] == ["我计划每周跑三次步，先从两公里开始。"]
    assert candidates[0]["status"] == "candidate"
    assert candidates[0]["source_path"].endswith(payload["note_path"])
    assert audit_events(database) == [("conversation_extracted", {"conversation_id": conversation_id, "document_id": payload["document_id"], "candidates": 1})]


def test_repeating_the_extraction_updates_the_same_note_instead_of_stacking(setup):
    database, vault, client = setup
    conversation_id = seed_conversation(database, ["我习惯在早上写日记。"])
    first = client.post(
        f"/api/conversations/{conversation_id}/extract-candidates", json={"dry_run": False, "confirm": True}
    ).json()
    again = client.post(
        f"/api/conversations/{conversation_id}/extract-candidates", json={"dry_run": False, "confirm": True}
    ).json()
    assert again["document_id"] == first["document_id"]
    assert again["changed"] is False
    assert len(list(vault.rglob("*.md"))) == 1
    assert len(database.list_memories("candidate")) == 1

    database.add_message(conversation_id, "user", "我打算把日记改到晚上，方便复盘。", [])
    third = client.post(
        f"/api/conversations/{conversation_id}/extract-candidates", json={"dry_run": False, "confirm": True}
    ).json()
    assert third["document_id"] == first["document_id"]
    assert third["changed"] is True
    assert third["memory_count"] == 2
    assert len(list(vault.rglob("*.md"))) == 1
    assert {item["content"] for item in database.list_memories("candidate")} == {
        "我习惯在早上写日记。", "我打算把日记改到晚上，方便复盘。",
    }
    assert len(database.list_memories("archived")) == 1


def test_an_empty_extraction_writes_nothing(setup):
    database, vault, client = setup
    conversation_id = seed_conversation(database, ["你好", "我该怎么办？"])
    payload = client.post(
        f"/api/conversations/{conversation_id}/extract-candidates",
        json={"dry_run": False, "confirm": True},
    ).json()
    assert payload["written"] is False
    assert payload["reason"] == "empty"
    assert payload["memory_count"] == 0
    assert not list(vault.rglob("*.md"))
    assert audit_events(database) == []


def test_service_preview_reports_existing_note(tmp_path):
    database = Database(tmp_path / "service.sqlite3")
    database.initialize()
    vault = tmp_path / "vault"
    vault.mkdir()
    service = ConversationMemoryService(database, vault)
    conversation_id = seed_conversation(database, ["我在练习写作，每周三篇。"])
    assert service.preview(conversation_id)["existing_document_id"] is None
    first = service.apply(conversation_id)
    assert service.preview(conversation_id)["existing_document_id"] == first["document_id"]
    repeated = service.apply(conversation_id)
    assert repeated["written"] is True
    assert repeated["changed"] is False
    assert repeated["document_id"] == first["document_id"]
    assert repeated["memory_count"] == 1
    assert [item["content"] for item in repeated["candidates"]] == ["我在练习写作，每周三篇。"]
