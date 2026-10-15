import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the local scheduler's settings panel（设置 → 定时提醒）。
// 工作台上的“提醒与草稿”列表面板 2026-10-05 按用户要求删除，相关用例一并移除；
// 提醒以后以铃铛推送的形式回来。No network, no model.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function harness({ supported = true, weekly = true, runtime = { last_check_at: "2026-10-04T08:00:00+08:00", consecutive_failures: 0, last_error_at: null } } = {}) {
  const nodes = new Map();
  const documentListeners = new Map();
  const requests = [];
  const capabilities = supported ? (weekly ? { scheduler: true, scheduler_weekly: true } : { scheduler: true }) : {};
  const state = { view: "workbench", config: { capabilities } };
  function element(selector, dataset = {}) {
    const node = {
      id: selector, value: "", checked: false, disabled: false, hidden: false, dataset, textContent: "", innerHTML: "", listeners: new Map(),
      addEventListener(type, handler) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(handler); },
      async emit(type, fields = {}) { const event = { target: this, currentTarget: this, preventDefault() { this.defaultPrevented = true; }, ...fields }; for (const handler of this.listeners.get(type) || []) await handler(event); return event; },
      querySelector(selector) { return selector.includes("submit") ? this.submitButton : null; },
    };
    node.submitButton = { disabled: false };
    nodes.set(selector, node);
    return node;
  }
  for (const id of ["schedule-status", "schedule-form", "schedule-morning-enabled", "schedule-morning-time", "schedule-evening-enabled", "schedule-evening-time", "schedule-weekly-enabled", "schedule-weekly-day", "schedule-weekly-time", "schedule-weekly-help", "schedule-feedback", "schedule-run-morning", "schedule-run-evening", "schedule-run-weekly"]) element(`#${id}`);
  const document = {
    querySelector: (selector) => nodes.get(selector) || null,
    querySelectorAll: () => [],
    addEventListener(type, handler) { documentListeners.set(type, handler); },
    dispatchEvent() {},
  };
  let respond = (url, options) => {
    if (url === "/api/schedule" && (!options || !options.method)) return { settings: { morning_enabled: true, morning_time: "08:00", evening_enabled: true, evening_time: "21:30", weekly_enabled: true, weekly_day: 6, weekly_time: "20:00" }, today_runs: [], runtime };
    if (url.endsWith("/run-now")) return { created: true, item: { id: 1, kind: "morning" } };
    if (url === "/api/schedule") return { morning_enabled: true, morning_time: "08:00", evening_enabled: true, evening_time: "21:30", weekly_enabled: true, weekly_day: 6, weekly_time: "20:00" };
    return {};
  };
  const core = {
    $: (selector) => nodes.get(selector) || null,
    state,
    jsonBody: (method, body) => ({ method, body: JSON.stringify(body) }),
    setStatus: (selector, message) => { const node = nodes.get(selector); if (node) node.textContent = message; },
    api: async (url, options = {}) => { requests.push({ url, options }); return respond(url, options); },
  };
  const context = vm.createContext({ document, window: { addEventListener() {} }, Intl, Date, console, CustomEvent: class {} });
  const coreModule = new vm.SyntheticModule(Object.keys(core), function () { for (const [key, value] of Object.entries(core)) this.setExport(key, value); }, { context });
  const modules = new Map([[path.join(frontend, "core.js"), coreModule]]);
  const pending = new Map();
  async function load(filename) {
    if (modules.has(filename)) return modules.get(filename);
    if (pending.has(filename)) return pending.get(filename);
    const promise = (async () => {
      const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context, identifier: filename });
      await module.link((specifier, parent) => load(path.resolve(path.dirname(parent.identifier), specifier)));
      await module.evaluate();
      modules.set(filename, module);
      pending.delete(filename);
      return module;
    })();
    pending.set(filename, promise);
    return promise;
  }
  const inboxModule = (await load(path.join(frontend, "inbox.js"))).namespace;
  inboxModule.initInbox();
  return { inboxModule, get: (selector) => nodes.get(selector), requests, documentListeners, state, setResponse(callback) { respond = callback; } };
}

test("unsupported service shows the notice and issues no schedule requests", async () => {
  const h = await harness({ supported: false });
  await h.inboxModule.loadSchedulePanel();
  assert.equal(h.requests.length, 0);
  assert.match(h.get("#schedule-status").textContent, /尚未支持|正在检查服务能力/);
});

