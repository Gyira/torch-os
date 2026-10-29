"""Generic AI proposals: pending → apply/reject, idempotency and identity verdict gate."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.db import Database
from backend.app.proposals import ProposalError, ProposalService, create_proposals_router


def make_service(tmp_path):
    database = Database(tmp_path / "proposals.sqlite3")
    database.initialize()
    return database, ProposalService(database)


def test_create_list_and_get_a_pending_proposal(tmp_path):
    database, service = make_service(tmp_path)
    payload = {"chapter": "topic", "title": "Rust 所有权", "body": "..."}
    created = service.create(
        kind="new_note", title="整理 Rust 所有权", payload=payload,
        summary="来自一次分享链接", reason="知识库还没有这篇",
        sources=["https://chat.deepseek.com/share/x"], role_id="academic_mentor")

    assert created["status"] == "pending"
    assert created["can_apply"] is True
    assert created["payload"] == payload
    assert created["sources"] == ["https://chat.deepseek.com/share/x"]
    assert service.get(created["id"])["id"] == created["id"]
    assert [item["id"] for item in service.list()] == [created["id"]]
    with database.connect() as connection:
        events = connection.execute(
            "SELECT event_type FROM audit_events ORDER BY id").fetchall()
    assert [row["event_type"] for row in events] == ["proposal_created"]


def test_apply_requires_confirmation_and_a_registered_handler(tmp_path):
    _, service = make_service(tmp_path)
    created = service.create(kind="new_task", title="写周报", payload={"title": "写周报"})

    with pytest.raises(ProposalError) as exc:
        service.apply(created["id"])
    assert exc.value.status_code == 422

    with pytest.raises(ProposalError) as exc:
        service.apply(created["id"], confirm=True)
    assert exc.value.status_code == 409
    assert "还没有启用" in str(exc.value)
    assert service.get(created["id"])["status"] == "pending"

    calls = []

    def handler(row, edits):
        calls.append((row["id"], edits))
        return {"task_path": "00_人生路/08_任务与待办/任务/写周报.md"}

    service.register("new_task", handler)
    applied = service.apply(created["id"], confirm=True, edits={"priority": "high"})
    assert applied["status"] == "applied"
    assert applied["result"]["task_path"].endswith("写周报.md")
    assert calls == [(created["id"], {"priority": "high"})]
    # 幂等：重复 apply 返回同一个已完成提案，不再调用 handler。
    assert service.apply(created["id"], confirm=True)["status"] == "applied"
    assert len(calls) == 1


def test_reject_keeps_vault_untouched_and_blocks_apply(tmp_path):
    _, service = make_service(tmp_path)
    created = service.create(kind="new_habit", title="每天走 6000 步",
                             payload={"name": "走路"})
    rejected = service.reject(created["id"], reason="这周先不加")
    assert rejected["status"] == "rejected"
    assert rejected["result"]["reason"] == "这周先不加"
    with pytest.raises(ProposalError):
        service.apply(created["id"], confirm=True)


def test_duplicate_dedupe_key_is_rejected(tmp_path):
    _, service = make_service(tmp_path)
    service.create(kind="new_note", title="同一篇", payload={"body": "x"},
                   dedupe_key="same-key")
    with pytest.raises(ProposalError) as exc:
        service.create(kind="new_note", title="同一篇", payload={"body": "x"},
                       dedupe_key="same-key")
    assert exc.value.status_code == 409


def test_identity_patch_with_dialogue_requires_the_verdict_token(tmp_path):
    _, service = make_service(tmp_path)
    calls = []
    service.register("identity_patch",
                     lambda row, edits: calls.append(row["id"]) or {"field": "values"})
    created = service.create(
        kind="identity_patch", title="更新价值观",
        payload={"changes": [{"field": "values", "from": "", "to": "诚实"}]},
        role_id="yiling", base_revision="rev-1",
        verdict={"decision": "accept", "requires_dialogue": True},
        verdict_token="verdict-token-1")

    assert created["requires_dialogue"] is True
    assert created["verdict_ready"] is True
    with pytest.raises(ProposalError) as exc:
        service.apply(created["id"], confirm=True)
    assert "苏格拉底" in str(exc.value)

    with pytest.raises(ProposalError):
        service.apply(created["id"], confirm=True, verdict_token="wrong")
    assert calls == []

    applied = service.apply(created["id"], confirm=True, verdict_token="verdict-token-1")
    assert applied["status"] == "applied"
    assert calls == [created["id"]]


def test_memory_hub_identity_patch_can_be_applied_without_a_dialogue_token(tmp_path):
    _, service = make_service(tmp_path)
    service.register("identity_patch", lambda row, edits: {"field": "values"})
    created = service.create(
        kind="identity_patch", title="忆灵建议更新价值观",
        payload={"changes": [{"field": "values", "from": "", "to": "诚实"}]},
        role_id="yiling", base_revision="rev-1",
        verdict={"decision": "accept"})
    assert created["requires_dialogue"] is False
    assert service.apply(created["id"], confirm=True)["status"] == "applied"


def test_http_surface_maps_errors_and_lists_pending(tmp_path):
    _, service = make_service(tmp_path)
    app = FastAPI()
    app.include_router(create_proposals_router(service))
    client = TestClient(app)
    created = service.create(kind="new_note", title="测试", payload={"body": "x"})

    listing = client.get("/api/proposals").json()["items"]
    assert [item["id"] for item in listing] == [created["id"]]
    assert client.get(f"/api/proposals/{created['id']}").status_code == 200

    unconfirmed = client.post(f"/api/proposals/{created['id']}/apply",
                              json={"confirm": False})
    assert unconfirmed.status_code == 422
    no_handler = client.post(f"/api/proposals/{created['id']}/apply",
                             json={"confirm": True})
    assert no_handler.status_code == 409

    rejected = client.post(f"/api/proposals/{created['id']}/reject",
                           json={"reason": "先不要"})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
