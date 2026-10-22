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
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

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


def seed_qa_workspace(base_url: str) -> dict:
    """Give a disposable QA workspace the two facts the probe clicks on.

    A real vault must never be written by a read-only probe, so this only runs
    when /api/health reports runtime_label=qa.
    """
    result = {"seeded": False, "diary": None, "search_note": None}
    try:
        with httpx.Client(base_url=base_url, timeout=20) as client:
            client.get("/")
            health = client.get("/api/health")
            if health.status_code != 200 or health.json().get("runtime_label") != "qa":
                result["skipped"] = "not-qa"
                return result
            day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            note = client.get("/api/journal/note", params={"day": day})
            if note.status_code == 200 and not note.json().get("exists"):
                generated = client.post("/api/journal/note/generate", json={"day": day, "mode": "create"})
                result["diary"] = generated.status_code
            search = client.get("/api/search", params={"q": "考研", "limit": 1})
            items = search.json().get("items") if search.status_code == 200 else []
            if not items:
                created = client.post("/api/notes", json={
                    "title": "考研规划",
                    "content": "准备考研的示例笔记，用于 QA 探针的全局搜索。",
                    "type": "note",
                })
                result["search_note"] = created.status_code
            suggestions = client.get("/api/steward/inbox-suggestions")
            if suggestions.status_code == 200 and not suggestions.json().get("items"):
                seeded_inbox = []
                for title, body in (
                    ("QA 整理收件箱五步法", "第一步：列清单；第二步：分类。"),
                    ("QA 读书笔记：原则", "作者说要做减法，读完记一条。"),
                ):
                    created = client.post("/api/capture", json={
                        "title": title, "body": body, "chapter": "inbox",
                    })
                    if created.status_code == 200:
                        seeded_inbox.append(title)
                result["inbox_notes"] = seeded_inbox
            result["seeded"] = True
    except Exception as error:  # seeding is best-effort; the probe still reports what it can
        result["error"] = f"{type(error).__name__}: {error}"
    return result


