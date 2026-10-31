// 数据看板纯渲染模型：日/周/月/年结构、数值格式、转义与汇总。
import test from "node:test";
import assert from "node:assert/strict";
import {
  heatmapBoardBody, heatPercent, formatHeatValue, heatInkForColor, ROW_ORDER,
} from "../heatmap-board-model.js";

function fixtureDays(count = 31) {
  return Array.from({ length: count }, (_, index) => {
    const day = String(index + 1).padStart(2, "0");
    const future = index > count - 6;
    return {
      date: `2026-10-${day}`,
      weekday: ((index + 3) % 7) + 1,
      future,
      rows: {
        life: { count: index % 4, score: (index % 4) / 4, level: Math.min(4, index % 4) },
        knowledge: { count: 0, score: 0, level: 0 },
        self: { count: 0, score: 0, level: 0 },
        todo_created: { count: 0, score: 0, level: 0 },
        todo_done: { count: 0, score: 0, level: 0 },
        habits: { scheduled: 2, done: index % 3, skipped: 0, missed: 0, score: null, level: 0 },
        total: { score: (index % 5) / 4, level: Math.min(4, index % 5) },
      },
    };
  });
}

function fixture(view = "day") {
  const rows = [...ROW_ORDER, "total"].map((key) => ({
    key, label: { life: "人生路", knowledge: "知识库", self: "自我档案",
      todo_created: "任务创建", todo_done: "任务完成", habits: "习惯完成", total: "总热度" }[key],
    unit: ["life", "knowledge", "self"].includes(key) ? "篇" :
      ["todo_created", "todo_done"].includes(key) ? "项" : "",
  }));
  const days = fixtureDays();
  return {
    view,
    anchor: "2026-10-31",
    today: "2026-10-31",
    range: { from: "2026-10-01", to: "2026-10-31" },
    period: { label: view === "year" ? "2026年" : "2026年10月", kind: view },
    rows,
    days,
    detail: {
      date: "2026-10-31",
      rows: {
        life: {
          count: 3, score: 0.75, level: 3,
          documents: ["00_人生路/01_日记/2026-10-31.md", "00_人生路/<b>危险.md"],
        },
        knowledge: { count: 0, score: 0, level: 0, documents: [] },
        self: { count: 0, score: 0, level: 0, documents: [] },
        todo_created: { count: 1, score: 0.25, level: 1, documents: ["02_生活运营/00_待办事项/新建.md"] },
        todo_done: { count: 0, score: 0, level: 0, documents: [] },
        habits: {
          scheduled: 2, done: 1, skipped: 1, missed: 0, score: 1, level: 4,
          done_titles: ["阅读"], skipped_titles: ["跑步"], missed_titles: [],
        },
        total: { score: 0.6, level: 3 },
      },
    },
    summary: {
      days: 26,
      active_days: 12,
      rows: {
        total: { score: 0.42, level: 2, active_days: 12, best: { date: "2026-10-31", level: 3, score: 0.6 } },
        life: { count: 9, active_days: 6, score: 0.22, level: 1 },
        habits: { scheduled: 20, done: 12, skipped: 2, active_days: 10, score: 0.6, level: 2 },
      },
    },
    storage: { status: "written", path: ".obsidian/torch-os-heatmap/daily/2026/2026-10-31.json" },
  };
}

test("day view renders the month calendar and a detail panel with docs and habits", () => {
  const html = heatmapBoardBody(fixture("day"));
  assert.match(html, /heatmap-body is-day/);
  assert.match(html, /heat-calendar/);
  assert.equal((html.match(/data-heatmap-day=/g) || []).length, 26); // 26 个非未来日期
  assert.match(html, /is-selected/);
  assert.match(html, /heat-detail-title/);
  assert.match(html, /heat-total-badge[\s\S]*?60%/);
  assert.match(html, /00_人生路\/01_日记\/2026-10-31.md/); // title 里保留完整路径
  assert.match(html, /2026-10-31\.md/);
  assert.ok(!html.includes("<b>危险")); // 文档名转义
  assert.ok(html.includes("&lt;b&gt;危险.md"));
  assert.match(html, /heat-habit-group is-done/);
  assert.match(html, /阅读/);
  assert.match(html, /主动休息/);
  assert.match(html, /跑步/);
});

test("week view lays out 7 days x 7 rows and a summary", () => {
  const data = fixture("week");
  const dates = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29",
    "2026-10-30", "2026-10-31", "2026-11-01"];
  const template = fixtureDays(31)[25];
  data.days = dates.map((date, index) => ({
    ...template, date, weekday: index + 1, future: index === 6,
  }));
  data.range = { from: "2026-10-26", to: "2026-11-01" };
  const html = heatmapBoardBody(data);
  assert.match(html, /heat-week-grid/);
  assert.equal((html.match(/heat-week-dayhead/g) || []).length, 7);
  assert.equal((html.match(/heat-week-row/g) || []).length, 7); // 6 行 + 总热度
  assert.match(html, /heat-summary/);
  assert.match(html, /2026年10月 · 热度汇总/);
});

test("month view shows the month calendar and the row summary", () => {
  const html = heatmapBoardBody(fixture("month"));
  assert.match(html, /heatmap-body is-month/);
  assert.match(html, /heat-summary-grid/);
  assert.match(html, /活跃 6 天/);
});

test("year view renders twelve month cards with mini heat grids", () => {
  const html = heatmapBoardBody(fixture("year"));
  assert.match(html, /heat-year-grid/);
  assert.equal((html.match(/heat-month-card/g) || []).length, 12);
  assert.match(html, /data-heatmap-month="2026-10"/);
  assert.match(html, /heat-mini-cell/);
  assert.match(html, /十二个月的热度/);
});

test("value formatting follows the seven-row rules", () => {
  assert.equal(heatPercent(0.625), 63);
  assert.equal(heatPercent(null), 0);
  assert.equal(formatHeatValue("life", { count: 3 }, fixture()), "3 篇");
  assert.equal(formatHeatValue("todo_created", { count: 2 }, fixture()), "2 项");
  assert.equal(formatHeatValue("habits", { scheduled: 3, skipped: 1, done: 1 }, fixture()), "1/2");
  assert.equal(formatHeatValue("habits", { scheduled: 0 }, fixture()), "—");
  assert.equal(formatHeatValue("total", { score: 0.5 }, fixture()), "50%");
});

test("custom heat colors keep readable text on the darkest cells", () => {
  assert.equal(heatInkForColor("#2f9e6a"), "#ffffff");
  assert.equal(heatInkForColor("#f5d76e"), "#151517");
  assert.equal(heatInkForColor("not-a-color"), "#ffffff");
});

test("empty board data renders nothing instead of throwing", () => {
  assert.equal(heatmapBoardBody(null), "");
  assert.equal(heatmapBoardBody({ view: "day" }).includes("heat-calendar"), true);
});
