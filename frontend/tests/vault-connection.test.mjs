import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the Obsidian connection panel (core.js is stubbed).
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

class NodeDouble {
  constructor(id = "") {
    this.id = id;
    this.listeners = new Map();
    this.dataset = {};
    this.value = "";
    this.checked = false;
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

async function harness({ capabilities = { vault_connection: true }, connection = null, preview = null, saved = null, onboarding: report = null, confirmAnswer = true, saveError = "" } = {}) {
  const nodes = new Map();
  const requests = [];
  const documentListeners = new Map();
  const getNode = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, new NodeDouble(selector));
    return nodes.get(selector);
  };
  const document = {
    activeElement: null,
    querySelector: getNode,
    querySelectorAll: () => [],
    addEventListener(type, callback) { if (!documentListeners.has(type)) documentListeners.set(type, []); documentListeners.get(type).push(callback); },
    dispatchEvent(event) { for (const callback of documentListeners.get(event?.type) || []) callback(event); return true; },
  };
  const state = { view: "settings", config: { capabilities } };
  const core = new vm.SyntheticModule(
    ["$", "state", "api", "escapeHtml", "jsonBody", "setStatus", "toast", "confirmAction"],
    function () {
      this.setExport("$", getNode);
      this.setExport("state", state);
      this.setExport("api", async (url, options = {}) => {
        requests.push({ url, options });
        if (url === "/api/vault/connection" && (options.method || "GET") === "GET") return connection;
        if (url === "/api/vault/connection" && options.method === "POST") {
          if (saveError) throw new Error(saveError);
          return saved;
        }
        if (url === "/api/vault/scan-preview") return preview;
        if (url === "/api/vault/onboarding.json") return report;
        throw new Error(`unexpected ${url}`);
      });
      this.setExport("escapeHtml", (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character])));
      this.setExport("jsonBody", (method, data) => ({ method, body: JSON.stringify(data) }));
      this.setExport("setStatus", (selector, message, tone = "") => { getNode(selector).textContent = message; getNode(selector).tone = tone; });
      this.setExport("toast", (message) => { getNode("#toast").textContent = message; });
      this.setExport("confirmAction", async () => confirmAnswer);
    },
    { context: vm.createContext({ document }) },
  );
  const filename = path.join(frontend, "vault-connection.js");
  const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context: core.context, identifier: filename });
  await module.link((specifier, referring) => {
    const resolved = path.resolve(path.dirname(referring.identifier), specifier);
    if (resolved === path.join(frontend, "core.js")) return core;
    throw new Error(`unexpected import ${specifier}`);
  });
  await module.evaluate();
  return { namespace: module.namespace, requests, getNode, state, document };
}

test("the onboarding report renders checks and offers the markdown download", async () => {
  const h = await harness({
    connection: connectionPayload,
    onboarding: {
      schema: 1,
      generated_at: "2026-10-05T01:10:00+08:00",
      connection: connectionPayload,
      diff: { would_index: 4, new: 3, changed: 1, unchanged: 8, missing: 2, excluded: 1, oversized: 0, unreadable: 0, with_source_id: 5, without_source_id: 7, duplicate_ids: [], duplicate_id_count: 0, top_level: { "01-<b>Notes</b>": 9, "02-Area": 3 } },
      checks: [
        { key: "path", level: "ok", label: "目录存在", detail: "路径可读" },
        { key: "duplicates", level: "warn", label: "重复记忆 ID", detail: "2 组重复：dup-1" },
      ],
      next_steps: ["先确认只读模式。", "运行扫描。", "想写作时再关闭只读。"],
      note: "报告只统计路径、大小与哈希；不包含任何笔记正文。",
    },
  });
  h.namespace.initVaultConnection();
  await h.getNode("#vault-onboarding").emit("click");
  assert.equal(h.requests.at(-1).url, "/api/vault/onboarding.json");
  const html = h.getNode("#vault-report").innerHTML;
  assert.equal(html.includes("<b>"), false);
  assert.ok(html.includes("&lt;b&gt;"));
  assert.match(html, /通过<\/strong> · 目录存在/);
  assert.match(html, /注意<\/strong> · 重复记忆 ID/);
  assert.match(h.getNode("#vault-connection-status").textContent, /真正扫描会索引 4 篇/);
  assert.match(html, /下载 Markdown 报告/);
  assert.equal(h.getNode("#vault-report-download") !== undefined, true);
});

