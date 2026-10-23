"""Harness (ACP) as a third chat brain: persistent sessions over JSON-RPC stdio.

The brain spawns ``dsh --profile acp`` (the harness automation server), mounts
our read-only ``torch`` MCP bridge into each session, streams the answer back
chunk by chunk, and stores the ACP session id per local conversation so the
harness session can be resumed later.

Privacy boundary (user-approved 2026-10-05): in harness mode the harness
decides what reaches DeepSeek; it uses the credentials configured inside DSH.
Only read-only tools are expected to be mounted; permission requests are
auto-allowed for ``torch`` tool calls and rejected for everything else.
"""

from __future__ import annotations

import json
import os
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .db import Database, utc_now


PROTOCOL_VERSION = 1
MAX_PROMPT_SECONDS = 240.0
STARTUP_SECONDS = 30.0
LIVE_HEALTH_SECONDS = 0.6
LIVE_ATTACH_SECONDS = 3.0
_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS harness_sessions (
    conversation_id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    cwd TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


class HarnessError(RuntimeError):
    """The harness could not start, connect or finish the turn."""


def _dsh_home() -> Path:
    return Path(os.environ.get('DSH_HOME') or (Path.home() / '.dsh'))


def resolve_profile() -> str:
    """Use the torch-live profile when it exists; otherwise keep the shipped acp profile."""
    requested = (os.environ.get('TORCH_DSH_PROFILE') or 'torch-live').strip()
    profiles = _dsh_home() / 'profiles'
    if requested and (profiles / requested).is_dir():
        return requested
    return 'acp'


def resolve_command() -> list[str] | None:
    """Return the ACP server command, or None when dsh is not installed."""
    dsh = shutil.which('dsh')
    if not dsh:
        return None
    return [dsh, '--profile', resolve_profile()]


def _spawn_argv(command: list[str]) -> list[str]:
    """Windows npm shims are .CMD files; CreateProcess cannot execute them directly."""
    if os.name == 'nt' and command and command[0].lower().endswith(('.cmd', '.bat')):
        return [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', *command]
    return list(command)


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


class LiveStreamReader:
    """Read the dsh live-stream plugin's loopback SSE for one ACP session."""

    def __init__(self, port: int, token: str, session_id: str,
                 on_event: Callable[[dict[str, Any]], None]):
        self.port = int(port)
        self.token = token
        self.session_id = session_id
        self.on_event = on_event
        self.stop_event = threading.Event()
        self._response = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self.stop_event.set()
        response, self._response = self._response, None
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def _run(self) -> None:
        query = urllib.parse.urlencode({'session': self.session_id, 'token': self.token})
        url = f'http://127.0.0.1:{self.port}/events?{query}'
        deadline = time.monotonic() + LIVE_ATTACH_SECONDS
        while not self.stop_event.is_set():
            try:
                response = _LOOPBACK_OPENER.open(url, timeout=60)
                self._response = response
                with response:
                    for raw in response:
                        if self.stop_event.is_set():
                            return
                        line = raw.decode('utf-8', 'replace').strip()
                        if not line.startswith('data: '):
                            continue
                        try:
                            payload = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        if isinstance(payload, dict):
                            deadline = time.monotonic() + 30.0
                            try:
                                self.on_event(payload)
                            except Exception:
                                pass
                self._response = None
            except Exception:
                self._response = None
                if self.stop_event.is_set():
                    return
                if time.monotonic() > deadline:
                    return
                time.sleep(0.1)


class HarnessBrain:
    def __init__(self, database: Database, workspace_dir: Path,
                 command: list[str] | None = None, prompt_timeout: float = MAX_PROMPT_SECONDS,
                 startup_timeout: float = STARTUP_SECONDS):
        self.database = database
        self.workspace_dir = Path(workspace_dir)
        self.command = list(command) if command else resolve_command()
        self.prompt_timeout = float(prompt_timeout)
        self.startup_timeout = float(startup_timeout)
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr: list[str] = []
        self._next_id = 1
        self._initialized = False
        self._turn: dict[str, Any] | None = None
        self._on_delta: Callable[[str], None] | None = None
        self._on_event: Callable[[dict[str, Any]], None] | None = None
        self._live_reader: LiveStreamReader | None = None
        self._live_active = False
        self._version: str | None = None
        self.profile = resolve_profile()
        self.live_port = _free_loopback_port()
        self.live_token = secrets.token_urlsafe(18)
        self.live_enabled = os.environ.get('TORCH_LIVE', '1') != '0'
        with self.database.connect() as connection:
            connection.executescript(_SESSION_SCHEMA)

    # ---------------------------------------------------------------- status
    def available(self) -> bool:
        return bool(self.command)

    def version(self) -> str | None:
        if not self.command:
            return None
        if self._version is not None:
            return self._version
        try:
            completed = subprocess.run(
                _spawn_argv(self.command[:1] + ['--version']), capture_output=True, text=True,
                encoding='utf-8', timeout=20)
            self._version = (completed.stdout or completed.stderr or '').strip().splitlines()[-1:]
            self._version = self._version[0] if self._version else None
        except (OSError, subprocess.SubprocessError, IndexError):
            self._version = None
        return self._version

    def live_health(self) -> bool:
        if not self.live_enabled:
            return False
        try:
            with _LOOPBACK_OPENER.open(
                    f'http://127.0.0.1:{self.live_port}/health',
                    timeout=LIVE_HEALTH_SECONDS) as response:
                return bool(json.loads(response.read().decode('utf-8')).get('ok'))
        except Exception:
            return False

    def status(self) -> dict[str, Any]:
        available = self.available()
        return {
            'available': available,
            'dsh': self.command[0] if available else None,
            'version': self.version(),
            'workspace': str(self.workspace_dir),
            'running': self._process is not None and self._process.poll() is None,
            'profile': self.profile,
            'model': os.environ.get('TORCH_AGENT_MODEL', 'deepseek-v4-flash'),
            'live': {'enabled': self.live_enabled, 'port': self.live_port, 'ok': self.live_health()},
            'note': ('Harness 模式会把对话与工具读到的本机内容交给 dsh，由其决定发往 DeepSeek 的上下文；'
                     '使用你 DSH 里的凭据。只读工具自动放行，其它工具调用会被拒绝。'),
        }

    # ------------------------------------------------------------- transport
    def _spawn(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        if not self.command:
            raise HarnessError('未检测到 dsh：请先安装 DeepSeek Harness（npm install -g @deepseek-ai/dsh）')
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env['TORCH_LIVE_PORT'] = str(self.live_port)
        env['TORCH_LIVE_TOKEN'] = self.live_token
        try:
            self._process = subprocess.Popen(
                _spawn_argv(self.command), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', bufsize=1, cwd=str(self.workspace_dir), env=env)
        except OSError as error:
            raise HarnessError(f'无法启动 dsh：{error}') from error
        self._messages = queue.Queue()
        self._stderr = []
        self._initialized = False
        threading.Thread(target=self._pump_stdout, args=(self._process.stdout,), daemon=True).start()
        threading.Thread(target=self._pump_stderr, args=(self._process.stderr,), daemon=True).start()

    def _pump_stdout(self, stream) -> None:
        for line in stream:
            text = line.strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                self._messages.put(message)

    def _pump_stderr(self, stream) -> None:
        for line in stream:
            self._stderr.append(line.rstrip())
            del self._stderr[:-20]

    def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise HarnessError(self._process_error())
        with self._write_lock:
            try:
                process.stdin.write(json.dumps(message, ensure_ascii=False) + '\n')
                process.stdin.flush()
            except (OSError, ValueError) as error:
                raise HarnessError(f'dsh 连接已断开：{error}') from error

    def _process_error(self) -> str:
        tail = ' | '.join(self._stderr[-3:])
        return f'dsh 已退出：{tail}' if tail else 'dsh 连接不可用'

    def _wait_message(self, message_id: int, deadline: float) -> dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HarnessError('等待 dsh 响应超时')
            try:
                message = self._messages.get(timeout=remaining)
            except queue.Empty:
                raise HarnessError('等待 dsh 响应超时') from None
            if 'method' in message and 'id' in message:
                self._answer_agent_request(message)
                continue
            if 'method' in message:
                self._handle_notification(message)
                continue
            if message.get('id') == message_id:
                return message

    def _request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        message_id = self._next_id
        self._next_id += 1
        deadline = time.monotonic() + timeout
        self._write({'jsonrpc': '2.0', 'id': message_id, 'method': method, 'params': params})
        response = self._wait_message(message_id, deadline)
        if 'error' in response:
            detail = (response.get('error') or {}).get('message') or '未知错误'
            raise HarnessError(f'{method} 失败：{detail}')
        return response.get('result') or {}

    def _emit(self, payload: dict[str, Any]) -> None:
        callback = self._on_event
        if callback is None:
            return
        try:
            callback(payload)
        except Exception:  # never let a consumer break the pump or live thread
            pass

    def _handle_notification(self, message: dict[str, Any]) -> None:
        if message.get('method') != 'session/update':
            return
        turn = self._turn
        if turn is None:
            return
        update = (message.get('params') or {}).get('update') or {}
        kind = update.get('sessionUpdate')
        if kind == 'agent_message_chunk':
            text = ((update.get('content') or {}).get('text')) or ''
            if text:
                turn['texts'].append(text)
                if self._on_event is not None:
                    if not self._live_active:
                        self._emit({'type': 'text', 'text': text})
                elif self._on_delta is not None:
                    try:
                        self._on_delta(text)
                    except Exception:
                        pass
        elif kind == 'agent_thought_chunk':
            turn['thoughts'] += 1
            text = ((update.get('content') or {}).get('text')) or ''
            if text and self._on_event is not None and not self._live_active:
                self._emit({'type': 'thought', 'text': text})
        elif kind in {'tool_call', 'tool_call_update'}:
            title = str(update.get('title') or update.get('toolCallId') or '工具调用')
            if title not in turn['tools']:
                turn['tools'].append(title)
            if self._on_event is not None:
                status = str(update.get('status') or '')
                event = {
                    'type': 'tool_result' if status in {'completed', 'failed', 'cancelled'} else 'tool_call',
                    'id': update.get('toolCallId') or update.get('tool_call_id'),
                    'title': update.get('title'),
                    'name': update.get('name'),
                    'kind': update.get('kind'),
                    'status': status or None,
                    'input': update.get('rawInput') or update.get('input'),
                    'content': update.get('content'),
                }
                self._emit(event)

    def _answer_agent_request(self, message: dict[str, Any]) -> None:
        method = message.get('method')
        message_id = message.get('id')
        if method == 'session/request_permission':
            params = message.get('params') or {}
            serialized = json.dumps(params.get('toolCall') or {}, ensure_ascii=False)
            # Only the torch MCP server is auto-approved. Everything else is denied.
            allow = 'mcp__torch' in serialized or 'torch__' in serialized
            options = params.get('options') or []
            wanted = 'allow_once' if allow else 'reject_once'
            option_id = next((item.get('optionId') for item in options if item.get('kind') == wanted), None)
            if option_id is None and options:
                option_id = options[0].get('optionId')
            self._write({'jsonrpc': '2.0', 'id': message_id,
                         'result': {'outcome': {'outcome': 'selected', 'optionId': option_id}}})
            return
        self._write({'jsonrpc': '2.0', 'id': message_id,
                     'error': {'code': -32601, 'message': f'本客户端不支持 {method}'}})

    # ------------------------------------------------------------- protocol
    def _ensure_ready(self) -> None:
        if self._initialized and self._process is not None and self._process.poll() is None:
            return
        self._spawn()
        result = self._request('initialize', {
            'protocolVersion': PROTOCOL_VERSION,
            'clientCapabilities': {'fs': {'readTextFile': False, 'writeTextFile': False}, 'terminal': False},
            'clientInfo': {'name': 'torch-os', 'version': '1.0.0'},
        }, timeout=self.startup_timeout)
        if result.get('protocolVersion') != PROTOCOL_VERSION:
            raise HarnessError(f"ACP 协议版本不匹配：{result.get('protocolVersion')}")
        self._initialized = True

    def _mcp_servers(self, base_url: str) -> list[dict[str, Any]]:
        project_root = Path(__file__).resolve().parents[2]
        bridge = project_root / 'scripts' / 'mcp_bridge.py'
        return [{
            'name': 'torch',
            'command': sys.executable,
            'args': [str(bridge), '--base-url', base_url, '--retry-seconds', '10'],
            'env': [],
        }]

    def _stored_session(self, conversation_id: int) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                'SELECT * FROM harness_sessions WHERE conversation_id=?', (conversation_id,)).fetchone()
        return dict(row) if row else None

    def _store_session(self, conversation_id: int, session_id: str, cwd: str) -> None:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO harness_sessions(conversation_id,session_id,cwd,created_at,updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(conversation_id) DO UPDATE SET session_id=excluded.session_id,
                cwd=excluded.cwd, updated_at=excluded.updated_at""",
                (conversation_id, session_id, cwd, now, now))

    def _open_session(self, conversation_id: int, base_url: str) -> tuple[str, bool]:
        stored = self._stored_session(conversation_id)
        cwd = str(self.workspace_dir)
        if stored and stored['cwd'] == cwd:
            try:
                result = self._request('session/resume', {
                    'sessionId': stored['session_id'], 'cwd': cwd,
                    'mcpServers': self._mcp_servers(base_url),
                }, timeout=self.startup_timeout)
                session_id = result.get('sessionId') or stored['session_id']
                if session_id != stored['session_id']:
                    self._store_session(conversation_id, session_id, cwd)
                return session_id, True
            except HarnessError:
                pass
        result = self._request('session/new', {
            'cwd': cwd, 'mcpServers': self._mcp_servers(base_url),
        }, timeout=self.startup_timeout)
        session_id = result.get('sessionId')
        if not session_id:
            raise HarnessError('dsh 没有返回会话 ID')
        self._store_session(conversation_id, session_id, cwd)
        return session_id, False

    def run_turn(self, conversation_id: int, message: str, base_url: str = 'http://127.0.0.1:8765',
                 on_delta: Callable[[str], None] | None = None,
                 on_event: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        """Send one prompt and collect the answer. Blocking; one turn at a time.

        ``on_event`` receives display events: live token/thinking/tool deltas from
        the loopback plugin, plus ACP tool updates and a committed-text fallback.
        """
        with self._lock:
            self._ensure_ready()
            session_id, resumed = self._open_session(conversation_id, base_url)
            self._turn = {'texts': [], 'tools': [], 'thoughts': 0}
            self._on_delta = on_delta
            self._on_event = on_event
            self._live_active = False
            reader = None
            if on_event is not None:
                self._emit({'type': 'session', 'session_id': session_id, 'resumed': resumed})
                if self.live_enabled and self.live_health():
                    self._live_active = True
                    reader = LiveStreamReader(self.live_port, self.live_token, session_id, self._emit)
                    reader.start()
                    self._live_reader = reader
            try:
                result = self._request('session/prompt', {
                    'sessionId': session_id,
                    'prompt': [{'type': 'text', 'text': message}],
                }, timeout=self.prompt_timeout)
            finally:
                if reader is not None:
                    reader.close()
                turn, self._turn = self._turn, None
                self._on_delta = None
                self._on_event = None
                self._live_reader = None
                self._live_active = False
            answer = ''.join(turn['texts']).strip()
            if not answer:
                answer = '（Harness 没有返回文本内容；可能是工具调用后停止或需要更明确的提示。）'
            return {
                'answer': answer,
                'stop_reason': result.get('stopReason'),
                'tools': turn['tools'],
                'session_id': session_id,
                'resumed': resumed,
            }

    def cancel(self, conversation_id: int) -> bool:
        """Ask the ACP session to cancel its in-flight turn. Safe to call from another thread."""
        stored = self._stored_session(conversation_id)
        if not stored:
            return False
        try:
            self._write({'jsonrpc': '2.0', 'method': 'session/cancel',
                         'params': {'sessionId': stored['session_id']}})
            return True
        except Exception:
            return False

    def close(self) -> None:
        process, self._process = self._process, None
        self._initialized = False
        if process and process.poll() is None:
            try:
                process.stdin.close()
            except Exception:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    def reset(self, conversation_id: int | None = None) -> dict[str, Any]:
        """Stop the ACP child and forget stored sessions so the next turn starts clean."""
        with self._lock:
            self.close()
            with self.database.connect() as connection:
                if conversation_id is None:
                    cursor = connection.execute('DELETE FROM harness_sessions')
                else:
                    cursor = connection.execute(
                        'DELETE FROM harness_sessions WHERE conversation_id=?', (conversation_id,))
                cleared = cursor.rowcount or 0
        return {'cleared_sessions': cleared, 'process_stopped': True}
