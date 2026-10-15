import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the share-link import panel (core.js and library.js are stubbed).
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

class NodeDouble {
  constructor(id = "") {
    this.id = id;
    this.listeners = new Map();
    this.dataset = {};
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

async function harness({ capabilities = { share_import: true }, preview = null, apply = null, previewError = "" } = {}) {
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
  const state = { view: "documents", config: { capabilities } };
  let refreshed = 0;
  const core = new vm.SyntheticModule(
    ["$", "state", "api", "escapeHtml", "jsonBody", "setStatus", "toast", "changed"],
    function () {
      this.setExport("$", getNode);
      this.setExport("state", state);
      this.setExport("api", async (url, options = {}) => {
        requests.push({ url, options });
        if (url === "/api/imports/share/preview") {
          if (previewError) throw new Error(previewError);
          return preview;
        }
        if (url === "/api/imports/share/apply") return apply;
        throw new Error(`unexpected ${url}`);
      });
      this.setExport("escapeHtml", (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character])));
      this.setExport("jsonBody", (method, data) => ({ method, body: JSON.stringify(data) }));
      this.setExport("setStatus", (selector, message, tone = "") => { getNode(selector).textContent = message; getNode(selector).tone = tone; });
      this.setExport("toast", (message) => { getNode("#toast").textContent = message; });
      this.setExport("changed", () => { refreshed += 1; });
    },
    { context: vm.createContext({ document }) },
  );
  const library = new vm.SyntheticModule(["loadDocuments"], function () {
    this.setExport("loadDocuments", async () => { refreshed += 1; });
  }, { context: core.context });
  const modules = new Map([[path.join(frontend, "core.js"), core], [path.join(frontend, "library.js"), library]]);
  const filename = path.join(frontend, "share-import.js");
  const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context: core.context, identifier: filename });
  modules.set(filename, module);
  await module.link((specifier, referring) => modules.get(path.resolve(path.dirname(referring.identifier), specifier)));
  await module.evaluate();
  return { namespace: module.namespace, requests, getNode, state, document, refreshed: () => refreshed };
}

const previewPayload = {
  share_id: "abc123",
  title: "虚构分享 <img src=x onerror=1>",
  model_type: "default",
  message_count: 2,
  kept: 2,
  sentences: [{ text: "我打算每周三晚上学习英语。", message_id: 1 }, { text: "<script>alert(1)</script>", message_id: 2 }],
  skipped: { question: 1, chatter: 0, short: 1, duplicate: 0, over_limit: 0 },
  existing_document_id: null,
  note_path: "90-Inbox/deepseek-share-abc123.md",
};

test("the preview button waits for a link and renders escaped counts", async () => {
  const h = await harness({ preview: previewPayload });
  h.namespace.initShareImport();
  assert.equal(h.getNode("#share-preview").disabled, true);
  h.getNode("#share-url").value = "https://chat.deepseek.com/share/abc123";
  await h.getNode("#share-url").emit("input");
  assert.equal(h.getNode("#share-preview").disabled, false);

  await h.getNode("#share-preview").emit("click");
  const request = h.requests.at(-1);
  assert.deepEqual(request, { url: "/api/imports/share/preview", options: { method: "POST", body: JSON.stringify({ url: "https://chat.deepseek.com/share/abc123" }) } });
  const html = h.getNode("#share-result").innerHTML;
  assert.equal(html.includes("<img"), false);
  assert.equal(html.includes("<script"), false);
  assert.ok(html.includes("&lt;img"));
  assert.match(html, /将写入 2 条候选/);
  assert.match(html, /跳过：问句 1、寒暄 0、太短 1、重复 0/);
  assert.match(h.getNode("#share-status").textContent, /可提炼 2 条/);
  assert.equal(h.getNode("#share-apply").disabled, false);
});

test("importing needs the preview first and then writes with explicit confirmation", async () => {
  const h = await harness({
    preview: previewPayload,
    apply: { ...previewPayload, written: true, changed: true, document_id: 5, memory_count: 2, candidates: [] },
  });
  h.namespace.initShareImport();
  h.getNode("#share-url").value = "abc123";
  await h.getNode("#share-url").emit("input");
  await h.getNode("#share-preview").emit("click");
  const before = h.requests.length;
  await h.getNode("#share-apply").emit("click");
  const apply = h.requests.at(-1);
  assert.equal(h.requests.length, before + 1);
  assert.deepEqual(JSON.parse(apply.options.body), { url: "abc123", confirm: true });
  assert.match(h.getNode("#share-status").textContent, /已生成 2 条待确认候选/);
  assert.ok(h.refreshed() >= 1);
});

test("an empty share and an older service both stay safe", async () => {
  const empty = await harness({ preview: { ...previewPayload, kept: 0, sentences: [] } });
  empty.namespace.initShareImport();
  empty.getNode("#share-url").value = "abc123";
  await empty.getNode("#share-url").emit("input");
  await empty.getNode("#share-preview").emit("click");
  assert.match(empty.getNode("#share-result").innerHTML, /没有可提炼的陈述句/);
  assert.equal(empty.getNode("#share-apply").disabled, true);

  const failed = await harness({ preview: previewPayload, previewError: "分享链接被重定向，已停止抓取" });
  failed.namespace.initShareImport();
  failed.getNode("#share-url").value = "abc123";
  await failed.getNode("#share-url").emit("input");
  await failed.getNode("#share-preview").emit("click");
  assert.match(failed.getNode("#share-status").textContent, /预览失败：分享链接被重定向/);
  assert.equal(failed.getNode("#share-apply").disabled, true);

  const old = await harness({ capabilities: {} });
  old.namespace.initShareImport();
  assert.equal(old.getNode("#share-url").disabled, true);
  assert.equal(old.getNode("#share-preview").disabled, true);
  assert.match(old.getNode("#share-support").textContent, /尚未支持分享链接导入/);
});
