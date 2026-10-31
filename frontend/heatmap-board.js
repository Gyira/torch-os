// 数据看板控制器（2026-10-31）：横幅「回看」按钮打开的弹窗。
//
// 纯渲染在 heatmap-board-model.js（node 直测）；这里只负责取数、视图切换、
// 日/周/月/年导航与 .obsidian 存档状态展示。

import { $, api, escapeHtml } from "./core.js";
import { shanghaiDay, shiftDay } from "./journal-model.js";
import { DEFAULT_HEAT_COLOR } from "./banner.js";
import { heatmapBoardBody, heatInkForColor } from "./heatmap-board-model.js";

const board = {
  view: "day",
  anchor: shanghaiDay(),
  data: null,
  request: 0,
  loading: false,
  initialized: false,
};

function todayKey() {
  return shanghaiDay();
}

function setStatus(message, tone = "neutral") {
  const node = $("#heatmap-status");
  if (!node) return;
  node.textContent = message || "";
  node.className = `status-message ${tone}`;
  node.hidden = !message;
}

function setLoading(value) {
  board.loading = Boolean(value);
  const dialog = $("#heatmap-dialog");
  if (dialog) dialog.classList.toggle("is-loading", board.loading);
}

function syncToolbar() {
  const dialog = $("#heatmap-dialog");
  if (!dialog) return;
  const data = board.data;
  const period = $("#heatmap-period");
  if (period) period.textContent = data?.period?.label || "正在读取…";
  for (const button of dialog.querySelectorAll("[data-heatmap-view]")) {
    const active = button.dataset.heatmapView === board.view;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  }
  const today = todayKey();
  const anchor = board.anchor || today;
  const next = $("#heatmap-next");
  const prev = $("#heatmap-prev");
  const todayButton = $("#heatmap-today");
  if (prev) prev.disabled = board.loading;
  if (todayButton) todayButton.disabled = anchor === today || board.loading;
  if (next) {
    let blocked = false;
    if (board.view === "day") blocked = anchor >= today;
    else if (board.view === "week") {
      const date = new Date(`${anchor}T00:00:00Z`);
      const end = new Date(date.getTime() + (7 - ((date.getUTCDay() + 6) % 7) - 1) * 86400000);
      blocked = end.toISOString().slice(0, 10) >= today;
    } else if (board.view === "month") blocked = anchor.slice(0, 7) >= today.slice(0, 7);
    else blocked = anchor.slice(0, 4) >= today.slice(0, 4);
    next.disabled = blocked || board.loading;
  }
}

function renderFooter() {
  const footer = $("#heatmap-foot");
  if (!footer) return;
  const storage = board.data?.storage || {};
  const tone = storage.status === "error" ? "error" : "ok";
  const statusText = {
    written: "已写入快照",
    unchanged: "快照已是最新",
    skipped: storage.reason === "read-only" ? "只读模式，未写入快照" : "未写入快照",
    error: "快照写入失败",
  }[storage.status] || "";
  const path = storage.path || `${storage.root || ".obsidian/torch-os-heatmap"}/`;
  footer.innerHTML = `<div class="heat-foot-rules">
      <p><strong>怎么算</strong>：人生路/知识库/自我档案看当天新建文档数；任务看创建与完成数；习惯按当天完成比例。计数行每 4 篇（项）满格。</p>
      <p><strong>总热度</strong>：其余各行得分的等权平均，任何一行都不会单独把总分拉满。</p>
      ${board.data?.truncated ? "<p class=\"heat-foot-warn\">文件数超过扫描上限，统计可能不完整。</p>" : ""}
    </div>
    <p class="heat-foot-storage" data-tone="${tone}" title="${escapeHtml(path)}"><span aria-hidden="true">▦</span> ${escapeHtml(path)}${statusText ? ` · ${statusText}` : ""}</p>`;
}

function render() {
  const content = $("#heatmap-content");
  const dialog = $("#heatmap-dialog");
  if (!content || !dialog) return;
  const requested = String(board.data?.heat_color || "").trim();
  const color = /^#[0-9a-fA-F]{6}$/.test(requested) ? requested : DEFAULT_HEAT_COLOR;
  dialog.style.setProperty("--heat-color", color);
  dialog.style.setProperty("--heat-on-color", heatInkForColor(color));
  content.innerHTML = heatmapBoardBody(board.data);
  syncToolbar();
  renderFooter();
}

