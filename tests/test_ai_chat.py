"""Role chat/run endpoints: threads, proposal hand-off, task apply and identity gate."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app import ai_roles, task_reports
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.deepseek_balance import DeepSeekBalanceService
from backend.app.dsh_web import DshCommand
from backend.app.identity import read_identity

TASK_REPLY = {
    "reply": "先做最小的一步：把周报写出来。",
    "proposals": [{
        "kind": "new_task",
        "title": "写周报",
        "reason": "今天是截止日",
        "payload": {"title": "写周报", "date": "2026-10-29", "priority": "high"},
        "sources": ["S1"],
    }],
    "hub_events": [{"kind": "observation", "summary": "他今天在整理任务"}],
}

IDENTITY_REPLY = {
    "reply": "你的理由成立，我愿意改。",
    "identity_verdict": {"decision": "accept", "confidence": 0.8,
                         "reason": "你给出了三个具体反例"},
    "proposals": [{
        "kind": "identity_patch",
        "title": "调整价值观",
        "reason": "你证明了旧描述不准确",
        "payload": {"changes": [{"field": "values", "from": "", "to": "诚实"}]},
    }],
}


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        DeepSeekBalanceService, "describe",
        lambda self: {"enabled": True, "saved": True, "env": False,
                      "dsh": False, "source": "saved"})
    monkeypatch.setattr(DeepSeekBalanceService, "resolve_key", lambda self: "sk-test")
    monkeypatch.setattr(
        ai_roles, "resolve_command",
        lambda environ=None: DshCommand(("dsh",), "installed"))

    state = {"text": json.dumps(TASK_REPLY, ensure_ascii=False)}
    seen: dict[str, object] = {}

    def fake_run(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["session_id"] = kwargs.get("session_id")
        return ai_roles.HeadlessResult(
            text=state["text"], session_id="session-role-1", seconds=0.2,
            returncode=0, stderr_tail="")

    monkeypatch.setattr(ai_roles, "run_headless", fake_run)
    database = Database(tmp_path / "chat.sqlite3")
    app = create_app(database, tmp_path / "vault", security_environ={},
                     env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client, database, state, seen


def test_chat_stores_thread_turns_and_creates_a_task_proposal(tmp_path, monkeypatch):
    client, database, _, seen = make_client(tmp_path, monkeypatch)
    response = client.post("/api/ai/chat", json={
        "role_id": "life_assistant", "mode": "tasks",
        "message": "帮我理一下今天", "entry": "workbench.tasks",
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["reply"].startswith("先做最小的一步")
    assert payload["thread_id"].startswith("thread_")
    assert len(payload["proposals"]) == 1
    assert payload["proposals"][0]["kind"] == "new_task"
    assert payload["hub_events"] == 1
    assert "用户这一轮说的话" in str(seen["prompt"])
    assert "只读资料" in str(seen["prompt"])

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_threads").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM ai_turns").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM memory_hub_events").fetchone()[0] == 1

    thread_id = payload["thread_id"]
    detail = client.get(f"/api/ai/threads/{thread_id}").json()
    assert [turn["role"] for turn in detail["turns"]] == ["user", "assistant"]
    assert len(client.get("/api/ai/threads").json()["items"]) == 1


def test_continue_thread_reuses_the_dsh_session(tmp_path, monkeypatch):
    client, _, _, seen = make_client(tmp_path, monkeypatch)
    first = client.post("/api/ai/chat", json={
        "role_id": "life_assistant", "mode": "tasks", "message": "第一句",
    }).json()
    second = client.post("/api/ai/chat", json={
        "role_id": "life_assistant", "mode": "tasks", "message": "第二句",
        "thread_id": first["thread_id"],
    })
    assert second.status_code == 200, second.text
    assert seen["session_id"] == "session-role-1"
    detail = client.get(f"/api/ai/threads/{first['thread_id']}").json()
    assert len(detail["turns"]) == 4
    # 续聊提示里应带上上一轮，而不是只发第二句。
    assert "第一句" in str(seen["prompt"])


def test_task_proposal_applies_into_a_markdown_task_note(tmp_path, monkeypatch):
    client, _, _, _ = make_client(tmp_path, monkeypatch)
    payload = client.post("/api/ai/chat", json={
        "role_id": "life_assistant", "mode": "tasks", "message": "排一下今天",
    }).json()
    proposal_id = payload["proposals"][0]["id"]
    applied = client.post(f"/api/proposals/{proposal_id}/apply", json={"confirm": True})
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied"
    titles = [task["title"] for task in task_reports.open_tasks(tmp_path / "vault")]
    assert "写周报" in titles


def test_run_once_returns_structured_polish_and_sends_no_context_by_default(tmp_path, monkeypatch):
    client, _, state, seen = make_client(tmp_path, monkeypatch)
    state["text"] = json.dumps(
        {"polished": "更顺的句子。", "changes": ["标点"]}, ensure_ascii=False)
    response = client.post("/api/ai/run", json={
        "role_id": "journal_companion", "mode": "polish",
        "input": "原句", "entry": "life.editor",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["structured"]["polished"] == "更顺的句子。"
    assert body["structured"]["changes"] == ["标点"]
    assert body["sources"] == []
    assert "只读资料" not in str(seen["prompt"])


def test_identity_patch_needs_the_socratic_verdict_token(tmp_path, monkeypatch):
    client, database, state, _ = make_client(tmp_path, monkeypatch)
    state["text"] = json.dumps(IDENTITY_REPLY, ensure_ascii=False)
    response = client.post("/api/ai/chat", json={
        "role_id": "yiling", "mode": "converse",
        "message": "我觉得我的价值观描述不准确，因为……",
        "entry": "self.socrates",
    })
    assert response.status_code == 200, response.text
    proposal = response.json()["proposals"][0]
    assert proposal["kind"] == "identity_patch"
    assert proposal["requires_dialogue"] is True
    assert proposal["verdict_ready"] is True

    blocked = client.post(f"/api/proposals/{proposal['id']}/apply",
                          json={"confirm": True})
    assert blocked.status_code == 409
    assert "苏格拉底" in blocked.text

    with database.connect() as connection:
        token = connection.execute(
            "SELECT verdict_token FROM ai_proposals WHERE id=?",
            (proposal["id"],)).fetchone()[0]
    assert token
    # P3 已注册身份补丁 handler：带正确 verdict token 才能写入。
    with_token = client.post(f"/api/proposals/{proposal['id']}/apply",
                             json={"confirm": True, "verdict_token": token})
    assert with_token.status_code == 200, with_token.text
    assert read_identity(tmp_path / "vault")["values"] == "诚实"


def test_chat_rejects_wrong_role_mode_and_unknown_selector(tmp_path, monkeypatch):
    client, _, _, _ = make_client(tmp_path, monkeypatch)
    assert client.post("/api/ai/chat", json={
        "role_id": "nobody", "mode": "tasks", "message": "x",
    }).status_code == 404
    assert client.post("/api/ai/chat", json={
        "role_id": "life_assistant", "mode": "classify", "message": "x",
    }).status_code == 422
    assert client.post("/api/ai/chat", json={
        "role_id": "life_assistant", "mode": "tasks", "message": "x",
        "selectors": [{"entry": "share"}],
    }).status_code == 422


def test_delete_thread_requires_confirmation(tmp_path, monkeypatch):
    client, _, _, _ = make_client(tmp_path, monkeypatch)
    thread_id = client.post("/api/ai/chat", json={
        "role_id": "life_assistant", "mode": "tasks", "message": "x",
    }).json()["thread_id"]
    assert client.delete(f"/api/ai/threads/{thread_id}").status_code == 422
    assert client.delete(f"/api/ai/threads/{thread_id}?confirm=true").status_code == 200
    assert client.get(f"/api/ai/threads/{thread_id}").status_code == 404
