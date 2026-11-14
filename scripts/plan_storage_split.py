"""Write a non-executable storage classification report outside the personal vault."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.storage_layout import new_layout, migration_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--target-base", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.resolve().is_relative_to(args.source.resolve()) or args.out.resolve().is_relative_to(args.target_base.resolve()):
        parser.error("报告必须保存在源库和目标目录之外")
    result = migration_plan(args.source, new_layout(args.target_base))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"files": len(result["files"]), "dry_run": True, "executable": False}))


if __name__ == "__main__":
    main()
