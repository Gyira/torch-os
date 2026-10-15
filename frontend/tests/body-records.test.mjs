import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline module/event integration for the body-record panel on the journal
// page. Layout and real rendering are verified separately in the browser.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

class NodeDouble {
  constructor(id = "") {
    this.id = id;
    this.listeners = new Map();
    this.dataset = {};
    this.attributes = {};
    this.value = "";
    this.disabled = false;
    this.hidden = false;
    this.isConnected = true;
    this.open = false;
    this.textContent = "";
    this.innerHTML = "";
    this.className = "";
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(callback);
  }
  async emit(type, values = {}) {
    const event = { target: this, currentTarget: this, preventDefault() { this.defaultPrevented = true; }, ...values };
    for (const callback of this.listeners.get(type) || []) await callback(event);
    return event;
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
  showModal() { this.open = true; this.modalCount = (this.modalCount || 0) + 1; }
  close(value = "") { this.returnValue = value; this.open = false; }
}

async function harness({ capabilities = { body_records: true }, bodyRecords = null, bodyStatus = 200, lateConfig = false } = {}) {
  const nodes = new Map();
  const documentListeners = new Map();
  const windowListeners = new Map();
  const dispatched = [];
  const getNode = (selector) => {
    if (!nodes.has(selector)) {
      const node = new NodeDouble(selector);
      node.querySelector = getNode;
      node.querySelectorAll = () => [];
      node.focus = () => { node.focused = true; document.activeElement = node; };
      nodes.set(selector, node);
    }
    return nodes.get(selector);
  };
  const document = {
    activeElement: null,
    querySelector: getNode,
    querySelectorAll: () => [],
    addEventListener(type, callback) {
      if (!documentListeners.has(type)) documentListeners.set(type, []);
      documentListeners.get(type).push(callback);
    },
    dispatchEvent(event) { dispatched.push(event?.type); for (const callback of documentListeners.get(event?.type) || []) callback(event); return true; },
  };
  const requests = [];
  const location = { hash: "#journal", assign(url) { this.assigned = url; } };
  const bodyByDate = new Map();
  const unrecorded = (day) => ({ date: day, recorded: false, weight_kg: null, sleep_minutes: null, exercise_minutes: null, revision: 0, updated_at: null });
  const respond = (url, options = {}) => {
    if (url.startsWith("/api/body/entries") && url.includes("?")) {
      const params = new URLSearchParams(url.split("?")[1]);
      const from = params.get("from_date");
      const to = params.get("to_date");
      const entries = [...bodyByDate.values()]
        .filter((row) => row.recorded && row.date >= from && row.date <= to)
        .sort((a, b) => a.date.localeCompare(b.date));
      return { from_date: from, to_date: to, entries, warning: "只列出有记录的日期；未记录不代表当天没有发生。" };
    }
    if (url.startsWith("/api/body/entries")) {
      if (bodyStatus !== 200) return {};
      const day = url.split("/").pop();
      if (options.method === "PUT") {
        const payload = JSON.parse(options.body);
        const recorded = !(payload.weight_kg == null && payload.sleep_minutes == null && payload.exercise_minutes == null);
        const value = recorded
          ? { date: day, recorded: true, weight_kg: payload.weight_kg, sleep_minutes: payload.sleep_minutes, exercise_minutes: payload.exercise_minutes, revision: payload.revision + 1, updated_at: "2026-10-04T04:00:00+00:00" }
          : unrecorded(day);
        bodyByDate.set(day, value);
        return structuredClone(value);
      }
      return structuredClone(bodyByDate.get(day) || unrecorded(day));
    }
    if (/^\/api\/journal\/[^?]+$/.test(url)) {
      const day = url.slice("/api/journal/".length);
      return { date: day, content: "", highlights: "", obstacles: "", next_action: "", goal_id: null, revision: 0, recorded: false, created_at: null, updated_at: null };
    }
    return [];
  };
  const context = vm.createContext({
    document, AbortController, TypeError, Intl, Date, console,
    CustomEvent: class { constructor(type) { this.type = type; } },
    location,
    history: { pushState(_state, _title, hash) { location.hash = hash; }, replaceState(_state, _title, hash) { location.hash = hash; } },
    window: {
      setTimeout: () => 1, clearTimeout() {},
      addEventListener(type, callback) { windowListeners.set(type, callback); },
      scrollTo() {},
      confirm() { throw new Error("Native confirmation dialogs are forbidden in UI behavior tests"); },
    },
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (bodyStatus !== 200 && String(url).startsWith("/api/body/entries")) {
        return { ok: false, status: bodyStatus, json: async () => ({ detail: "身体记录测试失败" }) };
      }
      return { ok: true, status: 200, json: async () => respond(String(url), options) };
    },
  });
  const modules = new Map();
  const pending = new Map();
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
  const core = (await load("core.js")).namespace;
  core.state.config = lateConfig ? null : { capabilities };
  const model = (await load("journal-model.js")).namespace;
  const journal = (await load("journal.js")).namespace;
  const day = model.shanghaiDay();
  const previous = model.shiftDay(day, -1);
  if (bodyRecords) {
    const value = { date: day, recorded: true, weight_kg: null, sleep_minutes: null, exercise_minutes: null, revision: 1, updated_at: "2026-10-04T03:00:00+00:00", ...bodyRecords };
    bodyByDate.set(day, value);
  }
  journal.initJournal();
  const body = (await load("body-records.js")).namespace;
  await body.loadBodyRecord(day);
  return {
    core, journal, body, model, getNode, requests, dispatched, day, previous, context, document, location,
    windowListeners,
    bodyPuts: () => requests.filter(({ url, options }) => String(url).startsWith("/api/body/entries") && options?.method === "PUT"),
    bodyGets: () => requests.filter(({ url, options }) => String(url).startsWith("/api/body/entries") && !String(url).includes("?") && options?.method === undefined),
    bodyRangeGets: () => requests.filter(({ url, options }) => String(url).startsWith("/api/body/entries?") && options?.method === undefined),
  };
}

