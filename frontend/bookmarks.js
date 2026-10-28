// 网页书签栏（2026-10-28）：工作台问候语右侧一条 Edge 风格的快捷入口。
//
// 交互照 Edge：点书签 → 用系统默认浏览器打开；最右端齿轮 → 管理面板（加/改/删/排序）。
// 数据在 bookmark-model.js（纯函数、可单测），这里只负责 DOM 与打开动作。
// 在 Obsidian 插件里通过 `torch:open-url` 宿主桥调用 shell.openExternal，
// 浏览器直开时退回 window.open，所以两种承载方式都能落到默认浏览器。

import { escapeHtml, setStatus } from "./core.js";
import { isObsidianHost } from "./task-open.js";
import {
  BOOKMARK_LIMIT,
  addBookmark,
  colorIndex,
  moveBookmark,
  normalizeBookmarkUrl,
  readBookmarks,
  removeBookmark,
  siteInitial,
  updateBookmark,
  writeBookmarks,
} from "./bookmark-model.js";

const BAR_ID = "workbench-bookmarks";
const DIALOG_CLASS = "bookmark-dialog";

let items = [];
let dialog = null;

function storage() {
  try {
    return globalThis.localStorage || null;
  } catch {
    return null;
  }
}

function statusNode(root) {
  return root?.querySelector?.("[data-bookmark-status]") || null;
}

function iconMarkup(bookmark) {
  return `<span class="bookmark-chip-icon" data-tone="${colorIndex(bookmark.url)}" aria-hidden="true">${escapeHtml(siteInitial(bookmark.name, bookmark.url))}</span>`;
}

function chipMarkup(bookmark) {
  return `<button type="button" class="bookmark-chip" data-bookmark-open="${escapeHtml(bookmark.id)}" title="${escapeHtml(`${bookmark.name} · ${bookmark.url}`)}">
    ${iconMarkup(bookmark)}
    <span class="bookmark-chip-name">${escapeHtml(bookmark.name)}</span>
  </button>`;
}

/** 重画问候语右侧那条书签栏；列表为空时留一个「添加」入口，不是一片空白。 */
function renderBar() {
  const bar = document.getElementById(BAR_ID);
  if (!bar) return;
  // 标记里先是 hidden：脚本没跑起来时不显示一条空壳；渲染出内容再打开。
  bar.hidden = false;
  const chips = items.map(chipMarkup).join("");
  bar.innerHTML = `
    <div class="bookmark-bar-chips">${chips || `<button type="button" class="bookmark-chip bookmark-chip-ghost" data-bookmark-manage>
      <span class="bookmark-chip-icon" data-tone="4" aria-hidden="true">＋</span>
      <span class="bookmark-chip-name">添加网页书签</span>
    </button>`}</div>
    <span class="bookmark-bar-sep" aria-hidden="true"></span>
    <button type="button" class="bookmark-gear" data-bookmark-manage aria-label="添加或管理网页书签" title="添加 / 管理网页书签">
      <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
        <circle cx="12" cy="12" r="3"></circle>
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
      </svg>
    </button>`;
}

function save(next) {
  items = next;
  writeBookmarks(storage(), items);
  renderBar();
}

/**
 * 用默认浏览器打开一个网址；返回 false 表示这条地址不可用。
 * Obsidian 里交给插件（Electron shell.openExternal），浏览器里直接开新标签页。
 */
export function openBookmarkUrl(url) {
  const target = normalizeBookmarkUrl(url);
  if (!target) return false;
  if (isObsidianHost() && globalThis.parent && globalThis.parent !== globalThis) {
    try {
      globalThis.parent.postMessage({ type: "torch:open-url", url: target }, "*");
      return true;
    } catch {
      // 宿主桥不可用就退回下面的 window.open
    }
  }
  try {
    globalThis.open?.(target, "_blank", "noopener");
    return true;
  } catch {
    return false;
  }
}

