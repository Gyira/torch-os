import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import path from "node:path";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

// Offline module/event integration; layout and browser navigation are verified separately.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const today = "2026-10-03";
class Clock extends Date { constructor(value) { super(value === undefined ? "2026-10-03T01:00:00Z" : value); } }
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const unescapeHtml = (value) => value.replace(/&(?:amp|lt|gt|quot|#39);/g, (text) => ({ "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'" }[text]));

async function harness() {
  const nodes = new Map();
  const dynamic = new Set();
  const windowListeners = new Map();
  const documentListeners = new Map();
  const weekdays = Array.from({ length: 7 }, (_, index) => ({ value: String(index), checked: true }));
  const requests = [], confirmations = [], decisions = [];
  const state = { view: "habits", config: { capabilities: { habits: true } } };
  let updated = 0;
  const get = (selector) => nodes.get(selector) || null;
  function element(selector, dataset = {}) {
    let html = "";
    const node = {
      value: "", textContent: "", disabled: false, hidden: false, dataset, listeners: new Map(),
      addEventListener(type, handler) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(handler); },
      async emit(type, fields = {}) { const event = { target: this, preventDefault() { this.defaultPrevented = true; }, ...fields }; for (const handler of this.listeners.get(type) || []) await handler(event); return event; },
      closest(query) { const key = query.replace(/^\[data-|-?\]$/g, "").replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()); return key in this.dataset ? this : null; },
      focus() {},
      reset() { for (const key of ["title", "cue", "minimum", "start"]) get(`#habit-${key}`).value = ""; weekdays.forEach((day) => { day.checked = true; }); },
      get innerHTML() { return html; },
      set innerHTML(value) {
        html = value;
        if (selector !== "#habit-list") return;
        for (const key of dynamic) nodes.delete(key);
        dynamic.clear();
        for (const match of value.matchAll(/<(input|textarea|button|p)\b([^>]*\bid="([^"]+)"[^>]*)>(?:([^<]*)<\/textarea>)?/g)) {
          const [, tag, attrs, id, contents] = match;
          const data = {};
          for (const attribute of attrs.matchAll(/data-([a-z-]+)="([^"]*)"/g)) data[attribute[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = unescapeHtml(attribute[2]);
          const input = element(`#${id}`, data);
          input.value = unescapeHtml(tag === "textarea" ? contents || "" : attrs.match(/\bvalue="([^"]*)"/)?.[1] || "");
          dynamic.add(`#${id}`);
        }
      },
    };
    nodes.set(selector, node);
    return node;
  }
  for (const id of ["fields", "create", "date", "filter", "refresh", "support", "list", "title", "cue", "minimum", "start", "form", "create-feedback"]) element(`#habit-${id}`);
  element("#habits-status");
  const document = {
    querySelector: get,
    querySelectorAll(query) {
      if (query === "[name=habit-weekday]:checked") return weekdays.filter((day) => day.checked);
      const key = { "[data-habit-edit]": "habitEdit", "[data-habit-note]": "habitNote", "[data-habit-action]": "habitAction" }[query];
      return key ? [...nodes.values()].filter((node) => key in node.dataset) : [];
    },
    addEventListener(type, callback) { documentListeners.set(type, callback); },
  };
  const habit = { id: 4, title: "读一页", cue: "洗漱后", minimum_action: "一页", start_date: "2026-09-27", weekdays: [0, 1, 2, 3, 4, 5, 6], status: "active", revision: 2 };
  const notes = new Map();
  function defaultResponse(url, options = {}) {
    if (url === "/api/habits") return options.method === "POST" ? structuredClone(habit) : [structuredClone(habit)];
    if (url === "/api/habits/4") {
      if (options.method === "PATCH") Object.assign(habit, JSON.parse(options.body), { revision: habit.revision + 1 });
      return structuredClone(habit);
    }
    if (url.includes("/summary?")) {
      const day = url.split("to_date=")[1];
      return { entries: [{ date: day, scheduled: true, status: "unrecorded", note: notes.get(day) || "", revision: notes.has(day) ? 1 : 0 }], done: 0, skipped: 0, unrecorded: 1, completion_rate: 0 };
    }
    return {};
  }
  let respond = defaultResponse;
  const core = {
    $: get, state, escapeHtml, jsonBody: (method, body) => ({ method, body: JSON.stringify(body) }),
    api: async (url, options = {}) => { requests.push({ url, options }); return respond(url, options); },
    renderState: (node, type, message) => { node.innerHTML = `<p>${message}</p>`; },
    setStatus: (selector, message) => { const node = typeof selector === "string" ? get(selector) : selector; if (node) node.textContent = message; },
    confirmAction: async (options) => { confirmations.push(options); return decisions.shift() ?? false; },
    changed: () => { updated += 1; },
    toast: () => {},
  };
  const context = vm.createContext({ Date: Clock, Intl, document, window: { addEventListener(type, callback) { windowListeners.set(type, callback); } } });
  const modules = new Map();
  const pending = new Map();
  const coreModule = new vm.SyntheticModule(Object.keys(core), function () { for (const [key, value] of Object.entries(core)) this.setExport(key, value); }, { context });
  modules.set(path.join(frontend, "core.js"), coreModule);
  function load(filename) {
    if (modules.has(filename)) return modules.get(filename);
    if (pending.has(filename)) return pending.get(filename);
    const promise = (async () => {
      const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context, identifier: filename });
      await module.link((specifier, parent) => load(path.resolve(path.dirname(parent.identifier), specifier)));
      modules.set(filename, module);
      pending.delete(filename);
      return module;
    })();
    pending.set(filename, promise);
    return promise;
  }
  const module = await load(path.join(frontend, "habits.js")); await module.evaluate();
  const habits = module.namespace;
  habits.initHabits(); get("#habit-filter").value = "active";
  const click = (action, id = "4") => get("#habit-list").emit("click", { target: element(`button:${action}`, { habitAction: action, habitId: id }) });
  const input = async (selector, value) => { const node = get(selector); assert.ok(node, `${selector} is rendered`); node.value = value; await get("#habit-list").emit("input", { target: node }); };
  const submit = () => get("#habit-list").emit("submit", { target: element("form:editor", { habitEditor: "" }) });
  const unload = () => { const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }; windowListeners.get("beforeunload")(event); return event; };
  return { get, habits, habit, notes, requests, confirmations, decisions, weekdays, click, input, submit, unload, defaultResponse, updated: () => updated, setResponse(callback) { respond = callback; } };
}

