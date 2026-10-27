import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 双栏小组件拖拽的 DOM 层：用一个小假 DOM 验证包装、跨栏拖放、空栏落点和键盘换栏。
// 不替代真实浏览器的拖拽手势验证。
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LAYOUT_KEY = "torch-os:workbench-widget-layout";
const LABELS = { weather: "天气", almanac: "运势", countdown: "倒计时", deepseek: "DeepSeek 余额", moon: "月亮" };

function matches(node, selector) {
  return selector.split(",").some((part) => {
    const target = part.trim();
    if (!target) return false;
    if (target.startsWith(".")) return node.classList.contains(target.slice(1));
    if (target === "[contenteditable='true']") return node.getAttribute("contenteditable") === "true";
    return node.tagName === target.toUpperCase();
  });
}

class FakeElement {
  constructor(tag = "div") {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.parentElement = null;
    this.dataset = {};
    this.attributes = {};
    this.className = "";
    this.listeners = new Map();
    this.draggable = false;
    this.textContent = "";
    this.rect = { top: 0, height: 100 };
    this.classList = {
      add: (...names) => this.writeClasses([...this.classes(), ...names]),
      remove: (...names) => this.writeClasses(this.classes().filter((name) => !names.includes(name))),
      contains: (name) => this.classes().includes(name),
    };
  }
  classes() { return String(this.className || "").split(/\s+/).filter(Boolean); }
  writeClasses(names) { this.className = [...new Set(names)].join(" "); }
  get nextSibling() {
    if (!this.parentNode) return null;
    const index = this.parentNode.children.indexOf(this);
    return this.parentNode.children[index + 1] || null;
  }
  getAttribute(name) { return this.attributes[name] ?? null; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  detach(node) {
    if (!node.parentNode) return;
    const index = node.parentNode.children.indexOf(node);
    if (index >= 0) node.parentNode.children.splice(index, 1);
    node.parentNode = null;
    node.parentElement = null;
  }
  appendChild(node) {
    this.detach(node);
    node.parentNode = this;
    node.parentElement = this;
    this.children.push(node);
    return node;
  }
  insertBefore(node, reference) {
    // DOM 规范：child 就是 node 本身时是 no-op，避免拖到紧邻位置时把节点挪走。
    if (reference === node) return node;
    this.detach(node);
    const index = reference ? this.children.indexOf(reference) : -1;
    if (index < 0) this.children.push(node);
    else this.children.splice(index, 0, node);
    node.parentNode = this;
    node.parentElement = this;
    return node;
  }
  remove() { this.detach(this); }
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(callback);
  }
  querySelector(selector) {
    if (!selector.startsWith(".")) return null;
    for (const child of this.children) {
      if (child.classList.contains(selector.slice(1))) return child;
      const found = child.querySelector(selector);
      if (found) return found;
    }
    return null;
  }
  closest(selector) {
    let node = this;
    while (node) {
      if (matches(node, selector)) return node;
      node = node.parentElement;
    }
    return null;
  }
  contains(node) {
    let current = node;
    while (current) {
      if (current === this) return true;
      current = current.parentElement;
    }
    return false;
  }
  getBoundingClientRect() { return this.rect; }
  focus() { this.focused = true; }
}

function makeWidget(key, label) {
  const card = new FakeElement("section");
  card.dataset.widget = key;
  card.dataset.widgetLabel = label;
  card.setAttribute("aria-label", label);
  const content = new FakeElement("div");
  content.className = "content";
  content.textContent = `${label}内容`;
  card.appendChild(content);
  return card;
}

function makeColumn(keys, column, label) {
  const container = new FakeElement("aside");
  container.className = `workbench-side workbench-side-${column}`;
  container.dataset.widgetColumn = column;
  container.dataset.widgetColumnLabel = label;
  for (const key of keys) container.appendChild(makeWidget(key, LABELS[key] || key));
  return container;
}

function setup({ left = ["weather", "countdown"], right = ["almanac", "deepseek"] } = {}) {
  return {
    left: makeColumn(left, "left", "左侧"),
    right: makeColumn(right, "right", "右侧"),
  };
}

function transfer() {
  const data = new Map();
  return {
    types: [],
    dropEffect: "",
    effectAllowed: "",
    setData(type, value) {
      data.set(type, String(value));
      if (!this.types.includes(type)) this.types.push(type);
    },
    getData(type) { return data.get(type) || ""; },
    setDragImage(image) { this.image = image; },
  };
}

