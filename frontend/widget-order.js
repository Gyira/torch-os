// 工作台左右小组件布局（2026-10-27）：纯函数 + localStorage 缓存（镜像到 .torch-os）+ 拖放数据层。
// 每个小组件有稳定 id；布局 = { left: [key...], right: [key...] }。
// 旧版只存一个扁平数组，读取时按默认分栏迁移（天气/倒计时/纪念日在左，运势/DeepSeek 在右）；
// 顺序写入当前个人数据库的 .torch-os/personal-data.json（localStorage 只是缓存），不上传。
export const WIDGET_LAYOUT_DEFAULT = Object.freeze({
  left: Object.freeze(["weather", "countdown", "anniversary"]),
  right: Object.freeze(["almanac", "train-tracker", "deepseek"]),
});
export const WIDGET_COLUMN_DEFAULT = Object.freeze({
  weather: "left",
  countdown: "left",
  anniversary: "left",
  almanac: "right",
  "train-tracker": "right",
  deepseek: "right",
});
export const WIDGET_ORDER_DEFAULT = Object.freeze([
  ...WIDGET_LAYOUT_DEFAULT.left,
  ...WIDGET_LAYOUT_DEFAULT.right,
]);
export const WIDGET_DRAG_MIME = "application/x-torch-workbench-widget";
const LAYOUT_KEY = "torch-os:workbench-widget-layout";
const LEGACY_ORDER_KEY = "torch-os:workbench-widget-order";

function normalizeColumn(value) {
  return value === "left" ? "left" : "right";
}

function defaultColumn(key, defaults) {
  if (defaults && defaults[key] === "left") return "left";
  if (defaults && defaults[key] === "right") return "right";
  return WIDGET_COLUMN_DEFAULT[key] === "left" ? "left" : "right";
}

/** 保留认识的键并去重，再按已知顺序补齐缺失项（单列内部排序用）。 */
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

/**
 * 把存储布局整理成 {left, right}：
 * - 旧版扁平数组按 defaults（缺省 WIDGET_COLUMN_DEFAULT）分栏，保留列内相对顺序；
 * - 新对象只保留 known 里的键，跨列去重；
 * - 缺失的已知组件补到它的默认栏末尾，未知组件丢弃。
 */
export function resolveWidgetLayout(stored, known = WIDGET_ORDER_DEFAULT, defaults = WIDGET_COLUMN_DEFAULT) {
  const knownList = Array.from(new Set(
    (Array.isArray(known) ? known : []).map((key) => String(key || "").trim()).filter(Boolean),
  ));
  const knownSet = new Set(knownList);
  const result = { left: [], right: [] };
  const seen = new Set();
  const place = (key, column) => {
    const normalized = String(key || "").trim();
    if (!knownSet.has(normalized) || seen.has(normalized)) return;
    seen.add(normalized);
    result[normalizeColumn(column)].push(normalized);
  };
  if (Array.isArray(stored)) {
    for (const key of stored) place(key, defaultColumn(key, defaults));
  } else if (stored && typeof stored === "object") {
    for (const key of Array.isArray(stored.left) ? stored.left : []) place(key, "left");
    for (const key of Array.isArray(stored.right) ? stored.right : []) place(key, "right");
  }
  for (const key of knownList) {
    if (!seen.has(key)) place(key, defaultColumn(key, defaults));
  }
  return result;
}

export function readStoredWidgetLayout() {
  try {
    const raw = window.localStorage.getItem(LAYOUT_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)
          && (Array.isArray(parsed.left) || Array.isArray(parsed.right))) {
        return {
          layout: {
            left: Array.isArray(parsed.left) ? parsed.left : [],
            right: Array.isArray(parsed.right) ? parsed.right : [],
          },
          legacy: false,
        };
      }
    }
  } catch { /* 隐私模式 / 配额 / 坏 JSON：当没存过 */ }
  try {
    const raw = window.localStorage.getItem(LEGACY_ORDER_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return { layout: parsed, legacy: true };
    }
  } catch { /* 同上 */ }
  return null;
}

export function storeWidgetLayout(layout) {
  try {
    window.localStorage.setItem(LAYOUT_KEY, JSON.stringify({
      left: Array.isArray(layout?.left) ? layout.left : [],
      right: Array.isArray(layout?.right) ? layout.right : [],
    }));
    window.localStorage.removeItem(LEGACY_ORDER_KEY);
  } catch { /* 隐私模式：布局在本次页面内仍然有效 */ }
}

export function clearWidgetOrder() {
  try {
    window.localStorage.removeItem(LAYOUT_KEY);
    window.localStorage.removeItem(LEGACY_ORDER_KEY);
  } catch { /* ignore */ }
}

/** 一列里的直接子元素 → 当前 key 顺序（状态播报和空槽没有 data-widget，自动排除）。 */
export function currentWidgetOrder(container) {
  return Array.from(container?.children || [])
    .map((node) => String(node?.dataset?.widget || "").trim())
    .filter(Boolean);
}

export function currentWidgetLayout(columns) {
  return {
    left: currentWidgetOrder(columns?.left),
    right: currentWidgetOrder(columns?.right),
  };
}

/**
 * 按给定顺序重排一列。默认顺序只作为“认识哪些组件”的锚点：
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
 * 把 {left, right} 布局应用到两栏；已经是目标布局时不动 DOM。
 * defaults 用当前 HTML 的落位，新组件没有存过布局时留在作者放的那一栏。
 */
export function applyWidgetLayout(columns, layout, defaults = WIDGET_COLUMN_DEFAULT) {
  const left = columns?.left || null;
  const right = columns?.right || null;
  const nodes = new Map();
  for (const container of [left, right]) {
    for (const child of Array.from(container?.children || [])) {
      const key = String(child?.dataset?.widget || "").trim();
      if (key && !nodes.has(key)) nodes.set(key, child);
    }
  }
  const resolved = resolveWidgetLayout(layout, Array.from(nodes.keys()), defaults);
  const current = currentWidgetLayout({ left, right });
  if (current.left.join("|") === resolved.left.join("|")
      && current.right.join("|") === resolved.right.join("|")) {
    return resolved;
  }
  for (const [container, keys] of [[left, resolved.left], [right, resolved.right]]) {
    if (!container) continue;
    for (const key of keys) {
      const node = nodes.get(key);
      if (node) container.appendChild(node);
    }
  }
  return resolved;
}

/**
 * 读取本机布局并应用；没有存过时保持 HTML 里的默认分栏。
 * 旧版扁平顺序按当前落位分栏迁移，然后写回新 key。
 */
export function applyStoredWidgetLayout(columns) {
  const current = currentWidgetLayout(columns);
  const defaults = {};
  for (const column of ["left", "right"]) {
    for (const key of current[column]) defaults[key] = column;
  }
  const stored = readStoredWidgetLayout();
  if (!stored) return current;
  const known = [...current.left, ...current.right];
  const resolved = resolveWidgetLayout(stored.layout, known, defaults);
  applyWidgetLayout(columns, resolved, defaults);
  if (stored.legacy) storeWidgetLayout(resolved);
  return resolved;
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
