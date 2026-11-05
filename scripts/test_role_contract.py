"""B3-3: role card / policy / manifest contract checks.

No model, no user vault. Reads only the repository's role cards and the
code-owned policy table so documentation cannot silently drift from ability.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import vault_layout
from backend.app.ai_roles import (ENTRIES, PROPOSAL_KINDS, RoleRegistry,
                                  RoleService)
from backend.app.role_policy import (POLICY_VERSION, ROLE_ACTIONS, ROLE_ROOTS,
                                     WRITE_ROOTS, active_role)

REPO = Path(__file__).resolve().parents[1]


class RoleContractTests(unittest.TestCase):
    def setUp(self):
        self.registry = RoleRegistry(REPO / "dsh/roles")
        self.service = RoleService(self.registry)
        self.manifest = self.service.manifest()

    def test_every_visible_role_has_fixed_policy(self):
        self.assertEqual(self.manifest["errors"], [])
        visible = {item["id"] for item in self.manifest["items"]}
        self.assertEqual(visible, set(ROLE_ROOTS))
        for item in self.manifest["items"]:
            role_id = item["id"]
            policy = item.get("policy") or {}
            self.assertEqual(policy.get("version"), POLICY_VERSION)
            self.assertEqual(policy.get("read_roots"), list(ROLE_ROOTS[role_id]))
            self.assertEqual(policy.get("write_roots"), list(WRITE_ROOTS[role_id]))
            self.assertEqual(policy.get("actions"), sorted(ROLE_ACTIONS[role_id]))
            profile_policy = self.service.profile(role_id).get("policy") or {}
            self.assertEqual(profile_policy, policy)
        self.assertEqual(set(ROLE_ROOTS), set(ROLE_ACTIONS))
        self.assertEqual(set(ROLE_ROOTS), set(WRITE_ROOTS))

    def test_role_cards_use_registered_entries_and_proposals(self):
        action_kinds = {"new_note", "archive_note", "new_task", "new_habit",
                        "health_report", "finance_report"}
        for role in self.registry.roles.values():
            with self.subTest(role=role.id):
                self.assertTrue(role.modes)
                self.assertTrue(set(role.entry) <= ENTRIES)
                self.assertTrue(set(role.proposals) <= PROPOSAL_KINDS)
                self.assertEqual(role.tools, "none")
                if role.id in ROLE_ACTIONS:
                    self.assertTrue(
                        (set(role.proposals) & action_kinds) <= set(ROLE_ACTIONS[role.id]),
                        f"{role.id} 角色卡提议了策略未开放的动作")

    def test_mentor_converse_prompt_lists_archive_books(self):
        """导师对话会把模型输出直接写档案：prompt 必须给出 11 个本子 key。"""
        from backend.app import self_archive
        prompt = self.registry.get("mentor").prompt_for("converse")
        for key in self_archive.BOOK_ORDER:
            self.assertIn(key, prompt)
        # 身份文件不是本子；模型最容易把来源标题当成 section。
        self.assertIn("不是本子", prompt)

    def test_mentor_write_scope_is_self_archive_only(self):
        self.assertEqual(ROLE_ROOTS["mentor"], tuple(vault_layout.PILLAR_DIRS.values()))
        self.assertEqual(WRITE_ROOTS["mentor"], ("01_自我档案",))
        self.assertEqual(ROLE_ACTIONS["mentor"], frozenset())
        self.assertTrue(self.manifest["mentor_autonomous_archive"])

    def test_domain_write_scope_stays_inside_read_scope(self):
        for role_id, roots in WRITE_ROOTS.items():
            for root in roots:
                self.assertTrue(
                    any(root == allowed or root.startswith(allowed + "/")
                        for allowed in ROLE_ROOTS[role_id]),
                    f"{role_id} writes outside its read scope: {root}")

    def test_legacy_and_alias_are_not_separate_roles(self):
        visible = {item["id"] for item in self.manifest["items"]}
        self.assertNotIn("yiling", visible)
        self.assertNotIn("health_estimator", visible)
        self.assertNotIn("health_estimator", self.registry.roles)
        self.assertEqual(active_role("health_estimator"), "health_doctor")
        self.assertIn("estimate", self.registry.get("health_doctor").modes)
        with self.assertRaises(HTTPException) as raised:
            active_role("yiling")
        self.assertEqual(raised.exception.status_code, 409)

    def test_manifest_does_not_expose_prompts_or_keys(self):
        text = repr(self.manifest)
        self.assertNotIn("sk-", text)
        self.assertNotIn("## mode:", text)
        self.assertNotIn("identity_text", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
