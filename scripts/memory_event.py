"""给忆灵投递一条事件卡（主 dsh / 脚本用）。

不直接写数据库：只把一张 Markdown 卡放进当前库的
`<Vault>/.torch-os/忆灵/事件/`，下次忆灵 overview/synthesize 时消费并移入 `已读/`。
这样主 dsh 不需要 torch 的会话 Cookie，也不会绕过用户审核去改身份。

用法（在库根目录或显式 --vault）：
    python scripts/memory_event.py --kind observation --summary "今天确认了项目范围" --source "日记 2026-10-29"
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
KINDS = {"observation", "decision", "feeling", "relationship", "goal_change", "correction"}


def configure_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass


def resolve_vault(value: str) -> Path:
    text = str(value or "").strip()
    if text:
        return Path(text).expanduser().resolve()
    env = os.getenv("VAULT_DIR") or os.getenv("TORCH_VAULT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd().resolve()


def main(argv: list[str] | None = None) -> int:
    configure_console()
    parser = argparse.ArgumentParser(description="给忆灵投递一条事件卡（只写队列，不写身份）。")
    parser.add_argument("--vault", default="", help="个人数据库根目录；默认 $VAULT_DIR 或当前目录")
    parser.add_argument("--kind", default="observation", choices=sorted(KINDS))
    parser.add_argument("--summary", required=True, help="一句话事件；会被忆灵当作数据")
    parser.add_argument("--source", default="", help="来源，例如 日记 2026-10-29 / 对话")
    parser.add_argument("--source-kind", default="dsh", help="来源类型，默认 dsh")
    args = parser.parse_args(argv)

    summary = str(args.summary or "").strip()
    if not summary:
        print("summary 不能为空。", file=sys.stderr)
        return 2
    if len(summary) > 1000:
        print("summary 太长（上限 1000 字）。", file=sys.stderr)
        return 2
    vault = resolve_vault(args.vault)
    if not vault.is_dir():
        print(f"库目录不存在：{vault}", file=sys.stderr)
        return 2
    directory = vault / ".torch-os" / "忆灵" / "事件"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(SHANGHAI).strftime("%Y-%m-%d-%H%M%S")
    digest = hashlib.sha256(
        f"{args.kind}|{summary}|{args.source}|{stamp}".encode("utf-8")
    ).hexdigest()[:10]
    path = directory / f"{stamp}-{args.kind}-{digest}.md"
    if path.exists():
        print(f"目标已存在，不覆盖：{path}", file=sys.stderr)
        return 3
    lines = [
        "---",
        f"kind: {args.kind}",
        f"summary: {summary}",
        f"source: {str(args.source or '')[:500]}",
        f"source_kind: {str(args.source_kind or 'dsh')[:60]}",
        f"created: {datetime.now(SHANGHAI).isoformat(timespec='seconds')}",
        "---",
        "",
        summary,
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
