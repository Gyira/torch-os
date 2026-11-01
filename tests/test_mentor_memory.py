"""人生导师分层记忆：核心/时间线/问答。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app import mentor_memory, vault_layout


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault_layout.ensure_layout(vault)
    mentor_memory.ensure_memory(vault)
    return vault


def test_ensure_memory_creates_three_files(tmp_path):
    vault = make_vault(tmp_path)
    for name in ("核心记忆.md", "记忆时间线.md", "问答记忆.md"):
        assert (vault / vault_layout.SELF_MENTOR_MEMORY_DIR / name).is_file()


def test_append_timeline_dedupes_and_context_includes_it(tmp_path):
    vault = make_vault(tmp_path)
    added = mentor_memory.append_timeline(vault, [
        {"kind": "decision", "text": "决定先准备考研", "sources": ["S1"]},
        {"kind": "decision", "text": "决定先准备考研", "sources": ["S1"]},
        {"kind": "relationship", "text": "和母亲聊了职业选择"},
    ])
    assert len(added) == 2
    rows = mentor_memory.read_timeline(vault)
    assert [row["text"] for row in rows] == ["决定先准备考研", "和母亲聊了职业选择"]
    assert rows[0]["kind"] == "decision"
    context = mentor_memory.context_text(vault)
    assert "决定先准备考研" in context


def test_append_qa_and_read_qa(tmp_path):
    vault = make_vault(tmp_path)
    mentor_memory.append_qa(
        vault, question="冲突时你实际怎么选？", answer="先讲清事实，再谈感受。",
        qid="q_values_001", section="self_values", sources=["S2"],
        entry_id="ent_x")
    text = mentor_memory.read_qa(vault)["text"]
    assert "q_values_001" in text
    assert "先讲清事实" in text
    assert "ent_x" in text


def test_write_core_backs_up_and_truncation_guard(tmp_path):
    vault = make_vault(tmp_path)
    mentor_memory.write_core(vault, "第一版核心记忆")
    assert mentor_memory.read_core(vault)["text"] == "第一版核心记忆"
    mentor_memory.write_core(vault, "第二版核心记忆")
    backups = list((vault / vault_layout.SELF_MENTOR_HISTORY_DIR / "核心记忆").glob("*.md"))
    assert len(backups) == 1
    with pytest.raises(mentor_memory.MentorMemoryError):
        mentor_memory.write_core(vault, "长" * 3_001)


def test_read_only_blocks_memory_writes(tmp_path):
    vault = make_vault(tmp_path)
    with pytest.raises(mentor_memory.MentorMemoryError):
        mentor_memory.append_timeline(
            vault, [{"kind": "decision", "text": "测试"}], read_only=True)