test("habit editor saves only editable fields with original revision and leaves schedule and archive status intact", async () => {
  const h = await harness(); h.habit.status = "archived"; h.get("#habit-filter").value = "archived";
  await h.habits.loadHabits(); await h.click("edit");
  assert.match(h.get("#habit-list").innerHTML, /已归档，保存修改后仍保持归档/);
  await h.input("#habit-edit-title", "睡前阅读"); await h.input("#habit-edit-cue", ""); await h.input("#habit-edit-minimum_action", "两段");
  await h.submit();
  assert.deepEqual(JSON.parse(h.requests.find((item) => item.options.method === "PATCH").options.body), { revision: 2, title: "睡前阅读", cue: "", minimum_action: "两段" });
  assert.equal(h.habit.status, "archived"); assert.equal(h.habit.start_date, "2026-09-27"); assert.equal(h.habit.weekdays.length, 7);
  assert.equal(h.get("#habit-edit-title"), null); assert.match(h.get("#habits-status").textContent, /已保存/); assert.equal(h.updated(), 1);
  assert.equal(h.unload().defaultPrevented, false);
});

test("opening and cancelling unchanged editor or restoring unchanged note does not prompt", async () => {
  const h = await harness(); h.notes.set(today, "原来的备注"); await h.habits.loadHabits();
  await h.click("edit"); assert.equal(await h.habits.beforeLeaveHabits(), true); assert.equal(h.unload().defaultPrevented, false);
  await h.click("cancel-edit"); assert.equal(h.confirmations.length, 0);
  await h.input("#habit-note-4", "修改一下"); await h.input("#habit-note-4", "原来的备注");
  assert.equal(await h.habits.beforeLeaveHabits(), true); assert.equal(h.confirmations.length, 0); assert.equal(h.unload().defaultPrevented, false);
});

test("dirty editor cancellation requires confirmation and only discards the editor after acceptance", async () => {
  const h = await harness(); await h.habits.loadHabits(); await h.click("edit"); await h.input("#habit-edit-title", "保留修改");
  await h.click("cancel-edit"); assert.equal(h.get("#habit-edit-title").value, "保留修改");
  h.decisions.push(true); await h.click("cancel-edit");
  assert.equal(h.get("#habit-edit-title"), null); assert.equal(h.confirmations.length, 2); assert.equal(h.requests.some((item) => item.options.method), false);
});

test("editor network failure keeps values and permits an explicit retry", async () => {
  const h = await harness(); await h.habits.loadHabits(); await h.click("edit"); await h.input("#habit-edit-title", "保留修改");
  h.setResponse((url, options) => { if (options.method === "PATCH") throw new Error("断网"); return h.defaultResponse(url, options); });
  await h.submit(); assert.equal(h.get("#habit-edit-title").value, "保留修改"); assert.match(h.get("#habit-edit-feedback").textContent, /保留.*重试/); assert.equal(h.get("#habit-edit-save").disabled, false);
  h.setResponse(h.defaultResponse); await h.submit(); assert.equal(h.habit.title, "保留修改");
});

