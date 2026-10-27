// 工作台右侧小组件的顺序（2026-10-27）：纯函数 + 本机存储 + 拖放数据层。
// 以后新增右侧小组件时，只要在 .workbench-side 的直接子元素上写
// data-widget="<稳定 id>"（可选 data-widget-label="显示名"），
// widget-drag.js 就会自动把它纳入拖拽 / 上下键排序；顺序只保存在当前浏览器，
// 不写 Obsidian 库，也不上传。和 nav-order.js 一样，默认顺序是重排的锚点。
export const WIDGET_ORDER_DEFAULT = ["weather", "almanac", "countdown", "deepseek"];
export const WIDGET_DRAG_MIME = "application/x-torch-workbench-widget";
const STORAGE_KEY = "torch-os:workbench-widget-order";

/** 保留认识的键并去重，再按已知顺序补齐缺失项（新组件默认排在最后）。 */
export function resolveWidgetOrder(stored, known = WIDGET_ORDER_DEFAULT) {
  const result = [];
  const knownSet = new Set(known);
  if (Array.isArray(stored)) {
    for (const key of stored) {
      if (knownSet.has(key) && !result.includes(key)) result.push(key);
    }
  }
  for (const key of known) {
    if (!result.includes(key)) result.push(key);
  }
  return result;
}

/** 上下移动一格（键盘排序用），越界时原样返回。 */
export function moveWidget(order, key, delta) {
  const list = [...order];
  const index = list.indexOf(key);
  if (index < 0 || !Number.isInteger(delta) || delta === 0) return list;
  const target = Math.min(list.length - 1, Math.max(0, index + delta));
  if (target === index) return list;
  list.splice(index, 1);
  list.splice(target, 0, key);
  return list;
}

export function readStoredWidgetOrder() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string") : null;
  } catch {
    return null;
  }
}

export function storeWidgetOrder(order) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(order));
  } catch { /* 隐私模式：顺序在本次页面内仍然有效 */ }
}

export function clearWidgetOrder() {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch { /* ignore */ }
}

/** .workbench-side 的直接子元素（包装后是 .workbench-widget）→ 当前 key 顺序。 */
export function currentWidgetOrder(container) {
  return Array.from(container?.children || [])
    .map((node) => String(node?.dataset?.widget || "").trim())
    .filter(Boolean);
}

/**
 * 按给定顺序重排容器里的小组件。默认顺序只作为“认识哪些组件”的锚点：
 * 当前 DOM 里出现了但默认表里没有的新组件，会排到最后且不会被丢掉。
 */
export function applyWidgetOrder(container, order) {
  const nodes = Array.from(container?.children || []).filter((node) => node?.dataset?.widget);
  if (!nodes.length) return [];
  const domKeys = nodes.map((node) => node.dataset.widget);
  const known = [];
  for (const key of WIDGET_ORDER_DEFAULT) {
    if (domKeys.includes(key) && !known.includes(key)) known.push(key);
  }
  for (const key of domKeys) {
    if (!known.includes(key)) known.push(key);
  }
  const resolved = resolveWidgetOrder(order, known);
  const current = nodes.map((node) => node.dataset.widget);
  // 已经是目标顺序就不要搬 DOM：避免每次重画都打断焦点/过渡。
  if (current.length === resolved.length && current.every((key, index) => key === resolved[index])) {
    return resolved;
  }
  const byKey = new Map(nodes.map((node) => [node.dataset.widget, node]));
  for (const key of resolved) {
    const node = byKey.get(key);
    if (node) container.appendChild(node);
  }
  return resolved;
}

/**
 * 读取本机保存的顺序并重排；没有保存过时保持 DOM 里的默认顺序，
 * 免得将来新加的小组件被自动挪到末尾。保存过则未知的新组件追加在末尾。
 */
export function applyStoredWidgetOrder(container) {
  const stored = readStoredWidgetOrder();
  if (!stored) return currentWidgetOrder(container);
  return applyWidgetOrder(container, stored);
}

/** dragstart 时写入小组件 key；没有 dataTransfer（测试/旧环境）时返回 false。 */
export function setWidgetDragData(dataTransfer, key) {
  if (!dataTransfer || !key) return false;
  try {
    dataTransfer.effectAllowed = "move";
    dataTransfer.setData(WIDGET_DRAG_MIME, String(key));
    dataTransfer.setData("text/plain", String(key));
    return true;
  } catch {
    return false;
  }
}

/** 只接管我们自己的小组件拖放，不要误接文件/文本/任务拖放。 */
export function isWidgetDrag(event) {
  const types = event?.dataTransfer?.types;
  if (!types) return false;
  try {
    return Array.from(types).includes(WIDGET_DRAG_MIME);
  } catch {
    return false;
  }
}

/** drop 时读取被拖的小组件 key；只在 drop 事件里读得到自定义类型。 */
export function readWidgetDragKey(event) {
  try {
    return event?.dataTransfer?.getData(WIDGET_DRAG_MIME) || "";
  } catch {
    return "";
  }
}

/** 落点在目标的上半区还是下半区：上半区插到前面，下半区插到后面。 */
export function dropAfterTarget(rect, clientY) {
  if (!rect) return false;
  return Number(clientY) > Number(rect.top) + Number(rect.height) / 2;
}
