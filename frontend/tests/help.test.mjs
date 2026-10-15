import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the static in-app guide. No network, no personal data.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));

async function harness() {
  const nodes = new Map();
  const requests = [];
  const getNode = (selector) => {
    if (!nodes.has(selector)) {
      nodes.set(selector, {
        id: selector, value: "", textContent: "", innerHTML: "", listeners: new Map(), dataset: {},
        addEventListener(type, callback) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(callback); },
        async emit(type, values = {}) { const event = { target: this, currentTarget: this, preventDefault() {}, ...values }; for (const callback of this.listeners.get(type) || []) await callback(event); return event; },
        closest(selector) { const match = selector.match(/^\[data-([a-z-]+)\]$/); if (!match) return null; const key = match[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()); return this.dataset[key] !== undefined ? this : null; },
      });
    }
    return nodes.get(selector);
  };
  const document = { querySelector: getNode, querySelectorAll: () => [] };
  const context = vm.createContext({ document, Intl, Date });
  const core = new vm.SyntheticModule(["$", "escapeHtml", "setStatus"], function () {
    this.setExport("$", getNode);
    this.setExport("escapeHtml", escapeHtml);
    this.setExport("setStatus", (selector, message, tone = "") => { const node = typeof selector === "string" ? getNode(selector) : selector; node.textContent = message; node.tone = tone; });
  }, { context });
  const modules = new Map([[path.join(frontend, "core.js"), core]]);
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
  const help = (await load("help.js")).namespace;
  help.initHelp();
  return { help, getNode, requests };
}

test("the in-app guide renders every static section locally", async () => {
  const h = await harness();
  const html = h.getNode("#help-body").innerHTML;
  for (const title of ["快速上手", "记录与记忆", "记录、复盘与成长", "目标与任务", "数据、备份与恢复", "隐私与云端边界", "键盘与无障碍"]) {
    assert.match(html, new RegExp(title));
  }
  assert.match(h.getNode("#help-status").textContent, /共 \d+ 条帮助/);
  assert.equal(h.requests.length, 0);
});

test("guide search filters locally, reports counts and can be cleared", async () => {
  const h = await harness();
  const total = (h.getNode("#help-body").innerHTML.match(/class="help-item"/g) || []).length;
  const input = h.getNode("#help-search");
  input.value = "备份";
  await input.emit("input");
  const filtered = (h.getNode("#help-body").innerHTML.match(/class="help-item"/g) || []).length;
  assert.ok(filtered >= 1 && filtered < total);
  assert.match(h.getNode("#help-status").textContent, new RegExp(`${filtered} / ${total} 条帮助`));
  assert.match(h.getNode("#help-body").innerHTML, /备份与恢复/);

  input.value = "绝不可能出现的词xyz";
  await input.emit("input");
  assert.match(h.getNode("#help-body").innerHTML, /没有找到/);
  const clear = { dataset: { helpClear: "" } };
  clear.closest = (selector) => selector === "[data-help-clear]" ? clear : null;
  await h.getNode("#help-body").emit("click", { target: clear });
  assert.equal(h.getNode("#help-search").value, "");
  assert.match(h.getNode("#help-body").innerHTML, /快速上手/);
  assert.equal(h.requests.length, 0);
});
