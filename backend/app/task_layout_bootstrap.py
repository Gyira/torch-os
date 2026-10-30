"""任务数据层启动迁移（2026-10-30）。

把旧布局一次性、幂等地整理成：

```
00_人生路/07_任务与待办/
  收集箱/ 已完成/ 回收站/ _系统/ <清单>...
```

迁移内容：

- `07_工作复盘` → `06_工作复盘`
- `06_目标与计划/04_习惯追踪` → `04_目标/04_习惯追踪`
- `08_任务与待办` → `07_任务与待办`
- 展开 `07_任务与待办/任务/`：清单文件夹上移，散落任务进收集箱
- done 任务统一进 `已完成/`，并写 frontmatter `清单`
- 旧 `.torch-os/task-trash` 批次搬进新回收站
- 同步 `dida_task_links.relpath`，避免第一次同步误判成本地编辑

迁移前会把相关目录复制到 `.torch-os/backups/task-layout/<stamp>/`；任何一步
失败都保留原文件，不覆盖、不删除非空目录。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import task_notes, task_trash_store, vault_layout

MARKER_REL = Path(".torch-os", "task-layout.json")
BACKUP_ROOT_REL = Path(".torch-os", "backups", "task-layout")
LOG_ROOT_REL = Path(".torch-os", "migrations")

_RENAME_PAIRS = (
    ("00_人生路/07_工作复盘", "00_人生路/06_工作复盘"),
    ("00_人生路/06_目标与计划/04_习惯追踪", "00_人生路/04_目标/04_习惯追踪"),
    ("00_人生路/08_任务与待办", "00_人生路/07_任务与待办"),
)
_BACKUP_DIRS = (
    "00_人生路/04_目标",
    "00_人生路/06_目标与计划",
    "00_人生路/06_工作复盘",
    "00_人生路/07_工作复盘",
    "00_人生路/07_任务与待办",
    "00_人生路/08_任务与待办",
    ".torch-os/task-trash",
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _remove_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def _move_or_merge(source: Path, target: Path, moves: list[dict[str, str]],
                   conflicts: list[str]) -> None:
    """目录/文件移动；目标已存在时合并，绝不覆盖文件。"""
    if not source.exists():
        return
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(source, target)
            moves.append({"source": str(source), "target": str(target)})
        except OSError:
            conflicts.append(f"{source} -> {target}")
        return
    if source.is_dir() and target.is_dir():
        for child in list(source.iterdir()):
            _move_or_merge(child, target / child.name, moves, conflicts)
        _remove_empty(source)
        return
    conflicts.append(f"{source} -> {target}")


def _unique_file(target: Path) -> Path:
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    index = 2
    while True:
        candidate = target.with_name(f"{stem} {index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _flatten_inner_task_dir(vault: Path, root: Path, moves: list[dict[str, str]],
                            conflicts: list[str]) -> int:
    inner = root / task_notes.LEGACY_INNER_DIR_NAME
    if not inner.is_dir():
        return 0
    moved = 0
    for child in list(inner.iterdir()):
        if child.is_dir():
            target = root / child.name
        elif child.suffix.lower() == ".md":
            target = root / task_notes.INBOX / child.name
        else:
            target = root / child.name
        if target.exists() and target.is_file():
            target = _unique_file(target)
        before = len(moves)
        _move_or_merge(child, target, moves, conflicts)
        moved += max(0, len(moves) - before)
    _remove_empty(inner)
    return moved


def _move_loose_root_tasks(vault: Path, root: Path, moves: list[dict[str, str]],
                           conflicts: list[str]) -> int:
    inbox = root / task_notes.INBOX
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    moved = 0
    for entry in entries:
        if entry.is_dir() or entry.suffix.lower() != ".md" or entry.name.startswith("."):
            continue
        inbox.mkdir(parents=True, exist_ok=True)
        target = _unique_file(inbox / entry.name)
        before = len(moves)
        _move_or_merge(entry, target, moves, conflicts)
        moved += max(0, len(moves) - before)
    return moved


def _ensure_list_folders(vault: Path) -> int:
    try:
        from . import tasks  # 延迟导入，避免任务模块初始化环
        state = tasks.read_tasks(vault, read_only=False)
    except Exception:
        return 0
    created = 0
    for entry in state.get("lists", []):
        name = str(entry.get("name") or "").strip()
        if not name or name.casefold() == task_notes.INBOX.casefold():
            continue
        if name.casefold() in {item.casefold() for item in task_notes.RESERVED_FOLDER_NAMES}:
            continue
        try:
            folder = task_notes.list_folder(vault, name)
            if not folder.is_dir():
                folder.mkdir(parents=True, exist_ok=True)
                created += 1
        except Exception:
            continue
    return created


def _move_done_tasks(vault: Path) -> tuple[int, list[dict[str, str]]]:
    moved = 0
    errors: list[dict[str, str]] = []
    try:
        notes, _warnings = task_notes.scan_task_notes(vault)
    except Exception as error:
        return 0, [{"stage": "scan", "error": type(error).__name__}]
    for note in notes:
        if not note.get("done") or note.get("storage") == "done":
            continue
        try:
            task_notes.rewrite_task_note(
                vault, note, folder=task_notes.INBOX if note["list_name"] == task_notes.INBOX
                else note["list_name"],
                title=note["title"], values=task_notes.note_values(note),
            )
            moved += 1
        except Exception as error:
            errors.append({"path": note.get("relpath", ""), "error": type(error).__name__})
    return moved, errors


def _migrate_old_trash(vault: Path, moves: list[dict[str, str]],
                       conflicts: list[str]) -> int:
    old = vault / task_trash_store.LEGACY_TRASH_DIR
    if not old.is_dir():
        return 0
    new_root = task_trash_store.trash_root(vault)
    new_root.mkdir(parents=True, exist_ok=True)
    moved = 0
    for batch in sorted(old.iterdir(), key=lambda item: item.name):
        if not batch.is_dir() or batch.is_symlink():
            continue
        target = new_root / batch.name
        before = len(moves)
        _move_or_merge(batch, target, moves, conflicts)
        moved += max(0, len(moves) - before)
    _remove_empty(old)
    return moved


def _backup(vault: Path, database: Any, stamp: str) -> Path:
    backup = vault / BACKUP_ROOT_REL / stamp
    backup.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for relative in _BACKUP_DIRS:
        source = vault / relative
        if not source.exists():
            continue
        target = backup / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
        copied.append(relative)
    if database is not None:
        db_path = Path(getattr(database, "path", ""))
        if db_path.is_file():
            try:
                with sqlite3.connect(db_path) as source_connection:
                    with sqlite3.connect(backup / "torch_os.sqlite3") as target_connection:
                        source_connection.backup(target_connection)
                copied.append(str(db_path))
            except Exception:
                pass
    (backup / "manifest.json").write_text(
        json.dumps({"created_at": datetime.now(timezone.utc).isoformat(),
                    "copied": copied}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    return backup


def _update_dida_relpaths(vault: Path, database: Any) -> int:
    if database is None:
        return 0
    try:
        notes, _warnings = task_notes.scan_task_notes(vault)
    except Exception:
        return 0
    by_dida = {str(note.get("dida_id") or ""): note for note in notes if note.get("dida_id")}
    if not by_dida:
        return 0
    updated = 0
    try:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT external_id, relpath FROM dida_task_links WHERE external_id != ''"
            ).fetchall()
            for row in rows:
                dida_id = str(row["external_id"] if hasattr(row, "keys") else row[0])
                old_rel = str(row["relpath"] if hasattr(row, "keys") else row[1] or "")
                note = by_dida.get(dida_id)
                if note is None or not old_rel:
                    continue
                if (vault / old_rel).exists():
                    continue
                connection.execute(
                    """UPDATE dida_task_links
                       SET relpath = ?, last_list_name = ?, last_local_revision = ?, updated_at = ?
                       WHERE external_id = ?""",
                    (note["relpath"], note["list_name"], note.get("revision", ""),
                     datetime.now(timezone.utc).isoformat(), dida_id),
                )
                updated += 1
    except Exception:
        return updated
    return updated


def plan_task_layout(vault_dir: Path | str) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    actions: list[str] = []
    for source, target in _RENAME_PAIRS:
        if (vault / source).exists():
            actions.append(f"{source} -> {target}")
    root = task_notes.task_root(vault)
    if (root / task_notes.LEGACY_INNER_DIR_NAME).is_dir():
        actions.append(f"展开 {root / task_notes.LEGACY_INNER_DIR_NAME}")
    for relative in vault_layout.TASK_SPECIAL_DIRS:
        if not (vault / relative).is_dir():
            actions.append(f"创建 {relative}")
    try:
        notes, _warnings = task_notes.scan_task_notes(vault)
        for note in notes:
            if note.get("done") and note.get("storage") != "done":
                actions.append(f"移动已完成任务 {note['relpath']}")
    except Exception:
        pass
    if (vault / task_trash_store.LEGACY_TRASH_DIR).is_dir():
        actions.append("迁移旧 .torch-os/task-trash")
    return {"dry_run": True, "actions": actions, "count": len(actions)}


def ensure_task_data_layout(vault_dir: Path | str, *, database: Any = None,
                            dry_run: bool = False) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    if dry_run:
        return plan_task_layout(vault)
    if not vault.is_dir():
        return {"status": "skipped", "reason": "vault_missing", "moved": 0}

    stamp = _stamp()
    moves: list[dict[str, str]] = []
    conflicts: list[str] = []
    errors: list[str] = []

    # 先判断是否需要做任何事，避免每次启动都备份。
    needs = plan_task_layout(vault)
    if not needs["count"]:
        return {"status": "done", "already_done": True, "moved": 0}

    backup = _backup(vault, database, stamp)
    for source, target in _RENAME_PAIRS:
        _move_or_merge(vault / source, vault / target, moves, conflicts)
    _remove_empty(vault / "00_人生路/06_目标与计划")

    try:
        vault_layout.ensure_layout(vault)
    except Exception as error:
        errors.append(f"ensure_layout:{type(error).__name__}")

    root = task_notes.task_root(vault)
    _flatten_inner_task_dir(vault, root, moves, conflicts)
    _move_loose_root_tasks(vault, root, moves, conflicts)
    list_folders = _ensure_list_folders(vault)
    done_moved, done_errors = _move_done_tasks(vault)
    errors.extend(f"{item.get('path')}:{item.get('error')}" for item in done_errors)
    trash_moved = _migrate_old_trash(vault, moves, conflicts)
    db_updated = _update_dida_relpaths(vault, database)

    finished = datetime.now(timezone.utc).isoformat()
    marker = {
        "schema": 1,
        "status": "done",
        "run_id": stamp,
        "started_at": stamp,
        "finished_at": finished,
        "backup_dir": str(backup.relative_to(vault)) if backup.exists() else "",
        "moves": moves,
        "conflicts": conflicts,
        "errors": errors,
        "done_tasks_moved": done_moved,
        "list_folders_created": list_folders,
        "trash_batches_moved": trash_moved,
        "dida_relpaths_updated": db_updated,
    }
    marker_path = vault / MARKER_REL
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    log_dir = vault / LOG_ROOT_REL / stamp
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "task-layout.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    if database is not None:
        try:
            with database.connect() as connection:
                connection.execute(
                    "INSERT INTO audit_events(event_type, detail_json, created_at) VALUES (?, ?, ?)",
                    ("task_layout_bootstrapped",
                     json.dumps({"moved": len(moves), "done": done_moved,
                                 "lists": list_folders, "trash": trash_moved,
                                 "dida": db_updated, "conflicts": len(conflicts)},
                                ensure_ascii=False),
                     datetime.now(timezone.utc).isoformat()),
                )
        except Exception:
            pass
    return marker


__all__ = ["MARKER_REL", "plan_task_layout", "ensure_task_data_layout"]
