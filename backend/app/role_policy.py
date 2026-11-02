"""Code-owned role read scopes. Prompt cards cannot grant filesystem access."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import HTTPException

from . import vault_layout as layout

POLICY_VERSION = "B3-1-v1"
CURRENT_READ_POLICY: ContextVar[Any] = ContextVar("role_read_policy", default=None)


@contextmanager
def scoped_reads(policy):
    token = CURRENT_READ_POLICY.set(policy)
    try:
        yield
    finally:
        CURRENT_READ_POLICY.reset(token)


def is_scoped_read() -> bool:
    return CURRENT_READ_POLICY.get() is not None


def guard_note_read(vault, relative):
    policy = CURRENT_READ_POLICY.get()
    if policy is not None:
        if Path(vault).resolve() != policy.vault:
            raise RoleScopeError("资料属于另一个数据库")
        policy.check_path(relative, habit_summary=True)
HEALTH = layout.chapter_dir("health")
FINANCE = PurePosixPath(layout.chapter_dir("finance")).parent.as_posix()
HABITS = layout.chapter_dir("habit")
ROLE_ROOTS = {
    "mentor": tuple(layout.PILLAR_DIRS.values()),
    "journal_companion": (layout.PILLAR_DIRS["life"],),
    "academic_mentor": (layout.PILLAR_DIRS["knowledge"],),
    "health_doctor": (HEALTH,),
    "finance_advisor": (FINANCE,),
    "love_master": (layout.LOVE_ROOT,),
    "life_assistant": (layout.PILLAR_DIRS["ops"],),
}
SOURCE_ROOTS = {
    "identity": (layout.SELF_ARCHIVE_ROOT,),
    "journal": (layout.chapter_dir("journal"),),
    "journals_recent": (layout.chapter_dir("journal"),),
    "tasks": (layout.TASK_ROOT, HABITS),
    "health": (HEALTH,), "finance": (FINANCE,), "love": (layout.LOVE_ROOT,),
    "self_archive": (layout.SELF_ARCHIVE_ROOT,),
    "mentor_memory": (layout.SELF_MENTOR_MEMORY_DIR,),
    "mentor_questions": (layout.SELF_ARCHIVE_ROOT, layout.SELF_MENTOR_QUESTIONS_DIR),
    "recent_notes": (layout.PILLAR_DIRS["knowledge"],),
    "ops_summary": (layout.TASK_ROOT, HABITS, HEALTH, FINANCE),
    "life_report": (layout.PILLAR_DIRS["ops"], HABITS),
    "bazi_chart": (layout.SELF_BAZI_ROOT,),
}


class RoleScopeError(HTTPException):
    def __init__(self, message: str = "该资料不在当前角色的读取范围内"):
        super().__init__(403, f"role_scope_denied: {message}")


def active_role(role_id: str) -> str:
    if role_id == "yiling":
        raise HTTPException(409, "role_retired: 旧忆灵仅保留历史，请前往自我档案 → 人生导师继续。")
    # Compatibility name only: never a separately registered identity/session.
    if role_id == "health_estimator":
        return "health_doctor"
    if role_id not in ROLE_ROOTS:
        raise RoleScopeError("角色没有已登记的资料范围")
    return role_id


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


@dataclass(frozen=True)
class RolePolicy:
    role_id: str
    vault: Path

    @classmethod
    def for_role(cls, role_id: str, vault: Path | str) -> "RolePolicy":
        return cls(active_role(role_id), Path(vault).resolve())

    def fingerprint(self, mode: str) -> str:
        data = [POLICY_VERSION, str(self.vault).casefold(), self.role_id, mode,
                ROLE_ROOTS[self.role_id], SOURCE_ROOTS]
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def check_path(self, relative: str, *, habit_summary: bool = False,
                   note: bool = False) -> Path:
        raw = str(relative).replace("\\", "/")
        parts = PurePosixPath(raw).parts
        if (not parts or raw.startswith("/") or ":" in raw
                or any(p in {".", ".."} or p.startswith(".") for p in raw.split("/"))):
            raise RoleScopeError("资料路径无效或属于系统数据")
        roots = ROLE_ROOTS[self.role_id]
        allowed = any(_under(raw, root) for root in roots)
        if habit_summary and self.role_id == "life_assistant" and _under(raw, HABITS):
            allowed = True
        if not allowed:
            raise RoleScopeError()
        if note and (PurePosixPath(raw).suffix.lower() != ".md"
                     or any(p in {"_系统", "_备份", "backups", "logs"} for p in parts)):
            raise RoleScopeError("只能引用业务 Markdown 笔记")
        target = self.vault
        for part in parts:
            target /= part
            try:
                info = target.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise RoleScopeError("资料路径包含符号链接或目录联接")
        # All segments are relative and link-free; do not resolve the leaf again.
        # Windows resolution can open a file while a cooperating writer replaces it.
        return target

    def check_source(self, source: str, *, love_settings: Any = None) -> None:
        roots = SOURCE_ROOTS.get(source)
        if roots is None:
            raise RoleScopeError("未登记的资料来源")
        # Complete authorization precedes filesystem traversal and content reads.
        paths = [self.check_path(root, habit_summary=source in {"tasks", "ops_summary", "life_report"})
                 for root in roots]
        for path in paths:
            # Metadata only; never follow a junction/symlink into another domain.
            for directory, dirs, files in os.walk(path, followlinks=False):
                for name in dirs + files:
                    child = Path(directory) / name
                    info = child.lstat()
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        raise RoleScopeError("资料目录包含链接，请先恢复为普通文件或目录")
        if source in {"love", "life_report"} and love_settings is not None:
            from .love_layout import love_paths
            expected = love_paths(self.vault)
            # Reject legacy/external databases and tampered page paths before querying.
            if type(love_settings) is not type(expected) or love_settings != expected:
                raise RoleScopeError("恋爱资料必须来自当前库的固定目录")

    def validate_selectors(self, selectors: list[Any], *, love_settings: Any = None) -> None:
        # Two passes: an invalid later selector must not cause an earlier source read.
        for item in selectors:
            if not isinstance(item, dict):
                raise RoleScopeError("资料选择器格式无效")
            source = str(item.get("entry") or "")
            if source == "note":
                self.check_path(str(item.get("path") or ""), note=True)
            elif source not in SOURCE_ROOTS:
                raise RoleScopeError("未登记的资料来源")
            else:
                for root in SOURCE_ROOTS[source]:
                    self.check_path(root, habit_summary=source in {"tasks", "ops_summary", "life_report"})
        for item in selectors:
            if item["entry"] != "note":
                self.check_source(item["entry"], love_settings=love_settings)


@dataclass(frozen=True)
class PreparedContext:
    """Created by the service after authorizing a server-owned source builder."""
    role_id: str
    vault: Path
    source: str
    fingerprint: str
    value: Any
    owner: Any

    @property
    def text(self) -> str:
        return str(self.value[0] if isinstance(self.value, tuple) else self.value)
