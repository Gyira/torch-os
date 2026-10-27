// 工作台右侧小组件拖拽排序（2026-10-27）。
// 约定：.workbench-side 的每个直接子元素只要带 data-widget="<稳定 id>"，
// 就会被自动包进 .workbench-widget 并配一个拖拽手柄；以后再加右侧小组件
// 不需要改这个文件，只要补 data-widget / data-widget-label 即可。
// 整张卡片都可以拖（卡片里的按钮、下拉、输入框除外）；落点在目标的上/下半区
// 决定插到前面还是后面。顺序由 widget-order.js 记在本机；聚焦手柄后 ↑/↓ 也能移动。
import {
  applyStoredWidgetOrder, dropAfterTarget, isWidgetDrag, moveWidget,
  readWidgetDragKey, setWidgetDragData, storeWidgetOrder, currentWidgetOrder,
  applyWidgetOrder,
} from "./widget-order.js";

const WRAPPER_CLASS = "workbench-widget";
const HANDLE_CLASS = "widget-drag-handle";
const DRAG_READY = "widgetDragReady";
const statusNodes = new WeakMap();
let draggedKey = "";
let pressedTarget = null;

function defaultContainer() {
  return typeof document === "undefined" ? null : document.querySelector?.(".workbench-side");
}

function widgetByKey(container, key) {
  if (!key) return null;
  return Array.from(container?.children || [])
    .find((node) => node?.classList?.contains(WRAPPER_CLASS) && node.dataset?.widget === key) || null;
}

/**
 * 当前拖动位置应该插到哪个小组件的前/后。
 * 落在卡片上时看上下半区；落在卡片间隙（container 自身）时按中线找最近的插入点。
 */
function dropSpot(container, event, dragged) {
  const hovered = event.target?.closest?.(`.${WRAPPER_CLASS}`);
  if (hovered && hovered.parentElement === container) {
    if (hovered === dragged) return null;
    return { wrapper: hovered, after: dropAfterTarget(hovered.getBoundingClientRect?.(), event.clientY) };
  }
  const others = Array.from(container?.children || [])
    .filter((node) => node?.classList?.contains(WRAPPER_CLASS) && node !== dragged);
  if (!others.length) return null;
  const y = Number(event.clientY);
  for (const node of others) {
    const rect = node.getBoundingClientRect?.();
    if (rect && y < Number(rect.top) + Number(rect.height) / 2) {
      return { wrapper: node, after: false };
    }
  }
  return { wrapper: others[others.length - 1], after: true };
}

function wrapperLabel(wrapper) {
  return String(wrapper?.dataset?.widgetLabel || wrapper?.dataset?.widget || "小组件");
}

function statusFor(container) {
  const existing = statusNodes.get(container);
  if (existing && (existing.parentElement === container || existing.parentNode === container)) return existing;
  const status = document.createElement("p");
  status.className = "visually-hidden";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.dataset.widgetStatus = "";
  container.appendChild(status);
  statusNodes.set(container, status);
  return status;
}

function announce(container, wrapper) {
  const key = wrapper?.dataset?.widget || "";
  const index = currentWidgetOrder(container).indexOf(key);
  if (!key || index < 0) return;
  statusFor(container).textContent = `已把「${wrapperLabel(wrapper)}」移到第 ${index + 1} 位。`;
}

function clearDropMarks(container) {
  for (const node of Array.from(container?.children || [])) {
    node.classList?.remove("is-reorder-before", "is-reorder-after");
  }
}

function setWrappersDraggable(container, draggable) {
  for (const node of Array.from(container?.children || [])) {
    if (node?.classList?.contains(WRAPPER_CLASS)) node.draggable = draggable;
  }
}

function finishDrag(container) {
  for (const node of Array.from(container?.children || [])) {
    node.classList?.remove("is-dragging");
  }
  clearDropMarks(container);
  setWrappersDraggable(container, true);
  draggedKey = "";
  pressedTarget = null;
}

function makeHandle(wrapper) {
  const handle = document.createElement("button");
  handle.type = "button";
  handle.className = HANDLE_CLASS;
  handle.draggable = true;
  handle.dataset.widgetHandle = "";
  handle.setAttribute("aria-label", `拖动「${wrapperLabel(wrapper)}」调整位置，也可以按上下方向键移动`);
  handle.title = "拖动调整位置（聚焦后按 ↑/↓）";
  wrapper.appendChild(handle);
  return handle;
}

/** 把还没有包装的小组件包进 .workbench-widget，并补齐手柄。幂等。 */
export function enhanceWorkbenchWidgets(container = defaultContainer()) {
  if (!container) return [];
  const created = [];
  for (const child of Array.from(container.children || [])) {
    const key = String(child?.dataset?.widget || "").trim();
    if (!key) continue;
    if (child.classList?.contains(WRAPPER_CLASS)) {
      if (typeof child.querySelector !== "function" || !child.querySelector(`.${HANDLE_CLASS}`)) makeHandle(child);
      continue;
    }
    if (typeof container.insertBefore !== "function") continue;
    const wrapper = document.createElement("div");
    wrapper.className = WRAPPER_CLASS;
    wrapper.dataset.widget = key;
    wrapper.dataset.widgetLabel = String(child.dataset?.widgetLabel || child.getAttribute?.("aria-label") || key);
    wrapper.draggable = true;
    container.insertBefore(wrapper, child);
    wrapper.appendChild(child);
    makeHandle(wrapper);
    created.push(wrapper);
  }
  return created;
}

