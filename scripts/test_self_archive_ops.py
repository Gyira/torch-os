"""B4a: archive supersede consistency, idempotency and crash recovery.

Temporary vaults only; never reads personal configuration or calls a model.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import mentor_pending, self_archive


def make_payload(title: str, text: str, *, section: str = "self_facts") -> dict:
    return {
        "book_key": section,
        "entry_type": "inference",
        "confidence": "medium",
        "title": title,
        "text": text,
    }


class ArchiveOperationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="torch-archive-ops-")
        self.addCleanup(temp.cleanup)
        self.vault = Path(temp.name) / "vault"
        self.vault.mkdir()

    def create(self, title="旧判断", text="旧正文"):
        return self_archive.create_entry(self.vault, **make_payload(title, text))

    def simulate_interrupted_supersede(self, old, *, operation_id="crash-op"):
        payload = self_archive._entry_payload(
            book_key=old["section"], entry_type="inference", confidence="medium",
            title="新判断", text="新正文", evidence="", counterexample="",
            verify="", sources=[], origin="mentor", supersedes=old["entry_id"])
        new_id = "ent_crashnew001"
        new_path = self_archive._entry_target(self.vault, payload, new_id)
        metadata = self_archive._entry_metadata(payload, new_id)
        new_entry = self_archive._write_entry_file(
            self.vault, new_path, metadata, payload["text"])
        record = {
            "schema": self_archive.OP_SCHEMA,
            "operation_id": operation_id, "kind": "supersede",
            "status": "new_written",
            "section": old["section"], "entry_type": "inference", "title": "新判断",
            "old_entry_id": old["entry_id"], "old_path": old["path"],
            "old_revision": old["revision"], "base_revision": old["revision"],
            "new_entry_id": new_entry["entry_id"],
            "new_path": new_entry["path"],
        }
        self_archive._save_op(self.vault, record)
        return new_entry, record

    def test_supersede_replaces_one_current_version_and_keeps_history(self):
        old = self.create()
        new = self_archive.supersede_entry(
            self.vault, old["entry_id"], operation_id="op-one",
            entry_type="inference", confidence="high", title="新判断",
            text="新正文", base_revision=old["revision"])
        current = self_archive.list_entries(self.vault, "self_facts")
        history = self_archive.list_entries(
            self.vault, "self_facts", include_history=True)
        self.assertEqual([item["entry_id"] for item in current], [new["entry_id"]])
        self.assertEqual(len(history), 2)
        old_row = next(item for item in history if item["entry_id"] == old["entry_id"])
        self.assertEqual(old_row["status"], "superseded")
        self.assertEqual(old_row["text"], "旧正文")

    def test_same_operation_id_applies_once_and_returns_same_entry(self):
        old = self.create()
        first = self_archive.supersede_entry(
            self.vault, old["entry_id"], operation_id="same-op",
            entry_type="inference", confidence="medium", title="新判断",
            text="新正文", base_revision=old["revision"])
        second = self_archive.supersede_entry(
            self.vault, old["entry_id"], operation_id="same-op",
            entry_type="inference", confidence="medium", title="新判断",
            text="新正文", base_revision=old["revision"])
        self.assertEqual(first["entry_id"], second["entry_id"])
        self.assertEqual(len(self_archive.list_entries(self.vault, "self_facts")), 1)
        self.assertEqual(
            len(list((self.vault / self_archive.book_meta("self_facts")["path"]).glob("*.md"))), 2)

    def test_second_correction_with_same_revision_fails_after_first_wins(self):
        old = self.create()
        first = self_archive.supersede_entry(
            self.vault, old["entry_id"], operation_id="op-a",
            entry_type="inference", confidence="medium", title="判断 A",
            text="A", base_revision=old["revision"])
        with self.assertRaises((self_archive.SelfArchiveError, self_archive.SelfArchiveConflict)):
            self_archive.supersede_entry(
                self.vault, old["entry_id"], operation_id="op-b",
                entry_type="inference", confidence="medium", title="判断 B",
                text="B", base_revision=old["revision"])
        current = self_archive.list_entries(self.vault, "self_facts")
        self.assertEqual([item["entry_id"] for item in current], [first["entry_id"]])

    def test_stale_base_revision_never_overwrites_external_edit(self):
        old = self.create()
        path = self.vault / old["path"]
        path.write_text(path.read_text(encoding="utf-8").replace("旧正文", "外部改过的正文"),
                        encoding="utf-8")
        with self.assertRaises(self_archive.SelfArchiveConflict):
            self_archive.supersede_entry(
                self.vault, old["entry_id"], operation_id="stale",
                entry_type="inference", confidence="medium", title="新判断",
                text="新正文", base_revision=old["revision"])
        current = self_archive.list_entries(self.vault, "self_facts")
        self.assertEqual(len(current), 1)
        self.assertIn("外部改过的正文", current[0]["text"])

    def test_interrupted_new_write_is_hidden_until_recovery(self):
        old = self.create()
        new, _record = self.simulate_interrupted_supersede(old)
        current = self_archive.list_entries(self.vault, "self_facts")
        history = self_archive.list_entries(
            self.vault, "self_facts", include_history=True)
        self.assertEqual([item["entry_id"] for item in current], [old["entry_id"]])
        pending = next(item for item in history if item["entry_id"] == new["entry_id"])
        self.assertTrue(pending.get("operation_pending"))
        result = self_archive.recover_incomplete_operations(self.vault)
        self.assertEqual(result["done"], 1)
        current = self_archive.list_entries(self.vault, "self_facts")
        self.assertEqual([item["entry_id"] for item in current], [new["entry_id"]])
        old_row = self_archive.find_entry(
            self.vault, old["entry_id"], include_history=True)
        self.assertEqual(old_row["status"], "superseded")

    def test_recovery_conflict_leaves_external_edit_as_current(self):
        old = self.create()
        new, _record = self.simulate_interrupted_supersede(old)
        old_path = self.vault / old["path"]
        old_path.write_text(
            old_path.read_text(encoding="utf-8").replace("旧正文", "恢复前外部编辑"),
            encoding="utf-8")
        result = self_archive.recover_incomplete_operations(self.vault)
        self.assertEqual(result["conflict"], 1)
        current = self_archive.list_entries(self.vault, "self_facts")
        self.assertEqual([item["entry_id"] for item in current], [old["entry_id"]])
        self.assertIn("恢复前外部编辑", current[0]["text"])
        self.assertTrue((self.vault / new["path"]).is_file())

    def test_read_only_recovery_reports_without_writing(self):
        old = self.create()
        new, record = self.simulate_interrupted_supersede(old)
        result = self_archive.recover_incomplete_operations(
            self.vault, read_only=True)
        self.assertTrue(result["read_only"])
        self.assertEqual(result["pending"], 1)
        current = self_archive.list_entries(self.vault, "self_facts")
        self.assertEqual([item["entry_id"] for item in current], [old["entry_id"]])
        self.assertEqual(self_archive._load_op(self.vault, record["operation_id"])["status"],
                         "new_written")
        self.assertTrue((self.vault / new["path"]).is_file())
        with self.assertRaises(self_archive.SelfArchiveError):
            self_archive.supersede_entry(
                self.vault, old["entry_id"], operation_id="readonly",
                entry_type="inference", confidence="medium", title="新判断",
                text="新正文", read_only=True)

    def test_create_operation_id_is_idempotent(self):
        first = self_archive.create_entry(
            self.vault, **make_payload("新条目", "正文"), operation_id="create-op")
        second = self_archive.create_entry(
            self.vault, **make_payload("新条目", "正文"), operation_id="create-op")
        self.assertEqual(first["entry_id"], second["entry_id"])
        self.assertEqual(len(self_archive.list_entries(self.vault, "self_facts")), 1)

    def test_duplicate_pending_accept_applies_once(self):
        old = self.create()
        card = mentor_pending.create_pending(self.vault, {
            "section": old["section"], "action": "supersede",
            "entry_id": old["entry_id"], "entry_type": "inference",
            "confidence": "high", "title": "新判断", "new_text": "新正文",
            "base_revision": old["revision"],
        })
        first = mentor_pending.accept_pending(
            self.vault, card["pending_id"], confirm=True)
        second = mentor_pending.accept_pending(
            self.vault, card["pending_id"], confirm=True)
        self.assertTrue(second.get("duplicate"))
        self.assertEqual(first["entry"]["entry_id"], second["entry"]["entry_id"])
        current = self_archive.list_entries(self.vault, "self_facts")
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["entry_id"], first["entry"]["entry_id"])

    def test_concurrent_pending_accept_creates_one_entry(self):
        old = self.create()
        card = mentor_pending.create_pending(self.vault, {
            "section": old["section"], "action": "supersede",
            "entry_id": old["entry_id"], "entry_type": "inference",
            "confidence": "high", "title": "并发判断", "new_text": "并发正文",
            "base_revision": old["revision"],
        })
        results: list[dict] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                barrier.wait(timeout=10)
                results.append(mentor_pending.accept_pending(
                    self.vault, card["pending_id"], confirm=True))
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(errors, errors)
        self.assertEqual(len(results), 2)
        entry_ids = {item["entry"]["entry_id"] for item in results}
        self.assertEqual(len(entry_ids), 1)
        self.assertEqual(len(self_archive.list_entries(self.vault, "self_facts")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
