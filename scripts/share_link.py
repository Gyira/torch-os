"""CLI for the same capture implementation used by Torch HTTP APIs."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.share_link import *  # retain legacy helper imports for scripts
from backend.app.share_capture import capture

def main(argv=None):
    parser = argparse.ArgumentParser(description="取回 ChatGPT / DeepSeek 分享或粘贴正文为 Markdown")
    parser.add_argument("link", nargs="?", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--text-file", type=Path, help="UTF-8 正文兜底，不访问网页")
    parser.add_argument("--rounds", default="")
    parser.add_argument("--answers-only", action="store_true")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS)
    args = parser.parse_args(argv)
    try:
        result = capture(args.link, rounds=args.rounds, answers_only=args.answers_only,
                         timeout=args.timeout, pasted_text=args.text_file.read_text(encoding="utf-8") if args.text_file else "")
        result["too_long"] = len(result["markdown"]) > args.max_chars
        if result["too_long"] and (args.out or not args.json):
            raise ShareLinkError("正文超限，请缩小范围。")
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(result["markdown"])
        if args.json:
            print(json.dumps({k: v for k, v in result.items() if k != "markdown"}, ensure_ascii=False, indent=2))
        elif not args.out:
            print(result["markdown"], end="")
    except (ShareLinkError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 3
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
