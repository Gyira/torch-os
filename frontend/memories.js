import { $, api, escapeHtml, jsonBody, renderState, setStatus, openDialog, setBusy, toast, changed, confirmAction, formatDate, sourceLocationMarkup, state } from "./core.js";
import { cleanMemoryText, memoryDisplayTitle } from "./memory-text.js";

let statusFilter = "candidate";
let requestId = 0;
let memories = [];
let searchQuery = "";
let sourceFilter = "";
let monthFilter = "";
const busyIds = new Set();
const selectedIds = new Set();
const statusLabels = { candidate: "待确认", active: "已确认", rejected: "已拒绝", archived: "已归档" };
const kinds = { fact: "事实", experience: "经历", belief: "信念", goal: "目标", preference: "偏好", note: "记录" };
const statusLabel = (status) => statusLabels[status] || status;
const usageAvailable = () => state.config?.capabilities?.memory_usage === true;
const exportAvailable = () => state.config?.capabilities?.memory_export === true;
const deleteAvailable = () => state.config?.capabilities?.memory_delete === true;
const batchAvailable = () => state.config?.capabilities?.memory_batch === true;
const tidyAvailable = () => state.config?.capabilities?.candidate_tidy === true;

function renderExportControls() {
  const available = exportAvailable();
  ["#export-memories-md", "#export-memories-json"].forEach((selector) => {
    const node = $(selector);
    if (node) node.disabled = !available;
  });
  const tidy = $("#tidy-candidates");
  if (tidy) tidy.disabled = !tidyAvailable();
}

function exportMemories(format) {
  if (!exportAvailable()) return;
  location.assign(`/api/export/memories.${format}`);
  toast(`已开始下载记忆导出（${format === "md" ? "Markdown" : "JSON"}），文件在浏览器的下载目录。`, "success");
}

function renderMemory(memory) {
  const id = escapeHtml(memory.id);
  const select = memory.status === "candidate" && batchAvailable()
    ? `<label class="check-label memory-select"><input type="checkbox" data-memory-select="${id}" ${selectedIds.has(memory.id) ? "checked" : ""} aria-label="选择《${escapeHtml(memory.title || "未命名记忆")}》用于批量审核" />选择</label>`
    : "";
  const button = (status, label, className = "") => `<button class="small-button ${className}" data-memory-action="${status}" data-memory-id="${id}">${label}</button>`;
  const statusActions = memory.status === "candidate" ? button("active", "确认", "confirm") + button("rejected", "拒绝", "reject") : button("candidate", memory.status === "active" ? "撤回确认" : "重新审核");
  const archive = memory.status !== "archived" ? button("archived", "归档") : "";
  const usage = usageAvailable() ? `<button class="small-button" data-memory-usage="${id}">使用记录</button>` : "";
  const remove = deleteAvailable() ? `<button class="small-button reject" data-memory-delete="${id}">删除</button>` : "";
  return `<article class="memory-card" data-memory-card="${id}"><div class="memory-content"><div class="memory-heading">${select}<span class="eyebrow">${escapeHtml(kinds[memory.kind] || memory.kind || "记忆")}</span><span class="badge ${Object.hasOwn(statusLabels, memory.status) ? memory.status : ""}">${escapeHtml(statusLabel(memory.status))}</span></div><h3>${escapeHtml(memoryDisplayTitle(memory))}</h3><p class="preserve-lines">${escapeHtml(cleanMemoryText(memory.content) || memory.content)}</p><p class="meta">${memory.confidence ? `置信度：${escapeHtml(memory.confidence)} · ` : ""}更新：${escapeHtml(formatDate(memory.updated_at || memory.created_at))}</p><details class="memory-source"><summary>查看来源</summary><p class="preserve-lines">${escapeHtml(memory.source_quote || memory.content || "暂无来源片段")}</p><code>${escapeHtml(memory.source_path || "来源未标注")}</code>${sourceLocationMarkup(memory)}${memory.document_id || memory.source_document_id ? `<button class="text-button" data-document-id="${escapeHtml(memory.document_id || memory.source_document_id)}">展开资料原文</button>` : ""}</details><span class="memory-feedback" data-memory-feedback role="status"></span></div><div class="memory-actions">${statusActions}${archive}<button class="small-button" data-memory-edit="${id}">编辑</button><button class="small-button" data-memory-history="${id}">历史</button>${usage}${remove}</div></article>`;
}

