import { $, api, confirmAction, escapeHtml, jsonBody, renderState, setStatus, toast, changed, state } from "./core.js";
import { loadDocuments } from "./library.js";

const PAGE_LIMIT = 50;
const SELECTION_LIMIT = 20;
let packageRequest = 0;
let previewRequest = 0;
let previewPackage = null;
let files = [];
let eligiblePaths = new Set();
const selectedPaths = new Set();
let loadingPackages = false;
let previewing = false;
let checkingStates = false;
let applying = false;
let auditing = false;
let auditRequest = 0;
let planning = false;
let planRequest = 0;
let applyingPlan = false;
let attachmentPlan = null;
let statesRequest = 0;
let loadingHistory = false;
let historyRequest = 0;
let scanningHistory = false;
let loadingCleanup = false;
let cleanupRequest = 0;

const eligible = (file) => file.type === "markdown" && file.import_allowed === true && !file.sensitive;
const count = (value) => Array.isArray(value) ? value.length : Number.isFinite(Number(value)) ? Number(value) : "—";
const warningText = (value) => typeof value === "string" ? value : value?.message || value?.reason || JSON.stringify(value);

function syncControls() {
  const busy = loadingPackages || previewing || applying || auditing || planning || applyingPlan
    || loadingHistory || scanningHistory || loadingCleanup;
  const selectionReady = selectedPaths.size > 0 && selectedPaths.size <= SELECTION_LIMIT && previewPackage === $("#import-package").value;
  $("#import-package").disabled = busy;
  $("#refresh-imports").disabled = busy;
  $("#preview-import").disabled = busy || !$("#import-package").value;
  $("#preview-import").textContent = previewing ? "正在生成清单…" : "生成预览清单";
  $("#apply-import").disabled = busy || !selectionReady;
  $("#apply-import").textContent = applying ? "正在导入…" : "导入所选记录";
  $("#audit-import-links").disabled = busy || !selectionReady || state.config?.capabilities?.link_audit !== true;
  $("#audit-import-links").textContent = auditing ? "正在核对链接…" : state.config?.capabilities?.link_audit === true ? "核对所选链接" : "链接核对待服务更新";
  $("#plan-import-attachments").disabled = busy || !selectionReady || state.config?.capabilities?.attachment_plan !== true;
  $("#plan-import-attachments").textContent = planning ? "正在生成预览…" : state.config?.capabilities?.attachment_plan === true ? "预览附件本地化" : "附件预览待服务更新";
  const plannable = Boolean(attachmentPlan && Array.isArray(attachmentPlan.notes)
    && attachmentPlan.notes.some((note) => note.plannable));
  $("#apply-attachment-plan").disabled = busy || !plannable || state.config?.capabilities?.attachment_localize !== true;
  $("#apply-attachment-plan").textContent = applyingPlan ? "正在执行…" : "执行附件本地化";
  const statesButton = $("#check-import-states");
  if (statesButton) statesButton.disabled = busy || previewing || applying || checkingStates || !selectionReady;
  if (statesButton) statesButton.textContent = checkingStates ? "正在检查…" : "检查重复与变化";
  const historyButton = $("#check-import-history");
  historyButton.disabled = busy || !selectionReady || state.config?.capabilities?.import_history !== true;
  historyButton.textContent = loadingHistory ? "正在读取历史…" : state.config?.capabilities?.import_history === true ? "查看导入历史" : "历史待服务更新";
  const cleanupButton = $("#preview-history-cleanup");
  cleanupButton.disabled = busy || !selectionReady || state.config?.capabilities?.history_cleanup_preview !== true;
  cleanupButton.textContent = loadingCleanup ? "正在统计…" : state.config?.capabilities?.history_cleanup_preview === true ? "清理预览" : "清理预览待服务更新";
  $("#clear-import-selection").disabled = busy || selectedPaths.size === 0;
  $("#import-selection-count").textContent = `已选择 ${selectedPaths.size} / ${SELECTION_LIMIT} 篇`;
  $("#import-search").disabled = busy;
  $("#import-selected-only").disabled = busy;
  $("#import-preview").setAttribute("aria-busy", String(previewing || applying || auditing || planning || applyingPlan));
  document.querySelectorAll("[data-import-path]").forEach((input) => {
    input.disabled = busy || !eligiblePaths.has(input.dataset.importPath) || (!input.checked && selectedPaths.size >= SELECTION_LIMIT);
  });
}

function clearLinkAudit() {
  auditRequest += 1;
  $("#import-link-audit-result").replaceChildren();
  setStatus("#import-link-audit-status", "");
  planRequest += 1;
  attachmentPlan = null;
  const planResult = $("#import-attachment-plan-result");
  if (planResult) planResult.replaceChildren();
  setStatus("#import-attachment-plan-status", "");
}

const STATE_LABELS = { new: "新增", unchanged: "未变化", updated: "内容已更新（旧版本会存历史）", "user-edited": "已被你编辑（不会覆盖）" };
const BLOCKED_STATES = new Set(["unchanged", "user-edited"]);
let importStates = new Map();

function clearImportStates() {
  statesRequest += 1;
  importStates = new Map();
  const result = $("#import-states-result");
  if (result) result.replaceChildren();
  setStatus("#import-states-status", "");
  clearImportHistory();
}

