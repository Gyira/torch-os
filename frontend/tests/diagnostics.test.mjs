import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the read-only self-check panel. No network, no model.
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
  generated_at: "2026-10-04T15:00:00+08:00",
  integrity: "ok",
  version: "0.13.0",
  counts: {
    documents_available: 3, memories_active: 2, memories_candidate: 5, memories_rejected: 0,
    memories_archived: 1, conversations: 1, messages: 4, goals: 2, tasks: 3,
    journal_entries: 2, habit_entries: 5, study_sessions: 3, body_entries: 4,
    inbox_items: 2, memory_usages: 1, audit_events: 40,
  },
  vault: { markdown_files: 12, relationships_present: true },
  backups: { count: 2, latest: { filename: "<img src=x>.zip", bytes: 1024, modified_at: "2026-10-04T14:50:00+08:00" }, verification: { checked: "<img src=x>.zip", ok: true, error: null, relationship_files: 2 } },
};

async function harness({ diagnostics = true, fail = false } = {}) {
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
  let diagnosticsFail = fail;
  const respond = (url) => {
    if (url === "/api/health") return { status: "ok", version: "0.13.0", runtime_label: "personal" };
    if (url === "/api/config/status") {
      return {
        model_configured: false, provider: "local", model_name: "未配置", state: "local",
              capabilities: diagnostics ? { diagnostics: true } : {},
      };
    }
    if (url === "/api/backups") return [];
    if (url === "/api/diagnostics/summary") {
      if (diagnosticsFail) return { httpError: 500, message: "<bad> 自检失败" };
      return structuredClone(summary);
    }
    return {};
  };
  const context = vm.createContext({
    document, AbortController, TypeError, Intl, Date, console,
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
  const settings = (await load("settings.js")).namespace;
  settings.initSettings();
  return {
    settings, getNode, requests,
    setFail(value) { diagnosticsFail = value; },
    clickRetry() {
      const target = new NodeDouble("synthetic-retry");
      target.dataset.retry = "diagnostics";
      document.dispatchEvent({ type: "click", target });
    },
  };
}

test("self-check runs on demand, renders counts and escapes file names", async () => {
  const h = await harness();
  await h.settings.loadSettings();
  assert.equal(h.getNode("#service-label").textContent, "本地服务已连接");
  assert.equal(h.getNode("#service-version").textContent, "v0.13.0");
  assert.equal(h.getNode("#run-diagnostics").disabled, false);
  assert.equal(h.requests.some(({ url }) => url === "/api/diagnostics/summary"), false, "never runs without a click");

  await h.getNode("#run-diagnostics").emit("click");
  const html = h.getNode("#diagnostics-panel").innerHTML;
  assert.match(html, /数据库完整性/);
  assert.match(html, /ok（通过）/);
  assert.match(html, /资料（可用）/);
  assert.match(html, /已确认 2 · 待确认 5 · 已拒绝 0 · 已归档 1/);
  assert.match(html, /Vault Markdown/);
  assert.match(html, /检测到关系分区，未计入/);
  assert.match(html, /本地备份/);
  assert.match(html, /最近备份校验/);
  assert.match(html, /通过（路径、哈希、SQLite 完整性）/);
  assert.match(html, /关系分区明文文件 2 个/);
  assert.match(html, /&lt;img src=x&gt;\.zip/);
  assert.doesNotMatch(html, /<img src=x>/);
  assert.equal(h.getNode("#run-diagnostics").disabled, false, "button is re-enabled after the run");
});

test("an older service disables the self-check and sends no request", async () => {
  const h = await harness({ diagnostics: false });
  await h.settings.loadSettings();
  assert.equal(h.getNode("#run-diagnostics").disabled, true);
  await h.getNode("#run-diagnostics").emit("click");
  assert.equal(h.requests.some(({ url }) => url === "/api/diagnostics/summary"), false);
});

test("a failed self-check keeps an escaped retry that reloads the panel", async () => {
  const h = await harness({ fail: true });
  await h.settings.loadSettings();
  await h.getNode("#run-diagnostics").emit("click");
  assert.match(h.getNode("#diagnostics-panel").innerHTML, /&lt;bad&gt;|自检失败/);
  assert.doesNotMatch(h.getNode("#diagnostics-panel").innerHTML, /<bad>/);
  assert.match(h.getNode("#diagnostics-panel").innerHTML, /data-retry="diagnostics"/);

  h.setFail(false);
  h.clickRetry();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(h.getNode("#diagnostics-panel").innerHTML, /数据库完整性/);
});
