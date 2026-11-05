"""B4b: mentor autonomous archive updates with fake model output.

Temporary vault + SQLite only; no real model, no personal data.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import mentor_attachments, mentor_pending, self_archive, self_synthesize
from backend.app.acp_client import AcpResult
from backend.app.ai_gateway import HeadlessResult
from backend.app.ai_roles import (
    RoleRegistry, RoleService, _fallback_reply_from_structured, _visible_reply_text,
)
from backend.app.db import Database

REPO = Path(__file__).resolve().parents[1]


def reply(**fields) -> HeadlessResult:
    payload = {"reply": "收到", **fields}
    return HeadlessResult(json.dumps(payload, ensure_ascii=False), "fake-session", 0.01, 0, "")


class AutonomousArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="torch-mentor-auto-")
        self.addCleanup(temp.cleanup)
        self.vault = Path(temp.name) / "vault"
        self.vault.mkdir()
        self.db = Database(Path(temp.name) / "state.sqlite")
        self.db.initialize()
        self.registry = RoleRegistry(REPO / "dsh/roles")
        self.service = RoleService(
            self.registry, database=self.db, vault_dir=self.vault,
            autonomous_archive=True)
        self.service._execute = Mock(return_value=reply())

    def chat(self, fake: HeadlessResult, message: str = "我最近发现一件事"):
        self.service._execute = Mock(return_value=fake)
        return self.service.chat(
            "mentor", "converse", message, entry="self.mentor",
            selectors=[{"entry": "self_archive"}])

    def create_old(self):
        return self_archive.create_entry(
            self.vault, book_key="self_facts", entry_type="inference",
            confidence="medium", title="旧判断", text="旧正文")

    def test_chat_commits_fact_and_keeps_operation_result(self):
        result = self.chat(reply(archive_updates=[{
            "action": "create", "section": "self_facts", "entry_type": "fact",
            "confidence": "high", "title": "作息", "text": "通常凌晨一点睡",
            "evidence": "用户原话", "sources": ["S1"],
        }]))
        updates = result["archive_updates"]
        self.assertEqual(len(updates), 1)
        self.assertTrue(updates[0]["ok"], updates[0])
        self.assertEqual(updates[0]["entry_type_label"], "事实")
        self.assertTrue(updates[0]["operation_id"].startswith("chat:"))
        entry = self_archive.find_entry(self.vault, updates[0]["entry_id"])
        self.assertEqual(entry["entry_type"], "fact")
        self.assertTrue(entry["sources"])

    def test_placeholder_reply_is_retried_once(self):
        first = reply(
            reply="见上。",
            memory_notes=[{"kind": "observation",
                           "text": "他对考研的迷茫集中在是否要辞职。", "sources": ["S2"]}],
            questions=[{"prompt": "你考这个研最想解决的是哪件事？",
                        "target_section": "self_motivation", "entry_type": "inference"}],
        )
        second = reply(reply="我听见你卡在「要不要辞职」这个代价上。我们先不急着做决定……")
        self.service._execute = Mock(side_effect=[first, second])
        result = self.service.chat(
            "mentor", "converse", "对于考研这件事我其实很迷茫，因为我不知道这会不会需要让我辞职",
            entry="self.mentor", selectors=[{"entry": "self_archive"}])
        self.assertNotEqual(result["reply"], "见上。")
        self.assertIn("我听见你卡在", result["reply"])
        self.assertEqual(self.service._execute.call_count, 2)

    def test_placeholder_reply_falls_back_to_structured_fields(self):
        first = reply(
            reply="见上。",
            memory_notes=[{"kind": "observation",
                           "text": "他对考研的迷茫集中在是否要辞职。", "sources": ["S2"]}],
            questions=[{"prompt": "你考这个研最想解决的是哪件事？",
                        "target_section": "self_motivation", "entry_type": "inference"}],
        )
        self.service._execute = Mock(side_effect=[first, reply(reply="同上。")])
        result = self.service.chat(
            "mentor", "converse", "考研要不要辞职", entry="self.mentor",
            selectors=[{"entry": "self_archive"}])
        self.assertIn("我先把我看到的说清楚", result["reply"])
        self.assertIn("先确认一件事", result["reply"])
        self.assertNotIn("见上", result["reply"])

    def test_visible_reply_guard_detects_placeholders(self):
        from backend.app.ai_schemas import parse_reply
        for text in ("见上。", "同上！", "如上", "见上文。", "……", "..."):
            parsed, structured = parse_reply(json.dumps({"reply": text}))
            self.assertEqual(_visible_reply_text(parsed, text, structured), "", text)
        parsed, structured = parse_reply(json.dumps({"reply": "今天先睡一觉，明天再拆。"}))
        self.assertEqual(_visible_reply_text(parsed, "", structured), "今天先睡一觉，明天再拆。")
        empty, _ = parse_reply("{}")
        self.assertIn("没能生成完整回答", _fallback_reply_from_structured(empty))
        raw_json = '{"reply": ""}'
        parsed_json, structured_json = parse_reply(raw_json)
        self.assertEqual(_visible_reply_text(parsed_json, raw_json, structured_json), "")

    def test_chat_injects_attachment_text_and_returns_metadata(self):
        attachment = mentor_attachments.save_attachment(
            self.vault, "笔记.md", "附件的独特内容 ABC。".encode("utf-8"))
        self.service._execute = Mock(return_value=reply(reply="我读到了附件。"))
        result = self.service.chat(
            "mentor", "converse", "读一下这个文件", entry="self.mentor",
            selectors=[{"entry": "self_archive"}], attachments=[attachment["id"]])
        prompt = self.service._execute.call_args[0][0]
        self.assertIn("用户本轮附加的资料", prompt)
        self.assertIn("附件的独特内容 ABC", prompt)
        self.assertEqual(result["attachments"][0]["name"], "笔记.md")
        thread = self.service.thread(result["thread_id"])
        user_turn = next(turn for turn in thread["turns"] if turn["role"] == "user")
        self.assertEqual(user_turn["structured"]["attachments"][0]["name"], "笔记.md")

    def test_image_attachment_uses_acp_transport(self):
        import io
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (16, 16), "blue").save(buffer, format="PNG")
        attachment = mentor_attachments.save_attachment(
            self.vault, "截图.png", buffer.getvalue())
        self.assertEqual(attachment["kind"], "image")
        self.service._execute_acp = Mock(return_value=AcpResult(
            text="我看到了这张图片。", session_id="",
            seconds=1.0, stop_reason="end_turn"))
        result = self.service.chat(
            "mentor", "converse", "看看这张图", entry="self.mentor",
            selectors=[{"entry": "self_archive"}], attachments=[attachment["id"]])
        self.assertEqual(result["reply"], "我看到了这张图片。")
        self.assertEqual(self.service._execute_acp.call_count, 1)
        self.assertEqual(self.service._execute.call_count, 0)
        call = self.service._execute_acp.call_args
        images = call.kwargs["images"]
        self.assertEqual(images[0]["media_type"], "image/png")
        self.assertIn("附件 A1", call.args[0])
        self.assertIn("截图.png", call.args[0])
        self.assertEqual(result["attachments"][0]["kind"], "image")
        thread = self.service.thread(result["thread_id"])
        user_turn = next(turn for turn in thread["turns"] if turn["role"] == "user")
        self.assertEqual(user_turn["structured"]["attachments"][0]["kind"], "image")

    def test_attachment_filename_source_is_accepted_for_fact(self):
        import io
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (24, 24), "green").save(buffer, format="PNG")
        attachment = mentor_attachments.save_attachment(
            self.vault, "招聘计划表.png", buffer.getvalue())
        payload = {
            "reply": "读到了招聘表。",
            "archive_updates": [{
                "action": "create", "section": "self_facts", "entry_type": "fact",
                "confidence": "high", "title": "招聘表里的学历门槛",
                "text": "招聘表的学历栏要求本科及以上。",
                "evidence": "用户附件里的招聘表原文。",
                "sources": [f"用户附件：{attachment['name']}"],
            }],
        }
        self.service._execute_acp = Mock(return_value=AcpResult(
            text=json.dumps(payload, ensure_ascii=False), session_id="",
            seconds=1.0, stop_reason="end_turn"))
        result = self.service.chat(
            "mentor", "converse", "看看这张招聘表", entry="self.mentor",
            selectors=[{"entry": "self_archive"}], attachments=[attachment["id"]])
        update = result["archive_updates"][0]
        self.assertTrue(update["ok"], update)
        entry = self_archive.find_entry(self.vault, update["entry_id"])
        self.assertEqual(entry["section"], "self_facts")
        self.assertEqual(entry["sources"], [f"附件：{attachment['name']}"])

    def test_short_section_alias_is_normalized(self):
        result = self.chat(reply(archive_updates=[{
            "action": "create", "section": "motivation", "entry_type": "inference",
            "confidence": "medium", "title": "考研顾虑集中在辞职代价",
            "text": "他对考研的迷茫主要落在要不要辞职这个代价上。",
            "sources": ["S1"],
        }]))
        update = result["archive_updates"][0]
        self.assertTrue(update["ok"], update)
        self.assertEqual(update["section"], "self_motivation")
        self.assertEqual(update["section_title"], "动机与需求")
        self.assertEqual(len(self_archive.list_entries(self.vault, "self_motivation")), 1)

    def test_identity_section_is_routed_to_basic_facts(self):
        result = self.chat(reply(archive_updates=[{
            "action": "create", "section": "identity", "entry_type": "fact",
            "confidence": "high", "title": "单位学历优待只认全日制",
            "text": "他所在单位只对全日制学历给予优待。",
            "evidence": "用户原话", "sources": ["S1"],
        }]))
        update = result["archive_updates"][0]
        self.assertTrue(update["ok"], update)
        self.assertEqual(update["section"], "self_facts")
        self.assertEqual(update["section_title"], "基础事实")
        self.assertEqual(len(self_archive.list_entries(self.vault, "self_facts")), 1)

    def test_unknown_section_error_lists_valid_books(self):
        result = self.chat(reply(archive_updates=[{
            "action": "create", "section": "research_plan", "entry_type": "inference",
            "confidence": "medium", "title": "无家可归", "text": "不知道怎么归类",
            "sources": ["S1"],
        }]))
        update = result["archive_updates"][0]
        self.assertFalse(update["ok"])
        self.assertIn("research_plan", update["error"])
        for key in self_archive.BOOK_ORDER:
            self.assertIn(key, update["error"])
        self.assertEqual(self_archive.all_entries(self.vault), [])

    def test_question_with_chinese_book_title_is_saved(self):
        result = self.chat(reply(questions=[{
            "prompt": "你考这个研，最想解决的是哪一件事？",
            "target_section": "动机与需求", "entry_type": "inference",
        }]))
        self.assertEqual(len(result["question_candidates"]), 1)
        self.assertEqual(result["question_candidates"][0]["section"], "self_motivation")

    def test_fact_without_source_is_rejected_as_inference_not_written(self):
        result = self.chat(reply(archive_updates=[{
            "action": "create", "section": "self_facts", "entry_type": "fact",
            "confidence": "high", "title": "猜测", "text": "他大概喜欢安静",
            "sources": [],
        }]))
        self.assertFalse(result["archive_updates"][0]["ok"])
        self.assertIn("来源", result["archive_updates"][0]["error"])
        self.assertEqual(self_archive.list_entries(self.vault, "self_facts"), [])

    def test_fact_requires_trusted_source_not_just_url_or_missing_path(self):
        url_result = self.chat(reply(archive_updates=[{
            "action": "create", "section": "self_facts", "entry_type": "fact",
            "title": "外部说法", "text": "网上看到的", "sources": ["https://example.com/proof"],
        }]))
        self.assertFalse(url_result["archive_updates"][0]["ok"])
        missing = "01_自我档案/01_档案/01_基础事实/不存在.md"
        path_result = self.chat(reply(archive_updates=[{
            "action": "create", "section": "self_facts", "entry_type": "fact",
            "title": "虚构引用", "text": "不存在的笔记", "sources": [missing],
        }]))
        self.assertFalse(path_result["archive_updates"][0]["ok"])
        self.assertIn("来源文件不存在", path_result["archive_updates"][0]["error"])
        local = self.vault / "01_自我档案/01_档案/01_基础事实/已有记录.md"
        local.parent.mkdir(parents=True)
        local.write_text("---\ntitle: 已有记录\n---\n\n用户原话", encoding="utf-8")
        ok_result = self.chat(reply(archive_updates=[{
            "action": "create", "section": "self_facts", "entry_type": "fact",
            "title": "本地事实", "text": "有本地依据", "sources": [local.relative_to(self.vault).as_posix()],
        }]))
        self.assertTrue(ok_result["archive_updates"][0]["ok"], ok_result)

    def test_supersede_links_previous_version_and_keeps_history(self):
        old = self.create_old()
        result = self.chat(reply(archive_updates=[{
            "action": "supersede", "section": "self_facts", "entry_id": old["entry_id"],
            "entry_type": "inference", "confidence": "high", "title": "新判断",
            "text": "新正文", "sources": ["S1"],
        }]))
        update = result["archive_updates"][0]
        self.assertTrue(update["ok"], update)
        self.assertEqual(update["previous_entry_id"], old["entry_id"])
        current = self_archive.list_entries(self.vault, "self_facts")
        self.assertEqual([item["entry_id"] for item in current], [update["entry_id"]])
        history = self_archive.find_entry(
            self.vault, old["entry_id"], include_history=True)
        self.assertEqual(history["status"], "superseded")

    def test_unknown_source_is_dropped_instead_of_blocking_entry(self):
        result = self.chat(reply(archive_updates=[{
            "action": "create", "section": "self_experiments",
            "entry_type": "hypothesis", "confidence": "medium",
            "title": "用真题体感定专业", "text": "先各做一套真题再决定。",
            "sources": ["A1", "S1"],
        }]))
        update = result["archive_updates"][0]
        self.assertTrue(update["ok"], update)
        entry = self_archive.find_entry(self.vault, update["entry_id"])
        self.assertEqual(len(entry["sources"]), 1)
        self.assertNotIn("A1", entry["sources"][0])

    def test_fact_with_one_valid_source_survives_a_bad_citation(self):
        result = self.chat(reply(archive_updates=[{
            "action": "create", "section": "self_facts", "entry_type": "fact",
            "confidence": "high", "title": "有效事实", "text": "有依据。",
            "sources": ["A1", "S1"],
        }]))
        update = result["archive_updates"][0]
        self.assertTrue(update["ok"], update)
        entry = self_archive.find_entry(self.vault, update["entry_id"])
        self.assertEqual(len(entry["sources"]), 1)

    def test_partial_failure_is_reported_per_item_and_does_not_block_others(self):
        result = self.chat(reply(archive_updates=[
            {"action": "create", "section": "self_facts", "entry_type": "inference",
             "title": "有效", "text": "有依据的推断", "sources": ["S1"]},
            {"action": "create", "section": "self_facts", "entry_type": "fact",
             "title": "无效", "text": "越界来源", "sources": ["C:/secret.md"]},
        ]))
        updates = result["archive_updates"]
        self.assertEqual(len(updates), 2)
        self.assertEqual([item["ok"] for item in updates], [True, False])
        self.assertEqual(len(self_archive.list_entries(self.vault, "self_facts")), 1)
        self.assertEqual(result["pending"], [])

    def test_duplicate_chat_request_writes_one_entry(self):
        fake = reply(archive_updates=[{
            "action": "create", "section": "self_facts", "entry_type": "inference",
            "title": "重复", "text": "同一轮内容", "sources": ["S1"],
        }])
        first = self.chat(fake, message="同一句话")
        second = self.chat(fake, message="同一句话")
        self.assertEqual(first["archive_updates"][0]["entry_id"],
                         second["archive_updates"][0]["entry_id"])
        self.assertEqual(len(self_archive.list_entries(self.vault, "self_facts")), 1)

    def test_switch_off_keeps_pending_card_and_old_accept_flow(self):
        self.service.autonomous_archive = False
        result = self.chat(reply(archive_updates=[{
            "action": "create", "section": "self_facts", "entry_type": "inference",
            "title": "待确认", "text": "先走旧流程", "sources": ["S1"],
        }]))
        self.assertEqual(result["archive_updates"], [])
        self.assertEqual(len(result["pending"]), 1)
        self.assertEqual(self_archive.list_entries(self.vault, "self_facts"), [])
        accepted = mentor_pending.accept_pending(
            self.vault, result["pending"][0]["pending_id"], confirm=True)
        self.assertTrue(accepted["accepted"])
        self.assertEqual(len(self_archive.list_entries(self.vault, "self_facts")), 1)

    def test_read_only_chat_reports_failure_without_writing(self):
        self.service.read_only = True
        result = self.chat(reply(archive_updates=[{
            "action": "create", "section": "self_facts", "entry_type": "inference",
            "title": "只读", "text": "不应写入", "sources": ["S1"],
        }]))
        self.assertFalse(result["archive_updates"][0]["ok"])
        self.assertEqual(self_archive.list_entries(self.vault, "self_facts"), [])

    def test_synthesis_commits_patches_and_reports_errors(self):
        self.service._execute = Mock(return_value=HeadlessResult(json.dumps({
            "portrait": "画像",
            "portrait_confidence": 0.6,
            "archive_patches": [
                {"section": "self_facts", "action": "create",
                 "entry_type": "inference", "title": "综合判断",
                 "new_text": "最近的观察", "sources": ["S1"]},
                {"section": "self_body", "action": "create",
                 "entry_type": "fact", "title": "无来源事实",
                 "new_text": "不应写成事实", "sources": []},
            ],
            "memory_updates": [],
            "question_candidates": [],
            "gaps": [],
        }, ensure_ascii=False), "fake", 0.01, 0, ""))
        result = self_synthesize.synthesize(
            self.service, self.vault, autonomous_archive=True)
        self.assertEqual(len(result["archive_updates"]), 2)
        self.assertEqual([item["ok"] for item in result["archive_updates"]], [True, False])
        self.assertEqual(result["patches"], [])
        self.assertEqual(len(self_archive.list_entries(self.vault, "self_facts")), 1)
        self.assertEqual(self_archive.list_entries(self.vault, "self_body"), [])

    def test_synthesis_switch_off_uses_pending_cards(self):
        self.service._execute = Mock(return_value=HeadlessResult(json.dumps({
            "portrait": "", "portrait_confidence": 0.0,
            "archive_patches": [{
                "section": "self_facts", "action": "create",
                "entry_type": "inference", "title": "待确认综合",
                "new_text": "旧流程", "sources": ["S1"]}],
            "memory_updates": [], "question_candidates": [], "gaps": [],
        }, ensure_ascii=False), "fake", 0.01, 0, ""))
        result = self_synthesize.synthesize(
            self.service, self.vault, autonomous_archive=False)
        self.assertEqual(result["archive_updates"], [])
        self.assertEqual(len(result["patches"]), 1)
        self.assertEqual(self_archive.list_entries(self.vault, "self_facts"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
