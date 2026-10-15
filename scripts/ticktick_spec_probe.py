"""滴答清单实测探针（2026-10-08）：连用户自己登录的浏览器，把界面「结构」看一遍。

用途：做「时间管理模块」第 2/3 轮时的参照——用户只需说一句“照滴答那套做”，
开发方自己去看真实界面，不必让用户手写提示词或截图。结论见 `docs/TICKTICK_SPEC.md`。

前置（用户做一次）：用一个**独立 Edge 实例**登录滴答清单——
  msedge.exe --remote-debugging-port=9223 --user-data-dir=<独立目录> https://ticktick.com/signin
然后用本脚本连上去。

用法：
  python scripts/ticktick_spec_probe.py shoot <名字>      截图当前界面
  python scripts/ticktick_spec_probe.py probe             列出可见交互控件（按钮/页签/菜单项）与主要区域
  python scripts/ticktick_spec_probe.py find "<文字>"      找控件（看它叫什么、在哪、什么结构）
  python scripts/ticktick_spec_probe.py click "<控件文字>"  按文字/aria-label 点一个控件
  python scripts/ticktick_spec_probe.py rowbuttons <y>     悬停某一行的 y，列出右侧浮现的图标按钮
  python scripts/ticktick_spec_probe.py tap "<x> <y>"      按坐标点（悬停才出现的控件靠它）
  python scripts/ticktick_spec_probe.py rclick "<x> <y>"   右键
  python scripts/ticktick_spec_probe.py keys "<按键>"       发按键（如 Escape）
  python scripts/ticktick_spec_probe.py js "<表达式>"       任意只读表达式
  python scripts/ticktick_spec_probe.py nav "<url>"        导航到某个地址

边界（别越界）：
- 只读：截图 + 读控件文字（菜单名/按钮名这类界面文案，截断 40 字）与结构；
- **不导出任何任务内容**、不做批量抓取、不调它的接口；
- 截图只落在 %TEMP%，不会进仓库。
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import tempfile
import time
from pathlib import Path

import websockets
import urllib.request

PORT = 9223
TMP = Path(tempfile.gettempdir())


def targets():
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5) as response:
        return json.loads(response.read().decode('utf-8'))


class CDP:
    def __init__(self, socket):
        self.socket = socket
        self.counter = 0

    async def call(self, method, params=None):
        self.counter += 1
        message_id = self.counter
        await self.socket.send(json.dumps({'id': message_id, 'method': method, 'params': params or {}}))
        while True:
            message = json.loads(await self.socket.recv())
            if message.get('id') == message_id:
                if 'error' in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get('result', {})

    async def evaluate(self, expression):
        result = await self.call('Runtime.evaluate', {
            'expression': expression, 'returnByValue': True, 'awaitPromise': True})
        return result.get('result', {}).get('value')


PROBE = """(() => {
  const pick = (nodes) => [...nodes].map((el) => ({
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role') || '',
    label: (el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().slice(0, 40),
    text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 40),
    cls: (el.className || '').toString().split(' ').slice(0, 3).join('.'),
  })).filter((item) => item.label || item.text);
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const controls = [...document.querySelectorAll(
    'button, [role=button], [role=menuitem], [role=tab], [role=option], [role=checkbox], input, select, a[href]'
  )].filter(visible);
  const headings = [...document.querySelectorAll('h1,h2,h3,[role=heading]')].filter(visible);
  return JSON.stringify({
    url: location.href,
    title: document.title,
    headings: pick(headings).slice(0, 15),
    controls: pick(controls).slice(0, 60),
    controlCount: controls.length,
    // 只给“有没有”这类信号，不取任务正文
    listSignals: {
      checkboxes: document.querySelectorAll('input[type=checkbox]').length,
      listItems: document.querySelectorAll('[role=listitem], li').length,
      dialogs: document.querySelectorAll('[role=dialog], .modal, .dialog').length,
    },
  }, null, 1);
})()"""


async def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else 'probe'
    argument = sys.argv[2] if len(sys.argv) > 2 else ''
    pages = [item for item in targets() if item.get('type') == 'page']
    page = next((item for item in pages if 'ticktick' in item.get('url', '')), None) or (pages[0] if pages else None)
    if not page:
        raise SystemExit('没找到浏览器标签页：确认那个 Edge 窗口还开着')
    async with websockets.connect(page['webSocketDebuggerUrl'], max_size=50_000_000) as socket:
        cdp = CDP(socket)
        await cdp.call('Runtime.enable')
        if action == 'shoot':
            name = argument or f'ticktick-{int(time.time())}'
            data = await cdp.call('Page.captureScreenshot', {'format': 'png'})
            path = TMP / f'{name}.png'
            path.write_bytes(base64.b64decode(data['data']))
            print('saved', path)
            return
        if action == 'rowbuttons':
            y = int(argument)
            # 先晃一下：鼠标没动位置时 Chromium 不重发 mouseMoved，React 的 hover 状态不会刷新。
            await cdp.call('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': 170, 'y': y - 8, 'button': 'none'})
            await asyncio.sleep(0.2)
            await cdp.call('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': 170, 'y': y, 'button': 'none'})
            await asyncio.sleep(0.6)
            print(await cdp.evaluate(f"""(() => {{
              const found = [...document.querySelectorAll('button')].map((el) => {{
                const r = el.getBoundingClientRect();
                return {{x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), cls: (el.className || '').toString().slice(0, 40)}};
              }}).filter((o) => o.x > 225 && o.x < 300 && Math.abs(o.y - {y}) < 14);
              return JSON.stringify(found);
            }})()"""))
            return
        if action == 'find':
            result = await cdp.evaluate(f"""(() => {{
              const wanted = {json.dumps(argument)};
              const nodes = [...document.querySelectorAll('*')];
              const hits = nodes.filter((el) => {{
                const text = (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 40);
                const label = (el.getAttribute('aria-label') || el.getAttribute('title') || '').slice(0, 40);
                return text === wanted || label === wanted || label.includes(wanted) || text.includes(wanted);
              }}).slice(0, 12).map((el) => ({{
                tag: el.tagName.toLowerCase(),
                cls: (el.className || '').toString().split(' ').slice(0, 4).join('.'),
                text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 30),
                label: el.getAttribute('aria-label') || el.getAttribute('title') || '',
                childCount: el.children.length,
              }}));
              return JSON.stringify(hits, null, 1);
            }})()""")
            print(result)
            return
        if action == 'js':
            print(await cdp.evaluate(argument))
            return
        if action == 'click':
            result = await cdp.evaluate(f"""(() => {{
              const wanted = {json.dumps(argument)};
              const nodes = [...document.querySelectorAll('button, [role=button], [role=menuitem], [role=tab], a[href], [title]')];
              const hit = nodes.find((el) => (el.innerText || '').replace(/\\s+/g, ' ').trim() === wanted)
                       || nodes.find((el) => (el.innerText || '').replace(/\\s+/g, ' ').trim().includes(wanted))
                       || nodes.find((el) => (el.getAttribute('aria-label') || el.getAttribute('title') || '').includes(wanted));
              if (!hit) return 'NOT FOUND: ' + wanted;
              hit.click();
              return 'clicked: ' + (hit.innerText || hit.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().slice(0, 40);
            }})()""")
            print(result)
            await asyncio.sleep(1.2)
        if action in ('move', 'tap', 'rclick'):
            coords = [int(part) for part in argument.replace(',', ' ').split()]
            x, y = coords[0], coords[1]
            button = 'right' if action == 'rclick' else 'left'
            await cdp.call('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': x, 'y': y, 'button': 'none'})
            await asyncio.sleep(0.4)
            if action != 'move':
                await cdp.call('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': x, 'y': y, 'button': button, 'clickCount': 1})
                await cdp.call('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': x, 'y': y, 'button': button, 'clickCount': 1})
            await asyncio.sleep(1.0)
            print(f'{action} at {x},{y}')
        if action == 'keys':
            await cdp.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': argument})
            await cdp.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': argument})
            await asyncio.sleep(0.8)
            print('sent key', argument)
        if action == 'nav':
            await cdp.call('Page.navigate', {'url': argument})
            await asyncio.sleep(3)
            print('navigated', argument)
        await asyncio.sleep(0.6)
        print(await cdp.evaluate(PROBE))


asyncio.run(main())
