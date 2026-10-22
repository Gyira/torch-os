import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 日记模块（工作台 2.0）：创建/追加日记、AI 润色勾选、记录模块（今天动过的文件）。No network, no model.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function harness({ confirm = true } = {}) {
  const nodes = new Map();
  const documentListeners = new Map();
  const requests = [];
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const state = { view: "workbench", config: { capabilities: {} } };
  function element(selector, dataset = {}) {
    const node = {
      id: selector, value: "", checked: false, disabled: false, hidden: false, dataset, textContent: "", innerHTML: "", listeners: new Map(),
      addEventListener(type, handler) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(handler); },
      async emit(type, fields = {}) { const event = { target: this, currentTarget: this, preventDefault() {}, ...fields }; for (const handler of this.listeners.get(type) || []) await handler(event); return event; },
    };
    nodes.set(selector, node);
    return node;
  }
  element("#journal-note-generate");
  element("#journal-note-status");
  element("#journal-note-body");
  element("#journal-note-use-model");
  element("#journal-activity");
  element("#journal-activity-refresh");
  element("#journal-date").value = "2026-10-05";
  const document = {
    querySelector: (selector) => nodes.get(selector) || null,
    querySelectorAll: () => [],
    addEventListener(type, handler) { documentListeners.set(type, handler); },
    dispatchEvent() {},
  };
  let respond = (url, options) => {
    if (url.startsWith("/api/journal/note?") && (!options || !options.method)) return { day: "2026-10-05", exists: false, content: "", relpath: "02-Life/journal/2026-10-05.md", path: "C:\\vault\\02-Life\\journal\\2026-10-05.md", vault_name: "个人知识库", updated_at: null };
    if (url === "/api/journal/note/generate") {
      const body = JSON.parse(options.body);
      return { day: body.day, exists: true, created: body.mode === "create", appended: body.mode === "append", content: "# 2026.10.05（第41周，周日）\n\n## 自动汇总 · 20:00\n- 待办：完成 1 / 1 项", relpath: `02-Life/journal/${body.day}.md`, path: `C:\\vault\\02-Life\\journal\\${body.day}.md`, vault_name: "个人知识库", updated_at: "2026-10-05T20:00:00+08:00" };
    }
    if (url.startsWith("/api/vault/activity")) return { day: "2026-10-05", count: 0, files: [] };
    return {};
  };
  const core = {
    $: (selector) => nodes.get(selector) || null,
    state,
    escapeHtml,
    formatRelativeDate: (value) => String(value ?? ""),
    jsonBody: (method, body) => ({ method, body: JSON.stringify(body) }),
    setStatus: (selector, message, tone) => { const node = nodes.get(selector); if (node) { node.textContent = message; node.tone = tone || ""; } },
    toast: () => {},
    confirmAction: async () => confirm,
    api: async (url, options = {}) => { requests.push({ url, options }); return respond(url, options); },
  };
  const context = vm.createContext({ document, window: { addEventListener() {} }, Intl, Date, console, CustomEvent: class {} });
  const coreModule = new vm.SyntheticModule(Object.keys(core), function () { for (const [key, value] of Object.entries(core)) this.setExport(key, value); }, { context });
  const modules = new Map([[path.join(frontend, "core.js"), coreModule]]);
  const pending = new Map();
  async function load(filename) {
    if (modules.has(filename)) return modules.get(filename);
    if (pending.has(filename)) return pending.get(filename);
    const promise = (async () => {
      const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context, identifier: filename });
      await module.link((specifier, parent) => load(path.resolve(path.dirname(parent.identifier), specifier)));
      await module.evaluate();
      modules.set(filename, module);
      pending.delete(filename);
      return module;
    })();
    pending.set(filename, promise);
    return promise;
  }
  const module = (await load(path.join(frontend, "journal-note.js"))).namespace;
  module.initJournalNote();
  return { module, get: (selector) => nodes.get(selector), requests, documentListeners, setResponse(callback) { respond = callback; } };
}

const NOTE_READ = (overrides = {}) => (url, options) => {
  if (url.startsWith("/api/journal/note?") && (!options || !options.method)) {
    return { day: "2026-10-05", exists: true, content: "# 我手写的\n", relpath: "02-Life/journal/2026-10-05.md", path: "C:\\vault\\02-Life\\journal\\2026-10-05.md", vault_name: "个人知识库", updated_at: "2026-10-05T09:00:00+08:00", ...overrides };
  }
  if (url === "/api/journal/note/generate") return { day: "2026-10-05", exists: true, appended: true, content: "# 我手写的\n\n---\n\n## 自动汇总", relpath: "02-Life/journal/2026-10-05.md", path: "C:\\vault\\02-Life\\journal\\2026-10-05.md", vault_name: "个人知识库", updated_at: "2026-10-05T20:00:00+08:00" };
  if (url.startsWith("/api/vault/activity")) return { day: "2026-10-05", count: 0, files: [] };
  return {};
};

