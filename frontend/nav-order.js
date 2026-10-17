// Sidebar menu order: one shared source for labels plus a local, per-browser order.
// 五大模块收敛后的产品侧边栏（对话交给 dsh；帮助并进设置）。
export const NAV_ORDER_DEFAULT = ["workbench", "identity", "brain", "settings"];

export const NAV_LABELS = {
  workbench: "工作台", identity: "数字分身", brain: "第二大脑", settings: "设置与状态",
};
const STORAGE_KEY = "torch-os:nav-order";

export function resolveOrder(stored, known = NAV_ORDER_DEFAULT) {
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

export function moveItem(order, key, delta) {
  const list = [...order];
  const index = list.indexOf(key);
  if (index < 0 || !Number.isInteger(delta) || delta === 0) return list;
  const target = Math.min(list.length - 1, Math.max(0, index + delta));
  if (target === index) return list;
  list.splice(index, 1);
  list.splice(target, 0, key);
  return list;
}

export function readStoredOrder() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string") : null;
  } catch {
    return null;
  }
}

export function storeOrder(order) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(order));
  } catch { /* private mode: order simply stays for this page */ }
}

export function clearOrder() {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch { /* ignore */ }
}

export function currentNavOrder(known = NAV_ORDER_DEFAULT) {
  return resolveOrder(readStoredOrder(), known);
}

export function applyNavOrder() {
  const buttons = Array.from(document.querySelectorAll(".nav-item"));
  if (!buttons.length) return [...NAV_ORDER_DEFAULT];
  // The default order is the anchor, not the current DOM order: after a move the
  // DOM is already reordered, and deriving "known" from it would make
  // "restore default" a no-op. Views that are not in the default list (a newer
  // menu entry) keep working by being appended after the known ones.
  const known = [...NAV_ORDER_DEFAULT];
  for (const button of buttons) {
    const view = button.dataset.view;
    if (view && !known.includes(view)) known.push(view);
  }
  const order = resolveOrder(readStoredOrder(), known);
  const parent = buttons[0].parentElement;
  if (!parent) return order;
  // 侧边栏现在是两级菜单：二级列表必须紧跟它的一级项，否则重排后会对错模块。
  const sublists = typeof document.querySelectorAll === "function"
    ? Array.from(document.querySelectorAll(".nav-sublist")) : [];
  for (const key of order) {
    const button = buttons.find((item) => item.dataset.view === key);
    if (!button) continue;
    parent.appendChild(button);
    const sublist = sublists.find((item) => item.dataset.sublist === key);
    if (sublist) parent.appendChild(sublist);
  }
  // 浏览器/Obsidian 都用顶部模块导航；设置里的顺序调整也要同步到它。
  const topButtons = typeof document.querySelectorAll === "function"
    ? Array.from(document.querySelectorAll(".panel-nav-item")) : [];
  if (topButtons.length) {
    const topParent = topButtons[0].parentElement;
    if (topParent) {
      for (const key of order) {
        const button = topButtons.find((item) => item.dataset.view === key);
        if (button) topParent.appendChild(button);
      }
    }
  }
  return order;
}
