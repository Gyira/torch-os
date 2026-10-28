// 纪念日小组件（2026-10-27，参考 Apex Dashboard 的 Anniversary widget）：
// 照片/渐变背景上的标题 + 已过去时长 + 历史日期；点卡片打开设置。
// 支持阳历/农历、年 月 日 / 总天数 / 天 + 小时、每年提醒、本机图片或 https 背景 URL。
// 配置写入当前个人数据库的 .torch-os/personal-data.json（localStorage 只是缓存），不上传。
import { $, escapeHtml, openDialog, toast } from "./core.js";
import {
  LUNAR_MONTH_NAMES, LUNAR_YEAR_MIN, LUNAR_YEAR_MAX, MAX_IMAGE_BYTES, PRECISIONS,
  anniversaryMatch, formatAnniversaryCard, formatDate, lunarDayOptions, lunarMonthOptions,
  lunarPartsFromSolar, normalizeAnniversary, normalizeBackground, normalizeStartDate,
  parseAnniversaryDate, solarFromLunar,
} from "./anniversary-model.js";

const HOST_ID = "workbench-anniversary";
const STORE_KEY = "torch-os:anniversary";
const REMIND_KEY = "torch-os:anniversary-reminded";
const TICK_MS = 60_000;

let config = null;
let initialized = false;
let sessionReminderKey = "";

function readStored() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeStored(next) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(next));
  } catch { /* 隐私模式/配额满：本次页面仍然有效 */ }
}

function loadConfig() {
  const stored = readStored();
  const next = normalizeAnniversary(stored);
  if (!stored) writeStored(next);
  return next;
}

function backgroundValue(value) {
  return `url("${String(value || "").replace(/"/g, "%22")}")`;
}

function applyCardBackground(host) {
  const background = host?.querySelector?.(".anniversary-bg");
  if (!background) return;
  if (config.background) background.style.backgroundImage = backgroundValue(config.background);
  else background.style.removeProperty("background-image");
}

function cardAria(view) {
  return `纪念日：${view.title}，${view.value}，${view.dateText}`
    + `${view.reminder ? `，${view.reminder}` : ""}。点击设置`;
}

/** 长时长（如 12年11个月29天）自动收小字号，避免撑出小卡片。 */
function valueSizeClass(text) {
  const length = String(text || "").length;
  if (length > 8) return " is-longer";
  if (length > 5) return " is-long";
  return "";
}

/** 完整重画卡片（配置变化/首次进入）。 */
function paint() {
  const host = document.getElementById(HOST_ID);
  if (!host || !config) return;
  const view = formatAnniversaryCard(config, new Date());
  host.setAttribute("aria-label", cardAria(view));
  host.innerHTML = [
    '<div class="anniversary-bg" aria-hidden="true"></div>',
    '<div class="anniversary-shade" aria-hidden="true"></div>',
    '<span class="anniversary-settings-hint" aria-hidden="true">⚙</span>',
    '<div class="anniversary-inner">',
    `<p class="anniversary-label">${escapeHtml(view.title)}</p>`,
    `<p class="anniversary-value${valueSizeClass(view.value)}">${escapeHtml(view.value)}</p>`,
    `<p class="anniversary-date">${escapeHtml(view.dateText)}</p>`,
    `<p class="anniversary-reminder"${view.reminder ? "" : " hidden"}>${escapeHtml(view.reminder)}</p>`,
    "</div>",
  ].join("");
  applyCardBackground(host);
}

/** 只更新会随时间变化的文字，避免每分钟重建背景层、重复加载图片。 */
function refreshLive() {
  const host = document.getElementById(HOST_ID);
  if (!host || !config) return;
  if (!host.querySelector(".anniversary-inner")) {
    paint();
    return;
  }
  const view = formatAnniversaryCard(config, new Date());
  const value = host.querySelector(".anniversary-value");
  if (value) {
    value.textContent = view.value;
    value.className = `anniversary-value${valueSizeClass(view.value)}`;
  }
  const date = host.querySelector(".anniversary-date");
  if (date) date.textContent = view.dateText;
  const reminder = host.querySelector(".anniversary-reminder");
  if (reminder) {
    reminder.textContent = view.reminder;
    reminder.hidden = !view.reminder;
  }
  host.setAttribute("aria-label", cardAria(view));
  maybeRemind();
}

function maybeRemind() {
  if (!config?.annualReminder) return false;
  const now = new Date();
  const match = anniversaryMatch(config, now);
  if (!match) return false;
  const key = `${formatDate(now)}|${config.startDate}|${config.calendar}|${config.name}`;
  if (sessionReminderKey === key) return false;
  try {
    if (localStorage.getItem(REMIND_KEY) === key) {
      sessionReminderKey = key;
      return false;
    }
  } catch { /* 隐私模式：只在本次页面里防重复 */ }
  sessionReminderKey = key;
  try { localStorage.setItem(REMIND_KEY, key); } catch { /* 同上 */ }
  toast(`${config.name || "纪念日"}：已经 ${match.years} 年了`, "success");
  return true;
}

