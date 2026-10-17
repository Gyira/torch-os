import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the per-memory answer-usage dialog. No network, no model:
// every fetch is answered by the harness and nothing is written anywhere.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

class NodeDouble {
  constructor(id = "") {
    this.id = id;
    this.listeners = new Map();
    this.dataset = {};
    this.value = "";
    this.disabled = false;
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
    const event = { target: this, currentTarget: this, preventDefault() {}, ...values };
    for (const callback of this.listeners.get(type) || []) await callback(event);
    return event;
  }
  setAttribute() {}
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; for (const callback of this.listeners.get("close") || []) callback({}); }
  closest(selector) {
    const match = selector.match(/^\[data-([a-z-]+)\]$/);
    if (!match) return null;
    const key = match[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    return this.dataset[key] !== undefined ? this : null;
  }
}

const memory = {
  id: 7, kind: "fact", status: "active", title: "虚构记忆",
  content: "虚构内容用于离线测试。", source_path: "01-Identity/身份（identity）.md",
  updated_at: "2026-10-04T04:00:00+00:00",
};
const usageWithRows = {
  memory: { memory_id: 7, status: "active", title: "虚构记忆" },
  total: 5,
  items: [
    { message_id: 42, conversation_id: 3, used_at: "2026-10-04T05:00:00+00:00", conversation_title: "虚构对话", question: "<img src=x onerror=1> 我是谁？" },
  ],
};

