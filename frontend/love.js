// 「恋爱系统」v2（2026-11-01）：七个页面。
// 数据全部来自本机 API；聊天原文与照片只保存在 02_生活运营/01_恋爱系统/。
import { api, confirmAction, escapeHtml, openDialog, toast } from "./core.js";
import { openRoleDrawer } from "./roles.js";
import { COMPANIONS } from "./ai-companions.js";
import { renderLoveCycle, handleLoveCycleAction } from "./love-cycle.js";
import { invalidateChapterCache, openNoteEditor } from "./note-editor.js";

const PAGE_TITLES = {
  memories: "恋爱记忆",
  chat: "微信聊天",
  schedule: "排班日历",
  cycle: "经期关怀",
  gallery: "恋爱相册",
  bazi: "八字合婚",
  master: "恋爱大师",
};
const CONSENT_KEY = "torch-os:love-consent-v3";
const MONTH_KEY = "torch-os:love-schedule-month";
const CHAT_MONTH_KEY = "torch-os:love-chat-month";
const PAGE_KEYS = Object.keys(PAGE_TITLES);

const love = {
  inited: false,
  loading: false,
  page: "memories",
  consent: false,
  status: null,
  memory: null,
  memoryNotebook: "",
  memoryNotes: [],
  chatStatus: null,
  chatCalendar: null,
  chatMonth: "",
  chatDay: "",
  chatDayData: null,
  chatSessions: null,
  chatSessionsLoading: false,
  chatSessionsError: "",
  chatKeyword: "",
  chatLog: null,
  chatPoll: 0,
  chatRebinding: false,
  scheduleMonth: "",
  scheduleData: null,
  gallery: null,
  galleryView: "all",
  galleryAlbum: 0,
  galleryKind: "",
  galleryItems: null,
  gallerySelection: new Set(),
  baziHistory: [],
  baziSelectedId: 0,
  baziDraft: null,
  master: null,
  masterRuns: [],
  masterSelectedId: 0,
  masterPoll: 0,
};

const $id = (id) => document.getElementById(id);
const numberFormat = new Intl.NumberFormat("zh-CN");

