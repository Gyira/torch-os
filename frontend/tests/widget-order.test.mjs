import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LAYOUT_KEY = "torch-os:workbench-widget-layout";
const LEGACY_KEY = "torch-os:workbench-widget-order";

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

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
  return { order: module.namespace, store };
}

function fakeContainer(keys = [], label = "") {
  const container = {
    children: [],
    appends: 0,
    dataset: { widgetColumnLabel: label },
    appendChild(node) {
      const previous = node.parentElement;
      if (previous && previous !== container) {
        const index = previous.children.indexOf(node);
        if (index >= 0) previous.children.splice(index, 1);
      }
      const index = this.children.indexOf(node);
      if (index >= 0) this.children.splice(index, 1);
      this.children.push(node);
      node.parentElement = this;
      this.appends += 1;
      return node;
    },
  };
  for (const key of keys) container.appendChild({ dataset: { widget: key }, parentElement: null });
  container.appends = 0;
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

test("the default layout is balanced and legacy order helpers still work", async () => {
  const { order: mod } = await loadModule();
  assert.deepEqual(plain(mod.WIDGET_LAYOUT_DEFAULT), {
    left: ["weather", "countdown", "anniversary", "duty"], right: ["almanac", "train-tracker", "deepseek"],
  });
  assert.deepEqual(plain(mod.WIDGET_COLUMN_DEFAULT), {
    weather: "left", countdown: "left", anniversary: "left", duty: "left",
    almanac: "right", "train-tracker": "right", deepseek: "right",
  });

  assert.deepEqual(plain(mod.resolveWidgetOrder(null, ["weather", "countdown", "almanac", "deepseek"])),
    ["weather", "countdown", "almanac", "deepseek"]);
  assert.deepEqual(plain(mod.resolveWidgetOrder(["countdown", "gone", "weather", "countdown"],
    ["weather", "countdown", "almanac", "deepseek"])),
  ["countdown", "weather", "almanac", "deepseek"]);
  assert.deepEqual(plain(mod.moveWidget(["weather", "countdown", "almanac"], "almanac", -1)),
    ["weather", "almanac", "countdown"]);
  assert.deepEqual(plain(mod.moveWidget(["weather", "countdown", "almanac"], "weather", -1)),
    ["weather", "countdown", "almanac"]);
});

test("resolveWidgetLayout splits a legacy flat order by its default columns", async () => {
  const { order: mod } = await loadModule();
  const known = ["weather", "countdown", "almanac", "deepseek"];
  assert.deepEqual(plain(mod.resolveWidgetLayout(null, known)), {
    left: ["weather", "countdown"], right: ["almanac", "deepseek"],
  });
  assert.deepEqual(plain(mod.resolveWidgetLayout(
    ["almanac", "weather", "deepseek", "countdown"], known)), {
    left: ["weather", "countdown"], right: ["almanac", "deepseek"],
  });
  assert.deepEqual(plain(mod.resolveWidgetLayout(
    { left: ["countdown", "gone", "weather"], right: ["almanac", "countdown"] }, known)), {
    left: ["countdown", "weather"], right: ["almanac", "deepseek"],
  });
  // 旧版扁平顺序没有栏位信息时，用调用方给的落位（defaults）分栏。
  assert.deepEqual(plain(mod.resolveWidgetLayout(
    ["weather", "moon", "almanac"], ["weather", "moon", "almanac"],
    { weather: "right", moon: "left", almanac: "left" })), {
    left: ["moon", "almanac"], right: ["weather"],
  });
});

test("storage round-trips the two-column layout and migrates the old flat key", async () => {
  const { order: mod, store } = await loadModule();
  assert.equal(mod.readStoredWidgetLayout(), null);
  mod.storeWidgetLayout({ left: ["countdown"], right: ["weather", "almanac"] });
  assert.deepEqual(plain(mod.readStoredWidgetLayout()), {
    layout: { left: ["countdown"], right: ["weather", "almanac"] }, legacy: false,
  });

  store.delete(LAYOUT_KEY);
  store.set(LEGACY_KEY, JSON.stringify(["countdown", "weather", "almanac"]));
  assert.deepEqual(plain(mod.readStoredWidgetLayout()), {
    layout: ["countdown", "weather", "almanac"], legacy: true,
  });

  mod.clearWidgetOrder();
  assert.equal(store.has(LAYOUT_KEY), false);
  assert.equal(store.has(LEGACY_KEY), false);
  assert.equal(mod.readStoredWidgetLayout(), null);
});

test("applyStoredWidgetLayout applies the saved columns and appends new widgets", async () => {
  const { order: mod, store } = await loadModule();
  // 没保存过：保持 HTML 里的默认分栏。
  const left = fakeContainer(["weather", "countdown"], "左侧");
  const right = fakeContainer(["almanac", "deepseek"], "右侧");
  assert.deepEqual(plain(mod.applyStoredWidgetLayout({ left, right })), {
    left: ["weather", "countdown"], right: ["almanac", "deepseek"],
  });

  store.set(LAYOUT_KEY, JSON.stringify({
    left: ["countdown"], right: ["weather", "almanac"],
  }));
  const movedLeft = fakeContainer(["weather", "countdown"], "左侧");
  const movedRight = fakeContainer(["almanac", "deepseek", "moon"], "右侧");
  const applied = plain(mod.applyStoredWidgetLayout({ left: movedLeft, right: movedRight }));
  assert.deepEqual(applied, {
    left: ["countdown"], right: ["weather", "almanac", "deepseek", "moon"],
  });
  assert.deepEqual(movedLeft.children.map((node) => node.dataset.widget), ["countdown"]);
  assert.deepEqual(movedRight.children.map((node) => node.dataset.widget),
    ["weather", "almanac", "deepseek", "moon"]);
});

test("a legacy flat order migrates by the current columns and is written back", async () => {
  const { order: mod, store } = await loadModule();
  store.set(LEGACY_KEY, JSON.stringify(["countdown", "weather", "almanac"]));
  const left = fakeContainer(["weather", "countdown"], "左侧");
  const right = fakeContainer(["almanac", "deepseek"], "右侧");
  const applied = plain(mod.applyStoredWidgetLayout({ left, right }));
  assert.deepEqual(applied, {
    left: ["countdown", "weather"], right: ["almanac", "deepseek"],
  });
  assert.equal(store.has(LEGACY_KEY), false);
  assert.deepEqual(JSON.parse(store.get(LAYOUT_KEY)), {
    left: ["countdown", "weather"], right: ["almanac", "deepseek"],
  });
});

test("applyWidgetOrder is idempotent within one column", async () => {
  const { order: mod } = await loadModule();
  const container = fakeContainer(["weather", "almanac", "countdown"]);
  assert.deepEqual(plain(mod.applyWidgetOrder(container, ["countdown", "gone", "weather", "almanac"])),
    ["countdown", "weather", "almanac"]);
  assert.deepEqual(container.children.map((node) => node.dataset.widget),
    ["countdown", "weather", "almanac"]);
  const before = container.appends;
  mod.applyWidgetOrder(container, ["countdown", "weather", "almanac"]);
  assert.equal(container.appends, before);
});

test("applyWidgetLayout does not touch the DOM when it is already in place", async () => {
  const { order: mod } = await loadModule();
  const left = fakeContainer(["weather", "countdown"], "左侧");
  const right = fakeContainer(["almanac", "deepseek"], "右侧");
  const before = left.appends + right.appends;
  mod.applyWidgetLayout({ left, right }, {
    left: ["weather", "countdown"], right: ["almanac", "deepseek"],
  });
  assert.equal(left.appends + right.appends, before);
});

test("widget drag data only accepts our own MIME", async () => {
  const { order: mod } = await loadModule();
  const data = new Map();
  const dt = transfer({ data });
  assert.equal(mod.setWidgetDragData(dt, "weather"), true);
  assert.equal(dt.effectAllowed, "move");
  assert.equal(data.get(mod.WIDGET_DRAG_MIME), "weather");
  assert.equal(data.get("text/plain"), "weather");
  assert.equal(mod.setWidgetDragData(null, "weather"), false);
  assert.equal(mod.setWidgetDragData(transfer({ failSet: true }), "weather"), false);

  assert.equal(mod.isWidgetDrag({ dataTransfer: transfer({ types: [mod.WIDGET_DRAG_MIME] }) }), true);
  assert.equal(mod.isWidgetDrag({ dataTransfer: transfer({ types: ["Files"] }) }), false);
  assert.equal(mod.isWidgetDrag({}), false);
  assert.equal(mod.readWidgetDragKey({ dataTransfer: transfer({ data }) }), "weather");
  assert.equal(mod.readWidgetDragKey({ dataTransfer: transfer() }), "");
});

test("dropAfterTarget uses the lower half of the card as insertion point", async () => {
  const { order: mod } = await loadModule();
  assert.equal(mod.dropAfterTarget({ top: 100, height: 40 }, 119), false);
  assert.equal(mod.dropAfterTarget({ top: 100, height: 40 }, 121), true);
  assert.equal(mod.dropAfterTarget(null, 121), false);
});

test("the workbench has two widget columns and wires drag sorting", async () => {
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  const headStart = html.indexOf('<div class="workbench-head-row">');
  const leftStart = html.indexOf('workbench-side-left', headStart);
  const mainStart = html.indexOf('<div class="workbench-head-main">', leftStart);
  const rightStart = html.indexOf('workbench-side-right', mainStart);
  const lifeStart = html.indexOf('<section id="life-view"', rightStart);
  assert.ok(headStart > 0 && leftStart > headStart && mainStart > leftStart
    && rightStart > mainStart && lifeStart > rightStart, "找不到左右两栏工作台结构");

  for (const [key, label] of [["weather", "天气"], ["countdown", "倒计时"], ["anniversary", "纪念日"], ["duty", "值班"]]) {
    const at = html.indexOf(`data-widget="${key}"`, leftStart);
    assert.ok(at > leftStart && at < mainStart, `${key} 小组件必须在左栏`);
    assert.match(html.slice(at, at + 160), new RegExp(`data-widget-label="${label}"`));
  }
  for (const [key, label] of [["almanac", "运势"], ["train-tracker", "列车追踪"], ["deepseek", "DeepSeek 余额"]]) {
    const at = html.indexOf(`data-widget="${key}"`, rightStart);
    assert.ok(at > rightStart && at < lifeStart, `${key} 小组件必须在右栏`);
    assert.match(html.slice(at, at + 160), new RegExp(`data-widget-label="${label}"`));
  }

  const workbench = await readFile(path.join(frontend, "workbench.js"), "utf8");
  assert.match(workbench, /initWidgetDrag/);
  assert.match(workbench, /refreshWorkbenchWidgets/);
  assert.match(workbench, /initAnniversaryWidget/);
  assert.match(workbench, /initTrainTrackerWidget/);
  assert.match(workbench, /loadTrainTrackerWidget/);

  const drag = await readFile(path.join(frontend, "widget-drag.js"), "utf8");
  for (const marker of [
    "enhanceWorkbenchWidgets", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
    "is-reorder-before", "is-reorder-after", "is-drop-target", "workbench-side-empty",
  ]) {
    assert.ok(drag.includes(marker), `widget-drag.js 缺少 ${marker}`);
  }
});
