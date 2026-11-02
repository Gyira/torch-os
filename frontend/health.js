// 「健康系统」= 生活运营下与恋爱系统平级的二级分区（2026-11-01 改版）。
// 私人数据全部由后端读写 02_生活运营/02_健康系统 里的 Markdown；这里只做界面与交互，
// 不在浏览器里保存任何健康记录。评分、BMI、沿用状态都由后端算好，前端只负责展示与录入。
//
// 新版结构：今日记录 / 身体情况 / 睡眠情况 / 饮食情况 / 运动记录 / 健康档案 / 私人医生。
// 当天没记录时，后端返回每项「最近一次非空记录」；前端显示「沿用 X 月 X 日」，
// 但不会把旧数据复制成今天的新记录。
import { api, escapeHtml, jsonBody, openDialog, confirmAction, toast } from "./core.js";
import { renderMarkdownDocument } from "./pillar-markdown.js";
import {
  todayText, shiftDay, weekdayLabel, weekdayFull, shortDate, monthDayLabel,
  formatNumber, formatSigned, formatDuration, durationParts, durationBetween,
  freshnessText, percentOf, pointsFor, seriesStats, average, stddev, clockMinutes,
  lineChart, barChart, ringMarkup, progressMarkup, macroProgress,
} from "./health-model.js";

const PAGES = {
  today: { title: "今日记录", subtitle: "今天想记录什么？" },
  body: { title: "身体情况", subtitle: "体重和身体成分，记一次就会沿用。" },
  sleep: { title: "睡眠情况", subtitle: "睡了多久最重要，入睡和起床时间可选。" },
  diet: { title: "饮食情况", subtitle: "大概写一写吃了什么，AI 帮你估算。" },
  exercise: { title: "运动记录", subtitle: "散步、跑步、力量都算，写一句也行。" },
  archive: { title: "健康档案", subtitle: "全部健康数据，一眼看完。" },
  doctor: { title: "私人医生", subtitle: "有哪里不舒服，可以直接问。" },
};
const PAGE_KEYS = Object.keys(PAGES);

// 新版四维评分；window 是沿用有效期（天）。
const CURRENT = {
  sleep: { label: "睡眠", icon: "眠", page: "sleep", window: 3 },
  body: { label: "身体", icon: "身", page: "body", window: 30 },
  nutrition: { label: "饮食", icon: "食", page: "diet", window: 7 },
  movement: { label: "运动", icon: "动", page: "exercise", window: 14 },
};
const CURRENT_KEYS = Object.keys(CURRENT);

const MEAL_TYPES = ["早餐", "午餐", "晚餐", "加餐"];
const TRAINING_KINDS = ["力量", "跑步", "快走", "骑行", "游泳", "球类", "瑜伽", "柔韧", "有氧", "其他"];
const QUALITY_LABELS = ["很差", "较差", "一般", "不错", "很好"];
const CHAT_IDLE_STATUS = "不会诊断、不开药 · 自动记入问诊记录";

const HEALTH = {
  inited: false,
  loading: false,
  page: "today",
  day: todayText(),
  overview: null,
  history: [],
  historyEnd: "",
  archive: null,
  doctor: null,
  reports: [],
  consults: [],
  foods: null,
  error: "",
  trendDays: 30,
  dietDraft: null,
  exerciseDraft: null,
  trainingDraft: { exercises: [] },
  exerciseResults: [],
  exerciseFacets: null,
  doctorChat: { messages: [], threadId: "", busy: false },
  activeDialogAction: null,
};

const node = (id) => document.getElementById(id);
const esc = escapeHtml;
const profile = () => HEALTH.overview?.profile || {};
const actual = () => HEALTH.overview?.today || { sections: {}, meals: [], training: [] };
const currentScore = () => HEALTH.overview?.current || {};
const effective = (key) => HEALTH.overview?.effective?.[key] || null;
const sectionValues = (section) => (actual().sections?.[section] || {});
const isToday = () => HEALTH.day === todayText();
const selectedWeekStart = () => HEALTH.overview?.week_start || HEALTH.day;

function healthApi(path, options = {}) {
  return api(path, { timeoutMs: 30000, ...options });
}

function formatClock(value) {
  return /^\d{2}:\d{2}$/.test(String(value || "")) ? String(value) : "";
}

function shortDuration(minutes) {
  const parts = durationParts(minutes);
  if (!parts.hours && !parts.minutes) return "—";
  return `${parts.hours ? `${parts.hours}h` : ""}${parts.minutes ? String(parts.minutes).padStart(2, "0") : ""}`;
}

function ageFromBirth(birth) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(birth || ""))) return null;
  const born = new Date(`${birth}T00:00:00`);
  const now = new Date();
  let age = now.getFullYear() - born.getFullYear();
  if (now.getMonth() < born.getMonth()
      || (now.getMonth() === born.getMonth() && now.getDate() < born.getDate())) age -= 1;
  return age > 0 && age < 120 ? age : null;
}

function toneOf(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "none";
  if (number >= 85) return "great";
  if (number >= 70) return "good";
  if (number >= 55) return "fair";
  return "low";
}

function freshness(meta, windowDays) {
  return freshnessText(meta, { today: todayText(), window: windowDays });
}

function isStale(meta, windowDays) {
  return Boolean(meta?.date) && Number(meta.age) > Number(windowDays);
}

function scoreNote(score) {
  const sources = score?.sources || {};
  const parts = CURRENT_KEYS
    .filter((key) => sources[key]?.date)
    .map((key) => `${CURRENT[key].label} ${monthDayLabel(sources[key].date)}`);
  if (!parts.length) return "还没有可用的记录。";
  return `基于最新记录 · ${parts.join(" · ")}`;
}

function setStatus(form, message, tone = "") {
  const status = form?.querySelector(".health-form-status");
  if (!status) return;
  status.textContent = message || "";
  status.className = `health-form-status status-message${tone ? ` ${tone}` : ""}`;
}

function setBusy(form, busy, label = "保存中…") {
  const submit = form?.querySelector("button[type=submit]");
  if (!submit) return;
  if (busy) {
    submit.dataset.label = submit.textContent;
    submit.textContent = label;
    submit.disabled = true;
  } else {
    submit.textContent = submit.dataset.label || "保存";
    submit.disabled = false;
  }
}

function readFields(form) {
  const values = {};
  for (const input of form.querySelectorAll("[name]")) {
    const name = input.name;
    const raw = String(input.value ?? "").trim();
    values[name] = raw === "" ? null : (input.type === "number" ? Number(raw) : raw);
  }
  return values;
}

function modalForm(kind, { icon, title, lead, body, submitLabel = "保存", footerNote = "" }) {
  return `<form class="health-modal" data-health-form="${kind}" novalidate>
    <header class="health-modal-head">
      <span class="health-modal-icon" aria-hidden="true">${esc(icon)}</span>
      <div><h2>${esc(title)}</h2><p>${esc(lead)}</p></div>
      <button class="health-modal-close" type="button" data-dialog-action="close" aria-label="关闭">×</button>
    </header>
    <div class="health-modal-body">${body}</div>
    <footer class="health-modal-foot">
      <span class="health-form-status status-message" role="status">${esc(footerNote)}</span>
      <div class="health-modal-actions">
        <button class="health-btn ghost" type="button" data-dialog-action="close">取消</button>
        <button class="health-btn primary" type="submit">${esc(submitLabel)}</button>
      </div>
    </footer>
  </form>`;
}

function openHealthDialog(html) {
  const dialog = node("detail-dialog");
  if (dialog) dialog.classList.add("health-modal-mode");
  openDialog("", html);
  const content = node("dialog-content");
  if (content) content.scrollTop = 0;
  window.setTimeout(() => {
    const target = content?.querySelector("textarea:not([disabled]), input:not([type=hidden]):not([disabled]), select:not([disabled])");
    target?.focus?.({ preventScroll: true });
  }, 30);
}

function closeHealthDialog() {
  const dialog = node("detail-dialog");
  if (dialog?.open) dialog.close();
}

function bindDialogAction(handler) {
  HEALTH.activeDialogAction = handler;
}

function readNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function readMealRows(root, selector = "[data-meal-row]") {
  const meals = [];
  for (const row of root.querySelectorAll(selector)) {
    const get = (field) => row.querySelector(`[data-meal-field="${field}"]`)?.value ?? "";
    const name = String(get("name")).trim();
    if (!name) continue;
    const meal = {
      meal_type: String(get("meal_type") || "加餐"),
      name,
      portion: String(get("portion")).trim(),
      calories: readNumber(get("calories")),
      protein_g: readNumber(get("protein_g")),
      carbs_g: readNumber(get("carbs_g")),
      fat_g: readNumber(get("fat_g")),
    };
    if (row.dataset.source) meal.source = row.dataset.source;
    meals.push(meal);
  }
  return meals;
}

function mealRowMarkup(meal, index, { estimate = false } = {}) {
  const typeOptions = MEAL_TYPES.map((type) =>
    `<option value="${type}"${meal.meal_type === type ? " selected" : ""}>${type}</option>`).join("");
  return `<div class="health-meal-row" data-meal-row data-source="${esc(meal.source || "")}" data-index="${index}">
    <label><span>餐次</span><select data-meal-field="meal_type">${typeOptions}</select></label>
    <label class="health-meal-name"><span>食物</span><input type="text" data-meal-field="name" maxlength="60" value="${esc(meal.name || "")}" placeholder="例如：鸡胸肉饭" /></label>
    <label><span>份量</span><input type="text" data-meal-field="portion" maxlength="30" value="${esc(meal.portion || "")}" placeholder="1 碗" /></label>
    <label><span>热量 kcal</span><input type="number" min="0" max="5000" step="1" data-meal-field="calories" value="${meal.calories ?? ""}" /></label>
    <label><span>蛋白 g</span><input type="number" min="0" max="500" step="1" data-meal-field="protein_g" value="${meal.protein_g ?? ""}" /></label>
    <label><span>碳水 g</span><input type="number" min="0" max="500" step="1" data-meal-field="carbs_g" value="${meal.carbs_g ?? ""}" /></label>
    <label><span>脂肪 g</span><input type="number" min="0" max="500" step="1" data-meal-field="fat_g" value="${meal.fat_g ?? ""}" /></label>
    <button class="health-row-remove" type="button" data-dialog-action="remove-meal" data-index="${index}" aria-label="删除这一条">×</button>
  </div>`;
}

function sessionRowMarkup(session, index) {
  const kinds = TRAINING_KINDS.map((kind) =>
    `<option value="${kind}"${session.kind === kind ? " selected" : ""}>${kind}</option>`).join("");
  const exercises = esc(JSON.stringify(session.exercises || []));
  return `<div class="health-session-row" data-session-row data-index="${index}" data-session-id="${esc(session.id || "")}" data-exercises="${exercises}">
    <label><span>类型</span><select data-session-field="kind">${kinds}</select></label>
    <label><span>分钟</span><input type="number" min="0" max="600" step="1" data-session-field="minutes" value="${session.minutes ?? ""}" /></label>
    <label><span>消耗 kcal</span><input type="number" min="0" max="5000" step="1" data-session-field="calories" value="${session.calories ?? ""}" /></label>
    <label><span>距离 km</span><input type="number" min="0" max="200" step="0.1" data-session-field="distance_km" value="${session.distance_km ?? ""}" /></label>
    <label><span>步数</span><input type="number" min="0" max="100000" step="1" data-session-field="steps" value="${session.steps ?? ""}" /></label>
    <label class="health-session-note"><span>备注</span><input type="text" data-session-field="note" maxlength="200" value="${esc(session.note || "")}" placeholder="例如：跑步机 8.5 速" /></label>
    <button class="health-row-remove" type="button" data-dialog-action="remove-session" data-index="${index}" aria-label="删除这一条">×</button>
  </div>`;
}

function readSessionRows(root) {
  const sessions = [];
  for (const row of root.querySelectorAll("[data-session-row]")) {
    const get = (field) => row.querySelector(`[data-session-field="${field}"]`)?.value ?? "";
    const kind = String(get("kind") || "其他");
    const minutes = readNumber(get("minutes"));
    const calories = readNumber(get("calories"));
    const distance = readNumber(get("distance_km"));
    const steps = readNumber(get("steps"));
    const note = String(get("note")).trim();
    if (!minutes && !calories && !distance && !steps && !note) continue;
    const session = { kind, note, source: row.dataset.source || "manual" };
    if (row.dataset.sessionId) session.id = row.dataset.sessionId;
    try {
      const exercises = JSON.parse(row.dataset.exercises || "[]");
      if (Array.isArray(exercises) && exercises.length) session.exercises = exercises;
    } catch { /* 没有动作明细也正常 */ }
    if (minutes) session.minutes = minutes;
    if (calories) session.calories = calories;
    if (distance) session.distance_km = distance;
    if (steps) session.steps = steps;
    sessions.push(session);
  }
  return sessions;
}

function guessMealType() {
  const hour = new Date().getHours();
  if (hour < 10) return "早餐";
  if (hour < 15) return "午餐";
  if (hour < 21) return "晚餐";
  return "加餐";
}

// ── 初始化与加载 ─────────────────────────────────────────────────────

export function initHealthPane() {
  if (HEALTH.inited) return;
  HEALTH.inited = true;
  document.querySelectorAll("[data-health-page]").forEach((button) => {
    button.addEventListener("click", () => { navigate(button.dataset.healthPage); });
  });
  node("health-refresh")?.addEventListener("click", () => refresh());
  node("health-today")?.addEventListener("click", () => {
    HEALTH.day = todayText();
    if (node("health-day")) node("health-day").value = HEALTH.day;
    refresh();
  });
  node("health-day")?.addEventListener("change", (event) => {
    const value = String(event.target.value || "").trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return;
    HEALTH.day = value;
    refresh();
  });
  const content = node("health-content");
  content?.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-health-form]");
    if (!form) return;
    event.preventDefault();
    handleForm(form).catch((error) => {
      setStatus(form, error.message, "error");
      setBusy(form, false);
    });
  });
  content?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-health-action]");
    if (!button) return;
    handleAction(button).catch((error) => toast(`操作失败：${error.message}`, "error"));
  });
  content?.addEventListener("input", (event) => {
    if (event.target.matches("[data-food-search]")) paintFoodList(event.target.value);
    if (event.target.matches("[data-exercise-search]")) scheduleExerciseSearch(event.target.value);
    if (event.target.matches("[data-set-reps], [data-set-weight]")) syncTrainingDraft();
    if (event.target.matches("[data-body-weight]")) updateBodyBmiPreview();
  });
  content?.addEventListener("change", (event) => {
    if (event.target.matches("[data-exercise-body]")) runExerciseSearch("");
  });
  const dialogContent = node("dialog-content");
  dialogContent?.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-health-form]");
    if (!form) return;
    event.preventDefault();
    handleForm(form).catch((error) => {
      setStatus(form, error.message, "error");
      setBusy(form, false);
    });
  });
  dialogContent?.addEventListener("click", (event) => {
    const submit = event.target.closest("button[type=submit]");
    if (submit) {
      const form = submit.closest("form[data-health-form]");
      if (form) {
        event.preventDefault();
        form.requestSubmit(submit);
        return;
      }
    }
    const button = event.target.closest("[data-dialog-action]");
    if (!button) return;
    const action = button.dataset.dialogAction;
    if (action === "close") { closeHealthDialog(); return; }
    HEALTH.activeDialogAction?.(action, button, event);
  });
  node("detail-dialog")?.addEventListener("close", () => {
    node("detail-dialog")?.classList.remove("health-modal-mode");
    HEALTH.activeDialogAction = null;
  });
}

export async function loadHealthPane() {
  if (!HEALTH.inited) initHealthPane();
  if (node("health-day")) node("health-day").value = HEALTH.day;
  await refresh({ quiet: Boolean(HEALTH.overview) });
}

async function refresh({ quiet = false } = {}) {
  if (HEALTH.loading) return;
  HEALTH.loading = true;
  HEALTH.error = "";
  const content = node("health-content");
  if (!quiet && content) content.innerHTML = `<div class="health-loading">正在读取健康记录…</div>`;
  try {
    const data = await healthApi(`/api/health/overview?day=${encodeURIComponent(HEALTH.day)}`);
    HEALTH.overview = data;
    HEALTH.day = data.day || HEALTH.day;
    HEALTH.reports = Array.isArray(data.reports) ? data.reports : [];
    if (node("health-day")) node("health-day").value = HEALTH.day;
    await ensurePageData();
    renderPage();
  } catch (error) {
    HEALTH.error = error.message;
    if (content) {
      content.innerHTML = `<div class="health-error"><strong>健康数据暂时读不到</strong><p>${esc(error.message)}</p>
        <button class="health-btn primary" type="button" data-health-action="retry">重试</button></div>`;
    }
  } finally {
    HEALTH.loading = false;
  }
}

