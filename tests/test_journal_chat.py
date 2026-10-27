"""日记对话通道：事件解析、Key 注入、失败降级。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import journal_chat
from backend.app.db import Database

NDJSON = "\n".join([
    '{"type":"session","sessionId":"session-abc","cwd":"C:\\\\vault"}',
    '{"type":"status","phase":"turn_start","turn":1}',
    '{"type":"final","text":"今天先说你改的那篇《考研规划》吧。"}',
])


def test_parse_events_reads_session_and_final_text():
    assert journal_chat.parse_events(NDJSON) == ("session-abc", "今天先说你改的那篇《考研规划》吧。")
    assert journal_chat.parse_events("not json\n\n{broken") == ("", "")


def test_opening_prompt_carries_facts_and_rules():
    digest = {"signals": ["完成 1 件：写周报", "还挂着 1 件：背单词（2026-10-22）"]}
    prompt = journal_chat.opening_prompt(digest)
    assert "写周报" in prompt and "背单词（2026-10-22）" in prompt
    assert "一次只问一个问题" in prompt
    assert "不读写任何文件" in prompt


def test_run_turn_parses_stdout_and_reports_session(tmp_path):
    seen = {}

    def runner(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs.get("env")
        return SimpleNamespace(returncode=0, stdout=NDJSON, stderr="")

    turn = journal_chat.run_turn(
        "开始吧", workspace=tmp_path,
        environ={"DEEPSEEK_API_KEY": "sk-test"}, runner=runner)

    assert turn.session_id == "session-abc"
    assert turn.text.startswith("今天先说你改的那篇")
    assert seen["argv"][-1] == "开始吧"
    assert seen["env"]["DEEPSEEK_API_KEY"] == "sk-test"


def test_run_turn_resumes_with_session_id(tmp_path):
    seen = {}

    def runner(argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout=NDJSON, stderr="")

    journal_chat.run_turn("接着说", workspace=tmp_path, environ={},
                          session_id="session-abc", runner=runner)
    argv = seen["argv"]
    assert "--session-id" in argv and argv[argv.index("--session-id") + 1] == "session-abc"
    assert argv[-1] == "接着说"


def test_run_turn_degrades_on_empty_or_failed_output(tmp_path):
    empty = lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr="")  # noqa: E731
    with pytest.raises(journal_chat.ChatUnavailable):
        journal_chat.run_turn("hi", workspace=tmp_path, environ={}, runner=empty)

    failed = lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="boom")  # noqa: E731
    with pytest.raises(journal_chat.ChatUnavailable):
        journal_chat.run_turn("hi", workspace=tmp_path, environ={}, runner=failed)


def test_chat_environment_injects_the_key():
    env = journal_chat.chat_environment({"PATH": "x"}, "sk-1")
    assert env["DEEPSEEK_API_KEY"] == "sk-1"
    assert env["PYTHONUTF8"] == "1"


def make_client(tmp_path, *, key="sk-test", enabled=True):
    app = FastAPI()
    app.include_router(journal_chat.create_journal_chat_router(
        Database(tmp_path / "chat.sqlite3"), tmp_path / "vault",
        SimpleNamespace(resolve_key=lambda: key), tmp_path, enabled=enabled))
    return TestClient(app)


def test_chat_without_key_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(journal_chat, "resolve_command", lambda environ=None: SimpleNamespace(argv=("dsh",)))
    client = make_client(tmp_path, key="")

    status = client.get("/api/journal/chat/status").json()
    assert status["available"] is False and "Key" in status["reason"]

    reply = client.post("/api/journal/chat", json={"day": "2026-10-28", "message": "你好"})
    assert reply.status_code == 409


def test_chat_without_dsh_command_reports_why(tmp_path, monkeypatch):
    monkeypatch.setattr(journal_chat, "resolve_command", lambda environ=None: None)
    client = make_client(tmp_path)
    status = client.get("/api/journal/chat/status").json()
    assert status["available"] is False and "dsh" in status["reason"]


def test_chat_turn_returns_reply(tmp_path, monkeypatch):
    monkeypatch.setattr(journal_chat, "resolve_command", lambda environ=None: SimpleNamespace(argv=("dsh",)))

    def fake_turn(prompt, **kwargs):
        assert prompt == "今天有点累"
        return journal_chat.ChatTurn(session_id="session-x", text="那就少说两句。", seconds=1.2)

    monkeypatch.setattr(journal_chat, "run_turn", fake_turn)
    client = make_client(tmp_path)

    payload = client.post("/api/journal/chat", json={
        "day": "2026-10-28", "message": "今天有点累", "mode": "turn"}).json()
    assert payload["reply"] == "那就少说两句。"
    assert payload["session_id"] == "session-x"


def test_chat_start_builds_prompt_from_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(journal_chat, "resolve_command", lambda environ=None: SimpleNamespace(argv=("dsh",)))
    captured = {}

    def fake_turn(prompt, **kwargs):
        captured["prompt"] = prompt
        return journal_chat.ChatTurn(session_id="session-y", text="问一句。", seconds=0.4)

    monkeypatch.setattr(journal_chat, "run_turn", fake_turn)
    client = make_client(tmp_path)

    client.post("/api/journal/chat", json={"day": "2026-10-28", "mode": "start"})
    assert "今天的盘点" in captured["prompt"]
