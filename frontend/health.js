// 「健康系统」= 生活运营下与恋爱系统平级的二级分区（2026-10-31）。
// 私人数据全部由后端读写 02_生活运营/02_健康系统 里的 Markdown；这里只做界面与交互，
// 不在浏览器里保存任何健康记录。评分、BMI、洞察都由后端算好，前端只负责展示与录入。
import { api, escapeHtml, jsonBody, openDialog, confirmAction, toast } from "./core.js";
import { openRoleDrawer } from "./roles.js";
import { COMPANIONS } from "./ai-companions.js";
import {
  SECTION_LABELS, todayText, shiftDay, weekdayLabel, shortDate, formatNumber,
  formatSigned, formatDuration, pointsFor, seriesStats,
  average, stddev, clockMinutes, lineChart, barChart, heatmap,
  ringMarkup, progressMarkup,
  macroProgress, insightTone,
} from "./health-model.js";

const PAGE_TITLES = {
  dashboard: "今日总览", body: "身体与体征", sleep: "睡眠", nutrition: "饮食营养",
  activity: "日常活动", mind: "心理健康", training: "训练", medical: "体检与医疗",
  trends: "趋势看板", insights: "关联洞察", reports: "健康报告", settings: "设置",
};
const RECORD_PAGES = new Set(["body", "sleep", "nutrition", "activity", "mind", "training"]);
const EMOTION = { 1: "😞", 2: "😕", 3: "😐", 4: "🙂", 5: "😄" };
const MOOD_OPTIONS = [
  [1, "很低落"], [2, "偏低"], [3, "一般"], [4, "不错"], [5, "很好"],
];
const PRESSURE_OPTIONS = [[1, "很放松"], [2, "较放松"], [3, "一般"], [4, "有点紧"], [5, "很紧张"]];
const ENERGY_OPTIONS = [[1, "没力气"], [2, "偏累"], [3, "一般"], [4, "有精神"], [5, "很充沛"]];
const ANXIETY_OPTIONS = [[1, "很安定"], [2, "较安定"], [3, "一般"], [4, "有点焦虑"], [5, "很焦虑"]];
const RATING_LABELS = {
  mood: MOOD_OPTIONS, stress: PRESSURE_OPTIONS, energy: ENERGY_OPTIONS,
  anxiety: ANXIETY_OPTIONS, quality: [[1, "很差"], [2, "较差"], [3, "一般"], [4, "不错"], [5, "很好"]],
  rpe: Array.from({ length: 10 }, (_item, index) => [index + 1, String(index + 1)]),
  soreness: [[1, "没有"], [2, "轻微"], [3, "明显"], [4, "较重"], [5, "很酸"]],
  mood_after: MOOD_OPTIONS,
};
const MEAL_TYPES = ["早餐", "午餐", "晚餐", "加餐"];
const TRAINING_KINDS = ["力量", "有氧", "球类", "柔韧", "其他"];
const DIMENSION_LABELS = {
  sleep: "睡眠", nutrition: "营养", activity: "活动",
  mental: "心理", body: "身体", training: "训练",
};

const health = {
  inited: false,
  loading: false,
  page: "dashboard",
  day: todayText(),
  overview: null,
  history: [],
  historyDays: 30,
  trends: null,
  trendMetrics: ["sleep.duration_min", "sleep.score"],
  trendDays: 30,
  insights: [],
  insightDays: 90,
  labs: null,
  reports: [],
  error: "",
  draft: null,
  exerciseResults: [],
  exerciseFacets: null,
};

const node = (id) => document.getElementById(id);

function healthApi(path, options = {}) {
  return api(path, { timeoutMs: 30000, ...options });
}

function toneOf(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "none";
  if (number >= 85) return "great";
  if (number >= 70) return "good";
  if (number >= 55) return "fair";
  return "low";
}

function scoreMarkup(score, { size = 92, label = "" } = {}) {
  return ringMarkup(score?.total ?? null, { size, label: label || "健康指数" });
}

function dimensionBars(score) {
  const breakdown = score?.breakdown || {};
  return `<div class="health-dimensions">${Object.entries(DIMENSION_LABELS).map(([key, label]) => {
    const value = breakdown[key];
    const known = Number.isFinite(Number(value));
    return `<div class="health-dimension" data-tone="${known ? toneOf(value) : "none"}">
      <span class="health-dimension-label">${label}</span>
      ${progressMarkup(known ? Number(value) : 0, { className: "health-dimension-progress", label: `${label}得分` })}
      <span class="health-dimension-value">${known ? Math.round(value) : "不足"}</span>
    </div>`;
  }).join("")}</div>`;
}

function emptyState(title, text, action = "") {
  return `<div class="health-empty"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(text)}</p>${action}</div>`;
}

function sectionValues(record, section) {
  return (record?.sections || {})[section] || {};
}

function fieldLabel(field) {
  return `${field.label}${field.unit ? `（${field.unit}）` : ""}`;
}

function controlMarkup(field, value, { idPrefix = "hf" } = {}) {
  const id = `${idPrefix}-${field.field}`;
  const data = `data-field="${escapeHtml(field.field)}"`;
  if (field.kind === "time") {
    return `<input id="${id}" type="time" ${data} value="${escapeHtml(value || "")}" />`;
  }
  if (field.kind === "text") {
    return `<input id="${id}" type="text" ${data} maxlength="200" value="${escapeHtml(value ?? "")}" />`;
  }
  if (field.kind === "rating") {
    const options = RATING_LABELS[field.field] || Array.from({ length: Number(field.max) || 5 },
      (_item, index) => [index + 1, String(index + 1)]);
    const selected = Number(value);
    return `<select id="${id}" ${data}><option value="">未记录</option>${options.map(([optionValue, label]) =>
      `<option value="${optionValue}"${Number(optionValue) === selected ? " selected" : ""}>${escapeHtml(label)}</option>`).join("")}</select>`;
  }
  const step = field.step ?? (field.kind === "number" ? 0.1 : 1);
  const minimum = field.min ?? "";
  const maximum = field.max ?? "";
  return `<input id="${id}" type="number" inputmode="decimal" ${data} step="${step}" min="${minimum}" max="${maximum}" value="${value ?? ""}" placeholder="未记录" />`;
}

function sectionForm(section, record, { title = "", submit = "保存" } = {}) {
  const catalog = health.overview?.catalog;
  const group = (catalog?.groups || []).find((item) => item.key === section);
  if (!group) return "";
  const values = sectionValues(record, section);
  const fields = group.fields.map((field) => `<label class="health-field">
      <span class="health-field-label">${escapeHtml(fieldLabel(field))}${field.help ? `<em title="${escapeHtml(field.help)}">?</em>` : ""}</span>
      ${controlMarkup(field, values[field.field])}
    </label>`).join("");
  return `<form class="health-form" data-health-form="section" data-section="${section}" novalidate>
    ${title ? `<h3>${escapeHtml(title)}</h3>` : ""}
    <div class="health-form-grid">${fields}</div>
    <div class="health-form-actions">
      <span class="health-form-status status-message" role="status"></span>
      <button class="button primary" type="submit">${escapeHtml(submit)}</button>
    </div>
  </form>`;
}

function readFields(form) {
  const values = {};
  for (const input of form.querySelectorAll("[data-field]")) {
    const raw = String(input.value ?? "").trim();
    values[input.dataset.field] = raw === "" ? null : (input.type === "number" ? Number(raw) : raw);
  }
  return values;
}

function goalInputs() {
  const goals = health.overview?.profile?.goals || {};
  const labels = {
    weight_kg: "目标体重（kg）", body_fat_pct: "目标体脂（%）", sleep_minutes: "目标睡眠（分钟）",
    steps: "目标步数", water_ml: "目标饮水（ml）", weekly_training: "每周训练（次）",
    calories: "目标热量（kcal）", protein_g: "目标蛋白（g）",
  };
  return Object.entries(labels).map(([key, label]) => `<label class="health-field">
      <span class="health-field-label">${escapeHtml(label)}</span>
      <input type="number" data-goal="${key}" step="1" min="0" value="${goals[key] ?? ""}" placeholder="自动" />
    </label>`).join("");
}

// ── 加载与导航 ───────────────────────────────────────────────────────

export function initHealthPane() {
  if (health.inited) return;
  health.inited = true;
  document.querySelectorAll("[data-health-page]").forEach((button) => {
    button.addEventListener("click", () => { navigate(button.dataset.healthPage); });
  });
  node("health-refresh")?.addEventListener("click", () => refresh({ quiet: true }));
  node("health-doctor")?.addEventListener("click", () => {
    openRoleDrawer({
      ...COMPANIONS.health,
      selectors: [{ entry: "health" }],
      prompt: "基于健康系统最近的记录，给我三条今天能做的调整。",
    });
  });
  node("health-day")?.addEventListener("change", (event) => {
    const value = String(event.target.value || "").trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return;
    health.day = value;
    refresh({ quiet: true });
  });
  node("health-prev-day")?.addEventListener("click", () => shiftSelectedDay(-1));
  node("health-next-day")?.addEventListener("click", () => shiftSelectedDay(1));
  node("health-today")?.addEventListener("click", () => {
    health.day = todayText();
    if (node("health-day")) node("health-day").value = health.day;
    refresh({ quiet: true });
  });

  const content = node("health-content");
  content?.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-health-form]");
    if (!form) return;
    event.preventDefault();
    submitForm(form).catch((error) => failForm(form, error));
  });
  content?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-health-action]");
    if (!button) return;
    Promise.resolve(handleAction(button, event)).catch((error) => {
      toast(`操作失败：${error.message}`, "error");
    });
  });
  content?.addEventListener("input", (event) => {
    if (event.target.matches("[data-exercise-search]")) scheduleExerciseSearch(event.target.value);
    if (event.target.matches("[data-exercise-body]")) scheduleExerciseSearch(event.target.value);
    if (event.target.matches("[data-set-reps], [data-set-weight]")) syncDraftFromDom();
  });
  content?.addEventListener("change", (event) => {
    if (event.target.matches("[data-trend-metric]")) toggleTrendMetric(event.target);
    if (event.target.matches("[data-lab-preset]")) fillLabPreset(event.target);
    if (event.target.matches("[data-exercise-body]")) runExerciseSearch("");
  });
}

