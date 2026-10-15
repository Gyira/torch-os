import { $, api, escapeHtml, jsonBody, renderState, setStatus, setBusy, toast, changed, formatDate } from "./core.js";

let documents = [];
let loadId = 0;
let scanning = false;

function renderDocuments() {
  const query = $("#document-search").value.trim().toLocaleLowerCase();
  const selected = documents.filter((item) => `${item.title} ${item.path}`.toLocaleLowerCase().includes(query));
  if (!selected.length) return renderState($("#document-list"), "empty", query ? "没有匹配的资料，试试其他关键词。" : "还没有资料。保存一条记录，或扫描 Vault 建立本地索引。");
  $("#document-list").innerHTML = selected.map((item) => `<article class="document-card"><div><h3>${escapeHtml(item.title || "未命名资料")}</h3><code>${escapeHtml(item.path)}</code><p class="meta">索引时间：${escapeHtml(formatDate(item.indexed_at))}</p></div><button class="small-button" data-document-id="${escapeHtml(item.id)}">阅读原文</button></article>`).join("");
}

export async function loadDocuments() {
  const request = ++loadId;
  setStatus("#document-status", "加载中…");
  renderState($("#document-list"), "loading", "正在读取本地索引…");
  try {
    const result = await api("/api/documents");
    if (request !== loadId) return;
    documents = result;
    renderDocuments();
    setStatus("#document-status", `${documents.length} 份资料`, "success");
  } catch (error) {
    if (request !== loadId) return;
    renderState($("#document-list"), "error", error.message, "documents");
    setStatus("#document-status", "读取失败", "error");
  }
}

export async function scanVault() {
  if (scanning) return;
  scanning = true;
  $("#scan-btn").disabled = true;
  $("#scan-btn").textContent = "扫描中…";
  setStatus("#scan-status", "正在扫描 Vault…");
  try {
    const result = await api("/api/vault/scan", { method: "POST" });
    const count = Array.isArray(result.errors) ? result.errors.length : 0;
    const summary = `扫描 ${result.scanned || 0} · 更新 ${result.changed || 0} · 跳过 ${result.skipped || 0}${count ? ` · 错误 ${count}` : ""}`;
    setStatus("#scan-status", summary, count ? "warning" : "success");
    toast(count ? `扫描完成，有 ${count} 个文件未处理。` : "本地索引已更新。", count ? "warning" : "success");
    changed();
    await loadDocuments();
  } catch (error) { setStatus("#scan-status", `扫描失败：${error.message}`, "error"); toast(error.message, "error"); }
  finally { scanning = false; $("#scan-btn").disabled = false; $("#scan-btn").textContent = "扫描 Vault"; }
}

async function saveNote(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (form.querySelector("button[type=submit]").disabled) return;
  const title = $("#note-title").value.trim();
  const content = $("#note-content").value.trim();
  if (!title || !content) return setStatus("#note-feedback", "标题和正文不能只包含空格。", "error");
  setBusy(form, true);
  setStatus("#note-feedback", "正在保存到本地…");
  try {
    const result = await api("/api/notes", jsonBody("POST", { title, content, type: $("#note-type").value }));
    form.reset();
    const count = Array.isArray(result.memories) ? result.memories.length : null;
    setStatus("#note-feedback", `原文已保存${count !== null ? ` · ${count} 条候选记忆` : "，可前往记忆页审核"}`, "success");
    toast("记录已保存，候选记忆需要你确认。", "success");
    changed();
    await loadDocuments();
  } catch (error) { setStatus("#note-feedback", error.message, "error"); }
  finally { setBusy(form, false); }
}

export function initLibrary() {
  $("#note-form").addEventListener("submit", saveNote);
  $("#document-search").addEventListener("input", renderDocuments);
  $("#scan-btn").addEventListener("click", scanVault);
  $("#refresh-documents").addEventListener("click", loadDocuments);
  document.addEventListener("click", (event) => { if (event.target.closest('[data-retry="documents"]')) loadDocuments(); if (event.target.closest('[data-retry="scan"]')) scanVault(); });
}
