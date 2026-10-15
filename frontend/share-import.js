// DeepSeek 分享链接 → 待确认候选：先预览（只提炼你说的话），确认后才写入。
import { $, state, api, escapeHtml, jsonBody, setStatus, toast, changed } from "./core.js";
import { loadDocuments } from "./library.js";

let previewed = "";
let busy = false;
let loadId = 0;

const available = () => state.config?.capabilities?.share_import === true;

function syncControls() {
  const enabled = available();
  const url = $("#share-url");
  const preview = $("#share-preview");
  const applyButton = $("#share-apply");
  if (url) url.disabled = !enabled || busy;
  if (preview) preview.disabled = !enabled || busy || !String(url?.value || "").trim();
  if (applyButton) applyButton.disabled = !enabled || busy || !previewed;
  const support = $("#share-support");
  if (support) {
    support.hidden = enabled;
    support.textContent = state.config
      ? "当前运行的服务尚未支持分享链接导入。请更新并重启本地服务，再刷新页面。"
      : "正在检查服务能力；若未恢复，请到设置页重新检查本地服务。";
  }
}

function skippedLine(skipped = {}) {
  const parts = [`问句 ${skipped.question || 0}`, `寒暄 ${skipped.chatter || 0}`, `太短 ${skipped.short || 0}`, `重复 ${skipped.duplicate || 0}`];
  if (skipped.over_limit) parts.push(`超出上限 ${skipped.over_limit}`);
  return `跳过：${parts.join("、")}`;
}

function renderPreview(payload) {
  const target = $("#share-result");
  target.hidden = false;
  if (!payload.kept) {
    target.innerHTML = `<p class="inline-warning">这条分享里没有可提炼的陈述句（${escapeHtml(skippedLine(payload.skipped))}）。</p>`;
    return;
  }
  const list = payload.sentences.map((item) => `<li>${escapeHtml(item.text)}</li>`).join("");
  target.innerHTML = `
    <p class="meta">${escapeHtml(payload.title || "未命名分享")}${payload.model_type ? ` · ${escapeHtml(payload.model_type)}` : ""} · ${payload.message_count} 条你说的话</p>
    <p class="meta">将写入 ${payload.kept} 条候选；${escapeHtml(skippedLine(payload.skipped))}。</p>
    ${payload.existing_document_id ? '<p class="meta">这条分享已经导入过，再次导入会更新同一篇笔记，不会重复堆叠。</p>' : ""}
    <ul class="extract-list">${list}</ul>
    <p class="field-help">笔记路径：${escapeHtml(payload.note_path || "")}；确认之前这些句子不参与回答。</p>`;
}

async function previewShare() {
  if (busy || !available()) return;
  const url = String($("#share-url").value || "").trim();
  if (!url) return;
  const request = ++loadId;
  busy = true;
  syncControls();
  setStatus("#share-status", "正在读取分享内容…");
  try {
    const payload = await api("/api/imports/share/preview", jsonBody("POST", { url }));
    if (request !== loadId) return;
    previewed = payload.kept ? url : "";
    renderPreview(payload);
    setStatus("#share-status", payload.kept ? `预览：可提炼 ${payload.kept} 条。` : "这条分享没有可提炼的句子。", payload.kept ? "success" : "warning");
  } catch (error) {
    previewed = "";
    $("#share-result").hidden = true;
    setStatus("#share-status", `预览失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncControls();
  }
}

async function applyShare() {
  const url = previewed;
  if (busy || !url) return;
  busy = true;
  syncControls();
  setStatus("#share-status", "正在写入候选记忆…");
  try {
    const result = await api("/api/imports/share/apply", jsonBody("POST", { url, confirm: true }));
    const summary = result.written
      ? `已生成 ${result.memory_count} 条待确认候选（${result.changed ? "新建了提炼笔记" : "内容与上次相同，未重复写入"}）。`
      : "这条分享里没有可写入的句子。";
    setStatus("#share-status", summary, result.written ? "success" : "warning");
    toast(summary, result.written ? "success" : "info");
    if (result.written) {
      previewed = "";
      await loadDocuments();
      changed();
    }
  } catch (error) {
    setStatus("#share-status", `导入失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncControls();
  }
}

export function initShareImport() {
  $("#share-url")?.addEventListener("input", syncControls);
  $("#share-preview")?.addEventListener("click", previewShare);
  $("#share-apply")?.addEventListener("click", applyShare);
  document.addEventListener("config-changed", syncControls);
  syncControls();
}
