import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the backup retention preview. It must stay read-only.
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
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
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

const plan = {
  generated_at: "2026-10-04T18:50:00+08:00",
  keep: 2,
  total: 3,
  total_bytes: 600 * 1024,
  keep_bytes: 500 * 1024,
  remove_bytes: 100 * 1024,
  keep_list: [
    { filename: "torch-os-new.zip", bytes: 300 * 1024, modified_at: "2026-10-04T18:00:00+08:00" },
    { filename: "torch-os-mid.zip", bytes: 200 * 1024, modified_at: "2026-10-04T12:00:00+08:00" },
  ],
  remove_list: [
    { filename: "<img src=x>.zip", bytes: 100 * 1024, modified_at: "2026-10-03T12:00:00+08:00" },
  ],
  note: "只读预览：不会删除或移动任何备份文件；删除需要你之后明确操作。",
};

async function harness({ capabilities = { backup_plan: true }, fail = false } = {}) {
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
  let keyResponse = { status: 200, value: { saved: true, configured: true, model_name: "deepseek-chat", note: "已写入" } };
  const respond = (url) => {
    if (url === "/api/health") return { status: "ok", version: "0.24.0", runtime_label: "personal" };
    if (url === "/api/config/status") {
      return { model_configured: false, provider: "local", model_name: "未配置", state: "local", capabilities };
    }
    if (url === "/api/backups") return [];
    if (url.startsWith("/api/backups/plan")) {
      if (failing) return { httpError: 500, message: "<bad> 预览失败" };
      return structuredClone(plan);
    }
    if (url.startsWith("/api/audit/summary")) return { generated_at: "x", window_days: 30, total_events: 0, chat: {}, exports: 0, backups: 0, errors: 0, by_type: [], note: "只读统计" };
    if (url === "/api/config/model-key") return keyResponse;
    return {};
  };
  const context = vm.createContext({
    document, AbortController, TypeError, Intl, Date, console,
    CustomEvent: class { constructor(type) { this.type = type; } },
    window: { setTimeout: () => 1, clearTimeout() {}, addEventListener() {} },
    fetch: async (url, options) => {
      requests.push({ url: String(url), method: options?.method || "GET", options });
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
  getNode("#backup-keep-count").value = "10";
  return {
    settings, getNode, requests, document,
    setFail(value) { failing = value; },
    setKeyResponse(value, status = 200) { keyResponse = { status, value }; },
    clickRetry() {
      const target = new NodeDouble("synthetic-retry");
      target.dataset.retry = "retention";
      document.dispatchEvent({ type: "click", target });
    },
  };
}

test("the retention preview lists keep/remove and never sends a write", async () => {
  const h = await harness();
  await h.settings.loadSettings();
  assert.equal(h.getNode("#preview-retention").disabled, false);
  await h.getNode("#preview-retention").emit("click");
  const html = h.getNode("#retention-panel").innerHTML;
  assert.match(html, /共 3 份（600 KB）/);
  assert.match(html, /保留最近 2 份（500 KB）/);
  assert.match(html, /计划外 1 份（100 KB）/);
  assert.match(html, /将保留/);
  assert.match(html, /计划外（预览中不会删除）/);
  assert.match(html, /&lt;img src=x&gt;\.zip/);
  assert.doesNotMatch(html, /<img src=x>/);
  assert.match(html, /不会删除或移动任何备份文件/);
  assert.equal(h.getNode("#retention-feedback").textContent.includes("没有删除任何文件"), true);
  assert.equal(h.requests.some(({ url }) => url.startsWith("/api/backups/plan")), true);
  assert.equal(h.requests.every(({ method }) => method === "GET"), true, "preview must only read");
});

test("an older service disables the retention preview", async () => {
  const h = await harness({ capabilities: {} });
  await h.settings.loadSettings();
  assert.equal(h.getNode("#preview-retention").disabled, true);
  await h.getNode("#preview-retention").emit("click");
  assert.equal(h.requests.some(({ url }) => url.startsWith("/api/backups/plan")), false);
});

test("an invalid keep count fails locally and a failed preview keeps a retry", async () => {
  const invalid = await harness();
  await invalid.settings.loadSettings();
  invalid.getNode("#backup-keep-count").value = "0";
  await invalid.getNode("#preview-retention").emit("click");
  assert.match(invalid.getNode("#retention-feedback").textContent, /1–200/);
  assert.equal(invalid.requests.some(({ url }) => url.startsWith("/api/backups/plan")), false);

  const h = await harness({ fail: true });
  await h.settings.loadSettings();
  await h.getNode("#preview-retention").emit("click");
  assert.match(h.getNode("#retention-panel").innerHTML, /&lt;bad&gt;|预览失败/);
  assert.doesNotMatch(h.getNode("#retention-panel").innerHTML, /<bad>/);
  h.setFail(false);
  h.clickRetry();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(h.getNode("#retention-panel").innerHTML, /共 3 份/);
});


test("the settings page can write a model key locally without echoing it", async () => {
  const h = await harness({ capabilities: { backup_plan: true, model_key_write: true } });
  await h.settings.loadSettings();
  assert.equal(h.getNode("#model-key-panel").hidden, false);
  assert.equal(h.getNode("#model-key-input").type, undefined); // the real browser verifies type=password

  const invalid = await harness({ capabilities: { backup_plan: true, model_key_write: true } });
  await invalid.settings.loadSettings();
  invalid.getNode("#model-key-input").value = "short";
  await invalid.getNode("#model-key-form").emit("submit");
  assert.match(invalid.getNode("#model-key-feedback").textContent, /8–200/);
  assert.equal(invalid.requests.some(({ url }) => url === "/api/config/model-key"), false);

  const key = "sk-" + "a".repeat(32);
  h.getNode("#model-key-input").value = key;
  const pending = h.getNode("#model-key-form").emit("submit");
  await new Promise((resolve) => setImmediate(resolve));
  h.getNode("#confirm-accept").emit("click");
  await pending;
  const request = h.requests.find(({ url }) => url === "/api/config/model-key");
  assert.ok(request, "confirmed submit writes once");
  assert.deepEqual(JSON.parse(request.options.body), { api_key: key, confirm: true });
  assert.equal(h.getNode("#model-key-input").value, "");
  assert.doesNotMatch(h.getNode("#model-key-feedback").textContent, /sk-a{10}/);
  assert.match(h.getNode("#model-key-feedback").textContent, /已写入本地 \.env/);

  const older = await harness({ capabilities: { backup_plan: true } });
  await older.settings.loadSettings();
  assert.equal(older.getNode("#model-key-panel").hidden, true);
});