async function ensurePageData() {
  if (["body", "sleep", "diet", "exercise"].includes(HEALTH.page)) {
    if (!HEALTH.history.length || HEALTH.historyEnd !== HEALTH.day) {
      const data = await healthApi(`/api/health/history?days=90&end=${encodeURIComponent(HEALTH.day)}`);
      HEALTH.history = Array.isArray(data.records) ? data.records : [];
      HEALTH.historyEnd = HEALTH.day;
    }
  }
  if (HEALTH.page === "archive") {
    HEALTH.archive = await healthApi(`/api/health/archive?day=${encodeURIComponent(HEALTH.day)}`);
  }
  if (HEALTH.page === "doctor") {
    HEALTH.doctor = await healthApi("/api/health/doctor");
    HEALTH.reports = Array.isArray(HEALTH.doctor.reports) ? HEALTH.doctor.reports : HEALTH.reports;
    HEALTH.consults = Array.isArray(HEALTH.doctor.consults) ? HEALTH.doctor.consults : HEALTH.consults;
  }
}

async function navigate(page) {
  const target = PAGES[page] ? page : "today";
  if (HEALTH.page === target && HEALTH.overview) return;
  HEALTH.page = target;
  document.querySelectorAll("[data-health-page]").forEach((button) => {
    const active = button.dataset.healthPage === target;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  if (!HEALTH.overview) { await refresh(); return; }
  try {
    await ensurePageData();
    renderPage();
  } catch (error) {
    const content = node("health-content");
    if (content) content.innerHTML = `<div class="health-error"><strong>读取失败</strong><p>${esc(error.message)}</p></div>`;
  }
}

function renderPage() {
  const content = node("health-content");
  if (!content || !HEALTH.overview) return;
  const renderers = {
    today: renderToday, body: renderBody, sleep: renderSleep, diet: renderDiet,
    exercise: renderExercise, archive: renderArchive, doctor: renderDoctor,
  };
  updateHeader();
  content.innerHTML = (renderers[HEALTH.page] || renderToday)();
  if (HEALTH.page === "doctor") {
    paintChat();
    const input = node("health-chat-input");
    if (input) input.focus({ preventScroll: true });
  }
}

function updateHeader() {
  const page = PAGES[HEALTH.page] || PAGES.today;
  if (node("health-title")) node("health-title").textContent = page.title;
  if (node("health-subtitle")) {
    const suffix = HEALTH.page === "today" || HEALTH.page === "archive" || HEALTH.page === "doctor"
      ? ""
      : ` · ${monthDayLabel(HEALTH.day)} ${weekdayFull(HEALTH.day)}${isToday() ? "（今天）" : ""}`;
    node("health-subtitle").textContent = `${page.subtitle}${suffix}`;
  }
  if (node("health-day")) node("health-day").value = HEALTH.day;
  document.querySelectorAll("[data-health-page]").forEach((button) => {
    const active = button.dataset.healthPage === HEALTH.page;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const score = currentScore().total;
  if (node("health-menu-status")) {
    node("health-menu-status").textContent = Number.isFinite(Number(score))
      ? `当前健康指数 ${Math.round(score)} · 数据只在本机`
      : "还没有足够数据 · 数据只在本机";
  }
}

function retryMarkup() {
  return `<div class="health-error"><strong>健康数据暂时读不到</strong><p>${esc(HEALTH.error || "请稍后重试")}</p>
    <button class="health-btn primary" type="button" data-health-action="retry">重试</button></div>`;
}

// ── 今日记录 ─────────────────────────────────────────────────────────

function renderToday() {
  const overview = HEALTH.overview;
  const score = currentScore();
  const week = Array.isArray(overview.week) ? overview.week : [];
  const recorded = week.find((item) => item.date === HEALTH.day)?.recorded;
  const lead = recorded
    ? "今天已经记了一些；没记的项目会沿用上一次，不会因为没更新就变空白。"
    : "今天还没记录；先从下面任意一项开始，只填一项也算。";
  return `<div class="health-today">
    <section class="health-today-hero">
      <div class="health-today-copy">
        <span class="health-kicker">${isToday() ? "TODAY" : "BACKFILL"} · ${esc(weekdayFull(HEALTH.day))}</span>
        <h2>${esc(monthDayLabel(HEALTH.day))}</h2>
        <p class="health-today-lead">${esc(lead)}</p>
        <div class="health-source-chips">${CURRENT_KEYS.map((key) => {
          const source = score.sources?.[key];
          const label = source?.date
            ? `${CURRENT[key].label} · ${freshness(source, CURRENT[key].window)}`
            : `${CURRENT[key].label} · 待记录`;
          return `<button type="button" class="health-source-chip${source?.date ? "" : " is-empty"}" data-health-action="go-page" data-page="${CURRENT[key].page}">${esc(label)}</button>`;
        }).join("")}</div>
      </div>
      <div class="health-score-block">
        ${ringMarkup(score.total, { size: 138, label: "当前健康指数" })}
        <p class="health-score-note">${esc(scoreNote(score))}</p>
      </div>
      <div class="health-dim-grid" aria-label="四项健康进度">${CURRENT_KEYS.map(dimensionMarkup).join("")}</div>
    </section>
    ${renderWeek(week)}
    <div class="health-record-grid">
      ${sleepCard()}
      ${bodyCard()}
      ${dietCard()}
      ${exerciseCard()}
    </div>
    <section class="health-foot-note">
      <span class="health-foot-icon" aria-hidden="true">医</span>
      <div><strong>记录只在本机。</strong> 健康指数是趋势参考，不是医学诊断；不舒服时请直接问私人医生或线下就医。</div>
      <button class="health-btn ghost" type="button" data-health-action="go-page" data-page="doctor">问问私人医生</button>
    </section>
  </div>`;
}

function dimensionMarkup(key) {
  const meta = CURRENT[key];
  const value = currentScore().breakdown?.[key];
  const source = currentScore().sources?.[key];
  return `<button type="button" class="health-dim" data-tone="${toneOf(value)}" data-health-action="go-page" data-page="${meta.page}">
    <span class="health-dim-icon" aria-hidden="true">${meta.icon}</span>
    <span class="health-dim-main"><strong>${meta.label}</strong><small>${esc(freshness(source, meta.window))}</small></span>
    <span class="health-dim-value">${value === null || value === undefined ? "—" : Math.round(value)}</span>
    ${progressMarkup(value ?? 0, { className: "health-dim-progress", label: `${meta.label}得分` })}
  </button>`;
}

function renderWeek(week) {
  const start = HEALTH.overview.week_start;
  const end = HEALTH.overview.week_end;
  return `<section class="health-week-card">
    <div class="health-week-head">
      <div><span class="health-kicker">这一周</span><strong>${esc(monthDayLabel(start))} – ${esc(monthDayLabel(end))}</strong></div>
      <div class="health-week-actions">
        <button class="health-week-arrow" type="button" data-health-action="week-prev" aria-label="上一周">‹</button>
        <button class="health-btn ghost small" type="button" data-health-action="this-week">本周</button>
        <button class="health-week-arrow" type="button" data-health-action="week-next" aria-label="下一周">›</button>
      </div>
    </div>
    <div class="health-week-days">${week.map((item) => {
      const selected = item.date === HEALTH.day;
      return `<button type="button" class="health-week-day${selected ? " is-selected" : ""}${item.is_today ? " is-today" : ""}" data-health-action="pick-day" data-day="${item.date}" aria-pressed="${selected}">
        <span class="health-week-name">周${esc(weekdayLabel(item.date))}</span>
        <strong>${esc(shortDate(item.date))}</strong>
        <span class="health-week-marker${item.recorded ? " is-recorded" : ""}" aria-hidden="true"></span>
        <small>${item.sleep_minutes ? shortDuration(item.sleep_minutes) : "—"}</small>
        ${item.is_today ? '<em class="health-week-today">今天</em>' : ""}
      </button>`;
    }).join("")}</div>
  </section>`;
}

function cardShell({ key, icon, title, headline, sub, meta, windowDays, actionLabel }) {
  const sectionActual = actual();
  const hasToday = key === "sleep" ? Boolean(sectionValues("sleep").duration_min)
    : key === "body" ? Boolean(sectionValues("body").weight_kg)
      : key === "diet" ? Boolean((sectionActual.meals || []).length || sectionValues("nutrition").food_text)
        : Boolean((sectionActual.training || []).length || sectionValues("activity").exercise_text || sectionValues("activity").steps);
  return `<article class="health-record-card" data-tone="${key}">
    <header class="health-record-head">
      <span class="health-record-icon" aria-hidden="true">${icon}</span>
      <div><h3>${esc(title)}</h3><p>${esc(freshness(meta, windowDays))}</p></div>
      <button class="health-btn primary small" type="button" data-health-action="record" data-section="${key}">${hasToday ? "更新" : actionLabel}</button>
    </header>
    <div class="health-record-value">${headline}</div>
    <p class="health-record-sub">${sub}</p>
    <button class="health-record-more" type="button" data-health-action="go-page" data-page="${CURRENT[key]?.page || key}">查看详情 →</button>
  </article>`;
}

function sleepCard() {
  const meta = effective("sleep");
  const values = meta?.values || sectionValues("sleep");
  const duration = values.duration_min;
  const headline = duration ? `${formatDuration(duration)}` : "还没记录";
  const detail = [];
  if (values.bed && values.wake) detail.push(`${values.bed} → ${values.wake}`);
  if (values.quality) detail.push(`质量 ${values.quality}/5`);
  return cardShell({
    key: "sleep", icon: "眠", title: "睡了多久",
    headline: duration ? `${esc(formatDuration(duration))}` : "还没记录",
    sub: detail.length ? esc(detail.join(" · ")) : "只填时长也行，入睡和起床时间可选。",
    meta, windowDays: 3, actionLabel: "记录",
  });
}

function bodyCard() {
  const meta = effective("body");
  const values = meta?.values || sectionValues("body");
  const weight = values.weight_kg;
  const previous = meta?.previous?.values?.weight_kg;
  const delta = weight && previous ? Number(weight) - Number(previous) : null;
  const derived = currentScore().derived || {};
  const detail = [];
  if (delta !== null) detail.push(`比上次 ${formatSigned(delta, 1, "kg")}`);
  if (derived.bmi) detail.push(`BMI ${derived.bmi}`);
  if (values.body_fat_pct) detail.push(`体脂 ${formatNumber(values.body_fat_pct, 1, "%")}`);
  return cardShell({
    key: "body", icon: "身", title: "体重变化",
    headline: weight ? `${formatNumber(weight, 1)} <small>kg</small>` : "还没记录",
    sub: detail.length ? esc(detail.join(" · ")) : "只填体重就行，其他身体数据可选。",
    meta, windowDays: 30, actionLabel: "记录",
  });
}

function dietCard() {
  const meta = effective("nutrition");
  const totals = meta?.totals || {};
  const meals = meta?.meals || [];
  const text = String(meta?.values?.food_text || "").trim();
  const calories = Number(totals.calories) || 0;
  const headline = calories ? `${formatNumber(calories, 0)} <small>kcal</small>`
    : text ? "已记文字" : "还没记录";
  const detail = [];
  if (meals.length) detail.push(`${meals.length} 条餐次`);
  if (totals.protein_g) detail.push(`蛋白 ${formatNumber(totals.protein_g, 0, "g")}`);
  if (totals.water_ml) detail.push(`饮水 ${formatNumber(totals.water_ml, 0, "ml")}`);
  if (!detail.length && text) detail.push(text.slice(0, 26));
  return cardShell({
    key: "diet", icon: "食", title: "饮食记录",
    headline,
    sub: detail.length ? esc(detail.join(" · ")) : "大概写一写吃了什么，AI 会帮你估算。",
    meta, windowDays: 7, actionLabel: "记录",
  });
}

function exerciseCard() {
  const meta = effective("activity");
  const totals = meta?.totals || {};
  const text = String(meta?.values?.exercise_text || "").trim();
  const minutes = Number(totals.minutes) || 0;
  const calories = Number(totals.calories) || 0;
  const headline = minutes ? `${formatNumber(minutes, 0)} <small>分钟</small>`
    : calories ? `${formatNumber(calories, 0)} <small>kcal</small>`
      : text ? "已记文字" : "还没记录";
  const detail = [];
  if (totals.sessions) detail.push(`${Number(totals.sessions)} 次`);
  if (calories) detail.push(`消耗 ${formatNumber(calories, 0, "kcal")}`);
  if (totals.steps) detail.push(`${formatNumber(totals.steps, 0, "步")}`);
  if (!detail.length && text) detail.push(text.slice(0, 26));
  return cardShell({
    key: "exercise", icon: "动", title: "运动记录",
    headline,
    sub: detail.length ? esc(detail.join(" · ")) : "散步、跑步、力量都算，写一句也行。",
    meta, windowDays: 14, actionLabel: "记录",
  });
}

// ── 身体情况 ─────────────────────────────────────────────────────────

function renderBody() {
  const meta = effective("body");
  const values = meta?.values || {};
  const derived = currentScore().derived || {};
  const previous = meta?.previous?.values?.weight_kg;
  const weight = values.weight_kg;
  const delta = weight && previous ? Number(weight) - Number(previous) : null;
  const weightPoints = pointsFor(HEALTH.history, "body.weight_kg").slice(-HEALTH.trendDays);
  const chart = lineChart([
    { label: "体重", points: weightPoints, unit: "kg", color: "var(--health-body)" },
  ], { normalize: false, width: 960, height: 280 });
  const metrics = [
    ["体重", weight, "kg", 1],
    ["BMI", derived.bmi, "", 1],
    ["体脂率", values.body_fat_pct, "%", 1],
    ["肌肉量", values.muscle_kg, "kg", 1],
    ["体水分", values.water_pct, "%", 1],
    ["内脏脂肪", values.visceral_fat, "级", 0],
    ["腰围", values.waist_cm, "cm", 1],
    ["基础代谢", values.bmr || derived.bmr, "kcal", 0],
  ];
  return `<div class="health-page">
    <section class="health-page-hero" data-tone="body">
      <div class="health-hero-main">
        <span class="health-kicker">最新体重</span>
        <div class="health-big-value">${weight ? formatNumber(weight, 1) : "—"}<small>kg</small></div>
        <p>${delta !== null ? `比上次 ${formatSigned(delta, 1, "kg")}` : "第一次记录体重"}</p>
      </div>
      <div class="health-hero-facts">
        <div><span>BMI</span><strong>${derived.bmi ?? "—"}</strong><small>${esc(derived.bmi_band || "填身高后自动算")}</small></div>
        <div><span>体脂率</span><strong>${formatNumber(values.body_fat_pct, 1, "%")}</strong><small>参考 ${profile().gender === "女" ? "18–32%" : "10–28%"}</small></div>
        <div><span>记录日期</span><strong>${meta ? esc(monthDayLabel(meta.date)) : "—"}</strong><small>${meta ? esc(freshness(meta, 30)) : "还没记录"}</small></div>
      </div>
      <button class="health-btn primary" type="button" data-health-action="record" data-section="body">${sectionValues("body").weight_kg ? "更新身体数据" : "记录身体数据"}</button>
    </section>
    ${staleBanner(meta, 30, "体重")}
    <section class="health-card wide">
      <header class="health-card-head"><div><span class="health-kicker">趋势</span><h3>体重趋势</h3></div>
        <div class="health-segmented">${[30, 90].map((days) => `<button type="button" class="${HEALTH.trendDays === days ? "active" : ""}" data-health-action="trend-days" data-days="${days}">${days} 天</button>`).join("")}</div></header>
      ${weightPoints.length >= 2 ? `${chart.svg}${recordStats(weightPoints, { precision: 1, unit: "kg" })}` : `<div class="health-chart-empty">再记录一次体重，就能看到趋势。</div>`}    </section>
    <section class="health-metric-grid">${metrics.map(([label, value, unit, precision]) =>
      `<div class="health-metric-card"><span>${label}</span><strong>${formatNumber(value, precision, unit)}</strong><small>${meta ? esc(monthDayLabel(meta.date)) : "—"}</small></div>`).join("")}</section>
    <section class="health-card wide">
      <header class="health-card-head"><div><span class="health-kicker">历史</span><h3>最近记录</h3></div><span>${HEALTH.history.length} 天内有记录</span></header>
      ${bodyHistory()}
    </section>
  </div>`;
}

function bodyHistory() {
  const rows = (HEALTH.history || []).filter((record) => record.sections?.body?.weight_kg).slice(-14).reverse();
  if (!rows.length) return `<p class="health-muted">还没有体重记录。</p>`;
  return `<ul class="health-history">${rows.map((record) => {
    const body = record.sections.body || {};
    return `<li><span>${esc(monthDayLabel(record.date))}</span><strong>${formatNumber(body.weight_kg, 1, "kg")}</strong>
      <small>体脂 ${formatNumber(body.body_fat_pct, 1, "%")} · 腰围 ${formatNumber(body.waist_cm, 1, "cm")}</small></li>`;
  }).join("")}</ul>`;
}

function recordStats(points, { precision = 0, unit = "" } = {}) {
  const stats = seriesStats(points);
  if (!stats.count) return `<p class="health-muted">还没有足够数据。</p>`;
  return `<dl class="health-stats">
    <div><dt>最新</dt><dd>${formatNumber(stats.latest, precision, unit)}</dd></div>
    <div><dt>均值</dt><dd>${formatNumber(stats.avg, precision, unit)}</dd></div>
    <div><dt>区间</dt><dd>${formatNumber(stats.min, precision)}–${formatNumber(stats.max, precision, unit)}</dd></div>
    <div><dt>变化</dt><dd>${formatSigned(stats.delta, precision, unit)}</dd></div>
  </dl>`;
}

function staleBanner(meta, windowDays, label) {
  if (!meta?.date) return "";
  if (isStale(meta, windowDays)) {
    return `<div class="health-banner warn"><strong>该更新${esc(label)}了</strong><span>上次记录是 ${esc(monthDayLabel(meta.date))}（${Number(meta.age)} 天前），超过 ${windowDays} 天就不再参与健康指数。</span></div>`;
  }
  if (Number(meta.age) > 0) {
    return `<div class="health-banner soft"><strong>沿用了上一次记录</strong><span>最近一次是 ${esc(monthDayLabel(meta.date))}（${Number(meta.age)} 天前）；今天还没更新，页面先沿用这条。</span></div>`;
  }
  return "";
}

// ── 睡眠情况 ─────────────────────────────────────────────────────────

function renderSleep() {
  const meta = effective("sleep");
  const values = meta?.values || {};
  const derived = currentScore().derived || {};
  const duration = values.duration_min;
  const durationPoints = pointsFor(HEALTH.history, "sleep.duration_min");
  const recent = durationPoints.slice(-HEALTH.trendDays)
    .map((point) => ({ ...point, value: Number(point.value) / 60 }));
  const bedtimes = (HEALTH.history || []).map((record) => clockMinutes(record.sections?.sleep?.bed))
    .filter((value) => value !== null);
  const regularity = stddev(bedtimes);
  const averageDuration = average(durationPoints.map((point) => point.value));
  const chart = barChart(recent.map((point) => ({
    ...point, color: point.value >= 7 ? "var(--health-good)" : "var(--health-warn)",
  })), { unit: "小时", width: 960, height: 280 });
  return `<div class="health-page">
    <section class="health-page-hero" data-tone="sleep">
      <div class="health-hero-main">
        <span class="health-kicker">最近一次睡眠</span>
        <div class="health-big-value">${duration ? esc(formatDuration(duration)) : "—"}</div>
        <p>${values.bed && values.wake ? `${esc(values.bed)} → ${esc(values.wake)}` : "入睡和起床时间可选"}${values.quality ? ` · 质量 ${values.quality}/5` : ""}</p>
      </div>
      <div class="health-hero-facts">
        <div><span>睡眠评分</span><strong>${derived.sleep_score ?? "—"}</strong><small>${esc(derived.sleep_rating || "需要时长")}</small></div>
        <div><span>平均时长</span><strong>${averageDuration ? esc(formatDuration(averageDuration)) : "—"}</strong><small>${durationPoints.length} 天有记录</small></div>
        <div><span>作息规律</span><strong>${regularity === null ? "—" : `±${Math.round(regularity)} 分`}</strong><small>按入睡时间</small></div>
      </div>
      <button class="health-btn primary" type="button" data-health-action="record" data-section="sleep">${sectionValues("sleep").duration_min ? "更新睡眠" : "记录睡眠"}</button>
    </section>
    ${staleBanner(meta, 3, "睡眠")}
    <section class="health-card wide">
      <header class="health-card-head"><div><span class="health-kicker">趋势</span><h3>睡了多久（小时）</h3></div>
        <div class="health-segmented">${[7, 30, 90].map((days) => `<button type="button" class="${HEALTH.trendDays === days ? "active" : ""}" data-health-action="trend-days" data-days="${days}">${days} 天</button>`).join("")}</div></header>
      ${recent.length >= 2 ? chart : `<div class="health-chart-empty">再记录一晚睡眠，就能看到趋势。</div>`}
    </section>
    <section class="health-card wide">
      <header class="health-card-head"><div><span class="health-kicker">历史</span><h3>最近睡眠</h3></div><span>只显示有记录的日期</span></header>
      ${sleepHistory()}
    </section>
  </div>`;
}

function sleepHistory() {
  const rows = (HEALTH.history || []).filter((record) => record.sections?.sleep?.duration_min).slice(-14).reverse();
  if (!rows.length) return `<p class="health-muted">还没有睡眠记录。</p>`;
  return `<ul class="health-history">${rows.map((record) => {
    const sleep = record.sections.sleep || {};
    return `<li><span>${esc(monthDayLabel(record.date))}</span><strong>${esc(formatDuration(sleep.duration_min))}</strong>
      <small>${esc([sleep.bed && sleep.wake ? `${sleep.bed} → ${sleep.wake}` : "",
        sleep.quality ? `质量 ${sleep.quality}/5` : ""].filter(Boolean).join(" · ") || "只有时长")}</small></li>`;
  }).join("")}</ul>`;
}

// ── 饮食情况 ─────────────────────────────────────────────────────────

function renderDiet() {
  const meta = effective("nutrition");
  const totals = meta?.totals || {};
  const meals = meta?.meals || [];
  const targets = HEALTH.overview?.targets || {};
  const macros = macroProgress(totals, targets);
  const foodText = String(meta?.values?.food_text || "").trim();
  const calories = Number(totals.calories) || 0;
  const caloriePercent = percentOf(calories, targets.calories);
  const caloriePoints = pointsFor(HEALTH.history, "nutrition.calories").slice(-HEALTH.trendDays);
  const chart = barChart(caloriePoints, {
    unit: "kcal", width: 960, height: 260, color: "var(--health-diet)",
  });
  return `<div class="health-page">
    <section class="health-page-hero" data-tone="diet">
      <div class="health-hero-main">
        <span class="health-kicker">最近一次饮食</span>
        <div class="health-big-value">${calories ? formatNumber(calories, 0) : "—"}<small>kcal</small></div>
        <p>${foodText ? esc(foodText.slice(0, 48)) : meals.length ? `${meals.length} 条餐次` : "还没记录"}</p>
      </div>
      <div class="health-hero-facts">
        <div><span>蛋白质</span><strong>${formatNumber(totals.protein_g, 0, "g")}</strong><small>${targets.protein_g ? `目标 ${formatNumber(targets.protein_g, 0, "g")}` : "目标未设"}</small></div>
        <div><span>饮水</span><strong>${formatNumber(totals.water_ml, 0, "ml")}</strong><small>${targets.water_ml ? `目标 ${formatNumber(targets.water_ml, 0, "ml")}` : ""}</small></div>
        <div><span>记录日期</span><strong>${meta ? esc(monthDayLabel(meta.date)) : "—"}</strong><small>${meta ? esc(freshness(meta, 7)) : "还没记录"}</small></div>
      </div>
      <button class="health-btn primary" type="button" data-health-action="record" data-section="diet">${(actual().meals || []).length || sectionValues("nutrition").food_text ? "更新饮食" : "记录饮食"}</button>
    </section>
    ${staleBanner(meta, 7, "饮食")}
    <section class="health-card wide">
      <header class="health-card-head"><div><span class="health-kicker">今天吃了什么</span><h3>餐次明细</h3></div><span>${meals.length} 条</span></header>
      ${meals.length ? `<div class="health-meals">${MEAL_TYPES.map((type) => {
        const group = meals.filter((meal) => meal.meal_type === type || (!meal.meal_type && type === "加餐"));
        if (!group.length) return "";
        return `<div class="health-meal-group"><h4>${type}</h4>${group.map((meal) =>
          `<div class="health-meal-line"><span>${esc(meal.name)}</span><small>${esc(meal.portion || "")}${meal.calories ? ` · ${formatNumber(meal.calories, 0, "kcal")}` : ""}</small></div>`).join("")}</div>`;
      }).join("")}</div>` : `<p class="health-muted">还没有餐次。记录时写一句「米饭、青菜和鸡胸肉」，AI 会帮你拆。</p>`}
      ${foodText ? `<p class="health-quote">“${esc(foodText)}”</p>` : ""}
    </section>
    <section class="health-card wide">
      <header class="health-card-head"><div><span class="health-kicker">营养</span><h3>热量与三大营养素</h3></div><span>${caloriePercent === null ? "" : `${Math.round(caloriePercent)}% 目标`}</span></header>
      ${progressMarkup(caloriePercent ?? 0, { className: "health-calorie-progress", label: "热量完成度" })}
      <div class="health-macro-grid">${macros.map((macro) => `<div class="health-macro">
        <span>${macro.label}</span><strong>${formatNumber(macro.value, 0, macro.unit)}</strong>
        <small>${macro.target ? `目标 ${formatNumber(macro.target, 0, macro.unit)}` : "目标未设置"}</small>
        ${progressMarkup(macro.percent, { className: `health-macro-progress ${macro.cls}`, label: `${macro.label}完成度` })}
      </div>`).join("")}</div>
    </section>
    <section class="health-card wide">
      <header class="health-card-head"><div><span class="health-kicker">趋势</span><h3>每天的热量（kcal）</h3></div>
        <div class="health-segmented">${[7, 30, 90].map((days) => `<button type="button" class="${HEALTH.trendDays === days ? "active" : ""}" data-health-action="trend-days" data-days="${days}">${days} 天</button>`).join("")}</div></header>
      ${caloriePoints.length >= 2 ? chart : `<div class="health-chart-empty">再记录一天饮食，就能看到趋势。</div>`}
    </section>
    <section class="health-card wide">
      <header class="health-card-head"><div><span class="health-kicker">历史</span><h3>最近饮食</h3></div><span>只显示有记录的日期</span></header>
      ${dietHistory()}
    </section>
  </div>`;
}

function dietHistory() {
  const rows = (HEALTH.history || []).filter((record) =>
    (record.meals || []).length || (record.sections?.nutrition?.food_text || "")).slice(-14).reverse();
  if (!rows.length) return `<p class="health-muted">还没有饮食记录。</p>`;
  return `<ul class="health-history">${rows.map((record) => {
    const totals = record.nutrition || {};
    const text = record.sections?.nutrition?.food_text || "";
    return `<li><span>${esc(monthDayLabel(record.date))}</span><strong>${formatNumber(totals.calories, 0, "kcal")}</strong>
      <small>${esc(text.slice(0, 40) || `${(record.meals || []).length} 条餐次`)}</small></li>`;
  }).join("")}</ul>`;
}

// ── 运动记录 ─────────────────────────────────────────────────────────

function renderExercise() {
  const meta = effective("activity");
  const totals = meta?.totals || {};
  const values = meta?.values || {};
  const weekStart = HEALTH.overview?.week_start || "";
  const weekEnd = HEALTH.overview?.week_end || "";
  const weekRecords = (HEALTH.history || []).filter((record) => record.date >= weekStart && record.date <= weekEnd);
  const weekMinutes = weekRecords.reduce((sum, record) => sum + Number(record.training_totals?.minutes || 0), 0);
  const weekCalories = weekRecords.reduce((sum, record) => sum + Number(record.training_totals?.calories || 0), 0);
  const weekSessions = weekRecords.reduce((sum, record) => sum + (record.training || []).length, 0);
  const text = String(values.exercise_text || "").trim();
  const minutePoints = pointsFor(HEALTH.history, "training.minutes").slice(-HEALTH.trendDays);
  const chart = barChart(minutePoints, {
    unit: "分钟", width: 960, height: 260, color: "var(--health-exercise)",
  });
  return `<div class="health-page">
    <section class="health-page-hero" data-tone="exercise">
      <div class="health-hero-main">
        <span class="health-kicker">这一周运动</span>
        <div class="health-big-value">${formatNumber(weekMinutes, 0)}<small>分钟</small></div>
        <p>${weekSessions} 次${weekCalories ? ` · 消耗 ${formatNumber(weekCalories, 0, "kcal")}` : ""} · 本周合计</p>
      </div>
      <div class="health-hero-facts">
        <div><span>最近一次</span><strong>${meta ? esc(monthDayLabel(meta.date)) : "—"}</strong><small>${meta ? esc(freshness(meta, 14)) : "还没记录"}</small></div>
        <div><span>最近运动</span><strong>${formatNumber(totals.minutes, 0, "分钟")}</strong><small>${Number(totals.sessions) || 0} 次</small></div>
        <div><span>步数</span><strong>${formatNumber(values.steps, 0)}</strong><small>日常活动</small></div>
      </div>
      <div class="health-hero-actions">
        <button class="health-btn primary" type="button" data-health-action="record" data-section="exercise">${(actual().training || []).length || sectionValues("activity").exercise_text ? "更新运动" : "记录运动"}</button>
        <button class="health-btn ghost" type="button" data-health-action="precise-training">精细记录训练</button>
      </div>
    </section>
    ${staleBanner(meta, 14, "运动")}
    <section class="health-card wide">
      <header class="health-card-head"><div><span class="health-kicker">最近一次</span><h3>动了什么</h3></div><span>${esc(text.slice(0, 40))}</span></header>
      ${(meta?.training || []).length ? `<div class="health-sessions">${meta.training.map((session) => sessionMarkup(session, true, meta.date)).join("")}</div>`
        : `<p class="health-muted">还没有运动明细${text ? "，只记了文字描述。" : "。"}</p>`}
    </section>
    <section class="health-card wide">
      <header class="health-card-head"><div><span class="health-kicker">趋势</span><h3>每天运动多久（分钟）</h3></div>
        <div class="health-segmented">${[7, 30, 90].map((days) => `<button type="button" class="${HEALTH.trendDays === days ? "active" : ""}" data-health-action="trend-days" data-days="${days}">${days} 天</button>`).join("")}</div></header>
      ${minutePoints.length >= 2 ? chart : `<div class="health-chart-empty">再记录一次运动，就能看到趋势。</div>`}
    </section>
    <section class="health-card wide">
      <header class="health-card-head"><div><span class="health-kicker">历史</span><h3>最近运动</h3></div><span>只显示有记录的日期</span></header>
      ${exerciseHistory()}
    </section>
  </div>`;
}

function sessionMarkup(session, withDelete = true, day = HEALTH.day) {
  const parts = [session.kind || "运动"];
  if (session.minutes) parts.push(`${session.minutes} 分钟`);
  if (session.calories) parts.push(`${formatNumber(session.calories, 0, "kcal")}`);
  if (session.distance_km) parts.push(`${formatNumber(session.distance_km, 1, "km")}`);
  if (session.steps) parts.push(`${formatNumber(session.steps, 0, "步")}`);
  if (session.note) parts.push(session.note);
  return `<article class="health-session"><strong>${esc(parts.shift())}</strong><span>${esc(parts.join(" · "))}</span>
    ${withDelete && session.id ? `<button class="health-row-remove" type="button" data-health-action="delete-session" data-id="${esc(session.id)}" data-day="${esc(day)}" aria-label="删除这次运动">×</button>` : ""}</article>`;
}

function exerciseHistory() {
  const rows = (HEALTH.history || []).filter((record) => (record.training || []).length).slice(-14).reverse();
  if (!rows.length) return `<p class="health-muted">还没有运动记录。</p>`;
  return `<ul class="health-history">${rows.map((record) => {
    const totals = record.training_totals || {};
    return `<li><span>${esc(monthDayLabel(record.date))}</span><strong>${formatNumber(totals.minutes, 0, "分钟")}</strong>
      <small>${(record.training || []).map((session) => session.kind).join(" / ")}${totals.calories ? ` · ${formatNumber(totals.calories, 0, "kcal")}` : ""}</small></li>`;
  }).join("")}</ul>`;
}

// ── 健康档案 ─────────────────────────────────────────────────────────

function renderArchive() {
  const data = HEALTH.archive;
  if (!data) return `<div class="health-loading">正在读取健康档案…</div>`;
  const profile = data.profile || {};
  const latest = data.latest || {};
  const score = data.current || {};
  const age = ageFromBirth(profile.birth_date);
  const body = latest.body?.values || {};
  const sleep = latest.sleep?.values || {};
  const nutrition = latest.nutrition?.totals || {};
  const activity = latest.activity?.totals || {};
  const labs = data.labs || { rows: [] };
  const reports = data.reports || [];
  const consults = data.consults || [];
  const custom = profile.custom_metrics || [];
  const customValues = latest.custom?.values || {};
  const goals = profile.goals || {};
  return `<div class="health-archive">
    <section class="health-archive-cover">
      <div class="health-avatar" aria-hidden="true">${esc(Array.from(profile.name || "我")[0] || "我")}</div>
      <div class="health-cover-copy">
        <span class="health-kicker">HEALTH ARCHIVE · 健康档案</span>
        <h2>${esc(profile.name || "我的健康档案")}</h2>
        <p>${esc([profile.gender || "性别未填", age ? `${age} 岁` : "年龄未填", profile.height_cm ? `${profile.height_cm} cm` : "身高未填", profile.blood_type ? `血型 ${profile.blood_type}` : ""].filter(Boolean).join(" · "))}</p>
      </div>
      <div class="health-cover-actions">
        <button class="health-btn ghost" type="button" data-health-action="edit-profile">编辑档案</button>
        <button class="health-btn ghost" type="button" data-health-action="edit-goals">编辑目标</button>
      </div>
      <div class="health-cover-stats">
        <div><span>最新体重</span><strong>${body.weight_kg ? formatNumber(body.weight_kg, 1, "kg") : "—"}</strong><small>${latest.body ? esc(monthDayLabel(latest.body.date)) : "还没记录"}</small></div>
        <div><span>BMI</span><strong>${score.derived?.bmi ?? "—"}</strong><small>${esc(score.derived?.bmi_band || "需要身高体重")}</small></div>
        <div><span>最近睡眠</span><strong>${sleep.duration_min ? esc(formatDuration(sleep.duration_min)) : "—"}</strong><small>${latest.sleep ? esc(monthDayLabel(latest.sleep.date)) : "还没记录"}</small></div>
        <div><span>最近运动</span><strong>${formatNumber(activity.minutes, 0, "分钟")}</strong><small>${Number(activity.sessions) || 0} 次</small></div>
      </div>
    </section>

    <section class="health-dossier-grid">
      <article class="health-dossier">
        <header><h3>基本档案</h3><button class="health-btn ghost small" type="button" data-health-action="edit-profile">编辑</button></header>
        <dl class="health-facts">
          <div><dt>称呼</dt><dd>${esc(profile.name || "未填写")}</dd></div>
          <div><dt>性别</dt><dd>${esc(profile.gender || "未填写")}</dd></div>
          <div><dt>出生日期</dt><dd>${esc(profile.birth_date || "未填写")}</dd></div>
          <div><dt>身高</dt><dd>${profile.height_cm ? `${profile.height_cm} cm` : "未填写"}</dd></div>
          <div><dt>血型</dt><dd>${esc(profile.blood_type || "未填写")}</dd></div>
          <div><dt>过敏</dt><dd>${esc(profile.allergies || "未填写")}</dd></div>
          <div class="wide"><dt>既往病史</dt><dd>${esc(profile.medical_history || "未填写")}</dd></div>
        </dl>
      </article>

      <article class="health-dossier">
        <header><h3>身体数据</h3><span>${latest.body ? esc(freshness(latest.body, 30)) : "还没记录"}</span></header>
        <dl class="health-facts">
          <div><dt>体重</dt><dd>${formatNumber(body.weight_kg, 1, "kg")}</dd></div>
          <div><dt>体脂率</dt><dd>${formatNumber(body.body_fat_pct, 1, "%")}</dd></div>
          <div><dt>肌肉量</dt><dd>${formatNumber(body.muscle_kg, 1, "kg")}</dd></div>
          <div><dt>体水分</dt><dd>${formatNumber(body.water_pct, 1, "%")}</dd></div>
          <div><dt>腰围</dt><dd>${formatNumber(body.waist_cm, 1, "cm")}</dd></div>
          <div><dt>基础代谢</dt><dd>${formatNumber(body.bmr || score.derived?.bmr, 0, "kcal")}</dd></div>
        </dl>
      </article>

      <article class="health-dossier">
        <header><h3>睡眠</h3><span>${latest.sleep ? esc(freshness(latest.sleep, 3)) : "还没记录"}</span></header>
        <dl class="health-facts">
          <div><dt>时长</dt><dd>${sleep.duration_min ? esc(formatDuration(sleep.duration_min)) : "—"}</dd></div>
          <div><dt>入睡 → 起床</dt><dd>${sleep.bed && sleep.wake ? `${esc(sleep.bed)} → ${esc(sleep.wake)}` : "—"}</dd></div>
          <div><dt>主观质量</dt><dd>${sleep.quality ? `${sleep.quality}/5` : "—"}</dd></div>
          <div><dt>睡眠评分</dt><dd>${score.derived?.sleep_score ?? "—"}</dd></div>
        </dl>
      </article>

      <article class="health-dossier">
        <header><h3>饮食</h3><span>${latest.nutrition ? esc(freshness(latest.nutrition, 7)) : "还没记录"}</span></header>
        <dl class="health-facts">
          <div><dt>热量</dt><dd>${formatNumber(nutrition.calories, 0, "kcal")}</dd></div>
          <div><dt>蛋白质</dt><dd>${formatNumber(nutrition.protein_g, 0, "g")}</dd></div>
          <div><dt>碳水</dt><dd>${formatNumber(nutrition.carbs_g, 0, "g")}</dd></div>
          <div><dt>脂肪</dt><dd>${formatNumber(nutrition.fat_g, 0, "g")}</dd></div>
          <div><dt>饮水</dt><dd>${formatNumber(nutrition.water_ml, 0, "ml")}</dd></div>
          <div><dt>餐次</dt><dd>${(latest.nutrition?.meals || []).length} 条</dd></div>
        </dl>
        ${latest.nutrition?.values?.food_text ? `<p class="health-quote">“${esc(latest.nutrition.values.food_text)}”</p>` : ""}
      </article>

      <article class="health-dossier">
        <header><h3>运动</h3><span>${latest.activity ? esc(freshness(latest.activity, 14)) : "还没记录"}</span></header>
        <dl class="health-facts">
          <div><dt>最近运动</dt><dd>${formatNumber(activity.minutes, 0, "分钟")}</dd></div>
          <div><dt>次数</dt><dd>${Number(activity.sessions) || 0} 次</dd></div>
          <div><dt>消耗</dt><dd>${formatNumber(activity.calories, 0, "kcal")}</dd></div>
          <div><dt>步数</dt><dd>${formatNumber(latest.activity?.values?.steps, 0)}</dd></div>
        </dl>
        ${latest.activity?.values?.exercise_text ? `<p class="health-quote">“${esc(latest.activity.values.exercise_text)}”</p>` : ""}
      </article>

      <article class="health-dossier wide">
        <header><h3>检查与用药</h3><div class="health-dossier-actions"><button class="health-btn ghost small" type="button" data-health-action="add-lab">添加检验</button><button class="health-btn ghost small" type="button" data-health-action="add-medication">添加用药</button></div></header>
        ${labsMarkup(labs)}
        ${medicationsMarkup(profile.medications || [])}
      </article>

      <article class="health-dossier">
        <header><h3>目标</h3><button class="health-btn ghost small" type="button" data-health-action="edit-goals">编辑</button></header>
        <dl class="health-facts">${Object.entries(goals).filter(([, value]) => value !== null && value !== "").map(([key, value]) =>
          `<div><dt>${esc(goalLabel(key))}</dt><dd>${esc(goalValue(key, value))}</dd></div>`).join("") || `<p class="health-muted">还没有设置目标。</p>`}</dl>
      </article>

      <article class="health-dossier">
        <header><h3>自定义指标</h3><button class="health-btn ghost small" type="button" data-health-action="add-custom-metric">添加</button></header>
        ${custom.length ? `<ul class="health-mini-list">${custom.map((item) => `<li><span>${esc(item.label)}</span><strong>${formatNumber(customValues[item.label], 1, item.unit || "")}</strong><button class="health-row-remove" type="button" data-health-action="delete-custom-metric" data-label="${esc(item.label)}" aria-label="删除 ${esc(item.label)}">×</button></li>`).join("")}</ul>`
          : `<p class="health-muted">例如「尿酸」「腰臀比」；加完后可以在每日记录里填。</p>`}
      </article>

      <article class="health-dossier wide">
        <header><h3>问诊记录</h3><button class="health-btn ghost small" type="button" data-health-action="go-page" data-page="doctor">去私人医生</button></header>
        ${consults.length ? `<ul class="health-mini-list">${consults.slice(0, 5).map((consult) => `<li><button class="health-link" type="button" data-health-action="open-consult" data-path="${esc(consult.path)}">${esc(consult.title)}</button><small>${esc(consult.date)}</small></li>`).join("")}</ul>`
          : `<p class="health-muted">还没有问诊记录；在私人医生页问一句，回复会自动记到当天文档。</p>`}
      </article>

      <article class="health-dossier wide">
        <header><h3>健康报告</h3><button class="health-btn ghost small" type="button" data-health-action="go-page" data-page="doctor">去私人医生</button></header>
        ${reports.length ? `<ul class="health-mini-list">${reports.slice(0, 5).map((report) => `<li><button class="health-link" type="button" data-health-action="open-report" data-path="${esc(report.path)}">${esc(report.title)}</button><small>${esc(report.date)}</small></li>`).join("")}</ul>`
          : `<p class="health-muted">还没有报告；在私人医生页可以按最新数据生成一份。</p>`}
      </article>
    </section>
  </div>`;
}

function labsMarkup(labs) {
  const rows = Array.isArray(labs.rows) ? labs.rows : [];
  if (!rows.length) return `<p class="health-muted">还没有检验记录。添加一次体检的关键项，异常会在这里标出来。</p>`;
  const groups = new Map();
  for (const row of rows) {
    const key = row.category || "其他";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return `<div class="health-lab-list">${[...groups.entries()].map(([category, items]) =>
    `<div class="health-lab-group"><h4>${esc(category)}</h4><table class="health-table"><thead><tr><th>日期</th><th>项目</th><th>数值</th><th>参考</th><th></th></tr></thead><tbody>
      ${items.map((row) => `<tr class="${row.abnormal ? "abnormal" : ""}"><td>${esc(row.date)}</td><td>${esc(row.test)}</td>
        <td>${formatNumber(row.value, Number.isInteger(row.value) ? 0 : 2)} ${esc(row.unit || "")}${row.abnormal ? "<em>异常</em>" : ""}</td>
        <td>${row.ref_min === null && row.ref_max === null ? "—" : `${row.ref_min ?? "—"} – ${row.ref_max ?? "—"}`}</td>
        <td><button class="health-row-remove" type="button" data-health-action="delete-lab" data-id="${esc(row.id)}" aria-label="删除这条检验">×</button></td></tr>`).join("")}
    </tbody></table></div>`).join("")}</div>
    <p class="health-note">异常只按参考区间判断，不是诊断；有报告时以报告上的区间为准。</p>`;
}

function medicationsMarkup(medications) {
  if (!medications.length) return `<p class="health-muted">还没有用药记录。</p>`;
  return `<h4 class="health-subhead">在用药</h4><ul class="health-mini-list">${medications.map((item) => `<li><span>${esc(item.name)}</span><strong>${esc([item.dosage, item.frequency].filter(Boolean).join(" · "))}</strong><button class="health-row-remove" type="button" data-health-action="delete-medication" data-name="${esc(item.name)}" aria-label="删除 ${esc(item.name)}">×</button></li>`).join("")}</ul>`;
}

function goalLabel(key) {
  return {
    weight_kg: "目标体重", body_fat_pct: "目标体脂", sleep_minutes: "目标睡眠",
    steps: "目标步数", water_ml: "目标饮水", weekly_training: "每周运动",
    calories: "目标热量", protein_g: "目标蛋白",
  }[key] || key;
}

function goalValue(key, value) {
  if (key === "sleep_minutes") return formatDuration(value);
  if (key === "water_ml") return `${value} ml`;
  if (key === "calories") return `${value} kcal`;
  if (key === "protein_g") return `${value} g`;
  if (key === "steps") return `${value} 步`;
  if (key === "weekly_training") return `${value} 次`;
  if (key === "weight_kg") return `${value} kg`;
  if (key === "body_fat_pct") return `${value}%`;
  return String(value);
}

// ── 私人医生 ─────────────────────────────────────────────────────────

function renderDoctor() {
  const data = HEALTH.doctor;
  if (!data) return `<div class="health-loading">正在读取私人医生…</div>`;
  const caps = data.capabilities || {};
  const scope = data.scope || {};
  const reports = Array.isArray(data.reports) ? data.reports : HEALTH.reports;
  const consults = Array.isArray(data.consults) ? data.consults : HEALTH.consults;
  return `<div class="health-doctor">
    <section class="health-doctor-hero">
      <div class="health-doctor-avatar" aria-hidden="true">医</div>
      <div class="health-doctor-copy">
        <span class="health-kicker">PRIVATE DOCTOR · 私人医生</span>
        <h2>有哪里不舒服，直接说。</h2>
        <p>已读取 ${Number(scope.days) || 0} 天记录 · ${Number(scope.labs) || 0} 项检验 · ${Number(scope.reports) || 0} 份报告 · <span id="health-consult-scope">${Number(scope.consults) || 0}</span> 份问诊记录；回答只基于这些数据和你的描述。</p>
      </div>
      <span class="health-doctor-badge ${caps.ai ? "is-on" : "is-off"}">${caps.ai ? "AI 可用" : "AI 未配置"}</span>
    </section>
    <section class="health-chat-card">
      <header class="health-chat-head"><div><span class="health-kicker">问诊</span><h3>和私人医生聊聊</h3></div><span class="health-chat-status" id="health-chat-status">${caps.ai ? CHAT_IDLE_STATUS : "AI 未配置，暂时不能问诊"}</span></header>
      <div class="health-chat-messages" id="health-chat-messages"></div>
      <div class="health-chat-quick">${[
        "我最近睡不好", "帮我看看体重趋势", "今天吃得怎么样", "我有点不舒服",
      ].map((text) => `<button type="button" class="health-chip" data-health-action="chat-prompt" data-text="${esc(text)}">${esc(text)}</button>`).join("")}</div>
      <form class="health-chat-form" data-health-form="doctor-chat">
        <textarea id="health-chat-input" name="message" rows="2" maxlength="2000" placeholder="${caps.ai ? "例如：这两天头有点晕，睡眠也不太好……" : "AI 未配置"}" ${caps.ai ? "" : "disabled"}></textarea>
        <button class="health-btn primary" type="submit" ${caps.ai ? "" : "disabled"}>发送</button>
      </form>
      <p class="health-disclaimer">每次问诊会自动追加到当天「问诊记录」；私人医生只做健康建议，不诊断疾病、不开药。持续异常或危险信号请尽快线下就医。</p>
    </section>
    <section class="health-notebook">
      <header class="health-notebook-head">
        <div class="health-notebook-cover" aria-hidden="true">诊</div>
        <div><span class="health-kicker">固定本子 · 自动记录</span><h3>问诊记录</h3><p id="health-consult-count">${consults.length} 天 · 每次问诊自动追加</p></div>
      </header>
      <div id="health-consult-list">${consultListMarkup(consults)}</div>
    </section>
    <section class="health-notebook">
      <header class="health-notebook-head">
        <div class="health-notebook-cover" aria-hidden="true">报告</div>
        <div><span class="health-kicker">固定本子 · 不可删除</span><h3>健康报告</h3><p>${reports.length} 份 · 按日期归档，最新数据生成</p></div>
        <button class="health-btn primary" type="button" data-health-action="generate-report" ${caps.ai ? "" : "disabled"}>生成健康报告</button>
      </header>
      ${reports.length ? `<div class="health-report-list">${reports.map((report) => `<article class="health-report-item">
        <button class="health-report-open" type="button" data-health-action="open-report" data-path="${esc(report.path)}">
          <span class="health-report-date">${esc(monthDayLabel(report.date || ""))}</span>
          <strong>${esc(report.title || "健康报告")}</strong>
          <small>${esc(report.excerpt || "点击打开")}</small>
        </button>
        <button class="health-row-remove" type="button" data-health-action="delete-report" data-path="${esc(report.path)}" aria-label="删除这份报告">×</button>
      </article>`).join("")}</div>`
        : `<div class="health-empty"><strong>还没有健康报告</strong><p>点上面的「生成健康报告」，私人医生会基于最近 30 天记录和最新档案写一份。</p></div>`}
    </section>
  </div>`;
}

function consultListMarkup(consults) {
  if (!consults.length) {
    return `<div class="health-empty"><strong>还没有问诊记录</strong><p>在上面问一句，回复会自动追加到当天文档；同一天多次问诊会写在同一篇里。</p></div>`;
  }
  return `<div class="health-report-list">${consults.map((consult) => `<article class="health-report-item">
    <button class="health-report-open" type="button" data-health-action="open-consult" data-path="${esc(consult.path)}">
      <span class="health-report-date">${esc(monthDayLabel(consult.date || ""))}</span>
      <strong>${esc(consult.title || "问诊记录")}</strong>
      <small>${esc(consult.excerpt || "点击打开")}</small>
    </button>
    <button class="health-row-remove" type="button" data-health-action="delete-consult" data-path="${esc(consult.path)}" aria-label="删除这份问诊记录">×</button>
  </article>`).join("")}</div>`;
}

function paintConsults() {
  const container = node("health-consult-list");
  if (container) container.innerHTML = consultListMarkup(HEALTH.consults);
  const count = node("health-consult-count");
  if (count) count.textContent = `${HEALTH.consults.length} 天 · 每次问诊自动追加`;
  const scope = node("health-consult-scope");
  if (scope) scope.textContent = String(HEALTH.consults.length);
}

function paintChat() {
  const container = node("health-chat-messages");
  if (!container) return;
  const messages = HEALTH.doctorChat.messages;
  if (!messages.length) {
    container.innerHTML = `<div class="health-chat-empty"><strong>你可以这样问</strong><p>“我最近睡不好” · “帮我看看体重” · “今天吃得怎么样” · “我有点不舒服”</p></div>`;
  } else {
    container.innerHTML = messages.map((message) => {
      if (message.role === "user") {
        return `<div class="health-chat-bubble is-user"><p>${esc(message.content)}</p></div>`;
      }
      return `<div class="health-chat-bubble is-doctor"><span class="health-chat-mark" aria-hidden="true">医</span><div class="md-body">${message.html || esc(message.content)}</div></div>`;
    }).join("");
  }
  container.scrollTop = container.scrollHeight;
  const send = document.querySelector(".health-chat-form button[type=submit]");
  if (send) {
    send.disabled = HEALTH.doctorChat.busy || !HEALTH.doctor?.capabilities?.ai;
    send.textContent = HEALTH.doctorChat.busy ? "医生正在看…" : "发送";
  }
  const status = node("health-chat-status");
  if (status) status.textContent = HEALTH.doctorChat.busy ? "正在读取你的记录…"
    : (HEALTH.doctorChat.contextNotice || CHAT_IDLE_STATUS);
}

async function sendDoctorMessage(text) {
  const message = String(text || "").trim();
  if (!message || HEALTH.doctorChat.busy) return;
  if (!HEALTH.doctor?.capabilities?.ai) { toast("AI 未配置，暂时不能问诊", "error"); return; }
  HEALTH.doctorChat.busy = true;
  HEALTH.doctorChat.contextNotice = "";
  HEALTH.doctorChat.messages.push({ role: "user", content: message });
  paintChat();
  const form = document.querySelector(".health-chat-form");
  if (form) form.reset();
  try {
    const payload = await healthApi("/api/ai/chat", {
      method: "POST", timeoutMs: 180000,
      body: JSON.stringify({
        role_id: "health_doctor", mode: "advice", entry: "health.report",
        thread_id: HEALTH.doctorChat.threadId || "", message,
      }),
    });
    HEALTH.doctorChat.threadId = payload.thread_id || HEALTH.doctorChat.threadId;
    HEALTH.doctorChat.contextNotice = [payload.context_reset
      ? "历史对话保留，从这一轮起按新的资料范围继续。" : "", ...(payload.action_errors || [])].filter(Boolean).join(" ");
    const reply = String(payload.reply || "");
    HEALTH.doctorChat.messages.push({
      role: "assistant", content: reply,
      html: renderMarkdownDocument(reply).html,
    });
    if (reply) await saveConsult(message, reply);
  } catch (error) {
    const input = node("health-chat-input");
    if (input && !input.value) input.value = message;
    HEALTH.doctorChat.messages.push({
      role: "assistant", content: `这一轮没有成功：${error.message}`,
      html: `<p class="health-inline-error">这一轮没有成功：${esc(error.message)}</p>`,
    });
  } finally {
    HEALTH.doctorChat.busy = false;
    paintChat();
    const input = node("health-chat-input");
    if (input) input.focus({ preventScroll: true });
  }
}

async function saveConsult(question, answer) {
  try {
    const result = await healthApi("/api/health/consult", {
      method: "POST", body: JSON.stringify({ question, answer }),
    });
    HEALTH.consults = Array.isArray(result.consults) ? result.consults : HEALTH.consults;
    if (HEALTH.doctor) {
      HEALTH.doctor.consults = HEALTH.consults;
      if (HEALTH.doctor.scope) HEALTH.doctor.scope.consults = HEALTH.consults.length;
    }
    paintConsults();
  } catch (error) {
    toast(`这轮问诊没写进本子：${error.message}`, "error");
  }
}

// ── 动作分发 ─────────────────────────────────────────────────────────

async function handleAction(button) {
  const action = button.dataset.healthAction;
  if (action === "retry") { await refresh(); return; }
  if (action === "go-page") { await navigate(button.dataset.page); return; }
  if (action === "record") { openRecordDialog(button.dataset.section); return; }
  if (action === "precise-training") { openTrainingDialog(); return; }
  if (action === "pick-day") {
    const day = String(button.dataset.day || "");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return;
    HEALTH.day = day;
    if (node("health-day")) node("health-day").value = day;
    await refresh({ quiet: true });
    return;
  }
  if (action === "week-prev" || action === "week-next") {
    HEALTH.day = shiftDay(HEALTH.day, action === "week-prev" ? -7 : 7);
    if (node("health-day")) node("health-day").value = HEALTH.day;
    await refresh({ quiet: true });
    return;
  }
  if (action === "this-week") {
    HEALTH.day = todayText();
    if (node("health-day")) node("health-day").value = HEALTH.day;
    await refresh({ quiet: true });
    return;
  }
  if (action === "trend-days") {
    HEALTH.trendDays = Number(button.dataset.days) || 30;
    renderPage();
    return;
  }
  if (action === "delete-session") {
    const ok = await confirmAction({ title: "删除这次运动？", message: "只删除这一天的这一条运动记录。", confirmLabel: "删除", danger: true });
    if (!ok) return;
    await healthApi(`/api/health/day/${encodeURIComponent(button.dataset.day)}/training/${encodeURIComponent(button.dataset.id)}`, { method: "DELETE" });
    toast("已删除", "success");
    await refresh({ quiet: true });
    return;
  }
  if (action === "open-report") { await openReport(button.dataset.path); return; }
  if (action === "delete-report") {
    const ok = await confirmAction({ title: "删除这份健康报告？", message: "报告会移进笔记回收站，可以从回收站恢复。", confirmLabel: "删除", danger: true });
    if (!ok) return;
    await healthApi(`/api/health/reports?path=${encodeURIComponent(button.dataset.path)}`, { method: "DELETE" });
    toast("报告已移进回收站", "success");
    await refresh({ quiet: true });
    return;
  }
  if (action === "open-consult") { await openConsult(button.dataset.path); return; }
  if (action === "delete-consult") {
    const ok = await confirmAction({ title: "删除这份问诊记录？", message: "问诊记录会移进笔记回收站，可以从回收站恢复。", confirmLabel: "删除", danger: true });
    if (!ok) return;
    const result = await healthApi(`/api/health/consults?path=${encodeURIComponent(button.dataset.path)}`, { method: "DELETE" });
    HEALTH.consults = Array.isArray(result.consults) ? result.consults : HEALTH.consults;
    if (HEALTH.doctor) {
      HEALTH.doctor.consults = HEALTH.consults;
      if (HEALTH.doctor.scope) HEALTH.doctor.scope.consults = HEALTH.consults.length;
    }
    paintConsults();
    toast("问诊记录已移进回收站", "success");
    return;
  }
  if (action === "generate-report") { await generateReport(button); return; }
  if (action === "chat-prompt") { await sendDoctorMessage(button.dataset.text); return; }
  if (action === "edit-profile") { openProfileDialog(); return; }
  if (action === "edit-goals") { openGoalsDialog(); return; }
  if (action === "add-custom-metric") { openCustomMetricDialog(); return; }
  if (action === "delete-custom-metric") { await deleteCustomMetric(button.dataset.label); return; }
  if (action === "add-lab") { openLabDialog(); return; }
  if (action === "delete-lab") { await deleteLab(button.dataset.id); return; }
  if (action === "add-medication") { openMedicationDialog(); return; }
  if (action === "delete-medication") { await deleteMedication(button.dataset.name); return; }
}

async function handleForm(form) {
  const kind = form.dataset.healthForm;
  if (kind === "doctor-chat") {
    const message = String(form.querySelector("[name=message]")?.value || "").trim();
    if (!message) return;
    form.reset();
    await sendDoctorMessage(message);
    return;
  }
  if (kind === "sleep") { await saveSleep(form); return; }
  if (kind === "body") { await saveBody(form); return; }
  if (kind === "diet") { await submitDiet(form); return; }
  if (kind === "exercise") { await submitExercise(form); return; }
  if (kind === "training") { await saveTraining(form); return; }
  if (kind === "profile") { await saveProfile(form); return; }
  if (kind === "goals") { await saveGoals(form); return; }
  if (kind === "custom-metric") { await saveCustomMetric(form); return; }
  if (kind === "lab") { await saveLab(form); return; }
  if (kind === "medication") { await saveMedication(form); return; }
  throw new Error("未知表单");
}

// ── 记录弹窗：睡眠 / 身体 ────────────────────────────────────────────

function openRecordDialog(section) {
  if (section === "sleep") openSleepDialog();
  else if (section === "body") openBodyDialog();
  else if (section === "diet") openDietDialog();
  else if (section === "exercise") openExerciseDialog();
}

function openSleepDialog() {
  const values = sectionValues("sleep");
  const parts = durationParts(values.duration_min);
  const quality = Number(values.quality) || 0;
  const body = `
    <div class="health-modal-primary">
      <label class="health-big-field"><span>睡眠时长 <em>必填</em></span>
        <div class="health-duration-input">
          <input id="sleep-hours" name="hours" type="number" min="0" max="24" step="1" value="${parts.hours || ""}" placeholder="7" />
          <span>小时</span>
          <input id="sleep-minutes" name="minutes" type="number" min="0" max="59" step="5" value="${parts.minutes || ""}" placeholder="30" />
          <span>分</span>
        </div>
      </label>
      <div class="health-quick-chips">${[360, 390, 420, 450, 480, 510, 540].map((minutes) =>
        `<button type="button" class="health-chip" data-dialog-action="sleep-preset" data-minutes="${minutes}">${minutes / 60} 小时</button>`).join("")}</div>
    </div>
    <div class="health-modal-grid two">
      <label class="health-field"><span>入睡时间（可选）</span><input id="sleep-bed" name="bed" type="time" value="${esc(values.bed || "")}" /></label>
      <label class="health-field"><span>醒来时间（可选）</span><input id="sleep-wake" name="wake" type="time" value="${esc(values.wake || "")}" /></label>
    </div>
    <p class="health-auto-note" id="sleep-auto-note">填了入睡和醒来时间，会自动算时长；也可以只填时长。</p>
    <fieldset class="health-rating-field"><legend>睡得好吗？（可选）</legend>
      <input type="hidden" name="quality" id="sleep-quality" value="${quality || ""}" />
      <div class="health-rating-row">${[1, 2, 3, 4, 5].map((value) => `<button type="button" class="health-rating${quality === value ? " is-active" : ""}" data-dialog-action="sleep-quality" data-value="${value}"><strong>${value}</strong><small>${QUALITY_LABELS[value - 1]}</small></button>`).join("")}</div>
    </fieldset>
    <details class="health-advanced"><summary>手环数据（可选）</summary>
      <div class="health-modal-grid">
        <label class="health-field"><span>深睡（分钟）</span><input type="number" name="deep_min" min="0" max="720" value="${values.deep_min ?? ""}" /></label>
        <label class="health-field"><span>REM（分钟）</span><input type="number" name="rem_min" min="0" max="600" value="${values.rem_min ?? ""}" /></label>
        <label class="health-field"><span>夜间清醒（分钟）</span><input type="number" name="awake_min" min="0" max="600" value="${values.awake_min ?? ""}" /></label>
        <label class="health-field"><span>入睡耗时（分钟）</span><input type="number" name="latency_min" min="0" max="240" value="${values.latency_min ?? ""}" /></label>
        <label class="health-field"><span>夜醒次数</span><input type="number" name="wake_count" min="0" max="30" value="${values.wake_count ?? ""}" /></label>
        <label class="health-field"><span>睡前屏幕（分钟）</span><input type="number" name="screen_min" min="0" max="600" value="${values.screen_min ?? ""}" /></label>
      </div>
    </details>
    <p class="health-modal-date">记录到：${esc(monthDayLabel(HEALTH.day))} ${esc(weekdayFull(HEALTH.day))}${isToday() ? "（今天）" : ""}</p>`;
  openHealthDialog(modalForm("sleep", {
    icon: "眠", title: "昨晚睡了多久？", lead: "只填时长也可以。", body, submitLabel: "保存睡眠",
  }));
  const form = document.querySelector('#dialog-content form[data-health-form="sleep"]');
  if (!form) return;
  bindDialogAction((action, button) => {
    if (action === "sleep-preset") {
      const minutes = Number(button.dataset.minutes);
      form.querySelector("[name=hours]").value = Math.floor(minutes / 60);
      form.querySelector("[name=minutes]").value = minutes % 60;
      const note = form.querySelector("#sleep-auto-note");
      if (note) note.textContent = `已填 ${minutes / 60} 小时。`;
      return;
    }
    if (action === "sleep-quality") {
      const value = Number(button.dataset.value);
      form.querySelector("#sleep-quality").value = value;
      form.querySelectorAll(".health-rating").forEach((item) => item.classList.toggle("is-active", Number(item.dataset.value) === value));
    }
  });
  form.addEventListener("input", (event) => {
    if (!["bed", "wake"].includes(event.target.name)) return;
    const bed = form.querySelector("#sleep-bed").value;
    const wake = form.querySelector("#sleep-wake").value;
    const minutes = durationBetween(bed, wake);
    if (minutes) {
      form.querySelector("[name=hours]").value = Math.floor(minutes / 60);
      form.querySelector("[name=minutes]").value = minutes % 60;
      const note = form.querySelector("#sleep-auto-note");
      if (note) note.textContent = `按 ${bed} → ${wake} 自动算出 ${formatDuration(minutes)}。`;
    }
  });
}

async function saveSleep(form) {
  const hours = Number(form.querySelector("[name=hours]").value) || 0;
  const minutes = Number(form.querySelector("[name=minutes]").value) || 0;
  const duration = hours * 60 + minutes;
  if (!duration) { setStatus(form, "先填睡了多久，只填小时也可以。", "error"); return; }
  const values = { duration_min: duration };
  for (const name of ["bed", "wake", "quality", "deep_min", "rem_min", "awake_min", "latency_min", "wake_count", "screen_min"]) {
    const input = form.querySelector(`[name="${name}"]`);
    const raw = String(input?.value ?? "").trim();
    values[name] = raw === "" ? null : (input.type === "number" ? Number(raw) : raw);
  }
  setBusy(form, true);
  try {
    await patchSection("sleep", values);
    toast("睡眠已保存", "success");
    closeHealthDialog();
    await refresh({ quiet: true });
  } catch (error) {
    setStatus(form, error.message, "error");
    setBusy(form, false);
  }
}

function openBodyDialog() {
  const values = sectionValues("body");
  const previous = effective("body")?.previous?.values?.weight_kg;
  const body = `
    <div class="health-modal-primary">
      <label class="health-big-field"><span>今天体重 <em>必填</em></span>
        <div class="health-weight-input"><input name="weight_kg" data-body-weight type="number" min="20" max="400" step="0.1" value="${values.weight_kg ?? ""}" placeholder="例如 73.4" /><span>kg</span></div>
      </label>
      <p class="health-auto-note" id="body-bmi-note">${previous ? `上一次 ${formatNumber(previous, 1, "kg")}；填完会自动算变化。` : "填完会自动算 BMI 和变化。"}</p>
    </div>
    <details class="health-advanced" open><summary>更多身体数据（可选）</summary>
      <div class="health-modal-grid">
        <label class="health-field"><span>体脂率（%）</span><input type="number" name="body_fat_pct" min="3" max="70" step="0.1" value="${values.body_fat_pct ?? ""}" /></label>
        <label class="health-field"><span>肌肉量（kg）</span><input type="number" name="muscle_kg" min="3" max="150" step="0.1" value="${values.muscle_kg ?? ""}" /></label>
        <label class="health-field"><span>体水分（%）</span><input type="number" name="water_pct" min="20" max="75" step="0.1" value="${values.water_pct ?? ""}" /></label>
        <label class="health-field"><span>内脏脂肪（级）</span><input type="number" name="visceral_fat" min="1" max="30" step="1" value="${values.visceral_fat ?? ""}" /></label>
        <label class="health-field"><span>腰围（cm）</span><input type="number" name="waist_cm" min="30" max="200" step="0.1" value="${values.waist_cm ?? ""}" /></label>
        <label class="health-field"><span>胸围（cm）</span><input type="number" name="chest_cm" min="30" max="200" step="0.1" value="${values.chest_cm ?? ""}" /></label>
        <label class="health-field"><span>臀围（cm）</span><input type="number" name="hip_cm" min="30" max="200" step="0.1" value="${values.hip_cm ?? ""}" /></label>
        <label class="health-field"><span>臂围（cm）</span><input type="number" name="arm_cm" min="15" max="100" step="0.1" value="${values.arm_cm ?? ""}" /></label>
        <label class="health-field"><span>腿围（cm）</span><input type="number" name="thigh_cm" min="20" max="120" step="0.1" value="${values.thigh_cm ?? ""}" /></label>
        <label class="health-field"><span>小腿围（cm）</span><input type="number" name="calf_cm" min="15" max="80" step="0.1" value="${values.calf_cm ?? ""}" /></label>
        <label class="health-field"><span>基础代谢（kcal）</span><input type="number" name="bmr" min="500" max="5000" step="1" value="${values.bmr ?? ""}" /></label>
      </div>
    </details>
    <p class="health-modal-date">记录到：${esc(monthDayLabel(HEALTH.day))} ${esc(weekdayFull(HEALTH.day))}${isToday() ? "（今天）" : ""}</p>`;
  openHealthDialog(modalForm("body", {
    icon: "身", title: "今天体重多少？", lead: "只填体重也可以，其他数据可选。", body, submitLabel: "保存身体数据",
  }));
  const form = document.querySelector('#dialog-content form[data-health-form="body"]');
  if (!form) return;
  bindDialogAction(() => {});
  form.addEventListener("input", updateBodyBmiPreview);
}

function updateBodyBmiPreview() {
  const form = document.querySelector('#dialog-content form[data-health-form="body"]');
  const note = node("body-bmi-note");
  if (!form || !note) return;
  const weight = Number(form.querySelector("[name=weight_kg]")?.value);
  const height = Number(profile().height_cm);
  if (!weight || !height) return;
  const bmi = weight / ((height / 100) ** 2);
  const band = bmi < 18.5 ? "偏瘦" : bmi < 24 ? "正常" : bmi < 28 ? "超重" : "肥胖";
  const previous = Number(effective("body")?.previous?.values?.weight_kg);
  const delta = previous ? weight - previous : null;
  note.textContent = `BMI ${bmi.toFixed(1)}（${band}）${delta !== null ? ` · 比上次 ${formatSigned(delta, 1, "kg")}` : ""}`;
}

async function saveBody(form) {
  const values = {};
  for (const input of form.querySelectorAll("[name]")) {
    const raw = String(input.value ?? "").trim();
    if (raw === "") { values[input.name] = null; continue; }
    values[input.name] = input.type === "number" ? Number(raw) : raw;
  }
  if (!values.weight_kg) { setStatus(form, "先填体重，其他都可以不填。", "error"); return; }
  setBusy(form, true);
  try {
    await patchSection("body", values);
    toast("身体数据已保存", "success");
    closeHealthDialog();
    await refresh({ quiet: true });
  } catch (error) {
    setStatus(form, error.message, "error");
    setBusy(form, false);
  }
}

async function patchSection(section, values) {
  const revision = actual().revision || "";
  await healthApi(`/api/health/day/${encodeURIComponent(HEALTH.day)}`, {
    method: "PATCH",
    body: JSON.stringify({ revision, changes: { [section]: values } }),
  });
}

// ── 记录弹窗：饮食 ───────────────────────────────────────────────────

async function loadFoods() {
  if (HEALTH.foods) return HEALTH.foods;
  const data = await healthApi("/api/health/foods?limit=200");
  HEALTH.foods = Array.isArray(data.items) ? data.items : [];
  return HEALTH.foods;
}

function openDietDialog() {
  const nutrition = sectionValues("nutrition");
  HEALTH.dietDraft = {
    food_text: String(nutrition.food_text || ""),
    water_ml: nutrition.water_ml ?? 0,
    meals: (actual().meals || []).map((meal) => ({ ...meal })),
    view: "input",
    error: "",
    note: "",
    estimate: null,
    pickerOpen: false,
    pickerQuery: "",
  };
  renderDietDialog();
  loadFoods().then(() => {
    if (HEALTH.dietDraft?.pickerOpen) renderDietDialog();
  }).catch(() => {});
}

function syncDietDraftFromDom() {
  const draft = HEALTH.dietDraft;
  if (!draft) return;
  const form = document.querySelector('#dialog-content form[data-health-form="diet"]');
  if (!form) return;
  const text = form.querySelector("[name=food_text]");
  if (text) draft.food_text = text.value;
  const water = form.querySelector("[name=water_ml]");
  if (water) draft.water_ml = water.value;
  if (draft.view === "input") draft.meals = readMealRows(form);
}

function renderDietDialog() {
  const draft = HEALTH.dietDraft;
  if (!draft) return;
  if (draft.view === "preview") { renderDietPreview(); return; }
  const last = effective("nutrition");
  const lastHint = last && !sectionValues("nutrition").food_text && !(actual().meals || []).length
    ? `<p class="health-auto-note">上次记录：${esc(monthDayLabel(last.date))} · ${formatNumber(last.totals?.calories, 0, "kcal")}。今天可以重新记，也可以沿用上一次。</p>`
    : "";
  const meals = draft.meals.length
    ? draft.meals.map((meal, index) => mealRowMarkup(meal, index)).join("")
    : `<p class="health-muted">还没有餐次；写一句描述，AI 会帮你拆成食物和热量。</p>`;
  const body = `
    <div class="health-modal-primary">
      <label class="health-big-field"><span>今天吃了什么？ <em>必填</em></span>
        <textarea name="food_text" rows="4" class="health-big-textarea" placeholder="例如：早上两个鸡蛋一杯牛奶，中午一碗米饭配青菜和鸡胸肉，晚上吃了半碗面……">${esc(draft.food_text)}</textarea>
      </label>
      ${lastHint}
      <div class="health-dialog-tools">
        <button type="button" class="health-btn ghost small" data-dialog-action="diet-picker">从常吃食物选</button>
        <button type="button" class="health-btn ghost small" data-dialog-action="diet-add-meal">加一条精确记录</button>
        <span class="health-tool-hint">保存时会先让 AI 估算，结果可以改。</span>
      </div>
      ${draft.error ? `<p class="health-inline-error">${esc(draft.error)}</p>` : ""}
      <div class="${draft.pickerOpen ? "" : "health-hidden"}" data-diet-picker>
        <div class="health-food-search"><input type="search" data-food-search placeholder="搜米饭、鸡蛋、牛奶、面条……" value="${esc(draft.pickerQuery || "")}" /></div>
        <div class="health-food-list" data-food-list></div>
      </div>
    </div>
    <div class="health-meal-editor" data-meal-editor>${meals}</div>
    <div class="health-water-row">
      <span>饮水</span>
      <input type="number" name="water_ml" min="0" max="8000" step="50" value="${draft.water_ml || ""}" placeholder="0" />
      <span>ml</span>
      <div class="health-water-actions">
        <button type="button" class="health-chip" data-dialog-action="water-add" data-delta="250">＋250</button>
        <button type="button" class="health-chip" data-dialog-action="water-add" data-delta="500">＋500</button>
        <button type="button" class="health-chip" data-dialog-action="water-add" data-delta="-250">－250</button>
      </div>
    </div>
    <p class="health-modal-date">记录到：${esc(monthDayLabel(HEALTH.day))} ${esc(weekdayFull(HEALTH.day))}${isToday() ? "（今天）" : ""}</p>`;
  openHealthDialog(modalForm("diet", {
    icon: "食", title: "今天吃了什么？", lead: "写大概就行，AI 会按常见份量估算。", body, submitLabel: "保存并估算",
  }));
  const form = document.querySelector('#dialog-content form[data-health-form="diet"]');
  if (!form) return;
  bindDialogAction(async (action, button) => {
    if (action === "diet-picker") {
      syncDietDraftFromDom();
      HEALTH.dietDraft.pickerOpen = !HEALTH.dietDraft.pickerOpen;
      renderDietDialog();
      if (HEALTH.dietDraft.pickerOpen) {
        const input = document.querySelector("[data-food-search]");
        if (input) input.focus();
      }
      return;
    }
    if (action === "diet-add-meal") {
      syncDietDraftFromDom();
      HEALTH.dietDraft.meals.push({ meal_type: guessMealType(), name: "", portion: "", calories: null, protein_g: null, carbs_g: null, fat_g: null, source: "manual" });
      renderDietDialog();
      return;
    }
    if (action === "remove-meal") {
      syncDietDraftFromDom();
      HEALTH.dietDraft.meals.splice(Number(button.dataset.index), 1);
      renderDietDialog();
      return;
    }
    if (action === "pick-food") {
      const food = (HEALTH.foods || []).find((item) => item.id === button.dataset.foodId);
      if (!food) return;
      syncDietDraftFromDom();
      HEALTH.dietDraft.meals.push({
        meal_type: guessMealType(), name: food.name, portion: food.portion,
        calories: food.calories, protein_g: food.protein_g, carbs_g: food.carbs_g,
        fat_g: food.fat_g, source: "preset",
      });
      HEALTH.dietDraft.pickerOpen = false;
      HEALTH.dietDraft.pickerQuery = "";
      renderDietDialog();
      toast(`已加入：${food.name}`, "success");
      return;
    }
    if (action === "water-add") {
      syncDietDraftFromDom();
      const current = Number(form.querySelector("[name=water_ml]").value) || 0;
      const value = Math.max(0, current + Number(button.dataset.delta || 0));
      form.querySelector("[name=water_ml]").value = value;
      HEALTH.dietDraft.water_ml = value;
      return;
    }
    if (action === "diet-back") {
      syncDietEstimateFromDom();
      HEALTH.dietDraft.view = "input";
      renderDietDialog();
      return;
    }
    if (action === "diet-skip-estimate") {
      syncDietDraftFromDom();
      HEALTH.dietDraft.estimate = null;
      HEALTH.dietDraft.view = "input";
      await saveDiet();
      return;
    }
  });
  if (draft.pickerOpen) paintFoodList(draft.pickerQuery || "");
  form.addEventListener("input", (event) => {
    if (event.target.matches("[data-food-search]")) paintFoodList(event.target.value);
  });
}

function paintFoodList(query = "") {
  const list = document.querySelector("[data-food-list]");
  if (!list) return;
  const text = String(query || "").trim().toLowerCase();
  const items = (HEALTH.foods || []).filter((food) => !text
    || String(food.name).toLowerCase().includes(text)
    || String(food.portion || "").toLowerCase().includes(text)).slice(0, 32);
  if (!items.length) {
    list.innerHTML = `<p class="health-muted">没有匹配的食物；可以直接在描述里写。</p>`;
    return;
  }
  list.innerHTML = items.map((food) => `<button type="button" class="health-food-item" data-dialog-action="pick-food" data-food-id="${esc(food.id)}">
    <strong>${esc(food.name)}</strong><small>${esc(food.portion)} · ${formatNumber(food.calories, 0, "kcal")}</small>
  </button>`).join("");
  const search = document.querySelector("[data-food-search]");
  if (search) {
    HEALTH.dietDraft.pickerQuery = search.value;
  }
}

async function submitDiet(form) {
  syncDietDraftFromDom();
  const draft = HEALTH.dietDraft;
  if (!draft) return;
  if (draft.view === "preview") {
    await saveDiet(form);
    return;
  }
  if (!draft.food_text.trim() && !draft.meals.length) {
    setStatus(form, "写一句吃了什么，或者加一条精确记录。", "error");
    return;
  }
  if (!draft.food_text.trim() || draft.meals.length) {
    await saveDiet(form);
    return;
  }
  setBusy(form, true, "AI 估算中…");
  setStatus(form, "正在按常见份量估算，最多等一会儿…");
  try {
    const result = await healthApi("/api/health/estimate", {
      method: "POST", timeoutMs: 120000,
      body: JSON.stringify({ kind: "food", text: draft.food_text }),
    });
    draft.estimate = (result.items || []).map((item) => ({ ...item }));
    draft.note = result.note || "";
    draft.view = "preview";
    setBusy(form, false);
    renderDietDialog();
  } catch (error) {
    setBusy(form, false);
    draft.error = `AI 暂时估不出来：${error.message}`;
    setStatus(form, "可以先保存文字，之后再补热量。", "error");
    renderDietDialog();
  }
}

function renderDietPreview() {
  const draft = HEALTH.dietDraft;
  const items = draft.estimate || [];
  const totals = estimateFoodTotals(items);
  const body = `
    <p class="health-auto-note">${esc(draft.note || "按常见份量估算。数字都可以改；确认后才会写进今天的记录。")}</p>
    <div class="health-estimate-list">${items.map((item, index) => mealRowMarkup(item, index)).join("")}</div>
    <div class="health-estimate-total"><span>合计</span><strong>${formatNumber(totals.calories, 0, "kcal")}</strong><small>蛋白 ${formatNumber(totals.protein_g, 0, "g")} · 碳水 ${formatNumber(totals.carbs_g, 0, "g")} · 脂肪 ${formatNumber(totals.fat_g, 0, "g")}</small></div>
    <div class="health-dialog-tools"><button type="button" class="health-btn ghost small" data-dialog-action="diet-add-meal">加一条</button><span class="health-tool-hint">不对就返回修改，或者直接改上面的数字。</span></div>`;
  openHealthDialog(modalForm("diet", {
    icon: "食", title: "AI 估算结果", lead: "确认前可以改数字。", body, submitLabel: "确认保存",
  }));
  const form = document.querySelector('#dialog-content form[data-health-form="diet"]');
  if (!form) return;
  bindDialogAction((action, button) => {
    if (action === "diet-back") {
      syncDietEstimateFromDom();
      HEALTH.dietDraft.view = "input";
      renderDietDialog();
      return;
    }
    if (action === "remove-meal") {
      syncDietEstimateFromDom();
      HEALTH.dietDraft.estimate.splice(Number(button.dataset.index), 1);
      renderDietDialog();
      return;
    }
    if (action === "diet-add-meal") {
      syncDietEstimateFromDom();
      HEALTH.dietDraft.estimate.push({ meal_type: guessMealType(), name: "", portion: "", calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0, source: "manual" });
      renderDietDialog();
    }
  });
  form.addEventListener("input", () => {
    const current = document.querySelector("#dialog-content form[data-health-form='diet']");
    if (!current) return;
    const rows = readMealRows(current);
    const total = estimateFoodTotals(rows);
    const host = current.querySelector(".health-estimate-total");
    if (host) host.innerHTML = `<span>合计</span><strong>${formatNumber(total.calories, 0, "kcal")}</strong><small>蛋白 ${formatNumber(total.protein_g, 0, "g")} · 碳水 ${formatNumber(total.carbs_g, 0, "g")} · 脂肪 ${formatNumber(total.fat_g, 0, "g")}</small>`;
  });
}

function syncDietEstimateFromDom() {
  const form = document.querySelector('#dialog-content form[data-health-form="diet"]');
  if (!form || !HEALTH.dietDraft?.estimate) return;
  HEALTH.dietDraft.estimate = readMealRows(form);
}

function estimateFoodTotals(items) {
  return (items || []).reduce((total, item) => ({
    calories: total.calories + (Number(item.calories) || 0),
    protein_g: total.protein_g + (Number(item.protein_g) || 0),
    carbs_g: total.carbs_g + (Number(item.carbs_g) || 0),
    fat_g: total.fat_g + (Number(item.fat_g) || 0),
  }), { calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0 });
}

async function saveDiet(form) {
  const draft = HEALTH.dietDraft;
  if (!draft) return;
  if (draft.view === "preview") syncDietEstimateFromDom();
  const meals = (draft.view === "preview" ? draft.estimate : draft.meals) || [];
  if (!draft.food_text.trim() && !meals.length) {
    if (form) setStatus(form, "写一句吃了什么，或者加一条精确记录。", "error");
    return;
  }
  if (form) setBusy(form, true);
  try {
    await healthApi(`/api/health/day/${encodeURIComponent(HEALTH.day)}/diet`, {
      method: "PUT",
      body: JSON.stringify({
        revision: actual().revision || "",
        food_text: draft.food_text,
        water_ml: readNumber(draft.water_ml) || 0,
        meals: meals.filter((meal) => String(meal.name || "").trim()).map((meal) => ({
          meal_type: meal.meal_type || "加餐",
          name: meal.name,
          portion: meal.portion || "",
          calories: meal.calories ?? null,
          protein_g: meal.protein_g ?? null,
          carbs_g: meal.carbs_g ?? null,
          fat_g: meal.fat_g ?? null,
          source: meal.source || "manual",
        })),
      }),
    });
    toast("饮食已保存", "success");
    closeHealthDialog();
    await refresh({ quiet: true });
  } catch (error) {
    if (form) { setStatus(form, error.message, "error"); setBusy(form, false); }
    else toast(error.message, "error");
  }
}

// ── 记录弹窗：运动 ───────────────────────────────────────────────────

function openExerciseDialog() {
  const activity = sectionValues("activity");
  HEALTH.exerciseDraft = {
    exercise_text: String(activity.exercise_text || ""),
    minutes: "",
    calories: "",
    steps: activity.steps ?? "",
    sessions: (actual().training || []).map((session) => ({ ...session })),
    view: "input",
    estimate: null,
    note: "",
    error: "",
  };
  renderExerciseDialog();
}

function renderExerciseDialog() {
  const draft = HEALTH.exerciseDraft;
  if (!draft) return;
  if (draft.view === "preview") { renderExercisePreview(); return; }
  const last = effective("activity");
  const lastHint = last && !sectionValues("activity").exercise_text && !(actual().training || []).length
    ? `<p class="health-auto-note">上次记录：${esc(monthDayLabel(last.date))} · ${formatNumber(last.totals?.minutes, 0, "分钟")}。今天可以重新记。</p>`
    : "";
  const body = `
    <div class="health-modal-primary">
      <label class="health-big-field"><span>今天做了什么运动？ <em>必填</em></span>
        <textarea name="exercise_text" rows="3" class="health-big-textarea" placeholder="例如：跑步 1 小时，跑步机显示 500 大卡；或者散步 40 分钟……">${esc(draft.exercise_text)}</textarea>
      </label>
      ${lastHint}
      <div class="health-modal-grid three">
        <label class="health-field"><span>时长（可选）</span><input type="number" name="minutes" min="0" max="600" step="1" value="${draft.minutes}" placeholder="分钟" /></label>
        <label class="health-field"><span>消耗（可选）</span><input type="number" name="calories" min="0" max="5000" step="1" value="${draft.calories}" placeholder="kcal" /></label>
        <label class="health-field"><span>步数（可选）</span><input type="number" name="steps" min="0" max="100000" step="1" value="${draft.steps}" placeholder="步" /></label>
      </div>
      <div class="health-dialog-tools">
        <button type="button" class="health-btn ghost small" data-dialog-action="exercise-precise">精细记录训练</button>
        <span class="health-tool-hint">只写一句也行；保存时会先让 AI 估算，结果可以改。</span>
      </div>
      ${draft.error ? `<p class="health-inline-error">${esc(draft.error)}</p>` : ""}
    </div>
    <div class="health-session-editor">${draft.sessions.length ? draft.sessions.map((session, index) => sessionRowMarkup(session, index)).join("") : `<p class="health-muted">还没有运动明细。</p>`}</div>
    <p class="health-modal-date">记录到：${esc(monthDayLabel(HEALTH.day))} ${esc(weekdayFull(HEALTH.day))}${isToday() ? "（今天）" : ""}</p>`;
  openHealthDialog(modalForm("exercise", {
    icon: "动", title: "今天动了什么？", lead: "散步、跑步、力量都算；写一句就行。", body, submitLabel: "保存并估算",
  }));
  const form = document.querySelector('#dialog-content form[data-health-form="exercise"]');
  if (!form) return;
  bindDialogAction((action, button) => {
    if (action === "exercise-precise") {
      closeHealthDialog();
      openTrainingDialog();
      return;
    }
    if (action === "remove-session") {
      syncExerciseDraftFromDom();
      HEALTH.exerciseDraft.sessions.splice(Number(button.dataset.index), 1);
      renderExerciseDialog();
      return;
    }
    if (action === "exercise-back") {
      syncExerciseEstimateFromDom();
      HEALTH.exerciseDraft.view = "input";
      renderExerciseDialog();
      return;
    }
  });
}

function syncExerciseDraftFromDom() {
  const draft = HEALTH.exerciseDraft;
  const form = document.querySelector('#dialog-content form[data-health-form="exercise"]');
  if (!draft || !form) return;
  const text = form.querySelector("[name=exercise_text]");
  if (text) draft.exercise_text = text.value;
  const minutes = form.querySelector("[name=minutes]");
  if (minutes) draft.minutes = minutes.value;
  const calories = form.querySelector("[name=calories]");
  if (calories) draft.calories = calories.value;
  const steps = form.querySelector("[name=steps]");
  if (steps) draft.steps = steps.value;
  if (form.querySelector("[data-session-row]")) draft.sessions = readSessionRows(form);
}

function syncExerciseEstimateFromDom() {
  const form = document.querySelector('#dialog-content form[data-health-form="exercise"]');
  if (!form || !HEALTH.exerciseDraft?.estimate) return;
  HEALTH.exerciseDraft.estimate = readSessionRows(form);
}

async function submitExercise(form) {
  syncExerciseDraftFromDom();
  const draft = HEALTH.exerciseDraft;
  if (!draft) return;
  if (draft.view === "preview") {
    syncExerciseEstimateFromDom();
    await saveExercise(form);
    return;
  }
  if (!draft.exercise_text.trim() && !draft.sessions.length && !readNumber(draft.minutes) && !readNumber(draft.calories) && !readNumber(draft.steps)) {
    setStatus(form, "写一句运动，或者填时长/消耗。", "error");
    return;
  }
  const hasNumbers = Boolean(readNumber(draft.minutes) || readNumber(draft.calories));
  if (hasNumbers || draft.sessions.length) {
    await saveExercise(form);
    return;
  }
  setBusy(form, true, "AI 估算中…");
  setStatus(form, "正在按常见运动强度估算…");
  try {
    const result = await healthApi("/api/health/estimate", {
      method: "POST", timeoutMs: 120000,
      body: JSON.stringify({ kind: "exercise", text: draft.exercise_text }),
    });
    draft.estimate = (result.items || []).map((item) => ({ ...item }));
    draft.note = result.note || "";
    draft.view = "preview";
    setBusy(form, false);
    renderExerciseDialog();
  } catch (error) {
    setBusy(form, false);
    draft.error = `AI 暂时估不出来：${error.message}`;
    setStatus(form, "可以直接保存文字，或者填一个大概时长。", "error");
    renderExerciseDialog();
  }
}

function renderExercisePreview() {
  const draft = HEALTH.exerciseDraft;
  const items = draft.estimate || [];
  const totals = estimateExerciseTotals(items);
  const body = `
    <p class="health-auto-note">${esc(draft.note || "按常见运动强度估算。数字都可以改；确认后才会写进今天的记录。")}</p>
    <div class="health-estimate-list">${items.map((session, index) => sessionRowMarkup(session, index)).join("")}</div>
    <div class="health-estimate-total"><span>合计</span><strong>${formatNumber(totals.minutes, 0, "分钟")}</strong><small>${formatNumber(totals.calories, 0, "kcal")} · ${formatNumber(totals.distance_km, 1, "km")} · ${formatNumber(totals.steps, 0, "步")}</small></div>`;
  openHealthDialog(modalForm("exercise", {
    icon: "动", title: "AI 估算结果", lead: "确认前可以改数字。", body, submitLabel: "确认保存",
  }));
  const form = document.querySelector('#dialog-content form[data-health-form="exercise"]');
  if (!form) return;
  bindDialogAction((action, button) => {
    if (action === "exercise-back") {
      syncExerciseEstimateFromDom();
      HEALTH.exerciseDraft.view = "input";
      renderExerciseDialog();
      return;
    }
    if (action === "remove-session") {
      syncExerciseEstimateFromDom();
      HEALTH.exerciseDraft.estimate.splice(Number(button.dataset.index), 1);
      renderExerciseDialog();
    }
  });
  form.addEventListener("input", () => {
    const current = document.querySelector("#dialog-content form[data-health-form='exercise']");
    if (!current) return;
    const total = estimateExerciseTotals(readSessionRows(current));
    const host = current.querySelector(".health-estimate-total");
    if (host) host.innerHTML = `<span>合计</span><strong>${formatNumber(total.minutes, 0, "分钟")}</strong><small>${formatNumber(total.calories, 0, "kcal")} · ${formatNumber(total.distance_km, 1, "km")} · ${formatNumber(total.steps, 0, "步")}</small>`;
  });
}

function estimateExerciseTotals(items) {
  return (items || []).reduce((total, item) => ({
    minutes: total.minutes + (Number(item.minutes) || 0),
    calories: total.calories + (Number(item.calories) || 0),
    distance_km: total.distance_km + (Number(item.distance_km) || 0),
    steps: total.steps + (Number(item.steps) || 0),
  }), { minutes: 0, calories: 0, distance_km: 0, steps: 0 });
}

async function saveExercise(form) {
  const draft = HEALTH.exerciseDraft;
  if (!draft) return;
  const sessions = draft.view === "preview" ? draft.estimate : draft.sessions;
  const manualMinutes = readNumber(draft.minutes);
  const manualCalories = readNumber(draft.calories);
  const list = (sessions || []).filter((session) => String(session.kind || "").trim());
  if (manualMinutes || manualCalories) {
    list.push({
      kind: "其他", minutes: manualMinutes || null, calories: manualCalories || null,
      note: draft.exercise_text, source: "manual",
    });
  }
  if (form) setBusy(form, true);
  try {
    await healthApi(`/api/health/day/${encodeURIComponent(HEALTH.day)}/exercise`, {
      method: "PUT",
      body: JSON.stringify({
        revision: actual().revision || "",
        exercise_text: draft.exercise_text,
        steps: readNumber(draft.steps),
        sessions: list.map((session) => ({
          kind: session.kind || "其他",
          minutes: session.minutes ?? null,
          calories: session.calories ?? null,
          distance_km: session.distance_km ?? null,
          steps: session.steps ?? null,
          note: session.note || "",
          source: session.source || "manual",
          exercises: Array.isArray(session.exercises) ? session.exercises : [],
        })),
      }),
    });
    toast("运动已保存", "success");
    closeHealthDialog();
    await refresh({ quiet: true });
  } catch (error) {
    if (form) { setStatus(form, error.message, "error"); setBusy(form, false); }
    else toast(error.message, "error");
  }
}

// ── 精细训练弹窗 ─────────────────────────────────────────────────────

function openTrainingDialog() {
  HEALTH.trainingDraft = { exercises: [] };
  HEALTH.exerciseResults = [];
  const body = `
    <div class="health-modal-grid">
      <label class="health-field"><span>类型</span><select name="kind">${TRAINING_KINDS.map((kind) => `<option value="${kind}">${kind}</option>`).join("")}</select></label>
      <label class="health-field"><span>时长（分钟，可选）</span><input type="number" name="minutes" min="1" max="600" step="1" /></label>
      <label class="health-field"><span>主观疲劳 RPE（可选）</span><select name="rpe"><option value="">未记录</option>${Array.from({ length: 10 }, (_item, index) => `<option value="${index + 1}">${index + 1}</option>`).join("")}</select></label>
      <label class="health-field"><span>消耗 kcal（可选）</span><input type="number" name="calories" min="0" max="5000" step="1" /></label>
    </div>
    <div class="health-exercise-picker">
      <div class="health-exercise-search-row">
        <input type="search" data-exercise-search placeholder="搜动作：深蹲 / 卧推 / 划船" />
        <select data-exercise-body><option value="">全部部位</option></select>
      </div>
      <div id="health-exercise-results" class="health-exercise-results"></div>
      <div id="health-training-draft" class="health-training-draft"></div>
    </div>
    <p class="health-modal-date">记录到：${esc(monthDayLabel(HEALTH.day))}</p>`;
  openHealthDialog(modalForm("training", {
    icon: "力", title: "精细记录一次训练", lead: "力量训练可以记动作和组数；跑步等有氧只填时长也行。", body, submitLabel: "保存训练",
  }));
  const form = document.querySelector('#dialog-content form[data-health-form="training"]');
  if (!form) return;
  bindDialogAction((action, button) => {
    if (action === "add-exercise") {
      const item = HEALTH.exerciseResults[Number(button.dataset.index)];
      if (!item) return;
      if (HEALTH.trainingDraft.exercises.some((exercise) => exercise.exercise_id === item.id)) { toast("这个动作已经加过了"); return; }
      HEALTH.trainingDraft.exercises.push({ exercise_id: item.id, name: item.name, sets: [{ reps: null, weight_kg: null }] });
      renderTrainingDraft();
      return;
    }
    if (action === "remove-exercise") {
      syncTrainingDraft();
      HEALTH.trainingDraft.exercises.splice(Number(button.dataset.index), 1);
      renderTrainingDraft();
      return;
    }
    if (action === "add-set") {
      syncTrainingDraft();
      HEALTH.trainingDraft.exercises[Number(button.dataset.exercise)]?.sets.push({ reps: null, weight_kg: null });
      renderTrainingDraft();
      return;
    }
    if (action === "remove-set") {
      syncTrainingDraft();
      HEALTH.trainingDraft.exercises[Number(button.dataset.exercise)]?.sets.splice(Number(button.dataset.set), 1);
      renderTrainingDraft();
    }
  });
  form.addEventListener("input", (event) => {
    if (event.target.matches("[data-exercise-search]")) scheduleExerciseSearch(event.target.value);
    if (event.target.matches("[data-set-reps], [data-set-weight]")) syncTrainingDraft();
  });
  form.addEventListener("change", (event) => {
    if (event.target.matches("[data-exercise-body]")) runExerciseSearch("");
  });
  runExerciseSearch("");
}

function renderTrainingDraft() {
  const container = node("health-training-draft");
  if (!container) return;
  const draft = HEALTH.trainingDraft;
  if (!draft.exercises.length) {
    container.innerHTML = `<p class="health-muted">还没有动作；搜索后点一下加入。</p>`;
    return;
  }
  container.innerHTML = draft.exercises.map((exercise, exerciseIndex) => `<article class="health-draft-exercise">
    <header><strong>${esc(exercise.name)}</strong><button class="health-row-remove" type="button" data-dialog-action="remove-exercise" data-index="${exerciseIndex}" aria-label="移除 ${esc(exercise.name)}">×</button></header>
    <div class="health-set-list">${exercise.sets.map((set, setIndex) => `<div class="health-set">
      <label>次数<input type="number" min="0" max="1000" step="1" data-set-reps data-exercise="${exerciseIndex}" data-set-index="${setIndex}" value="${set.reps ?? ""}" /></label>
      <label>重量 kg<input type="number" min="0" max="1000" step="0.5" data-set-weight data-exercise="${exerciseIndex}" data-set-index="${setIndex}" value="${set.weight_kg ?? ""}" /></label>
      <button class="health-row-remove" type="button" data-dialog-action="remove-set" data-exercise="${exerciseIndex}" data-set="${setIndex}" aria-label="删除这一组">−</button>
    </div>`).join("")}</div>
    <button class="health-btn ghost small" type="button" data-dialog-action="add-set" data-exercise="${exerciseIndex}">＋ 加一组</button>
  </article>`).join("");
}

function syncTrainingDraft() {
  const draft = HEALTH.trainingDraft;
  document.querySelectorAll("[data-draft-exercise]").forEach(() => {});
  document.querySelectorAll(".health-draft-exercise").forEach((card, index) => {
    const exercise = draft.exercises[index];
    if (!exercise) return;
    card.querySelectorAll(".health-set").forEach((row, setIndex) => {
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

async function runExerciseSearch(query = "") {
  const container = node("health-exercise-results");
  const form = document.querySelector('#dialog-content form[data-health-form="training"]');
  if (!container || !form) return;
  const body = form.querySelector("[data-exercise-body]")?.value || "";
  container.innerHTML = `<p class="health-muted">正在搜索…</p>`;
  try {
    const data = await healthApi(`/api/health/exercises?q=${encodeURIComponent(query || "")}&body=${encodeURIComponent(body)}&limit=24`);
    HEALTH.exerciseResults = data.items || [];
    HEALTH.exerciseFacets = data.facets || HEALTH.exerciseFacets;
    const bodySelect = form.querySelector("[data-exercise-body]");
    if (bodySelect && bodySelect.options.length <= 1 && data.facets?.bodies) {
      bodySelect.innerHTML = `<option value="">全部部位</option>${data.facets.bodies.map((item) => `<option value="${esc(item.key)}">${esc(item.label)}（${item.count}）</option>`).join("")}`;
    }
    container.innerHTML = HEALTH.exerciseResults.length
      ? HEALTH.exerciseResults.map((item, index) => `<button type="button" class="health-exercise-item" data-dialog-action="add-exercise" data-index="${index}">
          <strong>${esc(item.name)}</strong><small>${esc([item.body_label, item.equipment_label].filter(Boolean).join(" · "))}</small></button>`).join("")
      : `<p class="health-muted">没有匹配的动作；换个关键词。</p>`;
  } catch (error) {
    container.innerHTML = `<p class="health-inline-error">动作库读取失败：${esc(error.message)}</p>`;
  }
}

async function saveTraining(form) {
  syncTrainingDraft();
  const values = readFields(form);
  const exercises = HEALTH.trainingDraft.exercises.map((exercise) => ({
    exercise_id: exercise.exercise_id, name: exercise.name,
    sets: (exercise.sets || []).filter((set) => set.reps !== null || set.weight_kg !== null),
  })).filter((exercise) => exercise.sets.length);
  if (!exercises.length && !values.minutes && !values.calories) {
    setStatus(form, "至少加一个动作，或者填训练时长。", "error");
    return;
  }
  setBusy(form, true);
  try {
    await healthApi(`/api/health/day/${encodeURIComponent(HEALTH.day)}/training`, jsonBody("POST", {
      kind: values.kind || "其他", minutes: values.minutes, calories: values.calories,
      rpe: values.rpe, note: "", source: "manual", exercises,
    }));
    toast("训练已保存", "success");
    closeHealthDialog();
    await refresh({ quiet: true });
  } catch (error) {
    setStatus(form, error.message, "error");
    setBusy(form, false);
  }
}

// ── 健康档案弹窗 ─────────────────────────────────────────────────────

function openProfileDialog() {
  const profile = HEALTH.archive?.profile || HEALTH.overview?.profile || {};
  const body = `
    <div class="health-modal-grid">
      <label class="health-field"><span>称呼</span><input type="text" name="name" maxlength="40" value="${esc(profile.name || "")}" /></label>
      <label class="health-field"><span>性别</span><select name="gender">${["", "男", "女", "其他"].map((value) => `<option value="${value}"${profile.gender === value ? " selected" : ""}>${value || "未填写"}</option>`).join("")}</select></label>
      <label class="health-field"><span>出生日期</span><input type="date" name="birth_date" value="${esc(profile.birth_date || "")}" /></label>
      <label class="health-field"><span>身高（cm）</span><input type="number" name="height_cm" min="50" max="260" step="0.1" value="${profile.height_cm ?? ""}" /></label>
      <label class="health-field"><span>血型</span><input type="text" name="blood_type" maxlength="10" value="${esc(profile.blood_type || "")}" /></label>
    </div>
    <label class="health-field"><span>既往病史</span><textarea name="medical_history" rows="2" maxlength="4000">${esc(profile.medical_history || "")}</textarea></label>
    <label class="health-field"><span>过敏</span><textarea name="allergies" rows="2" maxlength="2000">${esc(profile.allergies || "")}</textarea></label>`;
  openHealthDialog(modalForm("profile", {
    icon: "档", title: "编辑基本档案", lead: "敏感数据只在本机保存，不进入 AI 记忆。", body, submitLabel: "保存档案",
  }));
  const form = document.querySelector('#dialog-content form[data-health-form="profile"]');
  if (!form) return;
  bindDialogAction(() => {});
}

async function saveProfile(form) {
  const values = readFields(form);
  values.revision = (HEALTH.archive?.profile || HEALTH.overview?.profile || {}).revision || "";
  setBusy(form, true);
  try {
    await healthApi("/api/health/profile", { method: "PUT", body: JSON.stringify(values) });
    toast("档案已保存", "success");
    closeHealthDialog();
    await refresh({ quiet: true });
    if (HEALTH.page === "archive") { HEALTH.archive = await healthApi(`/api/health/archive?day=${encodeURIComponent(HEALTH.day)}`); renderPage(); }
  } catch (error) {
    setStatus(form, error.message, "error");
    setBusy(form, false);
  }
}

function openGoalsDialog() {
  const profile = HEALTH.archive?.profile || HEALTH.overview?.profile || {};
  const goals = profile.goals || {};
  const fields = [
    ["weight_kg", "目标体重（kg）", "number"], ["body_fat_pct", "目标体脂（%）", "number"],
    ["sleep_minutes", "目标睡眠（分钟）", "number"], ["steps", "目标步数", "number"],
    ["water_ml", "目标饮水（ml）", "number"], ["weekly_training", "每周运动（次）", "number"],
    ["calories", "目标热量（kcal）", "number"], ["protein_g", "目标蛋白（g）", "number"],
  ];
  const body = `<div class="health-modal-grid">${fields.map(([key, label, type]) =>
    `<label class="health-field"><span>${label}</span><input type="${type}" name="goal_${key}" step="1" min="0" value="${goals[key] ?? ""}" placeholder="自动" /></label>`).join("")}</div>
    <p class="health-auto-note">留空表示自动：热量和蛋白会按最新体重与目标估算。</p>`;
  openHealthDialog(modalForm("goals", {
    icon: "标", title: "编辑目标", lead: "目标只影响进度条和评分，不改变记录。", body, submitLabel: "保存目标",
  }));
  const form = document.querySelector('#dialog-content form[data-health-form="goals"]');
  if (!form) return;
  bindDialogAction(() => {});
}

async function saveGoals(form) {
  const goals = {};
  for (const input of form.querySelectorAll("[name^=goal_]")) {
    const key = input.name.slice(5);
    goals[key] = input.value === "" ? null : Number(input.value);
  }
  const profile = HEALTH.archive?.profile || HEALTH.overview?.profile || {};
  setBusy(form, true);
  try {
    await healthApi("/api/health/profile", { method: "PUT", body: JSON.stringify({ goals, revision: profile.revision || "" }) });
    toast("目标已保存", "success");
    closeHealthDialog();
    await refresh({ quiet: true });
  } catch (error) {
    setStatus(form, error.message, "error");
    setBusy(form, false);
  }
}

function openCustomMetricDialog() {
  const body = `<div class="health-modal-grid">
    <label class="health-field"><span>名称</span><input type="text" name="label" maxlength="20" placeholder="例如：尿酸" required /></label>
    <label class="health-field"><span>单位</span><input type="text" name="unit" maxlength="10" placeholder="例如：μmol/L" /></label>
  </div>`;
  openHealthDialog(modalForm("custom-metric", {
    icon: "＋", title: "添加自定义指标", lead: "加完后可以在每日记录里填数值。", body, submitLabel: "添加",
  }));
  const form = document.querySelector('#dialog-content form[data-health-form="custom-metric"]');
  if (!form) return;
  bindDialogAction(() => {});
}

async function saveCustomMetric(form) {
  const values = readFields(form);
  if (!values.label) { setStatus(form, "先写指标名称。", "error"); return; }
  const profile = HEALTH.archive?.profile || HEALTH.overview?.profile || {};
  const custom = [...(profile.custom_metrics || []), { label: values.label, unit: values.unit || "" }];
  setBusy(form, true);
  try {
    await healthApi("/api/health/profile", { method: "PUT", body: JSON.stringify({ custom_metrics: custom, revision: profile.revision || "" }) });
    toast("指标已添加", "success");
    closeHealthDialog();
    await refresh({ quiet: true });
  } catch (error) {
    setStatus(form, error.message, "error");
    setBusy(form, false);
  }
}

async function deleteCustomMetric(label) {
  const profile = HEALTH.archive?.profile || HEALTH.overview?.profile || {};
  const ok = await confirmAction({ title: `删除指标「${label}」？`, message: "只删除指标定义，已经写进每日记录的数值不会丢。", confirmLabel: "删除", danger: true });
  if (!ok) return;
  const custom = (profile.custom_metrics || []).filter((item) => item.label !== label);
  await healthApi("/api/health/profile", { method: "PUT", body: JSON.stringify({ custom_metrics: custom, revision: profile.revision || "" }) });
  toast("已删除", "success");
  await refresh({ quiet: true });
}

function openLabDialog() {
  const labs = HEALTH.archive?.labs || {};
  const tests = labs.tests || [];
  const body = `<div class="health-modal-grid">
    <label class="health-field"><span>日期</span><input type="date" name="date" value="${esc(HEALTH.day)}" required /></label>
    <label class="health-field"><span>项目</span><select name="test" data-lab-preset>${tests.map((item) => `<option value="${esc(item.name)}" data-category="${esc(item.category)}" data-unit="${esc(item.unit)}" data-min="${item.ref_min}" data-max="${item.ref_max}">${esc(item.name)}（${esc(item.category)}）</option>`).join("")}<option value="自定义">自定义项目</option></select></label>
    <label class="health-field"><span>自定义项目名</span><input type="text" name="custom_test" maxlength="60" placeholder="选“自定义”时填" /></label>
    <label class="health-field"><span>数值</span><input type="number" name="value" step="0.01" required /></label>
    <label class="health-field"><span>单位</span><input type="text" name="unit" maxlength="12" /></label>
    <label class="health-field"><span>参考下限</span><input type="number" name="ref_min" step="0.01" /></label>
    <label class="health-field"><span>参考上限</span><input type="number" name="ref_max" step="0.01" /></label>
    <label class="health-field"><span>备注</span><input type="text" name="note" maxlength="200" /></label>
  </div>`;
  openHealthDialog(modalForm("lab", {
    icon: "检", title: "添加一条检验", lead: "按报告上的数值填；异常只是提示，不是诊断。", body, submitLabel: "添加检验",
  }));
  const form = document.querySelector('#dialog-content form[data-health-form="lab"]');
  if (!form) return;
  bindDialogAction(() => {});
  const select = form.querySelector("[data-lab-preset]");
  select?.addEventListener("change", () => {
    const option = select.selectedOptions?.[0];
    if (!option || select.value === "自定义") { form.querySelector("[name=custom_test]")?.focus(); return; }
    const set = (name, value) => { const input = form.querySelector(`[name="${name}"]`); if (input && value !== undefined) input.value = value; };
    set("unit", option.dataset.unit || "");
    set("ref_min", option.dataset.min ?? "");
    set("ref_max", option.dataset.max ?? "");
  });
}

async function saveLab(form) {
  const values = readFields(form);
  if (values.test === "自定义") {
    if (!values.custom_test) { setStatus(form, "选了自定义项目，就要写项目名。", "error"); return; }
    values.test = values.custom_test;
  }
  delete values.custom_test;
  if (!values.test || values.value === null) { setStatus(form, "项目和数值都要填。", "error"); return; }
  const labs = HEALTH.archive?.labs || {};
  values.category = (labs.tests || []).find((item) => item.name === values.test)?.category || "其他";
  values.unit = values.unit || "";
  values.note = values.note || "";
  values.revision = labs.revision || "";
  setBusy(form, true);
  try {
    await healthApi("/api/health/labs", jsonBody("POST", values));
    toast("检验已添加", "success");
    closeHealthDialog();
    await refresh({ quiet: true });
  } catch (error) {
    setStatus(form, error.message, "error");
    setBusy(form, false);
  }
}

async function deleteLab(id) {
  const labs = HEALTH.archive?.labs || {};
  const ok = await confirmAction({ title: "删除这条检验记录？", message: "删除后只能从备份找回。", confirmLabel: "删除", danger: true });
  if (!ok) return;
  await healthApi(`/api/health/labs/${encodeURIComponent(id)}?revision=${encodeURIComponent(labs.revision || "")}`, { method: "DELETE" });
  toast("已删除", "success");
  await refresh({ quiet: true });
}

function openMedicationDialog() {
  const body = `<div class="health-modal-grid">
    <label class="health-field"><span>药品</span><input type="text" name="name" maxlength="60" required /></label>
    <label class="health-field"><span>剂量</span><input type="text" name="dosage" maxlength="40" placeholder="例如 5mg" /></label>
    <label class="health-field"><span>频次</span><input type="text" name="frequency" maxlength="40" placeholder="例如 每晚一次" /></label>
  </div><p class="health-note">用药只做记录，不替代医嘱；调整或停药请先问医生。</p>`;
  openHealthDialog(modalForm("medication", {
    icon: "药", title: "添加用药记录", lead: "只记录，不评价。", body, submitLabel: "添加用药",
  }));
  const form = document.querySelector('#dialog-content form[data-health-form="medication"]');
  if (!form) return;
  bindDialogAction(() => {});
}

async function saveMedication(form) {
  const values = readFields(form);
  if (!values.name) { setStatus(form, "先写药品名。", "error"); return; }
  const profile = HEALTH.archive?.profile || HEALTH.overview?.profile || {};
  const medications = [...(profile.medications || []), {
    name: values.name, dosage: values.dosage || "", frequency: values.frequency || "", active: true,
  }];
  setBusy(form, true);
  try {
    await healthApi("/api/health/profile", { method: "PUT", body: JSON.stringify({ medications, revision: profile.revision || "" }) });
    toast("用药已添加", "success");
    closeHealthDialog();
    await refresh({ quiet: true });
  } catch (error) {
    setStatus(form, error.message, "error");
    setBusy(form, false);
  }
}

async function deleteMedication(name) {
  const profile = HEALTH.archive?.profile || HEALTH.overview?.profile || {};
  const ok = await confirmAction({ title: `删除用药「${name}」？`, message: "只删除这条用药记录。", confirmLabel: "删除", danger: true });
  if (!ok) return;
  const medications = (profile.medications || []).filter((item) => item.name !== name);
  await healthApi("/api/health/profile", { method: "PUT", body: JSON.stringify({ medications, revision: profile.revision || "" }) });
  toast("已删除", "success");
  await refresh({ quiet: true });
}

// ── 报告 ────────────────────────────────────────────────────────────

async function generateReport(button) {
  if (!HEALTH.doctor?.capabilities?.ai) { toast("AI 未配置，暂时不能生成报告", "error"); return; }
  const ok = await confirmAction({
    title: "生成一份健康报告？",
    message: "私人医生会读取最近 30 天记录、最新身体档案与检验记录，生成后保存到「健康报告」本子。",
    confirmLabel: "生成报告",
  });
  if (!ok) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "正在写报告…";
  try {
    const result = await healthApi("/api/health/report", {
      method: "POST", timeoutMs: 180000,
      body: JSON.stringify({ confirm: true, days: 30 }),
    });
    toast("健康报告已生成", "success");
    HEALTH.reports = Array.isArray(result.reports) ? result.reports : HEALTH.reports;
    await refresh({ quiet: true });
    if (result.note?.path) await openReport(result.note.path);
  } catch (error) {
    toast(`报告没有生成：${error.message}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function openReport(path) {
  return openHealthNote(path, "report");
}

async function openConsult(path) {
  return openHealthNote(path, "consult");
}

async function openHealthNote(path, kind = "report") {
  if (!path) return;
  const isConsult = kind === "consult";
  const label = isConsult ? "问诊记录" : "健康报告";
  const endpoint = isConsult ? "/api/health/consults" : "/api/health/reports";
  openHealthDialog(`<div class="health-loading">正在读取${label}…</div>`);
  try {
    const note = await healthApi(`/api/notes?path=${encodeURIComponent(path)}`);
    const body = `<article class="health-report-reader">
      <header><div><span class="health-kicker">${label}</span><h2>${esc(note.title || label)}</h2><p>${esc(monthDayLabel(String(note.date || "").slice(0, 10)))} · 保存在「${label}」本子</p></div><button class="health-modal-close" type="button" data-dialog-action="close" aria-label="关闭">×</button></header>
      <div class="md-body">${renderMarkdownDocument(String(note.body || note.content || "")).html}</div>
      <footer><button class="health-btn ghost" type="button" data-dialog-action="close">关闭</button><button class="health-btn danger" type="button" data-dialog-action="delete-current-note" data-kind="${isConsult ? "consult" : "report"}" data-path="${esc(path)}" data-revision="${esc(note.revision || "")}">删除这份${label}</button></footer>
    </article>`;
    openHealthDialog(body);
    bindDialogAction(async (action, button) => {
      if (action !== "delete-current-note") return;
      const noteKind = button.dataset.kind === "consult" ? "consult" : "report";
      const noteLabel = noteKind === "consult" ? "问诊记录" : "健康报告";
      const ok = await confirmAction({ title: `删除这份${noteLabel}？`, message: `${noteLabel}会移进笔记回收站，可以从回收站恢复。`, confirmLabel: "删除", danger: true });
      if (!ok) return;
      const target = noteKind === "consult" ? "/api/health/consults" : "/api/health/reports";
      const result = await healthApi(`${target}?path=${encodeURIComponent(button.dataset.path)}&revision=${encodeURIComponent(button.dataset.revision || "")}`, { method: "DELETE" });
      if (noteKind === "consult") {
        HEALTH.consults = Array.isArray(result.consults) ? result.consults : HEALTH.consults;
        if (HEALTH.doctor) HEALTH.doctor.consults = HEALTH.consults;
        paintConsults();
      }
      toast(`${noteLabel}已移进回收站`, "success");
      closeHealthDialog();
      await refresh({ quiet: true });
    });
  } catch (error) {
    openHealthDialog(`<p class="health-inline-error">${label}读取失败：${esc(error.message)}</p>`);
  }
}
