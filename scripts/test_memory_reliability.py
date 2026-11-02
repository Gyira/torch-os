"""B1 regression: temporary Markdown only, no user configuration or model calls."""
from __future__ import annotations

import multiprocessing
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import mentor_memory as memory, memory_store, self_archive, self_synthesize
from backend.app.ai_roles import RoleService
from backend.app import vault_layout


def append_pair(root, index):
    memory.append_timeline(root, [{"kind": "observation", "text": f"event-{index}"}])
    memory.append_qa(root, question=f"question-{index}", answer=f"answer-{index}", qid=f"q-{index}")


def slow_append_pair(root, index):
    # Widen the read/write window to exercise overlap, without a barrier inside
    # the lock (which would deadlock a correct implementation).
    original = memory._read_update
    def slow_read(update):
        result = original(update)
        time.sleep(.02)
        return result
    with patch.object(memory, "_read_update", side_effect=slow_read):
        append_pair(root, index)


def interrupted_writer(root, ready):
    def stop_before_replace(*args):
        ready.set()
        time.sleep(30)
        raise OSError("test worker was not stopped")
    with patch.object(memory_store.os, "replace", side_effect=stop_before_replace):
        memory.write_core(root, "interrupted")


class MemoryReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="torch-memory-test-")
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name)

    def writers(self):
        return [
            (memory.write_core, memory, self.vault / vault_layout.SELF_MENTOR_MEMORY_DIR / "核心记忆.md", "核心记忆-fixed*.md"),
            (self_archive.write_portrait, self_archive, self.vault / vault_layout.SELF_PORTRAIT_RELATIVE, "画像-fixed*.md"),
        ]

    def test_same_second_history_keeps_every_previous_version(self):
        for write, module, path, pattern in self.writers():
            with self.subTest(path=path.name), patch.object(module, "_stamp", return_value="fixed"):
                write(self.vault, "version-A")
                first = path.read_bytes()
                write(self.vault, "version-B")
                second = path.read_bytes()
                write(self.vault, "version-C")
                snapshots = [p.read_bytes() for p in self.vault.rglob(pattern)]
                self.assertCountEqual(snapshots, [first, second])

    def test_backup_creation_failure_preserves_current_bytes(self):
        original_open = Path.open
        def fail_backup(path, mode="r", *args, **kwargs):
            if mode == "xb":
                raise OSError("simulated history unavailable")
            return original_open(path, mode, *args, **kwargs)
        for write, module, path, _ in self.writers():
            with self.subTest(path=path.name):
                write(self.vault, "original")
                before = path.read_bytes()
                error_type = memory.MentorMemoryError if module is memory else self_archive.SelfArchiveError
                with patch.object(Path, "open", fail_backup), self.assertRaises(error_type):
                    write(self.vault, "must not replace")
                self.assertEqual(path.read_bytes(), before)

    def test_backup_flush_failure_preserves_current_and_cleans_partial_snapshot(self):
        memory.write_core(self.vault, "original")
        with patch.object(memory_store.os, "fsync", side_effect=OSError("disk error")):
            with self.assertRaises(memory.MentorMemoryError):
                memory.write_core(self.vault, "new")
        self.assertEqual(memory.read_core(self.vault)["text"], "original")
        history = self.vault / vault_layout.SELF_MENTOR_HISTORY_DIR
        self.assertEqual(list(history.rglob("*.md")), [])

    def test_replace_failure_preserves_current_and_complete_snapshot(self):
        memory.write_core(self.vault, "original")
        with patch.object(memory_store.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(memory.MentorMemoryError):
                memory.write_core(self.vault, "new")
        self.assertEqual(memory.read_core(self.vault)["text"], "original")
        self.assertEqual(len(list((self.vault / vault_layout.SELF_MENTOR_HISTORY_DIR).rglob("*.md"))), 1)
        self.assertEqual(list(self.vault.rglob("*.tmp")), [])

    def test_process_interruption_keeps_current_and_releases_lock(self):
        memory.write_core(self.vault, "original")
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        process = context.Process(target=interrupted_writer, args=(str(self.vault), ready))
        process.start()
        try:
            self.assertTrue(ready.wait(10), "worker did not reach commit boundary")
        finally:
            process.terminate()
            process.join(10)
        self.assertFalse(process.is_alive())
        self.assertEqual(memory.read_core(self.vault)["text"], "original")
        result = memory.write_core(self.vault, "next successful write")
        self.assertTrue((self.vault / result["history_path"]).is_file())
        self.assertEqual(memory.read_core(self.vault)["text"], "next successful write")

    def test_external_edit_is_not_overwritten(self):
        path = self.vault / "external.md"
        path.write_text("original", encoding="utf-8")
        with self.assertRaises(memory.MentorMemoryError) as raised:
            with memory_store.update_document(path, memory.MentorMemoryError) as update:
                path.write_text("external edit", encoding="utf-8")
                update.write("stale value")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(path.read_text(encoding="utf-8"), "external edit")
        self.assertEqual(list(self.vault.rglob("*.tmp")), [])

    def test_threads_keep_all_appends(self):
        # Slow operations force overlapping requests without weakening locking.
        original = memory._read_update
        def slow_read(update):
            result = original(update)
            time.sleep(.02)
            return result
        with patch.object(memory, "_read_update", side_effect=slow_read):
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(lambda index: append_pair(str(self.vault), index), range(16)))
        self.assertEqual(len(memory.read_timeline(self.vault)), 16)
        qa = memory.read_qa(self.vault)["text"]
        for index in range(16):
            self.assertIn(f"## q-{index}\n", qa)

    def test_processes_keep_all_appends(self):
        with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
            jobs = [pool.submit(slow_append_pair, str(self.vault), index) for index in range(8)]
            for job in jobs:
                job.result(timeout=30)
        self.assertEqual(len(memory.read_timeline(self.vault)), 8)
        qa = memory.read_qa(self.vault)["text"]
        for index in range(8):
            self.assertIn(f"## q-{index}\n", qa)

    def test_initialization_does_not_replace_existing_memory(self):
        memory.write_core(self.vault, "existing")
        memory.ensure_memory(self.vault)
        self.assertEqual(memory.read_core(self.vault)["text"], "existing")
        memory.append_timeline(self.vault, [{"text": "kept"}])
        memory.ensure_memory(self.vault)
        self.assertEqual(len(memory.read_timeline(self.vault)), 1)

    def test_reader_handle_does_not_make_concurrent_write_fail(self):
        # Hold a real read handle open. On Windows it blocks os.replace unless
        # readers and writers coordinate; no mocked successful replacement.
        memory.write_core(self.vault, "original")
        original_open = Path.open
        reading, release, writing = threading.Event(), threading.Event(), threading.Event()
        target = self.vault / vault_layout.SELF_MENTOR_MEMORY_DIR / "核心记忆.md"

        @contextmanager
        def held_read(path, mode="r", *args, **kwargs):
            with original_open(path, mode, *args, **kwargs) as handle:
                if path == target and mode in ("r", "rb") and threading.current_thread().name.startswith("reader"):
                    reading.set()
                    if not release.wait(5):
                        raise RuntimeError("reader was not released")
                yield handle

        def write():
            writing.set()
            return memory.write_core(self.vault, "next")

        with patch.object(Path, "open", held_read), ThreadPoolExecutor(1, thread_name_prefix="reader") as readers, ThreadPoolExecutor(1) as writers:
            reader = readers.submit(memory.read_core, self.vault)
            try:
                self.assertTrue(reading.wait(3))
                writer = writers.submit(write)
                self.assertTrue(writing.wait(3))
                time.sleep(.1)
                self.assertFalse(writer.done(), "writer must wait for the open reader")
            finally:
                release.set()
            self.assertEqual(reader.result(timeout=3)["text"], "original")
            self.assertEqual(writer.result(timeout=3)["text"], "next")
        self.assertEqual(memory.read_core(self.vault)["text"], "next")

    def test_file_probe_waits_for_existing_writer(self):
        # A second writer must not even resolve/stat the target while the first
        # writer is at commit. These metadata probes also take Windows handles.
        from backend.app import vault as vault_module
        memory.append_qa(self.vault, question="first", answer="first", qid="first")
        original_replace = memory_store.os.replace
        original_safe = vault_module.safe_note_path
        committing, release, probe, started = (threading.Event() for _ in range(4))

        def hold_commit(source, target):
            if threading.current_thread().name.startswith("first"):
                committing.set()
                if not release.wait(5):
                    raise RuntimeError("writer was not released")
            return original_replace(source, target)

        def observe_probe(*args, **kwargs):
            if threading.current_thread().name.startswith("second"):
                probe.set()
            return original_safe(*args, **kwargs)

        def append(identifier):
            started.set()
            return memory.append_qa(self.vault, question=identifier, answer=identifier, qid=identifier)

        with patch.object(memory_store.os, "replace", hold_commit), patch.object(vault_module, "safe_note_path", observe_probe), ThreadPoolExecutor(1, thread_name_prefix="first") as first, ThreadPoolExecutor(1, thread_name_prefix="second") as second:
            one = first.submit(append, "one")
            try:
                self.assertTrue(committing.wait(3))
                started.clear()
                two = second.submit(append, "two")
                self.assertTrue(started.wait(3))
                self.assertFalse(probe.wait(.1), "target checked before its write lock")
            finally:
                release.set()
            one.result(timeout=3)
            two.result(timeout=3)
        qa = memory.read_qa(self.vault)["text"]
        for identifier in ("first", "one", "two"):
            self.assertIn(f"## {identifier}\n", qa)

    def test_portrait_concurrent_results_match_committed_versions(self):
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda i: self_archive.write_portrait(self.vault, f"portrait-{i}"), range(8)))
        self.assertEqual([row["text"] for row in results], [f"portrait-{i}" for i in range(8)])
        history = self.vault / vault_layout.SELF_MENTOR_PORTRAIT_HISTORY
        self.assertEqual(len(list(history.glob("*.md"))), 7)
        self.assertIn(self_archive.read_portrait(self.vault)["text"], [row["text"] for row in results])

    def test_vault_escape_rejected_without_read_or_write(self):
        with tempfile.TemporaryDirectory(prefix="torch-outside-") as folder:
            outside = Path(folder) / "private.md"
            outside.write_text("unchanged", encoding="utf-8")
            with self.assertRaises(ValueError):
                memory_store.read_document(outside, vault_dir=self.vault)
            with self.assertRaises(ValueError):
                with memory_store.update_document(outside, memory.MentorMemoryError, vault_dir=self.vault) as update:
                    update.write("wrong")
            self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged")

    def test_symlink_memory_rejected(self):
        memory.ensure_memory(self.vault)
        target = self.vault / vault_layout.SELF_MENTOR_MEMORY_DIR / "核心记忆.md"
        outside = self.vault / "other.md"
        outside.write_text("unchanged", encoding="utf-8")
        target.unlink()
        try:
            os.symlink(outside, target)
        except OSError as error:
            self.skipTest(f"symlink unavailable: {error}")
        with self.assertRaises(ValueError):
            memory.read_core(self.vault)
        with self.assertRaises(ValueError):
            memory.write_core(self.vault, "wrong")
        self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged")

    def test_link_checks_are_not_bypassed_by_lock_key_normalization(self):
        memory.write_core(self.vault, "original")
        target = self.vault / vault_layout.SELF_MENTOR_MEMORY_DIR / "核心记忆.md"
        for method in ("is_symlink", "is_junction"):
            if not hasattr(Path, method):
                continue
            original = getattr(Path, method)
            with self.subTest(method=method), patch.object(Path, method,
                    lambda path: path == target or original(path)):
                with self.assertRaises(ValueError):
                    memory.read_core(self.vault)
                with self.assertRaises(ValueError):
                    memory.write_core(self.vault, "wrong")
        self.assertEqual(memory.read_core(self.vault)["text"], "original")

    def test_write_result_retains_read_format(self):
        for write, read in ((memory.write_core, memory.read_core),
                            (self_archive.write_portrait, self_archive.read_portrait)):
            with self.subTest(writer=write.__name__):
                result = write(self.vault, "第一行\r\n\r\n第二行")
                result.pop("history_path")
                self.assertEqual(result, read(self.vault))

    def test_read_only_writes_create_nothing(self):
        for call in (
            lambda: memory.ensure_memory(self.vault, read_only=True),
            lambda: memory.write_core(self.vault, "new", read_only=True),
            lambda: memory.append_timeline(self.vault, [{"text": "new"}], read_only=True),
            lambda: memory.append_qa(self.vault, question="q", answer="a", read_only=True),
            lambda: self_archive.write_portrait(self.vault, "new", read_only=True),
        ):
            with self.assertRaises((memory.MentorMemoryError, self_archive.SelfArchiveError)):
                call()
        self.assertEqual(list(self.vault.iterdir()), [])

    def test_bad_encoding_and_yaml_do_not_reset_existing_history(self):
        for raw in (b"\xff\xfeinvalid", b"---\nkey: [\n---\nold history"):
            for name, write in (
                ("记忆时间线.md", lambda: memory.append_timeline(self.vault, [{"text":"new"}])),
                ("问答记忆.md", lambda: memory.append_qa(self.vault, question="q", answer="a")),
            ):
                path = self.vault / vault_layout.SELF_MENTOR_MEMORY_DIR / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                with self.assertRaises(memory.MentorMemoryError):
                    write()
                self.assertEqual(path.read_bytes(), raw)

    def test_synthesis_reports_memory_failure(self):
        updates = [SimpleNamespace(kind="core", text="new", sources=[])]
        with patch.object(memory, "write_core", side_effect=memory.MentorMemoryError("backup failed", 500)):
            with self.assertRaisesRegex(self_synthesize.SynthesizeError, "核心记忆保存失败"):
                self_synthesize._persist_memory(self.vault, updates, read_only=False)

    def test_chat_memory_failure_has_explicit_error(self):
        service = SimpleNamespace(vault_dir=self.vault, read_only=False)
        note = SimpleNamespace(kind="observation", text="remember me", sources=[])
        errors = []
        with patch.object(memory, "append_timeline", side_effect=memory.MentorMemoryError("save failed", 500)):
            count = RoleService._ingest_memory_notes(service, SimpleNamespace(memory_notes=[note]),
                                                     SimpleNamespace(id="mentor"), errors=errors)
        self.assertEqual(count, 0)
        self.assertEqual(errors, ["save failed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
