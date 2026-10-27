// 工作台左右小组件拖拽（2026-10-27）。
// 约定：左右两个 .workbench-side 栏的直接子元素只要带 data-widget="<稳定 id>"，
// 就会被自动包进 .workbench-widget 并配一个拖拽手柄；以后新增小组件
// 不需要改这个文件，只要补 data-widget / data-widget-label 并放进某一栏即可。
// 整张卡片都可以拖（卡片里的按钮、下拉、输入框除外）；可以跨栏拖动，
// 落点在目标的上/下半区决定插到前面还是后面；空栏会显示可拖入的提示。
// 布局由 widget-order.js 记在本机；聚焦手柄后 ↑/↓ 在本栏移动，←/→ 换到另一栏。
import {
  applyStoredWidgetLayout, applyWidgetOrder, currentWidgetLayout, currentWidgetOrder,
  dropAfterTarget, isWidgetDrag, moveWidget, readWidgetDragKey, setWidgetDragData,
  storeWidgetLayout,
} from "./widget-order.js";

const WRAPPER_CLASS = "workbench-widget";
const HANDLE_CLASS = "widget-drag-handle";
const EMPTY_CLASS = "workbench-side-empty";
const DRAG_READY = "widgetDragReady";
const statusNodes = new WeakMap();
let draggedKey = "";
let pressedTarget = null;

function defaultColumns() {
  if (typeof document === "undefined") return { left: null, right: null };
  const left = document.querySelector?.(".workbench-side-left") || null;
  const right = document.querySelector?.(".workbench-side-right") || null;
  if (left || right) return { left, right };
  // 兼容旧版单栏结构（以及测试里的极简 DOM）：只有一个 .workbench-side。
  return { left: null, right: document.querySelector?.(".workbench-side") || null };
}

function columnsList(columns) {
  return [columns?.left, columns?.right].filter(Boolean);
}

function widgetByKey(container, key) {
  if (!key) return null;
  return Array.from(container?.children || [])
    .find((node) => node?.classList?.contains(WRAPPER_CLASS) && node.dataset?.widget === key) || null;
}

function wrapperInColumns(columns, key) {
  return widgetByKey(columns?.left, key) || widgetByKey(columns?.right, key);
}

function columnLabel(container) {
  return String(container?.dataset?.widgetColumnLabel || "组件栏");
}

/**
 * 当前拖动位置应该插到哪个小组件的前/后。
 * 落在卡片上时看上下半区；落在卡片间隙（container 自身）时按中线找最近的插入点；
 * 目标栏为空时返回 empty，交给调用方直接 append。
 */
function dropSpot(container, event, dragged) {
  const hovered = event.target?.closest?.(`.${WRAPPER_CLASS}`);
  if (hovered && hovered.parentElement === container) {
    if (hovered === dragged) return null;
    return { wrapper: hovered, after: dropAfterTarget(hovered.getBoundingClientRect?.(), event.clientY) };
  }
  const others = Array.from(container?.children || [])
    .filter((node) => node?.classList?.contains(WRAPPER_CLASS) && node !== dragged);
  if (!others.length) return { empty: true };
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
  statusFor(container).textContent = `已把「${wrapperLabel(wrapper)}」移到${columnLabel(container)}第 ${index + 1} 位。`;
}

function clearColumnMarks(container) {
  container?.classList?.remove?.("is-drop-target");
  for (const node of Array.from(container?.children || [])) {
    node.classList?.remove?.("is-reorder-before", "is-reorder-after");
  }
}

function clearDropMarks(columns) {
  for (const container of columnsList(columns)) clearColumnMarks(container);
}

function setWrappersDraggable(container, draggable) {
  for (const node of Array.from(container?.children || [])) {
    if (node?.classList?.contains(WRAPPER_CLASS)) node.draggable = draggable;
  }
}

function finishDrag(columns) {
  clearDropMarks(columns);
  for (const container of columnsList(columns)) {
    for (const node of Array.from(container?.children || [])) {
      node.classList?.remove?.("is-dragging");
    }
    setWrappersDraggable(container, true);
  }
  draggedKey = "";
  pressedTarget = null;
}

function makeHandle(wrapper) {
  const handle = document.createElement("button");
  handle.type = "button";
  handle.className = HANDLE_CLASS;
  handle.draggable = true;
  handle.dataset.widgetHandle = "";
  handle.setAttribute("aria-label",
    `拖动「${wrapperLabel(wrapper)}」调整位置；聚焦后按 ↑/↓ 在本栏移动，←/→ 换到另一栏`);
  handle.title = "拖动调整位置（聚焦后按 ↑/↓ 本栏移动，←/→ 换栏）";
  wrapper.appendChild(handle);
  return handle;
}

