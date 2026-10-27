import asyncio, os, subprocess, sys

async def main():
    env = {k: v for k, v in os.environ.items() if not k.startswith("DSH")}
    env["DSH_AGENTS_HOME"] = r"C:\Users\19814\Desktop\Torch\torch-os\dsh"
    env["TORCH_REPO_ROOT"] = r"C:\Users\19814\Desktop\Torch\torch-os"
    env["PYTHONUTF8"] = "1"
    args = [r"C:\Windows\system32\cmd.exe", "/d", "/c",
            r"C:\Users\19814\AppData\Roaming\npm\dsh.CMD", "web", "--no-open",
            "--host", "127.0.0.1", "--port", "0"]
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=r"C:\Users\19814\Desktop\Torch\个人数据库", env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(15):
        raw = await asyncio.wait_for(proc.stdout.readline(), timeout=25)
        if not raw:
            break
        line = raw.decode("utf-8", "replace").rstrip()
        print("OUT:", line)
        if "dsh web:" in line:
            break
    if proc.returncode is None:
        proc.kill()
    print("exit:", await proc.wait())

asyncio.run(main())
