"""自我档案 v2 / 人生导师 / 八字系统的浏览器快速探针（2026-11-01）。

用法：
    python -m backend.app.qa_runtime --port 8899
    python scripts/self_probe.py --base-url http://127.0.0.1:8899

只访问 127.0.0.1 的 QA 实例，不调用模型、不碰真实库。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_pillars_probe import CDP, find_edge, targets  # noqa: E402

ERROR_HOOK = """(() => {
  window.__torchErrors = [];
  window.addEventListener('error', (event) => {
    window.__torchErrors.push(String(event.message || event.error || 'error'));
  });
  window.addEventListener('unhandledrejection', (event) => {
    window.__torchErrors.push(String(event.reason || 'rejection'));
  });
  return true;
})()"""

READ_SELF = """(() => {
  const shell = document.querySelector('#self-shell');
  const books = document.querySelectorAll('.self-book-card');
  const groups = document.querySelectorAll('.self-rail-group');
  const head = shell ? shell.querySelector('.pillar-head h2') : null;
  const overview = document.querySelector('.self-overview-books');
  const portrait = document.querySelector('.self-portrait');
  const order = overview && portrait
    ? (overview.compareDocumentPosition(portrait) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0
    : false;
  return {
    shell: Boolean(shell),
    title: head ? head.textContent.trim() : '',
    books: books.length,
    groups: groups.length,
    portrait_after_books: order,
    loading: Boolean(shell && shell.querySelector('.self-loading')),
    errors: Array.isArray(window.__torchErrors) ? window.__torchErrors.slice(0, 8) : [],
  };
})()"""

READ_BOOK = """(() => ({
  page: Boolean(document.querySelector('.self-book-page')),
  title: document.querySelector('.self-book-page h3')?.textContent?.trim() || '',
  entries: document.querySelectorAll('.self-entry').length,
  empty: Boolean(document.querySelector('.self-empty')),
  debate: document.querySelectorAll('[data-self-debate]').length,
  loading: Boolean(document.querySelector('.self-loading')),
  errors: Array.isArray(window.__torchErrors) ? window.__torchErrors.slice(0, 8) : [],
}))()"""

READ_MENTOR = """(() => ({
  shell: Boolean(document.querySelector('.mentor-shell')),
  sessions: Boolean(document.querySelector('.mentor-sessions')),
  chat: Boolean(document.querySelector('.mentor-chat')),
  side: Boolean(document.querySelector('.mentor-side')),
  composer: Boolean(document.querySelector('[data-mentor-input]')),
  tabs: document.querySelectorAll('[data-mentor-tab]').length,
  loading: Boolean(document.querySelector('.self-loading')),
  errors: Array.isArray(window.__torchErrors) ? window.__torchErrors.slice(0, 8) : [],
}))()"""

READ_WORKBENCH = """(() => ({
  strip: Boolean(document.querySelector('#workbench-mentor')),
  hidden: document.querySelector('#workbench-mentor')?.hidden ?? true,
  text: document.querySelector('#workbench-mentor')?.textContent?.trim()?.slice(0, 80) || '',
  errors: Array.isArray(window.__torchErrors) ? window.__torchErrors.slice(0, 8) : [],
}))()"""

READ_PENDING = "document.querySelectorAll('.self-pending-card').length"

READ_QUESTIONS = "document.querySelectorAll('.mentor-question').length"

READ_BAZI = """(() => ({
  shell: Boolean(document.querySelector('.bazi-shell')),
  chart: Boolean(document.querySelector('.bazi-pillars')),
  topics: document.querySelectorAll('[data-bazi-topic]').length,
  readings: Boolean(document.querySelector('.bazi-readings-section')),
  text: document.querySelector('#self-main')?.textContent?.trim()?.slice(0, 40) || '',
  loading: Boolean(document.querySelector('.self-loading')),
  errors: Array.isArray(window.__torchErrors) ? window.__torchErrors.slice(0, 8) : [],
}))()"""


async def run(base_url: str, port: int = 9335) -> dict:
    edge = find_edge()
    profile = tempfile.mkdtemp(prefix="torch-self-edge-")
    process = subprocess.Popen([
        edge, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions",
        f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
        "--window-size=1440,1000", f"{base_url}/#workbench",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report: dict = {"ok": False, "checks": {}, "errors": []}
    try:
        target = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                target = next((item for item in targets(port)
                               if item.get("type") == "page" and "webSocketDebuggerUrl" in item
                               and str(item.get("url") or "").startswith(base_url)), None)
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
            await cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": ERROR_HOOK})
            await cdp.call("Page.navigate", {"url": f"{base_url}/#workbench"})
            await cdp.wait_for("document.readyState === 'complete'", timeout=45)
            await cdp.wait_for("document.querySelector('#workbench-mentor')?.hidden === false", timeout=45)
            report["checks"]["workbench"] = await cdp.evaluate(READ_WORKBENCH)
            await cdp.evaluate("location.hash = '#self'")
            await cdp.wait_for("document.querySelectorAll('.self-book-card').length === 11", timeout=45)
            report["checks"]["self"] = await cdp.evaluate(READ_SELF)
            await cdp.evaluate("document.querySelector('[data-self-book=\"self_values\"]').click()")
            await cdp.wait_for("Boolean(document.querySelector('.self-book-page'))", timeout=30)
            report["checks"]["book"] = await cdp.evaluate(READ_BOOK)
            await cdp.evaluate("document.querySelector('[data-self-page=\"mentor\"][data-self-sub=\"chat\"]').click()")
            await cdp.wait_for("Boolean(document.querySelector('.mentor-shell'))", timeout=30)
            await cdp.wait_for("document.querySelectorAll('[data-mentor-tab]').length === 4", timeout=30)
            await cdp.evaluate("document.querySelector('[data-mentor-tab=\"pending\"]').click()")
            await cdp.wait_for("document.querySelectorAll('.self-pending-card').length >= 1", timeout=30)
            pending_count = await cdp.evaluate(READ_PENDING)
            await cdp.evaluate("document.querySelector('[data-mentor-tab=\"questions\"]').click()")
            await cdp.wait_for("document.querySelectorAll('.mentor-question').length === 3", timeout=30)
            question_count = await cdp.evaluate(READ_QUESTIONS)
            report["checks"]["mentor"] = {**(await cdp.evaluate(READ_MENTOR)),
                                          "pending": pending_count, "questions": question_count}
            await cdp.evaluate("document.querySelector('[data-self-page=\"bazi\"][data-self-sub=\"chart\"]').click()")
            await cdp.wait_for("Boolean(document.querySelector('.bazi-shell'))", timeout=30)
            await cdp.wait_for("Boolean(document.querySelector('.bazi-pillars'))", timeout=30)
            report["checks"]["bazi"] = await cdp.evaluate(READ_BAZI)
            await cdp.evaluate("location.hash = '#workbench'")
            await cdp.wait_for("document.querySelector('[data-mentor-strip-open]')", timeout=30)
            await cdp.evaluate("document.querySelector('[data-mentor-strip-open]').click()")
            await cdp.wait_for("Boolean(document.querySelector('.mentor-shell'))", timeout=30)
            await cdp.wait_for("location.hash === '#self'", timeout=10)
            await cdp.wait_for(
                "document.querySelector('[data-mentor-tab=\"questions\"]')?.classList.contains('is-active')",
                timeout=30)
            report["checks"]["strip_click"] = bool(await cdp.evaluate(
                "document.querySelector('[data-mentor-tab=\"questions\"]')?.classList.contains('is-active')"))
            self_check = report["checks"].get("self") or {}
            book_check = report["checks"].get("book") or {}
            mentor_check = report["checks"].get("mentor") or {}
            workbench_check = report["checks"].get("workbench") or {}
            report["ok"] = (
                not workbench_check.get("hidden")
                and bool(self_check.get("shell"))
                and self_check.get("books") == 11
                and bool(self_check.get("portrait_after_books"))
                and bool(book_check.get("page"))
                and book_check.get("entries", 0) >= 1
                and bool(mentor_check.get("composer"))
                and mentor_check.get("pending", 0) >= 1
                and mentor_check.get("questions") == 3
                and (report["checks"].get("bazi") or {}).get("chart")
                and bool(report["checks"].get("strip_click"))
            )
            report["errors"] = self_check.get("errors", []) + list(cdp.errors)
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8899")
    parser.add_argument("--port", type=int, default=9335)
    args = parser.parse_args()
    report = asyncio.run(run(args.base_url, args.port))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
