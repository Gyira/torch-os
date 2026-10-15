"""Real-browser theme probe: does the workbench follow the dsh theme?

Drives a headless Edge over the DevTools protocol against a local service and
checks three things the unit tests cannot prove:

1. `?embed=1&theme=dark` paints dark before any host message arrives (no flash);
2. a dsh token bundle posted by the host flips the palette to dsh's own colours
   (`data-torch-host="dsh"`, page background and card surface from `--dsw-*`);
3. a bundle without the required tokens never claims a host, and a standalone
   page follows the OS colour scheme instead.

It only talks to 127.0.0.1, never calls a model and never writes user data.
The token values below are the ones dsh 0.2.0-rc.2 publishes for light and dark.
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

# dsh 0.2.0-rc.2 的别名 token（读自 @deepseek-ai/dsh-client-ui-theme）。
DSH_LIGHT = {
    '--dsw-alias-bg-base': '#ffffff',
    '--dsw-alias-bg-layer-1': '#ffffff',
    '--dsw-alias-bg-layer-2': '#ffffff',
    '--dsw-alias-bg-overlay': '#e9ecf2',
    '--dsw-alias-border-l1': '#0000000a',
    '--dsw-alias-border-l2': '#0000001a',
    '--dsw-alias-brand-primary': '#0f1115',
    '--dsw-alias-label-primary': '#0f1115',
    '--dsw-alias-label-secondary': '#61666b',
    '--dsw-alias-state-error-primary': '#ec1313',
    '--dsw-alias-state-idle-primary': '#d4d4d4',
    '--dsw-alias-state-success-primary': '#22c55e',
    '--dsw-alias-state-warn-primary': '#f59e0b',
    '--dsw-specific-sidebar-fill': '#f9fafb',
}
DSH_DARK = {
    '--dsw-alias-bg-base': '#151517',
    '--dsw-alias-bg-layer-1': '#232324',
    '--dsw-alias-bg-layer-2': '#2c2c2e',
    '--dsw-alias-bg-overlay': '#61666b',
    '--dsw-alias-border-l1': '#ffffff0f',
    '--dsw-alias-border-l2': '#ffffff1f',
    '--dsw-alias-brand-primary': '#f9fafb',
    '--dsw-alias-label-primary': '#f9fafb',
    '--dsw-alias-label-secondary': '#cfd3d6',
    '--dsw-alias-state-error-primary': '#f25a5a',
    '--dsw-alias-state-idle-primary': '#545557',
    '--dsw-alias-state-success-primary': '#22c55e',
    '--dsw-alias-state-warn-primary': '#f59e0b',
    '--dsw-specific-sidebar-fill': '#1b1b1c',
}

PROBE = """(() => {
  const root = getComputedStyle(document.documentElement);
  const card = document.querySelector('.settings-card, .metric, .panel, .profile-card');
  return {
    theme: document.documentElement.dataset.torchTheme || null,
    host: document.documentElement.dataset.torchHost || null,
    rootBg: root.backgroundColor,
    rootInk: root.color,
    cardBg: card ? getComputedStyle(card).backgroundColor : null,
    layer1: getComputedStyle(document.documentElement).getPropertyValue('--dsw-alias-bg-layer-1').trim(),
    scheme: root.colorScheme,
  };
})()"""


def rgb_channels(value: str) -> list[float]:
    """Chromium reports color-mix() results as `color(srgb r g b)` with 0..1 floats."""
    text = (value or '').strip()
    if text.startswith('color('):
        parts = text[len('color('):-1].split()
        numbers = [float(item) for item in parts[1:4]]
        return [item * 255 for item in numbers]
    cleaned = text.replace('rgb(', ' ').replace('rgba(', ' ').replace(')', ' ')
    cleaned = cleaned.replace('/', ' ').replace(',', ' ')
    return [float(item) for item in cleaned.split()[:3]]


def channel_min(rgb: str) -> int:
    return round(min(rgb_channels(rgb)))


def channel_max(rgb: str) -> int:
    return round(max(rgb_channels(rgb)))


def post_tokens(theme: str, tokens: dict) -> str:
    payload = json.dumps({'type': 'torch:theme', 'scheme': theme, 'tokens': tokens})
    return (
        "window.postMessage(" + payload + ", '*'); 'posted'"
    )


async def run(base_url: str, report_path: Path, artifact_dir: Path, port: int,
              attach_port: int | None = None) -> dict:
    """Drive a headless Edge over CDP.

    By default the probe starts its own Edge. On a machine where the process
    tree is managed (a Python child cannot keep a browser alive), start Edge
    yourself and pass `--attach-port`: the probe then only connects and
    navigates, and leaves the browser alone.
    """
    edge = find_edge()
    profile_dir = None
    process = None
    if attach_port is None:
        profile_dir = tempfile.mkdtemp(prefix='torch-theme-edge-')
        process = subprocess.Popen([
            edge,
            '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
            '--disable-extensions', f'--remote-debugging-port={port}', f'--user-data-dir={profile_dir}',
            '--window-size=1440,960', 'about:blank',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port = port
    else:
        port = attach_port
    report = {'format': 'torch-os-browser-theme', 'version': 1, 'base_url': base_url,
              'headless': True, 'attached': attach_port is not None, 'ok': False}
    shots: list[str] = []

    async def shoot(cdp: CDP, name: str):
        shot = await cdp.call('Page.captureScreenshot', {'format': 'png'})
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / name
        path.write_bytes(base64.b64decode(shot['data']))
        shots.append(str(path))
        return str(path)

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
            await cdp.call('Emulation.setDeviceMetricsOverride', {
                'width': 1180, 'height': 900, 'deviceScaleFactor': 1, 'mobile': False,
            })
            await cdp.call('Page.navigate', {'url': f'{base_url}/?embed=1&theme=dark#today'})
            await cdp.wait_for("document.readyState === 'complete'")
            await cdp.wait_for("document.documentElement.dataset.torchTheme === 'dark'")
            await cdp.wait_for("!document.querySelector('#today-view').hidden")

            # 1) 只有 URL 提示、没有任何宿主消息：必须已经是暗色，且不许自称宿主。
            report['url_hint'] = await cdp.evaluate(PROBE)
            report['url_hint']['sidebarHidden'] = await cdp.evaluate(
                "getComputedStyle(document.querySelector('.sidebar')).display === 'none'")
            await shoot(cdp, 'theme-0.94-url-hint-dark.png')

            # 2) 宿主（dsh 暗色）下发完整 token 包。
            await cdp.evaluate(post_tokens('dark', DSH_DARK))
            await cdp.wait_for("document.documentElement.dataset.torchHost === 'dsh'")
            report['host_dark'] = await cdp.evaluate(PROBE)
            await shoot(cdp, 'theme-0.94-dsh-dark-today.png')

            await cdp.evaluate("location.hash = '#habits'")
            await cdp.wait_for("!document.querySelector('#habits-view').hidden")
            report['host_dark_habits'] = await cdp.evaluate(PROBE)
            # 习惯卡片是异步加载的：先等卡片（.habit-week）出现，网格是按习惯逐行渲染的。
            await cdp.wait_for("document.querySelector('#habits-view [data-habit-grid]') !== null", timeout=30.0)
            await cdp.wait_for("document.querySelector('#habits-view .habit-week') !== null", timeout=30.0)
            cells = 0
            for _ in range(3):
                await cdp.evaluate(
                    "(() => { const b = document.querySelector('#habits-view [data-habit-grid]');"
                    " if (b && !b.disabled) { b.click(); return true; } return false; })()")
                for _ in range(12):
                    await asyncio.sleep(1.0)
                    cells = await cdp.evaluate("document.querySelectorAll('#habits-view .habit-grid-cell').length")
                    if cells:
                        break
                if cells:
                    break
            report['host_dark_habits']['gridCells'] = cells
            report['host_dark_habits']['doneCellBg'] = await cdp.evaluate(
                "(document.querySelector('#habits-view .habit-grid-cell.done')"
                " ? getComputedStyle(document.querySelector('#habits-view .habit-grid-cell.done')).backgroundColor : null)")
            await shoot(cdp, 'theme-0.94-dsh-dark-habits.png')

            await cdp.evaluate("location.hash = '#settings'")
            await cdp.wait_for("!document.querySelector('#settings-view').hidden")
            await cdp.wait_for("document.querySelector('#settings-view .settings-card') !== null")
            report['host_dark_settings'] = await cdp.evaluate(PROBE)
            await shoot(cdp, 'theme-0.94-dsh-dark-settings.png')

            await cdp.evaluate("location.hash = '#memory'")
            await cdp.wait_for("!document.querySelector('#memory-view').hidden")
            report['host_dark_memory'] = await cdp.evaluate(PROBE)
            await shoot(cdp, 'theme-0.94-dsh-dark-memory.png')

            # 3) 宿主切回亮色：同一页面不刷新也要跟着变。
            await cdp.evaluate("location.hash = '#today'")
            await cdp.wait_for("!document.querySelector('#today-view').hidden")
            await cdp.evaluate(post_tokens('light', DSH_LIGHT))
            await cdp.wait_for("document.documentElement.dataset.torchTheme === 'light'")
            report['host_light'] = await cdp.evaluate(PROBE)
            await shoot(cdp, 'theme-0.94-dsh-light-today.png')

            # 4) dsh 还没渲染出主题时只发来半包：不许打上宿主标记（否则颜色会整片失效）。
            await cdp.call('Page.navigate', {'url': f'{base_url}/?embed=1#today'})
            await cdp.wait_for("document.readyState === 'complete' && !document.querySelector('#today-view').hidden")
            await cdp.evaluate(post_tokens('dark', {'--dsw-alias-bg-base': '#151517'}))
            await asyncio.sleep(0.4)
            report['partial_tokens'] = await cdp.evaluate(PROBE)

            # 5) 独立打开（非嵌入）跟随系统：用 CDP 模拟系统暗色。
            await cdp.call('Emulation.setEmulatedMedia', {
                'media': '',
                'features': [{'name': 'prefers-color-scheme', 'value': 'dark'}],
            })
            await cdp.call('Page.navigate', {'url': f'{base_url}/#today'})
            await cdp.wait_for("document.readyState === 'complete' && !document.querySelector('#today-view').hidden")
            await cdp.wait_for("document.documentElement.dataset.torchTheme === 'dark'")
            report['standalone_os_dark'] = await cdp.evaluate(PROBE)
            report['standalone_os_dark']['sidebarVisible'] = await cdp.evaluate(
                "getComputedStyle(document.querySelector('.sidebar')).display !== 'none'")
            await shoot(cdp, 'theme-0.94-standalone-os-dark.png')

            # 6) 独立打开的亮色：这条路径必须还是我们原来的样子（不跟随 dsh）。
            #    带一个查询参数，否则同 URL 只有 hash 变化、文档不会重新加载。
            await cdp.call('Emulation.setEmulatedMedia', {
                'media': '',
                'features': [{'name': 'prefers-color-scheme', 'value': 'light'}],
            })
            await cdp.call('Page.navigate', {'url': f'{base_url}/?standalone=light#today'})
            await cdp.wait_for("document.readyState === 'complete' && !document.querySelector('#today-view').hidden")
            await cdp.wait_for("document.documentElement.dataset.torchTheme === 'light'")
            report['standalone_os_light'] = await cdp.evaluate(PROBE)
            await shoot(cdp, 'theme-0.94-standalone-light.png')

        dark_ok = (
            report['url_hint']['theme'] == 'dark'
            and report['url_hint']['host'] is None
            and channel_max(report['url_hint']['rootBg']) <= 45
            and report['url_hint']['sidebarHidden']
        )
        host_dark_ok = (
            report['host_dark']['host'] == 'dsh'
            and report['host_dark']['theme'] == 'dark'
            and report['host_dark']['rootInk'] == 'rgb(249, 250, 251)'
            and report['host_dark']['layer1'] == '#232324'
            and channel_max(report['host_dark']['rootBg']) <= 45
            and report['host_dark']['cardBg'] == 'rgb(35, 35, 36)'
            and report['host_dark']['scheme'] == 'dark'
        )
        host_light_ok = (
            report['host_light']['host'] == 'dsh'
            and report['host_light']['theme'] == 'light'
            and report['host_light']['rootInk'] == 'rgb(15, 17, 21)'
            and channel_min(report['host_light']['rootBg']) >= 225
            and report['host_light']['cardBg'] == 'rgb(255, 255, 255)'
        )
        partial_ok = (
            report['partial_tokens']['host'] is None
            and report['partial_tokens']['theme'] == 'dark'
        )
        standalone_ok = (
            report['standalone_os_dark']['theme'] == 'dark'
            and report['standalone_os_dark']['host'] is None
            and channel_max(report['standalone_os_dark']['rootBg']) <= 45
            and report['standalone_os_dark']['sidebarVisible']
        )
        standalone_light_ok = (
            report['standalone_os_light']['theme'] == 'light'
            and report['standalone_os_light']['host'] is None
            and channel_min(report['standalone_os_light']['rootBg']) >= 225
        )
        report['checks'] = {
            'url_hint_dark': dark_ok, 'host_dark': host_dark_ok, 'host_light': host_light_ok,
            'partial_tokens_safe': partial_ok, 'standalone_follows_os': standalone_ok,
            'standalone_light_intact': standalone_light_ok,
        }
        report['ok'] = all(report['checks'].values())
        report['screenshots'] = shots
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
    parser = argparse.ArgumentParser(description='Headless Edge probe for the dsh theme following.')
    parser.add_argument('--base-url', default='http://127.0.0.1:8766')
    parser.add_argument('--report', default='data/diagnostics/browser-theme-0.94.json')
    parser.add_argument('--artifacts', default='artifacts')
    parser.add_argument('--port', type=int, default=9335)
    parser.add_argument('--attach-port', type=int, default=None,
                        help='附着到已用该调试端口启动的 Edge，而不是自己拉起浏览器')
    args = parser.parse_args()
    report = asyncio.run(run(args.base_url, Path(args.report), Path(args.artifacts), args.port, args.attach_port))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['ok'] else 1)


if __name__ == '__main__':
    main()
