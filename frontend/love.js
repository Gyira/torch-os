// 「恋爱系统」= 原生重做进「生活运营」的二级分区。
// 数据来自原有 love_analysis.db，由 torch 后端直接读写；这里只做界面与交互。
// 不在前端保存任何聊天内容：所有渲染都来自本机 API，页面刷新后重新读取。
import { $, api, escapeHtml, jsonBody, openDialog, confirmAction, toast } from "./core.js";

const PAGE_TITLES = {
  dashboard: "总览",
  import: "导入聊天",
  wechat: "微信同步",
  stats: "统计分析",
  analysis: "AI 深度分析",
  profile: "画像",
  journal: "恋爱日志",
  memory: "每日记忆",
  report: "报告",
  schedule: "值班日历",
  settings: "设置",
};
const CONSENT_KEY = "torch-os:love-consent-v1";
const PROJECT_KEY = "torch-os:love-project";
const MOODS = [
  ["happy", "开心", "😊"], ["love", "心动", "❤️"], ["miss", "想念", "💭"],
  ["excited", "兴奋", "🎉"], ["neutral", "平静", "😐"], ["sad", "难过", "😢"],
  ["angry", "生气", "😠"],
];
const MOOD_LABEL = Object.fromEntries(MOODS.map(([value, label, icon]) => [value, `${icon} ${label}`]));
const IMPACTS = { positive: "正向", negative: "负向", neutral: "中性" };
const SEVERITIES = { high: "高风险", medium: "中风险", low: "低风险" };

const love = {
  inited: false,
  loading: false,
  loadingProjects: false,
  page: "dashboard",
  projectId: "",
  projects: [],
  status: null,
  settings: null,
  consent: false,
  pendingFile: null,
  trend: "day",
  analysisLogs: [],
  selectedAnalysisId: "",
  analysisPoll: 0,
  lastReport: null,
  scheduleMonth: "",
  scheduleData: null,
  wechatStatus: null,
  wechatSource: null,
  wechatSessions: null,
  wechatSessionsLoading: false,
  wechatSessionsError: "",
  wechatSelectedSession: "",
  wechatPendingFile: null,
  wechatFilePreview: null,
  wechatSyncLog: null,
  wechatSyncPoll: 0,
  wechatPreviewSince: null,
  memoryDays: [],
  memoryDate: "",
  memoryDraftId: "",
  memoryDraft: null,
};

// ── 小工具 ─────────────────────────────────────────────────────────

const numberFormat = new Intl.NumberFormat("zh-CN");

function node(id) { return document.getElementById(id); }

function readStorage(key) {
  try { return window.localStorage.getItem(key); } catch { return null; }
}

function writeStorage(key, value) {
  try { window.localStorage.setItem(key, value); } catch { /* private mode */ }
}

function removeStorage(key) {
  try { window.localStorage.removeItem(key); } catch { /* ignore */ }
}

function formatInt(value) {
  const number = Number(value);
  return Number.isFinite(number) ? numberFormat.format(Math.round(number)) : "—";
}

function formatSeconds(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.round((seconds % 3600) / 60);
    return `${hours} 小时${minutes ? ` ${minutes} 分` : ""}`;
  }
  const days = Math.floor(seconds / 86400);
  const hours = Math.round((seconds % 86400) / 3600);
  return `${days} 天${hours ? ` ${hours} 小时` : ""}`;
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

// CSP 的 style-src 只有 'self'：不能写内联 style 属性。
// 用预置宽度类表达进度，既满足内容安全策略，也避免样式被浏览器丢弃。
function widthClass(value) {
  const number = Number(value);
  const step = Number.isFinite(number) ? Math.max(0, Math.min(100, Math.round(number / 5) * 5)) : 0;
  return `love-width-${step}`;
}

function wordSizeClass(weight) {
  const number = Number(weight);
  const bucket = Number.isFinite(number) ? Math.max(1, Math.min(6, Math.ceil(number * 6))) : 1;
  return `love-word-s${bucket}`;
}

function todayText() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date());
}

