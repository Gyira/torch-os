import test from "node:test";
import assert from "node:assert/strict";

import {
  BOOKS, bookPageMarkup, entriesMarkup, entryCardMarkup, migrationMarkup,
  overviewMarkup, pendingCardMarkup,
} from "../self-archive-model.js";

// 自我档案 v2 纯渲染：本子、条目、画像位置、迁移与待确认卡片。

test("十一个本子 key 唯一，顺序稳定", () => {
  assert.equal(BOOKS.length, 11);
  assert.equal(new Set(BOOKS.map((book) => book.key)).size, 11);
  assert.equal(BOOKS[0].key, "self_facts");
  assert.equal(BOOKS.at(-1).key, "self_experiments");
});

test("总览先本子后画像", () => {
  const html = overviewMarkup({
    books: [{ key: "self_values", count: 2 }],
    count: 2,
    portrait: { text: "一句话画像", updated_at: "2026-11-01T12:00:00+08:00" },
    migration: { needed: false },
  }, (text) => `<p>${text}</p>`);
  assert.ok(html.indexOf("self-book-grid") < html.indexOf("self-portrait"));
  assert.match(html, /一句话画像/);
  assert.match(html, /十一个本子/);
});

test("条目按类型分组，并保留证据/来源", () => {
  const html = entriesMarkup([
    { entry_id: "e1", title: "事实一", entry_type: "fact", confidence: "high", text: "正文", status: "active" },
    { entry_id: "e2", title: "推断一", entry_type: "inference", confidence: "medium", text: "正文", evidence: "三次反例", sources: ["S1"], status: "active" },
  ]);
  assert.ok(html.indexOf("事实") < html.indexOf("推断"));
  assert.match(html, /三次反例/);
  assert.match(html, /S1/);
});

test("条目卡片有辩论入口，但没有直接编辑/删除", () => {
  const html = entryCardMarkup({
    entry_id: "e1", title: "世界观", entry_type: "narrative",
    confidence: "medium", text: "世界可理解。", status: "active",
  });
  assert.match(html, /data-self-debate="e1"/);
  assert.doesNotMatch(html, /data-self-edit/);
  assert.doesNotMatch(html, /data-self-delete/);
});

test("本子页显示现行与历史切换", () => {
  const html = bookPageMarkup({
    book: { key: "self_values", title: "三观与决策", subtitle: "价值观", path: "01_自我档案/01_档案/06_三观与决策" },
    entries: [],
    history: false,
  });
  assert.match(html, /三观与决策/);
  assert.match(html, /data-self-history-toggle="1"/);
  assert.match(html, /这个本子还没有条目/);
});

test("迁移卡只在需要时出现", () => {
  assert.equal(migrationMarkup({ needed: false }), "");
  const html = migrationMarkup({ needed: true, files: 6 });
  assert.match(html, /检测到旧版自我档案结构/);
  assert.match(html, /6 个旧文件/);
});

test("待确认卡片可采纳、改后采纳、驳回", () => {
  const html = pendingCardMarkup({
    pending_id: "pend_1", section_title: "三观与决策", title: "世界观",
    confidence: "medium", new_text: "世界更像情境决定。", old_text: "世界是善意的。",
    evidence: "三次见闻", sources: ["S1"], reason: "用户给出反例",
  });
  assert.match(html, /data-pending-accept="pend_1"/);
  assert.match(html, /data-pending-edit="pend_1"/);
  assert.match(html, /data-pending-reject="pend_1"/);
  assert.match(html, /世界更像情境决定/);
});