function rowMarkup(bookmark) {
  return `<li class="bookmark-manage-row" data-bookmark-row="${escapeHtml(bookmark.id)}">
    ${iconMarkup(bookmark)}
    <input type="text" data-field="name" value="${escapeHtml(bookmark.name)}" maxlength="24" aria-label="书签名称" />
    <input type="text" data-field="url" value="${escapeHtml(bookmark.url)}" maxlength="500" aria-label="书签网址" />
    <button type="button" class="small-button" data-bookmark-action="open">打开</button>
    <button type="button" class="small-button" data-bookmark-action="up" aria-label="上移" title="上移">↑</button>
    <button type="button" class="small-button" data-bookmark-action="down" aria-label="下移" title="下移">↓</button>
    <button type="button" class="small-button reject" data-bookmark-action="delete">删除</button>
  </li>`;
}

function listMarkup() {
  if (!items.length) {
    return '<li class="bookmark-manage-empty">还没有书签。在上面填一条网址，点「添加」就会出现在问候语右侧。</li>';
  }
  return items.map(rowMarkup).join("");
}

function dialogMarkup() {
  return `
    <div class="pillar-dialog-head">
      <div>
        <p class="eyebrow">BOOKMARKS</p>
        <h2 id="bookmark-dialog-title">网页书签</h2>
        <p class="field-help">添加后立刻出现在「晚上好」右侧；点书签用默认浏览器打开。网页版问完，把「分享」链接贴到下面「与神对话」里，我就能取回原文写进四库。书签跟着当前个人数据库走，换电脑也还在。</p>
      </div>
      <button type="button" class="text-button" data-bookmark-close>关闭</button>
    </div>
    <div class="bookmark-dialog-body">
      <div class="bookmark-add">
        <input type="text" data-bookmark-new-name maxlength="24" placeholder="名称（可留空）" aria-label="新书签名称" />
        <input type="text" data-bookmark-new-url maxlength="500" placeholder="https://chat.deepseek.com" aria-label="新书签网址" />
        <button type="button" class="button primary" data-bookmark-add>添加</button>
      </div>
      <p class="status-message" data-bookmark-status role="status" aria-live="polite"></p>
      <ul class="bookmark-manage-list" data-bookmark-list>${listMarkup()}</ul>
      <p class="field-help bookmark-manage-hint">最多 ${BOOKMARK_LIMIT} 个；↑↓ 调顺序。只接受 http/https 地址，重名网址不会重复添加。</p>
    </div>`;
}

function refreshList() {
  const list = dialog?.querySelector?.("[data-bookmark-list]");
  if (list) list.innerHTML = listMarkup();
}

function syncRow(row, bookmark) {
  if (!row || !bookmark) return;
  const icon = row.querySelector(".bookmark-chip-icon");
  if (icon) {
    icon.textContent = siteInitial(bookmark.name, bookmark.url);
    icon.dataset.tone = String(colorIndex(bookmark.url));
  }
  // 名称留空、网址缺协议时，模型会补成规范值：把输入框回写成真正存下来的样子。
  const nameInput = row.querySelector('[data-field="name"]');
  if (nameInput) nameInput.value = bookmark.name;
  const urlInput = row.querySelector('[data-field="url"]');
  if (urlInput) urlInput.value = bookmark.url;
  row.title = `${bookmark.name} · ${bookmark.url}`;
}

function addFromDialog(root) {
  const nameInput = root.querySelector("[data-bookmark-new-name]");
  const urlInput = root.querySelector("[data-bookmark-new-url]");
  const result = addBookmark(items, {
    name: nameInput?.value || "",
    url: urlInput?.value || "",
  });
  if (result.error) {
    setStatus(statusNode(root), result.error, "error");
    urlInput?.focus?.();
    return;
  }
  save(result.list);
  if (nameInput) nameInput.value = "";
  if (urlInput) urlInput.value = "";
  refreshList();
  setStatus(statusNode(root), `已添加「${result.bookmark.name}」，继续加直接再填一条。`, "success");
  urlInput?.focus?.();
}

