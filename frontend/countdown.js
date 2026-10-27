// 工作台右侧倒计时卡（仿 Apex Dashboard 的 countdown 小组件）。
// 配置留在本机 localStorage；核心计算在 countdown-model.js，便于测试。
import { $, escapeHtml, openDialog } from "./core.js";
import {
  COUNTDOWN_THEMES, countdownState, defaultCountdown,
  formatTarget, loadCountdown, normalizeCountdown, saveCountdown,
} from "./countdown-model.js";

const HOST_ID = "workbench-countdown";
const MAX_IMAGE_BYTES = 1_200_000;
let currentConfig = null;
let timer = 0;
let lastValue = null;

function safeStorage() {
  try { return window.localStorage; } catch { return null; }
}

function ensureConfig() {
  if (!currentConfig) currentConfig = loadCountdown(safeStorage());
  return currentConfig;
}

function updateCard(host, state, { animate = false } = {}) {
  if (!host) return;
  host.classList.toggle("is-expired", state.expired);
  const number = host.querySelector?.("[data-countdown-number]");
  const unit = host.querySelector?.("[data-countdown-unit]");
  const line = host.querySelector?.("[data-countdown-value-line]");
  const expired = host.querySelector?.("[data-countdown-expired]");
  const reminder = host.querySelector?.("[data-countdown-reminder]");
  if (number) {
    const nextText = state.expired ? "" : String(state.value);
    if (animate && !state.expired && lastValue !== null && nextText !== String(lastValue)) {
      number.classList?.remove("is-flipping");
      // 重新触发同一段 CSS 动画：读一次布局，不写内联样式。
      void number.offsetWidth;
      number.classList?.add("is-flipping");
      window.setTimeout?.(() => number.classList?.remove("is-flipping"), 420);
    }
    if (nextText) number.textContent = nextText;
    lastValue = state.expired ? lastValue : state.value;
  }
  if (unit) unit.textContent = state.unit;
  if (line) line.hidden = state.expired;
  if (expired) expired.hidden = !state.expired;
  if (reminder) {
    reminder.hidden = !state.reminderActive;
    if (state.reminderActive) reminder.textContent = `提醒期 · 还剩 ${state.days} 天`;
  }
  host.setAttribute?.("title", `${state.label} · ${formatTarget(state.target)}`);
}

function paint(host) {
  if (!host) return;
  const config = ensureConfig();
  const state = countdownState(config);
  host.dataset.theme = config.theme;
  host.classList.toggle("countdown-has-image", Boolean(config.image));
  host.innerHTML = `
    <div class="countdown-bg" aria-hidden="true"></div>
    <div class="countdown-shade" aria-hidden="true"></div>
    <button class="countdown-settings-btn" type="button" data-countdown-settings aria-label="倒计时设置" title="倒计时设置">
      <span aria-hidden="true">⚙</span>
    </button>
    <div class="countdown-inner">
      <p class="countdown-label">${config.label ? `距离<span>${escapeHtml(config.label)}</span>还有` : "倒计时"}</p>
      <p class="countdown-value-line" data-countdown-value-line>
        <strong class="countdown-number" data-countdown-number>${state.value}</strong>
        <span class="countdown-unit" data-countdown-unit>${escapeHtml(state.unit)}</span>
      </p>
      <p class="countdown-expired-text" data-countdown-expired hidden>已到期</p>
      <p class="countdown-reminder" data-countdown-reminder hidden></p>
    </div>`;
  const background = host.querySelector?.(".countdown-bg");
  if (background) {
    if (config.image) background.style.backgroundImage = `url("${config.image.replace(/"/g, "%22")}")`;
    else background.style.removeProperty?.("background-image");
  }
  updateCard(host, state);
}

function startTicker() {
  if (timer || typeof window.setInterval !== "function") return;
  timer = window.setInterval(() => {
    const host = document.getElementById(HOST_ID);
    if (!host) return;
    if (host.isConnected === false) {
      window.clearInterval(timer);
      timer = 0;
      return;
    }
    updateCard(host, countdownState(ensureConfig()), { animate: true });
  }, 30000);
}

function handleClick(event) {
  const button = event.target?.closest?.("[data-countdown-settings]");
  if (!button) return;
  event.preventDefault?.();
  event.stopPropagation?.();
  openSettings();
}

export function initCountdownWidget() {
  const host = document.getElementById(HOST_ID);
  if (!host) return;
  if (host.dataset.countdownReady !== "1") {
    host.dataset.countdownReady = "1";
    host.addEventListener("click", handleClick);
  }
  ensureConfig();
  paint(host);
  startTicker();
}

function themeOptions(selected) {
  return COUNTDOWN_THEMES.map(([id, label]) =>
    `<option value="${escapeHtml(id)}" ${id === selected ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
}

function setPreview(preview, theme, image) {
  if (!preview) return;
  preview.dataset.theme = theme;
  preview.classList.toggle("has-image", Boolean(image));
  if (image) preview.style.backgroundImage = `url("${image.replace(/"/g, "%22")}")`;
  else preview.style.removeProperty?.("background-image");
}

