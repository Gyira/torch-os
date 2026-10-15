"""Harness brain (ACP client): streaming, permissions, sessions, fallbacks."""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import harness_brain
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.harness_brain import HarnessBrain, HarnessError
from backend.app.model_gateway import ModelGateway

FAKE_ACP = r'''
import json, os, sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
log_path = os.environ.get("FAKE_ACP_LOG")

def log(event):
    if log_path:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

def emit(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    method = message.get("method")
    mid = message.get("id")
    if mid is not None and method is None:
        log({"permissionResponse": message.get("result")})
        continue
    if method == "initialize":
        emit({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": 1, "agentInfo": {"name": "fake-acp", "version": "0.0.1"},
            "agentCapabilities": {"mcpCapabilities": {"http": False},
                "promptCapabilities": {"image": False, "audio": False, "embeddedContext": False},
                "sessionCapabilities": {"close": {}, "list": {}, "resume": {}}},
            "authMethods": []}})
    elif method == "session/new":
        log({"newSession": message["params"].get("mcpServers")})
        emit({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "sess-1", "configOptions": []}})
    elif method == "session/resume":
        log({"resume": message["params"].get("sessionId")})
        emit({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": message["params"]["sessionId"], "configOptions": []}})
    elif method == "session/prompt":
        log({"prompt": message["params"]["prompt"][0]["text"]})
        emit({"jsonrpc": "2.0", "id": 9001, "method": "session/request_permission", "params": {
            "sessionId": "sess-1", "toolCall": {"toolCallId": "t1", "title": "mcp__torch__today"},
            "options": [{"optionId": "allow-once", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"}]}})
        first = json.loads(sys.stdin.readline())
        log({"firstPermission": first.get("result")})
        emit({"jsonrpc": "2.0", "id": 9002, "method": "session/request_permission", "params": {
            "sessionId": "sess-1", "toolCall": {"toolCallId": "t2", "title": "shell: rm -rf"},
            "options": [{"optionId": "allow-once", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"}]}})
        second = json.loads(sys.stdin.readline())
        log({"secondPermission": second.get("result")})
        emit({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "sess-1", "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "你好，"}}}})
        emit({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "sess-1", "update": {
            "sessionUpdate": "tool_call", "title": "mcp__torch__today", "toolCallId": "t1"}}})
        emit({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "sess-1", "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "这是假 harness 的回答。"}}}})
        emit({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
    elif method == "session/close":
        emit({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif mid is not None:
        emit({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown " + str(method)}})
'''


def make_db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "harness.sqlite3")
    database.initialize()
    return database


def fake_command(tmp_path: Path, monkeypatch) -> list[str]:
    script = tmp_path / "fake_acp.py"
    script.write_text(FAKE_ACP, encoding="utf-8")
    monkeypatch.setenv("FAKE_ACP_LOG", str(tmp_path / "acp.log"))
    return [sys.executable, str(script)]


def read_log(tmp_path: Path) -> list[dict]:
    path = tmp_path / "acp.log"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_run_turn_streams_answer_persists_session_and_guards_permissions(tmp_path, monkeypatch):
    database = make_db(tmp_path)
    brain = HarnessBrain(database, tmp_path / "ws", command=fake_command(tmp_path, monkeypatch), prompt_timeout=20)
    try:
        turn = brain.run_turn(7, "你好", "http://127.0.0.1:8765")
        assert turn["answer"] == "你好，这是假 harness 的回答。"
        assert turn["tools"] == ["mcp__torch__today"]
        assert turn["stop_reason"] == "end_turn"
        assert turn["session_id"] == "sess-1" and turn["resumed"] is False
        with database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM harness_sessions WHERE conversation_id=7").fetchone()
        assert row["session_id"] == "sess-1"

        second = brain.run_turn(7, "再问一次", "http://127.0.0.1:8765")
        assert second["resumed"] is True
        log = read_log(tmp_path)
        assert log[0]["newSession"][0]["name"] == "torch"
        assert any("mcp_bridge.py" in arg for arg in log[0]["newSession"][0]["args"])
        assert any(entry.get("resume") == "sess-1" for entry in log)
        first_permission = next(entry for entry in log if "firstPermission" in entry)
        second_permission = next(entry for entry in log if "secondPermission" in entry)
        assert first_permission["firstPermission"]["outcome"]["optionId"] == "allow-once"
        assert second_permission["secondPermission"]["outcome"]["optionId"] == "reject-once"
        assert any(entry.get("prompt") == "你好" for entry in log)
    finally:
        brain.close()


def test_missing_or_broken_command_reports_clear_errors(tmp_path, monkeypatch):
    database = make_db(tmp_path)
    monkeypatch.setattr(harness_brain, "resolve_command", lambda: None)
    missing = HarnessBrain(database, tmp_path / "ws")
    assert missing.available() is False
    assert "未检测到 dsh" in missing.status()["note"] or missing.status()["available"] is False
    with pytest.raises(HarnessError, match="未检测到 dsh"):
        missing.run_turn(1, "你好")

    broken = HarnessBrain(database, tmp_path / "ws", command=["definitely-not-a-real-dsh-xyz"])
    with pytest.raises(HarnessError, match="无法启动 dsh"):
        broken.run_turn(2, "你好")


