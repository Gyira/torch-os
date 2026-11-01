"""自我档案 v2：本子、条目、画像与待确认卡片。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app import mentor_pending, self_archive, vault_layout


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault_layout.ensure_layout(vault)
    return vault


def test_ensure_self_layout_creates_fixed_books(tmp_path):
    vault = make_vault(tmp_path)
    for key in self_archive.BOOK_ORDER:
        meta = self_archive.book_meta(key)
        assert (vault / meta["path"]).is_dir()
    for relative in (
        vault_layout.SELF_MENTOR_MEMORY_DIR,
        vault_layout.SELF_MENTOR_PENDING_PATCHES,
        vault_layout.SELF_MENTOR_QUESTIONS_DIR,
        vault_layout.SELF_BAZI_READINGS_DIR,
    ):
        assert (vault / relative).is_dir()


def test_create_and_list_entries_roundtrip(tmp_path):
    vault = make_vault(tmp_path)
    item = self_archive.create_entry(
        vault, book_key="self_values", entry_type="inference",
        confidence="medium", title="被临时改计划时容易烦躁",
        text="我在被临时改计划时容易烦躁。", evidence="三次改计划后回复变慢",
        counterexample="对方生病时接受度较高",
        verify="下次记录身体反应", sources=["S1"], origin="mentor",
    )
    assert item["entry_id"].startswith("ent_")
    assert item["section"] == "self_values"
    assert item["status"] == "active"
    assert item["revision"]
    rows = self_archive.list_entries(vault, "self_values")
    assert [row["entry_id"] for row in rows] == [item["entry_id"]]
    found = self_archive.find_entry(vault, item["entry_id"])
    assert found and found["title"] == "被临时改计划时容易烦躁"


def test_supersede_requires_matching_revision_and_keeps_history(tmp_path):
    vault = make_vault(tmp_path)
    old = self_archive.create_entry(
        vault, book_key="self_values", entry_type="narrative",
        confidence="low", title="世界观", text="世界基本是善意的。")
    with pytest.raises(self_archive.SelfArchiveConflict):
        self_archive.supersede_entry(
            vault, old["entry_id"], entry_type="narrative", confidence="high",
            title="世界观", text="世界更像情境决定。", base_revision="wrong")
    new = self_archive.supersede_entry(
        vault, old["entry_id"], entry_type="narrative", confidence="high",
        title="世界观", text="世界更像情境决定。",
        base_revision=old["revision"], evidence="多次见闻")
    assert new["entry_id"] != old["entry_id"]
    history = self_archive.list_entries(vault, "self_values", include_history=True)
    statuses = {row["entry_id"]: row["status"] for row in history}
    assert statuses[old["entry_id"]] == "superseded"
    assert statuses[new["entry_id"]] == "active"
    assert new["supersedes"] == old["entry_id"]
    assert len(self_archive.list_entries(vault, "self_values")) == 1


def test_portrait_write_backs_up_previous_version(tmp_path):
    vault = make_vault(tmp_path)
    first = self_archive.write_portrait(vault, "第一版画像", sources=["S1"])
    assert first["exists"] is True
    second = self_archive.write_portrait(vault, "第二版画像", confidence=0.7)
    assert second["text"] == "第二版画像"
    backups = list((vault / vault_layout.SELF_MENTOR_PORTRAIT_HISTORY).glob("*.md"))
    assert len(backups) == 1
    assert "第一版画像" in backups[0].read_text(encoding="utf-8")


def test_pending_accept_creates_entry_and_archives_card(tmp_path):
    vault = make_vault(tmp_path)
    card = mentor_pending.create_pending(vault, {
        "section": "self_body", "action": "create", "entry_type": "fact",
        "confidence": "high", "title": "睡眠偏晚",
        "new_text": "通常 1 点后入睡。", "reason": "连续两周日记", "sources": ["S1"],
    })
    assert card["status"] == "pending"
    assert mentor_pending.count_pending(vault) == 1
    result = mentor_pending.accept_pending(vault, card["pending_id"], confirm=True)
    assert result["entry"]["title"] == "睡眠偏晚"
    assert mentor_pending.count_pending(vault) == 0
    archived = list((vault / vault_layout.SELF_MENTOR_PENDING_ACCEPTED).glob("*.md"))
    assert len(archived) == 1
    with pytest.raises(mentor_pending.PendingError):
        mentor_pending.accept_pending(vault, card["pending_id"], confirm=True)


def test_pending_accept_supersede_with_edited_text(tmp_path):
    vault = make_vault(tmp_path)
    old = self_archive.create_entry(
        vault, book_key="self_emotion", entry_type="behavior",
        confidence="low", title="压力动作", text="压力大时刷手机。")
    card = mentor_pending.create_pending(vault, {
        "section": "self_emotion", "action": "supersede", "entry_id": old["entry_id"],
        "entry_type": "behavior", "confidence": "medium", "title": "压力动作",
        "new_text": "压力大时先刷手机，然后打扫。", "base_revision": old["revision"],
    })
    result = mentor_pending.accept_pending(
        vault, card["pending_id"], confirm=True, edited_text="压力大时先刷手机，再出门走一圈。")
    assert "出门走一圈" in result["entry"]["text"]
    assert result["entry"]["supersedes"] == old["entry_id"]
    assert self_archive.find_entry(vault, old["entry_id"])["status"] == "superseded"


def test_pending_reject_does_not_write_entry(tmp_path):
    vault = make_vault(tmp_path)
    card = mentor_pending.create_pending(vault, {
        "section": "self_narrative", "action": "create", "entry_type": "narrative",
        "confidence": "low", "title": "标签", "new_text": "我是 INTJ。",
    })
    mentor_pending.reject_pending(vault, card["pending_id"], reason="标签不算结论")
    assert self_archive.list_entries(vault, "self_narrative") == []
    assert list((vault / vault_layout.SELF_MENTOR_PENDING_REJECTED).glob("*.md"))


def test_read_only_blocks_archive_writes(tmp_path):
    vault = make_vault(tmp_path)
    with pytest.raises(self_archive.SelfArchiveError):
        self_archive.create_entry(
            vault, book_key="self_facts", entry_type="fact", confidence="high",
            title="测试", text="测试", read_only=True)
