"""四库迁移执行与回滚（2026-10-21）。

安全顺序：预检冲突 → 完整快照（含附件）→ 校验计划哈希 → 逐文件原子移动
→ 统一改写内部链接 → 重建索引 → 写布局标记与报告。
每一步都写 `.torch-os/migrations/<run_id>/journal.jsonl`，支持回滚与崩溃恢复。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import vault_layout, vault_links
from .backup import create_backup, verify_backup
from .db import Database, utc_now
from .markdown_store import guarded_write, load_note
from .migration_plan import MigrationPlan, plan_markdown, plan_migration
from .vault import scan_vault

SHANGHAI = ZoneInfo("Asia/Shanghai")
HIDDEN_TOP = {".obsidian", ".git", ".trash", ".claude", ".claudian", ".torch-os"}
MIGRATIONS_DIR = Path(".torch-os") / "migrations"
LAYOUT_FILE = Path(".torch-os") / "layout.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_id() -> str:
    stamp = datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


def _walk_vault(vault: Path) -> list[str]:
    found: list[str] = []
    for directory, subdirs, filenames in os.walk(vault, followlinks=False):
        subdirs[:] = sorted(name for name in subdirs
                            if name not in HIDDEN_TOP and not name.startswith(".")
                            and not (Path(directory) / name).is_symlink())
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.is_symlink():
                continue
            found.append(path.relative_to(vault).as_posix())
    return found


def _snapshot_vault(vault: Path, run_dir: Path) -> dict[str, str]:
    """完整快照（含附件），返回 relative→sha256；这是回滚的最终兜底。"""
    target_root = run_dir / "vault-backup"
    manifest: dict[str, str] = {}
    for relative in _walk_vault(vault):
        source = vault / relative
        try:
            digest = _sha256(source)
        except OSError as error:
            raise RuntimeError(f"快照读取失败：{relative}: {error}") from error
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if _sha256(target) != digest:
            raise RuntimeError(f"快照校验失败：{relative}")
        manifest[relative] = digest
    (run_dir / "snapshot.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                         encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _append_journal(run_dir: Path, entry: dict[str, Any]) -> None:
    with (run_dir / "journal.jsonl").open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _move(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RuntimeError(f"目标已存在：{target}")
    try:
        os.replace(source, target)
    except OSError:
        # 跨盘：复制到临时文件、校验后删除源。
        temporary = target.with_name(target.name + f".migrate-{secrets.token_hex(4)}")
        shutil.copy2(source, temporary)
        if _sha256(temporary) != _sha256(source):
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"跨盘复制校验失败：{source}")
        os.replace(temporary, target)
        source.unlink()


def apply_migration(vault_dir: Path | str, database: Database | None = None, *,
                    expected_hash: str = "", confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        raise ValueError("迁移需要显式确认")
    vault = Path(vault_dir).resolve()
    plan = plan_migration(vault)
    if not expected_hash or expected_hash != plan.plan_hash:
        raise ValueError("迁移计划已变化，请重新预览")
    if plan.conflicts:
        raise ValueError(f"还有 {len(plan.conflicts)} 个冲突，先处理后再迁移")
    moves = plan.moves()
    if not moves:
        return {"applied": False, "reason": "没有需要移动的文件",
                "plan_hash": plan.plan_hash}
    run_id = _run_id()
    run_dir = vault / MIGRATIONS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "plan.json", plan.as_dict())
    state = {"run_id": run_id, "status": "snapshotting", "started_at": utc_now(),
             "plan_hash": plan.plan_hash}
    _write_json(run_dir / "state.json", state)

    backup_info: dict[str, Any] | None = None
    if database is not None and Path(database.path).is_file():
        backup_dir = Path(database.path).parent / "backups"
        backup_info = create_backup(database, vault, backup_dir)
        verified = verify_backup(Path(backup_info["path"]))
        backup_info["verified"] = bool(verified.get("verified", True))
        if not backup_info["verified"]:
            raise RuntimeError("迁移前备份校验失败")
    manifest = _snapshot_vault(vault, run_dir)

    state.update({"status": "applying", "backup": backup_info,
                  "snapshot_files": len(manifest), "moved": 0})
    _write_json(run_dir / "state.json", state)
    vault_layout.ensure_layout(vault)

    moved: list[dict[str, str]] = []
    try:
        for item in moves:
            source = vault / item.source
            target = vault / item.target
            if not source.exists():
                if target.exists() and _sha256(target) == item.sha256:
                    _append_journal(run_dir, {"action": "skip_moved", "source": item.source,
                                              "target": item.target, "sha256": item.sha256})
                    moved.append({"source": item.source, "target": item.target,
                                  "sha256": item.sha256})
                    continue
                raise RuntimeError(f"源文件已消失且目标不匹配：{item.source}")
            if _sha256(source) != item.sha256:
                raise RuntimeError(f"源文件在预览后发生变化：{item.source}")
            _move(source, target)
            actual = _sha256(target)
            if actual != item.sha256:
                raise RuntimeError(f"移动后校验失败：{item.source}")
            moved.append({"source": item.source, "target": item.target,
                          "sha256": item.sha256})
            _append_journal(run_dir, {"action": "move", **moved[-1]})
            state["moved"] = len(moved)
            _write_json(run_dir / "state.json", state)

        mapping = {entry["source"]: entry["target"] for entry in moved}
        inverse = {entry["target"]: entry["source"] for entry in moved}
        rewritten: list[dict[str, str]] = []
        for relative in _walk_vault(vault):
            if not relative.casefold().endswith(".md"):
                continue
            path = vault / relative
            old_relative = inverse.get(relative, relative)
            try:
                loaded = load_note(path)
            except Exception:
                continue
            if not loaded["exists"]:
                continue
            text = "\n".join(loaded["lines"])
            rewritten_text, changes = vault_links.rewrite_links(
                text, mapping, source_old=old_relative, source_new=relative)
            if not changes or rewritten_text == text:
                continue
            before = _sha256(path)
            guarded_write(loaded, rewritten_text.split("\n"))
            rewritten.append({"path": relative, "before_sha256": before,
                              "after_sha256": _sha256(path)})
            _append_journal(run_dir, {"action": "rewrite_links", "path": relative,
                                      "before_sha256": before})
        state.update({"status": "indexing", "rewritten": len(rewritten)})
        _write_json(run_dir / "state.json", state)

        _write_json(vault / LAYOUT_FILE, {
            "layout_version": vault_layout.LAYOUT_VERSION,
            "run_id": run_id, "plan_hash": plan.plan_hash, "applied_at": utc_now(),
        })
        if database is not None:
            scan_result = scan_vault(vault, database)
        else:
            scan_result = {}
        report = {
            "run_id": run_id, "plan_hash": plan.plan_hash,
            "applied_at": utc_now(), "stats": plan.stats(),
            "moved": moved, "rewritten": rewritten,
            "backup": backup_info, "scan": scan_result,
            "layout": vault_layout.layout_status(vault),
        }
        _write_json(run_dir / "report.json", report)
        report_dir = (vault / vault_layout.chapter_dir("task") / "_系统" / "迁移记录")
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{run_id}.md").write_text(
            plan_markdown(plan) + "\n\n## 结果\n\n```json\n"
            + json.dumps({"stats": plan.stats(), "scan": scan_result,
                          "rewritten": rewritten}, ensure_ascii=False, indent=2)
            + "\n```\n", encoding="utf-8")
        state.update({"status": "done", "finished_at": utc_now()})
        _write_json(run_dir / "state.json", state)
        if database is not None:
            database.audit("vault_migration_applied", {
                "run_id": run_id, "plan_hash": plan.plan_hash,
                "moved": len(moved), "rewritten": len(rewritten),
                "conflicts": 0,
            })
        return {"applied": True, **report}
    except Exception as error:
        state.update({"status": "failed", "error": str(error), "failed_at": utc_now()})
        _write_json(run_dir / "state.json", state)
        raise


def rollback_migration(vault_dir: Path | str, run_id: str,
                       database: Database | None = None) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    run_dir = vault / MIGRATIONS_DIR / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(f"迁移记录不存在：{run_id}")
    journal = run_dir / "journal.jsonl"
    if not journal.is_file():
        raise RuntimeError("迁移日志缺失，无法安全回滚")
    entries = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    moves = [entry for entry in entries if entry.get("action") in {"move", "skip_moved"}]
    rewritten = [entry for entry in entries if entry.get("action") == "rewrite_links"]
    restored_links = 0
    for entry in rewritten:
        backup = run_dir / "vault-backup" / entry["path"]
        if backup.is_file():
            target = vault / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
            restored_links += 1
    restored_moves = 0
    for entry in reversed(moves):
        source = vault / entry["source"]
        target = vault / entry["target"]
        if source.exists():
            continue
        if not target.exists():
            raise RuntimeError(f"回滚时目标已不存在：{entry['target']}")
        if _sha256(target) != entry["sha256"]:
            raise RuntimeError(f"回滚时目标内容已变化：{entry['target']}")
        _move(target, source)
        restored_moves += 1
    marker = vault / LAYOUT_FILE
    if marker.exists():
        marker.unlink()
    if database is not None:
        scan_result = scan_vault(vault, database)
        database.audit("vault_migration_rolled_back", {
            "run_id": run_id, "moves": restored_moves, "links": restored_links,
        })
    else:
        scan_result = {}
    report = {"run_id": run_id, "rolled_back_at": utc_now(),
              "moves": restored_moves, "links": restored_links, "scan": scan_result}
    _write_json(run_dir / "rollback.json", report)
    return report


def migration_status(vault_dir: Path | str) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    root = vault / MIGRATIONS_DIR
    runs: list[dict[str, Any]] = []
    if root.is_dir():
        for directory in sorted(root.iterdir(), reverse=True):
            if not directory.is_dir():
                continue
            state = _read_json(directory / "state.json") or {}
            report = _read_json(directory / "report.json") or {}
            runs.append({
                "run_id": directory.name,
                "status": state.get("status", "unknown"),
                "started_at": state.get("started_at"),
                "finished_at": state.get("finished_at"),
                "stats": report.get("stats") or state.get("stats") or {},
                "error": state.get("error", ""),
            })
    return {"layout": vault_layout.layout_status(vault),
            "layout_version": vault_layout.LAYOUT_VERSION,
            "runs": runs[:20]}


__all__ = ["apply_migration", "rollback_migration", "migration_status"]
