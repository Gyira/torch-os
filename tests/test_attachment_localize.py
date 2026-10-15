"""Attachment localization apply: copy, history, rewrite, rollback."""

import hashlib
import zipfile
from pathlib import Path

import pytest

from backend.app import attachment_localize
from backend.app.attachment_localize import localize_wolai_attachments
from backend.app.attachment_plan import plan_wolai_attachment_localization
from backend.app.db import Database
from backend.app.vault import parse_text
from backend.app.wolai_import import (
    WolaiImportError,
    _prepare_note,
    _read_owned_target,
    import_selected_notes,
)


def make_archive(tmp_path: Path, entries: dict[str, str | bytes], name="localize.zip") -> Path:
    archive_path = tmp_path / name
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for path, content in entries.items():
            archive.writestr(path, content.encode("utf-8") if isinstance(content, str) else content)
    return archive_path


def make_database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "localize.sqlite3")
    database.initialize()
    return database


def tree(vault: Path) -> dict[str, str]:
    return {
        str(path.relative_to(vault)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(vault.rglob("*")) if path.is_file()
    }


def test_localize_copies_shared_attachment_rewrites_links_and_keeps_history(tmp_path):
    archive = make_archive(tmp_path, {
        "notes/a.md": '![图](../assets/pic.png "标题")\n',
        "notes/b.md": "![图](../assets/pic.png)\n",
        "assets/pic.png": b"PNG-DATA",
    })
    vault = tmp_path / "vault"
    database = make_database(tmp_path)
    import_selected_notes(archive, ["notes/a.md", "notes/b.md"], vault, database)
    archive_before = archive.read_bytes()
    digest = hashlib.sha256(b"PNG-DATA").hexdigest()
    stored = f"{digest[:12]}-pic.png"

    def note_path(member: str) -> Path:
        with zipfile.ZipFile(archive) as zf:
            return vault / _prepare_note(member, zf.read(member))["relative_path"]

    original = {member: note_path(member).read_bytes() for member in ("notes/a.md", "notes/b.md")}
    result = localize_wolai_attachments(archive, ["notes/a.md", "notes/b.md"], vault, database)
    assert result["writes"] is True
    assert result["counts"] == {
        "notes_localized": 2, "notes_skipped": 0, "attachments_referenced": 1,
        "attachments_copied": 1, "attachments_reused": 0, "bytes_copied": len(b"PNG-DATA"),
    }
    attachment = vault / "80-Wolai/attachments" / stored
    assert attachment.read_bytes() == b"PNG-DATA"

    for member in ("notes/a.md", "notes/b.md"):
        target = note_path(member)
        metadata, body = parse_text(target.read_text(encoding="utf-8"))
        assert f"attachments/{stored}" in body
        assert metadata["content_sha256"] == hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert metadata["source_ref"] == member and metadata["source_kind"] == "imported"
        assert target.read_bytes() != original[member]
        with zipfile.ZipFile(archive) as zf:
            note = _prepare_note(member, zf.read(member))
        assert _read_owned_target(target, note) is not None  # 仍是受管导入文件
        history = vault / ".wolai-import-history" / Path(note["relative_path"]).stem
        saved = history / f"{hashlib.sha256(original[member]).hexdigest()}.md"
        assert saved.read_bytes() == original[member]
    assert '"标题"' in (vault / "80-Wolai" / f"{Path(note_path('notes/a.md')).stem}.md").read_text(encoding="utf-8")
    assert archive.read_bytes() == archive_before
    with database.connect() as connection:
        events = [dict(row) for row in connection.execute(
            "SELECT * FROM audit_events WHERE event_type='wolai_attachments_localized'")]
    assert len(events) == 1


def test_localize_skips_not_plannable_notes_and_refuses_empty_batches(tmp_path):
    archive = make_archive(tmp_path, {
        "a.md": "![x](assets/x.png)\n", "b.md": "![x](assets/x.png)\n", "c.md": "![x](assets/x.png)\n",
        "assets/x.png": b"x",
    })
    vault = tmp_path / "vault"
    database = make_database(tmp_path)
    import_selected_notes(archive, ["a.md", "c.md"], vault, database)
    # 直接编辑 c.md 的受管文件，使它变成 user-edited。
    with zipfile.ZipFile(archive) as zf:
        c_path = vault / _prepare_note("c.md", zf.read("c.md"))["relative_path"]
    c_path.write_bytes(c_path.read_bytes() + b"\nlocal edit\n")

    result = localize_wolai_attachments(archive, ["a.md", "b.md"], vault, database)
    assert result["counts"]["notes_localized"] == 1 and result["counts"]["notes_skipped"] == 1
    assert result["skipped_notes"][0]["source"] == "b.md"
    assert result["skipped_notes"][0]["state"] == "not-imported"
    before = tree(vault)
    with pytest.raises(WolaiImportError, match="没有可本地化"):
        localize_wolai_attachments(archive, ["b.md"], vault, database)
    with pytest.raises(WolaiImportError, match="没有可本地化"):
        localize_wolai_attachments(archive, ["c.md"], vault, database)
    assert tree(vault) == before


def test_localize_rolls_back_all_writes_when_a_later_note_fails(tmp_path, monkeypatch):
    archive = make_archive(tmp_path, {
        "a.md": "![x](assets/a.png)\n", "b.md": "![y](assets/b.png)\n",
        "assets/a.png": b"aaaa", "assets/b.png": b"bbbb",
    })
    vault = tmp_path / "vault"
    database = make_database(tmp_path)
    import_selected_notes(archive, ["a.md", "b.md"], vault, database)
    before = tree(vault)
    original = attachment_localize._replace_note
    calls = {"count": 0}

    def flaky(target, expected, new):
        calls["count"] += 1
        if calls["count"] == 2:
            raise WolaiImportError("模拟第二条笔记写入失败")
        return original(target, expected, new)

    monkeypatch.setattr(attachment_localize, "_replace_note", flaky)
    with pytest.raises(WolaiImportError, match="已回滚"):
        localize_wolai_attachments(archive, ["a.md", "b.md"], vault, database)
    assert tree(vault) == before
    assert calls["count"] == 2


def test_localize_guards_repeat_runs_and_reimport_reenables_it(tmp_path):
    archive = make_archive(tmp_path, {"a.md": "![x](assets/a.png)\n", "assets/a.png": b"aaaa"})
    vault = tmp_path / "vault"
    database = make_database(tmp_path)
    import_selected_notes(archive, ["a.md"], vault, database)
    first = localize_wolai_attachments(archive, ["a.md"], vault, database)
    assert first["counts"]["attachments_copied"] == 1
    after_first = tree(vault)
    with pytest.raises(WolaiImportError, match="没有可本地化"):
        localize_wolai_attachments(archive, ["a.md"], vault, database)
    assert tree(vault) == after_first

    # 重新导入会用原始链接替换本地化版本（旧版本进历史），之后再执行会复用附件文件。
    import_selected_notes(archive, ["a.md"], vault, database)
    plan = plan_wolai_attachment_localization(archive, ["a.md"], vault)
    assert plan["notes"][0]["state"] == "ready" and plan["notes"][0]["plannable"] is True
    second = localize_wolai_attachments(archive, ["a.md"], vault, database)
    assert second["counts"]["attachments_copied"] == 0 and second["counts"]["attachments_reused"] == 1
