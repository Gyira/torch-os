import test from "node:test";
import assert from "node:assert/strict";

import { pendingItemMarkup, pendingListMarkup, pendingStripMarkup } from "../pending-markup.js";

// 待确认中枢的纯渲染：工作台那行数字、弹层里的一张卡片、空状态。
// 只测纯函数，不发请求、不碰 DOM。

const SUMMARY = { total: 14, draft: 13, fact: 1, triage: 0 };
const DRAFT = {
  kind: "draft", id: "7", title: "今日复盘草稿", preview: "完成 2 件 · 逾期 1 件",
  lines: ["习惯 完成 3 · 跳过 0 · 未记 2"], source: "2026-10-27 · 定时草稿",
  created: "2026-10-27T20:30:00+08:00", actions: ["read", "dismiss"],
};

test("no pending work renders no strip at all", () => {
  assert.equal(pendingStripMarkup({ total: 0, draft: 0, fact: 0, triage: 0 }), "");
  assert.equal(pendingStripMarkup(undefined), "");
});

test("the strip shows the total and only the kinds that have work", () => {
  const html = pendingStripMarkup(SUMMARY);
  assert.match(html, /data-pending-open/);
  assert.match(html, /pending-strip-count">14</);
  assert.match(html, /草稿 13 · 事实 1/);
  assert.equal(html.includes("分流 0"), false, "0 条的类型不该占位");
});

test("a draft card offers exactly read and dismiss", () => {
  const html = pendingItemMarkup(DRAFT);
  assert.match(html, /data-pending-kind="draft"/);
  assert.match(html, /data-pending-action="read"/);
  assert.match(html, /data-pending-action="dismiss"/);
  assert.match(html, /今日复盘草稿/);
  assert.match(html, /完成 2 件/);
});

test("only the destructive actions are styled as text buttons", () => {
  const html = pendingItemMarkup({ ...DRAFT, actions: ["accept", "reject"] });
  assert.match(html, /class="button secondary" data-pending-action="accept"/);
  assert.match(html, /class="text-button" data-pending-action="reject"/);
});

test("item text is escaped, never injected as markup", () => {
  const html = pendingItemMarkup({
    ...DRAFT, title: "<img src=x onerror=alert(1)>", preview: "<script>bad()</script>",
    lines: ["<b>粗体</b>"], source: "<i>来源</i>",
  });
  assert.equal(html.includes("<img"), false);
  assert.equal(html.includes("<script>"), false);
  assert.equal(html.includes("<b>"), false);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test("empty rooms explain themselves instead of showing a blank list", () => {
  const html = pendingListMarkup([], "fact");
  assert.match(html, /没有待确认的内容/);
  assert.match(html, /采纳才会写进身份卡或记忆与自传/);
  assert.equal(html.includes("pending-item"), false);
});

test("every room explains what it is when empty", () => {
  for (const kind of ["draft", "fact", "triage", "love"]) {
    const html = pendingListMarkup([], kind);
    assert.match(html, /没有待确认的内容/, kind);
    assert.ok(html.length > 60, kind);
  }
});

test("listed items keep their order", () => {
  const second = { ...DRAFT, id: "8", title: "今日规划草稿" };
  const html = pendingListMarkup([DRAFT, second], "draft");
  assert.ok(html.indexOf("今日复盘草稿") < html.indexOf("今日规划草稿"));
});

test("triage cards carry the suggest engine's chapter as the default choice", () => {
  const TRIAGE = {
    kind: "triage", id: "03_知识库/00_收件箱/待分类.md", title: "待分类的一条",
    preview: "随手记下的东西", lines: ["建议：生活心得（可参考）", "出现“心情”，更像生活心得。"],
    source: "03_知识库/00_收件箱", created: "2026-10-27", actions: ["move"],
    suggest: { chapter: "insight_life", title: "生活心得", confidence: "medium", reason: "出现“心情”" },
  };
  const chapters = [
    { key: "inbox", title: "收件箱", pillar: "知识库" },
    { key: "insight_life", title: "生活心得", pillar: "人生路" },
  ];
  const html = pendingItemMarkup(TRIAGE, { chapters });

  assert.match(html, /data-pending-chapter/);
  assert.match(html, /value="insight_life" selected/);
  assert.match(html, /data-pending-action="move"/);
  assert.match(html, /人生路 \/ 生活心得/);
});

test("love drafts offer write and discard, and discard is the destructive one", () => {
  const html = pendingItemMarkup({
    kind: "love", id: "d1", title: "2026-10-20 · 每日记忆",
    preview: "今天一起去看了海", lines: [], source: "每日记忆 · 恋爱系统",
    created: "", actions: ["write", "discard"],
  });
  assert.match(html, /class="button secondary" data-pending-action="write"/);
  assert.match(html, /class="text-button" data-pending-action="discard"/);
  assert.match(html, /写入四库/);
});

test("a strip counts every room that has work", () => {
  const html = pendingStripMarkup({ total: 6, draft: 0, fact: 1, triage: 2, love: 3 });
  assert.match(html, />6</);
  assert.match(html, /事实 1 · 收件箱 2 · 恋爱记忆 3/);
  assert.equal(html.includes("草稿 0"), false);
});