function currentMonthText() {
  return todayText().slice(0, 7);
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

function currentProject() {
  return love.projects.find((project) => project.id === love.projectId) || null;
}

function projectQuery(path) {
  return `/api/love/projects/${encodeURIComponent(love.projectId)}${path}`;
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

function stopAnalysisPoll() {
  if (love.analysisPoll) {
    window.clearTimeout(love.analysisPoll);
    love.analysisPoll = 0;
  }
}

function stopWechatPoll() {
  if (love.wechatSyncPoll) {
    window.clearTimeout(love.wechatSyncPoll);
    love.wechatSyncPoll = 0;
  }
}

function resetWechatSourceState() {
  love.wechatSource = null;
  love.wechatSessions = null;
  love.wechatSessionsError = "";
  love.wechatSelectedSession = "";
  love.wechatPendingFile = null;
  love.wechatFilePreview = null;
  love.wechatSyncLog = null;
  love.wechatPreviewSince = null;
  stopWechatPoll();
}

function resetMemoryState() {
  love.memoryDays = [];
  love.memoryDate = "";
  love.memoryDraftId = "";
  love.memoryDraft = null;
}

// ── 生命周期 ───────────────────────────────────────────────────────

export function initLovePane() {
  if (love.inited) return;
  love.inited = true;
  love.consent = readStorage(CONSENT_KEY) === "accepted";

  document.querySelectorAll("[data-love-page]").forEach((button) => {
    button.addEventListener("click", () => setLovePage(button.dataset.lovePage));
  });
  node("love-project-select")?.addEventListener("change", (event) => {
    love.projectId = event.target.value || "";
    if (love.projectId) writeStorage(PROJECT_KEY, love.projectId);
    stopAnalysisPoll();
    resetWechatSourceState();
    resetMemoryState();
    love.selectedAnalysisId = "";
    love.lastReport = null;
    renderLovePage();
  });
  node("love-refresh")?.addEventListener("click", () => reloadLove({ quiet: true }));
  node("love-consent-accept")?.addEventListener("click", () => {
    love.consent = true;
    writeStorage(CONSENT_KEY, "accepted");
    applyConsent();
    renderLovePage();
  });

  const content = node("love-content");
  content?.addEventListener("click", (event) => {
    Promise.resolve(handleLoveAction(event)).catch((error) => {
      toast(`操作失败：${error.message}`, "error");
    });
  });
}

export async function loadLovePane() {
  if (!love.inited) initLovePane();
  if (love.loading) return;
  love.loading = true;
  const content = node("love-content");
  if (content && !love.consent) {
    content.innerHTML = `<div class="state-message"><strong>恋爱系统</strong><p>请先阅读数据边界说明。</p></div>`;
  }
  applyConsent();
  try {
    const [status, projectData, settings] = await Promise.all([
      loveApi("/api/love/status", { timeoutMs: 20000 }),
      loveApi("/api/love/projects", { timeoutMs: 20000 }),
      loveApi("/api/love/settings", { timeoutMs: 20000 }),
    ]);
    love.status = status;
    love.settings = settings;
    love.projects = Array.isArray(projectData.projects) ? projectData.projects : [];
    chooseProject();
    syncProjectPicker();
    updateMenuStatus();
    if (love.consent) await renderLovePage();
  } catch (error) {
    if (content) content.innerHTML = errorBox(error.message);
    updateMenuStatus(error.message, "error");
  } finally {
    love.loading = false;
  }
}

async function reloadLove({ quiet = false } = {}) {
  if (love.loadingProjects) return;
  love.loadingProjects = true;
  try {
    const [status, projectData, settings] = await Promise.all([
      loveApi("/api/love/status", { timeoutMs: 20000 }),
      loveApi("/api/love/projects", { timeoutMs: 20000 }),
      loveApi("/api/love/settings", { timeoutMs: 20000 }),
    ]);
    love.status = status;
    love.settings = settings;
    love.projects = Array.isArray(projectData.projects) ? projectData.projects : [];
    chooseProject();
    syncProjectPicker();
    updateMenuStatus();
    await renderLovePage();
  } catch (error) {
    if (!quiet) toast(`刷新失败：${error.message}`, "error");
    else {
      const content = node("love-content");
      if (content) content.innerHTML = errorBox(error.message);
    }
  } finally {
    love.loadingProjects = false;
  }
}

function chooseProject() {
  const ids = love.projects.map((project) => project.id);
  const stored = readStorage(PROJECT_KEY);
  if (love.projectId && ids.includes(love.projectId)) return;
  if (stored && ids.includes(stored)) {
    love.projectId = stored;
    return;
  }
  love.projectId = ids[0] || "";
  if (love.projectId) writeStorage(PROJECT_KEY, love.projectId);
}

function syncProjectPicker() {
  const select = node("love-project-select");
  if (!select) return;
  select.replaceChildren();
  if (!love.projects.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "还没有项目";
    select.append(option);
    select.disabled = true;
    return;
  }
  select.disabled = false;
  for (const project of love.projects) {
    const option = document.createElement("option");
    option.value = project.id;
    option.textContent = `${project.name} · ${formatInt(project.message_count)} 条`;
    select.append(option);
  }
  select.value = love.projectId;
}

function updateMenuStatus(message = "", tone = "") {
  const target = node("love-menu-status");
  if (!target) return;
  if (message) {
    target.textContent = message;
    target.dataset.tone = tone;
    return;
  }
  const status = love.status;
  if (!status) {
    target.textContent = "正在读取本机数据…";
    target.dataset.tone = "";
    return;
  }
  if (!status.available) {
    target.textContent = status.error || "恋爱数据库不可用";
    target.dataset.tone = "error";
    return;
  }
  target.textContent = `${love.projects.length} 个项目 · ${formatInt(status.message_count)} 条消息 · 本机`;
  target.dataset.tone = "ok";
}

function applyConsent() {
  const overlay = node("love-consent");
  if (overlay) overlay.hidden = love.consent;
}

function setLovePage(page) {
  if (!PAGE_TITLES[page]) return;
  if (love.page !== page) {
    stopAnalysisPoll();
    stopWechatPoll();
    love.page = page;
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
  const title = node("love-title");
  if (title) title.textContent = PAGE_TITLES[love.page] || "恋爱系统";
}

async function renderLovePage() {
  stopAnalysisPoll();
  stopWechatPoll();
  renderLoveNav();
  applyConsent();
  const content = node("love-content");
  if (!content) return;
  if (!love.consent) {
    content.innerHTML = "";
    return;
  }
  if (!PAGE_TITLES[love.page]) love.page = "dashboard";
  content.innerHTML = loadingBox();
  const page = love.page;
  try {
    const renderer = PAGE_RENDERERS[page];
    if (renderer) await renderer(content);
    if (love.page !== page) return;
  } catch (error) {
    if (love.page !== page) return;
    content.innerHTML = errorBox(error.message);
  }
}

// ── 总览 ───────────────────────────────────────────────────────────

async function renderDashboard(content) {
  const project = currentProject();
  if (!project) {
    content.innerHTML = card("恋爱系统", emptyBox(
      "还没有聊天项目",
      "导入一份微信聊天记录，就能开始统计、画像、AI 分析和恋爱日志。数据会保存在本机原有数据库里。",
      '<button class="button primary" type="button" data-love-action="goto" data-page="import">导入聊天记录</button>',
    ), {
      eyebrow: "RELATIONSHIP OS",
      subtitle: "原有项目会直接出现在这里；新导入的数据继续写入同一份本机数据库。",
    });
    return;
  }
  const [overview, latest] = await Promise.all([
    loveApi(projectQuery("/stats/overview"), { timeoutMs: 45000 }),
    loveApi(projectQuery("/analysis/latest"), { timeoutMs: 45000 }),
  ]);
  if (love.page !== "dashboard") return;
  const analysis = latest?.result || null;
  const status = latest?.status || "none";
  const analysisBadge = status === "completed"
    ? `<span class="love-badge ok">已完成${analysis?.relationship_stage ? ` · ${escapeHtml(analysis.relationship_stage)}` : ""}</span>`
    : status === "running"
      ? '<span class="love-badge warn">分析中</span>'
      : status === "failed"
        ? '<span class="love-badge error">上次失败</span>'
        : '<span class="love-badge">尚未分析</span>';

  const projectRows = love.projects.map((item) => {
    const active = item.id === love.projectId;
    const last = item.latest_analysis_status === "completed" ? "已分析"
      : item.latest_analysis_status === "running" ? "分析中"
        : item.latest_analysis_status === "failed" ? "分析失败" : "未分析";
    return `<li class="love-project-row${active ? " active" : ""}">
      <button class="love-project-pick" type="button" data-love-action="switch-project" data-id="${escapeHtml(item.id)}">
        <span class="love-project-name">${escapeHtml(item.name)}</span>
        <span class="meta">${escapeHtml(item.partner_a)} × ${escapeHtml(item.partner_b)} · ${formatInt(item.message_count)} 条 · ${escapeHtml(last)}</span>
      </button>
      <button class="small-button reject" type="button" data-love-action="project-delete" data-id="${escapeHtml(item.id)}" data-name="${escapeHtml(item.name)}">删除</button>
    </li>`;
  }).join("");

  const latestJournal = Number(project.journal_count || 0);
  content.innerHTML = `
    <div class="love-stat-grid">
      <article class="love-stat"><span>总消息</span><strong>${formatInt(overview.total_messages)}</strong><small>${formatInt(overview.total_days)} 天 · 日均 ${formatInt(overview.avg_daily_messages)}</small></article>
      <article class="love-stat"><span>${escapeHtml(overview.sender_a.name)}</span><strong>${formatInt(overview.sender_a.messages)}</strong><small>${percent(overview.sender_a.ratio, 1)} 的消息</small></article>
      <article class="love-stat"><span>${escapeHtml(overview.sender_b.name)}</span><strong>${formatInt(overview.sender_b.messages)}</strong><small>${percent(overview.sender_b.ratio, 1)} 的消息</small></article>
      <article class="love-stat"><span>恋爱日志</span><strong>${formatInt(latestJournal)}</strong><small>${analysisBadge}</small></article>
    </div>
    <div class="love-dashboard-grid">
      ${card("这段关系", `
        <dl class="love-facts">
          <div><dt>项目</dt><dd>${escapeHtml(project.name)}</dd></div>
          <div><dt>对话</dt><dd>${escapeHtml(project.partner_a)} × ${escapeHtml(project.partner_b)}</dd></div>
          <div><dt>时间范围</dt><dd>${formatDate(overview.date_range_start)} ~ ${formatDate(overview.date_range_end)}</dd></div>
          <div><dt>最近分析</dt><dd>${analysisBadge}${latest?.completed_at ? ` · ${formatDateTime(latest.completed_at)}` : ""}</dd></div>
        </dl>
        <div class="love-inline-actions">
          <button class="button primary" type="button" data-love-action="goto" data-page="analysis">打开 AI 分析</button>
          <button class="button secondary" type="button" data-love-action="goto" data-page="stats">看统计</button>
          <button class="button secondary" type="button" data-love-action="goto" data-page="journal">翻恋爱日志</button>
        </div>`, {
        eyebrow: "CURRENT PROJECT",
        subtitle: "所有页面都围绕当前选中的项目工作。",
      })}
      ${card("项目", `<ul class="love-project-list">${projectRows}</ul>`, {
        eyebrow: "PROJECTS",
        subtitle: "切换项目只改变当前视图，不会复制或移动数据。",
        actions: '<button class="small-button" type="button" data-love-action="goto" data-page="import">导入新聊天</button>',
      })}
    </div>
    <div class="love-tip-strip">
      <span>🔒</span>
      <p>聊天、画像与日志只保存在当前个人数据库的 <code>.torch-os/love/</code> 里，不参与普通导出；拷贝整个个人数据库即可迁移。AI 分析只发送脱敏后的内容。</p>
    </div>`;
}

// ── 导入 ───────────────────────────────────────────────────────────

async function renderImport(content) {
  content.innerHTML = card("导入聊天记录", `
    <div class="love-import-zone" id="love-import-zone">
      <input id="love-file" type="file" accept=".txt,text/plain" hidden />
      <button class="love-import-pick" type="button" data-love-action="pick-file">
        <span class="love-import-icon" aria-hidden="true">↥</span>
        <strong>选择微信聊天记录 .txt</strong>
        <small>支持 UTF-8 / GBK / UTF-16；单文件不超过 40MB。文件只在本机解析。</small>
      </button>
    </div>
    <div id="love-import-preview" class="love-import-preview" hidden></div>
    <div class="love-tip-strip"><span>i</span><p>导入会新建一个独立项目，原有项目和消息不会被覆盖。系统会自动合并同一文件内的完全重复行。</p></div>`, {
    eyebrow: "IMPORT",
    subtitle: "和原来的「恋爱分析助手」使用同一套解析规则；导入后的数据直接写入本机原有数据库。",
  });
  node("love-file")?.addEventListener("change", handleImportFile);
}

async function handleImportFile(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  if (file.size > 40 * 1024 * 1024) {
    toast("文件超过 40MB 上限。", "error");
    event.target.value = "";
    return;
  }
  love.pendingFile = file;
  const preview = node("love-import-preview");
  if (!preview) return;
  preview.hidden = false;
  preview.innerHTML = loadingBox(`正在解析 ${file.name}…`);
  const form = new FormData();
  form.append("file", file);
  try {
    const data = await loveApi("/api/love/import/preview", { method: "POST", body: form, timeoutMs: 180000 });
    const lines = (data.lines || []).map((line) => escapeHtml(line)).join("\n");
    preview.innerHTML = `
      <div class="love-preview-head">
        <div><span class="eyebrow">PREVIEW</span><h3>${escapeHtml(file.name)}</h3></div>
        <span class="love-badge ${data.message_count ? "ok" : "error"}">${formatInt(data.message_count)} 条可导入消息</span>
      </div>
      <div class="love-preview-stats">
        <span>编码 <strong>${escapeHtml(data.encoding || "—")}</strong></span>
        <span>发送者 <strong>${(data.senders || []).map(escapeHtml).join(" / ") || "未识别"}</strong></span>
        <span>时间范围 <strong>${formatDate(data.date_range_start)} ~ ${formatDate(data.date_range_end)}</strong></span>
        <span>行数 <strong>${formatInt(data.line_count)}</strong></span>
      </div>
      <label class="field"><span>项目名称（可选）</span><input id="love-import-name" maxlength="200" placeholder="留空则自动按对话双方和日期命名" /></label>
      <details class="love-preview-lines"><summary>查看前 30 行原文</summary><pre>${lines || "（空）"}</pre></details>
      <div class="love-inline-actions">
        <button class="button primary" type="button" data-love-action="import-confirm">确认导入</button>
        <button class="button secondary" type="button" data-love-action="import-reset">换一个文件</button>
      </div>`;
    if (!data.message_count) {
      toast("没有解析到消息，请确认是聊天记录 TXT。", "warning");
    }
  } catch (error) {
    preview.innerHTML = errorBox(error.message);
  }
}

async function confirmImport(button) {
  if (!love.pendingFile) return;
  const nameInput = node("love-import-name");
  const form = new FormData();
  form.append("file", love.pendingFile);
  form.append("project_name", nameInput?.value?.trim() || "");
  await withBusy(button, "正在导入…", async () => {
    try {
      const result = await loveApi("/api/love/import", { method: "POST", body: form, timeoutMs: 240000 });
      love.pendingFile = null;
      toast(`已导入 ${formatInt(result.message_count)} 条消息。`, "success");
      love.projectId = result.project_id;
      writeStorage(PROJECT_KEY, result.project_id);
      await reloadLove({ quiet: true });
      love.page = "stats";
      await renderLovePage();
    } catch (error) {
      toast(`导入失败：${error.message}`, "error");
    }
  });
}

// ── 统计 ───────────────────────────────────────────────────────────

async function renderStats(content) {
  const project = currentProject();
  if (!project) {
    content.innerHTML = emptyBox("还没有项目", "先导入聊天记录，才能做统计。",
      '<button class="button primary" type="button" data-love-action="goto" data-page="import">导入聊天</button>');
    return;
  }
  const [overview, trends, timing, media, keywords] = await Promise.all([
    loveApi(projectQuery("/stats/overview"), { timeoutMs: 45000 }),
    loveApi(projectQuery(`/stats/trends?granularity=${encodeURIComponent(love.trend)}`), { timeoutMs: 45000 }),
    loveApi(projectQuery("/stats/timing"), { timeoutMs: 45000 }),
    loveApi(projectQuery("/stats/media"), { timeoutMs: 45000 }),
    loveApi(projectQuery("/stats/keywords?top_n=100"), { timeoutMs: 60000 }),
  ]);
  if (love.page !== "stats") return;

  const distribution = timing.reply_distribution || {};
  const distributionMax = Math.max(1, ...Object.values(distribution).map(Number));
  const distributionHtml = Object.entries(distribution).map(([label, count]) => `
    <li><span>${escapeHtml(label)}</span><div class="love-meter"><i class="${widthClass(Number(count) / distributionMax * 100)}"></i></div><strong>${formatInt(count)}</strong></li>`).join("");

  const mediaTotal = Number(media.text_count || 0) + Number(media.sticker_count || 0) + Number(media.image_count || 0);
  const mediaSegments = [
    ["文字", media.text_count, "text"],
    ["表情", media.sticker_count, "sticker"],
    ["图片", media.image_count, "image"],
  ].map(([label, count, kind]) => {
    const share = mediaTotal ? Number(count) / mediaTotal * 100 : 0;
    return `<span class="love-media-seg ${kind} ${widthClass(share)}" title="${escapeHtml(label)} ${formatInt(count)} 条">${share >= 8 ? escapeHtml(label) : ""}</span>`;
  }).join("");

  const words = (keywords.words || []);
  const wordHtml = words.map((word) => {
    const weight = Number(word.weight) || 0;
    return `<span class="love-word ${wordSizeClass(weight)}" title="${escapeHtml(word.word)} · ${formatInt(word.count)} 次">${escapeHtml(word.word)}</span>`;
  }).join("");

  content.innerHTML = `
    <div class="love-stat-grid">
      <article class="love-stat"><span>总消息</span><strong>${formatInt(overview.total_messages)}</strong><small>${formatInt(overview.total_days)} 天 · 日均 ${formatInt(overview.avg_daily_messages)}</small></article>
      <article class="love-stat"><span>${escapeHtml(overview.sender_a.name)} 发起</span><strong>${percent(overview.sender_a.ratio, 1)}</strong><small>${formatInt(overview.sender_a.messages)} 条</small></article>
      <article class="love-stat"><span>${escapeHtml(overview.sender_b.name)} 发起</span><strong>${percent(overview.sender_b.ratio, 1)}</strong><small>${formatInt(overview.sender_b.messages)} 条</small></article>
      <article class="love-stat"><span>平均回复间隔</span><strong>${formatSeconds(timing.avg_reply_interval_seconds)}</strong><small>中位数 ${formatSeconds(timing.median_reply_interval_seconds)}</small></article>
    </div>
    <div class="love-stats-grid">
      ${card("消息趋势", `
        <div class="love-segmented" role="group" aria-label="趋势粒度">
          ${[["day", "按天"], ["week", "按周"], ["month", "按月"]].map(([value, label]) =>
            `<button type="button" class="love-seg${love.trend === value ? " active" : ""}" data-love-action="trend" data-value="${value}">${label}</button>`).join("")}
        </div>
        ${trendChart(trends, overview)}
      `, { eyebrow: "TRENDS", subtitle: `${escapeHtml(overview.sender_a.name)} 与 ${escapeHtml(overview.sender_b.name)} 的消息量变化。` })}
      ${card("主动与回应", `
        <dl class="love-facts">
          <div><dt>平均回复间隔</dt><dd>${formatSeconds(timing.avg_reply_interval_seconds)}</dd></div>
          <div><dt>回复中位数</dt><dd>${formatSeconds(timing.median_reply_interval_seconds)}</dd></div>
          <div><dt>最长沉默</dt><dd>${formatSeconds(timing.longest_silence_seconds)}</dd></div>
          <div><dt>沉默区间</dt><dd class="meta">${formatDateTime(timing.longest_silence_start)} ~ ${formatDateTime(timing.longest_silence_end)}</dd></div>
        </dl>
        <h3 class="love-subhead">回复间隔分布</h3>
        <ul class="love-meter-list">${distributionHtml || '<li class="meta">消息太少，暂时无法计算。</li>'}</ul>
      `, { eyebrow: "TIMING", subtitle: "只统计非系统消息；回复间隔按连续不同发送者计算。" })}
      ${card("消息形态", `
        <div class="love-media-bar">${mediaSegments}</div>
        <div class="love-media-legend">
          <span><i class="text"></i>文字 ${formatInt(media.text_count)}</span>
          <span><i class="sticker"></i>表情 ${formatInt(media.sticker_count)} · ${percent(media.sticker_ratio, 1)}</span>
          <span><i class="image"></i>图片 ${formatInt(media.image_count)} · ${percent(media.image_ratio, 1)}</span>
        </div>
      `, { eyebrow: "MEDIA", subtitle: "按微信导出里的常见标记分类，只是近似统计。", className: "love-span-2" })}
      ${card("高频词", `
        <div class="love-wordcloud">${wordHtml || '<p class="meta">没有足够的文本词频。</p>'}</div>
        <p class="field-help">使用本地 jieba 分词与停用词过滤；词频不代表关系质量。引擎：${escapeHtml(keywords.engine || "—")}</p>
      `, { eyebrow: "WORDS", subtitle: "聊天里出现最多的词，帮你回看共同话题。", className: "love-span-2" })}
    </div>
    <div class="love-inline-actions love-page-actions">
      <button class="button primary" type="button" data-love-action="goto" data-page="analysis">进入 AI 深度分析</button>
      <button class="button secondary" type="button" data-love-action="goto" data-page="profile">查看画像</button>
      <button class="button secondary" type="button" data-love-action="goto" data-page="journal">恋爱日志</button>
    </div>`;
}

function trendChart(trends, overview) {
  const data = trends?.data || [];
  if (!data.length) return '<div class="love-empty-small">还没有可供绘制的消息。</div>';
  const width = 760;
  const height = 260;
  const padding = { left: 38, right: 14, top: 16, bottom: 28 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const max = Math.max(1, ...data.map((item) => Number(item.total) || 0));
  const step = plotWidth / data.length;
  const barWidth = Math.max(1, Math.min(12, step * 0.68));
  const grid = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
    const y = padding.top + plotHeight * (1 - ratio);
    return `<line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" class="love-grid-line" />
      <text x="${padding.left - 7}" y="${y + 3}" class="love-axis-text" text-anchor="end">${formatInt(max * ratio)}</text>`;
  }).join("");
  const bars = data.map((item, index) => {
    const x = padding.left + index * step + (step - barWidth) / 2;
    const a = Number(item.sender_a) || 0;
    const b = Number(item.sender_b) || 0;
    const total = a + b;
    const totalHeight = total / max * plotHeight;
    const aHeight = total ? a / total * totalHeight : 0;
    const bHeight = total ? b / total * totalHeight : 0;
    const y = padding.top + plotHeight - totalHeight;
    const title = `${item.date}：共 ${total} 条（${overview.sender_a.name} ${a} / ${overview.sender_b.name} ${b}）`;
    return `<g><title>${escapeHtml(title)}</title>
      <rect x="${x.toFixed(2)}" y="${(y + bHeight).toFixed(2)}" width="${barWidth.toFixed(2)}" height="${aHeight.toFixed(2)}" class="love-bar-a" />
      <rect x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${barWidth.toFixed(2)}" height="${bHeight.toFixed(2)}" class="love-bar-b" />
    </g>`;
  }).join("");
  const labelEvery = Math.max(1, Math.ceil(data.length / 8));
  const labels = data.map((item, index) => index % labelEvery === 0
    ? `<text x="${(padding.left + index * step + step / 2).toFixed(2)}" y="${height - 8}" class="love-axis-text" text-anchor="middle">${escapeHtml(String(item.date).slice(5))}</text>`
    : "").join("");
  return `<div class="love-chart-wrap">
    <svg class="love-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="消息趋势图">
      ${grid}${bars}${labels}
    </svg>
    <div class="love-legend">
      <span><i class="love-bar-a"></i>${escapeHtml(overview.sender_a.name)}</span>
      <span><i class="love-bar-b"></i>${escapeHtml(overview.sender_b.name)}</span>
      <span class="meta">最高 ${formatInt(max)} 条 / 周期</span>
    </div>
  </div>`;
}

// ── AI 分析 ────────────────────────────────────────────────────────

async function renderAnalysis(content) {
  const project = currentProject();
  if (!project) {
    content.innerHTML = emptyBox("还没有项目", "先导入聊天记录，才能让 AI 分析。",
      '<button class="button primary" type="button" data-love-action="goto" data-page="import">导入聊天</button>');
    return;
  }
  const logsData = await loveApi(projectQuery("/analysis/logs"), { timeoutMs: 45000 });
  if (love.page !== "analysis") return;
  love.analysisLogs = Array.isArray(logsData.logs) ? logsData.logs : [];
  const active = love.analysisLogs.find((log) => log.id === love.selectedAnalysisId)
    || love.analysisLogs[0]
    || null;
  if (active && !love.selectedAnalysisId) love.selectedAnalysisId = active.id;
  const hasKey = Boolean(love.settings?.api_key_configured);

  const keyCard = hasKey
    ? `<span class="love-badge ok">API Key 已配置</span>`
    : `<span class="love-badge error">未配置 API Key</span>
       <button class="text-button" type="button" data-love-action="goto" data-page="settings">去配置</button>`;

  let body = "";
  if (active?.status === "running") {
    body = `<div class="love-analysis-running">
      <div class="love-spinner" aria-hidden="true"></div>
      <div><strong>正在分析 ${escapeHtml(project.name)}</strong>
      <p>已开始 ${formatDateTime(active.created_at)}；通常需要 30–120 秒。</p>
      <p class="meta">页面可以切走，分析在本地服务里继续。</p></div>
    </div>`;
    scheduleAnalysisPoll(active.id);
  } else if (active?.status === "failed") {
    body = `<div class="state-message error-state"><strong>这次分析没有完成</strong><p>${escapeHtml(active.error || "未知错误")}</p>
      ${hasKey ? '<button class="text-button" type="button" data-love-action="run-analysis">重新分析</button>' : ""}</div>`;
  } else if (active?.result) {
    body = analysisResultHtml(active.result, active);
  } else if (!hasKey) {
    body = emptyBox(
      "还需要一个 DeepSeek API Key",
      "分析会先把聊天脱敏，再发送给你配置的 DeepSeek 接口。Key 加密保存在本机，不进仓库、不进日志。",
      '<button class="button primary" type="button" data-love-action="goto" data-page="settings">打开设置</button>',
    );
  } else {
    body = emptyBox(
      "还没有分析记录",
      "第一次分析会读取最近 8000 条消息（更早的会均匀采样），脱敏后发送给 DeepSeek。",
      '<button class="button primary" type="button" data-love-action="run-analysis">开始第一次分析</button>',
    );
  }

  const history = historyHtml(active);
  content.innerHTML = `
    ${card("DeepSeek 深度分析", `
      <div class="love-analysis-toolbar">
        <div class="love-analysis-meta">${keyCard}<span class="meta">模型 ${escapeHtml(love.settings?.api_model || "deepseek-chat")}</span></div>
        <div class="love-inline-actions">
          ${hasKey ? '<button class="button primary" type="button" data-love-action="run-analysis">重新分析</button>' : ""}
        </div>
      </div>
      <p class="field-help love-analysis-warning">点击分析会把脱敏后的聊天内容发送到 DeepSeek。姓名会替换为「用户A / 用户B」，电话、证件、邮箱、链接、银行卡号会被隐藏；原始记录不出本机。</p>
      ${body}
      ${history}
    `, {
    eyebrow: "AI ANALYSIS",
    subtitle: "分析结果会保存到本机数据库；提取出的对方特质会合并进画像，但不会覆盖已锁定的条目。",
    actions: `<button class="small-button" type="button" data-love-action="goto" data-page="analysis">刷新记录</button>`,
  })}`;
}

function scheduleAnalysisPoll(logId) {
  stopAnalysisPoll();
  const tick = async () => {
    const pane = node("love-view");
    if (love.page !== "analysis" || (pane && pane.hidden)) {
      love.analysisPoll = 0;
      return;
    }
    try {
      const status = await loveApi(projectQuery(`/analysis/status/${encodeURIComponent(logId)}`), { timeoutMs: 20000 });
      if (["completed", "failed"].includes(status.status)) {
        stopAnalysisPoll();
        if (status.status === "completed") toast("AI 分析完成。", "success");
        else toast("AI 分析失败，请查看原因。", "error");
        await reloadLove({ quiet: true });
        return;
      }
    } catch {
      // 网络抖动不终止轮询，下一轮再试。
    }
    love.analysisPoll = window.setTimeout(tick, 2500);
  };
  love.analysisPoll = window.setTimeout(tick, 1200);
}

function historyHtml(active) {
  if (!love.analysisLogs.length) return "";
  const rows = love.analysisLogs.slice(0, 8).map((log) => {
    const isActive = active?.id === log.id;
    const label = log.status === "completed" ? "已完成"
      : log.status === "running" ? "分析中"
        : log.status === "failed" ? "失败" : log.status;
    return `<li class="${isActive ? "active" : ""}">
      <button type="button" class="love-log-link" data-love-action="analysis-log" data-id="${escapeHtml(log.id)}">
        <span>${formatDateTime(log.created_at)}</span>
        <span class="love-badge ${log.status === "completed" ? "ok" : log.status === "failed" ? "error" : "warn"}">${escapeHtml(label)}</span>
        <span class="meta">${formatInt(log.prompt_tokens)} + ${formatInt(log.completion_tokens)} tokens</span>
      </button>
    </li>`;
  }).join("");
  return `<div class="love-log-list"><h3 class="love-subhead">分析记录</h3><ul>${rows}</ul></div>`;
}

function analysisResultHtml(result, meta) {
  const tone = result.emotional_tone || {};
  const patterns = result.communication_patterns || {};
  const events = (result.key_events || []).map((event) => {
    const impact = ["positive", "negative", "neutral"].includes(event.impact) ? event.impact : "neutral";
    return `<li class="love-event impact-${impact}">
      <span class="love-event-date">${escapeHtml(event.date || "")}</span>
      <p>${escapeHtml(event.description || "")}</p>
      <span class="love-badge">${escapeHtml(IMPACTS[impact] || impact)}</span>
    </li>`;
  }).join("");
  const risks = (result.risk_warnings || []).map((risk) => {
    const severity = ["high", "medium", "low"].includes(risk.severity) ? risk.severity : "low";
    return `<li class="love-risk risk-${severity}">
      <div><span class="love-badge ${severity === "high" ? "error" : severity === "medium" ? "warn" : "ok"}">${escapeHtml(SEVERITIES[severity])}</span>
      <strong>${escapeHtml(risk.aspect || "")}</strong></div>
      <p>${escapeHtml(risk.detail || "")}</p>
    </li>`;
  }).join("");
  const advice = (result.personalized_advice || []).map((item) => `<li>${escapeHtml(String(item))}</li>`).join("");
  const traits = (result.partner_traits || []).map((trait) => `
    <li><span>${escapeHtml(trait.trait || "")}</span><p>${escapeHtml(trait.value || "")}</p>
    <small>置信度 ${percent(trait.confidence, 0)}${trait.evidence ? ` · ${escapeHtml(String(trait.evidence).slice(0, 120))}` : ""}</small></li>`).join("");
  return `
    <div class="love-analysis-hero">
      <div><span class="eyebrow">RELATIONSHIP STAGE</span><strong>${escapeHtml(result.relationship_stage || "未给出")}</strong></div>
      <div class="meta">分析于 ${formatDateTime(meta.completed_at)} · ${escapeHtml(meta.model || "deepseek-chat")}</div>
    </div>
    <div class="love-analysis-tone">
      <div class="love-tone-card"><span>整体情绪</span><strong>${escapeHtml(tone.overall || "—")}</strong></div>
      <div class="love-tone-card"><span>情绪波动</span><strong>${percent(tone.fluctuation, 0)}</strong>
        <div class="love-meter"><i class="${widthClass(Number(tone.fluctuation || 0) * 100)}"></i></div></div>
      <div class="love-tone-card"><span>情绪趋势</span><strong>${escapeHtml(tone.trend || "—")}</strong></div>
    </div>
    <div class="love-analysis-section">
      <h3>沟通模式</h3>
      <dl class="love-facts">
        <div><dt>主动度</dt><dd>${escapeHtml(patterns.initiative_balance || "—")}</dd></div>
        <div><dt>回应风格</dt><dd>${escapeHtml(patterns.response_style || "—")}</dd></div>
        <div><dt>主要话题</dt><dd>${(patterns.key_topics || []).map(escapeHtml).join("、") || "—"}</dd></div>
      </dl>
    </div>
    ${events ? `<div class="love-analysis-section"><h3>关键事件</h3><ul class="love-event-list">${events}</ul></div>` : ""}
    ${risks ? `<div class="love-analysis-section"><h3>需要注意</h3><ul class="love-risk-list">${risks}</ul></div>` : ""}
    ${advice ? `<div class="love-analysis-section"><h3>给你的建议</h3><ol class="love-advice">${advice}</ol></div>` : ""}
    ${traits ? `<div class="love-analysis-section"><h3>提取到的对方特质</h3><ul class="love-trait-mini">${traits}</ul><p class="field-help">这些条目已合并进「画像」；已锁定的特质不会被覆盖。</p></div>` : ""}`;
}

async function runAnalysis(button) {
  if (!love.projectId) return;
  const confirmed = await confirmAction({
    title: "开始一次 AI 深度分析？",
    message: "这次操作会把脱敏后的聊天内容发送给您配置的 DeepSeek 接口，可能会产生 token 费用。原始聊天记录仍只留在本机。",
    confirmLabel: "开始分析",
    cancelLabel: "先算了",
  });
  if (!confirmed) return;
  await withBusy(button, "正在提交…", async () => {
    try {
      const result = await loveApi(projectQuery("/analysis"), {
        method: "POST", body: JSON.stringify({ analysis_type: "deep" }), timeoutMs: 20000,
      });
      love.selectedAnalysisId = result.log_id;
      toast("已开始分析，页面会自动更新。", "info");
      await renderLovePage();
    } catch (error) {
      toast(`无法开始分析：${error.message}`, "error");
    }
  });
}

// ── 画像 ───────────────────────────────────────────────────────────

async function renderProfile(content) {
  const project = currentProject();
  if (!project) {
    content.innerHTML = emptyBox("还没有项目", "先导入聊天记录，AI 分析后才能形成画像。",
      '<button class="button primary" type="button" data-love-action="goto" data-page="import">导入聊天</button>');
    return;
  }
  const profile = await loveApi(projectQuery("/profile"), { timeoutMs: 45000 });
  if (love.page !== "profile") return;
  const traits = [...(profile.traits || [])].sort((a, b) => Number(Boolean(b.locked)) - Number(Boolean(a.locked))
    || Number(b.confidence || 0) - Number(a.confidence || 0));
  const lockedCount = traits.filter((trait) => trait.locked).length;
  const rows = traits.map((trait) => {
    const key = String(trait.trait || "");
    return `<article class="love-trait ${trait.locked ? "locked" : ""}">
      <div class="love-trait-head">
        <div><h3>${trait.locked ? "🔒" : "🔍"} ${escapeHtml(key)}</h3>
          <span class="meta">${trait.locked ? "已确认，不会被 AI 覆盖" : `置信度 ${percent(trait.confidence, 0)}`}${trait.update_count ? ` · 更新 ${trait.update_count} 次` : ""}</span></div>
        <div class="love-trait-actions">
          <button class="small-button" type="button" data-love-action="profile-lock" data-key="${escapeHtml(key)}" data-locked="${trait.locked ? "0" : "1"}">${trait.locked ? "解锁" : "锁定"}</button>
          <button class="small-button" type="button" data-love-action="profile-edit" data-key="${escapeHtml(key)}">编辑</button>
          <button class="small-button reject" type="button" data-love-action="profile-delete" data-key="${escapeHtml(key)}">删除</button>
        </div>
      </div>
      <p>${escapeHtml(trait.value || "")}</p>
      ${trait.evidence ? `<details class="love-evidence"><summary>依据</summary><p>${escapeHtml(trait.evidence)}</p></details>` : ""}
      ${Array.isArray(trait.alternate_values) && trait.alternate_values.length ? `<p class="meta">已锁定后收到 ${trait.alternate_values.length} 条备选发现，可在数据库里追溯。</p>` : ""}
    </article>`;
  }).join("");
  content.innerHTML = `
    ${card(`${profile.partner_name || project.partner_b} 的画像`, `
      <div class="love-profile-summary">
        <span class="love-badge ok">${lockedCount} 条已确认</span>
        <span class="love-badge">${traits.length - lockedCount} 条 AI 推测</span>
        <button class="small-button" type="button" data-love-action="profile-add">添加特质</button>
      </div>
      <p class="field-help">锁定的条目会作为「已确认信息」进入下一次 AI 分析；未锁定的条目可能被置信度更高的新分析替换。</p>
      <div class="love-trait-list">${rows || emptyBox("还没有画像", "运行一次 AI 深度分析，或手动添加一条特质。")}</div>
    `, {
      eyebrow: "PROFILE",
      subtitle: "画像来自 AI 提取和你的手动修正；它不是对任何人的定论。",
    })}`;
}

async function openTraitDialog(mode, traitKey = "") {
  const project = currentProject();
  if (!project) return;
  const profile = await loveApi(projectQuery("/profile"), { timeoutMs: 30000 });
  const existing = (profile.traits || []).find((trait) => trait.trait === traitKey);
  const title = mode === "edit" ? "编辑特质" : "添加特质";
  openDialog(title, `
    <form id="love-trait-form" class="love-dialog-form">
      <div class="field"><label for="love-trait-key">特质</label>
        <input id="love-trait-key" name="trait" maxlength="120" required value="${escapeHtml(existing?.trait || "")}" ${existing ? "readonly" : ""} placeholder="例如：沟通风格、情感需求、雷区" /></div>
      <div class="field"><label for="love-trait-value">描述</label>
        <textarea id="love-trait-value" name="value" rows="3" maxlength="2000" required placeholder="具体、可验证的描述">${escapeHtml(existing?.value || "")}</textarea></div>
      <div class="field"><label for="love-trait-evidence">依据（可选）</label>
        <textarea id="love-trait-evidence" name="evidence" rows="2" maxlength="2000" placeholder="来自哪些对话或观察">${escapeHtml(existing?.evidence || "")}</textarea></div>
      <p class="field-help">保存后可以随时锁定，锁定后 AI 不会覆盖这条。</p>
      <div class="form-actions"><span id="love-trait-feedback" class="status-message" role="status"></span>
        <div class="love-inline-actions">
          <button class="button secondary" type="button" id="love-trait-cancel">取消</button>
          <button class="button primary" type="submit">保存</button>
        </div>
      </div>
    </form>`);
  node("love-trait-cancel")?.addEventListener("click", () => node("detail-dialog")?.close());
  node("love-trait-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    const key = form.elements.trait.value.trim();
    const value = form.elements.value.value.trim();
    if (!key || !value) return;
    await withBusy(submit, "保存中…", async () => {
      try {
        if (mode === "edit") {
          await loveApi(projectQuery(`/profile/traits/${encodeURIComponent(key)}`), {
            method: "PUT",
            body: JSON.stringify({ value, evidence: form.elements.evidence.value.trim() }),
            timeoutMs: 30000,
          });
        } else {
          await loveApi(projectQuery("/profile/traits"), {
            method: "POST",
            body: JSON.stringify({ trait: key, value, evidence: form.elements.evidence.value.trim() || "手动添加" }),
            timeoutMs: 30000,
          });
        }
        node("detail-dialog")?.close();
        toast("画像已更新。", "success");
        await renderLovePage();
      } catch (error) {
        const feedback = node("love-trait-feedback");
        if (feedback) feedback.textContent = error.message;
        toast(`保存失败：${error.message}`, "error");
      }
    });
  });
}

async function toggleTraitLock(button) {
  const key = button.dataset.key || "";
  const locked = button.dataset.locked === "1";
  await withBusy(button, "处理中…", async () => {
    try {
      await loveApi(projectQuery(`/profile/traits/${encodeURIComponent(key)}/lock`), {
        method: "PUT", body: JSON.stringify({ locked }), timeoutMs: 30000,
      });
      toast(locked ? "已锁定，AI 不会再覆盖。" : "已解锁。", "success");
      await renderLovePage();
    } catch (error) {
      toast(`操作失败：${error.message}`, "error");
    }
  });
}

async function deleteTrait(button) {
  const key = button.dataset.key || "";
  const confirmed = await confirmAction({
    title: "删除这条画像？", message: `将删除「${key}」。此操作只影响本机画像，不会删除聊天记录。`,
    confirmLabel: "删除", danger: true,
  });
  if (!confirmed) return;
  try {
    await loveApi(projectQuery(`/profile/traits/${encodeURIComponent(key)}`), { method: "DELETE", timeoutMs: 30000 });
    toast("画像条目已删除。", "success");
    await renderLovePage();
  } catch (error) {
    toast(`删除失败：${error.message}`, "error");
  }
}

// ── 恋爱日志 ───────────────────────────────────────────────────────

async function renderJournal(content) {
  const project = currentProject();
  if (!project) {
    content.innerHTML = emptyBox("还没有项目", "导入聊天记录后，才能生成或手写恋爱日志。",
      '<button class="button primary" type="button" data-love-action="goto" data-page="import">导入聊天</button>');
    return;
  }
  const data = await loveApi(projectQuery("/journal"), { timeoutMs: 45000 });
  if (love.page !== "journal") return;
  const entries = Array.isArray(data.entries) ? data.entries : [];
  const groups = new Map();
  for (const entry of entries) {
    const month = String(entry.date || "").slice(0, 7);
    if (!groups.has(month)) groups.set(month, []);
    groups.get(month).push(entry);
  }
  const groupsHtml = [...groups.entries()].map(([month, items]) => `
    <section class="love-journal-month">
      <h3>${escapeHtml(formatMonth(month))}<span>${items.length} 篇</span></h3>
      ${items.map((entry) => journalEntryHtml(entry)).join("")}
    </section>`).join("");
  content.innerHTML = `
    ${card("恋爱日志", `
      <div class="love-journal-toolbar">
        <div class="love-inline-actions">
          <button class="button primary" type="button" data-love-action="journal-new">写日志</button>
          <button class="button secondary" type="button" data-love-action="journal-generate-all">AI 批量补日记</button>
          <a class="button secondary" href="/api/love/projects/${encodeURIComponent(love.projectId)}/journal/export" download>导出 TXT</a>
        </div>
        <span class="meta">共 ${formatInt(entries.length)} 篇 · 保存在本机数据库</span>
      </div>
      <div class="love-journal-body">${groupsHtml || emptyBox("还没有日志", "点「写日志」手写，或让 AI 根据当天聊天生成一版再改。")}</div>
    `, {
      eyebrow: "JOURNAL",
      subtitle: "日志与聊天项目关联；AI 只在你点击生成时读取当天的聊天。",
    })}`;
}

function journalEntryHtml(entry) {
  return `<article class="love-journal-entry">
    <div class="love-journal-date"><strong>${escapeHtml(String(entry.date || "").slice(8, 10))}</strong><span>${escapeHtml(weekday(entry.date))}</span></div>
    <div class="love-journal-content">
      <div class="love-journal-head">
        <div>${entry.mood ? `<span class="love-badge">${escapeHtml(MOOD_LABEL[entry.mood] || entry.mood)}</span>` : '<span class="meta">未标记心情</span>'}
          <span class="meta">${formatDateTime(entry.updated_at || entry.created_at)}</span></div>
        <div class="love-trait-actions">
          <button class="small-button" type="button" data-love-action="journal-edit" data-id="${escapeHtml(entry.id)}">编辑</button>
          <button class="small-button reject" type="button" data-love-action="journal-delete" data-id="${escapeHtml(entry.id)}">删除</button>
        </div>
      </div>
      <p class="love-journal-text">${escapeHtml(entry.content || "")}</p>
    </div>
  </article>`;
}

async function openJournalDialog(mode, entryId = "") {
  const project = currentProject();
  if (!project) return;
  let entry = null;
  if (mode === "edit") {
    const data = await loveApi(projectQuery("/journal"), { timeoutMs: 30000 });
    entry = (data.entries || []).find((item) => item.id === entryId);
    if (!entry) {
      toast("日志不存在，请刷新后重试。", "error");
      return;
    }
  }
  const moodOptions = MOODS.map(([value, label, icon]) =>
    `<option value="${value}" ${entry?.mood === value ? "selected" : ""}>${icon} ${label}</option>`).join("");
  openDialog(mode === "edit" ? "编辑恋爱日志" : "写恋爱日志", `
    <form id="love-journal-form" class="love-dialog-form">
      <div class="form-grid">
        <div class="field"><label for="love-journal-date">日期</label>
          <input id="love-journal-date" name="date" type="date" required value="${escapeHtml(entry?.date || todayText())}" ${mode === "edit" ? "readonly" : ""} /></div>
        <div class="field"><label for="love-journal-mood">心情</label>
          <select id="love-journal-mood" name="mood"><option value="">不标记</option>${moodOptions}</select></div>
      </div>
      <div class="field"><label for="love-journal-content">正文</label>
        <textarea id="love-journal-content" name="content" rows="9" maxlength="5000" required placeholder="今天发生了什么、当时怎么想…">${escapeHtml(entry?.content || "")}</textarea></div>
      <div class="love-journal-ai">
        <button class="button secondary" type="button" id="love-journal-generate">用当天聊天生成一版</button>
        <span id="love-journal-ai-feedback" class="status-message" role="status"></span>
      </div>
      <p class="field-help">AI 生成会覆盖正文输入框，可先保存再改。不会自动保存，直到你点「保存」。</p>
      <div class="form-actions"><span id="love-journal-feedback" class="status-message" role="status"></span>
        <div class="love-inline-actions">
          <button class="button secondary" type="button" id="love-journal-cancel">取消</button>
          <button class="button primary" type="submit">保存</button>
        </div>
      </div>
    </form>`);
  node("love-journal-cancel")?.addEventListener("click", () => node("detail-dialog")?.close());
  node("love-journal-generate")?.addEventListener("click", async (event) => {
    const day = node("love-journal-date")?.value;
    if (!day) { toast("先选日期。", "warning"); return; }
    await withBusy(event.currentTarget, "生成中…", async () => {
      const feedback = node("love-journal-ai-feedback");
      if (feedback) feedback.textContent = "正在读取当天的聊天并调用 DeepSeek…";
      try {
        const result = await loveApi(projectQuery("/journal/generate"), {
          method: "POST", body: JSON.stringify({ date: day }), timeoutMs: 180000,
        });
        const textarea = node("love-journal-content");
        if (textarea) textarea.value = result.content || "";
        if (feedback) feedback.textContent = "已生成，可修改后保存。";
      } catch (error) {
        if (feedback) feedback.textContent = error.message;
        toast(`生成失败：${error.message}`, "error");
      }
    });
  });
  node("love-journal-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    const payload = {
      content: form.elements.content.value.trim(),
      mood: form.elements.mood.value || null,
    };
    if (!payload.content) return;
    await withBusy(submit, "保存中…", async () => {
      try {
        if (mode === "edit") {
          await loveApi(projectQuery(`/journal/${encodeURIComponent(entry.id)}`), {
            method: "PUT", body: JSON.stringify(payload), timeoutMs: 30000,
          });
        } else {
          await loveApi(projectQuery("/journal"), {
            method: "POST",
            body: JSON.stringify({ date: form.elements.date.value, ...payload }),
            timeoutMs: 30000,
          });
        }
        node("detail-dialog")?.close();
        toast("日志已保存。", "success");
        await renderLovePage();
      } catch (error) {
        const feedback = node("love-journal-feedback");
        if (feedback) feedback.textContent = error.message;
        toast(`保存失败：${error.message}`, "error");
      }
    });
  });
}