test("schedule save validates locally, sends booleans, and surfaces the result", async () => {
  const h = await harness();
  await h.inboxModule.loadSchedulePanel();
  assert.equal(h.get("#schedule-morning-time").value, "08:00");
  h.get("#schedule-morning-time").value = "8:00";
  await h.get("#schedule-form").emit("submit");
  assert.equal(h.requests.filter(({ url, options }) => url === "/api/schedule" && options?.method === "PUT").length, 0);
  assert.match(h.get("#schedule-feedback").textContent, /HH:MM/);
  h.get("#schedule-morning-enabled").checked = false;
  h.get("#schedule-morning-time").value = "07:30";
  h.get("#schedule-evening-time").value = "22:00";
  await h.get("#schedule-form").emit("submit");
  const put = h.requests.find(({ options }) => options?.method === "PUT");
  assert.deepEqual(JSON.parse(put.options.body), { morning_enabled: false, morning_time: "07:30", evening_enabled: true, evening_time: "22:00", weekly_enabled: true, weekly_day: 6, weekly_time: "20:00" });
  assert.match(h.get("#schedule-feedback").textContent, /已保存/);
});

test("the settings panel saves the weekly schedule", async () => {
  const h = await harness();
  await h.inboxModule.loadSchedulePanel();
  assert.equal(h.get("#schedule-weekly-day").value, "6");
  assert.equal(h.get("#schedule-weekly-time").value, "20:00");
  h.get("#schedule-weekly-enabled").checked = false;
  h.get("#schedule-weekly-day").value = "2";
  h.get("#schedule-weekly-time").value = "19:15";
  await h.get("#schedule-form").emit("submit");
  const put = h.requests.find(({ options }) => options?.method === "PUT");
  assert.deepEqual(JSON.parse(put.options.body), { morning_enabled: true, morning_time: "08:00", evening_enabled: true, evening_time: "21:30", weekly_enabled: false, weekly_day: 2, weekly_time: "19:15" });
});

test("an older service hides the weekly controls and never sends weekly fields", async () => {
  const h = await harness({ weekly: false });
  await h.inboxModule.loadSchedulePanel();
  assert.equal(h.get("#schedule-weekly-enabled").disabled, true);
  assert.equal(h.get("#schedule-weekly-day").disabled, true);
  assert.equal(h.get("#schedule-weekly-time").disabled, true);
  assert.equal(h.get("#schedule-run-weekly").disabled, true);
  assert.match(h.get("#schedule-weekly-help").textContent, /未提供周报/);

  h.get("#schedule-morning-time").value = "07:45";
  await h.get("#schedule-form").emit("submit");
  const put = h.requests.find(({ options }) => options?.method === "PUT");
  assert.deepEqual(Object.keys(JSON.parse(put.options.body)).sort(), ["evening_enabled", "evening_time", "morning_enabled", "morning_time"]);

  const before = h.requests.length;
  await h.get("#schedule-run-weekly").emit("click");
  assert.equal(h.requests.length, before);
});

test("run-now posts the kind and reports the result", async () => {
  const h = await harness();
  await h.get("#schedule-run-morning").emit("click");
  const run = h.requests.find(({ url }) => url === "/api/schedule/run-now");
  assert.deepEqual(JSON.parse(run.options.body), { kind: "morning" });
  assert.match(h.get("#schedule-feedback").textContent, /已生成|已有/);
});

test("the schedule panel shows the local scheduler runtime state", async () => {
  const h = await harness();
  await h.inboxModule.loadSchedulePanel();
  assert.match(h.get("#schedule-status").textContent, /调度正常/);
  assert.doesNotMatch(h.get("#schedule-status").textContent, /失败/);

  const failing = await harness({
    runtime: { last_check_at: "2026-10-04T08:00:00+08:00", consecutive_failures: 2, last_error_at: "2026-10-04T07:59:00+08:00" },
  });
  await failing.inboxModule.loadSchedulePanel();
  assert.match(failing.get("#schedule-status").textContent, /调度连续失败 2 次/);
});

test("a late capability update reloads the schedule panel while settings is open", async () => {
  const h = await harness({ supported: false });
  await h.inboxModule.loadSchedulePanel();
  assert.equal(h.requests.length, 0);
  // Settings arrive after the panel first rendered: settings.js dispatches config-changed.
  h.state.view = "settings";
  h.state.config = { capabilities: { scheduler: true } };
  h.documentListeners.get("config-changed")({ target: h.get("#schedule-form") });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(h.requests.some(({ url }) => url === "/api/schedule"), true);
});
