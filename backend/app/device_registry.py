"""Local-only device identity and explicit workspace/path alias bindings."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .storage_layout import StorageLayout, checked_path, legacy_identity, workspace_identity


class DeviceRegistry:
    def __init__(self, local_root: Path | str):
        self.root = checked_path(local_root)

    @contextmanager
    def connect(self):
        checked_path(self.root)
        checked_path(self.root / "device-registry.sqlite3")
        self.root.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.root / "device-registry.sqlite3", timeout=30)
        con.row_factory = sqlite3.Row
        try:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS device(singleton INTEGER PRIMARY KEY CHECK(singleton=1), id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, notes TEXT NOT NULL, roots TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS aliases(alias TEXT PRIMARY KEY, workspace TEXT NOT NULL REFERENCES workspaces(id));
            """)
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def device_id(self):
        with self.connect() as con:
            row = con.execute("SELECT id FROM device WHERE singleton=1").fetchone()
            if row:
                from uuid import UUID
                return str(UUID(row["id"]))
            identity = str(uuid4())
            con.execute("INSERT INTO device VALUES(1,?)", (identity,))
            return identity

    def bind(self, layout: StorageLayout, *, legacy_paths=()):
        """Explicit migration/test operation. Never called by ordinary app startup."""
        if not layout.workspace_id or workspace_identity(layout.notes) != layout.workspace_id:
            raise ValueError("必须先验证笔记库的稳定身份")
        if self.root.is_relative_to(layout.notes) or layout.notes.is_relative_to(self.root):
            raise ValueError("设备注册表必须在笔记库之外")
        roots = {k: str(getattr(layout, k)) for k in ("notes", "state", "archive", "cache", "exports")}
        aliases = {legacy_identity(layout.notes)}
        for path in legacy_paths:
            previous = checked_path(path)
            identity = workspace_identity(previous)
            if identity is not None and identity != layout.workspace_id:
                raise ValueError("旧路径属于其他工作区，不能复用其身份")
            aliases.add(legacy_identity(previous))
        with self.connect() as con:
            for alias in aliases:
                row = con.execute("SELECT workspace FROM aliases WHERE alias=?", (alias,)).fetchone()
                if row and row["workspace"] != layout.workspace_id:
                    raise ValueError("旧路径已绑定其他工作区，请先核对迁移记录")
            con.execute("INSERT INTO workspaces VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET notes=excluded.notes,roots=excluded.roots",
                        (layout.workspace_id, str(layout.notes), json.dumps(roots)))
            for alias in aliases:
                con.execute("INSERT OR IGNORE INTO aliases VALUES(?,?)", (alias, layout.workspace_id))

    def lookup(self, identity: str):
        if not (self.root / "device-registry.sqlite3").exists():
            return None
        with self.connect() as con:
            row = con.execute("SELECT * FROM workspaces WHERE id=? OR id=(SELECT workspace FROM aliases WHERE alias=?)",
                              (identity, identity)).fetchone()
            if not row:
                return None
            if workspace_identity(row["notes"]) != row["id"]:
                raise ValueError("已注册笔记库缺失或身份变化，拒绝隐式创建或改绑")
            roots = json.loads(row["roots"])
            if roots.get("notes") != row["notes"]:
                raise ValueError("设备路径记录不一致")
            if self.root.is_relative_to(checked_path(row["notes"])):
                raise ValueError("设备注册表不能位于笔记库内")
            StorageLayout.split(**roots, workspace_id=row["id"])
            return {"workspace_id": row["id"], "roots": roots}
