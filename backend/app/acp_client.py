"""人生导师图片通道：最小 ACP（Agent Client Protocol）stdio 客户端（2026-11-05）。

导师的普通对话仍走 `dsh headless`；只有带图片的回合走 ACP：
`dsh --profile acp --patch dsh/acp-mentor.patch.yml`。ACP 的 `session/prompt`
原生支持 image content block，deepseek-flash 声明了图片输入，因此模型能直接看图。
一次回合一个短会话；图片只以 base64 随提示词发送，不把 Vault 路径交给 dsh。
"""
from __future__ import annotations

import base64
import json
import queue
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .ai_gateway import (
    AIError, DEFAULT_TIMEOUT, MAX_PROMPT_CHARS, _kill_tree, headless_environment,
    redact,
)
from .dsh_web import DshCommand, resolve_command, spawn_argv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACP_PATCH = PROJECT_ROOT / "dsh" / "acp-mentor.patch.yml"
PROTOCOL_VERSION = 1
MAX_REPLY_CHARS = 200_000
MAX_STDERR_LINES = 20
START_TIMEOUT = 30.0
CLOSE_TIMEOUT = 3.0
# 每个 ACP 进程都会拉起 node；与 headless 同样的并发上限，避免图片回合互相抢内存。
_ACP_SEMAPHORE = threading.BoundedSemaphore(2)


class AcpError(AIError):
    """ACP 通道失败；AI 路由会把它映射成 503。"""


class AcpUnavailable(AcpError):
    """dsh ACP 不可用（未安装、配置缺失、进程提前退出）。"""


class AcpProtocolError(AcpError):
    """ACP 返回了无法使用的协议内容。"""


class AcpTimeout(AcpError):
    """ACP 回合超过时限，进程树已被终止。"""


@dataclass(frozen=True)
class AcpResult:
    text: str
    session_id: str
    seconds: float
    stop_reason: str
    stderr_tail: str = ""


