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
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .db import Database, utc_now


PROTOCOL_VERSION = 1
MAX_PROMPT_SECONDS = 240.0
STARTUP_SECONDS = 30.0
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


def resolve_command() -> list[str] | None:
    """Return the ACP server command, or None when dsh is not installed."""
    dsh = shutil.which('dsh')
    if not dsh:
        return None
    return [dsh, '--profile', 'acp']


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
        self._version: str | None = None
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
                [self.command[0], '--version'], capture_output=True, text=True,
                encoding='utf-8', timeout=20)
            self._version = (completed.stdout or completed.stderr or '').strip().splitlines()[-1:]
            self._version = self._version[0] if self._version else None
        except (OSError, subprocess.SubprocessError, IndexError):
            self._version = None
        return self._version

    def status(self) -> dict[str, Any]:
        available = self.available()
        return {
            'available': available,
            'dsh': self.command[0] if available else None,
            'version': self.version(),
            'workspace': str(self.workspace_dir),
            'running': self._process is not None and self._process.poll() is None,
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
        try:
            self._process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', bufsize=1, cwd=str(self.workspace_dir))
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
                callback = self._on_delta
                if callback is not None:
                    try:
                        callback(text)
                    except Exception:  # never let a consumer break the pump thread
                        pass
        elif kind == 'agent_thought_chunk':
            turn['thoughts'] += 1
        elif kind in {'tool_call', 'tool_call_update'}:
            title = str(update.get('title') or update.get('toolCallId') or '工具调用')
            if title not in turn['tools']:
                turn['tools'].append(title)

    def _answer_agent_request(self, message: dict[str, Any]) -> None:
        method = message.get('method')
        message_id = message.get('id')
        if method == 'session/request_permission':
            params = message.get('params') or {}
            allow = 'torch' in json.dumps(params.get('toolCall') or {}, ensure_ascii=False)
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
                return result.get('sessionId') or stored['session_id'], True
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
                 on_delta: Callable[[str], None] | None = None) -> dict[str, Any]:
        """Send one prompt and collect the streamed answer. Blocking; one turn at a time."""
        with self._lock:
            self._ensure_ready()
            session_id, resumed = self._open_session(conversation_id, base_url)
            self._turn = {'texts': [], 'tools': [], 'thoughts': 0}
            self._on_delta = on_delta
            try:
                result = self._request('session/prompt', {
                    'sessionId': session_id,
                    'prompt': [{'type': 'text', 'text': message}],
                }, timeout=self.prompt_timeout)
            finally:
                turn, self._turn = self._turn, None
                self._on_delta = None
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
