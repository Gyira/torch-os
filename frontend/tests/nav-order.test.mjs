import test from "node:test";
import assert from "node:assert/strict";
import { visiblePaneIds } from "../nav-order.js";
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
  const moved = Array.from(moveItem(NAV_ORDER_DEFAULT, "knowledge", -1));
  assert.deepEqual(moved.slice(0, 5), ["workbench", "life", "self", "knowledge", "ops"]);
  assert.deepEqual(Array.from(moveItem(NAV_ORDER_DEFAULT, "workbench", -1)), Array.from(NAV_ORDER_DEFAULT));
  assert.deepEqual(Array.from(moveItem(NAV_ORDER_DEFAULT, "settings", 1)), Array.from(NAV_ORDER_DEFAULT));
  assert.deepEqual(Array.from(moveItem(NAV_ORDER_DEFAULT, "missing", 1)), Array.from(NAV_ORDER_DEFAULT));
});

test("storage round-trips the order and reset restores the default", async () => {
  const { NAV_ORDER_DEFAULT, clearOrder, currentNavOrder, readStoredOrder, storeOrder } = await loadModule();
  const custom = ["settings", "knowledge", "workbench", "life", "self", "ops"];
  assert.equal(readStoredOrder(), null);
  storeOrder(custom);
  assert.deepEqual(Array.from(readStoredOrder()), custom);
  assert.deepEqual(Array.from(currentNavOrder()), custom);
  clearOrder();
  assert.equal(readStoredOrder(), null);
  assert.deepEqual(Array.from(currentNavOrder()), Array.from(NAV_ORDER_DEFAULT));
});

test("applyNavOrder anchors on the default so reset still restores it after a move", async () => {
  const dom = fakeSidebar(["workbench", "life", "self", "ops", "knowledge", "settings"]);
  const { NAV_ORDER_DEFAULT, applyNavOrder, clearOrder, moveItem, storeOrder } = await loadModule({
    document: { querySelectorAll: (selector) => (selector === ".nav-item" ? dom.buttons.slice() : []) },
  });
  storeOrder(Array.from(moveItem(NAV_ORDER_DEFAULT, "workbench", 1)));
  const moved = Array.from(applyNavOrder());
  assert.deepEqual(moved.slice(0, 2), ["life", "workbench"]);
  assert.deepEqual(dom.order().slice(0, 2), ["life", "workbench"]);
  clearOrder();
  const restored = Array.from(applyNavOrder());
  assert.deepEqual(restored.slice(0, 2), ["workbench", "life"]);
  assert.deepEqual(dom.order().slice(0, 2), ["workbench", "life"]);
});

test("applyNavOrder keeps a sidebar entry that is not in the default list", async () => {
  const dom = fakeSidebar(["workbench", "settings", "experimental"]);
  const { applyNavOrder } = await loadModule({
    document: { querySelectorAll: (selector) => (selector === ".nav-item" ? dom.buttons.slice() : []) },
  });
  const order = Array.from(applyNavOrder());
  assert.equal(order[order.length - 1], "experimental");
  // 假 DOM 里没有四库按钮，applyNavOrder 只重排实际存在的节点；未知入口仍排最后。
  assert.deepEqual(dom.order(), ["workbench", "settings", "experimental"]);
});

test("visiblePaneIds keeps the workbench stacked except for the standalone recycle page", () => {
  const workbench = {
    stacked: true,
    pagePanes: ["recycle-view"],
    panes: [
      ["workbench-view", "待办事项"],
      ["agent-view", "与神对话"],
      ["recycle-view", "回收中心"],
    ],
  };
  assert.deepEqual(visiblePaneIds(workbench, "workbench-view"), ["workbench-view", "agent-view"]);
  assert.deepEqual(visiblePaneIds(workbench, "agent-view"), ["workbench-view", "agent-view"]);
  assert.deepEqual(visiblePaneIds(workbench, "recycle-view"), ["recycle-view"]);

  const ops = { stacked: false, panes: [["ops-view", "生活运营"], ["love-view", "恋爱系统"]] };
  assert.deepEqual(visiblePaneIds(ops, "love-view"), ["love-view"]);
  assert.deepEqual(visiblePaneIds(ops, ""), []);
  assert.deepEqual(visiblePaneIds(null, "anything"), []);
});
