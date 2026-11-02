"""B3-1: temporary vaults, fake model output; never reads personal configuration."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from backend.app import ai_context, health, life_reports, memory_hub, vault_layout
from backend.app.ai_gateway import HeadlessResult
from backend.app.ai_roles import RoleRegistry, RoleService, create_ai_router
from backend.app.db import Database
from backend.app.love_layout import love_paths
from backend.app.role_policy import (HABITS, HEALTH, FINANCE, ROLE_ROOTS,
                                      RolePolicy, RoleScopeError, scoped_reads)

REPO = Path(__file__).resolve().parents[1]


class RoleBoundaryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="torch-role-test-")
        self.addCleanup(temp.cleanup)
        self.vault = Path(temp.name) / "vault"
        self.vault.mkdir()
        self.db = Database(Path(temp.name) / "state.sqlite")
        self.db.initialize()
        self.registry = RoleRegistry(REPO / "dsh/roles")
        self.service = RoleService(self.registry, database=self.db, vault_dir=self.vault,
                                   love_settings=love_paths(self.vault))
        self.model = Mock(return_value=HeadlessResult("回答", "new-session", .01, 0, ""))
        self.service._execute = self.model

    def note(self, relative, body="allowed-content"):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\ntitle: 测试\n---\n\n" + body, encoding="utf-8")
        return path

    def test_every_role_scope_and_sibling_denial(self):
        domains = ["00_人生路", "01_自我档案", vault_layout.LOVE_ROOT, HEALTH,
                   FINANCE, "02_生活运营/04_家庭系统", "03_知识库"]
        for role, roots in ROLE_ROOTS.items():
            for domain in domains:
                relative = domain + "/测试.md"
                allowed = any(domain == r or domain.startswith(r + "/") for r in roots)
                with self.subTest(role=role, domain=domain):
                    policy = RolePolicy.for_role(role, self.vault)
                    if allowed:
                        self.assertEqual(policy.check_path(relative, note=True), self.vault / relative)
                    else:
                        with self.assertRaises(RoleScopeError):
                            policy.check_path(relative, note=True)

    def test_all_selector_permissions_before_any_content_read(self):
        with patch.object(ai_context, "_build_one") as reader:
            with self.assertRaises(RoleScopeError):
                self.service.run_once("health_doctor", "advice", "问题", selectors=[
                    {"entry": "health"}, {"entry": "identity"}])
            reader.assert_not_called()
        self.model.assert_not_called()

    def test_all_source_selectors_have_positive_and_negative_cases(self):
        sources = {
            "identity": "mentor", "journal": "journal_companion", "journals_recent": "journal_companion",
            "tasks": "life_assistant", "health": "health_doctor", "finance": "finance_advisor",
            "love": "love_master", "self_archive": "mentor", "mentor_memory": "mentor",
            "recent_notes": "academic_mentor", "ops_summary": "life_assistant", "bazi_chart": "mentor",
        }
        for source, role in sources.items():
            with self.subTest(source=source):
                policy = RolePolicy.for_role(role, self.vault)
                policy.validate_selectors([{"entry": source}], love_settings=self.service.love_settings)
                denied = "health_doctor" if role != "health_doctor" else "academic_mentor"
                with self.assertRaises(RoleScopeError):
                    RolePolicy.for_role(denied, self.vault).validate_selectors([{"entry": source}])

    def test_path_escape_prefix_hidden_and_stream_rejected(self):
        policy = RolePolicy.for_role("health_doctor", self.vault)
        for path in [HEALTH + "/../财务/a.md", HEALTH + "副本/a.md", "C:/secret.md",
                     "//server/file.md", HEALTH + "/a.md:secret", HEALTH + "/.env",
                     HEALTH + "/_系统/a.md", HEALTH + "/.backup/a.md"]:
            with self.subTest(path=path), self.assertRaises(RoleScopeError):
                policy.check_path(path, note=True)

    def test_junction_rejected_before_builder(self):
        path = self.note(HEALTH + "/linked.md")
        original = Path.lstat
        def reparse(item, *args, **kwargs):
            value = original(item, *args, **kwargs)
            if item == path:
                return type("Reparse", (), {"st_mode": value.st_mode, "st_file_attributes": 0x400})()
            return value
        builder = Mock()
        with patch.object(Path, "lstat", reparse), self.assertRaises(RoleScopeError):
            self.service.prepare_context("health_doctor", "health", builder)
        builder.assert_not_called()

    def test_real_symlink_escape_when_supported(self):
        path = self.vault / HEALTH / "linked.md"
        path.parent.mkdir(parents=True)
        target = Path(self.vault.parent) / "outside.md"
        target.write_text("secret", encoding="utf-8")
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest("Windows symlink privilege unavailable; reparse guard covered separately")
        with self.assertRaises(RoleScopeError):
            RolePolicy.for_role("health_doctor", self.vault).check_path(HEALTH + "/linked.md")

    def test_note_context_and_user_text_are_separate(self):
        relative = HEALTH + "/记录.md"
        self.note(relative)
        self.service.run_once("health_doctor", "advice", "我主动告诉你其他话题", selectors=[{"entry": "note", "path": relative}])
        prompt = self.model.call_args.args[0]
        self.assertIn("allowed-content", prompt)
        self.assertIn("我主动告诉你其他话题", prompt)
        self.assertNotIn("01_自我档案", prompt)

    def test_default_life_assistant_excludes_identity(self):
        self.note(vault_layout.SELF_IDENTITY_RELATIVE, "private-identity-sentinel")
        self.service.chat("life_assistant", "tasks", "今天干什么")
        prompt = self.model.call_args.args[0]
        self.assertNotIn("private-identity-sentinel", prompt)
        self.assertNotIn("身份与三观", prompt)

    def test_habit_summary_does_not_call_workbench_or_grant_life_notes(self):
        from backend.app import workbench
        with patch.object(workbench, "collect_day", side_effect=AssertionError("global workbench read")):
            result = ai_context.assemble(self.db, self.vault, role_id="life_assistant", selectors=[{"entry": "tasks"}])
        self.assertIn("任务", result.text)
        with self.assertRaises(RoleScopeError):
            RolePolicy.for_role("life_assistant", self.vault).check_path(HABITS + "/习惯.md", note=True)

    def test_raw_run_and_foreign_prepared_context_rejected(self):
        with self.assertRaises(RoleScopeError):
            self.service.run("health_doctor", "report", context_text="unvalidated")
        with self.assertRaises(RoleScopeError):
            self.service.run("health_doctor", "report", session_id="old")
        context = self.service.prepare_context("mentor", "self_archive", lambda: "private")
        with self.assertRaises(RoleScopeError):
            self.service.run("health_doctor", "report", context=context)
        self.model.assert_not_called()

    def test_prepared_builder_denied_before_read(self):
        builder = Mock(return_value="secret")
        with self.assertRaises(RoleScopeError):
            self.service.prepare_context("health_doctor", "self_archive", builder)
        builder.assert_not_called()

    def test_external_love_database_and_altered_paths_rejected(self):
        from dataclasses import replace
        paths = self.service.love_settings
        for settings in [object(), replace(paths, chat=self.vault / "03_知识库")]:
            builder = Mock()
            with self.assertRaises(RoleScopeError):
                self.service.prepare_context("love_master", "love", builder, love_settings=settings)
            builder.assert_not_called()

    def test_modern_layout_only_for_ai_legacy_still_readable_outside_ai(self):
        from backend.app import identity, journal_note
        self.note(identity.IDENTITY_LEGACY_ALT, "legacy-identity")
        day = "2026-11-03"
        self.note(vault_layout.legacy_journal_path(day), "legacy-journal")
        policy = RolePolicy.for_role("mentor", self.vault)
        with scoped_reads(policy):
            self.assertNotIn("legacy-identity", str(identity.read_identity(self.vault)))
            self.assertFalse(journal_note.read_note(self.vault, day)["exists"])
        self.assertTrue(journal_note.read_note(self.vault, day)["exists"])

    def test_old_session_and_history_rotate_once_without_deletion(self):
        thread = self.service._create_thread("health_doctor", "advice", "旧对话")
        self.service._append_turn(thread["id"], "user", "old-private-sentinel")
        self.service._update_thread(thread["id"], dsh_session_id="old-session")
        first = self.service.chat("health_doctor", "advice", "new-question", thread_id=thread["id"])
        self.assertTrue(first["context_reset"])
        self.assertEqual(self.model.call_args.kwargs["session_id"], "")
        self.assertNotIn("old-private-sentinel", self.model.call_args.args[0])
        # Recreate service to verify persistence rather than an in-memory flag.
        other = RoleService(self.registry, database=self.db, vault_dir=self.vault)
        other._execute = self.model
        second = other.chat("health_doctor", "advice", "follow-up", thread_id=thread["id"])
        self.assertFalse(second["context_reset"])
        self.assertEqual(self.model.call_args.kwargs["session_id"], "new-session")
        self.assertIn("new-question", self.model.call_args.args[0])
        self.assertNotIn("old-private-sentinel", self.model.call_args.args[0])
        self.assertEqual(len(other.thread(thread["id"])["turns"]), 5)

    def test_late_response_does_not_publish_or_ingest(self):
        thread = self.service._create_thread("mentor", "converse", "")
        def supersede(*args, **kwargs):
            with self.db.connect() as con:
                con.execute("UPDATE ai_thread_policy SET request_token='replacement', scope_fingerprint='new-policy' WHERE thread_id=?", (thread["id"],))
            return HeadlessResult('{"reply":"stale","memory_notes":[]}', "stale-session", .1, 0, "")
        self.model.side_effect = supersede
        with patch.object(self.service, "_create_proposals") as proposals, patch.object(self.service, "_ingest_memory_notes") as memory:
            with self.assertRaises(HTTPException) as error:
                self.service.chat("mentor", "converse", "question", thread_id=thread["id"])
            self.assertEqual(error.exception.status_code, 409)
            proposals.assert_not_called()
            memory.assert_not_called()
        self.assertEqual(self.service.thread(thread["id"])["turns"], [])
        self.assertEqual(self.service._thread_row(thread["id"])["dsh_session_id"], "")

    def test_failed_first_turn_keeps_reset_notice_for_retry(self):
        thread = self.service._create_thread("health_doctor", "advice", "")
        self.service._append_turn(thread["id"], "user", "old-question")
        self.model.side_effect = RuntimeError("temporary failure")
        with self.assertRaises(RuntimeError):
            self.service.chat("health_doctor", "advice", "retry-question", thread_id=thread["id"])
        self.model.side_effect = None
        result = self.service.chat("health_doctor", "advice", "retry-question", thread_id=thread["id"])
        self.assertTrue(result["context_reset"])
        self.assertNotIn("old-question", self.model.call_args.args[0])

    def test_retired_role_and_hub_stop_before_collection(self):
        for call in [lambda: self.service.chat("yiling", "converse", "hi"),
                     lambda: self.service.run_once("yiling", "synthesize", "hi"),
                     lambda: self.service.run("yiling", "synthesize")]:
            with self.assertRaises(HTTPException) as error:
                call()
            self.assertEqual(error.exception.status_code, 409)
        with patch.object(memory_hub, "collect_context") as reader:
            with self.assertRaises(memory_hub.MemoryHubError):
                memory_hub.synthesize(self.db, self.vault, self.service, None)
            reader.assert_not_called()
        self.model.assert_not_called()

    def test_one_doctor_role_and_estimate_compatibility_alias(self):
        self.assertNotIn("health_estimator", self.registry.roles)
        self.assertIn("estimate", self.registry.get("health_doctor").modes)
        result = self.service.run_once("health_estimator", "estimate", "一个苹果", entry="health.report")
        self.assertEqual(result["role_id"], "health_doctor")
        self.assertEqual(self.model.call_args.args[1].id, "health_doctor")
        self.assertEqual(self.model.call_args.kwargs["timeout"], 60)
        self.assertEqual(health.ESTIMATE_ROLE, "health_doctor")

    def test_http_denial_is_403_and_keeps_model_idle(self):
        app = FastAPI()
        app.include_router(create_ai_router(self.service))
        with TestClient(app) as client:
            response = client.post("/api/ai/chat", json={"role_id": "health_doctor", "mode": "advice", "message": "draft", "selectors": [{"entry": "identity"}]})
            self.assertEqual(response.status_code, 403)
            self.assertIn("role_scope_denied", response.json()["detail"])
        self.model.assert_not_called()
        self.assertEqual(self.service.threads(), [])

    def test_selection_path_cannot_bypass_role(self):
        with self.assertRaises(RoleScopeError):
            self.service.chat("health_doctor", "advice", "问题", selection_text="引用",
                              selection_path=vault_layout.SELF_IDENTITY_RELATIVE)
        self.model.assert_not_called()

    def test_health_and_life_reports_use_scoped_context(self):
        self.model.return_value = HeadlessResult('{"note":{"markdown":"报告"}}', "", .01, 0, "")
        with patch.object(health.pillar_notes, "create_note", return_value={"path": "report.md"}), \
             patch.object(health, "_report_title", return_value="测试报告"):
            health._report_body(self.db, self.vault, self.service, read_only=False)
        self.assertIn("只读资料", self.model.call_args.args[0])
        for domain in ("life", "health", "finance", "love"):
            with self.subTest(domain=domain), patch.object(life_reports.pillar_notes, "create_note", return_value={"path": "report.md"}):
                result = life_reports.generate_report(self.db, self.vault, self.service, None, domain,
                                                     love_settings=self.service.love_settings)
                self.assertEqual(result["domain"], domain)

    def test_love_analysis_and_journal_batch_keep_their_existing_outputs(self):
        from backend.app import love_master
        paths = self.service.love_settings
        love_master.ensure_master_schema(paths)
        with love_master._connect(paths.master_db) as con:
            con.execute("INSERT INTO runs(id,skill,status,created_at) VALUES(1,'portrait','pending','today')")
        self.model.return_value = HeadlessResult('{"title":"日记","body":"原有保存流程"}', "", .01, 0, "")
        with patch.object(love_master, "build_ai_context", return_value=("love-only-sentinel", {})), \
             patch.object(love_master, "_write_master_note", return_value={"path": "love.md"}):
            love_master._execute_run(paths, self.db, self.service, "", 1, "portrait", {}, False)
        self.assertIn("love-only-sentinel", self.model.call_args.args[0])
        with love_master._connect(paths.master_db) as con:
            self.assertEqual(con.execute("SELECT status FROM runs WHERE id=1").fetchone()[0], "completed")
        with patch.object(love_master, "_note_dates", return_value=set()), \
             patch.object(love_master, "_message_days", return_value=["2026-11-03"]), \
             patch.object(love_master, "_day_messages", return_value=[{"text": "原文用于日记"}]), \
             patch.object(love_master.note_editor, "create_note", return_value={"path": "draft.md"}) as writer:
            result = love_master._journal_batch(paths, self.db, self.service, 1, {}, False)
        self.assertEqual(len(result["created"]), 1)
        self.assertIn("原文用于日记", self.model.call_args.args[0])
        self.assertEqual(writer.call_args.kwargs["chapter"], vault_layout.LOVE_DRAFT_CHAPTER)

    def test_mentor_bazi_and_daily_questions_use_prepared_sources(self):
        from backend.app import self_bazi, mentor_questions
        self.model.return_value = HeadlessResult('{"summary":"解读","drafts":[]}', "", .01, 0, "")
        with patch.object(self_bazi, "read_profile", return_value={"exists": True}), \
             patch.object(self_bazi, "chart_from_profile", return_value={"test": "bazi-source"}), \
             patch.object(self_bazi, "write_reading", return_value={"path": "reading.md"}):
            self_bazi.generate_reading(self.service, self.vault)
        self.assertIn("bazi-source", self.model.call_args.args[0])
        with patch.object(mentor_questions, "ensure_today", return_value={"items": [{"id": "q1", "prompt": "daily-question-source"}]}):
            mentor_questions.draft_today(self.service, self.vault)
        self.assertIn("daily-question-source", self.model.call_args.args[0])

    def test_estimate_http_still_returns_preview_items(self):
        self.model.return_value = HeadlessResult(json.dumps({"items": [{"name": "苹果", "portion": "一个", "calories": 90}]}), "", .01, 0, "")
        app = FastAPI()
        app.include_router(health.create_health_router(self.db, self.vault, role_service=self.service))
        with TestClient(app) as client:
            result = client.post("/api/health/estimate", json={"kind": "food", "text": "一个苹果"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["items"][0]["name"], "苹果")
        self.assertEqual(self.model.call_args.args[1].id, "health_doctor")

    def test_context_fingerprint_change_rejects_already_prepared_data(self):
        context = self.service.prepare_context("health_doctor", "health", lambda: "data")
        with patch("backend.app.role_policy.POLICY_VERSION", "new-version"), self.assertRaises(RoleScopeError):
            self.service.run("health_doctor", "report", context=context)
        self.model.assert_not_called()

    def test_legacy_thread_read_remains_available(self):
        thread = self.service._create_thread("mentor", "converse", "legacy")
        self.service._append_turn(thread["id"], "assistant", "历史记录")
        with self.db.connect() as con:
            con.execute("UPDATE ai_threads SET role_id='yiling' WHERE id=?", (thread["id"],))
        app = FastAPI()
        app.include_router(create_ai_router(self.service))
        with TestClient(app) as client:
            result = client.get("/api/ai/threads/" + thread["id"])
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["turns"][0]["content"], "历史记录")


if __name__ == "__main__":
    unittest.main(verbosity=2)
