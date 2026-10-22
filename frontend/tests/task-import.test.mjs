import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

// Offline event tests with synthetic records only; no browser or backend writes.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const keyFor = (index) => index.toString(16).padStart(64, "0");
const samplePreview = (count = 4, overrides = {}) => ({ document_id: 7, revision: 3, sha256: "a".repeat(64), warnings: [], items: Array.from({ length: count }, (_, index) => ({ key: keyFor(index + 1), line: index + 2, title: `虚构待办 ${index + 1}`, description: `- [${index === 0 ? "x" : " "}] 虚构待办 ${index + 1}`, checked: index === 0, heading: "测试 / 合成" })), ...overrides });

async function harness(preview = samplePreview()) {
  const nodes = new Map();
  class Element {
    constructor(selector = "") { this.selector = selector; this.listeners = new Map(); this.dataset = {}; this.attributes = {}; this.value = ""; this.checked = false; this.disabled = false; this.textContent = ""; this.html = ""; this.classList = { add() {}, remove() {}, toggle() {} }; }
    set innerHTML(html) {
      this.html = html;
      // Replacing markup also replaces its child controls and old listeners.
      for (const match of html.matchAll(/\bid="([^"]+)"/g)) nodes.set(`#${match[1]}`, new Element(`#${match[1]}`));
    }
    get innerHTML() { return this.html; }
    addEventListener(type, callback) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(callback); }
    async emit(type, values = {}) { const event = { target: this, currentTarget: this, preventDefault() {}, ...values }; for (const handler of this.listeners.get(type) || []) await handler(event); }
    setAttribute(name, value) { this.attributes[name] = value; }
    querySelectorAll() { return []; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    focus() { this.focused = true; }
  }
  const getNode = (selector) => { if (!nodes.has(selector)) nodes.set(selector, new Element(selector)); return nodes.get(selector); };
  const requests = [];
  let response = (url) => url.endsWith("/preview") ? preview : url === "/api/goals" ? [{ id: "00_人生路/06_目标与计划/01_年度目标/虚构目标.md", title: "虚构目标", status: "active" }] : { created_count: 1, existing_count: 0, tasks: [], warnings: [] };
  const context = vm.createContext({
    document: { querySelector: getNode, querySelectorAll: () => [], addEventListener() {}, dispatchEvent() {} },
    window: { setTimeout: () => 1, clearTimeout() {} },
    AbortController, TypeError, Intl, Date, CustomEvent: class { constructor(type) { this.type = type; } },
    fetch: async (url, options) => { requests.push({ url, options }); return { ok: true, status: 200, json: async () => response(url, options) }; },
  });
  const modules = new Map();
  const pending = new Map();
  async function load(name) {
    const filename = path.resolve(frontend, name);
    if (modules.has(filename)) return modules.get(filename);
    if (pending.has(filename)) return pending.get(filename);
    const promise = (async () => {
      const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context, identifier: filename, importModuleDynamically: async (specifier, referrer) => load(path.relative(frontend, path.resolve(path.dirname(referrer.identifier), specifier))) });
      await module.link((specifier, referrer) => load(path.relative(frontend, path.resolve(path.dirname(referrer.identifier), specifier))));
      await module.evaluate();
      modules.set(filename, module);
      pending.delete(filename);
      return module;
    })();
    pending.set(filename, promise);
    return promise;
  }
  const core = (await load("core.js")).namespace;
  const taskImport = (await load("task-import.js")).namespace;
  const open = () => taskImport.openTaskImport({ id: 7, title: "虚构资料 <unsafe>" });
  const select = async (key, checked = true) => {
    const input = { dataset: { taskImportKey: key }, checked, closest() { return this; } };
    await getNode("#task-import-items").emit("change", { target: input });
    return input;
  };
  return { core, taskImport, open, select, getNode, context, requests, setResponse(callback) { response = callback; } };
}

test("task preview selects nothing, caps displayed rows, and escapes all source text", async () => {
  const preview = samplePreview(70, { warnings: ['<img src=x onerror="bad()">'] });
  preview.items[0] = { ...preview.items[0], title: "<script>title()</script>", description: "<img src=x>", heading: "<b>heading</b>" };
  const { open, getNode, requests } = await harness(preview);
  await open();
  assert.match(getNode("#dialog-content").innerHTML, /复制为独立的 Markdown 任务笔记，不反向同步/);
  assert.match(getNode("#dialog-content").innerHTML, /&lt;unsafe&gt;/);
  const html = getNode("#task-import-items").innerHTML;
  assert.equal((html.match(/data-task-import-key=/g) || []).length, 50);
  assert.equal(/\schecked(?:\s|\/|>)/.test(html), false);
  assert.equal(html.includes("<script>"), false);
  assert.ok(html.includes("&lt;script&gt;"));
  assert.ok(html.includes("&lt;b&gt;"));
  assert.ok(getNode("#task-import-warnings").innerHTML.includes("&lt;img"));
  assert.match(html, /来源已完成，完成日期未知/);
  assert.equal(getNode("#task-import-apply").disabled, true);
  assert.equal(requests.some(({ options }) => options.method === "POST"), false);
  assert.equal(requests.some(({ url }) => /model|chat/.test(url)), false);
  getNode("#task-import-search").value = "虚构待办 70";
  await getNode("#task-import-search").emit("input");
  assert.equal((getNode("#task-import-items").innerHTML.match(/data-task-import-key=/g) || []).length, 1);
});

