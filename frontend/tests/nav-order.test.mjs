import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function loadModule({ document: doc } = {}) {
  const store = new Map();
  const context = vm.createContext({
    window: { localStorage: {
      getItem: (key) => store.has(key) ? store.get(key) : null,
      setItem: (key, value) => store.set(key, String(value)),
      removeItem: (key) => store.delete(key),
    } },
    ...(doc ? { document: doc } : {}),
  });
  const module = new vm.SourceTextModule(await readFile(path.join(frontend, "nav-order.js"), "utf8"), {
    context, identifier: path.join(frontend, "nav-order.js"),
  });
  await module.link(() => { throw new Error("no imports expected"); });
  await module.evaluate();
  return module.namespace;
}

function fakeSidebar(views) {
  const parent = {
    children: [],
    appendChild(node) {
      const index = this.children.indexOf(node);
      if (index >= 0) this.children.splice(index, 1);
      this.children.push(node);
    },
  };
  const buttons = views.map((view) => ({ dataset: { view }, parentElement: parent, focus() { this.focused = true; } }));
  parent.children.push(...buttons);
  return { parent, buttons, order: () => parent.children.map((button) => button.dataset.view) };
}

test("resolveOrder keeps known keys, drops strangers and appends missing ones", async () => {
  const { NAV_ORDER_DEFAULT, resolveOrder } = await loadModule();
  assert.deepEqual(Array.from(resolveOrder(null)), Array.from(NAV_ORDER_DEFAULT));
  assert.deepEqual(
    Array.from(resolveOrder(["settings", "today", "bogus", "today"], ["today", "profile", "settings"])),
    ["settings", "today", "profile"],
  );
  assert.deepEqual(Array.from(resolveOrder([], NAV_ORDER_DEFAULT)), Array.from(NAV_ORDER_DEFAULT));
});

test("moveItem moves by one and clamps at the edges", async () => {
  const { NAV_ORDER_DEFAULT, moveItem } = await loadModule();
  const moved = Array.from(moveItem(NAV_ORDER_DEFAULT, "brain", -1));
  assert.deepEqual(moved.slice(0, 3), ["workbench", "brain", "identity"]);
  assert.deepEqual(moved, ["workbench", "brain", "identity", "settings"]);
  assert.deepEqual(Array.from(moveItem(NAV_ORDER_DEFAULT, "workbench", -1)), Array.from(NAV_ORDER_DEFAULT));
  assert.deepEqual(Array.from(moveItem(NAV_ORDER_DEFAULT, "settings", 1)), Array.from(NAV_ORDER_DEFAULT));
  assert.deepEqual(Array.from(moveItem(NAV_ORDER_DEFAULT, "missing", 1)), Array.from(NAV_ORDER_DEFAULT));
});

test("storage round-trips the order and reset restores the default", async () => {
  const { NAV_ORDER_DEFAULT, clearOrder, currentNavOrder, readStoredOrder, storeOrder } = await loadModule();
  const custom = ["settings", "brain", "workbench", "identity"];
  assert.equal(readStoredOrder(), null);
  storeOrder(custom);
  assert.deepEqual(Array.from(readStoredOrder()), custom);
  assert.deepEqual(Array.from(currentNavOrder()), custom);
  clearOrder();
  assert.equal(readStoredOrder(), null);
  assert.deepEqual(Array.from(currentNavOrder()), Array.from(NAV_ORDER_DEFAULT));
});

test("applyNavOrder anchors on the default so reset still restores it after a move", async () => {
  const dom = fakeSidebar(["workbench", "identity", "brain", "settings"]);
  const { NAV_ORDER_DEFAULT, applyNavOrder, clearOrder, moveItem, storeOrder } = await loadModule({
    document: { querySelectorAll: (selector) => (selector === ".nav-item" ? dom.buttons.slice() : []) },
  });
  storeOrder(Array.from(moveItem(NAV_ORDER_DEFAULT, "workbench", 1)));
  const moved = Array.from(applyNavOrder());
  assert.deepEqual(moved.slice(0, 2), ["identity", "workbench"]);
  assert.deepEqual(dom.order().slice(0, 2), ["identity", "workbench"]);
  clearOrder();
  const restored = Array.from(applyNavOrder());
  assert.deepEqual(restored.slice(0, 2), ["workbench", "identity"]);
  assert.deepEqual(dom.order().slice(0, 2), ["workbench", "identity"]);
});

test("applyNavOrder keeps a sidebar entry that is not in the default list", async () => {
  const dom = fakeSidebar(["workbench", "settings", "experimental"]);
  const { applyNavOrder } = await loadModule({
    document: { querySelectorAll: (selector) => (selector === ".nav-item" ? dom.buttons.slice() : []) },
  });
  const order = Array.from(applyNavOrder());
  assert.equal(order[order.length - 1], "experimental");
  assert.deepEqual(dom.order(), ["workbench", "settings", "experimental"]);
});
