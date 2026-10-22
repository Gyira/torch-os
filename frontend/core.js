import { formatRelativeDate } from "./date-format.js";
export { formatRelativeDate };

export const $ = (selector, root = document) => root.querySelector(selector);
export const state = { view: "workbench", config: null, configLoaded: false };
export const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
export const jsonBody = (method, data) => ({ method, body: JSON.stringify(data) });
export const changed = () => document.dispatchEvent(new CustomEvent("data-changed"));

/** Central API boundary: JSON errors are shown without rendering server HTML. */
export async function api(path, options = {}) {
  const { timeoutMs = 30000, ...requestOptions } = options;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, { ...requestOptions, headers: { "Content-Type": "application/json", ...requestOptions.headers }, signal: controller.signal });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) throw new Error("本地会话已失效，请刷新页面后再试。");
      const detail = payload.detail || payload.message;
      const message = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((item) => item.msg).filter(Boolean).join("；") : "";
      const error = new Error(message || `请求失败（${response.status}），请稍后重试。`);
      error.status = response.status;
      throw error;
    }
    return payload;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时，请稍后检查结果，避免重复提交。");
    if (error instanceof TypeError) throw new Error("无法连接本地服务，请确认服务正在运行后重试。");
    throw error;
  } finally { window.clearTimeout(timer); }
}

export function toast(message, tone = "info") {
  const node = $("#toast");
  node.textContent = message;
  node.dataset.tone = tone;
  node.classList.add("show");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => node.classList.remove("show"), 5000);
}

export function setStatus(selector, message, tone = "neutral") {
  const node = typeof selector === "string" ? $(selector) : selector;
  if (!node) return;
  node.textContent = message;
  node.className = `status-message ${tone}`;
}

export function renderState(container, type, message, retry = "") {
  container.innerHTML = `<div class="state-message ${escapeHtml(type)}-state"><strong>${{ error: "暂时无法加载", loading: "正在加载", empty: "暂无内容" }[type] || "提示"}</strong><p>${escapeHtml(message)}</p>${retry ? `<button class="text-button" data-retry="${escapeHtml(retry)}">重试</button>` : ""}</div>`;
}

export function formatDate(value) {
  if (!value) return "未设置";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Shanghai" }).format(date);
}

export function sourceLocationMarkup(source) {
  const start = Number(source.source_start_line);
  const end = Number(source.source_end_line);
  const positioned = source.source_location_basis === "document_body" && Number.isInteger(start) && start > 0 && Number.isInteger(end) && end >= start;
  const location = positioned ? `来源位置：资料正文第 ${start}${end === start ? "" : `–${end}`} 行（已去除文首元数据与首尾空白，非原始文件行号）。` : "此条来源尚未记录正文行号，可以展开完整原文查阅。";
  return `<p class="meta source-location">${escapeHtml(location)}${source.confidence === "user_corrected" ? " 来源定位对应原始摘录，不定位后来人工修改的记忆文字。" : ""}</p>`;
}

