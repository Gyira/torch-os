import test from "node:test";
import assert from "node:assert/strict";

import { decorateHabitSync, didaLinkBadge, ENTRY_STATUS_LABELS } from "../habits-model.js";

test("decorateHabitSync merges link status by uid and leaves others untouched", () => {
  const habits = [
    { id: 1, uid: "h1", title: "读书" },
    { id: 2, uid: "h2", title: "跑步" },
  ];
  const links = [
    { uid: "h1", external_id: "dida-1", status: "synced" },
    { uid: "missing", external_id: "dida-x", status: "conflict" },
  ];
  const result = decorateHabitSync(habits, links);
  assert.equal(result[0].dida_status, "synced");
  assert.equal(result[0].dida_id, "dida-1");
  assert.equal(result[1].dida_status, "");
  assert.equal(result[1].dida_id, "");
  assert.equal(habits[0].dida_status, undefined);
});

test("decorateHabitSync is defensive about bad payloads", () => {
  assert.deepEqual(decorateHabitSync(null, null), []);
  assert.deepEqual(decorateHabitSync([{ uid: "h1" }], [{ status: "conflict" }]),
    [{ uid: "h1", dida_status: "", dida_id: "" }]);
});

test("ENTRY_STATUS_LABELS includes failed", () => {
  assert.equal(ENTRY_STATUS_LABELS.failed, "失败");
  assert.equal(ENTRY_STATUS_LABELS.skipped, "主动休息");
});

test("didaLinkBadge distinguishes statuses", () => {
  assert.equal(didaLinkBadge(null), null);
  assert.deepEqual(didaLinkBadge({ status: "synced" }), { text: "滴答", tone: "dida" });
  assert.deepEqual(didaLinkBadge({ status: "conflict" }),
    { text: "滴答冲突", tone: "conflict" });
  assert.deepEqual(didaLinkBadge({ status: "remote_deleted" }),
    { text: "滴答已删", tone: "muted" });
  assert.deepEqual(didaLinkBadge({ status: "unknown" }),
    { text: "滴答", tone: "dida" });
});