function matchesFilters(memory) {
  if (sourceFilter && String(memory.source_path || "") !== sourceFilter) return false;
  if (monthFilter === "unknown") {
    if (memory.record_month) return false;
  } else if (monthFilter && memory.record_month !== monthFilter) {
    return false;
  }
  const terms = searchQuery.toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return true;
  const haystack = [memory.title, memory.content, memory.source_path, memory.source_quote]
    .map((value) => String(value ?? "").toLowerCase())
    .join("\n");
  return terms.every((term) => haystack.includes(term));
}

function renderFilterOptions() {
  const sources = [...new Set(memories.map((memory) => String(memory.source_path || "")).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right, "zh-Hans-CN"));
  if (sourceFilter && !sources.includes(sourceFilter)) sourceFilter = "";
  const sourceSelect = $("#memory-source-filter");
  if (sourceSelect) {
    sourceSelect.innerHTML = '<option value="">全部来源</option>' +
      sources.map((path) => `<option value="${escapeHtml(path)}">${escapeHtml(path)}</option>`).join("");
    sourceSelect.value = sourceFilter;
  }
  const months = [...new Set(memories.map((memory) => memory.record_month).filter(Boolean))].sort().reverse();
  if (monthFilter && monthFilter !== "unknown" && !months.includes(monthFilter)) monthFilter = "";
  const monthSelect = $("#memory-month-filter");
  if (monthSelect) {
    monthSelect.innerHTML = '<option value="">全部月份</option>' +
      months.map((month) => `<option value="${escapeHtml(month)}">${escapeHtml(month)}</option>`).join("") +
      '<option value="unknown">未标注日期</option>';
    monthSelect.value = monthFilter;
  }
}

function visibleMemories() {
  return memories.filter(matchesFilters);
}

function renderList() {
  const container = $("#memory-list");
  const label = statusFilter === "all" ? "记忆" : statusLabel(statusFilter);
  if (!memories.length) {
    renderState(container, "empty", statusFilter === "candidate" ? "还没有待确认内容。可以先在“记录与资料”写一条记录，或扫描 Vault。" : `当前没有${statusFilter === "all" ? "" : label}记忆。`);
    renderBatchBar();
    return;
  }
  const visible = visibleMemories();
  if (!visible.length) {
    renderState(container, "empty", `没有符合筛选的内容（当前${label}共 ${memories.length} 条）。`);
    container.innerHTML += '<div class="panel-footer"><button class="text-button" data-memory-search-clear>清除筛选</button></div>';
    renderBatchBar();
    return;
  }
  container.innerHTML = visible.map(renderMemory).join("");
  renderBatchBar();
}

function renderBatchBar() {
  const bar = $("#memory-batch-bar");
  if (!bar) return;
  const candidates = memories.filter((memory) => memory.status === "candidate").length;
  const show = batchAvailable() && candidates > 0;
  bar.hidden = !show;
  if (!show) return;
  const count = selectedIds.size;
  $("#memory-batch-count").textContent = `已选 ${count} 条`;
  ["#memory-batch-confirm", "#memory-batch-reject"].forEach((selector) => {
    const node = $(selector);
    if (node) node.disabled = count === 0;
  });
  const clear = $("#memory-batch-clear");
  if (clear) clear.disabled = count === 0;
}

function updateListStatus() {
  const label = statusFilter === "all" ? "记忆" : statusLabel(statusFilter);
  if (searchQuery.trim() || sourceFilter || monthFilter) {
    setStatus("#memory-status", `${visibleMemories().length} / ${memories.length} 条匹配`, "success");
    return;
  }
  setStatus("#memory-status", `${memories.length} 条${label}`, "success");
}

