"""日历七种视图的真实浏览器探针（2026-10-11，第 4 轮验收用）。

只做只读导航：点「日历」按钮、切七种视图、翻页、开视图菜单——不改任何数据（不建任务、不勾选）。
用 --attach-port 附着到一个已经由 PowerShell 拉起的 headless Edge（本机 Python 拉不起 Edge）。
报告失败也写盘（照 `docs/QA.md` 的纪律）。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
from pathlib import Path

from browser_smoke import CDP, debug_targets

VIEWS = [
    ("year", "年视图", "document.querySelectorAll('.time-year-month').length === 12"),
    ("month", "月视图",
     "document.querySelectorAll('.time-cell').length > 0 && document.querySelectorAll('.time-cell').length % 7 === 0"),
    ("week", "周视图", "document.querySelectorAll('.time-col').length === 7"),
    ("day", "日视图", "document.querySelectorAll('.time-col').length === 1"),
    ("agenda", "日程视图", "document.querySelectorAll('.time-agenda-day').length >= 1"),
    ("multiday", "多日视图", "document.querySelectorAll('.time-col').length === 2"),
    ("multiweek", "多周视图", "document.querySelectorAll('.time-cell').length === 14"),
]


async def run(base_url: str, report_path: Path, port: int, shots_dir: Path,
              keep: list[str] | None = None) -> dict:
    report = {"format": "torch-os-browser-calendar", "version": 1, "base_url": base_url,
              "ok": False, "views": {}, "shots": []}
    shots_dir.mkdir(parents=True, exist_ok=True)
    keep = keep or []
    try:
        target = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not target:
            try:
                target = next((item for item in debug_targets(port)
                               if item.get("type") == "page" and "127.0.0.1" in item.get("url", "")), None)
            except Exception:
                pass
            if not target:
                await asyncio.sleep(0.3)
        if not target:
            raise RuntimeError("无法连接 Edge 调试端口（按 URL 找 127.0.0.1 的页面）")
        import websockets
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=20_000_000) as socket:
            cdp = CDP(socket)
            await cdp.call("Page.enable")
            await cdp.call("Runtime.enable")
            await cdp.call("Page.navigate", {"url": f"{base_url}/#workbench"})
            await cdp.wait_for("document.querySelectorAll('[data-time-page]').length === 4", timeout=25)
            await cdp.evaluate("document.querySelector('[data-time-page=\"calendar\"]').click()")
            await cdp.wait_for("document.querySelectorAll('.time-cal-view').length === 7", timeout=15)

            # 右上角控件都在（视图 = 一排 7 颗按钮，2026-10-11 用户反馈后改的）
            report["header"] = await cdp.evaluate(
                "({add: Boolean(document.querySelector('.time-cal-add-btn')),"
                " views: Array.from(document.querySelectorAll('.time-cal-view')).map(b => b.textContent),"
                " nav: Array.from(document.querySelectorAll('.time-cal-nav button')).map(b => b.textContent),"
                " more: Boolean(document.querySelectorAll('.time-cal-controls .text-button').length >= 3)})")

            for key, label, check in VIEWS:
                # 直接点那一排按钮（和用户点的一样）
                await cdp.evaluate(f"document.querySelector('.time-cal-view[data-cal-view=\"{key}\"]').click()")
                await asyncio.sleep(0.4)
                # 档位是本机持久的（上次点过 5 天就还是 5 天）：多日先点回 2 天、多周先点回 2 周再断言
                if key in ("multiday", "multiweek"):
                    want = "2 天" if key == "multiday" else "2 周"
                    await cdp.evaluate(
                        "Array.from(document.querySelectorAll('.time-cal-count'))"
                        f".find(node => node.textContent.trim() === '{want}').click()")
                    await asyncio.sleep(0.4)
                ok = bool(await cdp.evaluate(f"Boolean({check})"))
                facts = await cdp.evaluate(
                    "({title: (document.querySelector('#time-cal-title') || {}).textContent,"
                    " cells: document.querySelectorAll('.time-cell').length,"
                    " cols: document.querySelectorAll('.time-col').length,"
                    " blocks: document.querySelectorAll('.time-block').length,"
                    " chips: document.querySelectorAll('.time-chip').length,"
                    " days: document.querySelectorAll('.time-agenda-day').length,"
                    " counts: Array.from(document.querySelectorAll('.time-cal-count')).map(b => b.textContent),"
                    " active: (document.querySelector('.time-cal-view.is-active') || {}).textContent})")
                report["views"][key] = {"label": label, "ok": ok, "facts": facts}
                if key in keep:
                    await asyncio.sleep(0.3)
                    shot = await cdp.call("Page.captureScreenshot", {"format": "png"})
                    path = shots_dir / f"time-calendar-{key}.png"
                    path.write_bytes(base64.b64decode(shot["data"]))
                    report["shots"].append(str(path))

            # 翻页与「今天」都只动本机状态（不改数据）：周视图 → 下一周 → 回到今天
            await cdp.evaluate("document.querySelector('.time-cal-view[data-cal-view=\"week\"]').click()")
            await asyncio.sleep(0.3)
            before = await cdp.evaluate("document.querySelector('#time-cal-title').textContent")
            await cdp.evaluate("document.querySelectorAll('.time-cal-nav button')[2].click()")   # ›
            await asyncio.sleep(0.3)
            after = await cdp.evaluate("document.querySelector('#time-cal-title').textContent")
            await cdp.evaluate("document.querySelectorAll('.time-cal-nav button')[1].click()")   # 今天
            await asyncio.sleep(0.3)
            homed = await cdp.evaluate("document.querySelector('#time-cal-title').textContent")
            report["navigation"] = {"before": before, "next": after, "today": homed,
                                    "ok": bool(before and after and homed)}

            # 档位按钮：多日视图里点「5 天」→ 列数变 5
            await cdp.evaluate("document.querySelector('.time-cal-view[data-cal-view=\"multiday\"]').click()")
            await asyncio.sleep(0.4)
            await cdp.evaluate(
                "Array.from(document.querySelectorAll('.time-cal-count'))"
                ".find(node => node.textContent.includes('5')).click()")
            await asyncio.sleep(0.4)
            five = await cdp.evaluate("document.querySelectorAll('.time-col').length")
            report["multiday_span"] = {"cols": five, "ok": five == 5}

            report["ok"] = (all(item["ok"] for item in report["views"].values())
                            and bool(report["navigation"]["ok"]) and bool(report["multiday_span"]["ok"]))
    except Exception as error:   # 失败也写报告（文档纪律）
        report["error"] = f"{type(error).__name__}: {error}"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--report", default="data/diagnostics/browser-calendar.json")
    parser.add_argument("--attach-port", type=int, required=True)
    parser.add_argument("--shots", default="artifacts")
    parser.add_argument("--keep", default="month,week,year,agenda",
                        help="逗号分隔：给哪几个视图留截图")
    args = parser.parse_args()
    report = asyncio.run(run(args.base_url, Path(args.report), args.attach_port,
                             Path(args.shots), [item for item in args.keep.split(",") if item]))
    # 控制台可能是 GBK：报告里带 ▾ / ✓，打印用 ensure_ascii 免得最后一步自己崩掉
    print(json.dumps(report, ensure_ascii=True, indent=2)[:2000])
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
