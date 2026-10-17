import { $, state, api, escapeHtml, jsonBody, renderState, formatDate, setStatus, toast, confirmAction } from "./core.js";
import { NAV_LABELS, applyNavOrder, clearOrder, currentNavOrder, moveItem, storeOrder } from "./nav-order.js";

let loadId = 0;
let backupLoadId = 0;
let backupBusy = false;
const diagnosticsAvailable = () => state.config?.capabilities?.diagnostics === true;
const auditSummaryAvailable = () => state.config?.capabilities?.audit_summary === true;
const exportBundleAvailable = () => state.config?.capabilities?.export_bundle === true;
const backupPlanAvailable = () => state.config?.capabilities?.backup_plan === true;
const restoredAvailable = () => state.config?.capabilities?.restored_copies === true;
const modelKeyWriteAvailable = () => state.config?.capabilities?.model_key_write === true;

function sizeText(bytes) {
  const value = Math.max(0, Number(bytes) || 0);
  if (value === 0) return "0 KB";
  return value >= 1048576 ? `${(value / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(value / 1024))} KB`;
}

async function previewRetention() {
  if (!backupPlanAvailable()) return;
  const button = $("#preview-retention");
  if (button.disabled) return;
  const keep = Number($("#backup-keep-count").value);
  if (!Number.isInteger(keep) || keep < 1 || keep > 200) {
    setStatus("#retention-feedback", "保留份数需要是 1–200 的整数。", "error");
    return;
  }
  button.disabled = true;
  setStatus("#retention-feedback", "正在生成只读预览…");
  try {
    const plan = await api(`/api/backups/plan?keep=${keep}`);
    const keepList = Array.isArray(plan.keep_list) ? plan.keep_list : [];
    const removeList = Array.isArray(plan.remove_list) ? plan.remove_list : [];
    const line = (item) => `<li><code>${escapeHtml(item.filename)}</code><span>${escapeHtml(sizeText(item.bytes))} · ${escapeHtml(formatDate(item.modified_at))}</span></li>`;
    $("#retention-panel").innerHTML =
      `<p class="meta">共 ${escapeHtml(plan.total)} 份（${escapeHtml(sizeText(plan.total_bytes))}）· 保留最近 ${escapeHtml(plan.keep)} 份（${escapeHtml(sizeText(plan.keep_bytes))}）· 计划外 ${escapeHtml(removeList.length)} 份（${escapeHtml(sizeText(plan.remove_bytes))}）</p>` +
      `<div class="profile-pool"><div><h3>将保留</h3>${keepList.length ? `<ul>${keepList.map(line).join("")}</ul>` : '<p class="empty-state">没有备份。</p>'}</div><div><h3>计划外（预览中不会删除）</h3>${removeList.length ? `<ul>${removeList.map(line).join("")}</ul>` : '<p class="empty-state">当前份数不超过保留数，没有计划外备份。</p>'}</div></div>` +
      `<p class="field-help">${escapeHtml(plan.note || "只读预览：不会删除或移动任何备份文件。")}</p>`;
    $("#retention-panel").hidden = false;
    setStatus("#retention-feedback", "预览完成；没有删除任何文件。", "success");
  } catch (error) {
    renderState($("#retention-panel"), "error", error.message, "retention");
    $("#retention-panel").hidden = false;
    setStatus("#retention-feedback", "预览失败。", "error");
  } finally {
    button.disabled = !backupPlanAvailable();
  }
}