function shiftSelectedDay(delta) {
  health.day = shiftDay(health.day, delta);
  if (node("health-day")) node("health-day").value = health.day;
  refresh({ quiet: true });
}

export async function loadHealthPane() {
  if (!health.inited) initHealthPane();
  if (health.loading) return;
  if (node("health-day")) node("health-day").value = health.day;
  await refresh();
}

async function refresh({ quiet = false } = {}) {
  if (health.loading) return;
  health.loading = true;
  health.error = "";
  const content = node("health-content");
  if (!quiet && content) content.innerHTML = `<div class="health-loading">正在读取健康记录…</div>`;
  try {
    const overview = await healthApi(`/api/health/overview?day=${encodeURIComponent(health.day)}`);
    health.overview = overview;
    health.day = overview.day || health.day;
    if (node("health-day")) node("health-day").value = health.day;
    await ensurePageData();
    renderPage();
  } catch (error) {
    health.error = error.message;
    if (content) {
      content.innerHTML = `<div class="health-error"><strong>健康数据暂时读不到</strong><p>${escapeHtml(error.message)}</p>
        <button class="button secondary" type="button" data-health-action="retry">重试</button></div>`;
    }
  } finally {
    health.loading = false;
  }
}

async function navigate(page) {
  const target = PAGE_TITLES[page] ? page : "dashboard";
  if (health.page === target) return;
  health.page = target;
  document.querySelectorAll("[data-health-page]").forEach((button) => {
    const active = button.dataset.healthPage === target;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  const title = node("health-title");
  if (title) title.textContent = PAGE_TITLES[target];
  if (health.overview) {
    try {
      await ensurePageData();
      renderPage();
    } catch (error) {
      const content = node("health-content");
      if (content) content.innerHTML = `<div class="health-error"><strong>读取失败</strong><p>${escapeHtml(error.message)}</p></div>`;
    }
  } else {
    await refresh();
  }
}

async function ensurePageData() {
  if (RECORD_PAGES.has(health.page)) {
    const data = await healthApi(`/api/health/history?days=30&end=${encodeURIComponent(health.day)}`);
    health.history = Array.isArray(data.records) ? data.records : [];
    health.historyDays = 30;
    return;
  }
  if (health.page === "medical") {
    health.labs = await healthApi("/api/health/labs");
    return;
  }
  if (health.page === "trends") {
    health.trends = await healthApi(`/api/health/trends?metrics=${encodeURIComponent(health.trendMetrics.join(","))}&days=${health.trendDays}&end=${encodeURIComponent(health.day)}`);
    return;
  }
  if (health.page === "insights") {
    const data = await healthApi(`/api/health/insights?days=${health.insightDays}&end=${encodeURIComponent(health.day)}`);
    health.insights = Array.isArray(data.insights) ? data.insights : [];
    return;
  }
  if (health.page === "reports") {
    const data = await healthApi("/api/health/reports");
    health.reports = Array.isArray(data.reports) ? data.reports : [];
    return;
  }
  if (health.page === "training") {
    health.exerciseFacets = health.exerciseFacets
      || (await healthApi("/api/health/exercises?limit=1")).facets;
  }
}

function renderPage() {
  const content = node("health-content");
  if (!content || !health.overview) return;
  const title = node("health-title");
  if (title) title.textContent = PAGE_TITLES[health.page] || "健康系统";
  const menu = node("health-menu-status");
  if (menu) {
    const score = health.overview.today?.score?.total;
    menu.textContent = Number.isFinite(Number(score))
      ? `今日健康指数 ${Math.round(score)} · 记录只在本机`
      : "今天还没有足够数据 · 记录只在本机";
  }
  const renderers = {
    dashboard: renderDashboard, body: renderBody, sleep: renderSleep,
    nutrition: renderNutrition, activity: renderActivity, mind: renderMind,
    training: renderTraining, medical: renderMedical, trends: renderTrends,
    insights: renderInsights, reports: renderReports, settings: renderSettings,
  };
  content.innerHTML = (renderers[health.page] || renderDashboard)();
  if (health.page === "training") {
    renderDraft();
    if (!health.exerciseResults.length) runExerciseSearch("");
  }
}

function dayHeading() {
  const today = health.day === todayText();
  return `${health.day} 周${weekdayLabel(health.day)}${today ? " · 今天" : ""}`;
}

// ── 今日总览 ─────────────────────────────────────────────────────────

function renderDashboard() {
  const overview = health.overview;
  const today = overview.today || {};
  const score = today.score || {};
  const missing = Array.isArray(overview.missing) ? overview.missing : [];
  const targets = overview.targets || {};
  const nutrition = today.nutrition || {};
  const sections = today.sections || {};
  const sleep = sections.sleep || {};
  const body = sections.body || {};
  const activity = sections.activity || {};
  const mind = sections.mind || {};
  const training = today.training || [];
  const week = Array.isArray(overview.week) ? overview.week : [];
  const labs = overview.labs || {};
  const reports = Array.isArray(overview.reports) ? overview.reports : [];

  return `<div class="health-dashboard">
    <section class="health-hero">
      ${scoreMarkup(score, { size: 96 })}
      <div class="health-hero-copy">
        <p class="eyebrow">TODAY</p>
        <h2>${escapeHtml(dayHeading())}</h2>
        <p class="health-hero-note">${score.total === null || score.total === undefined
          ? "今天的数据还不够算分；先记录睡眠、体重或情绪。"
          : `综合睡眠、营养、活动、心理、身体与训练${score.insufficient?.length ? `；${score.insufficient.join("、")}数据不足，未计入` : ""}。`}</p>
        <p class="health-hero-targets">目标：睡眠 ${formatDuration(targets.sleep_minutes)} · 步数 ${formatNumber(targets.steps, 0)} · 饮水 ${formatNumber(targets.water_ml, 0)} ml</p>
      </div>
      <div class="health-hero-actions">
        <button class="button primary" type="button" data-health-action="edit-section" data-section="sleep">记录睡眠</button>
        <button class="button secondary" type="button" data-health-action="edit-section" data-section="body">记体重</button>
        <button class="button ghost" type="button" data-health-action="edit-section" data-section="mind">打分情绪</button>
      </div>
    </section>

    ${dimensionBars(score)}

    <section class="health-week" aria-label="最近七天">
      ${week.map((item) => `<button type="button" class="health-week-day${item.has_data ? "" : " empty"}" data-health-action="pick-day" data-day="${item.date}" title="${escapeHtml(item.date)}">
        <span>周${escapeHtml(weekdayLabel(item.date))}</span>
        <strong class="${item.score ? `tone-${toneOf(item.score)}` : ""}">${item.score ?? "—"}</strong>
        <small>${item.sleep_minutes ? formatDuration(item.sleep_minutes) : "无睡眠"}</small>
        <small>${item.steps ? `${formatNumber(item.steps, 0)} 步` : "无步数"}</small>
      </button>`).join("")}
    </section>

    ${labs.abnormal?.length ? `<section class="health-banner warn"><strong>检验提示</strong><span>${escapeHtml(labs.abnormal.map((row) => `${row.test} ${row.value}${row.unit}`).join("；"))} 超出参考区间；这是提示，不是诊断。</span></section>` : ""}

    <div class="health-card-grid">
      ${dashboardCard("sleep", "睡眠", sleep.duration_min ? formatDuration(sleep.duration_min) : "还没记录",
        [["入睡 → 起床", sleep.bed && sleep.wake ? `${sleep.bed} → ${sleep.wake}` : "—"],
         ["主观质量", sleep.quality ? `${sleep.quality}/5` : "—"],
         ["睡眠评分", score.derived?.sleep_score ? `${score.derived.sleep_score} 分` : "—"]])}
      ${dashboardCard("body", "身体", body.weight_kg ? `${formatNumber(body.weight_kg, 1, "kg")}` : "还没称重",
        [["BMI", score.derived?.bmi ? `${score.derived.bmi}（${score.derived.bmi_band}）` : "—"],
         ["体脂率", body.body_fat_pct ? `${formatNumber(body.body_fat_pct, 1, "%")}` : "—"],
         ["基础代谢", body.bmr || score.derived?.bmr ? `${formatNumber(body.bmr || score.derived.bmr, 0, "kcal")}` : "—"]])}
      ${dashboardCard("nutrition", "饮食", `${formatNumber(nutrition.calories, 0, "kcal")} / ${formatNumber(targets.calories, 0, "kcal")}`,
        [["蛋白质", `${formatNumber(nutrition.protein_g, 0, "g")} / ${formatNumber(targets.protein_g, 0, "g")}`],
         ["饮水", `${formatNumber(nutrition.water_ml, 0, "ml")} / ${formatNumber(targets.water_ml, 0, "ml")}`],
         ["餐次", `${(today.meals || []).length} 条`]])}
      ${dashboardCard("activity", "活动", activity.steps ? `${formatNumber(activity.steps, 0, "步")}` : "还没记录",
        [["目标", `${formatNumber(targets.steps, 0, "步")}`],
         ["久坐", activity.sedentary_min ? formatDuration(activity.sedentary_min) : "—"],
         ["活动消耗", activity.active_cal ? `${formatNumber(activity.active_cal, 0, "kcal")}` : "—"]])}
      ${dashboardCard("mind", "心理", mind.mood ? `${EMOTION[mind.mood]} ${mind.mood}/5` : "还没打分",
        [["压力", mind.stress ? `${mind.stress}/5` : "—"],
         ["精力", mind.energy ? `${mind.energy}/5` : "—"],
         ["正念", mind.mindful_min ? `${formatNumber(mind.mindful_min, 0, "分钟")}` : "—"]])}
      ${dashboardCard("training", "训练", training.length ? `${training.length} 次` : "今天没有训练",
        [["容量", today.training_totals?.volume_kg ? `${formatNumber(today.training_totals.volume_kg, 0, "kg")}` : "—"],
         ["时长", today.training_totals?.minutes ? `${formatNumber(today.training_totals.minutes, 0, "分钟")}` : "—"],
         ["RPE", training[0]?.rpe ? `${training[0].rpe}/10` : "—"]],
        `<button class="text-button" type="button" data-health-action="go-page" data-page="training">查看训练 →</button>`)}
    </div>

    <section class="health-panel">
      <div class="health-panel-head"><h3>今天还没记录</h3><span>${missing.length ? `${missing.length} 项` : "都记了"}</span></div>
      ${missing.length ? `<div class="health-missing-chips">${missing.map((item) =>
        `<button type="button" class="health-chip" data-health-action="edit-section" data-section="${escapeHtml(item.key.split(".")[0])}">＋ ${escapeHtml(item.label)}</button>`).join("")}</div>`
        : `<p class="health-muted">睡眠、体重、饮水、步数、情绪都有记录了。</p>`}
    </section>

    <section class="health-panel">
      <div class="health-panel-head"><h3>最近报告</h3><button class="text-button" type="button" data-health-action="go-page" data-page="reports">全部 →</button></div>
      ${reports.length ? `<ul class="health-report-list">${reports.slice(0, 3).map((item) =>
        `<li><button type="button" class="health-link" data-health-action="preview-report" data-path="${escapeHtml(item.path || "")}">${escapeHtml(item.title || "健康报告")}</button><span>${escapeHtml(item.date || "")}</span></li>`).join("")}</ul>`
        : `<p class="health-muted">还没有报告；可以在「健康报告」里让私人医生按最近 7 天生成一份。</p>`}
    </section>

    ${customMetricsCard(today)}
  </div>`;
}

function customMetricsCard(today) {
  const custom = health.overview?.profile?.custom_metrics || [];
  if (!custom.length) return "";
  const values = today.custom || {};
  return `<section class="health-panel">
    <div class="health-panel-head"><h3>自定义指标</h3><span>${custom.length} 个</span></div>
    <form class="health-form compact" data-health-form="custom">
      <div class="health-form-grid">${custom.map((item) => `<label class="health-field">
        <span class="health-field-label">${escapeHtml(item.label)}${item.unit ? `（${escapeHtml(item.unit)}）` : ""}</span>
        <input type="number" step="0.1" data-custom-field="${escapeHtml(item.label)}" value="${values[item.label] ?? ""}" placeholder="未记录" /></label>`).join("")}</div>
      <div class="health-form-actions"><span class="health-form-status status-message" role="status"></span>
        <button class="button secondary" type="submit">保存自定义指标</button></div>
    </form>
  </section>`;
}

function dashboardCard(section, title, headline, rows, extra = "") {
  return `<article class="health-card" data-tone="${section}">
    <div class="health-card-head"><div><span class="eyebrow">${escapeHtml(title)}</span><h3>${escapeHtml(headline)}</h3></div>
      <button class="small-button" type="button" data-health-action="edit-section" data-section="${section}">记录</button></div>
    <dl class="health-stats">${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>
    ${extra}
  </article>`;
}

// ── 身体与体征 ───────────────────────────────────────────────────────

function recordStats(points, { unit = "", precision = 0 } = {}) {
  const stats = seriesStats(points);
  if (!stats.count) return `<p class="health-muted">还没有足够数据。</p>`;
  return `<dl class="health-stats inline">
    <div><dt>最新</dt><dd>${formatNumber(stats.latest, precision, unit)}</dd></div>
    <div><dt>均值</dt><dd>${formatNumber(stats.avg, precision, unit)}</dd></div>
    <div><dt>区间</dt><dd>${formatNumber(stats.min, precision)}–${formatNumber(stats.max, precision, unit)}</dd></div>
    <div><dt>变化</dt><dd>${formatSigned(stats.delta, precision, unit)}</dd></div>
  </dl>`;
}

function renderBody() {
  const today = health.overview.today || {};
  const score = today.score || {};
  const derived = score.derived || {};
  const values = sectionValues(today, "body");
  const history = health.history || [];
  const weightPoints = pointsFor(history, "body.weight_kg");
  const fatPoints = pointsFor(history, "body.body_fat_pct");
  const waistPoints = pointsFor(history, "body.waist_cm");
  const latest = weightPoints[weightPoints.length - 1];
  const previous = weightPoints[weightPoints.length - 2];
  const delta = latest && previous ? latest.value - previous.value : null;
  const chart = lineChart([
    { label: "体重", points: weightPoints, unit: "kg", color: "var(--health-series-0)" },
    { label: "体脂率", points: fatPoints, unit: "%", color: "var(--health-series-1)" },
    { label: "腰围", points: waistPoints, unit: "cm", color: "var(--health-series-2)" },
  ], { normalize: false });

  return `<div class="health-page-grid">
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>身体成分</h3><span>${latest ? `最新 ${latest.date}` : "还没有记录"}</span></div>
      <div class="health-metric-row">
        <div class="health-metric"><span>BMI</span><strong>${derived.bmi ?? "—"}</strong><small>${escapeHtml(derived.bmi_band || "需要身高与体重")}</small></div>
        <div class="health-metric"><span>体重</span><strong>${formatNumber(values.weight_kg, 1, "kg")}</strong><small>${delta === null ? "没有上一次对比" : `比上次 ${formatSigned(delta, 1, "kg")}`}</small></div>
        <div class="health-metric"><span>体脂</span><strong>${formatNumber(values.body_fat_pct, 1, "%")}</strong><small>参考 10–28%</small></div>
        <div class="health-metric"><span>基础代谢</span><strong>${formatNumber(values.bmr || derived.bmr, 0, "kcal")}</strong><small>Mifflin-St Jeor 估算</small></div>
      </div>
      ${sectionForm("body", today, { title: "记录今天的身体数据" })}
    </section>
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>30 天趋势</h3><span>${escapeHtml(health.day)}</span></div>
      ${chart.svg}
      ${recordStats(weightPoints, { precision: 1, unit: "kg" })}
    </section>
    <section class="health-panel">
      <div class="health-panel-head"><h3>生命体征</h3><span>${escapeHtml(health.day)}</span></div>
      ${sectionForm("vitals", today, { submit: "保存体征" })}
      ${abnormalMarkup(today)}
    </section>
    <section class="health-panel">
      <div class="health-panel-head"><h3>最近记录</h3><span>近 30 天</span></div>
      ${recentBodyList(history)}
    </section>
  </div>`;
}

function abnormalMarkup(today) {
  const items = Array.isArray(today.abnormal) ? today.abnormal : [];
  if (!items.length) return `<p class="health-muted">今天的体征没有超出参考区间的记录。</p>`;
  return `<ul class="health-abnormal">${items.map((item) =>
    `<li><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.value)}</span><em>${escapeHtml(item.level)}</em></li>`).join("")}</ul>`;
}

