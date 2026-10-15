// 横幅纯函数：归一化、图片地址、引言/统计渲染与轮换调度。
import test from "node:test";
import assert from "node:assert/strict";
import {
  normalizeBanner, imageSrc, quotesHtml, statsHtml, startRotation, backdropVars,
  ROTATION_MINUTES, safeFont, firstFontName, BANNER_FONT_GROUPS,
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
});

test("backdrop converts darkness into brightness and scrim like apex", () => {
  assert.deepEqual(backdropVars(6, 30), { blur: "6px", bright: "0.79", scrim: "0.4" });
  assert.equal(backdropVars(0, 0).bright, "1");
  assert.equal(backdropVars(99, 100).blur, "20px");
  assert.equal(backdropVars(99, 100).bright, "0.3");
  assert.equal(backdropVars(NaN, NaN).blur, "6px");
});

test("stats respect the left/center picks and the details switch", () => {
  const stats = {
    day: "2026-10-05", journal_streak: 4, today_percent: 62, memories: 98, notes: 12,
    daily_done: 3, daily_total: 5, habits_done: 1, habits_total: 2, open_todos: 2,
    activity_today: 6, activity: [{ day: "2026-10-05", count: 6 }],
  };
  const custom = statsHtml(stats, { leftStat: "memories", centerStat: "percent", details: false });
  assert.ok(custom.includes("记忆") && custom.includes("98"));
  assert.ok(custom.includes("今日完成度") && custom.includes("62"));
  assert.equal(custom.includes("banner-heatmap"), false);
  assert.equal(custom.includes("banner-stats-strip"), false);
  assert.equal(custom.includes("banner-stats-prog-track"), false);

  const filesPick = statsHtml(stats, { leftStat: "files" });
  assert.ok(filesPick.includes("今日文件") && filesPick.includes("6"));
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

test("stats render the three-column layout with heatmap and progress rows", () => {
  const html = statsHtml({
    day: "2026-10-05",
    journal_streak: 4, today_percent: 62, memories: 98, notes: 12,
    daily_done: 3, daily_total: 5, habits_done: 1, habits_total: 2,
    open_todos: 2, activity_today: 6,
    activity: [{ day: "2026-10-04", count: 0 }, { day: "2026-10-05", count: 6 }],
  });
  assert.ok(html.includes("banner-stats-left") && html.includes("banner-stats-center") && html.includes("banner-stats-right"));
  assert.ok(html.includes("连续日记") && html.includes("资料") && html.includes("今日完成度"));
  assert.ok(html.includes("62%"));
  assert.ok(html.includes("3/5") && html.includes("1/2"));
  assert.ok(html.includes("level-4")); // 6 是最大值 → 最深一级
  assert.ok(html.includes("today")); // 今天那格描边
  assert.ok(html.includes("width:60%")); // 每日必做 3/5
  assert.ok(html.includes("width:50%")); // 习惯 1/2
  assert.ok(html.includes("待办还差 2 项"));

  const empty = statsHtml(null);
  assert.ok(empty.includes("—"));
  assert.equal(empty.includes("NaN"), false);
  assert.ok(empty.includes("banner-stats-center")); // 无数据也保持三列骨架
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