const connectionPayload = {
  path: "D:\\Obsidian\\<img src=x onerror=1>",
  exists: true,
  looks_like_obsidian: true,
  read_only: true,
  markdown_files: 128,
  total_bytes: 2_500_000,
  truncated: false,
  skipped_directories: [".obsidian", ".trash"],
  sample_paths: ["Notes/a.md"],
  writable: false,
  restart_required_for_changes: true,
  note: "只读模式下扫描与检索照常，任何写入（新建笔记、提炼笔记、导入）都会被拒绝。",
};

test("loading renders the vault shape with escaped paths and badges", async () => {
  const h = await harness({ connection: connectionPayload });
  h.namespace.initVaultConnection();
  await h.namespace.loadVaultConnection();
  const html = h.getNode("#vault-connection-body").innerHTML;
  assert.equal(html.includes("<img"), false);
  assert.ok(html.includes("&lt;img"));
  assert.match(html, /看起来是 Obsidian 库/);
  assert.match(html, /只读安全模式/);
  assert.match(html, /128/);
  assert.match(html, /2\.4 MB/);
  assert.equal(h.getNode("#vault-read-only").checked, true);
  assert.match(h.getNode("#vault-restart-note").textContent, /重启本地服务后生效/);
  assert.equal(h.getNode("#vault-save").disabled, false);
});

test("the read-only preview reports counts without saving", async () => {
  const h = await harness({
    connection: connectionPayload,
    preview: {
      path: "D:\\Obsidian", scanned: 12, new: 3, changed: 1, unchanged: 8, missing: 2, excluded: 4,
      unreadable: 0, would_index: 4, truncated: false,
      samples: { new: ["Notes/<b>new</b>.md"], changed: [], unreadable: [], missing: ["Gone.md"] },
      note: "这是只读预览：没有写入数据库，也没有改动任何文件。",
    },
  });
  h.namespace.initVaultConnection();
  await h.getNode("#vault-preview-scan").emit("click");
  const request = h.requests.at(-1);
  assert.equal(request.url, "/api/vault/scan-preview");
  assert.equal(request.options.method, "POST");
  const html = h.getNode("#vault-scan-preview").innerHTML;
  assert.equal(html.includes("<b>"), false);
  assert.match(html, /新增 3、变化 1、未变 8/);
  assert.match(html, /真正扫描时会索引 4 篇/);
  assert.match(h.getNode("#vault-connection-status").textContent, /会索引 4 篇/);
});

test("saving writes the path and read-only choice after confirmation", async () => {
  const h = await harness({ connection: connectionPayload, saved: { ...connectionPayload, read_only: false, saved: true, note: "已写入本地 .env；重启本地服务后生效。" } });
  h.namespace.initVaultConnection();
  await h.namespace.loadVaultConnection();
  h.getNode("#vault-path").value = "D:\\Obsidian\\MyVault";
  h.getNode("#vault-read-only").checked = false;
  await h.getNode("#vault-form").emit("submit");
  const request = h.requests.at(-1);
  assert.deepEqual(JSON.parse(request.options.body), { path: "D:\\Obsidian\\MyVault", read_only: false });
  assert.match(h.getNode("#vault-connection-status").textContent, /已写入本地 \.env/);

  const declined = await harness({ connection: connectionPayload, confirmAnswer: false });
  declined.namespace.initVaultConnection();
  declined.getNode("#vault-path").value = "D:\\Other";
  await declined.getNode("#vault-form").emit("submit");
  assert.equal(declined.requests.filter((item) => item.options?.method === "POST").length, 0);

  const old = await harness({ capabilities: {} });
  old.namespace.initVaultConnection();
  await old.namespace.loadVaultConnection();
  assert.equal(old.requests.length, 0);
  assert.equal(old.getNode("#vault-save").disabled, true);
  assert.match(old.getNode("#vault-connection-support").textContent, /尚未支持 Vault 连接设置/);
});
