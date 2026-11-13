"""Metadata-only P0 inventory. Never opens SQLite or reads note/credential bodies."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def inventory(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("工作区目录不存在")
    groups, databases, conflicts, errors = {}, [], [], []
    count = total = md_bytes = md_count = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()
                   and not (Path(directory) / d).is_junction()]
        for name in files:
            path = Path(directory) / name
            if path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            try:
                size = path.stat().st_size
            except OSError:
                errors.append(relative)
                continue
            count += 1
            total += size
            top = relative.split("/")[0]
            group = groups.setdefault(top, {"files": 0, "bytes": 0})
            group["files"] += 1
            group["bytes"] += size
            if path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
                databases.append({"path": relative, "bytes": size, "authority": "待按实际读写代码核对，不可默认视为缓存"})
            if "conflict" in name.lower() or "冲突" in name:
                conflicts.append(relative)
            if path.suffix.lower() == ".md" and top.startswith(("00_", "01_", "02_", "03_")):
                md_count += 1
                md_bytes += size
    return {"files": count, "bytes": total, "business_markdown": {"files": md_count, "bytes": md_bytes},
            "groups": groups, "databases": databases, "conflicts": conflicts, "unreadable": errors,
            "read_only": True, "note_bodies_read": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.resolve().is_relative_to(args.vault.resolve()):
        parser.error("盘点报告必须写在数据工作区之外")
    result = inventory(args.vault)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("files", "bytes", "business_markdown", "read_only")}, ensure_ascii=False))
