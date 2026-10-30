import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_LIFE_WRITE_CHAPTER, KNOWLEDGE_NOTEBOOKS, LIFE_NOTEBOOKS,
  buildMoveTargetGroups, buildNotebookCards, canDeleteNotebook, customNotebookKey,
  customNotebookName, customNotebookPillar, defaultWriteChapter, greetingForHour,
  isProtectedNotebook, journalHeading, knowledgeHeroState, lifeHeroState,
  mergeNotesByRecency, noteCards, notebookById, notebookForChapter, notebooksFor,
  subchaptersFor,
} from "../notebook-model.js";

const LIFE_ROWS = [
  { key: "journal", title: "日记", count: 3, latest: { title: "2026-10-30", chapter: "journal", date: "2026-10-30", path: "日记/2026-10-30.md" } },
  { key: "reflection", title: "反思", count: 2, latest: { title: "反思 A", chapter: "reflection", date: "2026-10-29", path: "反思/A.md" } },
  { key: "review_week", title: "周复盘", count: 1, latest: { title: "周复盘 A", chapter: "review_week", date: "2026-10-26", path: "反思/W.md" } },
  { key: "review_month", title: "月复盘", count: 0, latest: null },
  { key: "insight_life", title: "生活心得", count: 1, latest: { title: "心得 A", chapter: "insight_life", date: "2026-10-20", path: "心得/A.md" } },
  { key: "essay", title: "随笔", count: 4, latest: { title: "随笔 A", chapter: "essay", date: "2026-10-28", path: "随笔/A.md" } },
  { key: "unknown_new", title: "未登记", count: 1, latest: { title: "新章节", chapter: "unknown_new", date: "2026-10-27", path: "其他/N.md" } },
];

test("life notebooks aggregate counts and keep the newest note", () => {
  const cards = buildNotebookCards("life", LIFE_ROWS);
  const byKey = new Map(cards.map((card) => [card.key, card]));
  assert.equal(byKey.get("journal").count, 3);
  assert.equal(byKey.get("reflection").count, 3);
  assert.equal(byKey.get("reflection").latest.title, "反思 A");
  assert.equal(byKey.get("essay").count, 4);
  // 未登记的章节不能被静默藏掉。
  assert.equal(byKey.get("other").count, 1);
  assert.ok(byKey.has("project"));
  assert.equal(byKey.get("project").empty, true);
});

test("notebook lookup maps every declared chapter and ignores unknown ones", () => {
  assert.equal(notebookForChapter("life", "review_week").key, "reflection");
  assert.equal(notebookForChapter("life", "essay").key, "essay");
  assert.equal(notebookForChapter("knowledge", "method_general").key, "method");
  assert.equal(notebookForChapter("knowledge", "not-a-chapter"), null);
});

test("move targets group by notebook, skip the current chapter and keep custom notebooks", () => {
  const groups = buildMoveTargetGroups("life", [
    { key: "reflection", title: "反思" },
    { key: "review_week", title: "周复盘" },
    { key: "review_month", title: "月复盘" },
    { key: "insight_life", title: "生活心得" },
    { key: "essay", title: "随笔" },
    { key: "custom:读书摘记", title: "读书摘记", custom: true },
    { key: "unknown_new", title: "未登记" },
  ], "reflection");
  assert.deepEqual(groups.map((group) => group.title), [
    "反思复盘", "心得体会", "随笔", "我的本子", "其他",
  ]);
  assert.deepEqual(groups[0].chapters.map((chapter) => chapter.key), ["review_week", "review_month"]);
  assert.deepEqual(groups[1].chapters.map((chapter) => chapter.key), ["insight_life"]);
  assert.deepEqual(groups[3].chapters.map((chapter) => chapter.key), ["custom:读书摘记"]);
  assert.deepEqual(groups[4].chapters.map((chapter) => chapter.key), ["unknown_new"]);
  // 当前章节永远不会出现在目标里。
  assert.ok(!groups.some((group) => group.chapters.some((chapter) => chapter.key === "reflection")));
});

test("subchapters only include the notebook's own chapters with counts", () => {
  const rows = subchaptersFor("life", "reflection", LIFE_ROWS);
  assert.deepEqual(rows.map((row) => [row.key, row.count]), [
    ["reflection", 2], ["review_week", 1], ["review_month", 0],
  ]);
});

test("custom notebooks become their own cards, not one shared 其他 card", () => {
  const customRows = [{
    key: "custom:读书摘记", title: "读书摘记", path: "00_人生路/读书摘记",
    count: 2, latest: { title: "第一条", chapter: "custom:读书摘记", date: "2026-10-30", path: "00_人生路/读书摘记/第一条.md" },
  }];
  const cards = buildNotebookCards("life", LIFE_ROWS, customRows);
  const custom = cards.find((card) => card.key === "custom:读书摘记");
  assert.equal(custom.title, "读书摘记");
  assert.equal(custom.count, 2);
  assert.equal(custom.custom, true);
  assert.equal(custom.chapters[0].key, "custom:读书摘记");
  assert.ok(!cards.some((card) => card.key === "other" && card.title === "其他"
    && card.chapters.some((chapter) => chapter.key === "custom:读书摘记")));
  assert.equal(notebookById("life", "custom:读书摘记").title, "读书摘记");
  assert.equal(notebookForChapter("life", "custom:读书摘记").title, "读书摘记");
  assert.deepEqual(subchaptersFor("life", "custom:读书摘记", customRows).map((row) => row.title),
    ["读书摘记"]);
});

