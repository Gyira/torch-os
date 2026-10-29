"""Role gateway: stdin prompt, NDJSON parsing, environment isolation, failure modes."""

from __future__ import annotations

import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from backend.app import ai_gateway
from backend.app.dsh_web import DshCommand

NDJSON = "\n".join([
    '{"type":"session","sessionId":"session-role-1"}',
    '{"type":"status","phase":"turn_start","turn":1}',
    '{"type":"final","text":"先把今天最重的那件事拆成两步。"}',
])


def test_parse_events_reads_session_final_and_errors():
    session, text, errors = ai_gateway.parse_events(NDJSON)
    assert session == "session-role-1"
    assert text.startswith("先把今天最重")
    assert errors == []

    session, text, errors = ai_gateway.parse_events(
        'not json\n{"type":"error","message":"boom"}\n{broken')
    assert (session, text, errors) == ("", "", ["boom"])


def test_headless_argv_uses_stdin_and_can_resume():
    command = DshCommand(("dsh",), "installed")
    argv = ai_gateway.headless_argv(command)
    assert argv[-4:] == ["--profile", "headless", "--json", "-"]

    resumed = ai_gateway.headless_argv(command, session_id="session-abc")
    assert resumed[-4:] == ["--json", "--session-id", "session-abc", "-"]
    # 参数顺序：session-id 必须在任务 `-` 之前。
    assert resumed.index("--session-id") < resumed.index("-")


def test_headless_environment_drops_vault_and_repo_paths():
    env = ai_gateway.headless_environment({
        "PATH": "C:/bin", "VAULT_DIR": "C:/secret/vault", "DATA_DIR": "C:/secret/data",
        "TORCH_REPO_ROOT": "C:/repo", "DEEPSEEK_API_KEY": "sk-should-not-be-copied",
        "SystemRoot": "C:/Windows",
    }, key="sk-test", agents_home="C:/repo/dsh/roles")
    assert env["PATH"] == "C:/bin"
    assert env["SystemRoot"] == "C:/Windows"
    assert env["DEEPSEEK_API_KEY"] == "sk-test"
    assert env["DSH_AGENTS_HOME"] == "C:/repo/dsh/roles"
    assert "VAULT_DIR" not in env and "DATA_DIR" not in env and "TORCH_REPO_ROOT" not in env


def test_headless_environment_never_copies_an_unapproved_key():
    env = ai_gateway.headless_environment({"DEEPSEEK_API_KEY": "sk-old"}, key="")
    assert "DEEPSEEK_API_KEY" not in env


def test_run_headless_sends_prompt_on_stdin_and_parses_final(tmp_path):
    seen = {}

    def runner(argv, **kwargs):
        seen["argv"] = argv
        seen["input"] = kwargs.get("input")
        seen["cwd"] = kwargs.get("cwd")
        return SimpleNamespace(returncode=0, stdout=NDJSON, stderr="")

    result = ai_gateway.run_headless(
        "帮我看看今天", workspace=tmp_path, environ={"PATH": "x"},
        command=DshCommand(("dsh",), "installed"), runner=runner)

    assert seen["input"] == "帮我看看今天"
    assert seen["argv"][-1] == "-"
    assert seen["cwd"] == str(tmp_path)
    assert result.session_id == "session-role-1"
    assert result.text.startswith("先把今天最重")
    assert result.returncode == 0


def test_run_headless_accepts_plain_text_from_older_builds(tmp_path):
    def runner(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout="直接回答，没有 NDJSON。\n", stderr="")

    result = ai_gateway.run_headless(
        "hi", workspace=tmp_path, environ={},
        command=DshCommand(("dsh",), "installed"), runner=runner)
    assert result.text == "直接回答，没有 NDJSON。"


def test_run_headless_rejects_empty_prompt(tmp_path):
    with pytest.raises(ai_gateway.AIError):
        ai_gateway.run_headless("   ", workspace=tmp_path, environ={},
                                command=DshCommand(("dsh",), "installed"))


def test_run_headless_degrades_on_failed_or_empty_output(tmp_path):
    def failed(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom sk-secret")

    with pytest.raises(ai_gateway.AIUnavailable) as exc:
        ai_gateway.run_headless("hi", workspace=tmp_path, environ={},
                                command=DshCommand(("dsh",), "installed"), runner=failed)
    assert "boom" in str(exc.value)
    assert "sk-secret" not in str(exc.value)

    def empty(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with pytest.raises(ai_gateway.AIProtocolError):
        ai_gateway.run_headless("hi", workspace=tmp_path, environ={},
                                command=DshCommand(("dsh",), "installed"), runner=empty)


def test_run_headless_maps_timeout(tmp_path):
    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    with pytest.raises(ai_gateway.AITimeout):
        ai_gateway.run_headless("hi", workspace=tmp_path, environ={},
                                command=DshCommand(("dsh",), "installed"),
                                timeout=1, runner=slow)


def test_redact_hides_keys_and_tokens():
    assert "sk-***" in ai_gateway.redact("key sk-abcdef")
    assert "token=***" in ai_gateway.redact("http://127.0.0.1:1/?token=secret&x=1")


def test_role_runs_never_exceed_the_global_concurrency_limit(tmp_path):
    """P5 验收：多个页面同时发角色请求时，全局最多只跑 2 个 dsh。"""
    lock = threading.Lock()
    active = 0
    peak = 0

    def runner(argv, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.4)
        with lock:
            active -= 1
        return SimpleNamespace(returncode=0, stdout=NDJSON, stderr="")

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(
                ai_gateway.run_headless, "并发检查", workspace=tmp_path, environ={},
                command=DshCommand(("dsh",), "installed"), runner=runner, timeout=10,
            )
            for _ in range(4)
        ]
        results = [future.result(timeout=20) for future in futures]

    assert [result.text for result in results] == ["先把今天最重的那件事拆成两步。"] * 4
    assert peak == ai_gateway.DEFAULT_CONCURRENCY == 2
