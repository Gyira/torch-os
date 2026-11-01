import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

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
  closest() { return null; }
}

const snapshot = {
  filename: "torch-os-full-20261102T030000000000Z-abcdef12.zip.enc",
  bytes: 1024 * 1024,
  modified_at: "2026-11-02T03:00:00+00:00",
  encrypted: true,
};

async function harness(capabilities = { cloud_backup: true }, { passwordSet = true } = {}) {
  const nodes = new Map();
  const requests = [];
  const getNode = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, new NodeDouble(selector));
    return nodes.get(selector);
  };
  const document = {
    querySelector: getNode,
    querySelectorAll: () => [],
    addEventListener() {},
    dispatchEvent() { return true; },
  };
  const cloud = {
    settings: {
      directory: "D:\\BaiduSyncdisk\\薪火OS备份",
      frequency: "daily", keep: 7, encrypt: true, password_set: passwordSet,
      last_status: "completed", last_run_at: "2026-11-02T03:00:00+00:00",
      last_file: snapshot.filename, last_bytes: snapshot.bytes, last_files: 12,
    },
    job: { status: "idle" },
    snapshots: [snapshot],
  };
  const respond = (url, options) => {
    if (url === "/api/health") return { status: "ok", runtime_label: "personal" };
    if (url === "/api/config/status") return { capabilities };
    if (url === "/api/backups") return [];
    if (url.startsWith("/api/audit/summary")) return { generated_at: "x", by_type: [] };
    if (url === "/api/cloud-backup" && (!options || !options.method)) return structuredClone(cloud);
    if (url === "/api/cloud-backup/settings" && options?.method === "PUT") return structuredClone(cloud);
    if (url === "/api/cloud-backup/run" && options?.method === "POST") {
      return { ...structuredClone(cloud), job: { status: "running", progress: "正在扫描个人数据库" } };
    }
    return {};
  };
  const context = vm.createContext({
    document, AbortController, TypeError, Intl, Date, console, JSON,
    CustomEvent: class { constructor(type) { this.type = type; } },
    window: { setTimeout: () => 1, clearTimeout() {}, addEventListener() {}, innerWidth: 1200 },
    fetch: async (url, options) => {
      requests.push({ url: String(url), method: options?.method || "GET", options });
      const result = respond(String(url), options);
      return { ok: true, status: 200, json: async () => result };
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
  return { settings, getNode, requests };
}

test("cloud backup settings load and show the last complete snapshot", async () => {
  const h = await harness();
  await h.settings.loadSettings();
  assert.equal(h.getNode("#cloud-backup-card").hidden, false);
  assert.match(h.getNode("#cloud-backup-dir").value, /BaiduSyncdisk/);
  assert.equal(h.getNode("#cloud-backup-frequency").value, "daily");
  assert.equal(h.getNode("#cloud-backup-keep").value, "7");
  assert.equal(h.getNode("#cloud-backup-encrypt").checked, true);
  assert.match(h.getNode("#cloud-backup-password-hint").textContent, /已设置备份密码/);
  assert.match(h.getNode("#cloud-backup-status").innerHTML, /上次完整备份成功/);
  assert.match(h.getNode("#cloud-backup-list").innerHTML, /torch-os-full-/);
  assert.match(h.getNode("#cloud-backup-list").innerHTML, /已加密/);
});

test("saving cloud backup settings sends the form and starting a run polls status", async () => {
  const h = await harness();
  await h.settings.loadSettings();
  h.getNode("#cloud-backup-dir").value = "D:\\BaiduSyncdisk\\薪火OS备份";
  h.getNode("#cloud-backup-password").value = "";
  await h.getNode("#cloud-backup-save").emit("click");
  const saved = h.requests.find((row) => row.url === "/api/cloud-backup/settings" && row.method === "PUT");
  assert.ok(saved);
  const body = JSON.parse(saved.options.body);
  assert.equal(body.directory, "D:\\BaiduSyncdisk\\薪火OS备份");
  assert.equal(body.frequency, "daily");
  assert.equal(body.encrypt, true);
  assert.equal(body.password, "");
  assert.match(h.getNode("#cloud-backup-feedback").textContent, /已保存/);

  await h.getNode("#cloud-backup-run").emit("click");
  assert.ok(h.requests.some((row) => row.url === "/api/cloud-backup/run" && row.method === "POST"));
  assert.equal(h.getNode("#cloud-backup-run").disabled, true);
  assert.match(h.getNode("#cloud-backup-status").innerHTML, /正在创建完整快照/);
});

test("cloud backup requires a password before the first encrypted backup", async () => {
  const h = await harness(undefined, { passwordSet: false });
  await h.settings.loadSettings();
  assert.match(h.getNode("#cloud-backup-password-hint").textContent, /首次使用请设置至少 8 位密码/);
  h.getNode("#cloud-backup-dir").value = "D:\\BaiduSyncdisk\\薪火OS备份";
  h.getNode("#cloud-backup-password").value = "";
  await h.getNode("#cloud-backup-save").emit("click");
  assert.equal(h.requests.some((row) => row.method === "PUT"), false);
  assert.match(h.getNode("#cloud-backup-feedback").textContent, /密码/);

  h.getNode("#cloud-backup-password").value = "12345678";
  await h.getNode("#cloud-backup-save").emit("click");
  const saved = h.requests.find((row) => row.method === "PUT");
  assert.ok(saved);
  assert.equal(JSON.parse(saved.options.body).password, "12345678");
});