function precisionOptions(selected) {
  return PRECISIONS.map(([id, label]) =>
    `<option value="${escapeHtml(id)}"${id === selected ? " selected" : ""}>${escapeHtml(label)}</option>`).join("");
}

function startParts(raw) {
  const start = parseAnniversaryDate(raw);
  if (!start) return { date: "", time: "" };
  const date = formatDate(start);
  return {
    date,
    time: String(raw).includes("T") || /\s\d{2}:\d{2}$/.test(String(raw))
      ? `${String(start.getHours()).padStart(2, "0")}:${String(start.getMinutes()).padStart(2, "0")}`
      : "",
  };
}

function openSettings() {
  if (!config) return;
  const draft = normalizeAnniversary(config);
  const initialStart = startParts(draft.startDate);
  let pendingBackground = config.background || "";
  const isDataBackground = String(config.background || "").startsWith("data:");
  openDialog("纪念日", `
    <form id="anniversary-form" class="anniversary-form">
      <div class="field">
        <label for="anniversary-name">名称</label>
        <input id="anniversary-name" maxlength="60" value="${escapeHtml(draft.name)}" placeholder="纪念日" />
        <span class="field-help">显示为卡片标题（如「在一起的那天」）。</span>
      </div>
      <div class="form-grid">
        <div class="field">
          <label for="anniversary-calendar">历法</label>
          <select id="anniversary-calendar">
            <option value="solar"${draft.calendar === "solar" ? " selected" : ""}>阳历</option>
            <option value="lunar"${draft.calendar === "lunar" ? " selected" : ""}>农历</option>
          </select>
          <span class="field-help">农历按农历显示日期，并在农历周年当天提醒。</span>
        </div>
        <div class="field">
          <label for="anniversary-precision">计时显示</label>
          <select id="anniversary-precision">${precisionOptions(draft.precision)}</select>
          <span class="field-help">已过去时长的显示粒度。</span>
        </div>
      </div>
      <div class="field" data-anniversary-solar-row>
        <label for="anniversary-date">历史日期</label>
        <div class="anniversary-date-row">
          <input id="anniversary-date" type="date" value="${escapeHtml(initialStart.date)}" />
          <input id="anniversary-time" type="time" value="${escapeHtml(initialStart.time)}" aria-label="历史时间（可选）" />
        </div>
        <span class="field-help">YYYY-MM-DD，时间可选（用于「天 + 小时」精度）。</span>
      </div>
      <div class="field" data-anniversary-lunar-row hidden>
        <label>历史日期（农历）</label>
        <div class="anniversary-lunar-row">
          <input data-anniversary-lunar-year type="number" min="${LUNAR_YEAR_MIN}" max="${LUNAR_YEAR_MAX}" aria-label="农历年" />
          <select data-anniversary-lunar-month aria-label="农历月"></select>
          <select data-anniversary-lunar-day aria-label="农历日"></select>
        </div>
        <span class="field-help">按农历选择，保存为对应的阳历日期。</span>
      </div>
      <div class="field">
        <label class="anniversary-toggle">
          <input id="anniversary-reminder" type="checkbox"${draft.annualReminder ? " checked" : ""} />
          <span class="anniversary-toggle-track" aria-hidden="true"></span>
          <span>每年提醒</span>
        </label>
        <span class="field-help">每年的同一月日提醒一次；农历按农历周年。</span>
      </div>
      <div class="field">
        <label for="anniversary-bg-url">背景</label>
        <div class="anniversary-bg-row">
          <input id="anniversary-bg-url" value="${escapeHtml(isDataBackground ? "" : config.background || "")}" placeholder="https://… 图片地址" />
          <button type="button" class="small-button" id="anniversary-bg-choose">选择本机图片</button>
          <button type="button" class="text-button" id="anniversary-bg-clear">清除</button>
        </div>
        <input id="anniversary-bg-file" type="file" accept="image/*" hidden />
        <div id="anniversary-bg-preview" class="anniversary-bg-preview"><span>背景预览</span></div>
        <span class="field-help">网络图片由浏览器直接加载（仅 https）；本机图片写入当前个人数据库的 <code>.torch-os</code> 个性化数据。</span>
      </div>
      <div class="anniversary-preview">
        <span>预览</span>
        <strong id="anniversary-preview-value" aria-live="polite">--</strong>
        <small id="anniversary-preview-date"></small>
      </div>
      <div class="form-actions">
        <span id="anniversary-feedback" class="status-message" role="status"></span>
        <div class="anniversary-form-buttons">
          <button type="button" class="button secondary" id="anniversary-cancel">取消</button>
          <button type="submit" class="button primary">保存</button>
        </div>
      </div>
    </form>`);
  $("#detail-dialog")?.classList.add("anniversary-mode");
  bindSettingsForm({ draft, pendingBackground });
}

