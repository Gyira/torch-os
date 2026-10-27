// 右侧小组件：DeepSeek API 余额（数据经本地后端代理，Key 不下发到浏览器）。
// 余额来自官方 /user/balance；本机可选保存 Key（加密），否则自动使用环境变量 / DSH 凭据。
import { $, api, escapeHtml, jsonBody, openDialog } from "./core.js";
import {
  DEEPSEEK_TOP_UP_URL, balanceSourceLabel, balanceSourceShort,
  formatBalanceAmount, formatBalanceTime, isLowBalance,
  readAlertThreshold, writeAlertThreshold,
} from "./deepseek-balance-model.js";

const HOST_ID = "workbench-deepseek";
const AUTO_REFRESH_MS = 60_000;
const MIN_RELOAD_MS = 12_000;
let lastPayload = null;
let lastGood = null;
let loading = false;
let initialized = false;
let timer = 0;
let lastLoadAt = 0;
let pendingReload = false;

function host() {
  return document.getElementById(HOST_ID);
}

function workbenchVisible() {
  const view = document.getElementById("workbench-view");
  return !view || view.hidden !== true;
}

function headMarkup() {
  const disabled = loading ? " disabled" : "";
  return `<div class="deepseek-head">
    <span class="deepseek-brand"><span class="deepseek-mark" aria-hidden="true">DS</span>DeepSeek 余额</span>
    <span class="deepseek-head-actions">
      <button type="button" class="deepseek-icon-btn" data-balance-refresh aria-label="刷新余额" title="刷新余额"${disabled}>↻</button>
      <button type="button" class="deepseek-icon-btn" data-balance-settings aria-label="余额设置" title="余额设置">⚙</button>
    </span>
  </div>`;
}

function loadingMarkup() {
  return `${headMarkup()}<p class="deepseek-state-text">正在读取余额…</p>`;
}

function balanceMarkup(data, failure) {
  const threshold = readAlertThreshold();
  const currency = String(data.currency || "CNY").toUpperCase();
  const amount = formatBalanceAmount(data.total_balance, currency);
  const topped = formatBalanceAmount(data.topped_up_balance, currency);
  const granted = formatBalanceAmount(data.granted_balance, currency);
  const limit = threshold > 0 ? formatBalanceAmount(threshold, currency) : "";
  const low = isLowBalance(data.total_balance, threshold);
  const updated = formatBalanceTime(data.fetched_at);
  const source = balanceSourceShort(data.source);
  const topUpUrl = escapeHtml(DEEPSEEK_TOP_UP_URL);
  const unavailable = data.is_available === false
    ? '<p class="deepseek-alert is-low">账户余额不足，暂时无法调用 API</p>' : "";
  let alert = "";
  if (failure) {
    alert = `<p class="deepseek-alert is-error">刷新失败：${escapeHtml(failure.error || "请稍后重试")} · 显示上次余额</p>`;
  } else if (threshold > 0) {
    alert = low
      ? `<p class="deepseek-alert is-low">余额偏低 · 低于 ${escapeHtml(limit)}，建议充值</p>`
      : `<p class="deepseek-alert">余额预警线 ${escapeHtml(limit)}</p>`;
  } else {
    alert = '<button type="button" class="deepseek-alert deepseek-alert-link" data-balance-settings title="本机余额预警；不会修改 DeepSeek 平台设置">余额预警未开启 · 去设置</button>';
  }
  const meta = [];
  if (updated) meta.push(`更新于 ${updated}`);
  if (source) meta.push(`来源：${source}`);
  return `${headMarkup()}
    <p class="deepseek-amount">${escapeHtml(amount)}<span class="deepseek-currency">${escapeHtml(currency)}</span></p>
    <p class="deepseek-breakdown">充值余额 ${escapeHtml(topped)} · 赠金 ${escapeHtml(granted)}</p>
    ${unavailable}
    ${alert}
    <div class="deepseek-foot">
      <span class="deepseek-updated">${escapeHtml(meta.join(" · "))}</span>
      <a class="deepseek-topup" href="${topUpUrl}" target="_blank" rel="noopener noreferrer">去充值</a>
    </div>`;
}