async function load() {
  if (!board.initialized) return;
  const request = ++board.request;
  setLoading(true);
  setStatus("正在读取热力数据…");
  try {
    const data = await api(
      `/api/heatmap/board?view=${encodeURIComponent(board.view)}&day=${encodeURIComponent(board.anchor)}`,
      { timeoutMs: 60000 });
    if (request !== board.request) return;
    board.data = data;
    board.view = data.view || board.view;
    board.anchor = data.anchor || board.anchor;
    setStatus("");
    render();
  } catch (error) {
    if (request !== board.request) return;
    setStatus(`读取失败：${error.message}`, "error");
  } finally {
    if (request === board.request) {
      setLoading(false);
      syncToolbar();
    }
  }
}

function shiftMonth(day, delta) {
  const date = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return day;
  const dayNumber = date.getUTCDate();
  date.setUTCDate(1);
  date.setUTCMonth(date.getUTCMonth() + delta);
  const monthEnd = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate();
  date.setUTCDate(Math.min(dayNumber, monthEnd));
  return date.toISOString().slice(0, 10);
}

function shiftYear(day, delta) {
  const date = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return day;
  const dayNumber = date.getUTCDate();
  const month = date.getUTCMonth();
  date.setUTCDate(1);
  date.setUTCFullYear(date.getUTCFullYear() + delta);
  date.setUTCMonth(month);
  const monthEnd = new Date(Date.UTC(date.getUTCFullYear(), month + 1, 0)).getUTCDate();
  date.setUTCDate(Math.min(dayNumber, monthEnd));
  return date.toISOString().slice(0, 10);
}

function shiftAnchor(direction) {
  const today = todayKey();
  let anchor = board.anchor;
  if (board.view === "day") anchor = shiftDay(anchor, direction);
  else if (board.view === "week") anchor = shiftDay(anchor, direction * 7);
  else if (board.view === "month") anchor = shiftMonth(anchor, direction);
  else anchor = shiftYear(anchor, direction);
  if (anchor > today) anchor = today;
  board.anchor = anchor;
  load();
}

function switchView(view) {
  if (!["day", "week", "month", "year"].includes(view) || view === board.view) return;
  board.view = view;
  load();
}

export function initHeatmapBoard() {
  if (board.initialized) return;
  const dialog = $("#heatmap-dialog");
  const trigger = $("#workbench-banner-review");
  if (!dialog || !trigger) return;
  board.initialized = true;

  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    board.anchor = todayKey();
    board.view = "day";
    board.data = null;
    const content = $("#heatmap-content");
    if (content) content.innerHTML = "";
    const footer = $("#heatmap-foot");
    if (footer) footer.innerHTML = "";
    if (dialog.open) {
      load();
      return;
    }
    dialog.showModal();
    load();
  });
  trigger.addEventListener("dblclick", (event) => event.stopPropagation());
  $("#heatmap-close")?.addEventListener("click", () => dialog.close());
  for (const button of dialog.querySelectorAll("[data-heatmap-view]")) {
    button.addEventListener("click", () => switchView(button.dataset.heatmapView));
  }
  $("#heatmap-prev")?.addEventListener("click", () => shiftAnchor(-1));
  $("#heatmap-next")?.addEventListener("click", () => shiftAnchor(1));
  $("#heatmap-today")?.addEventListener("click", () => {
    board.anchor = todayKey();
    load();
  });
  $("#heatmap-content")?.addEventListener("click", (event) => {
    const dayButton = event.target.closest?.("[data-heatmap-day]");
    if (dayButton) {
      board.view = "day";
      board.anchor = dayButton.dataset.heatmapDay;
      load();
      return;
    }
    const monthButton = event.target.closest?.("[data-heatmap-month]");
    if (monthButton) {
      board.view = "month";
      board.anchor = `${monthButton.dataset.heatmapMonth}-01`;
      load();
    }
  });
  dialog.addEventListener("close", () => {
    board.request += 1;
    board.data = null;
  });
}