test("task selections reject unknown keys, cap twenty, and persist through filtering", async () => {
  const { open, select, getNode } = await harness(samplePreview(24));
  await open();
  assert.equal((await select(keyFor(99))).checked, false);
  for (let index = 1; index <= 20; index += 1) await select(keyFor(index));
  assert.equal((await select(keyFor(21))).checked, false);
  assert.match(getNode("#task-import-selection").textContent, /已选择 20 \/ 20/);
  getNode("#task-import-search").value = "no match";
  await getNode("#task-import-search").emit("input");
  assert.equal(getNode("#task-import-apply").disabled, false);
  getNode("#task-import-search").value = "";
  getNode("#task-import-selected-only").checked = true;
  await getNode("#task-import-selected-only").emit("change");
  assert.equal((getNode("#task-import-items").innerHTML.match(/data-task-import-key=/g) || []).length, 20);
  await getNode("#task-import-clear").emit("click");
  assert.match(getNode("#task-import-selection").textContent, /已选择 0/);
  assert.equal(getNode("#task-import-apply").disabled, true);
});

test("explicit task apply sends selection and version guards and labels unknown completion dates", async () => {
  const { open, select, getNode, requests, setResponse } = await harness();
  await open();
  await select(keyFor(1));
  await select(keyFor(2));
  getNode("#task-import-goal").value = "00_人生路/06_目标与计划/01_年度目标/虚构目标.md";
  setResponse(() => ({ created_count: 1, existing_count: 1, tasks: [{ title: "<任务>", description: "<script>raw</script>", status: "completed", completed_at: null }], warnings: ["<版本提示>"] }));
  await getNode("#task-import-apply").emit("click");
  const request = requests.find(({ options }) => options.method === "POST");
  assert.equal(request.url, "/api/documents/7/tasks/apply");
  assert.deepEqual(JSON.parse(request.options.body), { revision: 3, sha256: "a".repeat(64), selected_keys: [keyFor(1), keyFor(2)], goal_path: "00_人生路/06_目标与计划/01_年度目标/虚构目标.md", confirm: true });
  const result = getNode("#task-import-result").innerHTML;
  assert.match(result, /已创建 1 项 · 已有 1 项/);
  assert.match(result, /完成日期未知，不计入今天完成/);
  assert.ok(result.includes("&lt;任务&gt;"));
  assert.ok(result.includes("&lt;script&gt;"));
  assert.ok(result.includes("&lt;版本提示&gt;"));
  assert.match(getNode("#task-import-selection").textContent, /已选择 2/);
});

test("task import conflict preserves selection but requires a new preview before another apply", async () => {
  const { open, select, getNode, requests, context } = await harness();
  await open();
  await select(keyFor(1));
  context.fetch = async (url, options) => { requests.push({ url, options }); return { ok: false, status: 409, json: async () => ({ detail: "来源已变化" }) }; };
  await getNode("#task-import-apply").emit("click");
  assert.equal(getNode("#task-import-apply").disabled, true);
  assert.match(getNode("#task-import-selection").textContent, /已选择 1/);
  assert.match(getNode("#task-import-feedback").textContent, /不能继续提交旧版本/);
  await getNode("#task-import-apply").emit("click");
  assert.equal(requests.filter(({ options }) => options.method === "POST").length, 1);
  context.fetch = async (url, options) => { requests.push({ url, options }); return { ok: true, status: 200, json: async () => url.endsWith("/preview") ? samplePreview(3, { revision: 4, sha256: "b".repeat(64) }) : [] }; };
  await getNode("#task-import-reload").emit("click");
  assert.match(getNode("#task-import-selection").textContent, /已选择 0/);
  assert.equal(getNode("#task-import-apply").disabled, true);
  await select(keyFor(3));
  await getNode("#task-import-apply").emit("click");
  const body = JSON.parse(requests.filter(({ options }) => options.method === "POST").at(-1).options.body);
  assert.equal(body.revision, 4);
  assert.equal(body.sha256, "b".repeat(64));
});

test("network failures retain a task selection and allow an explicit retry", async () => {
  const { open, select, getNode, context } = await harness();
  await open();
  await select(keyFor(2));
  context.fetch = async () => { throw new TypeError("offline"); };
  await getNode("#task-import-apply").emit("click");
  assert.equal(getNode("#task-import-apply").disabled, false);
  assert.match(getNode("#task-import-feedback").textContent, /当前选择已保留/);
  assert.match(getNode("#task-import-selection").textContent, /已选择 1/);
});

