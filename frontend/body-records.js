import { $, api, changed, confirmAction, escapeHtml, formatDate, jsonBody, renderState, setStatus, state, toast } from "./core.js";
import { shanghaiDay, shiftDay } from "./journal-model.js";

// Local body records (weight/sleep/exercise) for the selected journal day.
// Values stay in the local database: they never become memories or cloud
// context by themselves, and they only appear in local draft reports.

const fieldIds = { weight_kg: "body-weight", sleep_minutes: "body-sleep", exercise_minutes: "body-exercise" };

let record = null;
let busy = false;
let conflict = false;
let loadId = 0;
let historyId = 0;

const available = () => state.config?.capabilities?.body_records === true;
const exportAvailable = () => state.config?.capabilities?.body_export === true;
const summaryAvailable = () => state.config?.capabilities?.body_summary === true;

function fieldValue(id) {
  const raw = $(`#${id}`).value.trim();
  return raw === "" ? null : Number(raw);
}

function draft() {
  return Object.fromEntries(Object.entries(fieldIds).map(([field, id]) => [field, fieldValue(id)]));
}

function normalize(value) {
  return value == null || value === "" ? null : Number(value);
}

export function hasBodyChanges() {
  if (!record) return false;
  const current = draft();
  return Object.keys(fieldIds).some((field) => current[field] !== normalize(record[field]));
}

function fillInputs(value) {
  Object.entries(fieldIds).forEach(([field, id]) => {
    const node = $(`#${id}`);
    if (node) node.value = value?.[field] == null ? "" : String(value[field]);
  });
}

function renderControls() {
  const enabled = available() && Boolean(record) && !busy;
  $("#body-fields").disabled = !enabled;
  $("#body-save").disabled = !enabled || conflict || !hasBodyChanges();
  $("#body-clear").disabled = !enabled || conflict || !record?.recorded;
  $("#body-form").setAttribute("aria-busy", String(busy));
  if (!state.config) $("#body-state").textContent = "正在检查服务能力…";
  else if (!available()) $("#body-state").textContent = "当前服务版本未提供身体记录，请更新并重启本地服务。";
  else if (busy) $("#body-state").textContent = "正在处理本地身体记录…";
  else if (record?.recorded) $("#body-state").textContent = `${record.date} · 已保存 · 第 ${record.revision} 版 · 更新于 ${formatDate(record.updated_at)}`;
  else if (record) $("#body-state").textContent = `${record.date} · 尚未记录，可以只填其中一项`;
  else $("#body-state").textContent = "打开每日记录后读取这一天的身体数据。";
  ["#body-export-csv", "#body-export-json"].forEach((selector) => {
    const node = $(selector);
    if (node) node.disabled = !exportAvailable();
  });
  ["#body-summary-md", "#body-summary-json"].forEach((selector) => {
    const node = $(selector);
    if (node) node.disabled = !summaryAvailable();
  });
  const period = $("#body-summary-period");
  if (period) period.disabled = !summaryAvailable();
}

function exportBody(format) {
  if (!exportAvailable()) return;
  location.assign(`/api/body/export.${format}`);
  toast(`已开始下载身体记录导出（${format.toUpperCase()}），文件在浏览器的下载目录。`, "success");
}

function exportBodySummary(format) {
  if (!summaryAvailable()) return;
  const period = $("#body-summary-period")?.value === "week" ? "week" : "month";
  location.assign(`/api/body/summary.${format}?period=${period}`);
  toast(`已开始下载身体记录${period === "week" ? "本周" : "本月"}汇总（${format.toUpperCase()}）；只含天数与分钟合计，不含体重数值。`, "success");
}

function applyRecord(day, value) {
  const payload = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  record = {
    date: typeof payload.date === "string" ? payload.date : day,
    recorded: payload.recorded === true,
    weight_kg: normalize(payload.weight_kg),
    sleep_minutes: normalize(payload.sleep_minutes),
    exercise_minutes: normalize(payload.exercise_minutes),
    revision: Number.isInteger(payload.revision) ? payload.revision : 0,
    updated_at: payload.updated_at || null,
  };
  conflict = false;
  fillInputs(record);
}