test("knowledge custom notebooks use a distinct key and show their own root", () => {
  const key = customNotebookKey("资料卡", "knowledge");
  assert.equal(key, "custom:knowledge:资料卡");
  assert.equal(customNotebookPillar(key), "knowledge");
  assert.equal(customNotebookName(key), "资料卡");
  assert.equal(customNotebookPillar("custom:读书摘记"), "life");
  assert.equal(customNotebookName("custom:读书摘记"), "读书摘记");

  const customRows = [{
    key, title: "资料卡", path: "03_知识库/资料卡", count: 1,
    latest: { title: "卡片 A", chapter: key, date: "2026-10-30", path: "03_知识库/资料卡/A.md" },
  }];
  const cards = buildNotebookCards("knowledge", [], customRows);
  const custom = cards.find((card) => card.key === key);
  assert.equal(custom.title, "资料卡");
  assert.equal(custom.description, "自建本子 · 03_知识库/资料卡");
  assert.equal(custom.pillar, "knowledge");
  assert.equal(notebookById("knowledge", key).title, "资料卡");
  assert.equal(notebookForChapter("knowledge", key).key, key);
  assert.deepEqual(subchaptersFor("knowledge", key, customRows).map((row) => row.title),
    ["资料卡"]);
});

test("the general life writing entry defaults to essay", () => {
  assert.equal(DEFAULT_LIFE_WRITE_CHAPTER, "essay");
  assert.equal(defaultWriteChapter("life"), "essay");
  assert.equal(defaultWriteChapter("knowledge"), "topic");
  assert.equal(defaultWriteChapter(""), "essay");
});

test("deleted builtin notebooks are filtered and protected defaults stay", () => {
  const rows = [
    { key: "reflection", title: "反思", count: 1, latest: null },
    { key: "review_week", title: "周复盘", count: 1, latest: null },
    { key: "essay", title: "随笔", count: 1, latest: null },
  ];
  const cards = buildNotebookCards("life", rows, [], ["reflection"]);
  assert.ok(!cards.some((card) => card.key === "reflection"));
  assert.ok(cards.some((card) => card.key === "essay"));
  const other = cards.find((card) => card.key === "other");
  assert.ok(!other || !other.chapters.some((chapter) =>
    ["reflection", "review_week", "review_month"].includes(chapter.key)));
  assert.equal(isProtectedNotebook("life", "essay"), true);
  assert.equal(isProtectedNotebook("life", "reflection"), false);
  assert.equal(isProtectedNotebook("knowledge", "inbox"), true);
  assert.equal(canDeleteNotebook("life", { key: "reflection" }), true);
  assert.equal(canDeleteNotebook("life", { key: "essay" }), false);
  assert.equal(canDeleteNotebook("knowledge", { key: "inbox" }), false);
  assert.equal(canDeleteNotebook("knowledge", { key: "topic" }), true);
  assert.equal(canDeleteNotebook("life", { key: "custom:自建", custom: true }), true);
});

test("life hero says what to do today and counts the streak", () => {
  const week = [
    { date: "2026-10-27", exists: false },
    { date: "2026-10-28", exists: true },
    { date: "2026-10-29", exists: true },
    { date: "2026-10-30", exists: true },
  ];
  const state = lifeHeroState({
    today: { date: "2026-10-30", journal_written: true, journal_updated_at: "2026-10-30T09:00:00+08:00", week },
  }, "2026-10-30");
  assert.equal(state.written, true);
  assert.equal(state.streak, 3);
  assert.equal(state.weekDone, 3);
  assert.match(state.headline, /留下一页/);

  const empty = lifeHeroState({ today: { date: "2026-10-30", journal_written: false, week } }, "2026-10-30");
  assert.equal(empty.streak, 0);
  assert.match(empty.headline, /写点什么/);
});

test("knowledge hero surfaces the inbox backlog", () => {
  assert.equal(knowledgeHeroState({ inbox_count: 4 }).inboxCount, 4);
  assert.match(knowledgeHeroState({ inbox_count: 4 }).copy, /4 条/);
  assert.match(knowledgeHeroState({}).copy, /DeepSeek/);
});

test("journal cards read like a date, not a filename", () => {
  const [card] = noteCards([{
    path: "00_人生路/01_日记/2026-10-30.md", chapter: "journal", date: "2026-10-30",
    title: "2026-10-30", excerpt: "今天把项目收尾了。",
  }]);
  assert.equal(card.title, "日记 · 10月30日 周五");
  assert.equal(card.excerpt, "今天把项目收尾了。");
});

test("journal heading uses the ISO week and weekday", () => {
  assert.equal(journalHeading("2026-10-30"), "# 2026-10-30（第44周，周五）");
});

test("recent notes merge by date without duplicates", () => {
  const merged = mergeNotesByRecency([
    [{ path: "a.md", date: "2026-10-28" }, { path: "b.md", date: "2026-10-30" }],
    [{ path: "a.md", date: "2026-10-28" }, { path: "c.md", date: "2026-10-29" }],
  ]);
  assert.deepEqual(merged.map((note) => note.path), ["b.md", "c.md", "a.md"]);
});

test("definitions stay readable and cover the expected notebooks", () => {
  assert.ok(LIFE_NOTEBOOKS.some((item) => item.daily));
  assert.ok(KNOWLEDGE_NOTEBOOKS.some((item) => item.primary));
  assert.equal(notebooksFor("life").length, LIFE_NOTEBOOKS.length);
  assert.equal(notebooksFor("unknown").length, 0);
  assert.equal(greetingForHour(9), "早上好");
  assert.equal(greetingForHour(21), "晚上好");
});