function clearImportHistory() {
  historyRequest += 1;
  const historyResult = $("#import-history-result");
  if (historyResult) historyResult.replaceChildren();
  setStatus("#import-history-status", "");
  cleanupRequest += 1;
  const cleanupResult = $("#import-cleanup-result");
  if (cleanupResult) cleanupResult.replaceChildren();
  setStatus("#import-cleanup-status", "");
}

function renderImportStates(payload) {
  const counts = payload.counts || {};
  const items = Array.isArray(payload.items) ? payload.items : [];
  importStates = new Map(items.map((item) => [item.path, item.state]));
  for (const [path, state] of importStates) {
    if (BLOCKED_STATES.has(state)) selectedPaths.delete(path);
  }
  $("#import-states-result").innerHTML = `<dl class="import-link-counts">${Object.entries(STATE_LABELS).map(([key, label]) => `<div><dt>${label}</dt><dd>${linkCount(counts[key])}</dd></div>`).join("")}</dl><p class="field-help">${escapeHtml(payload.note || "")}</p><ol class="import-link-issues">${items.map((item) => `<li><strong>${escapeHtml(STATE_LABELS[item.state] || item.state)}</strong><p class="meta">来源：<code>${escapeHtml(item.path)}</code></p><p>Vault：<code>${escapeHtml(item.vault_path || "")}</code></p></li>`).join("")}</ol>`;
  renderFiles();
  const skipped = items.filter((item) => BLOCKED_STATES.has(item.state)).length;
  return skipped;
}

async function checkImportStates() {
  const id = $("#import-package").value;
  const paths = [...selectedPaths];
  if (checkingStates || auditing || planning || applying || previewing || loadingPackages || id !== previewPackage) return;
  if (!paths.length || paths.length > SELECTION_LIMIT || paths.some((item) => !eligiblePaths.has(item))) {
    return toast("请选择 1–20 篇明确允许导入的 Markdown 记录后检查。", "error");
  }
  clearImportStates();
  const request = ++statesRequest;
  const current = () => request === statesRequest && previewPackage === id && $("#import-package").value === id;
  checkingStates = true;
  syncControls();
  setStatus("#import-states-status", `正在检查所选 ${paths.length} 篇记录与当前 Vault 的关系…`);
  try {
    const result = await api(`/api/imports/${encodeURIComponent(id)}/states`, jsonBody("POST", { selected_paths: paths }));
    if (!current()) return;
    const skipped = renderImportStates(result);
    setStatus("#import-states-status", skipped
      ? `检查完成：${skipped} 篇已自动取消勾选（未变化或已被你编辑），可导入的会保持勾选；没有写入任何文件。`
      : "检查完成：没有写入任何文件。", "success");
  } catch (error) {
    if (current()) setStatus("#import-states-status", `检查未完成：${error.message} 已保留当前选择，可重试。`, "error");
  } finally {
    checkingStates = false;
    syncControls();
  }
}

const HISTORY_STATE_LABELS = { managed: "受管文件", missing: "尚未导入或文件缺失", unmanaged: "当前文件未被管理（可能已修改）" };
const INDEX_STATE_LABELS = { current: "索引已更新", stale: "索引待刷新：扫描后候选才会更新", missing: "文件缺失，无索引" };

function historyTime(value) {
  return typeof value === "string" ? value.replace("T", " ").slice(0, 16) : "";
}

function renderImportHistory(result) {
  const notes = Array.isArray(result.notes) ? result.notes.slice(0, PAGE_LIMIT) : [];
  const restoreAvailable = state.config?.capabilities?.history_restore === true;
  const stale = notes.some((note) => note.index_state === "stale");
  $("#import-history-result").innerHTML = notes.map((note) => {
    const versions = Array.isArray(note.versions) ? note.versions.slice(0, PAGE_LIMIT) : [];
    const indexLine = note.index_state
      ? `<p class="meta">${escapeHtml(INDEX_STATE_LABELS[note.index_state] || note.index_state)}</p>`
      : "";
    return `<article class="import-plan-note"><strong>${escapeHtml(note.source)}</strong><p class="meta">${escapeHtml(HISTORY_STATE_LABELS[note.state] || note.state)}${note.current_sha256 ? ` · 当前 ${escapeHtml(String(note.current_sha256).slice(0, 12))}` : ""}</p>${indexLine}${versions.length ? `<ol class="import-link-issues">${versions.map((version) => `<li><strong>${escapeHtml(String(version.sha256).slice(0, 12))}</strong>${version.current ? ' <span class="badge">当前</span>' : ""}<p class="meta">${escapeHtml(String(version.bytes))} 字节 · ${escapeHtml(historyTime(version.modified_at))}</p>${version.current ? '<p class="meta">这就是当前内容，无需恢复。</p>' : `<button class="small-button" type="button" data-history-restore="${escapeHtml(version.sha256)}" data-history-source="${escapeHtml(note.source)}" aria-label="恢复 ${escapeHtml(note.source)} 的版本 ${escapeHtml(String(version.sha256).slice(0, 12))}" ${restoreAvailable ? "" : "disabled"}>恢复此版本</button>`}</li>`).join("")}</ol>` : '<p class="meta">这篇记录还没有历史版本（重新导入或本地化后会在这里出现）。</p>'}</article>`;
  }).join("") + (stale
    ? '<div class="growth-actions"><button class="small-button" type="button" data-history-scan>扫描并刷新候选</button></div><p class="field-help">扫描只更新本地索引：旧候选归档、新候选需要重新审核；不改原文、不联网。</p>'
    : "");
}

