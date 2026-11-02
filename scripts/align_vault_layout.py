"""显式、离线的前端目录对齐。默认预览；--apply 执行；--rollback MANIFEST 回退。

只处理已知目录，拒绝链接/目录联接与同名覆盖；备份校验成功后才搬文件。
运行前停止该库的 Torch 后端，关闭正在编辑的笔记。不会调用模型或重提取记忆。
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import vault_layout as layout, vault_links


MOVES = {
    "00_人生路/09_随笔": layout.chapter_dir("essay"),
    "00_人生路/06_工作复盘/01_项目复盘": layout.chapter_dir("work_review_project"),
    "00_人生路/06_工作复盘/02_会议收获": layout.chapter_dir("work_review_meeting"),
    "00_人生路/06_工作复盘/03_踩坑记录": layout.chapter_dir("work_review_pitfall"),
    "00_人生路/06_工作复盘": "00_人生路/07_工作复盘",
    "02_生活运营/03_证件与事务": layout.chapter_dir("affairs"),
    "02_生活运营/04_财务报告": layout.chapter_dir("finance_report"),
    "02_生活运营/05_生活建议": layout.chapter_dir("life_advice"),
    "02_生活运营/06_家庭与智能家居": layout.chapter_dir("family"),
    "02_生活运营/06_健康报告": layout.chapter_dir("health_report"),
    "02_生活运营/07_情感与记忆": layout.chapter_dir("love_memory"),
    "02_生活运营/08_财富与记账": layout.chapter_dir("finance"),
    **dict(layout.OPS_LAYOUT_MOVES),
}
EMPTY_LEGACY = tuple(sorted({
    item[3].split("/")[0] + "/" + item[3].split("/")[1]
    for item in layout.CHAPTERS if item[1] == "self" and item[0] in layout.LEGACY_CHAPTER_KEYS
}))


def safe(root: Path, relative: str) -> Path:
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts or not part.parts:
        raise ValueError(f"非法相对路径：{relative}")
    path = root / part
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"路径越界：{relative}")
    for candidate in [path, *path.parents]:
        if candidate == root:
            break
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError(f"不处理链接/联接：{relative}")
    return path


def walk(root: Path, relative: str) -> tuple[list[str], list[str]]:
    base = safe(root, relative)
    files, dirs = [], []
    if not base.exists():
        return files, dirs
    if not base.is_dir():
        raise ValueError(f"预期为目录：{relative}")
    for directory, children, names in os.walk(base, followlinks=False):
        rel = Path(directory).relative_to(root).as_posix()
        safe(root, rel)
        dirs.append(rel)
        for name in children + names:
            child = f"{rel}/{name}"
            safe(root, child)
            if name in names:
                files.append(child)
    return sorted(files), sorted(dirs)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def remap(value: str, mapping: dict[str, str]) -> str:
    for old in sorted(mapping, key=len, reverse=True):
        if value == old or value.startswith(old + "/"):
            return mapping[old] + value[len(old):]
    return value


def plan(root: Path) -> dict:
    mapping = dict(MOVES)
    self_root = safe(root, layout.SELF_ROOT)
    if self_root.exists():
        for child in self_root.iterdir():
            if child.name.startswith("99_重构备份-"):
                mapping[child.relative_to(root).as_posix()] = ".torch-os/backups/self-archive/" + child.name
    sources, old_dirs, blocked = set(), set(), []
    for source in mapping:
        files, dirs = walk(root, source)
        sources.update(files)
        old_dirs.update(dirs)
    for source in EMPTY_LEGACY:
        files, dirs = walk(root, source)
        if files:
            blocked.append(source + "：仍有资料，需单独确认归属")
        else:
            old_dirs.update(dirs)
    files = []
    targets = set()
    for source in sorted(sources):
        target = remap(source, mapping)
        if target == source:
            continue
        destination = safe(root, target)
        if destination.exists() or target.casefold() in targets:
            raise ValueError(f"目标冲突，未执行：{target}")
        targets.add(target.casefold())
        files.append({"source": source, "target": target, "sha256": digest(safe(root, source))})
    # 保留已有本子，空的旧产物目录不带到新树。占位系统与前端入口对应。
    required = set(layout.OPS_SYSTEM_DIRS)
    lazy_dirs = {layout.chapter_dir(key) for key in (*layout.LAZY_CHAPTER_KEYS, "finance", "love_memory")}
    for source in old_dirs:
        target = remap(source, mapping)
        if target != source and target not in lazy_dirs:
            required.add(target)
    # 不给用户已删除的本子重建空目录。
    return {"mapping": mapping, "files": files, "remove_dirs": sorted(old_dirs, key=lambda s: (-s.count('/'), s)),
            "required_dirs": sorted(required), "blocked": blocked}


def json_paths(value, mapping):
    if isinstance(value, str):
        return remap(value, mapping)
    if isinstance(value, list):
        return [json_paths(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: json_paths(item, mapping) for key, item in value.items()}
    return value


def db_changes(connection, mapping):
    """只更新路径及结构化引用；记忆正文、审核决定和审计历史不改。"""
    fields = {
        "documents": ("path", "metadata_json"), "messages": ("citations_json",),
        "ai_turns": ("sources_json", "proposals_json", "structured_json"),
        "ai_proposals": ("payload_json", "sources_json", "result_json"),
        "journal_actions": ("source_path",), "dida_task_links": ("relpath",),
        "memory_hub_runs": ("snapshot_path",),
    }
    changes = []
    for table, columns in fields.items():
        present = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        for column in columns:
            if column not in present:
                continue
            for rowid, old in connection.execute(f'SELECT rowid, "{column}" FROM "{table}"'):
                if not isinstance(old, str):
                    continue
                if column.endswith("_json"):
                    try:
                        obj = json.loads(old)
                    except ValueError:
                        continue
                    updated = json_paths(obj, mapping)
                    new = json.dumps(updated, ensure_ascii=False) if updated != obj else old
                else:
                    new = remap(old, mapping)
                if old != new:
                    changes.append({"table": table, "column": column, "rowid": rowid, "before": old, "after": new})
    return changes


def write_manifest(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def apply(root: Path, database: Path | None = None) -> Path | None:
    result = plan(root)
    if not result["files"] and not result["remove_dirs"] and all(safe(root, p).is_dir() for p in result["required_dirs"]):
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_rel = f".torch-os/backups/frontend-layout/{stamp}"
    backup = safe(root, backup_rel)
    backup.mkdir(parents=True)
    manifest = backup / "manifest.json"
    result.update({"vault": str(root), "backup": backup_rel, "status": "preparing", "edits": [],
                   "created_dirs": [], "db_changes": [], "database": str(database) if database else ""})
    mapping = {row["source"]: row["target"] for row in result["files"]}
    # 移动笔记指向未移动附件的相对链接也需重算；不重写无关 wiki 链接。
    user_files = []
    for pillar in layout.PILLAR_DIRS.values():
        files, _ = walk(root, pillar)
        user_files.extend(files)
    user_files.extend(p.name for p in root.glob("*.md") if p.is_file())
    known_files = set(user_files)
    for name in user_files:
        if not name.endswith(".md") or "/99_重构备份-" in name:
            continue
        path = safe(root, name)
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        links = dict(mapping)
        if name in mapping:
            for link in vault_links.scan_links(text, name):
                if link["kind"] == "markdown" and link["target"] in known_files:
                    links.setdefault(link["target"], link["target"])
        rewritten, _ = vault_links.rewrite_links(text, links, name, mapping.get(name, name))
        if text != rewritten:
            result["edits"].append({"source": name, "target": mapping.get(name, name),
                                    "before": hashlib.sha256(raw).hexdigest(),
                                    "after": hashlib.sha256(rewritten.encode('utf-8')).hexdigest()})
            safe(root, f"{backup_rel}/rewritten/{name}").parent.mkdir(parents=True, exist_ok=True)
            safe(root, f"{backup_rel}/rewritten/{name}").write_bytes(rewritten.encode("utf-8"))
    originals = {row["source"] for row in result["files"] + result["edits"]}
    expected = {row["source"]: row["sha256"] for row in result["files"]}
    expected.update({row["source"]: row["before"] for row in result["edits"]})
    for name in originals:
        source = safe(root, name)
        dest = safe(root, f"{backup_rel}/originals/{name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        if digest(source) != expected[name] or digest(dest) != expected[name]:
            raise RuntimeError("备份校验失败，未移动资料")
    conn = sqlite3.connect(database) if database and database.is_file() else None
    try:
        if conn:
            with closing(sqlite3.connect(backup / "database.sqlite3")) as saved:
                conn.backup(saved)
            conn.execute("BEGIN IMMEDIATE")
            result["db_changes"] = db_changes(conn, result["mapping"])
        required = set(result["required_dirs"])
        required.update(str(Path(row['target']).parent).replace('\\', '/') for row in result["files"])
        for name in list(required):
            required.update(p.as_posix() for p in Path(name).parents if p.as_posix() != ".")
        result["created_dirs"] = sorted((p for p in required if not safe(root, p).exists()), key=lambda s: (s.count('/'), s))
        result["status"] = "applying"
        write_manifest(manifest, result)
        if conn:
            for row in result["db_changes"]:
                conn.execute(f'UPDATE "{row["table"]}" SET "{row["column"]}"=? WHERE rowid=?', (row["after"], row["rowid"]))
        for name in result["created_dirs"]:
            safe(root, name).mkdir(exist_ok=True)
        for row in result["files"]:
            source, dest = safe(root, row["source"]), safe(root, row["target"])
            if digest(source) != row["sha256"] or dest.exists():
                raise RuntimeError("资料在预览后变化，停止迁移")
            source.rename(dest)
            if digest(dest) != row["sha256"]:
                raise RuntimeError("迁移后校验失败")
        for row in result["edits"]:
            target = safe(root, row["target"])
            if digest(target) != row["before"]:
                raise RuntimeError("链接更新前文件变化，停止迁移")
            shutil.copy2(safe(root, f"{backup_rel}/rewritten/{row['source']}"), target)
        for name in result["remove_dirs"]:
            path = safe(root, name)
            if path.exists():
                path.rmdir()  # 仅移除空目录；非空/并发新增文件导致失败，不递归删除。
        if conn:
            conn.commit()
        result["status"] = "done"
        write_manifest(manifest, result)
    except Exception:
        if conn:
            conn.rollback()
        result["status"] = "interrupted"
        write_manifest(manifest, result)
        raise
    finally:
        if conn:
            conn.close()
    return manifest


def rollback(root: Path, manifest: Path):
    manifest = safe(root, manifest.resolve().relative_to(root).as_posix())
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if Path(data["vault"]).resolve() != root:
        raise ValueError("清单不属于此库")
    edits = {row["target"]: row for row in data["edits"]}
    # 整批预检：回退不覆盖迁移后新增/修改的内容。
    for row in data["files"]:
        source, target = safe(root, row["source"]), safe(root, row["target"])
        if source.exists():
            if target.exists() or digest(source) != row["sha256"]:
                raise ValueError("原路径已被占用；未执行回退")
        elif not target.is_file() or digest(target) not in {row["sha256"], edits.get(row["target"], {}).get("after")}:
            raise ValueError("迁移后资料已变化；未执行回退")
    for row in data["edits"]:
        target = safe(root, row["target"])
        if target.exists() and digest(target) not in {row["before"], row["after"]}:
            raise ValueError("链接文件已编辑；未执行回退")
        original = safe(root, f"{data['backup']}/originals/{row['source']}")
        if digest(original) != row["before"]:
            raise ValueError("备份损坏；未执行回退")
    conn = sqlite3.connect(data["database"]) if data["database"] else None
    try:
        if conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in data["db_changes"]:
                current = conn.execute(f'SELECT "{row["column"]}" FROM "{row["table"]}" WHERE rowid=?', (row["rowid"],)).fetchone()
                if current is None or current[0] not in (row["before"], row["after"]):
                    raise ValueError("数据库引用已变化；未执行回退")
        for name in reversed(data["remove_dirs"]):
            safe(root, name).mkdir(parents=True, exist_ok=True)
        for row in data["edits"]:
            target = safe(root, row["target"])
            if target.exists():
                shutil.copy2(safe(root, f"{data['backup']}/originals/{row['source']}"), target)
        for row in reversed(data["files"]):
            source, target = safe(root, row["source"]), safe(root, row["target"])
            if not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                target.rename(source)
        for name in reversed(data["created_dirs"]):
            path = safe(root, name)
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        if conn:
            for row in data["db_changes"]:
                conn.execute(f'UPDATE "{row["table"]}" SET "{row["column"]}"=? WHERE rowid=?', (row["before"], row["rowid"]))
            conn.commit()
        data["status"] = "rolled_back"
        write_manifest(manifest, data)
    finally:
        if conn:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    root = args.vault.resolve(strict=True)
    if args.rollback:
        rollback(root, args.rollback)
        print("回退完成")
    elif args.apply:
        manifest = apply(root, args.database or root / ".torch-os/torch_os.sqlite3")
        print(str(manifest) if manifest else "目录已对齐，无需迁移")
    else:
        result = plan(root)
        print(json.dumps({"moves": [f"{a} → {b}" for a, b in result['mapping'].items() if safe(root, a).exists()],
                          "files": len(result['files']), "remove_empty_dirs": len(result['remove_dirs']),
                          "blocked": result['blocked']}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
