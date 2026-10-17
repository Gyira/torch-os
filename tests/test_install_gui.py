"""图形安装器的自测：不弹窗、不联网，只测那些能出事的地方。

GUI 最容易坏在“流程决策”而不是像素：已装好的依赖被重复安装、.env 被覆盖、
示例数据删掉别人的文件、镜像重试逻辑反了。这些用例把 `run_install` 的子进程换成假的来钉住它们。
tkinter 的界面本身不在这里测——那需要真实桌面，仓库的自检要保持无头可跑。
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip('tkinter')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import install_gui  # noqa: E402


def fake_venv_tree(tmp_path: Path) -> Path:
    """一棵够 run_install 跑完的最小仓库树（venv 里放个空文件冒充 python.exe）。"""
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    (scripts / 'check_deps.py').write_text('', encoding='utf-8')
    venv = tmp_path / '.venv' / ('Scripts' if os.name == 'nt' else 'bin')
    venv.mkdir(parents=True)
    (venv / ('python.exe' if os.name == 'nt' else 'python')).write_bytes(b'')
    (tmp_path / '.env.example').write_text('X=1\n', encoding='utf-8')
    return tmp_path


def test_venv_python_lives_inside_the_repo_venv(tmp_path):
    python = install_gui.venv_python(tmp_path)
    assert python.parent.parent == tmp_path / '.venv'
    assert python.name.startswith('python')


def test_python_version_gate():
    assert install_gui.python_version_supported((3, 12, 6))
    assert install_gui.python_version_supported((3, 10, 0))
    assert not install_gui.python_version_supported((3, 9, 18))


def test_env_file_is_created_once_and_never_overwritten(tmp_path):
    (tmp_path / '.env.example').write_text('KEY=example\n', encoding='utf-8')
    assert install_gui.ensure_env_file(tmp_path) is True
    assert (tmp_path / '.env').read_text(encoding='utf-8') == 'KEY=example\n'

    (tmp_path / '.env').write_text('KEY=mine\n', encoding='utf-8')
    assert install_gui.ensure_env_file(tmp_path) is False
    assert (tmp_path / '.env').read_text(encoding='utf-8') == 'KEY=mine\n'


def test_copy_examples_merges_and_overwrites_but_never_deletes(tmp_path):
    source = tmp_path / 'examples' / 'vault'
    (source / 'notes').mkdir(parents=True)
    (source / 'notes' / 'same.md').write_text('示例', encoding='utf-8')
    target = tmp_path / 'data' / 'vault'
    (target / 'notes').mkdir(parents=True)
    (target / 'notes' / 'same.md').write_text('用户自己的', encoding='utf-8')
    (target / 'keep.md').write_text('别动我', encoding='utf-8')

    copied = install_gui.copy_examples(tmp_path)

    assert copied == 1
    assert (target / 'notes' / 'same.md').read_text(encoding='utf-8') == '示例'  # 同名覆盖
    assert (target / 'keep.md').read_text(encoding='utf-8') == '别动我'          # 其它文件不动


def test_obsidian_vault_needs_the_dot_folder(tmp_path):
    assert install_gui.is_obsidian_vault(tmp_path) is False
    (tmp_path / '.obsidian').mkdir()
    assert install_gui.is_obsidian_vault(tmp_path) is True


def test_run_install_skips_pip_when_the_environment_is_ready(tmp_path, monkeypatch):
    """体检返回 0 时不许再跑 pip——这正是“别让人重复安装”的核心。"""
    fake_venv_tree(tmp_path)
    (tmp_path / 'examples' / 'vault').mkdir(parents=True)
    (tmp_path / 'examples' / 'vault' / 'demo.md').write_text('虚构', encoding='utf-8')
    calls: list[str] = []

    def fake_run(cmd, emit, cancel, cwd):
        calls.append(' '.join(str(part) for part in cmd))
        return 0

    monkeypatch.setattr(install_gui, 'run_command', fake_run)
    events: list = []
    plan = install_gui.InstallPlan(root=tmp_path, seed_examples=True,
                                   obsidian_vault='', mirror_retry=True)

    ok, message = install_gui.run_install(plan, lambda kind, payload: events.append((kind, payload)),
                                          threading.Event())

    assert ok, message
    assert len(calls) == 1 and 'check_deps.py' in calls[0]
    assert not any(' -m pip install' in call for call in calls)
    assert (tmp_path / '.env').read_text(encoding='utf-8') == 'X=1\n'
    assert (tmp_path / 'data' / 'vault' / 'demo.md').read_text(encoding='utf-8') == '虚构'
    states = {payload[0]: payload[1] for kind, payload in events if kind == 'step'}
    assert states['deps'] == 'skip'


def test_run_install_retries_with_the_mirror_when_the_official_source_fails(tmp_path, monkeypatch):
    fake_venv_tree(tmp_path)
    calls: list[str] = []

    def fake_run(cmd, emit, cancel, cwd):
        joined = ' '.join(str(part) for part in cmd)
        calls.append(joined)
        if 'check_deps.py' in joined:
            return 1 if sum('check_deps.py' in call for call in calls) == 1 else 0
        if 'pip' in joined:
            return 0 if install_gui.MIRROR_URL in joined else 1
        return 0

    monkeypatch.setattr(install_gui, 'run_command', fake_run)
    events: list = []
    plan = install_gui.InstallPlan(root=tmp_path, seed_examples=False,
                                   obsidian_vault='', mirror_retry=True)

    ok, message = install_gui.run_install(plan, lambda kind, payload: events.append((kind, payload)),
                                          threading.Event())

    assert ok, message
    pip_calls = [call for call in calls if ' -m pip install' in call]
    assert len(pip_calls) == 2
    assert install_gui.MIRROR_URL not in pip_calls[0] and install_gui.MIRROR_URL in pip_calls[1]
    states = {payload[0]: payload[1] for kind, payload in events if kind == 'step'}
    assert states['deps'] == 'ok'


def test_module_import_does_not_open_a_window():
    """导入只能定义类和函数——测试/静态检查运行时不该冒出窗口或 Tk 实例。"""
    assert callable(install_gui.main)
    assert callable(install_gui.run_install)