async function loadImportHistory(successMessage = null) {
  if (state.config?.capabilities?.import_history !== true) return;
  const id = $("#import-package").value;
  const paths = [...selectedPaths];
  if (loadingHistory || checkingStates || auditing || planning || applyingPlan || applying || previewing || loadingPackages || id !== previewPackage) return;
  if (!paths.length || paths.length > SELECTION_LIMIT || paths.some((item) => !eligiblePaths.has(item))) {
    return toast("请选择 1–20 篇明确允许导入的 Markdown 记录后查看历史。", "error");
  }
  clearImportHistory();
  const request = ++historyRequest;
  const current = () => request === historyRequest && previewPackage === id && $("#import-package").value === id;
  loadingHistory = true;
  syncControls();
  setStatus("#import-history-status", `正在读取所选 ${paths.length} 篇记录的导入历史…`);
  try {
    const result = await api(`/api/imports/${encodeURIComponent(id)}/history`, jsonBody("POST", { selected_paths: paths }));
    if (!current()) return;
    renderImportHistory(result);
    setStatus("#import-history-status", successMessage
      || `历史读取完成：${linkCount(result.summary?.versions)} 个历史版本；未展示正文、未写入任何文件。`, "success");
  } catch (error) {
    if (current()) setStatus("#import-history-status", `历史读取未完成：${error.message} 已保留当前选择，可重试。`, "error");
  } finally { loadingHistory = false; syncControls(); }
}

async function restoreImportHistory(button) {
  if (state.config?.capabilities?.history_restore !== true) return;
  const id = $("#import-package").value;
  if (loadingHistory || checkingStates || auditing || planning || applyingPlan || applying || previewing || loadingPackages) return;
  const source = button.dataset.historySource;
  const sha256 = button.dataset.historyRestore;
  const confirmed = await confirmAction({
    title: "恢复这个历史版本？",
    message: `会把当前内容先存入历史，再用版本 ${String(sha256).slice(0, 12)} 替换这篇笔记。下次扫描会重新生成候选；原 ZIP 不动。`,
    confirmLabel: "恢复此版本", cancelLabel: "先不恢复", trigger: button,
  });
  if (!confirmed) return;
  loadingHistory = true;
  syncControls();
  setStatus("#import-history-status", "正在恢复历史版本…");
  try {
    const result = await api(`/api/imports/${encodeURIComponent(id)}/history/restore`, jsonBody("POST", { source_path: source, sha256 }));
    loadingHistory = false;
    changed();
    await loadImportHistory(`已恢复版本 ${String(sha256).slice(0, 12)}${result.previous_sha256 ? "；恢复前的内容已存入历史" : "（文件此前缺失，已重建）"}。`);
  } catch (error) {
    loadingHistory = false;
    setStatus("#import-history-status", `恢复未完成：${error.message}`, "error");
    syncControls();
  }
}

async function scanForImportHistory() {
  if (scanningHistory) return;
  scanningHistory = true;
  syncControls();
  setStatus("#import-history-status", "正在扫描资料库并刷新候选…");
  try {
    const result = await api("/api/vault/scan", { method: "POST" });
    changed();
    const errors = Array.isArray(result.errors) ? result.errors.length : 0;
    await loadImportHistory(
      `扫描完成：扫描 ${linkCount(result.scanned)} · 更新 ${linkCount(result.changed)} · 跳过 ${linkCount(result.skipped)}`
      + `${errors ? ` · 错误 ${errors}` : ""}；旧候选已归档，新候选需要重新审核。`);
  } catch (error) {
    setStatus("#import-history-status", `扫描未完成：${error.message}`, "error");
  } finally {
    scanningHistory = false;
    syncControls();
  }
}

function renderCleanupPreview(result) {
  const summary = result.summary || {};
  const policy = result.policy || {};
  const notes = Array.isArray(result.notes) ? result.notes.slice(0, PAGE_LIMIT) : [];
  $("#import-cleanup-result").innerHTML = `<dl class="import-link-counts"><div><dt>历史版本</dt><dd>${linkCount(summary.versions)}</dd></div><div><dt>历史占用</dt><dd>${escapeHtml(planSizeText(summary.bytes))}</dd></div><div><dt>可清理（预览）</dt><dd>${linkCount(summary.cleanable)}</dd></div><div><dt>可释放</dt><dd>${escapeHtml(planSizeText(summary.cleanable_bytes))}</dd></div></dl><p class="field-help" data-cleanup-summary>${escapeHtml(result.note || "这只是预览。")} 保留策略：每篇最近 ${linkCount(policy.keep_newest)} 个版本与 ${linkCount(policy.min_age_days)} 天内的版本一律保留。</p>${notes.map((note) => `<article class="import-plan-note"><strong>${escapeHtml(note.source)}</strong><p class="meta">历史 ${linkCount(note.versions)} 个 · 可清理 ${linkCount(note.cleanable)} 个（${escapeHtml(planSizeText(note.cleanable_bytes))}）</p>${(Array.isArray(note.items) ? note.items : []).slice(0, PAGE_LIMIT).map((item) => `<p class="meta"><code>${escapeHtml(String(item.sha256).slice(0, 12))}</code> · ${escapeHtml(String(item.bytes))} 字节 · ${linkCount(item.age_days)} 天前 · ${item.keep ? "保留" : "可清理"}：${escapeHtml(item.reason || "")}</p>`).join("")}</article>`).join("")}<p class="field-help">这里没有任何删除按钮；是否执行清理仍等你决定。</p>`;
}