function fire(container, type, props = {}) {
  const event = {
    type,
    currentTarget: container,
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() {},
    ...props,
  };
  for (const callback of container.listeners.get(type) || []) callback(event);
  return event;
}

function wrapper(container, key) {
  return container.children.find((node) => node.dataset.widget === key) || null;
}

function keys(container) {
  return container.children.map((node) => node.dataset.widget).filter(Boolean);
}

function handleOf(container, key) {
  return wrapper(container, key)?.querySelector(".widget-drag-handle") || null;
}

async function loadModules(columns, store) {
  const document = {
    querySelector: (selector) => {
      if (selector === ".workbench-side-left") return columns.left;
      if (selector === ".workbench-side-right") return columns.right;
      if (selector === ".workbench-side") return columns.right || columns.left;
      return null;
    },
    createElement: (tag) => new FakeElement(tag),
  };
  const window = { localStorage: {
    getItem: (key) => store.has(key) ? store.get(key) : null,
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  } };
  const context = vm.createContext({ document, window, console });
  const orderPath = path.join(frontend, "widget-order.js");
  const orderModule = new vm.SourceTextModule(await readFile(orderPath, "utf8"), {
    context, identifier: orderPath,
  });
  await orderModule.link(() => { throw new Error("no imports expected"); });
  await orderModule.evaluate();
  const dragPath = path.join(frontend, "widget-drag.js");
  const dragModule = new vm.SourceTextModule(await readFile(dragPath, "utf8"), {
    context, identifier: dragPath,
  });
  await dragModule.link((specifier) => {
    if (specifier === "./widget-order.js") return orderModule;
    throw new Error(`unexpected import: ${specifier}`);
  });
  await dragModule.evaluate();
  return { order: orderModule.namespace, drag: dragModule.namespace, store };
}

test("initWidgetDrag wraps every direct data-widget child in both columns and adds a handle", async () => {
  const columns = setup();
  const store = new Map();
  const { drag } = await loadModules(columns, store);
  assert.equal(drag.initWidgetDrag(columns), true);
  assert.deepEqual(keys(columns.left), ["weather", "countdown"]);
  assert.deepEqual(keys(columns.right), ["almanac", "deepseek"]);
  for (const column of [columns.left, columns.right]) {
    for (const key of keys(column)) {
      const host = wrapper(column, key);
      assert.ok(host.classList.contains("workbench-widget"));
      assert.equal(host.draggable, true);
      assert.ok(host.querySelector(".widget-drag-handle"), `${key} 缺少拖拽手柄`);
      assert.equal(host.children[0].dataset.widget, key, "原卡片应该被包进包装层");
    }
    assert.equal(column.children.find((node) => node.tagName === "P"), undefined, "没有移动时不创建播报/空槽");
  }
});

test("the default initializer finds the left and right columns by class", async () => {
  const columns = setup();
  const store = new Map();
  const { drag } = await loadModules(columns, store);
  assert.equal(drag.initWidgetDrag(), true);
  assert.ok(wrapper(columns.left, "weather"));
  assert.ok(wrapper(columns.right, "almanac"));
});

test("a saved two-column layout is applied on init and a new widget is appended", async () => {
  const columns = setup({ left: ["weather", "countdown"], right: ["almanac", "deepseek", "moon"] });
  const store = new Map([[LAYOUT_KEY, JSON.stringify({ left: ["countdown"], right: ["weather", "almanac"] })]]);
  const { drag } = await loadModules(columns, store);
  drag.initWidgetDrag(columns);
  assert.deepEqual(keys(columns.left), ["countdown"]);
  assert.deepEqual(keys(columns.right), ["weather", "almanac", "deepseek", "moon"]);
});

test("keyboard on the handle moves within a column and stores the layout", async () => {
  const columns = setup({ left: ["weather", "almanac", "countdown"], right: ["deepseek"] });
  const store = new Map();
  const { drag } = await loadModules(columns, store);
  drag.initWidgetDrag(columns);
  const handle = handleOf(columns.left, "countdown");
  const event = fire(columns.left, "keydown", { target: handle, key: "ArrowUp" });
  assert.equal(event.defaultPrevented, true);
  assert.deepEqual(keys(columns.left), ["weather", "countdown", "almanac"]);
  assert.deepEqual(JSON.parse(store.get(LAYOUT_KEY)), {
    left: ["weather", "countdown", "almanac"], right: ["deepseek"],
  });
  assert.equal(handle.focused, true, "移动后手柄保持焦点");
  const status = columns.left.children.find((node) => node.tagName === "P");
  assert.match(status.textContent, /左侧第 2 位/);
});

