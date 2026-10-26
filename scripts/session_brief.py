"""开场简报（2026-10-26）：给 dsh 新会话一份只读的四库摘要。

只读四库 Markdown，不调用模型、不写任何文件。给 AGENTS.md 里的开场流程用：
    .\\torch-os\\.venv\\Scripts\\python.exe .\\torch-os\\scripts\\session_brief.py

Vault 解析顺序：--vault 参数 → 环境变量 VAULT_DIR（Obsidian 插件会传）→ 仓库 .env。
"""

from __future__ import annotations

import argparse
import os
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
PILLARS = ("00_人生路", "01_自我档案", "02_生活运营", "03_知识库")
SKIP_DIRS = {".obsidian", ".torch-os", ".git", ".trash", ".claudian", "_旧版", "_系统", "_模板", "_附件"}
RECENT_LIMIT = 8
AI_LIMIT = 6
TASK_LIMIT = 8
INBOX_LIMIT = 6


def resolve_vault(explicit: str = "") -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.getenv("VAULT_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    repo = Path(__file__).resolve().parents[1]
    try:
        from backend.app.config import VAULT_DIR  # 读仓库 .env
        return Path(VAULT_DIR).resolve()
    except Exception:
        return (repo.parent / "个人数据库").resolve()


def _front_matter(path: Path) -> tuple[dict[str, str], str]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}, ""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, parts[2]


def _title(path: Path, body: str = "") -> str:
    for line in body.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
    return unicodedata.normalize("NFC", path.stem)


def _markdown_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found: list[Path] = []
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [name for name in subdirs if name not in SKIP_DIRS and not name.startswith(".")]
        for name in files:
            if name.endswith(".md") and not name.startswith("."):
                found.append(Path(directory) / name)
    return found


def _truthy(value: str) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "on", "完成"}


def _is_ai_captured(meta: dict[str, str]) -> bool:
    source = " ".join(str(meta.get(key, "")) for key in ("来源", "source", "出处")).casefold()
    return "dsh" in source


def _read_identity(vault: Path) -> list[str]:
    candidates = [
        vault / "01_自我档案/01_内核与三观/身份（identity）.md",
        vault / "01-Identity/身份（identity）.md",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        meta, _body = _front_matter(path)
        rows = []
        for key, label in (("name", "姓名"), ("motto", "座右铭"), ("worldview", "三观"),
                           ("lifeview", "人生观"), ("values", "价值观")):
            value = str(meta.get(key, "")).strip()
            if value:
                rows.append(f"{label}：{value[:160]}")
        return rows
    return []


def build_brief(vault: Path) -> str:
    now = datetime.now(SHANGHAI)
    day = now.date()
    lines: list[str] = [f"# 四库简报 · {day.isoformat()}（{['周一','周二','周三','周四','周五','周六','周日'][day.weekday()]}）"]

    identity = _read_identity(vault)
    if identity:
        lines.extend(["", "## 关于我", *[f"- {row}" for row in identity]])

    diary = vault / f"00_人生路/01_日记/{day.isoformat()}.md"
    if diary.is_file():
        meta, body = _front_matter(diary)
        filled = [line for line in body.splitlines()
                  if line.strip().startswith("- ") and line.strip() != "-"]
        lines.extend(["", f"## 今日日记  已写（{len(filled)} 条记录）"])
    else:
        lines.extend(["", "## 今日日记  还没写"])

    tasks: list[tuple[str, str, str]] = []
    for path in _markdown_files(vault / "00_人生路/08_任务与待办/任务"):
        meta, body = _front_matter(path)
        if _truthy(meta.get("完成", "")) or _truthy(meta.get("done", "")):
            continue
        due = meta.get("日期") or meta.get("截止") or meta.get("开始") or ""
        tasks.append((due or "9999-99-99", _title(path, body), path.relative_to(vault).as_posix()))
    tasks.sort()
    if tasks:
        lines.extend(["", f"## 未完成任务（{len(tasks)}）"])
        for due, title, _path in tasks[:TASK_LIMIT]:
            lines.append(f"- {title}" + (f" · {due}" if due != "9999-99-99" else ""))
        if len(tasks) > TASK_LIMIT:
            lines.append(f"- …另有 {len(tasks) - TASK_LIMIT} 条")

    goals = _markdown_files(vault / "00_人生路/04_目标")
    if goals:
        lines.extend(["", f"## 目标笔记（{len(goals)}）"])
        for path in sorted(goals)[:6]:
            meta, body = _front_matter(path)
            lines.append(f"- {_title(path, body)}（{path.parent.name}）")

    projects = _markdown_files(vault / "00_人生路/05_项目/01_项目计划")
    if projects:
        lines.extend(["", f"## 项目计划（{len(projects)}）"])
        for path in sorted(projects)[:6]:
            meta, body = _front_matter(path)
            lines.append(f"- {_title(path, body)}")

    inbox = _markdown_files(vault / "03_知识库/00_收件箱")
    if inbox:
        lines.extend(["", f"## 收件箱（{len(inbox)} 条待分流）"])
        for path in sorted(inbox, key=lambda p: p.stat().st_mtime, reverse=True)[:INBOX_LIMIT]:
            lines.append(f"- {_title(path)}")

    recent: list[tuple[float, Path]] = []
    for pillar in PILLARS:
        for path in _markdown_files(vault / pillar):
            try:
                recent.append((path.stat().st_mtime, path))
            except OSError:
                continue
    recent.sort(reverse=True)
    if recent:
        lines.extend(["", "## 最近改动"])
        for stamp, path in recent[:RECENT_LIMIT]:
            when = datetime.fromtimestamp(stamp, SHANGHAI).strftime("%m-%d %H:%M")
            lines.append(f"- {when} · {path.relative_to(vault).as_posix()}")

    captured: list[tuple[float, Path, dict[str, str], str]] = []
    for pillar in PILLARS:
        for path in _markdown_files(vault / pillar):
            meta, body = _front_matter(path)
            if not _is_ai_captured(meta):
                continue
            try:
                captured.append((path.stat().st_mtime, path, meta, body))
            except OSError:
                continue
    captured.sort(reverse=True)
    if captured:
        lines.extend(["", f"## 最近由 dsh 归档（{len(captured)}）"])
        for _stamp, path, meta, body in captured[:AI_LIMIT]:
            lines.append(f"- {_title(path, body)}（{meta.get('type', '')}）")

    lines.extend(["", "> 简报只读生成；分类建议写在回答里，写入前仍需用户确认。"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="dsh 开场：四库只读简报")
    parser.add_argument("--vault", default="", help="Vault 根目录（默认用 VAULT_DIR 或仓库 .env）")
    args = parser.parse_args(argv)
    vault = resolve_vault(args.vault)
    if not vault.is_dir():
        print(f"找不到 Vault：{vault}", file=sys.stderr)
        return 2
    print(build_brief(vault))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