async function previewHistoryCleanup() {
  if (state.config?.capabilities?.history_cleanup_preview !== true) return;
  const id = $("#import-package").value;
  const paths = [...selectedPaths];
  if (loadingCleanup || loadingHistory || scanningHistory || checkingStates || auditing || planning || applyingPlan || applying || previewing || loadingPackages || id !== previewPackage) return;
  if (!paths.length || paths.length > SELECTION_LIMIT || paths.some((item) => !eligiblePaths.has(item))) {
    return toast("请选择 1–20 篇明确允许导入的 Markdown 记录后预览清理。", "error");
  }
  clearImportHistory();
  const request = ++cleanupRequest;
  const current = () => request === cleanupRequest && previewPackage === id && $("#import-package").value === id;
  loadingCleanup = true;
  syncControls();
  setStatus("#import-cleanup-status", `正在统计所选 ${paths.length} 篇记录的历史保留情况…`);
  try {
    const result = await api(`/api/imports/${encodeURIComponent(id)}/history/cleanup-preview`, jsonBody("POST", { selected_paths: paths }));
    if (!current()) return;
    renderCleanupPreview(result);
    setStatus("#import-cleanup-status", `清理预览完成：可清理 ${linkCount(result.summary?.cleanable)} 个旧副本；未删除任何文件。`, "success");
  } catch (error) {
    if (current()) setStatus("#import-cleanup-status", `清理预览未完成：${error.message} 已保留当前选择，可重试。`, "error");
  } finally { loadingCleanup = false; syncControls(); }
}

function resetPreview() {
  previewRequest += 1;
  previewPackage = null;
  files = [];
  eligiblePaths.clear();
  selectedPaths.clear();
  $("#import-preview").hidden = true;
  $("#import-file-list").replaceChildren();
  $("#import-warnings").replaceChildren();
  $("#import-result").replaceChildren();
  clearLinkAudit();
  $("#import-search").value = "";
  $("#import-selected-only").checked = false;
  syncControls();
}