test("409 blocks blind editor retries even after list refresh; explicit reload cancellation preserves draft", async () => {
  const h = await harness(); await h.habits.loadHabits(); await h.click("edit"); await h.input("#habit-edit-title", "冲突草稿");
  h.setResponse((url, options) => { if (options.method === "PATCH") throw Object.assign(new Error("版本冲突"), { status: 409 }); return h.defaultResponse(url, options); });
  await h.submit(); await h.habits.loadHabits(); await h.submit();
  assert.equal(h.requests.filter((item) => item.options.method === "PATCH").length, 1); assert.equal(h.get("#habit-edit-save").disabled, true);
  await h.click("reload-edit"); assert.equal(h.get("#habit-edit-title").value, "冲突草稿"); assert.equal(h.requests.some((item) => item.url === "/api/habits/4" && !item.options.method), false);
  h.habit.title = "另一页的修改"; h.habit.revision = 7; h.decisions.push(true); await h.click("reload-edit");
  assert.equal(h.get("#habit-edit-title").value, "另一页的修改"); assert.equal(h.get("#habit-edit-save").disabled, false);
  h.setResponse(h.defaultResponse); await h.input("#habit-edit-title", "核对后修改"); await h.submit();
  assert.equal(JSON.parse(h.requests.filter((item) => item.options.method === "PATCH").at(-1).options.body).revision, 7);
});

test("failed explicit reload preserves draft and conflict protection", async () => {
  const h = await harness(); await h.habits.loadHabits(); await h.click("edit"); await h.input("#habit-edit-title", "冲突草稿");
  h.setResponse((url, options) => { throw Object.assign(new Error("读取失败"), { status: options.method === "PATCH" ? 409 : 500 }); });
  await h.submit(); h.decisions.push(true); await h.click("reload-edit");
  assert.equal(h.get("#habit-edit-title").value, "冲突草稿"); assert.equal(h.get("#habit-edit-save").disabled, true); assert.match(h.get("#habit-edit-feedback").textContent, /版本仍保留/);
});

test("date/filter/list refreshes preserve editor snapshot and day-specific note drafts", async () => {
  const h = await harness(); await h.habits.loadHabits(); await h.click("edit"); await h.input("#habit-edit-title", "未保存名称"); await h.input("#habit-note-4", "今天的备注草稿");
  h.habit.title = "其他页面保存"; h.habit.revision = 8;
  h.get("#habit-date").value = "2026-10-02"; await h.get("#habit-date").emit("change");
  assert.equal(h.get("#habit-edit-title").value, "未保存名称"); assert.equal(h.get("#habit-note-4").value, "");
  await h.input("#habit-note-4", "昨天的备注草稿"); h.get("#habit-filter").value = "archived"; await h.get("#habit-filter").emit("change");
  assert.equal(h.get("#habit-edit-title").value, "未保存名称");
  h.get("#habit-filter").value = "active"; await h.get("#habit-filter").emit("change");
  h.get("#habit-date").value = today; await h.get("#habit-date").emit("change");
  assert.equal(h.get("#habit-note-4").value, "今天的备注草稿"); await h.submit();
  assert.equal(JSON.parse(h.requests.find((item) => item.options.method === "PATCH").options.body).revision, 2);
  assert.equal(h.get("#habit-note-4").value, "今天的备注草稿");
});

test("leaving confirms unsaved data but preserves it for returning to the page", async () => {
  const h = await harness(); await h.habits.loadHabits(); await h.click("edit"); await h.input("#habit-edit-title", "下次继续");
  assert.equal(await h.habits.beforeLeaveHabits(), false); assert.equal(h.unload().defaultPrevented, true);
  h.decisions.push(true); assert.equal(await h.habits.beforeLeaveHabits(), true); await h.habits.loadHabits();
  assert.equal(h.get("#habit-edit-title").value, "下次继续"); assert.match(h.confirmations.at(-1).message, /保留草稿/);
});

test("create schedule-only changes and note-only changes are guarded without treating default inputs as dirty", async () => {
  const h = await harness(); await h.habits.loadHabits(); assert.equal(await h.habits.beforeLeaveHabits(), true);
  h.weekdays[0].checked = false; assert.equal(await h.habits.beforeLeaveHabits(), false); assert.equal(h.unload().defaultPrevented, true);
  h.weekdays[0].checked = true; assert.equal(await h.habits.beforeLeaveHabits(), true);
  await h.input("#habit-note-4", "未保存备注"); assert.equal(await h.habits.beforeLeaveHabits(), false);
  await h.input("#habit-note-4", ""); assert.equal(await h.habits.beforeLeaveHabits(), true);
});

