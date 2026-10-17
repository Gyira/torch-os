"""安装入口只有图形安装器：`安装.cmd` 得在、得是纯 ASCII、得把安装器拉起来。

2026-10-17：入口从「直接跑 setup.ps1」升级为图形安装器，随后旧控制台安装脚本被清理，
所以这里同时钉住“没有第二条安装路径”——`-console` 分支和 `scripts\\setup.ps1` 都不许回来。
两条硬约束——cmd.exe 按系统代码页读 .cmd，所以这个文件一写中文（连同 BOM）在双击时就可能
乱码甚至解析出错；双击时的当前目录不是仓库根，所以必须靠 `%~dp0` 自己定位。
中文提示一律留给 Python 侧（UTF-8），这个文件只做选择与兜底。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / '安装.cmd'


def read_entry() -> str:
    assert ENTRY.is_file(), f'缺少一键安装入口：{ENTRY.name}'
    return ENTRY.read_bytes().decode('ascii')  # 非 ASCII（中文注释、UTF-8 BOM）在这里就该炸


def test_entry_launches_the_graphical_installer():
    text = read_entry()
    assert 'scripts\\install_gui.py' in text
    assert 'pythonw' in text      # 双击时不要多一个黑框
    assert 'start ""' in text     # 启动后立刻还回控制台/资源管理器
    assert '%~dp0' in text        # 双击时当前目录不是仓库根
    assert 'py -3.12' in text     # 优先挑 3.12（py 启动器即使不在 PATH 也在）
    assert 'WindowsApps' in text  # 绕开微软商店的空壳别名


def test_entry_is_the_only_install_path():
    text = read_entry()
    assert '-console' not in text
    assert 'setup.ps1' not in text
    assert not (ROOT / 'scripts' / 'setup.ps1').exists(), '旧的 setup.ps1 不应再出现'


def test_readme_points_at_the_double_click_entry():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert '安装.cmd' in readme
    assert 'scripts\\setup.ps1' not in readme
