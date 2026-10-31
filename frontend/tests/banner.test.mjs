// 横幅纯函数：归一化、图片地址、引言/统计渲染与轮换调度。
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  normalizeBanner, imageSrc, quotesHtml, statsHtml, startRotation, backdropVars,
  ROTATION_MINUTES, safeFont, firstFontName, BANNER_FONT_GROUPS,
  parseMottos, mottoText, heatDaysForWidth,
} from "../banner.js";

test("normalize fills defaults, trims entries and clamps the overlay", () => {
  const banner = normalizeBanner({
    mottos: ["  第一句  ", "", "第二句"],
    heroes: [" image/a.png "],
    hero_overlay: 2.5,
    hero_focal: " 50% 40% ",
    banner_mode: "stats",
  });
  assert.deepEqual(banner.mottos, ["第一句", "第二句"]);
  assert.deepEqual(banner.heroes, ["image/a.png"]);
  assert.equal(banner.overlay, 0.9); // 上限 0.9
  assert.equal(banner.focal, "50% 40%");
  assert.equal(banner.mode, "stats");

  const empty = normalizeBanner(null);
  assert.deepEqual(empty.heroes, []);
  assert.equal(empty.overlay, 0.45);
  assert.equal(empty.mode, "quote");
  assert.equal(empty.accent, "");

  assert.equal(normalizeBanner({ banner_accent: "#FF8800" }).accent, "#FF8800");
  assert.equal(normalizeBanner({ banner_accent: "red" }).accent, "");
  assert.equal(normalizeBanner({ banner_accent: "#12345" }).accent, "");

  // 统计设置：缺省、钳位与白名单。
  const statsOptions = normalizeBanner({ banner_blur: 99, banner_darkness: -5, banner_left_stat: "memories", banner_center_stat: "percent", banner_details: false });
  assert.equal(statsOptions.blur, 20);
  assert.equal(statsOptions.darkness, 0);
  assert.equal(statsOptions.leftStat, "memories");
  assert.equal(statsOptions.centerStat, "percent");
  assert.equal(statsOptions.details, false);
  assert.equal(normalizeBanner({ banner_left_stat: "hack" }).leftStat, "notes");
  assert.equal(normalizeBanner(null).details, true);
  // 热力格颜色独立于强调色：默认绿、支持自定义、非法值回默认。
  assert.equal(normalizeBanner(null).heatColor, "#2f9e6a");
  assert.equal(normalizeBanner({ banner_heat_color: "#3B82F6" }).heatColor, "#3b82f6");
  assert.equal(normalizeBanner({ banner_heat_color: "red" }).heatColor, "#2f9e6a");
});

test("backdrop converts darkness into brightness and scrim like apex", () => {
  assert.deepEqual(backdropVars(6, 30), { blur: "6px", bright: "0.79", scrim: "0.4" });
  assert.equal(backdropVars(0, 0).bright, "1");
  assert.equal(backdropVars(99, 100).blur, "20px");
  assert.equal(backdropVars(99, 100).bright, "0.3");
  assert.equal(backdropVars(NaN, NaN).blur, "6px");
});

function isoDay(offset) {
  const date = new Date(Date.UTC(2026, 9, 31));
  date.setUTCDate(date.getUTCDate() - offset);
  return date.toISOString().slice(0, 10);
}

function heatmapFixture(level = 0) {
  const cells = Array.from({ length: 28 }, (_, index) => ({
    date: isoDay(27 - index),
    level: index === 27 ? level : 0,
    today: index === 27,
    title: `第 ${index + 1} 天`,
  }));
  const rows = [
    ["life", "人生路"], ["knowledge", "知识库"], ["self", "自我档案"],
    ["todo_created", "任务创建"], ["todo_done", "任务完成"], ["habits", "习惯"],
    ["total", "总热度"],
  ].map(([key, label]) => ({ key, label, cells }));
  return { days: 28, start: "2026-10-04", today: "2026-10-31", rows };
}

test("stats respect the left/center picks and the details switch", () => {
  const stats = {
    day: "2026-10-31", journal_streak: 4, today_percent: 62, memories: 98, notes: 12,
    daily_done: 3, daily_total: 5, habits_done: 1, habits_total: 2, open_todos: 2,
    activity_today: 6, heat_score: 0.62, heat_level: 3, heatmap: heatmapFixture(4),
  };
  const custom = statsHtml(stats, { leftStat: "memories", centerStat: "percent", details: false });
  assert.ok(custom.includes("记忆") && custom.includes("98"));
  assert.ok(custom.includes("今日完成度") && custom.includes("62"));
  assert.equal(custom.includes("banner-heatmap"), false);
  assert.equal(custom.includes("banner-stats-strip"), false);
  assert.equal(custom.includes("banner-stats-prog-track"), false);

  const filesPick = statsHtml(stats, { leftStat: "files" });
  assert.ok(filesPick.includes("今日新增") && filesPick.includes("6"));
});

