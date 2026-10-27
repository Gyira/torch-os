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
from typing import Any
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
    """默认简报的「关于我」：只列填了的字段；座右铭兼容 `mottos` 列表。"""
    for relative in IDENTITY_CANDIDATES:
        path = vault / relative
        if not path.is_file():
            continue
        meta = _load_front_matter(path)
        mottos = meta.get("mottos")
        rows = []
        for key, label in (("name", "姓名"), ("motto", "座右铭"), ("worldview", "三观"),
                           ("lifeview", "人生观"), ("values", "价值观")):
            value = str(meta.get(key) or "").strip()
            if key == "motto" and not value and isinstance(mottos, list):
                value = " / ".join(str(item).strip() for item in mottos if str(item).strip())
            if value:
                rows.append(f"{label}：{' '.join(value.split())[:160]}")
        return rows
    return []


def _clip(value: str, limit: int = 72) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _load_front_matter(path: Path) -> dict[str, Any]:
    """身份这类字段有列表与多行，优先用 PyYAML；没有就退回逐行解析。"""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        import yaml
    except ImportError:
        return _front_matter(path)[0]
    try:
        loaded = yaml.safe_load(parts[1]) or {}
    except Exception:
        return _front_matter(path)[0]
    return loaded if isinstance(loaded, dict) else {}


def _open_tasks(vault: Path) -> list[tuple[str, str, str]]:
    tasks: list[tuple[str, str, str]] = []
    for path in _markdown_files(vault / "00_人生路/08_任务与待办/任务"):
        meta, body = _front_matter(path)
        if _truthy(meta.get("完成", "")) or _truthy(meta.get("done", "")):
            continue
        due = meta.get("日期") or meta.get("截止") or meta.get("开始") or ""
        tasks.append((due or "9999-99-99", _title(path, body), path.relative_to(vault).as_posix()))
    tasks.sort()
    return tasks


IDENTITY_CANDIDATES = (
    "01_自我档案/01_内核与三观/身份（identity）.md",
    "01-Identity/身份（identity）.md",
)
IDENTITY_FIELDS = (("name", "称呼"), ("motto", "座右铭"), ("worldview", "世界观"),
                   ("lifeview", "人生观"), ("values", "价值观"), ("notes", "我写的"))


def _identity_rows(vault: Path) -> tuple[list[str], int]:
    """身份卡：座右铭兼容 `mottos` 列表；文件缺失只留一行说明。"""
    for relative in IDENTITY_CANDIDATES:
        path = vault / relative
        if not path.is_file():
            continue
        meta = _load_front_matter(path)
        mottos = meta.get("mottos")
        rows: list[str] = []
        filled = 0
        for key, label in IDENTITY_FIELDS:
            value = str(meta.get(key) or "").strip()
            if key == "motto" and not value and isinstance(mottos, list):
                value = " / ".join(str(item).strip() for item in mottos if str(item).strip())
            if value:
                filled += 1
                rows.append(f"- {label}：{_clip(value)}")
            else:
                rows.append(f"- {label}：（未写）")
        return rows, filled
    return ["- （还没有身份文件）"], 0


def _note_title(path: Path) -> str:
    meta, body = _front_matter(path)
    return str(meta.get("title") or "").strip() or _title(path, body)


def _summary_line(body: str) -> str:
    """归档笔记的第一行引用块就是摘要；没有就空着，别猜。"""
    for line in body.splitlines():
        text = line.strip()
        if not text:
            continue
        return _clip(text.lstrip("> ").strip(), 64) if text.startswith(">") else ""
    return ""


def _archived_notes(vault: Path) -> list[tuple[float, Path, dict[str, str], str]]:
    found: list[tuple[float, Path, dict[str, str], str]] = []
    for pillar in PILLARS:
        for path in _markdown_files(vault / pillar):
            try:
                meta, body = _front_matter(path)
                if not _is_ai_captured(meta):
                    continue
                found.append((path.stat().st_mtime, path, meta, body))
            except OSError:
                continue
    found.sort(reverse=True)
    return found


def _diary_excerpt(path: Path) -> str:
    """日记只有模板、没写字时返回空串，避免把空模板当成「有内容」。"""
    if not path.is_file():
        return ""
    _meta, body = _front_matter(path)
    for line in body.splitlines():
        text = line.strip()
        if not text or text.startswith("#") or text.startswith(">"):
            continue
        if "____" in text or set(text) <= set("-* "):
            continue
        if text.startswith("**") and text.endswith("**"):
            continue  # 模板里的粗体小标题（每日三问、今天取得了哪些进步）
        return _clip(text, 48)
    return ""