async function runBatch(status) {
  if (!batchAvailable() || !selectedIds.size) return;
  const ids = [...selectedIds].sort((left, right) => left - right);
  const label = status === "active" ? "确认" : "拒绝";
  const confirmed = await confirmAction({
    title: `批量${label} ${ids.length} 条记忆？`,
    message: status === "active"
      ? "只对来源仍然有效的候选生效；来源已变更或不可用的条目会被跳过并在结果里列出。每条记忆仍会留下独立的审核记录。"
      : "被拒绝的条目不再参与回答，之后仍可重新审核。每条记忆仍会留下独立的审核记录。",
    confirmLabel: `批量${label}`,
  });
  if (!confirmed) return;
  try {
    const result = await api("/api/memories/batch-status", jsonBody("POST", { ids, status, confirm: true }));
    const skipped = Array.isArray(result.skipped) ? result.skipped.length : 0;
    toast(`已${label} ${result.updated} 条${skipped ? `，跳过 ${skipped} 条` : ""}。`, "success");
    selectedIds.clear();
    await loadMemories();
    setStatus("#memory-status", `批量${label}完成：更新 ${result.updated} 条${skipped ? ` · 跳过 ${skipped} 条` : ""}`, skipped ? "warning" : "success");
    changed();
  } catch (error) {
    toast(`批量${label}未完成：${error.message}`, "error");
  }
}

async function tidyCandidates() {
  if (!tidyAvailable()) return;
  try {
    setStatus("#memory-status", "正在预览整理效果…");
    const preview = await api("/api/memories/tidy-candidates", jsonBody("POST", { dry_run: true }));
    if (!preview.updated && !preview.not_memory_like) {
      setStatus("#memory-status", `没有需要整理的候选（共 ${Number(preview.total) || 0} 条）`, "success");
      return;
    }
    const confirmed = await confirmAction({
      title: `整理 ${preview.updated} 条候选？`,
      message: `会把候选的标题和内容改写成可读的句子（最多取第一句），原始摘录与来源保持不变；另有 ${preview.not_memory_like} 条不像完整语句，会保持原样供你拒绝。每条改动都会留下历史记录。`,
      confirmLabel: "整理候选",
    });
    if (!confirmed) return;
    const result = await api("/api/memories/tidy-candidates", jsonBody("POST", { dry_run: false, confirm: true }));
    toast(`已整理 ${result.updated} 条候选。`, "success");
    await loadMemories();
    setStatus("#memory-status", `已整理 ${result.updated} 条；${result.not_memory_like} 条不像完整语句，建议逐条拒绝。`, result.not_memory_like ? "warning" : "success");
    changed();
  } catch (error) {
    setStatus("#memory-status", `整理未完成：${error.message}`, "error");
  }
}

export async function loadMemories(status = statusFilter) {
  statusFilter = status;
  document.querySelectorAll("[data-memory-filter]").forEach((button) => { const selected = button.dataset.memoryFilter === status; button.classList.toggle("active", selected); button.setAttribute("aria-pressed", String(selected)); });
  const request = ++requestId;
  setStatus("#memory-status", "加载中…");
  renderState($("#memory-list"), "loading", "正在读取本地记忆…");
  try {
    const rows = await api(status === "all" ? "/api/memories" : `/api/memories?status=${encodeURIComponent(status)}`);
    if (request !== requestId) return;
    memories = rows;
    selectedIds.clear();
    renderFilterOptions();
    renderList();
    updateListStatus();
  } catch (error) {
    if (request !== requestId) return;
    renderState($("#memory-list"), "error", error.message, "memory");
    setStatus("#memory-status", "读取失败", "error");
  }
}

async function updateStatus(button) {
  const id = button.dataset.memoryId;
  if (busyIds.has(id)) return;
  const card = button.closest("[data-memory-card]");
  const buttons = [...card.querySelectorAll("button")];
  busyIds.add(id);
  buttons.forEach((item) => { item.disabled = true; });
  card.setAttribute("aria-busy", "true");
  setStatus(card.querySelector("[data-memory-feedback]"), "正在更新…");
  try {
    const updated = await api(`/api/memories/${encodeURIComponent(id)}/status`, jsonBody("PATCH", { status: button.dataset.memoryAction }));
    toast(`记忆状态：${statusLabel(updated.status)}`, "success");
    changed();
    await loadMemories();
  } catch (error) {
    setStatus(card.querySelector("[data-memory-feedback]"), `更新失败：${error.message}`, "error");
  } finally { busyIds.delete(id); buttons.forEach((item) => { item.disabled = false; }); card.removeAttribute("aria-busy"); }
}