async function loadRestored() {
  if (!restoredAvailable()) return;
  const button = $("#list-restored");
  if (button.disabled) return;
  button.disabled = true;
  setStatus("#restored-feedback", "正在读取恢复副本…");
  renderState($("#restored-panel"), "loading", "正在统计恢复副本数量与大小…");
  try {
    const result = await api("/api/backups/restored");
    const copies = Array.isArray(result.copies) ? result.copies : [];
    const line = (item) => `<li><code>${escapeHtml(item.name)}</code><span>${escapeHtml(formatDate(item.modified_at))} · ${escapeHtml(sizeText(item.total_bytes))} · ${escapeHtml(Number(item.file_count) || 0)} 个文件 · Vault ${escapeHtml(Number(item.vault_files) || 0)} 个${item.has_database ? "" : " · 缺少数据库"}</span>${item.has_database ? `<button class="text-button" data-verify-restored="${escapeHtml(item.name)}">验证副本</button><span class="status-message" data-verify-status="${escapeHtml(item.name)}" role="status"></span>` : ""}</li>`;
    $("#restored-panel").innerHTML =
      `<p class="meta">共 ${escapeHtml(Number(result.total) || 0)} 份（${escapeHtml(sizeText(result.total_bytes))}）</p>` +
      (copies.length
        ? `<ul class="history-list">${copies.map(line).join("")}</ul>`
        : '<p class="empty-state">还没有恢复副本。备份列表里点“恢复为验证副本”之后，会出现在这里；它不会覆盖当前数据。</p>') +
      `<p class="field-help">${escapeHtml(result.note || "只读列表：不会删除、移动或切换任何数据。")}</p>`;
    $("#restored-panel").hidden = false;
    setStatus("#restored-feedback", "读取完成；没有改动任何数据。", "success");
  } catch (error) {
    renderState($("#restored-panel"), "error", error.message, "restored");
    $("#restored-panel").hidden = false;
    setStatus("#restored-feedback", "读取失败。", "error");
  } finally {
    button.disabled = !restoredAvailable();
  }
}

async function verifyRestored(name) {
  if (!/^[0-9a-f]{32}$/.test(String(name || ""))) return;
  const status = document.querySelector(`[data-verify-status="${name}"]`);
  if (status) { status.textContent = "正在验证副本…"; status.className = "status-message"; }
  try {
    const result = await api(`/api/backups/restored/${name}/verify`);
    if (status) {
      status.textContent = `验证通过：SQLite 完整（schema v${Number(result.sqlite_user_version) || 0}）· ${Number(result.file_count) || 0} 个文件 · Vault ${Number(result.vault_files) || 0} 个`;
      status.classList.add("success");
    }
  } catch (error) {
    if (status) { status.textContent = `验证失败：${error.message}`; status.classList.add("error"); }
  }
}

const EVENT_LABELS = {
  vault_scan: "资料扫描", note_created: "新建记录", chat_completed: "问答完成", model_error: "模型调用错误",
  memory_status_changed: "记忆审核变更", memory_exported: "记忆导出",
  backup_created: "创建备份", backup_restored_to_new_directory: "恢复为验证副本", source_reextracted: "资料重新提取",
  goal_created: "新建目标", goal_updated: "更新目标", goal_deleted: "删除目标",
  task_created: "新建任务", task_updated: "更新任务", task_deleted: "删除任务",
  task_trash_purged: "彻底删除回收站任务",
  journal_saved: "保存日记", journal_task_created: "日记转任务", journal_exported: "日记快照导出",
  journal_bulk_exported: "日记全量导出", markdown_tasks_imported: "导入 Markdown 待办",
  study_subject_created: "新建学习科目", study_subject_updated: "更新学习科目",
  study_session_created: "新建学习记录", study_session_updated: "更新学习记录",
  habit_created: "新建习惯", habit_updated: "更新习惯", habit_entry_saved: "保存习惯打卡",
  body_entry_saved: "保存身体记录", body_entry_cleared: "清除身体记录", body_exported: "身体记录导出",
  body_summary_exported: "身体记录汇总导出",
  planning_exported: "目标与任务导出", growth_exported: "习惯与学习导出",
  conversations_exported: "对话导出", export_bundle_created: "一键导出包",
  scheduler_report_created: "生成定时草稿", scheduler_loop_error: "调度循环错误",
  dida_sync_completed: "滴答同步完成", dida_sync_failed: "滴答同步失败",
  dida_sync_settings_updated: "更新滴答设置", dida_sync_credentials_cleared: "清除滴答凭据",
  dida_sync_keep_local: "滴答冲突保留本地",
  wolai_notes_imported: "导入 Wolai 记录",
};