def test_chat_endpoint_uses_harness_and_reports_status(tmp_path, monkeypatch):
    command = fake_command(tmp_path, monkeypatch)
    monkeypatch.setattr(harness_brain, "resolve_command", lambda: list(command))
    database = Database(tmp_path / "app.sqlite3")
    app = create_app(
        database, tmp_path / "vault",
        ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1),
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    status = client.get("/api/harness/status").json()
    assert status["available"] is True
    assert client.get("/api/config/status").json()["capabilities"]["harness"] is True

    payload = client.post("/api/chat", json={"message": "你好", "use_harness": True}).json()
    assert payload["mode"] == "harness"
    assert payload["answer"] == "你好，这是假 harness 的回答。"
    assert payload["harness"]["session_id"] == "sess-1"
    assert payload["harness"]["tools"] == ["mcp__torch__today"]
    with database.connect() as connection:
        events = [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type='harness_turn'")]
    assert len(events) == 1 and '"ok": true' in events[0]["detail_json"]


def test_chat_stream_endpoint_emits_deltas_and_falls_back_locally(tmp_path, monkeypatch):
    command = fake_command(tmp_path, monkeypatch)
    monkeypatch.setattr(harness_brain, "resolve_command", lambda: list(command))
    database = Database(tmp_path / "app.sqlite3")
    app = create_app(
        database, tmp_path / "vault",
        ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1),
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")

    with client.stream("POST", "/api/chat/stream",
                       json={"message": "流式问题", "use_harness": True}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    deltas = [event for event in events if event["type"] == "delta"]
    assert len(deltas) == 2
    assert "".join(event["text"] for event in deltas) == "你好，这是假 harness 的回答。"
    done = next(event for event in events if event["type"] == "done")
    assert done["mode"] == "harness"
    assert done["answer"] == "你好，这是假 harness 的回答。"
    assert done["harness"]["tools"] == ["mcp__torch__today"]
    stored = database.get_conversation(done["conversation_id"])
    assert [message["role"] for message in stored["messages"]] == ["user", "assistant"]
    assert stored["messages"][-1]["content"] == done["answer"]
    with database.connect() as connection:
        audits = [dict(row) for row in connection.execute(
            "SELECT event_type FROM audit_events WHERE event_type='harness_turn'")]
    assert len(audits) == 1


def test_chat_stream_endpoint_falls_back_when_dsh_cannot_start(tmp_path, monkeypatch):
    monkeypatch.setattr(harness_brain, "resolve_command", lambda: ["definitely-not-a-real-dsh-xyz"])
    database = Database(tmp_path / "broken.sqlite3")
    app = create_app(
        database, tmp_path / "vault",
        ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1),
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    with client.stream("POST", "/api/chat/stream",
                       json={"message": "回退问题", "use_harness": True}) as response:
        body = "".join(response.iter_text())
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert events and all(event["type"] != "delta" for event in events)
    done = next(event for event in events if event["type"] == "done")
    assert done["mode"] == "fallback"
    assert "Harness 未完成" in done["warning"]
    assert done["answer"]


def test_harness_reset_stops_child_and_clears_sessions(tmp_path, monkeypatch):
    command = fake_command(tmp_path, monkeypatch)
    database = make_db(tmp_path)
    brain = HarnessBrain(database, tmp_path / "ws", command=list(command), prompt_timeout=20)
    try:
        brain.run_turn(5, "重置前", "http://127.0.0.1:8765")
        with database.connect() as connection:
            assert connection.execute(
                "SELECT 1 FROM harness_sessions WHERE conversation_id=5").fetchone()
        assert brain.status()["running"] is True
        result = brain.reset(5)
        assert result == {"cleared_sessions": 1, "process_stopped": True}
        with database.connect() as connection:
            assert connection.execute("SELECT 1 FROM harness_sessions").fetchone() is None
        assert brain.status()["running"] is False
    finally:
        brain.close()


def test_harness_reset_endpoint_clears_session_and_audits(tmp_path, monkeypatch):
    command = fake_command(tmp_path, monkeypatch)
    monkeypatch.setattr(harness_brain, "resolve_command", lambda: list(command))
    database = Database(tmp_path / "app.sqlite3")
    app = create_app(
        database, tmp_path / "vault",
        ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1),
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    assert client.get("/api/config/status").json()["capabilities"]["harness_reset"] is True
    payload = client.post("/api/chat", json={"message": "重置", "use_harness": True}).json()
    response = client.post("/api/harness/reset", json={"conversation_id": payload["conversation_id"]})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["cleared_sessions"] == 1
    assert client.get("/api/harness/status").json()["running"] is False
    with database.connect() as connection:
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type='harness_reset'")]
    assert len(rows) == 1
    assert str(payload["conversation_id"]) in rows[0]["detail_json"]
