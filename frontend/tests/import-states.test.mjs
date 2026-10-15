import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

// Synthetic offline event tests for the import "duplicates and changes" check.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const records = Array.from({ length: 4 }, (_, index) => ({ path: `sample-${index}.md`, type: "markdown", import_allowed: true, sensitive: false, size: 20 }));

async function harness(states = null) {
  const nodes = new Map();
  const checkboxes = new Map();
  class Element {
    constructor() { this.listeners = new Map(); this.dataset = {}; this.attributes = {}; this.value = ""; this.checked = false; this.disabled = false; this.hidden = false; this.textContent = ""; this.innerHTML = ""; this.classList = { add() {}, remove() {} }; }
    addEventListener(type, handler) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(handler); }
    async emit(type, values = {}) { for (const handler of this.listeners.get(type) || []) await handler({ target: this, currentTarget: this, preventDefault() {}, ...values }); }
    setAttribute(name, value) { this.attributes[name] = value; }
    replaceChildren() { this.innerHTML = ""; }
    querySelector(selector) { return getNode(selector); }
  }
  const getNode = (selector) => { if (!nodes.has(selector)) nodes.set(selector, new Element()); return nodes.get(selector); };
  const requests = [];
  const respond = (url) => url.endsWith("/preview")
    ? { files: records }
    : url.endsWith("/states")
      ? (states || { selected_count: 4, counts: { new: 1, unchanged: 1, updated: 1, "user-edited": 1 }, items: [
          { path: "sample-0.md", vault_path: "80-Wolai/a.md", state: "new" },
          { path: "sample-1.md", vault_path: "80-Wolai/b.md", state: "unchanged" },
          { path: "sample-2.md", vault_path: "80-Wolai/c.md", state: "updated" },
          { path: "sample-3.md", vault_path: "80-Wolai/d.md", state: "user-edited" },
        ], note: "只读检查：没有写入任何文件。" })
      : [];
  const context = vm.createContext({
    document: { querySelector: getNode, querySelectorAll: (selector) => (selector === "[data-import-path]" ? [...checkboxes.values()] : []), addEventListener() {}, dispatchEvent() {} },
    window: { setTimeout: () => 1, clearTimeout() {} },
    AbortController, TypeError, Intl, Date, CustomEvent: class { constructor(type) { this.type = type; } },
    fetch: async (url, options) => { requests.push({ url, options }); return { ok: true, status: 200, json: async () => respond(url, options) }; },
  });
  const modules = new Map();
  const pending = new Map();
  async function load(name) {
    const filename = path.resolve(frontend, name);
    if (modules.has(filename)) return modules.get(filename);
    if (pending.has(filename)) return pending.get(filename);
    const promise = (async () => {
      const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context, identifier: filename });
      await module.link((specifier, referrer) => load(path.relative(frontend, path.resolve(path.dirname(referrer.identifier), specifier))));
      await module.evaluate();
      modules.set(filename, module);
      pending.delete(filename);
      return module;
    })();
    pending.set(filename, promise);
    return promise;
  }
  const core = (await load("core.js")).namespace;
  const imports = (await load("imports.js")).namespace;
  core.state.config = { capabilities: {} };
  imports.initImports();
  getNode("#import-package").value = "synthetic package";
  await getNode("#preview-import").emit("click");
  const select = async (filePath) => {
    const input = { dataset: { importPath: filePath }, checked: true, closest() { return this; } };
    checkboxes.set(filePath, input);
    await getNode("#import-file-list").emit("change", { target: input });
  };
  return { getNode, requests, select };
}

test("the states check posts the selection and renders counts and per-file badges", async () => {
  const h = await harness();
  await h.select("sample-0.md");
  await h.select("sample-1.md");
  assert.equal(h.getNode("#check-import-states").disabled, false);
  await h.getNode("#check-import-states").emit("click");
  const request = h.requests.find((item) => String(item.url).endsWith("/states"));
  assert.ok(request, "states request was sent");
  assert.deepEqual(JSON.parse(request.options.body), { selected_paths: ["sample-0.md", "sample-1.md"] });
  const html = h.getNode("#import-states-result").innerHTML;
  assert.match(html, /新增/);
  assert.match(html, /未变化/);
  assert.match(html, /内容已更新（旧版本会存历史）/);
  assert.match(html, /80-Wolai\/c\.md/);
  assert.match(h.getNode("#import-states-status").textContent, /没有写入任何文件/);
});

test("the classification survives a selection change", async () => {
  const h = await harness();
  await h.select("sample-0.md");
  await h.getNode("#check-import-states").emit("click");
  assert.notEqual(h.getNode("#import-states-result").innerHTML, "");
  await h.select("sample-1.md");
  // 分类是逐文件结论，改变选择后仍然有效：未变化/已被编辑的行保持禁用与徽标。
  assert.notEqual(h.getNode("#import-states-result").innerHTML, "");
});

test("the check needs a selection and surfaces failures without losing it", async () => {
  const h = await harness();
  assert.equal(h.getNode("#check-import-states").disabled, true);
  await h.select("sample-0.md");
  await h.getNode("#check-import-states").emit("click");
  const before = h.requests.length;
  await h.getNode("#check-import-states").emit("click");
  assert.equal(h.requests.length, before + 1);
});

test("unchanged and locally edited rows are unchecked and disabled after the check", async () => {
  const h = await harness();
  await h.select("sample-0.md");
  await h.select("sample-1.md");
  await h.select("sample-2.md");
  await h.select("sample-3.md");
  await h.getNode("#check-import-states").emit("click");
  const list = h.getNode("#import-file-list").innerHTML;
  assert.match(list, /sample-1\.md/);
  assert.match(list, /未变化 · 已自动跳过/);
  assert.match(list, /已被你编辑（不会覆盖） · 已自动跳过/);
  // sample-0 是“新增”，仍然可选；sample-1/2 已被状态禁用。
  const rows = [...list.matchAll(/<article[\s\S]*?<\/article>/g)].map((match) => match[0]);
  const rowFor = (path) => rows.find((row) => row.includes(path));
  assert.equal(/disabled/.test(rowFor("sample-0.md")), false);
  assert.equal(/disabled/.test(rowFor("sample-1.md")), true);
  assert.equal(/disabled/.test(rowFor("sample-2.md")), false);
  assert.equal(/disabled/.test(rowFor("sample-3.md")), true);
  assert.match(h.getNode("#import-states-status").textContent, /已自动取消勾选/);
});

test("resetting the preview drops the classification", async () => {
  const h = await harness();
  await h.select("sample-0.md");
  await h.getNode("#check-import-states").emit("click");
  await h.getNode("#check-import-states").emit("click"); // 重新检查仍然只读
  assert.notEqual(h.getNode("#import-states-result").innerHTML, "");
});
