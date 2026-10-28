import test from "node:test";
import assert from "node:assert/strict";

import {
  habitCalendarHtml, habitCardHtml, habitCardsHtml, habitDetailHtml,
  habitLogsHtml, habitStatsHtml, habitWarningsHtml, habitWeekdayLabel,
  weekStripHtml,
} from "../habits-markup.js";
import { monthGrid } from "../habits-model.js";

const TODAY = "2026-10-28";

function habit(overrides = {}) {
  return {
    id: 1, uid: "h1", title: "每天阅读", icon: "📚", color: "#3b82f6",
    group: "成长", frequency: { kind: "daily" }, frequency_label: "每天",
    reminder: "20:30", auto_log: true, status: "active", ended: false,
    scheduled_today: true, start_date: "2026-10-01", target_days: null,
    entry_today: { date: TODAY, status: "done", note: "", revision: 3 },
    stats: {
      month_done: 12, total_done: 30, month_completion_rate: 0.75,
      current_streak: 5, best_streak: 9, streak_unit: "天",
      target_days: null, target_elapsed_days: null, target_percent: null,
    },
    ...overrides,
  };
}

test("周条只输出 7 天并标出今天和完成", () => {
  const html = weekStripHtml({ days: [
    { date: "2026-10-27", weekday: 1, scheduled: 2, done: 2, skipped: 0, completion_rate: 1 },
    { date: TODAY, weekday: 2, scheduled: 2, done: 1, skipped: 0, completion_rate: 0.5, is_today: true },
  ] }, TODAY);
  assert.match(html, /habit-week-strip/);
  assert.match(html, /is-complete/);
  assert.match(html, /is-today/);
  assert.equal((html.match(/habit-week-cell/g) || []).length, 2);
  // CSP style-src 'self' blocks inline style attributes; progress goes through CSSOM.
  assert.equal(html.includes("style="), false);
  assert.match(html, /data-habit-progress="100"/);
});

test("习惯卡包含图标、连击、菜单，并转义用户文本", () => {
  const html = habitCardHtml(habit({ title: "<script>alert(1)</script>" }), { selectedId: 1 });
  assert.match(html, /is-selected/);
  assert.match(html, /is-checked/);
  assert.match(html, /📚/);
  assert.match(html, /🔥 5天/);
  assert.match(html, /data-habit-check="1"/);
  assert.match(html, /data-habit-menu="1"/);
  assert.match(html, /&lt;script&gt;/);
  assert.equal(html.includes("<script>alert"), false);
});

test("休息状态不打勾，已归档禁用打卡", () => {
  const skipped = habitCardHtml(habit({
    entry_today: { date: TODAY, status: "skipped", note: "休息", revision: 1 },
  }), { selectedId: 1 });
  assert.match(skipped, /is-skipped/);
  assert.match(skipped, /休息/);
  const archived = habitCardHtml(habit({ status: "archived", scheduled_today: false }), { selectedId: 1 });
  assert.match(archived, /is-archived/);
  assert.match(archived, /已归档/);
  assert.match(archived, /disabled/);
});

test("统计四格、目标条和空状态", () => {
  const html = habitStatsHtml(habit({
    target_days: 21,
    stats: { ...habit().stats, target_days: 21, target_elapsed_days: 5, target_percent: 0.2381 },
  }));
  assert.match(html, /月打卡/);
  assert.match(html, /总打卡/);
  assert.match(html, /月完成率/);
  assert.match(html, /当前连续/);
  assert.match(html, /75%/);
  assert.match(html, /坚持 5 \/ 21 天/);
  assert.equal(html.includes("style="), false);
  assert.match(html, /data-habit-target-percent="24"/);
  assert.match(habitCardsHtml([], 0), /还没有习惯/);
});

test("月历按状态着色，邻月/未来不可点", () => {
  const grid = monthGrid([
    { date: "2026-10-01", scheduled: true, status: "done" },
    { date: "2026-10-02", scheduled: true, status: "skipped" },
    { date: "2026-10-03", scheduled: true, status: "unrecorded" },
  ], "2026-10", TODAY);
  const html = habitCalendarHtml(grid);
  assert.match(html, /status-done/);
  assert.match(html, /status-skipped/);
  assert.match(html, /is-unscheduled/);
  assert.match(html, /is-today/);
  assert.match(html, /habit-cal-weekday">周日/);
  assert.match(html, /disabled/);
});

test("日志列表显示备注、状态和空提示", () => {
  const html = habitLogsHtml([
    { date: "2026-10-28", status: "done", note: "<b>读完</b>", updated_at: "2026-10-28T20:31:00+08:00" },
    { date: "2026-10-27", status: "skipped", note: "", updated_at: "" },
  ], "2026-10");
  assert.match(html, /10-28/);
  assert.match(html, /完成/);
  assert.match(html, /&lt;b&gt;读完&lt;\/b&gt;/);
  assert.match(html, /主动休息/);
  assert.match(html, /没有备注/);
  assert.match(html, /10月打卡日志/);
  assert.match(habitLogsHtml([], "2026-10"), /还没有打卡记录/);
});

test("详情空态、警告区和星期文案", () => {
  assert.match(habitDetailHtml(null), /选择一个习惯/);
  assert.match(habitWarningsHtml([{ path: "x.md", reason: "坏 YAML" }]), /读取提醒/);
  assert.match(habitWarningsHtml([]), /^$/);
  assert.equal(habitWeekdayLabel("2026-10-28"), "周三");
  const detail = habitDetailHtml(habit(), {
    month: "2026-10", monthTitle: "2026年10月",
    grid: monthGrid([], "2026-10", TODAY), logs: [],
  });
  assert.match(detail, /每天阅读/);
  assert.match(detail, /2026年10月/);
  assert.match(detail, /habit-calendar/);
});