function eventLabel(eventType) {
  const type = String(eventType || "");
  return EVENT_LABELS[type] ? `${EVENT_LABELS[type]}（${type}）` : type;
}

function downloadExportBundle() {
  if (!exportBundleAvailable()) return;
  location.assign("/api/export/all.zip");
  toast("已开始下载可读导出包（ZIP），它用于阅读与迁移，不用于恢复。", "success");
}

let currentSettingsSection = "status";

function showSettingsSection(name, { focus = true } = {}) {
  const sections = Array.from(document.querySelectorAll("[data-settings-section]"));
  if (!sections.length) return;
  const target = sections.find((section) => section.dataset.settingsSection === name) || sections[0];
  currentSettingsSection = target.dataset.settingsSection;
  sections.forEach((section) => { section.hidden = section !== target; });
  document.querySelectorAll("[data-settings-nav]").forEach((button) => {
    const active = button.dataset.settingsNav === currentSettingsSection;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", String(active));
  });
  if (focus) target.querySelector("h2, h3")?.focus?.();
}

// The last user action stays visible: a background refresh redraws the list and
// must not silently swap the result message back to the generic hint.
let navOrderNotice = { message: "", tone: "" };

function renderNavOrder(message, tone = "") {
  const list = $("#nav-order-list");
  if (!list) return;
  // Keep the keyboard cursor on the same row button when the list is redrawn
  // (a late settings refresh must not drop focus back to the page body).
  const active = document.activeElement;
  const insideList = Boolean(active?.closest?.("#nav-order-list"));
  const activeKey = insideList ? active.dataset.navKey : null;
  const activeMove = insideList ? Number(active.dataset.navMove) : 0;
  if (message) navOrderNotice = { message, tone };
  const order = currentNavOrder();
  list.innerHTML = order.map((key, index) => {
    const label = NAV_LABELS[key] || key;
    return `<li><span>${escapeHtml(label)}</span><div class="nav-order-actions"><button type="button" class="small-button" data-nav-move="-1" data-nav-key="${escapeHtml(key)}" ${index === 0 ? "disabled" : ""} aria-label="上移${escapeHtml(label)}">↑</button><button type="button" class="small-button" data-nav-move="1" data-nav-key="${escapeHtml(key)}" ${index === order.length - 1 ? "disabled" : ""} aria-label="下移${escapeHtml(label)}">↓</button></div></li>`;
  }).join("");
  setStatus("#nav-order-feedback", navOrderNotice.message || "顺序保存在本机浏览器；可随时恢复默认。", navOrderNotice.tone);
  if (activeKey) focusNavMoveButton(activeKey, activeMove);
}

// 列表每次移动都会重绘，焦点要回到同一项的同方向按钮（到底时回退到另一方向），
// 否则键盘用户点击一次“上移”后焦点会掉回页面开头。
function focusNavMoveButton(key, delta) {
  const buttons = Array.from(document.querySelectorAll("[data-nav-move]"));
  const same = buttons.find((button) => button.dataset.navKey === key && Number(button.dataset.navMove) === delta && !button.disabled);
  const fallback = buttons.find((button) => button.dataset.navKey === key && !button.disabled);
  (same || fallback)?.focus?.();
}