function recentBodyList(history) {
  const rows = (history || []).filter((record) => (record.sections?.body || {}).weight_kg)
    .slice(-14).reverse();
  if (!rows.length) return `<p class="health-muted">还没有体重记录。</p>`;
  return `<ul class="health-rows">${rows.map((record) => {
    const body = record.sections.body || {};
    return `<li><span>${escapeHtml(shortDate(record.date))}</span>
      <strong>${formatNumber(body.weight_kg, 1, "kg")}</strong>
      <small>体脂 ${formatNumber(body.body_fat_pct, 1, "%")} · 腰围 ${formatNumber(body.waist_cm, 1, "cm")}</small></li>`;
  }).join("")}</ul>`;
}

// ── 睡眠 ─────────────────────────────────────────────────────────────

function renderSleep() {
  const today = health.overview.today || {};
  const sleep = sectionValues(today, "sleep");
  const score = today.score || {};
  const detail = score.derived || {};
  const history = health.history || [];
  const durationPoints = pointsFor(history, "sleep.duration_min").slice(-7);
  const scorePoints = pointsFor(history, "sleep.score").slice(-30);
  const durations = pointsFor(history, "sleep.duration_min").map((point) => point.value);
  const bedtimes = (history || []).map((record) => clockMinutes(record.sections?.sleep?.bed))
    .filter((value) => value !== null);
  const regularity = stddev(bedtimes);
  const parts = sleepScoreParts(sleep);

  return `<div class="health-page-grid">
    <section class="health-panel">
      <div class="health-panel-head"><h3>昨晚睡眠</h3><span>${sleep.duration_min ? formatDuration(sleep.duration_min) : "没有记录"}</span></div>
      <div class="health-metric-row compact">
        <div class="health-metric"><span>睡眠评分</span><strong>${detail.sleep_score ?? "—"}</strong><small>${escapeHtml(detail.sleep_rating || "需要时长")}</small></div>
        <div class="health-metric"><span>入睡 → 起床</span><strong>${sleep.bed && sleep.wake ? `${escapeHtml(sleep.bed)}–${escapeHtml(sleep.wake)}` : "—"}</strong><small>按起床日归属</small></div>
      </div>
      ${parts}
    </section>
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>近 7 天时长</h3><span>分钟</span></div>
      ${barChart(durationPoints.map((point) => ({ ...point, color: point.value >= 420 ? "var(--health-good)" : "var(--health-warn)" })), { unit: "分钟", height: 160 })}
    </section>
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>30 天睡眠评分</h3><span>0–100</span></div>
      ${lineChart([{ label: "睡眠评分", points: scorePoints, unit: "分", color: "var(--health-series-1)" }], { height: 190 }).svg}
      <dl class="health-stats inline">
        <div><dt>平均时长</dt><dd>${durations.length ? formatDuration(average(durations)) : "—"}</dd></div>
        <div><dt>规律度</dt><dd>${regularity === null ? "—" : `±${Math.round(regularity)} 分钟`}</dd></div>
        <div><dt>记录天数</dt><dd>${durations.length}</dd></div>
      </dl>
    </section>
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>记录睡眠</h3><span>手环数据可以照抄</span></div>
      ${sectionForm("sleep", today, { submit: "保存睡眠" })}
    </section>
  </div>`;
}

