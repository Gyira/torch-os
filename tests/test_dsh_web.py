"""Managed DeepSeek Harness process: readiness parsing, command choice, lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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
        assert runtime.status()["adopted"] is False
        assert runtime._state_path().exists()
        await runtime.close()
        assert runtime.status()["state"] == "stopped"
        assert not runtime._state_path().exists()

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


class _AuthHandler(BaseHTTPRequestHandler):
    """Stands in for dsh's launch-token exchange (303 + Set-Cookie)."""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        query = parse_qs(urlsplit(self.path).query)
        if query.get("token") == ["test-token"]:
            self.send_response(303)
            self.send_header("Location", "./")
            self.send_header("Set-Cookie", "dsh_session=stub; Path=/; HttpOnly")
            self.end_headers()
            return
        self.send_response(401)
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


@contextlib.contextmanager
def _auth_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _write_handshake(runtime: dsh_web.DshWebRuntime, *, port: int,
                     token: str = "test-token", listen_pid: int | None = None) -> Path:
    path = runtime._state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": dsh_web._HANDSHAKE_SCHEMA,
        "workspace": str(runtime.workspace.resolve()),
        "port": port,
        "token": token,
        "pid": None,
        "listen_pid": listen_pid,
        "started_at": "2026-10-27T00:00:00+00:00",
        "command": [],
    }), encoding="utf-8")
    return path


def test_runtime_adopts_a_running_dsh(tmp_path) -> None:
    marker = tmp_path / "spawned"
    script = tmp_path / "fake_dsh.py"
    script.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('x', encoding='utf-8')\n"
        "print('dsh web: http://127.0.0.1:54321/?token=test-token', flush=True)\n"
        "import time; time.sleep(30)\n",
        encoding="utf-8",
    )
    command = dsh_web.DshCommand((sys.executable, str(script)), "custom")

    with _auth_server() as port:
        async def scenario():
            runtime = dsh_web.DshWebRuntime(tmp_path / "data", tmp_path / "vault",
                                            command=command, startup_timeout=5)
            _write_handshake(runtime, port=port)
            await runtime.start()
            status = runtime.status()
            assert status["state"] == "ready", status
            assert status["adopted"] is True
            assert runtime.upstream().port == port
            assert not marker.exists(), "adoption must not spawn a second dsh"
            await runtime.close()
            assert runtime.status()["state"] == "stopped"

        asyncio.run(scenario())
    assert not marker.exists()


def test_probe_rejects_a_wrong_launch_token(tmp_path) -> None:
    with _auth_server() as port:
        async def scenario():
            runtime = dsh_web.DshWebRuntime(tmp_path / "data", tmp_path / "vault",
                                            command=None)
            good = dsh_web.DshLaunch(port, "test-token", "")
            bad = dsh_web.DshLaunch(port, "other-token", "")
            assert await runtime._probe(good) is True
            assert await runtime._probe(bad) is False

        asyncio.run(scenario())


def test_stale_handshake_is_replaced_by_a_fresh_start(tmp_path) -> None:
    script = _fake_dsh(tmp_path)
    command = dsh_web.DshCommand((sys.executable, str(script)), "custom")

    async def scenario():
        runtime = dsh_web.DshWebRuntime(tmp_path / "data", tmp_path / "vault",
                                        command=command, startup_timeout=10)
        _write_handshake(runtime, port=9, token="dead-token")
        await runtime.start()
        assert runtime.status()["state"] == "ready"
        assert runtime.status()["adopted"] is False
        assert runtime.upstream().port == 54321
        assert json.loads(runtime._state_path().read_text(encoding="utf-8"))["port"] == 54321
        await runtime.close()
        assert not runtime._state_path().exists()

    asyncio.run(scenario())


def test_close_leaves_the_dsh_for_another_torch_entry(tmp_path) -> None:
    async def scenario():
        runtime = dsh_web.DshWebRuntime(tmp_path / "data", tmp_path / "vault",
                                        command=None)
        with _auth_server() as port:
            _write_handshake(runtime, port=port)
            await runtime.start()
            assert runtime.status()["adopted"] is True
            other = runtime._owners_dir() / f"{os.getpid()}-other.owner"
            other.parent.mkdir(parents=True, exist_ok=True)
            other.write_text("{}", encoding="utf-8")
            try:
                await runtime.close()
                assert runtime.status()["state"] == "stopped"
                assert runtime._state_path().exists(), "the other entry still needs it"
                assert other.exists()
            finally:
                other.unlink(missing_ok=True)
                runtime._state_path().unlink(missing_ok=True)

    asyncio.run(scenario())


def test_owner_files_are_pruned_by_pid(tmp_path) -> None:
    runtime = dsh_web.DshWebRuntime(tmp_path / "data", tmp_path / "vault", command=None)
    runtime._register_owner()
    dead = runtime._owners_dir() / "2000000000-dead.owner"
    dead.write_text("{}", encoding="utf-8")
    assert runtime._live_owner_count() == 1
    assert not dead.exists()
    runtime._release_owner()
    assert runtime._live_owner_count() == 0


def test_restart_is_refused_while_another_entry_owns_the_dsh(tmp_path) -> None:
    runtime = dsh_web.DshWebRuntime(tmp_path / "data", tmp_path / "vault", command=None)
    runtime._register_owner()
    other = runtime._owners_dir() / f"{os.getpid()}-other.owner"
    other.write_text("{}", encoding="utf-8")
    try:
        with pytest.raises(dsh_web.DshError):
            runtime.request_restart()
        assert runtime.state == "stopped"
    finally:
        other.unlink(missing_ok=True)
        runtime._release_owner()
