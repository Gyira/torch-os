from pathlib import Path
import argparse
import json
import sys
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.storage_audit import audit_references


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="只读扫描 Markdown 引用；不修改笔记")
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.resolve().is_relative_to(args.source.resolve()):
        parser.error("报告必须放在源库之外")
    result = audit_references(args.source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"scanned_notes": result["scanned_notes"], "references": dict(Counter(r["status"] for r in result["references"])),
                      "blocked": len(result["blocked"]), "safe_to_migrate": False}))
