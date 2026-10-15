import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Pure read-only grid layout: Monday-first weeks, explicit statuses, escaped labels.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function loadGrid() {
  const context = vm.createContext({ Intl, Date });
  const modules = new Map();
  const pending = new Map();
  const core = new vm.SyntheticModule(["escapeHtml"], function () {
    this.setExport("escapeHtml", (value) => String(value ?? "").replace(/[&<>"']/g,
      (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])));
  }, { context });
  modules.set(path.join(frontend, "core.js"), core);
  async function load(name) {
    const filename = path.resolve(frontend, name);
    if (modules.has(filename)) return modules.get(filename);
    if (pending.has(filename)) return pending.get(filename);
    const promise = (async () => {
      const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context, identifier: filename });
      await module.link((specifier, referring) => load(path.relative(frontend, path.resolve(path.dirname(referring.identifier), specifier))));
      await module.evaluate();
      modules.set(filename, module);
      pending.delete(filename);
      return module;
    })();
    pending.set(filename, promise);
    return promise;
  }
  return (await load("habit-grid.js")).namespace;
}

test("buildHabitGrid aligns Monday-first and pads partial weeks", async () => {
  const { buildHabitGrid } = await loadGrid();
  const grid = buildHabitGrid([], "2026-09-05", "2026-10-04");
  assert.equal(grid.weeks.length, 5);
  assert.equal(grid.weeks[0].filter((cell) => cell === null).length, 5);
  const days = grid.weeks.flat().filter(Boolean);
  assert.equal(days.length, 30);
  assert.equal(days[0].date, "2026-09-05");
  assert.equal(days[29].date, "2026-10-04");
  assert.equal(days.every((cell) => cell.status === "unrecorded" && cell.scheduled === false), true);

  const wider = buildHabitGrid([], "2026-09-04", "2026-10-04");
  assert.equal(wider.weeks.length, 5);
  assert.equal(wider.weeks[0].filter((cell) => cell === null).length, 4);
  assert.equal(wider.weeks.flat().filter(Boolean).length, 31);
});

test("buildHabitGrid maps recorded statuses and habitGridHtml labels them", async () => {
  const { buildHabitGrid, habitGridHtml } = await loadGrid();
  const entries = [
    { date: "2026-10-04", scheduled: true, status: "done" },
    { date: "2026-10-03", scheduled: true, status: "skipped" },
    { date: "2026-10-02", scheduled: false, status: "unrecorded" },
  ];
  const grid = buildHabitGrid(entries, "2026-09-05", "2026-10-04");
  const cells = grid.weeks.flat().filter(Boolean);
  const cellOf = (day) => JSON.stringify(cells.find((cell) => cell.date === day));
  assert.equal(cellOf("2026-10-04"), JSON.stringify({ date: "2026-10-04", status: "done", scheduled: true }));
  assert.equal(cellOf("2026-10-03"), JSON.stringify({ date: "2026-10-03", status: "skipped", scheduled: true }));
  assert.equal(cellOf("2026-10-02"), JSON.stringify({ date: "2026-10-02", status: "unrecorded", scheduled: false }));

  const html = habitGridHtml(grid);
  assert.match(html, /role="list"/);
  assert.match(html, /aria-label="2026-10-04 已完成"/);
  assert.match(html, /aria-label="2026-10-03 主动休息"/);
  assert.match(html, /aria-label="2026-10-02 非计划日"/);
  assert.match(html, /class="habit-grid-cell done"/);
  assert.match(html, /class="habit-grid-cell unscheduled"/);
  assert.match(html, /空白不等于失败/);
  assert.equal((html.match(/habit-grid-cell /g) || []).length, 35);
});
