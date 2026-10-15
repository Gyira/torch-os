import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Pure read-only month calendar: Monday-first layout, recorded markers, escaped labels.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function loadCalendar() {
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
  return (await load("journal-calendar.js")).namespace;
}

test("month helpers move across year boundaries and know month lengths", async () => {
  const { monthKey, shiftMonth, monthBounds } = await loadCalendar();
  assert.equal(monthKey("2026-10-05"), "2026-10");
  assert.equal(shiftMonth("2026-01", -1), "2025-12");
  assert.equal(shiftMonth("2025-12", 1), "2026-01");
  assert.equal(shiftMonth("2026-10", 3), "2027-01");
  assert.equal(shiftMonth("2026-10", 0), "2026-10");
  const bounds = monthBounds("2026-10");
  assert.equal(bounds.start, "2026-10-01");
  assert.equal(bounds.end, "2026-10-31");
  assert.equal(monthBounds("2026-02").end, "2026-02-28");
  assert.equal(monthBounds("2024-02").end, "2024-02-29");
});

test("calendar pads Monday-first weeks and marks recorded, today and future days", async () => {
  const { buildJournalCalendar } = await loadCalendar();
  const calendar = buildJournalCalendar("2026-10", "2026-10-05", [
    { date: "2026-10-03" }, "2026-10-31", "2026-09-30",
  ]);
  assert.equal(calendar.weeks.length, 5);
  assert.equal(calendar.weeks[0].filter((cell) => cell === null).length, 3); // 2026-10-01 is Thursday.
  const days = calendar.weeks.flat().filter(Boolean);
  assert.equal(days.length, 31);
  assert.equal(days[0].date, "2026-10-01");
  assert.equal(days[30].date, "2026-10-31");
  const byDate = new Map(days.map((cell) => [cell.date, cell]));
  assert.equal(byDate.get("2026-10-03").recorded, true);
  assert.equal(byDate.get("2026-10-31").recorded, true);
  assert.equal(byDate.get("2026-10-05").today, true);
  assert.equal(byDate.get("2026-10-05").future, false);
  assert.equal(byDate.get("2026-10-06").future, true);
  assert.equal(calendar.recorded, 2); // The September date is outside this month.
});

test("calendar HTML keeps future days unclickable, names buttons and escapes labels", async () => {
  const { buildJournalCalendar, journalCalendarHtml } = await loadCalendar();
  const calendar = buildJournalCalendar("2026-10", "2026-10-05", ["2026-10-03"]);
  const html = journalCalendarHtml({ ...calendar, month: "<2026-10>" });
  assert.match(html, /&lt;2026-10&gt; 的每日记录月历/);
  assert.equal((html.match(/<button/g) || []).length, 5); // 1–5 October are clickable.
  assert.equal((html.match(/class="journal-calendar-cell future"/g) || []).length, 26);
  assert.match(html, /data-journal-calendar-day="2026-10-03"/);
  assert.match(html, /aria-label="2026-10-03 · 已记录"/);
  assert.match(html, /aria-label="2026-10-05 · 未记录 · 今天"/);
  assert.match(html, /aria-label="2026-10-06 · 未记录 · 未来日期"/);
  assert.match(html, /class="journal-calendar-cell recorded" type="button" data-journal-calendar-day="2026-10-03"/);
  assert.doesNotMatch(html, /<script|href=/);
});
