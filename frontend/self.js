import { openDshDrawer } from "./dsh-drawer.js";
import { editArchiveEntry } from "./self-entry-editor.js";
// 自我档案三部分外壳（2026-11-01）：自我档案 / 人生导师 / 八字系统。
// 左栏两级展开；档案模块在 self-archive.js，导师在 mentor.js，八字在 self-bazi.js。
import { $, api, escapeHtml, toast } from "./core.js";
import { showMarkdownNote } from "./markdown-dialog.js";
import { BOOKS } from "./self-archive-model.js";
import {
  initSelfArchive, loadArchiveOverview, openBook, currentBookKey,
} from "./self-archive.js";
import { initMentor, leaveMentor, loadMentor } from "./mentor.js";
import { initSelfBazi, loadSelfBazi } from "./self-bazi.js";

const RAIL_STORAGE = "torch:self-rail";
const state = {
  page: "archive",
  sub: "overview",
  bookKey: "",
  history: false,
  overview: null,
  initialized: false,
  searchTimer: 0,
};
let railCollapsed = new Set();

function el(id) { return document.getElementById(id); }
function main() { return el("self-main"); }

function readCollapsed() {
  try {
    const raw = globalThis.localStorage?.getItem(RAIL_STORAGE);
    const parsed = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string") : []);
  } catch {
    return new Set();
  }
}

function writeCollapsed() {
  try {
    globalThis.localStorage?.setItem(RAIL_STORAGE, JSON.stringify([...railCollapsed]));
  } catch { /* private mode */ }
}

function railGroup({ key, title, items, open }) {
  const collapsed = railCollapsed.has(key);
  return `<div class="self-rail-group${collapsed ? " is-collapsed" : ""}" data-self-group="${key}">
    <button type="button" class="self-rail-group-toggle" data-self-toggle="${key}" aria-expanded="${collapsed ? "false" : "true"}">
      <span>${escapeHtml(title)}</span><i aria-hidden="true"></i>
    </button>
    <div class="self-rail-children" ${collapsed ? "hidden" : ""}>${items}</div>
  </div>`;
}

function railBookItem(book) {
  return `<button type="button" class="self-rail-item" data-self-page="archive" data-self-sub="book:${escapeHtml(book.key)}">${escapeHtml(book.title)}<span class="self-rail-count" data-self-book-count="${escapeHtml(book.key)}"></span></button>`;
}

function railMarkup() {
  const books = BOOKS.map(railBookItem).join("");
  return `<nav class="self-rail" aria-label="自我档案导航">
    ${railGroup({
      key: "archive", title: "自我档案",
      items: `<button type="button" class="self-rail-item active" data-self-page="archive" data-self-sub="overview">档案总览</button>${books}`,
    })}
    ${railGroup({
      key: "mentor", title: "经历与记忆",
      items: `<button type="button" class="self-rail-item" data-self-page="mentor" data-self-sub="history">历史对话</button>
        <button type="button" class="self-rail-item" data-self-page="mentor" data-self-sub="memory">记忆</button>
        <button type="button" class="self-rail-item" data-self-page="mentor" data-self-sub="pending">待确认<span class="self-rail-badge" data-self-badge="pending" hidden>0</span></button>
        <button type="button" class="self-rail-item" data-self-page="mentor" data-self-sub="questions">今日提问<span class="self-rail-badge" data-self-badge="questions" hidden>0</span></button>`,
    })}
    ${railGroup({
      key: "bazi", title: "八字系统",
      items: `<button type="button" class="self-rail-item" data-self-page="bazi" data-self-sub="chart">我的命盘</button>
        <button type="button" class="self-rail-item" data-self-page="bazi" data-self-sub="readings">AI 解读</button>
        <button type="button" class="self-rail-item" data-self-page="bazi" data-self-sub="history">历史</button>`,
    })}
  </nav>`;
}

function shellMarkup() {
  return `<header class="pillar-head self-head">
    <div class="pillar-head-main">
      <span class="pillar-icon" aria-hidden="true">我</span>
      <div>
        <p class="eyebrow">四库 · 自我档案</p>
        <h2>自我档案</h2>
        <p class="pillar-subtitle">记录经历，修正对自己的认识，回看每一次变化。</p>
      </div>
    </div>
    <div class="pillar-head-actions">
      <label class="pillar-search"><span class="sr-only">搜索自我档案</span>
        <input type="search" data-self-search placeholder="搜索档案条目">
      </label>
      <button type="button" class="button primary" data-self-new>记一条</button>
      <button type="button" class="button secondary" title="用当前配置的模型分批读取四库中的 Markdown、TXT、PDF 和 Word（.docx），整理经历、记忆与个人画像" data-self-update>整理全部文档</button>
      <button type="button" class="button secondary" data-self-ask>问 DSH</button>
      <span role="status" id="self-job-status"></span>
    </div>
  </header>
  <div class="self-body">
    ${railMarkup()}
    <div id="self-main" class="self-main" aria-live="polite"></div>
  </div>
  <div id="self-search-results" class="self-search-results" hidden></div>`;
}

