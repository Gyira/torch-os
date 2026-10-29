"""忆灵：事件去重、综合、画像快照、复盘卡、身份补丁与 revision 保护。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.app import memory_hub
from backend.app.ai_gateway import HeadlessResult
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.identity import IdentityWrite, read_identity, write_identity
from backend.app.proposals import ProposalError, ProposalService

SYNTH = {
    "portrait": "他最近在收尾一个长期项目，反复提到“先做最小的一步”。",
    "observations": [{"title": "收尾模式", "text": "最近记录集中在项目收尾。"}],
    "review_cards": [{
        "kind": "observation", "title": "先做最小的一步",
        "text": "他在多篇日记里用同一句话推动自己。",
        "confidence": "high", "sources": ["S1"],
    }],
    "identity_patches": [{
        "field": "values", "from": "", "to": "诚实",
        "reason": "多次提到宁可吃亏也不骗人", "sources": ["S1"],
    }],
    "sources": ["S1"],
    "confidence": 0.8,
}


class FakeRole:
    def __init__(self, text: str = ""):
        self.text = text or json.dumps(SYNTH, ensure_ascii=False)
        self.calls = []

    def run(self, role_id, mode, **kwargs):
        self.calls.append((role_id, mode, kwargs))
        return HeadlessResult(text=self.text, session_id="session-yiling",
                              seconds=0.2, returncode=0, stderr_tail="")


def make_vault(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    write_identity(vault, IdentityWrite(name="小明", revision=""))
    journal = vault / "00_人生路/01_日记/2026-10-29.md"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("---\ntype: journal\n---\n\n今天把项目收尾了，先做最小的一步。\n",
                       encoding="utf-8")
    database = Database(tmp_path / "hub.sqlite3")
    database.initialize()
    return database, vault


def test_events_are_deduplicated_and_absorbed(tmp_path):
    database, _ = make_vault(tmp_path)
    memory_hub.ingest_event(database, kind="observation", summary="今天在整理任务",
                            source_ref="thread-1", source_kind="life_assistant")
    memory_hub.ingest_event(database, kind="observation", summary="今天在整理任务",
                            source_ref="thread-1", source_kind="life_assistant")
    assert len(memory_hub.list_events(database)) == 1


def test_synthesize_writes_portrait_cards_snapshots_and_identity_patch(tmp_path):
    database, vault = make_vault(tmp_path)
    proposals = ProposalService(database)
    memory_hub.ingest_event(database, kind="observation", summary="今天在整理任务")
    role = FakeRole()

    result = memory_hub.synthesize(database, vault, role, proposals, range_days=7)

    assert result["status"] == "ok"
    assert result["events"] == 1
    portrait = memory_hub.read_portrait(vault)
    assert "收尾" in portrait["portrait"]
    assert (vault / memory_hub.PORTRAIT_REL).is_file()
    assert role.calls and "今天在整理任务" in role.calls[0][2]["context_text"]
    assert "## 身份" in role.calls[0][2]["context_text"]
    assert memory_hub.list_cards(vault)
    assert (vault / result["report"]["observation"]).is_file()
    assert len(result["identity_patches"]) == 1
    patch = result["identity_patches"][0]
    assert patch["kind"] == "identity_patch"
    assert patch["requires_dialogue"] is False
    assert memory_hub.list_events(database, status="new") == []


def test_keep_and_dismiss_cards(tmp_path):
    database, vault = make_vault(tmp_path)
    card = memory_hub._write_card(vault, kind="observation", title="测试卡",
                                  text="这是一条观察", sources=["S1"])
    kept = memory_hub.keep_card(database, vault, card["id"])
    assert kept["note"]["chapter"] == "yiling"
    assert (vault / kept["note"]["path"]).is_file()
    assert memory_hub.list_cards(vault) == []

    card2 = memory_hub._write_card(vault, kind="question", title="问题卡", text="问什么？")
    dismissed = memory_hub.dismiss_card(database, vault, card2["id"], reason="不想答")
    assert dismissed["archived_as"].endswith(".md")
    assert memory_hub.list_cards(vault) == []


def test_identity_patch_is_revision_guarded_and_backed_up(tmp_path):
    database, vault = make_vault(tmp_path)
    current = read_identity(vault)
    patch = {
        "id": "prop_identity",
        "base_revision": current["revision"],
        "payload": {"changes": [{"field": "values", "from": "", "to": "诚实"}]},
    }
    applied = memory_hub.apply_identity_patch(database, vault, patch)
    assert applied["fields"] == ["values"]
    assert "诚实" in read_identity(vault)["values"]
    assert (vault / ".torch-os/身份备份").is_dir()

    stale = {
        "id": "prop_stale",
        "base_revision": "old-revision",
        "payload": {"changes": [{"field": "values", "to": "新的"}]},
    }
    with pytest.raises(ProposalError) as exc:
        memory_hub.apply_identity_patch(database, vault, stale)
    assert exc.value.status_code == 409


def test_memory_hub_api_synthesize_and_apply_identity(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    database = Database(tmp_path / "hub-api.sqlite3")
    app = create_app(database, vault, security_environ={}, env_file=tmp_path / ".env",
                     enable_dsh=False)
    app.state.ai_service.run = lambda *args, **kwargs: HeadlessResult(
        text=json.dumps(SYNTH, ensure_ascii=False), session_id="session-hub",
        seconds=0.1, returncode=0, stderr_tail="")
    client = TestClient(app)
    client.get("/")

    blocked = client.post("/api/memory-hub/synthesize", json={"confirm": False})
    assert blocked.status_code == 422
    synthesized = client.post("/api/memory-hub/synthesize",
                              json={"confirm": True, "range_days": 7})
    assert synthesized.status_code == 200, synthesized.text
    body = synthesized.json()
    assert body["status"] == "ok"
    overview = client.get("/api/memory-hub/overview").json()
    assert overview["portrait"]["portrait"]
    assert overview["cards"]
    assert overview["identity_patches"]

    proposal_id = overview["identity_patches"][0]["id"]
    applied = client.post(f"/api/proposals/{proposal_id}/apply",
                          json={"confirm": True})
    assert applied.status_code == 200, applied.text
    assert "诚实" in read_identity(vault)["values"]