function sleepScoreParts(sleep) {
  const duration = Number(sleep.duration_min);
  if (!duration) return "";
  const total = duration || 1;
  const rows = [
    ["深睡占比", sleep.deep_min ? `${Math.round((sleep.deep_min / total) * 100)}%` : "—"],
    ["REM 占比", sleep.rem_min ? `${Math.round((sleep.rem_min / total) * 100)}%` : "—"],
    ["入睡耗时", sleep.latency_min ? `${sleep.latency_min} 分钟` : "—"],
    ["夜醒", sleep.wake_count ?? "—"],
  ];
  return `<dl class="health-stats inline">${rows.map(([label, value]) =>
    `<div><dt>${label}</dt><dd>${escapeHtml(String(value))}</dd></div>`).join("")}</dl>`;
}

// ── 饮食 ─────────────────────────────────────────────────────────────

function renderNutrition() {
  const today = health.overview.today || {};
  const totals = today.nutrition || {};
  const targets = health.overview.targets || {};
  const meals = Array.isArray(today.meals) ? today.meals : [];
  const history = health.history || [];
  const caloriesPoints = pointsFor(history, "nutrition.calories");
  const weightPoints = pointsFor(history, "body.weight_kg");
  const macros = macroProgress(totals, targets);
  const caloriePercent = targets.calories ? Math.min(100, (Number(totals.calories || 0) / targets.calories) * 100) : 0;

  return `<div class="health-page-grid">
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>今日摄入</h3><span>${escapeHtml(health.day)}</span></div>
      <div class="health-calorie">
        ${progressMarkup(caloriePercent, { className: "health-calorie-progress", label: "今日热量完成度" })}
        <div class="health-calorie-copy"><strong>${formatNumber(totals.calories, 0, "kcal")}</strong><span>目标 ${formatNumber(targets.calories, 0, "kcal")}</span></div>
      </div>
      <div class="health-macro-grid">${macros.map((macro) => `<div class="health-macro">
        <span>${escapeHtml(macro.label)}</span>
        <strong>${formatNumber(macro.value, 0, macro.unit)}</strong>
        <small>${macro.target ? `目标 ${formatNumber(macro.target, 0, macro.unit)}` : "目标未设置"}</small>
        ${progressMarkup(macro.percent, { className: `health-macro-progress ${macro.cls}`, label: `${macro.label}完成度` })}
      </div>`).join("")}</div>
      <div class="health-water">
        <div><span>饮水</span><strong>${formatNumber(totals.water_ml, 0, "ml")}</strong><small>目标 ${formatNumber(targets.water_ml, 0, "ml")}</small></div>
        <div class="health-water-actions">
          <button class="button secondary" type="button" data-health-action="water" data-delta="250">＋250</button>
          <button class="button secondary" type="button" data-health-action="water" data-delta="500">＋500</button>
          <button class="button ghost" type="button" data-health-action="water" data-delta="-250">－250</button>
        </div>
      </div>
    </section>

    <section class="health-panel wide">
      <div class="health-panel-head"><h3>餐次</h3><span>${meals.length} 条</span></div>
      ${meals.length ? `<div class="health-meals">${MEAL_TYPES.map((type) => {
        const group = meals.filter((meal) => meal.meal_type === type || (!meal.meal_type && type === "加餐"));
        if (!group.length) return "";
        return `<div class="health-meal-group"><h4>${type}</h4>
          ${group.map((meal) => `<div class="health-meal">
            <span>${escapeHtml(meal.name)}</span>
            <small>${formatNumber(meal.calories, 0, "kcal")} · 蛋白 ${formatNumber(meal.protein_g, 0, "g")}</small>
            <button class="icon-button" type="button" aria-label="删除 ${escapeHtml(meal.name)}" data-health-action="delete-meal" data-id="${escapeHtml(meal.id)}">×</button>
          </div>`).join("")}</div>`;
      }).join("")}</div>` : `<p class="health-muted">还没有餐次记录；加上一条，热量与宏量会自动合计。</p>`}
      <form class="health-form compact" data-health-form="meal">
        <div class="health-form-grid">
          <label class="health-field"><span class="health-field-label">餐次</span>
            <select data-field="meal_type">${MEAL_TYPES.map((type) => `<option value="${type}">${type}</option>`).join("")}</select></label>
          <label class="health-field"><span class="health-field-label">食物</span>
            <input type="text" data-field="name" maxlength="60" placeholder="例如：鸡胸肉饭" required /></label>
          <label class="health-field"><span class="health-field-label">热量（kcal）</span>
            <input type="number" data-field="calories" min="0" max="10000" step="1" /></label>
          <label class="health-field"><span class="health-field-label">蛋白（g）</span>
            <input type="number" data-field="protein_g" min="0" max="1000" step="1" /></label>
          <label class="health-field"><span class="health-field-label">碳水（g）</span>
            <input type="number" data-field="carbs_g" min="0" max="1000" step="1" /></label>
          <label class="health-field"><span class="health-field-label">脂肪（g）</span>
            <input type="number" data-field="fat_g" min="0" max="1000" step="1" /></label>
        </div>
        <div class="health-form-actions"><span class="health-form-status status-message" role="status"></span>
          <button class="button primary" type="submit">添加餐次</button></div>
      </form>
    </section>

    <section class="health-panel wide">
      <div class="health-panel-head"><h3>30 天热量与体重</h3><span>量纲不同时按区间归一化</span></div>
      ${lineChart([
        { label: "热量", points: caloriesPoints, unit: "kcal", color: "var(--health-series-2)" },
        { label: "体重", points: weightPoints, unit: "kg", color: "var(--health-series-0)" },
      ], { height: 200 }).svg}
    </section>

    <section class="health-panel wide">
      <div class="health-panel-head"><h3>营养目标与饮水</h3><span>在设置里调整目标</span></div>
      <p class="health-note">有餐次记录时，热量与三大宏量以餐次合计为准；这里适合补记饮水、咖啡因、酒精，或没有餐次明细时填当天总量。</p>
      ${sectionForm("nutrition", today, { submit: "保存营养与饮水" })}
    </section>
  </div>`;
}

// ── 活动 ─────────────────────────────────────────────────────────────

function renderActivity() {
  const today = health.overview.today || {};
  const activity = sectionValues(today, "activity");
  const targets = health.overview.targets || {};
  const history = health.history || [];
  const steps = pointsFor(history, "activity.steps");
  const sedentary = pointsFor(history, "activity.sedentary_min");
  const overTarget = steps.filter((point) => point.value >= (targets.steps || 8000)).length;
  return `<div class="health-page-grid">
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>今日活动</h3><span>目标 ${formatNumber(targets.steps, 0, "步")}</span></div>
      <div class="health-metric-row">
        <div class="health-metric"><span>步数</span><strong>${formatNumber(activity.steps, 0)}</strong><small>${activity.steps ? `${Math.round((activity.steps / (targets.steps || 8000)) * 100)}% 目标` : "还没记录"}</small></div>
        <div class="health-metric"><span>久坐</span><strong>${activity.sedentary_min ? formatDuration(activity.sedentary_min) : "—"}</strong><small>建议少于 8 小时</small></div>
        <div class="health-metric"><span>活动消耗</span><strong>${formatNumber(activity.active_cal, 0, "kcal")}</strong><small>设备估算</small></div>
        <div class="health-metric"><span>距离</span><strong>${formatNumber(activity.distance_km, 1, "km")}</strong><small>可选</small></div>
      </div>
      ${sectionForm("activity", today, { submit: "保存活动" })}
    </section>
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>近 30 天步数</h3><span>达标 ${overTarget} 天</span></div>
      ${barChart(steps, { unit: "步", height: 180 })}
      ${recordStats(steps, { precision: 0, unit: "步" })}
    </section>
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>久坐时长</h3><span>30 天</span></div>
      ${lineChart([{ label: "久坐", points: sedentary, unit: "分钟", color: "var(--health-series-3)" }], { height: 180 }).svg}
    </section>
  </div>`;
}

// ── 心理 ─────────────────────────────────────────────────────────────