function readStorage(key) {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
function writeStorage(key, value) {
  try { window.localStorage.setItem(key, value); } catch { /* private mode */ }
}
function formatInt(value) {
  const number = Number(value);
  return Number.isFinite(number) ? numberFormat.format(Math.round(number)) : "—";
}
function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeZone: "Asia/Shanghai" }).format(date);
}
function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Shanghai",
  }).format(date);
}
function formatMonth(value) {
  const match = /^(\d{4})-(\d{2})$/.exec(String(value || ""));
  return match ? `${match[1]} 年 ${Number(match[2])} 月` : String(value || "");
}
function weekday(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", { weekday: "long", timeZone: "Asia/Shanghai" }).format(date);
}
function percent(value, digits = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(digits)}%` : "—";
}
function todayText() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date());
}
function currentMonthText() {
  return todayText().slice(0, 7);
}
function shiftMonth(month, delta) {
  const [year, monthNumber] = String(month).split("-").map(Number);
  const shifted = new Date(Date.UTC(year, monthNumber - 1 + delta, 1));
  return `${shifted.getUTCFullYear()}-${String(shifted.getUTCMonth() + 1).padStart(2, "0")}`;
}
function monthLabel(month) {
  const [year, monthNumber] = String(month).split("-");
  return `${year}年${Number(monthNumber)}月`;
}
function monthLeadingBlanks(month) {
  const [year, monthNumber] = String(month).split("-").map(Number);
  const day = new Date(Date.UTC(year, monthNumber - 1, 1)).getUTCDay();
  return (day + 6) % 7;
}
function loadingBox(text = "正在读取本机数据…") {
  return `<div class="state-message love-loading"><strong>${escapeHtml(text)}</strong><p>恋爱系统的数据只在本机读取，不联网。</p></div>`;
}
function errorBox(message) {
  return `<div class="state-message error-state"><strong>暂时无法读取</strong><p>${escapeHtml(message)}</p><button class="text-button" type="button" data-love-action="retry-page">重试</button></div>`;
}
function emptyBox(title, text, actions = "") {
  return `<div class="love-empty"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(text)}</p>${actions ? `<div class="love-empty-actions">${actions}</div>` : ""}</div>`;
}
function card(title, body, { eyebrow = "", subtitle = "", actions = "", className = "" } = {}) {
  return `<article class="settings-card love-card ${escapeHtml(className)}">
    <div class="settings-card-head">
      <div>${eyebrow ? `<span class="eyebrow">${escapeHtml(eyebrow)}</span>` : ""}
        ${title ? `<h2>${escapeHtml(title)}</h2>` : ""}
        ${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ""}</div>
      ${actions ? `<div class="love-card-actions">${actions}</div>` : ""}
    </div>
    ${body}
  </article>`;
}
function loveApi(path, options = {}) {
  return api(path, { timeoutMs: 120000, ...options });
}
function withBusy(button, label, fn) {
  if (!button || button.disabled) return Promise.resolve();
  const original = button.textContent;
  button.disabled = true;
  if (label) button.textContent = label;
  return Promise.resolve().then(fn).finally(() => {
    if (button.isConnected !== false) {
      button.disabled = false;
      button.textContent = original;
    }
  });
}
function stopChatPoll() {
  if (love.chatPoll) {
    window.clearTimeout(love.chatPoll);
    love.chatPoll = 0;
  }
}
function stopMasterPoll() {
  if (love.masterPoll) {
    window.clearTimeout(love.masterPoll);
    love.masterPoll = 0;
  }
}
function widthClass(value) {
  const number = Number(value);
  const step = Number.isFinite(number) ? Math.max(0, Math.min(100, Math.round(number / 5) * 5)) : 0;
  return `love-width-${step}`;
}

// ── 生命周期 ───────────────────────────────────────────────────────

export function initLovePane() {
  if (love.inited) return;
  love.inited = true;
  love.consent = readStorage(CONSENT_KEY) === "accepted";
  document.querySelectorAll("[data-love-page]").forEach((button) => {
    button.addEventListener("click", () => setLovePage(button.dataset.lovePage));
  });
  $id("love-refresh")?.addEventListener("click", () => reloadLove({ quiet: true }));
  $id("love-settings-open")?.addEventListener("click", () => openLoveSettings());
  $id("love-settings-close")?.addEventListener("click", () => closeLoveSettings());
  $id("love-settings-backdrop")?.addEventListener("click", () => closeLoveSettings());
  $id("love-consent-accept")?.addEventListener("click", () => {
    love.consent = true;
    writeStorage(CONSENT_KEY, "accepted");
    applyConsent();
    renderLovePage();
  });
  const content = $id("love-content");
  content?.addEventListener("click", (event) => {
    Promise.resolve(handleLoveAction(event)).catch((error) => {
      toast(`操作失败：${error.message}`, "error");
    });
  });
  // 设置抽屉在 #love-content 之外，里面的 data-love-action 也要走同一套分发。
  $id("love-settings-body")?.addEventListener("click", (event) => {
    Promise.resolve(handleLoveAction(event)).catch((error) => {
      toast(`操作失败：${error.message}`, "error");
    });
  });
  document.addEventListener("torch:notes-changed", (event) => {
    if (love.page !== "memories") return;
    if (event.detail?.pillar === "love" || String(event.detail?.path || "").includes("/01_恋爱系统/")) {
      renderLovePage();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !love.settingsOpen) return;
    closeLoveSettings();
  });
}

export async function loadLovePane() {
  if (!love.inited) initLovePane();
  if (love.loading) return;
  love.loading = true;
  applyConsent();
  try {
    love.status = await loveApi("/api/love/status", { timeoutMs: 20000 });
  } catch {
    love.status = null;
  }
  love.loading = false;
  if (!love.consent) {
    renderConsentPlaceholder();
    return;
  }
  await renderLovePage();
}

async function reloadLove({ quiet = false } = {}) {
  if (quiet) stopChatPoll();
  await renderLovePage();
}

function renderConsentPlaceholder() {
  const content = $id("love-content");
  if (content) {
    content.innerHTML = `<div class="state-message"><strong>恋爱系统</strong><p>请先阅读数据边界说明。</p></div>`;
  }
}

function applyConsent() {
  const overlay = $id("love-consent");
  if (overlay) overlay.hidden = love.consent;
  document.querySelectorAll("[data-love-page]").forEach((button) => {
    button.disabled = !love.consent;
  });
}

function setLovePage(page) {
  const next = PAGE_KEYS.includes(String(page)) ? String(page) : "memories";
  love.page = next;
  const overlay = $id("love-consent");
  if (!love.consent) {
    if (overlay) overlay.hidden = false;
    return;
  }
  renderLovePage();
}

function renderLoveNav() {
  document.querySelectorAll("[data-love-page]").forEach((button) => {
    const active = button.dataset.lovePage === love.page;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const title = $id("love-title");
  if (title) title.textContent = PAGE_TITLES[love.page] || "恋爱记忆";
  const status = $id("love-menu-status");
  if (status) {
    const paths = love.status?.paths?.root;
    status.textContent = paths ? `本机数据 · ${paths.split(/[\\/]/).slice(-2).join("/")}` : "本机数据 · 不联网";
    status.dataset.tone = "";
  }
}

async function renderLovePage() {
  if (!love.consent) {
    renderConsentPlaceholder();
    return;
  }
  renderLoveNav();
  const content = $id("love-content");
  if (!content) return;
  content.innerHTML = loadingBox();
  try {
    if (love.page === "cycle") {
      await renderLoveCycle(content, {
        card,
        isActive: () => love.page === "cycle",
        rerender: renderLovePage,
      });
      return;
    }
    const renderer = PAGE_RENDERERS[love.page] || renderMemories;
    await renderer(content);
  } catch (error) {
    if (love.page === "cycle") return;
    content.innerHTML = errorBox(error.message);
  }
}

// ── 恋爱记忆 ───────────────────────────────────────────────────────

function greetingForHour() {
  const hour = new Date().getHours();
  if (hour < 6) return "夜深了";
  if (hour < 11) return "早上好";
  if (hour < 14) return "中午好";
  if (hour < 18) return "下午好";
  return "晚上好";
}

function memoryDateLabel(note) {
  const day = String(note?.date || "").slice(0, 10);
  if (day) return day;
  return "";
}

function memoryHeatmap(dates = {}) {
  const today = new Date(`${todayText()}T12:00:00+08:00`);
  const weeks = 16;
  const days = weeks * 7;
  const start = new Date(today);
  start.setDate(start.getDate() - (days - 1) - ((start.getDay() + 6) % 7));
  const cells = [];
  let max = 1;
  const values = [];
  for (let index = 0; index < days + 6; index += 1) {
    const day = new Date(start);
    day.setDate(start.getDate() + index);
    const key = day.toISOString().slice(0, 10);
    const count = Number(dates[key] || 0);
    max = Math.max(max, count);
    values.push({ key, count, day });
  }
  for (const item of values) {
    const level = item.count === 0 ? 0 : Math.min(4, 1 + Math.floor((item.count / max) * 3));
    cells.push(`<span class="love-heat-cell level-${level}" title="${escapeHtml(`${item.key} · ${item.count} 篇`)}" aria-label="${escapeHtml(`${item.key}，${item.count} 篇记忆`)}"></span>`);
  }
  return `<div class="love-heatmap-wrap">
    <div class="love-heatmap" role="img" aria-label="恋爱记忆热力图">${cells.join("")}</div>
    <div class="love-heatmap-legend"><span>少</span><i class="level-0"></i><i class="level-1"></i><i class="level-2"></i><i class="level-3"></i><i class="level-4"></i><span>多</span></div>
  </div>`;
}

function memoryNotebookCard(notebook) {
  const deleteButton = notebook.custom
    ? `<button class="love-notebook-delete" type="button" data-love-action="memory-delete-notebook" data-key="${escapeHtml(notebook.key)}" data-title="${escapeHtml(notebook.title)}" aria-label="删除本子 ${escapeHtml(notebook.title)}">删除</button>`
    : "";
  const latest = notebook.latest
    ? `最近：${escapeHtml(notebook.latest.title || "")}`
    : "还没有记录，打开写下第一篇";
  return `<div class="love-notebook-card-wrap">
    <button type="button" class="notebook-card tone-${escapeHtml(notebook.tone || "slate")}" data-love-action="memory-notebook" data-key="${escapeHtml(notebook.key)}">
      <span class="notebook-card-top"><span class="notebook-icon" aria-hidden="true">${escapeHtml(notebook.icon || "本")}</span><span class="notebook-count">${Number(notebook.count) || 0} 篇</span></span>
      <span class="notebook-title">${escapeHtml(notebook.title)}</span>
      <span class="notebook-desc">${escapeHtml(notebook.description || "")}</span>
      <span class="notebook-latest">${latest}</span>
      <span class="notebook-open">打开本子 <i aria-hidden="true">→</i></span>
    </button>
    ${deleteButton}
  </div>`;
}

function memoryNoteCard(note) {
  const day = memoryDateLabel(note);
  const tags = Array.isArray(note.tags) && note.tags.length
    ? `<span class="love-memory-tags">${note.tags.slice(0, 3).map((tag) => `<i>${escapeHtml(tag)}</i>`).join("")}</span>`
    : "";
  return `<button type="button" class="love-memory-note" data-love-action="memory-open" data-path="${escapeHtml(note.path)}">
    <span class="love-memory-note-date">${escapeHtml(day ? day.slice(5) : "—")}<small>${escapeHtml(day ? day.slice(0, 4) : "")}</small></span>
    <span class="love-memory-note-body">
      <strong>${escapeHtml(note.title || "未命名记忆")}</strong>
      <p>${escapeHtml(note.excerpt || "还没有正文")}</p>
      ${tags}
    </span>
  </button>`;
}

async function renderMemories(content) {
  const data = await loveApi("/api/love/memory/overview", { timeoutMs: 30000 });
  love.memory = data;
  const notebooks = Array.isArray(data.notebooks) ? data.notebooks : [];
  let notes = Array.isArray(data.recent) ? data.recent : [];
  if (love.memoryNotebook) {
    const listed = await loveApi(`/api/love/memory/notes?limit=200&notebook=${encodeURIComponent(love.memoryNotebook)}`, { timeoutMs: 30000 });
    notes = Array.isArray(listed.items) ? listed.items : [];
  }
  love.memoryNotes = notes;
  const activeNotebook = notebooks.find((item) => item.key === love.memoryNotebook);
  const exportTarget = love.memoryNotebook || "";
  content.innerHTML = `
    <section class="love-memory-hero">
      <div class="love-memory-hero-main">
        <p class="eyebrow">${escapeHtml(greetingForHour())} · ${escapeHtml(weekday(todayText()))}</p>
        <h3>把今天记下来。</h3>
        <p class="love-memory-copy">本子是普通 Markdown 文件夹；写在应用里，Obsidian 里也能改。AI 批量补日记会先放进「AI 草稿」，你确认后再移入正式本子。</p>
        <div class="love-inline-actions">
          <button class="button primary" type="button" data-love-action="memory-new">✍ 写点什么</button>
          <a class="button secondary" href="/api/love/memory/export${exportTarget ? `?notebook=${encodeURIComponent(exportTarget)}` : ""}" download>导出 TXT</a>
          ${data.draft_count ? `<button class="button secondary" type="button" data-love-action="memory-promote-drafts">把 AI 草稿移入恋爱日志（${formatInt(data.draft_count)}）</button>` : ""}
        </div>
        <p class="love-memory-stats">共 ${formatInt(data.total)} 篇 · 恋爱日志 ${formatInt(data.journal_count)} · AI 草稿 ${formatInt(data.draft_count)} · 自定义本子 ${formatInt(data.custom_count)}</p>
      </div>
      <div class="love-memory-hero-side">${memoryHeatmap(data.dates || {})}</div>
    </section>
    <section class="notebook-section love-memory-notebooks">
      <div class="notebook-section-head">
        <div><p class="eyebrow">NOTEBOOKS</p><h3>我的本子</h3></div>
        <div class="notebook-section-actions"><button class="button secondary small-create" type="button" data-love-action="memory-create-notebook">＋ 新建本子</button></div>
      </div>
      <div class="notebook-grid">
        ${notebooks.map(memoryNotebookCard).join("")}
        <button class="notebook-card notebook-create-card" type="button" data-love-action="memory-create-notebook"><span class="notebook-create-plus">＋</span><span class="notebook-title">新建本子</span><span class="notebook-desc">在 01_恋爱记忆 下建一个文件夹</span></button>
      </div>
    </section>
    <section class="recent-section love-memory-notes">
      <div class="notebook-section-head">
        <div><p class="eyebrow">${activeNotebook ? "NOTEBOOK" : "RECENT"}</p><h3>${activeNotebook ? escapeHtml(activeNotebook.title) : "最近写过"}</h3></div>
        <div class="notebook-section-actions">${activeNotebook ? `<button class="text-button" type="button" data-love-action="memory-clear-filter">看全部</button>` : ""}<span class="meta">${formatInt(notes.length)} 篇</span></div>
      </div>
      ${notes.length ? `<div class="love-memory-note-list">${notes.map(memoryNoteCard).join("")}</div>` : emptyBox("还没有记忆", "点「写点什么」留下第一篇。", '<button class="button primary" type="button" data-love-action="memory-new">写点什么</button>')}
    </section>
    <div class="love-tip-strip"><span>🔒</span><p>恋爱记忆存放在 <code>02_生活运营/01_恋爱系统/01_恋爱记忆/</code>；删除会进入应用回收站，不会直接消失。</p></div>`;
}

async function openMemoryNote(path) {
  const note = await api(`/api/notes?path=${encodeURIComponent(path)}`, { timeoutMs: 30000 });
  openNoteEditor({ note, context: "love" });
}

function openNewMemory() {
  openNoteEditor({ context: "love", chapter: "love_journal" });
}

function openNotebookDialog() {
  openDialog("新建本子", `
    <form id="love-notebook-form" class="love-dialog-form">
      <div class="field"><label for="love-notebook-name">本子名称</label><input id="love-notebook-name" name="name" maxlength="48" required placeholder="例如：第一次旅行、日常碎片" /></div>
      <p class="field-help">会在 <code>01_恋爱记忆/</code> 下创建一个文件夹；之后可以在编辑器里把笔记移进去。</p>
      <div class="form-actions"><span id="love-notebook-feedback" class="status-message" role="status"></span><div class="love-inline-actions"><button class="button secondary" type="button" id="love-notebook-cancel">取消</button><button class="button primary" type="submit">创建</button></div></div>
    </form>`);
  $id("love-notebook-cancel")?.addEventListener("click", () => $id("detail-dialog")?.close());
  $id("love-notebook-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    const name = form.elements.name.value.trim();
    if (!name) return;
    await withBusy(submit, "创建中…", async () => {
      try {
        await loveApi("/api/notebooks", { method: "POST", body: JSON.stringify({ pillar: "love", name }), timeoutMs: 30000 });
        invalidateChapterCache();
        $id("detail-dialog")?.close();
        toast("本子已创建。", "success");
        await renderLovePage();
      } catch (error) {
        const feedback = $id("love-notebook-feedback");
        if (feedback) feedback.textContent = error.message;
        toast(`创建失败：${error.message}`, "error");
      }
    });
  });
}

async function deleteMemoryNotebook(button) {
  const key = button.dataset.key || "";
  const title = button.dataset.title || "这个本子";
  const confirmed = await confirmAction({
    title: `删除「${title}」？`,
    message: "本子会整体移入应用的本子回收站，里面的笔记不会直接消失；以后可以在回收中心或文件管理器找回。",
    confirmLabel: "删除本子", danger: true,
  });
  if (!confirmed) return;
  await withBusy(button, "删除中…", async () => {
    try {
      await loveApi("/api/notebooks/delete", {
        method: "POST",
        body: JSON.stringify({ pillar: "love", key, confirm: true }),
        timeoutMs: 60000,
      });
      invalidateChapterCache();
      if (love.memoryNotebook === key) love.memoryNotebook = "";
      toast("本子已移入回收站。", "success");
      await renderLovePage();
    } catch (error) {
      toast(`删除失败：${error.message}`, "error");
    }
  });
}

async function promoteAiDrafts(button) {
  const confirmed = await confirmAction({
    title: "把 AI 草稿移入恋爱日志？",
    message: "会把「AI 草稿」本子里的所有笔记移动到「恋爱日志」。移动后仍可再改本子或删除。",
    confirmLabel: "移入恋爱日志",
  });
  if (!confirmed) return;
  await withBusy(button, "移动中…", async () => {
    try {
      const result = await loveApi("/api/love/memory/drafts/promote", { method: "POST", timeoutMs: 60000 });
      toast(`已移动 ${formatInt(result.moved)} 篇${result.failed?.length ? `，${result.failed.length} 篇失败` : ""}。`, result.failed?.length ? "warning" : "success");
      await renderLovePage();
    } catch (error) {
      toast(`移动失败：${error.message}`, "error");
    }
  });
}

// ── 微信聊天 ───────────────────────────────────────────────────────

function chatMonthValue() {
  const saved = String(readStorage(CHAT_MONTH_KEY) || "");
  const stored = /^\d{4}-\d{2}$/.test(String(love.chatMonth || "")) ? love.chatMonth
    : (/^\d{4}-\d{2}$/.test(saved) ? saved : "");
  return stored || currentMonthText();
}

function chatStatusLabel(state) {
  return { no_chat: "无聊天", synced: "已同步", unsynced: "未同步", future: "" }[state] || state;
}

function chatCalendarMarkup(data) {
  const cells = [];
  for (let index = 0; index < monthLeadingBlanks(data.month); index += 1) {
    cells.push('<span class="love-chat-day is-empty" aria-hidden="true"></span>');
  }
  for (const day of data.days || []) {
    const classes = ["love-chat-day", `is-${day.state}`];
    if (day.is_today) classes.push("is-today");
    if (day.date === love.chatDay) classes.push("is-selected");
    const label = `${day.date} ${day.weekday}，${chatStatusLabel(day.state)}${day.count ? `，${day.count} 条` : ""}`;
    cells.push(`<button type="button" class="${classes.join(" ")}" data-love-action="chat-day" data-date="${day.date}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}">
      <span class="love-chat-day-num">${day.day}</span>
      ${day.count ? `<span class="love-chat-day-count">${formatInt(day.count)}</span>` : `<span class="love-chat-day-state">${escapeHtml(chatStatusLabel(day.state))}</span>`}
    </button>`);
  }
  return `<div class="love-chat-calendar-wrap">
    <div class="love-calendar-weekdays" aria-hidden="true">${["一", "二", "三", "四", "五", "六", "日"].map((name) => `<span>${name}</span>`).join("")}</div>
    <div class="love-chat-calendar">${cells.join("")}</div>
    <div class="love-calendar-legend love-chat-legend" aria-hidden="true">
      <span><i class="is-synced"></i>已同步</span>
      <span><i class="is-no_chat"></i>无聊天</span>
      <span><i class="is-unsynced"></i>未同步</span>
    </div>
  </div>`;
}

function chatMessageMarkup(message) {
  const content = escapeHtml(message.content || "");
  let media = "";
  if (message.has_media && message.media_url) {
    if (message.media_kind === "video") {
      media = `<video class="love-chat-media" controls preload="metadata" src="${escapeHtml(message.media_url)}"></video>`;
    } else if (message.media_kind === "voice") {
      media = `<audio class="love-chat-audio" controls preload="none" src="${escapeHtml(message.media_url)}"></audio>`;
    } else if (message.media_kind === "emoji") {
      media = `<img class="love-chat-emoji" src="${escapeHtml(message.media_url)}" alt="${escapeHtml(content || "收藏表情")}" loading="lazy" />`;
    } else {
      media = `<a class="love-chat-media-link" href="${escapeHtml(message.media_url)}" target="_blank" rel="noopener"><img class="love-chat-media" src="${escapeHtml(message.media_url)}" alt="${escapeHtml(content || "聊天媒体")}" loading="lazy" /></a>`;
    }
  }
  const placeholders = new Set(["[图片]", "[视频]", "[语音]", "[表情]", "[动画表情]"]);
  // 有媒体时才隐藏占位文字；媒体没导出成功时保留 [图片]/[表情]，避免空白气泡。
  const hidePlaceholder = message.has_media && placeholders.has(content);
  return `<article class="love-chat-msg ${message.is_send ? "is-me" : "is-other"}">
    <div class="love-chat-bubble">
      <div class="love-chat-meta"><span>${escapeHtml(message.is_send ? "我" : "对象")}</span><time>${escapeHtml(message.time || "")}</time></div>
      ${media}
      ${content && !hidePlaceholder ? `<p>${content}</p>` : ""}
    </div>
  </article>`;
}

async function loadChatSessions({ keyword = "", silent = false } = {}) {
  if (love.chatSessionsLoading) return;
  love.chatSessionsLoading = true;
  love.chatSessionsError = "";
  try {
    const data = await loveApi(`/api/love/chat/sessions?keyword=${encodeURIComponent(keyword)}`, { timeoutMs: 40000 });
    love.chatSessions = Array.isArray(data.items) ? data.items : [];
    love.chatKeyword = keyword;
  } catch (error) {
    love.chatSessions = [];
    love.chatSessionsError = error.message;
    if (!silent) toast(`读取联系人失败：${error.message}`, "error");
  } finally {
    love.chatSessionsLoading = false;
  }
}

function chatTokenForm(status) {
  return `<form id="love-chat-token-form" class="love-key-form">
    <div class="form-grid">
      <div class="field"><label for="love-chat-port">WeFlow API 端口</label><input id="love-chat-port" name="port" type="number" min="1" max="65535" value="${escapeHtml(String(status.port || 5031))}" required /></div>
      <div class="field"><label for="love-chat-token">${status.token_configured ? "替换 Access Token（可留空）" : "Access Token"}</label><input id="love-chat-token" name="token" type="password" autocomplete="off" maxlength="400" placeholder="${status.token_configured ? "已配置；留空只改端口" : "粘贴 WeFlow → 设置 → API 服务里的 Token"}" /></div>
    </div>
    <div class="love-inline-actions"><button class="button primary" type="submit">保存并测试连接</button>${status.token_configured ? '<button class="button secondary" type="button" data-love-action="chat-clear-token">清除 Token</button>' : ""}<button class="text-button" type="button" data-love-action="settings-open">微信组件修复</button></div>
    <span id="love-chat-token-feedback" class="status-message" role="status"></span>
    <p class="field-help">只访问 <code>127.0.0.1</code>，Token 加密保存在 <code>02_微信聊天/.credentials.json</code>，不会回显，也不写日志。微信组件过期时报错 101，可在设置抽屉里修复。</p>
  </form>`;
}

function chatBindForm(status) {
  const sessions = Array.isArray(love.chatSessions) ? love.chatSessions : [];
  const options = sessions.map((item) => `<option value="${escapeHtml(item.id)}" ${love.chatStatus?.contact?.session_id === item.id ? "selected" : ""}>${escapeHtml(item.name || item.id)}${item.message_count ? ` · ${formatInt(item.message_count)} 条` : ""}</option>`).join("");
  return `<div class="love-chat-bind">
    <form id="love-chat-search-form" class="love-key-form">
      <div class="field"><label for="love-chat-keyword">搜索联系人</label><input id="love-chat-keyword" name="keyword" type="search" maxlength="100" value="${escapeHtml(love.chatKeyword || "")}" placeholder="微信备注名或昵称；留空列出最近单聊" /></div>
      <div class="love-inline-actions"><button class="button secondary" type="submit">搜索</button><span class="field-help">${love.chatSessionsLoading ? "正在读取…" : `已读取 ${sessions.length} 个单聊`}</span></div>
    </form>
    ${love.chatSessionsError ? `<p class="inline-error">${escapeHtml(love.chatSessionsError)}</p>` : ""}
    ${sessions.length ? `<form id="love-chat-bind-form" class="love-key-form">
      <div class="form-grid">
        <div class="field"><label for="love-chat-session">要同步的单聊</label><select id="love-chat-session" name="session_id">${options}</select></div>
        <div class="field"><label for="love-chat-me">你在聊天里的名字</label><input id="love-chat-me" name="me_sender" maxlength="100" value="${escapeHtml(status.contact?.me_sender || "我")}" /></div>
      </div>
      <div class="love-inline-actions"><button class="button primary" type="submit">绑定这个联系人</button></div>
      <p class="field-help">只会读取这一个单聊。换绑会先备份当前 chat.db，再清空旧聊天；不会读取群聊或其他人。</p>
    </form>` : '<p class="field-help">还没有读取到联系人。确认 WeFlow 和微信都在运行，且 Token 已配置后，点「搜索」。</p>'}
  </div>`;
}

async function renderChat(content) {
  let status;
  try {
    status = await loveApi("/api/love/chat/status", { timeoutMs: 20000 });
  } catch (error) {
    status = { token_configured: false, port: 5031, contact: {}, error: error.message };
  }
  love.chatStatus = status;
  const month = chatMonthValue();
  love.chatMonth = month;
  writeStorage(CHAT_MONTH_KEY, month);
  const hasHistory = Number(status.total || 0) > 0;
  const canViewChat = Boolean(status.contact?.session_id) || hasHistory;
  const calendar = canViewChat
    ? await loveApi(`/api/love/chat/calendar?month=${encodeURIComponent(month)}`, { timeoutMs: 30000 }).catch(() => null)
    : null;
  love.chatCalendar = calendar;
  if (!love.chatDay) {
    const days = calendar?.days || [];
    love.chatDay = days.find((day) => day.is_today)?.date || days[0]?.date || todayText();
  }
  if (canViewChat && love.chatDay) {
    try {
      love.chatDayData = await loveApi(`/api/love/chat/day?date=${encodeURIComponent(love.chatDay)}`, { timeoutMs: 30000 });
    } catch {
      love.chatDayData = null;
    }
  } else {
    love.chatDayData = null;
  }
  const hasToken = Boolean(status.token_configured);
  if (hasToken && !status.contact?.session_id && love.chatSessions === null
      && !love.chatSessionsLoading) {
    await loadChatSessions({ silent: true });
  }
  const hasContact = Boolean(status.contact?.session_id) && !love.chatRebinding;
  let setup = "";
  if (!hasToken) {
    setup = card("连接微信", chatTokenForm(status), {
      eyebrow: "WEFLOW",
      subtitle: "先让 torch 连上本机 WeFlow；微信密码与数据库密钥都不经过 torch。",
    });
  } else if (!hasContact) {
    setup = card("选择你的对象", chatBindForm(status), {
      eyebrow: "ONE CONTACT",
      subtitle: "只绑定一个单聊；之后微信聊天页只看这个人的消息。",
    });
  }
  const statusCard = hasContact ? card("当前联系人", `
    <dl class="love-facts">
      <div><dt>对象</dt><dd>${escapeHtml(status.contact.display_name || "—")}</dd></div>
      <div><dt>会话</dt><dd><code>${escapeHtml(status.contact.session_id || "")}</code></dd></div>
      <div><dt>消息</dt><dd>${formatInt(status.total)} 条${status.first_day ? ` · ${escapeHtml(status.first_day)} 至 ${escapeHtml(status.last_day)}` : ""}</dd></div>
      <div><dt>上次同步</dt><dd>${status.contact.last_sync_at ? formatDateTime(status.contact.last_sync_at) : "还没有同步"}${status.contact.last_sync_status === "failed" ? " · 上次失败" : ""}</dd></div>
    </dl>
    ${status.contact.last_error ? `<p class="inline-error">${escapeHtml(status.contact.last_error)}</p>` : ""}
    <div class="love-inline-actions">
      <button class="button primary" type="button" data-love-action="chat-sync-apply">同步到本机</button>
      <button class="button secondary" type="button" data-love-action="chat-sync-preview">先预览</button>
      <label class="check-label"><input id="love-chat-full" type="checkbox" /> 从最早消息重新扫描</label>
      <button class="text-button" type="button" data-love-action="chat-rebind">换绑</button>
      <button class="text-button" type="button" data-love-action="chat-unbind">解除绑定</button>
    </div>
    <div id="love-chat-sync-status" class="love-sync-result">${love.chatLog ? chatSyncLogMarkup(love.chatLog) : ""}</div>
    <p class="field-help">同步只写入 <code>02_微信聊天/chat.db</code> 与 <code>media/</code>；不会写进四库。图片、视频、语音和收藏表情会复制进库；勾选「从最早消息重新扫描」可一次性补齐历史媒体，首次会比较久。</p>` , {
    eyebrow: "WECHAT",
    subtitle: "日历看板用三种状态标出哪天有聊天、哪天还没同步。",
  }) : "";
  const calendarCard = canViewChat && calendar ? card("聊天日历", `
    <div class="love-schedule-toolbar">
      <div class="love-month-nav"><button class="small-button" type="button" data-love-action="chat-month" data-value="-1" aria-label="上个月">‹</button><strong>${monthLabel(month)}</strong><button class="small-button" type="button" data-love-action="chat-month" data-value="1" aria-label="下个月">›</button><button class="text-button" type="button" data-love-action="chat-month-today">今天</button></div>
      <span class="meta">已同步 ${formatInt(calendar.total_days)} 天 · 未同步 ${formatInt(calendar.unsynced_days)} 天</span>
    </div>
    ${chatCalendarMarkup(calendar)}
    <p class="field-help">点日期查看当天聊天；「未同步」表示最后成功同步之后，或历史还没有覆盖到的日期。</p>`, {
    eyebrow: "CALENDAR",
    subtitle: "一眼看出哪天没同步。",
  }) : "";
  const dayCard = canViewChat && love.chatDay ? card(`${love.chatDay} 的聊天`, `
    <div class="love-inline-actions love-chat-day-actions">
      <span class="meta">${formatInt(love.chatDayData?.count || 0)} 条消息</span>
      <a class="text-button" href="/api/love/chat/export?date=${encodeURIComponent(love.chatDay)}&fmt=txt" download>导出当天 TXT</a>
      <a class="text-button" href="/api/love/chat/export?date=${encodeURIComponent(love.chatDay)}&fmt=json" download>JSON</a>
      <a class="text-button" href="/api/love/chat/export?date=all&fmt=txt" download>导出全部 TXT</a>
    </div>
    <div class="love-chat-messages">
      ${(love.chatDayData?.messages || []).length ? love.chatDayData.messages.map(chatMessageMarkup).join("") : emptyBox("这一天没有聊天", "可能是没聊，也可能还没同步。先看日历上的状态，再点「同步到本机」。")}
    </div>`, {
    eyebrow: "DAY VIEW",
    subtitle: "媒体会从本机 media 目录直接显示。",
  }) : "";
  const weflowWarning = hasToken && status.weflow_ok === false
    ? `<div class="inline-warning">WeFlow 连接失败：${escapeHtml(status.weflow_error || "请检查 WeFlow 是否运行、组件是否过期（101）。")}<button class="text-button" type="button" data-love-action="settings-open">打开微信组件修复</button></div>`
    : "";
  const privacy = card("数据边界", `
    <ul class="love-advice">
      <li>微信原文只进 <code>02_微信聊天/chat.db</code>；不会写进恋爱记忆 Markdown。</li>
      <li>WeFlow 组件过期会报 101；设置抽屉提供开源组件替换、备份与回滚。</li>
      <li>同步失败不会删除已有聊天；换绑会先备份再清空。</li>
    </ul>`, { eyebrow: "PRIVACY", subtitle: "只读你选中的一个单聊。" });
  content.innerHTML = weflowWarning + setup + statusCard + calendarCard + dayCard + privacy;
  bindChatForms();
  if (love.chatLog && ["queued", "running"].includes(love.chatLog.status)) {
    pollChatSync(love.chatLog.id);
  }
}

function formatDuration(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  if (value < 60) return `${value} 秒`;
  const minutes = Math.floor(value / 60);
  const rest = value % 60;
  if (minutes < 60) return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
  const hours = Math.floor(minutes / 60);
  const remaining = minutes % 60;
  return remaining ? `${hours} 小时 ${remaining} 分` : `${hours} 小时`;
}

/** 同步进行中的进度条：有 total 时显示确定进度与预计剩余，没有总量时显示不确定进度 + 速度/已用时间。 */
function syncProgressMarkup(log) {
  const status = String(log.status || "");
  if (!["queued", "running", "completed"].includes(status)) return "";
  const fetched = Number(log.fetched) || 0;
  const total = Number(log.total) || 0;
  if (status === "queued") {
    return '<progress class="love-sync-progress" aria-label="同步排队中"></progress><span class="love-sync-progress-meta">排队中…</span>';
  }
  if (status === "completed") {
    return `<progress class="love-sync-progress" max="100" value="100" aria-label="同步完成"></progress><span class="love-sync-progress-meta">完成 · 共 ${formatInt(fetched)} 条</span>`;
  }
  const percent = log.progress_percent == null ? null : Number(log.progress_percent);
  let rate = log.rate_per_second == null ? null : Number(log.rate_per_second);
  const eta = log.eta_seconds == null ? null : Number(log.eta_seconds);
  const started = Date.parse(log.started_at || "");
  const elapsed = Number.isFinite(started) ? Math.max(0, (Date.now() - started) / 1000) : null;
  if ((rate == null || !(rate > 0)) && elapsed && fetched > 0) rate = fetched / elapsed;
  const parts = [total > 0
    ? `已处理 ${formatInt(fetched)} / ${formatInt(total)} 条`
    : `已处理 ${formatInt(fetched)} 条`];
  if (rate != null && rate > 0) parts.push(`约 ${formatInt(Math.round(rate))} 条/秒`);
  if (eta != null && eta >= 0) parts.push(`约剩 ${formatDuration(eta)}`);
  else if (elapsed != null) parts.push(`已用 ${formatDuration(elapsed)}`);
  const value = total > 0 && percent != null
    ? ` value="${Math.max(0, Math.min(99, percent))}"` : "";
  const label = total > 0 ? "同步进度" : "同步进度（总量未知）";
  return `<progress class="love-sync-progress" max="100"${value} aria-label="${label}"></progress><span class="love-sync-progress-meta">${parts.join(" · ")}</span>`;
}

function chatSyncLogMarkup(log) {
  const labels = { queued: "排队中", running: "同步中", completed: "已完成", failed: "失败" };
  return `<div class="love-sync-line"><span class="love-badge ${log.status === "completed" ? "ok" : log.status === "failed" ? "error" : "warn"}">${escapeHtml(labels[log.status] || log.status)}</span><span>抓取 ${formatInt(log.fetched)} · 新增 ${formatInt(log.inserted)} · 重复 ${formatInt(log.duplicates)}${log.matched ? ` · 匹配 ${formatInt(log.matched)}` : ""}${log.media_saved ? ` · 媒体 ${formatInt(log.media_saved)}` : ""}${log.media_failed ? ` · 媒体失败 ${formatInt(log.media_failed)}` : ""}</span></div>${syncProgressMarkup(log)}${log.error ? `<p class="inline-error">${escapeHtml(log.error)}</p>` : ""}${(log.warnings || []).length ? `<details class="love-preview-lines"><summary>媒体警告 ${log.warnings.length} 条</summary><pre>${log.warnings.map(escapeHtml).join("\n")}</pre></details>` : ""}`;
}

function bindChatForms() {
  $id("love-chat-token-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    const port = Number(form.elements.port.value) || 5031;
    const token = form.elements.token.value.trim();
    await withBusy(submit, "测试连接…", async () => {
      try {
        await loveApi("/api/love/chat/settings", { method: "PUT", body: JSON.stringify({ port, token }), timeoutMs: 40000 });
        form.elements.token.value = "";
        toast("WeFlow 连接成功，Token 已加密保存。", "success");
        love.chatSessions = null;
        await renderLovePage();
      } catch (error) {
        const feedback = $id("love-chat-token-feedback");
        if (feedback) feedback.textContent = error.message;
        toast(`连接失败：${error.message}`, "error");
      }
    });
  });
  $id("love-chat-search-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    await withBusy(button, "读取中…", async () => {
      await loadChatSessions({ keyword: form.elements.keyword.value.trim() });
      await renderLovePage();
    });
  });
  $id("love-chat-bind-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    const sessionId = form.elements.session_id.value;
    const meSender = form.elements.me_sender.value.trim() || "我";
    if (!sessionId) return;
    await withBusy(submit, "绑定中…", async () => {
      try {
        const replace = Boolean(love.chatStatus?.contact?.session_id);
        const result = await loveApi("/api/love/chat/bind", {
          method: "POST",
          body: JSON.stringify({ session_id: sessionId, me_sender: meSender, replace }),
          timeoutMs: 60000,
        });
        love.chatDay = "";
        love.chatLog = null;
        love.chatRebinding = false;
        toast(result.backup ? "已换绑；旧聊天已备份。" : "已绑定这个联系人。", "success");
        await renderLovePage();
      } catch (error) {
        toast(`绑定失败：${error.message}`, "error");
      }
    });
  });
}

async function startChatSync(mode, button) {
  const full = Boolean($id("love-chat-full")?.checked);
  if (mode === "apply" && !love.chatStatus?.contact?.session_id) return;
  await withBusy(button, mode === "preview" ? "正在预览…" : "正在同步…", async () => {
    try {
      const log = await loveApi("/api/love/chat/sync", {
        method: "POST",
        body: JSON.stringify({ mode, full }),
        timeoutMs: 60000,
      });
      love.chatLog = { ...log, fetched: 0, inserted: 0, duplicates: 0, matched: 0, warnings: [] };
      const target = $id("love-chat-sync-status");
      if (target) target.innerHTML = chatSyncLogMarkup(love.chatLog);
      pollChatSync(log.log_id);
    } catch (error) {
      toast(`同步启动失败：${error.message}`, "error");
    }
  });
}

function pollChatSync(logId) {
  stopChatPoll();
  const tick = async () => {
    if (love.page !== "chat") {
      love.chatPoll = 0;
      return;
    }
    try {
      const log = await loveApi(`/api/love/chat/sync/${logId}`, { timeoutMs: 20000 });
      love.chatLog = log;
      const target = $id("love-chat-sync-status");
      if (target) target.innerHTML = chatSyncLogMarkup(log);
      if (["completed", "failed"].includes(log.status)) {
        love.chatPoll = 0;
        toast(log.status === "completed" ? "微信聊天同步完成。" : "微信聊天同步失败，请查看原因。", log.status === "completed" ? "success" : "error");
        if (log.status === "completed") await renderLovePage();
        return;
      }
    } catch {
      // 网络抖动继续轮询
    }
    love.chatPoll = window.setTimeout(tick, 1800);
  };
  love.chatPoll = window.setTimeout(tick, 1000);
}

// ── 排班日历 ───────────────────────────────────────────────────────

function scheduleMonthValue() {
  const stored = /^\d{4}-\d{2}$/.test(String(readStorage(MONTH_KEY) || "")) ? readStorage(MONTH_KEY) : "";
  return /^\d{4}-\d{2}$/.test(String(love.scheduleMonth || "")) ? love.scheduleMonth : (stored || currentMonthText());
}

function scheduleDayLabel(day) {
  if (day.override) return "手动";
  if (!day.in_range) return "不在表内";
  return day.rest ? "休息" : "上班";
}

async function renderSchedule(content) {
  const month = scheduleMonthValue();
  love.scheduleMonth = month;
  writeStorage(MONTH_KEY, month);
  const [status, data] = await Promise.all([
    loveApi("/api/love/schedule/status", { timeoutMs: 20000 }),
    loveApi(`/api/love/schedule/month?month=${encodeURIComponent(month)}`, { timeoutMs: 30000 }),
  ]);
  love.scheduleData = data;
  if (!data.found) {
    content.innerHTML = card("排班日历", `
      <div class="love-schedule-toolbar">
        <div class="love-month-nav"><button class="small-button" type="button" data-love-action="schedule-month" data-value="-1">‹</button><strong>${monthLabel(month)}</strong><button class="small-button" type="button" data-love-action="schedule-month" data-value="1">›</button><button class="text-button" type="button" data-love-action="schedule-today">今天</button></div>
        <input id="love-schedule-file" type="file" accept=".xlsx,.xlsm" hidden /><button class="small-button" type="button" data-love-action="schedule-upload">导入排班表</button>
      </div>
      <div class="state-message error-state"><strong>${escapeHtml(data.error || "还没有排班表")}</strong><p>把 .xlsx 排班表放进 <code>03_排班日历/xlsx/</code>，或点「导入排班表」。也可以直接点日期手动覆盖。</p></div>
    `, { eyebrow: "SCHEDULE", subtitle: "手动覆盖与 Excel 底表分开保存。" });
    bindScheduleFile();
    return;
  }
  const cells = [];
  for (let index = 0; index < monthLeadingBlanks(month); index += 1) cells.push('<span class="love-day-cell is-empty" aria-hidden="true"></span>');
  for (const day of data.days || []) {
    const duty = String(day.duty || "").replace(/\s+/g, " ").trim();
    const note = String(day.note || "").replace(/\s+/g, " ").trim();
    const parts = [duty, note].filter(Boolean);
    const classes = ["love-day-cell"];
    if (day.in_range) classes.push(day.rest ? "is-rest" : "is-work");
    else classes.push("is-out");
    if (day.override) classes.push("is-override");
    if (day.date === data.today) classes.push("is-today");
    const label = `${day.date} ${scheduleDayLabel(day)}${duty ? `，值乘：${duty}` : ""}${note ? `，备注：${note}` : ""}`;
    cells.push(`<button type="button" class="${classes.join(" ")}" data-love-action="schedule-day" data-date="${day.date}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}">
      <span class="love-day-num">${Number(day.date.slice(8))}</span>
      <span class="love-day-state">${scheduleDayLabel(day)}</span>
      ${parts.length ? `<span class="love-day-note">${escapeHtml(parts.join(" · "))}</span>` : ""}
    </button>`);
  }
  content.innerHTML = card("排班日历", `
    <div class="love-schedule-toolbar">
      <div class="love-month-nav"><button class="small-button" type="button" data-love-action="schedule-month" data-value="-1">‹</button><strong>${monthLabel(month)}</strong><button class="small-button" type="button" data-love-action="schedule-month" data-value="1">›</button><button class="text-button" type="button" data-love-action="schedule-today">今天</button></div>
      <input id="love-schedule-file" type="file" accept=".xlsx,.xlsm" hidden /><button class="small-button" type="button" data-love-action="schedule-upload">导入排班表</button>
    </div>
    <div class="love-calendar-wrap">
      <div class="love-calendar-weekdays" aria-hidden="true">${["一", "二", "三", "四", "五", "六", "日"].map((name) => `<span>${name}</span>`).join("")}</div>
      <div class="love-calendar-grid">${cells.join("")}</div>
    </div>
    <div class="love-calendar-legend" aria-hidden="true"><span><i class="is-work"></i>上班</span><span><i class="is-rest"></i>休息</span><span><i class="is-override"></i>手动覆盖</span><span><i class="is-out"></i>不在表内</span></div>
    <p class="field-help">点任意一天：可覆盖上班/休息、值乘车次与备注；Excel 仍是底表，覆盖可随时撤销。</p>
  `, { eyebrow: "SCHEDULE", subtitle: `${data.manual_only ? "当前只有手动排班" : `Excel 覆盖 ${escapeHtml(data.range?.start || "—")} ~ ${escapeHtml(data.range?.end || "—")}`}。` });
  bindScheduleFile();
}

function bindScheduleFile() {
  const input = $id("love-schedule-file");
  if (!input || input.dataset.bound) return;
  input.dataset.bound = "1";
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      toast("正在导入排班表…", "info");
      const result = await loveApi("/api/love/schedule/excel", { method: "POST", body: form, timeoutMs: 120000 });
      toast(result.message || "排班表已更新。", "success");
      await renderLovePage();
    } catch (error) {
      toast(`导入失败：${error.message}`, "error");
    }
  });
}

function openScheduleDay(day) {
  const baseDuty = String(day.duty || "").trim();
  const state = day.override ? "已手动覆盖" : (day.in_range ? (day.rest ? "休息" : "上班") : "不在排班表范围内");
  openDialog(`${day.date} 排班`, `
    <form id="love-schedule-day-form" class="love-dialog-form">
      <p class="field-help">${escapeHtml(state)}。Excel 原值：${escapeHtml(day.excel_work ? "上班" : day.excel_rest ? "休息" : "—")}${day.excel_duty ? ` · ${escapeHtml(day.excel_duty)}` : ""}。</p>
      <div class="form-grid">
        <div class="field"><label for="love-schedule-status">状态</label><select id="love-schedule-status" name="status"><option value="follow" ${!day.override ? "selected" : ""}>跟随排班表</option><option value="work" ${day.override && day.work ? "selected" : ""}>上班</option><option value="rest" ${day.override && day.rest ? "selected" : ""}>休息</option></select></div>
        <div class="field"><label for="love-schedule-duty">值乘车次</label><input id="love-schedule-duty" name="duty" maxlength="500" value="${escapeHtml(day.override && day.duty !== undefined ? day.duty : baseDuty)}" placeholder="例如 G1234；留空表示没有车次" /></div>
      </div>
      <div class="field"><label for="love-schedule-note">备注</label><textarea id="love-schedule-note" name="note" rows="4" maxlength="500" placeholder="交接事项、临时调整…">${escapeHtml(day.note || "")}</textarea></div>
      <div class="form-actions"><span id="love-schedule-feedback" class="status-message" role="status"></span><div class="love-inline-actions"><button class="button secondary" type="button" id="love-schedule-cancel">取消</button>${day.override ? '<button class="text-button" type="button" id="love-schedule-clear">恢复跟随排班表</button>' : ""}<button class="button primary" type="submit">保存</button></div></div>
    </form>`);
  $id("love-schedule-cancel")?.addEventListener("click", () => $id("detail-dialog")?.close());
  $id("love-schedule-clear")?.addEventListener("click", async () => {
    try {
      await loveApi(`/api/love/schedule/override?date=${encodeURIComponent(day.date)}`, { method: "DELETE", timeoutMs: 20000 });
      $id("detail-dialog")?.close();
      toast("已恢复跟随 Excel。", "success");
      await renderLovePage();
    } catch (error) {
      toast(`操作失败：${error.message}`, "error");
    }
  });
  $id("love-schedule-day-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    const status = form.elements.status.value;
    const duty = form.elements.duty.value.trim();
    const note = form.elements.note.value;
    await withBusy(submit, "保存中…", async () => {
      try {
        await loveApi("/api/love/schedule/override", {
          method: "PUT",
          body: JSON.stringify({ date: day.date, status, duty: duty || null }),
          timeoutMs: 20000,
        });
        await loveApi("/api/love/schedule/note", {
          method: "PUT",
          body: JSON.stringify({ date: day.date, note }),
          timeoutMs: 20000,
        });
        $id("detail-dialog")?.close();
        toast("排班已更新。", "success");
        await renderLovePage();
      } catch (error) {
        const feedback = $id("love-schedule-feedback");
        if (feedback) feedback.textContent = error.message;
        toast(`保存失败：${error.message}`, "error");
      }
    });
  });
}

// ── 恋爱相册 ───────────────────────────────────────────────────────

function galleryAlbumName(id) {
  const albums = love.gallery?.albums || [];
  return albums.find((album) => Number(album.id) === Number(id))?.name || "未分类";
}

function galleryMediaCard(item) {
  const selected = love.gallerySelection.has(item.id);
  const thumb = item.has_thumb
    ? `<img src="${escapeHtml(item.thumb_url)}" alt="${escapeHtml(item.original_name || "照片")}" loading="lazy" />`
    : item.kind === "video"
      ? `<span class="love-gallery-play" aria-hidden="true">▶</span>`
      : `<span class="love-gallery-no-thumb" aria-hidden="true">▧</span>`;
  return `<div class="love-gallery-item${selected ? " is-selected" : ""}" data-media-id="${item.id}">
    <button type="button" class="love-gallery-open" data-love-action="gallery-open" data-id="${item.id}">${thumb}</button>
    <label class="love-gallery-check"><input type="checkbox" data-gallery-select="${item.id}" ${selected ? "checked" : ""} aria-label="选择 ${escapeHtml(item.original_name || "媒体")}" /></label>
    <span class="love-gallery-caption">${escapeHtml(item.original_name || (item.kind === "video" ? "视频" : "照片"))}</span>
  </div>`;
}

function galleryAlbumCard(album) {
  const cover = album.cover
    ? `<img src="${escapeHtml(album.cover)}" alt="" loading="lazy" />`
    : `<span class="love-gallery-album-empty" aria-hidden="true">▧</span>`;
  return `<div class="love-gallery-album-wrap">
    <button type="button" class="love-gallery-album" data-love-action="gallery-album" data-id="${album.id}">
      <span class="love-gallery-album-cover">${cover}</span>
      <strong>${escapeHtml(album.name)}</strong>
      <small>${formatInt(album.photos)} 张照片 · ${formatInt(album.videos)} 个视频</small>
    </button>
    ${album.protected ? "" : `<div class="love-gallery-album-actions"><button class="text-button" type="button" data-love-action="gallery-rename-album" data-id="${album.id}" data-name="${escapeHtml(album.name)}">改名</button><button class="text-button reject" type="button" data-love-action="gallery-delete-album" data-id="${album.id}" data-name="${escapeHtml(album.name)}">删除</button></div>`}
  </div>`;
}

async function renderGallery(content) {
  const overview = await loveApi("/api/love/gallery/overview", { timeoutMs: 30000 });
  love.gallery = overview;
  const albums = overview.albums || [];
  if (love.galleryAlbum && !albums.some((album) => Number(album.id) === Number(love.galleryAlbum))) love.galleryAlbum = 0;
  let items = [];
  if (love.galleryView !== "albums") {
    const params = new URLSearchParams();
    if (love.galleryAlbum) params.set("album_id", String(love.galleryAlbum));
    if (love.galleryKind) params.set("kind", love.galleryKind);
    params.set("limit", "300");
    const data = await loveApi(`/api/love/gallery/media?${params.toString()}`, { timeoutMs: 30000 });
    items = Array.isArray(data.items) ? data.items : [];
    love.galleryItems = items;
  }
  const albumOptions = albums.map((album) => `<option value="${album.id}" ${Number(love.galleryAlbum) === Number(album.id) ? "selected" : ""}>${escapeHtml(album.name)}</option>`).join("");
  const viewTabs = [["all", "全部"], ["albums", "相簿"], ["photos", "照片"], ["videos", "视频"]].map(([key, label]) =>
    `<button type="button" class="love-seg${love.galleryView === key ? " active" : ""}" data-love-action="gallery-view" data-view="${key}">${label}</button>`).join("");
  const selection = love.gallerySelection.size;
  content.innerHTML = card("恋爱相册", `
    <div class="love-gallery-toolbar">
      <div class="love-segmented" role="group" aria-label="相册视图">${viewTabs}</div>
      <div class="love-inline-actions">
        <select id="love-gallery-upload-album" class="love-gallery-select" aria-label="上传到相簿">${albumOptions}</select>
        <input id="love-gallery-files" type="file" accept="image/*,video/*" multiple hidden />
        <button class="button primary" type="button" data-love-action="gallery-upload">上传照片/视频</button>
        <button class="button secondary" type="button" data-love-action="gallery-create-album">＋ 新建相簿</button>
      </div>
    </div>
    ${selection ? `<div class="love-gallery-bulk"><span>已选 ${formatInt(selection)} 项</span><select id="love-gallery-move-target" class="love-gallery-select">${albumOptions}</select><button class="small-button" type="button" data-love-action="gallery-bulk-move">移动</button><button class="small-button reject" type="button" data-love-action="gallery-bulk-delete">删除</button><button class="text-button" type="button" data-love-action="gallery-clear-selection">取消选择</button></div>` : ""}
    ${love.galleryView === "albums" ? `<div class="love-gallery-albums">${albums.map(galleryAlbumCard).join("")}</div>` : `
      <div class="love-gallery-meta"><span class="meta">${love.galleryAlbum ? `相簿：${escapeHtml(galleryAlbumName(love.galleryAlbum))}` : "全部媒体"} · ${formatInt(items.length)} 项</span>${love.galleryAlbum ? '<button class="text-button" type="button" data-love-action="gallery-all">看全部</button>' : ""}</div>
      ${items.length ? `<div class="love-gallery-grid">${items.map(galleryMediaCard).join("")}</div>` : emptyBox("还没有照片或视频", "点右上角「上传照片/视频」；文件会复制进 05_恋爱相册，不引用外部路径。")}
    `}
    <p class="field-help">相册文件在 <code>02_生活运营/01_恋爱系统/05_恋爱相册/</code>；删除会同时删除文件和缩略图。</p>
  `, { eyebrow: "ALBUM", subtitle: "照片和视频都复制进库；一个文件属于一个相簿。", actions: `<span class="meta">${formatInt(overview.total)} 个媒体</span>` });
  $id("love-gallery-files")?.addEventListener("change", () => {
    const files = $id("love-gallery-files")?.files;
    if (files?.length) uploadGalleryFiles(files);
  });
  document.querySelectorAll("[data-gallery-select]").forEach((input) => {
    input.addEventListener("change", () => {
      const id = Number(input.dataset.gallerySelect);
      if (!Number.isFinite(id)) return;
      if (input.checked) love.gallerySelection.add(id);
      else love.gallerySelection.delete(id);
      renderLovePage();
    });
  });
}

async function captureVideoThumb(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    video.preload = "metadata";
    video.muted = true;
    video.src = url;
    let settled = false;
    const finish = (blob) => {
      if (settled) return;
      settled = true;
      URL.revokeObjectURL(url);
      resolve(blob);
    };
    video.addEventListener("loadedmetadata", () => {
      try { video.currentTime = Math.min(0.2, video.duration || 0.2); } catch { finish(null); }
    });
    video.addEventListener("seeked", () => {
      try {
        const canvas = document.createElement("canvas");
        const width = Math.min(480, video.videoWidth || 480);
        const height = Math.max(1, Math.round((video.videoHeight || 270) * (width / (video.videoWidth || 480))));
        canvas.width = width;
        canvas.height = height;
        canvas.getContext("2d")?.drawImage(video, 0, 0, width, height);
        canvas.toBlob((blob) => finish(blob), "image/jpeg", 0.82);
      } catch { finish(null); }
    });
    video.addEventListener("error", () => finish(null));
    window.setTimeout(() => finish(null), 6000);
  });
}

async function uploadGalleryFiles(files) {
  const albumId = Number($id("love-gallery-upload-album")?.value) || 1;
  let saved = 0;
  let failed = 0;
  toast(`正在上传 ${files.length} 个文件…`, "info");
  for (const file of files) {
    const form = new FormData();
    form.append("files", file);
    form.append("album_id", String(albumId));
    if (file.type.startsWith("video/")) {
      const thumb = await captureVideoThumb(file);
      if (thumb) form.append("thumbs", thumb, "thumb.jpg");
    }
    try {
      const result = await loveApi("/api/love/gallery/upload", { method: "POST", body: form, timeoutMs: 300000 });
      saved += Array.isArray(result.saved) ? result.saved.length : 0;
      failed += Array.isArray(result.skipped) ? result.skipped.length : 0;
    } catch {
      failed += 1;
    }
  }
  toast(`上传完成：成功 ${saved}，跳过/失败 ${failed}。`, failed ? "warning" : "success");
  const input = $id("love-gallery-files");
  if (input) input.value = "";
  love.gallerySelection.clear();
  await renderLovePage();
}

function openGalleryViewer(item) {
  const media = item.kind === "video"
    ? `<video class="love-gallery-viewer-media" controls autoplay preload="metadata" src="${escapeHtml(item.file_url)}"></video>`
    : `<img class="love-gallery-viewer-media" src="${escapeHtml(item.file_url)}" alt="${escapeHtml(item.original_name || "照片")}" />`;
  openDialog(item.original_name || (item.kind === "video" ? "视频" : "照片"), `
    <div class="love-gallery-viewer">${media}</div>
    <dl class="love-facts"><div><dt>相簿</dt><dd>${escapeHtml(galleryAlbumName(item.album_id))}</dd></div><div><dt>大小</dt><dd>${formatInt(Math.round((item.size || 0) / 1024))} KB</dd></div><div><dt>加入时间</dt><dd>${escapeHtml(formatDateTime(item.created_at))}</dd></div></dl>
    <div class="love-inline-actions"><a class="button secondary" href="${escapeHtml(item.file_url)}" download>下载</a><button class="button secondary" type="button" id="love-gallery-viewer-move">移动</button><button class="button secondary reject" type="button" id="love-gallery-viewer-delete">删除</button><button class="button primary" type="button" id="love-gallery-viewer-close">关闭</button></div>
  `);
  $id("love-gallery-viewer-close")?.addEventListener("click", () => $id("detail-dialog")?.close());
  $id("love-gallery-viewer-delete")?.addEventListener("click", async () => {
    if (!await confirmAction({ title: "删除这个媒体？", message: "文件和缩略图会从相册目录删除，无法在应用内恢复。", confirmLabel: "删除", danger: true })) return;
    try {
      await loveApi(`/api/love/gallery/media/${item.id}`, { method: "DELETE", timeoutMs: 30000 });
      $id("detail-dialog")?.close();
      love.gallerySelection.delete(item.id);
      toast("已删除。", "success");
      await renderLovePage();
    } catch (error) { toast(`删除失败：${error.message}`, "error"); }
  });
  $id("love-gallery-viewer-move")?.addEventListener("click", () => openGalleryMoveDialog([item.id]));
}

function openGalleryMoveDialog(ids) {
  const albums = love.gallery?.albums || [];
  openDialog("移动到相簿", `
    <form id="love-gallery-move-form" class="love-dialog-form">
      <div class="field"><label for="love-gallery-move-album">目标相簿</label><select id="love-gallery-move-album" name="album_id">${albums.map((album) => `<option value="${album.id}">${escapeHtml(album.name)}</option>`).join("")}</select></div>
      <div class="form-actions"><div class="love-inline-actions"><button class="button secondary" type="button" id="love-gallery-move-cancel">取消</button><button class="button primary" type="submit">移动</button></div></div>
    </form>`);
  $id("love-gallery-move-cancel")?.addEventListener("click", () => $id("detail-dialog")?.close());
  $id("love-gallery-move-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const albumId = Number($id("love-gallery-move-album")?.value) || 1;
    try {
      for (const id of ids) {
        await loveApi(`/api/love/gallery/media/${id}`, { method: "PATCH", body: JSON.stringify({ album_id: albumId }), timeoutMs: 30000 });
      }
      $id("detail-dialog")?.close();
      love.gallerySelection.clear();
      toast("已移动。", "success");
      await renderLovePage();
    } catch (error) { toast(`移动失败：${error.message}`, "error"); }
  });
}

// ── 八字合婚 ───────────────────────────────────────────────────────

const WX_CLASS = { 木: "mu", 火: "huo", 土: "tu", 金: "jin", 水: "shui" };
function wxClass(value) { return `wx-${WX_CLASS[value] || "none"}`; }
function wxChips(values) {
  return (Array.isArray(values) ? values : []).map((value) => `<span class="love-bazi-chip ${wxClass(value)}">${escapeHtml(value)}</span>`).join("");
}
function baziPillarMarkup(pillar) {
  return `<div class="love-bazi-pillar"><span class="love-bazi-slot">${escapeHtml(pillar.slot || "")}</span><b class="love-bazi-gan ${wxClass(pillar.gan_wx)}">${escapeHtml(pillar.gan || "")}</b><b class="love-bazi-zhi ${wxClass(pillar.zhi_wx)}">${escapeHtml(pillar.zhi || "")}</b><small>${escapeHtml(pillar.gan_ss || "")} · ${escapeHtml(pillar.zhi_ss || "")}</small><em>藏 ${escapeHtml(pillar.hidden || "—")}</em></div>`;
}
function baziChartMarkup(person, role) {
  const pillars = (Array.isArray(person.pillars) ? person.pillars : []).map(baziPillarMarkup).join("");
  return `<article class="love-bazi-chart is-${role}">
    <header class="love-bazi-chart-head"><div><span class="eyebrow">${role === "him" ? "HIM · 男命" : "HER · 女命"}</span><h3>${escapeHtml(person.name || "")}</h3><p>${escapeHtml(person.birth || "")}</p><p class="love-bazi-place">${escapeHtml(person.place || "")}</p></div><span class="love-bazi-master">${escapeHtml(person.day_master || "")}</span></header>
    <div class="love-bazi-pillars">${pillars}</div>
    <dl class="love-bazi-meta"><div><dt>格局</dt><dd>${escapeHtml(person.pattern || "")}</dd></div><div><dt>喜</dt><dd>${wxChips(person.favor)}</dd></div><div><dt>忌</dt><dd>${wxChips(person.avoid)}</dd></div><div><dt>大运</dt><dd>${escapeHtml(person.dayun || "")}</dd></div></dl>
    ${person.time_note ? `<p class="love-bazi-note">${escapeHtml(person.time_note)}</p>` : ""}
  </article>`;
}
function baziHeroMarkup(data) {
  const hehun = data.hehun || {};
  return `<section class="love-bazi-hero"><div class="love-bazi-seal" aria-hidden="true">合</div><div class="love-bazi-hero-text"><span class="eyebrow">BAZI · 合婚参考</span><h2>${escapeHtml(hehun.kicker || "合婚")}</h2><p>${escapeHtml(hehun.summary || "")}</p><div class="love-bazi-hero-tags"><span class="love-bazi-verdict">${escapeHtml(hehun.verdict || "")}</span>${hehun.score ? `<span class="love-badge ok">${escapeHtml(hehun.score)}</span>` : ""}<span class="love-badge">本机生成</span></div></div></section>`;
}
function baziPointsMarkup(points) {
  return `<div class="love-bazi-points">${(Array.isArray(points) ? points : []).map((item) => `<article><span class="love-bazi-glyph" aria-hidden="true">${escapeHtml(item.glyph || "☯")}</span><h3>${escapeHtml(item.title || "")}</h3><p>${escapeHtml(item.body || "")}</p></article>`).join("")}</div>`;
}
function baziYearsMarkup(years) {
  return `<ol class="love-bazi-years">${(Array.isArray(years) ? years : []).map((item) => `<li data-tone="${escapeHtml(item.tone || "flat")}"><span class="love-bazi-year">${escapeHtml(item.year || "")}</span><b>${escapeHtml(item.gz || "")}</b><p>${escapeHtml(item.text || "")}</p></li>`).join("")}</ol>`;
}
function baziAdviceMarkup(advice) {
  return `<div class="love-bazi-advice">${(Array.isArray(advice) ? advice : []).map((item, index) => `<article><span class="love-bazi-index" aria-hidden="true">${String(index + 1).padStart(2, "0")}</span><h3>${escapeHtml(item.title || "")}</h3><p>${escapeHtml(item.body || "")}</p></article>`).join("")}</div>`;
}
function baziHerMarkup(her) {
  const sections = (Array.isArray(her.sections) ? her.sections : []).map((section) => `<article><h3>${escapeHtml(section.title || "")}</h3><p>${escapeHtml(section.body || "")}</p></article>`).join("");
  return `<section class="love-bazi-her"><div class="love-bazi-section-head"><span class="eyebrow">HER · 命盘详解</span><h2>${escapeHtml(her.name || "她")}的盘</h2><p>${escapeHtml(her.day_master_note || "")}</p></div><div class="love-bazi-details">${sections}</div></section>`;
}
function baziMarkup(data) {
  const hehun = data.hehun || {};
  return `<div class="love-bazi-page">${baziHeroMarkup(data)}<div class="love-bazi-pair">${baziChartMarkup(data.him || {}, "him")}${baziChartMarkup(data.her || {}, "her")}</div>${card("三条依据", baziPointsMarkup(hehun.points), { eyebrow: "WHY", subtitle: "天干、地支、五行，三条都指向同一件事。" })}<aside class="love-bazi-review"><span class="eyebrow">NOTE</span><strong>口径说明</strong><p>${escapeHtml(hehun.review || "")}</p></aside>${card("关键年份", baziYearsMarkup(hehun.years), { eyebrow: "TIMELINE", subtitle: "只标大节点；具体到月要看当年流月。" })}${card("相处建议", baziAdviceMarkup(hehun.advice), { eyebrow: "HOW", subtitle: "三条就够，做到比知道有用。" })}${baziHerMarkup(data.her || {})}<p class="love-bazi-boundary">${escapeHtml(hehun.boundary || "")}</p></div>`;
}

function baziPersonFields(prefix, label, draft) {
  return `<fieldset class="love-bazi-fieldset"><legend>${escapeHtml(label)}</legend>
    <div class="form-grid">
      <div class="field"><label for="bazi-${prefix}-name">姓名</label><input id="bazi-${prefix}-name" name="${prefix}_name" maxlength="60" required value="${escapeHtml(draft.name || "")}" placeholder="姓名或称呼" /></div>
      <div class="field"><label for="bazi-${prefix}-date">公历生日</label><input id="bazi-${prefix}-date" name="${prefix}_date" type="date" value="${escapeHtml(draft.birth_date || "")}" /></div>
      <div class="field"><label for="bazi-${prefix}-time">出生时间</label><input id="bazi-${prefix}-time" name="${prefix}_time" type="time" value="${escapeHtml(draft.birth_time || "")}" /></div>
      <div class="field"><label for="bazi-${prefix}-place">出生地</label><input id="bazi-${prefix}-place" name="${prefix}_place" maxlength="120" value="${escapeHtml(draft.place || "")}" placeholder="城市即可" /></div>
    </div>
    <details class="love-bazi-advanced"><summary>已排好八字？可直接填四柱</summary>
      <div class="form-grid love-bazi-pillar-inputs">
        ${[["year", "年柱"], ["month", "月柱"], ["day", "日柱"], ["hour", "时柱"]].map(([key, title]) => `<div class="field"><label for="bazi-${prefix}-${key}">${title}</label><input id="bazi-${prefix}-${key}" name="${prefix}_${key}" maxlength="2" value="${escapeHtml(draft.pillars?.[key] || "")}" placeholder="甲子" /></div>`).join("")}
      </div>
      <p class="field-help">四柱都填时优先使用你填的；留空则按生日时间本地排盘。</p>
    </details>
  </fieldset>`;
}

function defaultBaziDraft() {
  return {
    him: { name: "", birth_date: "", birth_time: "", place: "", pillars: { year: "", month: "", day: "", hour: "" } },
    her: { name: "", birth_date: "", birth_time: "", place: "", pillars: { year: "", month: "", day: "", hour: "" } },
    title: "",
  };
}

async function renderBazi(content) {
  if (!love.baziDraft) love.baziDraft = defaultBaziDraft();
  const history = await loveApi("/api/love/bazi/history?limit=40", { timeoutMs: 20000 }).catch(() => ({ items: [] }));
  love.baziHistory = Array.isArray(history.items) ? history.items : [];
  if (!love.baziSelectedId && love.baziHistory.length) love.baziSelectedId = love.baziHistory[0].id;
  let report = null;
  if (love.baziSelectedId) {
    report = await loveApi(`/api/love/bazi/reports/${love.baziSelectedId}`, { timeoutMs: 30000 }).catch(() => null);
  }
  const historyHtml = love.baziHistory.length
    ? `<div class="love-bazi-history-list">${love.baziHistory.map((item) => `<div class="love-bazi-history-row${Number(item.id) === Number(love.baziSelectedId) ? " active" : ""}"><button type="button" class="love-bazi-history-open" data-love-action="bazi-select" data-id="${item.id}"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.him)} × ${escapeHtml(item.her)} · ${escapeHtml(item.verdict || "分析中")}</span><small>${escapeHtml(formatDateTime(item.created_at))}</small></button><button class="small-button reject" type="button" data-love-action="bazi-delete" data-id="${item.id}" data-title="${escapeHtml(item.title)}">删除</button></div>`).join("")}</div>`
    : '<p class="field-help">还没有历史页面。填好双方信息后点「AI 分析」。</p>';
  content.innerHTML = `
    ${card("合婚输入", `
      <form id="love-bazi-form" class="love-bazi-form">
        <div class="love-bazi-pair-inputs">${baziPersonFields("him", "男方", love.baziDraft.him)}${baziPersonFields("her", "女方", love.baziDraft.her)}</div>
        <div class="field"><label for="bazi-title">标题（可选）</label><input id="bazi-title" name="title" maxlength="120" value="${escapeHtml(love.baziDraft.title || "")}" placeholder="留空自动用双方姓名" /></div>
        <div class="form-actions"><span id="love-bazi-feedback" class="status-message" role="status"></span><div class="love-inline-actions"><button class="button primary" type="submit">AI 分析</button><button class="button secondary" type="button" data-love-action="bazi-reset">清空输入</button></div></div>
        <p class="field-help">四柱、十神、藏干、大运在本地计算；AI 只写格局、喜忌与合婚解读。分析会通过 dsh 发送脱敏后的双方信息，不发送照片或聊天原文。</p>
      </form>`, {
      eyebrow: "BAZI INPUT",
      subtitle: "出生信息 + 可选四柱；生成后保存为历史页面。",
    })}
    ${card("历史页面", historyHtml, { eyebrow: "HISTORY", subtitle: "可切换查看、删除或打印/导出 HTML。" })}
    ${report && report.result ? `<div class="love-bazi-result">${baziMarkup(report.result)}<div class="love-inline-actions love-bazi-export"><a class="button secondary" href="/api/love/bazi/reports/${report.id}/html" target="_blank" rel="noopener">打印 / 导出 HTML</a></div></div>` : report?.status === "failed" ? errorBox(report.error || "八字合婚生成失败") : emptyBox("还没有八字页面", "填好双方信息，点「AI 分析」生成第一份。")}
  `;
  bindBaziForm();
}

function bindBaziForm() {
  const form = $id("love-bazi-form");
  if (!form) return;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    generateBazi(form.querySelector('button[type="submit"]'));
  });
  form.querySelectorAll("input").forEach((input) => {
    input.addEventListener("input", () => {
      const name = input.name || "";
      const match = /^(him|her)_(name|date|time|place|year|month|day|hour)$/.exec(name);
      if (!match) {
        if (name === "title") love.baziDraft.title = input.value;
        return;
      }
      const [, person, field] = match;
      if (["year", "month", "day", "hour"].includes(field)) love.baziDraft[person].pillars[field] = input.value.trim();
      else if (field === "date") love.baziDraft[person].birth_date = input.value;
      else if (field === "time") love.baziDraft[person].birth_time = input.value;
      else love.baziDraft[person][field] = input.value;
    });
  });
}

function readBaziPerson(prefix) {
  const value = (name) => $id(`bazi-${prefix}-${name}`)?.value?.trim() || "";
  return {
    name: value("name"),
    birth_date: value("date"),
    birth_time: value("time"),
    place: value("place"),
    pillars: { year: value("year"), month: value("month"), day: value("day"), hour: value("hour") },
  };
}

async function generateBazi(button) {
  const him = readBaziPerson("him");
  const her = readBaziPerson("her");
  if (!him.name || !her.name) {
    toast("请先填写双方姓名。", "warning");
    return;
  }
  const completePillars = (person) => Object.values(person.pillars).every(Boolean);
  if (!him.birth_date && !completePillars(him)) {
    toast("男方需要填写出生日期，或填完整四柱。", "warning");
    return;
  }
  if (!her.birth_date && !completePillars(her)) {
    toast("女方需要填写出生日期，或填完整四柱。", "warning");
    return;
  }
  const confirmed = await confirmAction({
    title: "生成八字合婚？",
    message: "会通过 dsh 调用 AI 写格局、喜忌与合婚分析，可能需要一到几分钟；命盘本身在本地计算，结果会保存为历史页面。",
    confirmLabel: "开始分析",
  });
  if (!confirmed) return;
  love.baziDraft = { him, her, title: $id("bazi-title")?.value?.trim() || "" };
  await withBusy(button, "AI 分析中…", async () => {
    try {
      const report = await loveApi("/api/love/bazi/generate", {
        method: "POST",
        body: JSON.stringify({ him, her, title: love.baziDraft.title }),
        timeoutMs: 360000,
      });
      love.baziSelectedId = report.id;
      toast("八字合婚已生成。", "success");
      await renderLovePage();
    } catch (error) {
      const feedback = $id("love-bazi-feedback");
      if (feedback) feedback.textContent = error.message;
      toast(`生成失败：${error.message}`, "error");
    }
  });
}

async function deleteBaziReport(button) {
  const id = Number(button.dataset.id);
  const title = button.dataset.title || "这份八字合婚";
  if (!await confirmAction({ title: `删除「${title}」？`, message: "只删除这条历史页面，不影响输入资料。", confirmLabel: "删除", danger: true })) return;
  try {
    await loveApi(`/api/love/bazi/reports/${id}`, { method: "DELETE", timeoutMs: 20000 });
    if (love.baziSelectedId === id) love.baziSelectedId = 0;
    toast("已删除。", "success");
    await renderLovePage();
  } catch (error) { toast(`删除失败：${error.message}`, "error"); }
}

// ── 恋爱大师 ───────────────────────────────────────────────────────

function masterTrendChart(trend) {
  const data = Array.isArray(trend) ? trend : [];
  if (!data.length) return '<div class="love-empty-small">还没有聊天趋势。</div>';
  const width = 760;
  const height = 220;
  const padding = { left: 38, right: 14, top: 16, bottom: 26 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const max = Math.max(1, ...data.map((item) => Number(item.total) || 0));
  const step = plotWidth / data.length;
  const barWidth = Math.max(1, Math.min(10, step * 0.66));
  const bars = data.map((item, index) => {
    const x = padding.left + index * step + (step - barWidth) / 2;
    const total = Number(item.total) || 0;
    const heightValue = total / max * plotHeight;
    const y = padding.top + plotHeight - heightValue;
    return `<rect x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${barWidth.toFixed(2)}" height="${heightValue.toFixed(2)}" class="love-bar-a"><title>${escapeHtml(`${item.day}：${total} 条`)}</title></rect>`;
  }).join("");
  const labels = data.map((item, index) => index % Math.max(1, Math.ceil(data.length / 7)) === 0 ? `<text x="${(padding.left + index * step + step / 2).toFixed(2)}" y="${height - 6}" class="love-axis-text" text-anchor="middle">${escapeHtml(String(item.day || "").slice(5))}</text>` : "").join("");
  return `<div class="love-chart-wrap"><svg class="love-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="消息趋势">${[0, 0.5, 1].map((ratio) => `<line x1="${padding.left}" y1="${padding.top + plotHeight * (1 - ratio)}" x2="${width - padding.right}" y2="${padding.top + plotHeight * (1 - ratio)}" class="love-grid-line" />`).join("")}${bars}${labels}</svg><div class="love-legend"><span class="meta">最高 ${formatInt(max)} 条 / 天 · 最近 ${data.length} 天</span></div></div>`;
}

