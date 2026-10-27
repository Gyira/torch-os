"""恋爱系统数据目录：首次迁移旧数据、保留新数据、缺省结构。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.app.love_storage import migrate_love_data


def _make_db(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("CREATE TABLE items (name TEXT)")
        connection.execute("INSERT INTO items (name) VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _read_db(path: Path) -> str:
    connection = sqlite3.connect(str(path))
    try:
        return connection.execute("SELECT name FROM items").fetchone()[0]
    finally:
        connection.close()


def test_migrates_legacy_love_files_into_the_new_data_dir(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    old_db = legacy / "love_analysis.db"
    _make_db(old_db, "old")
    (legacy / ".api_key.key").write_bytes(b"legacy-key")
    old_xlsx = legacy / "xlsx"
    old_xlsx.mkdir()
    (old_xlsx / "5月排班.xlsx").write_bytes(b"xlsx")
    (old_xlsx / "6月排班.xlsm").write_bytes(b"xlsm")
    old_stopwords = legacy / "cn_stopwords.txt"
    old_stopwords.write_text("的\n了\n", encoding="utf-8")

    target = tmp_path / "个人数据库" / ".torch-os" / "love"
    report = migrate_love_data(
        target,
        legacy_db_path=old_db,
        legacy_key_file=legacy / ".api_key.key",
        legacy_excel_dir=old_xlsx,
        legacy_stopwords_path=old_stopwords,
    )

    assert report["db"] == "copied"
    assert report["excel"] == 2
    assert report["stopwords"] is True
    assert report["key"] is True
    assert report["errors"] == []
    assert "timetable" not in report
    assert _read_db(target / "love_analysis.db") == "old"
    assert (target / ".api_key.key").read_bytes() == b"legacy-key"
    assert (target / "xlsx" / "5月排班.xlsx").read_bytes() == b"xlsx"
    assert (target / "xlsx" / "6月排班.xlsm").read_bytes() == b"xlsm"
    assert (target / "cn_stopwords.txt").is_file()
    # 旧文件保留，不搬空原位置。
    assert old_db.is_file() and (old_xlsx / "5月排班.xlsx").is_file()


def test_existing_new_love_data_is_never_overwritten(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    old_db = legacy / "love_analysis.db"
    _make_db(old_db, "old")
    (legacy / ".api_key.key").write_bytes(b"legacy-key")
    old_xlsx = legacy / "xlsx"
    old_xlsx.mkdir()
    (old_xlsx / "old.xlsx").write_bytes(b"old")

    target = tmp_path / "love"
    target.mkdir()
    new_db = target / "love_analysis.db"
    _make_db(new_db, "new")
    (target / ".api_key.key").write_bytes(b"new-key")
    (target / "xlsx").mkdir()
    (target / "xlsx" / "new.xlsx").write_bytes(b"new")

    report = migrate_love_data(
        target,
        legacy_db_path=old_db,
        legacy_key_file=legacy / ".api_key.key",
        legacy_excel_dir=old_xlsx,
    )

    assert report["db"] == "kept"
    assert report["excel"] == 0
    assert report["key"] is False  # 新 Key 已存在，不覆盖
    assert _read_db(new_db) == "new"
    assert (target / ".api_key.key").read_bytes() == b"new-key"
    assert [path.name for path in (target / "xlsx").iterdir()] == ["new.xlsx"]


def test_missing_legacy_sources_still_create_the_empty_structure(tmp_path: Path) -> None:
    target = tmp_path / "个人数据库" / ".torch-os" / "love"
    report = migrate_love_data(
        target,
        legacy_db_path=tmp_path / "missing.db",
        legacy_key_file=tmp_path / "missing.key",
        legacy_excel_dir=tmp_path / "missing-xlsx",
        legacy_stopwords_path=tmp_path / "missing.txt",
    )

    assert report["db"] == "missing"
    assert report["excel"] == 0
    assert report["stopwords"] is False
    assert report["errors"] == []
    assert (target / "xlsx").is_dir()
