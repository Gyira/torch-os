"""pytest 的临时根目录固定为系统 TEMP 下的专用目录，绕开“编号目录 + pytest-current 符号链接”。

2026-10-05 踩坑：提权会话的 pytest 在 `%TEMP%\\pytest-of-<用户名>\\` 创建了 `pytest-current`
符号链接；后续清理把链接目标删掉、留下悬空链接——普通用户的进程既读不了也删不掉这个
重解析点（`PermissionError: [WinError 5]`），于是**之后每次 pytest 启动都在清理阶段崩溃**。
指定 basetemp 后 pytest 直接使用该目录，不再创建、扫描或清理那套编号目录/符号链接，
谁跑、是否提权都不再踩它。（系统 TEMP 里那个悬空的旧目录留着无妨，不要去动它。）
"""

from __future__ import annotations

import tempfile
from pathlib import Path


def pytest_configure(config) -> None:
    if not config.option.basetemp:
        config.option.basetemp = str(Path(tempfile.gettempdir()) / "torch-pytest")
