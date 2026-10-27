"""归档出口：把 dsh 润色好的正文写成一篇四库笔记。

只做机械部分——章节校验、文件名、frontmatter、防覆盖；分类与措辞由 dsh 负责。
写入复用 `backend.app.pillar_notes.create_note`，不另写一套写盘逻辑。

    python scripts/archive_note.py --list-chapters
    python scripts/archive_note.py --chapter reflection --title "..." --body-file draft.md --dry-run

退出码：0 成功（含 dry-run）；1 写入失败；2 参数或章节不合法。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.app import vault_layout  # noqa: E402
from backend.app.pillar_notes import create_note  # noqa: E402
from session_brief import resolve_vault  # noqa: E402


def _chapters() -> list[tuple[str, str, str, str, str]]:
    return list(vault_layout.CHAPTERS)


def print_chapters() -> None:
    print("章节 key\t库\t默认 type\t目录")
    for key, pillar, title, directory, default_type in _chapters():
        print(f"{key}\t{vault_layout.PILLAR_TITLES[pillar]}\t{default_type}\t{directory}\t{title}")


def _preview_path(vault: Path, chapter: str, title: str) -> str:
    """dry-run 用：算出现在会落到哪个文件名（含 -2 去重），不创建任何东西。"""
    directory = vault_layout.chapter_dir(chapter)
    stem = vault_layout.safe_note_stem(title)
    target = vault / directory / f"{stem}.md"
    index = 2
    while target.exists():
        target = vault / directory / f"{stem}-{index}.md"
        index += 1
    return target.relative_to(vault).as_posix()


def _body_text(args: argparse.Namespace) -> str:
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8-sig").strip()
    return str(args.body or "").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把一段润色好的正文写成四库笔记")
    parser.add_argument("--list-chapters", action="store_true", help="列出章节 key 与默认 type")
    parser.add_argument("--vault", default="", help="Vault 根目录（默认用 VAULT_DIR 或仓库 .env）")
    parser.add_argument("--chapter", default="", help="章节 key，见 --list-chapters")
    parser.add_argument("--title", default="", help="笔记标题（也是文件名主体）")
    parser.add_argument("--type", dest="note_type", default="", help="frontmatter type，默认取章节的 default_type")
    parser.add_argument("--tags", default="", help="标签，逗号分隔")
    parser.add_argument("--date", default="", help="日期字段，如 2026-10-27")
    parser.add_argument("--source", default="dsh", help="来源标记，默认 dsh")
    parser.add_argument("--body-file", default="", help="正文 Markdown 文件（UTF-8）")
    parser.add_argument("--body", default="", help="正文；和 --body-file 二选一")
    parser.add_argument("--dry-run", action="store_true", help="只报告落点，不写文件")
    args = parser.parse_args(argv)

    if args.list_chapters:
        print_chapters()
        return 0

    vault = resolve_vault(args.vault)
    if not vault.is_dir():
        print(f"找不到 Vault：{vault}", file=sys.stderr)
        return 2

    chapter = (args.chapter or "").strip()
    if not chapter or chapter not in vault_layout.CHAPTER_BY_KEY:
        print(f"未知章节：{chapter or '（空）'}；用 --list-chapters 查可用 key", file=sys.stderr)
        return 2
    title = str(args.title or "").strip()
    if not title:
        print("标题不能为空", file=sys.stderr)
        return 2
    body = _body_text(args)
    if not body:
        print("正文不能为空（--body 或 --body-file）", file=sys.stderr)
        return 2

    # 摘要约定：正文第一行是 `> 摘要`，开场内核卡会直接读它。不强制，但要提醒。
    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    if not first.startswith(">"):
        print("提示：正文第一行不是 `> 摘要`，记忆内核里这篇会显示不出结论", file=sys.stderr)

    pillar = vault_layout.CHAPTER_BY_KEY[chapter][1]
    tags = [item.strip() for item in str(args.tags or "").replace("，", ",").split(",") if item.strip()]

    if args.dry_run:
        print("dry-run：不会写任何文件")
        print(f"章节：{chapter}（{vault_layout.chapter_dir(chapter)}）")
        print(f"落点：{_preview_path(vault, chapter, title)}")
        print(f"标题：{title}")
        print(f"类型：{args.note_type or vault_layout.chapter_default_type(chapter)}")
        print(f"标签：{'、'.join(tags) or '（无）'}")
        print(f"正文字数：{len(body)}")
        return 0

    try:
        note = create_note(vault, pillar=pillar, chapter=chapter, title=title, body=body,
                           note_type=args.note_type, tags=tags, source=args.source, date=args.date)
    except Exception as error:  # create_note 用 HTTPException 报 422，CLI 统一成退出码 2
        print(f"写入失败：{error}", file=sys.stderr)
        return 1

    relative = str(note.get("path") or "")
    print(f"已归档：{relative}")
    print(f"链接：[[{relative.removesuffix('.md')}]]")
    print(f"类型：{note.get('type')} · 章节：{chapter} · 正文字数：{len(body)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
