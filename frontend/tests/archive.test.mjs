import test from "node:test";
import assert from "node:assert/strict";

import { archiveCompleteness, archiveMarkup } from "../archive-markup.js";

// 自我档案首屏：封面 / 目录 / 自传时间线。只测纯函数，不碰 DOM。

const IDENTITY = {
  name: "小林", motto: "", mottos: ["应该去看更大的世界", "站在属于你的位置"],
  worldview: "世界是可被理解的", lifeview: "", values: "", notes: "",
  path: "01_自我档案/01_内核与三观/身份（identity）.md",
  updated_at: "2026-10-27T15:39:06+00:00",
};

const CHAPTERS = [
  { key: "core_values", title: "内核与三观", count: 1, latest: null },
  { key: "memory", title: "记忆与自传", count: 2, latest: null },
  { key: "idea", title: "想法与灵感", count: 0, latest: null },
];

test("座右铭写在 mottos 列表里也算填过", () => {
  const stats = archiveCompleteness({ mottos: ["去更大的世界"] });
  assert.equal(stats.filled, 1);
  assert.equal(stats.total, 6);
  assert.ok(stats.missing.includes("世界观"));
  assert.equal(stats.missing.includes("座右铭"), false);
});

test("空白身份就是 0/6，不假装认识我", () => {
  const stats = archiveCompleteness({});
  assert.equal(stats.filled, 0);
  assert.equal(stats.percent, 0);
});

test("封面把空缺说成空缺，而不是留一片白", () => {
  const html = archiveMarkup({ identity: {}, chapters: [], timeline: [], noteCount: 0 });
  assert.match(html, /还没有署名/);
  assert.match(html, /还没有座右铭/);
  assert.match(html, /档案完整度 0\/6/);
  assert.match(html, /还没写/);
  assert.match(html, /还没有写进自传/);
});

test("填过的字段进封面，座右铭逐行显示", () => {
  const html = archiveMarkup({ identity: IDENTITY, chapters: CHAPTERS, timeline: [], noteCount: 3 });
  assert.match(html, /小林/);
  assert.match(html, /应该去看更大的世界/);
  assert.match(html, /站在属于你的位置/);
  assert.match(html, /世界是*可被理解的/);
  assert.match(html, /档案完整度 3\/6/);
  assert.match(html, /下一步可以补/);
});

test("目录带编号、条数和一句说明", () => {
  const html = archiveMarkup({ identity: IDENTITY, chapters: CHAPTERS, timeline: [], noteCount: 3 });
  assert.match(html, /archive-toc-num">01</);
  assert.match(html, /archive-toc-num">03</);
  assert.match(html, /你信什么，什么不能碰/);
  assert.match(html, /data-chapter="memory"/);
  assert.match(html, /archive-toc-count is-empty">0</);
});

test("时间线按传入顺序渲染，带日期和摘要", () => {
  const html = archiveMarkup({
    identity: IDENTITY, chapters: CHAPTERS, noteCount: 3,
    timeline: [
      { path: "01_自我档案/02_记忆与自传/a.md", title: "专科毕业进铁路", date: "2026-10-27", summary: "2025 年 9 月入职" },
      { path: "01_自我档案/02_记忆与自传/b.md", title: "第一次考研决定", date: "2026-10-20", summary: "" },
    ],
  });
  assert.match(html, /archive-time">2026-10-27/);
  assert.match(html, /专科毕业进铁路/);
  assert.match(html, /2025 年 9 月入职/);
  assert.ok(html.indexOf("专科毕业进铁路") < html.indexOf("第一次考研决定"));
});

test("档案里的文字都经过转义", () => {
  const html = archiveMarkup({
    identity: { name: "<img src=x>", worldview: "<script>bad()</script>" },
    chapters: [{ key: "memory", title: "<b>标题</b>", count: 1 }],
    timeline: [{ path: "x.md", title: "<i>事件</i>", date: "2026-10-27", summary: "<u>摘要</u>" }],
    noteCount: 1,
  });
  assert.equal(html.includes("<img"), false);
  assert.equal(html.includes("<script>"), false);
  assert.equal(html.includes("<b>"), false);
  assert.equal(html.includes("<i>事件</i>"), false);
});
