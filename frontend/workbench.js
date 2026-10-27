// 工作台 2.0（2026-10-05）：今日待办（左清单 / 右每日勾选）；与神对话由 dsh-pane.js 加载完整的 DeepSeek Harness。
// Hero 挂三条座右铭与头图；右侧是时钟天气卡与黄历卡。数据本体都在 Markdown：
// 待办在 00_人生路/08_任务与待办/_系统/待办（todos）.md，每日必做勾在当天日记，习惯在本地库。
import { $, state, api, escapeHtml, jsonBody, setStatus } from "./core.js";
import { shouldShowWelcome } from "./welcome.js";
import { shanghaiDay } from "./journal-model.js";
import { initAlmanacWidget, renderAlmanac } from "./almanac-widget.js";
import { initWeatherWidget, loadWeatherWidget } from "./weather-widget.js";
import { initAnniversaryWidget } from "./anniversary-widget.js";
import { applyBanner, startRotation, imageSrc } from "./banner.js";
import { setBannerSource, initBannerEditor } from "./banner-editor.js";
import { initTimeModule, loadTimeTasks, renderTimeProgress } from "./time-module.js";
import { initCountdownWidget } from "./countdown.js";
import { initDeepSeekBalance, loadDeepSeekBalance } from "./deepseek-balance.js";
import { initTrainTrackerWidget, loadTrainTrackerWidget } from "./train-tracker-widget.js";
import { initWidgetDrag, refreshWorkbenchWidgets } from "./widget-drag.js";
import { loadPendingStrip } from "./pending.js";

let current = null;
let bannerStats = null;
let rotationSignature = "";
let rotationGeneration = 0;

function timeGreeting() {
  const hour = Number(new Intl.DateTimeFormat("en-GB", { hour: "2-digit", hour12: false, timeZone: "Asia/Shanghai" }).format(new Date()));
  if (hour >= 5 && hour < 11) return "早上好";
  if (hour >= 11 && hour < 13) return "中午好";
  if (hour >= 13 && hour < 18) return "下午好";
  return "晚上好";
}

function describeProgress(progress) {
  if (!progress || !progress.total) return "今天还没有安排，先加一条待办吧。";
  return `今日闭环 ${progress.done} / ${progress.total} 项（含日记）`;
}

export function renderWorkbench(payload) {
  current = payload;
  const name = (payload.name || "").trim();
  $("#workbench-hello").textContent = `${timeGreeting()}${name ? "，" + name : ""}`;
  $("#workbench-date").textContent = payload.day;

  // 横幅（引言/统计双模式、多图轮换、焦点、遮罩；数据在 01-Identity/身份（identity）.md）。
  const banner = applyBanner($("#workbench-banner"), payload, bannerStats);
  setBannerSource(payload);
  if (banner) {
    const signature = `${banner.heroes.join("|")}:${banner.mode}:${banner.focal}`;
    if (signature !== rotationSignature) {
      rotationSignature = signature;
      rotationGeneration += 1;
      const generation = rotationGeneration;
      startRotation(banner, (index) => {
        if (generation !== rotationGeneration) return;
        const background = $("#workbench-hero");
        if (!background) return;
        const safe = index % banner.heroes.length;
        background.style.backgroundImage = `url("${imageSrc(banner.heroes[safe], safe).replace(/"/g, "%22")}")`;
      }, (fn, ms) => window.setTimeout(fn, ms));
    }
    if (banner.mode === "stats" && !bannerStats) loadBannerStats();
  }

  // 今日完成度那条进度条搬进了时间管理模块顶部（2026-10-08），这里只把数据喂过去。
  renderTimeProgress(payload.progress);
  const progress = $("#time-progress-bar");
  if (progress) progress.title = describeProgress(payload.progress);

  renderAlmanac($("#workbench-almanac"), {
    day: payload.day, name, progress: payload.progress, fortune: payload.fortune,
  });
  renderDaily(payload);
  renderWelcome(payload);
  // 右侧小组件可以拖拽换位：重画后把新出现的组件接进来，顺序保持本机保存的结果。
  refreshWorkbenchWidgets();
}

const WELCOME_KEY = "torch.welcome.v1.dismissed";

function welcomeDismissed() {
  try { return localStorage.getItem(WELCOME_KEY) === "1"; } catch { return false; }
}

function dismissWelcome() {
  try { localStorage.setItem(WELCOME_KEY, "1"); } catch { /* private mode */ }
}

