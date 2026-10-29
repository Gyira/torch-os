// 全局键盘入口：快捷键总览、g 组合跳转、n 快速记录，以及方向键列表导航。
// 规则在 shortcut-model.js（可单测）；这里只负责 DOM 与页面动作。
import { escapeHtml, state } from "./core.js";
import { openGlobalSearch } from "./global-search.js";
import { SHORTCUT_GROUPS, isEditableTarget, nextListIndex, shortcutCommand } from "./shortcut-model.js";

const CHORD_TIMEOUT_MS = 1800;

// 每一块用同一套方向键规则：焦点在条目上时移动；time-rail 是 tablist，移动即切换。
const ARROW_SCOPES = [
  { selector: ".panel-nav-scroll", item: "[data-view]", activate: false },
  { selector: "#module-nav", item: "[data-pane]", activate: false },
  { selector: ".settings-nav", item: "[data-settings-nav]", activate: false },
  { selector: ".pillar-rail", item: "[data-pillar-chapter]", activate: false },
  { selector: ".notebook-chips", item: "[data-pillar-chapter]", activate: false },
  { selector: ".pillar-week", item: "[data-journal-day]", activate: false },
  { selector: ".time-rail", item: "[data-time-page]", activate: true },
];

let chordPending = false;
let chordTimer = 0;

function dialogsOpen() {
  return Array.from(document.querySelectorAll("dialog[open]")).some((dialog) => dialog.open !== false);
}

function clearChord() {
  chordPending = false;
  window.clearTimeout(chordTimer);
  chordTimer = 0;
  hideChordHint();
}

function chordHintNode(create = false) {
  let node = document.getElementById?.("shortcut-chord-hint") || document.querySelector?.("#shortcut-chord-hint");
  if (!node && create) {
    node = document.createElement("div");
    node.id = "shortcut-chord-hint";
    node.className = "shortcut-chord-hint";
    node.setAttribute("role", "status");
    node.setAttribute("aria-live", "polite");
    document.body.append(node);
  }
  return node;
}

function showChordHint() {
  const node = chordHintNode(true);
  if (!node) return;
  node.textContent = "G 之后按 W/L/S/O/K/, 跳转，H 打开帮助；Esc 取消";
  node.hidden = false;
}

function hideChordHint() {
  const node = chordHintNode(false);
  if (node) node.hidden = true;
}

function startChord() {
  clearChord();
  chordPending = true;
  showChordHint();
  chordTimer = window.setTimeout(clearChord, CHORD_TIMEOUT_MS);
}

function openNewCapture() {
  if (state.view === "workbench") {
    const tasksTab = document.querySelector('[data-time-page="tasks"]');
    if (tasksTab && !tasksTab.classList.contains("is-active")) tasksTab.click();
    const input = document.querySelector("#time-add-input");
    input?.focus?.();
    input?.scrollIntoView?.({ block: "nearest" });
    return true;
  }
  if (["life", "self", "ops", "knowledge"].includes(state.view)) {
    const target = document.querySelector(
      `#${state.view}-view [data-editor-new], #${state.view}-view [data-pillar-new]`);
    if (!target) return false;
    target.click();
    return true;
  }
  return false;
}

function arrowInScope(event) {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return false;
  if (isEditableTarget(event.target)) return false;
  const key = String(event.key || "");
  if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(key)) return false;
  for (const scope of ARROW_SCOPES) {
    const container = event.target.closest?.(scope.selector);
    if (!container) continue;
    // 折叠分组里的章节仍在 DOM 里，但 hidden 容器下的条目不能进入方向键序列。
    const items = Array.from(container.querySelectorAll(scope.item))
      .filter((node) => !node.disabled && !node.closest?.("[hidden]"));
    if (items.length < 2) return false;
    const next = nextListIndex(key, items.indexOf(document.activeElement), items.length);
    if (next < 0) return false;
    event.preventDefault();
    event.stopPropagation();
    const target = items[next];
    target.focus?.({ preventScroll: true });
    target.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    if (scope.activate) target.click();
    return true;
  }
  return false;
}

