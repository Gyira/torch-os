import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function loadModule() {
  const store = new Map();
  const context = vm.createContext({
    window: { localStorage: {
      getItem: (key) => store.has(key) ? store.get(key) : null,
      setItem: (key, value) => store.set(key, String(value)),
      removeItem: (key) => store.delete(key),
    } },
  });
  const module = new vm.SourceTextModule(await readFile(path.join(frontend, "widget-order.js"), "utf8"), {
    context, identifier: path.join(frontend, "widget-order.js"),
  });
  await module.link(() => { throw new Error("no imports expected"); });
  await module.evaluate();
  return module.namespace;
}

function fakeContainer(keys) {
  const container = {
    children: [],
    appends: 0,
    appendChild(node) {
      this.appends += 1;
      const index = this.children.indexOf(node);
      if (index >= 0) this.children.splice(index, 1);
      this.children.push(node);
    },
  };
  container.children = keys.map((key) => ({ dataset: { widget: key }, parentElement: container }));
  return container;
}

function transfer({ types = [], data = new Map(), failSet = false } = {}) {
  return {
    types,
    effectAllowed: "",
    setData(type, value) {
      if (failSet) throw new Error("denied");
      data.set(type, String(value));
    },
    getData(type) { return data.get(type) || ""; },
  };
}

test("resolveWidgetOrder keeps known keys, drops strangers and appends missing ones", async () => {
  const { WIDGET_ORDER_DEFAULT, resolveWidgetOrder } = await loadModule();
  assert.deepEqual(Array.from(resolveWidgetOrder(null)), Array.from(WIDGET_ORDER_DEFAULT));
  assert.deepEqual(
    Array.from(resolveWidgetOrder(["countdown", "gone", "weather", "countdown"])),
    ["countdown", "weather", "almanac"],
  );
  assert.deepEqual(
    Array.from(resolveWidgetOrder(["moon", "weather"], ["weather", "almanac"])),
    ["weather", "almanac"],
  );
  assert.deepEqual(
    Array.from(resolveWidgetOrder(["countdown"], ["weather", "almanac", "countdown"])),
    ["countdown", "weather", "almanac"],
  );
});

test("moveWidget moves by one and clamps at the edges", async () => {
  const { WIDGET_ORDER_DEFAULT, moveWidget } = await loadModule();
  assert.deepEqual(Array.from(moveWidget(WIDGET_ORDER_DEFAULT, "countdown", -1)),
    ["weather", "countdown", "almanac"]);
  assert.deepEqual(Array.from(moveWidget(WIDGET_ORDER_DEFAULT, "weather", -1)),
    Array.from(WIDGET_ORDER_DEFAULT));
  assert.deepEqual(Array.from(moveWidget(WIDGET_ORDER_DEFAULT, "countdown", 1)),
    Array.from(WIDGET_ORDER_DEFAULT));
  assert.deepEqual(Array.from(moveWidget(WIDGET_ORDER_DEFAULT, "missing", 1)),
    Array.from(WIDGET_ORDER_DEFAULT));
});

test("storage round-trips the order and clear restores the default on a fresh page", async () => {
  const {
    WIDGET_ORDER_DEFAULT, clearWidgetOrder, currentWidgetOrder,
    readStoredWidgetOrder, storeWidgetOrder,
  } = await loadModule();
  const custom = ["countdown", "weather", "almanac"];
  assert.equal(readStoredWidgetOrder(), null);
  storeWidgetOrder(custom);
  assert.deepEqual(Array.from(readStoredWidgetOrder()), custom);
  clearWidgetOrder();
  assert.equal(readStoredWidgetOrder(), null);
  assert.deepEqual(Array.from(WIDGET_ORDER_DEFAULT), ["weather", "almanac", "countdown"]);
  assert.deepEqual(Array.from(currentWidgetOrder(fakeContainer(WIDGET_ORDER_DEFAULT))),
    Array.from(WIDGET_ORDER_DEFAULT));
});

