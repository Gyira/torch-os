// 纪念日数据层：时长计算、阳历/农历日期、每年提醒与配置归一化。
import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_BACKGROUND, anniversaryMatch, formatAnniversaryCard, formatDate,
  formatElapsed, formatLunarStartDate, lunarAnniversaryThisYear, lunarDayOptions,
  lunarMonthOptions, lunarPartsFromSolar, normalizeAnniversary, normalizeBackground,
  normalizeStartDate, oneYearAgoIso, parseAnniversaryDate, solarFromLunar,
} from "../anniversary-model.js";

const START = new Date(2025, 9, 19);
const NOW = new Date(2026, 9, 27, 19, 32);

test("elapsed time matches the reference card: 2025-10-19 → 1年8天", () => {
  assert.equal(formatElapsed(START, NOW, "ymd"), "1年8天");
  assert.equal(formatElapsed(START, NOW, "days"), "373天");
  assert.equal(formatElapsed(new Date(2025, 9, 19, 0, 0), NOW, "hours"), "373天 19小时");
  assert.equal(formatElapsed(new Date(2025, 9, 27), new Date(2026, 9, 27), "ymd"), "1年0天");
  assert.equal(formatElapsed(new Date(2026, 8, 27), new Date(2026, 9, 27), "ymd"), "1个月0天");
  assert.equal(formatElapsed(new Date(2026, 9, 20), new Date(2026, 9, 27), "ymd"), "7天");
  assert.equal(formatElapsed(new Date(2026, 9, 28), new Date(2026, 9, 27), "ymd"), "尚未到来");
  assert.equal(formatElapsed(null, NOW, "ymd"), "--");
});

test("dates parse strictly and keep an optional time", () => {
  assert.equal(oneYearAgoIso(new Date(2026, 9, 27)), "2025-10-27");
  assert.equal(normalizeStartDate("2025-10-19"), "2025-10-19");
  assert.equal(normalizeStartDate("2025-10-19 14:30"), "2025-10-19T14:30");
  assert.equal(normalizeStartDate("2025-10-19T14:30"), "2025-10-19T14:30");
  assert.equal(normalizeStartDate("2025-02-30"), "");
  assert.equal(normalizeStartDate("2025-13-01"), "");
  assert.equal(normalizeStartDate("not a date"), "");
  const parsed = parseAnniversaryDate("2025-10-19T14:30");
  assert.equal(formatDate(parsed), "2025-10-19");
  assert.equal(parsed.getHours(), 14);
  assert.equal(parsed.getMinutes(), 30);
});

test("lunar conversion, anniversary date and month/day options", () => {
  const parts = lunarPartsFromSolar(START);
  assert.equal(parts.year, 2025);
  assert.equal(parts.month, 8);
  assert.equal(parts.day, 28);
  assert.equal(formatLunarStartDate(START), "农历二〇二五年八月廿八");
  assert.equal(formatDate(lunarAnniversaryThisYear(START, NOW)), "2026-10-08");
  assert.deepEqual(solarFromLunar(2025, 8, 28), {
    date: "2025-10-19", year: 2025, month: 10, day: 19,
  });
  assert.equal(solarFromLunar(2025, 8, 30), null); // 2025 年八月只有 29 天

  const months = lunarMonthOptions(2025);
  const leapJune = months.find((month) => month.month === -6);
  assert.ok(leapJune, "2025 年应包含闰六月");
  assert.equal(leapJune.label, "闰六月");
  assert.equal(leapJune.dayCount, 29);
  assert.equal(lunarDayOptions(29).length, 29);
  assert.equal(lunarDayOptions(99).length, 30); // 农历最多 30 天
  assert.deepEqual(lunarMonthOptions(1800), []);
});

test("solar and lunar annual matches agree with the reference rules", () => {
  const solar = { startDate: "2025-10-27", calendar: "solar", annualReminder: true };
  assert.equal(anniversaryMatch(solar, new Date(2026, 9, 27)).years, 1);
  assert.equal(anniversaryMatch(solar, new Date(2026, 9, 26)), null);
  assert.equal(anniversaryMatch({ ...solar, annualReminder: false }, new Date(2026, 9, 27)), null);

  const lunar = { startDate: "2025-10-19", calendar: "lunar", annualReminder: true };
  assert.equal(anniversaryMatch(lunar, new Date(2026, 9, 8)).years, 1);
  assert.equal(anniversaryMatch(lunar, new Date(2026, 9, 27)), null);

  // 2/29 在平年落到 3/1。
  const leap = { startDate: "2024-02-29", calendar: "solar", annualReminder: true };
  assert.equal(anniversaryMatch(leap, new Date(2025, 2, 1)).years, 1);
  // 起始日期在未来时不提醒。
  assert.equal(anniversaryMatch(
    { startDate: "2027-10-27", calendar: "solar", annualReminder: true },
    new Date(2026, 9, 27),
  ), null);
});

test("config normalization fills defaults and rejects unsafe backgrounds", () => {
  const defaults = normalizeAnniversary(null, new Date(2026, 9, 27));
  assert.deepEqual(defaults, {
    name: "纪念日",
    calendar: "solar",
    startDate: "2025-10-27",
    precision: "ymd",
    annualReminder: false,
    background: DEFAULT_BACKGROUND,
  });

  assert.equal(normalizeAnniversary({ startDate: "2025-02-30" }).startDate, "");
  assert.equal(normalizeAnniversary({ precision: "weird" }).precision, "ymd");
  assert.equal(normalizeAnniversary({ calendar: "weird" }).calendar, "solar");
  assert.equal(normalizeAnniversary({ name: "   " }).name, "纪念日");
  assert.equal(normalizeAnniversary({ background: "" }).background, "");
  assert.equal(normalizeAnniversary({ background: "javascript:alert(1)" }).background, "");
  assert.equal(normalizeAnniversary({ background: "http://example.com/a.jpg" }).background, "");
  assert.equal(normalizeAnniversary({ background: "data:text/html;base64,AAAA" }).background, "");
  assert.equal(normalizeBackground("https://example.com/a.jpg"), "https://example.com/a.jpg");
  assert.equal(normalizeBackground("data:image/png;base64,AAAA"), "data:image/png;base64,AAAA");
  assert.equal(normalizeBackground("/assets/bg.jpg"), "/assets/bg.jpg");
  assert.equal(normalizeBackground('/assets/x");color:red'), "");
  assert.equal(normalizeBackground('https://example.com/a"b.jpg'), "");
});

test("card view carries title, elapsed value, date line and reminder", () => {
  const config = normalizeAnniversary({
    name: "纪念日", startDate: "2025-10-19", calendar: "solar",
    precision: "ymd", annualReminder: false, background: "",
  }, NOW);
  const view = formatAnniversaryCard(config, NOW);
  assert.equal(view.title, "纪念日");
  assert.equal(view.value, "1年8天");
  assert.equal(view.dateText, "2025-10-19");
  assert.equal(view.reminder, "");

  const reminder = formatAnniversaryCard({
    ...config, startDate: "2025-10-27", annualReminder: true,
  }, NOW);
  assert.equal(reminder.reminder, "今天 · 第 1 周年");

  const lunar = formatAnniversaryCard({
    ...config, calendar: "lunar",
  }, NOW);
  assert.ok(lunar.dateText.includes("农历二〇二五年八月廿八"));
  assert.ok(lunar.dateText.includes("2025-10-19"));

  const empty = formatAnniversaryCard({ ...config, startDate: "" }, NOW);
  assert.equal(empty.value, "--");
  assert.equal(empty.dateText, "未设置日期");
});