function errorMarkup(payload) {
  const code = payload.code || "NETWORK";
  const titles = {
    NO_KEY: "还没有配置 DeepSeek Key",
    AUTH: "Key 无效或已过期",
    DISABLED: "余额不可用",
    NETWORK: "余额读取失败",
    SERVER: "余额读取失败",
    RATE: "请求太频繁",
    SHAPE: "余额数据异常",
    HTTP: "余额读取失败",
    LOCAL: "余额读取失败",
  };
  const fallback = {
    NO_KEY: "在设置里粘贴 Key；如果 DSH 已配置，会自动读取 DSH 凭据。",
    AUTH: "请到设置里更新 DeepSeek API Key。",
    DISABLED: "当前运行模式不允许读取 DeepSeek 余额。",
    NETWORK: "无法连接 DeepSeek，请检查网络后重试。",
    SERVER: "DeepSeek 服务暂时不可用，请稍后重试。",
    RATE: "请稍后再试。",
    SHAPE: "余额接口返回结构异常，请稍后重试。",
  };
  const source = payload.source ? `<p class="deepseek-state-source">当前来源：${escapeHtml(balanceSourceLabel(payload.source))}</p>` : "";
  const canConfigure = code !== "DISABLED";
  const actions = `<div class="deepseek-state-actions">
    ${canConfigure ? '<button type="button" class="deepseek-button" data-balance-settings>去设置</button>' : ""}
    ${code !== "DISABLED" ? `<button type="button" class="text-button deepseek-retry" data-balance-refresh${loading ? " disabled" : ""}>重试</button>` : ""}
  </div>`;
  return `${headMarkup()}
    <p class="deepseek-state-title">${escapeHtml(titles[code] || "余额读取失败")}</p>
    <p class="deepseek-state-text">${escapeHtml(payload.error || fallback[code] || "请稍后重试。")}</p>
    ${source}
    ${actions}`;
}

function render() {
  const element = host();
  if (!element) return;
  element.classList.toggle("is-loading", loading);
  element.setAttribute("aria-busy", loading ? "true" : "false");
  const data = lastPayload?.ok ? lastPayload : lastGood;
  const failure = lastPayload && !lastPayload.ok ? lastPayload : null;
  if (data) {
    element.innerHTML = balanceMarkup(data, failure);
    return;
  }
  element.innerHTML = failure ? errorMarkup(failure) : loadingMarkup();
}

async function load({ refresh = false, force = false } = {}) {
  if (!host()) return;
  if (loading) {
    if (refresh || force) pendingReload = true;
    return;
  }
  const now = Date.now();
  if (!refresh && !force && now - lastLoadAt < MIN_RELOAD_MS) return;
  lastLoadAt = now;
  loading = true;
  render();
  let payload;
  try {
    payload = await api(`/api/deepseek/balance${refresh ? "?refresh=1" : ""}`);
    if (!payload || typeof payload !== "object") throw new Error("余额接口返回异常");
  } catch (error) {
    payload = {
      ok: false, code: "LOCAL", error: error.message || "余额读取失败",
      source: lastPayload?.source || lastGood?.source || "",
      sources: lastPayload?.sources || lastGood?.sources || {},
    };
  } finally {
    loading = false;
  }
  lastPayload = payload;
  if (payload.ok) {
    lastGood = payload;
  } else if (["NO_KEY", "AUTH", "DISABLED"].includes(payload.code)) {
    lastGood = null;
  }
  render();
  if (pendingReload) {
    pendingReload = false;
    load({ refresh: true });
  }
}

export function initDeepSeekBalance() {
  const element = host();
  if (!element || initialized) return false;
  initialized = true;
  element.addEventListener("click", (event) => {
    if (event.target?.closest?.("[data-balance-refresh]")) {
      event.preventDefault();
      load({ refresh: true });
      return;
    }
    if (event.target?.closest?.("[data-balance-settings]")) {
      event.preventDefault();
      openSettings();
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden || !workbenchVisible()) return;
    if (Date.now() - lastLoadAt > 30_000) load({ force: true });
  });
  render();
  if (typeof window.setInterval === "function") {
    timer = window.setInterval(() => {
      if (document.hidden || !workbenchVisible()) return;
      if (lastPayload && !lastPayload.ok && ["NO_KEY", "AUTH", "DISABLED"].includes(lastPayload.code)) return;
      load({ force: true });
    }, AUTO_REFRESH_MS);
  }
  return true;
}

