// 工作台「列车追踪」小组件：独立于恋爱系统。
// 关注列表是用户自己添加的车次号；经停时刻来自本地 .torch-os/train-tracker/timetable.json，
// 也可以在弹窗里手动从 12306 官方接口同步一次（失败保留本地数据）。
import { $, api, confirmAction, escapeHtml, jsonBody, openDialog, setStatus, toast } from "./core.js";
import { shanghaiDay } from "./journal-model.js";
import {
  TRAIN_WATCH_KEY, addWatchCode, cardModel, formatCountdown, normalizeWatch,
  normalizeTrainCode, removeWatchCode, selectWatchCode,
} from "./train-tracker-model.js";

const CARD_ID = "workbench-train-tracker";
const REFRESH_MS = 60 * 1000;

let watch = { codes: [], selected: "" };
let statusPayload = null;
let payload = null;
let initialized = false;
let loading = false;
let requestToken = 0;
let timer = 0;
let dialogOpen = false;
let watchError = "";

function storage() {
  try { return window.localStorage; } catch { return null; }
}

function readWatch() {
  try {
    const raw = storage()?.getItem(TRAIN_WATCH_KEY);
    return raw ? JSON.parse(raw) : { codes: [], selected: "" };
  } catch {
    return { codes: [], selected: "" };
  }
}

function writeWatch() {
  try {
    storage()?.setItem(TRAIN_WATCH_KEY, JSON.stringify(watch));
  } catch { /* 隐私模式/配额满：本次页面仍然可用 */ }
}

function selectedCode() {
  return watch.selected || "";
}

function scheduleRefresh() {
  if (timer) window.clearTimeout(timer);
  timer = 0;
  if (document.hidden) return;
  timer = window.setTimeout(() => { timer = 0; loadTrainTrackerWidget(); }, REFRESH_MS);
}

function renderCard(error = "") {
  const card = $(`#${CARD_ID}`);
  if (!card) return;
  const model = cardModel(payload, selectedCode());
  if (error) {
    model.status = "暂时读不到数据";
    model.meta = error;
    model.tone = "unknown";
  }
  const code = $("#train-tracker-code");
  const status = $("#train-tracker-status");
  const route = $("#train-tracker-route");
  const meta = $("#train-tracker-meta");
  const progress = $("#train-tracker-progress");
  const fill = $("#train-tracker-progress-fill");
  if (code) code.textContent = model.code;
  if (status) {
    status.textContent = model.status;
    status.dataset.tone = model.tone;
  }
  if (route) route.textContent = model.route;
  if (meta) meta.textContent = model.meta;
  if (progress) progress.setAttribute("aria-valuenow", String(Math.round(model.progress)));
  if (fill) fill.style.width = `${model.progress}%`;
  card.setAttribute("aria-label",
    model.route ? `${model.code} ${model.status}，点击查看` : `${model.code}，点击添加或查看车次`);
  card.classList.toggle("is-loading", loading);
}

async function loadSelected() {
  const code = selectedCode();
  if (!code) {
    payload = null;
    return;
  }
  try {
    payload = await api(`/api/train-tracker/train?code=${encodeURIComponent(code)}`, { timeoutMs: 15000 });
  } catch (error) {
    payload = { found: false, code, error: error.message };
  }
}

export async function loadTrainTrackerWidget() {
  if (!initialized) return;
  const token = ++requestToken;
  loading = true;
  watch = normalizeWatch(readWatch());
  let error = "";
  try {
    statusPayload = await api("/api/train-tracker/status", { timeoutMs: 15000 });
  } catch (problem) {
    statusPayload = null;
    error = problem.message;
  }
  if (token !== requestToken) return;
  if (error) payload = null;
  else await loadSelected();
  if (token !== requestToken) return;
  loading = false;
  renderCard(error);
  scheduleRefresh();
}

