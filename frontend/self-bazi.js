// 个人八字系统（2026-11-01）：命盘 / 五行 / 大运 / AI 解读 / 历史。
// 排盘由后端本地计算；AI 只写解读，不能改四柱。
import { api, confirmAction, escapeHtml, openDialog, toast } from "./core.js";
import { renderMarkdownDocument } from "./pillar-markdown.js";
import {
  TOPICS, baziPageMarkup, chartBoardMarkup, historyMarkup, readingMarkup,
} from "./self-bazi-model.js";

const state = {
  host: null,
  initialized: false,
  overview: null,
  profile: {},
  chart: null,
  readings: [],
  topic: "综合",
  page: "chart",
  busy: false,
};

function renderMarkdown(text) {
  try { return renderMarkdownDocument(String(text || "")).html; }
  catch { return escapeHtml(String(text || "")).replace(/\n/g, "<br>"); }
}

function formPayload(form) {
  const data = new FormData(form);
  return {
    name: String(data.get("name") || "").trim(),
    sex: String(data.get("sex") || "").trim(),
    birth_date: String(data.get("birth_date") || "").trim(),
    birth_time: String(data.get("birth_time") || "").trim(),
    birth_place: String(data.get("birth_place") || "").trim(),
    pillars_manual: String(data.get("pillars_manual") || "").trim(),
    revision: String(state.profile?.revision || ""),
  };
}

function setStatus(message, tone = "") {
  const node = state.host?.querySelector?.("[data-bazi-form-status]");
  if (!node) return;
  node.textContent = message || "";
  node.className = `status-message ${tone}`.trim();
}

function render() {
  const host = state.host;
  if (!host) return;
  host.innerHTML = baziPageMarkup({
    profile: state.profile || {},
    chart: state.chart,
    readings: state.readings,
  });
  const history = host.querySelector(".bazi-history-section");
  const current = host.querySelector(".bazi-readings-section");
  if (state.page === "history") {
    if (history) history.hidden = false;
    if (current) current.hidden = true;
    const historyHost = host.querySelector("[data-bazi-history-host]");
    if (historyHost) historyHost.innerHTML = historyMarkup(state.readings);
  } else if (current) {
    current.hidden = false;
    if (history) history.hidden = true;
  }
  const readingHost = host.querySelector("[data-bazi-reading-host]");
  if (readingHost && state.readings.length) {
    readingHost.innerHTML = readingMarkup(state.readings[0], renderMarkdown);
  }
  host.querySelectorAll("[data-bazi-topic]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.baziTopic === state.topic);
  });
}

async function loadOverview() {
  const overview = await api("/api/self/bazi/overview", { timeoutMs: 60000 });
  state.overview = overview;
  state.profile = overview.profile || {};
  state.chart = overview.chart || null;
  state.readings = Array.isArray(overview.readings) ? overview.readings : [];
}

async function saveProfile(form) {
  if (state.busy) return;
  state.busy = true;
  const button = form.querySelector("[data-bazi-save]");
  if (button) button.disabled = true;
  setStatus("正在保存并排盘…");
  try {
    const result = await api("/api/self/bazi/profile", {
      method: "PUT", body: JSON.stringify(formPayload(form)), timeoutMs: 60000,
    });
    state.profile = result;
    state.chart = result.chart || null;
    toast("命盘已保存。", "success");
    await loadOverview();
    state.page = "chart";
    render();
  } catch (error) {
    setStatus(`保存失败：${error.message}`, "error");
    toast(`保存失败：${error.message}`, "error");
    if (button) button.disabled = false;
  } finally {
    state.busy = false;
  }
}

async function previewChart(form) {
  try {
    const chart = await api("/api/self/bazi/chart", {
      method: "POST", body: JSON.stringify(formPayload(form)), timeoutMs: 60000,
    });
    openDialog("命盘试算（未保存）", `<div class="md-body">${chartBoardMarkup(chart)}</div>`);
  } catch (error) {
    toast(`试算失败：${error.message}`, "error");
  }
}

async function generateReading(topic) {
  if (state.busy) return;
  if (!state.profile?.exists) {
    toast("先保存命盘，再生成解读。", "warning");
    return;
  }
  const ok = await confirmAction({
    title: `生成「${topic}」解读？`,
    message: "会读取你的命盘、自我档案与导师记忆，调用 dsh 生成一篇解读；可能耗时 1–2 分钟，会消耗 token。",
    confirmLabel: "开始生成",
  });
  if (!ok) return;
  state.busy = true;
  const button = state.host?.querySelector?.("[data-bazi-generate]");
  if (button) { button.disabled = true; button.textContent = "解读中…"; }
  try {
    const item = await api("/api/self/bazi/generate", {
      method: "POST", body: JSON.stringify({ confirm: true, topic }),
      timeoutMs: 300000,
    });
    state.topic = item.topic || topic;
    await loadOverview();
    state.page = "chart";
    render();
    toast("解读已生成。", "success");
  } catch (error) {
    toast(`生成失败：${error.message}`, "error");
  } finally {
    state.busy = false;
    if (button) { button.disabled = false; button.textContent = "生成解读"; }
  }
}

