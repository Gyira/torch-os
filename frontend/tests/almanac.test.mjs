// 今日黄历数据层：真实黄历（lunar 库）、个人运势、月历与锁定/解锁 HTML。
import test from "node:test";
import assert from "node:assert/strict";
import {
  AUSPICIOUS_POOL, INAUSPICIOUS_POOL, FORTUNES,
  almanacCardHtml, almanacDialogHtml, almanacForDay, almanacSheetHtml,
  buildAlmanac, lunarAlmanac, monthGridData,
} from "../almanac.js";

// 用户提供的参考图就是 2026-10-27（星期二）这一天的黄历。
const SAMPLE_DAY = "2026-10-27";

test("the sample day matches the reference almanac exactly", () => {
  const data = lunarAlmanac(SAMPLE_DAY);
  assert.equal(data.day, SAMPLE_DAY);
  assert.equal(data.lunar.dateLabel, "农历九月十八");
  assert.equal(data.lunar.yearGanZhi, "丙午");
  assert.equal(data.lunar.zodiac, "马");
  assert.equal(data.lunar.monthGanZhi, "戊戌");
  assert.equal(data.lunar.dayGanZhi, "甲戌");
  assert.equal(data.lunar.yearLabel, "丙午马年");
  assert.equal(data.lunar.monthLabel, "戊戌月");
  assert.equal(data.lunar.dayLabel, "甲戌日");
  assert.equal(data.solar.weekdayLabel, "星期二");
  assert.equal(data.wuxing, "山头火");
  assert.equal(data.chongSha, "冲(戊辰)龙 煞北");
  assert.deepEqual(data.pengzu, ["甲不开仓财物耗散", "戌不吃犬作怪上床"]);
  assert.deepEqual(data.gods.map((god) => [god.label, god.direction]), [
    ["喜神", "东北"], ["福神", "正北"], ["财神", "东北"],
  ]);
  assert.deepEqual(data.yi, [
    "祭祀", "造车器", "出行", "修造", "上梁",
    "盖屋", "安门", "安床", "造畜稠", "教牛马",
  ]);
  assert.deepEqual(data.ji, [
    "出货财", "开仓", "动土", "破土", "安葬",
    "行丧", "伐木", "开渠", "栽种",
  ]);
  assert.deepEqual(data.jiShen, ["母仓", "守日", "天马"]);
  assert.deepEqual(data.xiongSha, ["月建", "小时", "土府", "白虎", "阴位"]);
});

test("personal reading stays deterministic and keeps its pool shape", () => {
  const labels = new Set(FORTUNES.map(([label]) => label));
  const aus = new Set(AUSPICIOUS_POOL.map(([title]) => title));
  const inaus = new Set(INAUSPICIOUS_POOL.map(([title]) => title));
  const first = almanacForDay(SAMPLE_DAY);
  const second = almanacForDay(SAMPLE_DAY);
  assert.deepEqual(first.personal, second.personal);
  assert.ok(labels.has(first.personal.fortune));
  assert.equal(first.personal.auspicious.length, 2);
  assert.equal(first.personal.inauspicious.length, 2);
  assert.ok(first.personal.auspicious.every(([title, note]) => aus.has(title) && note.length > 0));
  assert.ok(first.personal.inauspicious.every(([title, note]) => inaus.has(title) && note.length > 0));

  for (let offset = 0; offset < 60; offset += 1) {
    const day = new Date(Date.UTC(2026, 9, 5 + offset)).toISOString().slice(0, 10);
    const reading = buildAlmanac(day);
    assert.equal(reading.auspicious.length, 2);
    assert.equal(reading.inauspicious.length, 2);
    assert.equal(new Set(reading.auspicious.map(([title]) => title)).size, 2, "宜 不重复");
    assert.equal(new Set(reading.inauspicious.map(([title]) => title)).size, 2, "忌 不重复");
  }
});

