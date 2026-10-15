"""Managed Wolai import history: read-only listing and lossless restore."""

import hashlib
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend.app.db import Database
from backend.app.import_history import (
    SHANGHAI,
    list_wolai_import_history,
    preview_wolai_history_cleanup,
    restore_wolai_import_history,
)
from backend.app.wolai_import import (
    WolaiImportError,
    _prepare_note,
    _source_id,
    import_selected_notes,
)


def make_archive(tmp_path: Path, entries: dict[str, str | bytes], name="history.zip") -> Path:
    archive_path = tmp_path / name
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for path, content in entries.items():
            archive.writestr(path, content.encode("utf-8") if isinstance(content, str) else content)
    return archive_path


def make_database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "history.sqlite3")
    database.initialize()
    return database


def vault_note(vault: Path, member: str, source: bytes) -> Path:
    return vault / _prepare_note(member, source)["relative_path"]


def zip_raw(archive: Path, member: str) -> bytes:
    with zipfile.ZipFile(archive) as zf:
        return _prepare_note(member, zf.read(member))["raw"]


def test_history_listing_is_read_only_and_marks_the_current_version(tmp_path):
    vault = tmp_path / "vault"
    database = make_database(tmp_path)
    first = make_archive(tmp_path, {"笔记/甲.md": "# 甲\n\n第一版内容。\n"}, "first.zip")
    import_selected_notes(first, ["笔记/甲.md"], vault, database)
    second = make_archive(tmp_path, {
        "笔记/甲.md": "# 甲\n\n第二版内容。\n", "笔记/乙.md": "# 乙\n\n从未导入。\n",
    }, "second.zip")
    import_selected_notes(second, ["笔记/甲.md"], vault, database)
    v1_raw = zip_raw(first, "笔记/甲.md")
    v2_raw = zip_raw(second, "笔记/甲.md")

    before = sorted(str(path.relative_to(vault)) for path in vault.rglob("*") if path.is_file())
    report = list_wolai_import_history(second, ["笔记/甲.md", "笔记/乙.md"], vault, database)
    assert report["writes"] is False
    assert report["summary"] == {"notes": 2, "versions": 1, "notes_missing": 1, "notes_unmanaged": 0, "notes_stale": 0}
    first_note, second_note = report["notes"]
    assert first_note["state"] == "managed"
    assert first_note["index_state"] == "current"
    assert first_note["current_sha256"] == hashlib.sha256(v2_raw).hexdigest()
    versions = first_note["versions"]
    assert [item["sha256"] for item in versions] == [hashlib.sha256(v1_raw).hexdigest()]
    assert versions[0]["current"] is False and versions[0]["bytes"] == len(v1_raw)
    assert second_note["state"] == "missing" and second_note["current_sha256"] is None
    assert second_note["index_state"] == "missing"
    assert second_note["versions"] == []
    after = sorted(str(path.relative_to(vault)) for path in vault.rglob("*") if path.is_file())
    assert after == before
    assert "不展示历史正文" in " ".join(report["warnings"])
    assert "index_state=stale" in " ".join(report["warnings"])
    # 手动改过 → 本地索引与当前文件不一致，扫描后才会刷新候选。
    target = vault_note(vault, "笔记/甲.md", b"x")
    target.write_bytes(target.read_bytes() + b"\nlocal edit\n")
    stale = list_wolai_import_history(second, ["笔记/甲.md"], vault, database)
    assert stale["notes"][0]["state"] == "unmanaged"
    assert stale["notes"][0]["index_state"] == "stale"
    assert stale["summary"]["notes_stale"] == 1


