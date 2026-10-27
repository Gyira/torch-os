"""恋爱系统的数据目录与一次性迁移（2026-10-27）。

运行时数据统一放在当前个人数据库的 ``<个人数据库>/.torch-os/love/``：

- ``love_analysis.db``     恋爱数据库
- ``.api_key.key``         DeepSeek Key 的 AES 加密密钥文件
- ``xlsx/``                排班表 Excel
- ``schedule-notes.json``  值班日历备注（首次写备注时创建）
- ``cn_stopwords.txt``     分词停用词（旧项目资源，可选）

旧版本的数据（原「恋爱分析助手」数据库、仓库 ``data/love``、原项目目录）只在**目标文件
不存在**时复制过来，原文件保留；重复启动不会覆盖已经在新目录里的数据。
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_NAME = "love_analysis.db"
KEY_NAME = ".api_key.key"
EXCEL_DIR_NAME = "xlsx"
STOPWORDS_NAME = "cn_stopwords.txt"


def _same_path(first: Path | None, second: Path | None) -> bool:
    if not first or not second:
        return False
    try:
        return Path(first).resolve() == Path(second).resolve()
    except OSError:
        return False


def _copy_sqlite(source: Path, destination: Path) -> None:
    """Copy a SQLite database including committed WAL state, without touching the source."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(source)) as source_db, sqlite3.connect(str(destination)) as target_db:
        source_db.backup(target_db)


def _copy_file(source: Path, destination: Path) -> bool:
    if not source.is_file() or destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def migrate_love_data(
    data_dir: Path,
    *,
    legacy_db_path: Path | None = None,
    legacy_key_file: Path | None = None,
    legacy_excel_dir: Path | None = None,
    legacy_stopwords_path: Path | None = None,
) -> dict[str, Any]:
    """Ensure the love data directory exists and copy legacy files into it once.

    Returns a small report for diagnostics and the settings page. Every step is
    best-effort: a failed copy is recorded and does not prevent the app from
    starting with an empty/new database.
    """
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    excel_dir = root / EXCEL_DIR_NAME
    excel_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "data_dir": str(root),
        "db": "missing",
        "key": False,
        "excel": 0,
        "stopwords": False,
        "errors": [],
    }
    db_path = root / DB_NAME
    key_file = root / KEY_NAME
    stopwords_path = root / STOPWORDS_NAME

    # 数据库：新库已有有效文件就保留；空文件/不存在时才从旧位置复制。
    if db_path.exists() and db_path.stat().st_size > 0:
        report["db"] = "kept"
    elif legacy_db_path and Path(legacy_db_path).is_file():
        if _same_path(legacy_db_path, db_path):
            report["db"] = "kept"
        else:
            try:
                if db_path.exists():
                    db_path.unlink()
                _copy_sqlite(Path(legacy_db_path), db_path)
                report["db"] = "copied"
            except (OSError, sqlite3.Error) as exc:
                report["errors"].append(f"数据库迁移失败：{exc}")
    if _same_path(legacy_key_file, key_file):
        report["key"] = key_file.is_file()
    elif legacy_key_file and Path(legacy_key_file).is_file() and _copy_file(Path(legacy_key_file), key_file):
        report["key"] = True

    # 排班表：新目录已有 xlsx/xlsm 就不再打扰；否则复制旧目录里的所有表格。
    has_excel = any(excel_dir.glob("*.xlsx")) or any(excel_dir.glob("*.xlsm"))
    if not has_excel and legacy_excel_dir and Path(legacy_excel_dir).is_dir() \
            and not _same_path(legacy_excel_dir, excel_dir):
        for source in sorted(Path(legacy_excel_dir).iterdir()):
            if source.suffix.lower() not in (".xlsx", ".xlsm") or not source.is_file():
                continue
            try:
                if _copy_file(source, excel_dir / source.name):
                    report["excel"] += 1
            except OSError as exc:
                report["errors"].append(f"排班表迁移失败（{source.name}）：{exc}")

    if legacy_stopwords_path and not _same_path(legacy_stopwords_path, stopwords_path):
        if _copy_file(Path(legacy_stopwords_path), stopwords_path):
            report["stopwords"] = True

    if (report["db"] == "copied" or report["key"] or report["excel"]
            or report["stopwords"] or report["errors"]):
        logger.info(
            "love data root=%s db=%s excel=%s stopwords=%s key=%s errors=%s",
            root, report["db"], report["excel"],
            report["stopwords"], report["key"], report["errors"],
        )
    return report
