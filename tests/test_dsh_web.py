"""Managed DeepSeek Harness process: readiness parsing, command choice, lifecycle."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from backend.app import dsh_web


def _fake_dsh(tmp_path: Path, token: str = "test-token") -> Path:
    script = tmp_path / "fake_dsh.py"
    script.write_text(
        "import sys, time\n"
        "print('noise before readiness', flush=True)\n"
        f"print('dsh web: http://127.0.0.1:54321/?token={token}', flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    return script


def test_parse_readiness_line_accepts_loopback_and_redacts() -> None:
    launch = dsh_web.parse_readiness_line(
        "\x1b[32mdsh web: http://127.0.0.1:3080/?token=abc_DEF-123\x1b[0m")
    assert launch is not None
    assert launch.port == 3080 and launch.token == "abc_DEF-123"
    assert dsh_web.parse_readiness_line("dsh web: http://example.com:3080/?token=x") is None
    assert dsh_web.parse_readiness_line("dsh web: http://127.0.0.1:3080/") is None
    assert dsh_web.parse_readiness_line("nothing") is None
    assert "token=***" in dsh_web.redact("dsh web: http://127.0.0.1:1/?token=secret")
    assert "sk-***" in dsh_web.redact("key sk-abcdef")


def test_resolve_command_prefers_custom_then_installed_then_npx(monkeypatch) -> None:
    assert dsh_web.resolve_command({"TORCH_DSH_COMMAND": "custom --flag"}, which=lambda name: None) == \
        dsh_web.DshCommand(("custom", "--flag"), "custom")
    installed = dsh_web.resolve_command({}, which=lambda name: "C:/dsh.cmd" if name == "dsh" else None)
    assert installed == dsh_web.DshCommand(("C:/dsh.cmd",), "installed")
    npx = dsh_web.resolve_command({}, which=lambda name: "C:/npx.cmd" if name == "npx" else None)
    assert npx == dsh_web.DshCommand(("C:/npx.cmd", "-y", "@deepseek-ai/dsh"), "npx")
    assert dsh_web.resolve_command({}, which=lambda name: None) is None


def test_spawn_argv_wraps_windows_scripts(monkeypatch) -> None:
    monkeypatch.setattr(dsh_web.os, "name", "nt")
    argv = dsh_web.spawn_argv([r"C:\nodejs\npx.CMD", "-y", "pkg"])
    assert argv[:3] == [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c"]
    assert argv[3] == r"C:\nodejs\npx.CMD"


def test_runtime_starts_reads_readiness_and_stops(tmp_path) -> None:
    script = _fake_dsh(tmp_path)
    command = dsh_web.DshCommand((sys.executable, str(script)), "custom")

    async def scenario():
        runtime = dsh_web.DshWebRuntime(tmp_path / "data", tmp_path / "vault",
                                        command=command, startup_timeout=10)
        await runtime.start()
        status = runtime.status()
        assert status["state"] == "ready", status
        assert status["mode"] == "custom"
        launch = runtime.upstream()
        assert launch.port == 54321 and launch.token == "test-token"
        assert any("token=***" in line for line in runtime.log_tail())
        await runtime.close()
        assert runtime.status()["state"] == "stopped"

    asyncio.run(scenario())


def test_runtime_reports_missing_command(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dsh_web, "resolve_command", lambda environ=None, which=None: None)

    async def scenario():
        runtime = dsh_web.DshWebRuntime(tmp_path / "data", tmp_path / "vault",
                                        command=None, startup_timeout=2, environ={})
        await runtime.start()
        assert runtime.status()["state"] == "missing"
        await runtime.close()

    asyncio.run(scenario())


def test_disabled_runtime_never_spawns() -> None:
    runtime = dsh_web.DisabledDshRuntime(Path("."))
    runtime.request_start()
    assert runtime.status()["state"] == "disabled"
    with pytest.raises(dsh_web.DshError):
        runtime.upstream()