export async function loadImports() {
  if (applying || previewing || auditing || planning) return;
  const request = ++packageRequest;
  loadingPackages = true;
  syncControls();
  setStatus("#import-status", "正在读取本地导出包…");
  try {
    const result = await api("/api/imports");
    if (request !== packageRequest) return;
    const rows = Array.isArray(result) ? result : result.imports || [];
    const select = $("#import-package");
    const previous = select.value;
    select.innerHTML = '<option value="">请选择已落盘的导出包</option>' + rows.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.filename || item.relative_path || "导出包")} · ${(Math.max(0, Number(item.bytes) || 0) / 1048576).toFixed(1)} MB</option>`).join("");
    select.value = rows.some((item) => String(item.id) === previous) ? previous : "";
    if (previous !== select.value) resetPreview();
    setStatus("#import-status", rows.length ? `找到 ${rows.length} 个导出包。选择后生成清单；不会自动导入。` : "暂未发现已落盘的导出包。导出文件放入本地导入目录后，可刷新列表。", rows.length ? "success" : "neutral");
  } catch (error) {
    if (request === packageRequest) setStatus("#import-status", `导出包列表读取失败：${error.message} 点击“刷新导出包”重试。`, "error");
  } finally { if (request === packageRequest) { loadingPackages = false; syncControls(); } }
}

function renderWarnings(warnings) {
  const rows = Array.isArray(warnings) ? warnings : warnings ? [warnings] : [];
  $("#import-warnings").innerHTML = rows.length ? `<div class="inline-warning"><strong>预览提醒</strong><ul>${rows.slice(0, 10).map((item) => `<li>${escapeHtml(warningText(item))}</li>`).join("")}</ul>${rows.length > 10 ? `<p>另有 ${rows.length - 10} 条提醒。请逐条查看文件标记。</p>` : ""}</div>` : "";
}

function renderFiles() {
  const query = $("#import-search").value.trim().toLocaleLowerCase();
  const selectedOnly = $("#import-selected-only").checked;
  const filtered = files.filter((file) => file.type === "markdown" && String(file.path).toLocaleLowerCase().includes(query) && (!selectedOnly || selectedPaths.has(file.path)));
  const visible = filtered.slice(0, PAGE_LIMIT);
  $("#import-list-status").textContent = `${filtered.length} 篇匹配 · 显示 ${visible.length} 篇${filtered.length > PAGE_LIMIT ? `（最多显示前 ${PAGE_LIMIT} 篇，请搜索缩小范围）` : ""}。敏感或不可导入的文件不能勾选。`;
  if (!visible.length) renderState($("#import-file-list"), "empty", selectedOnly ? "当前没有符合筛选条件的已选记录。" : "没有匹配的 Markdown 记录，试试其他搜索词。");
  else $("#import-file-list").innerHTML = visible.map((file, index) => {
    const selectable = eligible(file);
    const checked = selectedPaths.has(file.path);
    const state = importStates.get(file.path);
    const blockedByState = Boolean(state && BLOCKED_STATES.has(state));
    const warnings = Array.isArray(file.warnings) ? file.warnings : file.warnings ? [file.warnings] : [];
    const note = file.sensitive ? "敏感内容 · 禁止导入" : blockedByState ? `${STATE_LABELS[state] || state} · 已自动跳过` : selectable ? "可选择导入" : "不可导入";
    const stateBadge = state && !blockedByState && !file.sensitive ? ` · ${STATE_LABELS[state] || state}` : "";
    return `<article class="import-file-row"><input id="import-file-${index}" type="checkbox" data-import-path="${escapeHtml(file.path)}" ${checked ? "checked" : ""} ${blockedByState || !selectable || applying || previewing || auditing || planning || loadingPackages || (!checked && selectedPaths.size >= SELECTION_LIMIT) ? "disabled" : ""} /><div><label for="import-file-${index}">${escapeHtml(file.path)}</label><p class="meta">${(Math.max(0, Number(file.size) || 0) / 1024).toFixed(1)} KB · ${note}${stateBadge}</p>${warnings.length ? `<p class="import-file-warning">${escapeHtml(warnings.map(warningText).join("；"))}</p>` : ""}</div></article>`;
  }).join("");
  syncControls();
}

async function previewImport() {
  const id = $("#import-package").value;
  if (!id || applying || previewing || planning || loadingPackages || auditing) return;
  resetPreview();
  const request = ++previewRequest;
  previewing = true;
  syncControls();
  setStatus("#import-status", "正在生成预览清单，不会写入资料库…");
  try {
    const result = await api(`/api/imports/${encodeURIComponent(id)}/preview`, { method: "POST" });
    if (request !== previewRequest || $("#import-package").value !== id) return;
    files = Array.isArray(result.files) ? result.files.filter((file) => typeof file.path === "string") : [];
    eligiblePaths = new Set(files.filter(eligible).map((file) => file.path));
    previewPackage = id;
    selectedPaths.clear();
    $("#import-summary").textContent = `共 ${count(result.file_count ?? files.length)} 项 · Markdown ${count(result.markdown_count ?? files.filter((file) => file.type === "markdown").length)} 篇 · 附件 ${count(result.attachment_count ?? files.filter((file) => file.type === "attachment").length)} 项 · 可勾选 ${eligiblePaths.size} 篇`;
    renderWarnings(result.warnings);
    $("#import-preview").hidden = false;
    renderFiles();
    setStatus("#import-status", "预览完成。默认未选择任何记录，请逐条判断后勾选 1–20 篇。", "success");
  } catch (error) { if (request === previewRequest) setStatus("#import-status", `预览失败：${error.message} 可以重新生成清单。`, "error"); }
  finally { if (request === previewRequest) { previewing = false; syncControls(); } }
}

const linkLabels = { resolved_note: "笔记路径存在", resolved_attachment: "附件路径存在", anchor_only: "页内锚点", external: "外部链接（未访问）", manual_review: "需人工核对", missing: "目标缺失", unsafe: "不安全路径", restricted_target: "受限目标", total: "链接总数" };
const reviewClassifications = new Set(["missing", "unsafe", "restricted_target", "manual_review"]);
const linkCount = (value) => Number.isInteger(value) && value >= 0 ? value : 0;

function renderLinkAudit(result) {
  const counts = result.counts || {};
  const items = Array.isArray(result.items) ? result.items.filter((item) => item && reviewClassifications.has(item.classification)) : [];
  const visible = items.slice(0, PAGE_LIMIT);
  const reviewCount = Math.max(items.length, [...reviewClassifications].reduce((total, key) => total + linkCount(counts[key]), 0));
  const warnings = Array.isArray(result.warnings) ? result.warnings : [];
  const summary = result.summary && typeof result.summary === "object" ? result.summary : null;
  const attachments = result.attachments && typeof result.attachments === "object" ? result.attachments : null;
  const hints = result.sensitive_hints && typeof result.sensitive_hints === "object" ? result.sensitive_hints : null;
  const hintCount = linkCount(hints?.archive_entries) + linkCount(hints?.restricted_targets);
  const summaryLine = summary && attachments && hints
    ? `<p class="field-help" data-link-audit-summary>附件：引用 ${linkCount(attachments.referenced)} 个（存在 ${linkCount(attachments.found)} · 缺失 ${linkCount(attachments.missing)}）· 包内附件 ${linkCount(attachments.archive_count)} 个 · 敏感提示 ${hintCount} 条</p>`
    : "";
  $("#import-link-audit-result").innerHTML = `${summaryLine}<dl class="import-link-counts">${Object.entries(linkLabels).map(([key, label]) => `<div><dt>${label}</dt><dd>${linkCount(counts[key])}</dd></div>`).join("")}</dl><p class="field-help">仅检查链接清单存在性，未验证附件正文或可用性，也不复制或本地化附件；不重写原文。</p>${warnings.length ? `<div class="inline-warning"><ul>${warnings.slice(0, 10).map((warning) => `<li>${escapeHtml(warningText(warning))}</li>`).join("")}</ul>${warnings.length > 10 ? `<p>另有 ${warnings.length - 10} 条提醒，可查看本地报告。</p>` : ""}</div>` : ""}<p class="meta">需要检查 ${reviewCount} 项 · 显示 ${visible.length} 项${reviewCount > visible.length ? `（最多显示前 ${PAGE_LIMIT} 项，完整清单见本地报告）` : ""}。</p>${visible.length ? `<ol class="import-link-issues">${visible.map((item) => {
    const line = Number.isInteger(item.line) && item.line > 0 ? `第 ${item.line} 行` : "行号未记录";
    const target = typeof item.target === "string" ? item.target : "未展示目标文本";
    const itemWarnings = Array.isArray(item.warnings) ? item.warnings : [];
    return `<li><strong>${linkLabels[item.classification]}</strong><p class="meta">来源：<code>${escapeHtml(item.source || "未记录来源")}</code> · ${line}</p><p>目标：<code>${escapeHtml(target)}</code></p>${typeof item.resolved_path === "string" ? `<p>清单路径：<code>${escapeHtml(item.resolved_path)}</code></p>` : ""}${itemWarnings.length ? `<p class="import-file-warning">${escapeHtml(itemWarnings.slice(0, 5).map(warningText).join("；"))}</p>` : ""}</li>`;
  }).join("")}</ol>` : '<p class="meta">返回清单中没有需要检查的条目；这不代表正文、锚点或外部链接已经验证。</p>'}${typeof result.markdown_path === "string" ? `<p class="meta">人话报告：<code>${escapeHtml(result.markdown_path)}</code></p>` : ""}${typeof result.report_path === "string" ? `<p class="meta">本地报告：<code>${escapeHtml(result.report_path)}</code></p>` : ""}`;
}

async function auditImportLinks() {
  if (state.config?.capabilities?.link_audit !== true) return;
  const id = $("#import-package").value;
  const paths = [...selectedPaths];
  if (auditing || planning || applying || previewing || loadingPackages || id !== previewPackage) return;
  if (!paths.length || paths.length > SELECTION_LIMIT || paths.some((item) => !eligiblePaths.has(item))) return toast("请选择 1–20 篇明确允许导入的 Markdown 记录后核对链接。", "error");
  clearLinkAudit();
  const request = ++auditRequest;
  const current = () => request === auditRequest && previewPackage === id && $("#import-package").value === id;
  auditing = true;
  syncControls();
  setStatus("#import-link-audit-status", `正在核对所选 ${paths.length} 篇记录的链接清单…`);
  try {
    const result = await api(`/api/imports/${encodeURIComponent(id)}/links/audit`, jsonBody("POST", { selected_paths: paths }));
    if (!current()) return;
    renderLinkAudit(result);
    setStatus("#import-link-audit-status", "链接清单核对完成，未导入资料或修改原文。", "success");
  } catch (error) { if (current()) setStatus("#import-link-audit-status", `链接核对未完成：${error.message} 已保留当前选择，可重试。`, "error"); }
  finally { auditing = false; syncControls(); }
}

const PLAN_STATE_LABELS = { ready: "已导入，可本地化", "not-imported": "尚未导入", "user-edited": "本地已修改", modified: "与导出包不同" };

function planSizeText(bytes) {
  const value = linkCount(bytes);
  if (value < 1024) return `${value} B`;
  if (value >= 1048576) return `${(value / 1048576).toFixed(1)} MB`;
  return `${(value / 1024).toFixed(1)} KB`;
}

function renderAttachmentPlan(result) {
  const summary = result.summary || {};
  const notes = Array.isArray(result.notes) ? result.notes : [];
  const warnings = Array.isArray(result.warnings) ? result.warnings : [];
  const rows = notes.slice(0, PAGE_LIMIT);
  $("#import-attachment-plan-result").innerHTML = `<dl class="import-link-counts"><div><dt>可本地化附件</dt><dd>${linkCount(summary.supported)}</dd></div><div><dt>计划复制</dt><dd>${escapeHtml(planSizeText(summary.copy_bytes))}</dd></div><div><dt>跳过</dt><dd>${linkCount(summary.skipped)}</dd></div><div><dt>待导入笔记</dt><dd>${linkCount(summary.notes_needing_import)}</dd></div><div><dt>被修改笔记</dt><dd>${linkCount(summary.notes_blocked)}</dd></div></dl><p class="field-help" data-attachment-plan-summary>${escapeHtml(result.note || "预览不写任何文件。")} 这只是预览：不会复制附件、不改写原文。执行时附件会按内容哈希复制到 80-Wolai/attachments/ 并改写链接。</p>${rows.map((note) => `<article class="import-plan-note"><strong>${escapeHtml(note.source)}</strong><p class="meta">${escapeHtml(PLAN_STATE_LABELS[note.state] || note.state)}${note.state_reason ? ` · ${escapeHtml(note.state_reason)}` : ""}</p>${(Array.isArray(note.items) ? note.items : []).slice(0, PAGE_LIMIT).map((item) => `<p class="meta">第 ${escapeHtml(item.line)} 行：<code>${escapeHtml(item.target)}</code> → ${item.supported ? `复制到 <code>${escapeHtml(item.copy_to)}</code>，链接改为 <code>${escapeHtml(item.new_target)}</code>` : escapeHtml(item.skipped_reason || "跳过")}</p>`).join("") || '<p class="meta">这篇记录没有引用 ZIP 内附件。</p>'}</article>`).join("")}${warnings.length ? `<div class="inline-warning"><ul>${warnings.slice(0, 10).map((warning) => `<li>${escapeHtml(warningText(warning))}</li>`).join("")}</ul></div>` : ""}${rows.length < notes.length ? `<p class="meta">另有 ${notes.length - rows.length} 篇未显示，可用搜索缩小范围。</p>` : ""}`;
}

async function planImportAttachments() {
  if (state.config?.capabilities?.attachment_plan !== true) return;
  const id = $("#import-package").value;
  const paths = [...selectedPaths];
  if (planning || applying || previewing || auditing || loadingPackages || id !== previewPackage) return;
  if (!paths.length || paths.length > SELECTION_LIMIT || paths.some((item) => !eligiblePaths.has(item))) {
    return toast("请选择 1–20 篇明确允许导入的 Markdown 记录后预览附件。", "error");
  }
  clearLinkAudit();
  const request = ++planRequest;
  const current = () => request === planRequest && previewPackage === id && $("#import-package").value === id;
  planning = true;
  syncControls();
  setStatus("#import-attachment-plan-status", `正在生成所选 ${paths.length} 篇记录的附件计划…`);
  try {
    const result = await api(`/api/imports/${encodeURIComponent(id)}/attachments/plan`, jsonBody("POST", { selected_paths: paths }));
    if (!current()) return;
    attachmentPlan = result;
    renderAttachmentPlan(result);
    setStatus("#import-attachment-plan-status", "附件计划已生成；未复制附件、未修改原文。", "success");
  } catch (error) {
    if (current()) setStatus("#import-attachment-plan-status", `附件预览未完成：${error.message} 已保留当前选择，可重试。`, "error");
  } finally { planning = false; syncControls(); }
}

function renderAttachmentLocalizeResult(result) {
  const counts = result.counts || {};
  const notes = Array.isArray(result.notes) ? result.notes.slice(0, PAGE_LIMIT) : [];
  const skipped = Array.isArray(result.skipped_notes) ? result.skipped_notes.slice(0, PAGE_LIMIT) : [];
  $("#import-attachment-plan-result").innerHTML = `<dl class="import-link-counts"><div><dt>已本地化笔记</dt><dd>${linkCount(counts.notes_localized)}</dd></div><div><dt>新复制附件</dt><dd>${linkCount(counts.attachments_copied)}</dd></div><div><dt>复用附件</dt><dd>${linkCount(counts.attachments_reused)}</dd></div><div><dt>复制字节</dt><dd>${escapeHtml(planSizeText(counts.bytes_copied))}</dd></div><div><dt>跳过笔记</dt><dd>${linkCount(counts.notes_skipped)}</dd></div></dl><p class="field-help" data-attachment-localize-summary>原 ZIP、Wolai 原空间与其它文件未改动；改写前的笔记已存入 .wolai-import-history/。下次扫描会按“来源变化”重新生成候选，需要你重新审核。</p><ol class="import-link-issues">${notes.map((note) => `<li><strong>已本地化</strong><p class="meta">来源：<code>${escapeHtml(note.source)}</code></p>${(Array.isArray(note.items) ? note.items : []).slice(0, PAGE_LIMIT).map((item) => `<p class="meta">第 ${escapeHtml(item.line)} 行 → <code>${escapeHtml(item.new_target)}</code></p>`).join("")}</li>`).join("")}</ol>${skipped.length ? `<div class="inline-warning"><ul>${skipped.map((note) => `<li>${escapeHtml(note.source)}：${escapeHtml(PLAN_STATE_LABELS[note.state] || note.state)}${note.state_reason ? ` · ${escapeHtml(note.state_reason)}` : ""}</li>`).join("")}</ul></div>` : ""}`;
}

async function applyAttachmentPlan() {
  if (state.config?.capabilities?.attachment_localize !== true) return;
  const id = $("#import-package").value;
  if (applyingPlan || planning || applying || previewing || auditing || loadingPackages || id !== previewPackage) return;
  if (!attachmentPlan || !Array.isArray(attachmentPlan.notes)) return;
  const plannable = attachmentPlan.notes.filter((note) => note.plannable);
  if (!plannable.length) return;
  const confirmed = await confirmAction({
    title: "执行附件本地化？",
    message: `将复制约 ${linkCount(attachmentPlan.summary?.supported)} 个附件到 80-Wolai/attachments/，并改写 ${plannable.length} 篇笔记的链接（改写前先存历史副本）。原 ZIP 与 Wolai 原空间不动；失败会整批回滚。`,
    confirmLabel: "执行本地化", cancelLabel: "先不执行", trigger: $("#apply-attachment-plan"),
  });
  if (!confirmed) return;
  const paths = [...selectedPaths];
  applyingPlan = true;
  syncControls();
  setStatus("#import-attachment-plan-status", "正在复制附件并改写链接…");
  try {
    const result = await api(`/api/imports/${encodeURIComponent(id)}/attachments/apply`, jsonBody("POST", { selected_paths: paths }));
    renderAttachmentLocalizeResult(result);
    setStatus("#import-attachment-plan-status",
      `本地化完成：${linkCount(result.counts?.notes_localized)} 篇笔记 · 新复制 ${linkCount(result.counts?.attachments_copied)} 个附件 · 复用 ${linkCount(result.counts?.attachments_reused)} 个。`,
      "success");
    attachmentPlan = null;
    changed();
    await loadDocuments();
  } catch (error) {
    setStatus("#import-attachment-plan-status", `本地化未完成：${error.message} 计划与选择仍保留，可核对后重试。`, "error");
  } finally { applyingPlan = false; syncControls(); }
}

async function applyImport() {
  const id = $("#import-package").value;
  const paths = [...selectedPaths];
  if (applying || previewing || loadingPackages || auditing || planning || id !== previewPackage) return;
  if (!paths.length || paths.length > SELECTION_LIMIT || paths.some((item) => !eligiblePaths.has(item))) return toast("请选择 1–20 篇明确允许导入的 Markdown 记录。", "error");
  applying = true;
  syncControls();
  setStatus("#import-status", `正在导入已选择的 ${paths.length} 篇记录…`);
  $("#import-result").replaceChildren();
  try {
    const result = await api(`/api/imports/${encodeURIComponent(id)}/apply`, jsonBody("POST", { selected_paths: paths }));
    const errorCount = count(result.errors ?? 0);
    setStatus("#import-status", `处理完成 · 新增 ${count(result.created ?? result.imported)} · 更新 ${count(result.updated ?? 0)} · 未变 ${count(result.unchanged ?? 0)} · 跳过 ${count(result.skipped ?? 0)} · 错误 ${errorCount}`, errorCount === 0 ? "success" : "warning");
    $("#import-result").innerHTML = '<p class="field-help">导入内容仍是候选，未经确认不会参与问答。附件仍保留在原 ZIP 中。</p><details><summary>查看本次处理摘要</summary><pre class="import-result-json"></pre></details>';
    $(".import-result-json", $("#import-result")).textContent = JSON.stringify(result, null, 2);
    selectedPaths.clear();
    clearLinkAudit();
    clearImportStates();
    renderFiles();
    changed();
    await loadDocuments();
    toast(errorCount === 0 ? "所选记录已处理，请到记忆页审核候选。" : "导入处理完成，请查看错误摘要。", errorCount === 0 ? "success" : "warning");
  } catch (error) { setStatus("#import-status", `导入未完成：${error.message} 已保留你的选择。`, "error"); }
  finally { applying = false; syncControls(); }
}

export function initImports() {
  document.addEventListener('config-changed', syncControls);
  $("#refresh-imports").addEventListener("click", loadImports);
  $("#import-package").addEventListener("change", () => { resetPreview(); setStatus("#import-status", "生成清单后再选择记录；不会自动导入。"); });
  $("#preview-import").addEventListener("click", previewImport);
  $("#apply-import").addEventListener("click", applyImport);
  $("#audit-import-links").addEventListener("click", auditImportLinks);
  $("#plan-import-attachments").addEventListener("click", planImportAttachments);
  $("#apply-attachment-plan").addEventListener("click", applyAttachmentPlan);
  $("#check-import-history").addEventListener("click", () => loadImportHistory());
  $("#preview-history-cleanup").addEventListener("click", previewHistoryCleanup);
  $("#import-history-result").addEventListener("click", (event) => {
    const button = event.target.closest("[data-history-restore]");
    if (button) return restoreImportHistory(button);
    const scan = event.target.closest("[data-history-scan]");
    if (scan) return scanForImportHistory();
    return null;
  });
  $("#check-import-states").addEventListener("click", checkImportStates);
  $("#import-search").addEventListener("input", renderFiles);
  $("#import-selected-only").addEventListener("change", renderFiles);
  $("#clear-import-selection").addEventListener("click", () => { if (!applying && !previewing && !auditing && !planning && !loadingPackages) { selectedPaths.clear(); clearLinkAudit(); clearImportHistory(); renderFiles(); } });
  $("#import-file-list").addEventListener("change", (event) => {
    const input = event.target.closest("[data-import-path]");
    if (!input) return;
    const path = input.dataset.importPath;
    if (applying || previewing || auditing || planning || loadingPackages || !eligiblePaths.has(path)) { input.checked = selectedPaths.has(path); return; }
    if (input.checked && selectedPaths.size >= SELECTION_LIMIT && !selectedPaths.has(path)) { input.checked = false; toast("每批最多选择 20 篇，可分批导入。", "warning"); return; }
    if (input.checked) selectedPaths.add(path); else selectedPaths.delete(path);
    clearLinkAudit();
    clearImportHistory();
    if ($("#import-selected-only").checked) renderFiles(); else syncControls();
  });
}
