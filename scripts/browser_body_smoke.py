"""Real-browser smoke for local body records and the evening draft line.

Drives a headless Edge session over the DevTools protocol against an isolated
QA workspace: fills weight/sleep/exercise on the journal page, saves twice to
verify the revision bump, asks the scheduler for a manual evening draft, then
checks that the draft renders the local body line. Only fictional values are
written; the personal workspace is never touched and no model is called.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))

from browser_smoke import CDP, debug_targets, find_edge  # noqa: E402


async def run(base_url: str, report_path: Path, screenshot_path: Path, port: int) -> dict:
    edge = find_edge()
    profile_dir = tempfile.mkdtemp(prefix='torch-edge-')
    process = subprocess.Popen([
        edge,
        '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
        '--disable-extensions', f'--remote-debugging-port={port}', f'--user-data-dir={profile_dir}',
        '--window-size=1440,960', f'{base_url}/#journal',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = {
        'format': 'torch-os-browser-body-smoke', 'version': 1, 'base_url': base_url,
        'headless': True, 'ok': False,
    }
    try:
        target = None
        deadline = time.monotonic() + 20
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
            await cdp.wait_for("document.readyState === 'complete'")
            await cdp.wait_for(
                "document.querySelector('#body-fields') && document.querySelector('#body-fields').disabled === false",
                timeout=30.0,
            )
            report['capability'] = await cdp.evaluate("""(() => ({
                state: document.querySelector('#body-state').textContent,
                fieldsDisabled: document.querySelector('#body-fields').disabled,
                feedback: document.querySelector('#body-feedback').textContent,
            }))()""")

            await cdp.evaluate("""(() => {
                const set = (id, value) => {
                    const node = document.querySelector(id);
                    node.value = value;
                    node.dispatchEvent(new Event('input', { bubbles: true }));
                };
                set('#body-weight', '68.5');
                set('#body-sleep', '450');
                set('#body-exercise', '30');
                return true;
            })()""")
            await cdp.wait_for("document.querySelector('#body-save').disabled === false")
            await cdp.evaluate("document.querySelector('#body-save').click()")
            await cdp.wait_for("document.querySelector('#body-feedback').textContent.includes('身体记录已保存到本地')")
            report['saved'] = await cdp.evaluate("""(() => ({
                state: document.querySelector('#body-state').textContent,
                feedback: document.querySelector('#body-feedback').textContent,
                weight: document.querySelector('#body-weight').value,
                sleep: document.querySelector('#body-sleep').value,
                exercise: document.querySelector('#body-exercise').value,
            }))()""")

            await cdp.evaluate("""(() => {
                const node = document.querySelector('#body-sleep');
                node.value = '480';
                node.dispatchEvent(new Event('input', { bubbles: true }));
                return true;
            })()""")
            await cdp.wait_for("document.querySelector('#body-save').disabled === false")
            await cdp.evaluate("document.querySelector('#body-save').click()")
            await cdp.wait_for("document.querySelector('#body-state').textContent.includes('第 2 版')")
            report['updated'] = await cdp.evaluate("""(() => ({
                state: document.querySelector('#body-state').textContent,
                sleep: document.querySelector('#body-sleep').value,
            }))()""")
            await cdp.wait_for("document.querySelector('#body-history').innerText.includes('睡眠 8 小时')")
            report['history'] = await cdp.evaluate("""(() => ({
                text: document.querySelector('#body-history').innerText,
                rows: document.querySelectorAll('#body-history .journal-history-row').length,
            }))()""")

            await cdp.evaluate("""(() => {
                const set = (id, value) => {
                    const node = document.querySelector(id);
                    node.value = value;
                    node.dispatchEvent(new Event('input', { bubbles: true }));
                };
                set('#journal-diet', '三餐正常，晚上外食');
                set('#journal-mood', '平静，有点累');
                return true;
            })()""")
            await cdp.wait_for("document.querySelector('#journal-save').disabled === false")
            await cdp.evaluate("document.querySelector('#journal-save').click()")
            await cdp.wait_for("document.querySelector('#journal-record-state').textContent.includes('已保存')")
            report['daily_fields'] = await cdp.evaluate("""(async () => {
                const day = document.querySelector('#journal-date').value;
                const response = await fetch(`/api/journal/${day}`);
                const record = await response.json();
                return {
                    status: response.status,
                    mood: record.mood,
                    diet: record.diet_summary,
                    recorded: record.recorded,
                };
            })()""")

            report['export'] = await cdp.evaluate("""(async () => {
                const csv = await fetch('/api/body/export.csv');
                const text = await csv.text();
                const json = await fetch('/api/body/export.json');
                const payload = await json.json();
                return {
                    csvStatus: csv.status,
                    csvType: csv.headers.get('content-type'),
                    head: text.split('\\n')[0],
                    jsonStatus: json.status,
                    total: payload.total,
                    buttonsEnabled: !document.querySelector('#body-export-csv').disabled
                        && !document.querySelector('#body-export-json').disabled,
                };
            })()""")

            report['run_now'] = await cdp.evaluate("""(async () => {
                const response = await fetch('/api/schedule/run-now', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ kind: 'evening' }),
                });
                return { status: response.status, body: await response.json() };
            })()""")

            await cdp.evaluate("location.hash = '#today'")
            await cdp.wait_for("!document.querySelector('#today-view').hidden")
            await cdp.evaluate("document.querySelector('#inbox-refresh').click()")
            await cdp.wait_for("document.querySelectorAll('#inbox-list details.inbox-detail').length >= 1")
            await cdp.evaluate(
                "Array.from(document.querySelectorAll('#inbox-list details.inbox-detail')).forEach((node) => { node.open = true; })"
            )
            report['inbox'] = await cdp.evaluate("""(() => ({
                text: document.querySelector('#inbox-list').innerText,
                items: document.querySelectorAll('#inbox-list .inbox-item').length,
            }))()""")

            shot = await cdp.call('Page.captureScreenshot', {'format': 'png'})
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            screenshot_path.write_bytes(base64.b64decode(shot['data']))
            report['screenshot'] = str(screenshot_path)
            body_line = '身体记录：体重 68.5 kg · 睡眠 8 小时 · 运动 30 分钟'
            report['body_line_expected'] = body_line
            report['ok'] = bool(
                not report['capability']['fieldsDisabled']
                and '已保存到本地' in report['saved']['feedback']
                and '第 1 版' in report['saved']['state']
                and '第 2 版' in report['updated']['state']
                and '体重 68.5 kg' in report['history']['text']
                and '睡眠 8 小时' in report['history']['text']
                and '天有记录' in report['history']['text']
                and report['daily_fields']['status'] == 200
                and report['daily_fields']['mood'] == '平静，有点累'
                and report['daily_fields']['diet'] == '三餐正常，晚上外食'
                and report['daily_fields']['recorded'] is True
                and report['export']['csvStatus'] == 200
                and report['export']['head'] == 'date,weight_kg,sleep_minutes,exercise_minutes'
                and report['export']['jsonStatus'] == 200
                and report['export']['total'] >= 1
                and report['export']['buttonsEnabled']
                and report['run_now']['status'] == 200
                and report['run_now']['body'].get('created') is True
                and body_line in report['inbox']['text']
            )
    finally:
        try:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        except Exception:
            pass
        shutil.rmtree(profile_dir, ignore_errors=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description='Headless Edge smoke for local body records.')
    parser.add_argument('--base-url', default='http://127.0.0.1:8767')
    parser.add_argument('--report', default='data/diagnostics/browser-0.7.0-body.json')
    parser.add_argument('--screenshot', default='artifacts/body-records-0.7-qa.png')
    parser.add_argument('--port', type=int, default=9333)
    args = parser.parse_args()
    report = asyncio.run(run(args.base_url, Path(args.report), Path(args.screenshot), args.port))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['ok'] else 1)


if __name__ == '__main__':
    main()