async function deleteJournal(button) {
  const confirmed = await confirmAction({
    title: "删除这篇日志？", message: "删除后无法在应用内恢复；聊天记录不会受影响。",
    confirmLabel: "删除", danger: true,
  });
  if (!confirmed) return;
  await withBusy(button, "删除中…", async () => {
    try {
      await loveApi(projectQuery(`/journal/${encodeURIComponent(button.dataset.id)}`), { method: "DELETE", timeoutMs: 30000 });
      toast("日志已删除。", "success");
      await renderLovePage();
    } catch (error) {
      toast(`删除失败：${error.message}`, "error");
    }
  });
}

async function batchGenerateJournals() {
  openDialog("AI 批量补日记", `
    <form id="love-batch-form" class="love-dialog-form">
      <p class="field-help">只会为「有聊天、还没有日志」的日期生成，已有日志不会被覆盖。每篇都会调用一次 DeepSeek。</p>
      <div class="field"><label for="love-batch-days">最多生成多少天</label>
        <input id="love-batch-days" type="number" min="1" max="60" step="1" value="10" required></div>
      <div class="form-actions"><span id="love-batch-feedback" class="status-message"></span>
        <div class="love-inline-actions">
          <button class="button secondary" type="button" id="love-batch-cancel">取消</button>
          <button class="button primary" type="submit">开始生成</button>
        </div>
      </div>
    </form>`);
  node("love-batch-cancel")?.addEventListener("click", () => node("detail-dialog")?.close());
  node("love-batch-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    const maxDays = Math.max(1, Math.min(60, Number(form.elements.max_days?.value || node("love-batch-days")?.value || 10)));
    await withBusy(submit, "生成中…", async () => {
      const feedback = node("love-batch-feedback");
      if (feedback) feedback.textContent = "正在逐日生成，可能需要几分钟…";
      try {
        const result = await loveApi(projectQuery("/journal/generate-all"), {
          method: "POST", body: JSON.stringify({ max_days: maxDays }), timeoutMs: 600000,
        });
        node("detail-dialog")?.close();
        toast(result.message || "批量生成完成。", result.failed ? "warning" : "success");
        await renderLovePage();
      } catch (error) {
        if (feedback) feedback.textContent = error.message;
        toast(`批量生成失败：${error.message}`, "error");
      }
    });
  });
}

