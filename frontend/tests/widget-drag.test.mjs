import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 小组件拖拽的 DOM 层：用一个小假 DOM 验证包装、拖放插入和键盘排序。
// 不替代真实浏览器的拖拽手势验证。
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

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

function setup(order = ["weather", "almanac", "countdown"]) {
  const labels = { weather: "天气", almanac: "运势", countdown: "倒计时", moon: "月亮" };
  const container = new FakeElement("aside");
  container.className = "workbench-side";
  for (const key of order) container.appendChild(makeWidget(key, labels[key] || key));
  return container;
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

async function loadModules(container, store) {
  const document = {
    querySelector: (selector) => (selector === ".workbench-side" ? container : null),
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
  return { order: orderModule.namespace, drag: dragModule.namespace };
}

test("initWidgetDrag wraps every direct data-widget child and adds a handle", async () => {
  const container = setup(["weather", "moon", "almanac", "countdown"]);
  const store = new Map();
  const { drag } = await loadModules(container, store);
  assert.equal(drag.initWidgetDrag(), true);
  assert.deepEqual(keys(container), ["weather", "moon", "almanac", "countdown"]);
  for (const key of keys(container)) {
    const host = wrapper(container, key);
    assert.ok(host.classList.contains("workbench-widget"));
    assert.equal(host.draggable, true);
    assert.ok(host.querySelector(".widget-drag-handle"), `${key} 缺少拖拽手柄`);
    assert.equal(host.children[0].dataset.widget, key, "原卡片应该被包进包装层");
  }
  const status = container.children.find((node) => node.tagName === "P");
  assert.equal(status, undefined, "没有移动时不创建播报节点");
});

test("a saved order is applied on init and a new widget is appended", async () => {
  const container = setup(["weather", "moon", "almanac", "countdown"]);
  const store = new Map([["torch-os:workbench-widget-order", JSON.stringify(["countdown", "weather", "almanac"])]]);
  const { drag } = await loadModules(container, store);
  drag.initWidgetDrag();
  assert.deepEqual(keys(container), ["countdown", "weather", "almanac", "moon"]);
});

test("keyboard on the handle moves the widget and stores the order", async () => {
  const container = setup();
  const store = new Map();
  const { drag } = await loadModules(container, store);
  drag.initWidgetDrag();
  const handle = handleOf(container, "countdown");
  const event = fire(container, "keydown", { target: handle, key: "ArrowUp" });
  assert.equal(event.defaultPrevented, true);
  assert.deepEqual(keys(container), ["weather", "countdown", "almanac"]);
  assert.deepEqual(JSON.parse(store.get("torch-os:workbench-widget-order")),
    ["weather", "countdown", "almanac"]);
  assert.equal(handle.focused, true, "移动后手柄保持焦点");
  const status = container.children.find((node) => node.tagName === "P");
  assert.match(status.textContent, /第 2 位/);
});

test("dragging a card to the upper half inserts it before the target", async () => {
  const container = setup();
  const store = new Map();
  const { drag } = await loadModules(container, store);
  drag.initWidgetDrag();
  for (const [index, key] of keys(container).entries()) {
    wrapper(container, key).rect = { top: index * 112, height: 100 };
  }
  const dragged = wrapper(container, "countdown");
  const target = wrapper(container, "weather");
  const dataTransfer = transfer();
  fire(container, "pointerdown", { target: dragged });
  fire(container, "dragstart", { target: dragged, dataTransfer });
  assert.ok(dragged.classList.contains("is-dragging"));
  assert.ok(dataTransfer.types.includes("application/x-torch-workbench-widget"));
  const over = fire(container, "dragover", { target: target, dataTransfer, clientY: 10 });
  assert.equal(over.defaultPrevented, true);
  assert.ok(target.classList.contains("is-reorder-before"));
  const drop = fire(container, "drop", { target: target, dataTransfer, clientY: 10 });
  assert.equal(drop.defaultPrevented, true);
  assert.deepEqual(keys(container), ["countdown", "weather", "almanac"]);
  assert.deepEqual(JSON.parse(store.get("torch-os:workbench-widget-order")),
    ["countdown", "weather", "almanac"]);
  assert.equal(dragged.classList.contains("is-dragging"), false);
  assert.equal(target.classList.contains("is-reorder-before"), false);
});

test("dragging a card to the lower half inserts it after the target", async () => {
  const container = setup();
  const store = new Map();
  const { drag } = await loadModules(container, store);
  drag.initWidgetDrag();
  wrapper(container, "weather").rect = { top: 0, height: 100 };
  wrapper(container, "almanac").rect = { top: 112, height: 100 };
  wrapper(container, "countdown").rect = { top: 224, height: 100 };
  const dragged = wrapper(container, "weather");
  const target = wrapper(container, "almanac");
  const dataTransfer = transfer();
  fire(container, "pointerdown", { target: dragged });
  fire(container, "dragstart", { target: dragged, dataTransfer });
  fire(container, "dragover", { target, dataTransfer, clientY: 200 });
  assert.ok(target.classList.contains("is-reorder-after"));
  fire(container, "drop", { target, dataTransfer, clientY: 200 });
  assert.deepEqual(keys(container), ["almanac", "weather", "countdown"]);
});

test("dropping in the gap between cards uses the nearest midpoint", async () => {
  const container = setup();
  const store = new Map();
  const { drag } = await loadModules(container, store);
  drag.initWidgetDrag();
  wrapper(container, "weather").rect = { top: 0, height: 100 };
  wrapper(container, "almanac").rect = { top: 112, height: 100 };
  wrapper(container, "countdown").rect = { top: 224, height: 100 };
  const dragged = wrapper(container, "countdown");
  const dataTransfer = transfer();
  fire(container, "pointerdown", { target: dragged });
  fire(container, "dragstart", { target: dragged, dataTransfer });
  const over = fire(container, "dragover", { target: container, dataTransfer, clientY: 120 });
  assert.equal(over.defaultPrevented, true);
  assert.ok(wrapper(container, "almanac").classList.contains("is-reorder-before"));
  fire(container, "drop", { target: container, dataTransfer, clientY: 120 });
  assert.deepEqual(keys(container), ["weather", "countdown", "almanac"]);
});

test("a drag that starts on a control is cancelled so the control keeps working", async () => {
  const container = setup();
  const store = new Map();
  const { drag } = await loadModules(container, store);
  drag.initWidgetDrag();
  const card = wrapper(container, "weather").children[0];
  const button = new FakeElement("button");
  card.appendChild(button);
  const dragged = wrapper(container, "weather");
  const dataTransfer = transfer();
  fire(container, "pointerdown", { target: button });
  assert.equal(dragged.draggable, false, "按在控件上时先关掉整卡拖动");
  const event = fire(container, "dragstart", { target: dragged, dataTransfer });
  assert.equal(event.defaultPrevented, true);
  assert.equal(dragged.classList.contains("is-dragging"), false);
  assert.equal(dataTransfer.types.length, 0);
  fire(container, "pointerup", { target: button });
  assert.equal(dragged.draggable, true, "松开后恢复整卡可拖");
});
