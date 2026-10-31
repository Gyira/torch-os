import test from "node:test";
import assert from "node:assert/strict";

import {
  OPS_SYSTEM_CARDS, opsHomeState, opsNotebookCards,
} from "../ops-model.js";

const OVERVIEW = {
  chapters: [
    { key: "task", title: "待办事项", count: 4, latest: null },
    { key: "health", title: "健康与体重", count: 12, latest: null },
    { key: "finance", title: "财富与记账", count: 3, latest: null },
  ],
  recent: [
    { path: "02_生活运营/00_待办事项/收集箱/写方案.md", chapter: "task", title: "写方案", date: "2026-10-31" },
    { path: "02_生活运营/01_健康与体重/2026-10.md", chapter: "health", title: "10 月健康", date: "2026-10-30" },
  ],
  task_summary: { open: 4, overdue: 2, due_today: 1 },
};

test("ops home only exposes the protected task notebook", () => {
  const cards = opsNotebookCards(OVERVIEW);
  assert.deepEqual(cards.map((card) => card.key), ["task"]);
  assert.equal(cards[0].count, 4);
  const home = opsHomeState(OVERVIEW);
  assert.equal(home.taskCard.key, "task");
  assert.deepEqual(home.summary, { open: 4, overdue: 2, dueToday: 1 });
  assert.deepEqual(home.recent.map((note) => note.title), ["写方案"]);
});

test("ops home lists four system cards with love and health ready", () => {
  const home = opsHomeState({});
  assert.deepEqual(home.systems.map((card) => card.key), [
    "love", "health", "finance", "family",
  ]);
  assert.equal(home.systems.filter((card) => card.status === "ready").length, 2);
  assert.equal(home.systems.find((card) => card.key === "love").statusLabel, "可用");
  assert.equal(home.systems.find((card) => card.key === "health").statusLabel, "可用");
  for (const key of ["finance", "family"]) {
    assert.equal(home.systems.find((card) => card.key === key).statusLabel, "尚未开发");
  }
  assert.equal(OPS_SYSTEM_CARDS.length, 4);
});

test("ops home tolerates missing overviews", () => {
  const home = opsHomeState({});
  assert.equal(home.taskCard.key, "task");
  assert.equal(home.taskCard.count, 0);
  assert.deepEqual(home.recent, []);
  assert.deepEqual(home.summary, { open: 0, overdue: 0, dueToday: 0 });
});
