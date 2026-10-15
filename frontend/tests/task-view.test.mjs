import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Pure filtering for the task list views. No DOM, no network.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function load() {
  const context = vm.createContext({});
  const filename = path.join(frontend, "task-view.js");
  const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context, identifier: filename });
  await module.link(() => { throw new Error("task-view.js must stay dependency-free"); });
  await module.evaluate();
  return module.namespace;
}

const tasks = [
  { id: 1, title: "今天到期", status: "todo", due_date: "2026-10-04" },
  { id: 2, title: "昨天逾期", status: "in_progress", due_date: "2026-10-03" },
  { id: 3, title: "明天到期", status: "todo", due_date: "2026-10-05" },
  { id: 4, title: "没有日期", status: "todo", due_date: null },
  { id: 5, title: "已完成且逾期", status: "completed", due_date: "2026-10-01" },
  { id: 6, title: "已取消", status: "cancelled", due_date: "2026-10-04" },
];
const TODAY = "2026-10-04";
const titles = (rows) => Array.from(rows, (task) => task.title);

test("task filters keep the open, due-today and overdue views correct", async () => {
  const { filterTasks, TASK_FILTERS } = await load();
  assert.deepEqual(Array.from(TASK_FILTERS), ["open", "due_today", "overdue", "completed", "all"]);
  assert.deepEqual(titles(filterTasks(tasks, "open", TODAY)), ["今天到期", "昨天逾期", "明天到期", "没有日期"]);
  assert.deepEqual(titles(filterTasks(tasks, "due_today", TODAY)), ["今天到期"]);
  assert.deepEqual(titles(filterTasks(tasks, "overdue", TODAY)), ["昨天逾期"]);
  assert.deepEqual(titles(filterTasks(tasks, "completed", TODAY)), ["已完成且逾期"]);
  assert.equal(filterTasks(tasks, "all", TODAY).length, 6);
  assert.deepEqual(Array.from(filterTasks([], "due_today", TODAY)), []);
  assert.deepEqual(Array.from(filterTasks(null, "overdue", TODAY)), []);
});

test("task ordering keeps priority, due, updated and manual views deterministic", async () => {
  const { orderTasks, TASK_SORTS } = await load();
  assert.deepEqual(Array.from(TASK_SORTS), ["default", "priority", "due", "updated", "manual"]);
  const rows = [
    { id: 1, title: "低与无截止", priority: "low", due_date: null, updated_at: "2026-10-01T00:00:00+00:00", sort_order: 0 },
    { id: 2, title: "紧急有截止", priority: "urgent", due_date: "2026-10-06", updated_at: "2026-10-02T00:00:00+00:00", sort_order: 0 },
    { id: 3, title: "高无截止", priority: "high", due_date: null, updated_at: "2026-10-04T00:00:00+00:00", sort_order: 20 },
    { id: 4, title: "中今天", priority: "medium", due_date: "2026-10-05", updated_at: "2026-10-03T00:00:00+00:00", sort_order: 10 },
  ];
  const titles = (items) => Array.from(items, (task) => task.title);
  assert.deepEqual(titles(orderTasks(rows, "default")), titles(rows)); // 服务端顺序原样保留
  assert.deepEqual(titles(orderTasks(rows, "priority")), ["紧急有截止", "高无截止", "中今天", "低与无截止"]);
  assert.deepEqual(titles(orderTasks(rows, "due")), ["中今天", "紧急有截止", "高无截止", "低与无截止"]);
  assert.deepEqual(titles(orderTasks(rows, "updated")), ["高无截止", "中今天", "紧急有截止", "低与无截止"]);
  assert.deepEqual(titles(orderTasks(rows, "manual")), ["低与无截止", "紧急有截止", "中今天", "高无截止"]);
  assert.deepEqual(Array.from(orderTasks(null, "priority")), []);
});

test("moveTask swaps neighbours in bounds without mutating the input", async () => {
  const { moveTask } = await load();
  const rows = [{ id: 1 }, { id: 2 }, { id: 3 }];
  const ids = (items) => Array.from(items, (task) => task.id);
  assert.deepEqual(ids(moveTask(rows, 2, -1)), [2, 1, 3]);
  assert.deepEqual(ids(moveTask(rows, 2, 1)), [1, 3, 2]);
  assert.deepEqual(ids(moveTask(rows, "1", -1)), [1, 2, 3]);
  assert.deepEqual(ids(moveTask(rows, 3, 1)), [1, 2, 3]);
  assert.deepEqual(ids(moveTask(rows, 999, 1)), [1, 2, 3]);
  assert.deepEqual(ids(moveTask(rows, 2, 0)), [1, 2, 3]);
  assert.deepEqual(ids(moveTask(null, 1, 1)), []);
  assert.deepEqual(ids(rows), [1, 2, 3]);
});
