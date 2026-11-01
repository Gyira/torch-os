// 自我档案页：总览（本子 + 画像）、本子条目、迁移与更新（2026-11-01）。
// 只渲染与交互；写入仍由后端「人生导师 → 待确认」链路负责。
import { api, confirmAction, escapeHtml, openDialog, toast } from "./core.js";
import { renderMarkdownDocument } from "./pillar-markdown.js";
import { isObsidianHost, obsidianOpenUri, openNoteMessage } from "./task-open.js";
import {
  bookPageMarkup, bookByKey, migrationPreviewMarkup, overviewMarkup,
} from "./self-archive-model.js";

const state = {
  host: null,
  overview: null,
  bookKey: "",
  entries: [],
  history: false,
  busy: false,
};
let initialized = false;
let callbacks = { onDebate: null, onChanged: null };

function el(id) { return document.getElementById(id); }

function renderMarkdown(text) {
  try { return renderMarkdownDocument(String(text || "")).html; }
  catch { return escapeHtml(String(text || "")).replace(/\n/g, "<br>"); }
}

function vaultName() {
  return String(state.overview?.vault_name || "").trim();
}

function openVaultPath(path) {
  const relative = String(path || "").trim();
  if (!relative) return;
  if (isObsidianHost() && globalThis.parent && globalThis.parent !== globalThis) {
    try { globalThis.parent.postMessage(openNoteMessage(relative), "*"); return; } catch { /* fall through */ }
  }
  const uri = obsidianOpenUri(vaultName(), relative);
  if (!uri) {
    toast("没有可用的 Obsidian 库名；可以在 Obsidian 里直接打开这个路径。", "warning");
    return;
  }
  try { globalThis.open?.(uri, "_blank", "noopener"); } catch { /* ignore */ }
}

function setBusy(button, busy, text = "正在整理…") {
  if (!button) return;
  button.disabled = busy;
  button.textContent = busy ? text : "更新自我档案";
}

function renderOverview() {
  if (!state.host) return;
  state.host.innerHTML = overviewMarkup(state.overview || {}, renderMarkdown);
}

function renderBook() {
  if (!state.host) return;
  state.host.innerHTML = bookPageMarkup({
    book: { key: state.bookKey, title: bookByKey(state.bookKey)?.title || "档案本子",
      subtitle: bookByKey(state.bookKey)?.subtitle || "",
      path: bookByKey(state.bookKey)?.folder
        ? `01_自我档案/01_档案/${bookByKey(state.bookKey).folder}` : "" },
    entries: state.entries,
    history: state.history,
  });
}

export function initSelfArchive(host, options = {}) {
  state.host = host;
  callbacks = {
    onDebate: typeof options.onDebate === "function" ? options.onDebate : null,
    onChanged: typeof options.onChanged === "function" ? options.onChanged : null,
  };
  if (initialized) return;
  initialized = true;
  host?.addEventListener("click", handleClick);
  document.addEventListener("click", handleGlobalClick);
  document.addEventListener("torch:self-updated", () => {
    loadArchiveOverview().catch(() => {});
  });
}

async function handleClick(event) {
  const book = event.target.closest?.("[data-self-book]");
  if (book) {
    await openBook(book.dataset.selfBook, false);
    return;
  }
  const history = event.target.closest?.("[data-self-history-toggle]");
  if (history) {
    await openBook(state.bookKey, history.dataset.selfHistoryToggle === "1");
    return;
  }
  const debate = event.target.closest?.("[data-self-debate]");
  if (debate) {
    const entry = state.entries.find((item) => item.entry_id === debate.dataset.selfDebate);
    if (typeof callbacks.onDebate === "function") {
      callbacks.onDebate({
        entryId: debate.dataset.selfDebate,
        title: debate.dataset.selfDebateTitle || entry?.title || "",
        text: entry?.text || "",
      });
    }
    return;
  }
  const update = event.target.closest?.("[data-self-update]");
  if (update) { await runSynthesis(update); return; }
  const refresh = event.target.closest?.("[data-self-refresh]");
  if (refresh) {
    if (state.bookKey) await openBook(state.bookKey, state.history);
    else await loadArchiveOverview();
    return;
  }
  const preview = event.target.closest?.("[data-self-migration-preview]");
  if (preview) { await previewMigration(); return; }
  const folder = event.target.closest?.("[data-self-open-folder]");
  if (folder) {
    const first = state.entries[0]?.path;
    if (first) openVaultPath(first);
    else toast("这个本子还没有条目，先在 Obsidian 里打开文件夹。", "info");
    return;
  }
  const portrait = event.target.closest?.("[data-self-open-portrait]");
  if (portrait) {
    const path = state.overview?.paths?.portrait || "";
    if (path) openVaultPath(path);
    else toast("还没有画像文件。", "info");
  }
}

