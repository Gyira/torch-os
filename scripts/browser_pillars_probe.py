"""四库真实浏览器探针（2026-10-21）：导航、渲染、截图。只访问 127.0.0.1。"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_edge() -> str:
    for candidate in EDGE_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("msedge")
    if found:
        return found
    raise SystemExit("未找到 Microsoft Edge")


def targets(port: int):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


class CDP:
    def __init__(self, socket):
        self.socket = socket
        self.counter = 0

    async def call(self, method: str, params: dict | None = None):
        self.counter += 1
        message_id = self.counter
        await self.socket.send(json.dumps({"id": message_id, "method": method,
                                           "params": params or {}}))
        while True:
            message = json.loads(await self.socket.recv())
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    async def evaluate(self, expression: str):
        result = await self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        return result.get("result", {}).get("value")

    async def wait_for(self, expression: str, timeout: float = 30.0, interval: float = 0.25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.evaluate(f"Boolean({expression})"):
                return True
            await asyncio.sleep(interval)
        raise TimeoutError(f"等待超时：{expression}")

    async def screenshot(self, path: Path):
        result = await self.call("Page.captureScreenshot", {"format": "png"})
        path.write_bytes(base64.b64decode(result["data"]))


async def run(base_url: str, output: Path, port: int):
    edge = find_edge()
    profile = tempfile.mkdtemp(prefix="torch-edge-pillars-")
    process = subprocess.Popen([
        edge, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions",
        f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
        "--window-size=1440,1000", f"{base_url}/#life",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = {"ok": False, "views": {}}
    try:
        target = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                target = next((item for item in targets(port)
                               if item.get("type") == "page" and "webSocketDebuggerUrl" in item), None)
            except Exception:
                pass
            if target:
                break
            await asyncio.sleep(0.3)
        if not target:
            raise RuntimeError("无法连接 Edge 调试端口")
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=20_000_000) as socket:
            cdp = CDP(socket)
            await cdp.call("Page.enable")
            await cdp.call("Runtime.enable")
            await cdp.wait_for("document.readyState === 'complete'")
            await cdp.wait_for("document.querySelectorAll('.panel-nav-item').length === 6", timeout=45)
            for view in ("life", "self", "ops", "knowledge"):
                await cdp.evaluate(f"document.querySelector('.panel-nav-item[data-view=\"{view}\"]').click()")
                await cdp.wait_for(
                    f"location.hash === '#{view}' && document.querySelector('#{view}-view .pillar-head')",
                    timeout=30)
                await asyncio.sleep(0.4)
                report["views"][view] = await cdp.evaluate(f"""(() => ({{
                    title: document.querySelector('#{view}-view .pillar-head h2')?.textContent || '',
                    chapters: document.querySelectorAll('#{view}-view .pillar-rail-item').length,
                    rows: document.querySelectorAll('#{view}-view .pillar-note-row').length,
                    empty: document.querySelectorAll('#{view}-view .pillar-empty').length,
                    aside: document.querySelectorAll('#{view}-view .pillar-card').length,
                    hash: location.hash,
                }}))()""")
                await cdp.screenshot(output / f"{view}.png")
            report["nav"] = await cdp.evaluate(
                "Array.from(document.querySelectorAll('.panel-nav-item')).map((node) => node.dataset.view)")
            # 工作台回归：任务板、问候、横幅、天气卡都还在。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"workbench\"]').click()")
            await cdp.wait_for("document.querySelector('#workbench-hello') && document.querySelector('#workbench-hello').textContent !== '你好'", timeout=45)
            await asyncio.sleep(0.5)
            await cdp.screenshot(output / "workbench.png")
            await cdp.evaluate("(() => { const node = Array.from(document.querySelectorAll('.time-side-item')).find((item) => item.dataset.timeId === '收集箱'); node?.click(); })()")
            await asyncio.sleep(0.5)
            report["workbench"] = await cdp.evaluate("""(() => ({
                hello: document.querySelector('#workbench-hello')?.textContent || '',
                banner: Boolean(document.querySelector('#workbench-banner')),
                timeBoard: Boolean(document.querySelector('#time-board')),
                taskRows: document.querySelectorAll('#time-board .time-task').length,
                inboxRows: document.querySelectorAll('#time-board .time-task').length,
                weather: document.querySelector('#workbench-weather')?.textContent?.slice(0, 40) || '',
                almanac: document.querySelector('#workbench-almanac')?.textContent?.slice(0, 40) || '',
                hash: location.hash,
            }))()""")
            # 设置 → 库结构与迁移
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"settings\"]').click()")
            await cdp.wait_for("location.hash === '#settings'")
            await cdp.evaluate("document.querySelector('[data-settings-nav=\"migration\"]').click()")
            await cdp.wait_for("!document.querySelector('[data-settings-section=\"migration\"]').hidden")
            await asyncio.sleep(0.5)
            await cdp.screenshot(output / "migration-settings.png")
            report["migration"] = await cdp.evaluate("""(() => ({
                visible: !document.querySelector('[data-settings-section="migration"]').hidden,
                status: document.querySelector('#migration-status')?.textContent || '',
                layout: document.querySelector('#migration-layout')?.textContent?.slice(0, 120) || '',
            }))()""")
            # 全局搜索
            await cdp.evaluate("document.querySelector('.panel-nav-search')?.click()")
            await cdp.wait_for("document.querySelector('.pillar-search-dialog')")
            await cdp.evaluate("""(() => {
                const input = document.querySelector('[data-global-query]');
                input.value = '考研';
                input.dispatchEvent(new Event('input', { bubbles: true }));
            })()""")
            await cdp.wait_for("document.querySelectorAll('[data-global-note]').length > 0", timeout=20)
            await cdp.screenshot(output / "global-search.png")
            report["search"] = await cdp.evaluate("""(() => ({
                results: document.querySelectorAll('[data-global-note]').length,
                status: document.querySelector('[data-global-status]')?.textContent || '',
            }))()""")
            report["ok"] = all(item["chapters"] > 0 for item in report["views"].values())
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
            return report
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(profile, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8899")
    parser.add_argument("--output", default=".pillar-probe")
    parser.add_argument("--port", type=int, default=9333)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(run(args.base_url, output, args.port))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