class _AcpSession:
    """One ACP stdio connection with ordered update collection."""

    def __init__(self, process: subprocess.Popen, timeout: float):
        self.process = process
        self.timeout = timeout
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=MAX_STDERR_LINES)
        self._counter = 0
        self._text_parts: list[str] = []
        self._write_lock = threading.Lock()
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        try:
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self._queue.put(line)
        finally:
            self._queue.put(None)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            text = redact(line.strip())
            if text:
                self._stderr.append(text)

    def _send(self, message: Mapping[str, Any]) -> None:
        assert self.process.stdin is not None
        payload = json.dumps(dict(message), ensure_ascii=False) + "\n"
        with self._write_lock:
            self.process.stdin.write(payload)
            self.process.stdin.flush()

    def _handle_update(self, message: Mapping[str, Any]) -> None:
        params = message.get("params") or {}
        update = params.get("update") or {}
        if not isinstance(update, dict):
            return
        if update.get("sessionUpdate") != "agent_message_chunk":
            return
        content = update.get("content") or {}
        if isinstance(content, dict) and content.get("type") == "text":
            text = str(content.get("text") or "")
            if text:
                self._text_parts.append(text)

    def _handle_server_request(self, message: Mapping[str, Any]) -> None:
        # Tools are disabled in the mentor ACP profile, so this should be empty.
        # Keeping a deterministic refusal prevents a deadlock if a future dsh
        # build asks for a permission or client-side filesystem operation.
        request_id = message.get("id")
        if request_id is None:
            return
        if message.get("method") == "session/request_permission":
            result: dict[str, Any] = {"outcome": {"outcome": "cancelled"}}
            self._send({"jsonrpc": "2.0", "id": request_id, "result": result})
            return
        self._send({
            "jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": "torch ACP client does not support this method"},
        })

    def request(self, method: str, params: Mapping[str, Any], *,
                timeout: float) -> dict[str, Any]:
        self._counter += 1
        request_id = self._counter
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method,
                    "params": dict(params)})
        deadline = time.monotonic() + max(0.1, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AcpTimeout(f"导师图片通道超过 {int(timeout)} 秒，已终止")
            try:
                line = self._queue.get(timeout=remaining)
            except queue.Empty as error:
                raise AcpTimeout(f"导师图片通道超过 {int(timeout)} 秒，已终止") from error
            if line is None:
                detail = self.stderr_tail()
                raise AcpUnavailable("dsh 图片通道提前退出" + (f"：{detail}" if detail else ""))
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") == request_id:
                if "error" in message:
                    error = message.get("error") or {}
                    raise AcpProtocolError(str(error.get("message") or "ACP 请求失败"))
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            if message.get("method") == "session/update":
                self._handle_update(message)
            elif message.get("method") and message.get("id") is not None:
                self._handle_server_request(message)

    @property
    def text(self) -> str:
        value = "".join(self._text_parts).strip()
        if len(value) > MAX_REPLY_CHARS:
            return value[: MAX_REPLY_CHARS - 1].rstrip() + "…"
        return value

    def stderr_tail(self) -> str:
        return " / ".join(list(self._stderr)[-5:])[:500]

    def close(self, session_id: str) -> None:
        if session_id:
            try:
                self.request("session/close", {"sessionId": session_id},
                             timeout=CLOSE_TIMEOUT)
            except Exception:  # noqa: BLE001 - best-effort shutdown
                pass
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass
        _kill_tree(self.process)
        try:
            self.process.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        for stream in (self.process.stdout, self.process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


def _acp_argv(command: DshCommand, patch: Path) -> list[str]:
    return spawn_argv([*command.argv, "--profile", "acp", "--patch", str(patch)])


def run_prompt(prompt: str, images: Iterable[Mapping[str, Any]], *,
               workspace: Path | str, environ: Mapping[str, str],
               key: str = "", timeout: float = DEFAULT_TIMEOUT,
               command: DshCommand | None = None,
               patch: Path | str = ACP_PATCH,
               session_id: str = "") -> AcpResult:
    """Run one image-bearing mentor turn through dsh ACP and return its text."""
    acquired = _ACP_SEMAPHORE.acquire(timeout=max(1.0, timeout))
    if not acquired:
        raise AcpTimeout("导师图片通道正忙，请稍后再试")
    try:
        return _run_prompt_locked(
            prompt, images, workspace=workspace, environ=environ, key=key,
            timeout=timeout, command=command, patch=patch, session_id=session_id)
    finally:
        _ACP_SEMAPHORE.release()


def _run_prompt_locked(prompt: str, images: Iterable[Mapping[str, Any]], *,
                       workspace: Path | str, environ: Mapping[str, str],
                       key: str = "", timeout: float = DEFAULT_TIMEOUT,
                       command: DshCommand | None = None,
                       patch: Path | str = ACP_PATCH,
                       session_id: str = "") -> AcpResult:
    text = str(prompt or "").strip()
    if not text:
        raise AcpUnavailable("这一轮没有可发送的内容")
    if len(text) > MAX_PROMPT_CHARS:
        raise AcpUnavailable("这一轮的上下文太长了，请缩小范围后再试")
    rows = list(images or [])
    if not rows:
        raise AcpUnavailable("图片通道没有收到图片")
    patch_path = Path(patch)
    if not patch_path.is_file():
        raise AcpUnavailable("缺少导师图片通道配置：dsh/acp-mentor.patch.yml")
    resolved = command or resolve_command(dict(environ))
    if resolved is None:
        raise AcpUnavailable("没有找到 dsh 命令，请先安装 DeepSeek Harness")
    target = Path(workspace)
    target.mkdir(parents=True, exist_ok=True)
    argv = _acp_argv(resolved, patch_path)
    env = headless_environment(environ, key=key)
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=str(target), env=env,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except OSError as error:
        raise AcpUnavailable(f"无法启动 dsh 图片通道：{error}") from error

    session = _AcpSession(process, timeout)
    acp_session_id = ""
    try:
        init = session.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {},
            "clientInfo": {"name": "torch-os", "version": "1.0"},
        }, timeout=min(START_TIMEOUT, timeout))
        capabilities = ((init.get("agentCapabilities") or {}).get("promptCapabilities") or {})
        if not capabilities.get("image"):
            raise AcpUnavailable("当前 dsh 构建没有声明图片提示词能力")
        created = session.request("session/new", {
            "cwd": str(target.resolve()), "mcpServers": [],
        }, timeout=min(START_TIMEOUT, timeout))
        acp_session_id = str(created.get("sessionId") or "")
        if not acp_session_id:
            raise AcpProtocolError("dsh 没有返回 ACP 会话 id")
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for image in rows:
            data = image.get("image_bytes")
            if not isinstance(data, (bytes, bytearray)) or not data:
                raise AcpProtocolError("图片附件内容为空，请重新上传")
            content.append({
                "type": "image",
                "mimeType": str(image.get("media_type") or "image/png"),
                "data": base64.b64encode(bytes(data)).decode("ascii"),
            })
        response = session.request("session/prompt", {
            "sessionId": acp_session_id, "prompt": content,
        }, timeout=timeout)
        answer = session.text
        if not answer:
            raise AcpProtocolError("dsh 这一轮图片回复是空的")
        return AcpResult(
            text=answer,
            session_id=session_id,
            seconds=round(time.monotonic() - started, 1),
            stop_reason=str(response.get("stopReason") or ""),
            stderr_tail=session.stderr_tail(),
        )
    finally:
        session.close(acp_session_id)


__all__ = [
    "ACP_PATCH", "AcpResult", "AcpError", "AcpUnavailable",
    "AcpProtocolError", "AcpTimeout", "run_prompt",
]