async function saveModelKey(event) {
  event.preventDefault();
  if (!modelKeyWriteAvailable()) return;
  const input = $("#model-key-input");
  const value = String(input.value || "").trim();
  if (!/^[A-Za-z0-9._\-]{8,200}$/.test(value)) {
    setStatus("#model-key-feedback", "密钥需要 8–200 个字母、数字、点、下划线或连字符；不会写入文件。", "error");
    return;
  }
  const confirmed = await confirmAction({
    title: "保存新的 API Key？",
    message: "密钥会写入本机 .env 的 MODEL_API_KEY 并立即在本机生效；不会保存到数据库、备份或导出，也不会回显。",
    confirmLabel: "写入本地 .env",
  });
  if (!confirmed) return;
  try {
    const result = await api("/api/config/model-key", jsonBody("POST", { api_key: value, confirm: true }));
    input.value = "";
    setStatus("#model-key-feedback", result.configured
      ? "已写入本地 .env 并立即生效；密钥不回显。"
      : "已写入本地 .env，但模型配置仍不完整：请检查 .env 的 MODEL_PROVIDER / MODEL_BASE_URL / MODEL_NAME。", result.configured ? "success" : "warning");
    toast("密钥已保存到本地 .env。", "success");
    await loadSettings();
  } catch (error) {
    input.value = "";
    setStatus("#model-key-feedback", `保存失败：${error.message}`, "error");
  }
}

function renderAuditSummary(payload) {
  const chat = payload.chat || {};
  const dayCount = Number(payload.window_days) || 30;
  const summary = [`近 ${dayCount} 天共 ${Number(payload.total_events) || 0} 条本地操作`];
  summary.push(`云端回答 ${Number(chat.cloud) || 0}`);
  summary.push(`本地回答 ${Number(chat.local) || 0}`);
  if (Number(chat.fallback)) summary.push(`本地回退 ${Number(chat.fallback)}`);
  if (Number(chat.other)) summary.push(`模式未标注 ${Number(chat.other)}`);
  summary.push(`导出 ${Number(payload.exports) || 0}`);
  summary.push(`备份 ${Number(payload.backups) || 0}`);
  summary.push(`错误 ${Number(payload.errors) || 0}`);
  const byType = Array.isArray(payload.by_type) ? payload.by_type : [];
  const latest = payload.latest_error;
  $("#audit-summary-panel").innerHTML = `<p class="meta">${escapeHtml(summary.join(" · "))}</p>` +
    (latest
      ? `<p class="inline-warning">最近错误事件：${escapeHtml(latest.event_type || "未知")}（${escapeHtml(formatDate(latest.created_at))}）；详情只保留在本地审计表。</p>`
      : '<p class="meta">窗口内没有记录到模型或调度错误事件。</p>') +
    (byType.length
      ? `<ul class="history-list">${byType.slice(0, 12).map((item) => `<li><code>${escapeHtml(eventLabel(item.event_type))}</code><span>${escapeHtml(Number(item.count) || 0)} 次 · 最近 ${escapeHtml(formatDate(item.last_at))}</span></li>`).join("")}</ul>`
      : '<p class="empty-state">这个窗口内还没有审计事件。</p>') +
    `<p class="field-help">${escapeHtml(payload.note || "只读统计：只有事件类型与数量，不含正文、问题、标题或路径。")}</p>`;
}

async function loadAuditSummary() {
  const button = $("#refresh-audit-summary");
  if (!auditSummaryAvailable()) { if (button) button.disabled = true; return; }
  if (button) button.disabled = true;
  renderState($("#audit-summary-panel"), "loading", "正在统计本地审计事件…");
  const days = Number($("#audit-summary-days")?.value) || 30;
  try {
    renderAuditSummary(await api(`/api/audit/summary?days=${days}`));
  } catch (error) {
    renderState($("#audit-summary-panel"), "error", error.message, "audit-summary");
  } finally {
    if (button) button.disabled = !auditSummaryAvailable();
  }
}

