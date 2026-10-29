"""Real subprocess checks for the role gateway: stdin prompt and process-tree timeout.

These do not call dsh or a model.  A tiny Python script stands in for dsh so we
verify the Windows/POSIX process handling that unit fake runners cannot cover.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.app import ai_gateway
from backend.app.dsh_web import DshCommand


def _script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake_dsh.py"
    path.write_text(body, encoding="utf-8")
    return path


def _alive(pid: int) -> bool:
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_real_subprocess_reads_prompt_from_stdin(tmp_path):
    script = _script(tmp_path, "\n".join([
        "import sys, json",
        "task = sys.stdin.read()",
        "print(json.dumps({'type':'session','sessionId':'session-real'}), flush=True)",
        "print(json.dumps({'type':'final','text':'echo:' + task}), flush=True)",
        "",
    ]))
    prompt = "你好，这是一个多行提示。\n第二行。emoji：🔥"
    result = ai_gateway.run_headless(
        prompt,
        workspace=tmp_path,
        environ=ai_gateway.headless_environment(os.environ),
        command=DshCommand((sys.executable, str(script)), "custom"),
        timeout=15,
    )
    assert result.session_id == "session-real"
    assert result.text == f"echo:{prompt}"


def test_timeout_kills_the_spawned_child_process(tmp_path):
    script = _script(tmp_path, "\n".join([
        "import subprocess, sys, time, os",
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])",
        "open('child.pid', 'w', encoding='utf-8').write(str(child.pid))",
        "print('started', flush=True)",
        "time.sleep(60)",
        "",
    ]))
    pid_file = tmp_path / "child.pid"
    with pytest.raises(ai_gateway.AITimeout):
        ai_gateway.run_headless(
            "hi",
            workspace=tmp_path,
            environ=ai_gateway.headless_environment(os.environ),
            command=DshCommand((sys.executable, str(script)), "custom"),
            timeout=1.5,
        )
    assert pid_file.is_file(), "子进程应已启动"
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and _alive(child_pid):
        time.sleep(0.2)
    assert not _alive(child_pid), f"超时后子进程仍在运行：{child_pid}"


def test_three_consecutive_timeout_rounds_leave_no_node_children(tmp_path):
    """P5 验收：连续 3 次角色调用被超时终止后，不残留 node 子进程。

    dsh 本身是 Node 程序，用真实的 ``node`` 作为被拉起的孙进程，比只用
    Python 子进程更接近实际进程树；每轮都重新检查上一轮的 node 已退出。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不在 PATH 中，跳过残留 node 检查")

    children: list[int] = []
    for index in range(3):
        pid_file = tmp_path / f"node-child-{index}.pid"
        script = _script(tmp_path, "\n".join([
            "import subprocess, sys, time",
            "child = subprocess.Popen([%s, '-e', 'setTimeout(function () {}, 120000)'])" % json.dumps(node),
            "open(%s, 'w', encoding='utf-8').write(str(child.pid))" % json.dumps(str(pid_file)),
            "print('started', flush=True)",
            "time.sleep(120)",
            "",
        ]))
        with pytest.raises(ai_gateway.AITimeout):
            ai_gateway.run_headless(
                f"第 {index + 1} 轮",
                workspace=tmp_path,
                environ=ai_gateway.headless_environment(os.environ),
                command=DshCommand((sys.executable, str(script)), "custom"),
                timeout=1.5,
            )
        assert pid_file.is_file(), "node 子进程应已启动"
        child_pid = int(pid_file.read_text(encoding="utf-8"))
        children.append(child_pid)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and _alive(child_pid):
            time.sleep(0.2)
        assert not _alive(child_pid), f"第 {index + 1} 轮超时后 node 子进程仍在运行：{child_pid}"

    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and any(_alive(pid) for pid in children):
        time.sleep(0.2)
    survivors = [pid for pid in children if _alive(pid)]
    assert not survivors, f"连续 3 轮后仍有 node 子进程：{survivors}"
