import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the goals/tasks export buttons. No network, no model.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

class NodeDouble {
  constructor(id = "") {
    this.id = id;
    this.listeners = new Map();
    this.dataset = {};
    this.value = "";
    this.disabled = false;
    this.checked = false;
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
  closest() { return null; }
  matches() { return false; }
}

async function harness({ capability = true } = {}) {
  const nodes = new Map();
  const documentListeners = new Map();
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
  const location = { hash: "#goals", assign(url) { this.assigned = url; } };
  const context = vm.createContext({
    document, location, AbortController, TypeError, Intl, Date, console,
    CustomEvent: class { constructor(type) { this.type = type; } },
    window: { setTimeout: () => 1, clearTimeout() {}, addEventListener() {} },
    fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }),
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
  core.state.config = { capabilities: capability ? { planning_export: true } : {} };
  const goals = (await load("goals.js")).namespace;
  goals.initGoals();
  return { core, getNode, location, documentListeners };
}

test("planning export buttons follow the capability and navigate to the export URLs", async () => {
  const h = await harness();
  assert.equal(h.getNode("#export-planning-md").disabled, false);
  assert.equal(h.getNode("#export-planning-json").disabled, false);
  await h.getNode("#export-planning-md").emit("click");
  assert.equal(h.location.assigned, "/api/export/planning.md");
  await h.getNode("#export-planning-json").emit("click");
  assert.equal(h.location.assigned, "/api/export/planning.json");
});

test("an older service disables the planning export and never navigates", async () => {
  const h = await harness({ capability: false });
  assert.equal(h.getNode("#export-planning-md").disabled, true);
  assert.equal(h.getNode("#export-planning-json").disabled, true);
  await h.getNode("#export-planning-md").emit("click");
  assert.equal(h.location.assigned, undefined);
});

test("a late capability update enables the planning export", async () => {
  const h = await harness({ capability: false });
  assert.equal(h.getNode("#export-planning-md").disabled, true);
  h.core.state.config = { capabilities: { planning_export: true } };
  for (const callback of h.documentListeners.get("config-changed") || []) callback({ type: "config-changed" });
  assert.equal(h.getNode("#export-planning-md").disabled, false);
});
