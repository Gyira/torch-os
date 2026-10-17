"""本地 `.env` 的单键原子写入：只改一行，保留其余内容与注释。

工作区切换与 Obsidian 库连接都靠它把 DATA_DIR / VAULT_DIR / VAULT_READ_ONLY 写进 `.env`；
进程不会重新读取 `.env`，所以调用方必须把 `restart_required` 如实告诉用户。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4


def write_env_value(path: Path, name: str, value: str) -> None:
    """Atomically replace one key in a local .env file, preserving every other line."""
    path = Path(path)
    lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    pattern = re.compile(rf'^\s*{re.escape(name)}\s*=')
    replaced = False
    output = []
    for line in lines:
        if pattern.match(line):
            output.append(f'{name}={value}')
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f'{name}={value}')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.tmp-{uuid4().hex[:8]}')
    temporary.write_text('\n'.join(output) + '\n', encoding='utf-8')
    os.replace(temporary, path)