test("keyboard ArrowRight and ArrowLeft moves a widget between columns", async () => {
  const columns = setup();
  const store = new Map();
  const { drag } = await loadModules(columns, store);
  drag.initWidgetDrag(columns);
  const handle = handleOf(columns.left, "weather");
  fire(columns.left, "keydown", { target: handle, key: "ArrowRight" });
  assert.deepEqual(keys(columns.left), ["countdown"]);
  assert.deepEqual(keys(columns.right), ["almanac", "deepseek", "weather"]);
  assert.deepEqual(JSON.parse(store.get(LAYOUT_KEY)), {
    left: ["countdown"], right: ["almanac", "deepseek", "weather"],
  });
  assert.match(columns.right.children.find((node) => node.tagName === "P").textContent,
    /右侧第 3 位/);
  assert.equal(handle.focused, true, "换栏后手柄保持焦点");

  fire(columns.right, "keydown", { target: handle, key: "ArrowLeft" });
  assert.deepEqual(keys(columns.left), ["countdown", "weather"]);
  assert.deepEqual(keys(columns.right), ["almanac", "deepseek"]);
});

test("dragging a card to the upper half inserts it before the target", async () => {
  const columns = setup({ left: ["weather", "almanac", "countdown"], right: ["deepseek"] });
  const store = new Map();
  const { drag } = await loadModules(columns, store);
  drag.initWidgetDrag(columns);
  for (const [index, key] of keys(columns.left).entries()) {
    wrapper(columns.left, key).rect = { top: index * 112, height: 100 };
  }
  const dragged = wrapper(columns.left, "countdown");
  const target = wrapper(columns.left, "weather");
  const dataTransfer = transfer();
  fire(columns.left, "pointerdown", { target: dragged });
  fire(columns.left, "dragstart", { target: dragged, dataTransfer });
  assert.ok(dragged.classList.contains("is-dragging"));
  assert.ok(dataTransfer.types.includes("application/x-torch-workbench-widget"));
  const over = fire(columns.left, "dragover", { target, dataTransfer, clientY: 10 });
  assert.equal(over.defaultPrevented, true);
  assert.ok(target.classList.contains("is-reorder-before"));
  const drop = fire(columns.left, "drop", { target, dataTransfer, clientY: 10 });
  assert.equal(drop.defaultPrevented, true);
  assert.deepEqual(keys(columns.left), ["countdown", "weather", "almanac"]);
  assert.deepEqual(JSON.parse(store.get(LAYOUT_KEY)), {
    left: ["countdown", "weather", "almanac"], right: ["deepseek"],
  });
  assert.equal(dragged.classList.contains("is-dragging"), false);
  assert.equal(target.classList.contains("is-reorder-before"), false);
});

test("dragging a card to the lower half inserts it after the target", async () => {
  const columns = setup({ left: ["weather", "almanac", "countdown"], right: ["deepseek"] });
  const store = new Map();
  const { drag } = await loadModules(columns, store);
  drag.initWidgetDrag(columns);
  wrapper(columns.left, "weather").rect = { top: 0, height: 100 };
  wrapper(columns.left, "almanac").rect = { top: 112, height: 100 };
  wrapper(columns.left, "countdown").rect = { top: 224, height: 100 };
  const dragged = wrapper(columns.left, "weather");
  const target = wrapper(columns.left, "almanac");
  const dataTransfer = transfer();
  fire(columns.left, "pointerdown", { target: dragged });
  fire(columns.left, "dragstart", { target: dragged, dataTransfer });
  fire(columns.left, "dragover", { target, dataTransfer, clientY: 200 });
  assert.ok(target.classList.contains("is-reorder-after"));
  fire(columns.left, "drop", { target, dataTransfer, clientY: 200 });
  assert.deepEqual(keys(columns.left), ["almanac", "weather", "countdown"]);
});

