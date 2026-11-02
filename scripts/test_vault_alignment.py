"""隔离临时库：前端目录契约、迁移/回退、冲突与数据完整性。"""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import align_vault_layout as migration
from backend.app import vault_layout as layout
from backend.app.task_layout_bootstrap import plan_task_layout


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def write(self, name, content="正文"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def seed(self):
        self.write("00_人生路/09_随笔/随笔.md", "[附件](../../03_知识库/备忘录/a.png)")
        self.write("03_知识库/备忘录/a.png", "attachment")
        self.write("03_知识库/备忘录/引用.md", "[[00_人生路/09_随笔/随笔]]")
        self.write("00_人生路/06_工作复盘/02_会议收获/会议.md")
        self.write("01_自我档案/99_重构备份-test/记忆.md", "历史，不能丢")
        (self.root / "01_自我档案/06_沉淀与创作/02_职业原则_old").mkdir(parents=True)
        (self.root / "02_生活运营/05_生活建议").mkdir(parents=True)
        self.write("02_生活运营/00_待办事项/清单/任务.md")
        self.write("02_生活运营/01_恋爱系统/01_恋爱记忆/恋爱日志/日记.md")

    def test_frontend_contract(self):
        repo = Path(__file__).resolve().parents[1]
        result = subprocess.run(["node", "--input-type=module", "-e",
            "import {LIFE_NOTEBOOKS} from './frontend/notebook-model.js';"
            "import {OPS_SYSTEM_CARDS} from './frontend/ops-model.js';"
            "console.log(JSON.stringify({life:LIFE_NOTEBOOKS,ops:OPS_SYSTEM_CARDS}));"],
            cwd=repo, capture_output=True, check=True, encoding="utf-8")
        front = json.loads(result.stdout)
        for i, book in enumerate(front["life"], 1):
            for chapter in book["chapters"]:
                self.assertEqual(layout.chapter_dir(chapter).split("/")[1], f"{i:02}_{book['title']}")
        self.assertEqual([Path(p).name for p in layout.OPS_SYSTEM_DIRS],
                         ["00_待办事项"] + [f"{i:02}_{card['title']}" for i, card in enumerate(front["ops"], 1)])

    def test_new_layout_only_frontend_roots(self):
        layout.ensure_layout(self.root)
        self.assertEqual(sorted(p.name for p in (self.root / layout.SELF_ROOT).iterdir()),
                         ["01_档案", "02_人生导师", "03_八字系统"])
        self.assertEqual(sorted(p.name for p in (self.root / layout.OPS_ROOT).iterdir()),
                         [Path(p).name for p in layout.OPS_SYSTEM_DIRS])
        self.assertEqual(len(list((self.root / "00_人生路").iterdir())), 7)
        self.assertFalse((self.root / layout.chapter_dir("life_advice")).exists())
        self.assertFalse(any("工作复盘" in action for action in plan_task_layout(self.root)["actions"]))

    def test_round_trip_and_idempotence(self):
        self.seed()
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        dirs = {p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_dir()}
        manifest = migration.apply(self.root)
        self.assertIsNotNone(manifest)
        self.assertEqual((self.root / "03_知识库/备忘录/引用.md").read_text(encoding="utf-8"), "[[00_人生路/06_随笔/随笔.md]]")
        self.assertEqual((self.root / "00_人生路/06_随笔/随笔.md").read_bytes(), before["00_人生路/09_随笔/随笔.md"])
        self.assertTrue((self.root / ".torch-os/backups/self-archive/99_重构备份-test/记忆.md").exists())
        self.assertFalse((self.root / "01_自我档案/06_沉淀与创作").exists())
        self.assertFalse((self.root / layout.chapter_dir("life_advice")).exists())
        self.assertIsNone(migration.apply(self.root))
        migration.rollback(self.root, manifest)
        after = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file() and '.torch-os' not in p.parts}
        self.assertEqual(after, before)
        self.assertEqual({p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_dir() and '.torch-os' not in p.parts}, dirs)

    def test_database_identity_and_citations_preserved(self):
        self.seed()
        db = self.root / "index.sqlite3"
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY,path TEXT UNIQUE);"
                               "CREATE TABLE messages(id INTEGER PRIMARY KEY,citations_json TEXT);"
                               "CREATE TABLE memories(id INTEGER PRIMARY KEY,document_id INTEGER,status TEXT);")
            conn.execute("INSERT INTO documents VALUES(42,?)", ("00_人生路/09_随笔/随笔.md",))
            conn.execute("INSERT INTO messages VALUES(1,?)", (json.dumps([{"path":"00_人生路/09_随笔/随笔.md"}], ensure_ascii=False),))
            conn.execute("INSERT INTO memories VALUES(8,42,'confirmed')")
        manifest = migration.apply(self.root, db)
        with closing(sqlite3.connect(db)) as conn, conn:
            self.assertEqual(conn.execute("SELECT * FROM documents").fetchone(), (42, "00_人生路/06_随笔/随笔.md"))
            self.assertEqual(conn.execute("SELECT * FROM memories").fetchone(), (8, 42, 'confirmed'))
            self.assertIn("06_随笔", conn.execute("SELECT citations_json FROM messages").fetchone()[0])
        migration.rollback(self.root, manifest)
        with closing(sqlite3.connect(db)) as conn, conn:
            self.assertEqual(conn.execute("SELECT path FROM documents").fetchone()[0], "00_人生路/09_随笔/随笔.md")

    def test_conflict_no_changes(self):
        self.seed()
        self.write("00_人生路/06_随笔/随笔.md", "不允许覆盖")
        with self.assertRaises(ValueError):
            migration.apply(self.root)
        self.assertTrue((self.root / "00_人生路/09_随笔/随笔.md").exists())

    def test_backup_failure_no_moves(self):
        self.seed()
        with patch.object(migration.shutil, "copy2", side_effect=OSError("backup failure")):
            with self.assertRaises(OSError):
                migration.apply(self.root)
        self.assertTrue((self.root / "00_人生路/09_随笔/随笔.md").exists())

    def test_rollback_refuses_new_edits(self):
        self.seed()
        manifest = migration.apply(self.root)
        self.write("00_人生路/06_随笔/随笔.md", "后来新增的记忆")
        with self.assertRaises(ValueError):
            migration.rollback(self.root, manifest)
        self.assertFalse((self.root / "00_人生路/09_随笔").exists())

    def test_nonempty_legacy_is_preserved(self):
        self.write("01_自我档案/01_内核与三观/旧记忆.md")
        result = migration.plan(self.root)
        self.assertEqual(len(result["blocked"]), 1)
        migration.apply(self.root)
        self.assertTrue((self.root / "01_自我档案/01_内核与三观/旧记忆.md").exists())

    def test_escape_rejected(self):
        with self.assertRaises(ValueError):
            migration.safe(self.root, "../outside")

    def test_restart_does_not_recreate_legacy_dirs(self):
        self.seed()
        migration.apply(self.root)
        layout.ensure_layout(self.root)
        layout.ensure_ops_layout(self.root)
        self.assertEqual(sorted(p.name for p in (self.root / layout.OPS_ROOT).iterdir()),
                         [Path(p).name for p in layout.OPS_SYSTEM_DIRS])
        self.assertFalse(any("工作复盘" in action for action in plan_task_layout(self.root)["actions"]))
        self.assertEqual(migration.plan(self.root)["remove_dirs"], [])

    def test_missing_fixed_directories_are_repaired_without_overwriting_files(self):
        layout.ensure_layout(self.root)
        note = self.write(layout.chapter_dir("essay") + "/保留.md", "保留现有内容")
        missing = [layout.chapter_dir("work_review_meeting"), layout.chapter_dir("self_facts"),
                   "02_生活运营/03_财务系统", "02_生活运营/01_恋爱系统/06_八字合婚"]
        for relative in missing:
            (self.root / relative).rmdir()
        created = layout.ensure_layout(self.root)
        for relative in missing:
            self.assertIn(relative, created)
            self.assertTrue((self.root / relative).is_dir())
        self.assertEqual(note.read_text(encoding="utf-8"), "保留现有内容")
        self.assertEqual(layout.ensure_layout(self.root), [])

    def test_fixed_path_file_collision_does_not_delete_file(self):
        path = self.write("02_生活运营/03_财务系统", "不能覆盖")
        with self.assertRaises(FileExistsError):
            layout.ensure_layout(self.root)
        self.assertEqual(path.read_text(encoding="utf-8"), "不能覆盖")


if __name__ == "__main__":
    unittest.main()