function renderWelcome(payload) {
  document.getElementById("workbench-welcome")?.remove();
  if (!shouldShowWelcome(payload, welcomeDismissed())) return;
  const banner = $("#workbench-banner");
  if (!banner) return;
  const card = document.createElement("section");
  card.id = "workbench-welcome";
  card.className = "workbench-welcome";
  card.setAttribute("role", "note");
  card.innerHTML = `
    <div>
      <p class="eyebrow">第一次来</p>
      <h3>先做一件小事</h3>
      <p>不用先整理结构。写一句日记、加一条任务，或者打开帮助看看四库怎么用。</p>
    </div>
    <div class="workbench-welcome-actions">
      <button type="button" class="small-button" data-welcome="journal">打开今天的日记</button>
      <button type="button" class="small-button" data-welcome="task">加一条任务</button>
      <button type="button" class="small-button" data-welcome="help">打开帮助</button>
      <button type="button" class="workbench-welcome-dismiss" data-welcome="dismiss" aria-label="关闭开始使用提示">×</button>
    </div>`;
  banner.insertAdjacentElement("afterend", card);
  card.querySelector('[data-welcome="journal"]')?.addEventListener("click", () => {
    dismissWelcome();
    card.remove();
    location.hash = "#life";
    let attempts = 0;
    const timer = window.setInterval(() => {
      const button = document.querySelector('#life-view [data-capture-chapter="journal"]');
      if (button) {
        window.clearInterval(timer);
        button.click();
      } else if (++attempts > 30) {
        window.clearInterval(timer);
      }
    }, 100);
  });
  card.querySelector('[data-welcome="task"]')?.addEventListener("click", () => {
    dismissWelcome();
    card.remove();
    const input = $("#time-add-input");
    input?.focus();
    input?.scrollIntoView({ block: "center", behavior: "smooth" });
  });
  card.querySelector('[data-welcome="help"]')?.addEventListener("click", () => {
    dismissWelcome();
    card.remove();
    const help = document.querySelector(".panel-nav-help");
    if (help) help.click();
    else location.hash = "#settings";
  });
  card.querySelector('[data-welcome="dismiss"]')?.addEventListener("click", () => {
    dismissWelcome();
    card.remove();
  });
}

function renderDaily(payload) {
  const rows = [
    ...(payload.daily || []).map((item) => ({
      kind: "daily", title: item.title, note: item.in_master === false ? "仅今天" : "",
      done: Boolean(item.done), key: item.key || "",
    })),
    ...(payload.habits || []).map((habit) => ({
      kind: "habit", title: habit.title, note: habit.minimum_action || "",
      done: Boolean(habit.done), id: habit.id, revision: habit.revision,
    })),
  ];
  const done = rows.filter((row) => row.done).length;
  if (payload.daily_error) {
    setStatus("#time-habits-count", `读取失败：${payload.daily_error}`, "error");
  } else {
    $("#time-habits-count").textContent = rows.length ? `完成 ${done} / ${rows.length}` : "";
  }
  $("#time-habits-hint").textContent = "每日必做在 00_人生路/08_任务与待办/_系统/待办（todos）.md 的「每日必做」区维护，勾选记进今天日记；习惯在人生路 →「习惯」里管理。";
  $("#time-habits-list").innerHTML = rows.length
    ? rows.map((row) => row.kind === "daily"
      ? `<li class="habit-check${row.done ? " done" : ""}"><label><input type="checkbox" data-daily-toggle data-daily-key="${row.key}" data-daily-title="${escapeHtml(row.title)}"${row.done ? " checked" : ""} aria-label="${escapeHtml(row.title)}" /><span>${escapeHtml(row.title)}</span></label>${row.note ? `<small>${escapeHtml(row.note)}</small>` : ""}</li>`
      : `<li class="habit-check${row.done ? " done" : ""}"><label><input type="checkbox" data-habit-toggle="${row.id}" data-habit-revision="${row.revision}"${row.done ? " checked" : ""} aria-label="${escapeHtml(row.title)}" /><span>${escapeHtml(row.title)}</span></label>${row.note ? `<small>${escapeHtml(row.note)}</small>` : ""}</li>`).join("")
    : '<li class="empty-state">还没有每日必做——去 00_人生路/08_任务与待办/_系统/待办（todos）.md 的「每日必做」区写几条。</li>';
}

