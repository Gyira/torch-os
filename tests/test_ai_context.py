"""Role context assembly: typed selectors, source numbering, budgets, honest misses."""

from __future__ import annotations

import pytest

from backend.app import ai_context, identity, pillar_notes, tasks
from backend.app.db import Database
from backend.app.identity import IdentityWrite
from backend.app.tasks import TaskCreate
from backend.app.vault_layout import ensure_layout


def make_vault(tmp_path):
    vault = tmp_path / "vault"
    ensure_layout(vault)
    database = Database(tmp_path / "context.sqlite3")
    database.initialize()
    identity.write_identity(
        vault, IdentityWrite(name="小明", values="诚实", revision=""))
    note = pillar_notes.create_note(
        vault, pillar="knowledge", chapter="topic", title="Rust 所有权",
        body="所有权是 Rust 的核心：每个值只有一个所有者。")
    journal = vault / "00_人生路" / "01_日记" / "2026-10-29.md"
    journal.write_text("---\ntype: journal\n---\n\n今天把方案写完了。\n", encoding="utf-8")
    tasks.create_task(
        vault, TaskCreate(title="写周报", date="2026-10-29", priority="high"),
        database)
    return database, vault, note["path"]


def test_assemble_numbers_sources_and_includes_requested_content(tmp_path):
    database, vault, note_path = make_vault(tmp_path)
    bundle = ai_context.assemble(
        database, vault,
        selectors=[
            {"entry": "identity"},
            {"entry": "note", "path": note_path},
            {"entry": "journal", "day": "2026-10-29"},
            {"entry": "tasks", "day": "2026-10-29"},
        ],
        max_chars=20_000)
    assert [source["id"] for source in bundle.sources] == ["S1", "S2", "S3", "S4"]
    assert [source["kind"] for source in bundle.sources] == [
        "identity", "note", "journal", "tasks"]
    assert "[S1] 身份与三观" in bundle.text
    assert "小明" in bundle.text and "诚实" in bundle.text
    assert "所有权是 Rust 的核心" in bundle.text
    assert "今天把方案写完了" in bundle.text
    assert "写周报" in bundle.text
    assert bundle.truncated is False
    assert bundle.chars == len(bundle.text)


def test_assemble_without_selectors_returns_no_context(tmp_path):
    database, vault, _ = make_vault(tmp_path)
    bundle = ai_context.assemble(database, vault, entry="workbench.tasks")
    assert bundle.text == ""
    assert bundle.sources == []
    assert bundle.truncated is False


def test_assemble_reports_missing_journal_honestly(tmp_path):
    database, vault, _ = make_vault(tmp_path)
    bundle = ai_context.assemble(
        database, vault, selectors=[{"entry": "journal", "day": "2020-01-01"}])
    assert "这一天没有日记" in bundle.text


def test_assemble_rejects_unknown_or_not_yet_enabled_sources(tmp_path):
    database, vault, _ = make_vault(tmp_path)
    with pytest.raises(ai_context.ContextError):
        ai_context.assemble(database, vault, selectors=[{"entry": "share"}])
    with pytest.raises(ai_context.ContextError):
        ai_context.assemble(database, vault, selectors=[{"entry": "unknown"}])
    with pytest.raises(ai_context.ContextError):
        ai_context.assemble(database, vault, selectors=[{}])
    with pytest.raises(ai_context.ContextError):
        ai_context.assemble(database, vault, selectors=[{"entry": "note"}])


def test_assemble_truncates_to_budget(tmp_path):
    database, vault, _ = make_vault(tmp_path)
    note = pillar_notes.create_note(
        vault, pillar="knowledge", chapter="topic", title="长文",
        body="长" * 5000)
    bundle = ai_context.assemble(
        database, vault, selectors=[{"entry": "note", "path": note["path"]}],
        max_chars=1200)
    assert bundle.truncated is True
    assert len(bundle.text) <= 1200
    assert bundle.text.endswith("…")


def test_assemble_limits_selector_count(tmp_path):
    database, vault, _ = make_vault(tmp_path)
    selectors = [{"entry": "identity"} for _ in range(ai_context.MAX_SELECTORS + 1)]
    with pytest.raises(ai_context.ContextError):
        ai_context.assemble(database, vault, selectors=selectors)