async def run(base_url: str, output: Path, port: int,
              width: int = 1440, height: int = 1000):
    edge = find_edge()
    seed = seed_qa_workspace(base_url)
    profile = tempfile.mkdtemp(prefix="torch-edge-pillars-")
    process = subprocess.Popen([
        edge, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions",
        f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
        f"--window-size={width},{height}", f"{base_url}/#life",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = {"ok": False, "seed": seed, "views": {}}
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
            # 顶部「?」帮助入口：应直接打开设置里的帮助分区。
            await cdp.evaluate("document.querySelector('.panel-nav-help').click()")
            await cdp.wait_for(
                "location.hash === '#settings' && document.querySelector('[data-settings-nav=\"help\"]')?.classList.contains('active') && !document.querySelector('[data-settings-section=\"help\"]')?.hidden",
                timeout=20)
            report["help_nav"] = await cdp.evaluate(
                "document.querySelector('[data-settings-section=\"help\"] h2')?.textContent || ''")
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"life\"]').click()")
            await cdp.wait_for("location.hash === '#life'")
            report["help_hidden_outside_settings"] = await cdp.evaluate(
                "document.querySelector('#help-view')?.offsetParent === null")
            undo_note = f"QA 撤销探针 {time.time_ns()}"
            undo_note_literal = json.dumps(undo_note, ensure_ascii=False)
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
                if view == "knowledge":
                    await cdp.evaluate("document.querySelector('#knowledge-view [data-pillar-chapter=\"knowledge\"][data-chapter=\"inbox\"]')?.click()")
                    await cdp.wait_for("document.querySelector('#knowledge-view [data-inbox-suggestions]')")
                    await asyncio.sleep(0.6)
                    await cdp.screenshot(output / "knowledge-inbox.png")
                    report["inbox_suggestions"] = await cdp.evaluate(
                        "document.querySelectorAll('#knowledge-view [data-inbox-select]').length")
                    report["inbox_batch"] = await cdp.evaluate("""(() => ({
                        checkboxes: document.querySelectorAll('#knowledge-view [data-inbox-select]').length,
                        applyDisabled: Boolean(document.querySelector('#knowledge-view [data-inbox-apply]')?.disabled),
                    }))()""")
                    if report["inbox_suggestions"]:
                        await cdp.evaluate("document.querySelector('#knowledge-view [data-inbox-select-all]')?.click()")
                        await cdp.wait_for("!document.querySelector('#knowledge-view [data-inbox-apply]')?.disabled")
                        report["inbox_batch_selected"] = await cdp.evaluate(
                            "document.querySelector('#knowledge-view [data-inbox-apply]')?.textContent || ''")
                        await cdp.evaluate("document.querySelector('#knowledge-view [data-inbox-apply]')?.click()")
                        await cdp.wait_for("document.querySelector('#confirm-dialog')?.open")
                        report["inbox_batch_confirm"] = await cdp.evaluate(
                            "document.querySelector('#confirm-message')?.textContent?.slice(0, 120) || ''")
                        await cdp.screenshot(output / "knowledge-inbox-batch.png")
                        await cdp.evaluate("document.querySelector('#confirm-accept')?.click()")
                        await cdp.wait_for(
                            "document.querySelectorAll('#knowledge-view [data-inbox-select]').length === 0",
                            timeout=30)
                        report["inbox_batch_applied"] = await cdp.evaluate(
                            "document.querySelector('#toast')?.textContent || ''")
                if view == "life":
                    report["life_week"] = await cdp.evaluate("""(() => ({
                        days: document.querySelectorAll('#life-view .pillar-week-day').length,
                        done: document.querySelectorAll('#life-view .pillar-week-day.is-done').length,
                    }))()""")
                    if await cdp.evaluate("Boolean(document.querySelector('#life-view .pillar-week-day.is-done'))"):
                        await cdp.evaluate("document.querySelector('#life-view .pillar-week-day.is-done').click()")
                        await cdp.wait_for("document.querySelector('.pillar-note-dialog')", timeout=20)
                        report["life_week_click"] = await cdp.evaluate(
                            "document.querySelector('.pillar-note-dialog .meta')?.textContent || ''")
                        await cdp.evaluate("document.querySelector('.pillar-note-dialog [data-close-note]')?.click()")
                    else:
                        report["life_week_click"] = None
                    await cdp.evaluate("document.querySelector('#life-view [data-pillar-chapter=\"life\"][data-chapter=\"habit\"]')?.click()")
                    await cdp.wait_for("document.querySelector('#life-view [data-habit-panel]')")
                    await asyncio.sleep(0.6)
                    await cdp.screenshot(output / "life-habits.png")
                    report["habit_cards"] = await cdp.evaluate(
                        "document.querySelectorAll('#life-view .pillar-habit-card').length")
                if view == "ops":
                    await cdp.evaluate("document.querySelector('#ops-view [data-ops-quick=\"health\"]')?.click()")
                    await cdp.wait_for("document.querySelector('#ops-view [data-ops-form=\"health\"]')")
                    await asyncio.sleep(0.4)
                    report["ops_quick_focus"] = await cdp.evaluate(
                        "document.activeElement?.getAttribute('name') || document.activeElement?.tagName || ''")
                    await cdp.screenshot(output / "ops-health.png")
                    # 删除 → 危险色确认 → 撤销：真实写入 QA 月度表后按原 row_id 放回。
                    await cdp.evaluate("""(() => {
                        const form = document.querySelector('#ops-view [data-ops-form="health"]');
                        form.querySelector('[name="weight_kg"]').value = '66.6';
                        form.querySelector('[name="note"]').value = __NOTE__;
                        form.requestSubmit();
                    })()""".replace("__NOTE__", undo_note_literal))
                    row_has_note = ("Array.from(document.querySelectorAll('#ops-view [data-ops-table=\"health\"] tbody tr'))"
                                    ".some((tr) => tr.textContent.includes(__NOTE__))")
                    await cdp.wait_for(row_has_note.replace("__NOTE__", undo_note_literal))
                    report["ops_rows_before_delete"] = await cdp.evaluate(
                        "document.querySelectorAll('#ops-view [data-ops-table=\"health\"] tbody tr').length")
                    await cdp.evaluate("""(() => {
                        const row = Array.from(document.querySelectorAll('#ops-view [data-ops-table="health"] tbody tr'))
                            .find((tr) => tr.textContent.includes(__NOTE__));
                        row?.querySelector('[data-ops-delete]')?.click();
                    })()""".replace("__NOTE__", undo_note_literal))
                    await cdp.wait_for("document.querySelector('#confirm-dialog[open]')")
                    report["ops_delete_confirm_danger"] = await cdp.evaluate(
                        "document.querySelector('#confirm-accept')?.classList.contains('danger') || false")
                    await cdp.evaluate("document.querySelector('#confirm-accept')?.click()")
                    await cdp.wait_for(f"!({row_has_note.replace('__NOTE__', undo_note_literal)})")
                    report["ops_rows_after_delete"] = await cdp.evaluate(
                        "document.querySelectorAll('#ops-view [data-ops-table=\"health\"] tbody tr').length")
                    await cdp.wait_for("document.querySelector('#toast.show .toast-action')")
                    report["ops_undo_prompt"] = await cdp.evaluate(
                        "document.querySelector('#toast.show .toast-action')?.textContent || ''")
                    await cdp.screenshot(output / "ops-delete-undo.png")
                    await cdp.evaluate("document.querySelector('#toast.show .toast-action')?.click()")
                    await cdp.wait_for(row_has_note.replace("__NOTE__", undo_note_literal))
                    report["ops_undo_restored"] = await cdp.evaluate(
                        "document.querySelectorAll('#ops-view [data-ops-table=\"health\"] tbody tr').length")
            # 薪火管家抽屉：提案 → 健康检查 → 本地问答（不执行任何提案）。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"knowledge\"]').click()")
            await cdp.wait_for("location.hash === '#knowledge' && document.querySelector('#knowledge-view [data-steward-open]')")
            await cdp.evaluate("document.querySelector('#knowledge-view [data-steward-open]').click()")
            await cdp.wait_for("document.querySelector('.pillar-steward-dialog')")
            await cdp.wait_for("document.querySelector('.steward-section-head')", timeout=20)
            report["steward_proposals"] = await cdp.evaluate(
                "document.querySelectorAll('.pillar-steward-dialog .steward-proposal').length")
            await cdp.screenshot(output / "steward-proposals.png")
            await cdp.evaluate("document.querySelector('.pillar-steward-dialog [data-steward-tab=\"health\"]').click()")
            await cdp.wait_for("document.querySelector('.steward-health-summary')", timeout=20)
            report["steward_health"] = await cdp.evaluate("""(() => ({
                checked: document.querySelector('.steward-health-summary')?.textContent?.replace(/\\s+/g, ' ').trim() || '',
                warnings: document.querySelectorAll('.steward-health-row[data-severity="warning"]').length,
                infos: document.querySelectorAll('.steward-health-row[data-severity="info"]').length,
            }))()""")
            await cdp.screenshot(output / "steward-health.png")
            await cdp.evaluate("document.querySelector('.pillar-steward-dialog [data-steward-tab=\"ask\"]').click()")
            await cdp.wait_for("document.querySelector('.pillar-steward-dialog [data-steward-ask-form]')", timeout=20)
            await cdp.evaluate("""(() => {
                const form = document.querySelector('.pillar-steward-dialog [data-steward-ask-form]');
                form.querySelector('[name="question"]').value = '考研';
                form.requestSubmit();
            })()""")
            await cdp.wait_for("document.querySelector('.pillar-steward-dialog .steward-answer-body')", timeout=30)
            report["steward_ask"] = await cdp.evaluate("""(() => ({
                mode: document.querySelector('.pillar-steward-dialog .steward-mode')?.textContent || '',
                citations: document.querySelectorAll('.pillar-steward-dialog .steward-citation').length,
                answer: document.querySelector('.pillar-steward-dialog .steward-answer-body')?.textContent?.slice(0, 80) || '',
            }))()""")
            await cdp.screenshot(output / "steward-ask.png")
            await cdp.evaluate("document.querySelector('.pillar-steward-dialog [data-steward-close]')?.click()")
            # 健康检查默认只看需要留意，可切到全部。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"knowledge\"]').click()")
            await cdp.wait_for("document.querySelector('#knowledge-view [data-steward-open]')")
            await cdp.evaluate("document.querySelector('#knowledge-view [data-steward-open]').click()")
            await cdp.wait_for("document.querySelector('.pillar-steward-dialog')")
            await cdp.evaluate("document.querySelector('.pillar-steward-dialog [data-steward-tab=\"health\"]').click()")
            await cdp.wait_for("document.querySelector('.steward-health-filters')", timeout=20)
            report["steward_health_filter"] = await cdp.evaluate("""(() => ({
                active: document.querySelector('.steward-filter.is-active')?.textContent || '',
                rows: document.querySelectorAll('.steward-health-row').length,
            }))()""")
            await cdp.evaluate("document.querySelector('.steward-filter[data-steward-health-filter=\"all\"]')?.click()")
            report["steward_health_all_rows"] = await cdp.evaluate(
                "document.querySelectorAll('.steward-health-row').length")
            await cdp.screenshot(output / "steward-health-filtered.png")
            await cdp.evaluate("document.querySelector('.pillar-steward-dialog [data-steward-close]')?.click()")
            # 写今日日记：必须打开权威的 00_人生路/01_日记/YYYY-MM-DD.md，不能另建同名笔记。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"life\"]').click()")
            await cdp.wait_for("document.querySelector('#life-view [data-capture-chapter=\"journal\"]')")
            await cdp.evaluate("document.querySelector('#life-view [data-capture-chapter=\"journal\"]').click()")
            await cdp.wait_for("document.querySelector('.pillar-note-dialog')", timeout=30)
            report["today_journal"] = await cdp.evaluate("""(() => ({
                path: document.querySelector('.pillar-note-dialog .meta')?.textContent || '',
                title: document.querySelector('.pillar-note-dialog h2')?.textContent || '',
            }))()""")
            await cdp.screenshot(output / "journal-detail.png")
            await cdp.evaluate("document.querySelector('.pillar-note-dialog [data-close-note]')?.click()")
            # 快速捕获：默认只露出标题/正文/去向，标签与来源收进“更多选项”。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"knowledge\"]').click()")
            await cdp.wait_for("document.querySelector('#knowledge-view [data-pillar-capture]')")
            await cdp.evaluate("document.querySelector('#knowledge-view [data-pillar-capture]').click()")
            await cdp.wait_for("document.querySelector('.pillar-capture-dialog')")
            await cdp.screenshot(output / "capture-dialog.png")
            report["capture"] = await cdp.evaluate("""(() => ({
                visibleFields: Array.from(document.querySelectorAll('.pillar-capture-dialog .field > span')).filter((node) => node.offsetParent !== null).map((node) => node.textContent.trim()),
                moreCollapsed: !document.querySelector('.pillar-capture-dialog details')?.open,
                chapter: document.querySelector('.pillar-capture-dialog select[name="chapter"]')?.value || '',
            }))()""")
            await cdp.evaluate("document.querySelector('.pillar-capture-dialog [data-capture-close]')?.click()")
            # Ctrl/⌘ K 全局搜索。
            await cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }))")
            await cdp.wait_for("document.querySelector('.pillar-search-dialog')")
            report["shortcut_search"] = True
            await cdp.evaluate("""(() => {
                const input = document.querySelector('[data-global-query]');
                input.value = '考研';
                input.dispatchEvent(new Event('input', { bubbles: true }));
            })()""")
            await cdp.wait_for("document.querySelectorAll('[data-global-note]').length > 0", timeout=20)
            await cdp.evaluate("document.querySelector('[data-global-query]').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))")
            report["search_keyboard"] = await cdp.evaluate("""(() => ({
                selected: document.querySelectorAll('[data-global-note].is-selected').length,
                current: document.querySelector('[data-global-note].is-selected')?.getAttribute('aria-current') || '',
            }))()""")
            await cdp.evaluate("document.querySelector('[data-global-query]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))")
            await cdp.wait_for("document.querySelector('.pillar-note-dialog')", timeout=20)
            report["search_enter_opened"] = await cdp.evaluate(
                "document.querySelector('.pillar-note-dialog h2')?.textContent || ''")
            await cdp.evaluate("document.querySelector('.pillar-note-dialog [data-close-note]')?.click()")
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
            await cdp.evaluate("document.querySelector('.pillar-search-dialog [data-search-close]')?.click()")
            # 快捷键总览：? 打开，包含分组与条目；关闭后能用 g 组合键与 n 快速记录。
            await cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: '?', bubbles: true }))")
            await cdp.wait_for("document.querySelector('.shortcut-dialog')")
            report["shortcut_sheet"] = await cdp.evaluate("""(() => ({
                groups: document.querySelectorAll('.shortcut-dialog .shortcut-group').length,
                rows: document.querySelectorAll('.shortcut-dialog .shortcut-row').length,
                text: document.querySelector('.shortcut-dialog')?.textContent?.slice(0, 80) || '',
            }))()""")
            await cdp.screenshot(output / "shortcut-sheet.png")
            await cdp.evaluate("document.querySelector('.shortcut-dialog [data-shortcut-close]')?.click()")
            await cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'g', bubbles: true }))")
            await cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'l', bubbles: true }))")
            await cdp.wait_for("location.hash === '#life'")
            report["chord_nav"] = await cdp.evaluate("location.hash")
            await cdp.wait_for("document.querySelector('#life-view [data-pillar-capture]')")
            await cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', bubbles: true }))")
            await cdp.wait_for("document.querySelector('.pillar-capture-dialog')")
            report["shortcut_capture"] = await cdp.evaluate("Boolean(document.querySelector('.pillar-capture-dialog'))")
            await cdp.evaluate("document.querySelector('.pillar-capture-dialog [data-capture-close]')?.click()")
            # 方向键：设置侧栏移动焦点，Enter 才切换分区。
            await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=\"settings\"]').click()")
            await cdp.wait_for("location.hash === '#settings'")
            await cdp.evaluate("document.querySelector('[data-settings-nav=\"status\"]').focus()")
            await cdp.evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))")
            report["arrow_nav"] = await cdp.evaluate("document.activeElement?.dataset?.settingsNav || ''")
            # 工具目录收缩为 12 个，旧学习/身体能力标记不再出现。
            await cdp.evaluate("document.querySelector('[data-settings-nav=\"data\"]').click()")
            await cdp.wait_for("!document.querySelector('[data-settings-section=\"data\"]')?.hidden")
            await cdp.evaluate("document.querySelector('#load-tools')?.click()")
            await cdp.wait_for("document.querySelector('#tools-status')?.textContent?.includes('共 12 个只读工具')", timeout=20)
            report["tools_count"] = 12
            report["retired_capabilities"] = await cdp.evaluate("""(async () => {
                const caps = (await fetch('/api/config/status').then((response) => response.json())).capabilities;
                return ['learning', 'learning_monthly', 'body_records', 'body_export', 'body_summary']
                    .filter((key) => key in caps);
            })()""")
            report["ok"] = (all(item["chapters"] > 0 for item in report["views"].values())
                            and report["shortcut_sheet"]["rows"] >= 10
                            and report["chord_nav"] == "#life"
                            and report["shortcut_capture"] is True
                            and report["arrow_nav"] == "model"
                            and report["ops_delete_confirm_danger"] is True
                            and report["ops_undo_prompt"] == "撤销"
                            and report["ops_rows_after_delete"] == report["ops_rows_before_delete"] - 1
                            and report["ops_undo_restored"] == report["ops_rows_before_delete"]
                            and report["tools_count"] == 12
                            and report["retired_capabilities"] == [])
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
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=1000)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(run(args.base_url, output, args.port, args.width, args.height))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
