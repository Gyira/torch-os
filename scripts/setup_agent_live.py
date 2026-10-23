"""Configure the torch-live dsh profile used by 与神对话.

This creates a dedicated DeepSeek Harness profile (a copy of the shipped `acp`
template) and inserts the local torch-live-stream plugin. The existing `acp`
profile is never modified, so Claudian/other clients keep their own setup.

Usage:
    python scripts/setup_agent_live.py            # create/update the profile
    python scripts/setup_agent_live.py --check    # only verify
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'agent' / 'dsh-torch-live' / 'index.js'
ROW_ID = 'torch-live-stream'


def dsh_argv(*args: str) -> list[str]:
    dsh = shutil.which('dsh')
    if not dsh:
        raise SystemExit('未找到 dsh。请先安装 DeepSeek Harness：npm install -g @deepseek-ai/dsh')
    command = [dsh, *args]
    if os.name == 'nt' and dsh.lower().endswith(('.cmd', '.bat')):
        return [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', *command]
    return command


def run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=timeout)


def profile_dir(name: str) -> Path:
    home = Path(os.environ.get('DSH_HOME') or (Path.home() / '.dsh'))
    return home / 'profiles' / name


def ensure_profile(name: str) -> Path:
    target = profile_dir(name)
    if target.is_dir():
        print(f'profile 已存在：{target}')
        return target
    print(f'创建 profile：{name}（模板 acp）')
    result = run(dsh_argv(name, '--from-default-profile', 'acp', '--dump-config'))
    if result.returncode != 0:
        raise SystemExit(f'创建 profile 失败：\n{result.stdout}\n{result.stderr}')
    if not target.is_dir():
        raise SystemExit(f'创建后仍找不到 profile 目录：{target}')
    return target


def configure_patch(target: Path) -> None:
    if not PLUGIN.is_file():
        raise SystemExit(f'找不到插件：{PLUGIN}')
    patch = target / 'cordis.patch.yml'
    if not patch.exists():
        raise SystemExit(f'找不到 profile patch：{patch}')
    text = patch.read_text(encoding='utf-8')
    if ROW_ID in text:
        row = re.search(
            r"(-\s*id:\s*" + re.escape(ROW_ID) + r"\s*\n\s*name:\s*)(['\"])(.*?)\2",
            text,
        )
        if row and row.group(3) != str(PLUGIN):
            text = text[:row.start(3)] + str(PLUGIN) + text[row.end(3):]
            patch.write_text(text, encoding='utf-8')
            print(f'已更新实时流插件路径：{PLUGIN}')
        else:
            print('实时流插件已在 profile patch 中，跳过。')
        return
    insertion = (
        '- insert:\n'
        f'    - id: {ROW_ID}\n'
        f"      name: '{PLUGIN}'\n"
    )
    stripped = text.strip()
    if stripped == '[]':
        text = insertion
    else:
        text = text.rstrip() + '\n' + insertion
    patch.write_text(text, encoding='utf-8')
    print(f'已写入实时流插件：{patch}')


def verify(name: str) -> bool:
    result = run(dsh_argv('--profile', name, '--dump-config'))
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        return False
    composed = result.stdout
    ok = ROW_ID in composed and 'torch-live-stream' in composed
    if ok:
        print(f'profile {name} 已包含实时流插件。')
    else:
        print(f'profile {name} 尚未包含实时流插件。', file=sys.stderr)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description='配置与神对话使用的 torch-live dsh profile')
    parser.add_argument('--profile', default='torch-live')
    parser.add_argument('--check', action='store_true', help='只检查，不写 profile')
    args = parser.parse_args()

    if args.check:
        return 0 if verify(args.profile) else 1
    target = ensure_profile(args.profile)
    configure_patch(target)
    return 0 if verify(args.profile) else 1


if __name__ == '__main__':
    raise SystemExit(main())