async function harness({ capabilities = { memory_usage: true }, list = [memory] } = {}) {
  const nodes = new Map();
  const documentListeners = new Map();
  const requests = [];
  const getNode = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, new NodeDouble(selector));
    return nodes.get(selector);
  };
  const document = {
    querySelector: getNode,
    querySelectorAll: () => [],
    createElement: () => new NodeDouble(),
    addEventListener(type, callback) {
      if (!documentListeners.has(type)) documentListeners.set(type, []);
      documentListeners.get(type).push(callback);
    },
    dispatchEvent(event) {
      for (const callback of documentListeners.get(event?.type) || []) callback(event);
      return true;
    },
  };
  let usageResponse = { status: 200, value: usageWithRows };
  let batchResponse = { status: 200, value: { status: "active", requested: 2, updated: 1, updated_ids: [1], skipped: [{ memory_id: 2, reason: "already" }] } };
  const respond = (url, options = {}) => {
    if (url === "/api/memories?status=candidate") return { status: 200, value: list };
    if (url === "/api/memories/7/usage") return usageResponse;
    if (url === "/api/memories/batch-status") return batchResponse;
    if (url === "/api/memories/tidy-candidates") {
      const body = JSON.parse(options?.body || "{}");
      return { status: 200, value: { dry_run: Boolean(body.dry_run), total: 2, updated: 1, unchanged: 0, not_memory_like: 1, samples: [] } };
    }
    return { status: 200, value: {} };
  };
  const context = vm.createContext({
    document, AbortController, TypeError, Intl, Date, console,
    CustomEvent: class { constructor(type) { this.type = type; } },
    location: { hash: "#memory", assign(url) { this.assigned = url; } },
    history: { pushState() {}, replaceState() {} },
    window: {
      setTimeout: () => 1,
      clearTimeout() {},
      addEventListener() {}, scrollTo() {},
      confirm() { throw new Error("Native confirmation dialogs are forbidden in UI behavior tests"); },
    },
    setImmediate,
    fetch: async (url, options) => {
      requests.push({ url: String(url), options });
      const entry = respond(String(url), options);
      if (entry.status !== 200) {
        return { ok: false, status: entry.status, json: async () => ({ detail: entry.value?.detail || "读取失败" }) };
      }
      return { ok: true, status: 200, json: async () => structuredClone(entry.value) };
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
  core.state.config = { capabilities };
  const memories = (await load("memories.js")).namespace;
  memories.initMemories();
  const flush = () => new Promise((resolve) => setImmediate(resolve));
  const clickUsage = async (id) => {
    const target = new NodeDouble("synthetic-usage");
    target.dataset.memoryUsage = String(id);
    document.dispatchEvent({ type: "click", target });
    await flush();
  };
  return {
    memories, getNode, requests, flush, clickUsage, location: context.location, document,
    setUsage(value, status = 200) { usageResponse = { status, value }; },
    setBatch(value, status = 200) { batchResponse = { status, value }; },
  };
}

test("the memory card offers a usage button and the dialog lists stored references", async () => {
  const h = await harness();
  await h.memories.loadMemories("candidate");
  assert.match(h.getNode("#memory-list").innerHTML, /data-memory-usage="7"/);
  assert.match(h.getNode("#memory-list").innerHTML, /使用记录/);

  await h.clickUsage(7);
  assert.equal(h.getNode("#dialog-title").textContent, "记忆使用记录");
  const html = h.getNode("#dialog-content").innerHTML;
  assert.match(html, /共 5 次引用/);
  assert.match(html, /&lt;img src=x onerror=1&gt; 我是谁？/);
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /虚构对话/);
  assert.ok(h.requests.some(({ url }) => url === "/api/memories/7/usage"));
});

test("an older service without the capability renders no usage button", async () => {
  const h = await harness({ capabilities: {} });
  await h.memories.loadMemories("candidate");
  assert.doesNotMatch(h.getNode("#memory-list").innerHTML, /data-memory-usage/);
  assert.match(h.getNode("#memory-list").innerHTML, /data-memory-history="7"/);
});

test("an unused memory renders the empty state with its current status", async () => {
  const h = await harness();
  await h.memories.loadMemories("candidate");
  h.setUsage({ memory: { memory_id: 7, status: "archived" }, total: 0, items: [] });
  await h.clickUsage(7);
  const html = h.getNode("#dialog-content").innerHTML;
  assert.match(html, /还没有被回答引用过/);
  assert.match(html, /已归档/);
});

test("a failed usage read keeps a retry that reloads the dialog", async () => {
  const h = await harness();
  await h.memories.loadMemories("candidate");
  h.setUsage({ detail: "记忆使用记录测试失败" }, 500);
  await h.clickUsage(7);
  let html = h.getNode("#dialog-content").innerHTML;
  assert.match(html, /记忆使用记录测试失败/);
  assert.match(html, /data-memory-usage="7"/);

  h.setUsage(usageWithRows);
  await h.clickUsage(7);
  html = h.getNode("#dialog-content").innerHTML;
  assert.match(html, /共 5 次引用/);
});

test("export buttons follow the capability and navigate to the export URLs", async () => {
  const h = await harness({ capabilities: { memory_usage: true, memory_export: true } });
  await h.memories.loadMemories("candidate");
  assert.equal(h.getNode("#export-memories-md").disabled, false);
  assert.equal(h.getNode("#export-memories-json").disabled, false);

  await h.getNode("#export-memories-md").emit("click");
  assert.equal(h.location.assigned, "/api/export/memories.md");
  await h.getNode("#export-memories-json").emit("click");
  assert.equal(h.location.assigned, "/api/export/memories.json");

  const older = await harness({ capabilities: { memory_usage: true } });
  await older.memories.loadMemories("candidate");
  assert.equal(older.getNode("#export-memories-md").disabled, true);
  await older.getNode("#export-memories-md").emit("click");
  assert.equal(older.location.assigned, undefined);
});

test("the search box filters the loaded list locally and can be cleared", async () => {
  const coffee = { ...memory, id: 7, title: "咖啡记录", content: "下午三点以后不喝咖啡。" };
  const tea = { ...memory, id: 8, title: "茶记录", content: "晚上喝淡茶。" };
  const h = await harness({ list: [coffee, tea] });
  await h.memories.loadMemories("candidate");
  assert.equal(h.requests.filter(({ url }) => url.startsWith("/api/memories")).length, 1);

  const search = h.getNode("#memory-search");
  search.value = "咖啡";
  await search.emit("input");
  let html = h.getNode("#memory-list").innerHTML;
  assert.match(html, /下午三点以后不喝咖啡。/);
  assert.doesNotMatch(html, /晚上喝淡茶/);
  assert.match(h.getNode("#memory-status").textContent, /1 \/ 2 条匹配/);
  assert.equal(h.requests.filter(({ url }) => url.startsWith("/api/memories")).length, 1, "filtering is client-side");

  search.value = "不存在的词xyz";
  await search.emit("input");
  html = h.getNode("#memory-list").innerHTML;
  assert.match(html, /没有符合筛选的内容/);
  assert.match(html, /data-memory-search-clear/);

  const target = new NodeDouble("synthetic-clear");
  target.dataset.memorySearchClear = "";
  h.document.dispatchEvent({ type: "click", target });
  html = h.getNode("#memory-list").innerHTML;
  assert.match(html, /下午三点以后不喝咖啡。/);
  assert.match(html, /晚上喝淡茶。/);
  assert.equal(h.getNode("#memory-status").textContent, "2 条待确认");
});

test("deleting a memory requires an explicit confirm and posts once", async () => {
  const h = await harness({ capabilities: { memory_usage: true, memory_delete: true } });
  await h.memories.loadMemories("candidate");
  assert.match(h.getNode("#memory-list").innerHTML, /data-memory-delete="7"/);

  const target = new NodeDouble("synthetic-delete");
  target.dataset.memoryDelete = "7";
  h.document.dispatchEvent({ type: "click", target });
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(h.getNode("#confirm-message").textContent, /无法从本系统恢复/);

  await h.getNode("#confirm-cancel").emit("click");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.requests.some(({ url }) => url === "/api/memories/7/delete"), false);

  h.document.dispatchEvent({ type: "click", target });
  await new Promise((resolve) => setImmediate(resolve));
  await h.getNode("#confirm-accept").emit("click");
  await new Promise((resolve) => setImmediate(resolve));
  const request = h.requests.find(({ url }) => url === "/api/memories/7/delete");
  assert.equal(request.options.method, "POST");
  assert.deepEqual(JSON.parse(request.options.body), { confirm: true });
  assert.match(h.getNode("#toast").textContent, /已永久删除/);

  const older = await harness({ capabilities: { memory_usage: true } });
  await older.memories.loadMemories("candidate");
  assert.doesNotMatch(older.getNode("#memory-list").innerHTML, /data-memory-delete/);
});