export async function loadWorkbench() {
  try {
    const payload = await api(`/api/workbench/today?day=${shanghaiDay()}`);
    renderWorkbench(payload);
    await loadTimeTasks();
    await syncDailyIfNeeded(payload);
  } catch (error) {
    setStatus("#time-status", `读取失败：${error.message}`, "error");
  }
  loadWeatherWidget();
  loadDeepSeekBalance();
  loadTrainTrackerWidget();
  loadPendingStrip();
}

/** 主清单里有项还没写进当天日记 / 日记或小节还不存在时，静默镜像一次（只重载一次，不留循环）。 */
async function syncDailyIfNeeded(payload) {
  if (payload.daily_error) return;
  const needs = payload.daily_missing > 0 || !payload.daily_note_exists || !payload.daily_section_exists;
  if (!needs) return;
  try {
    const result = await api("/api/todos/daily/sync", jsonBody("POST", { day: shanghaiDay() }));
    if (result.changed) renderWorkbench(await api(`/api/workbench/today?day=${shanghaiDay()}`));
  } catch (error) {
    setStatus("#time-habits-hint", `每日必做同步失败：${error.message}`, "warning");
  }
}

async function loadBannerStats() {
  try {
    bannerStats = await api("/api/banner/stats");
  } catch (error) {
    bannerStats = null; // 占位符由 statsHtml 兜底
  }
  applyBanner($("#workbench-banner"), current, bannerStats);
}

async function toggleDaily(key, title, done) {
  try {
    await api("/api/todos/daily/toggle", jsonBody("POST", { day: shanghaiDay(), key, title, done }));
    await loadWorkbench();
  } catch (error) {
    setStatus("#time-habits-count", `更新失败：${error.message}`, "error");
    await loadWorkbench();
  }
}

async function toggleHabit(id, revision, done) {
  try {
    await api(`/api/habits/${id}/entries/${shanghaiDay()}`, jsonBody("PUT", {
      revision: Number(revision) || 0, status: done ? "done" : "unrecorded", note: "",
    }));
    await loadWorkbench();
  } catch (error) {
    setStatus("#time-habits-count", `打卡失败：${error.message}`, "error");
    await loadWorkbench();
  }
}

/** 收起/展开（照 Apex Dashboard）：状态存 .torch-os 个性化数据（浏览器缓存只是镜像）；收起后是 4px 细条，书签按钮展开。 */
function initBannerCollapse() {
  const KEY = "torch-os:banner-collapsed";
  const banner = $("#workbench-banner");
  const button = $("#workbench-banner-collapse");
  const apply = (value) => {
    banner.classList.toggle("banner-collapsed", value);
    button.setAttribute("aria-expanded", String(!value));
    button.textContent = value ? "▸" : "▾";
  };
  let collapsed = false;
  try {
    collapsed = window.localStorage.getItem(KEY) === "1";
  } catch {
    collapsed = false; // 没有本地存储就保持展开
  }
  apply(collapsed);
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    collapsed = !collapsed;
    apply(collapsed);
    try {
      window.localStorage.setItem(KEY, collapsed ? "1" : "0");
    } catch { /* 存储不可用就算了 */ }
  });
  button.addEventListener("dblclick", (event) => event.stopPropagation());
}

export function initWorkbench() {
  // 天气卡：时钟、打开系统时自动刷新、点卡片打开详情浮窗；城市管理都在 weather-widget.js。
  initWeatherWidget();
  // 每日必做 + 习惯的勾选：现在挂在时间管理模块的「习惯打卡」页上
  $("#time-habits-list").addEventListener("change", (event) => {
    const dailyBox = event.target.closest("[data-daily-toggle]");
    if (dailyBox) { toggleDaily(dailyBox.dataset.dailyKey || "", dailyBox.dataset.dailyTitle || "", dailyBox.checked); return; }
    const habitBox = event.target.closest("[data-habit-toggle]");
    if (habitBox) toggleHabit(habitBox.dataset.habitToggle, habitBox.dataset.habitRevision, habitBox.checked);
  });
  document.addEventListener("data-changed", () => { if (state.view === "workbench") loadWorkbench(); });
  initBannerEditor(() => loadWorkbench());
  initBannerCollapse();
  initCountdownWidget();
  initAnniversaryWidget();
  initWidgetDrag();
  initAlmanacWidget();
  initDeepSeekBalance();
  initTrainTrackerWidget();
  initTimeModule();
}
