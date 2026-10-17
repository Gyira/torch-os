"""依赖体检：把 requirements 文件里钉住的版本和**当前解释器**里已装的版本逐条对上。

不联网、不下载、不动环境，只回答一个问题：这台机器还要不要再装一遍。
图形安装器（`安装.cmd`）装依赖之前先跑它——本机已经装好的版本不会让人重复安装，也就不会白白等一轮网络。

只认 `名字` 和 `名字==版本`（可带 extras）两种写法；其余行会原样报出来，绝不猜着跳过。
退出码：0 = 全部满足；1 = 有缺失或版本不符；2 = 用法或文件问题。
"""

from __future__ import annotations

import argparse
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIREMENTS = ROOT / 'requirements.lock.txt'
ENTRY = re.compile(r'^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[(?P<extras>[^\]]*)\])?\s*(?P<specifier>.*)$')


def parse_requirements(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """返回 (条目, 看不懂的行)。条目的版本要求为空串表示「只查装没装」。"""
    entries: list[tuple[str, str]] = []
    broken: list[str] = []
    for number, raw in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if not line or line.startswith(('-', '--')):  # 空行、注释、-r/--index-url 之类的选项
            continue
        match = ENTRY.match(line)
        if not match:
            broken.append(f'{path.name}:{number}: {line}')
            continue
        entries.append((match.group('name'), match.group('specifier').strip()))
    return entries, broken


def inspect(entries: list[tuple[str, str]]) -> tuple[int, list[str], list[str]]:
    """逐条比对：返回 (满足条数, 未装/版本不符的说明, 只查了是否安装的名字)。"""
    ok = 0
    problems: list[str] = []
    presence_only: list[str] = []
    for name, specifier in entries:
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            problems.append(f'未安装：{name}{specifier}')
            continue
        if specifier.startswith('=='):
            want = specifier[2:].strip()
            if installed != want:
                problems.append(f'版本不符：{name} 要求 {want}，本机是 {installed}')
                continue
        else:
            presence_only.append(name)
        ok += 1
    return ok, problems, presence_only


def main() -> int:
    # 报告里会出现中文与路径：GBK 控制台直接 print 会抛 UnicodeEncodeError，统一按 UTF-8 输出。
    # （放在 main 里而不是模块层：这个模块还要被测试 import，导入不该有副作用。）
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):  # pragma: no cover - 老解释器/被重定向
        pass
    parser = argparse.ArgumentParser(description='按 requirements 文件检查本机已装依赖，不联网。')
    parser.add_argument('requirements', nargs='?', default=str(DEFAULT_REQUIREMENTS),
                        help='要对照的 requirements 文件（默认 requirements.lock.txt）')
    args = parser.parse_args()

    path = Path(args.requirements)
    if not path.is_file():
        print(f'找不到 requirements 文件：{path}')
        return 2
    entries, broken = parse_requirements(path)
    if not entries and not broken:
        print(f'{path} 里没有可检查的条目。')
        return 2

    ok, problems, presence_only = inspect(entries)
    total = len(entries)
    print(f'依赖检查：{sys.executable}（Python {sys.version.split()[0]}）对照 {path}')
    if not problems and not broken:
        print(f'已满足 {ok}/{total} —— 本机已装好，不用再 pip install。')
        if presence_only:
            print(f'（{"、".join(presence_only)} 用的是区间写法，只查了是否安装，没有比对版本）')
        return 0
    print(f'已满足 {ok}/{total} —— 还差 {len(problems) + len(broken)} 条：')
    for item in problems:
        print(f'  {item}')
    for item in broken:
        print(f'  看不懂的行：{item}')
    print(f'补装：{sys.executable} -m pip install -r {path}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