function editMemory(id) {
  const memory = memories.find((row) => String(row.id) === String(id));
  if (!memory) return;
  const current = openDialog("编辑记忆", `<form id="memory-edit-form"><p class="field-help">修改内容会保留历史记录。已确认记忆修改后将重新成为候选，确认后才参与回答。记忆内容最多 2000 字。</p><div class="field"><label for="memory-edit-title">标题</label><input id="memory-edit-title" maxlength="200" required value="${escapeHtml(memory.title)}" /></div><div class="field"><label for="memory-edit-content">内容</label><textarea id="memory-edit-content" rows="8" maxlength="2000" required aria-describedby="memory-edit-feedback">${escapeHtml(memory.content)}</textarea></div><div class="form-actions"><span id="memory-edit-feedback" class="status-message" role="status"></span><button class="button primary" type="submit">保存修改</button></div></form>`);
  $("#memory-edit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (form.querySelector("button[type=submit]").disabled) return;
    const title = $("#memory-edit-title").value.trim();
    const content = $("#memory-edit-content").value.trim();
    if (!title || !content) return setStatus("#memory-edit-feedback", "标题和内容不能只包含空格。", "error");
    setBusy(form, true);
    try {
      await api(`/api/memories/${encodeURIComponent(id)}`, jsonBody("PATCH", { title, content }));
      if (current()) $("#detail-dialog").close();
      toast("记忆修改已保存，请审核最新内容。", "success");
      changed();
      await loadMemories();
    } catch (error) { if (current()) setStatus("#memory-edit-feedback", error.message, "error"); }
    finally { if (current()) setBusy(form, false); }
  });
}

async function showHistory(id) {
  const current = openDialog("记忆变更历史", '<div class="empty-state">正在读取变更历史…</div>');
  try {
    const result = await api(`/api/memories/${encodeURIComponent(id)}/history`);
    if (!current()) return;
    const rows = Array.isArray(result) ? result : result.history || result.events || [];
    if (!rows.length) return renderState($("#dialog-content"), "empty", "这条记忆暂时没有可显示的变更记录。");
    $("#dialog-content").innerHTML = `<ol class="history-list">${rows.map((row) => {
      let details = row.details || row.snapshot || {};
      try { if (typeof details === "string") details = JSON.parse(details); if (row.details_json) details = JSON.parse(row.details_json); } catch { details = {}; }
      const action = { created: "创建", edited: "编辑", updated: "更新", user_corrected: "人工修订", status_changed: "状态更新", status_change: "状态更新", content_updated: "内容更新", source_changed: "来源已变更", source_missing: "来源已移除" }[row.action || row.event_type] || row.action || row.event_type || "记忆变更";
      const before = row.old_status || row.before?.status || details.old_status || details.before?.status;
      const after = row.new_status || row.after?.status || row.status || details.new_status || details.after?.status;
      const content = row.content || row.new_content || row.after?.content || details.content || details.after?.content || "";
      return `<li><strong>${escapeHtml(action)}</strong><span class="meta">${escapeHtml(formatDate(row.created_at || row.timestamp))}</span>${after ? `<p>${before ? `${escapeHtml(statusLabel(before))} → ` : ""}${escapeHtml(statusLabel(after))}</p>` : ""}${content ? `<p class="preserve-lines">${escapeHtml(content)}</p>` : ""}</li>`;
    }).join("")}</ol>`;
  } catch (error) { if (current()) $("#dialog-content").innerHTML = `<p class="inline-error">${escapeHtml(error.message)}</p><button class="button secondary" data-memory-history="${escapeHtml(id)}">重新读取</button>`; }
}

