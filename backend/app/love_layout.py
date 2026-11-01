"""恋爱系统 v2 数据布局（2026-11-01）。

所有私人数据都在当前个人数据库（Obsidian 库）的
``02_生活运营/01_恋爱系统/`` 下，并按前端七个页面各建一个子目录：

- ``01_恋爱记忆``：Markdown 记忆与“本子”文件夹；没有数据库。
- ``02_微信聊天``：``chat.db``、加密凭据、媒体与导出。
- ``03_排班日历``：排班表 Excel、值班备注与手动覆盖。
- ``04_经期关怀``：``cycle.db``。
- ``05_恋爱相册``：``albums.db``、照片、视频与缩略图。
- ``06_八字合婚``：``bazi.db``。
- ``07_恋爱大师``：``analysis.db`` 与生成的报告。

旧 ``<个人数据库>/.torch-os/love/`` 只作为迁移来源，迁移后保留原文件。
本模块只负责路径与目录，不读写业务数据；各页面模块自己管理数据库。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .vault_connection import VaultReadOnlyError, ensure_vault_writable

LOVE_ROOT = "02_生活运营/01_恋爱系统"
MEMORY_DIR = f"{LOVE_ROOT}/01_恋爱记忆"
CHAT_DIR = f"{LOVE_ROOT}/02_微信聊天"
SCHEDULE_DIR = f"{LOVE_ROOT}/03_排班日历"
CYCLE_DIR = f"{LOVE_ROOT}/04_经期关怀"
GALLERY_DIR = f"{LOVE_ROOT}/05_恋爱相册"
BAZI_DIR = f"{LOVE_ROOT}/06_八字合婚"
MASTER_DIR = f"{LOVE_ROOT}/07_恋爱大师"
ARCHIVE_DIR = f"{LOVE_ROOT}/_归档"

# 本子：固定章节 + 自定义文件夹。
JOURNAL_NOTEBOOK = "恋爱日志"
AI_DRAFT_NOTEBOOK = "AI 草稿"
JOURNAL_CHAPTER = "love_journal"
DRAFT_CHAPTER = "love_drafts"
CUSTOM_PREFIX = "custom:love:"
CUSTOM_NOTEBOOK_PILLARS = ("life", "knowledge", "love")

# 数据库与关键文件。
CHAT_DB = "chat.db"
CHAT_CREDENTIALS = ".credentials.json"
CHAT_MEDIA_DIR = "media"
CHAT_EXPORTS_DIR = "exports"
SCHEDULE_EXCEL_DIR = "xlsx"
SCHEDULE_NOTES = "schedule-notes.json"
SCHEDULE_OVERRIDES = "overrides.json"
CYCLE_DB = "cycle.db"
GALLERY_DB = "albums.db"
GALLERY_PHOTOS = "照片"
GALLERY_VIDEOS = "视频"
GALLERY_THUMBS = "thumbnails"
BAZI_DB = "bazi.db"
MASTER_DB = "analysis.db"
MASTER_REPORTS = "reports"

# 旧版数据目录（相对于个人数据库的 .torch-os）。
LEGACY_LOVE_DIR = "love"
LEGACY_DB = "love_analysis.db"
LEGACY_KEY = ".api_key.key"


@dataclass(frozen=True)
class LovePaths:
    """所有页面路径的只读视图；构造时不做任何磁盘操作。"""

    root: Path
    memory: Path
    chat: Path
    schedule: Path
    cycle: Path
    gallery: Path
    bazi: Path
    master: Path
    archive: Path

    @property
    def chat_db(self) -> Path:
        return self.chat / CHAT_DB

    @property
    def chat_credentials(self) -> Path:
        return self.chat / CHAT_CREDENTIALS

    @property
    def chat_media(self) -> Path:
        return self.chat / CHAT_MEDIA_DIR

    @property
    def chat_exports(self) -> Path:
        return self.chat / CHAT_EXPORTS_DIR

    @property
    def schedule_excel(self) -> Path:
        return self.schedule / SCHEDULE_EXCEL_DIR

    @property
    def schedule_notes(self) -> Path:
        return self.schedule / SCHEDULE_NOTES

    @property
    def schedule_overrides(self) -> Path:
        return self.schedule / SCHEDULE_OVERRIDES

    @property
    def cycle_db(self) -> Path:
        return self.cycle / CYCLE_DB

    @property
    def gallery_db(self) -> Path:
        return self.gallery / GALLERY_DB

    @property
    def gallery_photos(self) -> Path:
        return self.gallery / GALLERY_PHOTOS

    @property
    def gallery_videos(self) -> Path:
        return self.gallery / GALLERY_VIDEOS

    @property
    def gallery_thumbs(self) -> Path:
        return self.gallery / GALLERY_THUMBS

    @property
    def bazi_db(self) -> Path:
        return self.bazi / BAZI_DB

    @property
    def master_db(self) -> Path:
        return self.master / MASTER_DB

    @property
    def master_reports(self) -> Path:
        return self.master / MASTER_REPORTS

    def as_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "memory": str(self.memory),
            "chat": str(self.chat),
            "schedule": str(self.schedule),
            "cycle": str(self.cycle),
            "gallery": str(self.gallery),
            "bazi": str(self.bazi),
            "master": str(self.master),
            "archive": str(self.archive),
        }


def love_paths(vault_dir: Path | str) -> LovePaths:
    root = Path(vault_dir).resolve()
    return LovePaths(
        root=root / LOVE_ROOT,
        memory=root / MEMORY_DIR,
        chat=root / CHAT_DIR,
        schedule=root / SCHEDULE_DIR,
        cycle=root / CYCLE_DIR,
        gallery=root / GALLERY_DIR,
        bazi=root / BAZI_DIR,
        master=root / MASTER_DIR,
        archive=root / ARCHIVE_DIR,
    )


def legacy_love_dir(database_path: Path | str) -> Path:
    """旧版恋爱数据目录：<个人数据库>/.torch-os/love。"""
    return Path(database_path).resolve().parent / LEGACY_LOVE_DIR


def ensure_love_layout(vault_dir: Path | str, *, read_only: bool = False,
                       create: bool = True) -> LovePaths:
    """返回路径并（可选）创建所有页面目录。

    读取状态时不要创建目录；写入/首次启动时调用 ``create=True``。
    只读模式允许读取，但拒绝创建。
    """
    paths = love_paths(vault_dir)
    if not create:
        return paths
    try:
        ensure_vault_writable(read_only, "建立恋爱系统数据目录")
    except VaultReadOnlyError:
        return paths
    for directory in (
        paths.root, paths.memory, paths.memory / JOURNAL_NOTEBOOK,
        paths.memory / AI_DRAFT_NOTEBOOK, paths.chat, paths.chat_media,
        paths.chat_exports, paths.schedule, paths.schedule_excel,
        paths.cycle, paths.gallery, paths.gallery_photos,
        paths.gallery_videos, paths.gallery_thumbs, paths.bazi,
        paths.master, paths.master_reports, paths.archive,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def relative_love_path(vault_dir: Path | str, path: Path | str) -> str:
    """绝对路径 → 以库根为基准的 POSIX 相对路径；不在库内抛 ValueError。"""
    root = Path(vault_dir).resolve()
    target = Path(path).resolve()
    if not target.is_relative_to(root):
        raise ValueError("路径不在当前个人数据库内")
    return target.relative_to(root).as_posix()


def love_root_relative() -> str:
    return LOVE_ROOT


def environment_love_root() -> str | None:
    """兼容旧环境的显式路径；只作为迁移来源，不再作为运行时权威。"""
    value = (os.environ.get("LOVE_DATA_DIR") or "").strip()
    return value or None


__all__ = [
    "LOVE_ROOT", "MEMORY_DIR", "CHAT_DIR", "SCHEDULE_DIR", "CYCLE_DIR",
    "GALLERY_DIR", "BAZI_DIR", "MASTER_DIR", "ARCHIVE_DIR",
    "JOURNAL_NOTEBOOK", "AI_DRAFT_NOTEBOOK", "JOURNAL_CHAPTER", "DRAFT_CHAPTER",
    "CUSTOM_PREFIX", "CUSTOM_NOTEBOOK_PILLARS", "LovePaths", "love_paths",
    "legacy_love_dir", "ensure_love_layout", "relative_love_path",
    "love_root_relative", "environment_love_root",
]