function dialogHtml() {
  const chips = watch.codes.length
    ? watch.codes.map((code) => `
      <button type="button" class="train-chip${code === watch.selected ? " active" : ""}"
        data-train-select="${escapeHtml(code)}" aria-pressed="${code === watch.selected}">${escapeHtml(code)}</button>`).join("")
    : '<span class="meta">还没有关注的车次</span>';
  const selected = selectedCode();
  const train = payload?.train;
  const realtime = payload?.realtime;
  const from = train?.from_station || "";
  const to = train?.to_station || "";
  let detail = "";
  if (!selected) {
    detail = `<div class="state-message train-dialog-empty"><strong>先加一趟车</strong>
      <p>输入车次号即可；有本地时刻表就能看到经停站和运行区间。没有时刻表时，可以在下面手动从 12306 同步。</p></div>`;
  } else if (!payload) {
    detail = '<p class="field-help">正在读取车次…</p>';
  } else if (!payload.found) {
    detail = `<div class="state-message train-dialog-empty"><strong>${escapeHtml(selected)} 还没有本地时刻表</strong>
      <p>${escapeHtml(payload.error || "可以导入时刻表 JSON，或在下面填写起止站后从 12306 同步。")}</p></div>`;
  } else {
    const tone = realtime?.status || "unknown";
    const stops = (train.stops || []).map((stop) => {
      const current = realtime?.current_station === stop.station;
      const next = realtime?.next_station === stop.station;
      const times = [stop.arrive, stop.depart].filter(Boolean).join(" / ");
      return `<li class="${current ? "current" : next ? "next" : ""}">
        <span class="train-stop-time">${escapeHtml(times || "—")}</span>
        <span class="train-stop-dot" aria-hidden="true"></span>
        <strong>${escapeHtml(stop.station)}</strong>
        ${current ? "<em>当前</em>" : next ? "<em>下一站</em>" : ""}
      </li>`;
    }).join("");
    const countdown = formatCountdown(realtime?.countdown_minutes);
    detail = `<article class="train-detail">
      <header class="train-detail-head">
        <div><span class="eyebrow">TRAIN</span><h3>${escapeHtml(train.train_code)}</h3>
          <p class="meta">${escapeHtml(train.from_station)} → ${escapeHtml(train.to_station)} · ${escapeHtml(train.depart_time)}–${escapeHtml(train.arrive_time)}</p></div>
        <span class="train-source">${train.source === "12306" ? "12306 同步" : "本地时刻表"}</span>
      </header>
      <div class="train-live train-tone-${escapeHtml(tone)}">
        <div><span class="train-status-pill">${escapeHtml(realtime?.status_text || "状态未知")}</span>
          <strong>${escapeHtml(realtime?.message || "")}</strong></div>
        ${countdown ? `<span class="meta">还有 ${escapeHtml(countdown)}</span>` : ""}
        <div class="train-progress"><i data-train-progress="${Math.max(0, Math.min(100, Number(realtime?.progress_pct) || 0))}"></i></div>
      </div>
      <ol class="train-stop-list">${stops}</ol>
      <p class="field-help">状态按本地时刻表和当前时间推算，不是 12306 实时位置。</p>
    </article>`;
  }
  return `<div class="train-dialog">
    <div class="train-dialog-chips" role="group" aria-label="关注的车次">${chips}</div>
    <form id="train-add-form" class="train-add-row">
      <label class="visually-hidden" for="train-add-input">添加车次</label>
      <input id="train-add-input" maxlength="6" placeholder="输入车次，如 G1" autocomplete="off" />
      <button class="button primary" type="submit">添加车次</button>
    </form>
    ${watchError ? `<p class="inline-error">${escapeHtml(watchError)}</p>` : ""}
    ${detail}
    <div class="train-dialog-actions">
      ${selected ? '<button id="train-remove-btn" class="text-button" type="button">从关注列表移除</button>' : ""}
      <input id="train-import-file" type="file" accept=".json,application/json" hidden aria-label="导入列车时刻表 JSON" />
      <button id="train-import-btn" class="button secondary" type="button">导入时刻表 JSON</button>
      <span class="meta">本地 ${Number(statusPayload?.count) || 0} 趟车次</span>
    </div>
    <p class="field-help">导入会替换本地时刻表；只想补一趟车，用下面的 12306 同步。</p>
    <details class="train-sync" ${selected && !payload?.found ? "open" : ""}>
      <summary>从 12306 同步时刻表</summary>
      <p class="field-help">只在你点击时请求 12306 官方接口；可能被限流或改版，失败时保留本地时刻表。首次同步新车型时请填写起止站。</p>
      <div class="train-sync-grid">
        <label>日期<input id="train-sync-date" type="date" value="${escapeHtml(shanghaiDay())}" /></label>
        <label>出发站<input id="train-sync-from" maxlength="60" value="${escapeHtml(from)}" placeholder="如 北京南" /></label>
        <label>到达站<input id="train-sync-to" maxlength="60" value="${escapeHtml(to)}" placeholder="如 上海虹桥" /></label>
      </div>
      <button id="train-sync-btn" class="button secondary" type="button" ${selected ? "" : "disabled"}>同步这趟车</button>
    </details>
    <p id="train-dialog-feedback" class="status-message" role="status" aria-live="polite"></p>
  </div>`;
}