/** 把还没有包装的小组件包进 .workbench-widget，并补齐手柄。幂等。 */
export function enhanceWorkbenchWidgets(container) {
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

function renderEmptyState(container) {
  if (!container) return;
  const hasWidget = Array.from(container.children || [])
    .some((node) => node?.classList?.contains(WRAPPER_CLASS));
  const existing = container.querySelector?.(`.${EMPTY_CLASS}`);
  if (hasWidget) {
    existing?.remove?.();
    return;
  }
  if (existing) return;
  const hint = document.createElement("p");
  hint.className = EMPTY_CLASS;
  hint.textContent = "拖动小组件到这里";
  container.appendChild(hint);
}

function renderEmptyStates(columns) {
  for (const container of columnsList(columns)) renderEmptyState(container);
}

function bind(container, columns) {
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
  const release = () => {
    for (const column of columnsList(columns)) setWrappersDraggable(column, true);
    pressedTarget = null;
  };
  container.addEventListener("pointerup", release, true);
  container.addEventListener("pointercancel", release, true);

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
    const dragged = wrapperInColumns(columns, key);
    if (!dragged) return;
    const spot = dropSpot(container, event, dragged);
    if (!spot) {
      clearDropMarks(columns);
      return;
    }
    event.preventDefault?.();
    if (event.dataTransfer) {
      try { event.dataTransfer.dropEffect = "move"; } catch { /* 合成事件不一定支持 */ }
    }
    clearDropMarks(columns);
    if (spot.empty) container.classList?.add?.("is-drop-target");
    else spot.wrapper.classList.add(spot.after ? "is-reorder-after" : "is-reorder-before");
  });

  container.addEventListener("dragleave", (event) => {
    if (!container.contains?.(event.relatedTarget)) clearColumnMarks(container);
  });

  container.addEventListener("drop", (event) => {
    if (!draggedKey && !isWidgetDrag(event)) return;
    const key = draggedKey || readWidgetDragKey(event);
    const dragged = wrapperInColumns(columns, key);
    if (!dragged) {
      finishDrag(columns);
      return;
    }
    const spot = dropSpot(container, event, dragged);
    if (!spot) {
      finishDrag(columns);
      return;
    }
    event.preventDefault?.();
    if (spot.empty) container.appendChild(dragged);
    else if (spot.after) container.insertBefore(dragged, spot.wrapper.nextSibling);
    else container.insertBefore(dragged, spot.wrapper);
    storeWidgetLayout(currentWidgetLayout(columns));
    renderEmptyStates(columns);
    announce(container, dragged);
    finishDrag(columns);
  });

  container.addEventListener("dragend", () => finishDrag(columns));

  container.addEventListener("keydown", (event) => {
    const handle = event.target?.closest?.(`.${HANDLE_CLASS}`);
    if (!handle) return;
    const wrapper = handle.closest?.(`.${WRAPPER_CLASS}`);
    if (!wrapper || wrapper.parentElement !== container) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const target = event.key === "ArrowLeft" ? columns?.left : columns?.right;
      if (!target || target === container) return;
      event.preventDefault?.();
      target.appendChild(wrapper);
      storeWidgetLayout(currentWidgetLayout(columns));
      renderEmptyStates(columns);
      announce(target, wrapper);
      handle.focus?.({ preventScroll: true });
      return;
    }
    const delta = event.key === "ArrowUp" ? -1 : event.key === "ArrowDown" ? 1 : 0;
    if (!delta) return;
    event.preventDefault?.();
    const key = wrapper.dataset.widget || "";
    const order = currentWidgetOrder(container);
    const moved = moveWidget(order, key, delta);
    if (moved.join("|") === order.join("|")) return;
    applyWidgetOrder(container, moved);
    storeWidgetLayout(currentWidgetLayout(columns));
    announce(container, wrapper);
    handle.focus?.({ preventScroll: true });
  });
}

/** 初始化 / 刷新左右两栏小组件；重复调用安全（重画后新出现的组件也会被接进来）。 */
export function initWidgetDrag(columns = defaultColumns()) {
  const normalized = { left: columns?.left || null, right: columns?.right || null };
  const list = columnsList(normalized);
  if (!list.length) return false;
  for (const container of list) enhanceWorkbenchWidgets(container);
  if (!draggedKey) applyStoredWidgetLayout(normalized);
  for (const container of list) {
    if (container.dataset && container.dataset[DRAG_READY] !== "1") {
      container.dataset[DRAG_READY] = "1";
      bind(container, normalized);
    }
  }
  renderEmptyStates(normalized);
  return true;
}

/** renderWorkbench 每次重画后调用：新组件自动包装、布局保持本机保存的结果。 */
export function refreshWorkbenchWidgets(columns = defaultColumns()) {
  return initWidgetDrag(columns);
}
