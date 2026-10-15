import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the live data-handbook card (core.js is stubbed).
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

class NodeDouble {
  constructor(id = "") {
    this.id = id;
    this.listeners = new Map();
    this.value = "";
    this.disabled = false;
    this.hidden = false;
    this.textContent = "";
    this.innerHTML = "";
  }
  addEventListener(type, callback) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(callback); }
  async emit(type, values = {}) {
    const event = { target: this, currentTarget: this, preventDefault() {}, ...values };
    for (const callback of this.listeners.get(type) || []) await callback(event);
    return event;
  }
}

async function harness({ capabilities = { schema_export: true }, schema = null, error = "" } = {}) {
  const nodes = new Map();
  const requests = [];
  const documentListeners = new Map();
  const getNode = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, new NodeDouble(selector));
    return nodes.get(selector);
  };
  const document = {
    querySelector: getNode,
    querySelectorAll: () => [],
    addEventListener(type, callback) { if (!documentListeners.has(type)) documentListeners.set(type, []); documentListeners.get(type).push(callback); },
    dispatchEvent(event) { for (const callback of documentListeners.get(event?.type) || []) callback(event); return true; },
  };
  const state = { view: "settings", config: { capabilities } };
  const core = new vm.SyntheticModule(
    ["$", "state", "api", "escapeHtml", "setStatus", "toast"],
    function () {
      this.setExport("$", getNode);
      this.setExport("state", state);
      this.setExport("api", async (url) => {
        requests.push({ url });
        if (error) throw new Error(error);
        return schema;
      });
      this.setExport("escapeHtml", (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character])));
      this.setExport("setStatus", (selector, message, tone = "") => { getNode(selector).textContent = message; getNode(selector).tone = tone; });
      this.setExport("toast", (message) => { getNode("#toast").textContent = message; });
    },
    { context: vm.createContext({ document, location: { assign(url) { getNode("#toast").assigned = url; } } }) },
  );
  const filename = path.join(frontend, "data-handbook.js");
  const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context: core.context, identifier: filename });
  await module.link((specifier) => {
    if (specifier === "./core.js") return core;
    throw new Error(`unexpected import ${specifier}`);
  });
  await module.evaluate();
  return { namespace: module.namespace, requests, getNode, state, document };
}

const schemaPayload = {
  schema: 1,
  generated_at: "2026-10-05T02:00:00+08:00",
  database: { file: "torch_os.sqlite3", bytes: 2_100_000, tables: 23, fts_helpers: 5 },
  groups: { "资料与记忆": 6, "对话": 2, "目标与任务": 3 },
  tables: [
    { name: "audit_events", rows: 42 },
    { name: "<img src=x onerror=1>", rows: 3 },
    { name: "memories", rows: 0 },
  ],
  note: "本导出只含结构与计数：没有笔记正文、没有问题文本、没有密钥。",
};

test("the handbook card renders live counts with escaped table names", async () => {
  const h = await harness({ schema: schemaPayload });
  h.namespace.initDataHandbook();
  await h.namespace.loadDataHandbook(true);
  const html = h.getNode("#schema-summary").innerHTML;
  assert.equal(html.includes("<img"), false);
  assert.ok(html.includes("&lt;img"));
  assert.match(html, /23 张/);
  assert.match(html, /2\.0 MB/);
  assert.match(html, /资料与记忆/);
  assert.match(html, /audit_events：42 行/);
  assert.match(h.getNode("#schema-status").textContent, /已读取 23 张表的实时结构/);
  assert.equal(h.getNode("#download-schema-md").disabled, false);
});

test("downloading assigns the markdown endpoint and an older service stays disabled", async () => {
  const h = await harness({ schema: schemaPayload });
  h.namespace.initDataHandbook();
  h.getNode("#download-schema-md").emit("click");
  assert.equal(h.getNode("#toast").assigned, "/api/schema.md");
  assert.match(h.getNode("#toast").textContent, /已开始下载数据手册/);

  const failed = await harness({ schema: schemaPayload, error: "服务暂时不可用" });
  failed.namespace.initDataHandbook();
  await failed.namespace.loadDataHandbook(true);
  assert.match(failed.getNode("#schema-status").textContent, /读取失败：服务暂时不可用/);

  const old = await harness({ capabilities: {} });
  old.namespace.initDataHandbook();
  await old.namespace.loadDataHandbook(true);
  assert.equal(old.requests.length, 0);
  assert.equal(old.getNode("#download-schema-md").disabled, true);
  assert.match(old.getNode("#schema-support").textContent, /尚未支持数据手册导出/);
});
