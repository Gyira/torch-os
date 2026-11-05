import test from "node:test";
import assert from "node:assert/strict";

import { habitCardHtml, habitDetailHtml } from "../habits-markup.js";

function habit(overrides = {}) {
  return {
    id: 1, uid: "h1", title: "读书", icon: "📚", color: "", group: "默认",
    frequency: { kind: "daily" }, frequency_label: "每天", status: "active",
    entry_today: { status: "unrecorded", date: "2026-11-05", revision: 0 },
    stats: {
      total_done: 3, current_streak: 2, streak_unit: "天", month_done: 1,
      month_completion_rate: 0.5, target_days: 21, target_elapsed_days: 5,
      target_percent: 0.24,
    },
    scheduled_today: true, reminder: "",
    ...overrides,
  };
}

test("habit card shows dida synced badge", () => {
  const html = habitCardHtml(habit({ dida_status: "synced" }));
  assert.match(html, /滴答/);
  assert.match(html, /habit-card-badge is-dida/);
});

test("habit card shows dida conflict badge and escapes title", () => {
  const html = habitCardHtml(habit({
    title: "<script>alert(1)</script>", dida_status: "conflict",
  }));
  assert.match(html, /滴答冲突/);
  assert.match(html, /is-conflict/);
  assert.equal(html.includes("<script>alert"), false);
});

test("habit card shows failed status", () => {
  const html = habitCardHtml(habit({
    entry_today: { status: "failed", date: "2026-11-05", revision: 1 },
  }));
  assert.match(html, /is-failed/);
  assert.match(html, /失败/);
  assert.match(html, /✕/);
});

test("habit card without link has no dida badge", () => {
  const html = habitCardHtml(habit());
  assert.equal(html.includes("滴答"), false);
});

test("habit detail shows dida badge", () => {
  const html = habitDetailHtml(habit({ dida_status: "remote_deleted" }), {
    month: "2026-11", monthTitle: "2026年11月", grid: { cells: [] }, logs: [],
  });
  assert.match(html, /滴答已删/);
  assert.match(html, /is-muted/);
});