async function openReading(name) {
  try {
    const reading = await api(`/api/self/bazi/readings/${encodeURIComponent(name)}`, {
      timeoutMs: 30000,
    });
    state.page = "chart";
    state.readings = [reading, ...state.readings.filter((item) => item.name !== name)];
    render();
    state.host?.querySelector?.(".bazi-readings-section")?.scrollIntoView?.({ block: "start" });
  } catch (error) {
    toast(`打不开这篇解读：${error.message}`, "error");
  }
}

async function deleteReading(name) {
  const ok = await confirmAction({
    title: "删除这篇解读？",
    message: "会移到八字系统的历史目录，不会直接删除。",
    confirmLabel: "删除", danger: true,
  });
  if (!ok) return;
  try {
    await api(`/api/self/bazi/readings/${encodeURIComponent(name)}?confirm=true`, {
      method: "DELETE", timeoutMs: 30000,
    });
    await loadOverview();
    render();
    toast("已移到历史目录。", "info");
  } catch (error) {
    toast(`删除失败：${error.message}`, "error");
  }
}

function editProfile() {
  const wrap = state.host?.querySelector?.(".bazi-form-wrap");
  if (wrap) {
    wrap.open = true;
    wrap.scrollIntoView?.({ behavior: "smooth", block: "start" });
    return;
  }
  state.host?.querySelector?.(".bazi-form")?.scrollIntoView?.({ behavior: "smooth", block: "start" });
}

async function handleClick(event) {
  const save = event.target.closest?.("[data-bazi-save]");
  const form = save?.closest?.("[data-bazi-form]");
  if (save && form) { event.preventDefault(); await saveProfile(form); return; }
  const preview = event.target.closest?.("[data-bazi-preview]");
  if (preview) {
    const current = preview.closest?.("[data-bazi-form]");
    if (current) await previewChart(current);
    return;
  }
  if (event.target.closest?.("[data-bazi-edit]")) { editProfile(); return; }
  const topic = event.target.closest?.("[data-bazi-topic]");
  if (topic) {
    state.topic = topic.dataset.baziTopic || "综合";
    render();
    return;
  }
  if (event.target.closest?.("[data-bazi-generate]")) {
    await generateReading(state.topic);
    return;
  }
  if (event.target.closest?.("[data-bazi-history]")) {
    state.page = "history";
    render();
    return;
  }
  if (event.target.closest?.("[data-bazi-back]")) {
    state.page = "chart";
    render();
    return;
  }
  const open = event.target.closest?.("[data-bazi-open]");
  if (open) { await openReading(open.dataset.baziOpen); return; }
  const remove = event.target.closest?.("[data-bazi-delete]");
  if (remove) { await deleteReading(remove.dataset.baziDelete); return; }
}

function bindEvents() {
  state.host?.addEventListener?.("submit", (event) => {
    const form = event.target.closest?.("[data-bazi-form]");
    if (!form) return;
    event.preventDefault();
    saveProfile(form).catch((error) => toast(error.message, "error"));
  });
  state.host?.addEventListener?.("click", (event) => {
    handleClick(event).catch((error) => toast(error.message, "error"));
  });
}

export function initSelfBazi(host) {
  if (state.initialized) return;
  state.host = host;
  state.initialized = true;
  bindEvents();
}

export async function loadSelfBazi(host, sub = "chart") {
  if (!host) return;
  if (!state.initialized || state.host !== host) {
    state.host = host;
    if (!state.initialized) { state.initialized = true; bindEvents(); }
  }
  state.page = sub === "history" ? "history" : "chart";
  if (!state.overview) host.innerHTML = `<div class="self-loading">正在读取八字系统…</div>`;
  try {
    await loadOverview();
  } catch (error) {
    host.innerHTML = `<div class="self-error"><p>读不到八字系统：${escapeHtml(error.message)}</p></div>`;
    return;
  }
  render();
  if (sub === "readings") {
    host.querySelector(".bazi-readings-section")?.scrollIntoView?.({ block: "start" });
  }
}