test("memory filters combine source, record month and keyword without refetching", async () => {
  const list = [
    { ...memory, id: 1, source_path: "01-Identity/a.md", record_month: "2026-09", content: "苹果记录" },
    { ...memory, id: 2, source_path: "01-Identity/a.md", record_month: "2026-10", content: "香蕉记录" },
    { ...memory, id: 3, source_path: "90-Inbox/b.md", record_month: null, content: "苹果和香蕉都在" },
  ];
  const h = await harness({ list });
  await h.memories.loadMemories("candidate");
  const cards = () => (h.getNode("#memory-list").innerHTML.match(/data-memory-card=/g) || []).length;
  assert.equal(cards(), 3);
  const sourceSelect = h.getNode("#memory-source-filter");
  assert.match(sourceSelect.innerHTML, /value="01-Identity\/a.md"/);
  assert.match(sourceSelect.innerHTML, /value="90-Inbox\/b.md"/);
  assert.match(h.getNode("#memory-month-filter").innerHTML, /value="2026-10"/);
  assert.match(h.getNode("#memory-month-filter").innerHTML, /value="unknown"/);
  const reads = h.requests.filter(({ url }) => url.startsWith("/api/memories?")).length;

  const monthSelect = h.getNode("#memory-month-filter");
  monthSelect.value = "unknown";
  await monthSelect.emit("change");
  assert.equal(cards(), 1);
  assert.match(h.getNode("#memory-list").innerHTML, /data-memory-card="3"/);

  monthSelect.value = "";
  await monthSelect.emit("change");
  assert.equal(cards(), 3);

  sourceSelect.value = "01-Identity/a.md";
  await sourceSelect.emit("change");
  assert.equal(cards(), 2);
  assert.equal(h.getNode("#memory-status").textContent, "2 / 3 条匹配");

  monthSelect.value = "2026-10";
  await monthSelect.emit("change");
  assert.equal(cards(), 1);
  assert.match(h.getNode("#memory-list").innerHTML, /data-memory-card="2"/);

  monthSelect.value = "";
  await monthSelect.emit("change");
  sourceSelect.value = "";
  await sourceSelect.emit("change");
  assert.equal(cards(), 3);
  h.getNode("#memory-search").value = "苹果";
  await h.getNode("#memory-search").emit("input");
  assert.equal(cards(), 2); // 苹果记录 in a.md and the undated inbox note
  assert.equal(h.getNode("#memory-status").textContent, "2 / 3 条匹配");

  const clear = new NodeDouble("synthetic-clear");
  clear.dataset.memorySearchClear = "";
  h.document.dispatchEvent({ type: "click", target: clear });
  assert.equal(cards(), 3);
  assert.equal(sourceSelect.value, "");
  assert.equal(monthSelect.value, "");
  assert.equal(h.getNode("#memory-status").textContent, "3 条待确认");
  assert.equal(h.requests.filter(({ url }) => url.startsWith("/api/memories?")).length, reads, "filters never refetch");
  assert.equal(h.requests.every(({ options }) => (options?.method || "GET") === "GET"), true);
});