function renderMind() {
  const today = health.overview.today || {};
  const mind = sectionValues(today, "mind");
  const history = health.history || [];
  const moodPoints = pointsFor(history, "mind.mood");
  const stressPoints = pointsFor(history, "mind.stress");
  const moodAverage = average(moodPoints.map((point) => point.value));
  return `<div class="health-page-grid">
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>今天的情绪</h3><span>${escapeHtml(health.day)}</span></div>
      <div class="health-mood-row">${MOOD_OPTIONS.map(([value, label]) =>
        `<button type="button" class="health-mood${Number(mind.mood) === value ? " active" : ""}" data-health-action="quick-mood" data-value="${value}" aria-label="${escapeHtml(label)}">
          <span>${EMOTION[value]}</span><small>${escapeHtml(label)}</small></button>`).join("")}</div>
      ${sectionForm("mind", today, { submit: "保存心理记录" })}
    </section>
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>30 天情绪与压力</h3><span>均值 ${moodAverage === null ? "—" : moodAverage.toFixed(1)}/5</span></div>
      ${lineChart([
        { label: "情绪", points: moodPoints, unit: "分", color: "var(--health-series-1)" },
        { label: "压力", points: stressPoints, unit: "分", color: "var(--health-series-3)" },
      ], { height: 200 }).svg}
      <div class="health-panel-head"><h3>情绪热力图</h3><span>近 60 天</span></div>
      ${heatmap(moodPoints, { days: 60, end: health.day, max: 5 })}
    </section>
  </div>`;
}

// ── 训练 ─────────────────────────────────────────────────────────────

function renderTraining() {
  const today = health.overview.today || {};
  const sessions = Array.isArray(today.training) ? today.training : [];
  const history = health.history || [];
  const volume = pointsFor(history, "training.volume_kg");
  const weekVolume = volume.slice(-7).reduce((sum, point) => sum + point.value, 0);
  return `<div class="health-page-grid">
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>今天的训练</h3><span>${sessions.length ? `${sessions.length} 次` : "没有记录"}</span></div>
      ${sessions.length ? `<div class="health-sessions">${sessions.map((session) =>
        `<article class="health-session">
          <header><strong>${escapeHtml(session.kind || "训练")}</strong><span>${session.minutes ? `${session.minutes} 分钟` : ""} ${session.volume_kg ? `· ${formatNumber(session.volume_kg, 0, "kg")}` : ""}</span>
            <button class="icon-button" type="button" aria-label="删除这次训练" data-health-action="delete-training" data-id="${escapeHtml(session.id)}">×</button></header>
          <ul>${(session.exercises || []).map((exercise) =>
            `<li>${escapeHtml(exercise.name)}：${(exercise.sets || []).map((set) => `${set.reps ?? "?"}×${set.weight_kg ?? "自重"}`).join("，")}</li>`).join("")}</ul>
          ${session.rpe ? `<p>RPE ${session.rpe}/10${session.soreness ? ` · 酸痛 ${session.soreness}/5` : ""}</p>` : ""}
        </article>`).join("")}</div>` : `<p class="health-muted">今天还没有训练记录。</p>`}
      <form class="health-form" data-health-form="training">
        <div class="health-form-grid">
          <label class="health-field"><span class="health-field-label">类型</span>
            <select data-field="kind">${TRAINING_KINDS.map((kind) => `<option value="${kind}">${kind}</option>`).join("")}</select></label>
          <label class="health-field"><span class="health-field-label">时长（分钟）</span>
            <input type="number" data-field="minutes" min="1" max="600" step="1" /></label>
          <label class="health-field"><span class="health-field-label">主观疲劳 RPE</span>
            <select data-field="rpe"><option value="">未记录</option>${Array.from({ length: 10 }, (_item, index) => `<option value="${index + 1}">${index + 1}</option>`).join("")}</select></label>
          <label class="health-field"><span class="health-field-label">肌肉酸痛</span>
            <select data-field="soreness"><option value="">未记录</option>${[1, 2, 3, 4, 5].map((value) => `<option value="${value}">${value}/5</option>`).join("")}</select></label>
          <label class="health-field"><span class="health-field-label">训练后心情</span>
            <select data-field="mood_after"><option value="">未记录</option>${[1, 2, 3, 4, 5].map((value) => `<option value="${value}">${EMOTION[value]} ${value}</option>`).join("")}</select></label>
        </div>
        <div class="health-exercise-picker">
          <div class="health-panel-head"><h4>动作（从本地动作库选择）</h4><span id="health-exercise-total">${health.exerciseFacets?.total ?? ""} 个动作</span></div>
          <div class="health-form-grid two">
            <label class="health-field"><span class="health-field-label">搜索</span>
              <input type="search" data-exercise-search placeholder="深蹲 / 卧推 / 划船" /></label>
            <label class="health-field"><span class="health-field-label">部位</span>
              <select data-exercise-body><option value="">全部</option>${(health.exerciseFacets?.bodies || []).map((item) =>
                `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}（${item.count}）</option>`).join("")}</select></label>
          </div>
          <div id="health-exercise-results" class="health-exercise-results"></div>
          <div id="health-training-draft" class="health-training-draft"></div>
        </div>
        <div class="health-form-actions"><span class="health-form-status status-message" role="status"></span>
          <button class="button primary" type="submit">保存训练</button></div>
      </form>
    </section>
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>近 30 天训练量</h3><span>近 7 天 ${formatNumber(weekVolume, 0, "kg")}</span></div>
      ${barChart(volume, { unit: "kg", height: 170 })}
      ${recentTrainingList(history)}
    </section>
  </div>`;
}

function recentTrainingList(history) {
  const rows = (history || []).filter((record) => (record.training || []).length).slice(-10).reverse();
  if (!rows.length) return `<p class="health-muted">还没有训练历史。</p>`;
  return `<ul class="health-rows">${rows.map((record) => {
    const totals = record.training_totals || {};
    return `<li><span>${escapeHtml(shortDate(record.date))}</span>
      <strong>${formatNumber(totals.volume_kg, 0, "kg")}</strong>
      <small>${(record.training || []).map((session) => session.kind).join(" / ")} · ${formatNumber(totals.minutes, 0, "分钟")}</small></li>`;
  }).join("")}</ul>`;
}

function draftState() {
  if (!health.draft) health.draft = { exercises: [] };
  return health.draft;
}

function renderDraft() {
  const container = node("health-training-draft");
  if (!container) return;
  const draft = draftState();
  if (!draft.exercises.length) {
    container.innerHTML = `<p class="health-muted">还没有选动作；搜索后点一下加入。</p>`;
    return;
  }
  container.innerHTML = draft.exercises.map((exercise, exerciseIndex) => `<article class="health-draft-exercise" data-draft-exercise="${exerciseIndex}">
    <header><strong>${escapeHtml(exercise.name)}</strong>
      <button class="icon-button" type="button" aria-label="移除 ${escapeHtml(exercise.name)}" data-health-action="remove-exercise" data-index="${exerciseIndex}">×</button></header>
    <div class="health-set-list">${exercise.sets.map((set, setIndex) => `<div class="health-set" data-set="${setIndex}">
      <label>次数<input type="number" min="0" max="1000" step="1" data-set-reps data-exercise="${exerciseIndex}" data-set-index="${setIndex}" value="${set.reps ?? ""}" /></label>
      <label>重量 kg<input type="number" min="0" max="1000" step="0.5" data-set-weight data-exercise="${exerciseIndex}" data-set-index="${setIndex}" value="${set.weight_kg ?? ""}" /></label>
      <button class="icon-button" type="button" aria-label="删除这一组" data-health-action="remove-set" data-exercise="${exerciseIndex}" data-set="${setIndex}">−</button>
    </div>`).join("")}</div>
    <button class="text-button" type="button" data-health-action="add-set" data-exercise="${exerciseIndex}">＋ 加一组</button>
  </article>`).join("");
}

function syncDraftFromDom() {
  const draft = draftState();
  document.querySelectorAll("[data-draft-exercise]").forEach((card) => {
    const index = Number(card.dataset.draftExercise);
    const exercise = draft.exercises[index];
    if (!exercise) return;
    card.querySelectorAll(".health-set").forEach((row) => {
      const setIndex = Number(row.dataset.set);
      const reps = Number(row.querySelector("[data-set-reps]")?.value);
      const weight = Number(row.querySelector("[data-set-weight]")?.value);
      exercise.sets[setIndex] = {
        reps: Number.isFinite(reps) && row.querySelector("[data-set-reps]")?.value !== "" ? reps : null,
        weight_kg: Number.isFinite(weight) && row.querySelector("[data-set-weight]")?.value !== "" ? weight : null,
      };
    });
  });
}

let exerciseSearchTimer = 0;
function scheduleExerciseSearch(query) {
  window.clearTimeout(exerciseSearchTimer);
  exerciseSearchTimer = window.setTimeout(() => runExerciseSearch(query), 220);
}

function fillLabPreset(select) {
  const form = select.closest("form");
  const option = select.selectedOptions?.[0];
  if (!form || !option) return;
  if (select.value === "自定义") {
    form.querySelector('[data-field="custom_test"]')?.focus();
    return;
  }
  const set = (name, value) => {
    const input = form.querySelector(`[data-field="${name}"]`);
    if (input && value !== undefined && value !== null) input.value = value;
  };
  set("category", option.dataset.category || "其他");
  set("unit", option.dataset.unit || "");
  set("ref_min", option.dataset.min ?? "");
  set("ref_max", option.dataset.max ?? "");
}

async function runExerciseSearch(query = "") {
  const container = node("health-exercise-results");
  if (!container) return;
  const bodySelect = document.querySelector("[data-exercise-body]");
  const searchInput = document.querySelector("[data-exercise-search]");
  const text = query || searchInput?.value || "";
  const body = bodySelect?.value || "";
  container.innerHTML = `<p class="health-muted">正在搜索…</p>`;
  try {
    const data = await healthApi(`/api/health/exercises?q=${encodeURIComponent(text)}&body=${encodeURIComponent(body)}&limit=24`);
    health.exerciseResults = data.items || [];
    health.exerciseFacets = data.facets || health.exerciseFacets;
    container.innerHTML = health.exerciseResults.length
      ? health.exerciseResults.map((item, index) => `<button type="button" class="health-exercise-item" data-health-action="add-exercise" data-index="${index}">
          <strong>${escapeHtml(item.name)}</strong>
          <small>${escapeHtml([item.body_label, item.equipment_label, item.target].filter(Boolean).join(" · "))}</small>
        </button>`).join("")
      : `<p class="health-muted">没有匹配的动作；换个关键词，或先在身体档案里手动写名称。</p>`;
  } catch (error) {
    container.innerHTML = `<p class="health-inline-error">动作库读取失败：${escapeHtml(error.message)}</p>`;
  }
}

