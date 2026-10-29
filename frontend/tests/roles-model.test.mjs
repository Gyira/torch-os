import test from "node:test";
import assert from "node:assert/strict";

import {
  escapeRoleText, messageMarkup, quickChips, roleTitle, sourceMarkup,
} from "../roles-model.js";

test("role text is escaped before it reaches the drawer", () => {
  const escaped = escapeRoleText('<img src=x onerror="alert(1)">');
  assert.ok(!escaped.includes("<img"));
  assert.ok(escaped.includes("&lt;img"));
  assert.equal(escapeRoleText("a & b"), "a &amp; b");
});

test("message markup labels user/assistant and keeps escaped content", () => {
  const user = messageMarkup({ role: "user", content: "<b>你</b>" });
  assert.ok(user.includes('data-role-message="user"'));
  assert.ok(user.includes("&lt;b&gt;你&lt;/b&gt;"));
  const assistant = messageMarkup({
    role: "assistant", content: "先做一步", sources: [{ id: "S1", title: "今天的任务" }],
  });
  assert.ok(assistant.includes('data-role-message="assistant"'));
  assert.ok(assistant.includes("先做一步"));
  assert.ok(assistant.includes("S1"));
  assert.ok(assistant.includes("今天的任务"));
});

test("assistant html from the markdown renderer is preserved", () => {
  const markup = messageMarkup({ role: "assistant", content: "x", html: "<p><strong>好</strong></p>" });
  assert.ok(markup.includes("<strong>好</strong>"));
});

test("role title combines the name and mode label", () => {
  assert.equal(roleTitle({ name: "忆灵" }, "converse"), "忆灵 · 对话");
  assert.equal(roleTitle({ name: "忆灵" }, "unknown"), "忆灵 · unknown");
});

test("quick chips are scoped to the role and mode", () => {
  assert.ok(quickChips("life_assistant", "tasks").length >= 2);
  assert.deepEqual(quickChips("life_assistant", "advice"), []);
  assert.ok(quickChips("yiling", "converse").some((text) => text.includes("纠结")));
});

test("source markup ignores empty entries", () => {
  assert.equal(sourceMarkup([]), "");
  assert.equal(sourceMarkup([{ id: "", title: "x" }]), "");
  assert.ok(sourceMarkup([{ id: "S2", kind: "journal" }]).includes("S2"));
});
