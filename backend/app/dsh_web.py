"""Managed DeepSeek Harness (dsh) web process.

The web UI is served by a local dsh process.  This module owns only its
lifecycle: resolving a command, starting it, parsing the readiness line, and
stopping the process tree we started.  HTTP/WebSocket forwarding lives in
``dsh_proxy``.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_READY = re.compile(r"dsh web:\s*(https?://\S+)")
_TOKEN = re.compile(r"(token=)[^&\s]+")
_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]+")
_MAX_LOG = 24
_REDACTED = "***"

# dsh 的工作流规则是项目的一部分（`torch-os/dsh/AGENTS.md`），不放在数据库里。
# 启动 dsh 时把 DSH_AGENTS_HOME 指向这里，dsh 会把它当成共享 agents 目录加载 AGENTS.md；
# 换库、换工作区都直接生效，不需要往用户目录或库里复制规则文件。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = PROJECT_ROOT / "dsh"


def workflow_environment(environ: dict[str, str] | None = None,
                         repo_root: Path | None = None) -> dict[str, str]:
    """给 dsh 子进程准备环境：项目内置规则 + 稳定的仓库路径。

    显式设置的值优先（例如用户自己指定 DSH_AGENTS_HOME）；只有规则文件存在时才注入，
    避免把 dsh 指到一个空目录。
    """
    env = dict(os.environ if environ is None else environ)
    root = Path(repo_root) if repo_root is not None else PROJECT_ROOT
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("TORCH_REPO_ROOT", str(root))
    workflow = root / "dsh"
    if (workflow / "AGENTS.md").is_file():
        env.setdefault("DSH_AGENTS_HOME", str(workflow))
    return env


class DshError(RuntimeError):
    """A dsh process could not be started or reached readiness."""


@dataclass(frozen=True)
class DshLaunch:
    port: int
    token: str
    url: str


@dataclass(frozen=True)
class DshCommand:
    argv: tuple[str, ...]
    kind: str  # installed | npx | custom


def _clean_line(line: str) -> str:
    return _ANSI.sub("", line).replace("\x00", "").strip()


def redact(text: str) -> str:
    text = _TOKEN.sub(r"\1***", str(text or ""))
    return _SECRET.sub("sk-***", text)


def parse_readiness_line(line: str) -> DshLaunch | None:
    """Parse one dsh stdout/stderr line into a loopback launch description."""
    text = _clean_line(line)
    match = _READY.search(text)
    if match is None:
        return None
    from urllib.parse import parse_qs, urlsplit

    try:
        parsed = urlsplit(match.group(1))
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").strip("[]").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return None
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    tokens = parse_qs(parsed.query, keep_blank_values=False).get("token") or []
    if len(tokens) != 1 or not tokens[0]:
        return None
    return DshLaunch(port=port, token=tokens[0], url=match.group(1))


def _is_windows_script(path: str) -> bool:
    return os.name == "nt" and path.lower().endswith((".cmd", ".bat"))


def spawn_argv(argv: Sequence[str]) -> list[str]:
    """Wrap Windows .cmd/.bat launchers in cmd.exe so CreateProcess works."""
    values = list(argv)
    if values and _is_windows_script(values[0]):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", *values]
    return values


def resolve_command(environ: dict[str, str] | None = None,
                    which=shutil.which) -> DshCommand | None:
    env = os.environ if environ is None else environ
    custom = (env.get("TORCH_DSH_COMMAND") or env.get("DSH_WEB_COMMAND") or "").strip()
    if custom:
        parts = shlex.split(custom, posix=os.name != "nt")
        if parts:
            return DshCommand(tuple(parts), "custom")
    installed = which("dsh")
    if installed:
        return DshCommand((installed,), "installed")
    npx = which("npx")
    if npx:
        return DshCommand((npx, "-y", "@deepseek-ai/dsh"), "npx")
    return None


class DisabledDshRuntime:
    """A no-I/O stand-in used by QA and explicitly disabled deployments."""

    enabled = False
    mode = "disabled"
    workspace: Path

    def __init__(self, workspace: Path | None = None):
        self.workspace = Path(workspace or ".")

    def status(self) -> dict[str, Any]:
        return {
            "state": "disabled", "available": False, "mode": "disabled",
            "workspace": str(self.workspace), "error": "", "log_path": "",
            "install_hint": "DeepSeek Harness is disabled in this runtime.",
            "autostart": False, "started_at": "", "generation": 0,
        }

    def request_start(self) -> None:
        return

    def request_restart(self) -> None:
        return

    async def start(self) -> None:
        return

    async def close(self) -> None:
        return

    def upstream(self) -> DshLaunch:
        raise DshError("DeepSeek Harness is disabled in this runtime.")


class DshWebRuntime:
    """Owns one dsh ``web`` child process for the current torch workspace."""

    enabled = True

    def __init__(self, data_dir: Path | str, workspace_dir: Path | str, *,
                 command: DshCommand | None = None,
                 startup_timeout: float = 180.0,
                 environ: dict[str, str] | None = None):
        self.data_dir = Path(data_dir)
        self.workspace = Path(workspace_dir)
        self.command = command
        self._injected_command = command
        self.startup_timeout = max(10.0, float(startup_timeout))
        self.environ = dict(os.environ if environ is None else environ)
        self.state = "stopped"
        self.error = ""
        self.started_at = ""
        self._launch: DshLaunch | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._start_task: asyncio.Task | None = None
        self._reader_tasks: list[asyncio.Task] = []
        self._watch_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._stopping = False
        self._generation = 0
        self._logs: list[str] = []
        self.log_path = self.data_dir / "diagnostics" / "dsh-web.log"
        self.install_hint = "npm install -g @deepseek-ai/dsh"

    # ---------- status ----------
    @property
    def mode(self) -> str:
        return self.command.kind if self.command else "unknown"

    @property
    def generation(self) -> int:
        return self._generation

    def upstream(self) -> DshLaunch:
        if self.state != "ready" or self._launch is None:
            raise DshError(self.error or "DeepSeek Harness 尚未就绪")
        return self._launch

    def status(self) -> dict[str, Any]:
        command = self.command
        node = self.environ.get("NODE_VERSION", "")
        return {
            "state": self.state,
            "available": bool(command) and self.state not in {"missing", "disabled"},
            "mode": self.mode,
            "command": command.argv[0] if command else "",
            "workspace": str(self.workspace),
            "started_at": self.started_at,
            "error": redact(self.error),
            "log_path": str(self.log_path),
            "install_hint": self.install_hint,
            "autostart": False,
            "generation": self._generation,
            "node_version": node,
            "node_required": ">=24.9",
        }

    # ---------- diagnostics ----------
    def _record(self, line: str) -> None:
        text = redact(_clean_line(line))
        if not text:
            return
        self._logs.append(text)
        self._logs = self._logs[-_MAX_LOG:]
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except OSError:
            pass

    def log_tail(self) -> list[str]:
        return list(self._logs)

    # ---------- start / stop ----------
    def request_start(self) -> None:
        if self.state in {"ready", "starting"}:
            return
        if self._start_task is not None and not self._start_task.done():
            return
        self.state = "starting"
        self._start_task = asyncio.create_task(self._start_impl())

    def request_restart(self) -> None:
        if self._start_task is not None and not self._start_task.done():
            return
        self.state = "starting"
        self._start_task = asyncio.create_task(self._restart_impl())

    async def start(self) -> None:
        """Start and wait for readiness (used by tests and explicit callers)."""
        if self.state == "ready":
            return
        if self._start_task is None or self._start_task.done():
            self._start_task = asyncio.create_task(self._start_impl())
        await asyncio.shield(self._start_task)

    async def _restart_impl(self) -> None:
        await self._stop_owned()
        if self._injected_command is None:
            # Let a newly installed dsh win over an earlier npx fallback.
            self.command = None
        await self._start_impl()

    def _arguments(self) -> list[str]:
        if self.command is None:
            self.command = resolve_command(self.environ)
        if self.command is None:
            self.state = "missing"
            self.error = "未找到 dsh 或 npx。请安装 Node.js >=24.9 后执行 npm install -g @deepseek-ai/dsh。"
            return []
        # Do not pass --public-url: dsh prints the advertised URL instead of the
        # listener URL, and we need the real upstream port to proxy to.
        args = [*self.command.argv, "web", "--no-open", "--host", "127.0.0.1", "--port", "0"]
        return spawn_argv(args)

    async def _start_impl(self) -> None:
        async with self._lock:
            if self.state == "ready":
                return
            self._stopping = False
            self.error = ""
            self._launch = None
            self._logs = []
            args = self._arguments()
            if not args:
                return
            self.state = "starting"
            try:
                self.workspace.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self.state = "error"
                self.error = f"无法创建 dsh 工作区：{exc}"
                self._record(self.error)
                return
            self._record(f"starting {' '.join(args[:3])} (cwd={self.workspace})")
            env = workflow_environment(self.environ)
            kwargs: dict[str, Any] = {
                "cwd": str(self.workspace),
                "env": env,
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                kwargs["start_new_session"] = True
            try:
                self._proc = await asyncio.create_subprocess_exec(*args, **kwargs)
            except (OSError, ValueError) as exc:
                self.state = "error"
                self.error = f"无法启动 dsh：{exc}"
                self._record(self.error)
                return
            self._reader_tasks = [
                asyncio.create_task(self._pump(self._proc.stdout, "stdout")),
                asyncio.create_task(self._pump(self._proc.stderr, "stderr")),
            ]
            self._watch_task = asyncio.create_task(self._watch(self._proc))
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if self._launch is not None:
                    self.state = "ready"
                    self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    self._generation += 1
                    self._record(f"ready on 127.0.0.1:{self._launch.port}")
                    return
                if self._proc.returncode is not None:
                    break
                await asyncio.sleep(0.1)
            if self._launch is None:
                self.error = ("dsh 启动超时；首次使用 npx 可能需要下载依赖。"
                              if self._proc.returncode is None else
                              f"dsh 启动失败（退出码 {self._proc.returncode}）")
                self._record(self.error)
                await self._stop_owned(force=True)
                self.state = "error"

    async def _pump(self, stream: asyncio.StreamReader | None, source: str) -> None:
        if stream is None:
            return
        while True:
            try:
                raw = await stream.readline()
            except (asyncio.CancelledError, OSError):
                return
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace")
            self._record(f"[{source}] {line}")
            launch = parse_readiness_line(line)
            if launch is not None and self._launch is None:
                self._launch = launch

    async def _watch(self, proc: asyncio.subprocess.Process) -> None:
        code = await proc.wait()
        if self._stopping:
            return
        self._record(f"dsh exited with code {code}")
        if self.state in {"starting", "ready"}:
            self.state = "error"
            self.error = f"dsh 已退出（退出码 {code}）"
            self._launch = None

    async def _stop_owned(self, *, force: bool = False) -> None:
        proc = self._proc
        self._stopping = True
        self._proc = None
        self._launch = None
        if self._start_task is not None and self._start_task is not asyncio.current_task():
            # The start task is the caller during restart; don't self-cancel.
            pass
        for task in self._reader_tasks:
            task.cancel()
        self._reader_tasks = []
        if self._watch_task is not None:
            self._watch_task.cancel()
            self._watch_task = None
        if proc is not None and proc.returncode is None:
            try:
                if os.name == "nt":
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(proc.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(killer.wait(), timeout=5)
                else:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, 15)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=3 if force else 5)
            except (OSError, ProcessLookupError):
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
        if self.state != "disabled":
            self.state = "stopped" if not force else self.state

    async def close(self) -> None:
        task = self._start_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._stop_owned(force=True)
        self.state = "stopped"
        self._start_task = None