function bind(container) {
  // 记录按下时的真实目标：从卡片内按钮/下拉开始拖时，dragstart 的 target
  // 可能已经是 draggable 的包装层，光看 dragstart 会漏掉这些控件。
  // 同时在这些控件上临时关掉包装层的 draggable，避免手抖把点击变成整卡拖动。
  container.addEventListener("pointerdown", (event) => {
    pressedTarget = event.target;
    const targetWrapper = event.target?.closest?.(`.${WRAPPER_CLASS}`);
    if (!targetWrapper || targetWrapper.parentElement !== container) return;
    const interactive = event.target?.closest?.("a, button, input, select, textarea, label, summary, [contenteditable='true']");
    const fromHandle = Boolean(event.target?.closest?.(`.${HANDLE_CLASS}`));
    targetWrapper.draggable = !interactive || fromHandle;
  }, true);
  container.addEventListener("pointerup", () => {
    setWrappersDraggable(container, true);
    pressedTarget = null;
  }, true);
  container.addEventListener("pointercancel", () => {
    setWrappersDraggable(container, true);
    pressedTarget = null;
  }, true);

  container.addEventListener("dragstart", (event) => {
    const wrapper = event.target?.closest?.(`.${WRAPPER_CLASS}`);
    if (!wrapper || wrapper.parentElement !== container) return;
    // 卡片里的按钮/下拉/输入框照常操作；只有手柄和非交互区域能发起拖动。
    const origin = pressedTarget || event.target;
    const interactive = origin?.closest?.("a, button, input, select, textarea, label, summary, [contenteditable='true']");
    const fromHandle = Boolean(origin?.closest?.(`.${HANDLE_CLASS}`));
    if (interactive && !fromHandle) {
      event.preventDefault?.();
      return;
    }
    const key = wrapper.dataset.widget || "";
    if (!key) return;
    draggedKey = key;
    wrapper.classList.add("is-dragging");
    setWidgetDragData(event.dataTransfer, key);
    if (event.dataTransfer?.setDragImage) {
      try { event.dataTransfer.setDragImage(wrapper, 24, 24); } catch { /* 合成事件不一定支持 */ }
    }
  });

  container.addEventListener("dragover", (event) => {
    if (!draggedKey && !isWidgetDrag(event)) return;
    const key = draggedKey || readWidgetDragKey(event);
    const dragged = widgetByKey(container, key);
    if (!dragged) return;
    const spot = dropSpot(container, event, dragged);
    if (!spot) {
      clearDropMarks(container);
      return;
    }
    event.preventDefault?.();
    if (event.dataTransfer) {
      try { event.dataTransfer.dropEffect = "move"; } catch { /* 合成事件不一定支持 */ }
    }
    clearDropMarks(container);
    spot.wrapper.classList.add(spot.after ? "is-reorder-after" : "is-reorder-before");
  });

  container.addEventListener("dragleave", (event) => {
    if (!container.contains?.(event.relatedTarget)) clearDropMarks(container);
  });

  container.addEventListener("drop", (event) => {
    if (!draggedKey && !isWidgetDrag(event)) return;
    const key = draggedKey || readWidgetDragKey(event);
    const dragged = widgetByKey(container, key);
    if (!dragged) {
      finishDrag(container);
      return;
    }
    const spot = dropSpot(container, event, dragged);
    if (!spot) {
      finishDrag(container);
      return;
    }
    event.preventDefault?.();
    if (spot.after) container.insertBefore(dragged, spot.wrapper.nextSibling);
    else container.insertBefore(dragged, spot.wrapper);
    storeWidgetOrder(currentWidgetOrder(container));
    announce(container, dragged);
    finishDrag(container);
  });

  container.addEventListener("dragend", () => finishDrag(container));

  container.addEventListener("keydown", (event) => {
    const handle = event.target?.closest?.(`.${HANDLE_CLASS}`);
    if (!handle) return;
    const wrapper = handle.closest?.(`.${WRAPPER_CLASS}`);
    if (!wrapper || wrapper.parentElement !== container) return;
    const delta = event.key === "ArrowUp" ? -1 : event.key === "ArrowDown" ? 1 : 0;
    if (!delta) return;
    event.preventDefault?.();
    const key = wrapper.dataset.widget || "";
    const order = currentWidgetOrder(container);
    const moved = moveWidget(order, key, delta);
    if (moved.join("|") === order.join("|")) return;
    applyWidgetOrder(container, moved);
    storeWidgetOrder(currentWidgetOrder(container));
    announce(container, wrapper);
    handle.focus?.({ preventScroll: true });
  });
}

/** 初始化 / 刷新右栏小组件；重复调用安全（重画后新出现的组件也会被接进来）。 */
export function initWidgetDrag(container = defaultContainer()) {
  if (!container) return false;
  enhanceWorkbenchWidgets(container);
  if (!draggedKey) applyStoredWidgetOrder(container);
  if (container.dataset && container.dataset[DRAG_READY] !== "1") {
    container.dataset[DRAG_READY] = "1";
    bind(container);
  }
  return true;
}

/** renderWorkbench 每次重画后调用：新组件自动包装、顺序保持本机保存的结果。 */
export function refreshWorkbenchWidgets(container = defaultContainer()) {
  return initWidgetDrag(container);
}
