import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  average, barChart, clockMinutes, durationBetween, extractMetric, formatDuration,
  formatNumber, formatSigned, heatmap, lastDates, lineChart, macroProgress,
  missingFor, pointsFor, progressMarkup, ringMarkup, scoreTone, seriesStats, shiftDay,
  sparkline, stddev, todayText, weekdayLabel,
} from "../health-model.js";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const RECORD = {
  date: "2026-10-31",
  sections: {
    sleep: { duration_min: 450, quality: 4 },
    body: { weight_kg: 73.4, body_fat_pct: 18.2 },
    activity: { steps: 8200 },
    mind: { mood: 4 },
    nutrition: { water_ml: 1800 },
  },
  nutrition: { calories: 1900, protein_g: 120 },
  training_totals: { minutes: 45, volume_kg: 4200, sessions: 1 },
  score: { total: 82, breakdown: { sleep: 88 } },
  custom: { 腰臀比: 0.86 },
};

test("number, duration and delta formatting stay readable", () => {
  assert.equal(formatNumber(73.44, 1), "73.4");
  assert.equal(formatNumber(73, 0, "kg"), "73 kg");
  assert.equal(formatNumber(null, 1, "kg"), "—");
  assert.equal(formatSigned(2.25, 1, "kg"), "+2.3 kg");
  assert.equal(formatSigned(-1, 0), "-1");
  assert.equal(formatDuration(450), "7 小时 30 分");
  assert.equal(formatDuration(420), "7 小时");
  assert.equal(formatDuration(45), "45 分钟");
  assert.equal(formatDuration(0), "—");
  assert.equal(durationBetween("23:40", "07:10"), 450);
  assert.equal(durationBetween("01:00", "08:00"), 420);
  assert.equal(durationBetween("", "08:00"), null);
});

test("dates shift across month boundaries and keep the leading zeros", () => {
  assert.equal(shiftDay("2026-03-01", -1), "2026-02-28");
  assert.equal(shiftDay("2026-12-31", 1), "2027-01-01");
  assert.deepEqual(lastDates(3, "2026-10-31"), ["2026-10-29", "2026-10-30", "2026-10-31"]);
  assert.equal(weekdayLabel("2026-10-31"), "六");
  assert.match(todayText(), /^\d{4}-\d{2}-\d{2}$/);
});

test("metric extraction understands sections, totals, score and custom values", () => {
  assert.equal(extractMetric(RECORD, "body.weight_kg"), 73.4);
  assert.equal(extractMetric(RECORD, "sleep.quality"), 4);
  assert.equal(extractMetric(RECORD, "nutrition.calories"), 1900);
  assert.equal(extractMetric(RECORD, "training.volume_kg"), 4200);
  assert.equal(extractMetric(RECORD, "score.total"), 82);
  assert.equal(extractMetric(RECORD, "custom.腰臀比"), 0.86);
  assert.equal(extractMetric(RECORD, "body.muscle_kg"), null);
  assert.equal(extractMetric(null, "body.weight_kg"), null);
  assert.deepEqual(pointsFor([RECORD], "body.weight_kg"),
    [{ date: "2026-10-31", value: 73.4 }]);
});

test("series stats and slope describe direction", () => {
  const stats = seriesStats([
    { date: "2026-10-29", value: 74 }, { date: "2026-10-30", value: 73.5 },
    { date: "2026-10-31", value: 73 },
  ]);
  assert.equal(stats.count, 3);
  assert.equal(stats.latest, 73);
  assert.equal(stats.delta, -1);
  assert.ok(stats.slope < 0);
  assert.deepEqual(seriesStats([]), {
    count: 0, latest: null, avg: null, min: null, max: null, delta: null, slope: null,
  });
  assert.equal(average([1, 2, 3]), 2);
  assert.equal(stddev([1, 1, 1]), 0);
  assert.equal(clockMinutes("23:30"), 1410);
  assert.equal(clockMinutes("01:30"), 1530, "凌晨时间归到 24 点之后");
});

test("tone thresholds match the ring thresholds", () => {
  assert.equal(scoreTone(86), "great");
  assert.equal(scoreTone(75), "good");
  assert.equal(scoreTone(60), "fair");
  assert.equal(scoreTone(30), "low");
  assert.equal(scoreTone(null), "none");
});

test("sparkline and line chart escape labels and return svg", () => {
  const spark = sparkline([{ value: 1 }, { value: 2 }, { value: 3 }]);
  assert.match(spark, /<svg/);
  assert.match(spark, /<path/);
  assert.match(sparkline([{ value: 1 }]), /health-spark-empty/);

  const chart = lineChart([
    { label: "体重<kg>", points: [{ date: "2026-10-30", value: 74 }, { date: "2026-10-31", value: 73 }], unit: "kg" },
    { label: "体脂", points: [{ date: "2026-10-31", value: 18 }], unit: "%" },
  ]);
  assert.match(chart.svg, /<svg/);
  assert.match(chart.svg, /体重&lt;kg&gt;/, "图例必须转义");
  assert.equal(chart.normalized, true, "单位不同时归一化");
  const single = lineChart([{ label: "体重", points: [{ date: "2026-10-31", value: 73 }], unit: "kg" }]);
  assert.equal(single.normalized, false);
  assert.match(lineChart([]).svg, /health-chart-empty/);
});

test("bar chart and heatmap render bounded marks", () => {
  const bars = barChart([{ date: "2026-10-30", value: 4000 }, { date: "2026-10-31", value: 8200 }], { unit: "步" });
  assert.equal((bars.match(/<rect/g) || []).length, 2);
  assert.match(bars, /8200/);
  const map = heatmap([{ date: "2026-10-31", value: 5 }], { days: 7, end: "2026-10-31", max: 5 });
  assert.equal((map.match(/health-heat-cell/g) || []).length, 7);
  assert.match(map, /level-4/);
});

test("macro progress and missing hints", () => {
  const macros = macroProgress({ protein_g: 60, carbs_g: 100, fat_g: 40 }, { protein_g: 120, carbs_g: 200, fat_g: 80 });
  assert.deepEqual(macros.map((item) => item.percent), [50, 50, 50]);
  assert.deepEqual(missingFor({ sections: { body: { weight_kg: 73 } } }).map((item) => item.key),
    ["sleep", "nutrition", "activity", "mind"]);
  assert.deepEqual(missingFor(RECORD), []);
});

test("score ring and progress bars use attributes, never inline styles", () => {
  const ring = ringMarkup(82, { size: 96 });
  assert.match(ring, /health-ring good/);
  assert.match(ring, /stroke-dasharray="82 100"/);
  assert.match(ring, />82</);
  assert.match(ringMarkup(null), /health-ring none/);
  assert.match(ringMarkup(null), />—</);
  const progress = progressMarkup(48.6, { className: "health-macro-progress protein", label: "蛋白" });
  assert.match(progress, /<progress/);
  assert.match(progress, /value="48.6"/);
  assert.equal(/style=/.test(progress), false);
});

test("health pages keep the workbench CSP: no inline styles or <style> blocks", async () => {
  for (const name of ["health.js", "health-model.js"]) {
    const source = await readFile(path.join(FRONTEND, name), "utf8");
    assert.equal(/style\s*=/.test(source), false, `${name} 不应写内联 style（CSP style-src 'self'）`);
    assert.equal(/<style\b/.test(source), false, `${name} 不应包含 <style> 块`);
  }
});