export async function loadBodyRecord(day) {
  const request = ++loadId;
  record = null;
  conflict = false;
  fillInputs(null);
  if (!available()) {
    renderControls();
    return false;
  }
  busy = true;
  renderControls();
  setStatus("#body-feedback", "正在读取这一天的身体记录…");
  try {
    const value = await api(`/api/body/entries/${day}`);
    if (request !== loadId) return false;
    applyRecord(day, value);
    setStatus("#body-feedback", record.recorded ? "已读取本地身体记录。" : "这一天还没有身体记录，填写后点击保存。", "neutral");
    return true;
  } catch (error) {
    if (request !== loadId) return false;
    record = null;
    setStatus("#body-feedback", `身体记录暂时无法读取：${error.message} 可点击“重新读取已保存版本”重试。`, "error");
    return false;
  } finally {
    if (request === loadId) {
      busy = false;
      renderControls();
    }
  }
}

function historyLine(item) {
  const parts = [];
  if (item.weight_kg != null) parts.push(`体重 ${item.weight_kg} kg`);
  if (item.sleep_minutes != null) {
    const minutes = Number(item.sleep_minutes);
    parts.push(`睡眠 ${Math.floor(minutes / 60)} 小时${minutes % 60 ? ` ${minutes % 60} 分` : ""}`);
  }
  if (item.exercise_minutes != null) parts.push(`运动 ${item.exercise_minutes} 分钟`);
  return parts.join(" · ") || "已保存（没有数值字段）";
}

export async function loadBodyHistory() {
  const request = ++historyId;
  const container = $("#body-history");
  const button = $("#body-history-refresh");
  if (!container) return;
  if (!available()) { container.innerHTML = ""; if (button) button.disabled = true; return; }
  if (button) button.disabled = true;
  try {
    const today = shanghaiDay();
    const result = await api(`/api/body/entries?from_date=${shiftDay(today, -29)}&to_date=${today}`);
    if (request !== historyId) return;
    const entries = Array.isArray(result?.entries) ? result.entries : [];
    if (!entries.length) return renderState(container, "empty", "最近 30 天还没有身体记录。需要时在上方填写一项并保存。");
    const coverage = { weight: 0, sleep: 0, exercise: 0 };
    entries.forEach((item) => {
      if (item.weight_kg != null) coverage.weight += 1;
      if (item.sleep_minutes != null) coverage.sleep += 1;
      if (item.exercise_minutes != null) coverage.exercise += 1;
    });
    const summary = `<p class="meta" id="body-history-summary">最近 30 天：${escapeHtml(entries.length)} 天有记录（体重 ${escapeHtml(coverage.weight)} · 睡眠 ${escapeHtml(coverage.sleep)} · 运动 ${escapeHtml(coverage.exercise)}）</p>`;
    container.innerHTML = summary + [...entries].sort((a, b) => String(b.date).localeCompare(String(a.date)))
      .map((item) => `<article class="journal-history-row"><div><strong>${escapeHtml(item.date)}</strong><p class="meta">${escapeHtml(historyLine(item))}</p></div><button class="small-button" data-journal-day="${escapeHtml(item.date)}" aria-label="打开 ${escapeHtml(item.date)} 的记录">查看记录</button></article>`).join("");
  } catch (error) {
    if (request === historyId) renderState(container, "error", error.message, "body-history");
  } finally {
    if (request === historyId && button) button.disabled = false;
  }
}