test("an older service disables the panel and sends no body request", async () => {
  const h = await harness({ capabilities: {} });
  assert.equal(h.getNode("#body-fields").disabled, true);
  assert.equal(h.getNode("#body-save").disabled, true);
  assert.equal(h.getNode("#body-clear").disabled, true);
  assert.match(h.getNode("#body-state").textContent, /未提供身体记录/);
  assert.equal(h.requests.some(({ url }) => String(url).startsWith("/api/body/entries")), false);
});

test("a capability that arrives late enables the panel and loads the selected day once", async () => {
  const h = await harness({ lateConfig: true });
  assert.equal(h.getNode("#body-fields").disabled, true);
  assert.match(h.getNode("#body-state").textContent, /正在检查服务能力/);
  assert.equal(h.bodyGets().length, 0);

  h.core.state.config = { capabilities: { body_records: true } };
  h.document.dispatchEvent({ type: "config-changed" });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(h.getNode("#body-fields").disabled, false);
  assert.match(h.getNode("#body-state").textContent, /尚未记录/);
  assert.equal(h.bodyGets().length, 1);

  h.document.dispatchEvent({ type: "config-changed" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.bodyGets().length, 1, "an already loaded day is never refetched or clobbered");
});

test("an unrecorded day loads empty and only changed input enables saving", async () => {
  const h = await harness();
  assert.equal(h.getNode("#body-fields").disabled, false);
  assert.equal(h.getNode("#body-weight").value, "");
  assert.match(h.getNode("#body-state").textContent, /尚未记录/);
  assert.equal(h.getNode("#body-save").disabled, true);
  assert.ok(h.requests.some(({ url }) => url === `/api/body/entries/${h.day}`));

  h.getNode("#body-weight").value = "68.5";
  await h.getNode("#body-form").emit("input");
  assert.equal(h.getNode("#body-save").disabled, false);
  await h.getNode("#body-form").emit("submit");

  const puts = h.bodyPuts();
  assert.equal(puts.length, 1);
  assert.deepEqual(JSON.parse(puts[0].options.body), { weight_kg: 68.5, sleep_minutes: null, exercise_minutes: null, revision: 0 });
  assert.match(h.getNode("#body-feedback").textContent, /已保存到本地/);
  assert.equal(h.getNode("#body-save").disabled, true);
  assert.equal(h.dispatched.includes("data-changed"), true);
  assert.equal(h.getNode("#body-clear").disabled, false);
});

test("the recent view lists saved days in plain facts and refreshes after each save", async () => {
  const h = await harness();
  assert.match(h.getNode("#body-history").innerHTML, /最近 30 天还没有身体记录/);
  assert.ok(h.requests.some(({ url }) => String(url).startsWith("/api/body/entries?from_date=")));

  h.getNode("#body-weight").value = "68.5";
  h.getNode("#body-sleep").value = "450";
  await h.getNode("#body-form").emit("input");
  await h.getNode("#body-form").emit("submit");
  await new Promise((resolve) => setImmediate(resolve));

  const html = h.getNode("#body-history").innerHTML;
  assert.match(html, /体重 68\.5 kg · 睡眠 7 小时 30 分/);
  assert.match(html, new RegExp(h.day));
  assert.doesNotMatch(html, /健康|正常|异常/);
  assert.match(html, /最近 30 天：1 天有记录（体重 1 · 睡眠 1 · 运动 0）/);
});

test("body export buttons follow the capability and never navigate on an older service", async () => {
  const enabled = await harness({ capabilities: { body_records: true, body_export: true } });
  assert.equal(enabled.getNode("#body-export-csv").disabled, false);
  assert.equal(enabled.getNode("#body-export-json").disabled, false);
  await enabled.getNode("#body-export-csv").emit("click");
  assert.equal(enabled.location.assigned, "/api/body/export.csv");
  await enabled.getNode("#body-export-json").emit("click");
  assert.equal(enabled.location.assigned, "/api/body/export.json");

  const older = await harness();
  assert.equal(older.getNode("#body-export-csv").disabled, true);
  assert.equal(older.getNode("#body-export-json").disabled, true);
  await older.getNode("#body-export-csv").emit("click");
  assert.equal(older.location.assigned, undefined);
});

test("body summary export follows its capability, period select and older-service fallback", async () => {
  const enabled = await harness({ capabilities: { body_records: true, body_summary: true } });
  assert.equal(enabled.getNode("#body-summary-md").disabled, false);
  assert.equal(enabled.getNode("#body-summary-json").disabled, false);
  assert.equal(enabled.getNode("#body-summary-period").disabled, false);
  await enabled.getNode("#body-summary-json").emit("click");
  assert.equal(enabled.location.assigned, "/api/body/summary.json?period=month");
  enabled.getNode("#body-summary-period").value = "week";
  await enabled.getNode("#body-summary-md").emit("click");
  assert.equal(enabled.location.assigned, "/api/body/summary.md?period=week");
  assert.equal(enabled.requests.every(({ options }) => (options?.method || "GET") === "GET"), true);

  const older = await harness({ capabilities: { body_records: true, body_export: true } });
  assert.equal(older.getNode("#body-summary-md").disabled, true);
  assert.equal(older.getNode("#body-summary-json").disabled, true);
  assert.equal(older.getNode("#body-summary-period").disabled, true);
  await older.getNode("#body-summary-json").emit("click");
  assert.equal(older.location.assigned, undefined);
});

test("a recorded day pre-fills values and keeps untouched fields on save", async () => {
  const h = await harness({ bodyRecords: { weight_kg: 70.0, sleep_minutes: 420, revision: 3 } });
  assert.equal(h.getNode("#body-weight").value, "70");
  assert.equal(h.getNode("#body-sleep").value, "420");
  assert.equal(h.getNode("#body-exercise").value, "");
  assert.equal(h.getNode("#body-save").disabled, true);

  h.getNode("#body-sleep").value = "450";
  await h.getNode("#body-form").emit("input");
  await h.getNode("#body-form").emit("submit");
  assert.deepEqual(JSON.parse(h.bodyPuts()[0].options.body), { weight_kg: 70, sleep_minutes: 450, exercise_minutes: null, revision: 3 });
  assert.equal(h.getNode("#body-sleep").value, "450");
});

test("a revision conflict keeps the draft and blocks blind resubmission", async () => {
  const h = await harness({ bodyRecords: { weight_kg: 70.0, revision: 3 } });
  h.context.fetch = async (url, options) => {
    h.requests.push({ url, options });
    return { ok: false, status: 409, json: async () => ({ detail: "身体记录已发生变化，请保留草稿并刷新后重试" }) };
  };
  h.getNode("#body-weight").value = "71";
  await h.getNode("#body-form").emit("input");
  await h.getNode("#body-form").emit("submit");
  assert.equal(h.getNode("#body-weight").value, "71");
  assert.equal(h.getNode("#body-save").disabled, true);
  assert.match(h.getNode("#body-feedback").textContent, /重新读取已保存版本/);
  await h.getNode("#body-form").emit("submit");
  assert.equal(h.bodyPuts().length, 1);
});

test("clearing requires explicit confirmation and sends explicit nulls", async () => {
  const h = await harness({ bodyRecords: { weight_kg: 70.0, sleep_minutes: 420, revision: 3 } });
  const cancelled = h.getNode("#body-clear").emit("click");
  await h.getNode("#confirm-cancel").emit("click");
  await cancelled;
  assert.equal(h.bodyPuts().length, 0);

  const clearing = h.getNode("#body-clear").emit("click");
  await h.getNode("#confirm-accept").emit("click");
  await clearing;
  assert.deepEqual(JSON.parse(h.bodyPuts()[0].options.body), { weight_kg: null, sleep_minutes: null, exercise_minutes: null, revision: 3 });
  assert.match(h.getNode("#body-state").textContent, /尚未记录/);
  assert.equal(h.getNode("#body-clear").disabled, true);
  assert.match(h.getNode("#body-feedback").textContent, /已清除/);
});

test("unsaved body edits warn on page leave and survive a journal day change untouched", async () => {
  const h = await harness();
  const unload = h.windowListeners.get("beforeunload");
  const clean = { preventDefault() { this.prevented = true; } };
  unload(clean);
  assert.equal(clean.prevented, undefined);

  h.getNode("#body-weight").value = "68.5";
  await h.getNode("#body-form").emit("input");
  const dirty = { preventDefault() { this.prevented = true; } };
  unload(dirty);
  assert.equal(dirty.prevented, true);

  // 日记换天（工作台 2.0 的 journal-day-changed）：有未保存草稿时静默不覆盖，
  // 也不去请求那一天的记录。
  h.document.dispatchEvent({ type: "journal-day-changed", detail: { day: h.previous } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.getNode("#body-weight").value, "68.5");
  assert.equal(h.requests.some(({ url }) => url === `/api/body/entries/${h.previous}`), false);

  // 草稿清掉之后，换天会正常加载那一天的记录。
  h.getNode("#body-weight").value = "";
  await h.getNode("#body-form").emit("input");
  h.document.dispatchEvent({ type: "journal-day-changed", detail: { day: h.previous } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(h.requests.some(({ url }) => url === `/api/body/entries/${h.previous}`));
});

test("a body fetch failure leaves the journal editable and explains the body panel state", async () => {
  const h = await harness({ bodyStatus: 500 });
  assert.equal(h.getNode("#journal-fields").disabled, false);
  assert.match(h.getNode("#body-feedback").textContent, /无法读取/);
  assert.equal(h.getNode("#body-fields").disabled, true);
  assert.equal(h.getNode("#body-save").disabled, true);
});