// ── 报告 ───────────────────────────────────────────────────────────

async function renderReport(content) {
  const project = currentProject();
  if (!project) {
    content.innerHTML = emptyBox("还没有项目", "先导入聊天记录，才能生成报告。",
      '<button class="button primary" type="button" data-love-action="goto" data-page="import">导入聊天</button>');
    return;
  }
  const latest = await loveApi(projectQuery("/analysis/latest"), { timeoutMs: 45000 });
  if (love.page !== "report") return;
  const hasAnalysis = latest?.status === "completed" && latest.result;
  const previewUrl = `/api/love/projects/${encodeURIComponent(love.projectId)}/report/preview?v=${Date.now()}`;
  const preview = love.lastReport
    ? `<div class="love-report-actions">
         <button class="button primary" type="button" data-love-action="report-download">下载 HTML</button>
         <button class="button secondary" type="button" data-love-action="report-print">打印 / 另存为 PDF</button>
         <button class="button secondary" type="button" data-love-action="report-generate">重新生成</button>
       </div>
       <iframe id="love-report-frame" class="love-report-frame" title="恋爱分析报告预览" sandbox="allow-same-origin" src="${escapeHtml(previewUrl)}"></iframe>`
    : `<div class="love-report-cta">
         <div><strong>生成一份可打印的本地报告</strong>
         <p>包含数据概览、${hasAnalysis ? "最新一次 AI 分析" : "画像与基础统计"}，不包含原始聊天内容。HTML 文件可以直接保存，或打印成 PDF。</p></div>
         <button class="button primary" type="button" data-love-action="report-generate">生成报告</button>
       </div>`;
  content.innerHTML = `${card("恋爱分析报告", `
    <div class="love-report-meta">
      <span class="love-badge ${hasAnalysis ? "ok" : "warn"}">${hasAnalysis ? "包含最新 AI 分析" : "还没有完成的 AI 分析"}</span>
      <span class="meta">${hasAnalysis ? `分析于 ${formatDateTime(latest.completed_at)}` : "可以先运行一次 AI 深度分析，报告会更完整。"}</span>
    </div>
    ${!hasAnalysis ? '<p class="field-help">报告仍会生成，只是 AI 分析部分为空。AI 分析需要先在设置里配置 API Key。</p>' : ""}
    ${preview}
    <p class="field-help">报告只在本机浏览器里生成与预览；下载的 HTML 内联全部样式，不依赖网络。</p>
  `, {
    eyebrow: "REPORT",
    subtitle: "把统计、AI 结论与画像整理成一页，适合自己留档或打印。",
  })}`;
}