test("batch review only writes after explicit confirmation and reports skips", async () => {
  const list = [
    { ...memory, id: 1, status: "candidate", title: "候选一" },
    { ...memory, id: 2, status: "candidate", title: "候选二" },
  ];
  const h = await harness({ capabilities: { memory_usage: true, memory_batch: true }, list });
  await h.memories.loadMemories("candidate");
  assert.match(h.getNode("#memory-list").innerHTML, /data-memory-select="1"/);
  assert.equal(h.getNode("#memory-batch-bar").hidden, false);
  assert.equal(h.getNode("#memory-batch-confirm").disabled, true);

  const box = (id, checked) => { const node = new NodeDouble("synthetic-select"); node.dataset.memorySelect = String(id); node.checked = checked; return node; };
  await h.getNode("#memory-list").emit("change", { target: box(1, true) });
  await h.getNode("#memory-list").emit("change", { target: box(2, true) });
  assert.equal(h.getNode("#memory-batch-count").textContent, "已选 2 条");
  assert.equal(h.getNode("#memory-batch-confirm").disabled, false);

  const batchTarget = new NodeDouble("synthetic-batch");
  batchTarget.dataset.memoryBatch = "active";
  h.document.dispatchEvent({ type: "click", target: batchTarget });
  await h.flush();
  assert.equal(h.requests.some(({ url }) => url === "/api/memories/batch-status"), false, "no write before confirmation");

  await h.getNode("#confirm-accept").emit("click");
  await h.flush();
  const request = h.requests.find(({ url }) => url === "/api/memories/batch-status");
  assert.equal(request.options.method, "POST");
  assert.deepEqual(JSON.parse(request.options.body), { ids: [1, 2], status: "active", confirm: true });
  assert.match(h.getNode("#toast").textContent, /已确认 1 条，跳过 1 条/);
  assert.match(h.getNode("#memory-status").textContent, /批量确认完成：更新 1 条 · 跳过 1 条/);
  assert.equal(h.getNode("#memory-batch-count").textContent, "已选 0 条");
  assert.equal(h.getNode("#memory-batch-confirm").disabled, true);

  const older = await harness({ capabilities: { memory_usage: true }, list });
  await older.memories.loadMemories("candidate");
  assert.doesNotMatch(older.getNode("#memory-list").innerHTML, /data-memory-select/);
  older.getNode("#memory-batch-reject");
  assert.equal(older.requests.some(({ url }) => url === "/api/memories/batch-status"), false);
});


test("the memory page previews and applies candidate tidying with explicit confirmation", async () => {
  const list = [{ ...memory, id: 1, status: "candidate", title: "杂记", content: "# 标题\n- [ ] 我计划明天去图书馆借书。" }];
  const h = await harness({ capabilities: { memory_usage: true, candidate_tidy: true }, list });
  await h.memories.loadMemories("candidate");
  assert.equal(h.getNode("#tidy-candidates").disabled, false);
  const calls = () => h.requests.filter(({ url }) => url === "/api/memories/tidy-candidates");

  const target = new NodeDouble("synthetic-tidy");
  target.dataset.memoryTidy = "";
  h.document.dispatchEvent({ type: "click", target });
  await h.flush();
  assert.equal(calls().length, 1);
  assert.equal(JSON.parse(calls()[0].options.body).dry_run, true);
  assert.equal(calls().length, 1, "apply waits for the confirmation dialog");

  await h.getNode("#confirm-accept").emit("click");
  await h.flush();
  assert.equal(calls().length, 2);
  assert.deepEqual(JSON.parse(calls()[1].options.body), { dry_run: false, confirm: true });
  assert.match(h.getNode("#toast").textContent, /已整理 1 条/);
  assert.match(h.getNode("#memory-status").textContent, /已整理 1 条/);

  const older = await harness({ capabilities: { memory_usage: true }, list });
  await older.memories.loadMemories("candidate");
  assert.equal(older.getNode("#tidy-candidates").disabled, true);
  older.document.dispatchEvent({ type: "click", target });
  assert.equal(older.requests.some(({ url }) => url === "/api/memories/tidy-candidates"), false);
});