function editRow(root, row, input) {
  const id = row.dataset.bookmarkRow;
  const field = input.dataset.field;
  const result = updateBookmark(items, id, { [field]: input.value });
  if (result.error) {
    const current = items.find((item) => item.id === id);
    if (input && current) input.value = current[field] || "";
    setStatus(statusNode(root), result.error, "error");
    return;
  }
  save(result.list);
  syncRow(row, result.bookmark);
  setStatus(statusNode(root), `已更新「${result.bookmark.name}」。`, "success");
}

function handleRowAction(root, row, action) {
  const id = row?.dataset?.bookmarkRow || "";
  const bookmark = items.find((item) => item.id === id);
  if (!bookmark) return;
  if (action === "open") {
    openBookmarkUrl(bookmark.url);
    return;
  }
  if (action === "up" || action === "down") {
    const result = moveBookmark(items, id, action === "up" ? -1 : 1);
    save(result.list);
    refreshList();
    setStatus(statusNode(root), result.moved ? `「${bookmark.name}」已${action === "up" ? "上移" : "下移"}。` : "已经到头了。", result.moved ? "success" : "neutral");
    return;
  }
  if (action === "delete") {
    const result = removeBookmark(items, id);
    save(result.list);
    refreshList();
    setStatus(statusNode(root), `已删除「${bookmark.name}」。`, "success");
  }
}

/** 打开齿轮面板：新增输入框直接聚焦，回车即添加。 */
export function openBookmarkDialog(trigger = null) {
  if (dialog) {
    dialog.querySelector("[data-bookmark-new-url]")?.focus?.();
    return;
  }
  const node = document.createElement("dialog");
  node.className = `pillar-dialog ${DIALOG_CLASS}`;
  node.setAttribute("aria-labelledby", "bookmark-dialog-title");
  node.innerHTML = dialogMarkup();
  document.body.append(node);
  dialog = node;

  const close = () => {
    if (node.open && typeof node.close === "function") node.close();
    node.remove();
    dialog = null;
    if (trigger?.isConnected !== false) trigger?.focus?.({ preventScroll: true });
  };

  node.querySelector("[data-bookmark-close]")?.addEventListener("click", close);
  node.addEventListener("cancel", (event) => {
    event.preventDefault();
    close();
  });
  node.addEventListener("click", (event) => {
    if (event.target === node) {
      // 原生 dialog 的遮罩点击命中 dialog 本身：只有点在外框之外才关。
      const rect = node.getBoundingClientRect?.();
      if (!rect) return;
      const outside = event.clientX < rect.left || event.clientX > rect.right
        || event.clientY < rect.top || event.clientY > rect.bottom;
      if (outside) close();
    }
  });
  node.addEventListener("click", (event) => {
    const addButton = event.target.closest?.("[data-bookmark-add]");
    if (addButton) {
      addFromDialog(node);
      return;
    }
    const action = event.target.closest?.("[data-bookmark-action]");
    if (!action) return;
    handleRowAction(node, action.closest("[data-bookmark-row]"), action.dataset.bookmarkAction);
  });
  node.addEventListener("change", (event) => {
    const input = event.target.closest?.("[data-field]");
    const row = input?.closest?.("[data-bookmark-row]");
    if (!input || !row) return;
    editRow(node, row, input);
  });
  node.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing) return;
    if (!event.target.closest?.("[data-bookmark-new-name], [data-bookmark-new-url]")) return;
    event.preventDefault();
    addFromDialog(node);
  });

  if (typeof node.showModal === "function") node.showModal();
  else node.setAttribute("open", "");
  node.querySelector("[data-bookmark-new-url]")?.focus?.();
}

/** 启动时读库里的书签并接上点击；由 workbench.js 调用一次。 */
export function initBookmarks() {
  items = readBookmarks(storage());
  renderBar();
  const bar = document.getElementById(BAR_ID);
  if (!bar) return;
  bar.addEventListener("click", (event) => {
    const manage = event.target.closest?.("[data-bookmark-manage]");
    if (manage) {
      openBookmarkDialog(manage);
      return;
    }
    const chip = event.target.closest?.("[data-bookmark-open]");
    if (!chip) return;
    const bookmark = items.find((item) => item.id === chip.dataset.bookmarkOpen);
    if (bookmark) openBookmarkUrl(bookmark.url);
  });
}
