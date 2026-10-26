// 「恋爱系统」= 原生重做进「生活运营」的二级分区。
// 数据来自原有 love_analysis.db，由 torch 后端直接读写；这里只做界面与交互。
// 不在前端保存任何聊天内容：所有渲染都来自本机 API，页面刷新后重新读取。
import { $, api, escapeHtml, openDialog, confirmAction, toast } from "./core.js";

const PAGE_TITLES = {
  dashboard: "总览",
  import: "导入聊天",
  stats: "统计分析",
  analysis: "AI 深度分析",
  profile: "画像",
  journal: "恋爱日志",
  report: "报告",
  lijie: "丽洁追踪",
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
  lijieMode: "today",
  lijieDate: "",
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
      <p>聊天、画像与日志只保存在本机原有数据库中，不进入 Obsidian 库，也不参与普通导出。AI 分析只发送脱敏后的内容。</p>
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

// ── 丽洁追踪 ───────────────────────────────────────────────────────

async function renderLijie(content) {
  let endpoint = "/api/love/lijie/today";
  if (love.lijieMode === "tomorrow") endpoint = "/api/love/lijie/tomorrow";
  if (love.lijieMode === "date" && love.lijieDate) {
    endpoint = `/api/love/lijie/day?date=${encodeURIComponent(love.lijieDate)}`;
  }
  const [status, schedule] = await Promise.all([
    loveApi("/api/love/lijie/status", { timeoutMs: 20000 }),
    loveApi(endpoint, { timeoutMs: 45000 }),
  ]);
  if (love.page !== "lijie") return;
  const modeButtons = [["today", "今天"], ["tomorrow", "明天"]].map(([value, label]) =>
    `<button type="button" class="love-seg${love.lijieMode === value ? " active" : ""}" data-love-action="lijie-mode" data-value="${value}">${label}</button>`).join("");
  const toolbar = `<div class="love-lijie-toolbar">
    <div class="love-segmented">${modeButtons}</div>
    <div class="love-lijie-date">
      <input id="love-lijie-date" type="date" value="${escapeHtml(love.lijieDate || todayText())}" aria-label="选择日期" />
      <button class="small-button" type="button" data-love-action="lijie-date">查看</button>
    </div>
    <input id="love-lijie-file" type="file" accept=".xlsx,.xlsm" hidden />
    <button class="small-button" type="button" data-love-action="lijie-upload">替换交路表</button>
    <input id="love-lijie-timetable" type="file" accept=".json,application/json" hidden />
    <button class="small-button" type="button" data-love-action="lijie-upload-timetable" title="导入 train_timetable.json，补全车次经停信息">更新时刻表</button>
  </div>`;

  if (!schedule.found) {
    content.innerHTML = card("丽洁追踪", `
      ${toolbar}
      <div class="state-message error-state love-lijie-missing">
        <strong>${escapeHtml(schedule.error || "没有找到交路表")}</strong>
        <p>${schedule.available_date_range ? `当前交路表可用范围：${escapeHtml(schedule.available_date_range)}` : `把 .xlsx 交路表放进 ${escapeHtml(status.excel_dir || "交路表目录")}，或点「替换交路表」。`}</p>
      </div>
      <dl class="love-facts">
        <div><dt>交路表目录</dt><dd class="love-path">${escapeHtml(status.excel_dir || "—")}</dd></div>
        <div><dt>时刻表</dt><dd>${status.timetable_exists ? `${formatInt(status.timetable_trains)} 趟` : "未找到 train_timetable.json"}</dd></div>
      </dl>
      ${schedule.fallback_note ? `<p class="inline-warning">${escapeHtml(schedule.fallback_note)}</p>` : ""}
    `, { eyebrow: "LIJIE TRACKER", subtitle: "读取本机交路表与时刻表；天气走 Open-Meteo，不连接 12306。" });
    bindLijieFile();
    return;
  }

  const realtime = schedule.realtime;
  const missingTrains = (schedule.trains || [])
    .filter((train) => !(train.stops || []).length)
    .map((train) => train.train_code);
  const realtimeHtml = realtime ? `<div class="love-lijie-live status-${escapeHtml(realtime.status || "unknown")}">
    <div class="love-lijie-live-head"><span class="love-badge ${realtime.status === "running" ? "ok" : "warn"}">${escapeHtml(realtime.status_text || "运行中")}</span>
      <strong>${escapeHtml(realtime.message || "")}</strong></div>
    ${realtime.countdown_minutes ? `<p class="meta">倒计时约 ${formatInt(realtime.countdown_minutes)} 分钟</p>` : ""}
    <div class="love-progress"><i class="${widthClass(Number(realtime.progress_pct) || 0)}"></i></div>
    ${realtime.segment_progress_pct !== undefined ? `<div class="love-progress small"><i class="${widthClass(Number(realtime.segment_progress_pct) || 0)}"></i></div>` : ""}
  </div>` : "";

  const trainsHtml = (schedule.trains || []).map((train) => {
    const isCurrent = realtime?.current_train?.train_code === train.train_code;
    const stops = (train.stops || []).map((stop) => {
      const current = isCurrent && realtime?.current_station === stop.station;
      const next = isCurrent && realtime?.next_station === stop.station;
      const weather = stop.city_en ? schedule.weather?.[stop.city_en] : null;
      return `<li class="${current ? "current" : ""}${next ? " next" : ""}">
        <span class="love-stop-time">${escapeHtml(stop.arrive || stop.depart || "—")}</span>
        <span class="love-stop-dot" aria-hidden="true"></span>
        <span class="love-stop-name">${escapeHtml(stop.station || "")}${current ? ' <em>当前</em>' : next ? ' <em>下一站</em>' : ""}</span>
        <span class="meta">${stop.depart && stop.arrive && stop.depart !== stop.arrive ? `发 ${escapeHtml(stop.depart)}` : ""}${weather?.temp_c !== undefined ? ` · ${escapeHtml(String(weather.temp_c))}°C ${escapeHtml(weather.weather_desc || "")}` : ""}</span>
      </li>`;
    }).join("");
    return `<article class="love-train-card ${isCurrent ? "current" : ""}">
      <div class="love-train-head">
        <div><span class="eyebrow">TRAIN</span><h3>${escapeHtml(train.train_code)} <span>${escapeHtml(String(train.duration_minutes || 0))} 分钟</span></h3></div>
        ${isCurrent ? '<span class="love-badge ok">当前值乘</span>' : ""}
      </div>
      <div class="love-train-route">
        <div><strong>${escapeHtml(train.from_station)}</strong><span>${escapeHtml(train.depart_time)} 发</span></div>
        <span class="love-train-arrow" aria-hidden="true">→</span>
        <div><strong>${escapeHtml(train.to_station)}</strong><span>${escapeHtml(train.arrive_time)} 到</span></div>
      </div>
      ${stops ? `<ol class="love-stop-list">${stops}</ol>` : '<p class="meta">时刻表里没有这趟车的经停信息。</p>'}
    </article>`;
  }).join("");

  const weatherCards = Object.entries(schedule.weather || {}).map(([, weather]) => `
    <article class="love-weather-card"><span>${escapeHtml(weather.city || weather.city_en || "")}</span>
      <strong>${escapeHtml(String(weather.temp_c ?? "—"))}°C</strong>
      <small>${escapeHtml(weather.weather_desc || "")} · 体感 ${escapeHtml(String(weather.feels_like_c ?? "—"))}°C · 湿度 ${escapeHtml(String(weather.humidity ?? "—"))}%</small>
    </article>`).join("");

  content.innerHTML = `
    <div class="love-lijie-header">
      <div><span class="eyebrow">LIJIE TRACKER</span><h2>${escapeHtml(schedule.date_display)} <span class="meta">${escapeHtml(schedule.weekday)}</span></h2>
        <p class="meta">${escapeHtml(schedule.name || "")}${schedule.group ? ` · ${escapeHtml(schedule.group)}` : ""} · ${schedule.is_rest ? "休息" : `${formatInt(schedule.total_segments)} 趟值乘`}</p></div>
      ${toolbar}
    </div>
    ${schedule.fallback_note ? `<p class="inline-warning">${escapeHtml(schedule.fallback_note)}</p>` : ""}
    ${missingTrains.length ? `<p class="inline-warning">本机时刻表还缺少 ${missingTrains.map(escapeHtml).join("、")} 的经停信息；交路表本身已读到。补全 train_timetable.json 后，实时位置与沿线天气才会出现。</p>` : ""}
    ${schedule.is_rest ? `<div class="love-rest-card"><span>💤</span><strong>今天没有值乘任务</strong><p>交路表里这一天是休息。好好休息。</p></div>` : ""}
    ${realtimeHtml}
    <div class="love-lijie-grid">
      <div class="love-lijie-trains">${trainsHtml || (schedule.is_rest ? "" : emptyBox("没有车次信息", "交路表里这一天没有可识别的车次。"))}</div>
      <aside class="love-lijie-side">
        ${weatherCards ? `<div class="love-weather-grid">${weatherCards}</div>` : '<p class="field-help">暂时没有取到沿线天气；不影响交路信息。</p>'}
        <dl class="love-facts">
          <div><dt>交路表</dt><dd>${escapeHtml(schedule.excel_file || status.excel_file || "—")}</dd></div>
          <div><dt>可用范围</dt><dd>${status.date_range ? `${escapeHtml(status.date_range.start)} ~ ${escapeHtml(status.date_range.end)}` : "—"}</dd></div>
          <div><dt>时刻表</dt><dd>${status.timetable_exists ? `${formatInt(status.timetable_trains)} 趟` : "未找到"}</dd></div>
        </dl>
      </aside>
    </div>`;
  bindLijieFile();
}

function bindLijieFile() {
  const input = node("love-lijie-file");
  if (input && !input.dataset.bound) {
    input.dataset.bound = "1";
    input.addEventListener("change", async () => {
      const file = input.files?.[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      try {
        toast("正在上传交路表…", "info");
        const result = await loveApi("/api/love/lijie/excel", { method: "POST", body: form, timeoutMs: 120000 });
        toast(result.message || "交路表已更新。", "success");
        await renderLovePage();
      } catch (error) {
        toast(`替换失败：${error.message}`, "error");
      }
    });
  }
  const timetable = node("love-lijie-timetable");
  if (timetable && !timetable.dataset.bound) {
    timetable.dataset.bound = "1";
    timetable.addEventListener("change", async () => {
      const file = timetable.files?.[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      try {
        toast("正在导入时刻表…", "info");
        const result = await loveApi("/api/love/lijie/timetable", { method: "POST", body: form, timeoutMs: 60000 });
        toast(result.message || "时刻表已更新。", "success");
        await renderLovePage();
      } catch (error) {
        toast(`时刻表导入失败：${error.message}`, "error");
      }
    });
  }
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
      <p class="field-help">Key 使用 AES-256-GCM 加密后存进原有本机数据库；密钥文件在数据库同目录。页面不会回显已保存的 Key。</p>
    `, { eyebrow: "AI SETTINGS", subtitle: "只有 AI 分析、生成日记和报告会用到这把 Key；其余功能完全离线。" })}
    ${card("数据与存储", `
      <dl class="love-facts">
        <div><dt>本机数据库</dt><dd class="love-path">${escapeHtml(status.db_path || "—")}</dd></div>
        <div><dt>数据库大小</dt><dd>${status.db_size ? `${(Number(status.db_size) / 1024 / 1024).toFixed(1)} MB` : "—"}</dd></div>
        <div><dt>项目 / 消息 / 日志</dt><dd>${formatInt(status.project_count)} / ${formatInt(status.message_count)} / ${formatInt(status.journal_count)}</dd></div>
        <div><dt>原项目资源</dt><dd class="love-path">${escapeHtml(status.app_dir || "—")}</dd></div>
        <div><dt>交路表目录</dt><dd class="love-path">${escapeHtml(status.lijie_excel_dir || "—")}</dd></div>
      </dl>
      <p class="field-help">这份数据库是原「恋爱分析助手」的本机数据，不进入 Obsidian 库、不参与工作台备份。需要备份时请直接复制上面的数据库文件（先退出分析任务）。</p>
      <div class="love-inline-actions">
        <button class="button secondary" type="button" data-love-action="settings-refresh">重新检查</button>
        <button class="button secondary" type="button" data-love-action="goto" data-page="lijie">去丽洁追踪</button>
      </div>
    `, { eyebrow: "DATA BOUNDARY", subtitle: "原生接入后仍然只认这一份本机数据，不做云端同步。" })}
    ${card("怎么用", `
      <ol class="love-advice">
        <li>导入聊天记录，或直接使用已存在的项目。</li>
        <li>在「统计分析」里看消息量、回复间隔、媒体和高频词。</li>
        <li>配置 Key 后运行一次「AI 深度分析」；结论和画像都会保存在本机。</li>
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
    case "lijie-mode":
      love.lijieMode = button.dataset.value === "tomorrow" ? "tomorrow" : "today";
      await renderLovePage();
      return;
    case "lijie-date": {
      const value = node("love-lijie-date")?.value;
      if (!value) return;
      love.lijieDate = value;
      love.lijieMode = "date";
      await renderLovePage();
      return;
    }
    case "lijie-upload":
      node("love-lijie-file")?.click();
      return;
    case "lijie-upload-timetable":
      node("love-lijie-timetable")?.click();
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
  stats: renderStats,
  analysis: renderAnalysis,
  profile: renderProfile,
  journal: renderJournal,
  report: renderReport,
  lijie: renderLijie,
  settings: renderSettings,
};
