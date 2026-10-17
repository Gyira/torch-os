"""双击安装入口不能悄悄坏掉：`安装.cmd` 得在、得是纯 ASCII、得真的把 setup.ps1 跑起来。

2026-10-17：给不想敲命令的人加的一键入口。两条硬约束——cmd.exe 按系统代码页读 .cmd，
所以这个文件一写中文（连同 BOM）在双击时就可能乱码甚至解析出错；双击时的当前目录不是仓库根，
所以必须靠 `%~dp0` 自己定位。中文提示一律留给 setup.ps1（UTF-8 + BOM）输出。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / '安装.cmd'


def test_entry_calls_setup_ps1_through_powershell():
    assert ENTRY.is_file(), f'缺少一键安装入口：{ENTRY.name}'
    text = ENTRY.read_bytes().decode('ascii')  # 非 ASCII（中文注释、UTF-8 BOM）在这里就该炸
    assert '%~dp0scripts\\setup.ps1' in text  # 双击时用 %~dp0 定位，别指望当前目录
    assert '-ExecutionPolicy Bypass' in text  # 没有它，默认策略下双击会报“禁止运行脚本”
    assert 'pause' in text                    # 否则出错时窗口一闪而过，什么都看不到
    assert '%*' in text                       # `安装.cmd -SeedExamples` 之类要能透传


def test_readme_points_at_the_double_click_entry():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert '安装.cmd' in readme