test("font keeps a real family stack and drops everything else", () => {
  assert.equal(normalizeBanner({}).font, "");
  assert.equal(normalizeBanner({ banner_font: "   " }).font, "");
  assert.equal(normalizeBanner({ banner_font: '"LXGW WenKai","霞鹜文楷",serif' }).font,
    '"LXGW WenKai","霞鹜文楷",serif');
  assert.equal(normalizeBanner({ banner_font: "x".repeat(121) }).font, ""); // 超长当没填
  // 能拼出别的 CSS 声明的字符（分号、花括号、反斜杠）一律丢掉
  assert.equal(normalizeBanner({ banner_font: "serif;} body{color:red" }).font, "");
  assert.equal(safeFont("Georgia,serif"), "Georgia,serif");
  assert.equal(safeFont(null), "");
  assert.equal(firstFontName('"Kaiti SC","KaiTi",serif'), "Kaiti SC");
  assert.equal(firstFontName("Georgia,serif"), "Georgia");

  // 候选表里每一条都必须能通过校验，否则用户选了却被悄悄丢回默认字体。
  for (const group of BANNER_FONT_GROUPS) {
    for (const font of group.fonts) {
      assert.equal(safeFont(font.value), font.value, `${font.label} 没通过字体校验`);
    }
  }
});

test("imageSrc routes vault paths through the sandboxed endpoint", () => {
  assert.equal(imageSrc("image/a.png", 2), "/api/identity/hero?i=2");
  assert.equal(imageSrc("https://cdn.example.com/x.jpg", 0), "https://cdn.example.com/x.jpg");
  assert.equal(imageSrc("", 0), "");
});

test("quotes escape html and mark the first line as primary", () => {
  const html = quotesHtml(["<img src=x>第一句", "第二句"]);
  assert.equal(html.includes("<img"), false);
  assert.ok(html.includes("&lt;img"));
  assert.ok(html.indexOf('class="workbench-motto-line primary"') < html.indexOf("第二句"));
});

test("a motto keeps explicit line breaks; blank lines separate mottos", () => {
  const text = "第一行\n第二行\n\n第三句";
  assert.deepEqual(parseMottos(text), ["第一行\n第二行", "第三句"]);
  assert.deepEqual(parseMottos("  单句  "), ["单句"]);
  assert.equal(mottoText(["第一行\n第二行", "第三句"]), "第一行\n第二行\n\n第三句");
  assert.equal(mottoText([]), "");
  const html = quotesHtml(parseMottos(text));
  assert.equal((html.match(/workbench-motto-line/g) || []).length, 2);
  assert.ok(html.includes("第一行\n第二行")); // CSS pre-line 负责显示换行
  assert.equal(normalizeBanner({ mottos: ["甲\n乙"] }).mottos[0], "甲\n乙");
});

test("stats render the seven-row heatmap, progress rows and today's total", () => {
  const html = statsHtml({
    day: "2026-10-31",
    journal_streak: 4, today_percent: 62, memories: 98, notes: 12,
    daily_done: 3, daily_total: 5, habits_done: 1, habits_total: 2,
    open_todos: 2, activity_today: 6, heat_score: 0.75, heat_level: 3,
    heatmap: heatmapFixture(4),
  });
  assert.ok(html.includes("banner-stats-left") && html.includes("banner-stats-center") && html.includes("banner-stats-right"));
  assert.ok(html.includes("资料") && html.includes("今日总热度"));
  assert.equal(html.includes("连续日记"), false); // 用户要求删掉中间“✍️ 连续日记”
  assert.ok(html.includes("75%"));
  assert.ok(html.includes("3/5") && html.includes("1/2"));
  assert.ok(html.includes("level-4")); // 今天那格是 100%
  assert.ok(html.includes("today")); // 今天那格描边
  assert.ok(html.includes("width:75%")); // 今日总热度
  assert.ok(html.includes("width:60%")); // 每日必做 3/5
  assert.ok(html.includes("width:50%")); // 习惯 1/2
  assert.ok(html.includes("待办还差 2 项"));
  assert.ok(html.includes("今天新增 6 篇"));
  assert.equal((html.match(/banner-heatmap-row/g) || []).length, 7);
  assert.equal((html.match(/banner-heat-cell/g) || []).length, 7 * 28);
  for (const label of ["人生路", "知识库", "自我档案", "任务创建", "任务完成", "习惯", "总热度"]) {
    assert.ok(html.includes(`banner-heatmap-label">${label}`));
  }

  const empty = statsHtml(null);
  assert.ok(empty.includes("—"));
  assert.equal(empty.includes("NaN"), false);
  assert.ok(empty.includes("banner-stats-center")); // 无数据也保持三列骨架
  assert.equal((empty.match(/banner-heatmap-row/g) || []).length, 7); // 读取中也有七行占位
});

