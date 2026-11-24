import { openDshDrawer } from "./dsh-drawer.js";
// 选中文字 → 向对应 AI 提问（2026-10-31）。
const MAX_SELECTION_CHARS = 4000;
let initialized = false;
let pending = null;
let lastAsked = "";
let lastPointer = { x: 0, y: 0 };
let hideTimer = 0;

function el(id) { return document.getElementById(id); }
function button() { return el("selection-ask"); }

function visible(node) {
  return Boolean(node && !node.hidden && node.offsetParent !== null);
}

function selectionText() {
  const text = String(window.getSelection?.()?.toString() || "").trim();
  return text;
}

function inRoleDrawer(node) {
  return Boolean(node?.closest?.("#role-drawer, #role-drawer-backdrop"));
}

function readerSelection() {
  const selection = window.getSelection?.();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  const node = range.commonAncestorContainer;
  const element = node?.nodeType === 1 ? node : node?.parentElement;
  if (!element || inRoleDrawer(element)) return null;
  const article = element.closest?.(".pillar-reader-article");
  if (!article) return null;
  const raw = selection.toString().trim();
  if (raw.length < 2) return null;
  return {
    surface: "reader",
    text: raw.slice(0, MAX_SELECTION_CHARS),
    source: "reader_selection",
    rect: range.getBoundingClientRect(),
    view: article.dataset.readerView || "",
    chapter: article.dataset.readerChapter || "",
    path: article.dataset.readerPath || "",
  };
}

function loveSelection() {
  const selection = window.getSelection?.();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  const love = el("love-view");
  if (!visible(love)) return null;
  const range = selection.getRangeAt(0);
  const node = range.commonAncestorContainer;
  const element = node?.nodeType === 1 ? node : node?.parentElement;
  if (!element || !love.contains(element) || inRoleDrawer(element)) return null;
  const raw = selection.toString().trim();
  if (raw.length < 2) return null;
  return {
    surface: "love",
    text: raw.slice(0, MAX_SELECTION_CHARS),
    source: "love_selection",
    rect: range.getBoundingClientRect(),
  };
}

function currentSelection() {
  return readerSelection() || loveSelection();
}

function hideAsk() {
  window.clearTimeout(hideTimer);
  hideTimer = 0;
  pending = null;
  const node = button();
  if (node) node.hidden = true;
}

function placeAsk(selection) {
  const node = button();
  if (!node) return;
  const rect = selection?.rect;
  let x = lastPointer.x;
  let y = lastPointer.y;
  if (rect && Number.isFinite(rect.left) && (rect.width || rect.height)) {
    x = rect.left + Math.min(rect.width / 2, 180);
    y = rect.top;
  } else if (selection?.anchor?.getBoundingClientRect) {
    const box = selection.anchor.getBoundingClientRect();
    x = box.left + Math.min(box.width / 2, 220);
    y = box.top + Math.min(box.height / 2, 160);
  }
  const margin = 10;
  const width = node.offsetWidth || 120;
  const height = node.offsetHeight || 32;
  const left = Math.max(margin, Math.min(window.innerWidth - width - margin, x - width / 2));
  const top = Math.max(margin, Math.min(window.innerHeight - height - margin, y - height - 8));
  node.style.setProperty("left", `${Math.round(left)}px`);
  node.style.setProperty("top", `${Math.round(top)}px`);
  node.hidden = false;
}


function schedule() {
  window.clearTimeout(hideTimer);
  hideTimer = window.setTimeout(() => {
    const selection = currentSelection();
    if (!selection) {
      hideAsk();
      return;
    }
    // 已经用这段选区打开过伙伴：保持收起，等用户选新的文字再出现。
    if (!el("global-dsh")?.hidden && `${selection.source}|${selection.text}` === lastAsked) {
      hideAsk();
      return;
    }
    pending = selection;
    const node = button();
    if (!node) return;
    const label = el("selection-ask-label");
    if (label) label.textContent = "问 DSH";
    node.setAttribute("aria-label", "带着选文问 DSH");
    placeAsk(selection);
  }, 140);
}

async function ask() {
  const selection = pending;
  if (!selection) return;
  lastAsked = `${selection.source}|${selection.text}`;
  hideAsk();
  openDshDrawer([selection.path, selection.text].filter(Boolean).join("\n"));

}

export function initSelectionAsk() {
  if (initialized) return;
  initialized = true;
  document.addEventListener("pointerup", (event) => {
    lastPointer = { x: event.clientX || 0, y: event.clientY || 0 };
    schedule();
  });
  document.addEventListener("keyup", (event) => {
    if (event.key === "Escape") { hideAsk(); return; }
    schedule();
  });
  document.addEventListener("select", schedule);
  document.addEventListener("selectionchange", schedule);
  document.addEventListener("pointerdown", (event) => {
    if (event.target?.closest?.("#selection-ask")) return;
    if (event.target?.closest?.("#role-drawer, .pillar-reader-article, #love-view")) {
      return;
    }
    hideAsk();
  }, true);
  document.addEventListener("scroll", hideAsk, true);
  window.addEventListener("resize", hideAsk);
  button()?.addEventListener("pointerdown", (event) => event.preventDefault());
  button()?.addEventListener("click", (event) => {
    event.preventDefault();
    ask();
  });
  document.addEventListener("torch:notes-changed", hideAsk);
}

export function hideSelectionAsk() {
  hideAsk();
}