test("dropping in the gap between cards uses the nearest midpoint", async () => {
  const columns = setup({ left: ["weather", "almanac", "countdown"], right: ["deepseek"] });
  const store = new Map();
  const { drag } = await loadModules(columns, store);
  drag.initWidgetDrag(columns);
  wrapper(columns.left, "weather").rect = { top: 0, height: 100 };
  wrapper(columns.left, "almanac").rect = { top: 112, height: 100 };
  wrapper(columns.left, "countdown").rect = { top: 224, height: 100 };
  const dragged = wrapper(columns.left, "countdown");
  const dataTransfer = transfer();
  fire(columns.left, "pointerdown", { target: dragged });
  fire(columns.left, "dragstart", { target: dragged, dataTransfer });
  const over = fire(columns.left, "dragover", { target: columns.left, dataTransfer, clientY: 120 });
  assert.equal(over.defaultPrevented, true);
  assert.ok(wrapper(columns.left, "almanac").classList.contains("is-reorder-before"));
  fire(columns.left, "drop", { target: columns.left, dataTransfer, clientY: 120 });
  assert.deepEqual(keys(columns.left), ["weather", "countdown", "almanac"]);
});

test("dragging into an empty column shows a drop target and moves the widget", async () => {
  const columns = setup({ left: [], right: ["almanac", "deepseek"] });
  const store = new Map();
  const { drag } = await loadModules(columns, store);
  drag.initWidgetDrag(columns);
  assert.ok(columns.left.querySelector(".workbench-side-empty"), "空栏应有拖入提示");
  const dragged = wrapper(columns.right, "almanac");
  const dataTransfer = transfer();
  fire(columns.right, "pointerdown", { target: dragged });
  fire(columns.right, "dragstart", { target: dragged, dataTransfer });
  fire(columns.left, "dragover", { target: columns.left, dataTransfer, clientY: 10 });
  assert.ok(columns.left.classList.contains("is-drop-target"));
  fire(columns.left, "drop", { target: columns.left, dataTransfer, clientY: 10 });
  assert.deepEqual(keys(columns.left), ["almanac"]);
  assert.deepEqual(keys(columns.right), ["deepseek"]);
  assert.equal(columns.left.querySelector(".workbench-side-empty"), null, "有组件后应移走空槽");
  assert.deepEqual(JSON.parse(store.get(LAYOUT_KEY)), {
    left: ["almanac"], right: ["deepseek"],
  });
  assert.match(columns.left.children.find((node) => node.tagName === "P").textContent,
    /左侧第 1 位/);
});

test("emptying a column brings the drop hint back", async () => {
  const columns = setup({ left: ["weather"], right: ["almanac"] });
  const store = new Map();
  const { drag } = await loadModules(columns, store);
  drag.initWidgetDrag(columns);
  const dragged = wrapper(columns.left, "weather");
  const target = wrapper(columns.right, "almanac");
  wrapper(columns.right, "almanac").rect = { top: 0, height: 100 };
  const dataTransfer = transfer();
  fire(columns.left, "pointerdown", { target: dragged });
  fire(columns.left, "dragstart", { target: dragged, dataTransfer });
  fire(columns.right, "dragover", { target, dataTransfer, clientY: 200 });
  fire(columns.right, "drop", { target, dataTransfer, clientY: 200 });
  assert.deepEqual(keys(columns.left), []);
  assert.deepEqual(keys(columns.right), ["almanac", "weather"]);
  assert.ok(columns.left.querySelector(".workbench-side-empty"));
});

test("a drag that starts on a control is cancelled so the control keeps working", async () => {
  const columns = setup({ left: ["weather"], right: ["almanac"] });
  const store = new Map();
  const { drag } = await loadModules(columns, store);
  drag.initWidgetDrag(columns);
  const card = wrapper(columns.left, "weather").children[0];
  const button = new FakeElement("button");
  card.appendChild(button);
  const dragged = wrapper(columns.left, "weather");
  const dataTransfer = transfer();
  fire(columns.left, "pointerdown", { target: button });
  assert.equal(dragged.draggable, false, "按在控件上时先关掉整卡拖动");
  const event = fire(columns.left, "dragstart", { target: dragged, dataTransfer });
  assert.equal(event.defaultPrevented, true);
  assert.equal(dragged.classList.contains("is-dragging"), false);
  assert.equal(dataTransfer.types.length, 0);
  fire(columns.left, "pointerup", { target: button });
  assert.equal(dragged.draggable, true, "松开后恢复整卡可拖");
});