// ── 医疗 ─────────────────────────────────────────────────────────────

function renderMedical() {
  const labs = health.labs || { rows: [], tests: [], categories: [], abnormal_count: 0 };
  const rows = Array.isArray(labs.rows) ? labs.rows : [];
  const medications = health.overview?.profile?.medications || [];
  const grouped = (labs.categories || []).map((category) => ({
    category,
    rows: rows.filter((row) => (row.category || "其他") === category),
  })).filter((group) => group.rows.length);
  const uncategorized = rows.filter((row) => !(labs.categories || []).includes(row.category));
  if (uncategorized.length) grouped.push({ category: "未分类", rows: uncategorized });

  return `<div class="health-page-grid">
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>检验结果</h3><span>${rows.length} 项${labs.abnormal_count ? ` · 异常 ${labs.abnormal_count} 项` : ""}</span></div>
      ${rows.length ? grouped.map((group) => `<div class="health-lab-group"><h4>${escapeHtml(group.category)}</h4>
        <table class="health-table"><thead><tr><th>日期</th><th>项目</th><th>数值</th><th>参考区间</th><th></th></tr></thead><tbody>
        ${group.rows.map((row) => `<tr class="${row.abnormal ? "abnormal" : ""}">
          <td>${escapeHtml(row.date)}</td><td>${escapeHtml(row.test)}</td>
          <td>${formatNumber(row.value, row.value === Math.round(row.value) ? 0 : 2)} ${escapeHtml(row.unit || "")}${row.abnormal ? "<em>异常</em>" : ""}</td>
          <td>${row.ref_min === null && row.ref_max === null ? "—" : `${row.ref_min ?? "—"} – ${row.ref_max ?? "—"}`}</td>
          <td><button class="icon-button" type="button" aria-label="删除这条检验" data-health-action="delete-lab" data-id="${escapeHtml(row.id)}">×</button></td>
        </tr>`).join("")}</tbody></table></div>`).join("")
        : `<p class="health-muted">还没有检验记录。录入一次体检的关键项后，异常项会在总览里提示。</p>`}
      <p class="health-note">异常标记只按参考区间判断，不是诊断；有报告时以报告上的区间为准，并请医生解读。</p>
      <form class="health-form" data-health-form="lab">
        <div class="health-form-grid">
          <label class="health-field"><span class="health-field-label">日期</span>
            <input type="date" data-field="date" value="${escapeHtml(health.day)}" required /></label>
          <label class="health-field"><span class="health-field-label">项目</span>
            <select data-field="test" data-lab-preset>${(labs.tests || []).map((item) =>
              `<option value="${escapeHtml(item.name)}" data-category="${escapeHtml(item.category)}" data-unit="${escapeHtml(item.unit)}" data-min="${item.ref_min}" data-max="${item.ref_max}">${escapeHtml(item.name)}（${escapeHtml(item.category)}）</option>`).join("")}
              <option value="自定义">自定义项目</option></select></label>
          <label class="health-field"><span class="health-field-label">自定义项目名（选“自定义”时填）</span>
            <input type="text" data-field="custom_test" maxlength="60" placeholder="例如：同型半胱氨酸" /></label>
          <label class="health-field"><span class="health-field-label">分类</span>
            <select data-field="category">${(labs.categories || []).map((category) =>
              `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`).join("")}</select></label>
          <label class="health-field"><span class="health-field-label">数值</span>
            <input type="number" data-field="value" step="0.01" required /></label>
          <label class="health-field"><span class="health-field-label">单位</span>
            <input type="text" data-field="unit" maxlength="12" /></label>
          <label class="health-field"><span class="health-field-label">参考下限</span>
            <input type="number" data-field="ref_min" step="0.01" /></label>
          <label class="health-field"><span class="health-field-label">参考上限</span>
            <input type="number" data-field="ref_max" step="0.01" /></label>
          <label class="health-field"><span class="health-field-label">备注</span>
            <input type="text" data-field="note" maxlength="200" /></label>
        </div>
        <div class="health-form-actions"><span class="health-form-status status-message" role="status"></span>
          <button class="button primary" type="submit">添加检验</button></div>
      </form>
    </section>

    <section class="health-panel">
      <div class="health-panel-head"><h3>用药</h3><span>${medications.length} 条</span></div>
      ${medications.length ? `<ul class="health-rows">${medications.map((item, index) =>
        `<li><span>${escapeHtml(item.name)}</span><strong>${escapeHtml(item.dosage || "")}</strong>
          <small>${escapeHtml(item.frequency || "")} ${item.active ? "" : "（已停）"}</small>
          <button class="icon-button" type="button" aria-label="移除 ${escapeHtml(item.name)}" data-health-action="delete-medication" data-index="${index}">×</button></li>`).join("")}</ul>`
        : `<p class="health-muted">还没有用药记录。</p>`}
      <form class="health-form compact" data-health-form="medication">
        <div class="health-form-grid">
          <label class="health-field"><span class="health-field-label">药品</span>
            <input type="text" data-field="name" maxlength="60" required /></label>
          <label class="health-field"><span class="health-field-label">剂量</span>
            <input type="text" data-field="dosage" maxlength="40" placeholder="例如 5mg" /></label>
          <label class="health-field"><span class="health-field-label">频次</span>
            <input type="text" data-field="frequency" maxlength="40" placeholder="例如 每晚一次" /></label>
        </div>
        <div class="health-form-actions"><span class="health-form-status status-message" role="status"></span>
          <button class="button secondary" type="submit">添加用药</button></div>
      </form>
      <p class="health-note">用药只做记录，不替代医嘱；调整或停药请先问医生。</p>
    </section>
  </div>`;
}

// ── 趋势 ─────────────────────────────────────────────────────────────

function renderTrends() {
  const trends = health.trends;
  if (!trends) return emptyState("正在读取趋势", "请稍候。");
  const available = (trends.available || []).filter((item, index, list) =>
    item && item.key && list.findIndex((other) => other.key === item.key) === index);
  const metrics = trends.metrics || [];
  const series = metrics.map((metric, index) => ({
    label: metric.label, points: metric.points || [], unit: metric.unit || "",
    color: `var(--health-series-${index % 4})`,
  }));
  return `<div class="health-page-grid">
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>多指标趋势</h3><span>${escapeHtml(trends.range?.start || "")} 至 ${escapeHtml(trends.range?.end || "")}</span></div>
      <div class="health-toolbar">
        ${[7, 30, 90, 180, 365].map((days) => `<button type="button" class="health-tab${health.trendDays === days ? " active" : ""}" data-health-action="trend-days" data-days="${days}">${days === 365 ? "1 年" : `${days} 天`}</button>`).join("")}
      </div>
      <div class="health-toolbar">
        ${(trends.presets || []).map((preset) => `<button type="button" class="health-chip" data-health-action="trend-preset" data-metrics="${escapeHtml(preset.metrics.join(","))}">${escapeHtml(preset.title)}</button>`).join("")}
      </div>
      ${lineChart(series, { height: 260 }).svg}
      ${metrics.length ? `<table class="health-table"><thead><tr><th>指标</th><th>最新</th><th>均值</th><th>范围</th><th>变化</th></tr></thead><tbody>
        ${metrics.map((metric) => `<tr><td>${escapeHtml(metric.label)}${metric.unit ? `（${escapeHtml(metric.unit)}）` : ""}</td>
          <td>${formatNumber(metric.stats?.latest, metric.precision)}</td>
          <td>${formatNumber(metric.stats?.avg, metric.precision)}</td>
          <td>${formatNumber(metric.stats?.min, metric.precision)}–${formatNumber(metric.stats?.max, metric.precision)}</td>
          <td>${formatSigned(metric.stats?.delta, metric.precision)}</td></tr>`).join("")}</tbody></table>`
        : `<p class="health-muted">这段时间里没有这些指标的数据。</p>`}
    </section>
    <section class="health-panel">
      <div class="health-panel-head"><h3>选择指标</h3><span>最多 4 个</span></div>
      <div class="health-metric-picker">${available.map((item) => `<label class="health-check">
        <input type="checkbox" data-trend-metric value="${escapeHtml(item.key)}"${health.trendMetrics.includes(item.key) ? " checked" : ""} />
        <span>${escapeHtml(item.label)}${item.unit ? `<em>${escapeHtml(item.unit)}</em>` : ""}</span></label>`).join("")}</div>
    </section>
  </div>`;
}

// ── 洞察 ─────────────────────────────────────────────────────────────

function renderInsights() {
  const items = Array.isArray(health.insights) ? health.insights : [];
  return `<div class="health-page-grid">
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>跨维度关联</h3><span>近 ${health.insightDays} 天</span></div>
      <div class="health-toolbar">${[30, 90, 180, 365].map((days) =>
        `<button type="button" class="health-tab${health.insightDays === days ? " active" : ""}" data-health-action="insight-days" data-days="${days}">${days === 365 ? "1 年" : `${days} 天`}</button>`).join("")}</div>
      ${items.length ? `<div class="health-insights">${items.map((item) => `<article class="health-insight ${item.read ? "read" : ""}" data-tone="${escapeHtml(insightTone(item.type))}">
        <header><span class="health-insight-type">${escapeHtml(item.dimension || "关联")}</span>
          <strong>${escapeHtml(item.title)}</strong><em>${escapeHtml(item.effect || "")}</em></header>
        <p>${escapeHtml(item.description || "")}</p>
        <footer><span>样本 n=${escapeHtml(String(item.sample_size))} · ${escapeHtml(item.range || "")}</span>
          <button type="button" class="text-button" data-health-action="toggle-insight" data-id="${escapeHtml(item.id)}">${item.read ? "标为未读" : "标为已读"}</button></footer>
      </article>`).join("")}</div>`
        : emptyState("还没有足够的样本", `关联洞察至少需要 7 天记录，且每组样本不少于 5；记录越多，结论才越可信。`)}
      <p class="health-note">洞察只说明相关，不证明因果；样本不足时系统宁可不生成。</p>
    </section>
  </div>`;
}