async function saveBody(event) {
  event.preventDefault();
  if (busy || !record || conflict || !hasBodyChanges()) return;
  const values = draft();
  if (Object.values(values).some((value) => value !== null && !Number.isFinite(value))) {
    setStatus("#body-feedback", "请检查输入：体重、睡眠和运动都需要是有效数字。", "error");
    return;
  }
  busy = true;
  renderControls();
  setStatus("#body-feedback", "正在保存到本地数据库…");
  try {
    const value = await api(`/api/body/entries/${record.date}`, jsonBody("PUT", { ...values, revision: record.revision }));
    applyRecord(record.date, value);
    setStatus("#body-feedback", value.recorded ? "身体记录已保存到本地。" : "已清除这一天的身体记录。", "success");
    changed();
    loadBodyHistory();
  } catch (error) {
    if (error.status === 409) {
      conflict = true;
      setStatus("#body-feedback", `${error.message} 当前输入已保留；请先复制需要的内容，再点击“重新读取已保存版本”。`, "error");
    } else {
      setStatus("#body-feedback", `${error.message} 当前输入已保留，可以重试。`, "error");
    }
  } finally {
    busy = false;
    renderControls();
  }
}

async function clearBody() {
  if (busy || !record?.recorded || conflict) return;
  const confirmed = await confirmAction({
    title: "清除这一天的身体记录？",
    message: "将删除这一天的体重、睡眠和运动三个字段。已确认记忆、每日记录文字和任务都不会受影响。",
    confirmLabel: "清除身体记录",
    cancelLabel: "保留记录",
  });
  if (!confirmed) return;
  busy = true;
  renderControls();
  setStatus("#body-feedback", "正在清除本地身体记录…");
  try {
    const value = await api(`/api/body/entries/${record.date}`, jsonBody("PUT", {
      weight_kg: null, sleep_minutes: null, exercise_minutes: null, revision: record.revision,
    }));
    applyRecord(record.date, value);
    setStatus("#body-feedback", "已清除这一天的身体记录。", "success");
    changed();
    loadBodyHistory();
  } catch (error) {
    if (error.status === 409) conflict = true;
    setStatus("#body-feedback", error.status === 409 ? `${error.message} 当前显示未变；请先点击“重新读取已保存版本”。` : `${error.message} 未清除，可以重试。`, "error");
  } finally {
    busy = false;
    renderControls();
  }
}

export function initBodyRecords() {
  $("#body-form").addEventListener("submit", saveBody);
  $("#body-clear").addEventListener("click", clearBody);
  $("#body-form").addEventListener("input", renderControls);
  $("#body-form").addEventListener("change", renderControls);
  document.addEventListener("data-changed", renderControls);
  // Capability/config can arrive after the journal page first rendered (for
  // example when the page is opened directly at #journal). Reload once the
  // service confirms support, but never clobber an already loaded record or
  // an unsaved draft.
  document.addEventListener("config-changed", () => {
    renderControls();
    loadBodyHistory();
    const day = $("#journal-date")?.value;
    if (available() && day && !record && !busy) loadBodyRecord(day);
  });
  const refresh = $("#body-history-refresh");
  if (refresh) refresh.addEventListener("click", loadBodyHistory);
  $("#body-export-csv")?.addEventListener("click", () => exportBody("csv"));
  $("#body-export-json")?.addEventListener("click", () => exportBody("json"));
  $("#body-summary-md")?.addEventListener("click", () => exportBodySummary("md"));
  $("#body-summary-json")?.addEventListener("click", () => exportBodySummary("json"));
  // 工作台 2.0：日记模块换天时，身体面板跟着走（有未保存草稿就静默不覆盖）。
  document.addEventListener("journal-day-changed", (event) => {
    const day = event?.detail?.day;
    if (!day || hasBodyChanges()) return;
    if (available()) loadBodyRecord(day);
  });
  // 表单退役后由身体模块自己守未保存草稿的离开提醒。
  window.addEventListener("beforeunload", (event) => {
    if (hasBodyChanges() || busy) { event.preventDefault(); event.returnValue = ""; }
  });
  loadBodyHistory();
  renderControls();
}
