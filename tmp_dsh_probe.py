"""Reproduce the torch -> dsh spawn to see the child's first stderr lines."""
import asyncio
import os
import subprocess
import sys

from backend.app.dsh_web import resolve_command, spawn_argv, workflow_environment

WORKSPACE = r"C:\Users\19814\Desktop\Torch\个人数据库"


async def main() -> int:
    command = resolve_command(dict(os.environ))
    print("resolved:", command)
    if command is None:
        return 2
    args = spawn_argv([*command.argv, "web", "--no-open", "--host", "127.0.0.1", "--port", "0"])
    env = workflow_environment(dict(os.environ))
    print("agents home:", env.get("DSH_AGENTS_HOME"))
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=WORKSPACE,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    collected = []
    try:
        while True:
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=25)
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip()
            collected.append(line)
            print("OUT:", line)
            if "dsh web:" in line or len(collected) >= 30:
                break
    except asyncio.TimeoutError:
        print("TIMEOUT waiting for output")
    finally:
        if proc.returncode is None:
            proc.kill()
        code = await proc.wait()
        print("exit:", code)
    if not any("dsh web:" in line for line in collected):
        print("NO READINESS LINE")
        return 1
    return 0


sys.exit(asyncio.run(main()))