function renderDialog() {
  openDialog("列车追踪", dialogHtml());
  $("#detail-dialog")?.classList.add("train-tracker-mode");
  bindDialog();
}

async function refreshDialog(message = "", tone = "success") {
  await loadTrainTrackerWidget();
  if (!dialogOpen) return;
  renderDialog();
  if (message) setStatus("#train-dialog-feedback", message, tone);
}

function bindDialog() {
  const content = $("#dialog-content");
  content?.querySelectorAll?.("[data-train-progress]").forEach((bar) => {
    const value = Math.max(0, Math.min(100, Number(bar.dataset.trainProgress) || 0));
    bar.style.width = `${value}%`;
  });
  content?.querySelectorAll?.("[data-train-select]").forEach((button) => {
    button.addEventListener("click", async () => {
      watch = selectWatchCode(watch, button.dataset.trainSelect || "");
      watchError = "";
      writeWatch();
      await refreshDialog();
    });
  });
  $("#train-add-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const next = addWatchCode(watch, $("#train-add-input")?.value || "");
    if (next.error) {
      watchError = next.error;
      setStatus("#train-dialog-feedback", next.error, "error");
      return;
    }
    watch = normalizeWatch(next);
    watchError = "";
    writeWatch();
    await refreshDialog(`已添加 ${watch.selected}`);
  });
  $("#train-import-btn")?.addEventListener("click", () => $("#train-import-file")?.click());
  $("#train-import-file")?.addEventListener("change", async () => {
    const input = $("#train-import-file");
    const file = input?.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    setStatus("#train-dialog-feedback", "正在导入时刻表…");
    try {
      const result = await api("/api/train-tracker/timetable", {
        method: "POST", body: form, timeoutMs: 30000,
      });
      input.value = "";
      await refreshDialog(result.message || "时刻表已更新。");
    } catch (error) {
      setStatus("#train-dialog-feedback", `导入失败：${error.message}`, "error");
    }
  });
  $("#train-sync-btn")?.addEventListener("click", async () => {
    if (!selectedCode()) return;
    const date = String($("#train-sync-date")?.value || shanghaiDay());
    const from = String($("#train-sync-from")?.value || "").trim();
    const to = String($("#train-sync-to")?.value || "").trim();
    const button = $("#train-sync-btn");
    if (button) button.disabled = true;
    setStatus("#train-dialog-feedback", "正在向 12306 请求这趟车的经停站…");
    try {
      const result = await api("/api/train-tracker/sync", {
        ...jsonBody("POST", {
          code: selectedCode(), from_station: from, to_station: to, date,
        }),
        timeoutMs: 60000,
      });
      await refreshDialog(result.message || "已从 12306 同步。");
      toast(`${selectedCode()} 已同步`);
      scheduleRefresh();
    } catch (error) {
      setStatus("#train-dialog-feedback", `同步失败：${error.message}`, "error");
      if (button) button.disabled = false;
    }
  });
  $("#train-remove-btn")?.addEventListener("click", async () => {
    const code = selectedCode();
    if (!code) return;
    const confirmed = await confirmAction({
      title: `移除 ${code}？`,
      message: "只是从关注列表移除，本地时刻表不会被删除。",
      confirmLabel: "移除",
    });
    if (!confirmed) return;
    watch = removeWatchCode(watch, code);
    watchError = "";
    writeWatch();
    await refreshDialog(`已移除 ${code}`);
  });
}

async function openTrainTracker() {
  if (dialogOpen) return;
  dialogOpen = true;
  renderDialog();
  await loadTrainTrackerWidget();
  if (dialogOpen) renderDialog();
}

export function initTrainTrackerWidget() {
  const card = $(`#${CARD_ID}`);
  if (!card || initialized) return;
  initialized = true;
  watch = normalizeWatch(readWatch());
  card.addEventListener("click", (event) => {
    if (event.target?.closest?.(".widget-drag-handle")) return;
    openTrainTracker();
  });
  card.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    if (event.target?.closest?.(".widget-drag-handle")) return;
    event.preventDefault();
    openTrainTracker();
  });
  $("#detail-dialog")?.addEventListener("close", () => {
    if (!dialogOpen) return;
    dialogOpen = false;
    $("#detail-dialog")?.classList.remove("train-tracker-mode");
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadTrainTrackerWidget();
  });
  loadTrainTrackerWidget();
}