async function showUsage(id) {
  const current = openDialog("记忆使用记录", '<div class="empty-state">正在读取回答使用记录…</div>');
  try {
    const result = await api(`/api/memories/${encodeURIComponent(id)}/usage`);
    if (!current()) return;
    const items = Array.isArray(result.items) ? result.items : [];
    const total = Number.isFinite(Number(result.total)) ? Number(result.total) : items.length;
    const status = result.memory?.status;
    const note = status && status !== "active"
      ? `这条记忆当前是${statusLabel(status)}，不再参与新回答；下面的历史引用只是它当时被用过的事实。`
      : "只有保存下来的回答才写入这里；发送前预览、本地草稿和检索评测都不会留下引用，查看不会改变记忆状态。";
    if (!items.length) return renderState($("#dialog-content"), "empty", `这条记忆还没有被回答引用过。${note}`);
    $("#dialog-content").innerHTML = `<p class="meta">最近 ${escapeHtml(items.length)} 条 / 共 ${escapeHtml(total)} 次引用</p><p class="field-help">${escapeHtml(note)}</p>` +
      `<ol class="history-list">${items.map((row) => `<li><strong>${escapeHtml(row.question || "（未找到对应问题）")}</strong><span class="meta">${escapeHtml(formatDate(row.used_at))}${row.conversation_title ? ` · ${escapeHtml(row.conversation_title)}` : ""}</span></li>`).join("")}</ol>`;
  } catch (error) {
    if (current()) $("#dialog-content").innerHTML = `<p class="inline-error">${escapeHtml(error.message)}</p><button class="button secondary" data-memory-usage="${escapeHtml(id)}">重新读取</button>`;
  }
}

async function deleteMemory(id) {
  if (!deleteAvailable()) return;
  const confirmed = await confirmAction({
    title: "永久删除这条记忆？",
    message: "删除后无法从本系统恢复：这条记忆、它的审核历史、使用记录与已保存回答里的引用快照都会被移除。你的原始 Markdown 资料不会被删除，但再次扫描该资料也不会重新生成这条候选；已导出的文件与旧备份不受影响。",
    confirmLabel: "永久删除",
    cancelLabel: "保留",
  });
  if (!confirmed) return;
  try {
    await api(`/api/memories/${encodeURIComponent(id)}/delete`, jsonBody("POST", { confirm: true }));
    toast("记忆已永久删除。", "success");
    changed();
    await loadMemories();
  } catch (error) {
    toast(`删除未完成：${error.message}`, "error");
  }
}

export function initMemories() {
  $("#refresh-memory").addEventListener("click", () => loadMemories());
  $("#memory-search").addEventListener("input", () => {
    searchQuery = $("#memory-search").value;
    renderList();
    updateListStatus();
  });
  $("#memory-source-filter")?.addEventListener("change", () => {
    sourceFilter = $("#memory-source-filter").value;
    renderList();
    updateListStatus();
  });
  $("#memory-month-filter")?.addEventListener("change", () => {
    monthFilter = $("#memory-month-filter").value;
    renderList();
    updateListStatus();
  });
  $("#memory-list").addEventListener("change", (event) => {
    const box = event.target.closest("[data-memory-select]");
    if (!box) return;
    const id = Number(box.dataset.memorySelect);
    if (box.checked) selectedIds.add(id);
    else selectedIds.delete(id);
    renderBatchBar();
  });
  $("#export-memories-md")?.addEventListener("click", () => exportMemories("md"));
  $("#export-memories-json")?.addEventListener("click", () => exportMemories("json"));
  renderExportControls();
  document.addEventListener("config-changed", () => {
    renderExportControls();
    if (state.view === "self") loadMemories();
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-memory-tidy]")) return tidyCandidates();
    const batch = event.target.closest("[data-memory-batch]");
    if (batch) return runBatch(batch.dataset.memoryBatch);
    if (event.target.closest("[data-memory-batch-clear]")) {
      selectedIds.clear();
      renderList();
      return;
    }
    const filter = event.target.closest("[data-memory-filter]");
    if (filter) return loadMemories(filter.dataset.memoryFilter);
    if (event.target.closest('[data-retry="memory"]')) return loadMemories();
    if (event.target.closest("[data-memory-search-clear]")) {
      searchQuery = "";
      sourceFilter = "";
      monthFilter = "";
      $("#memory-search").value = "";
      renderFilterOptions();
      renderList();
      updateListStatus();
      return;
    }
    const action = event.target.closest("[data-memory-action]");
    if (action) return updateStatus(action);
    const edit = event.target.closest("[data-memory-edit]");
    if (edit) return editMemory(edit.dataset.memoryEdit);
    const history = event.target.closest("[data-memory-history]");
    if (history) return showHistory(history.dataset.memoryHistory);
    const remove = event.target.closest("[data-memory-delete]");
    if (remove) return deleteMemory(remove.dataset.memoryDelete);
    const usage = event.target.closest("[data-memory-usage]");
    if (usage) return showUsage(usage.dataset.memoryUsage);
  });
}
