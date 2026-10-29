import test from "node:test";
import assert from "node:assert/strict";

import {
  kindLabel, proposalCanApply, proposalMarkup, proposalsMarkup,
} from "../proposals-model.js";

const task = {
  id: "prop_1",
  kind: "new_task",
  title: "写周报 <script>",
  reason: "今天截止",
  status: "pending",
  can_apply: true,
  sources: ["S1"],
  payload: { title: "写周报" },
};

test("proposal markup escapes AI text and shows apply/reject actions", () => {
  const markup = proposalMarkup(task);
  assert.ok(markup.includes('data-proposal-id="prop_1"'));
  assert.ok(markup.includes("新任务"));
  assert.ok(!markup.includes("<script>"));
  assert.ok(markup.includes("&lt;script&gt;"));
  assert.ok(markup.includes('data-proposal-action="apply"'));
  assert.ok(markup.includes('data-proposal-action="reject"'));
  assert.equal(proposalCanApply(task), true);
});

test("identity patch renders a from→to diff", () => {
  const markup = proposalMarkup({
    id: "prop_2",
    kind: "identity_patch",
    title: "调整价值观",
    status: "pending",
    can_apply: false,
    requires_dialogue: true,
    verdict_ready: false,
    payload: { changes: [{ field: "values", from: "随和", to: "诚实" }] },
  });
  assert.ok(markup.includes("身份修改"));
  assert.ok(markup.includes("随和"));
  assert.ok(markup.includes("诚实"));
  assert.ok(markup.includes("先和苏格拉底确认"));
  assert.ok(!markup.includes('data-proposal-action="apply"'));
});

test("applied proposal has no actions", () => {
  const markup = proposalMarkup({ ...task, status: "applied" });
  assert.ok(markup.includes("已采纳"));
  assert.ok(!markup.includes('data-proposal-action="apply"'));
  assert.equal(proposalCanApply({ ...task, status: "applied" }), false);
});

test("unknown kinds fall back to their id and empty lists render empty", () => {
  assert.equal(kindLabel("mystery"), "mystery");
  assert.equal(proposalsMarkup([]), "");
});
