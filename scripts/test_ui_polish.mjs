// 零依赖交互回归：运行真实前端函数，使用可控制的网络响应和最小 DOM 替身。
// 不启动真实库，不调用模型。node --test scripts/test_ui_polish.mjs
import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFile } from "node:fs/promises";
import * as editorModel from "../frontend/editor-model.js";
import * as taskModel from "../frontend/tasks-model.js";
import * as roleModel from "../frontend/roles-model.js";

class Element {
  constructor(tag, doc) {
    this.tagName = tag.toUpperCase(); this.doc = doc; this.children = []; this.attrs = {};
    this.events = {}; this.dataset = {}; this.value = ""; this.style = { setProperty() {} };
    this.hidden = false; this.disabled = false; this.inert = false; this._text = "";
    this.classList = {
      contains: (name) => this.className.split(/\s+/).includes(name),
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/), ...names])].filter(Boolean).join(" "); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter(x => !names.includes(x)).join(" "); },
      toggle: (name, on) => { (on ?? !this.classList.contains(name)) ? this.classList.add(name) : this.classList.remove(name); },
    };
  }
  get className() { return this.attrs.class || ""; }
  set className(value) { this.attrs.class = value; }
  get id() { return this.attrs.id || ""; }
  set id(value) { this.attrs.id = value; }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(""); }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  set innerHTML(html) {
    this.replaceChildren();
    const stack = [this];
    for (const token of html.match(/<[^>]+>|[^<]+/g) || []) {
      if (token.startsWith("</")) { stack.pop(); continue; }
      if (!token.startsWith("<")) { stack.at(-1)._text += token; continue; }
      const tag = token.match(/^<([\w-]+)/)?.[1];
      if (!tag) continue;
      const child = new Element(tag, this.doc);
      const attributes = token.slice(tag.length + 1, -1);
      for (const match of attributes.matchAll(/([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'))?/g)) {
        child.setAttribute(match[1], match[2] ?? match[3] ?? "");
      }
      stack.at(-1).append(child);
      if (!["input", "br", "hr"].includes(tag)) stack.push(child);
    }
  }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name === "hidden") this.hidden = true;
    if (name === "disabled") this.disabled = true;
    if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, x) => x.toUpperCase())] = String(value);
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  hasAttribute(name) { return Object.hasOwn(this.attrs, name); }
  removeAttribute(name) { delete this.attrs[name]; }
  append(...nodes) {
    for (const item of nodes) {
      const node = typeof item === "string" ? Object.assign(new Element("span", this.doc), { textContent: item }) : item;
      node.parentElement?.children.splice(node.parentElement.children.indexOf(node), 1);
      node.parentElement = this; this.children.push(node);
    }
  }
  replaceChildren(...nodes) { for (const node of [...this.children]) node.remove(); this._text = ""; this.append(...nodes); }
  remove() {
    if (this.contains(this.doc.activeElement)) this.doc.activeElement = this.doc.body;
    if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
    this.parentElement = null;
  }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  get isConnected() { return this.doc.body.contains(this); }
  matches(selector) {
    return selector.split(",").some(part => {
      let value = part.trim();
      if (value === ":hover") return Boolean(this.hovered);
      for (const match of value.matchAll(/:not\(([^)]+)\)/g)) if (this.matches(match[1])) return false;
      value = value.replace(/:not\([^)]+\)/g, "");
      const tag = value.match(/^[a-z]+/i)?.[0];
      if (tag && this.tagName !== tag.toUpperCase()) return false;
      const id = value.match(/#([\w-]+)/)?.[1];
      if (id && this.id !== id) return false;
      for (const match of value.matchAll(/\.([\w-]+)/g)) if (!this.classList.contains(match[1])) return false;
      for (const match of value.matchAll(/\[([\w-]+)(?:=['"]?([^'"\]]+)['"]?)?\]/g)) {
        if (match[1] === "open" ? !this.open : !this.hasAttribute(match[1])) return false;
        if (match[2] != null && this.getAttribute(match[1]) !== match[2]) return false;
      }
      return true;
    });
  }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
  querySelectorAll(selector) { return this.children.flatMap(node => [...(node.matches(selector) ? [node] : []), ...node.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(name, fn) { (this.events[name] ||= []).push(fn); }
  removeEventListener(name, fn) { this.events[name] = (this.events[name] || []).filter(x => x !== fn); }
  async fire(name, props = {}) {
    const event = { target: this, currentTarget: this, preventDefault() {}, stopPropagation() {}, ...props };
    for (const fn of [...(this.events[name] || [])]) await fn(event);
  }
  focus() { this.doc.activeElement = this; }
  scrollIntoView() {}
  getClientRects() { return this.hidden ? [] : [{}]; }
  getBoundingClientRect() { return { left: 20, top: 40, bottom: 70, width: 220, height: 220 }; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}

function environment() {
  const document = { activeElement: null, events: {}, dispatchEvent() {} };
  document.body = new Element("body", document); document.activeElement = document.body;
  document.createElement = (tag) => new Element(tag, document);
  document.createTextNode = (text) => Object.assign(new Element("span", document), { textContent: text });
  document.querySelector = (selector) => document.body.querySelector(selector);
  document.querySelectorAll = (selector) => document.body.querySelectorAll(selector);
  document.getElementById = (id) => document.querySelector(`#${id}`);
  for (const method of ["addEventListener", "removeEventListener", "fire"]) document[method] = Element.prototype[method];
  const timers = new Map(); let timerId = 0;
  const window = { events: {}, innerWidth: 1000, innerHeight: 800,
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; }, clearTimeout(id) { timers.delete(id); } };
  for (const method of ["addEventListener", "removeEventListener"]) window[method] = Element.prototype[method];
  const add = (id, tag = "input", value = "") => {
    const el = document.createElement(tag); el.id = id; el.value = value; document.body.append(el); return el;
  };
  return { document, window, timers, add, queueMicrotask, console, Date, setTimeout, clearTimeout,
    MutationObserver: class { observe() {} }, CustomEvent: class {},
    escapeHtml: value => String(value ?? ""), formatRelativeDate: value => value || "",
  };
}

async function load(name, env, setup = "") {
  const source = (await readFile(new URL(`../frontend/${name}`, import.meta.url), "utf8"))
    .replace(/^import\s+[\s\S]*?\sfrom\s+["'][^"']+["'];\s*/gm, "")
    .replace(/\bexport (?=(?:async )?function|const|let|class)/g, "");
  const context = vm.createContext(env);
  vm.runInContext(source + "\n" + setup, context, { filename: name });
  return context;
}
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const tick = async () => { await Promise.resolve(); await Promise.resolve(); };

test("导师回复成功但记忆写入失败时保留回复并明确提示失败", async () => {
  const env = environment();
  const messages = env.add("mentor-test-messages", "div"); messages.setAttribute("data-mentor-messages", "");
  const status = env.add("mentor-test-status", "p"); status.setAttribute("data-mentor-status", "");
  const input = env.add("mentor-test-input"); input.setAttribute("data-mentor-input", "");
  const notices = [];
  env.toast = (text, tone) => notices.push({text, tone});
  env.messageMarkup = item => `<p>${item.content}</p>`;
  env.renderMarkdownDocument = text => ({html: text});
  env.api = async path => path === "/api/ai/chat"
    ? {thread_id:"test", reply:"正常回复", memory_errors:["历史备份失败"], memory_notes:0}
    : {};
  const ctx = await load("mentor.js", env, "state.host = document.body;");
  await ctx.sendMessage("请记住这件事");
  assert.match(messages.textContent, /正常回复/);
  assert.match(status.textContent, /长期记忆保存失败/);
  assert.equal(notices.length, 1);
  assert.equal(notices[0].tone, "error");
  assert.match(notices[0].text, /历史备份失败/);
});

async function roleHarness() {
  const env = environment();
  Object.assign(env, roleModel, { initProposals() {}, toast() {}, proposalsMarkup: () => "", renderMarkdownDocument: text => ({html: text}) });
  for (const id of ["modal", "input", "send", "title", "subtitle", "eyebrow", "avatar", "hint", "status", "preview", "messages", "chips", "context", "dock", "close", "form"]) env.add(`role-drawer-${id}`, id === "modal" ? "dialog" : "div");
  env.add("role-drawer", "aside").hidden = true;
  const requests = [];
  env.api = async path => {
    if (path.endsWith("status")) return {available: true};
    if (path.endsWith("roles")) return {items: [{id:"a", name:"角色 A"}, {id:"b", name:"角色 B"}]};
    const request = deferred(); requests.push(request); return request.promise;
  };
  const ctx = await load("roles.js", env, "globalThis.snapshot = () => ({role: state.role?.id, busy: state.busy, thread: state.threadId, messages: [...state.messages]});");
  return { env, ctx, requests, input: env.document.getElementById("role-drawer-input") };
}

test("角色权限切换提示保留历史，并在后续轮次恢复正常状态", async () => {
  const {ctx, requests, input, env} = await roleHarness();
  await ctx.openRoleDrawer({roleId:"a"});
  input.value = "继续";
  const first = ctx.sendRoleMessage(input.value);
  requests[0].resolve({thread_id:"same", reply:"新范围回复", context_reset:true});
  await first;
  assert.match(env.document.getElementById("role-drawer-status").textContent, /历史对话保留/);
  input.value = "下一问";
  const next = ctx.sendRoleMessage(input.value);
  requests[1].resolve({thread_id:"same", reply:"后续回复", context_reset:false});
  await next;
  assert.doesNotMatch(env.document.getElementById("role-drawer-status").textContent, /历史对话保留/);
  assert.equal(ctx.snapshot().messages.length, 4);
});

test("越域建议被拒绝时保留正常回复并显示原因", async () => {
  const {ctx, requests, input, env} = await roleHarness();
  await ctx.openRoleDrawer({roleId:"a"});
  input.value = "讨论笔记";
  const sending = ctx.sendRoleMessage(input.value);
  requests[0].resolve({thread_id:"same", reply:"正常分析", proposals:[], action_errors:["建议未生成：写入目标不属于当前角色"]});
  await sending;
  assert.match(env.document.getElementById("role-drawer-status").textContent, /写入目标不属于当前角色/);
  assert.equal(ctx.snapshot().messages.at(-1).content, "正常分析");
  assert.equal(ctx.snapshot().busy, false);
});

test("角色旧回复不能污染重开的角色或解除新请求忙碌状态", async () => {
  const {ctx, requests, input} = await roleHarness();
  await ctx.openRoleDrawer({roleId:"a"});
  input.value = "旧问题";
  const oldSend = ctx.sendRoleMessage(input.value);
  ctx.closeRoleDrawer();
  await ctx.openRoleDrawer({roleId:"b"});
  input.value = "新问题";
  const newSend = ctx.sendRoleMessage(input.value);
  requests[0].resolve({thread_id:"old", reply:"旧回复"}); await oldSend;
  assert.equal(ctx.snapshot().role, "b");
  assert.equal(ctx.snapshot().busy, true);
  assert.equal(ctx.snapshot().messages.length, 1);
  requests[1].resolve({thread_id:"new", reply:"新回复"}); await newSend;
  assert.equal(ctx.snapshot().thread, "new");
  assert.equal(ctx.snapshot().busy, false);
});

test("角色失败恢复原文但保留后续输入；快捷提问不清草稿；关闭重开恢复草稿", async () => {
  const {ctx, requests, input, env} = await roleHarness();
  await ctx.openRoleDrawer({roleId:"a"});
  input.value = "  原文  ";
  const first = ctx.sendRoleMessage(input.value);
  requests[0].reject(new Error("超时")); await first;
  assert.equal(input.value, "  原文  ");
  const second = ctx.sendRoleMessage(input.value);
  input.value = "新草稿";
  requests[1].reject(new Error("失败")); await second;
  assert.equal(input.value, "新草稿");
  const quick = ctx.sendRoleMessage("快捷问题");
  assert.equal(input.value, "新草稿");
  env.document.body.focus();
  requests[2].resolve({reply:"完成"}); await quick;
  assert.equal(env.document.activeElement, env.document.body, "完成不抢焦点");
  ctx.closeRoleDrawer();
  await ctx.openRoleDrawer({roleId:"b"}); assert.equal(input.value, "");
  await ctx.openRoleDrawer({roleId:"a"}); assert.equal(input.value, "新草稿");
});

test("关闭令待打开角色请求失效，迟到响应不会重开抽屉", async () => {
  const {ctx, env} = await roleHarness();
  const request = deferred(); env.api = () => request.promise;
  const opening = ctx.openRoleDrawer({roleId:"a"});
  ctx.closeRoleDrawer(); request.resolve({available:true});
  assert.equal(await opening, false);
  assert.equal(env.document.getElementById("role-drawer").hidden, true);
});

test("编辑器角色草稿按文档隔离，正文脏状态改变不会丢失同文档草稿", async () => {
  const {ctx, env, input} = await roleHarness();
  env.add("note-editor-dialog", "dialog").showModal();
  env.add("note-editor-companion-slot", "div");
  await ctx.openRoleDrawer({roleId:"a", host:"editor", draftContext:"a.md", selectors:[{path:"a.md"}]});
  input.value = "A 的草稿";
  await ctx.openRoleDrawer({roleId:"a", host:"editor", draftContext:"b.md", selectors:[]});
  assert.equal(input.value, "");
  input.value = "B 的草稿";
  await ctx.openRoleDrawer({roleId:"a", host:"editor", draftContext:"a.md", selectors:[]});
  assert.equal(input.value, "A 的草稿");
  assert.equal(env.document.getElementById("role-drawer-modal").open, false);
});

test("设置抽屉关闭后丢弃旧响应；重开时旧请求不能覆盖当前加载状态", async () => {
  const env = environment();
  const dialog = env.add("love-settings-drawer", "dialog");
  const body = env.add("love-settings-body", "div");
  env.add("love-settings-close", "button");
  const pending = [];
  env.api = () => { const request = deferred(); pending.push(request); return request.promise; };
  const ctx = await load("love.js", env);
  const first = ctx.openLoveSettings();
  assert.equal(dialog.open, true);
  assert.equal(body.getAttribute("aria-busy"), "true");
  ctx.closeLoveSettings();
  assert.equal(dialog.open, false);
  const second = ctx.openLoveSettings();
  pending[0].resolve({ paths: { root: "旧库" } });
  await first;
  assert.equal(pending.length, 2, "旧请求不应继续请求聊天状态");
  assert.equal(body.getAttribute("aria-busy"), "true");
  assert.equal(body.textContent.includes("旧库"), false);
  ctx.closeLoveSettings();
  pending[1].resolve({ paths: { root: "新库" } });
  await second;
  assert.equal(body.getAttribute("aria-busy"), "false");
  assert.equal(body.textContent.includes("新库"), false);
});

test("任务慢提交保留新草稿、阻止重复请求、不抢走焦点", async () => {
  const env = environment(); const request = deferred(); let calls = 0;
  const input = env.add("time-add-input", "input", "第一条");
  const button = env.add("time-add-submit", "button"); const other = env.add("elsewhere", "button");
  const context = await load("time-module.js", { ...env, $: s => env.document.querySelector(s), INBOX: "收集箱",
    api: () => { calls++; return request.promise; }, jsonBody: (method, data) => ({ method, body: JSON.stringify(data) }),
    setStatus() {}, ...taskModel }, 'board = {revision:"r1"}; loadTimeTasks = async () => {};');
  const saving = context.createTask();
  await context.createTask(); assert.equal(calls, 1);
  assert.equal(button.getAttribute("aria-busy"), "true");
  input.value = "等待时写的第二条"; other.focus(); request.resolve({}); await saving;
  assert.equal(input.value, "等待时写的第二条"); assert.equal(env.document.activeElement, other);
  assert.equal(button.getAttribute("aria-busy"), null);
});

test("任务失败保留草稿并允许用户重试；成功清除原草稿", async () => {
  const env = environment(); const input = env.add("time-add-input", "input", "草稿"); env.add("time-add-submit", "button");
  let fail = true;
  const context = await load("time-module.js", { ...env, ...taskModel, $: s => env.document.querySelector(s),
    api: async () => { if (fail) throw Error("offline"); return {}; }, jsonBody() {}, setStatus() {} },
  'loadTimeTasks = async () => {};');
  await context.createTask(); assert.equal(input.value, "草稿");
  fail = false; await context.createTask(); assert.equal(input.value, "");
});

async function editorEnvironment(api) {
  const env = environment();
  for (const [id, value] of [["title", "提交的标题"], ["tags", "旧标签"], ["chapter", "life_notes"], ["text", "提交的正文"]]) env.add(`note-editor-${id}`, "input", value);
  env.add("note-editor-save", "button"); env.add("note-editor-status", "p");
  const context = await load("note-editor.js", { ...env, ...editorModel, api, toast() {} }, `
    Object.assign(state, {open:true, mode:"edit", title:"旧标题", tags:[], chapter:"life_notes", path:"note.md", revision:"r1"});
    dispatchNotesChanged = () => {}; updatePreview = () => {}; refreshTemplateStrip = () => {}; hideTemplateUndo = () => {};
    globalThis.editorState = state;
  `);
  return { ...env, context };
}

test("编辑器回包保留保存期间修改的标题、标签、本子、正文及脏状态", async () => {
  const request = deferred(); const { document, context } = await editorEnvironment(() => request.promise);
  const saving = context.saveNote();
  document.getElementById("note-editor-title").value = "新的标题";
  document.getElementById("note-editor-tags").value = "新标签";
  document.getElementById("note-editor-chapter").value = "other_book";
  document.getElementById("note-editor-text").value = "继续写的正文";
  request.resolve({ title: "提交的标题", tags: ["旧标签"], chapter: "life_notes", body: "提交的正文", path: "note.md", revision: "r2" });
  await saving;
  for (const [id, expected] of [["title", "新的标题"], ["tags", "新标签"], ["chapter", "other_book"], ["text", "继续写的正文"]]) assert.equal(document.getElementById(`note-editor-${id}`).value, expected);
  assert.equal(context.editorState.revision, "r2"); assert.equal(context.dirty(), true);
  assert.match(document.getElementById("note-editor-status").textContent, /仍有未保存/);
});

test("编辑器冲突不改草稿；保存中不能关闭或重载", async () => {
  const request = deferred(); const { context, document } = await editorEnvironment(() => request.promise);
  const saving = context.saveNote(); await context.closeEditor(); await context.reloadNote();
  assert.equal(context.editorState.open, true);
  request.reject(Object.assign(Error("conflict"), { status: 409 })); await saving;
  assert.equal(document.getElementById("note-editor-title").value, "提交的标题");
  assert.equal(context.editorState.revision, "r1"); assert.equal(context.editorState.busy, false);
});

test("移动失败保留目标本子以便重试，并保持未保存状态", async () => {
  let calls = 0;
  const { context, document } = await editorEnvironment(async () => {
    if (++calls === 2) throw Error("move failed");
    return { title:"提交的标题", tags:["旧标签"], chapter:"life_notes", body:"提交的正文", path:"note.md", revision:"r2" };
  });
  document.getElementById("note-editor-chapter").value = "target_book";
  await context.saveNote();
  assert.equal(calls, 2); assert.equal(document.getElementById("note-editor-chapter").value, "target_book");
  assert.equal(context.editorState.chapter, "life_notes"); assert.equal(context.dirty(), true);
});

test("搜索清空/更改查询立即失效旧响应，关闭后不更新且无定时器残留", async () => {
  const env = environment(); const requests = []; const opened = [];
  const context = await load("global-search.js", { ...env, api: () => { const req = deferred(); requests.push(req); return req.promise; }, openNote: path => opened.push(path) });
  context.openGlobalSearch(); const input = env.document.querySelector("[data-global-query]");
  input.value = "旧查询"; const first = input.fire("keydown", { key:"Enter" }); await first;
  input.value = ""; await input.fire("input");
  requests[0].resolve({ items:[{ title:"旧结果", path:"old.md" }] }); await tick();
  assert.equal(env.document.querySelectorAll("[role='option']").length, 0);
  input.value = "新查询"; await input.fire("keydown", { key:"Enter" });
  await env.document.querySelector("[data-search-close]").fire("click");
  requests[1].resolve({ items:[{ title:"迟来的结果", path:"late.md" }] }); await tick();
  assert.equal(env.document.querySelector("dialog"), null); assert.equal(env.timers.size, 0); assert.deepEqual(opened, []);
});

test("搜索结果有活动选项语义；输入法 Enter 不打开笔记", async () => {
  const env = environment(); const opened = [];
  const context = await load("global-search.js", { ...env, api: async () => ({ items:[{title:"测试",path:"test.md"}] }), openNote: path => opened.push(path) });
  context.openGlobalSearch(); const input = env.document.querySelector("[data-global-query]");
  input.value = "测试"; await input.fire("keydown", {key:"Enter"}); await tick();
  await input.fire("keydown", {key:"ArrowDown"});
  const option = env.document.querySelector("[role='option']");
  assert.equal(input.getAttribute("aria-activedescendant"), option.id);
  assert.equal(option.getAttribute("aria-selected"), "true");
  await input.fire("keydown", {key:"Enter",isComposing:true}); assert.deepEqual(opened, []);
  await input.fire("keydown", {key:"Enter"}); assert.deepEqual(opened, ["test.md"]);
});

test("优先级弹层进入当前项，方向键移动，Esc 关闭回焦并清理监听", async () => {
  const env = environment(); const anchor = env.add("priority", "button"); anchor.textContent = "优先级"; anchor.focus();
  const context = await load("tasks-picker.js", {...env, ...taskModel});
  context.openPriorityPicker(anchor, "none", () => {});
  assert.equal(env.document.activeElement.getAttribute("aria-pressed"), "true");
  await env.document.fire("keydown", {key:"ArrowDown", target:env.document.activeElement});
  assert.match(env.document.activeElement.textContent, /高/);
  await env.document.fire("keydown", {key:"Escape", target:env.document.activeElement});
  assert.equal(env.document.activeElement, anchor); assert.equal(env.document.querySelector(".time-popover"), null);
  assert.equal(env.document.events.focusin.length, 0); assert.equal(env.window.events.resize.length, 0);
});

test("Toast 不覆盖撤销；隐藏移除按钮并回焦；错误持续到手动关闭", async () => {
  const env = environment(); const root = env.add("toast", "div"); const trigger = env.add("trigger", "button"); trigger.focus();
  const context = await load("toast.js", env); let undo = 0;
  context.toast("可撤销操作", "success", {label:"撤销",onAction:async () => { undo++; }});
  context.toast("后来的成功", "success");
  assert.match(root.textContent, /可撤销操作/); assert.equal(env.timers.size, 0);
  const action = root.querySelector(".toast-action"); action.focus(); await action.fire("click");
  assert.equal(undo, 1); assert.match(root.textContent, /后来的成功/); assert.equal(env.document.activeElement, trigger);
  await root.querySelector(".toast-dismiss").fire("click");
  assert.equal(root.inert, true); assert.equal(root.children.length, 0);
  context.toast("错误需要处理", "error"); assert.equal(env.timers.size, 0);
  context.toast("普通成功", "success"); assert.match(root.textContent, /错误需要处理/);
});

test("Toast 暂停与恢复计时，模态框内提示属于同一 top layer", async () => {
  const env = environment(); const root = env.add("toast", "div"); const modal = env.add("modal", "dialog"); modal.open = true;
  const button = env.document.createElement("button"); modal.append(button); button.focus();
  const context = await load("toast.js", env); context.toast("保存完成");
  assert.equal(root.parentElement, modal); assert.equal(env.timers.size, 1);
  root.hovered = true; await root.fire("mouseenter"); assert.equal(env.timers.size, 0);
  root.hovered = false; await root.fire("mouseleave"); assert.equal(env.timers.size, 1);
  const expire = [...env.timers.values()][0]; expire(); assert.equal(root.children.length, 0); assert.equal(root.inert, true);
});