test("month grid is Monday-first, padded, and marks the sample day", () => {
  const grid = monthGridData(2026, 10, SAMPLE_DAY);
  assert.equal(grid.cells.length, 35);
  assert.deepEqual(grid.cells.slice(0, 3), [null, null, null]);
  assert.equal(grid.cells[3].day, "2026-10-01");
  assert.equal(grid.cells[3].solarDay, 1);
  assert.equal(grid.cells[3].lunarLabel, "廿一");
  // 2026-10-10 是农历九月初一：月格上显示月名。
  assert.equal(grid.cells[12].day, "2026-10-10");
  assert.equal(grid.cells[12].lunarLabel, "九月");
  const sample = grid.cells.find((cell) => cell?.day === SAMPLE_DAY);
  assert.ok(sample?.isToday);
  assert.equal(sample.solarDay, 27);

  // 跨年月份也按周一起始补齐整周。
  const december = monthGridData(2026, 12, "2026-12-01");
  assert.equal(december.cells.length % 7, 0);
  assert.equal(december.cells.filter(Boolean).length, 31);
});

test("locked compact card shows the date and sign-in but keeps the reading sealed", () => {
  const data = almanacForDay(SAMPLE_DAY);
  const locked = almanacCardHtml(data, { checkedIn: false, name: "阿泽" });
  assert.ok(locked.includes("签到查看今日运势"));
  assert.ok(locked.includes("丙午年 马 戊戌月 甲戌日"));
  assert.ok(locked.includes("九月十八"));
  assert.ok(locked.includes("今日运势已封存"));
  assert.ok(locked.includes("almanac-compact"));
  // 完整黄历与个人运势仍然封存
  assert.ok(!locked.includes("山头火"));
  assert.ok(!locked.includes("祭祀"));
  assert.ok(!locked.includes("五行"));
  assert.ok(!locked.includes("吉神"));
  assert.ok(!locked.includes(data.personal.fortune));
  assert.ok(!locked.includes(data.personal.auspicious[0][0]));
});

test("unlocked compact card keeps only the brief almanac and fortune", () => {
  const data = almanacForDay(SAMPLE_DAY);
  const html = almanacCardHtml(data, {
    checkedIn: true, name: "阿泽", progress: { done: 3, total: 24 },
  });
  for (const piece of [
    "丙午年", "马", "戊戌月", "甲戌日", "九月十八",
    "今日运势", data.personal.fortune, "✓ 已签到",
    "宜", data.personal.auspicious[0][0], "忌", data.personal.inauspicious[0][0],
    'data-almanac-unlocked="1"', 'role="button"',
  ]) {
    assert.ok(html.includes(piece), `缺少：${piece}`);
  }
  // 小尺寸只保留简要内容，完整黄历必须在详情浮窗里
  for (const hidden of [
    "山头火", "五行", "冲(戊辰)龙 煞北", "吉神", "凶神",
    "今日闭环", "个人运势", "查看详情 →", "almanac-card-foot",
  ]) {
    assert.equal(html.includes(hidden), false, `${hidden} 不应该出现在小卡片里`);
  }

  // 详情浮窗仍然包含完整黄历与个人运势说明。
  const sheet = almanacSheetHtml(data, {
    name: "阿泽", progress: { done: 3, total: 24 }, detailed: true,
  });
  for (const piece of [
    "五行", "山头火", "吉神", "凶神", "今日闭环 3 / 24 项（含日记）", "个人运势",
  ]) {
    assert.ok(sheet.includes(piece), `详情缺少：${piece}`);
  }
});

test("html escapes user-provided text and the dialog carries the date controls", () => {
  const data = almanacForDay(SAMPLE_DAY);
  const html = almanacSheetHtml(data, {
    name: '<img src=x onerror=1>', progress: { done: 3, total: 24 }, detailed: true,
  });
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img"));
  assert.ok(html.includes("今日闭环 3 / 24 项（含日记）"));

  const dialog = almanacDialogHtml(data, {
    name: "阿泽", progress: { done: 1, total: 2 }, viewYear: 2026, viewMonth: 10,
    today: SAMPLE_DAY,
  });
  assert.ok(dialog.includes("data-almanac-year"));
  assert.ok(dialog.includes("data-almanac-month"));
  assert.ok(dialog.includes("data-almanac-nav=\"prev-month\""));
  assert.ok(dialog.includes("data-almanac-nav=\"next-day\""));
  assert.ok(dialog.includes('data-almanac-day="2026-10-27"'));
  assert.ok(dialog.includes("2026年10月"));
  assert.ok(dialog.includes("今天"));
});
