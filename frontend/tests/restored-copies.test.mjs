import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the restored-copy inventory and verification panel. Read-only.
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

const COPY = "92c663f3fc5c4b47b46413b0132c8892";
const inventory = {
  generated_at: "2026-10-04T19:40:00+08:00",
  total: 2,
  total_bytes: 900 * 1024,
  copies: [
    { name: COPY, modified_at: "2026-10-04T19:30:00+08:00", file_count: 12, total_bytes: 800 * 1024, vault_files: 10, has_database: true },
    { name: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", modified_at: "2026-10-03T10:00:00+08:00", file_count: 3, total_bytes: 100 * 1024, vault_files: 1, has_database: false },
  ],
  note: "只读列表：只统计文件数量与大小，不读取正文，不删除、不移动、不切换任何数据。",
};

const verification = {
  name: COPY,
  checked_at: "2026-10-04T19:41:00+08:00",
  ok: true,
  sqlite_integrity: "ok",
  sqlite_user_version: 0,
  file_count: 12,
  total_bytes: 800 * 1024,
  vault_files: 10,
  vault_bytes: 700 * 1024,
  note: "只读验证：直接读取该副本自己的数据库，不改动副本、当前数据或备份文件。",
};

async function harness({ capabilities = { restored_copies: true, backup_plan: true }, failList = false, failVerify = false } = {}) {
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
  let listFails = failList;
  let verifyFails = failVerify;
  const respond = (url) => {
    if (url === "/api/health") return { status: "ok", version: "0.31.0", runtime_label: "personal" };
    if (url === "/api/config/status") {
      return { model_configured: false, provider: "local", model_name: "未配置", state: "local", capabilities };
    }
    if (url === "/api/backups") return [];
    if (url === "/api/backups/restored") {
      if (listFails) return { httpError: 500, message: "<bad> 读取失败" };
      return structuredClone(inventory);
    }
    if (url.startsWith(`/api/backups/restored/${COPY}/verify`)) {
      if (verifyFails) return { httpError: 409, message: "<bad> 数据库损坏" };
      return structuredClone(verification);
    }
    if (url.startsWith("/api/backups/plan")) {
      return { generated_at: "x", keep: 10, total: 0, total_bytes: 0, keep_bytes: 0, remove_bytes: 0, keep_list: [], remove_list: [], note: "只读预览" };
    }
    if (url.startsWith("/api/audit/summary")) return { generated_at: "x", window_days: 30, total_events: 0, chat: {}, exports: 0, backups: 0, errors: 0, by_type: [], note: "只读统计" };
    return {};
  };
  const context = vm.createContext({
    document, AbortController, TypeError, Intl, Date, console,
    CustomEvent: class { constructor(type) { this.type = type; } },
    window: { setTimeout: () => 1, clearTimeout() {}, addEventListener() {} },
    fetch: async (url, options) => {
      requests.push({ url: String(url), method: options?.method || "GET" });
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
    settings, getNode, requests, document,
    setListFail(value) { listFails = value; },
    setVerifyFail(value) { verifyFails = value; },
    clickVerify(name) {
      const target = new NodeDouble("synthetic-verify");
      target.dataset.verifyRestored = name;
      document.dispatchEvent({ type: "click", target });
    },
    clickRetry() {
      const target = new NodeDouble("synthetic-retry");
      target.dataset.retry = "restored";
      document.dispatchEvent({ type: "click", target });
    },
  };
}

test("restored copies list sizes, disable verify without a database and verify read-only", async () => {
  const h = await harness();
  await h.settings.loadSettings();
  assert.equal(h.getNode("#list-restored").disabled, false);
  await h.getNode("#list-restored").emit("click");
  const html = h.getNode("#restored-panel").innerHTML;
  assert.match(html, /共 2 份（900 KB）/);
  assert.match(html, /92c663f3fc5c4b47b46413b0132c8892/);
  assert.match(html, /12 个文件 · Vault 10 个/);
  assert.match(html, /缺少数据库/);
  assert.match(html, /data-verify-restored="92c663f3fc5c4b47b46413b0132c8892"/);
  assert.doesNotMatch(html, /data-verify-restored="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"/);
  assert.match(html, /不删除、不移动、不切换任何数据/);
  assert.equal(h.getNode("#restored-feedback").textContent.includes("没有改动任何数据"), true);

  h.clickVerify(COPY);
  await new Promise((resolve) => setImmediate(resolve));
  const status = h.getNode(`[data-verify-status="${COPY}"]`);
  assert.match(status.textContent, /验证通过：SQLite 完整（schema v0）/);
  assert.match(status.textContent, /12 个文件 · Vault 10 个/);
  assert.equal(h.requests.every(({ method }) => method === "GET"), true, "restored panel must only read");
});

test("an older service hides the feature and a non-hex name never reaches the API", async () => {
  const h = await harness({ capabilities: {} });
  await h.settings.loadSettings();
  assert.equal(h.getNode("#list-restored").disabled, true);
  await h.getNode("#list-restored").emit("click");
  assert.equal(h.requests.some(({ url }) => url.startsWith("/api/backups/restored")), false);

  const other = await harness();
  await other.settings.loadSettings();
  other.clickVerify("../../etc-passwd");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(other.requests.some(({ url }) => url.includes("etc-passwd")), false);
});

test("failures stay escaped and a retry reloads the inventory", async () => {
  const h = await harness({ failList: true });
  await h.settings.loadSettings();
  await h.getNode("#list-restored").emit("click");
  assert.match(h.getNode("#restored-panel").innerHTML, /&lt;bad&gt;|读取失败/);
  assert.doesNotMatch(h.getNode("#restored-panel").innerHTML, /<bad>/);
  h.setListFail(false);
  h.clickRetry();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(h.getNode("#restored-panel").innerHTML, /共 2 份/);

  const verifyFail = await harness({ failVerify: true });
  await verifyFail.settings.loadSettings();
  await verifyFail.getNode("#list-restored").emit("click");
  verifyFail.clickVerify(COPY);
  await new Promise((resolve) => setImmediate(resolve));
  const status = verifyFail.getNode(`[data-verify-status="${COPY}"]`);
  assert.match(status.textContent, /验证失败/);
  assert.match(status.textContent, /数据库损坏/);
  // The message stays plain text (never assigned as innerHTML), so it cannot render as HTML.
  assert.match(status.textContent, /<bad>/);
  assert.equal(status.innerHTML, "");
});
