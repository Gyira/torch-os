import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// A small DOM double exercises event logic without a server or model request.
// It does not replace browser layout or accessibility verification.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

class NodeDouble {
  constructor(id = "") {
    this.id = id;
    this.listeners = new Map();
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.value = "";
    this.disabled = false;
    this.checked = false;
    this.hidden = false;
    this.isConnected = true;
    this.options = [];
    this.textContent = "";
    this.innerHTML = "";
    this.scrollHeight = 100;
    // 记录 CSSOM 写入的自定义属性：页面 CSP 会拦内联 style 属性，颜色必须走这条通道。
    this.style = {
      props: new Map(),
      setProperty(name, value) { this.props.set(name, String(value)); },
      removeProperty(name) { this.props.delete(name); },
      getPropertyValue(name) { return this.props.get(name) || ""; },
    };
    // 会真的记进 className（2026-10-11 起：日历视图要靠类名断言 is-today / heat-N）
    this.classList = {
      add: (...names) => { this.className = [...new Set([...String(this.className).split(/\s+/).filter(Boolean), ...names])].join(" "); },
      remove: (...names) => { this.className = String(this.className).split(/\s+/).filter((name) => name && !names.includes(name)).join(" "); },
      toggle: (name, force) => {
        const has = String(this.className).split(/\s+/).includes(name);
        const want = force === undefined ? !has : Boolean(force);
        if (want && !has) this.classList.add(name);
        if (!want && has) this.classList.remove(name);
      },
      contains: (name) => String(this.className).split(/\s+/).includes(name),
    };
  }
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(callback);
  }
  async emit(type, values = {}) {
    const event = { target: this, currentTarget: this, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}, ...values };
    for (const callback of this.listeners.get(type) || []) await callback(event);
    return event;
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
  appendChild(node) { node.parent = this; this.children.push(node); }
  // 标准 DOM 的 append/prepend(...nodes)：时间管理模块与顶部导航都会用。
  append(...nodes) { for (const node of nodes) this.appendChild(node); }
  prepend(...nodes) {
    for (const node of [...nodes].reverse()) {
      node.parent = this;
      this.children.unshift(node);
    }
  }
  replaceWith(node) {
    if (!this.parent) return;
    const index = this.parent.children.indexOf(this);
    if (index >= 0) this.parent.children[index] = node;
    node.parent = this.parent;
  }
  getBoundingClientRect() {
    return { x: 0, y: 0, top: 0, left: 0, right: 200, bottom: 100, width: 200, height: 100 };
  }
  replaceChildren() { this.children = []; this.innerHTML = ""; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this); }
  focus() { this.focused = true; }
  requestSubmit() { this.submitRequests = (this.submitRequests || 0) + 1; }
  showModal() { this.open = true; this.modalCount = (this.modalCount || 0) + 1; }
  close(value = "") { this.returnValue = value; this.open = false; }
  scrollIntoView() {}
  click() { this.clicks = (this.clicks || 0) + 1; }
}

