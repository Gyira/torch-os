"""依赖体检的自测：本机装好了就得跳过安装，缺了或版本不符必须拦住。

诉求（2026-10-17）：`setup.ps1` 以前不看本机状态就 `pip install`，已经装过的人白等一轮网络。
这些用例钉住 `scripts/check_deps.py` 的三个退出码——0 = 已装好（setup 据此跳过安装）、
1 = 有缺失或版本不符（要点名报到哪一条）、2 = 文件读不了（setup 不当成“缺依赖”去装）。
"""

from __future__ import annotations

import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / 'scripts' / 'check_deps.py'
sys.path.insert(0, str(ROOT / 'scripts'))

import check_deps  # noqa: E402


def run(body: str, tmp_path: Path) -> subprocess.CompletedProcess:
    path = tmp_path / 'requirements.lock.txt'
    path.write_text(body, encoding='utf-8', newline='')
    return subprocess.run([sys.executable, str(CHECK), str(path)],
                          capture_output=True, text=True, encoding='utf-8', errors='replace')


def test_installed_environment_is_reported_as_ready(tmp_path):
    """装好的版本、不同大小写/下划线写法的名字、pip 选项行：都该判成“本机已装好”。"""
    body = ('# 已经装好就不该再让人装一遍\n'
            '\n'
            f'pytest=={metadata.version("pytest")}\n'
            f'pyyaml=={metadata.version("PyYAML")}\n'
            '-r requirements-extra.txt\n')
    result = run(body, tmp_path)
    assert result.returncode == 0, result.stdout
    assert '不用再 pip install' in result.stdout
    assert sys.executable in result.stdout  # 报的是跑它的那个解释器——“本机”到底是哪个环境


def test_missing_package_is_reported_with_the_install_command(tmp_path):
    result = run('torch-os-dependency-that-does-not-exist==1.0\n', tmp_path)
    assert result.returncode == 1
    assert '未安装：torch-os-dependency-that-does-not-exist==1.0' in result.stdout
    assert '-m pip install -r' in result.stdout


def test_version_mismatch_names_both_versions(tmp_path):
    result = run('pytest==0.0.1\n', tmp_path)
    assert result.returncode == 1
    assert '版本不符' in result.stdout
    assert metadata.version('pytest') in result.stdout


def test_a_line_it_cannot_parse_is_reported_not_skipped(tmp_path):
    """看不懂的行如果悄悄跳过，缺依赖会被判成“已装好”——必须报出来。"""
    result = run('!!! 这不是一条依赖 ; python_version < "3.99"\n', tmp_path)
    assert result.returncode == 1
    assert '看不懂的行' in result.stdout


def test_missing_requirements_file_is_a_usage_error(tmp_path):
    result = subprocess.run([sys.executable, str(CHECK), str(tmp_path / 'nope.txt')],
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert result.returncode == 2
    assert '找不到 requirements 文件' in result.stdout


def test_the_shipped_lock_file_pins_every_entry():
    """锁文件要一直是“逐条钉死版本”的：区间写法会让体检退化成只查是否安装。"""
    entries, broken = check_deps.parse_requirements(ROOT / 'requirements.lock.txt')
    assert broken == []
    assert entries
    assert all(specifier.startswith('==') for _, specifier in entries)