test("parallel apply attempts send one write and restore controls", async () => {
  const { open, select, getNode, requests, context } = await harness();
  await open();
  await select(keyFor(1));
  let release;
  const waiting = new Promise((resolve) => { release = resolve; });
  context.fetch = async (url, options) => { requests.push({ url, options }); await waiting; return { ok: true, status: 200, json: async () => ({ created_count: 1, existing_count: 0, tasks: [], warnings: [] }) }; };
  const saving = getNode("#task-import-apply").emit("click");
  await getNode("#task-import-apply").emit("click");
  await select(keyFor(2));
  assert.equal(getNode("#task-import-goal").disabled, true);
  assert.equal(requests.filter(({ options }) => options.method === "POST").length, 1);
  assert.match(getNode("#task-import-selection").textContent, /已选择 1/);
  release();
  await saving;
  assert.equal(getNode("#task-import-goal").disabled, false);
});

test("a late preview cannot replace a different dialog", async () => {
  const { open, core, getNode, context } = await harness();
  let release;
  const waiting = new Promise((resolve) => { release = resolve; });
  context.fetch = async () => { await waiting; return { ok: true, status: 200, json: async () => samplePreview() }; };
  const loading = open();
  core.openDialog("另一份资料", "<p>不能被旧预览覆盖</p>");
  release();
  await loading;
  assert.equal(getNode("#dialog-title").textContent, "另一份资料");
  assert.equal(getNode("#dialog-content").innerHTML, "<p>不能被旧预览覆盖</p>");
  assert.equal(getNode("#task-import-items").innerHTML, "");
});

test("a late apply response cannot replace or report into another dialog", async () => {
  const { open, select, core, getNode, context } = await harness();
  await open();
  await select(keyFor(1));
  let release;
  const waiting = new Promise((resolve) => { release = resolve; });
  context.fetch = async () => { await waiting; return { ok: true, status: 200, json: async () => ({ created_count: 1, existing_count: 0, tasks: [], warnings: [] }) }; };
  const saving = getNode("#task-import-apply").emit("click");
  core.openDialog("当前页面", "<p>保留当前页面</p>");
  release();
  await saving;
  assert.equal(getNode("#dialog-content").innerHTML, "<p>保留当前页面</p>");
  assert.equal(getNode("#task-import-result").innerHTML, "");
});

test("source dialog task entry lazy-loads only after a click", async () => {
  const { core, getNode, requests, setResponse } = await harness();
  setResponse((url) => url === "/api/documents/7" ? { id: 7, title: "虚构 Markdown", content: "- [ ] 示例", revision: 3, sha256: "a".repeat(64) } : url.endsWith("/preview") ? samplePreview() : []);
  await core.showDocument(7);
  assert.match(getNode("#dialog-content").innerHTML, /预览待办清单/);
  assert.equal(requests.some(({ url }) => url.endsWith("/tasks/preview")), false);
  await getNode("#preview-document-tasks").emit("click");
  assert.equal(requests.filter(({ url }) => url.endsWith("/tasks/preview")).length, 1);
  assert.equal(getNode("#dialog-title").textContent, "预览待办清单");
  assert.equal(requests.some(({ options }) => options.method === "POST"), false);
});

test("failed refresh retains previous selection but disables stale import", async () => {
  const { open, select, getNode, context } = await harness();
  await open();
  await select(keyFor(2));
  context.fetch = async () => { throw new TypeError("offline"); };
  await getNode("#task-import-reload").emit("click");
  assert.match(getNode("#task-import-selection").textContent, /已选择 1/);
  assert.equal(getNode("#task-import-apply").disabled, true);
  assert.equal(getNode("#task-import-reload").disabled, false);
  assert.match(getNode("#task-import-feedback").textContent, /原选择保留/);
});

test("reopening the same source cannot launch a second apply while the first is pending", async () => {
  const { open, select, getNode, context, requests } = await harness();
  await open();
  await select(keyFor(1));
  let release;
  const waiting = new Promise((resolve) => { release = resolve; });
  context.fetch = async (url, options) => { requests.push({ url, options }); await waiting; return { ok: true, status: 200, json: async () => ({ created_count: 1, existing_count: 0, tasks: [], warnings: [] }) }; };
  const saving = getNode("#task-import-apply").emit("click");
  await open();
  assert.equal(getNode("#task-import-reload").disabled, true);
  assert.match(getNode("#task-import-feedback").textContent, /同一来源的任务正在复制/);
  await getNode("#task-import-apply").emit("click");
  assert.equal(requests.filter(({ options }) => options.method === "POST").length, 1);
  release();
  await saving;
  assert.equal(getNode("#task-import-reload").disabled, false);
  assert.equal(getNode("#task-import-apply").disabled, true);
  assert.equal(getNode("#task-import-result").innerHTML, "");
});