async function harness() {
  const nodes = new Map();
  const getNode = (selector) => {
    if (!nodes.has(selector)) {
      const node = new NodeDouble(selector);
      node.querySelector = getNode;
      node.querySelectorAll = () => [];
      node.focus = () => { node.focused = true; document.activeElement = node; };
      nodes.set(selector, node);
    }
    return nodes.get(selector);
  };
  const document = {
    activeElement: null,
    body: new NodeDouble(),
    documentElement: new NodeDouble(),
    querySelector: getNode,
    querySelectorAll: () => [],
    getElementById: (id) => getNode(`#${id}`),
    createElement: () => { const node = new NodeDouble(); node.querySelector = getNode; node.querySelectorAll = () => []; return node; },
    // 时间管理模块的单选字段会在标签后拼一段文字节点（2026-10-08 起）
    createTextNode: (value) => { const node = new NodeDouble(); node.textContent = String(value); return node; },
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {},
  };
  const requests = [];
  const location = { hash: "#workbench" };
  const windowListeners = new Map();
  async function emitWindow(type, event) {
    for (const callback of windowListeners.get(type) || []) await callback(event);
  }
  let respond = (url) => url === "/api/conversations" ? [] : { answer: "测试回答", conversation_id: 1, citations: [], mode: "local" };
  const context = vm.createContext({
    document, AbortController, TypeError, Intl, Date, console, CustomEvent: class { constructor(type) { this.type = type; } },
    TextDecoder, TextEncoder,
    location,
    history: { pushState(_state, _title, hash) { location.hash = hash; }, replaceState(_state, _title, hash) { location.hash = hash; } },
    window: { setTimeout: () => 1, clearTimeout() {}, innerWidth: 1024, innerHeight: 768, addEventListener(type, callback) { if (!windowListeners.has(type)) windowListeners.set(type, []); windowListeners.get(type).push(callback); }, scrollTo() {}, confirm() { throw new Error("Native confirmation dialogs are forbidden in UI behavior tests"); }, localStorage: { store: new Map(), getItem(key) { return this.store.has(key) ? this.store.get(key) : null; }, setItem(key, value) { this.store.set(key, String(value)); } } },
    fetch: async (url, options) => {
      requests.push({ url, options });
      const payload = respond(url, options);
      if (payload && payload.__sse) {
        const chunks = payload.__sse.map((event) => new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`));
        let index = 0;
        return { ok: true, status: 200, json: async () => ({}), body: { getReader() { return { read: async () => index < chunks.length ? { value: chunks[index++], done: false } : { value: undefined, done: true } }; } } };
      }
      return { ok: true, status: 200, json: async () => payload };
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
  const core = (await load("core.js")).namespace;
  return { core, getNode, requests, context, load, setResponse(callback) { respond = callback; }, emitWindow };
}

test("expired local sessions instruct the user to refresh", async () => {
  const { core, context } = await harness();
  context.fetch = async () => ({ ok: false, status: 401, json: async () => ({ detail: "expired" }) });
  await assert.rejects(core.api("/api/documents"), /刷新页面/);
});
test("journal calendar uses Shanghai boundaries and rejects future or invalid dates", async () => {
  const { load } = await harness();
  const model = (await load("journal-model.js")).namespace;
  assert.equal(model.shanghaiDay(new Date("2026-10-02T15:59:59Z")), "2026-10-02");
  assert.equal(model.shanghaiDay(new Date("2026-10-02T16:00:00Z")), "2026-10-03");
  assert.equal(model.shiftDay("2026-03-01", -1), "2026-02-28");
  assert.equal(model.validJournalDay("2026-02-30", "2026-10-03"), false);
  assert.equal(model.validJournalDay("2026-10-04", "2026-10-03"), false);
  assert.equal(model.validJournalDay("2026-10-03", "2026-10-03"), true);
});

test("coverage distinguishes legacy ranges from bounded extraction and escapes warnings", async () => {
  const { core } = await harness();
  const legacy = core.coverageMarkup({ coverage_summary: { extraction_version: "legacy" } });
  assert.match(legacy, /旧版未记录候选提取范围，完整原文保留/);
  const bounded = core.coverageMarkup({ coverage_summary: { extraction_version: "paragraphs-v2", candidate_count: 40, total_candidate_segments: 52, omitted_segments: 12, truncated: true, warnings: ["<img src=x>"] } });
  assert.match(bounded, /40 条候选/);
  assert.match(bounded, /未提取 12 个/);
  assert.match(bounded, /不等于已理解全部事实/);
  assert.match(bounded, /已按当前规则提取，审核决定保持不变/);
  assert.ok(bounded.includes("&lt;img"));
  assert.equal(bounded.includes("<img"), false);
});

test("current, journal, and unknown extraction versions preserve review decisions without an upgrade button", async () => {
  const cases = [
    ["paragraphs-v2", /已按当前规则提取，审核决定保持不变/],
    ["journal-fields-v1", /日记快照按原始字段完整分片，不替换为普通段落提取/],
    ["paragraphs-v3", /需要兼容此版本后才能调整提取，保留已有审核决定/],
    ["<future-version>", /&lt;future-version&gt;/],
    [undefined, /已记录提取版本 未知/],
  ];
  for (const [version, expected] of cases) {
    const { core, setResponse, getNode, requests } = await harness();
    setResponse(() => ({ id: 9, title: "虚构资料", content: "完整本地正文", revision: 2, sha256: "a".repeat(64), coverage_summary: { extraction_version: version } }));
    await core.showDocument(9);
    const html = getNode("#dialog-content").innerHTML;
    assert.match(html, expected);
    assert.doesNotMatch(html, /id="reextract-document"/);
    assert.doesNotMatch(html, /归档这份资料/);
    assert.match(html, /id="preview-document-tasks"/);
    assert.match(html, /完整本地正文/);
    assert.equal(requests.some(({ options }) => options.method === "POST"), false);
  }
});

test("source positions label normalized body lines and identify corrected quotes", async () => {
  const { core } = await harness();
  const position = { source_start_line: 3, source_end_line: 5, source_location_basis: "document_body", confidence: "user_corrected" };
  const label = core.sourceLocationMarkup(position);
  assert.match(label, /资料正文第 3–5 行/);
  assert.match(label, /非原始文件行号/);
  assert.match(label, /对应原始摘录/);
  assert.match(core.sourceLocationMarkup({ ...position, source_location_basis: "unknown" }), /尚未记录正文行号/);
});

test("document reextraction requires explicit confirmation and passes version guards", async () => {
  const { core, requests, setResponse, getNode } = await harness();
  const doc = { id: 9, title: "虚构资料", content: "完整本地正文", revision: 2, sha256: "a".repeat(64), coverage_summary: { extraction_version: "legacy" } };
  setResponse((url) => url.endsWith("/reextract") ? { document: { ...doc, revision: 3, coverage_summary: { extraction_version: "paragraphs-v2" } }, candidate_count: 2, changed: true, warnings: [] } : doc);
  await core.showDocument(9);
  assert.match(getNode("#dialog-content").innerHTML, /完整本地正文/);
  assert.match(getNode("#dialog-content").innerHTML, /按新版提取候选/);
  assert.equal(requests.some(({ options }) => options.method === "POST"), false);
  const cancelled = getNode("#reextract-document").emit("click");
  assert.match(getNode("#confirm-message").textContent, /归档这份资料现有的候选和已确认记忆/);
  await getNode("#confirm-cancel").emit("click");
  await cancelled;
  assert.equal(requests.some(({ options }) => options.method === "POST"), false);
  // Call the current listener once; this DOM double does not remove old nodes on innerHTML assignment.
  const confirmed = getNode("#reextract-document").listeners.get("click")[0]();
  await getNode("#confirm-accept").emit("click");
  await confirmed;
  const request = requests.find(({ options }) => options.method === "POST");
  assert.equal(request.url, "/api/documents/9/reextract");
  assert.deepEqual(JSON.parse(request.options.body), { revision: 2, sha256: "a".repeat(64), confirm: true });
  assert.match(getNode("#reextract-feedback").textContent, /已生成 2 条候选/);
  assert.match(getNode("#dialog-content").innerHTML, /id="reextract-feedback"/);
  assert.doesNotMatch(getNode("#dialog-content").innerHTML, /id="reextract-document"/);
  assert.match(getNode("#dialog-content").innerHTML, /id="preview-document-tasks"/);
});

test("unchanged reextraction responses report preserved decisions without announcing new candidates", async () => {
  const { core, context, setResponse, getNode } = await harness();
  const doc = { id: 9, title: "虚构资料", content: "原文", revision: 2, sha256: "a".repeat(64), coverage_summary: { extraction_version: "legacy" } };
  let changes = 0;
  context.document.dispatchEvent = () => { changes += 1; };
  setResponse((url) => url.endsWith("/reextract") ? { document: { ...doc, coverage_summary: { extraction_version: "paragraphs-v2" } }, candidate_count: 2, changed: false, warnings: ["已使用当前提取规则，保留已有审核决定"] } : doc);
  await core.showDocument(9);
  const extracting = getNode("#reextract-document").emit("click");
  await getNode("#confirm-accept").emit("click");
  await extracting;
  assert.match(getNode("#reextract-feedback").textContent, /本次未改动候选或已确认记忆，保留已有审核决定/);
  assert.doesNotMatch(getNode("#reextract-feedback").textContent, /已生成|重新审核/);
  assert.doesNotMatch(getNode("#dialog-content").innerHTML, /id="reextract-document"/);
  assert.match(getNode("#dialog-content").innerHTML, /id="reextract-feedback"/);
  assert.equal(changes, 0);
});

test("a missing reextraction changed field does not falsely claim that candidates were generated", async () => {
  const { core, context, setResponse, getNode } = await harness();
  const doc = { id: 9, title: "虚构资料", content: "原文", revision: 2, sha256: "a".repeat(64), coverage_summary: { extraction_version: "legacy" } };
  let changes = 0;
  context.document.dispatchEvent = () => { changes += 1; };
  setResponse((url) => url.endsWith("/reextract") ? { document: { ...doc, coverage_summary: { extraction_version: "paragraphs-v2" } }, candidate_count: 2, warnings: [] } : doc);
  await core.showDocument(9);
  const extracting = getNode("#reextract-document").emit("click");
  await getNode("#confirm-accept").emit("click");
  await extracting;
  assert.match(getNode("#reextract-feedback").textContent, /未收到提取变更状态/);
  assert.doesNotMatch(getNode("#reextract-feedback").textContent, /已生成|保留已有审核决定/);
  assert.equal(changes, 0);
});

test("document reextraction conflicts require a fresh document before retry", async () => {
  const { core, context, setResponse, getNode } = await harness();
  setResponse(() => ({ id: 9, title: "虚构资料", content: "正文", revision: 2, sha256: "b".repeat(64) }));
  await core.showDocument(9);
  context.fetch = async () => ({ ok: false, status: 409, json: async () => ({ detail: "资料已变化" }) });
  const extracting = getNode("#reextract-document").emit("click");
  await getNode("#confirm-accept").emit("click");
  await extracting;
  assert.equal(getNode("#reextract-document").disabled, true);
  assert.match(getNode("#reextract-feedback").textContent, /重新打开资料原文/);
  assert.match(getNode("#dialog-content").innerHTML, /正文/);
});

test("page confirmation defaults to cancel, Escape rejects, and focus returns to its trigger", async () => {
  const { core, getNode, context } = await harness();
  const trigger = getNode("#agent-new");
  trigger.focus();
  const pending = core.confirmAction({ message: "确认提示", confirmLabel: "明确继续" });
  assert.equal(getNode("#confirm-dialog").open, true);
  assert.equal(getNode("#confirm-dialog").returnValue, "cancel");
  assert.equal(context.document.activeElement, getNode("#confirm-cancel"));
  const escape = await getNode("#confirm-dialog").emit("cancel");
  assert.equal(escape.defaultPrevented, true);
  assert.equal(await pending, false);
  assert.equal(context.document.activeElement, trigger);
  assert.equal(getNode("#confirm-dialog").open, false);
});

test("a second confirmation cannot borrow approval from an existing prompt", async () => {
  const { core, getNode } = await harness();
  const first = core.confirmAction({ title: "第一项操作", message: "第一项说明" });
  assert.equal(await core.confirmAction({ title: "第二项操作", message: "第二项说明" }), false);
  assert.equal(getNode("#confirm-dialog").modalCount, 1);
  assert.equal(getNode("#confirm-title").textContent, "第一项操作");
  await getNode("#confirm-accept").emit("click");
  assert.equal(await first, true);
  const next = core.confirmAction({ title: "下一项操作", message: "需要新的确认" });
  // A queued close event from the previous prompt must not cancel the newly opened one.
  await getNode("#confirm-dialog").emit("close");
  assert.equal(getNode("#confirm-dialog").open, true);
  await getNode("#confirm-cancel").emit("click");
  assert.equal(await next, false);
});

test("the shell keeps its skip link, main landmark and single page-title contract", async () => {
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  assert.match(html, /<a class="skip-link" href="#main-content">跳到主要内容<\/a>/);
  assert.match(html, /<main class="main" id="main-content" tabindex="-1">/);
  assert.match(html, /<h1 id="page-title" tabindex="-1">/);
  const views = [...html.matchAll(/data-view="([a-z]+)"/g)].map((match) => match[1]);
  assert.equal(views.length, 6);
  for (const view of views) {
    // 模块可以是实体页面（workbench / settings），也可以只有分区（四库）。
    const shell = new RegExp(`id="${view}-view"`).test(html);
    const panes = new RegExp(`data-module="${view}"`).test(html);
    assert.ok(shell || panes, `${view} exposes a shell or panes`);
  }
  // 工作台与设置页用页面标题；与神对话现在是同页区块，用自己的「与神对话」标签。
  assert.equal((html.match(/aria-labelledby="page-title"/g) || []).length, 2);
});

test("static markup keeps accessible names and live status regions", async () => {
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  const labelsFor = new Set([...html.matchAll(/<label\b[^>]*\bfor="([^"]+)"/g)].map((match) => match[1]));
  const unnamed = [];
  for (const match of html.matchAll(/<(input|select|textarea)\b([^>]*)>/g)) {
    const [, tag, attrs] = match;
    if (/\baria-label(?:ledby)?="/.test(attrs)) continue;
    const id = attrs.match(/\bid="([^"]+)"/)?.[1];
    if (id && labelsFor.has(id)) continue;
    const before = html.slice(Math.max(0, match.index - 200), match.index);
    if (before.lastIndexOf("<label") > before.lastIndexOf("</label>")) continue; // wrapping label
    unnamed.push(`${tag} ${id || "(no id)"}`);
  }
  assert.deepEqual(unnamed, [], "every form control needs a label, wrapping label or aria-label");

  const statusTags = [...html.matchAll(/<[a-z]+[^>]*class="[^"]*status-message[^"]*"[^>]*>/g)].map((match) => match[0]);
  assert.ok(statusTags.length >= 15, "status regions should exist");
  const silent = statusTags.filter((tag) => !/role="status"|role="alert"|aria-live/.test(tag));
  assert.deepEqual(silent, [], "status regions must be announced (role=status or aria-live)");
});

test("switching views focuses the page title and keeps aria-current in sync", async () => {
  const { load, getNode, context, setResponse } = await harness();
  setResponse((url) => url === "/api/health" ? { status: "ok" }
    : url === "/api/config/status" ? { model_configured: false, capabilities: {} }
      : []);
  const navItems = ["workbench", "life", "self", "ops", "knowledge", "settings"]
    .map((view) => { const node = getNode(`[data-view="${view}"]`); node.dataset.view = view; return node; });
  // 真实浏览器里 `.nav-item, .panel-nav-item` 会同时命中侧边栏与面板切换条；
  // 这个假 DOM 只有侧边栏节点，所以按“包含 .nav-item”来回答（0.94.0 修正）。
  context.document.querySelectorAll = (selector) => selector.includes(".nav-item") ? navItems : [];
  const app = (await load("app.js")).namespace;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(context.document.activeElement, null, "the first render must not steal focus");
  assert.equal(navItems[0].attributes["aria-current"], "page");

  assert.equal(await app.switchView("self"), true);
  assert.equal(context.document.activeElement, getNode("#page-title"));
  assert.equal(getNode("#page-title").textContent, "自我档案");
  assert.equal(navItems[2].attributes["aria-current"], "page");
  assert.equal(navItems[0].attributes["aria-current"], undefined);

  await app.switchView("knowledge");
  assert.equal(navItems[4].attributes["aria-current"], "page");
  assert.equal(navItems[2].attributes["aria-current"], undefined);
  assert.equal(context.document.activeElement, getNode("#page-title"));
});

test("a host navigate message switches modules before locating the pane", async () => {
  // dsh 侧栏的二级菜单会带 pane 发 torch:navigate；分区属于 stacked 的工作台时，
  // 必须先切到工作台再定位，否则 scrollIntoView 打在隐藏节点上，看起来像“点了没反应”。
  const { load, context, setResponse, emitWindow, core } = await harness();
  setResponse((url) => url === "/api/health" ? { status: "ok", version: "0.0.0-test" }
    : url === "/api/config/status" ? { model_configured: false, capabilities: {} } : []);
  context.location.search = "?embed=1";
  const app = (await load("app.js")).namespace;
  for (let index = 0; index < 4; index += 1) await new Promise((resolve) => setImmediate(resolve));
  assert.equal(await app.switchView("self"), true);
  assert.equal(context.location.hash, "#self");

  await emitWindow("message", { data: { type: "torch:navigate", view: "workbench", pane: "agent-view" } });
  for (let index = 0; index < 8; index += 1) await new Promise((resolve) => setImmediate(resolve));
  assert.equal(core.state.view, "workbench", "带 pane 的消息要先切到那个模块");
  assert.equal(context.location.hash, "#workbench");
});

test("the #help deep link opens settings and clicks the help section", async () => {
  // 帮助并进设置后，hashchange 的门一度只认模块名与 GO_TARGETS，#help 被静默忽略，
  // 真实浏览器探针在帮助步骤超时。
  const { load, context, getNode, core, setResponse, emitWindow } = await harness();
  setResponse((url) => url === "/api/health" ? { status: "ok", version: "0.0.0-test" }
    : url === "/api/config/status" ? { model_configured: false, capabilities: {} } : []);
  await load("app.js");
  for (let index = 0; index < 4; index += 1) await new Promise((resolve) => setImmediate(resolve));
  const helpNav = getNode('[data-settings-nav="help"]');
  assert.equal(helpNav.clicks || 0, 0);

  context.location.hash = "#help";
  await emitWindow("hashchange", {});
  for (let index = 0; index < 4; index += 1) await new Promise((resolve) => setImmediate(resolve));
  assert.equal(context.location.hash, "#settings");
  assert.equal(core.state.view, "settings");
  assert.equal(helpNav.clicks, 1, "帮助深链接要把设置页的帮助分区点开");

  // 空 hash 的后退（回到最初的 URL）应该落回工作台，而不是停留在旧页面。
  context.location.hash = "";
  await emitWindow("popstate", {});
  for (let index = 0; index < 4; index += 1) await new Promise((resolve) => setImmediate(resolve));
  assert.equal(core.state.view, "workbench");
});

test("entering the settings module runs its own panel loaders", async () => {
  // 0.96.0 起设置模块没有二级分区（ownNav），runModuleLoaders 一度跳过了它自己的
  // 加载器，导致调度状态永远为空、真实浏览器探针在 #schedule-status 超时。
  const { load, getNode, requests, setResponse } = await harness();
  setResponse((url) => url === "/api/health" ? { status: "ok", version: "0.0.0-test" }
    : url === "/api/config/status" ? { model_configured: false, capabilities: { scheduler: true } }
      : url === "/api/schedule" ? { settings: {}, today_runs: [], runtime: { consecutive_failures: 0 } }
        : []);
  const core = (await load("core.js")).namespace;
  core.state.config = { capabilities: { scheduler: true } };
  const app = (await load("app.js")).namespace;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests.some(({ url }) => url === "/api/schedule"), false, "boot 不该读调度设置");

  assert.equal(await app.switchView("settings"), true);
  for (let index = 0; index < 8; index += 1) await new Promise((resolve) => setImmediate(resolve));
  assert.ok(requests.some(({ url }) => url === "/api/schedule"), "进入设置页要读取调度设置");
  assert.match(getNode("#schedule-status").textContent, /调度/);
});

test("the sidebar second level lists exactly the panes each module defines", async () => {
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  const app = await readFile(path.join(frontend, "app.js"), "utf8");
  const modulesBlock = app.match(/const MODULES = \{([\s\S]*?)\n\};/)[1];
  const modules = {};
  // 每个模块一项：多行写法与 settings 的单行写法都要认。
  for (const entry of modulesBlock.split(/\n  (?=\w+: \{)/)) {
    const name = entry.match(/^\s*(\w+): \{/)?.[1];
    if (!name) continue;
    modules[name] = [...entry.matchAll(/\["([a-z-]+)", "([^"]+)"\]/g)].map((match) => [match[1], match[2]]);
  }
  assert.deepEqual(Object.keys(modules).sort(), [
    "knowledge", "life", "ops", "self", "settings", "workbench",
  ]);

  const groups = [...html.matchAll(/<div class="nav-sublist"[^>]*data-sublist="([a-z]+)"[^>]*>([\s\S]*?)<\/div>/g)]
    .map((match) => [match[1], [...match[2].matchAll(/data-pane="([a-z-]+)"[^>]*>([^<]+)</g)].map((pane) => [pane[1], pane[2].trim()])]);
  const sidebar = Object.fromEntries(groups);
  // 工作台与生活运营有二级菜单；其余模块自带章节轨/分区导航。
  assert.deepEqual(Object.keys(sidebar).sort(), ["ops", "workbench"]);
  for (const [view, panes] of Object.entries(sidebar)) {
    assert.deepEqual(panes, modules[view], `${view} 的二级菜单要和模块定义一致`);
  }
  // 一级项与二级项都要能在侧栏里找到；模块按钮声明了它控制哪个二级列表。
  for (const view of Object.keys(sidebar)) {
    assert.match(html, new RegExp(`data-view="${view}"[^>]*aria-controls="nav-sub-${view}"`), `${view} 的 aria-controls`);
    assert.match(html, new RegExp(`id="nav-sub-${view}"`), `${view} 的二级列表 id`);
  }
});

test("the settings shell exposes twelve navigable sections with focusable headings", async () => {
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  const nav = [...html.matchAll(/data-settings-nav="([a-z]+)"/g)].map((match) => match[1]);
  assert.deepEqual(nav, ["status", "schedule", "dida", "backup", "data", "obsidian", "migration", "audit", "appearance", "widgets", "help", "about"]);
  for (const name of nav) {
    const section = html.match(new RegExp(`data-settings-section="${name}"[^>]*>([\\s\\S]*?)</section>`));
    assert.ok(section, `${name} section exists`);
    assert.match(section[1], /tabindex="-1"/, `${name} heading is focusable`);
  }
  assert.equal((html.match(/class="settings-card[ "]/g) || []).length >= 6, true);
});

test("the appearance section lists every sidebar view and offers a reset", async () => {
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  const navOrder = await readFile(path.join(frontend, "nav-order.js"), "utf8");
  const appearance = html.match(/data-settings-section="appearance"[^>]*>([\s\S]*?)<\/section>/);
  assert.ok(appearance, "appearance section exists");
  assert.match(appearance[1], /id="nav-order-list"/);
  assert.match(appearance[1], /id="nav-order-reset"/);
  const defaultsBlock = navOrder.match(/NAV_ORDER_DEFAULT = \[([\s\S]*?)\]/)[1];
  const defaults = (defaultsBlock.match(/"([a-z]+)"/g) || []).map((value) => value.replace(/"/g, ""));
  const views = [...html.matchAll(/class="nav-item[^"]*" data-view="([a-z]+)"/g)].map((match) => match[1]);
  assert.deepEqual([...views].sort(), [...defaults].sort());
  assert.equal(defaults.length, 6);
});

test("the time dialogs collect every field value on submit", async () => {
  // 2026-10-08 的真 bug：字段没带 dataset.name，提交时取不到值 → 点“添加”没反应。
  const { load } = await harness();
  const dialogs = (await load("tasks-dialogs.js")).namespace;
  let saved = null;
  const dialog = dialogs.openListDialog({ folders: ["工作"], onSave: (values) => { saved = values; } });
  const form = dialog.children[0];
  const body = form.children.find((child) => child.className === "time-dialog-body");
  const fieldFor = (name) => body.children.find((child) => child.dataset.name === name);
  const submit = async () => form.emit("submit", { preventDefault() {} });

  // 空名称：不关窗、给提示、别静悄悄什么都不发生
  await submit();
  assert.equal(saved, null);
  const status = form.children.find((child) => child.className === "form-actions").children[0];
  assert.match(status.textContent, /名称/);
  assert.equal(dialog.open, true);

  // 填名称 + 点第 5 个颜色圆点（绿）+ 选文件夹 → 提交要把值带出来
  fieldFor("name").children[1].value = "客户E";
  fieldFor("folder").children[1].value = "工作";
  await fieldFor("color").children[1].children[4].emit("click");
  await submit();
  // 逐项比：vm 里造出来的对象原型和测试域不同，strict deepEqual 会误报
  assert.equal(saved.name, "");
  assert.equal(saved.new_name, "客户E");
  assert.equal(saved.color, "#22c55e");
  assert.equal(saved.folder, "工作");
  assert.equal(dialog.open, false); // 成功就关窗

  // 复制清单的三个范围也走同一套（单选）
  let scope = null;
  const copy = dialogs.openCopyListDialog({ name: "收集箱", onConfirm: (value) => { scope = value; } });
  const copyForm = copy.children[0];
  const copyBody = copyForm.children.find((child) => child.className === "time-dialog-body");
  const radios = copyBody.children.find((child) => child.dataset.name === "scope").children[1];
  const second = radios.children[1].children[0];   // 第二个选项 = all_keep
  second.checked = true;
  await second.emit("change");
  await copyForm.emit("submit", { preventDefault() {} });
  assert.equal(scope, "all_keep");
});

test("quick add keeps the dialog open and says why when it cannot save", async () => {
  // 用户报“点添加没反应”：空标题 / 写回失败都得有话直说 + 内容不丢（2026-10-11）
  const { load } = await harness();
  const dialogs = (await load("tasks-dialogs.js")).namespace;
  let draft = null;
  let result = true;
  const dialog = dialogs.openQuickAdd({
    day: "2026-10-11", time: "09:00", lists: [{ name: "收集箱", folder: "" }],
    onSave: (values) => { draft = values; return result; },
  });
  const form = dialog.children[0];
  const body = form.children.find((child) => child.className === "time-dialog-body");
  const fieldFor = (name) => body.children.find((child) => child.dataset.name === name);
  const status = form.children.find((child) => child.className === "form-actions").children[0];
  const submit = async () => form.emit("submit", { preventDefault() {} });

  // 预填：日期/时间/清单按调用方给的来
  assert.equal(fieldFor("date").children[1].value, "2026-10-11");
  assert.equal(fieldFor("time").children[1].value, "09:00");
  assert.equal(fieldFor("list").children[1].value, "收集箱");

  // ① 空标题：拦下、带语气色的提示、焦点回到标题输入框、窗不关
  await submit();
  assert.equal(draft, null);
  assert.match(status.textContent, /标题/);
  assert.match(status.className, /warning/);
  assert.equal(dialog.open, true);
  assert.equal(fieldFor("title").children[1].focused, true);

  // ② 写回失败（post 返回 false）：窗也不关，内容留着再试
  fieldFor("title").children[1].value = "买菜";
  result = false;
  await submit();
  assert.equal(draft.title, "买菜");
  assert.match(status.textContent, /没保存成功/);
  assert.equal(dialog.open, true);

  // ③ 成功：关窗
  result = true;
  await submit();
  assert.equal(dialog.open, false);
});

// ===== 日历七种视图（2026-10-11，第 4 轮）=====

function walk(root, found = []) {
  for (const child of root.children || []) {
    found.push(child);
    walk(child, found);
  }
  return found;
}

const hasClass = (node, name) => String(node.className || "").split(/\s+/).includes(name);
const byClass = (root, name) => walk(root).filter((node) => hasClass(node, name));
const byText = (root, text) => walk(root).filter((node) => node.textContent === text);

function calendarTask(overrides = {}) {
  return {
    key: "k", path: "00-System/任务/任务.md", legacy: false, list_name: "收集箱",
    title: "任务", done: false, done_on: "", date: "2026-10-11", time: "",
    priority: "none", tags: [], pinned: false, created: "",
    ...overrides,
  };
}

test("calendar shell draws the month grid, the header controls and the view menu", async () => {
  const { load, getNode, context } = await harness();
  const { renderCalendar } = (await load("tasks-calendar.js")).namespace;
  const container = getNode("#time-calendar-page");
  const patches = [];
  renderCalendar(container, {
    tasks: [
      calendarTask({ key: "a", title: "买菜", time: "09:00-10:00" }),
      calendarTask({ key: "b", title: "没日期的", date: "" }),
    ],
    today: "2026-10-11",
    lists: [{ name: "收集箱", color: "#3b82f6" }],
    state: { view: "month", anchor: "2026-10-11" },
    taskOptions: {},
    onState: (patch) => patches.push(patch),
  });

  // 月视图：整周补齐的格子 + 那天的小条（没日期的不出现）
  const cells = byClass(container, "time-cell");
  assert.equal(cells.length % 7, 0);
  assert.ok(cells.some((cell) => cell.dataset.day === "2026-10-11"));
  const chips = byClass(container, "time-chip");
  assert.deepEqual(chips.map((chip) => chip.dataset.taskKey), ["a"]);
  assert.equal(byText(container, "2026年10月").length, 1);

  // ‹ / › 按粒度翻月；「今天」回到锚点（vm 里造的对象跨域，先摊平成测试域的对象再比）
  const lastPatch = () => ({ ...patches.at(-1) });
  await byText(container, "‹")[0].emit("click");
  assert.deepEqual(lastPatch(), { anchor: "2026-09-11" });
  await byText(container, "›")[0].emit("click");
  assert.deepEqual(lastPatch(), { anchor: "2026-11-11" });
  await byText(container, "今天")[0].emit("click");
  assert.deepEqual(lastPatch(), { anchor: "2026-10-11" });
  // 点格子空白 = 去那天的日视图
  await cells.find((cell) => cell.dataset.day === "2026-10-12").emit("click");
  assert.deepEqual(lastPatch(), { view: "day", anchor: "2026-10-12" });
  patches.length = 0;

  // 视图一排按钮（2026-10-11 用户反馈：不要下拉）——点一下就切
  const viewButtons = byClass(container, "time-cal-view");
  assert.deepEqual(viewButtons.map((button) => button.textContent),
    ["年", "月", "周", "日", "日程", "多日", "多周"]);                        // 短名，全名在 aria-label
  assert.equal(viewButtons[5].attributes["aria-label"], "多日视图");
  assert.equal(viewButtons[1].attributes["aria-selected"], "true");          // 当前停在月视图
  assert.deepEqual(viewButtons.map((button) => button.dataset.calView),
    ["year", "month", "week", "day", "agenda", "multiday", "multiweek"]);
  assert.equal(byClass(container, "time-cal-counts").length, 0);             // 不是多日/多周就没有档位
  await viewButtons[2].emit("click");                                        // 周视图
  assert.deepEqual(lastPatch(), { view: "week" });
  await viewButtons[5].emit("click");                                        // 多日视图
  assert.deepEqual(lastPatch(), { view: "multiday" });

  // 多日/多周的档位按钮跟在激活的那颗旁边（重新画一次，带上多日状态）
  renderCalendar(container, {
    tasks: [], today: "2026-10-11", lists: [],
    state: { view: "multiday", anchor: "2026-10-11", days: 3 },
    taskOptions: {}, onState: (patch) => patches.push(patch),
  });
  const counts = byClass(container, "time-cal-count");
  assert.deepEqual(counts.map((chip) => chip.textContent), ["2 天", "3 天", "5 天", "7 天"]);
  assert.ok(hasClass(counts[1], "is-active"));                                // 3 天 = 当前档
  await counts[2].emit("click");
  assert.deepEqual(lastPatch(), { view: "multiday", days: 5 });

  // 「…」显示设置：显示已完成 + 周起始日
  const body = context.document.body;
  const more = byClass(container, "time-cal-controls")[0].children[2];
  await more.emit("click");
  await byClass(body, "time-popover-row").find((row) => row.innerHTML.includes("显示已完成任务")).emit("click");
  assert.deepEqual(lastPatch(), { showDone: true });
  await more.emit("click");
  await byClass(body, "time-popover-row").find((row) => row.innerHTML.includes("周一开头")).emit("click");
  assert.deepEqual(lastPatch(), { weekStart: 1 });
});

test("the time grid places timed tasks as blocks and untimed ones on the all-day row", async () => {
  const { load, getNode } = await harness();
  const { renderCalendarTime } = (await load("tasks-calendar-time.js")).namespace;
  const container = getNode("#cal-time");
  const opened = [];
  const added = [];
  renderCalendarTime(container, {
    tasks: [
      calendarTask({ key: "t", title: "晨跑", time: "09:00-10:30" }),
      calendarTask({ key: "u", title: "全天的事" }),
      calendarTask({ key: "v", title: "昨天的", date: "2026-10-10", time: "09:00" }),
    ],
    today: "2026-10-11",
    lists: [],
    colors: {},
    state: { view: "week", anchor: "2026-10-11", weekStart: 0, showDone: false },
    taskOptions: {},
    onOpenDay: (day) => opened.push(day),
    onQuickAdd: (day, time) => added.push([day, time]),
  });

  // 7 列 + 星期表头；时刻轴从 07:00 起（凌晨折叠），正午有专门写法
  const cols = byClass(container, "time-col");
  assert.equal(cols.length, 7);
  assert.deepEqual(cols.map((col) => col.dataset.day).slice(0, 2), ["2026-10-11", "2026-10-12"]);
  const labels = byClass(container, "time-axis-label");
  assert.equal(labels[0].textContent, "07:00");
  assert.ok(hasClass(labels[0], "is-folded"));       // 折叠带末尾已写过 -07:00，这一格不再重复（用户报过重叠）
  assert.equal(labels[1].textContent, "08:00");
  assert.equal(labels.length, 17);
  assert.ok(labels.some((label) => label.textContent === "正午"));
  assert.equal(byClass(container, "time-band-label")[0].textContent, "00:00 -07:00");

  // 时间块：9:00-10:30 → top=(540-420)/60*48=96，height=1.5h*48=72
  const blocks = byClass(container, "time-block");
  assert.deepEqual(blocks.map((block) => block.dataset.taskKey), ["t"]);
  assert.equal(blocks[0].style.top, "96px");
  assert.equal(blocks[0].style.height, "72px");
  // 没有时间的任务在「全天」区；昨天的不在这周里
  const allDayChips = byClass(container, "time-allday-col")
    .flatMap((col) => walk(col)).filter((node) => hasClass(node, "time-chip"));
  assert.deepEqual(allDayChips.map((chip) => chip.dataset.taskKey), ["u"]);

  // 点空白（09:00 的位置）→ 快速添加并预填那个时刻（半小时对齐）
  const todayCol = cols.find((col) => col.dataset.day === "2026-10-11");
  await todayCol.emit("click", { target: todayCol, clientY: 96 });
  assert.deepEqual(added.at(-1), ["2026-10-11", "09:00"]);
  // 点块本身不触发（target 不是列）
  await todayCol.emit("click", { target: blocks[0], clientY: 96 });
  assert.equal(added.length, 1);
  // 点日期数字 → 日视图
  await byClass(container, "time-day-num")[0].emit("click");
  assert.deepEqual(opened, ["2026-10-11"]);

  // 完成的任务默认不画（showDone=false），日视图只有一列
  const withDone = getNode("#cal-time-done");
  renderCalendarTime(withDone, {
    tasks: [calendarTask({ key: "d", title: "干完的", done: true })],
    today: "2026-10-11", lists: [], colors: {},
    state: { view: "day", anchor: "2026-10-11", showDone: false },
    taskOptions: {}, onOpenDay: () => {}, onQuickAdd: () => {},
  });
  assert.equal(byClass(withDone, "time-chip").length, 0);
  assert.equal(byClass(withDone, "time-col").length, 1);
});

test("year view heats the days and jumps back to the month", async () => {
  const { load, getNode } = await harness();
  const { renderCalendarGrid } = (await load("tasks-calendar-grid.js")).namespace;
  const container = getNode("#cal-year");
  const patches = [];
  renderCalendarGrid(container, {
    tasks: [
      calendarTask({ key: "a", date: "2026-03-05" }),
      calendarTask({ key: "b", date: "2026-03-05" }),
      calendarTask({ key: "c", date: "2026-03-05" }),
      calendarTask({ key: "d", date: "2026-03-06" }),
    ],
    today: "2026-10-11", lists: [], colors: {},
    state: { view: "year", anchor: "2026-10-11", weekStart: 0, showDone: false },
    taskOptions: {}, onState: (patch) => patches.push(patch),
  });
  assert.equal(byClass(container, "time-year-month").length, 12);
  const days = byClass(container, "time-year-day");
  const fifth = days.find((day) => day.dataset.day === "2026-03-05");
  assert.ok(hasClass(fifth, "heat-3"));
  assert.equal(fifth.title, "2026-03-05：3 项任务");
  assert.ok(hasClass(days.find((day) => day.dataset.day === "2026-03-06"), "heat-1"));   // 一两个任务 = 最浅一档
  await fifth.emit("click");
  assert.deepEqual({ ...patches.at(-1) }, { view: "month", anchor: "2026-03-05" });
});

test("agenda lists the next days with times and an all-day row", async () => {
  const { load, getNode } = await harness();
  const { renderAgenda } = (await load("tasks-calendar-agenda.js")).namespace;
  const container = getNode("#cal-agenda");
  const added = [];
  renderAgenda(container, {
    tasks: [
      calendarTask({ key: "a", title: "无时间的", date: "2026-10-11" }),
      calendarTask({ key: "b", title: "下午开会", date: "2026-10-11", time: "14:00-15:30" }),
      calendarTask({ key: "c", title: "几天后的", date: "2026-10-14" }),
    ],
    today: "2026-10-11", lists: [], colors: {},
    state: { view: "agenda", anchor: "2026-10-11", showDone: false },
    taskOptions: {}, onQuickAdd: (day) => added.push(day),
  });
  const days = byClass(container, "time-agenda-day");
  assert.deepEqual(days.map((day) => day.dataset.day), ["2026-10-11", "2026-10-14"]);   // 没任务的 12/13 不列
  const labels = byClass(container, "time-agenda-time").map((node) => node.textContent);
  assert.deepEqual(labels, ["全天", "14:00", "全天"]);                                 // 全天排最前
  const cards = byClass(container, "time-card");
  assert.deepEqual(cards.map((card) => card.dataset.taskKey), ["a", "b", "c"]);
  assert.equal(byText(container, "14:00-15:30").length, 1);
  await byClass(container, "time-agenda-add")[0].emit("click");
  assert.deepEqual(added, ["2026-10-11"]);
});

test("task rows expose an open-document button and keep legacy tasks read-only", async () => {
  const { load } = await harness();
  const { taskRow } = (await load("tasks.js")).namespace;
  const opened = [];
  const toggled = [];
  const base = calendarTask({
    key: "new", path: "00-System/任务/写方案.md", legacy: false,
    title: "写方案", done: false, date: "2026-10-11",
  });
  const options = {
    today: "2026-10-11", lists: [], draggable: true,
    onOpenDocument: (task) => opened.push(task.path),
    onToggle: (task, done) => toggled.push([task.key, done]),
    actions: {},
  };
  const row = taskRow(base, options);
  assert.equal(row.draggable, true);
  const dragData = new Map();
  await row.emit("dragstart", {
    dataTransfer: {
      types: ["application/x-torch-task-key"],
      effectAllowed: "",
      setData: (type, value) => dragData.set(type, value),
      getData: (type) => dragData.get(type) || "",
    },
  });
  assert.equal(dragData.get("application/x-torch-task-key"), "new");
  assert.equal(hasClass(row, "is-dragging"), true);
  await row.emit("dragend");
  assert.equal(hasClass(row, "is-dragging"), false);

  const boxes = byClass(row, "task-check");
  assert.equal(boxes.length, 1);
  assert.equal(boxes[0].disabled, false);
  await boxes[0].emit("change");
  assert.deepEqual(toggled, [["new", false]]);
  const open = byClass(row, "task-open");
  assert.equal(open.length, 1);
  await open[0].emit("click");
  assert.deepEqual(opened, ["00-System/任务/写方案.md"]);

  const legacy = taskRow({ ...base, key: "old", legacy: true, path: "00-System/任务（tasks）.md",
    title: "买菜" }, options);
  assert.equal(legacy.draggable, false);
  const legacyBox = byClass(legacy, "task-check")[0];
  assert.equal(legacyBox.disabled, true);
  await legacyBox.emit("change");
  assert.deepEqual(toggled, [["new", false]]);   // 旧任务不触发完成写操作
  assert.equal(byClass(legacy, "task-legacy-badge").length, 1);
  await byClass(legacy, "task-open")[0].emit("click");
  assert.deepEqual(opened, ["00-System/任务/写方案.md", "00-System/任务（tasks）.md"]);
});

test("sidebar list colors use CSSOM and defined lists can be dragged into order", async () => {
  const { load, getNode } = await harness();
  const { renderTaskSidebar } = (await load("tasks-sidebar.js")).namespace;
  const container = getNode("#time-sidebar");
  const moves = [];
  renderTaskSidebar(container, {
    tasks: [], folders: [], tags: [], selection: { list: "收集箱" },
    lists: [
      { name: "收集箱", color: "#3b82f6", folder: "", pinned: true, synthetic: false },
      { name: "心愿清单", color: "", folder: "", pinned: false, synthetic: false },
      { name: "自由清单", color: "", folder: "", pinned: false, synthetic: true },
    ],
    onListReorder: (payload) => moves.push(payload),
  });
  const rows = walk(container).filter((node) => node.dataset.listName);
  assert.deepEqual(rows.map((row) => row.dataset.listName), ["收集箱", "心愿清单", "自由清单"]);

  // 色点存在，颜色通过 CSSOM 的 style.setProperty 写入（页面 CSP 会丢弃 HTML 里的 style 属性）。
  const dots = walk(rows[0]).filter((node) => hasClass(node, "task-tag-dot"));
  assert.equal(dots.length, 1);
  assert.equal(dots[0].style.getPropertyValue("--tag-color"), "#3b82f6");
  // 置顶瓦片也走同一条通道，不再是灰点。
  const pinnedDots = walk(byClass(container, "time-pinned-tile")[0])
    .filter((node) => hasClass(node, "task-tag-dot"));
  assert.equal(pinnedDots.length, 1);
  assert.equal(pinnedDots[0].style.getPropertyValue("--tag-color"), "#3b82f6");

  // 定义过的清单可拖；目录里捡到的合成清单不可拖，避免排序无处可写。
  assert.equal(rows[0].draggable, true);
  assert.equal(rows[1].draggable, true);
  assert.equal(Boolean(rows[2].draggable), false);

  // 把「收集箱」拖到「心愿清单」上面：落点在上半部分 → before = 心愿清单。
  container.querySelectorAll = () => rows;
  const dragData = new Map();
  const transfer = {
    types: [],
    setData(type, value) { dragData.set(type, value); if (!this.types.includes(type)) this.types.push(type); },
    getData(type) { return dragData.get(type) || ""; },
  };
  await rows[0].emit("dragstart", { dataTransfer: transfer });
  await rows[1].emit("dragover", { dataTransfer: transfer, clientY: 10, preventDefault() {} });
  await rows[1].emit("drop", { dataTransfer: transfer, clientY: 10, preventDefault() {} });
  assert.deepEqual(moves.map((move) => ({ ...move })), [{ name: "收集箱", before: "心愿清单" }]);
  assert.equal(hasClass(rows[0], "is-list-dragging"), false);
  assert.equal(hasClass(rows[1], "is-reorder-before"), false);
});

test("standalone pages use the top module navigation instead of the outer sidebar", async () => {
  const h = await harness();
  await h.load("app.js");
  assert.equal(h.context.document.documentElement.classList.contains("top-nav-mode"), true);
  const main = h.getNode("#main-content");
  assert.equal(byClass(main, "panel-nav").length, 1);
  assert.deepEqual(byClass(main, "panel-nav-item").map((node) => node.dataset.view),
    ["workbench", "life", "self", "ops", "knowledge", "settings"]);
});

test("destructive confirmations opt into the danger style and reset for the next prompt", async () => {
  const { core, getNode } = await harness();
  const first = core.confirmAction({ message: "彻底删除", danger: true });
  assert.equal(getNode("#confirm-accept").classList.contains("danger"), true);
  assert.equal(getNode("#confirm-accept").classList.contains("primary"), false);
  await getNode("#confirm-cancel").emit("click");
  assert.equal(await first, false);

  const second = core.confirmAction({ message: "普通确认" });
  assert.equal(getNode("#confirm-accept").classList.contains("danger"), false);
  assert.equal(getNode("#confirm-accept").classList.contains("primary"), true);
  await getNode("#confirm-accept").emit("click");
  assert.equal(await second, true);
});

test("a toast action runs once, hides itself and leaves the follow-up toast", async () => {
  const { core, getNode } = await harness();
  const toastNode = getNode("#toast");
  let calls = 0;
  core.toast("已删除这条记录。", "success", {
    label: "撤销",
    timeout: 15000,
    onAction: async () => {
      calls += 1;
      core.toast("已恢复这条记录。", "success");
    },
  });
  assert.equal(toastNode.classList.contains("has-action"), true);
  const button = toastNode.children.find((node) => node.className === "toast-action");
  assert.ok(button, "undo toast exposes an action button");
  await button.emit("click");
  assert.equal(calls, 1);
  assert.equal(toastNode.children[0].textContent, "已恢复这条记录。");
  assert.equal(toastNode.classList.contains("has-action"), false);
});

test("the task-list delete confirmation can opt into the danger style", async () => {
  const { load } = await harness();
  const dialogs = (await load("tasks-dialogs.js")).namespace;
  const dialog = dialogs.confirmDialog({ title: "删除清单", message: "会移入回收站", danger: true, onConfirm: () => {} });
  const form = dialog.children[0];
  const footer = form.children.find((child) => child.className === "form-actions");
  const submit = footer.children.find((child) => child.type === "submit");
  assert.equal(hasClass(submit, "danger"), true);
  assert.equal(hasClass(submit, "primary"), false);
  const plain = dialogs.confirmDialog({ title: "解散文件夹", message: "清单和任务都不删", onConfirm: () => {} });
  const plainFooter = plain.children[0].children.find((child) => child.className === "form-actions");
  const plainSubmit = plainFooter.children.find((child) => child.type === "submit");
  assert.equal(hasClass(plainSubmit, "primary"), true);
  assert.equal(hasClass(plainSubmit, "danger"), false);
});

test("task rows show the linked goal and can filter the board by it", async () => {
  const { load } = await harness();
  const { taskRow } = (await load("tasks.js")).namespace;
  const selected = [];
  const row = taskRow(calendarTask({ goal: "00_人生路/06_目标与计划/01_年度目标/学习.md" }), {
    today: "2026-10-11", lists: [], draggable: false,
    goalLabel: () => "学习",
    onSelectGoal: (path) => selected.push(path),
    actions: {},
  });
  const chip = byClass(row, "task-goal-chip")[0];
  assert.equal(chip.textContent, "🎯 学习");
  assert.match(chip.title, /只看这个目标/);
  await chip.emit("click");
  assert.deepEqual(selected, ["00_人生路/06_目标与计划/01_年度目标/学习.md"]);
});

test("habit rows read entry_status and expose the habit badge and rest state", async () => {
  const { load } = await harness();
  const { taskRow } = (await load("tasks.js")).namespace;
  const toggled = [];
  const habit = calendarTask({
    key: "habit:h1:2026-10-11", kind: "habit", virtual: true, habit_id: 1,
    title: "每天阅读", icon: "📚", list_name: "习惯打卡",
    done: true, entry_status: "skipped", date: "2026-10-11", priority: "high",
    definition_revision: 2, entry_revision: 3, auto_log: false,
  });
  const row = taskRow(habit, {
    today: "2026-10-11", lists: [], draggable: true,
    onToggle: (task, done) => toggled.push([task.key, done]),
    actions: {},
  });
  assert.equal(hasClass(row, "is-habit"), true);
  assert.equal(hasClass(row, "is-skipped"), true);
  assert.equal(hasClass(row, "is-done"), false);
  const box = byClass(row, "task-check")[0];
  assert.equal(box.checked, false);
  await box.emit("change");
  assert.deepEqual(toggled, [["habit:h1:2026-10-11", false]]);
  assert.equal(byClass(row, "task-habit-badge").length, 1);
  assert.equal(byClass(row, "task-habit-skip").length, 1);
  assert.equal(byClass(row, "task-habit-icon").length, 1);
});
