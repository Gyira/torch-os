"""回收站短路径：压平后的短文件名仍能可靠恢复原文件。

临时库；不接触真实个人数据。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import pillar_notes, recycle, task_notes, task_trash, vault_layout
from backend.app.db import Database

LONG_TITLE = "关于如何在一个非常忙碌的星期里仍然保持边界感和自我照顾的完整反思" * 2


class RecycleShortPathTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="torch-recycle-paths-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()

    def test_note_trash_uses_short_flat_name_and_restores_original_path(self):
        relative = f"03_知识库/01_主题笔记/{LONG_TITLE}.md"
        source = self.vault / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "---\ntype: note\ntitle: 边界感\n---\n\n正文仍然完整。\n",
            encoding="utf-8")

        result = pillar_notes.trash_note(self.vault, relative)
        trash_rel = result["trash_path"]
        self.assertLess(len(trash_rel), 120, trash_rel)
        self.assertNotIn("01_主题笔记", trash_rel)
        self.assertIn("/files/", trash_rel)
        self.assertFalse(source.exists())

        listed = recycle.list_recycle(self.vault)
        items = [item for group in listed["groups"] for item in group["items"]]
        self.assertEqual([item["original_path"] for item in items], [relative])

        database = Database(self.root / "torch_os.sqlite3")
        database.initialize()
        restored = recycle.restore_recycle(
            database, self.vault, paths=[trash_rel])
        self.assertEqual(restored["restored"], 1, restored)
        self.assertTrue(source.is_file())
        self.assertIn("正文仍然完整", source.read_text(encoding="utf-8"))

    def test_task_trash_uses_short_flat_name_and_restores_original_filename(self):
        list_name = "很长的清单名字用来验证路径长度"
        created = task_notes.create_task_note(
            self.vault, list_name, LONG_TITLE, {})
        note = task_notes.read_task_note(self.vault, created["path"])
        self.assertIsNotNone(note)
        original_path = note["path"]

        task_notes.trash_note(self.vault, note)
        self.assertFalse(original_path.exists())
        items = task_trash.list_trash(self.vault)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertLess(len(item["trash_path"]), 150, item["trash_path"])
        self.assertNotIn(list_name, item["trash_path"])
        self.assertIn("/files/", item["trash_path"])
        self.assertEqual(item["title"], LONG_TITLE)

        restored = task_trash.restore_trash(
            self.vault, paths=[item["trash_path"]])
        self.assertEqual(restored["restored"], 1, restored)
        self.assertEqual(original_path.name, note["path"].name)
        self.assertTrue(original_path.is_file())
        self.assertIn(LONG_TITLE, task_notes.read_task_note(
            self.vault, original_path)["title"])

    def test_batch_names_stay_short_and_old_names_still_parse(self):
        name = vault_layout.new_recycle_batch_name()
        self.assertLessEqual(len(name), 22, name)
        self.assertRegex(name, r"^\d{8}T\d{6}-[0-9a-f]{6}$")
        # 旧批次（微秒 + Z）仍能读出时间，避免升级后回收站排序错乱。
        from backend.app import task_trash_store
        self.assertTrue(task_trash_store.batch_time(
            "20261022T160147657177Z-fdf9cc3a"))
        self.assertTrue(task_trash_store.batch_time(name))


if __name__ == "__main__":
    unittest.main(verbosity=2)