test("a missing diary offers creation and escapes what it renders", async () => {
  const h = await harness();
  h.setResponse((url, options) => {
    if (url.startsWith("/api/journal/note?") && (!options || !options.method)) return { day: "2026-10-05", exists: false, content: "", relpath: "02-Life/journal/2026-10-05.md", path: "C:\\vault\\02-Life\\journal\\2026-10-05.md", vault_name: "个人知识库", updated_at: null };
    if (url === "/api/journal/note/generate") return { day: "2026-10-05", exists: true, created: true, content: "<b>不该是 HTML</b>\n## 自动汇总", relpath: "02-Life/journal/2026-10-05.md", path: "C:\\vault\\02-Life\\journal\\2026-10-05.md", vault_name: "个人知识库", updated_at: "2026-10-05T20:00:00+08:00" };
    if (url.startsWith("/api/vault/activity")) return { day: "2026-10-05", count: 0, files: [] };
    return {};
  });
  await h.module.loadJournalNote("2026-10-05");
  assert.equal(h.get("#journal-note-generate").disabled, false);
  assert.equal(h.get("#journal-note-generate").textContent, "创建今天的日记");
  assert.match(h.get("#journal-note-body").innerHTML, /还没有日记/);

  await h.get("#journal-note-generate").emit("click");
  const post = h.requests.find(({ url }) => url === "/api/journal/note/generate");
  assert.deepEqual(JSON.parse(post.options.body), { day: "2026-10-05", mode: "create", use_model: false });
  const html = h.get("#journal-note-body").innerHTML;
  assert.equal(html.includes("<b>"), false);
  assert.ok(html.includes("&lt;b&gt;"));
  assert.ok(html.includes("02-Life/journal/2026-10-05.md"));
  assert.match(h.get("#journal-note-status").textContent, /已按模板生成/);
});

test("the AI polish checkbox is explicit and travels with the create request", async () => {
  const h = await harness();
  h.setResponse((url, options) => {
    if (url.startsWith("/api/journal/note?") && (!options || !options.method)) return { day: "2026-10-05", exists: false, content: "", relpath: "02-Life/journal/2026-10-05.md", path: "C:\\vault\\02-Life\\journal\\2026-10-05.md", vault_name: "个人知识库", updated_at: null };
    if (url === "/api/journal/note/generate") return { day: "2026-10-05", exists: true, created: true, content: "> AI 润色初稿\n\n今天…", relpath: "02-Life/journal/2026-10-05.md", path: "C:\\vault\\02-Life\\journal\\2026-10-05.md", vault_name: "个人知识库", updated_at: "2026-10-05T20:00:00+08:00" };
    if (url.startsWith("/api/vault/activity")) return { day: "2026-10-05", count: 0, files: [] };
    return {};
  });
  await h.module.loadJournalNote("2026-10-05");
  h.get("#journal-note-use-model").checked = true;
  await h.get("#journal-note-generate").emit("click");
  const post = h.requests.find(({ url }) => url === "/api/journal/note/generate");
  assert.equal(JSON.parse(post.options.body).use_model, true);
});

test("an existing diary is file-first: append asks first and only sends after a yes", async () => {
  const h = await harness({ confirm: false });
  h.setResponse(NOTE_READ());
  await h.module.loadJournalNote("2026-10-05");
  assert.equal(h.get("#journal-note-generate").textContent, "追加自动汇总");
  assert.match(h.get("#journal-note-body").innerHTML, /文件优先/);
  assert.ok(h.get("#journal-note-body").innerHTML.includes("obsidian://open"), "带保险库名时给出 Obsidian 打开链接");

  await h.get("#journal-note-generate").emit("click");
  assert.equal(h.requests.some(({ url }) => url === "/api/journal/note/generate"), false);
});

test("the activity timeline lists today's touched files with an escape check", async () => {
  const h = await harness();
  h.setResponse((url, options) => {
    if (url.startsWith("/api/vault/activity")) {
      return { day: "2026-10-05", count: 2, files: [
        { title: "<img src=x>恋爱日志", mtime: "2026-10-05T21:30:00", path: "02-Life/恋爱日志/恋爱日志.md" },
        { title: "读书笔记", mtime: "2026-10-05T09:05:00", path: "03-Learning/读书笔记/读书笔记.md" },
      ] };
    }
    if (url.startsWith("/api/journal/note?") && (!options || !options.method)) return { day: "2026-10-05", exists: false, content: "", relpath: "02-Life/journal/2026-10-05.md", path: "x", vault_name: "个人知识库", updated_at: null };
    return {};
  });
  await h.module.loadActivity("2026-10-05");
  const html = h.get("#journal-activity").innerHTML;
  assert.ok(html.includes("21:30"));
  assert.ok(html.includes("读书笔记"));
  assert.equal(html.includes("<img"), false);
  assert.ok(html.includes("&lt;img"));
  assert.ok(html.includes("2 个文件"));
});

test("day changes reload the diary and the activity together", async () => {
  const h = await harness();
  const handler = h.documentListeners.get("journal-day-changed");
  assert.equal(typeof handler, "function");
  await handler({ detail: { day: "2026-10-04" } });
  const noteRequest = h.requests.find(({ url }) => url.startsWith("/api/journal/note?"));
  assert.match(noteRequest.url, /day=2026-10-04/);
  const activityRequest = h.requests.find(({ url }) => url.startsWith("/api/vault/activity"));
  assert.match(activityRequest.url, /day=2026-10-04/);
});

test("a read failure keeps the button disabled and shows the reason", async () => {
  const h = await harness();
  h.setResponse(() => { throw Object.assign(new Error("后端未启动"), { status: 500 }); });
  await h.module.loadJournalNote("2026-10-05");
  assert.equal(h.get("#journal-note-generate").disabled, true);
  assert.match(h.get("#journal-note-status").textContent, /日记读取失败：后端未启动/);
});
