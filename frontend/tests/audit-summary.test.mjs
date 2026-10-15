import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the transparency panel. No network, no model.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

class NodeDouble {
  constructor(id = "") {
    this.id = id;
    this.listeners = new Map();
    this.dataset = {};
    this.value = "";
    this.disabled = false;
    this.checked = false;
    this.hidden = false;
    this.textContent = "";
    this.innerHTML = "";
    this.className = "";
    this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
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
  closest(selector) {
    const valued = selector.match(/^\[data-([a-z-]+)="([^"]+)"\]$/);
    if (valued) {
      const key = valued[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      return String(this.dataset[key]) === valued[2] ? this : null;
    }
    const plain = selector.match(/^\[data-([a-z-]+)\]$/);
    if (!plain) return null;
    const key = plain[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    return this.dataset[key] !== undefined ? this : null;
  }
}

const summary = {
  generated_at: "2026-10-04T17:40:00+08:00",
  window_days: 30,
  total_events: 12,
  chat: { cloud: 1, local: 2, fallback: 0, other: 0, total: 3 },
  exports: 4,
  backups: 2,
  errors: 1,
  latest_error: { event_type: "model_error", created_at: "2026-10-04T16:00:00+00:00" },
  by_type: [
    { event_type: "vault_scan", count: 5, last_at: "2026-10-04T16:05:00+00:00" },
    { event_type: "chat_completed", count: 3, last_at: "2026-10-04T16:00:00+00:00" },
    { event_type: "<img src=x>", count: 1, last_at: "2026-10-04T15:00:00+00:00" },
  ],
  note: "只读统计：只有事件类型与数量，不含正文、问题、标题或路径。",
};

async function harness({ capabilities = { audit_summary: true, export_bundle: true }, fail = false } = {}) {
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
    addEventListener(type, callback) {
      if (!documentListeners.has(type)) documentListeners.set(type, []);
      documentListeners.get(type).push(callback);
    },
    dispatchEvent(event) {
      for (const callback of documentListeners.get(event?.type) || []) callback(event);
      return true;
    },
  };
  let failing = fail;
  const respond = (url) => {
    if (url === "/api/health") return { status: "ok", version: "0.20.0", runtime_label: "personal" };
    if (url === "/api/config/status") {
      return {
        model_configured: false, provider: "local", model_name: "未配置", state: "local",
        embedding_provider: null,
        capabilities,
      };
    }
    if (url === "/api/backups") return [];
    if (url.startsWith("/api/audit/summary")) {
      if (failing) return { httpError: 500, message: "<bad> 统计失败" };
      return structuredClone(summary);
    }
    return {};
  };
  const location = { hash: "#settings", assign(url) { this.assigned = url; } };
  const context = vm.createContext({
    document, location, AbortController, TypeError, Intl, Date, console,
    CustomEvent: class { constructor(type) { this.type = type; } },
    window: { setTimeout: () => 1, clearTimeout() {}, addEventListener() {} },
    fetch: async (url) => {
      requests.push({ url: String(url) });
      const result = respond(String(url));
      return {
        ok: !result?.httpError,
        status: result?.httpError || 200,
        json: async () => result?.httpError ? { detail: result.message } : result,
      };
    },
  });
  const pending = new Map();
  function load(name) {
    const filename = path.resolve(frontend, name);
    if (pending.has(filename)) return pending.get(filename);
    const promise = (async () => {
      const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context, identifier: filename });
      await module.link((specifier, referring) => load(path.relative(frontend, path.resolve(path.dirname(referring.identifier), specifier))));
      await module.evaluate();
      return module;
    })();
    pending.set(filename, promise);
    return promise;
  }
  const settings = (await load("settings.js")).namespace;
  settings.initSettings();
  return {
    settings, getNode, requests, document, location,
    setFail(value) { failing = value; },
    clickRetry() {
      const target = new NodeDouble("synthetic-retry");
      target.dataset.retry = "audit-summary";
      document.dispatchEvent({ type: "click", target });
    },
  };
}

test("the transparency panel loads counts, escapes types and shows the latest error", async () => {
  const h = await harness();
  await h.settings.loadSettings();
  assert.equal(h.getNode("#refresh-audit-summary").disabled, false);
  const html = h.getNode("#audit-summary-panel").innerHTML;
  assert.match(html, /近 30 天共 12 条本地操作/);
  assert.match(html, /云端回答 1/);
  assert.match(html, /本地回答 2/);
  assert.match(html, /导出 4/);
  assert.match(html, /备份 2/);
  assert.match(html, /最近错误事件：model_error/);
  assert.match(html, /资料扫描（vault_scan）/);
  assert.match(html, /问答完成（chat_completed）/);
  assert.match(html, /&lt;img src=x&gt;/);
  assert.doesNotMatch(html, /<img src=x>/);
  assert.match(html, /不含正文、问题、标题或路径/);
});

test("an older service disables the transparency refresh and sends no request", async () => {
  const h = await harness({ capabilities: {} });
  await h.settings.loadSettings();
  assert.equal(h.getNode("#refresh-audit-summary").disabled, true);
  assert.equal(h.requests.some(({ url }) => url.startsWith("/api/audit/summary")), false);
});

test("changing the window reloads the summary with the selected days", async () => {
  const h = await harness();
  await h.settings.loadSettings();
  assert.ok(h.requests.some(({ url }) => url === "/api/audit/summary?days=30"));
  h.getNode("#audit-summary-days").value = "90";
  await h.getNode("#audit-summary-days").emit("change");
  assert.ok(h.requests.some(({ url }) => url === "/api/audit/summary?days=90"));
});

test("a failed transparency request keeps an escaped retry that reloads the panel", async () => {
  const h = await harness({ fail: true });
  await h.settings.loadSettings();
  assert.match(h.getNode("#audit-summary-panel").innerHTML, /&lt;bad&gt;|统计失败/);
  assert.doesNotMatch(h.getNode("#audit-summary-panel").innerHTML, /<bad>/);
  assert.match(h.getNode("#audit-summary-panel").innerHTML, /data-retry="audit-summary"/);

  h.setFail(false);
  h.clickRetry();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(h.getNode("#audit-summary-panel").innerHTML, /近 30 天共 12 条本地操作/);
});

test("the export-all button follows the capability and navigates to the ZIP", async () => {
  const h = await harness();
  await h.settings.loadSettings();
  assert.equal(h.getNode("#download-export-bundle").disabled, false);
  await h.getNode("#download-export-bundle").emit("click");
  assert.equal(h.location.assigned, "/api/export/all.zip");

  const older = await harness({ capabilities: {} });
  await older.settings.loadSettings();
  assert.equal(older.getNode("#download-export-bundle").disabled, true);
  await older.getNode("#download-export-bundle").emit("click");
  assert.equal(older.location.assigned, undefined);
});
