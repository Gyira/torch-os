import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the read-only tool catalog card (core.js is stubbed).
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

class NodeDouble {
  constructor(id = "") {
    this.id = id;
    this.listeners = new Map();
    this.disabled = false;
    this.hidden = false;
    this.textContent = "";
    this.innerHTML = "";
  }
  addEventListener(type, callback) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(callback); }
  async emit(type) {
    for (const callback of this.listeners.get(type) || []) await callback({ target: this, currentTarget: this, preventDefault() {} });
  }
}

async function harness({ capabilities = { tools: true }, catalog = null, error = "", selftest = null, selftestError = "" } = {}) {
  const nodes = new Map();
  const requests = [];
  const getNode = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, new NodeDouble(selector));
    return nodes.get(selector);
  };
  const document = { querySelector: getNode, querySelectorAll: () => [], addEventListener() {}, dispatchEvent() {} };
  const state = { view: "settings", config: { capabilities } };
  const core = new vm.SyntheticModule(
    ["$", "state", "api", "escapeHtml", "setStatus"],
    function () {
      this.setExport("$", getNode);
      this.setExport("state", state);
      this.setExport("api", async (url, options = {}) => {
        requests.push({ url, options });
        if (url.endsWith("/bridge/selftest")) {
          if (selftestError) throw new Error(selftestError);
          return selftest;
        }
        if (error) throw new Error(error);
        return catalog;
      });
      this.setExport("escapeHtml", (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character])));
      this.setExport("setStatus", (selector, message, tone = "") => { getNode(selector).textContent = message; getNode(selector).tone = tone; });
    },
    { context: vm.createContext({ document }) },
  );
  const filename = path.join(frontend, "tool-catalog.js");
  const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context: core.context, identifier: filename });
  await module.link((specifier) => {
    if (specifier === "./core.js") return core;
    throw new Error(`unexpected import ${specifier}`);
  });
  await module.evaluate();
  return { namespace: module.namespace, requests, getNode };
}

const catalogPayload = {
  schema: 1,
  count: 3,
  read_only: true,
  note: "全部工具只读且只在本机回环地址 + 本地会话下可用；写操作永远不会通过这些接口开放。",
  tools: [
    { name: "today", method: "GET", path: "/api/tools/today", summary: "某一天的结构化概况", parameters: ["day?(YYYY-MM-DD)"] },
    { name: "memories", method: "GET", path: "/api/tools/memories?q=", summary: "<img src=x onerror=1>", parameters: [] },
    { name: "vault", method: "GET", path: "/api/tools/vault", summary: "Vault 现状", parameters: [] },
  ],
};

test("the catalog card lists tools with escaped summaries and no writes", async () => {
  const h = await harness({ catalog: catalogPayload });
  h.namespace.initToolCatalog();
  await h.namespace.loadToolCatalog(true);
  assert.equal(h.requests.at(-1).url, "/api/tools");
  const html = h.getNode("#tools-summary").innerHTML;
  assert.equal(html.includes("<img"), false);
  assert.ok(html.includes("&lt;img"));
  assert.match(html, /GET \/api\/tools\/today/);
  assert.match(html, /day\?\(YYYY-MM-DD\)/);
  assert.match(html, /写操作永远不会通过/);
  assert.match(h.getNode("#tools-status").textContent, /共 3 个只读工具/);
});

test("failures and older services stay safe", async () => {
  const failed = await harness({ catalog: catalogPayload, error: "服务暂时不可用" });
  failed.namespace.initToolCatalog();
  await failed.namespace.loadToolCatalog(true);
  assert.match(failed.getNode("#tools-status").textContent, /读取失败：服务暂时不可用/);

  const old = await harness({ capabilities: {} });
  old.namespace.initToolCatalog();
  await old.namespace.loadToolCatalog(true);
  assert.equal(old.requests.length, 0);
  assert.equal(old.getNode("#load-tools").disabled, true);
  assert.match(old.getNode("#tools-support").textContent, /尚未支持只读工具接口/);
});

test("bridge self-test reports the handshake and tool count, and older services disable it", async () => {
  const ready = await harness({
    capabilities: { tools: true, mcp_bridge: true },
    selftest: { ok: true, protocol_version: "2025-06-18", tool_count: 13, resources: 2, sample_tool: "imports", elapsed_ms: 123 },
  });
  ready.namespace.initToolCatalog();
  assert.equal(ready.getNode("#run-bridge-selftest").disabled, false);
  await ready.getNode("#run-bridge-selftest").emit("click");
  const request = ready.requests.find(({ url }) => url.endsWith("/bridge/selftest"));
  assert.equal(request.options.method, "POST");
  assert.match(ready.getNode("#bridge-selftest-status").textContent,
    /桥自检通过：协议 2025-06-18 · 13 个只读工具 · 2 个资源 · 示例调用 imports 成功（123 毫秒）/);

  const failed = await harness({
    capabilities: { tools: true, mcp_bridge: true },
    selftest: { ok: false, error: "桥在 20 秒内没有完成" },
  });
  failed.namespace.initToolCatalog();
  await failed.getNode("#run-bridge-selftest").emit("click");
  assert.match(failed.getNode("#bridge-selftest-status").textContent, /桥自检未通过：桥在 20 秒内没有完成/);

  const old = await harness({ capabilities: { tools: true } });
  old.namespace.initToolCatalog();
  assert.equal(old.getNode("#run-bridge-selftest").disabled, true);
  await old.getNode("#run-bridge-selftest").emit("click");
  assert.equal(old.requests.length, 0);
});
