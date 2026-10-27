import test from "node:test";
import assert from "node:assert/strict";

import {
  defaultHabitDraft, entryFor, frequencyLabel, habitTaskKey, isHabitTask,
  logsForHabit, monthGrid, normalizeFrequency, percentLabel, shiftMonth,
  targetLabel, targetProgress, validateHabitDraft, weekStripDays,
} from "../habits-model.js";

const TODAY = "2026-10-28";

test("频率文案覆盖每天 / 每周 / 每月 / 每隔 N 天", () => {
  assert.equal(frequencyLabel({ kind: "daily" }), "每天");
  assert.equal(frequencyLabel({ kind: "weekly", weekdays: [0, 2, 4] }), "每周周一、周三、周五");
  assert.equal(frequencyLabel({ kind: "weekly", weekdays: [0, 1, 2, 3, 4, 5, 6] }), "每天");
  assert.equal(frequencyLabel({ kind: "monthly", month_days: [1, 15] }), "每月1日、15日");
  assert.equal(frequencyLabel({ kind: "interval", interval_days: 3 }), "每隔3天");
  assert.equal(targetLabel(null), "永远");
  assert.equal(targetLabel(21), "21天");
});

test("习惯任务判定与稳定 key", () => {
  assert.equal(isHabitTask({ kind: "habit" }), true);
  assert.equal(isHabitTask({ virtual: true }), true);
  assert.equal(isHabitTask({ kind: "task" }), false);
  assert.equal(habitTaskKey("h123", TODAY), `habit:h123:${TODAY}`);
});

test("月历周日开头，补齐邻月但不伪造计划", () => {
  const grid = monthGrid([
    { date: "2026-10-01", scheduled: true, status: "done" },
    { date: "2026-10-02", scheduled: true, status: "skipped" },
  ], "2026-10", TODAY);
  assert.equal(grid.cells.length % 7, 0);
  assert.equal(grid.cells[0].date, "2026-09-27"); // Sunday before Oct 1
  const first = grid.cells.find((cell) => cell.date === "2026-10-01");
  assert.equal(first.status, "done");
  assert.equal(first.scheduled, true);
  const second = grid.cells.find((cell) => cell.date === "2026-10-02");
  assert.equal(second.status, "skipped");
  const third = grid.cells.find((cell) => cell.date === "2026-10-03");
  assert.equal(third.scheduled, false);
  assert.equal(third.status, "unrecorded");
});

test("目标进度按日历窗口计算并夹紧", () => {
  assert.equal(targetProgress({ start_date: "2026-10-01", target_days: null }, TODAY), null);
  const progress = targetProgress({ start_date: "2026-10-01", target_days: 21 }, "2026-10-05");
  assert.deepEqual(progress, { targetDays: 21, elapsed: 5, percent: 24, ended: false });
  const before = targetProgress({ start_date: "2026-11-10", target_days: 21 }, TODAY);
  assert.equal(before.elapsed, 0);
  const ended = targetProgress({ start_date: "2026-09-01", target_days: 7 }, TODAY);
  assert.equal(ended.ended, true);
  assert.equal(ended.elapsed, 7);
});

test("草稿校验与频率归一", () => {
  const draft = defaultHabitDraft(TODAY, [{ name: "成长" }]);
  assert.equal(draft.title, "");
  assert.equal(draft.group, "成长");
  assert.match(validateHabitDraft(draft), /名称/);
  assert.equal(validateHabitDraft({ ...draft, title: "读书" }), "");
  assert.match(validateHabitDraft({ ...draft, title: "读书", frequency: { kind: "weekly", weekdays: [] } }), /每周/);
  assert.match(validateHabitDraft({ ...draft, title: "读书", reminder: "25:00" }), /提醒/);
  assert.deepEqual(normalizeFrequency("weekly", { weekdays: [2, 0, 2] }),
    { kind: "weekly", weekdays: [0, 2] });
  assert.deepEqual(normalizeFrequency("monthly", { month_days_text: "15, 1，15" }),
    { kind: "monthly", month_days: [1, 15] });
  assert.deepEqual(normalizeFrequency("interval", { interval_days: 0 }),
    { kind: "interval", interval_days: 1 });
});

test("月份翻页、周条和日志筛选", () => {
  assert.equal(shiftMonth("2026-10", 1), "2026-11");
  assert.equal(shiftMonth("2026-01", -1), "2025-12");
  assert.deepEqual(weekStripDays({ days: [{ date: TODAY }] }), [{ date: TODAY }]);
  assert.deepEqual(weekStripDays(null), []);
  const logs = [{ habit_id: 1, note: "a" }, { habit_id: 2, note: "b" }];
  assert.deepEqual(logsForHabit(logs, 2), [{ habit_id: 2, note: "b" }]);
  assert.equal(entryFor([{ date: TODAY }], TODAY).date, TODAY);
  assert.equal(entryFor([], TODAY), null);
  assert.equal(percentLabel(null), "—");
  assert.equal(percentLabel(0.6667), "67%");
});