function openSettings() {
  const config = normalizeCountdown(ensureConfig());
  let pendingImage = config.image || "";
  let pendingTheme = config.theme;
  openDialog("倒计时设置", `
    <form id="countdown-form" class="countdown-settings-form">
      <p class="field-help">配置只保存在当前浏览器（LocalStorage），不写 Obsidian 库；不同端口/浏览器各有一份。</p>
      <div class="field">
        <label for="countdown-label-input">标签</label>
        <input id="countdown-label-input" maxlength="80" value="${escapeHtml(config.label)}" placeholder="例如：New Year Countdown" />
      </div>
      <div class="field">
        <label for="countdown-target-input">目标日期与时间</label>
        <input id="countdown-target-input" type="datetime-local" required value="${escapeHtml(config.targetDate)}" />
      </div>
      <div class="form-grid">
        <div class="field">
          <label for="countdown-mode-input">显示模式</label>
          <select id="countdown-mode-input">
            <option value="days" ${config.displayMode === "days" ? "selected" : ""}>天</option>
            <option value="hours" ${config.displayMode === "hours" ? "selected" : ""}>小时</option>
            <option value="minutes" ${config.displayMode === "minutes" ? "selected" : ""}>分钟</option>
          </select>
        </div>
        <div class="field">
          <label for="countdown-reminder-input">提前提醒（天）</label>
          <input id="countdown-reminder-input" type="number" min="0" max="365" step="1" value="${Number(config.reminderDays) || 0}" />
          <span class="field-help">0 = 不提醒；进入提醒期时卡片会显示小字。</span>
        </div>
      </div>
      <div class="field">
        <label>背景</label>
        <div id="countdown-bg-preview" class="countdown-bg-preview" data-theme="${escapeHtml(config.theme)}">
          <span>背景预览</span>
        </div>
        <div class="countdown-bg-actions">
          <select id="countdown-theme-input" aria-label="背景主题">${themeOptions(config.theme)}</select>
          <button class="small-button" type="button" id="countdown-choose-image">选择图片</button>
          <button class="text-button" type="button" id="countdown-clear-image">使用渐变</button>
        </div>
        <input id="countdown-image-input" type="file" accept="image/*" hidden />
        <p class="field-help">图片只从本机读取，保存在此浏览器的本地存储里；不上传。建议小于 1 MB。</p>
      </div>
      <div class="form-actions">
        <span id="countdown-settings-feedback" class="status-message" role="status"></span>
        <div class="countdown-form-buttons">
          <button class="text-button" type="button" id="countdown-reset">恢复默认</button>
          <button class="button secondary" type="button" id="countdown-cancel">取消</button>
          <button class="button primary" type="submit">保存</button>
        </div>
      </div>
    </form>`);

  const preview = $("#countdown-bg-preview");
  const themeInput = $("#countdown-theme-input");
  const imageInput = $("#countdown-image-input");
  const feedback = $("#countdown-settings-feedback");
  const dialog = $("#detail-dialog");
  setPreview(preview, pendingTheme, pendingImage);

  const setFeedback = (message, tone = "") => {
    if (!feedback) return;
    feedback.textContent = message;
    feedback.className = `status-message ${tone}`.trim();
  };

  themeInput?.addEventListener("change", () => {
    pendingTheme = themeInput.value;
    setPreview(preview, pendingTheme, pendingImage);
  });
  $("#countdown-choose-image")?.addEventListener("click", () => imageInput?.click());
  $("#countdown-clear-image")?.addEventListener("click", () => {
    pendingImage = "";
    setPreview(preview, pendingTheme, pendingImage);
    setFeedback("已改为渐变背景，保存后生效。", "success");
  });
  imageInput?.addEventListener("change", () => {
    const file = imageInput.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setFeedback("请选择图片文件。", "error");
      imageInput.value = "";
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setFeedback("图片超过 1 MB；请先压缩，或改用渐变背景。", "error");
      imageInput.value = "";
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      pendingImage = String(reader.result || "");
      setPreview(preview, pendingTheme, pendingImage);
      setFeedback("图片已读入本机，保存后生效。", "success");
    };
    reader.onerror = () => setFeedback("读取图片失败，请重试。", "error");
    reader.readAsDataURL(file);
  });
  $("#countdown-reset")?.addEventListener("click", () => {
    const defaults = defaultCountdown();
    pendingImage = defaults.image;
    pendingTheme = defaults.theme;
    $("#countdown-label-input").value = defaults.label;
    $("#countdown-target-input").value = defaults.targetDate;
    $("#countdown-mode-input").value = defaults.displayMode;
    $("#countdown-reminder-input").value = String(defaults.reminderDays);
    if (themeInput) themeInput.value = defaults.theme;
    setPreview(preview, pendingTheme, pendingImage);
    setFeedback("已恢复默认值；点「保存」后写入。", "success");
  });
  $("#countdown-cancel")?.addEventListener("click", () => dialog?.close());
  $("#countdown-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const targetDate = $("#countdown-target-input")?.value?.trim() || "";
    if (!targetDate) {
      setFeedback("请选择目标日期与时间。", "error");
      return;
    }
    const next = normalizeCountdown({
      label: $("#countdown-label-input")?.value || "",
      targetDate,
      displayMode: $("#countdown-mode-input")?.value || "days",
      reminderDays: Number($("#countdown-reminder-input")?.value || 0),
      theme: pendingTheme,
      image: pendingImage,
    });
    try {
      currentConfig = saveCountdown(safeStorage(), next);
    } catch (error) {
      setFeedback(error.message, "error");
      return;
    }
    dialog?.close();
    lastValue = null;
    paint(document.getElementById(HOST_ID));
  });
}