export function coverageMarkup(documentData) {
  const coverage = documentData.coverage_summary;
  const legacy = !coverage || coverage.extraction_version === "legacy";
  const count = (value) => Number.isInteger(value) && value >= 0 ? String(value) : "未知";
  const summary = legacy ? "旧版未记录候选提取范围，完整原文保留。" : `本次生成 ${count(coverage.candidate_count)} 条候选；可提取正文片段 ${count(coverage.total_candidate_segments)} 个，未提取 ${count(coverage.omitted_segments)} 个。`;
  const extractionNote = legacy ? "" : coverage.extraction_version === "paragraphs-v2" ? "已按当前规则提取，审核决定保持不变。" : coverage.extraction_version === "journal-fields-v1" ? "日记快照按原始字段完整分片，不替换为普通段落提取。" : `已记录提取版本 ${coverage.extraction_version || "未知"}，需要兼容此版本后才能调整提取，保留已有审核决定。`;
  const warnings = Array.isArray(coverage?.warnings) ? coverage.warnings : [];
  return `<section class="source-coverage" aria-label="候选提取范围"><h3>候选提取范围</h3><p>${escapeHtml(summary)}</p>${extractionNote ? `<p class="field-help">${escapeHtml(extractionNote)}</p>` : ""}${coverage?.truncated ? '<p class="inline-warning">本篇提取已达到数量上限，仍有正文片段未进入候选列表。完整原文保留在下方。</p>' : ""}${warnings.length ? `<ul>${warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}<p class="field-help">候选提取范围不等于已理解全部事实，也不代表记忆已经确认。${legacy ? "" : "正文行号从去除文首元数据及首尾空白后的第 1 行起算。"}</p></section>`;
}

const formControlStates = new WeakMap();
export function setBusy(form, busy, label = "保存中…") {
  const button = form.querySelector("button[type=submit]");
  if (!button.dataset.idleLabel) button.dataset.idleLabel = button.textContent;
  if (busy) {
    const controls = [...form.querySelectorAll("input, textarea, select")];
    formControlStates.set(form, controls.map((control) => [control, control.disabled]));
    controls.forEach((control) => { control.disabled = true; });
  } else {
    (formControlStates.get(form) || []).forEach(([control, disabled]) => { control.disabled = disabled; });
    formControlStates.delete(form);
  }
  button.disabled = busy;
  button.textContent = busy ? label : button.dataset.idleLabel;
  form.setAttribute("aria-busy", String(busy));
}

let dialogVersion = 0;
export function openDialog(title, html) {
  const dialog = $("#detail-dialog");
  $("#dialog-title").textContent = title;
  $("#dialog-content").innerHTML = html;
  if (!dialog.open) dialog.showModal();
  const version = ++dialogVersion;
  return () => version === dialogVersion && dialog.open;
}

let activeConfirmation = null;

/** An explicit, cancellable in-page prompt. A second request never shares consent. */
export function confirmAction({ title = "请确认", message, confirmLabel = "继续", cancelLabel = "取消", trigger = document.activeElement }) {
  if (activeConfirmation) return Promise.resolve(false);
  const dialog = $("#confirm-dialog");
  $("#confirm-title").textContent = title;
  $("#confirm-message").textContent = message;
  $("#confirm-accept").textContent = confirmLabel;
  $("#confirm-cancel").textContent = cancelLabel;
  dialog.returnValue = "cancel";
  return new Promise((resolve) => {
    activeConfirmation = { resolve, trigger };
    try { dialog.showModal(); $("#confirm-cancel").focus(); }
    catch { finishConfirmation(false); }
  });
}

function finishConfirmation(accepted) {
  const pending = activeConfirmation;
  if (!pending) return;
  activeConfirmation = null;
  const dialog = $("#confirm-dialog");
  if (dialog.open) dialog.close(accepted ? "accept" : "cancel");
  if (pending.trigger?.isConnected !== false) pending.trigger?.focus?.();
  pending.resolve(accepted);
}

$("#confirm-cancel").addEventListener("click", () => finishConfirmation(false));
$("#confirm-accept").addEventListener("click", () => finishConfirmation(true));
$("#confirm-dialog").addEventListener("cancel", (event) => { event.preventDefault(); finishConfirmation(false); });
$("#confirm-dialog").addEventListener("close", () => { if (!$("#confirm-dialog").open) finishConfirmation(false); });

const reextractingDocuments = new Set();

function renderDocument(documentData, current) {
  const id = String(documentData.id);
  const legacy = !documentData.coverage_summary || documentData.coverage_summary.extraction_version === "legacy";
  const canReextract = legacy && Number.isInteger(documentData.revision) && documentData.revision > 0 && /^[a-f0-9]{64}$/i.test(documentData.sha256 || "");
  $("#dialog-title").textContent = documentData.title || "资料原文";
  $("#dialog-content").innerHTML = `<p class="meta source-path">${escapeHtml(documentData.path || "本地资料")}</p>${coverageMarkup(documentData)}<div class="source-extraction-actions">${canReextract ? `<button id="reextract-document" class="button secondary" ${reextractingDocuments.has(id) ? "disabled" : ""}>按新版提取候选</button><p class="field-help">升级旧版提取会归档这份资料现有的候选和已确认记忆并保留历史，新候选需要重新审核。</p>` : ""}<span id="reextract-feedback" class="status-message" role="status" aria-live="polite"></span></div><div class="source-extraction-actions"><button id="preview-document-tasks" class="button secondary">预览待办清单</button><p class="field-help">只预览 Markdown 复选框，选择后复制为独立任务，不反向同步来源。</p><span id="preview-document-tasks-feedback" class="status-message" role="status"></span></div><h3>完整资料正文</h3><pre class="source-content">${escapeHtml(documentData.content || "当前资料没有正文。")}</pre>`;
  $("#preview-document-tasks").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    if (button.disabled) return;
    button.disabled = true;
    try {
      const { openTaskImport } = await import("./task-import.js");
      if (current()) await openTaskImport(documentData);
    } catch (error) { if (current()) setStatus("#preview-document-tasks-feedback", `预览暂时无法打开：${error.message}`, "error"); }
    finally { if (current()) button.disabled = false; }
  });
  if (canReextract) $("#reextract-document").addEventListener("click", async () => {
    if (reextractingDocuments.has(id)) return;
    const confirmed = await confirmAction({ title: "按新版提取这份旧版资料的候选？", message: "升级旧版提取将归档这份资料现有的候选和已确认记忆，保留历史；新候选必须重新审核后才参与回答。资料原文不会被修改。", confirmLabel: "归档旧记忆并按新版提取", cancelLabel: "保留现有记忆" });
    if (!confirmed || !current()) return;
    reextractingDocuments.add(id);
    const button = $("#reextract-document");
    button.disabled = true;
    button.textContent = "提取中…";
    setStatus("#reextract-feedback", "正在从本地原文提取候选…");
    try {
      const result = await api(`/api/documents/${encodeURIComponent(id)}/reextract`, jsonBody("POST", { revision: documentData.revision, sha256: documentData.sha256, confirm: true }));
      reextractingDocuments.delete(id);
      if (current()) {
        renderDocument(result.document, current);
        const feedback = result.changed === true ? `已生成 ${Number(result.candidate_count) || 0} 条候选，请前往“记忆”逐条审核。` : result.changed === false ? "本次未改动候选或已确认记忆，保留已有审核决定。" : "处理完成，未收到提取变更状态；请重新打开资料核对审核状态。";
        setStatus("#reextract-feedback", feedback, typeof result.changed === "boolean" ? "success" : "neutral");
        $("#preview-document-tasks")?.focus();
      }
      if (result.changed === true) changed();
    } catch (error) {
      if (current()) {
        setStatus("#reextract-feedback", error.status === 409 ? `${error.message} 请关闭后重新打开资料原文，再决定是否提取。` : `${error.message} 可重试。`, "error");
        button.disabled = error.status === 409;
        button.textContent = "按新版提取候选";
      }
    } finally { reextractingDocuments.delete(id); }
  });
}

export async function showDocument(id) {
  if (!/^\d+$/.test(String(id))) return;
  const current = openDialog("资料原文", '<div class="empty-state">正在读取原文…</div>');
  try {
    const documentData = await api(`/api/documents/${id}`);
    if (!current()) return;
    renderDocument(documentData, current);
  } catch (error) {
    if (current()) $("#dialog-content").innerHTML = `<p class="inline-error">${escapeHtml(error.message)}</p><button class="button secondary" data-document-id="${escapeHtml(id)}">重新读取</button>`;
  }
}

$("#dialog-close").addEventListener("click", () => $("#detail-dialog").close());
$("#detail-dialog").addEventListener("close", () => { dialogVersion += 1; });
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-document-id]");
  if (button) showDocument(button.dataset.documentId);
});
