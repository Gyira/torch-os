import { buildMoveFolders } from "../move-folders.js";
import test from "node:test";
import assert from "node:assert/strict";

import {
  applyNotebookOrder,
  buildNotebookCards,
  customNotebookDefinition,
  customNotebookKey,
  customNotebookLeafName,
  customNotebookParentKey,
  customNotebookRelativeTrail,
  customNotebookTrail,
  isSubNotebookKey,
  subchaptersFor,
  subNotebookChapter,
  subNotebookKey,
  subNotebookName,
  subNotebookTrail,
} from "../notebook-model.js";

test("saved notebook order skips stale keys and appends new notebooks", () => {
  const cards = [{ key: "a" }, { key: "b" }, { key: "c" }];
  assert.deepEqual(
    applyNotebookOrder(cards, ["c", "gone", "a"]).map((card) => card.key),
    ["c", "a", "b"]);
  assert.deepEqual(
    applyNotebookOrder(cards, []).map((card) => card.key),
    ["a", "b", "c"]);
  assert.deepEqual(
    applyNotebookOrder(cards, ["a", "a"]).map((card) => card.key),
    ["a", "b", "c"]);
});

test("home cards follow the saved notebook order", () => {
  const cards = buildNotebookCards("life", [], [], [], ["essay", "journal"]);
  assert.equal(cards.length, 7);
  assert.deepEqual(cards.map((card) => card.key), [
    "essay", "journal", "reflection", "insight", "goal", "project", "work",
  ]);
});

test("sub-notebook keys keep parent/child hierarchy", () => {
  const parent = customNotebookKey("学习", "life");
  const child = customNotebookKey("学习/数学", "life");
  const grandchild = customNotebookKey("学习/数学/代数", "life");
  assert.equal(parent, "custom:学习");
  assert.equal(child, "custom:学习/数学");
  assert.equal(customNotebookParentKey(child), parent);
  assert.equal(customNotebookParentKey(parent), "");
  assert.equal(customNotebookLeafName(grandchild), "代数");
  assert.equal(customNotebookTrail(grandchild), "学习 / 数学 / 代数");
  assert.equal(customNotebookRelativeTrail(grandchild, parent), "数学 / 代数");
  const definition = customNotebookDefinition(child);
  assert.equal(definition.title, "数学");
  assert.equal(definition.parent, parent);
  assert.equal(definition.depth, 1);
});

test("home cards only show top-level books and count includes descendants", () => {
  const parent = customNotebookKey("学习");
  const child = customNotebookKey("学习/数学");
  const cards = buildNotebookCards("life", [], [
    { key: parent, title: "学习", path: "人生路/学习", count: 4, latest: null },
    { key: child, title: "数学", path: "人生路/学习/数学", count: 1, latest: null, parent },
  ]);
  const learning = cards.find((card) => card.key === parent);
  assert.ok(learning);
  assert.equal(cards.some((card) => card.key === child), false);
  assert.equal(learning.subCount, 1);
  assert.match(learning.description, /1 个子本子/);
});

test("subchaptersFor lists all descendants with relative trails", () => {
  const parent = customNotebookKey("学习");
  const child = customNotebookKey("学习/数学");
  const grandchild = customNotebookKey("学习/数学/代数");
  const rows = subchaptersFor("life", parent, [], [
    { key: parent, title: "学习", count: 3 },
    { key: child, title: "数学", count: 2 },
    { key: grandchild, title: "代数", count: 1 },
  ]);
  assert.deepEqual(rows.map((row) => row.key), [child, grandchild]);
  assert.deepEqual(rows.map((row) => row.title), ["数学", "数学 / 代数"]);
});

test("move picker nests real destinations under areas, renamed books and sub-books", () => {
  const parent = customNotebookKey("学习");
  const child = customNotebookKey("学习/数学");
  const roots = buildMoveFolders({
    life: [
      { key: child, parent, title: "数学", path: "人生路/学习/数学" },
      { key: parent, title: "学习", path: "人生路/学习" },
      { key: "essay", title: "随笔", path: "人生路/随笔" },
      { key: "sub:essay:草稿", chapter: "essay", title: "草稿", path: "人生路/随笔/草稿" },
      { key: "reflection", title: "反思", path: "人生路/反思复盘/反思" },
    ],
    knowledge: [{ key: "inbox", title: "收件箱", path: "知识库/收件箱" }],
    titles: { life: { [parent]: "课程", reflection: "复盘本" } },
    order: { life: [parent, "essay", "reflection"] },
  });
  const [life, knowledge] = roots;
  assert.deepEqual(life.children.map((n) => n.title), ["课程", "随笔", "复盘本"]);
  assert.equal(life.children[0].children[0].key, child);
  assert.equal(life.children[1].children[0].key, "sub:essay:草稿");
  assert.equal(life.children[2].path, undefined);
  assert.equal(life.children[2].children[0].title, "反思");
  assert.equal(knowledge.children[0].area, "knowledge");
});

test("built-in chapter sub-notebooks parse and group into their notebook", () => {
  const key = subNotebookKey("essay", "灵感");
  assert.equal(key, "sub:essay:灵感");
  assert.equal(isSubNotebookKey(key), true);
  assert.equal(subNotebookChapter(key), "essay");
  assert.equal(subNotebookName(key), "灵感");
  assert.equal(subNotebookTrail(subNotebookKey("reflection", "专题/复盘")), "专题 / 复盘");
  assert.equal(isSubNotebookKey("essay"), false);

  // 单章节本子：子本子直接显示名字。
  const single = subchaptersFor("life", "essay", [
    { key: "essay", title: "随笔", count: 2 },
  ], [], [
    { key, chapter: "essay", name: "灵感", chapter_title: "随笔", count: 1 },
  ]);
  assert.deepEqual(single.map((row) => row.title), ["随笔", "灵感"]);

  // 多章节本子：子本子带上基础章节名，避免不同章节下同名混淆。
  const multi = subchaptersFor("life", "reflection", [
    { key: "reflection", title: "反思", count: 1 },
    { key: "review_week", title: "周复盘", count: 0 },
    { key: "review_month", title: "月复盘", count: 0 },
  ], [], [
    {
      key: subNotebookKey("reflection", "专题"), chapter: "reflection",
      name: "专题", chapter_title: "反思", count: 1,
    },
  ]);
  assert.equal(multi.length, 4);
  assert.equal(multi[3].title, "反思 · 专题");
});
