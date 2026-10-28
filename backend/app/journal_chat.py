"""日记对话：把 dsh 当模型通道，一次一问。

为什么走 dsh 而不是直接调模型：人格、四库读取、归档规则都已经在 dsh 里，
面板只负责「问一句、收一句」，不重复造一套 prompt 治理。

三点约束：

- 每轮一个 `dsh headless --json` 子进程；首轮拿 `sessionId`，后续 `--session-id` 续聊；
- headless profile **不读凭据服务**，必须把 Key 注入子进程环境（`DeepSeekBalanceService.resolve_key`）；
- 任何失败（没 Key、超时、非零退出、空回复）都返回 `available=false`，面板退回「填一下」，
  绝不让日记卡在半截。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import journal_review
from .dsh_web import resolve_command, spawn_argv, workflow_environment
from .journal_shared import validate_day

MAX_MESSAGE = 4000
TURN_TIMEOUT = 120
SESSION_PREFIX = "session-"

PERSONA = """你是我的日记搭档，不是助手。现在是我睡前的 5–10 分钟。

今天的盘点（只读事实，不要质疑、不要复述整段）：
{facts}

规则：
1. 一次只问一个问题，20 字以内，必须挂着一个具体事实；
2. 我回答后，先接一句人话（不超过 20 字），再问下一个；不评价、不打分、不列清单、不说“你应该”；
3. 三个问题之后说「我整理一下」，然后只输出一段第一人称的日记正文（不超过 300 字），不要标题、不要解释；
4. 不读写任何文件，不调用任何工具。"""

DRAFT_INSTRUCTION = "把刚才的回答整理成一段第一人称的日记正文，不超过 300 字，不要标题、不要解释。"


@dataclass(frozen=True)
class ChatTurn:
    session_id: str
    text: str
    seconds: float


class ChatUnavailable(RuntimeError):
    """dsh 通道不可用：没命令、没 Key、超时或空回复。"""


def _fact_lines(digest: dict[str, Any]) -> str:
    lines = list(digest.get("signals") or [])
    if not lines:
        lines = ["今天没有留下可用的记录。"]
    return "\n".join(f"- {line}" for line in lines)


def opening_prompt(digest: dict[str, Any]) -> str:
    return PERSONA.format(facts=_fact_lines(digest))


def parse_events(stdout: str) -> tuple[str, str]:
    """从 NDJSON 里取 sessionId 与 final 文本；解析不了的行直接跳过。"""
    session_id = ""
    final_text = ""
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
        if event.get("type") == "session" and not session_id:
            session_id = str(event.get("sessionId") or "")
        elif event.get("type") == "final":
            final_text = str(event.get("text") or "")
    return session_id, final_text


def _command(environ: dict[str, str]) -> list[str]:
    command = resolve_command(environ)
    if command is None:
        raise ChatUnavailable("没有找到 dsh 命令，请先安装 DeepSeek Harness")
    return spawn_argv([*command.argv, "headless", "--json"])


def run_turn(prompt: str, *, workspace: Path, environ: dict[str, str],
             session_id: str = "", timeout: float = TURN_TIMEOUT,
             runner: Callable[..., Any] | None = None) -> ChatTurn:
    if not prompt.strip():
        raise HTTPException(400, "消息不能为空")
    if len(prompt) > MAX_MESSAGE:
        raise HTTPException(400, "这条消息太长了")
    argv = _command(environ)
    if session_id:
        if not session_id.startswith(SESSION_PREFIX):
            raise HTTPException(400, "session_id 不正确")
        argv += ["--session-id", session_id]
    argv.append(prompt)

    started = time.monotonic()
    run = runner or subprocess.run
    try:
        completed = run(argv, cwd=str(workspace), env=environ, capture_output=True,
                        text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ChatUnavailable(f"dsh 启动失败：{exc}") from exc
    elapsed = round(time.monotonic() - started, 1)

    if getattr(completed, "returncode", 1) != 0 and not (completed.stdout or "").strip():
        tail = " / ".join((completed.stderr or "").strip().splitlines()[-3:])[:300]
        raise ChatUnavailable("dsh 这一轮没有返回内容" + (f"：{tail}" if tail else ""))
    new_session, text = parse_events(completed.stdout or "")
    if not text.strip():
        raise ChatUnavailable("dsh 这一轮回复是空的")
    return ChatTurn(session_id=new_session or session_id, text=text.strip(), seconds=elapsed)


def chat_environment(base: dict[str, str], key: str) -> dict[str, str]:
    env = workflow_environment(base)
    if key:
        env["DEEPSEEK_API_KEY"] = key
    return env


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: str = ""
    message: str = Field(default="", max_length=MAX_MESSAGE)
    session_id: str = Field(default="", max_length=80)
    mode: str = Field(default="turn", max_length=12)


def create_journal_chat_router(database: Any, vault_dir: Path | str, balance: Any,
                               workspace: Path | str, prefix: str = "/api",
                               enabled: bool = True) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["journal-chat"])

    @router.get("/journal/chat/status")
    def chat_status():
        key = balance.resolve_key() if balance is not None else ""
        command = resolve_command(dict(os.environ))
        return {
            "available": bool(enabled and command and key),
            "reason": "" if (enabled and command and key) else (
                "dsh 未启用" if not enabled else "没有找到 dsh 命令" if not command else "没有可用的 DeepSeek Key"),
            "workspace": str(workspace),
        }

    @router.post("/journal/chat")
    def chat(payload: ChatRequest):
        if not enabled:
            raise HTTPException(409, "dsh 通道没有启用")
        key = balance.resolve_key() if balance is not None else ""
        if not key:
            raise HTTPException(409, "没有可用的 DeepSeek Key：请在 dsh 的 Models 页面存一次")
        target = validate_day(payload.day or journal_review.shanghai_today().isoformat()).isoformat()
        mode = payload.mode if payload.mode in {"start", "turn", "draft"} else "turn"

        if mode == "start":
            digest = journal_review.build_digest(database, vault_dir, target)
            prompt = opening_prompt(digest)
        elif mode == "draft":
            prompt = DRAFT_INSTRUCTION
        else:
            prompt = payload.message.strip()
            if not prompt:
                raise HTTPException(400, "说点什么再发")

        environ = chat_environment(dict(os.environ), key)
        try:
            turn = run_turn(prompt, workspace=Path(workspace), environ=environ,
                            session_id=payload.session_id)
        except ChatUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"day": target, "session_id": turn.session_id,
                "reply": turn.text, "seconds": turn.seconds}

    return router