test("heatmap chooses square-cell days that fit the banner width", () => {
  // 传入的是横幅实际宽度：宽屏最多 56 天，窄屏递减，但不会低于 14 天。
  assert.equal(heatDaysForWidth(2480), 56);
  assert.equal(heatDaysForWidth(1694), 56);
  assert.equal(heatDaysForWidth(1544), 52);
  assert.equal(heatDaysForWidth(1360), 47);
  assert.equal(heatDaysForWidth(1120), 39);
  assert.equal(heatDaysForWidth(712), 30);
  assert.equal(heatDaysForWidth(344), 18);
  assert.equal(heatDaysForWidth(280), 15);
  assert.equal(heatDaysForWidth(0), 15);
  // 天数随宽度单调不减，且永远在 14–56 之间（后端最多提供 56 天）。
  let previous = 0;
  for (const width of [280, 344, 500, 712, 944, 1120, 1360, 1544, 1694, 2480, 4000]) {
    const days = heatDaysForWidth(width);
    assert.ok(days >= 14 && days <= 56, `${width} -> ${days}`);
    assert.ok(days >= previous, `${width} 的天数不应比更窄的少`);
    previous = days;
  }

  const stats = { heatmap: heatmapFixture(4), heat_score: 0.5, activity_today: 2 };
  const html = statsHtml(stats, { heatDays: 14 });
  assert.equal((html.match(/banner-heat-cell/g) || []).length, 7 * 14);
  assert.ok(html.includes("近 14 天 · 每格一天"));
  assert.equal(html.includes("banner-stats-center-head"), false);
});

test("rotation cycles through images on the injected schedule and skips single images", () => {
  const applied = [];
  const timers = [];
  let pending = null;
  const scheduled = startRotation(
    { heroes: ["a.png", "b.png", "c.png"] }, (index) => applied.push(index),
    (fn, ms) => { timers.push(ms); pending = fn; });
  assert.equal(scheduled, true);
  assert.deepEqual(applied, []); // 首图已在外层应用；第一次轮换要等一个区间
  pending();
  pending();
  pending();
  assert.deepEqual(applied, [1, 2, 0]); // 到第三张后从头循环
  assert.ok(timers.every((ms) => ms === ROTATION_MINUTES * 60 * 1000));

  assert.equal(startRotation({ heroes: ["only.png"] }, () => {}), false);
  assert.equal(startRotation({ heroes: [] }, () => {}), false);
  assert.equal(startRotation(null, () => {}), false);
});

test("the editor exposes a file picker and uploads into the database .torch-os folder", async () => {
  const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
  const editor = await readFile(new URL("../banner-editor.js", import.meta.url), "utf8");
  assert.match(html, /id="banner-hero-file"[^>]*type="file"/);
  assert.match(html, /accept="image\/png,image\/jpeg,image\/webp,image\/gif"/);
  assert.match(html, /id="banner-hero-choose"/);
  assert.match(html, /id="banner-hero-url"/);
  assert.match(html, /\.torch-os\/横幅背景/);
  assert.doesNotMatch(html, /id="banner-heroes"/); // 不再要求用户手填一行一个路径
  assert.match(editor, /\/api\/identity\/banner\/image/);
  assert.match(editor, /data-banner-hero-remove/);
  assert.match(editor, /FormData/);
});

test("the banner exposes a one-click mode switch and a review board", async () => {
  const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
  assert.match(html, /id="workbench-banner-mode"/);
  assert.match(html, /id="workbench-banner-review"/);
  assert.match(html, /data-mode="quote"/);
  assert.match(html, /icon-mode-chart/);
  assert.match(html, /icon-mode-image/);
  assert.match(html, /id="heatmap-dialog"/);
  for (const view of ["day", "week", "month", "year"]) {
    assert.match(html, new RegExp(`data-heatmap-view="${view}"`));
  }
  assert.match(html, /id="heatmap-content"/);
  assert.match(html, /id="heatmap-foot"/);
  assert.match(html, /id="banner-heat-color"[^>]*type="color"/);
  assert.match(html, /data-heat-preset="#3b82f6"/);
  assert.match(html, /id="banner-heat-presets"/);
});
