"""给「与神对话」投递提问卡：大模型读完四库，把「这里有个缺口」变成一个问题。

torch 不调模型，也不该调；但「问什么最有价值」必须由读过内容的一方决定。
所以分工是：dsh 生成并投递（带依据），torch 决定什么时候问、答案写哪、用过收走。

    python scripts/question_draft.py add --prompt "..." --why "..." --target worldview
    python scripts/question_draft.py list

--target 可以是身份字段（name/motto/worldview/lifeview/values/notes），
也可以是章节 key（memory/relationship/meaning/distill_work…），用 --list-targets 查。

退出码：0 成功；2 参数不合法。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.app import vault_layout  # noqa: E402
from backend.app.questions import QUEUE_DIR, load_queue  # noqa: E402
from backend.app.identity import FIELD_LABELS  # noqa: E402
from session_brief import resolve_vault  # noqa: E402

FIELD_TARGETS = tuple(FIELD_LABELS)  # name/motto/worldview/lifeview/values/notes
CHAPTER_BLOCKED = {"task", "journal", "habit"}
MAX_PROMPT = 120
MAX_WHY = 300


def _targets() -> list[str]:
    return [*FIELD_TARGETS, *[key for key in vault_layout.CHAPTER_BY_KEY if key not in CHAPTER_BLOCKED]]


def _write(vault: Path, *, prompt: str, why: str, target: str, placeholder: str,
           priority: int) -> Path:
    directory = vault / QUEUE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{target}|{prompt}".encode("utf-8")).hexdigest()[:8]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{stamp}-{digest}.md"
    lines = ["---", f"prompt: {prompt}"]
    if target in FIELD_TARGETS:
        lines.append(f"field: {target}")
    else:
        lines.append(f"chapter: {target}")
    lines += [
        f"why: {why}",
        f"placeholder: {placeholder}" if placeholder else "placeholder: ''",
        f"priority: {priority}",
        f"created: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "source: dsh",
        "---",
        "",
        "由「与神对话」读过四库后投递；用过（回答或跳过）会自动挪进「已用」。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="投递主动提问卡（只写队列）")
    parser.add_argument("action", choices=("add", "list", "list-targets"))
    parser.add_argument("--vault", default="", help="Vault 根目录（默认用 VAULT_DIR 或仓库 .env）")
    parser.add_argument("--prompt", default="", help=f"问题本身（≤{MAX_PROMPT} 字）")
    parser.add_argument("--why", default="", help=f"为什么值得问，引用具体笔记（≤{MAX_WHY} 字）")
    parser.add_argument("--target", default="", help="身份字段或章节 key")
    parser.add_argument("--placeholder", default="", help="输入框提示语")
    parser.add_argument("--priority", type=int, default=120, help="越大越先问；题库是 50–100")
    args = parser.parse_args(argv)

    vault = resolve_vault(args.vault)
    if not vault.is_dir():
        print(f"找不到 Vault：{vault}", file=sys.stderr)
        return 2

    if args.action == "list-targets":
        print("身份字段：", "、".join(FIELD_TARGETS))
        print("章节：", "、".join(key for key in vault_layout.CHAPTER_BY_KEY if key not in CHAPTER_BLOCKED))
        return 0

    if args.action == "list":
        items = load_queue(vault)
        if not items:
            print("提问队列是空的（会回退到固定题库）。")
            return 0
        print(f"队列里 {len(items)} 条：")
        for item in items:
            target = item["field"] or item["chapter"]
            print(f"- [{target}] {item['prompt']}")
        return 0

    prompt = str(args.prompt or "").strip()
    why = str(args.why or "").strip()
    target = str(args.target or "").strip()
    if not prompt:
        print("--prompt 不能为空", file=sys.stderr)
        return 2
    if len(prompt) > MAX_PROMPT:
        print(f"--prompt 最多 {MAX_PROMPT} 字", file=sys.stderr)
        return 2
    if not why:
        print("--why 不能为空：说不出为什么问，就别问", file=sys.stderr)
        return 2
    if len(why) > MAX_WHY:
        print(f"--why 最多 {MAX_WHY} 字", file=sys.stderr)
        return 2
    if target not in _targets():
        print(f"未知 --target：{target or '（空）'}；用 --list-targets 查可用值", file=sys.stderr)
        return 2

    for item in load_queue(vault):
        if item["prompt"] == prompt:
            print(f"队列里已经有同样的问题，没有重复投递：{item['id']}")
            return 0

    path = _write(vault, prompt=prompt, why=why, target=target,
                  placeholder=str(args.placeholder or "").strip(),
                  priority=max(0, min(999, int(args.priority))))
    print(f"已投递：{path.relative_to(vault).as_posix()}")
    print("下次打开工作台就会问到；答完自动挪进「已用」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
