"""Managed DeepSeek Harness (dsh) web process.

The web UI is served by a local dsh process.  This module owns its lifecycle:
resolving a command, adopting a healthy dsh that already serves this workspace,
starting one when none is running, parsing the readiness line, and stopping the
process tree when the last torch entry leaves.  HTTP/WebSocket forwarding lives
in ``dsh_proxy``.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

import httpx

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_READY = re.compile(r"dsh web:\s*(https?://\S+)")
_TOKEN = re.compile(r"(token=)[^&\s]+")
_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]+")
_MAX_LOG = 24
_REDACTED = "***"
# 交接文件只记录"怎么连上已经运行的那个 dsh"，不参与 dsh 自己的会话存储。
_HANDSHAKE_SCHEMA = 1
_PROBE_TIMEOUT = 6.0
_PROBE_RETRY_DELAY = 0.5
_HEALTH_INTERVAL = 10.0
_HEALTH_TIMEOUT = 4.0
_HEALTH_STRIKES = 3

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


@dataclass(frozen=True)
class DshHandshake:
    """The launch credential of a dsh that may still be serving this workspace.

    The token is a per-process secret; it is the only way a *different* torch
    backend can authenticate to an already running dsh, so it is persisted for
    the lifetime of that process.  The file lives under ``data_dir`` beside the
    dsh log (local, user-owned); it is never returned by the status API.
    """

    port: int
    token: str
    pid: int | None
    listen_pid: int | None
    started_at: str
    command: tuple[str, ...]

    def launch(self) -> DshLaunch:
        return DshLaunch(port=self.port, token=self.token,
                         url=f"http://127.0.0.1:{self.port}/?token={quote(self.token, safe='')}")

    def to_record(self, workspace: str) -> dict[str, Any]:
        return {
            "schema": _HANDSHAKE_SCHEMA,
            "workspace": workspace,
            "port": self.port,
            "token": self.token,
            "pid": self.pid,
            "listen_pid": self.listen_pid,
            "started_at": self.started_at,
            "command": list(self.command),
        }

    @classmethod
    def from_record(cls, data: Any, workspace: str) -> "DshHandshake | None":
        if not isinstance(data, dict) or data.get("schema") != _HANDSHAKE_SCHEMA:
            return None
        stored = str(data.get("workspace") or "")
        if os.name == "nt":
            matches = stored.lower() == workspace.lower()
        else:
            matches = stored == workspace
        if not matches:
            return None
        try:
            port = int(data.get("port"))
        except (TypeError, ValueError):
            return None
        token = str(data.get("token") or "")
        if not 0 < port < 65536 or not token:
            return None
        pid = data.get("pid")
        listen_pid = data.get("listen_pid")
        return cls(
            port=port,
            token=token,
            pid=pid if isinstance(pid, int) and pid > 0 else None,
            listen_pid=listen_pid if isinstance(listen_pid, int) and listen_pid > 0 else None,
            started_at=str(data.get("started_at") or ""),
            command=tuple(str(part) for part in data.get("command") or ()),
        )


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


def _workspace_digest(workspace: Path) -> str:
    """Stable, case-folded name for one workspace's handshake/owner files."""
    value = Path(workspace).resolve().as_posix()
    if os.name == "nt":
        value = value.lower()
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


_KERNEL32: Any = None


def _kernel32() -> Any:
    global _KERNEL32
    if _KERNEL32 is None:
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = (wintypes.HANDLE,)
        api.CloseHandle.restype = wintypes.BOOL
        _KERNEL32 = api
    return _KERNEL32


