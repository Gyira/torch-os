import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the read-only Vault audit panel. No network, no model.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

class NodeDouble {
  constructor(id = "") {
    this.id = id;
    this.listeners = new Map();
    this.dataset = {};
    this.value = "";
    this.disabled = false;
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

const report = {
  generated_at: "2026-10-04T17:30:00+08:00",
  checked_files: 9,
  ok_files: 7,
  counts: { missing_frontmatter: 1, invalid_frontmatter: 0, missing_fields: 0, duplicate_id: 0, empty_body: 1, decode_failed: 0, too_large: 0, unreadable: 0 },
  issues: [
    { path: "<img src=x>.md", code: "missing_frontmatter", detail: "文件开头没有 front-matter" },
    { path: "04-Health/health.md", code: "empty_body", detail: "front-matter 之外没有正文" },
  ],
  truncated: 0,
  note: "只读报告：只包含相对路径与问题说明，不包含任何正文内容；体检不会修改或修复文件。",
};

async function harness({ capability = true, fail = false } = {}) {
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
  const context = vm.createContext({
    document, AbortController, TypeError, Intl, Date, console,
    CustomEvent: class { constructor(type) { this.type = type; } },
    window: { setTimeout: () => 1, clearTimeout() {}, addEventListener() {} },
    fetch: async (url) => {
      requests.push({ url: String(url) });
      if (failing) return { ok: false, status: 500, json: async () => ({ detail: "<bad> 体检失败" }) };
      return { ok: true, status: 200, json: async () => structuredClone(report) };
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
  const core = (await load("core.js")).namespace;
  core.state.config = { capabilities: capability ? { vault_audit: true } : {} };
  const audit = (await load("vault-audit.js")).namespace;
  audit.initVaultAudit();
  return {
    getNode, requests, audit, document,
    setFail(value) { failing = value; },
    clickRetry() {
      const target = new NodeDouble("synthetic-retry");
      target.dataset.retry = "vault-audit";
      document.dispatchEvent({ type: "click", target });
    },
  };
}

test("the vault audit runs on demand and renders escaped issue paths", async () => {
  const h = await harness();
  assert.equal(h.getNode("#run-vault-audit").disabled, false);
  assert.equal(h.requests.length, 0, "never runs without a click");

  await h.getNode("#run-vault-audit").emit("click");
  const html = h.getNode("#vault-audit-panel").innerHTML;
  assert.match(html, /检查 9 个文件/);
  assert.match(html, /正常 7/);
  assert.match(html, /缺少 front-matter 1/);
  assert.match(html, /正文为空 1/);
  assert.match(html, /&lt;img src=x&gt;\.md/);
  assert.doesNotMatch(html, /<img src=x>/);
  assert.match(html, /只读报告/);
  assert.equal(h.getNode("#run-vault-audit").disabled, false);
});

test("an older service disables the vault audit and sends no request", async () => {
  const h = await harness({ capability: false });
  assert.equal(h.getNode("#run-vault-audit").disabled, true);
  await h.getNode("#run-vault-audit").emit("click");
  assert.equal(h.requests.length, 0);
});

test("a failed vault audit keeps an escaped retry that reloads the panel", async () => {
  const h = await harness({ fail: true });
  await h.getNode("#run-vault-audit").emit("click");
  assert.match(h.getNode("#vault-audit-panel").innerHTML, /&lt;bad&gt;|体检失败/);
  assert.doesNotMatch(h.getNode("#vault-audit-panel").innerHTML, /<bad>/);
  assert.match(h.getNode("#vault-audit-panel").innerHTML, /data-retry="vault-audit"/);

  h.setFail(false);
  h.clickRetry();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(h.getNode("#vault-audit-panel").innerHTML, /检查 9 个文件/);
});
