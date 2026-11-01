"""人生导师每日三问：题库、选择、拟答、回答与跳过。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app import mentor_questions, self_archive, vault_layout


class FakeRole:
    def __init__(self, answer: str = "根据最近日记，你通常在 1 点后入睡。"):
        self.answer = answer
        self.calls = []

    def run_once(self, role_id, mode, input_text, *, entry="", selectors=None,
                 max_chars=None, runner=None):
        self.calls.append({"role_id": role_id, "mode": mode, "input": input_text})
        payload = json.loads(input_text)
        drafts = [
            {"id": row["id"], "can_answer": True, "answer": self.answer,
             "sources": ["S1"], "entry_type": row.get("entry_type") or "narrative"}
            for row in payload.get("questions", [])
        ]
        return {
            "structured": {"drafts": drafts},
            "text": "", "sources": [], "truncated": False, "seconds": 0.1,
        }


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault_layout.ensure_layout(vault)
    mentor_questions.ensure_bank(vault)
    return vault


def test_bank_is_created_and_parseable(tmp_path):
    vault = make_vault(tmp_path)
    assert (vault / vault_layout.SELF_MENTOR_QUESTIONS_DIR / "题库.md").is_file()
    rows = mentor_questions.read_bank(vault)
    assert len(rows) >= 60
    assert any(item["section"] == "self_values" for item in rows)
    assert all(item["prompt"] for item in rows)


def test_parse_bank_accepts_custom_markdown():
    rows = mentor_questions.parse_bank(
        "## 三观与决策\n\n"
        "- [q_x] 冲突时你怎么选？｜类型=narrative｜优先级=88｜提示=写真实选择\n")
    assert rows == [{
        "id": "q_x", "prompt": "冲突时你怎么选？", "entry_type": "narrative",
        "priority": 88, "hint": "写真实选择", "source": "bank",
        "section": "self_values", "section_title": "三观与决策",
    }]


def test_ensure_today_creates_three_questions_and_is_stable(tmp_path):
    vault = make_vault(tmp_path)
    first = mentor_questions.ensure_today(vault)
    assert len(first["items"]) == 3
    assert first["remaining"] == 3
    second = mentor_questions.ensure_today(vault)
    assert [item["id"] for item in second["items"]] == [
        item["id"] for item in first["items"]]
    path = vault / vault_layout.SELF_MENTOR_QUESTIONS_DIR / f"{first['date']}.md"
    assert path.is_file()


def test_draft_today_fills_suggested_answers(tmp_path):
    vault = make_vault(tmp_path)
    today = mentor_questions.ensure_today(vault)
    fake = FakeRole()
    result = mentor_questions.draft_today(fake, vault)
    assert result["drafted"] == len(today["items"])
    assert fake.calls and fake.calls[0]["mode"] == "question_draft"
    items = mentor_questions.ensure_today(vault)["items"]
    assert all(item["suggested"] for item in items)
    assert all(item["suggested_sources"] == ["S1"] for item in items)


def test_answer_without_suggestion_writes_memory_only(tmp_path):
    vault = make_vault(tmp_path)
    items = mentor_questions.ensure_today(vault)["items"]
    target = items[0]
    result = mentor_questions.answer(
        vault, target["id"], "我通常会先沉默，再回复变慢。")
    assert result["entry"] is None
    qa = (vault / vault_layout.SELF_MENTOR_MEMORY_DIR / "问答记忆.md").read_text(
        encoding="utf-8")
    assert "先沉默" in qa
    updated = mentor_questions.ensure_today(vault)["items"]
    assert next(item for item in updated if item["id"] == target["id"])["status"] == "answered"


def test_answer_accept_suggested_writes_archive_entry(tmp_path):
    vault = make_vault(tmp_path)
    mentor_questions.ensure_today(vault)
    mentor_questions.draft_today(FakeRole(), vault)
    items = mentor_questions.ensure_today(vault)["items"]
    target = next(item for item in items if item["status"] == "pending")
    result = mentor_questions.answer(
        vault, target["id"], "", accept_suggested=True)
    assert result["entry"] is not None
    assert result["entry"]["section"] == target["section"]
    assert self_archive.list_entries(vault, target["section"])


def test_skip_and_decline(tmp_path):
    vault = make_vault(tmp_path)
    items = mentor_questions.ensure_today(vault)["items"]
    first, second = items[0], items[1]
    mentor_questions.skip(vault, first["id"])
    mentor_questions.skip(vault, second["id"], forever=True)
    state = mentor_questions.load_state(vault)
    assert first["id"] in state["skipped"]
    assert second["id"] in state["declined"]
    updated = mentor_questions.ensure_today(vault)["items"]
    statuses = {item["id"]: item["status"] for item in updated}
    assert statuses[first["id"]] == "skipped"
    assert statuses[second["id"]] == "declined"


def test_read_only_does_not_write_today(tmp_path):
    vault = make_vault(tmp_path)
    result = mentor_questions.ensure_today(vault, read_only=True)
    assert result["saved"] is False
    date = result["date"]
    assert not (vault / vault_layout.SELF_MENTOR_QUESTIONS_DIR / f"{date}.md").exists()