def build_memory_card(vault: Path) -> str:
    """给 dsh 开场用的「记忆内核」：能常驻上下文的那几百字。"""
    today = datetime.now(SHANGHAI).date()
    rows, filled = _identity_rows(vault)
    lines: list[str] = [f"# 记忆内核 · {today.isoformat()}（{['周一','周二','周三','周四','周五','周六','周日'][today.weekday()]}）",
                        "", "## 我是谁", *rows]

    goals = sorted(_markdown_files(vault / "00_人生路/04_目标"))
    projects = sorted(_markdown_files(vault / "00_人生路/05_项目/01_项目计划"))
    tasks = _open_tasks(vault)[:3]
    busy: list[str] = []
    if goals:
        busy.append("- 目标：" + " · ".join(_clip(_note_title(path), 32) for path in goals[:2]))
    if projects:
        busy.append("- 项目：" + " · ".join(_clip(_note_title(path), 32) for path in projects[:2]))
    if tasks:
        busy.append("- 未完成：" + " · ".join(
            f"{title}（{due}）" if due != "9999-99-99" else title for due, title, _p in tasks))
    lines += ["", "## 我在忙什么", *(busy or ["- （还没有目标、项目或未完成任务）"])]

    week: list[tuple[str, str]] = []
    for offset in range(7):
        day = today - timedelta(days=offset)
        excerpt = _diary_excerpt(vault / f"00_人生路/01_日记/{day.isoformat()}.md")
        if excerpt:
            week.append((day.isoformat(), excerpt))
    if week:
        lines += ["", f"## 最近 7 天（日记 {len(week)}/7 天有内容）",
                  *[f"- {day} · {text}" for day, text in week[:3]]]
    else:
        lines += ["", "## 最近 7 天", "- 日记 0/7 天：只有模板，没有内容"]

    archived = _archived_notes(vault)
    lines += ["", f"## 最近结论（dsh 归档 {len(archived)} 篇）"]
    if archived:
        for stamp, path, _meta, body in archived[:5]:
            when = datetime.fromtimestamp(stamp, SHANGHAI).strftime("%m-%d")
            summary = _summary_line(body)
            lines.append(f"- {when} · {_note_title(path)}" + (f" — {summary}" if summary else ""))
    else:
        lines.append("- （还没有由 dsh 归档的结论）")

    notes = sum(len(_markdown_files(vault / pillar)) for pillar in PILLARS)
    density = ("薄" if filled <= 2 or not archived else
               "厚" if filled >= 5 and len(archived) >= 20 else "在长")
    hint = ("少用「你上次说过」，先问再答" if density == "薄" else
            "按检索协议引用旧结论" if density == "在长" else "可以放心引用旧结论")
    lines += ["", "## 记忆密度",
              f"- 身份 {filled}/{len(IDENTITY_FIELDS)} · 四库 {notes} 篇 · dsh 归档 {len(archived)} 篇",
              f"- 判断：{density} → {hint}",
              "", "> 内核卡只读生成；它越厚，靠的是每次归档回流，不是更长的上下文。"]
    return "\n".join(lines)


def build_brief(vault: Path, *, include_identity: bool = True) -> str:
    now = datetime.now(SHANGHAI)
    day = now.date()
    lines: list[str] = [f"# 四库简报 · {day.isoformat()}（{['周一','周二','周三','周四','周五','周六','周日'][day.weekday()]}）"]

    identity = _read_identity(vault) if include_identity else []
    if identity:
        lines.extend(["", "## 关于我", *[f"- {row}" for row in identity]])

    diary = vault / f"00_人生路/01_日记/{day.isoformat()}.md"
    excerpt = _diary_excerpt(diary)
    if excerpt:
        lines.extend(["", f"## 今日日记  已写：{excerpt}"])
    elif diary.is_file():
        lines.extend(["", "## 今日日记  只有模板，还没写"])
    else:
        lines.extend(["", "## 今日日记  还没写"])

    tasks = _open_tasks(vault)
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

    captured = _archived_notes(vault)
    if captured:
        lines.extend(["", f"## 最近由 dsh 归档（{len(captured)}）"])
        for _stamp, path, meta, body in captured[:AI_LIMIT]:
            lines.append(f"- {_title(path, body)}（{meta.get('type', '')}）")

    lines.extend(["", "> 简报只读生成；分类建议写在回答里，写入前仍需用户确认。"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="dsh 开场：四库只读简报")
    parser.add_argument("--vault", default="", help="Vault 根目录（默认用 VAULT_DIR 或仓库 .env）")
    parser.add_argument("--memory", action="store_true", help="只输出记忆内核卡（我是谁/在忙什么/最近结论）")
    parser.add_argument("--full", action="store_true", help="记忆内核 + 态势，「与神对话」开场用")
    args = parser.parse_args(argv)
    vault = resolve_vault(args.vault)
    if not vault.is_dir():
        print(f"找不到 Vault：{vault}", file=sys.stderr)
        return 2
    if args.memory:
        print(build_memory_card(vault))
        return 0
    if args.full:
        print(build_memory_card(vault))
        print("\n---\n")
    print(build_brief(vault, include_identity=not args.full))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
