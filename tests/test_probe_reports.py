"""探针与自检失败时也要落盘证据。

0.96.4 的教训：`browser_smoke.py` 与 `smoke_release.py` 以前只在成功时才写报告，
失败路径等于“无声”——0.96.0–0.96.3 两轮的真实浏览器探针一直在超时，却因为
`data/diagnostics` 里没有报告文件而被当成“已跑过”。这两条用例用连不上的端口
真实走一遍失败路径，要求报告必须以 `ok: false` + `error` 落盘。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import browser_smoke  # noqa: E402
import smoke_release  # noqa: E402


def test_browser_probe_writes_a_report_when_it_cannot_connect(tmp_path: Path) -> None:
    report_path = tmp_path / "browser-fail.json"
    with pytest.raises(RuntimeError, match="无法连接"):
        asyncio.run(browser_smoke.run(
            "http://127.0.0.1:9", report_path, tmp_path / "shot.png", 9,
            attach_port=9, connect_timeout=0.3,
        ))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["error"].startswith("RuntimeError")
    assert "无法连接" in report["error"]


def test_release_smoke_writes_a_report_when_a_request_raises(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 猴补一个会抛异常的 Client：不依赖“端口连不上”在本机是否被代理改写成 502。
    class ExplodingClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self) -> "ExplodingClient":
            return self

        def __exit__(self, *_exc) -> bool:
            return False

        def get(self, *_args, **_kwargs):
            raise httpx.ConnectError("测试用：连接失败")

        def post(self, *_args, **_kwargs):
            raise httpx.ConnectError("测试用：连接失败")

    report_path = tmp_path / "release-fail.json"
    monkeypatch.setattr(smoke_release.httpx, "Client", ExplodingClient)
    monkeypatch.setattr(sys, "argv", [
        "smoke_release.py", "--base-url", "http://127.0.0.1:9", "--report", str(report_path),
    ])
    with pytest.raises(SystemExit) as exit_info:
        smoke_release.main()
    assert exit_info.value.code == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert any("请求失败" in item and "ConnectError" in item for item in report["failures"])
