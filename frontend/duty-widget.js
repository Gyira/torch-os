// 工作台「值班」小组件：只读排班表里的上班/休息和当天值乘车次。
// 数据来自恋爱系统的值班日历 API（同一份排班表）；编辑备注仍去「生活运营 → 恋爱系统 → 值班日历」。
import { $, api, escapeHtml, openDialog } from "./core.js";
import { dutyCardModel, dutyDialogRows } from "./duty-model.js";

const CARD_ID = "workbench-duty";
const REFRESH_MS = 5 * 60 * 1000;
const UPCOMING_URL = "/api/love/schedule/upcoming?days=31";

let payload = null;
let initialized = false;
let loading = false;
let requestToken = 0;
let timer = 0;
let dialogOpen = false;

function scheduleRefresh() {
  if (timer) window.clearTimeout(timer);
  timer = 0;
  if (document.hidden) return;
  timer = window.setTimeout(() => { timer = 0; loadDutyWidget(); }, REFRESH_MS);
}

function renderCard() {
  const card = $(`#${CARD_ID}`);
  if (!card) return;
  const model = dutyCardModel(payload);
  const error = payload?.error && !payload?.found ? payload.error : "";
  const waiting = loading && !payload;
  const state = waiting ? "正在读取…" : error ? "暂时读不到" : model.state;
  const trainText = waiting ? "正在读取排班表…" : error || model.trainText;
  const nextText = waiting ? "" : error ? "检查排班表或恋爱系统是否可用" : model.nextText;
  const date = $("#duty-card-date");
  const stateNode = $("#duty-card-state");
  const train = $("#duty-card-train");
  const strip = $("#duty-card-strip");
  const next = $("#duty-card-next");
  if (date) {
    date.textContent = loading && !payload
      ? "正在读取…"
      : (error || !payload?.found) ? "值班数据" : model.dateLabel;
  }
  if (stateNode) {
    stateNode.textContent = state;
    stateNode.dataset.tone = error ? "out" : model.tone;
  }
  if (train) {
    train.textContent = trainText;
    train.title = trainText;
  }
  if (next) {
    next.textContent = nextText;
    next.title = nextText;
  }
  if (strip) {
    strip.innerHTML = model.strip.map((day) => {
      const tone = day.inRange ? (day.rest ? "is-rest" : "is-work") : "is-out";
      const classes = ["duty-strip-day", tone];
      if (day.isToday) classes.push("is-today");
      return `<span class="${classes.join(" ")}" title="${escapeHtml(day.label)}">
        <strong>${escapeHtml(day.day)}</strong>
        <small>${day.inRange ? (day.rest ? "休" : "班") : "—"}</small>
      </span>`;
    }).join("");
  }
  card.classList.toggle("is-loading", loading);
  card.setAttribute("aria-label",
    `${model.dateLabel} ${state}${model.train ? `，值乘 ${model.train}` : ""}；${nextText}；点击查看值班日历`);
}

function dialogHtml() {
  const rows = dutyDialogRows(payload);
  const error = payload?.error && !payload?.found ? payload.error : "";
  const list = rows.map((day) => {
    const tone = day.inRange ? (day.rest ? "is-rest" : "is-work") : "is-out";
    return `<li class="duty-dialog-row ${tone}">
      <span class="duty-dialog-date"><strong>${escapeHtml(day.monthDay)}</strong><small>${escapeHtml(day.weekday)}</small></span>
      <span class="duty-dialog-state">${escapeHtml(day.state)}</span>
      <span class="duty-dialog-duty" title="${escapeHtml(day.duty || "")}">${escapeHtml(day.duty || "—")}</span>
      ${day.note ? `<small class="duty-dialog-note">${escapeHtml(day.note)}</small>` : ""}
    </li>`;
  }).join("");
  return `<div class="duty-dialog">
    ${error ? `<p class="inline-error">${escapeHtml(error)}</p>` : ""}
    ${rows.length ? `<ol class="duty-dialog-list">${list}</ol>` : '<p class="field-help">正在读取排班表…</p>'}
    <p class="field-help">只读：上班/休息与值乘车次来自排班表；备注在「生活运营 → 恋爱系统 → 值班日历」里编辑。</p>
  </div>`;
}

function renderDialog() {
  openDialog("值班日历", dialogHtml());
  $("#detail-dialog")?.classList.add("duty-widget-mode");
}

export async function loadDutyWidget() {
  if (!initialized) return;
  const token = ++requestToken;
  loading = true;
  renderCard();
  try {
    payload = await api(UPCOMING_URL, { timeoutMs: 15000 });
  } catch (error) {
    payload = { found: false, error: error.message, days: [] };
  }
  if (token !== requestToken) return;
  loading = false;
  renderCard();
  if (dialogOpen) renderDialog();
  scheduleRefresh();
}

function openDutyWidget() {
  if (dialogOpen) return;
  dialogOpen = true;
  renderDialog();
  loadDutyWidget();
}

export function initDutyWidget() {
  const card = $(`#${CARD_ID}`);
  if (!card || initialized) return;
  initialized = true;
  card.addEventListener("click", (event) => {
    if (event.target?.closest?.(".widget-drag-handle")) return;
    openDutyWidget();
  });
  card.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    if (event.target?.closest?.(".widget-drag-handle")) return;
    event.preventDefault();
    openDutyWidget();
  });
  $("#detail-dialog")?.addEventListener("close", () => {
    if (!dialogOpen) return;
    dialogOpen = false;
    $("#detail-dialog")?.classList.remove("duty-widget-mode");
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadDutyWidget();
  });
  loadDutyWidget();
}