function onKeydown(event, navigate) {
  if (arrowInScope(event)) return;
  const command = shortcutCommand(event, {
    chordPending,
    editable: isEditableTarget(event.target),
    dialogOpen: dialogsOpen(),
  });
  if (!command) {
    // 组合键按错或超时：下一枚普通按键不再沿用旧组合状态。
    if (chordPending) clearChord();
    return;
  }
  if (command === "start-chord") {
    if (event.repeat) return;
    event.preventDefault();
    startChord();
    return;
  }
  if (command === "cancel-chord") {
    event.preventDefault();
    clearChord();
    return;
  }
  const wasChord = chordPending;
  if (chordPending) clearChord();
  if (command === "open-search") {
    event.preventDefault();
    openGlobalSearch();
    return;
  }
  if (command === "open-shortcuts") {
    event.preventDefault();
    openShortcutSheet(event.target);
    return;
  }
  if (command === "new-capture") {
    event.preventDefault();
    openNewCapture();
    return;
  }
  if (command.startsWith("navigate:")) {
    event.preventDefault();
    if (!wasChord) return;
    const target = command.slice("navigate:".length);
    if (typeof navigate === "function") navigate(target);
  }
}

function renderKeys(item) {
  return item.keys.map((key, index) => {
    const join = index ? (item.join === "then" ? "然后" : "+") : "";
    return `${join}<kbd>${escapeHtml(key)}</kbd>`;
  }).join("");
}

function shortcutMarkup() {
  return `<div class="shortcut-sheet-body">${SHORTCUT_GROUPS.map((group) => `
    <section class="shortcut-group" aria-labelledby="shortcut-group-${escapeHtml(group.id)}">
      <h3 id="shortcut-group-${escapeHtml(group.id)}">${escapeHtml(group.title)}</h3>
      <dl class="shortcut-list">${group.items.map((item) => `
        <div class="shortcut-row"><dt>${renderKeys(item)}</dt><dd>${escapeHtml(item.label)}</dd></div>`).join("")}</dl>
    </section>`).join("")}
    <p class="field-help">单键快捷键在输入框里不会触发，避免打断输入法；Ctrl/⌘ K 仍可用。G 组合键按下后 1.8 秒内按第二键，Esc 取消。</p>
  </div>`;
}

/** 打开静态快捷键总览；不读取任何个人数据。 */
export function openShortcutSheet(trigger = document.activeElement) {
  const existing = document.querySelector("dialog.shortcut-dialog");
  if (existing) {
    existing.querySelector("[data-shortcut-close]")?.focus?.();
    return;
  }
  const dialog = document.createElement("dialog");
  dialog.className = "pillar-dialog shortcut-dialog";
  dialog.setAttribute("aria-labelledby", "shortcut-sheet-title");
  dialog.innerHTML = `
    <div class="pillar-dialog-head">
      <div><p class="eyebrow">KEYBOARD</p><h2 id="shortcut-sheet-title">键盘快捷键</h2>
        <p class="field-help">全部是本地操作；按 Esc 关闭。</p></div>
      <button type="button" class="text-button" data-shortcut-close>关闭</button>
    </div>
    ${shortcutMarkup()}`;
  document.body.append(dialog);
  const close = () => {
    if (dialog.open) dialog.close();
    dialog.remove();
    if (trigger?.isConnected !== false) trigger?.focus?.({ preventScroll: true });
  };
  dialog.querySelector("[data-shortcut-close]")?.addEventListener("click", close);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
  dialog.querySelector("[data-shortcut-close]")?.focus?.();
}

/** 一次性绑定全局键盘与帮助页入口；navigate 由 app.js 提供真实路由。 */
export function initKeyboardShortcuts({ navigate } = {}) {
  document.addEventListener("keydown", (event) => onKeydown(event, navigate));
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest?.("#open-shortcuts, [data-open-shortcuts]");
    if (trigger) openShortcutSheet(trigger);
  });
}