export function loadDeepSeekBalance(options = {}) {
  initDeepSeekBalance();
  return load(options);
}

async function openSettings() {
  let info = null;
  try {
    info = await api("/api/deepseek/balance/settings");
  } catch {
    info = null;
  }
  const payload = lastPayload || lastGood || {};
  const sources = info || payload.sources || {};
  const source = info?.source || payload.source || "";
  const enabled = info ? info.enabled !== false : payload.enabled !== false;
  const saved = Boolean(sources.saved);
  const threshold = readAlertThreshold();
  const sourceText = `${escapeHtml(balanceSourceLabel(source))}${saved ? "（优先使用）" : ""}`;
  openDialog("DeepSeek 余额设置", `
    <form id="deepseek-balance-form" class="deepseek-balance-form">
      <p class="field-help">当前来源：<strong>${sourceText}</strong></p>
      <p class="field-help">本机保存的 Key 会用 AES-GCM 加密写入当前库的数据目录（通常是 <code>.torch-os</code>），不进 Obsidian Markdown、备份或导出。未保存时自动使用 <code>DEEPSEEK_API_KEY</code> 环境变量或 DSH 凭据里的同名 Key；它只会被发送到 DeepSeek 官方余额接口，不写日志。</p>
      ${enabled ? `
      <div class="field">
        <label for="deepseek-balance-key">DeepSeek API Key（留空 = 不修改）</label>
        <input id="deepseek-balance-key" type="password" autocomplete="off" spellcheck="false" placeholder="sk-..." />
        <span class="field-help">没有 Key？到 <a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener noreferrer">DeepSeek 开放平台</a> 创建。</span>
      </div>` : '<p class="field-help">当前运行模式不允许保存或读取 DeepSeek Key。</p>'}
      <div class="field">
        <label for="deepseek-balance-alert">余额预警线（0 = 关闭）</label>
        <input id="deepseek-balance-alert" type="number" min="0" step="0.01" value="${threshold || 0}" />
        <span class="field-help">低于或等于这里填的金额时，小组件会显示提醒；预警只在本机计算。</span>
      </div>
      <div class="form-actions">
        <span id="deepseek-balance-feedback" class="status-message" role="status"></span>
        <div class="deepseek-balance-actions">
          ${saved && enabled ? '<button type="button" class="text-button" id="deepseek-balance-clear">清除本机 Key</button>' : ""}
          <button type="button" class="button secondary" id="deepseek-balance-cancel">取消</button>
          ${enabled ? '<button type="submit" class="button primary" id="deepseek-balance-save">保存</button>' : ""}
        </div>
      </div>
    </form>`);

  const dialog = $("#detail-dialog");
  const feedback = $("#deepseek-balance-feedback");
  const setFeedback = (message, tone = "") => {
    if (!feedback) return;
    feedback.textContent = message;
    feedback.className = `status-message ${tone}`.trim();
  };
  $("#deepseek-balance-cancel")?.addEventListener("click", () => dialog?.close());
  $("#deepseek-balance-clear")?.addEventListener("click", async () => {
    setFeedback("正在清除本机 Key…");
    try {
      await api("/api/deepseek/balance/key", { method: "DELETE" });
      dialog?.close();
      await load({ refresh: true });
    } catch (error) {
      setFeedback(error.message, "error");
    }
  });
  $("#deepseek-balance-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const threshold = writeAlertThreshold($("#deepseek-balance-alert")?.value || 0);
    const keyInput = $("#deepseek-balance-key");
    const key = String(keyInput?.value || "").trim();
    if (!key) {
      render();
      dialog?.close();
      return;
    }
    const save = $("#deepseek-balance-save");
    if (save) save.disabled = true;
    setFeedback("正在保存 Key…");
    try {
      await api("/api/deepseek/balance/key", jsonBody("POST", { key }));
      if (keyInput) keyInput.value = "";
      dialog?.close();
      await load({ refresh: true });
    } catch (error) {
      if (save) save.disabled = false;
      setFeedback(error.message, "error");
    }
  });
}
