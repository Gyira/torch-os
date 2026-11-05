"""人生导师附件：文本/PDF/Word 提取、存储与提示词块（临时库，不联网）。"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import mentor_attachments


class MentorAttachmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="torch-mentor-att-")
        self.addCleanup(temp.cleanup)
        self.vault = Path(temp.name) / "vault"
        self.vault.mkdir()

    def save(self, name: str, data: bytes | str):
        if isinstance(data, str):
            data = data.encode("utf-8")
        return mentor_attachments.save_attachment(self.vault, name, data)

    def test_text_attachment_round_trip_and_prompt_block(self):
        meta = self.save("笔记.md", "# 标题\n\n这是一份附加资料。")
        self.assertTrue(meta["id"].startswith("att_"))
        self.assertEqual(meta["name"], "笔记.md")
        rows = mentor_attachments.load_attachments(self.vault, [meta["id"]])
        self.assertEqual(len(rows), 1)
        self.assertIn("这是一份附加资料", rows[0]["text"])
        block = mentor_attachments.prompt_block(rows)
        self.assertIn("用户本轮附加的资料", block)
        self.assertIn("笔记.md", block)
        self.assertIn("这是一份附加资料", block)
        self.assertTrue((self.vault / ".torch-os" / "mentor-uploads" / f"{meta['id']}.json").is_file())

    def test_unsupported_binary_is_rejected(self):
        with self.assertRaises(mentor_attachments.AttachmentError) as caught:
            self.save("archive.bin", b"\x00\x01\x02")
        self.assertEqual(caught.exception.status_code, 415)

    def test_corrupt_image_is_rejected(self):
        with self.assertRaises(mentor_attachments.AttachmentError) as caught:
            self.save("photo.png", b"\x89PNG\r\n\x1a\nnot really an image")
        self.assertEqual(caught.exception.status_code, 422)

    def test_image_round_trip_and_prompt_block(self):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (64, 32), "red").save(buffer, format="PNG")
        meta = self.save("照片.PNG", buffer.getvalue())
        self.assertEqual(meta["kind"], "image")
        self.assertEqual(meta["media_type"], "image/png")
        self.assertEqual(meta["width"], 64)
        self.assertEqual(meta["height"], 32)
        rows = mentor_attachments.load_attachments(self.vault, [meta["id"]])
        self.assertEqual(rows[0]["kind"], "image")
        self.assertEqual(rows[0]["media_type"], "image/png")
        self.assertTrue(rows[0]["image_bytes"])
        block = mentor_attachments.prompt_block(rows)
        self.assertIn("附件 A1", block)
        self.assertIn("图片", block)
        self.assertIn("照片.PNG", block)
        self.assertIn("64×32", block)

    def test_binary_content_with_text_extension_is_rejected(self):
        with self.assertRaises(mentor_attachments.AttachmentError):
            self.save("fake.txt", b"abc\x00def")

    def test_long_text_is_truncated(self):
        meta = self.save("long.txt", "字" * (mentor_attachments.MAX_TEXT_CHARS + 50))
        self.assertTrue(meta["truncated"])
        rows = mentor_attachments.load_attachments(self.vault, [meta["id"]])
        self.assertLessEqual(len(rows[0]["text"]), mentor_attachments.MAX_TEXT_CHARS)
        self.assertTrue(rows[0]["text"].endswith("…"))

    def test_missing_attachment_is_reported(self):
        with self.assertRaises(mentor_attachments.AttachmentError) as caught:
            mentor_attachments.load_attachments(self.vault, ["att_0123456789abcdef"])
        self.assertEqual(caught.exception.status_code, 404)

    def test_invalid_attachment_id_is_rejected(self):
        with self.assertRaises(mentor_attachments.AttachmentError):
            mentor_attachments.load_attachments(self.vault, ["../secret"])

    def test_too_many_attachments_are_rejected(self):
        ids = ["att_" + f"{index:016x}" for index in range(mentor_attachments.MAX_FILES + 1)]
        with self.assertRaises(mentor_attachments.AttachmentError):
            mentor_attachments.load_attachments(self.vault, ids)

    def test_docx_text_is_extracted(self):
        try:
            from docx import Document
        except ImportError:
            self.skipTest("python-docx 未安装")
        document = Document()
        document.add_paragraph("第一段来自 Word。")
        document.add_paragraph("第二段也在。")
        buffer = io.BytesIO()
        document.save(buffer)
        meta = self.save("说明.docx", buffer.getvalue())
        rows = mentor_attachments.load_attachments(self.vault, [meta["id"]])
        self.assertIn("第一段来自 Word", rows[0]["text"])
        self.assertIn("第二段也在", rows[0]["text"])

    def test_broken_pdf_is_reported(self):
        with self.assertRaises(mentor_attachments.AttachmentError):
            self.save("broken.pdf", b"not a real pdf")

    def test_old_attachment_is_pruned_on_save(self):
        directory = self.vault / ".torch-os" / "mentor-uploads"
        directory.mkdir(parents=True)
        old_id = "att_ffffffffffffffff"
        old = {
            "schema": 1, "id": old_id, "name": "old.md", "ext": ".md",
            "bytes": 1, "chars": 1, "truncated": False,
            "created": (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=40)).isoformat(),
            "text": "旧",
        }
        (directory / f"{old_id}.json").write_text(json.dumps(old), encoding="utf-8")
        self.save("new.md", "新文件")
        self.assertFalse((directory / f"{old_id}.json").exists())

    def test_prompt_block_respects_total_budget(self):
        rows = [{"name": "a.md", "chars": 1, "text": "甲" * 30_000},
                {"name": "b.md", "chars": 1, "text": "乙" * 30_000}]
        block = mentor_attachments.prompt_block(rows)
        self.assertLessEqual(block.count("乙"), mentor_attachments.MAX_TOTAL_CHARS)
        self.assertIn("本轮上下文截断", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
