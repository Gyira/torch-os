import test from "node:test";
import assert from "node:assert/strict";

import {
  habitCardHtml, habitDetailHtml, habitGroupChips, weekStripHtml,
} from "../habits-markup.js";

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

test("week strip shows per-day counts and progress track", () => {
  const html = weekStripHtml({
    days: [
      { date: "2026-11-04", weekday: 2, scheduled: 3, done: 2, skipped: 0, completion_rate: 0.67 },
      { date: "2026-11-05", weekday: 3, scheduled: 2, done: 2, skipped: 0, completion_rate: 1 },
      { date: "2026-11-06", weekday: 4, scheduled: 0, done: 0, skipped: 0, completion_rate: null },
    ],
  }, "2026-11-05");
  assert.match(html, /2\/3/);
  assert.match(html, /2\/2/);
  assert.match(html, /habit-week-cell is-today is-complete/);
  assert.match(html, /habit-week-track/);
  assert.match(html, /data-habit-progress="67"/);
  assert.match(html, /is-empty/);
});

test("group chips mark the current filter and keep counts", () => {
  const html = habitGroupChips([
    { name: "健康", count: 2 }, { name: "生活", count: 3 },
  ], "健康");
  assert.match(html, /data-habit-group=""[^>]*aria-pressed="false"/);
  assert.match(html, /data-habit-group="健康"[^>]*aria-pressed="true"/);
  assert.match(html, /habit-group-count">2</);
  assert.match(html, /habit-group-count">3</);
});