test("applyStoredWidgetOrder applies the saved order and appends new widgets", async () => {
  const { applyStoredWidgetOrder, storeWidgetOrder } = await loadModule();
  // 没保存过：保持 HTML 里的默认顺序（新组件也留在作者放的位置）。
  const untouched = fakeContainer(["weather", "moon", "almanac", "countdown"]);
  assert.deepEqual(Array.from(applyStoredWidgetOrder(untouched)),
    ["weather", "moon", "almanac", "countdown"]);
  // 保存过：先按保存顺序，再把后来新增的组件追加到末尾。
  storeWidgetOrder(["countdown", "weather", "almanac"]);
  const container = fakeContainer(["weather", "moon", "almanac", "countdown"]);
  const applied = Array.from(applyStoredWidgetOrder(container));
  assert.deepEqual(applied, ["countdown", "weather", "almanac", "moon"]);
  assert.deepEqual(container.children.map((node) => node.dataset.widget), applied);
});

test("applyWidgetOrder is idempotent and drops widgets that no longer exist", async () => {
  const { applyWidgetOrder } = await loadModule();
  const container = fakeContainer(["weather", "almanac", "countdown"]);
  assert.deepEqual(Array.from(applyWidgetOrder(container, ["countdown", "gone", "weather", "almanac"])),
    ["countdown", "weather", "almanac"]);
  assert.deepEqual(container.children.map((node) => node.dataset.widget),
    ["countdown", "weather", "almanac"]);
});

test("applyWidgetOrder does not touch the DOM when it is already in order", async () => {
  const { applyWidgetOrder } = await loadModule();
  const container = fakeContainer(["weather", "almanac", "countdown"]);
  const before = container.appends;
  applyWidgetOrder(container, ["weather", "almanac", "countdown"]);
  assert.equal(container.appends, before);
  applyWidgetOrder(container, ["countdown", "weather", "almanac"]);
  assert.ok(container.appends > before);
  assert.deepEqual(container.children.map((node) => node.dataset.widget),
    ["countdown", "weather", "almanac"]);
});

test("widget drag data only accepts our own MIME", async () => {
  const {
    WIDGET_DRAG_MIME, isWidgetDrag, readWidgetDragKey, setWidgetDragData,
  } = await loadModule();
  const data = new Map();
  const dt = transfer({ data });
  assert.equal(setWidgetDragData(dt, "weather"), true);
  assert.equal(dt.effectAllowed, "move");
  assert.equal(data.get(WIDGET_DRAG_MIME), "weather");
  assert.equal(data.get("text/plain"), "weather");
  assert.equal(setWidgetDragData(null, "weather"), false);
  assert.equal(setWidgetDragData(transfer({ failSet: true }), "weather"), false);

  assert.equal(isWidgetDrag({ dataTransfer: transfer({ types: [WIDGET_DRAG_MIME] }) }), true);
  assert.equal(isWidgetDrag({ dataTransfer: transfer({ types: ["Files"] }) }), false);
  assert.equal(isWidgetDrag({}), false);
  assert.equal(readWidgetDragKey({ dataTransfer: transfer({ data }) }), "weather");
  assert.equal(readWidgetDragKey({ dataTransfer: transfer() }), "");
});

test("dropAfterTarget uses the lower half of the card as insertion point", async () => {
  const { dropAfterTarget } = await loadModule();
  assert.equal(dropAfterTarget({ top: 100, height: 40 }, 119), false);
  assert.equal(dropAfterTarget({ top: 100, height: 40 }, 121), true);
  assert.equal(dropAfterTarget(null, 121), false);
});

test("the three right-side widgets opt in and workbench wires drag sorting", async () => {
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  const sideStart = html.indexOf('<aside class="workbench-side">');
  const agentStart = html.indexOf('<section id="agent-view"', sideStart);
  assert.ok(sideStart > 0 && agentStart > sideStart, "找不到工作台右侧栏");
  for (const [key, label] of [["weather", "天气"], ["almanac", "运势"], ["countdown", "倒计时"]]) {
    const at = html.indexOf(`data-widget="${key}"`);
    assert.ok(at > sideStart && at < agentStart, `${key} 小组件必须带 data-widget 且在右栏内`);
    assert.match(html.slice(at, at + 160), new RegExp(`data-widget-label="${label}"`));
  }

  const workbench = await readFile(path.join(frontend, "workbench.js"), "utf8");
  assert.match(workbench, /initWidgetDrag/);
  assert.match(workbench, /refreshWorkbenchWidgets/);

  const drag = await readFile(path.join(frontend, "widget-drag.js"), "utf8");
  for (const marker of ["enhanceWorkbenchWidgets", "ArrowUp", "ArrowDown", "is-reorder-before", "is-reorder-after"]) {
    assert.ok(drag.includes(marker), `widget-drag.js 缺少 ${marker}`);
  }
});