async function handleGlobalClick(event) {
  const apply = event.target.closest?.("[data-self-migration-apply]");
  if (apply) {
    await applyMigration(apply);
    return;
  }
  const close = event.target.closest?.("[data-dialog-close]");
  if (close) el("detail-dialog")?.close?.();
}

export async function loadArchiveOverview() {
  if (!state.host) return;
  state.host.innerHTML = `<div class="self-loading">正在读取自我档案…</div>`;
  try {
    state.overview = await api("/api/self/overview", { timeoutMs: 60000 });
    state.bookKey = "";
    state.entries = [];
    state.history = false;
    renderOverview();
  } catch (error) {
    state.host.innerHTML = `<div class="self-error"><p>读不到自我档案：${escapeHtml(error.message)}</p>
      <button type="button" class="button secondary" data-self-refresh>重试</button></div>`;
  }
}

export async function openBook(bookKey, history = false) {
  if (!state.host || !bookByKey(bookKey)) return;
  state.host.innerHTML = `<div class="self-loading">正在读取${escapeHtml(bookByKey(bookKey).title)}…</div>`;
  try {
    const payload = await api(
      `/api/self/books/${encodeURIComponent(bookKey)}?history=${history ? "1" : "0"}`,
      { timeoutMs: 30000 });
    state.bookKey = bookKey;
    state.entries = Array.isArray(payload.entries) ? payload.entries : [];
    state.history = history;
    renderBook();
    document.dispatchEvent(new CustomEvent("torch:self-book-opened", {
      detail: { bookKey, history },
    }));
  } catch (error) {
    state.host.innerHTML = `<div class="self-error"><p>读不到这个本子：${escapeHtml(error.message)}</p>
      <button type="button" class="button secondary" data-self-book="${escapeHtml(bookKey)}">重试</button></div>`;
  }
}

export function currentBookKey() { return state.bookKey; }

async function runSynthesis(button) {
  if (state.busy) return;
  const ok = await confirmAction({
    title: "更新自我档案？",
    message: "人生导师会读一遍自我档案、记忆、最近日记、任务和知识库，生成画像与待确认补丁。可能耗时 1–3 分钟，会消耗 DeepSeek token；不会自动改档案。",
    confirmLabel: "开始整理",
  });
  if (!ok) return;
  state.busy = true;
  setBusy(button, true);
  try {
    const result = await api("/api/self/synthesize", {
      method: "POST",
      body: JSON.stringify({ confirm: true, range_days: 30 }),
      timeoutMs: 360000,
    });
    toast(`画像已更新；${result.patches?.length || 0} 条档案补丁待确认。`, "success");
    if (typeof callbacks.onChanged === "function") callbacks.onChanged(result);
    if (state.bookKey) await openBook(state.bookKey, state.history);
    else await loadArchiveOverview();
  } catch (error) {
    toast(`整理失败：${error.message}`, "error");
  } finally {
    state.busy = false;
    setBusy(button, false);
  }
}

async function previewMigration() {
  try {
    const preview = await api("/api/self/migration/preview", { timeoutMs: 60000 });
    if (!preview.needed) {
      toast("没有需要迁移的旧档案。", "info");
      return;
    }
    openDialog("迁移自我档案", `${migrationPreviewMarkup(preview)}
      <div class="form-actions">
        <button type="button" class="button primary" data-self-migration-apply>备份并开始迁移</button>
      </div>`);
  } catch (error) {
    toast(`读取迁移计划失败：${error.message}`, "error");
  }
}

async function applyMigration(button) {
  const ok = await confirmAction({
    title: "确认迁移？",
    message: "会先整棵备份 01_自我档案，再按预览把旧笔记转换到新本子。旧文件会移进备份，不删除备份。",
    confirmLabel: "开始迁移",
  });
  if (!ok) return;
  button.disabled = true;
  button.textContent = "迁移中…";
  try {
    const result = await api("/api/self/migration/apply", {
      method: "POST", body: JSON.stringify({ confirm: true }), timeoutMs: 120000,
    });
    toast(`迁移完成：${result.moved || 0} 个文件归位。`, "success");
    el("detail-dialog")?.close?.();
    await loadArchiveOverview();
  } catch (error) {
    toast(`迁移失败：${error.message}`, "error");
    button.disabled = false;
    button.textContent = "备份并开始迁移";
  }
}
