"""Read-only real-browser smoke for the profile page, usage records and chat preview.

Drives a headless Edge session over the DevTools protocol: real Chromium
rendering, real clicks and real fetches against the local service. It never
sends data to the network beyond 127.0.0.1 and never calls a model.
"""
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
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]


def find_edge() -> str:
    for candidate in EDGE_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which('msedge')
    if found:
        return found
    raise SystemExit('未找到 Microsoft Edge')


def debug_targets(port: int):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=5) as response:
        return json.loads(response.read().decode('utf-8'))


class CDP:
    def __init__(self, socket):
        self.socket = socket
        self.counter = 0

    async def call(self, method: str, params: dict | None = None):
        self.counter += 1
        message_id = self.counter
        await self.socket.send(json.dumps({'id': message_id, 'method': method, 'params': params or {}}))
        while True:
            message = json.loads(await self.socket.recv())
            if message.get('id') == message_id:
                if 'error' in message:
                    raise RuntimeError(f'{method}: {message["error"]}')
                return message.get('result', {})

    async def evaluate(self, expression: str):
        result = await self.call('Runtime.evaluate', {
            'expression': expression, 'returnByValue': True, 'awaitPromise': True,
        })
        return result.get('result', {}).get('value')

    async def wait_for(self, expression: str, timeout: float = 20.0, interval: float = 0.25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.evaluate(f'Boolean({expression})'):
                return True
            await asyncio.sleep(interval)
        raise TimeoutError(f'等待超时：{expression}')


async def run(base_url: str, report_path: Path, screenshot_path: Path, port: int,
              expect_usage: bool = False, attach_port: int | None = None,
              connect_timeout: float = 20.0) -> dict:
    """Drive a headless Edge over the DevTools protocol.

    By default the probe starts its own Edge. Where the process tree is managed
    (a Python child cannot keep a browser alive), start Edge yourself and pass
    `--attach-port`; the probe then connects and navigates on its own.
    """
    edge = find_edge()
    profile_dir = None
    process = None
    if attach_port is not None:
        port = attach_port
    else:
        profile_dir = tempfile.mkdtemp(prefix='torch-edge-')
        process = subprocess.Popen([
            edge,
            '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
            '--disable-extensions', f'--remote-debugging-port={port}', f'--user-data-dir={profile_dir}',
            '--window-size=1440,960', f'{base_url}/#profile',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = {
        'format': 'torch-os-browser-smoke', 'version': 1, 'base_url': base_url,
        'headless': True, 'attached': attach_port is not None, 'ok': False,
    }
    try:
        target = None
        deadline = time.monotonic() + connect_timeout
        while time.monotonic() < deadline:
            try:
                for item in debug_targets(port):
                    if item.get('type') == 'page' and 'webSocketDebuggerUrl' in item:
                        target = item
                        break
            except Exception:
                pass
            if target:
                break
            await asyncio.sleep(0.3)
        if not target:
            raise RuntimeError('无法连接 Edge 调试端口')
        async with websockets.connect(target['webSocketDebuggerUrl'], max_size=10_000_000) as socket:
            cdp = CDP(socket)
            await cdp.call('Page.enable')
            await cdp.call('Runtime.enable')
            if attach_port is not None:
                await cdp.call('Page.navigate', {'url': f'{base_url}/#profile'})
            await cdp.wait_for("document.readyState === 'complete'")
            # 0.96.0：工作台在启动时同时读取问候/进度/今日流程/建议，先等它读完再往下走。
            await cdp.wait_for(
                "document.querySelector('#workbench-hello')"
                " && document.querySelector('#workbench-hello').textContent !== '你好'", timeout=45)
            await cdp.wait_for("document.querySelectorAll('#profile-view .profile-card').length >= 4")
            report['profile'] = await cdp.evaluate("""(() => ({
                cards: document.querySelectorAll('#profile-view .profile-card').length,
                status: document.querySelector('#profile-status').textContent,
                firstValue: document.querySelector('#profile-view .profile-card strong')?.textContent || '',
                quickQuestions: document.querySelectorAll('#profile-view [data-ask-question]').length,
                activeMemories: document.querySelectorAll('#profile-view .profile-memory').length,
                viewVisible: !document.querySelector('#profile-view').hidden,
                poolColumns: document.querySelectorAll('#profile-view .profile-pool > div').length,
                recordMonthVisible: document.querySelector('#profile-view').innerText.includes('按原始记录月份'),
                sourceProgress: (document.querySelector('#profile-view .profile-pool > div li')?.innerText || '').includes('待确认'),
                libraryPanel: Boolean(document.querySelector('#profile-view [data-panel="library"]')),
                libraryText: (document.querySelector('#profile-view [data-panel="library"]')?.innerText || '').slice(0, 200),
                libraryNote: document.querySelector('#profile-view [data-panel="library"] .field-help')?.textContent || '',
            }))()""")
            await cdp.evaluate("document.querySelector('[data-view=\"identity\"]').click()")
            await cdp.wait_for("location.hash === '#identity' && document.activeElement && document.activeElement.id === 'page-title'")
            report['a11y_nav'] = await cdp.evaluate("""(() => ({
                skipHref: document.querySelector('.skip-link')?.getAttribute('href') || '',
                mainTabIndex: document.querySelector('#main-content')?.tabIndex,
                current: document.querySelector('[data-view="identity"]')?.getAttribute('aria-current') || '',
                oldCurrent: document.querySelector('[data-view="workbench"]')?.getAttribute('aria-current') || '',
                focused: document.activeElement?.id || '',
                title: document.querySelector('#page-title').textContent,
            }))()""")
            await cdp.evaluate("location.hash = '#profile'")
            await cdp.wait_for("location.hash === '#identity' && document.querySelector('#profile-view').hidden === false && document.querySelectorAll('#profile-view [data-ask-question]').length === 4")
            # 对话已交给 dsh（2026-10-05）：快捷问题不再切页面，只会把问题交给宿主输入框；
            # 独立打开（无宿主）时退化为提示/复制，所以这里断言“留在原页 + 有提示”。
            await cdp.evaluate("document.querySelector('#profile-view [data-ask-question]').click()")
            await cdp.wait_for("document.querySelector('#toast').innerText.length > 0")
            report['compose_handoff'] = await cdp.evaluate("""(() => ({
                hash: location.hash,
                stayed: !document.querySelector('#profile-view').hidden,
                toast: document.querySelector('#toast').innerText,
                hasChatView: Boolean(document.querySelector('#chat-view')),
            }))()""")
            report['usage'] = await cdp.evaluate("""(() => ({
                sections: Array.from(document.querySelectorAll('#profile-view .profile-panel h2')).map(node => node.textContent),
                usageItems: document.querySelectorAll('#profile-view .profile-usage li').length,
                usageNote: document.querySelector('#profile-view .usage-note')?.textContent || '',
                poolItems: document.querySelectorAll('#profile-view .profile-pool li').length,
                activityPanel: !!document.querySelector('#profile-view [data-panel="activity"]'),
                activityText: (document.querySelector('#profile-view [data-panel="activity"]')?.innerText || '').slice(0, 400),
            }))()""")
            report['low_confidence'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/chat/preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: '我是谁' }),
                });
                const payload = await response.json();
                const flags = (payload.evidence || []).map((item) => item.low_confidence);
                return {
                    status: response.status,
                    count: flags.length,
                    allBoolean: flags.every((value) => typeof value === 'boolean'),
                };
            })()""")
            # 对话页已移除，会话导出接口仍在（旧接口）：只核对接口本身，不再看按钮。
            report['conversation_export'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/export/conversations.json');
                const payload = await response.json();
                return {
                    status: response.status,
                    hasCounts: Boolean(payload.counts && typeof payload.counts.conversations === 'number'),
                };
            })()""")
            await cdp.evaluate("location.hash = '#memory'")
            await cdp.wait_for(
                "!document.querySelector('#memory-view').hidden && document.querySelectorAll('#memory-view [data-memory-filter]').length >= 1",
                timeout=30.0,
            )
            await cdp.wait_for("document.querySelectorAll('#memory-view [data-memory-select]').length >= 1", timeout=30.0)
            await cdp.evaluate("document.querySelector('#memory-view [data-memory-select]').click()")
            await cdp.wait_for("document.querySelector('#memory-batch-count').innerText.includes('已选 1 条')")
            report['memory_batch'] = await cdp.evaluate("""(() => ({
                barVisible: !document.querySelector('#memory-batch-bar').hidden,
                count: document.querySelector('#memory-batch-count').textContent,
                confirmEnabled: !document.querySelector('#memory-batch-confirm').disabled,
                rejectEnabled: !document.querySelector('#memory-batch-reject').disabled,
            }))()""")
            report['tidy'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/memories/tidy-candidates', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ dry_run: true }),
                });
                const payload = await response.json();
                return {
                    status: response.status,
                    total: payload.total,
                    updated: payload.updated,
                    notMemoryLike: payload.not_memory_like,
                    buttonEnabled: !document.querySelector('#tidy-candidates').disabled,
                };
            })()""")
            await cdp.evaluate("document.querySelector('#memory-batch-clear').click()")
            await cdp.wait_for("document.querySelector('#memory-batch-count').innerText.includes('已选 0 条')")
            await cdp.evaluate("document.querySelector('#memory-view [data-memory-filter=\"active\"]').click()")
            await cdp.wait_for("document.querySelectorAll('#memory-view .memory-card').length >= 1", timeout=30.0)
            report['memory_filters'] = await cdp.evaluate("""(() => {
                const source = document.querySelector('#memory-source-filter');
                const month = document.querySelector('#memory-month-filter');
                const before = document.querySelectorAll('#memory-view .memory-card').length;
                month.value = 'unknown';
                month.dispatchEvent(new Event('change', { bubbles: true }));
                const after = document.querySelectorAll('#memory-view .memory-card').length;
                const status = document.querySelector('#memory-status').textContent;
                month.value = '';
                month.dispatchEvent(new Event('change', { bubbles: true }));
                return {
                    sourceOptions: source.options.length,
                    monthOptions: month.options.length,
                    hasUnknown: Array.from(month.options).some((option) => option.value === 'unknown'),
                    before, after, status,
                    restored: document.querySelectorAll('#memory-view .memory-card').length,
                };
            })()""")
            await cdp.evaluate("document.querySelector('#memory-view .memory-card [data-memory-edit]').click()")
            await cdp.wait_for("document.querySelector('#detail-dialog').open === true && document.querySelector('#dialog-content #memory-edit-form') !== null")
            report['dialog_a11y'] = await cdp.evaluate("""(() => {
                const scope = document.querySelector('#dialog-content');
                const controls = Array.from(scope.querySelectorAll('input,select,textarea'));
                const unnamed = controls.filter((el) => !(el.labels && el.labels.length) && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby'));
                return { controls: controls.length, unnamed: unnamed.length, dialogLabel: document.querySelector('#detail-dialog').getAttribute('aria-labelledby') || '' };
            })()""")
            await cdp.evaluate("document.querySelector('#detail-dialog').close()")
            await asyncio.sleep(0.4)
            await cdp.evaluate("document.querySelector('#memory-view .memory-card [data-memory-usage]').click()")
            await cdp.wait_for(
                "document.querySelector('#detail-dialog').open === true && !document.querySelector('#dialog-content').innerText.includes('正在读取')",
                timeout=30.0,
            )
            report['memory_usage'] = await cdp.evaluate("""(() => ({
                title: document.querySelector('#dialog-title').textContent,
                body: document.querySelector('#dialog-content').innerText.slice(0, 400),
                rows: document.querySelectorAll('#dialog-content .history-list li').length,
                readOnly: document.querySelector('#dialog-content').innerText.includes('不会留下引用')
                    || document.querySelector('#dialog-content').innerText.includes('不再参与新回答'),
            }))()""")
            await cdp.evaluate("document.querySelector('#detail-dialog').close()")
            report['memory_search'] = await cdp.evaluate("""(() => {
                const input = document.querySelector('#memory-search');
                const before = document.querySelectorAll('#memory-view .memory-card').length;
                input.value = '不存在的筛选词xyz';
                input.dispatchEvent(new Event('input', { bubbles: true }));
                const after = document.querySelectorAll('#memory-view .memory-card').length;
                const empty = document.querySelector('#memory-list').innerText.includes('没有符合筛选');
                const clearButton = Boolean(document.querySelector('#memory-list [data-memory-search-clear]'));
                input.value = '';
                input.dispatchEvent(new Event('input', { bubbles: true }));
                const restored = document.querySelectorAll('#memory-view .memory-card').length;
                return { before, after, empty, clearButton, restored };
            })()""")
            report['memory_delete'] = await cdp.evaluate("""(() => ({
                buttons: document.querySelectorAll('#memory-view [data-memory-delete]').length,
                usageButtons: document.querySelectorAll('#memory-view [data-memory-usage]').length,
            }))()""")
            report['export'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/export/memories.md');
                const text = await response.text();
                return {
                    status: response.status,
                    type: response.headers.get('content-type'),
                    disposition: response.headers.get('content-disposition'),
                    head: text.split('\\n').slice(0, 3).join(' | '),
                    buttonsEnabled: !document.querySelector('#export-memories-md').disabled
                        && !document.querySelector('#export-memories-json').disabled,
                };
            })()""")
            await cdp.evaluate("location.hash = '#today'")
            await cdp.wait_for("!document.querySelector('#workbench-view').hidden")
            # 工作台的“提醒与草稿”列表面板已删（0.97.0，提醒将以铃铛推送回归）；
            # 这里只验证 run-now 接口本身仍然可用（草稿进库，等铃铛上线）。
            report['inbox'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/schedule/run-now', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ kind: 'weekly' }),
                });
                const created = await response.json();
                return {
                    status: response.status,
                    created: created.created,
                    kind: created.item?.kind || null,
                };
            })()""")
            await cdp.evaluate("location.hash = '#goals'")
            await cdp.wait_for(
                "!document.querySelector('#goals-view').hidden && document.querySelector('#export-planning-md').disabled === false"
            )
            report['planning_export'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/export/planning.json');
                const payload = await response.json();
                return {
                    status: response.status,
                    hasCounts: Boolean(payload.counts && typeof payload.counts.goals === 'number'),
                    buttonsEnabled: !document.querySelector('#export-planning-md').disabled
                        && !document.querySelector('#export-planning-json').disabled,
                    taskFilters: Array.from(document.querySelectorAll('#task-filter option')).map((node) => node.value),
                };
            })()""")
            # Manual task ordering: move the first open task down, then refresh from
            # the server to prove the new sort_order survived.
            await cdp.wait_for("document.querySelector('#task-list [data-task-toggle]') !== null")
            report['task_sort'] = await cdp.evaluate("""(async () => {
                const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const ids = () => [...document.querySelectorAll('#task-list article.task-row')]
                    .map((row) => row.querySelector('[data-task-toggle]')?.dataset.taskToggle).filter(Boolean);
                const select = document.querySelector('#task-sort');
                const manualAvailable = [...select.options].some((option) => option.value === 'manual' && !option.disabled);
                if (!manualAvailable) return { ok: false, reason: 'manual option disabled' };
                const defaultIds = ids();
                select.value = 'manual';
                select.dispatchEvent(new Event('change', { bubbles: true }));
                for (let i = 0; i < 40 && JSON.stringify(ids()) === JSON.stringify(defaultIds); i += 1) await pause(100);
                const before = ids();
                const firstMove = document.querySelector('#task-list [data-task-move="down"]');
                if (!firstMove || before.length < 2) return { ok: false, reason: 'no movable task', before };
                const expected = [...before];
                [expected[0], expected[1]] = [expected[1], expected[0]];
                firstMove.click();
                for (let i = 0; i < 80 && JSON.stringify(ids()) !== JSON.stringify(expected); i += 1) await pause(100);
                const after = ids();
                document.querySelector('#refresh-goals').click();
                for (let i = 0; i < 80 && !(document.querySelector('#goals-status').textContent || '').includes('项任务'); i += 1) await pause(100);
                await pause(300);
                const reloaded = ids();
                return {
                    ok: JSON.stringify(after) === JSON.stringify(expected)
                        && JSON.stringify(reloaded) === JSON.stringify(after)
                        && window.localStorage.getItem('torch-os:task-sort') === 'manual',
                    defaultFirst: defaultIds.slice(0, 3),
                    before: before.slice(0, 3), expected: expected.slice(0, 3), after: after.slice(0, 3), reloaded: reloaded.slice(0, 3),
                    stored: window.localStorage.getItem('torch-os:task-sort'),
                    moveButtons: document.querySelectorAll('#task-list [data-task-move]').length,
                    named: [...document.querySelectorAll('#task-list [data-task-move]')]
                        .every((node) => (node.getAttribute('aria-label') || '').length > 0),
                };
            })()""")
            report['growth_export'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/export/growth.json');
                const payload = await response.json();
                return {
                    status: response.status,
                    hasCounts: Boolean(payload.counts && typeof payload.counts.habits === 'number'),
                };
            })()""")
            await cdp.evaluate("location.hash = '#documents'")
            await cdp.wait_for(
                "!document.querySelector('#documents-view').hidden && document.querySelector('#run-vault-audit').disabled === false"
            )
            await cdp.evaluate("document.querySelector('#run-vault-audit').click()")
            await cdp.wait_for("document.querySelector('#vault-audit-panel').innerText.includes('只读报告')")
            report['vault_audit'] = await cdp.evaluate("""(() => ({
                checked: document.querySelector('#vault-audit-panel').innerText.includes('检查'),
                ok: document.querySelector('#vault-audit-panel').innerText.includes('正常'),
                readOnly: document.querySelector('#vault-audit-panel').innerText.includes('只读报告'),
                body: document.querySelector('#vault-audit-panel').innerText.slice(0, 300),
            }))()""")
            # Wolai link report: preview the QA archive, select one eligible note and
            # audit links. This stays local, never imports, and writes only the
            # content-addressed report inside the QA workspace.
            await cdp.wait_for("document.querySelector('#import-package').options.length > 1")
            report['wolai_link_report'] = await cdp.evaluate("""(async () => {
                const packageSelect = document.querySelector('#import-package');
                if (!packageSelect.value) {
                    const option = [...packageSelect.options].find((node) => node.value);
                    if (!option) return { ok: false, reason: 'no import package' };
                    packageSelect.value = option.value;
                    packageSelect.dispatchEvent(new Event('change', { bubbles: true }));
                }
                const preview = document.querySelector('#preview-import');
                if (preview.disabled) return { ok: false, reason: 'preview stayed disabled' };
                preview.click();
                for (let i = 0; i < 80 && document.querySelectorAll('#import-file-list [data-import-path]').length === 0; i += 1) {
                    await new Promise((resolve) => setTimeout(resolve, 100));
                }
                const input = [...document.querySelectorAll('#import-file-list [data-import-path]')].find((node) => !node.disabled);
                if (!input) return { ok: false, reason: 'no eligible import file' };
                input.checked = true;
                input.dispatchEvent(new Event('change', { bubbles: true }));
                const audit = document.querySelector('#audit-import-links');
                if (audit.disabled) return { ok: false, reason: 'audit stayed disabled' };
                audit.click();
                for (let i = 0; i < 120 && !(document.querySelector('#import-link-audit-result').textContent || '').includes('附件：引用'); i += 1) {
                    await new Promise((resolve) => setTimeout(resolve, 100));
                }
                const result = document.querySelector('#import-link-audit-result');
                const text = result.textContent || '';
                return {
                    ok: text.includes('附件：引用') && text.includes('人话报告'),
                    summary: text.slice(0, 200),
                    hasLocalReport: text.includes('本地报告'),
                    hasMarkdownReport: text.includes('人话报告'),
                    escaped: !result.querySelector('script'),
                };
            })()""")
            report['attachment_plan'] = await cdp.evaluate("""(async () => {
                const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const button = document.querySelector('#plan-import-attachments');
                if (button.disabled) return { ok: false, reason: 'plan button disabled' };
                button.click();
                const result = document.querySelector('#import-attachment-plan-result');
                for (let i = 0; i < 120 && !(result.textContent || '').includes('可本地化附件'); i += 1) await pause(100);
                const text = result.textContent || '';
                const status = document.querySelector('#import-attachment-plan-status').textContent || '';
                const apply = document.querySelector('#apply-attachment-plan');
                return {
                    ok: text.includes('可本地化附件') && text.includes('不会复制附件、不改写原文')
                        && status.includes('未复制附件、未修改原文'),
                    summary: text.slice(0, 240),
                    previewOnly: text.includes('不会复制附件、不改写原文'),
                    mentionsState: text.includes('尚未导入') || text.includes('可本地化'),
                    applyDisabled: apply.disabled,
                    applyLabel: apply.textContent,
                };
            })()""")
            report['import_history'] = await cdp.evaluate("""(async () => {
                const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const button = document.querySelector('#check-import-history');
                if (button.disabled) return { ok: false, reason: 'history button disabled' };
                button.click();
                const result = document.querySelector('#import-history-result');
                for (let i = 0; i < 120 && !(result.textContent || '').includes('notes/readme.md'); i += 1) await pause(100);
                const text = result.textContent || '';
                const status = document.querySelector('#import-history-status').textContent || '';
                return {
                    ok: text.includes('notes/readme.md') && status.includes('个历史版本') && status.includes('未写入任何文件'),
                    mentionsMissing: text.includes('尚未导入或文件缺失'),
                    mentionsIndex: text.includes('文件缺失，无索引'),
                    noRestoreButtons: result.querySelectorAll('[data-history-restore]').length === 0,
                    noScanButtons: result.querySelectorAll('[data-history-scan]').length === 0,
                    status,
                    text: text.slice(0, 200),
                };
            })()""")
            report['cleanup_preview'] = await cdp.evaluate("""(async () => {
                const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const button = document.querySelector('#preview-history-cleanup');
                if (button.disabled) return { ok: false, reason: 'cleanup button disabled' };
                button.click();
                const result = document.querySelector('#import-cleanup-result');
                for (let i = 0; i < 120 && !(result.textContent || '').includes('没有任何删除按钮'); i += 1) await pause(100);
                const text = result.textContent || '';
                const status = document.querySelector('#import-cleanup-status').textContent || '';
                return {
                    ok: text.includes('可清理（预览）') && text.includes('不会删除任何文件')
                        && status.includes('未删除任何文件'),
                    previewOnly: text.includes('没有任何删除按钮'),
                    noButtons: result.querySelectorAll('button').length === 0,
                    status,
                    text: text.slice(0, 200),
                };
            })()""")
            # Share import: only the local validation path is exercised here. A real
            # share link would leave 127.0.0.1, which this smoke must never do.
            report['share_import'] = await cdp.evaluate("""(async () => {
                const input = document.querySelector('#share-url');
                const preview = document.querySelector('#share-preview');
                const apply = document.querySelector('#share-apply');
                const before = {
                    labelled: Boolean(document.querySelector('label[for="share-url"]')),
                    previewDisabled: preview.disabled,
                    applyDisabled: apply.disabled,
                };
                input.value = 'https://evil.example/share/abc123';
                input.dispatchEvent(new Event('input', { bubbles: true }));
                const enabledAfterInput = preview.disabled === false;
                preview.click();
                await new Promise((resolve) => setTimeout(resolve, 800));
                return {
                    ...before,
                    enabledAfterInput,
                    status: document.querySelector('#share-status').textContent,
                    applyStillDisabled: apply.disabled,
                    resultHidden: document.querySelector('#share-result').hidden,
                };
            })()""")
            await cdp.evaluate("location.hash = '#journal'")
            await cdp.wait_for(
                "!document.querySelector('#journal-view').hidden && document.querySelector('#journal-note-generate') && document.querySelector('#journal-note-generate').disabled === false"
            )
            await cdp.wait_for("document.querySelector('#journal-activity').innerText.length > 0")
            report['journal_export'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/export/journal.json');
                const payload = await response.json();
                return {
                    status: response.status,
                    hasCounts: Boolean(payload.counts && typeof payload.counts.days === 'number'),
                };
            })()""")
            # 月历现在住在「第二大脑 → 日记归档」：从那里的入口进，再等它写上"已记录"。
            await cdp.evaluate("location.hash = '#brain'")
            await cdp.wait_for("location.hash === '#brain'")
            await cdp.evaluate("[...document.querySelectorAll('.module-nav-item')].find((node) => node.textContent.includes('日记归档'))?.click()")
            await cdp.wait_for("document.querySelector('#journal-calendar-label').textContent.includes('已记录') && document.querySelectorAll('#journal-calendar-grid button').length > 0")
            report['journal_calendar'] = await cdp.evaluate("""(async () => {
                const label = () => document.querySelector('#journal-calendar-label').textContent;
                const pause = () => new Promise((resolve) => setTimeout(resolve, 100));
                const before = label();
                const month = before.slice(0, 7);
                const cells = document.querySelectorAll('#journal-calendar-grid [data-journal-calendar-day]').length;
                const recorded = document.querySelectorAll('#journal-calendar-grid .journal-calendar-cell.recorded').length;
                const future = document.querySelectorAll('#journal-calendar-grid .journal-calendar-cell.future').length;
                const named = [...document.querySelectorAll('#journal-calendar-grid button')].every((node) => (node.getAttribute('aria-label') || '').length > 0);
                document.querySelector('#journal-calendar-prev').click();
                for (let i = 0; i < 60 && label() === before; i += 1) await pause();
                const afterPrev = label();
                document.querySelector('#journal-calendar-current').click();
                for (let i = 0; i < 60 && label() !== before; i += 1) await pause();
                const restored = label() === before;
                const recordedCell = document.querySelector('#journal-calendar-grid .journal-calendar-cell.recorded');
                let opened = null;
                if (recordedCell) {
                    const wanted = recordedCell.dataset.journalCalendarDay;
                    recordedCell.click();
                    for (let i = 0; i < 80 && document.querySelector('#journal-date').value !== wanted; i += 1) await pause();
                    opened = document.querySelector('#journal-date').value === wanted ? wanted : null;
                }
                return { month, before, afterPrev, restored, cells, recorded, future, named, opened };
            })()""")
            # 0.98.0：今天的笔记走 Vault 文件（Obsidian 是编辑器）——一键生成按模板+当天事实落盘。
            report['journal_note'] = await cdp.evaluate("""(async () => {
                const day = document.querySelector('#journal-date').value;
                const before = await (await fetch(`/api/journal/note?day=${encodeURIComponent(day)}`)).json();
                const response = await fetch('/api/journal/note/generate', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ day, mode: 'create' }),
                });
                const result = await response.json();
                const after = await (await fetch(`/api/journal/note?day=${encodeURIComponent(day)}`)).json();
                return {
                    status: response.status,
                    day,
                    beforeExists: before.exists,
                    afterExists: after.exists,
                    hasTemplate: after.content.includes('每日三问'),
                    hasSummary: after.content.includes('## 自动汇总'),
                    relpath: after.relpath,
                };
            })()""")
            await cdp.evaluate("location.hash = '#settings'")
            await cdp.wait_for(
                "!document.querySelector('#settings-view').hidden && document.querySelector('#run-diagnostics').disabled === false"
            )
            report['settings_shell'] = await cdp.evaluate("""(async () => {
                const health = await (await fetch('/api/health')).json();
                return {
                navItems: document.querySelectorAll('#settings-view [data-settings-nav]').length,
                cards: document.querySelectorAll('#settings-view .settings-card').length,
                statusVisible: document.querySelector('[data-settings-section="status"]').hidden === false,
                modelHidden: document.querySelector('[data-settings-section="model"]').hidden,
                serviceLabel: document.querySelector('#service-label').textContent,
                serviceVersion: document.querySelector('#service-version').textContent,
                versionMatches: document.querySelector('#service-version').textContent === 'v' + health.version,
            }; })()""")
            await cdp.evaluate("document.querySelector('[data-settings-nav=\"appearance\"]').click()")
            await cdp.wait_for("document.querySelector('[data-settings-section=\"appearance\"]').hidden === false && document.querySelectorAll('#nav-order-list [data-nav-move]').length >= 4")
            report['nav_order'] = await cdp.evaluate("""(async () => {
                const pause = () => new Promise((resolve) => setTimeout(resolve, 120));
                const firstView = () => document.querySelector('.sidebar .nav-item')?.dataset.view;
                const before = firstView();
                const moveButton = document.querySelector('#nav-order-list li:nth-child(1) [data-nav-move="1"]');
                const movedKey = moveButton.dataset.navKey;
                moveButton.click();
                await pause();
                const after = firstView();
                const storedAfterMove = window.localStorage.getItem('torch-os:nav-order');
                const feedbackAfterMove = document.querySelector('#nav-order-feedback').textContent;
                const focusAfterMove = {
                    key: document.activeElement?.dataset?.navKey || null,
                    move: document.activeElement?.dataset?.navMove || null,
                    inList: document.activeElement?.closest?.('#nav-order-list') !== null,
                };
                document.querySelector('#nav-order-reset').click();
                await pause();
                return {
                    before,
                    after,
                    movedKey,
                    focusAfterMove,
                    feedbackAfterMove,
                    restored: firstView(),
                    sidebarAfterReset: [...document.querySelectorAll('.sidebar .nav-item')].map((node) => node.dataset.view).slice(0, 4),
                    rows: document.querySelectorAll('#nav-order-list li').length,
                    storedAfterMove: Boolean(storedAfterMove),
                    storedAfterReset: window.localStorage.getItem('torch-os:nav-order'),
                    feedback: document.querySelector('#nav-order-feedback').textContent,
                };
            })()""")
            await cdp.evaluate("document.querySelector('[data-settings-nav=\"data\"]').click()")
            await cdp.wait_for("document.querySelector('[data-settings-section=\"data\"]').hidden === false && document.activeElement.id === 'settings-data-heading'")
            await cdp.evaluate("document.querySelector('#run-diagnostics').click()")
            await cdp.wait_for("document.querySelector('#diagnostics-panel').innerText.includes('数据库完整性')")
            await cdp.wait_for("document.querySelector('#schedule-status').textContent.includes('调度')")
            report['diagnostics'] = await cdp.evaluate("""(() => ({
                integrityOk: document.querySelector('#diagnostics-panel').innerText.includes('ok（通过）'),
                counts: document.querySelector('#diagnostics-panel').innerText.includes('资料（可用）'),
                vault: document.querySelector('#diagnostics-panel').innerText.includes('Vault Markdown'),
                backupVerified: document.querySelector('#diagnostics-panel').innerText.includes('最近备份校验'),
                schedule: document.querySelector('#schedule-status').textContent,
                body: document.querySelector('#diagnostics-panel').innerText.slice(0, 400),
            }))()""")
            await cdp.wait_for("document.querySelector('#load-schema').disabled === false")
            await cdp.evaluate("document.querySelector('#load-schema').click()")
            await cdp.wait_for("document.querySelector('#schema-summary').innerText.includes('数据表')")
            report['schema_export'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/schema.json');
                const payload = await response.json();
                const markdown = await fetch('/api/schema.md');
                const text = await markdown.text();
                return {
                    tables: payload.database.tables,
                    ftsHelpers: payload.database.fts_helpers,
                    groups: Object.keys(payload.groups).length,
                    hasNote: payload.note.includes('没有笔记正文'),
                    summaryShows: document.querySelector('#schema-summary').innerText.includes('张'),
                    downloadEnabled: document.querySelector('#download-schema-md').disabled === false,
                    markdownStatus: markdown.status,
                    markdownHasTables: text.includes('表清单') && text.includes('`memories`'),
                };
            })()""")
            await cdp.wait_for("document.querySelector('#load-tools').disabled === false")
            await cdp.evaluate("document.querySelector('#load-tools').click()")
            await cdp.wait_for("document.querySelector('#tools-summary').innerText.includes('只读')")
            report['tool_catalog'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/tools');
                const payload = await response.json();
                return {
                    count: payload.count,
                    readOnly: payload.read_only,
                    names: payload.tools.map((tool) => tool.name),
                    allGet: payload.tools.every((tool) => tool.method === 'GET'),
                    note: payload.note.includes('永远不会'),
                    summaryShows: document.querySelector('#tools-summary').innerText.includes('GET /api/tools/today'),
                    bridgeHint: (document.querySelector('#tools-help')?.innerText || '').includes('mcp_bridge.py'),
                };
            })()""")
            await cdp.wait_for("document.querySelector('#run-bridge-selftest').disabled === false")
            await cdp.evaluate("document.querySelector('#run-bridge-selftest').click()")
            await cdp.wait_for(
                "document.querySelector('#bridge-selftest-status').textContent.includes('桥自检通过')"
                " || document.querySelector('#bridge-selftest-status').textContent.includes('桥自检未通过')"
            )
            report['bridge_selftest'] = await cdp.evaluate("""(() => {
                const text = document.querySelector('#bridge-selftest-status').textContent;
                return { ok: text.includes('桥自检通过'), text };
            })()""")
            await cdp.evaluate("document.querySelector('[data-settings-nav=\"audit\"]').click()")
            await cdp.wait_for("document.querySelector('[data-settings-section=\"audit\"]').hidden === false && document.querySelector('#audit-summary-panel').innerText.includes('条本地操作')")
            report['audit_summary'] = await cdp.evaluate("""(() => ({
                text: document.querySelector('#audit-summary-panel').innerText.slice(0, 300),
                hasCloudLine: document.querySelector('#audit-summary-panel').innerText.includes('云端回答'),
                hasNote: document.querySelector('#audit-summary-panel').innerText.includes('不含正文'),
                hasLabels: /（[a-z_]+）/.test(document.querySelector('#audit-summary-panel').innerText),
            }))()""")
            report['export_bundle'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/export/all.zip');
                const buffer = await response.arrayBuffer();
                return {
                    status: response.status,
                    type: response.headers.get('content-type'),
                    bytes: buffer.byteLength,
                    buttonEnabled: !document.querySelector('#download-export-bundle').disabled,
                };
            })()""")
            await cdp.evaluate("document.querySelector('[data-settings-nav=\"backup\"]').click()")
            await cdp.wait_for("document.querySelector('[data-settings-section=\"backup\"]').hidden === false && document.querySelector('#preview-retention').disabled === false")
            await cdp.evaluate("document.querySelector('#preview-retention').click()")
            await cdp.wait_for("document.querySelector('#retention-panel').hidden === false && document.querySelector('#retention-panel').innerText.includes('共')")
            report['retention'] = await cdp.evaluate("""(() => ({
                text: document.querySelector('#retention-panel').innerText.slice(0, 300),
                readOnly: document.querySelector('#retention-panel').innerText.includes('不会删除'),
                keepCount: document.querySelector('#backup-keep-count').value,
            }))()""")
            await cdp.wait_for("document.querySelector('#list-restored').disabled === false")
            await cdp.evaluate("document.querySelector('#list-restored').click()")
            await cdp.wait_for("document.querySelector('#restored-panel').hidden === false && document.querySelector('#restored-panel').innerText.length > 10")
            report['restored'] = await cdp.evaluate("""(() => ({
                text: document.querySelector('#restored-panel').innerText.slice(0, 300),
                readOnly: document.querySelector('#restored-panel').innerText.includes('不删除') || document.querySelector('#restored-panel').innerText.includes('不会删除'),
                empty: document.querySelector('#restored-panel').innerText.includes('还没有恢复副本'),
                verifyButtons: document.querySelectorAll('#restored-panel [data-verify-restored]').length,
            }))()""")
            if report['restored']['verifyButtons'] >= 1:
                await cdp.evaluate("document.querySelector('#restored-panel [data-verify-restored]').click()")
                await cdp.wait_for("document.querySelector('#restored-panel [data-verify-status]').innerText.length > 0")
                report['restored']['verifyStatus'] = await cdp.evaluate(
                    "document.querySelector('#restored-panel [data-verify-status]').innerText.slice(0, 200)")
            # 一键恢复演练：备份 → 恢复副本 → 只读验证 → 报告（只在 data/restored 内操作）。
            await cdp.wait_for("document.querySelector('#run-restore-drill').disabled === false")
            await cdp.evaluate("document.querySelector('#run-restore-drill').click()")
            await cdp.wait_for("document.querySelector('#confirm-dialog').open === true")
            await cdp.evaluate("document.querySelector('#confirm-accept').click()")
            await cdp.wait_for("document.querySelector('#drill-summary').innerText.includes('只读验证')", timeout=90.0)
            report['restore_drill'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/backups/drill/last.md');
                const text = await response.text();
                const reports = await (await fetch('/api/backups/drill/reports')).json();
                const firstName = reports.reports?.[0]?.name;
                const single = firstName ? await fetch(`/api/backups/drill/reports/${firstName}`) : null;
                return {
                    summary: document.querySelector('#drill-summary').innerText.slice(0, 300),
                    verified: document.querySelector('#drill-summary').innerText.includes('verified'),
                    integrity: document.querySelector('#drill-summary').innerText.includes('integrity ok'),
                    status: document.querySelector('#drill-status').textContent,
                    downloadEnabled: document.querySelector('#download-drill-report').disabled === false,
                    reportStatus: response.status,
                    reportHasTitle: text.includes('恢复演练报告（一键流程）'),
                    reportKeepsBoundary: text.includes('不覆盖当前库'),
                    reportsCount: reports.count,
                    historyShown: document.querySelector('#drill-reports').hidden === false
                        && document.querySelectorAll('#drill-reports [data-drill-report]').length >= 1,
                    singleStatus: single ? single.status : 0,
                };
            })()""")
            await cdp.evaluate("document.querySelector('[data-settings-nav=\"model\"]').click()")
            await cdp.wait_for("document.querySelector('[data-settings-section=\"model\"]').hidden === false")
            report['model_key'] = await cdp.evaluate("""(() => ({
                panelHidden: document.querySelector('#model-key-panel').hidden,
                keyFieldType: document.querySelector('#model-key-input').type,
                hintMentionsEnv: document.querySelector('#model-key-panel').innerText.includes('MODEL_API_KEY'),
                modelBadge: document.querySelector('#model-status-badge').textContent,
                modelRows: document.querySelectorAll('#model-status-panel .settings-list > div').length,
            }))()""")
            await cdp.evaluate("document.querySelector('[data-settings-nav=\"obsidian\"]').click()")
            await cdp.wait_for(
                "document.querySelector('[data-settings-section=\"obsidian\"]').hidden === false"
                " && document.querySelector('#vault-connection-body').innerText.includes('Markdown 文件')"
            )
            await cdp.evaluate("document.querySelector('#vault-preview-scan').click()")
            await cdp.wait_for("document.querySelector('#vault-scan-preview').hidden === false")
            report['vault_connection'] = await cdp.evaluate("""(() => ({
                badges: document.querySelectorAll('#vault-connection-body .vault-badge').length,
                pathShown: (document.querySelector('#vault-connection-body .vault-path')?.textContent || '').length > 0,
                preview: document.querySelector('#vault-scan-preview').innerText.slice(0, 300),
                previewReadOnly: document.querySelector('#vault-scan-preview').innerText.includes('只读预览'),
                formEnabled: document.querySelector('#vault-save').disabled === false,
                readOnlyChoice: document.querySelector('#vault-read-only').checked,
                modeConsistent: document.querySelector('#vault-connection-body').innerText.includes('只读安全模式')
                    === document.querySelector('#vault-read-only').checked,
            }))()""")
            await cdp.evaluate("document.querySelector('#vault-onboarding').click()")
            await cdp.wait_for("document.querySelector('#vault-report').hidden === false && document.querySelector('#vault-report-download') !== null")
            report['vault_onboarding'] = await cdp.evaluate("""(() => ({
                checks: document.querySelectorAll('#vault-report .extract-list li').length,
                steps: document.querySelector('#vault-report').innerText.includes('建议步骤'),
                noNoteText: document.querySelector('#vault-report').innerText.includes('只有统计，不含任何正文'),
                download: document.querySelector('#vault-report-download').textContent,
                markdownStatus: 0,
            }))()""")
            report['vault_onboarding']['markdownStatus'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/vault/onboarding.md');
                const text = await response.text();
                return { status: response.status, hasTitle: text.includes('接入演练报告'), hasSteps: text.includes('建议步骤') };
            })()""")
            await cdp.evaluate("location.hash = '#bazi'")
            # 0.96 起八字是「数字分身」下的分区：深链接仍然可用，hash 会被规范化为模块名。
            await cdp.wait_for("location.hash === '#identity' && document.querySelector('#bazi-view').hidden === false && document.querySelector('#bazi-save').disabled === false")
            await cdp.evaluate("""(() => {
                document.querySelector('#bazi-birth-date').value = '1990-01-01';
                document.querySelector('#bazi-birth-time').value = '12:00';
                document.querySelector('#bazi-form').requestSubmit();
            })()""")
            # Wait for this save to finish: the chart may already be rendered from a
            # previous profile, and the clear button stays disabled while saving.
            await cdp.wait_for("document.querySelector('#bazi-status').textContent.includes('已保存在本机')")
            await cdp.wait_for("document.querySelector('#bazi-clear').disabled === false && document.querySelector('#bazi-chart').innerText.includes('己巳')")
            report['bazi'] = await cdp.evaluate("""(() => ({
                saved: document.querySelector('#bazi-updated').textContent.includes('保存于'),
                pillars: [...document.querySelectorAll('#bazi-chart .bazi-table tbody tr:first-child td')].map((node) => node.textContent),
                dayMaster: document.querySelector('#bazi-chart .bazi-summary strong')?.textContent,
                elements: document.querySelectorAll('#bazi-chart .bazi-element').length,
                reflection: document.querySelector('#bazi-chart').innerText.includes('可以拿来问自己的问题'),
                offlineNote: document.querySelector('#bazi-chart').innerText.includes('不会把出生资料发送'),
                status: document.querySelector('#bazi-status').textContent,
            }))()""")
            await cdp.evaluate("document.querySelector('#bazi-clear').click()")
            await cdp.wait_for("document.querySelector('#confirm-dialog').open === true")
            await cdp.evaluate("document.querySelector('#confirm-accept').click()")
            await cdp.wait_for("document.querySelector('#bazi-updated').textContent.includes('还没有保存')")
            report['bazi']['cleared'] = await cdp.evaluate(
                "document.querySelector('#bazi-chart').innerText.includes('填好出生日期与时间')")
            await cdp.evaluate("location.hash = '#journal'")
            # 每日记录现在是工作台（stacked 模块）里的分区，hash 规范化为模块名。
            await cdp.evaluate("location.hash = '#workbench'")
            await cdp.wait_for("location.hash === '#workbench' && document.querySelector('#workbench-weather') && document.querySelector('#workbench-weather').innerText.length > 0")
            report['workbench2'] = await cdp.evaluate("""(() => ({
                mottos: document.querySelectorAll('.workbench-motto-line').length,
                heroVisible: Boolean(document.querySelector('#workbench-hero') && !document.querySelector('#workbench-hero').hidden),
                clock: (document.querySelector('.clock-time')?.textContent || '').length,
                weather: document.querySelector('#workbench-weather').innerText.includes('°'),
                # 时间管理模块（2026-10-08 起替换「今日待办」）：任务行 + 模块侧栏；习惯打卡已进模块内页。
                tasks: document.querySelectorAll('#time-board .time-task').length,
                moduleSide: document.querySelectorAll('#time-sidebar .time-side-item').length,
                daily: document.querySelectorAll('#time-habits-list .habit-check').length,
                noSuggestions: document.querySelector('#suggestion-list') === null,
            }))()""")
            report['body_summary'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/body/summary.json?period=month');
                const text = await response.text();
                return {
                    status: response.status,
                    hasSchema: text.includes('"schema"'),
                    hasWarning: text.includes('不做健康评价'),
                    leaksWeight: text.includes('weight_kg'),
                    buttonsEnabled: !document.querySelector('#body-summary-json').disabled && !document.querySelector('#body-summary-md').disabled,
                    text: text.slice(0, 300),
                };
            })()""")
            await cdp.evaluate("location.hash = '#habits'")
            await cdp.wait_for("location.hash === '#identity' && document.querySelector('#habits-view').hidden === false && document.querySelector('#habits-view [data-habit-grid]') !== null")
            await cdp.wait_for("document.querySelector('#habits-view .habit-week') !== null")
            await cdp.evaluate("document.querySelector('#habits-view [data-habit-grid]').click()")
            await cdp.wait_for("document.querySelectorAll('#habits-view .habit-grid-cell').length >= 30")
            report['habit_grid'] = await cdp.evaluate("""(() => ({
                cells: document.querySelectorAll('#habits-view .habit-grid-cell').length,
                labeled: document.querySelectorAll('#habits-view .habit-grid-cell[aria-label]').length,
                note: (document.querySelector('#habits-view [data-habit-grid-panel]')?.innerText || '').slice(0, 200),
                weekLine: (document.querySelector('#habits-view .habit-week')?.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 160),
            }))()""")
            # 学习页在 0.96.3 已从界面移除（学习写进日记，后端接口保留），探针不再访问它。
            await cdp.evaluate("location.hash = '#help'")
            await cdp.wait_for("location.hash === '#settings' && document.querySelector('#help-view').hidden === false && document.querySelectorAll('#help-view .help-section').length >= 5")
            report['help'] = await cdp.evaluate("""(() => {
                const total = document.querySelectorAll('#help-view .help-item').length;
                const input = document.querySelector('#help-search');
                input.value = '备份';
                input.dispatchEvent(new Event('input', { bubbles: true }));
                const filtered = document.querySelectorAll('#help-view .help-item').length;
                const status = document.querySelector('#help-status').textContent;
                input.value = '';
                input.dispatchEvent(new Event('input', { bubbles: true }));
                return {
                    sections: document.querySelectorAll('#help-view .help-section').length,
                    total, filtered, status,
                    restored: document.querySelectorAll('#help-view .help-item').length,
                };
            })()""")
            report['a11y_audit'] = await cdp.evaluate("""(() => {
                const controls = Array.from(document.querySelectorAll('input,select,textarea'));
                const unnamed = controls.filter((el) => {
                    if (el.type === 'hidden') return false;
                    const label = (el.getAttribute('aria-label') || '').trim();
                    const labelledBy = el.getAttribute('aria-labelledby');
                    return !(el.labels && el.labels.length) && !label && !labelledBy;
                });
                const status = Array.from(document.querySelectorAll('[class*="status-message"]'));
                const silent = status.filter((el) => !el.getAttribute('role') && !el.getAttribute('aria-live'));
                const namelessButtons = Array.from(document.querySelectorAll('button')).filter((el) => !el.textContent.trim() && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby'));
                return {
                    controls: controls.length,
                    unnamed: unnamed.length,
                    unnamedList: unnamed.slice(0, 5).map((el) => el.id || el.name || el.tagName),
                    status: status.length,
                    silent: silent.length,
                    silentList: silent.slice(0, 5).map((el) => el.id || el.className),
                    buttons: document.querySelectorAll('button').length,
                    namelessButtons: namelessButtons.length,
                    namelessList: namelessButtons.slice(0, 5).map((el) => el.id || el.className),
                };
            })()""")
            shot = await cdp.call('Page.captureScreenshot', {'format': 'png'})
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            screenshot_path.write_bytes(base64.b64decode(shot['data']))
            report['screenshot'] = str(screenshot_path)
            # Embed mode: the whole workbench must render inside the local dsh
            # sidebar panel and switch pages without a reload. Boot on another
            # module first, then let the host message drive it to the workbench's
            # journal section — the exact path the dsh sidebar plugin uses.
            await cdp.call('Page.navigate', {'url': f'{base_url}/?embed=1#profile'})
            await cdp.wait_for("document.body.classList.contains('embed-mode')")
            await cdp.wait_for("location.hash === '#identity'")
            report['embed_mode'] = await cdp.evaluate("""(() => {
                const sidebar = document.querySelector('.sidebar');
                window.dispatchEvent(new MessageEvent('message', {
                    data: { type: 'torch:navigate', view: 'workbench', pane: 'journal-view' },
                }));
                return {
                    bodyClass: document.body.classList.contains('embed-mode'),
                    sidebarHidden: !sidebar || getComputedStyle(sidebar).display === 'none',
                    title: document.title,
                    activeViews: document.querySelectorAll('.active-view').length,
                };
            })()""")
            for _ in range(20):
                if await cdp.evaluate("location.hash === '#workbench'"):
                    break
                await asyncio.sleep(0.25)
            report['embed_mode']['hash'] = await cdp.evaluate('location.hash')
            report['embed_mode']['journalVisible'] = await cdp.evaluate(
                "document.querySelector('#journal-view')?.hidden === false")
            report['ok'] = bool(
                report['profile']['viewVisible']
                and report['profile']['quickQuestions'] == 4
                and report['profile']['poolColumns'] >= 3
                and report['profile']['libraryPanel']
                and '不含正文' in report['profile']['libraryNote']
                and report['profile']['recordMonthVisible']
                and report['profile']['sourceProgress']
                and report['a11y_nav']['skipHref'] == '#main-content'
                and report['a11y_nav']['mainTabIndex'] == -1
                and report['a11y_nav']['current'] == 'page'
                and report['a11y_nav']['oldCurrent'] == ''
                and report['a11y_nav']['focused'] == 'page-title'
                and '回答使用记录' in report['usage']['sections']
                and '候选池分布' in report['usage']['sections']
                and report['usage']['activityPanel']
                and '记录覆盖面' in report['usage']['activityText']
                and '窗口：' in report['usage']['activityText']
                and '回答使用：' in report['usage']['usageNote']
                and (report['usage']['usageItems'] >= 1 or not expect_usage)
                and (report['usage']['poolItems'] >= 1 or not expect_usage)
                # 对话已交给 dsh：快捷问题只交接，不再切页面，也不该再有对话页。
                and report['compose_handoff']['stayed']
                and report['compose_handoff']['hasChatView'] is False
                and bool(report['compose_handoff']['toast'])
                and report['low_confidence']['status'] == 200
                and report['low_confidence']['count'] >= 1
                and report['low_confidence']['allBoolean']
                and report['conversation_export']['status'] == 200
                and report['conversation_export']['hasCounts']
                and report['memory_usage']['title'] == '记忆使用记录'
                and (report['memory_usage']['rows'] >= 1 or '还没有被回答引用过' in report['memory_usage']['body'])
                and report['memory_search']['after'] == 0
                and report['memory_search']['empty']
                and report['memory_search']['clearButton']
                and report['memory_search']['restored'] == report['memory_search']['before']
                and report['memory_delete']['buttons'] >= 1
                and report['export']['status'] == 200
                and 'attachment' in (report['export']['disposition'] or '')
                and '薪火OS 记忆导出' in report['export']['head']
                and report['export']['buttonsEnabled']
                and report['inbox']['status'] == 200
                and report['planning_export']['status'] == 200
                and report['planning_export']['hasCounts']
                and report['planning_export']['buttonsEnabled']
                and 'due_today' in report['planning_export']['taskFilters']
                and 'overdue' in report['planning_export']['taskFilters']
                and report['task_sort']['ok']
                and report['task_sort']['named']
                and report['task_sort']['moveButtons'] >= 2
                and report['growth_export']['status'] == 200
                and report['growth_export']['hasCounts']
                and report['vault_audit']['checked']
                and report['vault_audit']['ok']
                and report['vault_audit']['readOnly']
                and report['wolai_link_report']['ok']
                and report['wolai_link_report']['hasLocalReport']
                and report['wolai_link_report']['escaped']
                and report['attachment_plan']['ok']
                and report['attachment_plan']['previewOnly']
                and report['attachment_plan']['mentionsState']
                and report['attachment_plan']['applyDisabled']
                and '\u6267\u884c\u9644\u4ef6\u672c\u5730\u5316' == report['attachment_plan']['applyLabel']
                and report['import_history']['ok']
                and report['import_history']['mentionsMissing']
                and report['import_history']['mentionsIndex']
                and report['import_history']['noRestoreButtons']
                and report['import_history']['noScanButtons']
                and report['cleanup_preview']['ok']
                and report['cleanup_preview']['previewOnly']
                and report['cleanup_preview']['noButtons']
                and report['share_import']['labelled']
                and report['share_import']['previewDisabled']
                and report['share_import']['enabledAfterInput']
                and report['share_import']['applyDisabled']
                and '只接受 chat.deepseek.com' in report['share_import']['status']
                and report['share_import']['applyStillDisabled']
                and report['share_import']['resultHidden']
                and report['journal_export']['status'] == 200
                and report['journal_export']['hasCounts']
                and report['journal_calendar']['named']
                and report['journal_calendar']['cells'] >= 1
                and report['journal_calendar']['recorded'] >= 1
                and report['journal_calendar']['afterPrev'] != report['journal_calendar']['before']
                and report['journal_calendar']['restored']
                and bool(report['journal_calendar']['opened'])
                and report['journal_note']['status'] == 200
                and report['journal_note']['afterExists']
                and report['journal_note']['hasTemplate']
                and report['journal_note']['hasSummary']
                and report['journal_note']['relpath'].startswith('02-Life/journal/')
                and report['diagnostics']['integrityOk']
                and report['diagnostics']['counts']
                and report['diagnostics']['vault']
                and report['diagnostics']['backupVerified']
                and '调度' in report['diagnostics']['schedule']
                and report['schema_export']['tables'] >= 23
                and report['schema_export']['ftsHelpers'] >= 5
                and report['schema_export']['groups'] >= 7
                and report['schema_export']['hasNote']
                and report['schema_export']['summaryShows']
                and report['schema_export']['downloadEnabled']
                and report['schema_export']['markdownStatus'] == 200
                and report['schema_export']['markdownHasTables']
                and report['tool_catalog']['count'] == 13
                and report['tool_catalog']['readOnly']
                and report['tool_catalog']['allGet']
                and report['tool_catalog']['note']
                and report['tool_catalog']['bridgeHint']
                and report['bridge_selftest']['ok']
                and report['tool_catalog']['summaryShows']
                and report['tool_catalog']['names'] == ['today', 'memories', 'goals', 'habits', 'learning', 'journal', 'profile', 'vault', 'body', 'documents', 'imports', 'vault_audit', 'identity']
                and report['audit_summary']['hasCloudLine']
                and report['audit_summary']['hasNote']
                and report['audit_summary']['hasLabels']
                and report['export_bundle']['status'] == 200
                and 'zip' in (report['export_bundle']['type'] or '')
                and report['export_bundle']['bytes'] > 0
                and report['export_bundle']['buttonEnabled']
                and report['retention']['readOnly']
                and report['restored']['readOnly']
                and (report['restored']['empty'] or report['restored']['verifyButtons'] >= 1)
                and report['restore_drill']['verified']
                and report['restore_drill']['integrity']
                and '演练通过' in report['restore_drill']['status']
                and report['restore_drill']['downloadEnabled']
                and report['restore_drill']['reportStatus'] == 200
                and report['restore_drill']['reportHasTitle']
                and report['restore_drill']['reportKeepsBoundary']
                and report['restore_drill']['reportsCount'] >= 1
                and report['restore_drill']['historyShown']
                and report['restore_drill']['singleStatus'] == 200
                and report['model_key']['panelHidden'] is True
                and report['model_key']['keyFieldType'] == 'password'
                and report['model_key']['hintMentionsEnv']
                and report['model_key']['modelRows'] >= 4
                and report['vault_connection']['badges'] == 3
                and report['vault_connection']['pathShown']
                and '新增' in report['vault_connection']['preview']
                and report['vault_connection']['previewReadOnly']
                and report['vault_connection']['formEnabled']
                and report['vault_connection']['modeConsistent']
                and report['vault_onboarding']['checks'] >= 8
                and report['vault_onboarding']['steps']
                and report['vault_onboarding']['noNoteText']
                and '下载 Markdown 报告' in report['vault_onboarding']['download']
                and report['vault_onboarding']['markdownStatus']['status'] == 200
                and report['vault_onboarding']['markdownStatus']['hasTitle']
                and report['vault_onboarding']['markdownStatus']['hasSteps']
                and report['settings_shell']['navItems'] == 10
                and report['settings_shell']['cards'] >= 6
                and report['settings_shell']['statusVisible']
                and report['settings_shell']['modelHidden']
                and report['settings_shell']['versionMatches']
                and report['settings_shell']['serviceLabel'] == '本地服务已连接'
                and report['nav_order']['rows'] == 4
                and report['nav_order']['before'] != report['nav_order']['after']
                and report['nav_order']['focusAfterMove']['key'] == report['nav_order']['movedKey']
                and report['nav_order']['focusAfterMove']['move'] == '1'
                and report['nav_order']['focusAfterMove']['inList']
                and '第 2 位' in report['nav_order']['feedbackAfterMove']
                and report['nav_order']['restored'] == report['nav_order']['before']
                and report['nav_order']['sidebarAfterReset'] == ['workbench', 'identity', 'brain', 'settings']
                and report['nav_order']['storedAfterMove']
                and report['nav_order']['storedAfterReset'] is None
                and report['nav_order']['feedback']
                and report['bazi']['saved']
                and report['bazi']['pillars'] == ['己巳', '丙子', '丙寅', '甲午']
                and report['bazi']['dayMaster'] == '丙'
                and report['bazi']['elements'] == 5
                and report['bazi']['reflection']
                and report['bazi']['offlineNote']
                and '不会发送给任何模型或网络服务' in report['bazi']['status']
                and report['bazi']['cleared']
                and report['workbench2']['mottos'] >= 1
                and report['workbench2']['clock'] >= 8
                and report['workbench2']['weather']
                and report['workbench2']['noSuggestions']
                and report['workbench2']['daily'] >= 0
                and report['workbench2']['moduleSide'] >= 4  # 时间管理模块的侧栏（任务看板四项）
                and report['body_summary']['status'] == 200
                and report['body_summary']['hasSchema']
                and report['body_summary']['hasWarning']
                and report['body_summary']['leaksWeight'] is False
                and report['body_summary']['buttonsEnabled']
                and report['habit_grid']['cells'] >= 30
                and report['habit_grid']['labeled'] >= 30
                and '本周还剩' in report['habit_grid']['weekLine']
                and '个计划日' in report['habit_grid']['weekLine']
                and report['help']['sections'] >= 7
                and report['help']['total'] >= 15
                and 1 <= report['help']['filtered'] < report['help']['total']
                and report['help']['restored'] == report['help']['total']
                and report['a11y_audit']['unnamed'] == 0
                and report['a11y_audit']['silent'] == 0
                and report['a11y_audit']['namelessButtons'] == 0
                and report['dialog_a11y']['controls'] >= 2
                and report['dialog_a11y']['unnamed'] == 0
                and report['dialog_a11y']['dialogLabel'] == 'dialog-title'
                and report['memory_filters']['sourceOptions'] >= 2
                and report['memory_filters']['monthOptions'] >= 2
                and report['memory_filters']['hasUnknown']
                and report['memory_filters']['after'] <= report['memory_filters']['before']
                and report['memory_filters']['restored'] == report['memory_filters']['before']
                and report['memory_batch']['barVisible']
                and report['memory_batch']['count'] == '已选 1 条'
                and report['memory_batch']['confirmEnabled']
                and report['memory_batch']['rejectEnabled']
                and report['tidy']['status'] == 200
                and report['tidy']['buttonEnabled']
                and report['tidy']['total'] >= 0
                and report['embed_mode']['bodyClass']
                and report['embed_mode']['sidebarHidden']
                and report['embed_mode']['hash'] == '#workbench'
                and report['embed_mode']['journalVisible']
            )
    except Exception as error:
        # 失败也要留证据：以前只在成功时写盘。0.96.0–0.96.3 的探针超时因此长期静默——
        # 报告缺失，日志里却写着“探针跑完”（见 docs/DEVELOPMENT_LOG.md 0.96.4）。
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        if process is not None:
            try:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            except Exception:
                pass
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description='Headless Edge smoke for profile and chat preview.')
    parser.add_argument('--base-url', default='http://127.0.0.1:8766')
    parser.add_argument('--report', default='data/diagnostics/browser-0.5.0-smoke.json')
    parser.add_argument('--screenshot', default='artifacts/profile-preview-0.5-qa.png')
    parser.add_argument('--port', type=int, default=9333)
    parser.add_argument('--expect-usage', action='store_true',
                        help='Fail unless the profile page shows saved usage records and candidate-pool rows.')
    parser.add_argument('--attach-port', type=int, default=None,
                        help='附着到已用该调试端口启动的 Edge，而不是自己拉起浏览器')
    args = parser.parse_args()
    report = asyncio.run(run(args.base_url, Path(args.report), Path(args.screenshot), args.port,
                             args.expect_usage, args.attach_port))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['ok'] else 1)


if __name__ == '__main__':
    main()