function renderDiagnostics(payload) {
  const counts = payload.counts || {};
  const vault = payload.vault || {};
  const backups = payload.backups || {};
  const latest = backups.latest || {};
  const relationshipFiles = Number(backups.verification?.relationship_files);
  const line = (label, value) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`;
  $("#diagnostics-panel").innerHTML = `<dl class="settings-list">` +
    line("数据库完整性", payload.integrity === "ok" ? "ok（通过）" : String(payload.integrity || "未知")) +
    line("资料（可用）", `${counts.documents_available ?? 0} 份`) +
    line("记忆", `已确认 ${counts.memories_active ?? 0} · 待确认 ${counts.memories_candidate ?? 0} · 已拒绝 ${counts.memories_rejected ?? 0} · 已归档 ${counts.memories_archived ?? 0}`) +
    line("对话 / 消息", `${counts.conversations ?? 0} / ${counts.messages ?? 0}`) +
    line("目标 / 任务", `${counts.goals ?? 0} / ${counts.tasks ?? 0}`) +
    line("日记 / 习惯打卡 / 学习 / 身体记录", `${counts.journal_entries ?? 0} / ${counts.habit_entries ?? 0} / ${counts.study_sessions ?? 0} / ${counts.body_entries ?? 0}`) +
    line("收件箱草稿", `${counts.inbox_items ?? 0} 条`) +
    line("Vault Markdown", `${vault.markdown_files ?? 0} 个${vault.relationships_present ? "（检测到关系分区，未计入）" : ""}`) +
    line("本地备份", backups.count ? `${backups.count} 份 · 最近 ${latest.filename || "备份"}（${formatDate(latest.modified_at)}）` : "还没有备份") +
    line("最近备份校验", backups.verification?.checked ? (backups.verification.ok ? "通过（路径、哈希、SQLite 完整性）" : `未通过：${backups.verification.error || "原因未知"}`) : "还没有备份可校验") +
    line("语义检索", payload.embedding_provider ? `本地向量（${payload.embedding_provider}）` : "未启用（词法检索）") +
    `</dl>` +
    (relationshipFiles > 0
      ? `<p class="inline-warning">注意：最近备份包含关系分区明文文件 ${escapeHtml(relationshipFiles)} 个。备份是本地明文 ZIP，请勿把它带到不受控位置；是否把关系分区排除在备份之外仍待你确认。</p>`
      : "") +
    `<p class="field-help">自检只输出数量与完整性结果，不含任何记录正文；生成时间 ${escapeHtml(formatDate(payload.generated_at))}（版本 ${escapeHtml(payload.version || "未报告")}）。</p>`;
}

async function runDiagnostics() {
  if (!diagnosticsAvailable()) return;
  const button = $("#run-diagnostics");
  if (button.disabled) return;
  button.disabled = true;
  renderState($("#diagnostics-panel"), "loading", "正在做只读自检（完整性、计数、备份）…");
  try {
    renderDiagnostics(await api("/api/diagnostics/summary"));
    toast("数据与恢复自检完成。", "success");
  } catch (error) {
    renderState($("#diagnostics-panel"), "error", error.message, "diagnostics");
  } finally {
    button.disabled = !diagnosticsAvailable();
  }
}

export async function loadSettings() {
  const request = ++loadId;
  $("#refresh-settings").disabled = true;
  renderState($("#settings-panel"), "loading", "正在检查本地服务与模型配置…");
  const [health, config] = await Promise.allSettled([api("/api/health"), api("/api/config/status")]);
  if (request !== loadId) return;
  $("#refresh-settings").disabled = false;
  const healthy = health.status === "fulfilled" && health.value.status === "ok";
  const environment = health.status === "fulfilled" ? health.value.runtime_label : null;
  const banner = $("#environment-banner");
  if (banner) {
    banner.hidden = environment !== "qa";
    banner.textContent = environment === "qa" ? "隔离验收环境 · 使用独立测试资料，云端模型已禁用；这里的操作不会写入你的日常工作区。" : "";
  }
  $("#service-label").textContent = healthy ? "本地服务已连接" : "本地服务未连接";
  const versionLabel = $("#service-version");
  if (versionLabel) {
    versionLabel.textContent = healthy && health.value.version ? `v${health.value.version}` : "版本未读取";
  }
  $("#service-dot").classList.toggle("connected", healthy);
  if (config.status === "fulfilled") {
    state.config = config.value;
    state.configLoaded = true;
    document.dispatchEvent(new CustomEvent("torch:config"));
  } else {
    state.config = null;
    state.configLoaded = true;
    document.dispatchEvent(new CustomEvent("torch:config"));
  }
  const statusRows = [
    // Read-only capabilities also gate controls when the UI is newer than a running service.
    ["工作区", environment === "qa" ? "隔离验收工作区（强制本地模式）"
      : config.status === "fulfilled" && config.value.vault_scoped
        ? `Obsidian 库：${config.value.vault_name || "当前库"}`
        : environment === "personal" ? "个人工作区" : "尚未确认"],
    ["本地服务", healthy ? "已连接" : health.status === "rejected" ? health.reason.message : "服务状态异常"],
    ["版本", health.status === "fulfilled" ? health.value.version || "未报告" : "未读取"],
    ["语义检索", config.status === "fulfilled" ? (config.value.capabilities?.semantic_search ? `已启用（${config.value.embedding_provider || "本地向量"}）` : "词法检索（未启用本地向量）") : "未读取"],
    // 对话统一在 dsh（2026-10-05）：产品里不再有自己的对话入口。
    ["对话", "在 dsh 里进行（本工作台不再自带对话页）"],
    ["最近检查", formatDate(new Date().toISOString())],
  ];
  const modelRows = [
    ["模型配置", config.status === "fulfilled" ? config.value.model_configured ? "配置完整；当前配置不代表本次调用成功" : "未配置完整，使用本地问答" : config.reason.message],
    ["服务提供方", state.config?.provider || "未配置"],
    ["模型名称", state.config?.model_name || "未配置"],
    ["配置状态", typeof state.config?.state === "string" ? state.config.state : "以本页检查结果为准"],
    ["密钥位置", "只保存在本地 .env 的 MODEL_API_KEY；本页不回显已保存的密钥，写入后立即生效。"],
  ];
  const list = (rows) => `<dl class="settings-list">${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>`;
  $("#settings-panel").innerHTML = list(statusRows) + (!healthy || config.status === "rejected" ? '<div class="panel-footer"><button class="text-button" data-retry="settings">重新检查</button></div>' : "");
  const modelPanel = $("#model-status-panel");
  if (modelPanel) modelPanel.innerHTML = list(modelRows);
  const modelBadge = $("#model-status-badge");
  if (modelBadge) {
    const configured = config.status === "fulfilled" && config.value.model_configured;
    modelBadge.textContent = configured ? "云端已配置" : "仅本地模式";
    modelBadge.className = `badge ${configured ? "active" : "candidate"}`;
  }
  const diagnosticsButton = $("#run-diagnostics");
  if (diagnosticsButton) diagnosticsButton.disabled = !diagnosticsAvailable();
  const bundleButton = $("#download-export-bundle");
  if (bundleButton) bundleButton.disabled = !exportBundleAvailable();
  const retentionButton = $("#preview-retention");
  if (retentionButton) retentionButton.disabled = !backupPlanAvailable();
  const restoredButton = $("#list-restored");
  if (restoredButton) restoredButton.disabled = !restoredAvailable();
  const keyPanel = $("#model-key-panel");
  if (keyPanel) keyPanel.hidden = !modelKeyWriteAvailable();
  document.dispatchEvent(new CustomEvent('config-changed'));
  await loadBackups();
  await loadAuditSummary();
  renderNavOrder();
}

async function loadBackups() {
  const request = ++backupLoadId;
  renderState($("#backup-list"), "loading", "正在读取本地备份列表…");
  try {
    const result = await api("/api/backups");
    if (request !== backupLoadId) return;
    const rows = Array.isArray(result) ? result : result.backups || [];
    if (!rows.length) return renderState($("#backup-list"), "empty", "还没有本地备份。点击“创建备份”，保存当前资料与数据库的副本。");
    $("#backup-list").innerHTML = rows.map((item) => {
      const bytes = Math.max(0, Number(item.bytes) || 0);
      const size = bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
      return `<article class="document-card"><div><h3>${escapeHtml(item.filename)}</h3><p class="meta">${escapeHtml(formatDate(item.modified_at))} · ${size}</p></div><button class="small-button" data-restore-backup="${escapeHtml(item.filename)}" ${backupBusy ? "disabled" : ""}>恢复为验证副本</button></article>`;
    }).join("");
  } catch (error) { if (request === backupLoadId) renderState($("#backup-list"), "error", error.message, "backups"); }
}

async function runBackup(filename = null) {
  if (backupBusy) return;
  backupBusy = true;
  $("#create-backup").disabled = true;
  document.querySelectorAll("[data-restore-backup]").forEach((button) => { button.disabled = true; });
  const feedback = $("#backup-feedback");
  feedback.textContent = filename ? "正在校验备份并恢复到独立目录…" : "正在创建本地备份…";
  feedback.className = "backup-feedback";
  try {
    const path = filename ? `/api/backups/${encodeURIComponent(filename)}/restore` : "/api/backups";
    const result = await api(path, { method: "POST" });
    feedback.textContent = filename ? `验证副本已创建：${result.target_dir || "独立恢复目录"}。当前数据未被覆盖，也未自动切换。` : `备份已创建：${result.filename || "本地备份"}。`;
    feedback.classList.add("success");
    toast(filename ? "已恢复为独立验证副本。" : "本地备份已完成。", "success");
    await loadBackups();
    if (filename && restoredAvailable()) await loadRestored();
  } catch (error) { feedback.textContent = `操作未完成：${error.message}`; feedback.classList.add("error"); }
  finally { backupBusy = false; $("#create-backup").disabled = false; document.querySelectorAll("[data-restore-backup]").forEach((button) => { button.disabled = false; }); }
}

export function initSettings() {
  $("#refresh-settings").addEventListener("click", loadSettings);
  $("#create-backup").addEventListener("click", () => runBackup());
  $("#run-diagnostics")?.addEventListener("click", runDiagnostics);
  $("#refresh-audit-summary")?.addEventListener("click", loadAuditSummary);
  $("#audit-summary-days")?.addEventListener("change", loadAuditSummary);
  $("#download-export-bundle")?.addEventListener("click", downloadExportBundle);
  $("#preview-retention")?.addEventListener("click", previewRetention);
  $("#list-restored")?.addEventListener("click", loadRestored);
  $("#model-key-form")?.addEventListener("submit", saveModelKey);
  document.querySelectorAll("[data-settings-nav]").forEach((button) => {
    button.addEventListener("click", () => showSettingsSection(button.dataset.settingsNav));
  });
  document.addEventListener("click", (event) => {
    const move = event.target.closest("[data-nav-move]");
    if (move) {
      const key = move.dataset.navKey;
      const delta = Number(move.dataset.navMove);
      const order = moveItem(currentNavOrder(), key, delta);
      storeOrder(order);
      applyNavOrder();
      renderNavOrder(`“${NAV_LABELS[key] || key}”已移到第 ${order.indexOf(key) + 1} 位（共 ${order.length} 项）。`);
      focusNavMoveButton(key, delta);
      return;
    }
    if (event.target.closest("#nav-order-reset")) {
      clearOrder();
      applyNavOrder();
      renderNavOrder("已恢复默认顺序。", "success");
      return;
    }
    if (event.target.closest('[data-retry="settings"]')) { showSettingsSection("status", { focus: false }); loadSettings(); }
    if (event.target.closest('[data-retry="backups"]')) loadBackups();
    if (event.target.closest('[data-retry="diagnostics"]')) runDiagnostics();
    if (event.target.closest('[data-retry="audit-summary"]')) loadAuditSummary();
    if (event.target.closest('[data-retry="retention"]')) previewRetention();
    if (event.target.closest('[data-retry="restored"]')) loadRestored();
    const verify = event.target.closest("[data-verify-restored]");
    if (verify) verifyRestored(verify.dataset.verifyRestored);
    const restore = event.target.closest("[data-restore-backup]");
    if (restore) runBackup(restore.dataset.restoreBackup);
  });
}
