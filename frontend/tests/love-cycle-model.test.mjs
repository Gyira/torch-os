import test from "node:test";
import assert from "node:assert/strict";

import {
  calendarDayClasses, calendarMarkers, currentMonth, cycleTrendChart, dayAriaLabel,
  dayLabel, dayNumber, daysInMonth, daysUntilLabel, formatSleep, formatWater,
  monthLabel, monthLeadingBlanks, phaseTone, shiftMonth, shortDay, validMonth,
} from "../love-cycle-model.js";

test("月份与日期工具跨年、闰年和周一起始", () => {
  assert.equal(validMonth("2026-10"), "2026-10");
  assert.equal(validMonth("2026-13"), "");
  assert.equal(shiftMonth("2026-12", 1), "2027-01");
  assert.equal(shiftMonth("2026-01", -1), "2025-12");
  assert.equal(monthLabel("2026-10"), "2026年10月");
  assert.equal(daysInMonth("2028-02"), 29);
  assert.equal(daysInMonth("2026-02"), 28);
  assert.equal(monthLeadingBlanks("2026-10"), 3);
  assert.equal(currentMonth(new Date("2026-10-31T12:00:00Z")), "2026-10");
  assert.equal(dayNumber("2026-10-31"), 31);
  assert.equal(dayLabel("2026-10-31"), "10 月 31 日");
  assert.equal(shortDay("2026-10-31"), "10-31");
});

test("倒计时文案覆盖今天、未来和推迟", () => {
  assert.equal(daysUntilLabel(0), "预计今天");
  assert.equal(daysUntilLabel(6), "还有 6 天");
  assert.equal(daysUntilLabel(-3), "已推迟 3 天");
  assert.equal(daysUntilLabel(null), "—");
  assert.equal(formatSleep(7.5), "7.5 小时");
  assert.equal(formatSleep(null), "—");
  assert.equal(formatWater(1200), "1200 ml");
  assert.equal(formatWater(undefined), "—");
});

test("阶段与日历格子有白名单 class 和文字标记", () => {
  assert.equal(phaseTone("fertile"), "fertile");
  assert.equal(phaseTone("unexpected"), "unknown");
  const day = {
    date: "2026-10-31", is_period: true, is_ongoing: true, is_today: true, has_log: true,
  };
  const classes = calendarDayClasses(day);
  assert.match(classes, /lc-day/);
  assert.match(classes, /is-period/);
  assert.match(classes, /is-ongoing/);
  assert.match(classes, /is-today/);
  assert.match(classes, /has-log/);
  const markers = calendarMarkers(day);
  assert.deepEqual(markers.map((item) => item.label), ["经期·未结束", "记录"]);
  assert.match(dayAriaLabel(day), /经期未结束/);

  const predicted = calendarMarkers({ date: "2026-11-01", is_predicted: true });
  assert.deepEqual(predicted.map((item) => item.label), ["预计"]);
  const ovulation = calendarMarkers({ date: "2026-10-10", is_ovulation: true });
  assert.deepEqual(ovulation.map((item) => item.label), ["排卵"]);
  const fertile = calendarMarkers({ date: "2026-10-09", is_fertile: true });
  assert.deepEqual(fertile.map((item) => item.label), ["易孕"]);
});

test("周期趋势图在数据不足时为空，有数据时画正常带和点", () => {
  assert.equal(cycleTrendChart([]), "");
  assert.equal(cycleTrendChart([{ date: "2026-10-01", length: 28 }]), "");
  const chart = cycleTrendChart([
    { date: "2026-08-01", length: 28 },
    { date: "2026-08-29", length: 30 },
    { date: "2026-09-26", length: 26 },
  ]);
  assert.match(chart, /lc-chart-band/);
  assert.match(chart, /lc-chart-line/);
  assert.match(chart, /lc-chart-dot/);
  assert.match(chart, />28</);
  assert.match(chart, /最近周期长度趋势图/);
  assert.equal((chart.match(/lc-chart-dot/g) || []).length, 3);
  assert.equal(cycleTrendChart([{ date: "bad", length: 28 }, { date: "2026-10-01", value: 3 }]), "");
});