function bindSettingsForm({ draft, pendingBackground }) {
  const dialog = $("#detail-dialog");
  const feedback = $("#anniversary-feedback");
  const nameInput = $("#anniversary-name");
  const calendarInput = $("#anniversary-calendar");
  const precisionInput = $("#anniversary-precision");
  const dateInput = $("#anniversary-date");
  const timeInput = $("#anniversary-time");
  const solarRow = $("[data-anniversary-solar-row]");
  const lunarRow = $("[data-anniversary-lunar-row]");
  const yearInput = $("[data-anniversary-lunar-year]");
  const monthSelect = $("[data-anniversary-lunar-month]");
  const daySelect = $("[data-anniversary-lunar-day]");
  const reminderInput = $("#anniversary-reminder");
  const bgUrlInput = $("#anniversary-bg-url");
  const bgFile = $("#anniversary-bg-file");
  const bgPreview = $("#anniversary-bg-preview");
  const previewValue = $("#anniversary-preview-value");
  const previewDate = $("#anniversary-preview-date");
  if (!dialog || !feedback) return;

  let lunarYear = 0;
  let lunarMonth = 1;
  let lunarDay = 1;
  let background = pendingBackground;

  const setFeedback = (message, tone = "") => {
    feedback.textContent = message;
    feedback.className = `status-message ${tone}`.trim();
  };

  function setBackgroundPreview() {
    if (!bgPreview) return;
    const safe = normalizeBackground(background);
    if (safe) bgPreview.style.backgroundImage = backgroundValue(safe);
    else bgPreview.style.removeProperty("background-image");
  }

  function updatePreview() {
    const view = formatAnniversaryCard(normalizeAnniversary({
      ...draft, background, name: draft.name,
    }), new Date());
    if (previewValue) previewValue.textContent = view.value;
    if (previewDate) previewDate.textContent = view.dateText;
    setBackgroundPreview();
  }

  function readSolarInputs() {
    const date = dateInput?.value || "";
    const time = timeInput?.value || "";
    draft.startDate = normalizeStartDate(`${date}${time ? `T${time}` : ""}`);
    updatePreview();
  }

  function currentMonth() {
    return lunarMonthOptions(lunarYear).find((month) => month.month === lunarMonth) || null;
  }

  function renderMonthOptions() {
    if (!monthSelect) return;
    const months = lunarMonthOptions(lunarYear);
    const list = months.length
      ? months
      : Array.from({ length: 12 }, (_, index) => ({
        month: index + 1, dayCount: 30, label: `${LUNAR_MONTH_NAMES[index]}月`,
      }));
    if (!list.some((month) => month.month === lunarMonth)) {
      lunarMonth = Math.abs(lunarMonth);
      if (!list.some((month) => month.month === lunarMonth)) lunarMonth = list[0].month;
    }
    monthSelect.innerHTML = list.map((month) =>
      `<option value="${month.month}"${month.month === lunarMonth ? " selected" : ""}>${escapeHtml(month.label)}</option>`).join("");
    renderDayOptions();
  }

  function renderDayOptions() {
    if (!daySelect) return;
    const days = lunarDayOptions(currentMonth()?.dayCount || 30);
    lunarDay = Math.min(Math.max(1, lunarDay), days.length);
    daySelect.innerHTML = days.map((day) =>
      `<option value="${day.day}"${day.day === lunarDay ? " selected" : ""}>${escapeHtml(day.label)}</option>`).join("");
  }

  function commitLunar() {
    const converted = solarFromLunar(lunarYear, lunarMonth, lunarDay);
    if (converted) {
      draft.startDate = converted.date;
      if (dateInput) dateInput.value = converted.date;
    }
    updatePreview();
  }

  function seedLunar() {
    const start = parseAnniversaryDate(draft.startDate) || new Date();
    const parts = lunarPartsFromSolar(start);
    lunarYear = parts?.year || new Date().getFullYear();
    lunarMonth = parts?.month || 1;
    lunarDay = parts?.day || 1;
    if (yearInput) yearInput.value = String(lunarYear);
    renderMonthOptions();
    commitLunar();
  }

  function applyCalendarMode() {
    const lunar = draft.calendar === "lunar";
    if (solarRow) solarRow.hidden = lunar;
    if (lunarRow) lunarRow.hidden = !lunar;
    if (lunar) seedLunar();
    else readSolarInputs();
  }

  nameInput?.addEventListener("input", () => {
    draft.name = nameInput.value;
    updatePreview();
  });
  calendarInput?.addEventListener("change", () => {
    draft.calendar = calendarInput.value === "lunar" ? "lunar" : "solar";
    applyCalendarMode();
    updatePreview();
  });
  precisionInput?.addEventListener("change", () => {
    draft.precision = precisionInput.value;
    updatePreview();
  });
  dateInput?.addEventListener("change", readSolarInputs);
  timeInput?.addEventListener("change", readSolarInputs);
  reminderInput?.addEventListener("change", () => {
    draft.annualReminder = Boolean(reminderInput.checked);
  });
  yearInput?.addEventListener("change", () => {
    const year = Math.trunc(Number(yearInput.value));
    if (!Number.isFinite(year) || year < LUNAR_YEAR_MIN || year > LUNAR_YEAR_MAX) {
      yearInput.value = String(lunarYear);
      return;
    }
    lunarYear = year;
    renderMonthOptions();
    commitLunar();
  });
  monthSelect?.addEventListener("change", () => {
    lunarMonth = Number(monthSelect.value) || 1;
    renderDayOptions();
    commitLunar();
  });
  daySelect?.addEventListener("change", () => {
    lunarDay = Number(daySelect.value) || 1;
    commitLunar();
  });

  bgUrlInput?.addEventListener("input", () => {
    background = bgUrlInput.value.trim();
  });
  // 只在离开输入框时加载网络预览，避免每敲一个字符就向远程地址发请求。
  bgUrlInput?.addEventListener("change", () => {
    background = bgUrlInput.value.trim();
    setBackgroundPreview();
  });
  $("#anniversary-bg-choose")?.addEventListener("click", () => bgFile?.click());
  bgFile?.addEventListener("change", () => {
    const file = bgFile.files?.[0];
    if (!file) return;
    if (!/^image\/(png|jpeg|webp|gif|avif)$/i.test(file.type)) {
      setFeedback("请选择 PNG/JPG/WebP/GIF/AVIF 图片。", "error");
      bgFile.value = "";
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setFeedback("图片超过 1 MB，请先压缩。", "error");
      bgFile.value = "";
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      background = String(reader.result || "");
      if (bgUrlInput) bgUrlInput.value = "";
      updatePreview();
      setFeedback("图片已读入本机，保存后生效。", "success");
    };
    reader.onerror = () => setFeedback("读取图片失败，请重试。", "error");
    reader.readAsDataURL(file);
  });
  $("#anniversary-bg-clear")?.addEventListener("click", () => {
    background = "";
    if (bgUrlInput) bgUrlInput.value = "";
    if (bgFile) bgFile.value = "";
    updatePreview();
    setFeedback("已清除背景，保存后生效。", "success");
  });
  $("#anniversary-cancel")?.addEventListener("click", () => dialog.close());
  $("#anniversary-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    if (draft.calendar === "solar") readSolarInputs();
    if (!draft.startDate) {
      setFeedback("请选择历史日期。", "error");
      if (draft.calendar === "lunar") yearInput?.focus();
      else dateInput?.focus();
      return;
    }
    const typed = bgUrlInput?.value.trim() || "";
    if (typed && !normalizeBackground(typed)) {
      setFeedback("背景图片地址需以 https:// 开头，或选择本机图片。", "error");
      bgUrlInput?.focus();
      return;
    }
    if (typed) background = typed;
    const next = normalizeAnniversary({
      ...draft,
      name: nameInput?.value || "",
      calendar: calendarInput?.value || "solar",
      precision: precisionInput?.value || "ymd",
      annualReminder: Boolean(reminderInput?.checked),
      background,
    });
    if (!next.startDate) {
      setFeedback("历史日期无效，请重新选择。", "error");
      return;
    }
    config = next;
    writeStored(config);
    paint();
    const reminded = maybeRemind();
    dialog.close();
    if (!reminded) toast("纪念日已保存。", "success");
  });

  applyCalendarMode();
  setBackgroundPreview();
  updatePreview();
}

function bindCard(host) {
  if (host.dataset.anniversaryReady === "1") return;
  host.dataset.anniversaryReady = "1";
  host.addEventListener("click", () => openSettings());
  host.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    if (event.target?.closest?.("button, a, input, select, textarea")) return;
    event.preventDefault();
    openSettings();
  });
}

function bindDialogClose() {
  $("#detail-dialog")?.addEventListener("close", () => {
    $("#detail-dialog")?.classList.remove("anniversary-mode");
  });
}

export function initAnniversaryWidget() {
  if (initialized) return false;
  const host = document.getElementById(HOST_ID);
  if (!host) return false;
  initialized = true;
  config = loadConfig();
  bindCard(host);
  bindDialogClose();
  paint();
  maybeRemind();
  if (typeof window.setInterval === "function") {
    window.setInterval(() => {
      if (!document.hidden) refreshLive();
    }, TICK_MS);
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshLive();
  });
  return true;
}
