"""B3-2 regression: actual executors against temporary data, no model/network."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app import life_reports, note_editor, vault_layout
from backend.app.ai_gateway import HeadlessResult
from backend.app.ai_roles import RoleRegistry, RoleService
from backend.app.ai_schemas import Reply
from backend.app.db import Database
from backend.app.love_layout import love_paths
from backend.app.proposal_actions import register_proposal_handlers
from backend.app.proposals import ProposalError, ProposalService, create_proposals_router
from backend.app.role_policy import RolePolicy, RoleScopeError, HEALTH, HABITS

REPO = Path(__file__).resolve().parents[1]


class RoleActionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="torch-role-action-test-")
        self.addCleanup(temp.cleanup)
        self.vault = Path(temp.name) / "vault"
        self.vault.mkdir()
        self.db = Database(Path(temp.name) / "state.sqlite")
        self.db.initialize()
        self.proposals = ProposalService(self.db, vault_dir=self.vault)
        register_proposal_handlers(self.proposals, self.db, self.vault)
        self.registry = RoleRegistry(REPO / "dsh/roles")
        self.service = RoleService(self.registry, database=self.db, vault_dir=self.vault,
                                   proposals=self.proposals, love_settings=love_paths(self.vault))
        self.service._execute = Mock(return_value=HeadlessResult("回答", "s", .01, 0, ""))

    def proposal(self, role="academic_mentor", kind="new_note", payload=None, **kwargs):
        return self.proposals.create(role_id=role, kind=kind, title="测试建议",
                                     payload=payload if payload is not None else {"chapter": "topic", "body": "记录"}, **kwargs)

    def test_mentor_global_read_does_not_grant_global_write(self):
        policy = RolePolicy.for_role("mentor", self.vault)
        policy.check_path("03_知识库/01_主题笔记/内容.md", note=True)
        policy.check_write_path(vault_layout.SELF_MENTOR_MEMORY_DIR)
        with self.assertRaises(RoleScopeError):
            policy.check_write_path("03_知识库/01_主题笔记/内容.md")

    def test_create_cross_domain_proposal_is_rejected_without_row_or_file(self):
        with self.assertRaises(ProposalError) as error:
            self.proposal(payload={"chapter": "reflection", "body": "bad"})
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(self.proposals.list(), [])
        self.assertEqual(list(self.vault.rglob("*.md")), [])

    def test_final_edits_revalidated_and_proposal_remains_pending(self):
        proposal = self.proposal()
        with self.assertRaises(ProposalError) as error:
            self.proposals.apply(proposal["id"], confirm=True, edits={"chapter": "reflection"})
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(self.proposals.get(proposal["id"])["status"], "pending")
        self.assertEqual(list(self.vault.rglob("*.md")), [])
        saved = self.proposals.apply(proposal["id"], confirm=True, edits={"title": "正确修改"})
        self.assertEqual(saved["status"], "applied")
        self.assertTrue((self.vault / saved["result"]["note"]["path"]).exists())

    def test_unknown_or_retired_origin_cannot_apply_old_proposal(self):
        for role in ("yiling", "", "made_up"):
            with self.subTest(role=role):
                proposal = self.proposal(payload={"chapter": "topic", "body": role})
                with self.db.connect() as con:
                    con.execute("UPDATE ai_proposals SET role_id=? WHERE id=?", (role, proposal["id"]))
                with self.assertRaises(ProposalError):
                    self.proposals.apply(proposal["id"], confirm=True)
                self.assertEqual(self.proposals.get(proposal["id"])["status"], "pending")

    def test_old_pending_target_is_revalidated(self):
        proposal = self.proposal()
        with self.db.connect() as con:
            con.execute("UPDATE ai_proposals SET payload_json=? WHERE id=?",
                        (json.dumps({"chapter": "reflection", "body": "old"}), proposal["id"]))
        with self.assertRaises(ProposalError):
            self.proposals.apply(proposal["id"], confirm=True)
        self.assertEqual(list(self.vault.rglob("*.md")), [])

    def test_move_checks_source_and_edited_destination_before_read_or_write(self):
        source = note_editor.create_note(self.db, self.vault, chapter="topic", title="原文", body="keep")
        proposal = self.proposal(kind="archive_note", payload={"path": source["path"], "target_chapter": "reading"})
        for edits in ({"path": "00_人生路/06_随笔/他人.md"}, {"chapter": "reflection"}):
            with self.subTest(edits=edits), patch.object(note_editor, "move_note") as writer:
                with self.assertRaises(ProposalError):
                    self.proposals.apply(proposal["id"], confirm=True, edits=edits)
                writer.assert_not_called()
        result = self.proposals.apply(proposal["id"], confirm=True)
        self.assertFalse((self.vault / source["path"]).exists())
        self.assertTrue((self.vault / result["result"]["note"]["path"]).exists())

    def test_love_creates_task_through_typed_executor_without_task_read_scope(self):
        proposal = self.proposal("love_master", "new_task", {"title": "准备约会", "list": "约会", "due_date": "2026-11-05"})
        result = self.proposals.apply(proposal["id"], confirm=True, edits={"list_name": "", "date": "2026-11-06"})
        task = result["result"]["task"]
        self.assertTrue(task["path"].startswith(vault_layout.TASK_ROOT + "/"))
        self.assertEqual(task["list_name"], "收集箱")
        self.assertEqual(task["date"], "2026-11-06")
        with self.assertRaises(RoleScopeError):
            RolePolicy.for_role("love_master", self.vault).check_path(task["path"], note=True)

    def test_life_creates_habit_without_opening_life_notes(self):
        proposal = self.proposal("life_assistant", "new_habit", {"title": "散步", "minimum_action": "走五分钟"})
        result = self.proposals.apply(proposal["id"], confirm=True)
        self.assertEqual(result["status"], "applied")
        self.assertTrue(list((self.vault / HABITS).rglob("*.md")))
        with self.assertRaises(RoleScopeError):
            RolePolicy.for_role("life_assistant", self.vault).check_path("00_人生路/06_随笔/记录.md", note=True)

    def test_task_and_habit_cannot_receive_paths_or_actor_overrides(self):
        for kind in ("new_task", "new_habit"):
            proposal = self.proposal("life_assistant", kind, {"title": kind})
            for field in ("path", "target_chapter", "role_id", "vault_dir", "generated_by"):
                with self.subTest(kind=kind, field=field), self.assertRaises(ProposalError):
                    self.proposals.apply(proposal["id"], confirm=True, edits={field: "other"})
        self.assertEqual(list(self.vault.rglob("*.md")), [])

    def test_task_list_traversal_is_rejected(self):
        with self.assertRaises(ProposalError):
            self.proposal("love_master", "new_task", {"title": "bad", "list_name": "../../逃逸"})

    def test_replaced_destination_link_is_rejected_before_executor(self):
        proposal = self.proposal()
        target = self.vault / vault_layout.chapter_dir("topic")
        target.mkdir(parents=True)
        original = Path.lstat
        def linked(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == target:
                return type("Link", (), {"st_mode": value.st_mode, "st_file_attributes": 0x400})()
            return value
        with patch.object(Path, "lstat", linked), patch.object(note_editor, "create_note") as writer:
            with self.assertRaises(ProposalError):
                self.proposals.apply(proposal["id"], confirm=True)
            writer.assert_not_called()
        self.assertEqual(self.proposals.get(proposal["id"])["status"], "pending")

    def test_unimplemented_report_actions_stay_disabled(self):
        proposal = self.proposal("health_doctor", "health_report", {"body": "report"})
        with self.assertRaises(ProposalError) as error:
            self.proposals.apply(proposal["id"], confirm=True)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(list(self.vault.rglob("*.md")), [])

    def test_domain_roles_cannot_borrow_other_action_kinds(self):
        for role, kind in [("academic_mentor", "new_task"), ("love_master", "new_habit"), ("health_doctor", "new_note")]:
            with self.subTest(role=role), self.assertRaises(ProposalError):
                self.proposal(role, kind, {"title": "no", "chapter": "topic"})

    def test_generated_by_is_the_real_role(self):
        proposal = self.proposal(payload={"chapter": "topic", "body": "note", "generated_by": "mentor"})
        result = self.proposals.apply(proposal["id"], confirm=True)
        self.assertEqual(result["result"]["note"]["raw_metadata"]["generated_by"], "academic_mentor")

    def test_read_only_executor_writes_no_notes(self):
        register_proposal_handlers(self.proposals, self.db, self.vault, read_only=True)
        proposal = self.proposal()
        with self.assertRaises(ProposalError):
            self.proposals.apply(proposal["id"], confirm=True)
        self.assertEqual(list(self.vault.rglob("*.md")), [])

    def test_double_apply_claims_executor_once(self):
        proposal = self.proposal()
        started, release = threading.Event(), threading.Event()
        def writer(*args):
            started.set()
            self.assertTrue(release.wait(5))
            return {"done": True}
        handler = Mock(side_effect=writer)
        self.proposals.register("new_note", handler)
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.proposals.apply, proposal["id"], confirm=True)
            self.assertTrue(started.wait(5))
            try:
                with self.assertRaises(ProposalError):
                    self.proposals.apply(proposal["id"], confirm=True)
                with self.assertRaises(ProposalError):
                    self.proposals.reject(proposal["id"])
            finally:
                release.set()
            self.assertEqual(first.result()["status"], "applied")
        handler.assert_called_once()
        self.proposals.apply(proposal["id"], confirm=True)
        handler.assert_called_once()

    def test_proposal_sources_resolve_then_revalidate(self):
        reply = Reply.model_validate({"proposals": [{"kind": "new_note", "title": "建议", "payload": {"chapter": "topic", "body": "text"}, "sources": ["S1"]}]})
        role = self.registry.get("academic_mentor")
        source = "03_知识库/01_主题笔记/来源.md"
        items = self.service._create_proposals(reply, role, "discuss", "", sources=[{"id": "S1", "path": source}])
        self.assertEqual(items[0]["sources"], [source])
        with self.db.connect() as con:
            con.execute("UPDATE ai_proposals SET sources_json=? WHERE id=?", (json.dumps([HEALTH + "/私密.md"]), items[0]["id"]))
        with self.assertRaises(ProposalError):
            self.proposals.apply(items[0]["id"], confirm=True)

    def test_event_is_one_way_and_rejects_foreign_sources(self):
        thread = self.service._create_thread("health_doctor", "advice", "")
        reply = Reply.model_validate({"hub_events": [
            {"summary": "健康观察", "source_ref": "S1"},
            {"summary": "越域观察", "source_ref": vault_layout.SELF_IDENTITY_RELATIVE},
            {"summary": "伪造会话", "source_ref": "thread_someone_else"},
            {"summary": "错误地址", "source_ref": "https://[invalid"}]})
        errors = []
        count = self.service._ingest_events(reply, self.registry.get("health_doctor"), thread["id"],
            sources=[{"id": "S1", "kind": "health", "path": ""}], errors=errors)
        self.assertEqual(count, 1)
        self.assertEqual(len(errors), 3)
        with self.db.connect() as con:
            row = con.execute("SELECT source_kind,source_ref FROM memory_hub_events").fetchone()
        self.assertEqual(tuple(row), ("health_doctor", "context:health"))
        self.assertFalse((self.vault / vault_layout.SELF_ARCHIVE_ROOT).exists())

    def test_read_only_role_does_not_post_events(self):
        self.service.read_only = True
        errors = []
        reply = Reply.model_validate({"hub_events": [{"summary": "observation"}]})
        self.assertEqual(self.service._ingest_events(reply, self.registry.get("health_doctor"), "", errors=errors), 0)
        self.assertTrue(errors)
        with self.db.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM memory_hub_events").fetchone()[0], 0)

    def test_cross_domain_model_output_returns_explicit_errors_and_keeps_reply(self):
        self.service._execute.return_value = HeadlessResult(json.dumps({"reply": "正常回答", "proposals": [{"kind": "new_note", "title": "越域", "payload": {"chapter": "reflection"}}]}), "", .01, 0, "")
        result = self.service.chat("academic_mentor", "discuss", "问题")
        self.assertEqual(result["reply"], "正常回答")
        self.assertEqual(result["proposals"], [])
        self.assertEqual(len(result["action_errors"]), 1)

    def test_love_legacy_report_targets_love_and_stays_in_overview(self):
        self.service._execute.return_value = HeadlessResult('{"note":{"chapter":"life_advice","markdown":"恋爱报告"}}', "", .01, 0, "")
        result = life_reports.generate_report(self.db, self.vault, self.service, self.proposals, "love", love_settings=self.service.love_settings)
        path = result["note"]["path"]
        self.assertTrue(path.startswith(vault_layout.LOVE_DRAFT_DIR + "/"))
        self.assertIn(path, [item["path"] for item in life_reports._report_notes(self.vault)])

    def test_http_denial_has_stable_code_and_keeps_pending(self):
        proposal = self.proposal()
        app = FastAPI()
        app.include_router(create_proposals_router(self.proposals))
        with TestClient(app) as client:
            response = client.post(f"/api/proposals/{proposal['id']}/apply", json={"confirm": True, "edits": {"chapter": "reflection"}})
        self.assertEqual(response.status_code, 403)
        self.assertIn("role_scope_denied", response.json()["detail"])
        self.assertEqual(self.proposals.get(proposal["id"])["status"], "pending")


if __name__ == "__main__":
    unittest.main(verbosity=2)