def test_history_restore_archives_current_and_recreates_missing(tmp_path):
    vault = tmp_path / "vault"
    database = make_database(tmp_path)
    first = make_archive(tmp_path, {"note.md": "第一版\n"}, "first.zip")
    import_selected_notes(first, ["note.md"], vault, database)
    second = make_archive(tmp_path, {"note.md": "第二版\n"}, "second.zip")
    import_selected_notes(second, ["note.md"], vault, database)
    target = vault_note(vault, "note.md", b"x")
    v1_raw = zip_raw(first, "note.md")
    v2_raw = zip_raw(second, "note.md")
    v1_sha = hashlib.sha256(v1_raw).hexdigest()
    v2_sha = hashlib.sha256(v2_raw).hexdigest()

    result = restore_wolai_import_history(second, "note.md", v1_sha, vault, database)
    assert result["state_before"] == "managed"
    assert result["previous_sha256"] == v2_sha and result["restored_sha256"] == v1_sha
    assert result["previous_archived_path"].endswith(f"{v2_sha}.md")
    assert target.read_bytes() == v1_raw
    assert (vault / ".wolai-import-history" / result["previous_archived_path"].split("/")[1] / f"{v2_sha}.md").read_bytes() == v2_raw
    # 重复恢复同一版本会被拒绝且不再改动。
    with pytest.raises(WolaiImportError, match="无需恢复"):
        restore_wolai_import_history(second, "note.md", v1_sha, vault, database)
    # 文件缺失时可以直接从历史重建（无 previous）。
    target.unlink()
    again = restore_wolai_import_history(second, "note.md", v2_sha, vault, database)
    assert again["state_before"] == "missing" and again["previous_sha256"] is None
    assert target.read_bytes() == v2_raw
    with database.connect() as connection:
        events = [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type='wolai_history_restored'")]
    assert len(events) == 2


def test_history_restore_preserves_user_edits_and_rejects_foreign_versions(tmp_path):
    vault = tmp_path / "vault"
    database = make_database(tmp_path)
    first = make_archive(tmp_path, {"note.md": "第一版\n"}, "first.zip")
    import_selected_notes(first, ["note.md"], vault, database)
    second = make_archive(tmp_path, {"note.md": "第二版\n"}, "second.zip")
    import_selected_notes(second, ["note.md"], vault, database)
    target = vault_note(vault, "note.md", b"x")
    v1_sha = hashlib.sha256(zip_raw(first, "note.md")).hexdigest()

    target.write_bytes(target.read_bytes() + b"\nlocal edit\n")
    edited = target.read_bytes()
    result = restore_wolai_import_history(second, "note.md", v1_sha, vault, database)
    assert result["state_before"] == "unmanaged"
    assert result["previous_sha256"] == hashlib.sha256(edited).hexdigest()
    assert target.read_bytes() == zip_raw(first, "note.md")
    archived = vault / ".wolai-import-history" / Path(target).stem / f"{hashlib.sha256(edited).hexdigest()}.md"
    assert archived.read_bytes() == edited

    # 伪造一个哈希合法但不属于这篇来源的版本。
    foreign = _prepare_note("other.md", b"# other\n\nforeign\n")["raw"]
    foreign_sha = hashlib.sha256(foreign).hexdigest()
    history_dir = vault / ".wolai-import-history" / Path(target).stem
    (history_dir / f"{foreign_sha}.md").write_bytes(foreign)
    with pytest.raises(WolaiImportError, match="不属于"):
        restore_wolai_import_history(second, "note.md", foreign_sha, vault, database)
    with pytest.raises(WolaiImportError, match="不存在"):
        restore_wolai_import_history(second, "note.md", "0" * 64, vault, database)
    with pytest.raises(WolaiImportError, match="64 位"):
        restore_wolai_import_history(second, "note.md", "AB" * 32, vault, database)


def test_cleanup_preview_keeps_recent_versions_and_deletes_nothing(tmp_path):
    archive = make_archive(tmp_path, {"note.md": "第一版\n", "note2.md": "另一篇\n"})
    vault = tmp_path / "vault"
    database = make_database(tmp_path)
    now = datetime.now(SHANGHAI)

    def add_version(member: str, name: str, age_days: int, size: int) -> Path:
        folder = vault / ".wolai-import-history" / _source_id(member)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.md"
        path.write_bytes(b"x" * size)
        stamp = (now - timedelta(days=age_days)).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    add_version("note.md", "a" * 64, 1, 10)
    add_version("note.md", "b" * 64, 2, 10)
    add_version("note.md", "c" * 64, 3, 10)
    add_version("note.md", "d" * 64, 20, 40)   # 年轻，保留
    add_version("note.md", "e" * 64, 40, 40)   # 可清理
    add_version("note.md", "f" * 64, 90, 90)   # 可清理
    add_version("note2.md", "0" * 64, 1, 5)
    before = sorted(str(path.relative_to(vault)) for path in vault.rglob("*") if path.is_file())

    report = preview_wolai_history_cleanup(archive, ["note.md", "note2.md"], vault)
    assert report["writes"] is False
    assert report["policy"] == {"keep_newest": 3, "min_age_days": 30}
    assert report["summary"] == {"notes": 2, "versions": 7, "bytes": 205, "cleanable": 2, "cleanable_bytes": 130}
    first, second = report["notes"]
    assert first["cleanable"] == 2 and first["versions"] == 6
    reasons = {item["age_days"]: item for item in first["items"]}
    assert reasons[20]["keep"] is True and "30 天内" in reasons[20]["reason"]
    assert reasons[40]["keep"] is False and reasons[40]["age_days"] == 40
    assert reasons[1]["keep"] is True and "最近 3 个版本" in reasons[1]["reason"]
    assert second["cleanable"] == 0 and second["versions"] == 1
    assert "不会删除任何文件" in report["note"]
    after = sorted(str(path.relative_to(vault)) for path in vault.rglob("*") if path.is_file())
    assert after == before