test("pending editor writes block navigation and duplicate submit without prompting", async () => {
  const h = await harness(); await h.habits.loadHabits(); await h.click("edit"); await h.input("#habit-edit-title", "保存中");
  let resolve;
  h.setResponse((url, options) => options.method === "PATCH" ? new Promise((done) => { resolve = () => done(h.defaultResponse(url, options)); }) : h.defaultResponse(url, options));
  const pending = h.submit(); await h.submit();
  assert.equal(await h.habits.beforeLeaveHabits(), false); assert.equal(h.confirmations.length, 0); assert.equal(h.unload().defaultPrevented, true);
  assert.equal(h.requests.filter((item) => item.options.method === "PATCH").length, 1);
  resolve(); await pending; assert.equal(await h.habits.beforeLeaveHabits(), true);
});

test("opening a second editor requires explicit draft discard and personal input stays escaped", async () => {
  const h = await harness();
  const second = { ...h.habit, id: 5, title: '<img src=x onerror="bad()">' };
  h.setResponse((url, options) => url === "/api/habits" ? [structuredClone(h.habit), second] : h.defaultResponse(url, options));
  await h.habits.loadHabits(); await h.click("edit"); await h.input("#habit-edit-title", "第一份草稿");
  await h.click("edit", "5"); assert.equal(h.get("#habit-edit-title").value, "第一份草稿");
  h.decisions.push(true); await h.click("edit", "5");
  assert.equal(h.get("#habit-edit-title").value, second.title); assert.ok(!h.get("#habit-list").innerHTML.includes("<img"));
  assert.match(h.get("#habit-list").innerHTML, /&lt;img/); assert.equal((h.get("#habit-list").innerHTML.match(/data-habit-editor/g) || []).length, 1);
});

test("habit cards lazy-load a read-only 30-day grid, cache it, and collapse cleanly", async () => {
  const h = await harness();
  await h.habits.loadHabits();
  const collapsed = h.get("#habit-list").innerHTML;
  assert.match(collapsed, /data-habit-grid="4"/);
  assert.match(collapsed, /近 30 天网格/);
  assert.equal(collapsed.includes("habit-grid-cell"), false);
  const summaryCalls = () => h.requests.filter(({ url }) => url.includes("/summary?")).length;
  assert.equal(summaryCalls(), 1); // only the initial seven-day summary

  h.setResponse((url, options = {}) => {
    if (url.includes("/summary?")) {
      const from = url.split("from_date=")[1].split("&")[0];
      const to = url.split("to_date=")[1];
      const entries = [];
      let cursor = new Date(`${from}T00:00:00Z`);
      const end = new Date(`${to}T00:00:00Z`);
      while (cursor <= end) {
        const day = cursor.toISOString().slice(0, 10);
        entries.push({ date: day, scheduled: cursor.getUTCDay() !== 0, status: day === to ? "done" : "unrecorded", note: "", revision: 0 });
        cursor = new Date(cursor.getTime() + 86400000);
      }
      return { entries, done: 1, skipped: 0, unrecorded: entries.length - 1, completion_rate: 1 };
    }
    return h.defaultResponse(url, options);
  });
  const gridButton = { dataset: { habitGrid: "4" }, closest: (query) => query === "[data-habit-grid]" ? gridButton : null };
  await h.get("#habit-list").emit("click", { target: gridButton });
  const gridRequest = h.requests.filter(({ url }) => url.includes("from_date=2026-09-04&to_date=2026-10-03"));
  assert.equal(gridRequest.length, 1);
  assert.equal(gridRequest[0].options.method, undefined);
  const html = h.get("#habit-list").innerHTML;
  assert.match(html, /收起近 30 天/);
  assert.match(html, /aria-expanded="true"/);
  assert.equal((html.match(/habit-grid-cell /g) || []).length, 35); // 4 leading blanks + 30 days + 1 trailing blank
  assert.match(html, /aria-label="2026-10-03 已完成"/);
  assert.match(html, /空白不等于失败/);

  // Collapse, then reopen from cache without another request.
  await h.get("#habit-list").emit("click", { target: gridButton });
  assert.equal(h.get("#habit-list").innerHTML.includes("habit-grid-cell"), false);
  assert.match(h.get("#habit-list").innerHTML, /近 30 天网格/);
  await h.get("#habit-list").emit("click", { target: gridButton });
  assert.equal(summaryCalls(), 2); // the cached grid never re-fetches
  assert.equal((h.get("#habit-list").innerHTML.match(/habit-grid-cell /g) || []).length, 35);
  await h.get("#habit-list").emit("click", { target: gridButton });
  assert.equal(h.get("#habit-list").innerHTML.includes("habit-grid-cell"), false);
  assert.equal(h.requests.every(({ options }) => (options.method || "GET") === "GET"), true);
});
