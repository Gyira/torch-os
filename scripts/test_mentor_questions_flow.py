"""B5: daily-question answers save memory first, then archive once.

Temporary vault only; no model call and no personal data.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import mentor_archive, mentor_memory, mentor_questions, self_archive

SHANGHAI = ZoneInfo("Asia/Shanghai")


def item(qid="q_test_1", *, suggested="", sources=None, status="pending",
         answer="", archive_status="", answer_mode="") -> dict:
    return {
        "id": qid, "prompt": "你通常几点睡？", "section": "self_facts",
        "entry_type": "fact", "hint": "写最近一周", "source": "bank",
        "suggested": suggested, "suggested_sources": list(sources or []),
        "status": status, "answer": answer, "answer_mode": answer_mode,
        "archive_status": archive_status, "archive_entry_id": "",
        "archive_error": "",
    }


class DailyQuestionFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="torch-question-flow-")
        self.addCleanup(temp.cleanup)
        self.vault = Path(temp.name) / "vault"
        self.vault.mkdir()
        self.today = datetime.now(SHANGHAI).date().isoformat()

    def write_day(self, items, day=None):
        mentor_questions.write_today(self.vault, day or self.today, items)

    def read_day(self, day=None):
        return mentor_questions.parse_today(
            mentor_questions._dated_path(self.vault, day or self.today)
            .read_text(encoding="utf-8"))

    def qa_text(self):
        return mentor_memory.read_qa(self.vault)["text"]

    def test_user_answer_saves_qa_then_archives_fact(self):
        self.write_day([item()])
        result = mentor_questions.answer(self.vault, "q_test_1", "通常凌晨一点睡")
        self.assertTrue(result["answer_saved"])
        self.assertTrue(result["archive_saved"], result)
        self.assertIn("通常凌晨一点睡", self.qa_text())
        current = self.read_day()
        self.assertEqual(current[0]["status"], "answered")
        self.assertEqual(current[0]["archive_status"], "done")
        entry = self_archive.find_entry(self.vault, current[0]["archive_entry_id"])
        self.assertEqual(entry["entry_type"], "fact")
        self.assertEqual(entry["origin"], "user-answer")
        self.assertIn("问答记忆.md", entry["sources"][0])

    def test_accepted_suggestion_is_marked_question_origin(self):
        self.write_day([item(suggested="导师拟答内容", sources=["S1"])])
        result = mentor_questions.answer(
            self.vault, "q_test_1", "", accept_suggested=True)
        self.assertTrue(result["archive_saved"])
        entry = self_archive.find_entry(self.vault, result["entry"]["entry_id"])
        self.assertEqual(entry["origin"], "question")
        self.assertEqual(entry["text"], "导师拟答内容")
        self.assertIn("采纳导师拟答", entry["evidence"])

    def test_user_answer_does_not_turn_unaccepted_suggestion_into_fact(self):
        self.write_day([item(suggested="导师猜的答案")])
        result = mentor_questions.answer(
            self.vault, "q_test_1", "我其实十二点睡", accept_suggested=False)
        entry = self_archive.find_entry(self.vault, result["entry"]["entry_id"])
        self.assertEqual(entry["text"], "我其实十二点睡")
        self.assertIn("用户回答", entry["evidence"])
        self.assertNotIn("导师猜的答案", entry["text"])

    def test_archive_failure_keeps_answer_and_retry_does_not_duplicate(self):
        self.write_day([item()])
        failure = {
            "ok": False, "operation_id": "question:test:archive",
            "action": "create", "section": "self_facts",
            "section_title": "基础事实", "entry_type": "fact",
            "entry_type_label": "事实", "confidence": "medium",
            "confidence_label": "中", "title": "你通常几点睡？",
            "text": "", "evidence": "", "sources": [], "entry_id": "",
            "previous_entry_id": "", "previous_path": "", "path": "",
            "error": "模拟磁盘失败",
        }
        with patch.object(mentor_archive, "apply_candidate", return_value=failure):
            first = mentor_questions.answer(self.vault, "q_test_1", "凌晨一点")
        self.assertTrue(first["answer_saved"])
        self.assertFalse(first["archive_saved"])
        self.assertEqual(self.read_day()[0]["archive_status"], "failed")
        self.assertEqual(self.qa_text().count("## q_test_1"), 1)
        second = mentor_questions.answer(self.vault, "q_test_1", "凌晨一点")
        self.assertTrue(second["archive_saved"], second)
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.qa_text().count("## q_test_1"), 1)
        self.assertEqual(len(self_archive.list_entries(self.vault, "self_facts")), 1)

    def test_duplicate_submit_applies_once(self):
        self.write_day([item()])
        first = mentor_questions.answer(self.vault, "q_test_1", "凌晨一点")
        second = mentor_questions.answer(self.vault, "q_test_1", "凌晨一点")
        self.assertTrue(first["archive_saved"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["archive_entry_id"], second["archive_entry_id"])
        self.assertEqual(self.qa_text().count("## q_test_1"), 1)
        self.assertEqual(len(self_archive.list_entries(self.vault, "self_facts")), 1)

    def test_late_answer_after_midnight_updates_original_day(self):
        yesterday = (datetime.now(SHANGHAI).date() - timedelta(days=1)).isoformat()
        self.write_day([item()], day=yesterday)
        result = mentor_questions.answer(self.vault, "q_test_1", "昨晚十二点")
        self.assertTrue(result["archive_saved"])
        self.assertEqual(self.read_day(day=yesterday)[0]["status"], "answered")
        self.assertFalse(
            mentor_questions._dated_path(self.vault, self.today).exists())

    def test_empty_answer_creates_no_memory_or_archive(self):
        self.write_day([item()])
        with self.assertRaises(mentor_questions.QuestionError):
            mentor_questions.answer(self.vault, "q_test_1", "")
        self.assertEqual(self.qa_text().strip(), "")
        self.assertEqual(self_archive.list_entries(self.vault, "self_facts"), [])
        self.assertEqual(self.read_day()[0]["status"], "pending")

    def test_skip_does_not_create_answer_or_archive(self):
        self.write_day([item()])
        first = mentor_questions.skip(self.vault, "q_test_1")
        second = mentor_questions.skip(self.vault, "q_test_1")
        self.assertTrue(first["skipped"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.qa_text().strip(), "")
        self.assertEqual(self_archive.list_entries(self.vault, "self_facts"), [])
        self.assertEqual(self.read_day()[0]["status"], "skipped")

    def test_skip_retries_transient_replace_lock(self):
        """Windows 同步盘/杀毒软件短暂占用文件时，替换应重试而不是直接 500。"""
        self.write_day([item()])
        real_replace = os.replace
        attempts = {"count": 0}

        def flaky_replace(source, target):
            attempts["count"] += 1
            if attempts["count"] <= 2:
                raise PermissionError(13, "拒绝访问。", str(source), str(target))
            return real_replace(source, target)

        with patch.object(mentor_questions.os, "replace", side_effect=flaky_replace), \
                patch.object(mentor_questions.time, "sleep"):
            result = mentor_questions.skip(self.vault, "q_test_1")
        self.assertTrue(result["skipped"])
        self.assertGreaterEqual(attempts["count"], 3)
        self.assertEqual(self.read_day()[0]["status"], "skipped")

    def test_skip_reports_503_when_file_stays_locked(self):
        """文件被持续占用时给出可读的 503，而不是未处理的 500，也不留临时文件。"""
        self.write_day([item()])
        with patch.object(mentor_questions.os, "replace",
                          side_effect=PermissionError(13, "拒绝访问。")), \
                patch.object(mentor_questions.time, "sleep"):
            with self.assertRaises(mentor_questions.QuestionError) as caught:
                mentor_questions.skip(self.vault, "q_test_1")
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(self.read_day()[0]["status"], "pending")
        leftovers = list(
            mentor_questions._dated_path(self.vault, self.today).parent.glob(".q-*.tmp"))
        self.assertEqual(leftovers, [])

    def test_read_only_answer_has_no_side_effects(self):
        self.write_day([item()])
        with self.assertRaises(mentor_questions.QuestionError):
            mentor_questions.answer(self.vault, "q_test_1", "凌晨一点", read_only=True)
        self.assertEqual(self.qa_text().strip(), "")
        self.assertEqual(self_archive.list_entries(self.vault, "self_facts"), [])
        self.assertEqual(self.read_day()[0]["status"], "pending")


if __name__ == "__main__":
    unittest.main(verbosity=2)