function updateRailActive() {
  document.querySelectorAll("#self-view [data-self-page]").forEach((button) => {
    const isActive = button.dataset.selfPage === state.page
      && (state.page !== "archive"
        ? button.dataset.selfSub === state.sub
        : button.dataset.selfSub === (state.sub === "overview" ? "overview" : `book:${state.bookKey}`));
    button.classList.toggle("active", isActive);
    if (isActive) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  document.querySelectorAll("#self-view .self-rail-group").forEach((group) => {
    const key = group.dataset.selfGroup;
    const active = group.querySelector(".self-rail-item.active");
    const toggle = group.querySelector("[data-self-toggle]");
    if (active && railCollapsed.has(key)) {
      railCollapsed.delete(key);
      writeCollapsed();
      group.classList.remove("is-collapsed");
      toggle?.setAttribute("aria-expanded", "true");
      const children = group.querySelector(".self-rail-children");
      if (children) children.hidden = false;
    }
  });
}

function updateBadges() {
  const overview = state.overview || {};
  const pending = Number(overview.pending_count) || 0;
  const remaining = Number(overview.questions?.remaining) || 0;
  for (const [name, value] of [["pending", pending], ["questions", remaining]]) {
    document.querySelectorAll(`#self-view [data-self-badge="${name}"]`).forEach((node) => {
      node.textContent = String(value);
      node.hidden = value <= 0;
    });
  }
  document.dispatchEvent(new CustomEvent("torch:self-badges", {
    detail: { pending, questions: remaining,
      portraitUpdated: overview.portrait?.updated_at || "" },
  }));
}

async function refreshOverview({ render = false } = {}) {
  try {
    state.overview = await api("/api/self/overview", { timeoutMs: 60000 });
  } catch {
    return;
  }
  updateBadges();
  for (const book of state.overview.books || []) {
    const node = document.querySelector(`#self-view [data-self-book-count="${book.key}"]`);
    if (node) node.textContent = String(Number(book.count) || 0);
  }
  if (render) renderArchiveView();
}

async function renderArchiveView() {
  const host = main();
  if (!host) return;
  if (state.sub === "overview") {
    await loadArchiveOverview();
  } else if (state.sub.startsWith("book:")) {
    const key = state.sub.slice(5);
    state.bookKey = key;
    await openBook(key, state.history);
  }
  updateRailActive();
}

async function navigate(page, sub = "", options = {}) {
  if (page === "mentor" && sub === "chat") { openDshDrawer(); sub = "memory"; }
  const previousPage = state.page;
  const validPage = ["archive", "mentor", "bazi"].includes(page) ? page : "archive";
  // 离开导师页时让页面级异步回调失效；服务端历史和草稿都保留。
  if (previousPage === "mentor" && validPage !== "mentor") leaveMentor();
  state.page = validPage;
  state.sub = sub || (validPage === "archive" ? "overview" : validPage === "mentor" ? "memory" : "chart");
  if (validPage === "archive") {
    if (state.sub.startsWith("book:")) {
      state.bookKey = state.sub.slice(5);
      state.history = false;
    } else {
      state.sub = "overview";
      state.bookKey = "";
    }
  }
  updateRailActive();
  const host = main();
  if (!host) return;
  if (validPage === "archive") {
    await renderArchiveView();
  } else if (validPage === "mentor") {
    await loadMentor(host, state.sub, options);
  } else {
    await loadSelfBazi(host, state.sub, options);
  }
  updateRailActive();
  document.dispatchEvent(new CustomEvent("torch:self-page-changed", {
    detail: { page: state.page, sub: state.sub },
  }));
}

function handleDebate(detail) {
  openDshDrawer([detail.title, detail.text].filter(Boolean).join("\n"));
}

let pollTimer;
let trackingJob = "";
let jobSequence = 0;
async function showJob(job) {
  clearTimeout(pollTimer);
  const running = job.state === "running";
  const button = document.querySelector("[data-self-update]");
  if (button) button.disabled = running;
  el("self-job-status").textContent = job.message || "";
  el("self-job-status").title = (job.skipped || []).join("\n");
  if (running) {
    trackingJob = job.id;
    pollTimer = setTimeout(checkJob, 2200);
  } else if (trackingJob) {
    trackingJob = "";
    if (job.state === "done") await refreshOverview({ render: state.page === "archive" });
    if (job.state === "error") toast(job.message, "error");
  }
}

async function checkJob() {
  const seq = ++jobSequence;
  try {
    const job = await api("/api/self/organize");
    if (seq === jobSequence) await showJob(job);
  } catch (error) {
    if (trackingJob && seq === jobSequence) {
      el("self-job-status").textContent = "正在重新连接整理任务…";
      clearTimeout(pollTimer);
      pollTimer = setTimeout(checkJob, 4000);
    }
  }
}

async function runHeaderSynthesis(button) {
  if (button.disabled) return;
  button.disabled = true;
  ++jobSequence;
  try {
    await showJob(await api("/api/self/organize", { method: "POST", body: JSON.stringify({ confirm: true }) }));
  } catch (error) {
    button.disabled = false;
    toast(error.message, "error");
  }
}

async function runSearch(query) {
  const panel = el("self-search-results");
  if (!panel) return;
  const text = String(query || "").trim();
  if (!text) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  try {
    const payload = await api(`/api/search?pillar=self&q=${encodeURIComponent(text)}&limit=30`);
    const items = (payload.items || []).filter((item) =>
      String(item.path || "").startsWith("自我档案/档案/"));
    panel.innerHTML = items.length
      ? items.map((item) => `<button type="button" class="self-search-item" data-self-search-path="${escapeHtml(item.path)}">
          <strong>${escapeHtml(item.title)}</strong>
          <span>${escapeHtml(item.excerpt || "")}</span>
        </button>`).join("")
      : `<p class="field-help">没有匹配的档案条目。</p>`;
    panel.hidden = false;
  } catch (error) {
    panel.innerHTML = `<p class="field-help">搜索失败：${escapeHtml(error.message)}</p>`;
    panel.hidden = false;
  }
}

async function openSearchResult(path) {
  try {
    const note = await api(`/api/notes?path=${encodeURIComponent(path)}`);
    showMarkdownNote(note, state.overview?.vault_name);
  } catch (error) {
    toast(`打不开：${error.message}`, "error");
  }
}

function bindEvents() {
  const shell = el("self-shell");
  shell?.addEventListener("click", async (event) => {
    const toggle = event.target.closest?.("[data-self-toggle]");
    if (toggle) {
      const key = toggle.dataset.selfToggle;
      if (railCollapsed.has(key)) railCollapsed.delete(key);
      else railCollapsed.add(key);
      writeCollapsed();
      const group = toggle.closest(".self-rail-group");
      const collapsed = railCollapsed.has(key);
      group?.classList.toggle("is-collapsed", collapsed);
      toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      const children = group?.querySelector(".self-rail-children");
      if (children) children.hidden = collapsed;
      return;
    }
    const pageButton = event.target.closest?.("[data-self-page]");
    if (pageButton) {
      await navigate(pageButton.dataset.selfPage, pageButton.dataset.selfSub || "");
      return;
    }
    const update = event.target.closest?.("[data-self-update]");
    if (update) { await runHeaderSynthesis(update); return; }
    const ask = event.target.closest?.("[data-self-ask]");
    if (ask) { openDshDrawer(); return; }
    if (event.target.closest?.("[data-self-new]")) editArchiveEntry({ section: state.bookKey });
    const result = event.target.closest?.("[data-self-search-path]");
    if (result) { await openSearchResult(result.dataset.selfSearchPath); return; }
  });
  shell?.addEventListener("input", (event) => {
    const search = event.target.closest?.("[data-self-search]");
    if (!search) return;
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => runSearch(search.value), 220);
  });
  document.addEventListener("torch:self-book-opened", (event) => {
    const detail = event.detail || {};
    if (state.page !== "archive") return;
    state.sub = `book:${detail.bookKey}`;
    state.bookKey = detail.bookKey;
    state.history = Boolean(detail.history);
    updateRailActive();
  });
  document.addEventListener("torch:self-debate", (event) => {
    const detail = event.detail || {};
    handleDebate(detail);
  });
  document.addEventListener("torch:self-page", (event) => {
    const detail = event.detail || {};
    navigate(detail.page || "archive", detail.sub || "").catch((error) => toast(error.message, "error"));
  });
  document.addEventListener("torch:self-refresh", () => {
    refreshOverview({ render: state.page === "archive" }).catch(() => {});
  });
}

export function initSelf() {
  if (state.initialized) return;
  state.initialized = true;
  const shell = el("self-shell");
  if (!shell) return;
  railCollapsed = readCollapsed();
  shell.innerHTML = shellMarkup();
  initSelfArchive(main(), {
    onDebate: handleDebate,
    onChanged: () => {
      // 只刷新角标；档案页自己会重绘画像。切到导师待确认由顶部按钮负责，
      // 避免这里和 self-archive 的重绘互相覆盖。
      refreshOverview().catch(() => {});
    },
  });
  initMentor(main());
  initSelfBazi(main());
  bindEvents();
  document.addEventListener("torch:mentor-pending-changed", () => {
    refreshOverview().catch(() => {});
  });
}

export async function loadSelf() {
  initSelf();
  checkJob();
  await refreshOverview();
  if (state.page === "archive") await renderArchiveView();
  else if (state.page === "mentor") await loadMentor(main(), state.sub);
  else await loadSelfBazi(main(), state.sub);
  updateRailActive();
}

export function selfNavigate(page, sub = "") {
  return navigate(page, sub);
}

export function selfRefresh() {
  return loadSelf();
}

export function selfOverview() {
  return state.overview;
}

export function selfCurrentPage() {
  return { page: state.page, sub: state.sub, bookKey: state.bookKey || currentBookKey() };
}
