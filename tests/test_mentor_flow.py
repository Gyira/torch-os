"""人生导师闭环：对话裁决 → 待确认 / 记忆 / 动态题库；全量整理编排。"""

from __future__ import annotations

from pathlib import Path

from backend.app import (ai_roles, mentor_pending, mentor_questions, self_archive,
                         self_synthesize, vault_layout)
from backend.app.ai_schemas import parse_reply

ROOT = Path(__file__).resolve().parents[1]
ROLES = ROOT / "dsh" / "roles"


class FakeSynthesizeRole:
    def __init__(self):
        self.calls = []

    def run_once(self, role_id, mode, input_text, *, entry="", selectors=None,
                 max_chars=None, runner=None):
        self.calls.append({"role_id": role_id, "mode": mode, "max_chars": max_chars,
                           "selectors": selectors})
        return {
            "structured": {
                "portrait": "一句话画像：一个在准备考研、开始记录生活的人。",
                "portrait_confidence": 0.6,
                "archive_patches": [{
                    "section": "self_values", "action": "create",
                    "entry_type": "narrative", "confidence": "medium",
                    "title": "世界观", "new_text": "世界更像情境决定。",
                    "reason": "多次日记里出现", "sources": ["S1"],
                }],
                "memory_updates": [
                    {"kind": "timeline", "text": "开始准备考研", "sources": ["S2"]},
                ],
                "question_candidates": [{
                    "section": "self_body", "prompt": "最近睡眠怎么样？",
                    "entry_type": "fact", "priority": 70, "why": "档案没有身体记录",
                    "sources": ["S1"],
                }],
                "gaps": ["self_body"],
            },
            "text": "", "sources": [{"id": "S1"}], "truncated": False, "seconds": 1.0,
        }


def make_service(tmp_path: Path) -> tuple[ai_roles.RoleService, Path]:
    vault = tmp_path / "vault"
    vault_layout.ensure_layout(vault)
    registry = ai_roles.RoleRegistry(ROLES)
    service = ai_roles.RoleService(registry, enabled=True, vault_dir=vault)
    return service, vault


def test_converse_verdict_creates_pending_and_memory(tmp_path):
    service, vault = make_service(tmp_path)
    reply, ok = parse_reply(
        '{"reply":"我理解你的反例；这条确实需要改。",'
        '"archive_verdict":{"decision":"accept","section":"self_values",'
        '"action":"create","entry_type":"narrative","confidence":"medium",'
        '"title":"世界观","new_text":"世界更像情境决定。","reason":"用户给反例",'
        '"sources":["S1"]},'
        '"memory_notes":[{"kind":"decision","text":"决定先准备考研","sources":["S2"]}],'
        '"questions":[{"prompt":"最近睡得好吗？","target_section":"self_body"}]}')
    assert ok is True
    mentor = service.registry.get("mentor")
    cards = service._archive_pending(reply, mentor, "thread_x")
    assert len(cards) == 1
    assert cards[0]["section"] == "self_values"
    assert service._ingest_memory_notes(reply, mentor) == 1
    added = service._append_question_candidates(reply, mentor)
    assert added and added[0]["section"] == "self_body"
    assert mentor_pending.count_pending(vault) == 1
    assert self_archive.list_entries(vault, "self_values") == []


def test_reject_verdict_creates_nothing(tmp_path):
    service, vault = make_service(tmp_path)
    reply, ok = parse_reply(
        '{"reply":"还不够有说服力。","archive_verdict":{"decision":"reject",'
        '"section":"self_values","new_text":"新版本","reason":"证据不足"}}')
    assert ok is True
    mentor = service.registry.get("mentor")
    assert service._archive_pending(reply, mentor, "thread_x") == []
    assert mentor_pending.count_pending(vault) == 0


def test_synthesize_writes_portrait_pending_memory_and_questions(tmp_path):
    service, vault = make_service(tmp_path)
    fake = FakeSynthesizeRole()
    result = self_synthesize.synthesize(fake, vault, range_days=14)
    assert result["portrait_written"] is True
    assert result["portrait"]
    assert len(result["patches"]) == 1
    assert result["memory_updates"] >= 1
    assert result["questions"]
    assert fake.calls[0]["mode"] == "synthesize"
    assert fake.calls[0]["max_chars"] == self_synthesize.MAX_SYNTH_CHARS
    assert (vault / vault_layout.SELF_PORTRAIT_RELATIVE).is_file()
    assert mentor_pending.count_pending(vault) == 1
    assert self_archive.list_entries(vault, "self_values") == []  # 待确认，不直接写
    dynamic = (vault / vault_layout.SELF_MENTOR_QUESTIONS_DIR / "动态题库.md").read_text(
        encoding="utf-8")
    assert "最近睡眠怎么样" in dynamic
    timeline = (vault / vault_layout.SELF_MENTOR_MEMORY_DIR / "记忆时间线.md").read_text(
        encoding="utf-8")
    assert "开始准备考研" in timeline
