"""Attachment localization preview: read-only plan, no copies, no rewrites."""

import hashlib
import zipfile
from pathlib import Path

import pytest

from backend.app import attachment_plan
from backend.app.attachment_plan import plan_wolai_attachment_localization
from backend.app.db import Database
from backend.app.vault import parse_text
from backend.app.wolai_import import WolaiImportError, _render, import_selected_notes


def make_archive(tmp_path: Path, entries: dict[str, str | bytes], name="plan.zip") -> Path:
    archive_path = tmp_path / name
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for path, content in entries.items():
            archive.writestr(path, content.encode("utf-8") if isinstance(content, str) else content)
    return archive_path


def make_database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "plan.sqlite3")
    database.initialize()
    return database


def tree(vault: Path) -> dict[str, str]:
    return {
        str(path.relative_to(vault)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(vault.rglob("*")) if path.is_file()
    }


def test_plan_is_read_only_and_ready_for_an_imported_unmodified_note(tmp_path):
    archive = make_archive(tmp_path, {
        "notes/readme.md": "[子页](child.md)\n![图](../assets/pic.png \"标题\")\n[缺失](missing.png)\n",
        "notes/child.md": "# 子页",
        "assets/pic.png": b"PNG-DATA",
    })
    vault = tmp_path / "vault"
    database = make_database(tmp_path)
    imported = import_selected_notes(archive, ["notes/readme.md"], vault, database)
    assert imported["imported"] == 1
    before = tree(vault)
    archive_before = archive.read_bytes()

    plan = plan_wolai_attachment_localization(archive, ["notes/readme.md"], vault)
    assert plan["format"] == "torch-os-wolai-attachment-plan" and plan["version"] == 1
    assert plan["writes"] is False
    assert plan["summary"] == {
        "notes": 1, "attachments": 1, "unique_attachments": 1, "copy_bytes": 8,
        "supported": 1, "skipped": 0, "notes_ready": 1, "notes_needing_import": 0, "notes_blocked": 0,
    }
    note = plan["notes"][0]
    assert note["state"] == "ready" and note["plannable"] is True and note["state_reason"] is None
    item = note["items"][0]
    assert (item["line"], item["target"], item["resolved_path"]) == (2, "../assets/pic.png", "assets/pic.png")
    assert item["supported"] is True and item["skipped_reason"] is None and item["size"] == 8
    digest = hashlib.sha256(b"PNG-DATA").hexdigest()
    assert item["copy_to"] == f"80-Wolai/attachments/{digest[:12]}-pic.png"
    assert item["new_target"] == f"attachments/{digest[:12]}-pic.png"
    assert "不复制附件" in " ".join(plan["warnings"])

    assert tree(vault) == before and archive.read_bytes() == archive_before


def test_plan_reports_not_imported_user_edited_and_modified_notes(tmp_path):
    archive = make_archive(tmp_path, {
        "a.md": "![x](../assets/x.png)\n", "b.md": "![x](../assets/x.png)\n",
        "c.md": "![x](../assets/x.png)\n", "d.md": "![x](../assets/x.png)\n",
        "assets/x.png": b"x",
    })
    vault = tmp_path / "vault"
    database = make_database(tmp_path)
    import_selected_notes(archive, ["a.md", "c.md", "d.md"], vault, database)
    from backend.app.wolai_import import _prepare_note

    def vault_file(member: str) -> Path:
        with zipfile.ZipFile(archive) as zf:
            note = _prepare_note(member, zf.read(member))
        return vault / note["relative_path"]

    edited = vault_file("c.md")
    edited.write_bytes(edited.read_bytes() + b"\nlocal edit\n")
    modified = vault_file("d.md")
    metadata, body = parse_text(modified.read_text(encoding="utf-8"))
    changed_body = body + "\n\n本地化后的额外一行。"
    metadata["content_sha256"] = hashlib.sha256(changed_body.encode("utf-8")).hexdigest()
    modified.write_bytes(_render(metadata, changed_body))

    plan = plan_wolai_attachment_localization(archive, ["a.md", "b.md", "c.md", "d.md"], vault)
    states = {note["source"]: note for note in plan["notes"]}
    assert states["a.md"]["state"] == "ready" and states["a.md"]["plannable"] is True
    assert states["b.md"]["state"] == "not-imported"
    assert "导入页" in states["b.md"]["state_reason"]
    assert states["c.md"]["state"] == "user-edited"
    assert "重新导入" in states["c.md"]["state_reason"]
    assert states["d.md"]["state"] == "modified"
    assert all(states[key]["plannable"] is False for key in ("b.md", "c.md", "d.md"))
    assert plan["summary"]["notes_needing_import"] == 1 and plan["summary"]["notes_blocked"] == 2


def test_plan_skips_oversize_unsupported_and_sensitive_targets(tmp_path, monkeypatch):
    archive = make_archive(tmp_path, {
        "source.md": (
            "![ok](assets/ok.png)\n"
            "![big](assets/big.bin)\n"
            "[escaped](assets/a\\(b\\).png)\n"
            "[ref][pic]\n"
            "![sensitive](../家庭/photo.png)\n"
            "\n[pic]: assets/ref.png\n"
        ),
        "assets/ok.png": b"okok",
        "assets/big.bin": b"0123456789",
        "assets/a(b).png": b"ab",
        "assets/ref.png": b"ref",
        "家庭/photo.png": b"private",
    })
    monkeypatch.setattr(attachment_plan, "MAX_PLAN_ATTACHMENT_BYTES", 5)
    plan = plan_wolai_attachment_localization(archive, ["source.md"], tmp_path / "vault")
    items = plan["notes"][0]["items"]
    by_target = {item["target"]: item for item in items}
    assert set(by_target) == {"assets/ok.png", "assets/big.bin", "assets/a(b).png", "assets/ref.png"}
    assert by_target["assets/ok.png"]["supported"] is True
    assert by_target["assets/big.bin"]["supported"] is False and "不本地化" in by_target["assets/big.bin"]["skipped_reason"]
    assert by_target["assets/a(b).png"]["supported"] is False and "支持范围" in by_target["assets/a(b).png"]["skipped_reason"]
    assert by_target["assets/ref.png"]["supported"] is False
    assert plan["summary"]["skipped"] == 3 and plan["summary"]["supported"] == 1
    # 敏感目录的附件不会进入计划。
    assert all("photo" not in (item["resolved_path"] or "") for item in items)

    monkeypatch.setattr(attachment_plan, "MAX_PLAN_TOTAL_BYTES", 1)
    capped = plan_wolai_attachment_localization(archive, ["source.md"], tmp_path / "vault")
    assert capped["summary"]["supported"] == 0 and capped["summary"]["skipped"] == 4
    capped_items = {item["target"]: item for item in capped["notes"][0]["items"]}
    assert "不本地化" in capped_items["assets/big.bin"]["skipped_reason"]
    assert all("合计超过" in capped_items[target]["skipped_reason"]
               for target in ("assets/ok.png", "assets/a(b).png", "assets/ref.png"))


def test_plan_rejects_invalid_selections_without_reading_bodies(tmp_path, monkeypatch):
    archive = make_archive(tmp_path, {
        "source.md": "![x](assets/x.png)", "assets/x.png": b"x", "家庭/p.md": "private",
    })
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda *args, **kwargs: pytest.fail("Rejected selection must not read bodies"))
    for selection in ([], ["all"], ["source.md"] * 2, ["source.md"] * 21, ["missing.md"], ["assets/x.png"], ["家庭/p.md"]):
        with pytest.raises(WolaiImportError):
            plan_wolai_attachment_localization(archive, selection, tmp_path / "vault")
    assert not (tmp_path / "vault").exists()


def test_plan_dedupes_identical_attachment_content(tmp_path):
    archive = make_archive(tmp_path, {
        "source.md": "![a](assets/one.png)\n![b](assets/two.png)\n",
        "assets/one.png": b"same-bytes", "assets/two.png": b"same-bytes",
    })
    plan = plan_wolai_attachment_localization(archive, ["source.md"], tmp_path / "vault")
    items = plan["notes"][0]["items"]
    assert [item["supported"] for item in items] == [True, True]
    assert items[0]["copy_to"] == items[1]["copy_to"]
    assert items[0]["new_target"] == items[1]["new_target"]
    assert plan["summary"]["unique_attachments"] == 1
    assert plan["summary"]["copy_bytes"] == len(b"same-bytes")