const MASTER_SKILL_LABELS = {
  portrait: "关系画像", deep_analysis: "深度分析", report: "阶段报告", journal_batch: "AI 补日记",
};
const MASTER_STATUS_LABELS = {
  queued: "排队中", running: "进行中", completed: "已完成", failed: "失败",
};

function masterNoteMarkup(result) {
  const note = result?.note;
  if (note?.path) {
    return `<div class="love-analysis-section"><h3>已写入恋爱日志</h3><p>${escapeHtml(note.notebook || "")} / ${escapeHtml(note.title || "")}</p><div class="love-inline-actions"><button class="button secondary" type="button" data-love-action="memory-open" data-path="${escapeHtml(note.path)}">打开笔记</button><button class="button text-button" type="button" data-love-action="goto-memory">去恋爱记忆</button></div></div>`;
  }
  if (result?.note_error) {
    return `<p class="inline-error">分析已保存，但写入恋爱日志失败：${escapeHtml(result.note_error)}</p>`;
  }
  return "";
}

function masterResultMarkup(run) {
  if (!run) return "";
  if (run.status === "running" || run.status === "queued") {
    return `<div class="love-analysis-running"><div class="love-spinner" aria-hidden="true"></div><div><strong>正在进行 ${escapeHtml(MASTER_SKILL_LABELS[run.skill] || run.skill)}</strong><p>${escapeHtml(run.progress || "正在整理数据并请求 dsh…")}</p><p class="meta">页面可以切走，任务仍在本地服务里跑。</p></div></div>`;
  }
  if (run.status === "failed") return errorBox(run.error || "这次分析没有完成");
  const result = run.result || {};
  const structured = result.structured || {};
  const text = result.text || "";
  if (run.skill === "portrait") {
    const traits = Array.isArray(structured.traits) ? structured.traits : [];
    if (!structured.summary && !traits.length) {
      return `<pre class="love-master-text">${escapeHtml(text || JSON.stringify(structured, null, 2))}</pre>`;
    }
    return `<div class="love-analysis-hero"><div><span class="eyebrow">PORTRAIT</span><strong>${escapeHtml(structured.summary || "对象画像")}</strong></div><span class="meta">${escapeHtml(formatDateTime(run.completed_at))}</span></div>
      <div class="love-master-grid"><div><h3>画像条目</h3>${traits.length ? `<div class="love-trait-list">${traits.map((trait) => `<article class="love-trait"><h3>${escapeHtml(trait.trait || "")}</h3><p>${escapeHtml(trait.value || "")}</p><small>置信度 ${percent(trait.confidence, 0)}${trait.evidence ? ` · ${escapeHtml(String(trait.evidence).slice(0, 160))}` : ""}</small></article>`).join("")}</div>` : '<p class="meta">没有提取到画像条目。</p>'}</div><div><h3>沟通与照顾</h3><p>${escapeHtml(structured.communication_style || "")}</p><ul class="love-advice">${(structured.care_tips || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul><h3>需要留意</h3><ul class="love-risk-list">${(structured.risks || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>没有明显风险。</li>"}</ul></div></div>${masterNoteMarkup(result)}`;
  }
  if (run.skill === "deep_analysis") {
    const tone = structured.emotional_tone || {};
    const patterns = structured.communication_patterns || {};
    return `<div class="love-analysis-hero"><div><span class="eyebrow">DEEP ANALYSIS</span><strong>${escapeHtml(structured.relationship_stage || "关系分析")}</strong></div><span class="meta">${escapeHtml(formatDateTime(run.completed_at))}</span></div>
      <div class="love-analysis-tone"><div class="love-tone-card"><span>整体情绪</span><strong>${escapeHtml(tone.overall || "—")}</strong></div><div class="love-tone-card"><span>情绪波动</span><strong>${percent(tone.fluctuation, 0)}</strong></div><div class="love-tone-card"><span>趋势</span><strong>${escapeHtml(tone.trend || "—")}</strong></div></div>
      <div class="love-analysis-section"><h3>沟通模式</h3><dl class="love-facts"><div><dt>主动度</dt><dd>${escapeHtml(patterns.initiative_balance || "—")}</dd></div><div><dt>回应风格</dt><dd>${escapeHtml(patterns.response_style || "—")}</dd></div><div><dt>主要话题</dt><dd>${(patterns.key_topics || []).map(escapeHtml).join("、") || "—"}</dd></div></dl></div>
      ${(structured.key_events || []).length ? `<div class="love-analysis-section"><h3>关键事件</h3><ul class="love-event-list">${structured.key_events.map((event) => `<li class="love-event impact-${escapeHtml(event.impact || "neutral")}"><span>${escapeHtml(event.date || "")}</span><p>${escapeHtml(event.description || "")}</p></li>`).join("")}</ul></div>` : ""}
      ${(structured.risk_warnings || []).length ? `<div class="love-analysis-section"><h3>需要注意</h3><ul class="love-risk-list">${structured.risk_warnings.map((risk) => `<li class="love-risk risk-${escapeHtml(risk.severity || "low")}"><strong>${escapeHtml(risk.aspect || "")}</strong><p>${escapeHtml(risk.detail || "")}</p></li>`).join("")}</ul></div>` : ""}
      ${(structured.personalized_advice || []).length ? `<div class="love-analysis-section"><h3>建议</h3><ol class="love-advice">${structured.personalized_advice.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ol></div>` : ""}${masterNoteMarkup(result)}`;
  }
  if (run.skill === "report") {
    return `<div class="love-analysis-hero"><div><span class="eyebrow">REPORT</span><strong>${escapeHtml(structured.title || "关系阶段报告")}</strong></div><span class="meta">${escapeHtml(formatDateTime(run.completed_at))}</span></div><p>${escapeHtml(structured.summary || "")}</p>${(structured.sections || []).map((section) => `<div class="love-analysis-section"><h3>${escapeHtml(section.title || "")}</h3><p>${escapeHtml(section.body || "")}</p></div>`).join("")}${masterNoteMarkup(result)}`;
  }
  if (run.skill === "journal_batch") {
    const created = Array.isArray(structured.created) ? structured.created : [];
    const failed = Array.isArray(structured.failed) ? structured.failed : [];
    return `<div class="love-analysis-hero"><div><span class="eyebrow">AI DRAFTS</span><strong>${escapeHtml(structured.text || "批量补日记")}</strong></div><span class="meta">${escapeHtml(formatDateTime(run.completed_at))}</span></div><p>新增 ${formatInt(created.length)} 篇，失败 ${formatInt(failed.length)} 篇。草稿在「恋爱记忆 → AI 草稿」，确认后可一键移入恋爱日志。</p><div class="love-inline-actions"><button class="button secondary" type="button" data-love-action="goto-memory">去恋爱记忆</button></div>`;
  }
  return `<pre class="love-master-text">${escapeHtml(text || JSON.stringify(structured, null, 2))}</pre>`;
}

async function renderMaster(content) {
  const [overview, runsData] = await Promise.all([
    loveApi("/api/love/master/overview", { timeoutMs: 40000 }),
    loveApi("/api/love/master/runs?limit=30", { timeoutMs: 30000 }),
  ]);
  love.master = overview;
  love.masterRuns = Array.isArray(runsData.items) ? runsData.items : [];
  if (love.masterSelectedId && !love.masterRuns.some((item) => Number(item.id) === Number(love.masterSelectedId))) {
    love.masterSelectedId = 0;
  }
  let selected = null;
  if (love.masterSelectedId) {
    selected = await loveApi(`/api/love/master/runs/${love.masterSelectedId}`, { timeoutMs: 30000 }).catch(() => null);
  }
  const memory = overview.memory || {};
  const chat = overview.chat || {};
  const cycle = overview.cycle || {};
  const schedule = overview.schedule || {};
  const gallery = overview.gallery || {};
  const bazi = overview.bazi || {};
  const contact = chat.contact || {};
  const today = schedule.today || {};
  const statTiles = [
    ["消息", formatInt(chat.total), `${formatInt(chat.active_days)} 个活跃日`],
    ["恋爱记忆", formatInt(memory.total), `恋爱日志 ${formatInt(memory.journal_count)}`],
    ["相册", formatInt(gallery.total), `照片 ${formatInt(gallery.photos)} · 视频 ${formatInt(gallery.videos)}`],
    ["八字合婚", formatInt(bazi.count), bazi.latest?.verdict || "还没有历史"],
    ["经期", cycle.has_data ? (cycle.phase_label || "—") : "未记录", cycle.has_data ? `预计 ${cycle.next_expected || "—"}` : "去经期关怀记录"],
    ["今日排班", today.work ? "上班" : today.rest ? "休息" : "未排班", today.duty || "点排班日历查看"],
  ].map(([label, value, hint]) => `<article class="love-stat"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(hint)}</small></article>`).join("");
  const skillCards = `
    <article class="love-skill-card"><span class="eyebrow">PORTRAIT</span><h3>关系画像</h3><p>从记忆、聊天与统计里提炼对象画像、沟通方式和照顾建议。</p><button class="button primary" type="button" data-love-action="master-run" data-skill="portrait">生成画像</button></article>
    <article class="love-skill-card"><span class="eyebrow">DEEP ANALYSIS</span><h3>深度分析</h3><p>看关系阶段、沟通模式、关键事件与风险。</p><div class="love-inline-actions"><select id="love-master-deep-days" class="love-gallery-select"><option value="30">近 30 天</option><option value="90">近 90 天</option><option value="0">全部</option></select><button class="button primary" type="button" data-love-action="master-run" data-skill="deep_analysis">开始分析</button></div></article>
    <article class="love-skill-card"><span class="eyebrow">REPORT</span><h3>阶段报告</h3><p>生成可留档的报告，支持查看历史、删除与 HTML 导出。</p><div class="love-inline-actions"><select id="love-master-report-days" class="love-gallery-select"><option value="30">近 30 天</option><option value="90">近 90 天</option><option value="0">全部</option></select><button class="button primary" type="button" data-love-action="master-run" data-skill="report">生成报告</button></div></article>
    <article class="love-skill-card"><span class="eyebrow">AI JOURNAL</span><h3>AI 批量补日记</h3><p>只补有聊天、还没有记忆的日期，先写进「AI 草稿」。</p><div class="love-inline-actions"><input id="love-master-journal-days" class="love-gallery-select" type="number" min="1" max="60" value="10" aria-label="最多补几天" /><button class="button primary" type="button" data-love-action="master-run" data-skill="journal_batch">开始补写</button></div></article>
    <article class="love-skill-card"><span class="eyebrow">COACH</span><h3>沟通教练</h3><p>打开右侧恋爱大师角色层，带着当前数据聊今天能怎么做。</p><button class="button primary" type="button" data-love-action="master-chat">和恋爱大师聊聊</button></article>
    <article class="love-skill-card"><span class="eyebrow">BAZI</span><h3>八字合婚</h3><p>补齐双方出生信息，生成新的合婚页面。</p><button class="button secondary" type="button" data-love-action="goto-bazi">打开八字合婚</button></article>`;
  const history = love.masterRuns.length
    ? `<div class="love-master-history">${love.masterRuns.map((run) => `<div class="love-master-history-row${Number(run.id) === Number(love.masterSelectedId) ? " active" : ""}"><button type="button" class="love-master-history-open" data-love-action="master-select" data-id="${run.id}"><span class="love-badge ${run.status === "completed" ? "ok" : run.status === "failed" ? "error" : "warn"}">${escapeHtml(MASTER_STATUS_LABELS[run.status] || run.status)}</span><strong>${escapeHtml(MASTER_SKILL_LABELS[run.skill] || run.skill)}</strong><small>${escapeHtml(formatDateTime(run.created_at))}</small></button>${run.status === "completed" ? `<a class="small-button" href="/api/love/master/runs/${run.id}/html" target="_blank" rel="noopener">HTML</a>` : ""}<button class="small-button reject" type="button" data-love-action="master-delete" data-id="${run.id}">删除</button></div>`).join("")}</div>`
    : '<p class="field-help">还没有 AI 结果。</p>';
  content.innerHTML = `
    <section class="love-master-hero">
      <div><p class="eyebrow">LOVE MASTER</p><h3>${escapeHtml(contact.display_name || "这段关系")}的关系总览</h3><p class="love-master-copy">数据来自恋爱记忆、微信聊天、排班、经期、相册与八字；右侧角色层是唯一聊天入口，所有 AI 都走 dsh。</p><div class="love-inline-actions"><button class="button primary" type="button" data-love-action="master-chat">和恋爱大师聊聊</button><button class="button secondary" type="button" data-love-action="refresh">刷新看板</button></div></div>
      <div class="love-master-orbit" aria-hidden="true"><span>♥</span><small>${formatInt(chat.total)} 条消息</small></div>
    </section>
    <div class="love-stat-grid love-master-stats">${statTiles}</div>
    <div class="love-master-grid">
      ${card("消息趋势", masterTrendChart(chat.trend), { eyebrow: "DASHBOARD", subtitle: "最近 90 天；数据来自微信聊天页。" })}
      ${card("最近记忆", (memory.recent || []).length ? `<div class="love-master-recent">${memory.recent.slice(0, 8).map((note) => `<button type="button" class="love-memory-note small" data-love-action="memory-open" data-path="${escapeHtml(note.path)}"><span class="love-memory-note-date">${escapeHtml(String(note.date || "").slice(5))}</span><span class="love-memory-note-body"><strong>${escapeHtml(note.title || "")}</strong><p>${escapeHtml(note.excerpt || "")}</p></span></button>`).join("")}</div>` : '<p class="field-help">还没有恋爱记忆。</p>', { eyebrow: "MEMORY", subtitle: "最近写下的片段。" })}
    </div>
    ${card("数据看板", `
      <div class="love-master-stat-grid">
        <div class="love-master-stat"><span>我发出</span><strong>${formatInt(chat.me || 0)}</strong><small>${percent(chat.total ? (chat.me || 0) / chat.total : 0, 1)}</small></div>
        <div class="love-master-stat"><span>对象发出</span><strong>${formatInt(chat.partner || 0)}</strong><small>${percent(chat.total ? (chat.partner || 0) / chat.total : 0, 1)}</small></div>
        <div class="love-master-stat"><span>图片</span><strong>${formatInt((chat.media || {}).image || 0)}</strong><small>媒体</small></div>
        <div class="love-master-stat"><span>视频</span><strong>${formatInt((chat.media || {}).video || 0)}</strong><small>媒体</small></div>
        <div class="love-master-stat"><span>语音</span><strong>${formatInt((chat.media || {}).voice || 0)}</strong><small>媒体</small></div>
        <div class="love-master-stat"><span>最活跃</span><strong>${escapeHtml((chat.hours || []).slice().sort((a, b) => Number(b.count) - Number(a.count)).slice(0, 3).map((item) => `${String(item.hour).padStart(2, "0")}:00`).join(" · ") || "—")}</strong><small>按小时</small></div>
      </div>
      ${(chat.keywords || []).length ? `<div class="love-keyword-cloud">${chat.keywords.map((item) => `<span class="love-keyword love-word-${Math.min(6, Math.max(1, Math.ceil(Math.log2(Number(item.count) || 1))))}" title="${escapeHtml(item.word)} · ${formatInt(item.count)} 次">${escapeHtml(item.word)}</span>`).join("")}</div>` : ""}
      <p class="field-help">统计范围：${escapeHtml(formatDate(chat.first_day))} 至 ${escapeHtml(formatDate(chat.last_day))}；${formatInt(chat.active_days || 0)} 个活跃日。</p>
    `, { eyebrow: "STATS", subtitle: "原有的消息量、双方比例、媒体、活跃时段与高频词。" })}
    <section class="love-skill-section"><div class="notebook-section-head"><div><p class="eyebrow">SKILLS</p><h3>让恋爱大师做什么</h3></div><span class="meta">全部在恋爱大师页发起</span></div><div class="love-skill-grid">${skillCards}</div></section>
    ${card("分析历史", history, { eyebrow: "HISTORY", subtitle: "结果保存在 07_恋爱大师/analysis.db。" })}
    <div id="love-master-detail" class="love-master-detail">${selected ? masterResultMarkup(selected) : ""}</div>
  `;
  if (selected && ["running", "queued"].includes(selected.status)) pollMasterRun(selected.id);
}

async function runMasterSkill(skill, button) {
  const days = skill === "report"
    ? Number($id("love-master-report-days")?.value ?? 30)
    : skill === "deep_analysis"
      ? Number($id("love-master-deep-days")?.value ?? 30)
      : 30;
  const maxDays = Number($id("love-master-journal-days")?.value || 10);
  const labels = { portrait: "关系画像", deep_analysis: "深度分析", report: "阶段报告", journal_batch: "AI 批量补日记" };
  if (!await confirmAction({
    title: `开始${labels[skill] || "分析"}？`,
    message: "会通过 dsh 读取本机恋爱数据的脱敏摘要并生成结果；可能需要几分钟，结果会保存在本机。",
    confirmLabel: "开始",
  })) return;
  await withBusy(button, "已提交…", async () => {
    try {
      const result = await loveApi("/api/love/master/run", {
        method: "POST",
        body: JSON.stringify({ skill, days, max_days: maxDays }),
        timeoutMs: 30000,
      });
      love.masterSelectedId = result.run_id;
      toast("已开始，页面会自动更新。", "info");
      await renderLovePage();
      pollMasterRun(result.run_id);
    } catch (error) {
      toast(`启动失败：${error.message}`, "error");
    }
  });
}

function pollMasterRun(runId) {
  stopMasterPoll();
  const tick = async () => {
    if (love.page !== "master") {
      love.masterPoll = 0;
      return;
    }
    try {
      const run = await loveApi(`/api/love/master/runs/${runId}`, { timeoutMs: 20000 });
      love.masterSelectedId = runId;
      const target = $id("love-master-detail");
      if (target) target.innerHTML = masterResultMarkup(run);
      if (["completed", "failed"].includes(run.status)) {
        love.masterPoll = 0;
        toast(run.status === "completed" ? "恋爱大师已完成。" : "恋爱大师失败，请查看原因。", run.status === "completed" ? "success" : "error");
        await renderLovePage();
        return;
      }
    } catch {
      // 继续轮询
    }
    love.masterPoll = window.setTimeout(tick, 2200);
  };
  love.masterPoll = window.setTimeout(tick, 1200);
}

async function deleteMasterRun(button) {
  const id = Number(button.dataset.id);
  if (!await confirmAction({ title: "删除这条分析？", message: "只删除本机保存的分析结果，不影响聊天与记忆。", confirmLabel: "删除", danger: true })) return;
  try {
    await loveApi(`/api/love/master/runs/${id}`, { method: "DELETE", timeoutMs: 20000 });
    if (love.masterSelectedId === id) love.masterSelectedId = 0;
    toast("已删除。", "success");
    await renderLovePage();
  } catch (error) { toast(`删除失败：${error.message}`, "error"); }
}

// ── 设置抽屉 ───────────────────────────────────────────────────────

async function openLoveSettings() {
  const drawer = $id("love-settings-drawer");
  const backdrop = $id("love-settings-backdrop");
  if (!drawer) return;
  love.settingsOpen = true;
  drawer.hidden = false;
  if (backdrop) backdrop.hidden = false;
  document.body.classList.add("love-settings-open");
  const body = $id("love-settings-body");
  if (body) body.innerHTML = loadingBox("正在读取设置…");
  let status = love.status;
  let chat = love.chatStatus;
  try {
    status = await loveApi("/api/love/status", { timeoutMs: 20000 });
    love.status = status;
  } catch { /* 保留旧状态 */ }
  try {
    chat = await loveApi("/api/love/chat/status", { timeoutMs: 20000 });
    love.chatStatus = chat;
  } catch { /* WeFlow 可能没启动 */ }
  if (!body) return;
  const paths = status?.paths || {};
  const pathRows = [
    ["数据根目录", paths.root],
    ["恋爱记忆", paths.memory],
    ["微信聊天", paths.chat],
    ["排班日历", paths.schedule],
    ["经期关怀", paths.cycle],
    ["恋爱相册", paths.gallery],
    ["八字合婚", paths.bazi],
    ["恋爱大师", paths.master],
  ].map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd class="love-path">${escapeHtml(value || "—")}</dd></div>`).join("");
  const migration = status?.migration || {};
  const component = love.weflowComponent || {};
  body.innerHTML = `
    ${card("WeFlow 连接", `
      <form id="love-settings-weflow" class="love-key-form">
        <div class="form-grid"><div class="field"><label for="love-settings-port">端口</label><input id="love-settings-port" name="port" type="number" min="1" max="65535" value="${escapeHtml(String(chat?.port || 5031))}" /></div><div class="field"><label for="love-settings-token">Access Token</label><input id="love-settings-token" name="token" type="password" autocomplete="off" maxlength="400" placeholder="${chat?.token_configured ? "已配置；留空只改端口" : "粘贴 WeFlow Token"}" /></div></div>
        <div class="love-inline-actions"><button class="button primary" type="submit">保存并测试</button>${chat?.token_configured ? '<button class="button secondary" type="button" data-love-action="settings-clear-token">清除 Token</button>' : ""}</div>
        <p class="field-help">${chat?.token_configured ? "Token 已加密保存在 02_微信聊天/.credentials.json。" : "还没有配置 Token。"}${chat?.weflow_ok === false ? ` <span class="inline-error">${escapeHtml(chat.weflow_error || "连接失败")}</span>` : ""}</p>
      </form>`, { eyebrow: "WEFLOW" })}
    ${card("微信组件修复", `
      <p class="field-help">WeFlow 自带的闭源 WCDB 组件有到期校验，过期后会报 101。torch 不会破解它；这里用开源项目 <a href="https://github.com/olansoft/weflow-wcdb-api-oss" target="_blank" rel="noopener">olansoft/weflow-wcdb-api-oss</a> 构建的不过期 DLL 替换，并在替换前备份、失败可回滚。</p>
      <form id="love-weflow-dir-form" class="love-key-form">
        <div class="field">
          <label for="love-weflow-dir-input">WeFlow 安装目录（自动检测失败时手动指定）</label>
          <input id="love-weflow-dir-input" name="path" type="text" autocomplete="off" maxlength="1024" value="${escapeHtml(component.configured_dir || "")}" placeholder="例如 C:\\Program Files\\WeFlow，或 wcdb_api.dll 所在文件夹" />
          <p class="field-help">可以填 WeFlow 安装根目录，也可以直接填 wcdb_api.dll 所在文件夹；保存后会优先按这个目录查找。不知道路径时，在任务管理器找到 WeFlow.exe → 右键「打开文件所在的位置」。</p>
        </div>
        <div class="love-inline-actions"><button class="button secondary" type="submit">保存目录并检查</button></div>
      </form>
      <div id="love-weflow-component-status" class="love-component-status">正在检查…</div>
      <div class="love-inline-actions"><button class="button secondary" type="button" data-love-action="weflow-component-check">重新检查</button><button class="button primary" type="button" data-love-action="weflow-component-install">选择已构建的 DLL 并替换</button><button class="button secondary" type="button" data-love-action="weflow-component-bundled">使用内置 DLL 一键替换</button><button class="button secondary" type="button" data-love-action="weflow-component-rollback">回滚</button></div>
      <p class="field-help">当前环境可能没有 C++ 工具链；项目附带构建脚本与图文教程，首次需要先构建一次 wcdb_api.dll。</p>`, { eyebrow: "COMPONENT" })}
    ${card("数据目录", `<dl class="love-facts">${pathRows}</dl>${migration.status ? `<p class="field-help">迁移状态：${escapeHtml(migration.status)}${migration.source ? ` · 来源 ${escapeHtml(migration.source)}` : ""}${migration.backup ? ` · 备份 ${escapeHtml(migration.backup)}` : ""}</p>` : ""}`, { eyebrow: "DATA" })}
    ${card("AI 与隐私", `<dl class="love-facts"><div><dt>dsh</dt><dd>${status?.ai?.available ? '<span class="love-badge ok">可用</span>' : `<span class="love-badge warn">不可用</span> <span class="meta">${escapeHtml(status?.ai?.reason || "")}</span>`}</dd></div><div><dt>只读模式</dt><dd>${status?.read_only ? "开启" : "关闭"}</dd></div></dl><p class="field-help">所有 AI 都走 dsh；聊天原文脱敏后发送，经期只发送聚合阶段，不发送每日症状明细。原始文件仍只在本机。</p>`, { eyebrow: "AI" })}`;
  $id("love-settings-weflow")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    const port = Number(form.elements.port.value) || 5031;
    const token = form.elements.token.value.trim();
    await withBusy(submit, "测试中…", async () => {
      try {
        await loveApi("/api/love/chat/settings", { method: "PUT", body: JSON.stringify({ port, token }), timeoutMs: 40000 });
        form.elements.token.value = "";
        toast("WeFlow 设置已保存。", "success");
        await openLoveSettings();
      } catch (error) { toast(`保存失败：${error.message}`, "error"); }
    });
  });
  $id("love-weflow-dir-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.elements.path;
    const path = String(input?.value || "").trim();
    const submit = form.querySelector('button[type="submit"]');
    if (!path) {
      toast("请先填写 WeFlow 安装目录。", "warning");
      input?.focus();
      return;
    }
    await withBusy(submit, "检查中…", async () => {
      try {
        const result = await loveApi("/api/love/weflow/component/install-dir", {
          method: "PUT", body: JSON.stringify({ path }), timeoutMs: 30000,
        });
        toast(result.message || "目录已保存；请检查组件状态。", "success");
        await checkWeFlowComponent();
      } catch (error) { toast(`保存目录失败：${error.message}`, "error"); }
    });
  });
  checkWeFlowComponent();
}

function closeLoveSettings() {
  love.settingsOpen = false;
  const drawer = $id("love-settings-drawer");
  const backdrop = $id("love-settings-backdrop");
  if (drawer) drawer.hidden = true;
  if (backdrop) backdrop.hidden = true;
  document.body.classList.remove("love-settings-open");
}

async function checkWeFlowComponent() {
  const target = $id("love-weflow-component-status");
  if (!target) return;
  try {
    const data = await loveApi("/api/love/weflow/component/status", { timeoutMs: 20000 });
    love.weflowComponent = data;
    const dirInput = $id("love-weflow-dir-input");
    if (dirInput && !dirInput.value.trim()) {
      dirInput.value = data.configured_dir || data.install_dir || "";
    }
    const installed = data.installed
      ? "已找到 WeFlow"
      : data.configured_dir ? "手动目录里还没有 wcdb_api.dll" : "没有找到 WeFlow 安装目录";
    const expired = data.expired ? " · 组件已过期（101）" : "";
    const bundled = data.bundled_dll ? " · 已发现内置 DLL" : " · 没有内置 DLL（需先构建）";
    const manual = data.configured_dir
      ? ` · 手动目录${data.configured_dir_exists === false ? "不存在" : ""}`
      : "";
    target.innerHTML = `<span class="love-badge ${data.expired ? "error" : data.installed ? "ok" : "warn"}">${escapeHtml(installed)}${escapeHtml(expired)}</span><p class="field-help">${escapeHtml(data.dll_path || "未检测到 wcdb_api.dll")}${escapeHtml(bundled)}${escapeHtml(manual)}</p>${data.configured_dir ? `<p class="field-help">手动目录：${escapeHtml(data.configured_dir)}</p>` : ""}`;
  } catch (error) {
    target.innerHTML = `<span class="love-badge warn">组件检查不可用</span><p class="field-help">${escapeHtml(error.message)}</p>`;
  }
}

// ── 动作分发 ───────────────────────────────────────────────────────

async function handleLoveAction(event) {
  const button = event.target.closest("[data-love-action]");
  if (!button) return;
  const action = button.dataset.loveAction;
  if (action.startsWith("cycle-")) {
    await handleLoveCycleAction(event, { rerender: renderLovePage });
    return;
  }
  switch (action) {
    case "retry-page":
      await renderLovePage();
      return;
    case "refresh":
      await renderLovePage();
      return;
    case "goto-memory":
      love.page = "memories";
      await renderLovePage();
      return;
    case "goto-bazi":
      love.page = "bazi";
      await renderLovePage();
      return;
    case "settings-open":
      await openLoveSettings();
      return;
    case "memory-new":
      openNewMemory();
      return;
    case "memory-open":
      await openMemoryNote(button.dataset.path || "");
      return;
    case "memory-notebook":
      love.memoryNotebook = love.memoryNotebook === button.dataset.key ? "" : (button.dataset.key || "");
      await renderLovePage();
      return;
    case "memory-clear-filter":
      love.memoryNotebook = "";
      await renderLovePage();
      return;
    case "memory-create-notebook":
      openNotebookDialog();
      return;
    case "memory-delete-notebook":
      await deleteMemoryNotebook(button);
      return;
    case "memory-promote-drafts":
      await promoteAiDrafts(button);
      return;
    case "chat-clear-token":
      if (!await confirmAction({ title: "清除 WeFlow Token？", message: "清除后不能读取微信联系人，也不能同步；已有聊天保留。", confirmLabel: "清除", danger: true })) return;
      try {
        await loveApi("/api/love/chat/token", { method: "DELETE", timeoutMs: 20000 });
        toast("Token 已清除。", "success");
        await renderLovePage();
      } catch (error) { toast(`清除失败：${error.message}`, "error"); }
      return;
    case "chat-sync-apply":
      await startChatSync("apply", button);
      return;
    case "chat-sync-preview":
      await startChatSync("preview", button);
      return;
    case "chat-rebind":
      love.chatRebinding = true;
      love.chatSessions = love.chatSessions || [];
      if (love.chatStatus?.token_configured) await loadChatSessions({ silent: true });
      toast("在下方选择新的联系人；换绑会先备份并清空旧聊天。", "info");
      await renderLovePage();
      return;
    case "chat-unbind":
      if (!await confirmAction({ title: "解除微信绑定？", message: "解除后不再同步新消息；已经同步的聊天保留在 chat.db，可继续查看。", confirmLabel: "解除绑定", danger: true })) return;
      try {
        await loveApi("/api/love/chat/bind", { method: "DELETE", timeoutMs: 20000 });
        love.chatRebinding = false;
        love.chatDay = "";
        toast("已解除绑定。", "success");
        await renderLovePage();
      } catch (error) { toast(`解除失败：${error.message}`, "error"); }
      return;
    case "chat-day":
      love.chatDay = button.dataset.date || love.chatDay;
      await renderLovePage();
      return;
    case "chat-month":
      love.chatMonth = shiftMonth(chatMonthValue(), Number(button.dataset.value) < 0 ? -1 : 1);
      love.chatDay = "";
      await renderLovePage();
      return;
    case "chat-month-today":
      love.chatMonth = currentMonthText();
      love.chatDay = "";
      await renderLovePage();
      return;
    case "schedule-month":
      love.scheduleMonth = shiftMonth(scheduleMonthValue(), Number(button.dataset.value) < 0 ? -1 : 1);
      await renderLovePage();
      return;
    case "schedule-today":
      love.scheduleMonth = currentMonthText();
      await renderLovePage();
      return;
    case "schedule-upload":
      $id("love-schedule-file")?.click();
      return;
    case "schedule-day": {
      const date = button.dataset.date || "";
      const day = love.scheduleData?.days?.find((item) => item.date === date)
        || { date, in_range: false, work: null, rest: null, note: "", duty: "" };
      openScheduleDay(day);
      return;
    }
    case "gallery-view":
      love.galleryView = ["all", "albums", "photos", "videos"].includes(button.dataset.view) ? button.dataset.view : "all";
      love.galleryKind = love.galleryView === "photos" ? "photo" : love.galleryView === "videos" ? "video" : "";
      love.galleryAlbum = 0;
      love.gallerySelection.clear();
      await renderLovePage();
      return;
    case "gallery-album":
      love.galleryView = "all";
      love.galleryAlbum = Number(button.dataset.id) || 0;
      love.galleryKind = "";
      love.gallerySelection.clear();
      await renderLovePage();
      return;
    case "gallery-all":
      love.galleryAlbum = 0;
      love.galleryKind = "";
      love.galleryView = "all";
      await renderLovePage();
      return;
    case "gallery-upload":
      $id("love-gallery-files")?.click();
      return;
    case "gallery-open": {
      const item = love.galleryItems?.find((media) => Number(media.id) === Number(button.dataset.id));
      if (item) openGalleryViewer(item);
      return;
    }
    case "gallery-create-album": {
      openDialog("新建相簿", `<form id="love-gallery-album-form" class="love-dialog-form"><div class="field"><label for="love-gallery-album-name">相簿名称</label><input id="love-gallery-album-name" name="name" maxlength="48" required /></div><div class="form-actions"><div class="love-inline-actions"><button class="button secondary" type="button" id="love-gallery-album-cancel">取消</button><button class="button primary" type="submit">创建</button></div></div></form>`);
      $id("love-gallery-album-cancel")?.addEventListener("click", () => $id("detail-dialog")?.close());
      $id("love-gallery-album-form")?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const name = event.currentTarget.elements.name.value.trim();
        if (!name) return;
        try {
          await loveApi("/api/love/gallery/albums", { method: "POST", body: JSON.stringify({ name }), timeoutMs: 20000 });
          $id("detail-dialog")?.close();
          toast("相簿已创建。", "success");
          await renderLovePage();
        } catch (error) { toast(`创建失败：${error.message}`, "error"); }
      });
      return;
    }
    case "gallery-rename-album": {
      const id = Number(button.dataset.id);
      openDialog("重命名相簿", `<form id="love-gallery-rename-form" class="love-dialog-form"><div class="field"><label for="love-gallery-rename-name">新名称</label><input id="love-gallery-rename-name" name="name" maxlength="48" required value="${escapeHtml(button.dataset.name || "")}" /></div><div class="form-actions"><div class="love-inline-actions"><button class="button secondary" type="button" id="love-gallery-rename-cancel">取消</button><button class="button primary" type="submit">保存</button></div></div></form>`);
      $id("love-gallery-rename-cancel")?.addEventListener("click", () => $id("detail-dialog")?.close());
      $id("love-gallery-rename-form")?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const name = event.currentTarget.elements.name.value.trim();
        if (!name) return;
        try {
          await loveApi(`/api/love/gallery/albums/${id}`, { method: "PUT", body: JSON.stringify({ name }), timeoutMs: 20000 });
          $id("detail-dialog")?.close();
          toast("相簿已改名。", "success");
          await renderLovePage();
        } catch (error) { toast(`改名失败：${error.message}`, "error"); }
      });
      return;
    }
    case "gallery-delete-album": {
      const id = Number(button.dataset.id);
      if (!await confirmAction({ title: `删除相簿「${button.dataset.name || ""}」？`, message: "相簿里的照片/视频会移回「未分类」，不会删除文件。", confirmLabel: "删除相簿", danger: true })) return;
      try {
        await loveApi(`/api/love/gallery/albums/${id}`, { method: "DELETE", timeoutMs: 20000 });
        toast("相簿已删除，媒体已移回未分类。", "success");
        await renderLovePage();
      } catch (error) { toast(`删除失败：${error.message}`, "error"); }
      return;
    }
    case "gallery-bulk-move":
      openGalleryMoveDialog([...love.gallerySelection]);
      return;
    case "gallery-bulk-delete": {
      const ids = [...love.gallerySelection];
      if (!ids.length) return;
      if (!await confirmAction({ title: `删除选中的 ${ids.length} 项？`, message: "文件和缩略图会从相册目录删除，无法在应用内恢复。", confirmLabel: "删除", danger: true })) return;
      try {
        await loveApi("/api/love/gallery/media/bulk", { method: "POST", body: JSON.stringify({ ids, action: "delete" }), timeoutMs: 60000 });
        love.gallerySelection.clear();
        toast("已删除。", "success");
        await renderLovePage();
      } catch (error) { toast(`删除失败：${error.message}`, "error"); }
      return;
    }
    case "gallery-clear-selection":
      love.gallerySelection.clear();
      await renderLovePage();
      return;
    case "bazi-generate":
      await generateBazi(button);
      return;
    case "bazi-reset":
      love.baziDraft = defaultBaziDraft();
      await renderLovePage();
      return;
    case "bazi-select":
      love.baziSelectedId = Number(button.dataset.id) || 0;
      await renderLovePage();
      return;
    case "bazi-delete":
      await deleteBaziReport(button);
      return;
    case "master-run":
      await runMasterSkill(button.dataset.skill || "", button);
      return;
    case "master-select":
      love.masterSelectedId = Number(button.dataset.id) || 0;
      await renderLovePage();
      return;
    case "master-delete":
      await deleteMasterRun(button);
      return;
    case "master-chat":
      openRoleDrawer({
        ...COMPANIONS.love,
        selectors: [{ entry: "love" }],
        prompt: "基于我当前的恋爱数据，给我三条今天能做的具体建议；先说两三行态势。",
      });
      return;
    case "settings-clear-token":
      if (!await confirmAction({ title: "清除 WeFlow Token？", message: "清除后不能读取联系人，也不能同步；已有聊天保留。", confirmLabel: "清除", danger: true })) return;
      try {
        await loveApi("/api/love/chat/token", { method: "DELETE", timeoutMs: 20000 });
        toast("Token 已清除。", "success");
        await openLoveSettings();
      } catch (error) { toast(`清除失败：${error.message}`, "error"); }
      return;
    case "weflow-component-check":
      await checkWeFlowComponent();
      return;
    case "weflow-component-install":
      await installWeFlowComponent(button);
      return;
    case "weflow-component-bundled":
      await installBundledWeFlowComponent(button);
      return;
    case "weflow-component-rollback":
      await rollbackWeFlowComponent(button);
      return;
    default:
      return;
  }
}

async function installWeFlowComponent(button) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".dll";
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    await withBusy(button, "替换中…", async () => {
      try {
        const result = await loveApi("/api/love/weflow/component/replace", { method: "POST", body: form, timeoutMs: 120000 });
        toast(result.message || "组件已替换；请重启 WeFlow 后重试。", "success");
        await checkWeFlowComponent();
      } catch (error) {
        toast(`替换失败：${error.message}`, "error");
      }
    });
  });
  input.click();
}

async function installBundledWeFlowComponent(button) {
  if (!love.weflowComponent?.bundled_dll) {
    const choose = await confirmAction({
      title: "还没有内置 DLL",
      message: "assets/weflow/wcdb_api.dll 不存在。内置替换需要先按教程构建一次；也可以直接选择你已有的 wcdb_api.dll。替换前请完全退出 WeFlow（含托盘）。",
      confirmLabel: "选择已有 DLL",
      cancelLabel: "知道了",
    });
    if (choose) await installWeFlowComponent(button);
    return;
  }
  if (!await confirmAction({ title: "使用内置 DLL 替换？", message: "替换前会备份当前 WeFlow 组件；请先完全退出 WeFlow（含托盘）。", confirmLabel: "替换" })) return;
  await withBusy(button, "替换中…", async () => {
    try {
      const result = await loveApi("/api/love/weflow/component/replace", { method: "POST", timeoutMs: 120000 });
      toast(result.message || "组件已替换；请重启 WeFlow 后重试。", "success");
      await checkWeFlowComponent();
    } catch (error) {
      toast(`替换失败：${error.message}`, "error");
    }
  });
}

async function rollbackWeFlowComponent(button) {
  if (!await confirmAction({ title: "回滚微信组件？", message: "会把最近一次备份的 wcdb_api.dll 放回 WeFlow 目录，并重启前请先退出 WeFlow。", confirmLabel: "回滚" })) return;
  await withBusy(button, "回滚中…", async () => {
    try {
      const result = await loveApi("/api/love/weflow/component/rollback", { method: "POST", timeoutMs: 60000 });
      toast(result.message || "已回滚。", "success");
      await checkWeFlowComponent();
    } catch (error) { toast(`回滚失败：${error.message}`, "error"); }
  });
}

const PAGE_RENDERERS = {
  memories: renderMemories,
  chat: renderChat,
  schedule: renderSchedule,
  gallery: renderGallery,
  bazi: renderBazi,
  master: renderMaster,
};