async function generateReport(button) {
  await withBusy(button, "生成中…", async () => {
    try {
      const result = await loveApi(projectQuery("/report"), { method: "POST", timeoutMs: 60000 });
      love.lastReport = { html: result.html || "", name: result.project_name || "恋爱分析报告" };
      toast("报告已生成。", "success");
      await renderLovePage();
    } catch (error) {
      toast(`生成失败：${error.message}`, "error");
    }
  });
}

function downloadReport() {
  if (!love.lastReport?.html) return;
  const blob = new Blob([love.lastReport.html], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${(love.lastReport.name || "恋爱分析报告").replace(/[\\/:*?"<>|]+/g, "_")}.html`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function printReport() {
  if (!love.lastReport?.html) return;
  // 打印视图由后端单独出文档（自带内联样式的窄 CSP），避免把报告塞进工作台
  // 的严格 CSP；同一来源，浏览器允许在 load 后调用 print()。
  const url = `/api/love/projects/${encodeURIComponent(love.projectId)}/report/preview?v=${Date.now()}`;
  const win = window.open(url, "_blank");
  if (!win) {
    toast("浏览器拦截了新窗口，请改用「下载 HTML」。", "warning");
    return;
  }
  const trigger = () => {
    try { win.focus(); win.print(); } catch { /* 用户仍可手动打印 */ }
  };
  try {
    if (win.document?.readyState === "complete") trigger();
    else win.addEventListener("load", trigger, { once: true });
  } catch { /* ignore */ }
  // 某些浏览器在 window.open 返回前就完成 load；补一次延时兜底。
  window.setTimeout(() => {
    try { if (!win.closed && win.document?.readyState === "complete") win.focus(); } catch { /* ignore */ }
  }, 900);
}

// ── 值班日历 ─────────────────────────────────────────────────────────

function currentMonth() {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit",
  }).format(new Date()).slice(0, 7);
}

function validMonth(value) {
  return /^\d{4}-(0[1-9]|1[0-2])$/.test(String(value || "")) ? String(value) : "";
}

function shiftMonth(month, delta) {
  const [year, monthNumber] = month.split("-").map(Number);
  const shifted = new Date(Date.UTC(year, monthNumber - 1 + delta, 1));
  return `${shifted.getUTCFullYear()}-${String(shifted.getUTCMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(month) {
  const [year, monthNumber] = month.split("-");
  return `${year}年${Number(monthNumber)}月`;
}

/** 周一开头：返回本月 1 号前面要补几个空格。 */
function monthLeadingBlanks(month) {
  const [year, monthNumber] = month.split("-").map(Number);
  const weekday = new Date(Date.UTC(year, monthNumber - 1, 1)).getUTCDay();
  return (weekday + 6) % 7;
}

function scheduleToolbar(month) {
  return `<div class="love-schedule-toolbar">
    <div class="love-month-nav">
      <button class="small-button" type="button" data-love-action="schedule-month" data-value="-1" aria-label="上个月">‹</button>
      <strong>${monthLabel(month)}</strong>
      <button class="small-button" type="button" data-love-action="schedule-month" data-value="1" aria-label="下个月">›</button>
      <button class="text-button" type="button" data-love-action="schedule-today">今天</button>
    </div>
    <input id="love-schedule-file" type="file" accept=".xlsx,.xlsm" hidden />
    <button class="small-button" type="button" data-love-action="schedule-upload">导入排班表</button>
  </div>`;
}

async function renderSchedule(content) {
  const stored = validMonth(readStorage("torch-os:love-schedule-month"));
  const month = validMonth(love.scheduleMonth) || stored || currentMonth();
  love.scheduleMonth = month;
  writeStorage("torch-os:love-schedule-month", month);
  const [status, data] = await Promise.all([
    loveApi("/api/love/schedule/status", { timeoutMs: 20000 }),
    loveApi(`/api/love/schedule/month?month=${encodeURIComponent(month)}`, { timeoutMs: 30000 }),
  ]);
  if (love.page !== "schedule") return;
  love.scheduleData = data?.found ? data : null;
  const toolbar = scheduleToolbar(month);

  if (!data.found) {
    content.innerHTML = card("值班日历", `
      ${toolbar}
      <div class="state-message error-state love-schedule-missing">
        <strong>${escapeHtml(data.error || "还没有排班表")}</strong>
        <p>把 .xlsx 排班表放进 <code>${escapeHtml(status.excel_dir || ".torch-os/love/xlsx")}</code>，或点「导入排班表」。</p>
      </div>
    `, { eyebrow: "SCHEDULE", subtitle: "从排班表读取上班/休息和当天值乘车次；点日期可补备注。" });
    bindScheduleFile();
    return;
  }

  const cells = [];
  for (let index = 0; index < monthLeadingBlanks(month); index += 1) {
    cells.push('<span class="love-day-cell is-empty" aria-hidden="true"></span>');
  }
  for (const day of data.days) {
    const state = day.in_range ? (day.rest ? "休息" : "上班") : "—";
    const duty = String(day.duty || "").replace(/\s+/g, " ").trim();
    const note = String(day.note || "").replace(/\s+/g, " ").trim();
    // 用户备注优先；没有用户备注时，直接显示排班表里的当天值乘车次。
    const parts = [];
    if (duty) parts.push(duty);
    if (note) {
      const extra = duty && note.startsWith(duty)
        ? note.slice(duty.length).replace(/^[\s·,，;；/]+/, "").trim()
        : note;
      if (extra && extra !== duty) parts.push(extra);
    }
    const display = parts.join(" · ");
    const classes = ["love-day-cell"];
    classes.push(day.in_range ? (day.rest ? "is-rest" : "is-work") : "is-out");
    if (day.date === data.today) classes.push("is-today");
    const label = `${day.date} ${state}${duty ? `，值乘：${duty}` : ""}${note && note !== duty ? `，备注：${note}` : ""}`;
    cells.push(`<button type="button" class="${classes.join(" ")}" data-love-action="schedule-note"
      data-date="${day.date}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}">
      <span class="love-day-num">${Number(day.date.slice(8))}</span>
      <span class="love-day-state">${state}</span>
      ${display ? `<span class="love-day-note">${escapeHtml(display)}</span>` : ""}
    </button>`);
  }
  const inRange = data.days.filter((day) => day.in_range).length;
  const range = data.range || status.date_range;
  content.innerHTML = card("值班日历", `
    ${toolbar}
    ${inRange ? "" : `<p class="inline-warning">本月不在当前排班表范围内${range ? `（覆盖 ${escapeHtml(range.start)} ~ ${escapeHtml(range.end)}）` : ""}。</p>`}
    <div class="love-calendar-wrap">
      <div class="love-calendar-weekdays" aria-hidden="true">
        ${["一", "二", "三", "四", "五", "六", "日"].map((name) => `<span>${name}</span>`).join("")}
      </div>
      <div class="love-calendar-grid">${cells.join("")}</div>
    </div>
    <div class="love-calendar-legend" aria-hidden="true">
      <span><i class="is-work"></i>上班</span>
      <span><i class="is-rest"></i>休息</span>
      <span><i class="is-out"></i>不在表内</span>
    </div>
    <p class="field-help">点任意一天写备注；备注只保存在当前个人数据库的 <code>.torch-os/love/</code>。${range ? `当前排班表覆盖 ${escapeHtml(range.start)} ~ ${escapeHtml(range.end)}。` : ""}</p>
  `, { eyebrow: "SCHEDULE", subtitle: "上班/休息与排班表里的值乘车次会直接显示；点日期可补交接事项。" });
  bindScheduleFile();
}

function bindScheduleFile() {
  const input = node("love-schedule-file");
  if (!input || input.dataset.bound) return;
  input.dataset.bound = "1";
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      toast("正在导入排班表…", "info");
      const result = await loveApi("/api/love/schedule/excel", {
        method: "POST", body: form, timeoutMs: 120000,
      });
      toast(result.message || "排班表已更新。", "success");
      await renderLovePage();
    } catch (error) {
      toast(`导入失败：${error.message}`, "error");
    }
  });
}

function openScheduleNote(day) {
  const state = day.in_range ? (day.rest ? "休息" : "上班") : "这一天不在排班表范围内";
  const duty = String(day.duty || "").replace(/\s+/g, " ").trim();
  const dutyHint = duty ? `，当天值乘 ${duty}` : "";
  const initial = day.note || duty;
  openDialog(`${day.date} 值班备注`, `
    <form id="love-schedule-note-form" class="love-dialog-form">
      <p class="field-help">${escapeHtml(state)}${escapeHtml(dutyHint)}。车次来自排班表；备注只保存在当前个人数据库，可以补交接事项。</p>
      <div class="field"><label for="love-schedule-note">备注</label>
        <textarea id="love-schedule-note" rows="4" maxlength="500" placeholder="例如：G1 / G2，下午交接…">${escapeHtml(initial)}</textarea>
      </div>
      <div class="love-inline-actions">
        <button type="button" class="button secondary" id="love-schedule-note-cancel">取消</button>
        <button type="submit" class="button primary">保存备注</button>
      </div>
      <span id="love-schedule-note-feedback" class="status-message" role="status"></span>
    </form>
  `);
  node("love-schedule-note-cancel")?.addEventListener("click", () => node("detail-dialog")?.close());
  node("love-schedule-note-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const note = String(node("love-schedule-note")?.value || "");
    const feedback = node("love-schedule-note-feedback");
    if (feedback) feedback.textContent = "正在保存…";
    try {
      await loveApi("/api/love/schedule/note", jsonBody("PUT", { date: day.date, note }));
      node("detail-dialog")?.close();
      toast("备注已保存。", "success");
      await renderLovePage();
    } catch (error) {
      if (feedback) {
        feedback.textContent = `保存失败：${error.message}`;
        feedback.className = "status-message error";
      }
    }
  });
}

// ── 微信同步（WeFlow 本地 API）──────────────────────────────────────

const WECHAT_SYNC_LABELS = {
  queued: "排队中", running: "同步中", completed: "已完成", failed: "失败",
};

function formatEpoch(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  return formatDateTime(new Date(seconds * 1000).toISOString());
}

function wechatSessionOptions() {
  const sessions = Array.isArray(love.wechatSessions) ? love.wechatSessions : [];
  const current = love.wechatSelectedSession || love.wechatSource?.session_id || "";
  const options = [];
  if (current && love.wechatSource && !sessions.some((item) => item.id === current)) {
    options.push(`<option value="${escapeHtml(current)}" selected>${escapeHtml(love.wechatSource.contact_name || "已绑定联系人")}（已绑定）</option>`);
  }
  if (!sessions.length) {
    if (!options.length) options.push('<option value="">还没有读取到联系人</option>');
    return options.join("");
  }
  for (const item of sessions) {
    const selected = item.id === current ? " selected" : "";
    const count = item.message_count == null ? "" : ` · ${formatInt(item.message_count)} 条`;
    options.push(`<option value="${escapeHtml(item.id)}"${selected}>${escapeHtml(item.name)}${count}</option>`);
  }
  return options.join("");
}

function wechatMeOptions(project) {
  const current = love.wechatSource?.me_sender || project.partner_a || "";
  return [project.partner_a, project.partner_b]
    .filter(Boolean)
    .map((name) => `<option value="${escapeHtml(name)}"${name === current ? " selected" : ""}>${escapeHtml(name)}</option>`)
    .join("");
}

async function loadWechatSessions({ keyword = "", silent = false } = {}) {
  if (love.wechatSessionsLoading) return;
  love.wechatSessionsLoading = true;
  try {
    const suffix = keyword ? `?keyword=${encodeURIComponent(keyword)}` : "";
    const data = await loveApi(`/api/love/weflow/sessions${suffix}`, { timeoutMs: 60000 });
    love.wechatSessions = Array.isArray(data.sessions) ? data.sessions : [];
    love.wechatSessionsError = "";
    if (!silent) toast(`读取到 ${love.wechatSessions.length} 个单聊联系人。`, "success");
  } catch (error) {
    love.wechatSessions = [];
    love.wechatSessionsError = error.message;
    if (!silent) toast(`读取联系人失败：${error.message}`, "error");
  } finally {
    love.wechatSessionsLoading = false;
  }
}

function renderWechatSyncResult() {
  const target = node("love-weflow-sync-result");
  if (!target) return;
  const log = love.wechatSyncLog;
  if (!log) {
    target.innerHTML = '<p class="field-help">还没有运行同步。建议先「预览同步」，确认数量后再写入。</p>';
    return;
  }
  const label = WECHAT_SYNC_LABELS[log.status] || log.status || "—";
  const tone = log.status === "completed" ? "ok" : log.status === "failed" ? "error" : "warn";
  const finished = log.finished_at ? ` → ${formatDateTime(log.finished_at)}` : "";
  target.innerHTML = `
    <div class="love-sync-result-head">
      <span class="love-badge ${tone}">${escapeHtml(String(label))}</span>
      <span class="meta">${log.mode === "apply" ? "写入" : "预览"} · ${formatDateTime(log.started_at)}${finished}</span>
    </div>
    <dl class="love-facts">
      <div><dt>读取</dt><dd>${formatInt(log.fetched)} 条</dd></div>
      <div><dt>新增</dt><dd>${formatInt(log.inserted)} 条</dd></div>
      <div><dt>匹配旧消息</dt><dd>${formatInt(log.matched)} 条</dd></div>
      <div><dt>重复跳过</dt><dd>${formatInt(log.duplicates)} 条</dd></div>
      <div><dt>需人工确认</dt><dd>${formatInt(log.ambiguous)} 条</dd></div>
    </dl>
    ${log.error ? `<p class="inline-error">${escapeHtml(log.error)}</p>` : ""}
    ${log.status === "running" || log.status === "queued" ? '<p class="field-help">正在按页读取并合并；可以留在这个页面看进度。</p>' : ""}
    ${Number(log.ambiguous) > 0 ? '<p class="inline-warning">有消息同时匹配到多条旧记录，已跳过、没有自动写入，避免张冠李戴。</p>' : ""}
  `;
}

function pollWechatSync(logId, mode) {
  stopWechatPoll();
  const tick = async () => {
    if (love.page !== "wechat") return;
    try {
      const log = await loveApi(`/api/love/weflow/sync/${encodeURIComponent(logId)}`, { timeoutMs: 20000 });
      love.wechatSyncLog = log;
      renderWechatSyncResult();
      if (log.status === "queued" || log.status === "running") {
        love.wechatSyncPoll = window.setTimeout(tick, 1200);
        return;
      }
      if (log.status === "failed") {
        if (mode === "preview") love.wechatPreviewSince = null;
        toast(`同步失败：${log.error || "未知错误"}`, "error");
        return;
      }
      if (mode === "preview") {
        toast(`预览完成：新增 ${formatInt(log.inserted)} 条，匹配旧消息 ${formatInt(log.matched)} 条。`, "success");
        return;
      }
      love.wechatPreviewSince = null;
      toast(`同步完成：新增 ${formatInt(log.inserted)} 条，匹配 ${formatInt(log.matched)} 条。`, "success");
      await reloadLove({ quiet: true });
    } catch (error) {
      toast(`读取同步进度失败：${error.message}`, "error");
    }
  };
  tick();
}

async function renderWechat(content) {
  const project = currentProject();
  try {
    love.wechatStatus = await loveApi("/api/love/weflow/status", { timeoutMs: 20000 });
  } catch (error) {
    love.wechatStatus = {
      token_configured: false, port: 5031, last_sync_at: null, error: error.message,
    };
  }
  const status = love.wechatStatus || {};
  if (project) {
    try {
      const data = await loveApi(
        `/api/love/weflow/project?project_id=${encodeURIComponent(project.id)}`,
        { timeoutMs: 20000 },
      );
      love.wechatSource = data.source?.session_id ? data.source : null;
    } catch {
      love.wechatSource = null;
    }
  } else {
    love.wechatSource = null;
  }
  if (status.token_configured && love.wechatSessions === null) {
    await loadWechatSessions({ silent: true });
  }
  if (love.page !== "wechat") return;

  const source = love.wechatSource;
  const sessions = Array.isArray(love.wechatSessions) ? love.wechatSessions : [];
  const tokenBadge = status.token_configured
    ? '<span class="love-badge ok">已配置，加密存储</span>'
    : '<span class="love-badge error">未配置</span>';
  const sourceFacts = source ? `
    <dl class="love-facts">
      <div><dt>联系人</dt><dd>${escapeHtml(source.contact_name || source.session_id || "—")}</dd></div>
      <div><dt>角色映射</dt><dd>我 = ${escapeHtml(source.me_sender || "—")} · 对象 = ${escapeHtml(source.partner_sender || "—")}</dd></div>
      <div><dt>上次同步</dt><dd>${source.last_sync_at ? formatDateTime(source.last_sync_at) : "还没有同步"}</dd></div>
      <div><dt>增量位置</dt><dd>${source.watermark ? formatEpoch(source.watermark) : "从最早消息开始"}</dd></div>
    </dl>` : '<p class="field-help">还没有绑定微信单聊。先在下面读取联系人，再选择你和对象的那个单聊。</p>';

  const settingsCard = card("连接 WeFlow", `
    <dl class="love-facts">
      <div><dt>本地服务</dt><dd><code>http://127.0.0.1:${escapeHtml(String(status.port || 5031))}</code></dd></div>
      <div><dt>Access Token</dt><dd>${tokenBadge}</dd></div>
      <div><dt>微信</dt><dd>WeFlow 负责读取本机已登录的微信；torch 不保存微信密码</dd></div>
    </dl>
    ${status.error ? `<p class="inline-error">${escapeHtml(status.error)}</p>` : ""}
    <form id="love-weflow-form" class="love-key-form">
      <div class="form-grid">
        <div class="field"><label for="love-weflow-port">WeFlow API 端口</label>
          <input id="love-weflow-port" name="port" type="number" min="1" max="65535" value="${escapeHtml(String(status.port || 5031))}" required /></div>
        <div class="field"><label for="love-weflow-token">${status.token_configured ? "替换 Access Token（可留空）" : "Access Token"}</label>
          <input id="love-weflow-token" name="token" type="password" autocomplete="off" maxlength="300" placeholder="${status.token_configured ? "已配置；留空只改端口" : "粘贴 WeFlow → 设置 → API 服务里的 Token"}" /></div>
      </div>
      <div class="love-inline-actions">
        <button class="button primary" type="submit">保存并测试连接</button>
        <button class="button secondary" type="button" data-love-action="weflow-sessions">读取联系人</button>
        ${status.token_configured ? '<button class="button secondary" type="button" data-love-action="weflow-clear-token">清除 Token</button>' : ""}
      </div>
      <span id="love-weflow-feedback" class="status-message" role="status"></span>
      <p class="field-help">只访问 127.0.0.1，不连外网；Token 加密保存在 <code>.torch-os/love/</code>，不会回显，也不会写进日志。首次使用前请在 WeFlow 里开启 API 服务。</p>
    </form>
  `, { eyebrow: "WEFLOW", subtitle: "WeFlow 只负责从本机微信取数；torch 只读取你选中的一个单聊。" });

  const searchForm = `
    <form id="love-weflow-search-form" class="love-key-form">
      <div class="field"><label for="love-weflow-keyword">搜索联系人</label>
        <input id="love-weflow-keyword" name="keyword" type="search" maxlength="100" placeholder="输入微信备注名或昵称；留空列出最近会话" /></div>
      <div class="love-inline-actions">
        <button class="button secondary" type="submit">搜索</button>
        <span class="field-help">${love.wechatSessionsLoading ? "正在读取…" : `已读取 ${sessions.length} 个单聊`}</span>
      </div>
    </form>`;
  const sessionError = love.wechatSessionsError
    ? `<p class="inline-error">${escapeHtml(love.wechatSessionsError)}</p>` : "";
  const emptySessions = '<p class="field-help">还没有读取到联系人。确认 WeFlow 和微信都在运行后，点「读取联系人」。</p>';
  let sessionCard;
  if (!project) {
    const createForm = sessions.length ? `<form id="love-weflow-create-form" class="love-key-form">
      <div class="form-grid">
        <div class="field"><label for="love-weflow-create-session">要同步的单聊</label>
          <select id="love-weflow-create-session" name="session_id">${wechatSessionOptions()}</select></div>
        <div class="field"><label for="love-weflow-create-me">你在项目里的称呼</label>
          <input id="love-weflow-create-me" name="me_sender" type="text" maxlength="100" value="我" /></div>
      </div>
      <div class="love-inline-actions"><button class="button primary" type="submit">新建项目并绑定</button></div>
      <p class="field-help">只会读取这一个单聊；新建后可以预览，再写入聊天数据库。</p>
    </form>` : emptySessions;
    sessionCard = card("用微信新建项目", `
      <p class="field-help">当前还没有聊天项目。可以直接用下面这个单聊新建一个；之后也可以在「导入聊天」里继续合并其他记录。</p>
      ${searchForm}
      ${sessionError}
      ${createForm}
    `, { eyebrow: "WECHAT CONTACT", subtitle: "还没有项目时，从微信单聊开始。" });
  } else {
    sessionCard = card("选择单聊", `
      ${searchForm}
      ${sessionError}
      ${sessions.length ? `<form id="love-weflow-connect-form" class="love-key-form">
        <div class="form-grid">
          <div class="field"><label for="love-weflow-session">要同步的单聊</label>
            <select id="love-weflow-session" name="session_id">${wechatSessionOptions()}</select></div>
          <div class="field"><label for="love-weflow-me">项目里哪个人是你？</label>
            <select id="love-weflow-me" name="me_sender">${wechatMeOptions(project)}</select></div>
        </div>
        <div class="love-inline-actions"><button class="button primary" type="submit">绑定这个单聊</button></div>
        <p class="field-help">只会拉取这一个单聊；群聊、公众号和其他聊天不会被读取。绑定后，torch 会把「你」映射成项目的用户A，把对方映射成用户B，方便现有 AI 分析继续使用。</p>
      </form>` : emptySessions}
      <hr class="love-divider" />
      ${sourceFacts}
      ${source ? '<div class="love-inline-actions"><button class="button secondary" type="button" data-love-action="weflow-unbind">解除绑定</button></div>' : ""}
    `, { eyebrow: "WECHAT CONTACT", subtitle: "只选一个单聊；不会同步群聊或其他人。" });
  }

  const syncCard = source ? card("同步聊天", `
    <p class="field-help">按平台消息 ID、内容哈希和 ±2 分钟模糊匹配合并进现有项目；重复消息不会重复写入。每次「写入数据库」前都会先备份恋爱数据库。</p>
    <label class="check-label"><input id="love-weflow-full" type="checkbox" /> 从最早的消息重新扫描（平时不用勾）</label>
    <div class="love-inline-actions">
      <button class="button secondary" type="button" data-love-action="weflow-preview">预览同步</button>
      <button class="button primary" type="button" data-love-action="weflow-apply">写入数据库</button>
    </div>
    <div id="love-weflow-sync-result" class="love-sync-result"></div>
  `, { eyebrow: "WEFLOW SYNC", subtitle: "先预览，再写入；原文只进恋爱数据库。" }) : "";

  const filePreview = love.wechatFilePreview;
  const defaultFileMe = project && (filePreview?.senders || []).includes(project.partner_a)
    ? project.partner_a
    : (filePreview?.senders || [])[0] || "";
  const filePreviewHtml = filePreview ? `
    <dl class="love-facts">
      <div><dt>文件</dt><dd>${escapeHtml(filePreview.file_name || "—")}</dd></div>
      <div><dt>消息</dt><dd>${formatInt(filePreview.message_count)} 条</dd></div>
      <div><dt>日期</dt><dd>${formatDateTime(filePreview.date_range_start)} 至 ${formatDateTime(filePreview.date_range_end)}</dd></div>
    </dl>
    <form id="love-weflow-file-confirm-form" class="love-key-form">
      <div class="field"><label for="love-weflow-file-me">文件里哪个人是你？</label>
        <select id="love-weflow-file-me" name="me_sender">
          ${(filePreview.senders || []).map((name) => `<option value="${escapeHtml(name)}"${name === defaultFileMe ? " selected" : ""}>${escapeHtml(name)}</option>`).join("")}
        </select></div>
      <div class="love-inline-actions"><button class="button primary" type="submit">合并进当前项目</button></div>
      <p class="field-help">请核对哪一项是你自己的微信昵称；选错会把双方消息对调。只合并进当前项目，不会改绑 WeFlow；重复消息仍按平台消息 ID、内容哈希和模糊匹配跳过。</p>
    </form>` : "";
  const fileCard = project ? card("兜底：导入 ChatLab 文件", `
    <form id="love-weflow-file-form" class="love-key-form">
      <div class="field"><label for="love-weflow-file">WeFlow 导出的 ChatLab 文件（.json / .jsonl）</label>
        <input id="love-weflow-file" name="file" type="file" accept=".json,.jsonl,application/json" /></div>
      <div class="love-inline-actions"><button class="button secondary" type="submit">读取文件</button></div>
      <p class="field-help">WeFlow API 暂时不可用时用这个兜底；文件只在本机解析。只支持两位发送者的单聊，群聊会被拒绝。</p>
    </form>
    ${filePreviewHtml}
  `, { eyebrow: "CHATLAB FILE", subtitle: "同样只合并消息，不写四库。" }) : "";

  const privacyCard = card("数据边界", `
    <ul class="love-advice">
      <li>微信原文只保存在 <code>.torch-os/love/</code> 的恋爱数据库；不会写进四库。</li>
      <li>AI 只拿脱敏后的当天记录；每日记忆必须由你确认后才写入 <code>02_生活运营/05_情感与记忆</code>。</li>
      <li>关闭 WeFlow 或清除 Token 后，torch 不会主动读取微信。</li>
    </ul>
    <div class="love-inline-actions">
      <button class="button secondary" type="button" data-love-action="goto" data-page="memory">去每日记忆</button>
    </div>
  `, { eyebrow: "PRIVACY", subtitle: "只同步一个单聊，原文与四库分开。" });

  content.innerHTML = settingsCard + sessionCard + syncCard + fileCard + privacyCard;
  renderWechatSyncResult();

  node("love-weflow-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    const token = form.elements.token.value.trim();
    const port = Number(form.elements.port.value) || 5031;
    await withBusy(submit, "测试连接…", async () => {
      try {
        const next = await loveApi("/api/love/weflow/settings", {
          method: "PUT", body: JSON.stringify({ token, port }), timeoutMs: 40000,
        });
        love.wechatStatus = next;
        love.wechatSessions = null;
        love.wechatSessionsError = "";
        form.elements.token.value = "";
        toast(token
          ? "WeFlow 连接成功，Token 已加密保存。"
          : next.token_configured
            ? "端口已保存；Token 保持原样。"
            : "端口已保存；还没有配置 Token。", "success");
        await renderLovePage();
      } catch (error) {
        const feedback = node("love-weflow-feedback");
        if (feedback) feedback.textContent = error.message;
        toast(`连接失败：${error.message}`, "error");
      }
    });
  });

  node("love-weflow-search-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    await withBusy(button, "读取中…", async () => {
      await loadWechatSessions({ keyword: form.elements.keyword.value.trim() });
      await renderLovePage();
    });
  });

  node("love-weflow-connect-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const sessionId = form.elements.session_id.value;
    const meSender = form.elements.me_sender.value;
    if (!sessionId) {
      toast("请先选择一个单聊。", "warning");
      return;
    }
    await withBusy(button, "绑定中…", async () => {
      try {
        const result = await loveApi("/api/love/weflow/connect", {
          method: "POST",
          body: JSON.stringify({
            project_id: love.projectId, session_id: sessionId, me_sender: meSender,
          }),
          timeoutMs: 60000,
        });
        love.wechatSource = result.source || null;
        love.wechatSelectedSession = sessionId;
        love.wechatSyncLog = null;
        love.wechatPreviewSince = null;
        toast(result.swapped
          ? "已绑定，并把项目里的「我 / 对象」顺序调整为 AI 分析约定。"
          : "已绑定这个单聊。", "success");
        await reloadLove({ quiet: true });
      } catch (error) {
        toast(`绑定失败：${error.message}`, "error");
      }
    });
  });

  node("love-weflow-create-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const sessionId = form.elements.session_id.value;
    const meSender = form.elements.me_sender.value.trim() || "我";
    if (!sessionId) {
      toast("请先选择一个单聊。", "warning");
      return;
    }
    await withBusy(button, "新建中…", async () => {
      try {
        const result = await loveApi("/api/love/weflow/project", {
          method: "POST",
          body: JSON.stringify({ session_id: sessionId, me_sender: meSender }),
          timeoutMs: 60000,
        });
        love.projectId = result.project.id;
        love.projects = [result.project, ...love.projects];
        writeStorage(PROJECT_KEY, love.projectId);
        love.wechatSource = result.source || null;
        love.wechatSelectedSession = sessionId;
        love.wechatSyncLog = null;
        love.wechatPreviewSince = null;
        syncProjectPicker();
        toast("已新建项目并绑定这个单聊。", "success");
        await reloadLove({ quiet: true });
      } catch (error) {
        toast(`新建项目失败：${error.message}`, "error");
      }
    });
  });

  node("love-weflow-file-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const file = form.elements.file.files?.[0];
    if (!file) {
      toast("请先选择一个 ChatLab 文件。", "warning");
      return;
    }
    await withBusy(button, "读取中…", async () => {
      try {
        const body = new FormData();
        body.append("file", file);
        const preview = await loveApi("/api/love/weflow/import-preview", {
          method: "POST", body, timeoutMs: 120000,
        });
        love.wechatPendingFile = file;
        love.wechatFilePreview = preview;
        toast(`读取到 ${formatInt(preview.message_count)} 条消息。`, "success");
        await renderLovePage();
      } catch (error) {
        toast(`读取文件失败：${error.message}`, "error");
      }
    });
  });

  node("love-weflow-file-confirm-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const meSender = form.elements.me_sender.value;
    if (!love.wechatPendingFile) {
      toast("文件已不在当前页面，请重新选择。", "warning");
      return;
    }
    await withBusy(button, "合并中…", async () => {
      try {
        const body = new FormData();
        body.append("file", love.wechatPendingFile);
        body.append("project_id", love.projectId);
        body.append("me_sender", meSender);
        const result = await loveApi("/api/love/weflow/import-file", {
          method: "POST", body, timeoutMs: 180000,
        });
        const extra = result.swapped ? "；已调整项目角色顺序" : "";
        toast(`合并完成：新增 ${formatInt(result.inserted)} 条，匹配 ${formatInt(result.matched)} 条${extra}。`, "success");
        if (Number(result.ambiguous) > 0) {
          toast(`有 ${formatInt(result.ambiguous)} 条消息匹配到多条旧记录，已跳过。`, "warning");
        }
        love.wechatPendingFile = null;
        love.wechatFilePreview = null;
        await reloadLove({ quiet: true });
      } catch (error) {
        toast(`合并失败：${error.message}`, "error");
      }
    });
  });

  node("love-weflow-full")?.addEventListener("change", () => {
    // 换了扫描范围就必须重新预览，避免用旧预览的 since 直接写入。
    love.wechatPreviewSince = null;
  });

  if (love.wechatSyncLog && ["queued", "running"].includes(love.wechatSyncLog.status)) {
    pollWechatSync(love.wechatSyncLog.id, love.wechatSyncLog.mode);
  }
}

async function startWechatSync(mode, button) {
  const project = currentProject();
  const source = love.wechatSource;
  if (!project || !source) {
    toast("请先绑定一个微信单聊。", "warning");
    return;
  }
  const full = Boolean(node("love-weflow-full")?.checked);
  let since;
  if (mode === "apply" && love.wechatPreviewSince !== null) since = love.wechatPreviewSince;
  else if (full) since = 0;
  if (mode === "apply" && love.wechatPreviewSince === null) {
    const confirmed = await confirmAction({
      title: "还没有预览，直接写入？",
      message: "会按当前增量位置直接合并进现有项目。建议先点「预览同步」确认新增和重复数量。",
      confirmLabel: "直接写入",
    });
    if (!confirmed) return;
  }
  await withBusy(button, mode === "preview" ? "正在预览…" : "正在写入…", async () => {
    try {
      const body = { project_id: project.id, mode };
      if (since !== undefined) body.since = since;
      const log = await loveApi("/api/love/weflow/sync", {
        method: "POST", body: JSON.stringify(body), timeoutMs: 60000,
      });
      love.wechatSyncLog = log;
      if (mode === "preview") {
        love.wechatPreviewSince = since === undefined ? Number(source.watermark || 0) : since;
      }
      renderWechatSyncResult();
      pollWechatSync(log.id, mode);
    } catch (error) {
      toast(`同步启动失败：${error.message}`, "error");
    }
  });
}

async function clearWechatToken(button) {
  const confirmed = await confirmAction({
    title: "清除 WeFlow Token？",
    message: "清除后不能再读取微信联系人，也不能同步；已经同步的聊天和每日记忆不受影响。",
    confirmLabel: "清除 Token", danger: true,
  });
  if (!confirmed) return;
  await withBusy(button, "清除中…", async () => {
    try {
      const next = await loveApi("/api/love/weflow/token", {
        method: "DELETE", timeoutMs: 20000,
      });
      love.wechatStatus = next;
      love.wechatSessions = [];
      love.wechatSessionsError = "";
      toast("WeFlow Token 已清除。", "success");
      await renderLovePage();
    } catch (error) {
      toast(`清除失败：${error.message}`, "error");
    }
  });
}

async function unbindWechat(button) {
  const project = currentProject();
  if (!project) return;
  const confirmed = await confirmAction({
    title: "解除这个单聊的绑定？",
    message: "解除后不会再从 WeFlow 同步新消息；已经合并进项目的聊天记录会保留。",
    confirmLabel: "解除绑定", danger: true,
  });
  if (!confirmed) return;
  await withBusy(button, "解除中…", async () => {
    try {
      await loveApi(`/api/love/weflow/project?project_id=${encodeURIComponent(project.id)}`, {
        method: "DELETE", timeoutMs: 20000,
      });
      love.wechatSource = null;
      love.wechatSyncLog = null;
      love.wechatPreviewSince = null;
      toast("已解除绑定，聊天记录仍在项目里。", "success");
      await renderLovePage();
    } catch (error) {
      toast(`解除失败：${error.message}`, "error");
    }
  });
}

// ── 每日记忆 ───────────────────────────────────────────────────────

function memoryStatusLabel(status) {
  if (status === "written") return "已写入";
  if (status === "draft") return "待确认";
  return "未生成";
}

function memoryDayButtons() {
  if (!love.memoryDays.length) {
    return '<p class="field-help">这个项目还没有可整理的聊天日期。先在「导入聊天」或「微信同步」里准备一些消息。</p>';
  }
  return love.memoryDays.map((item) => {
    const active = item.date === love.memoryDate ? " active" : "";
    const status = memoryStatusLabel(item.status);
    return `<button type="button" class="love-memory-day${active}" data-love-action="memory-day" data-date="${escapeHtml(item.date)}">
      <span>${escapeHtml(item.date)}</span>
      <small>${formatInt(item.message_count)} 条 · ${status}</small>
    </button>`;
  }).join("");
}

function memoryEditorCard(project) {
  const draft = love.memoryDraft;
  if (!draft) {
    const inRecent = love.memoryDays.some((item) => item.date === love.memoryDate);
    return card(inRecent ? "还没有草稿" : "这一天还没有草稿", emptyBox(
      `${love.memoryDate || "这一天"} 还没有记忆草稿`,
      inRecent
        ? "生成后先在这里核对、修改，确认无误再写入 02_生活运营/05_情感与记忆。原文不会进入四库。"
        : "如果这一天有聊天记录，可以点左侧「生成这一天的草稿」；没有记录时不会生成，AI 也不会编造记忆。",
      '<button class="button primary" type="button" data-love-action="memory-generate-selected">生成这一天的草稿</button>',
    ), { eyebrow: "MEMORY DRAFT" });
  }
  const written = draft.status === "written";
  const badge = written
    ? '<span class="love-badge ok">已写入</span>'
    : '<span class="love-badge warn">待确认</span>';
  const target = draft.note_path || draft.target_path || "";
  const targetWarning = draft.target_exists && !draft.target_is_torch
    ? '<p class="inline-warning">目标文件名已被一篇不是 torch 生成的笔记占用。写入会被拒绝；请先在 Obsidian 里改名。</p>'
    : "";
  return card("记忆草稿", `
    <div class="love-memory-meta">
      ${badge}
      <span class="meta">${escapeHtml(draft.date || "")} · 模型整理，需你确认</span>
    </div>
    ${targetWarning}
    <form id="love-memory-editor-form" class="love-key-form">
      <div class="field"><label for="love-memory-markdown">记忆草稿（可直接修改）</label>
        <textarea id="love-memory-markdown" name="markdown" class="love-memory-textarea" rows="22" spellcheck="false">${escapeHtml(draft.markdown || "")}</textarea></div>
      <div class="love-inline-actions">
        <button class="button secondary" type="submit">保存修改</button>
        <button class="button primary" type="button" data-love-action="memory-write">${written ? "重新写入四库" : "写入生活运营"}</button>
        <button class="button secondary" type="button" data-love-action="memory-regenerate">重新生成</button>
        ${written ? "" : '<button class="text-button" type="button" data-love-action="memory-discard">丢弃草稿</button>'}
      </div>
      <p class="field-help">目标：<code>${escapeHtml(target)}</code>。写入后可以在 Obsidian 里继续修改；原始聊天不会进入四库。</p>
      <span id="love-memory-feedback" class="status-message" role="status"></span>
    </form>
    <details class="love-memory-source">
      <summary>这篇记忆用了多少聊天？</summary>
      <p class="field-help">${formatInt(draft.content?.message_count)} 条消息 · 生成于 ${formatDateTime(draft.updated_at)}</p>
    </details>
  `, { eyebrow: "MEMORY DRAFT", subtitle: "先核对事实，再写入生活运营。" });
}

async function renderMemory(content) {
  const project = currentProject();
  if (!project) {
    content.innerHTML = card("每日记忆", emptyBox(
      "还没有聊天项目",
      "每日记忆会从现有聊天项目里按天整理。先导入聊天或完成一次微信同步。",
      '<button class="button primary" type="button" data-love-action="goto" data-page="import">去导入聊天</button>',
    ), { eyebrow: "MEMORY" });
    return;
  }
  const data = await loveApi(
    `/api/love/memory/days?project_id=${encodeURIComponent(project.id)}`,
    { timeoutMs: 30000 },
  );
  love.memoryDays = Array.isArray(data.days) ? data.days : [];
  if (!love.memoryDate) love.memoryDate = love.memoryDays[0]?.date || todayText();
  const selected = love.memoryDays.find((item) => item.date === love.memoryDate);
  let draftId = selected?.draft_id || "";
  if (!selected && love.memoryDraft?.date === love.memoryDate) {
    // 日期列表只取最近 366 天；更早的日期刚生成/写入时仍要保留当前草稿。
    draftId = love.memoryDraft.id;
  }
  if (draftId !== love.memoryDraftId) {
    love.memoryDraftId = draftId;
    love.memoryDraft = null;
  }
  if (love.memoryDraftId && !love.memoryDraft) {
    try {
      love.memoryDraft = await loveApi(
        `/api/love/memory/drafts/${encodeURIComponent(love.memoryDraftId)}`,
        { timeoutMs: 20000 },
      );
    } catch (error) {
      love.memoryDraft = null;
      toast(`读取记忆草稿失败：${error.message}`, "error");
    }
  }
  if (love.page !== "memory") return;
  content.innerHTML = `
    <div class="love-memory-layout">
      <div class="love-memory-side">
        ${card("选择日期", `
          <form id="love-memory-generate-form" class="love-key-form">
            <div class="field"><label for="love-memory-date">日期</label>
              <input id="love-memory-date" name="date" type="date" value="${escapeHtml(love.memoryDate || todayText())}" required /></div>
            <div class="love-inline-actions">
              <button class="button primary" type="submit">生成这一天的草稿</button>
            </div>
          </form>
          <p class="field-help">只整理这一天；当天没有消息时不会生成。AI 用的是脱敏后的聊天记录，生成后你可以逐字修改。</p>
          <div class="love-memory-day-list">${memoryDayButtons()}</div>
        `, { eyebrow: "DAILY MEMORY", subtitle: "按天起草，确认后写入四库。" })}
      </div>
      <div class="love-memory-main">
        ${memoryEditorCard(project)}
        ${card("DSH 与四库", `
          <ul class="love-advice">
            <li>批量整理和每日摘要仍走原生 DeepSeek；DSH 是交互式工作台，没有稳定的批处理接口。</li>
            <li>确认写入后，笔记是普通 Markdown；DSH 可以直接读取 <code>02_生活运营/05_情感与记忆</code> 和四库其他内容继续对话。</li>
            <li>不想写进四库时，可以只保留草稿或丢弃；原始聊天始终在 <code>.torch-os/love/</code>。</li>
          </ul>
        `, { eyebrow: "DSH BOUNDARY" })}
      </div>
    </div>
  `;

  node("love-memory-generate-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const date = form.elements.date.value;
    const button = form.querySelector('button[type="submit"]');
    if (!date) return;
    await generateMemoryDraft(date, button);
  });

  node("love-memory-editor-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const draft = love.memoryDraft;
    if (!draft) return;
    const button = form.querySelector('button[type="submit"]');
    const markdown = form.elements.markdown.value;
    await withBusy(button, "保存中…", async () => {
      try {
        love.memoryDraft = await loveApi(
          `/api/love/memory/drafts/${encodeURIComponent(draft.id)}`,
          { method: "PUT", body: JSON.stringify({ markdown }), timeoutMs: 30000 },
        );
        toast("草稿已保存。", "success");
        await renderLovePage();
      } catch (error) {
        const feedback = node("love-memory-feedback");
        if (feedback) feedback.textContent = error.message;
        toast(`保存失败：${error.message}`, "error");
      }
    });
  });
}

async function generateMemoryDraft(date, button, { overwrite = false } = {}) {
  const project = currentProject();
  if (!project) return;
  const run = async (allowOverwrite) => {
    const draft = await loveApi("/api/love/memory/generate", {
      method: "POST",
      body: JSON.stringify({ project_id: project.id, date, overwrite: allowOverwrite }),
      timeoutMs: 180000,
    });
    love.memoryDate = date;
    love.memoryDraftId = draft.id;
    love.memoryDraft = draft;
    return draft;
  };
  await withBusy(button, "AI 正在整理…", async () => {
    try {
      await run(overwrite);
      toast("记忆草稿已生成，请先核对再写入。", "success");
      await renderLovePage();
    } catch (error) {
      if (error.status === 409 && !overwrite) {
        const confirmed = await confirmAction({
          title: "重新生成这一天的记忆？",
          message: "这一天已经写入过四库。重新生成会先变成草稿，原笔记不会立刻改动；最后仍需要你确认覆盖写入。",
          confirmLabel: "重新生成",
        });
        if (!confirmed) return;
        try {
          await run(true);
          toast("已重新生成草稿，请核对后覆盖写入。", "success");
          await renderLovePage();
        } catch (secondError) {
          toast(`生成失败：${secondError.message}`, "error");
        }
        return;
      }
      toast(`生成失败：${error.message}`, "error");
    }
  });
}

async function writeMemoryDraft(button) {
  const draft = love.memoryDraft;
  if (!draft) return;
  const write = (overwrite) => loveApi(
    `/api/love/memory/drafts/${encodeURIComponent(draft.id)}/write`,
    { method: "POST", body: JSON.stringify({ overwrite }), timeoutMs: 30000 },
  );
  await withBusy(button, "写入中…", async () => {
    try {
      love.memoryDraft = await write(false);
      toast(`已写入 ${love.memoryDraft.note_path}`, "success");
      await renderLovePage();
    } catch (error) {
      if (error.status === 409 && error.message.includes("目标文件已存在")) {
        const confirmed = await confirmAction({
          title: "覆盖已存在的笔记？",
          message: "目标文件已经存在，可能是上次写入的版本。用当前草稿覆盖它吗？",
          confirmLabel: "覆盖写入", danger: true,
        });
        if (!confirmed) return;
        try {
          love.memoryDraft = await write(true);
          toast(`已覆盖写入 ${love.memoryDraft.note_path}`, "success");
          await renderLovePage();
        } catch (secondError) {
          toast(`写入失败：${secondError.message}`, "error");
        }
        return;
      }
      toast(`写入失败：${error.message}`, "error");
    }
  });
}

async function discardMemoryDraft(button) {
  const draft = love.memoryDraft;
  if (!draft) return;
  const confirmed = await confirmAction({
    title: "丢弃这篇草稿？",
    message: "只删除应用里的草稿，不删除四库中的笔记；原始聊天也不受影响。",
    confirmLabel: "丢弃草稿", danger: true,
  });
  if (!confirmed) return;
  await withBusy(button, "丢弃中…", async () => {
    try {
      await loveApi(`/api/love/memory/drafts/${encodeURIComponent(draft.id)}`, {
        method: "DELETE", timeoutMs: 20000,
      });
      love.memoryDraft = null;
      love.memoryDraftId = "";
      toast("草稿已丢弃。", "success");
      await renderLovePage();
    } catch (error) {
      toast(`丢弃失败：${error.message}`, "error");
    }
  });
}

async function regenerateMemory(button) {
  const draft = love.memoryDraft;
  if (!draft) return;
  const confirmed = await confirmAction({
    title: "重新生成这篇记忆？",
    message: "会用当天聊天重新生成草稿；当前草稿里未保存的修改会丢失。",
    confirmLabel: "重新生成",
  });
  if (!confirmed) return;
  await generateMemoryDraft(draft.date, button, { overwrite: true });
}

// ── 设置 ───────────────────────────────────────────────────────────

async function renderSettings(content) {
  const status = love.status || await loveApi("/api/love/status", { timeoutMs: 20000 });
  const settings = love.settings || await loveApi("/api/love/settings", { timeoutMs: 20000 });
  if (love.page !== "settings") return;
  const keyConfigured = Boolean(settings.api_key_configured);
  content.innerHTML = `
    ${card("DeepSeek API", `
      <dl class="love-facts">
        <div><dt>接口地址</dt><dd><code>${escapeHtml(settings.api_base_url || "—")}</code></dd></div>
        <div><dt>模型</dt><dd><code>${escapeHtml(settings.api_model || "—")}</code></dd></div>
        <div><dt>API Key</dt><dd>${keyConfigured ? '<span class="love-badge ok">已配置，加密存储</span>' : '<span class="love-badge error">未配置</span>'}</dd></div>
      </dl>
      <form id="love-key-form" class="love-key-form">
        <div class="field"><label for="love-api-key">${keyConfigured ? "替换 API Key" : "填写 DeepSeek API Key"}</label>
          <input id="love-api-key" name="key" type="password" autocomplete="off" maxlength="300" required placeholder="sk-..." /></div>
        <div class="love-inline-actions">
          <button class="button primary" type="submit">保存 Key</button>
          ${keyConfigured ? '<button class="button secondary" type="button" data-love-action="settings-delete-key">删除 Key</button>' : ""}
        </div>
        <span id="love-key-feedback" class="status-message" role="status"></span>
      </form>
      <p class="field-help">Key 使用 AES-256-GCM 加密后存进当前个人数据库的恋爱数据库；密钥文件在数据库同目录（<code>.torch-os/love/</code>）。页面不会回显已保存的 Key。</p>
    `, { eyebrow: "AI SETTINGS", subtitle: "只有 AI 分析、每日记忆、生成日记和报告会用到这把 Key；其余功能完全离线。" })}
    ${card("数据与存储", `
      <dl class="love-facts">
        <div><dt>数据目录</dt><dd class="love-path">${escapeHtml(status.data_dir || "—")}</dd></div>
        <div><dt>数据库</dt><dd class="love-path">${escapeHtml(status.db_path || "—")}</dd></div>
        <div><dt>数据库大小</dt><dd>${status.db_size ? `${(Number(status.db_size) / 1024 / 1024).toFixed(1)} MB` : "—"}</dd></div>
        <div><dt>项目 / 消息 / 日志</dt><dd>${formatInt(status.project_count)} / ${formatInt(status.message_count)} / ${formatInt(status.journal_count)}</dd></div>
        <div><dt>排班表目录</dt><dd class="love-path">${escapeHtml(status.schedule_excel_dir || "—")}</dd></div>
        <div><dt>值班备注</dt><dd class="love-path">${escapeHtml(status.schedule_notes || "—")}</dd></div>
      </dl>
      <p class="field-help">恋爱系统的数据库、加密 Key、排班表和值班备注都放在当前个人数据库的 <code>.torch-os/love/</code> 里；拷贝整个个人数据库即可带走。API Key 只会保存在本机，不上传。</p>
      ${(status.migration?.db === "copied" || status.migration?.key
          || Number(status.migration?.excel) > 0 || status.migration?.stopwords)
        ? '<p class="inline-warning">已把旧位置的数据复制到当前个人数据库；原文件保留在原处。</p>' : ""}
      <div class="love-inline-actions">
        <button class="button secondary" type="button" data-love-action="settings-refresh">重新检查</button>
        <button class="button secondary" type="button" data-love-action="goto" data-page="schedule">打开值班日历</button>
      </div>
    `, { eyebrow: "DATA BOUNDARY", subtitle: "原生接入后仍然只认这一份本机数据，不做云端同步。" })}
    ${card("怎么用", `
      <ol class="love-advice">
        <li>导入聊天记录，或直接使用已存在的项目。</li>
        <li>在「微信同步」里连接 WeFlow，只绑一个单聊，先预览再合并进现有项目。</li>
        <li>在「统计分析」里看消息量、回复间隔、媒体和高频词。</li>
        <li>配置 Key 后运行一次「AI 深度分析」；结论和画像都会保存在本机。</li>
        <li>在「每日记忆」里按天生成草稿，核对后写入生活运营的「情感与记忆」。</li>
        <li>用「恋爱日志」按天记录，或让 AI 根据当天聊天先写一版，再自己修改。</li>
        <li>需要留档时在「报告」里生成 HTML，打印成 PDF。</li>
      </ol>
      <p class="field-help">AI 结论只是一种视角。涉及真实关系与安全的问题，请优先相信现实沟通和可信任的人。</p>
    `, { eyebrow: "GUIDE" })}`;
  node("love-key-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    const key = form.elements.key.value.trim();
    if (!key) return;
    await withBusy(submit, "保存中…", async () => {
      try {
        await loveApi("/api/love/settings/api-key", {
          method: "POST", body: JSON.stringify({ key }), timeoutMs: 30000,
        });
        love.settings = { ...(love.settings || {}), api_key_configured: true };
        toast("API Key 已保存并加密。", "success");
        await renderLovePage();
      } catch (error) {
        const feedback = node("love-key-feedback");
        if (feedback) feedback.textContent = error.message;
        toast(`保存失败：${error.message}`, "error");
      }
    });
  });
}

async function deleteApiKey(button) {
  const confirmed = await confirmAction({
    title: "删除本机保存的 API Key？",
    message: "删除后 AI 分析与 AI 生成日记会停止工作；聊天记录、画像和日志不受影响。",
    confirmLabel: "删除 Key", danger: true,
  });
  if (!confirmed) return;
  await withBusy(button, "删除中…", async () => {
    try {
      await loveApi("/api/love/settings/api-key", { method: "DELETE", timeoutMs: 30000 });
      love.settings = { ...(love.settings || {}), api_key_configured: false };
      toast("API Key 已删除。", "success");
      await renderLovePage();
    } catch (error) {
      toast(`删除失败：${error.message}`, "error");
    }
  });
}

// ── 动作分发 ───────────────────────────────────────────────────────

async function handleLoveAction(event) {
  const button = event.target.closest("[data-love-action]");
  if (!button) return;
  const action = button.dataset.loveAction;
  switch (action) {
    case "goto":
      setLovePage(button.dataset.page);
      return;
    case "retry-page":
      await reloadLove({ quiet: true });
      return;
    case "pick-file":
      node("love-file")?.click();
      return;
    case "import-confirm":
      await confirmImport(button);
      return;
    case "import-reset":
      love.pendingFile = null;
      await renderLovePage();
      return;
    case "trend":
      love.trend = ["day", "week", "month"].includes(button.dataset.value) ? button.dataset.value : "day";
      await renderLovePage();
      return;
    case "run-analysis":
      await runAnalysis(button);
      return;
    case "analysis-log":
      love.selectedAnalysisId = button.dataset.id || "";
      await renderLovePage();
      return;
    case "profile-add":
      await openTraitDialog("add");
      return;
    case "profile-edit":
      await openTraitDialog("edit", button.dataset.key || "");
      return;
    case "profile-lock":
      await toggleTraitLock(button);
      return;
    case "profile-delete":
      await deleteTrait(button);
      return;
    case "journal-new":
      await openJournalDialog("new");
      return;
    case "journal-edit":
      await openJournalDialog("edit", button.dataset.id || "");
      return;
    case "journal-delete":
      await deleteJournal(button);
      return;
    case "journal-generate-all":
      await batchGenerateJournals();
      return;
    case "report-generate":
      await generateReport(button);
      return;
    case "report-download":
      downloadReport();
      return;
    case "report-print":
      printReport();
      return;
    case "schedule-month": {
      const delta = Number(button.dataset.value) < 0 ? -1 : 1;
      love.scheduleMonth = shiftMonth(love.scheduleMonth || currentMonth(), delta);
      writeStorage("torch-os:love-schedule-month", love.scheduleMonth);
      await renderLovePage();
      return;
    }
    case "schedule-today":
      love.scheduleMonth = currentMonth();
      writeStorage("torch-os:love-schedule-month", love.scheduleMonth);
      await renderLovePage();
      return;
    case "schedule-upload":
      node("love-schedule-file")?.click();
      return;
    case "schedule-note": {
      const date = button.dataset.date || "";
      const day = love.scheduleData?.days?.find((item) => item.date === date)
        || { date, in_range: false, work: null, rest: null, note: "" };
      openScheduleNote(day);
      return;
    }
    case "wechat-sessions":
      await withBusy(button, "读取中…", async () => {
        const keyword = node("love-weflow-keyword")?.value || "";
        await loadWechatSessions({ keyword: keyword.trim() });
        await renderLovePage();
      });
      return;
    case "wechat-clear-token":
      await clearWechatToken(button);
      return;
    case "wechat-preview":
      await startWechatSync("preview", button);
      return;
    case "wechat-apply":
      await startWechatSync("apply", button);
      return;
    case "wechat-unbind":
      await unbindWechat(button);
      return;
    case "memory-day":
      love.memoryDate = button.dataset.date || love.memoryDate;
      love.memoryDraftId = "";
      love.memoryDraft = null;
      await renderLovePage();
      return;
    case "memory-generate-selected":
      if (!love.memoryDate) {
        toast("请先选择日期。", "warning");
        return;
      }
      await generateMemoryDraft(love.memoryDate, button);
      return;
    case "memory-write":
      await writeMemoryDraft(button);
      return;
    case "memory-regenerate":
      await regenerateMemory(button);
      return;
    case "memory-discard":
      await discardMemoryDraft(button);
      return;
    case "settings-delete-key":
      await deleteApiKey(button);
      return;
    case "settings-refresh":
      await reloadLove({ quiet: true });
      return;
    case "switch-project":
      love.projectId = button.dataset.id || "";
      if (love.projectId) writeStorage(PROJECT_KEY, love.projectId);
      love.selectedAnalysisId = "";
      love.lastReport = null;
      stopWechatPoll();
      resetWechatSourceState();
      resetMemoryState();
      syncProjectPicker();
      await renderLovePage();
      return;
    case "project-delete": {
      const id = button.dataset.id || "";
      const name = button.dataset.name || "这个项目";
      const confirmed = await confirmAction({
        title: `删除「${name}」？`,
        message: "会同时删除这个项目的消息、分析记录、画像和恋爱日志，且无法在应用内恢复。建议先确认数据库已有备份。",
        confirmLabel: "永久删除", danger: true,
      });
      if (!confirmed) return;
      try {
        await loveApi(`/api/love/projects/${encodeURIComponent(id)}`, {
          method: "DELETE", body: JSON.stringify({ confirm: true }), timeoutMs: 30000,
        });
        toast("项目已删除。", "success");
        if (love.projectId === id) {
          removeStorage(PROJECT_KEY);
          love.projectId = "";
        }
        await reloadLove({ quiet: true });
      } catch (error) {
        toast(`删除失败：${error.message}`, "error");
      }
      return;
    }
    default:
      return;
  }
}

const PAGE_RENDERERS = {
  dashboard: renderDashboard,
  import: renderImport,
  wechat: renderWechat,
  stats: renderStats,
  analysis: renderAnalysis,
  profile: renderProfile,
  journal: renderJournal,
  memory: renderMemory,
  report: renderReport,
  schedule: renderSchedule,
  settings: renderSettings,
};
