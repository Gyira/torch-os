"""Unified DeepSeek Harness headless gateway for v1.0 AI roles.

Why this module exists
----------------------
Every AI role in Torch OS is a configuration of the *same* dsh core.  The role
layer must not open a second model channel, hold its own DeepSeek key, or copy
the prompt/permission governance already living in dsh.  This gateway is the one
place that starts a headless dsh process and parses its events.

Design choices that matter on Windows
-------------------------------------
- The prompt is written to **stdin** and the task argument is ``-``.  The dsh
  CLI documents this form; it avoids multi-line argv being re-parsed by cmd.exe
  when dsh is installed as a ``.cmd`` shim.
- A timeout kills the whole process tree (``taskkill /T /F``), so a hung node
  child cannot survive a cancelled role call.
- The child runs in an empty workspace with a small environment whitelist.  It
  never receives ``VAULT_DIR``/``DATA_DIR``/``TORCH_REPO_ROOT``; all role context
  is assembled by the backend and passed as text.

The gateway never reads the Vault and never logs prompt/reply text or secrets.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .dsh_web import DshCommand, resolve_command, spawn_argv

MAX_PROMPT_CHARS = 200_000
DEFAULT_TIMEOUT = 120.0
MAX_TIMEOUT = 900.0
MAX_OUTPUT_BYTES = 2_000_000
DEFAULT_CONCURRENCY = 2

# 角色子进程最多并发两个：dsh 每个进程都会拉起 node，过多会互相抢内存。
_SEMAPHORE = threading.BoundedSemaphore(DEFAULT_CONCURRENCY)

_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]+")
_TOKEN = re.compile(r"(token=)[^&\s]+")

# 只允许子进程拿到运行 dsh 必需的环境变量；任何指向用户库/仓库的数据路径都不传。
_ENV_ALLOWED = {
    "PATH", "PATHEXT", "SYSTEMROOT", "COMSPEC", "WINDIR",
    "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME",
    "LANG", "LC_ALL", "LC_CTYPE", "PYTHONUTF8", "PYTHONIOENCODING",
}

# 这些变量即使调用方传了也绝不进入角色子进程。
_ENV_BLOCKED = {
    "TORCH_REPO_ROOT", "VAULT_DIR", "DATA_DIR", "DSH_AGENTS_HOME",
    "LOVE_DATA_DIR", "LOVE_DB_PATH", "LOVE_APP_DIR", "LOVE_EXCEL_DIR",
    "LOVE_TIMETABLE_PATH", "LOVE_STOPWORDS_PATH", "LOVE_TARGET_NAME",
}


class AIError(RuntimeError):
    """Base class for role-run failures that are safe to show to the user."""


class AIUnavailable(AIError):
    """dsh is missing, disabled, or the round returned nothing usable."""


class AITimeout(AIError):
    """The headless round exceeded its deadline and the process tree was killed."""


class AIProtocolError(AIError):
    """dsh exited but the NDJSON stream did not contain an answer."""


@dataclass(frozen=True)
class HeadlessResult:
    text: str
    session_id: str
    seconds: float
    returncode: int
    stderr_tail: str


@dataclass(frozen=True)
class CompletedRun:
    """Small adapter so tests can inject a runner without spawning a process."""

    returncode: int
    stdout: str
    stderr: str


def redact(text: str) -> str:
    """Remove API keys and dsh launch tokens from anything we may log/show."""
    value = _SECRET.sub("sk-***", str(text or ""))
    return _TOKEN.sub(r"\1***", value)


def parse_events(stdout: str) -> tuple[str, str, list[str]]:
    """Parse dsh ``--json`` NDJSON into ``(session_id, final_text, errors)``.

    Lines that are not JSON are ignored: dsh may print warnings to stdout in
    future versions, and a malformed line must not take down the whole round.
    """
    session_id = ""
    final_text = ""
    errors: list[str] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = str(event.get("type") or "")
        if kind == "session" and not session_id:
            session_id = str(event.get("sessionId") or "")
        elif kind == "final":
            final_text = str(event.get("text") or "")
        elif kind in {"error", "fatal"}:
            message = event.get("message") or event.get("error") or "dsh error"
            errors.append(str(message)[:300])
    return session_id, final_text, errors


def headless_argv(command: DshCommand, *, session_id: str = "",
                  style: str = "profile") -> list[str]:
    """Build the dsh argv for one headless round.

    ``style='profile'`` uses the documented ``--profile headless`` form.  The
    legacy ``headless`` subcommand is kept as a fallback for older dsh builds
    that may be present on a user's machine.
    """
    argv = [*command.argv]
    if style == "legacy":
        argv.append("headless")
    else:
        argv += ["--profile", "headless"]
    argv += ["--json"]
    if session_id:
        argv += ["--session-id", session_id]
    # `-` reads the task from stdin; see `dsh headless --help`.
    argv.append("-")
    return spawn_argv(argv)


def headless_environment(base: Mapping[str, str] | None = None, *,
                         key: str = "", agents_home: str | Path | None = None) -> dict[str, str]:
    """Build a minimal environment for a role subprocess.

    Only variables needed to launch dsh/node are copied.  The Vault path and
    repository root are deliberately absent, so even if the model could ask dsh
    to read files, it has no pointer to the user's data.
    """
    source = dict(os.environ if base is None else base)
    env: dict[str, str] = {}
    for name, value in source.items():
        upper = name.upper()
        if upper in _ENV_BLOCKED:
            continue
        if upper in _ENV_ALLOWED and value:
            env[name] = value
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if key:
        env["DEEPSEEK_API_KEY"] = key
    if agents_home:
        env["DSH_AGENTS_HOME"] = str(agents_home)
    return env


def _kill_tree(process: subprocess.Popen[str]) -> None:
    """Kill the dsh process and every child it spawned."""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.kill()
    except OSError:
        pass


def _run_process(argv: list[str], prompt: str, workspace: Path,
                 environ: Mapping[str, str], timeout: float) -> CompletedRun:
    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        start_new_session = True
    process = subprocess.Popen(
        argv,
        cwd=str(workspace),
        env=dict(environ),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    try:
        stdout, stderr = process.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_tree(process)
        with contextlib.suppress(Exception):
            process.communicate(timeout=5)
        raise AITimeout(f"dsh 这一轮超过 {int(timeout)} 秒，已终止") from exc
    return CompletedRun(returncode=process.returncode or 0,
                        stdout=stdout or "", stderr=stderr or "")


def run_headless(prompt: str, *, workspace: Path | str, environ: Mapping[str, str],
                 session_id: str = "", timeout: float = DEFAULT_TIMEOUT,
                 runner: Callable[..., Any] | None = None,
                 command: DshCommand | None = None,
                 style: str = "profile") -> HeadlessResult:
    """Run one role round through dsh headless and return its final text.

    ``runner`` is the test seam: it receives the same keyword arguments as
    ``subprocess.run`` and must return an object with ``returncode``/``stdout``/
    ``stderr``.  Production callers leave it as ``None``.
    """
    text = str(prompt or "").strip()
    if not text:
        raise AIError("这一轮没有可发送的内容")
    if len(text) > MAX_PROMPT_CHARS:
        raise AIError("这一轮的上下文太长了，请缩小范围后再试")
    if not 0 < timeout <= MAX_TIMEOUT:
        raise AIError("超时时间不合法")
    resolved = command or resolve_command(dict(environ))
    if resolved is None:
        raise AIUnavailable("没有找到 dsh 命令，请先安装 DeepSeek Harness")
    argv = headless_argv(resolved, session_id=session_id, style=style)
    target = Path(workspace)
    target.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        with _SEMAPHORE:
            if runner is not None:
                completed = runner(argv, input=text, cwd=str(target), env=dict(environ),
                                   timeout=timeout, text=True, encoding="utf-8",
                                   errors="replace")
            else:
                completed = _run_process(argv, text, target, environ, timeout)
    except subprocess.TimeoutExpired as exc:
        raise AITimeout(f"dsh 这一轮超过 {int(timeout)} 秒，已终止") from exc
    elapsed = round(time.monotonic() - started, 1)

    stdout = str(getattr(completed, "stdout", "") or "")
    stderr = str(getattr(completed, "stderr", "") or "")
    if len(stdout.encode("utf-8", "replace")) > MAX_OUTPUT_BYTES:
        raise AIProtocolError("dsh 返回的内容太长，已拒绝")
    returncode = int(getattr(completed, "returncode", 1) or 0)
    if returncode != 0 and not stdout.strip():
        tail = redact(" / ".join(stderr.strip().splitlines()[-3:]))[:300]
        raise AIUnavailable("dsh 这一轮没有返回内容" + (f"：{tail}" if tail else ""))

    new_session, final_text, errors = parse_events(stdout)
    if not final_text.strip() and stdout.strip() and not stdout.lstrip().startswith("{"):
        # Older dsh builds may print the final answer as plain text.
        final_text = stdout.strip()
    if not final_text.strip():
        detail = errors[0] if errors else ""
        raise AIProtocolError("dsh 这一轮回复是空的" + (f"：{detail}" if detail else ""))
    return HeadlessResult(
        text=final_text.strip(),
        session_id=new_session or session_id,
        seconds=elapsed,
        returncode=returncode,
        stderr_tail=redact(" / ".join(stderr.strip().splitlines()[-3:]))[:300],
    )


__all__ = [
    "AIError", "AIUnavailable", "AITimeout", "AIProtocolError",
    "HeadlessResult", "CompletedRun", "parse_events", "headless_argv",
    "headless_environment", "run_headless", "redact",
]