def _pid_alive(pid: int | None) -> bool:
    """Whether ``pid`` is still running, without killing it on Windows."""
    if not isinstance(pid, int) or pid <= 0 or pid > 0x7FFFFFFF:
        return False
    if os.name == "nt":
        import ctypes

        api = _kernel32()
        handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            # Access denied still proves the process exists; any other error
            # means the pid is gone.
            return ctypes.get_last_error() == 5
        try:
            return api.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _listener_pid(port: int) -> int | None:
    """Owner pid of a loopback listening port, or None when it cannot be read."""
    if not isinstance(port, int) or not 0 < port < 65536:
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    if os.name == "nt":
        try:
            done = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"], capture_output=True,
                timeout=5, creationflags=flags, check=False,
            )
        except OSError:
            return None
        # netstat writes in the OEM code page; only the ASCII columns matter.
        output = (done.stdout or b"").decode("utf-8", errors="replace")
        needle = f":{port}"
        for line in output.splitlines():
            parts = line.split()
            if (len(parts) >= 5 and parts[0] == "TCP"
                    and parts[1].endswith(needle) and parts[3] == "LISTENING"):
                try:
                    return int(parts[4])
                except ValueError:
                    return None
        return None
    for argv in (["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], ["fuser", f"{port}/tcp"]):
        try:
            done = subprocess.run(argv, capture_output=True, timeout=5, check=False)
        except OSError:
            continue
        output = (done.stdout or b"").decode("utf-8", errors="replace").strip()
        if done.returncode == 0 and output:
            try:
                return int(output.split()[0])
            except ValueError:
                continue
    return None


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
    """One dsh per workspace: adopt a leftover process, otherwise spawn one.

    A dsh holds a session's write lease for as long as it runs, so a second
    process would answer ``session/writer-held`` for every conversation the
    first one has open.  The handshake file and owner files therefore keep
    every torch entry on the same workspace pointed at the same dsh.
    """

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
        self._health_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._stopping = False
        self._generation = 0
        self._logs: list[str] = []
        self.log_path = self.data_dir / "diagnostics" / "dsh-web.log"
        self.install_hint = "npm install -g @deepseek-ai/dsh"
        # A dsh that survived an earlier backend keeps its per-process launch
        # token in a handshake file; that token is the only way to attach to it.
        self._adopted = False
        self._adopted_pid: int | None = None
        self._owner_file: Path | None = None

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
            "adopted": self._adopted,
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

    # ---------- handshake ----------
    def _workspace_key(self) -> str:
        return str(self.workspace.resolve())

    def _state_path(self) -> Path:
        return (self.data_dir / "diagnostics" / "dsh-web"
                / f"{_workspace_digest(self.workspace)}.json")

    def _owners_dir(self) -> Path:
        return (self.data_dir / "diagnostics" / "dsh-web"
                / f"{_workspace_digest(self.workspace)}.owners")

    def _read_handshake(self) -> DshHandshake | None:
        try:
            data = json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return DshHandshake.from_record(data, self._workspace_key())

    def _write_handshake(self, launch: DshLaunch, *, pid: int | None,
                         listen_pid: int | None) -> None:
        handshake = DshHandshake(
            port=launch.port,
            token=launch.token,
            pid=pid,
            listen_pid=listen_pid,
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            command=tuple(self.command.argv) if self.command else (),
        )
        path = self._state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
            temporary.write_text(
                json.dumps(handshake.to_record(self._workspace_key()), ensure_ascii=False),
                encoding="utf-8",
            )
            with contextlib.suppress(OSError):
                os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except OSError as exc:
            self._record(f"could not save the dsh handshake: {exc}")

    def _clear_handshake(self) -> None:
        with contextlib.suppress(OSError):
            self._state_path().unlink(missing_ok=True)

    # ---------- owners ----------
    # One small file per torch backend that may use this workspace's dsh.
    # Files are independent (no read-modify-write race); a crashed backend's
    # file is pruned by pid liveness, so the last backend can still stop dsh.
    def _register_owner(self) -> None:
        if self._owner_file is not None:
            return
        directory = self._owners_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        self._prune_owners()
        path = directory / f"{os.getpid()}-{uuid.uuid4().hex[:8]}.owner"
        try:
            path.write_text(
                json.dumps({"pid": os.getpid(), "since":
                            datetime.now(timezone.utc).isoformat(timespec="seconds")}),
                encoding="utf-8",
            )
        except OSError:
            return
        self._owner_file = path

    def _owner_pid(self, path: Path) -> int | None:
        try:
            return int(path.name.split("-", 1)[0])
        except (ValueError, IndexError):
            return None

    def _prune_owners(self) -> None:
        try:
            entries = list(self._owners_dir().glob("*.owner"))
        except OSError:
            return
        for path in entries:
            if path == self._owner_file:
                continue
            pid = self._owner_pid(path)
            if pid is None or not _pid_alive(pid):
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)

    def _live_owner_count(self) -> int:
        self._prune_owners()
        try:
            return sum(1 for _ in self._owners_dir().glob("*.owner"))
        except OSError:
            return 0

    def _other_owner_count(self) -> int:
        self._prune_owners()
        try:
            return sum(1 for path in self._owners_dir().glob("*.owner")
                       if path != self._owner_file)
        except OSError:
            return 0

    def _release_owner(self) -> None:
        if self._owner_file is None:
            return
        with contextlib.suppress(OSError):
            self._owner_file.unlink(missing_ok=True)
        self._owner_file = None

    # ---------- adoption ----------
    async def _probe(self, launch: DshLaunch, *, timeout: float = _PROBE_TIMEOUT) -> bool:
        """Ask a running dsh to exchange our launch token for its session cookie.

        This is exactly the exchange the proxy performs on the first request;
        a foreign process that happens to own a reused port cannot answer it.
        """
        url = f"http://127.0.0.1:{launch.port}/?token={quote(launch.token, safe='')}"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout), trust_env=False,
                follow_redirects=False,
            ) as client:
                response = await client.get(url)
        except (httpx.HTTPError, OSError):
            return False
        return response.status_code == 303 and bool(response.headers.get_list("set-cookie"))

    async def _try_adopt(self) -> bool:
        handshake = self._read_handshake()
        if handshake is None:
            return False
        launch = handshake.launch()
        for attempt in range(2):
            if await self._probe(launch):
                break
            if attempt == 0:
                await asyncio.sleep(_PROBE_RETRY_DELAY)
        else:
            await self._retire_handshake(handshake)
            return False
        self._launch = launch
        self._adopted = True
        self._adopted_pid = handshake.listen_pid or handshake.pid
        self.state = "ready"
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._generation += 1
        self._record(f"adopted the running dsh on 127.0.0.1:{launch.port} "
                     f"(pid={self._adopted_pid})")
        self._health_task = asyncio.create_task(self._watch_health(launch))
        return True

    async def _retire_handshake(self, handshake: DshHandshake) -> None:
        """Forget a handshake whose dsh does not answer; stop it only when the
        port is still owned by the pid recorded at spawn time."""
        listener = await asyncio.to_thread(_listener_pid, handshake.port)
        if handshake.listen_pid and listener == handshake.listen_pid:
            self._record(f"stopping the unresponsive dsh on 127.0.0.1:{handshake.port}")
            await self._terminate(handshake.listen_pid)
        self._clear_handshake()

    async def _watch_health(self, launch: DshLaunch) -> None:
        strikes = 0
        while True:
            await asyncio.sleep(_HEALTH_INTERVAL)
            if self.state != "ready" or self._launch is not launch:
                return
            if await self._probe(launch, timeout=_HEALTH_TIMEOUT):
                strikes = 0
                continue
            strikes += 1
            if strikes < _HEALTH_STRIKES:
                continue
            self._record("the adopted dsh stopped responding")
            self.state = "error"
            self.error = "运行中的 dsh 已停止响应；点「重启 dsh」会停止它并重新启动。"
            self._launch = None
            self._adopted = False
            # Keep the handshake: it is what lets the retry (or the next torch
            # entry) find and stop this wedged process before spawning a new
            # one, instead of leaving its session leases behind.
            return

    async def _terminate(self, pid: int | None) -> None:
        if not pid or pid <= 0:
            return
        if os.name == "nt":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError:
                return
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(killer.wait(), timeout=5)
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.sleep(0.4)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)

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
        # A shared dsh belongs to every torch entry that adopted it; replacing
        # it from here would silently cut off the other one.
        if self._other_owner_count() > 0:
            raise DshError("另一个 torch 入口正在使用这个 dsh；先关掉那个入口，再重启。")
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
        await self._shutdown(kill=True, force=True)
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
            self._adopted = False
            self._adopted_pid = None
            self._logs = []
            self._register_owner()
            # A healthy dsh for this workspace may have outlived the backend
            # that started it.  Attach to it instead of spawning a duplicate:
            # only one process may hold a session's write lease, so a second
            # dsh would answer session/writer-held for every open conversation.
            if await self._try_adopt():
                return
            args = self._arguments()
            if not args:
                self._release_owner()
                return
            self.state = "starting"
            try:
                self.workspace.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self.state = "error"
                self.error = f"无法创建 dsh 工作区：{exc}"
                self._record(self.error)
                self._release_owner()
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
                self._release_owner()
                return
            self._reader_tasks = [
                asyncio.create_task(self._pump(self._proc.stdout, "stdout")),
                asyncio.create_task(self._pump(self._proc.stderr, "stderr")),
            ]
            self._watch_task = asyncio.create_task(self._watch(self._proc))
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if self._launch is not None:
                    launch = self._launch
                    listen_pid = await asyncio.to_thread(_listener_pid, launch.port)
                    self._write_handshake(launch, pid=self._proc.pid, listen_pid=listen_pid)
                    self.state = "ready"
                    self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    self._generation += 1
                    self._record(f"ready on 127.0.0.1:{launch.port} (pid={listen_pid})")
                    return
                if self._proc.returncode is not None:
                    break
                await asyncio.sleep(0.1)
            if self._launch is None:
                self.error = ("dsh 启动超时；首次使用 npx 可能需要下载依赖。"
                              if self._proc.returncode is None else
                              f"dsh 启动失败（退出码 {self._proc.returncode}）")
                self._record(self.error)
                await self._shutdown(kill=True, force=True)
                self._release_owner()
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
        self._adopted = False
        self._clear_handshake()
        self._release_owner()

    async def _shutdown(self, *, kill: bool, force: bool = False) -> None:
        proc = self._proc
        adopted_pid = self._adopted_pid
        had_server = proc is not None or adopted_pid is not None
        self._stopping = True
        self._launch = None
        self._adopted = False
        self._adopted_pid = None
        if self._watch_task is not None:
            self._watch_task.cancel()
            self._watch_task = None
        if self._health_task is not None:
            self._health_task.cancel()
            self._health_task = None
        if not kill:
            # Another torch entry still proxies to this dsh.  Leave the child
            # and its pipes alone: closing our read ends could turn its later
            # log writes into EPIPE.  The other entry stops it when it leaves.
            if self.state != "disabled":
                self.state = "stopped"
            return
        self._proc = None
        for task in self._reader_tasks:
            task.cancel()
        self._reader_tasks = []
        if had_server:
            pid = adopted_pid
            if pid is None and proc is not None and proc.returncode is None:
                pid = proc.pid
            if pid is not None:
                await self._terminate(pid)
                if proc is not None and proc.returncode is None:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(proc.wait(), timeout=3 if force else 5)
                self._clear_handshake()
            # Without a pid we cannot prove which process to stop; keep the
            # handshake so a later backend can still adopt that server.
        if self.state != "disabled" and not force:
            self.state = "stopped"

    async def close(self) -> None:
        task = self._start_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._release_owner()
        # Another torch entry (plugin backend, browser mode) may still be
        # proxying to this very dsh; only the last one to leave stops it.
        await self._shutdown(kill=self._live_owner_count() == 0, force=True)
        self.state = "stopped"
        self._start_task = None