// ── 报告 ─────────────────────────────────────────────────────────────

function renderReports() {
  const caps = health.overview?.capabilities || {};
  const reports = Array.isArray(health.reports) ? health.reports : (health.overview?.reports || []);
  return `<div class="health-page-grid">
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>私人医生报告</h3><span>${caps.ai ? "AI 可用" : "AI 未配置"}</span></div>
      <p class="health-note">报告由「私人医生」角色读取健康系统的只读摘要生成，落盘到 <code>02_生活运营/02_健康系统/00_健康报告</code>。它不是诊断，也不能替代就医。</p>
      <div class="health-toolbar">
        <button class="button primary" type="button" data-health-action="generate-report" data-kind="week"${caps.ai ? "" : " disabled"}>生成健康周报</button>
        <button class="button secondary" type="button" data-health-action="generate-report" data-kind="month"${caps.ai ? "" : " disabled"}>生成健康月报</button>
        <span id="health-report-status" class="status-message" role="status"></span>
      </div>
    </section>
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>已保存的报告</h3><span>${reports.length} 篇</span></div>
      ${reports.length ? `<ul class="health-report-list">${reports.map((item) =>
        `<li><button type="button" class="health-link" data-health-action="preview-report" data-path="${escapeHtml(item.path || "")}">${escapeHtml(item.title || "健康报告")}</button><span>${escapeHtml(item.date || "")}</span></li>`).join("")}</ul>`
        : `<p class="health-muted">还没有报告。</p>`}
    </section>
  </div>`;
}

// ── 设置 ─────────────────────────────────────────────────────────────

function renderSettings() {
  const profile = health.overview?.profile || {};
  const custom = profile.custom_metrics || [];
  const weights = profile.score_weights || {};
  return `<div class="health-page-grid">
    <section class="health-panel wide">
      <div class="health-panel-head"><h3>身体档案</h3><span>敏感数据，只在本机</span></div>
      <form class="health-form" data-health-form="profile">
        <div class="health-form-grid">
          <label class="health-field"><span class="health-field-label">称呼</span>
            <input type="text" data-field="name" maxlength="40" value="${escapeHtml(profile.name || "")}" /></label>
          <label class="health-field"><span class="health-field-label">性别</span>
            <select data-field="gender">${["", "男", "女", "其他"].map((value) =>
              `<option value="${value}"${profile.gender === value ? " selected" : ""}>${value || "未填写"}</option>`).join("")}</select></label>
          <label class="health-field"><span class="health-field-label">出生日期</span>
            <input type="date" data-field="birth_date" value="${escapeHtml(profile.birth_date || "")}" /></label>
          <label class="health-field"><span class="health-field-label">身高（cm）</span>
            <input type="number" data-field="height_cm" min="50" max="260" step="0.1" value="${profile.height_cm ?? ""}" /></label>
          <label class="health-field"><span class="health-field-label">血型</span>
            <input type="text" data-field="blood_type" maxlength="10" value="${escapeHtml(profile.blood_type || "")}" /></label>
        </div>
        <div class="health-form-grid">
          <label class="health-field wide"><span class="health-field-label">既往病史</span>
            <textarea data-field="medical_history" rows="2" maxlength="4000">${escapeHtml(profile.medical_history || "")}</textarea></label>
          <label class="health-field wide"><span class="health-field-label">过敏</span>
            <textarea data-field="allergies" rows="2" maxlength="2000">${escapeHtml(profile.allergies || "")}</textarea></label>
        </div>
        <h4>目标</h4>
        <div class="health-form-grid">${goalInputs()}</div>
        <div class="health-form-actions"><span class="health-form-status status-message" role="status"></span>
          <button class="button primary" type="submit">保存档案与目标</button></div>
      </form>
    </section>

    <section class="health-panel">
      <div class="health-panel-head"><h3>自定义指标</h3><span>${custom.length} 个</span></div>
      ${custom.length ? `<ul class="health-rows">${custom.map((item, index) =>
        `<li><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.unit || "")}</strong>
          <button class="icon-button" type="button" aria-label="删除 ${escapeHtml(item.label)}" data-health-action="delete-custom-metric" data-index="${index}">×</button></li>`).join("")}</ul>`
        : `<p class="health-muted">例如「尿酸」「腰臀比」；加完后总览会出现录入入口，趋势里也能选。</p>`}
      <form class="health-form compact" data-health-form="custom-metric">
        <div class="health-form-grid">
          <label class="health-field"><span class="health-field-label">名称</span>
            <input type="text" data-field="label" maxlength="20" required /></label>
          <label class="health-field"><span class="health-field-label">单位</span>
            <input type="text" data-field="unit" maxlength="10" /></label>
        </div>
        <div class="health-form-actions"><button class="button secondary" type="submit">添加指标</button></div>
      </form>
    </section>

    <section class="health-panel">
      <div class="health-panel-head"><h3>评分权重</h3><span>只读</span></div>
      <dl class="health-stats">${Object.entries(DIMENSION_LABELS).map(([key, label]) =>
        `<div><dt>${label}</dt><dd>${Math.round((weights[key] ?? 0) * 100)}%</dd></div>`).join("")}</dl>
      <p class="health-note">缺数据的维度不参与加权；评分只用于观察趋势，不是医学评估。</p>
      <p class="health-note">数据位置：<code>02_生活运营/02_健康系统</code>；洞察已读状态在 <code>.torch-os/health</code>。</p>
    </section>
  </div>`;
}

// ── 表单提交与动作 ───────────────────────────────────────────────────

async function submitForm(form) {
  const kind = form.dataset.healthForm;
  const status = form.querySelector(".health-form-status");
  const submit = form.querySelector("button[type=submit]");
  const fail = (message) => {
    if (status) { status.textContent = message; status.className = "health-form-status status-message error"; }
    if (submit) submit.disabled = false;
  };
  if (submit) submit.disabled = true;
  if (status) { status.textContent = "正在保存…"; status.className = "health-form-status status-message"; }
  try {
    if (kind === "section") {
      const section = form.dataset.section;
      const values = readFields(form);
      const record = health.overview?.today || {};
      await healthApi(`/api/health/day/${encodeURIComponent(health.day)}`, {
        method: "PATCH", body: JSON.stringify({ revision: record.revision || "", changes: { [section]: values } }),
      });
      toast("已保存到健康系统", "success");
      await refresh({ quiet: true });
      return;
    }
    if (kind === "meal") {
      const values = readFields(form);
      if (!values.name) throw new Error("先写食物名称");
      await healthApi(`/api/health/day/${encodeURIComponent(health.day)}/meals`, jsonBody("POST", values));
      toast("已添加餐次", "success");
      await refresh({ quiet: true });
      return;
    }
    if (kind === "training") {
      syncDraftFromDom();
      const values = readFields(form);
      const exercises = draftState().exercises.map((exercise) => ({
        exercise_id: exercise.exercise_id, name: exercise.name,
        sets: (exercise.sets || []).filter((set) => set.reps !== null || set.weight_kg !== null),
      })).filter((exercise) => exercise.sets.length);
      if (!exercises.length && !values.minutes) throw new Error("至少加一个动作或填写训练时长");
      await healthApi(`/api/health/day/${encodeURIComponent(health.day)}/training`, jsonBody("POST", {
        ...values, exercises,
      }));
      health.draft = { exercises: [] };
      toast("训练已记录", "success");
      await refresh({ quiet: true });
      return;
    }
    if (kind === "lab") {
      const values = readFields(form);
      if (values.test === "自定义") {
        if (!values.custom_test) throw new Error("选了自定义项目，就要写项目名");
        values.test = values.custom_test;
      }
      delete values.custom_test;
      if (!values.test || values.value === null) throw new Error("项目和数值都要填");
      const labs = health.labs || {};
      await healthApi("/api/health/labs", jsonBody("POST", { ...values, revision: labs.revision || "" }));
      toast("检验结果已保存", "success");
      health.labs = await healthApi("/api/health/labs");
      await refresh({ quiet: true });
      return;
    }
    if (kind === "medication") {
      const values = readFields(form);
      if (!values.name) throw new Error("先写药品名");
      const profile = health.overview?.profile || {};
      const medications = [...(profile.medications || []), {
        name: values.name, dosage: values.dosage || "", frequency: values.frequency || "",
        active: true,
      }];
      await saveProfile({ medications });
      toast("已添加用药记录", "success");
      return;
    }
    if (kind === "profile") {
      const values = readFields(form);
      const goals = {};
      form.querySelectorAll("[data-goal]").forEach((input) => {
        const value = String(input.value || "").trim();
        goals[input.dataset.goal] = value === "" ? null : Number(value);
      });
      await saveProfile({ ...values, goals });
      toast("档案与目标已保存", "success");
      return;
    }
    if (kind === "custom") {
      const values = {};
      form.querySelectorAll("[data-custom-field]").forEach((input) => {
        const raw = String(input.value ?? "").trim();
        values[input.dataset.customField] = raw === "" ? null : Number(raw);
      });
      await healthApi(`/api/health/day/${encodeURIComponent(health.day)}`, jsonBody("PATCH", {
        revision: health.overview?.today?.revision || "", changes: { custom: values },
      }));
      toast("自定义指标已保存", "success");
      await refresh({ quiet: true });
      return;
    }
    if (kind === "custom-metric") {
      const values = readFields(form);
      if (!values.label) throw new Error("先写指标名称");
      const profile = health.overview?.profile || {};
      const custom = [...(profile.custom_metrics || []), {
        label: values.label, unit: values.unit || "", min: null, max: null,
      }];
      await saveProfile({ custom_metrics: custom });
      toast("自定义指标已添加", "success");
      return;
    }
    throw new Error("未知表单");
  } catch (error) {
    fail(error.message);
  }
}

