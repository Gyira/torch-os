// 工作台「值班」小组件：卡片摘要、7 天条带和 14 天弹窗模型。
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  DUTY_STRIP_DAYS, dutyCardModel, dutyDialogRows, dutyStateLabel,
  formatMonthDay, normalizeDutyPayload,
} from "../duty-model.js";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function payload() {
  return {
    today: "2026-11-01",
    start: "2026-11-01",
    found: true,
    error: "",
    days: [
      { date: "2026-11-01", weekday: "周日", in_range: true, work: true, rest: false, duty: "G1572/G335", note: "" },
      { date: "2026-11-02", weekday: "周一", in_range: true, work: true, rest: false, duty: "G334-C2735-C2736-G337", note: "" },
      { date: "2026-11-03", weekday: "周二", in_range: true, work: false, rest: true, duty: "", note: "补休" },
      { date: "2026-11-04", weekday: "周三", in_range: true, work: true, rest: false, duty: "G336/G1571", note: "" },
      { date: "2026-11-05", weekday: "周四", in_range: true, work: true, rest: false, duty: "", note: "" },
      { date: "2026-11-06", weekday: "周五", in_range: true, work: false, rest: true, duty: "", note: "" },
      { date: "2026-11-07", weekday: "周六", in_range: true, work: true, rest: false, duty: "G1", note: "" },
      { date: "2026-11-08", weekday: "周日", in_range: true, work: false, rest: true, duty: "", note: "" },
    ],
  };
}

test("duty payload normalizes boolean flags and junk dates", () => {
  const data = normalizeDutyPayload({
    today: "2026-11-01", found: true,
    days: [
      { date: "2026-11-01", weekday: "周日", in_range: true, work: true, rest: false, duty: " G1 / G2 ", note: "  " },
      { date: "bad", duty: "G9" },
      null,
    ],
  });
  assert.equal(data.days.length, 1);
  assert.equal(data.days[0].duty, "G1 / G2");
  assert.equal(data.days[0].note, "");
  assert.equal(data.days[0].work, true);
  assert.equal(dutyStateLabel(data.days[0]), "上班");
  assert.equal(dutyStateLabel({ inRange: false }), "未覆盖");
  assert.equal(formatMonthDay("2026-11-02"), "11-02");
});

test("card summary shows today's duty, next workday and a 7-day strip", () => {
  const model = dutyCardModel(payload());
  assert.equal(model.dateLabel, "11月1日 周日");
  assert.equal(model.state, "上班");
  assert.equal(model.tone, "work");
  assert.equal(model.train, "G1572/G335");
  assert.equal(model.nextText, "下次上班 11-02 · G334-C2735-C2736-G337");
  assert.equal(model.strip.length, DUTY_STRIP_DAYS);
  assert.equal(model.strip[0].isToday, true);
  assert.equal(model.strip[0].day, "01");
  assert.equal(model.strip[0].weekday, "日");
  assert.equal(model.strip[1].work, true);
  assert.equal(model.strip[2].rest, true);
});

test("rest and out-of-range days have clear fallbacks", () => {
  const rest = dutyCardModel({
    today: "2026-11-03", found: true,
    days: [{ date: "2026-11-03", weekday: "周二", in_range: true, work: false, rest: true, duty: "", note: "" }],
  });
  assert.equal(rest.state, "休息");
  assert.equal(rest.tone, "rest");
  assert.equal(rest.trainText, "今天休息");
  assert.equal(rest.nextText, "近期没有上班安排");

  const out = dutyCardModel({
    today: "2026-10-27", found: true,
    days: [{ date: "2026-10-27", weekday: "周二", in_range: false, work: null, rest: null, duty: "", note: "" }],
  });
  assert.equal(out.state, "未覆盖");
  assert.equal(out.tone, "out");
  assert.equal(out.trainText, "排班表未覆盖");

  const empty = dutyCardModel({ found: false, error: "还没有排班表", days: [] });
  assert.equal(empty.state, "未覆盖");
  assert.equal(empty.dateLabel, "正在读取…");
  assert.equal(empty.strip.length, 0);
});

test("dialog rows keep notes and full state for all upcoming days", () => {
  const rows = dutyDialogRows(payload());
  assert.equal(rows.length, 8);
  assert.equal(rows[2].state, "休息");
  assert.equal(rows[2].note, "补休");
  assert.equal(rows[2].monthDay, "11-03");
});

test("workbench duty card, widget and styles are wired together", async () => {
  const [html, workbench, widget, css] = await Promise.all([
    readFile(path.join(frontend, "index.html"), "utf8"),
    readFile(path.join(frontend, "workbench.js"), "utf8"),
    readFile(path.join(frontend, "duty-widget.js"), "utf8"),
    readFile(path.join(frontend, "styles.css"), "utf8"),
  ]);
  assert.match(html, /id="workbench-duty" class="duty-card" data-widget="duty" data-widget-label="值班"/);
  assert.match(workbench, /initDutyWidget/);
  assert.match(widget, /\/api\/love\/schedule\/upcoming\?days=31/);
  assert.match(widget, /dutyCardModel/);
  assert.match(css, /\.duty-card/);
  assert.match(css, /\.duty-strip-day\.is-today/);
  assert.equal(/style\s*=/.test(widget), false, "CSP 不允许内联 style");
});
