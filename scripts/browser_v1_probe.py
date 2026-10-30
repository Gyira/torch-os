"""v1.0 浏览器验收（2026-10-29）：工作台小组件、写作编辑器、分享入口、忆灵/生活运营卡片。

只访问 127.0.0.1 的 QA 实例，不调用模型、不碰真实库。用法：
    python -m backend.app.qa_runtime --port 8899
    python scripts/browser_v1_probe.py --base-url http://127.0.0.1:8899
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


async def run(base_url: str, port: int = 9334) -> dict:
    edge = find_edge()
    report: dict = {"ok": False, "checks": {}, "errors": []}
    profile = tempfile.mkdtemp(prefix="torch-edge-v1-")
    process = subprocess.Popen([
        edge, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions",
        f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
        "--window-size=1440,1000", f"{base_url}/#workbench",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        target = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                # Edge 首次启动可能自己打开同步确认页；只选回应用页。
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
            await cdp.call("Log.enable")
            await cdp.wait_for("document.readyState === 'complete'")
            await cdp.wait_for("document.querySelectorAll('.panel-nav-item').length === 6", timeout=45)

            await cdp.wait_for("document.querySelector('#workbench-memory')", timeout=30)
            report["checks"]["workbench"] = bool(await cdp.evaluate("""(() => ({
                memory: Boolean(document.querySelector('#workbench-memory')),
                old_question: Boolean(document.querySelector('#workbench-question')),
                widgets: document.querySelectorAll('[data-widget]').length,
            }))()"""))
            wb = await cdp.evaluate("""(() => ({
                memory: Boolean(document.querySelector('#workbench-memory')),
                old_question: Boolean(document.querySelector('#workbench-question')),
                widgets: document.querySelectorAll('[data-widget]').length,
            }))()""")
            report["checks"]["workbench_detail"] = wb
            if wb["old_question"] or not wb["memory"]:
                report["errors"].append("工作台忆灵条/旧提问卡不符合预期")

            # 设置 → 小组件：关掉天气，工作台不再显示。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"settings\"]').click()")
            await cdp.wait_for("location.hash === '#settings'", timeout=10)
            await cdp.evaluate("document.querySelector('[data-settings-nav=\"widgets\"]').click()")
            await cdp.wait_for("document.querySelectorAll('#widget-settings-list li').length >= 7", timeout=20)
            await cdp.evaluate("""(() => {
                const box = document.querySelector('[data-widget-toggle="weather"]');
                if (box.checked) box.click();
            })()""")
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"workbench\"]').click()")
            await cdp.wait_for("location.hash === '#workbench'", timeout=10)
            await cdp.wait_for("document.querySelector('[data-widget=\"weather\"]')?.hidden === true", timeout=20)
            report["checks"]["widget_hidden"] = True

            # 人生路：首页主行动 + 本子网格；今日日记能直接打开全屏编辑器。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"life\"]').click()")
            await cdp.wait_for("location.hash === '#life'", timeout=10)
            await cdp.wait_for("document.querySelector('#life-view .pillar-head')", timeout=30)
            await cdp.wait_for("document.querySelector('#life-view .life-hero')", timeout=30)
            life_home = await cdp.evaluate("""(() => ({
                hero: Boolean(document.querySelector('#life-view .life-hero')),
                notebooks: document.querySelectorAll('#life-view .notebook-card').length,
                today_action: Boolean(document.querySelector('#life-view [data-editor-new="life"][data-new-chapter="journal"]')),
            }))()""")
            report["checks"]["life_home"] = life_home
            if not life_home.get("hero") or life_home.get("notebooks", 0) < 7 or not life_home.get("today_action"):
                report["errors"].append("人生路首页主行动/本子网格不符合预期")
            await cdp.evaluate("document.querySelector('#life-view [data-editor-new=\"life\"][data-new-chapter=\"journal\"]').click()")
            await cdp.wait_for("document.querySelector('#note-editor-dialog')?.open === true", timeout=20)
            report["checks"]["journal_editor"] = await cdp.evaluate("""(() => ({
                journal: document.querySelector('#note-editor-dialog')?.classList.contains('is-journal'),
                heading: (document.querySelector('#note-editor-text')?.value || '').includes('第'),
                title: document.querySelector('#note-editor-title')?.value || '',
            }))()""")
            await cdp.evaluate("document.querySelector('#note-editor-close').click()")
            await cdp.wait_for("document.querySelector('#note-editor-dialog')?.open !== true", timeout=10)
            await cdp.wait_for("document.querySelector('#life-view .life-hero')", timeout=20)
            await cdp.evaluate("document.querySelector('#life-view [data-notebook=\"reflection\"]').click()")
            await cdp.wait_for("document.querySelector('#life-view .notebook-page')", timeout=30)
            report["checks"]["notebook_page"] = bool(await cdp.evaluate(
                "document.querySelector('#life-view .notebook-page-heading h3')?.textContent === '反思复盘'"))
            await cdp.evaluate("document.querySelector('#life-view [data-notebook-back]').click()")
            await cdp.wait_for("document.querySelector('#life-view .life-hero')", timeout=20)

            # 新建本子 = 在 00_人生路 下建一个文件夹；创建后直接进入本子。
            notebook_name = f"验收本子-{int(time.time()) % 1_000_000}"
            await cdp.evaluate("document.querySelector('#life-view [data-notebook-create]').click()")
            await cdp.wait_for("document.querySelector('#notebook-create-dialog')?.open === true", timeout=20)
            await cdp.evaluate(
                "(() => { const input=document.querySelector('#notebook-create-name');"
                f" input.value={json.dumps(notebook_name)};"
                " input.dispatchEvent(new Event('input',{bubbles:true})); })()")
            await cdp.evaluate("document.querySelector('#notebook-create-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))")
            await cdp.wait_for("document.querySelector('#notebook-create-dialog')?.open !== true", timeout=30)
            await cdp.wait_for(
                "document.querySelector('#life-view .notebook-page-heading h3')?.textContent === "
                f"{json.dumps(notebook_name)}", timeout=30)
            report["checks"]["notebook_created"] = await cdp.evaluate("""(() => ({
                title: document.querySelector('#life-view .notebook-page-heading h3')?.textContent || '',
                write: Boolean(document.querySelector('#life-view .notebook-page [data-editor-new]')),
            }))()""")
            await cdp.evaluate("document.querySelector('#life-view [data-notebook-back]').click()")
            await cdp.wait_for("document.querySelector('#life-view .life-hero')", timeout=20)
            report["checks"]["notebook_card_visible"] = bool(await cdp.evaluate(
                "[...document.querySelectorAll('#life-view .notebook-card')]"
                f".some((node) => node.textContent.includes({json.dumps(notebook_name)}))"))

            # 人生路：应用内写作保存。
            await cdp.evaluate("document.querySelector('#life-view [data-editor-new=\"life\"]').click()")
            await cdp.wait_for("document.querySelector('#note-editor-dialog')?.open === true", timeout=20)
            await cdp.evaluate("""(() => {
                const title = document.querySelector('#note-editor-title');
                const body = document.querySelector('#note-editor-text');
                title.value = '浏览器验收-反思';
                title.dispatchEvent(new Event('input', {bubbles:true}));
                body.value = '今天验证了应用内写作、工具栏和保存。';
                body.dispatchEvent(new Event('input', {bubbles:true}));
            })()""")
            await cdp.evaluate("document.querySelector('#note-editor-save').click()")
            await cdp.wait_for("document.querySelector('#note-editor-status')?.textContent.includes('已保存')", timeout=30)
            report["checks"]["editor_saved"] = True
            await cdp.evaluate("document.querySelector('#note-editor-close').click()")

            # 知识库：分享入口能打开。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"knowledge\"]').click()")
            await cdp.wait_for("location.hash === '#knowledge'", timeout=10)
            await cdp.wait_for("document.querySelector('#knowledge-view .pillar-head')", timeout=30)
            await cdp.wait_for("document.querySelector('#knowledge-view .knowledge-hero')", timeout=30)
            knowledge_home = await cdp.evaluate("""(() => ({
                ask: Boolean(document.querySelector('#knowledge-view [data-ask-deepseek]')),
                form: Boolean(document.querySelector('#knowledge-view [data-share-quick-form]')),
                notebooks: document.querySelectorAll('#knowledge-view .notebook-card').length,
            }))()""")
            report["checks"]["knowledge_home"] = knowledge_home
            if not knowledge_home.get("ask") or not knowledge_home.get("form") or knowledge_home.get("notebooks", 0) < 7:
                report["errors"].append("知识库首页提问入口不突出")
            await cdp.evaluate("document.querySelector('#knowledge-view [data-share-link]').click()")
            await cdp.wait_for("document.querySelector('#share-link-dialog')?.open === true", timeout=20)
            report["checks"]["share_dialog"] = bool(await cdp.evaluate("Boolean(document.querySelector('#share-link-url'))"))
            await cdp.evaluate("document.querySelector('#share-link-close').click()")

            # 自我档案：忆灵面板 + 苏格拉底按钮。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"self\"]').click()")
            await cdp.wait_for("location.hash === '#self'", timeout=10)
            await cdp.wait_for("document.querySelector('#self-hub [data-memory-socrates]')", timeout=30)
            report["checks"]["memory_hub"] = bool(await cdp.evaluate(
                "Boolean(document.querySelector('#self-hub [data-memory-synthesize]'))"))

            # 生活运营：AI 卡片四个入口。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"ops\"]').click()")
            await cdp.wait_for("location.hash === '#ops'", timeout=10)
            await cdp.wait_for("document.querySelector('[data-ops-ai] .ops-ai-card')", timeout=30)
            report["checks"]["ops_ai_buttons"] = int(await cdp.evaluate(
                "document.querySelectorAll('[data-ops-ai-report]').length"))
            report["js_errors"] = list(cdp.errors)
            report["ok"] = bool(
                report["checks"].get("workbench_detail", {}).get("memory")
                and not report["checks"].get("workbench_detail", {}).get("old_question")
                and report["checks"].get("widget_hidden")
                and report["checks"].get("life_home", {}).get("hero")
                and report["checks"].get("life_home", {}).get("notebooks", 0) >= 7
                and report["checks"].get("journal_editor", {}).get("journal")
                and report["checks"].get("notebook_page")
                and report["checks"].get("notebook_created", {}).get("write")
                and report["checks"].get("notebook_card_visible")
                and report["checks"].get("editor_saved")
                and report["checks"].get("knowledge_home", {}).get("ask")
                and report["checks"].get("knowledge_home", {}).get("form")
                and report["checks"].get("share_dialog")
                and report["checks"].get("memory_hub")
                and report["checks"].get("ops_ai_buttons") == 4
                and not report["errors"]
                and not cdp.errors
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="v1.0 QA 浏览器验收（不调用模型）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8899")
    parser.add_argument("--port", type=int, default=9334)
    args = parser.parse_args()
    report = asyncio.run(run(args.base_url, args.port))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
