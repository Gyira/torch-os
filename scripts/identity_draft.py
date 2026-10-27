"""给「与神对话」投递身份候选：只写队列，不进四库。

dsh 在对话里发现「关于我」的稳定事实（怎么称呼、三观、底线、偏好、经历）时，
用这个脚本投一张候选卡；我在「自我档案」的「身份待确认」里逐条采纳或丢弃。

    python scripts/identity_draft.py add --kind field --field name --text "..." --evidence "..."
    python scripts/identity_draft.py add --kind memory --text "..." --evidence "..."
    python scripts/identity_draft.py list

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

from backend.app.identity_inbox import INBOX_DIR, list_drafts  # noqa: E402
from session_brief import resolve_vault  # noqa: E402

FIELDS = {"name": "称呼", "motto": "座右铭", "worldview": "世界观",
          "lifeview": "人生观", "values": "价值观", "notes": "我自己写的"}
MAX_TEXT = 200
MAX_EVIDENCE = 500


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _write_draft(vault: Path, kind: str, field: str, text: str, evidence: str) -> Path:
    directory = vault / INBOX_DIR
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{kind}|{field}|{text}".encode("utf-8")).hexdigest()[:8]
    path = directory / f"{_stamp()}-{kind}-{digest}.md"
    index = 2
    while path.exists():
        path = directory / f"{_stamp()}-{kind}-{digest}-{index}.md"
        index += 1
    lines = ["---", f"kind: {kind}"]
    if kind == "field":
        lines.append(f"field: {field}")
    lines += [
        f"text: {text}",
        f"evidence: {evidence}" if evidence else "evidence: ''",
        f"created: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "source: dsh",
        "---",
        "",
        "由「与神对话」从对话里提炼；采纳前不属于四库。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="投递身份候选（只写队列）")
    parser.add_argument("action", choices=("add", "list"), help="add 投一张候选卡；list 看队列")
    parser.add_argument("--vault", default="", help="Vault 根目录（默认用 VAULT_DIR 或仓库 .env）")
    parser.add_argument("--kind", choices=("field", "memory"), default="memory", help="field=写身份字段；memory=写记忆与自传")
    parser.add_argument("--field", default="", help="kind=field 时指定字段：" + "、".join(FIELDS))
    parser.add_argument("--text", default="", help="一句话事实（≤200 字）")
    parser.add_argument("--evidence", default="", help="我的原话（≤500 字），用来复核")
    args = parser.parse_args(argv)

    vault = resolve_vault(args.vault)
    if not vault.is_dir():
        print(f"找不到 Vault：{vault}", file=sys.stderr)
        return 2

    if args.action == "list":
        items = list_drafts(vault)
        if not items:
            print("没有待确认的身份候选。")
            return 0
        print(f"待确认 {len(items)} 条：")
        for item in items:
            target = f"字段·{item['field_label']}" if item["kind"] == "field" else "记忆与自传"
            print(f"- {item['name']}  [{target}]  {item['text']}")
        return 0

    text = str(args.text or "").strip()
    if not text:
        print("--text 不能为空", file=sys.stderr)
        return 2
    if len(text) > MAX_TEXT:
        print(f"--text 最多 {MAX_TEXT} 字", file=sys.stderr)
        return 2
    evidence = str(args.evidence or "").strip()[:MAX_EVIDENCE]
    field = str(args.field or "").strip()
    if args.kind == "field":
        if field not in FIELDS:
            print(f"--field 必须是：{'、'.join(FIELDS)}", file=sys.stderr)
            return 2
    else:
        field = ""

    for item in list_drafts(vault):
        if item["text"] == text and item["field"] == field:
            print(f"已存在同样的候选，没有重复投递：{item['name']}")
            return 0

    path = _write_draft(vault, args.kind, field, text, evidence)
    print(f"已投递：{path.relative_to(vault).as_posix()}")
    print("到「自我档案 → 身份待确认」里采纳或丢弃；采纳才会写进四库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