function failForm(form, error) {
  const status = form.querySelector(".health-form-status");
  if (status) { status.textContent = error.message; status.className = "health-form-status status-message error"; }
  const submit = form.querySelector("button[type=submit]");
  if (submit) submit.disabled = false;
}

async function saveProfile(changes) {
  const profile = health.overview?.profile || {};
  await healthApi("/api/health/profile", {
    method: "PUT", body: JSON.stringify({ ...changes, revision: profile.revision || "" }),
  });
  await refresh({ quiet: true });
}

async function handleAction(button) {
  const action = button.dataset.healthAction;
  if (action === "retry") { await refresh(); return; }
  if (action === "go-page") { await navigate(button.dataset.page); return; }
  if (action === "pick-day") {
    health.day = button.dataset.day;
    if (node("health-day")) node("health-day").value = health.day;
    await refresh({ quiet: true });
    return;
  }
  if (action === "edit-section") { openSectionDialog(button.dataset.section); return; }
  if (action === "water") {
    const delta = Number(button.dataset.delta) || 0;
    const current = Number(health.overview?.today?.nutrition?.water_ml) || 0;
    const value = Math.max(0, current + delta);
    await healthApi(`/api/health/day/${encodeURIComponent(health.day)}`, jsonBody("PATCH", {
      revision: health.overview?.today?.revision || "",
      changes: { nutrition: { water_ml: value } },
    }));
    toast(`饮水 ${delta > 0 ? "+" : ""}${delta} ml`, "success");
    await refresh({ quiet: true });
    return;
  }
  if (action === "quick-mood") {
    const value = Number(button.dataset.value);
    await healthApi(`/api/health/day/${encodeURIComponent(health.day)}`, jsonBody("PATCH", {
      revision: health.overview?.today?.revision || "",
      changes: { mind: { mood: value } },
    }));
    toast(`今天的情绪记为 ${value}/5`, "success");
    await refresh({ quiet: true });
    return;
  }
  if (action === "delete-meal") {
    const ok = await confirmAction({ title: "删除这条餐次？", message: "只删除今天的这一条记录。", confirmLabel: "删除", danger: true });
    if (!ok) return;
    await healthApi(`/api/health/day/${encodeURIComponent(health.day)}/meals/${encodeURIComponent(button.dataset.id)}`, { method: "DELETE" });
    toast("已删除", "success");
    await refresh({ quiet: true });
    return;
  }
  if (action === "delete-training") {
    const ok = await confirmAction({ title: "删除这次训练？", message: "组记录会一起删除。", confirmLabel: "删除", danger: true });
    if (!ok) return;
    await healthApi(`/api/health/day/${encodeURIComponent(health.day)}/training/${encodeURIComponent(button.dataset.id)}`, { method: "DELETE" });
    toast("已删除", "success");
    await refresh({ quiet: true });
    return;
  }
  if (action === "add-exercise") {
    const item = health.exerciseResults[Number(button.dataset.index)];
    if (!item) return;
    const draft = draftState();
    if (draft.exercises.some((exercise) => exercise.exercise_id === item.id)) { toast("这个动作已经在今天的训练里了"); return; }
    draft.exercises.push({ exercise_id: item.id, name: item.name, sets: [{ reps: null, weight_kg: null }] });
    renderDraft();
    return;
  }
  if (action === "remove-exercise") {
    syncDraftFromDom();
    draftState().exercises.splice(Number(button.dataset.index), 1);
    renderDraft();
    return;
  }
  if (action === "add-set") {
    syncDraftFromDom();
    const exercise = draftState().exercises[Number(button.dataset.exercise)];
    exercise?.sets.push({ reps: null, weight_kg: null });
    renderDraft();
    return;
  }
  if (action === "remove-set") {
    syncDraftFromDom();
    const exercise = draftState().exercises[Number(button.dataset.exercise)];
    exercise?.sets.splice(Number(button.dataset.set), 1);
    renderDraft();
    return;
  }
  if (action === "delete-lab") {
    const ok = await confirmAction({ title: "删除这条检验记录？", message: "医疗数据删除后不可恢复，只能从备份找回。", confirmLabel: "删除", danger: true });
    if (!ok) return;
    await healthApi(`/api/health/labs/${encodeURIComponent(button.dataset.id)}?revision=${encodeURIComponent(health.labs?.revision || "")}`, { method: "DELETE" });
    health.labs = await healthApi("/api/health/labs");
    toast("已删除", "success");
    await refresh({ quiet: true });
    return;
  }
  if (action === "delete-medication" || action === "delete-custom-metric") {
    const index = Number(button.dataset.index);
    const profile = health.overview?.profile || {};
    const key = action === "delete-medication" ? "medications" : "custom_metrics";
    const list = [...(profile[key] || [])];
    list.splice(index, 1);
    await saveProfile({ [key]: list });
    toast("已删除", "success");
    return;
  }
  if (action === "trend-days") { health.trendDays = Number(button.dataset.days); await ensurePageData(); renderPage(); return; }
  if (action === "trend-preset") {
    health.trendMetrics = String(button.dataset.metrics || "").split(",").filter(Boolean).slice(0, 4);
    await ensurePageData();
    renderPage();
    return;
  }
  if (action === "insight-days") { health.insightDays = Number(button.dataset.days); await ensurePageData(); renderPage(); return; }
  if (action === "toggle-insight") {
    const item = health.insights.find((insight) => insight.id === button.dataset.id);
    if (!item) return;
    await healthApi("/api/health/insights/read", jsonBody("POST", { id: item.id, read: !item.read }));
    item.read = !item.read;
    renderPage();
    return;
  }
  if (action === "generate-report") {
    const kind = button.dataset.kind || "week";
    const label = kind === "week" ? "周报" : "月报";
    const ok = await confirmAction({
      title: `生成健康${label}？`,
      message: "会把最近的健康摘要发给本地配置的 DeepSeek，生成一篇报告保存到 00_健康报告。数据只在本机，报告需要你确认后才会落盘。",
      confirmLabel: `生成${label}`,
    });
    if (!ok) return;
    const status = node("health-report-status");
    button.disabled = true;
    if (status) { status.textContent = "私人医生正在读取数据并生成…"; status.className = "status-message"; }
    try {
      const result = await healthApi("/api/health/report", jsonBody("POST", { kind, confirm: true }));
      toast(`健康${label}已生成`, "success");
      health.reports = Array.isArray(result.reports) ? result.reports : health.reports;
      health.overview.reports = health.reports;
      if (status) { status.textContent = `已保存：${result.note?.title || `健康${label}`}`; status.className = "status-message success"; }
      await refresh({ quiet: true });
      if (result.note?.path) previewReport(result.note.path);
    } catch (error) {
      if (status) { status.textContent = error.message; status.className = "status-message error"; }
    } finally {
      button.disabled = false;
    }
    return;
  }
  if (action === "preview-report") { await previewReport(button.dataset.path); return; }
}

async function toggleTrendMetric(input) {
  const value = input.value;
  const selected = new Set(health.trendMetrics);
  if (input.checked) {
    if (selected.size >= 4) { input.checked = false; toast("最多同时看 4 个指标"); return; }
    selected.add(value);
  } else {
    selected.delete(value);
  }
  health.trendMetrics = [...selected];
  if (!health.trendMetrics.length) health.trendMetrics = ["sleep.duration_min"];
  await ensurePageData();
  renderPage();
}

function openSectionDialog(section) {
  const today = health.overview?.today || {};
  const title = SECTION_LABELS[section] || "记录";
  const form = sectionForm(section, today, { submit: "保存" });
  if (!form) return;
  const close = openDialog(`记录${title}`, `<p class="health-note">${escapeHtml(dayHeading())} · 数据写入 <code>02_生活运营/02_健康系统/01_每日健康</code></p>${form}`);
  const dialogContent = node("dialog-content");
  const submit = dialogContent?.querySelector("form");
  submit?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const values = readFields(submit);
      await healthApi(`/api/health/day/${encodeURIComponent(health.day)}`, jsonBody("PATCH", {
        revision: today.revision || "", changes: { [section]: values },
      }));
      toast("已保存", "success");
      if (close()) node("detail-dialog")?.close();
      await refresh({ quiet: true });
    } catch (error) {
      const status = submit.querySelector(".health-form-status");
      if (status) { status.textContent = error.message; status.className = "health-form-status status-message error"; }
    }
  });
}

async function previewReport(path) {
  if (!path) return;
  const close = openDialog("健康报告", `<div class="health-loading">正在读取…</div>`);
  try {
    const note = await api(`/api/notes?path=${encodeURIComponent(path)}`);
    if (!close()) return;
    node("dialog-title").textContent = note.title || "健康报告";
    node("dialog-content").innerHTML = `<p class="meta">${escapeHtml(path)}</p><pre class="health-report-body">${escapeHtml(note.body || note.content || "")}</pre>`;
  } catch (error) {
    if (close()) node("dialog-content").innerHTML = `<p class="health-inline-error">${escapeHtml(error.message)}</p>`;
  }
}
