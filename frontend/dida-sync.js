import { $, api, escapeHtml, formatDate, jsonBody, setStatus, toast, confirmAction, changed, state } from "./core.js";
import {
  conflictReasonText, syncBadge, syncCountsText, syncErrorText, syncWarningText,
  validateSyncForm, willArmPushDeletes,
} from "./dida-sync-model.js";

let loadId = 0;
let busy = false;
let syncing = false;
let quickStatus = null;
let quickStatusError = "";
let quickStatusRequest = 0;

const available = () => state.config?.capabilities?.dida_sync === true;

/** 时间管理模块右上角「立即同步」：跟着能力与配置状态显示/禁用。 */
function renderQuickState() {
  const buttons = [
    { node: $("#time-dida-sync"), idle: "立即同步" },
    { node: $("#habit-dida-sync"), idle: "滴答同步" },
  ].filter((entry) => entry.node);
  if (!buttons.length) return;
  if (!available()) {
    buttons.forEach(({ node }) => { node.hidden = true; });
    return;
  }
  const running = syncing || Boolean(quickStatus?.running);
  const disabled = !quickStatus
    || Boolean(quickStatusError)
    || !quickStatus.supported
    || Boolean(quickStatus.read_only)
    || !quickStatus.configured
    || running;
  const title = quickStatusError ? `无法读取滴答同步状态：${quickStatusError}`
    : !quickStatus ? "正在读取滴答同步状态…"
    : !quickStatus.supported ? "当前运行环境不支持滴答同步"
    : quickStatus.read_only ? "当前库是只读模式，不能同步"
    : !quickStatus.configured ? "请先到 设置 → 滴答同步 配置登录凭据"
    : running ? "正在从滴答清单同步…"
    : "从滴答清单同步任务与习惯";
  const fullTitle = ((quickStatus?.push_deletes || quickStatus?.push_changes)
    && !quickStatus?.csrf_set)
    ? `${title}（缺少 _csrf_token，本地删除/修改不会写回滴答）`
    : title;
  buttons.forEach(({ node, idle }) => {
    node.hidden = false;
    node.disabled = disabled;
    node.textContent = running ? "同步中…" : idle;
    node.title = fullTitle;
  });
}

async function loadQuickStatus() {
  if (!available()) return;
  const request = ++quickStatusRequest;
  try {
    const status = await api("/api/sync/dida/status");
    if (request !== quickStatusRequest) return;
    quickStatus = status;
    quickStatusError = "";
  } catch (error) {
    if (request !== quickStatusRequest) return;
    quickStatus = null;
    quickStatusError = error.message;
  }
  renderQuickState();
}

function applyCapability() {
  const nav = document.querySelector('[data-settings-nav="dida"]');
  const section = document.querySelector('[data-settings-section="dida"]');
  if (!available()) {
    if (nav) nav.hidden = true;
    if (section) section.hidden = true;
    quickStatus = null;
    quickStatusError = "";
    renderQuickState();
  } else {
    if (nav) nav.hidden = false;
    renderQuickState();
    loadQuickStatus();
  }
}

function formatWhen(value) {
  if (!value) return "从未";
  try { return formatDate(value); } catch { return value; }
}

function renderStatus(status) {
  const badge = syncBadge(status);
  const badgeNode = $("#dida-status-badge");
  if (badgeNode) {
    badgeNode.textContent = badge.text;
    badgeNode.className = `badge ${badge.tone}`;
  }
  quickStatus = status;
  quickStatusError = "";
  renderQuickState();
  const rows = [
    ["连接", status.configured ? (status.csrf_set ? "cookie 已保存（含 CSRF）" : "cookie 已保存") : "未保存 cookie"],
    ["上次成功", formatWhen(status.last_success_at)],
    ["上次尝试", formatWhen(status.last_attempt_at)],
    ["上次错误", status.last_error_code ? syncErrorText(status.last_error_code) : "无"],
    ["上次结果", status.last_run_counts && Object.keys(status.last_run_counts).length ? syncCountsText(status.last_run_counts) : "还没有运行过"],
    ["上次回写", status.last_push_at ? formatWhen(status.last_push_at) : "还没有"],
    ["回写错误", status.last_push_error_code ? syncErrorText(status.last_push_error_code) : "无"],
    ["冲突", String(status.conflicts || 0)],
    ["本地缺失", String(status.local_missing || 0)],
    ["远端已删除", String(status.remote_deleted || 0)],
    ["习惯同步", status.habits_enabled ? "已开启" : "未开启"],
    ["已关联习惯", String(status.habit_links || 0)],
    ["习惯冲突", String(status.habit_conflicts || 0)],
    ["习惯打卡链接", String(status.habit_checkin_links || 0)],
    ["打卡冲突", String(status.habit_checkin_conflicts || 0)],
    ["习惯回写", status.last_habit_push_at ? formatWhen(status.last_habit_push_at) : "还没有"],
    ["习惯回写错误", status.last_habit_push_error_code ? syncErrorText(status.last_habit_push_error_code) : "无"],
    ["下次定时", (status.enabled || status.habits_enabled) ? formatWhen(status.next_due_at) : "未启用"],
    ["设备 ID", status.device_id_prefix ? `${status.device_id_prefix}…` : "未生成"],
  ];
  const panel = $("#dida-status-panel");
  if (panel) {
    panel.innerHTML = `<dl class="settings-list">${rows.map(([label, value]) =>
      `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>`;
  }
  if ($("#dida-enabled")) $("#dida-enabled").checked = Boolean(status.enabled);
  if ($("#dida-push-deletes")) $("#dida-push-deletes").checked = Boolean(status.push_deletes);
  if ($("#dida-push-changes")) $("#dida-push-changes").checked = Boolean(status.push_changes);
  if ($("#dida-habits-enabled")) $("#dida-habits-enabled").checked = status.habits_enabled !== false;
  if ($("#dida-region")) $("#dida-region").value = status.region || "dida365";
  if ($("#dida-interval")) $("#dida-interval").value = String(status.interval_minutes || 15);
  if ($("#dida-completed-days")) $("#dida-completed-days").value = String(status.completed_days ?? 30);
  if ($("#dida-timezone")) $("#dida-timezone").value = status.timezone || "Asia/Shanghai";
  if ($("#dida-device-version")) $("#dida-device-version").value = String(status.device_version || 8000);
  if ($("#dida-cookie")) {
    $("#dida-cookie").value = "";
    $("#dida-cookie").placeholder = status.cookie_set
      ? "已保存；留空则不修改。粘贴新 cookie 可覆盖。"
      : "粘贴 t=... 或完整 Cookie";
  }
  if ($("#dida-csrf")) {
    $("#dida-csrf").value = "";
    $("#dida-csrf").placeholder = status.csrf_set
      ? "已保存；留空则不修改"
      : "本地删除写回滴答必需，从浏览器 Cookie 复制";
  }
  const test = $("#dida-test");
  const preview = $("#dida-preview");
  const run = $("#dida-run");
  const clear = $("#dida-clear");
  if (test) test.disabled = !status.configured || !status.supported;
  if (preview) preview.disabled = !status.configured || !status.supported;
  if (run) run.disabled = !status.configured || !status.supported || status.read_only || status.running;
  if (clear) clear.disabled = !status.configured;
  if (!status.supported) {
    setStatus("#dida-feedback", "当前运行环境不支持滴答同步。", "error");
  } else if (status.read_only) {
    setStatus("#dida-feedback", "当前库是只读模式：可以测试连接和预览，但不能写入任务笔记。", "warning");
  } else if ((status.push_deletes || status.push_changes) && !status.csrf_set) {
    setStatus("#dida-feedback", "本地删除/修改写回滴答需要 _csrf_token：请从浏览器 Cookie 里复制 _csrf_token 后保存。", "warning");
  }
}

function renderConflicts(rows = []) {
  const panel = $("#dida-conflicts");
  if (!panel) return;
  if (!rows.length) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  panel.hidden = false;
  const kindLabel = { task: "任务", habit: "习惯", habit_checkin: "打卡" };
  panel.innerHTML = `<h3>待处理冲突（${rows.length}）</h3><ul class="history-list">${rows.map((row) => {
    const kind = String(row.kind || "task");
    const title = row.pending_remote_title || row.last_title || (kind === "habit_checkin" ? "未命名打卡" : "未命名任务");
    const meta = [kindLabel[kind] || kind, row.date || "", row.relpath || ""].filter(Boolean).join(" · ");
    const action = kind === "task" ? "保留本地" : "保留本地";
    return `<li><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(conflictReasonText(row.conflict_reason))}</span><span class="meta">${escapeHtml(meta)}</span></div><button class="text-button" data-dida-keep-local="${escapeHtml(row.external_id)}" data-dida-kind="${escapeHtml(kind)}">${action}</button></li>`;
  }).join("")}</ul><p class="field-help">“保留本地”会把滴答当前状态记为已同步基线；习惯冲突会在下一次同步把本地版本写回滴答。不会修改本地笔记。</p>`;
}

async function loadDidaSync() {
  if (!available()) return;
  const request = ++loadId;
  setStatus("#dida-feedback", "正在读取滴答同步状态…");
  try {
    const status = await api("/api/sync/dida/status");
    if (request !== loadId) return;
    // renderStatus 会在缺 _csrf_token 等情况下写入警告，不能在读取结束后清掉。
    setStatus("#dida-feedback", "");
    renderStatus(status);
    const conflicts = status.conflicts
      ? await api("/api/sync/dida/conflicts?limit=100")
      : { conflicts: [] };
    const habitConflicts = status.habit_conflicts || status.habit_checkin_conflicts
      ? await api("/api/sync/dida/habit-conflicts?limit=100")
      : { conflicts: [] };
    if (request !== loadId) return;
    renderConflicts([...(conflicts.conflicts || []), ...(habitConflicts.conflicts || [])]);
  } catch (error) {
    if (request === loadId) setStatus("#dida-feedback", error.message, "error");
  }
}

function formPayload() {
  return {
    enabled: Boolean($("#dida-enabled")?.checked),
    region: $("#dida-region")?.value || "dida365",
    interval_minutes: Number($("#dida-interval")?.value),
    completed_days: Number($("#dida-completed-days")?.value),
    push_deletes: Boolean($("#dida-push-deletes")?.checked),
    push_changes: Boolean($("#dida-push-changes")?.checked),
    habits_enabled: Boolean($("#dida-habits-enabled")?.checked),
    timezone: $("#dida-timezone")?.value || "Asia/Shanghai",
    device_version: Number($("#dida-device-version")?.value),
    cookie: $("#dida-cookie")?.value || "",
    csrf_token: $("#dida-csrf")?.value || "",
  };
}

async function saveSettings(event) {
  event?.preventDefault();
  if (busy) return;
  const payload = formPayload();
  const status = await api("/api/sync/dida/status").catch(() => null);
  const check = validateSyncForm({
    ...payload,
    cookie_set: Boolean(status?.cookie_set),
    csrf_set: Boolean(status?.csrf_set),
  });
  if (!check.ok) {
    setStatus("#dida-feedback", check.message, "error");
    return;
  }
  if (willArmPushDeletes(payload, status)) {
    const accepted = await confirmAction({
      title: "启用“本地删除同步到滴答”？",
      message: "开启后，本机删除已同步的任务、清单或习惯会在下一次推送时从滴答删除，滴答回收站可能还能恢复；缺少 _csrf_token 时不会执行。",
      confirmLabel: "启用回写删除",
      danger: true,
    });
    if (!accepted) {
      if ($("#dida-push-deletes")) $("#dida-push-deletes").checked = Boolean(status?.push_deletes);
      setStatus("#dida-feedback", "没有启用“本地删除同步到滴答”。", "warning");
      return;
    }
  }
  const body = { ...payload };
  if (!body.cookie) delete body.cookie;
  if (!body.csrf_token) delete body.csrf_token;
  busy = true;
  setStatus("#dida-feedback", "正在保存设置…");
  try {
    await api("/api/sync/dida/settings", jsonBody("PUT", body));
    if ($("#dida-cookie")) $("#dida-cookie").value = "";
    if ($("#dida-csrf")) $("#dida-csrf").value = "";
    toast("滴答同步设置已保存。", "success");
    await loadDidaSync();
  } catch (error) {
    setStatus("#dida-feedback", error.message, "error");
  } finally {
    busy = false;
  }
}

async function testConnection() {
  if (busy) return;
  busy = true;
  setStatus("#dida-feedback", "正在测试滴答连接…");
  try {
    const result = await api("/api/sync/dida/test", { method: "POST" });
    const habitText = result.habit_count !== null && result.habit_count !== undefined
      ? `、${result.habit_count} 个习惯` : "";
    const habitWarning = result.habit_error
      ? ` 习惯接口不可用（${syncErrorText(result.habit_error)}）` : "";
    setStatus("#dida-feedback",
      `连接成功：${result.project_count} 个项目、${result.task_count} 个未完成任务${habitText}（${result.latency_ms} ms）。${habitWarning}`,
      result.habit_error ? "warning" : "success");
  } catch (error) {
    setStatus("#dida-feedback", error.message, "error");
  } finally {
    busy = false;
  }
}

function summaryText(result) {
  const counts = result.counts || {};
  const lines = [syncCountsText(counts)];
  const warnings = Array.isArray(result.warnings) ? result.warnings : [];
  if (warnings.length) lines.push(`提示：${warnings.map(syncWarningText).join("；")}`);
  return lines.join(" · ");
}

async function runSync(dryRun = false, target = "settings") {
  if (busy) return;
  busy = true;
  syncing = true;
  renderQuickState();
  const habit = target === "habit";
  const quick = target === "quick";
  const feedback = habit ? "#habit-page-status" : quick ? "#time-status" : "#dida-feedback";
  setStatus(feedback, dryRun ? "正在生成预览…" : "正在从滴答拉取任务与习惯…");
  const panel = $("#dida-summary");
  if (panel) panel.hidden = true;
  try {
    const result = await api("/api/sync/dida/run", {
      ...jsonBody("POST", { dry_run: Boolean(dryRun), force: false }),
      timeoutMs: 120000,
    });
    if (panel) {
      panel.hidden = false;
      panel.innerHTML = `<p class="meta">${dryRun ? "预览（没有写入）" : "同步完成"}：${escapeHtml(summaryText(result))}</p>`;
    }
    document.dispatchEvent(new CustomEvent("torch:tasks-changed"));
    changed();
    await loadDidaSync();
    const pushFailed = Array.isArray(result.warnings)
      && result.warnings.some((warning) => {
        const code = String(warning);
        return code.startsWith("push_") || code.startsWith("project_push_error_")
          || code === "habit_push_error" || code === "habit_delete_error";
      });
    if (habit) {
      toast(`${dryRun ? "预览完成（未写入）" : "滴答同步完成"}：${summaryText(result)}`,
        pushFailed ? "warning" : "success");
      setStatus(feedback, "");
    } else {
      setStatus(feedback,
        quick ? `${dryRun ? "预览完成（未写入）" : "同步完成"}：${summaryText(result)}`
          : dryRun ? "预览完成，没有写入任何文件。"
          : pushFailed ? "任务与习惯已同步，但部分本地修改/删除未能写回滴答；请到设置 → 滴答同步 检查 _csrf_token 或「回写错误」。"
          : "同步完成。",
        pushFailed ? "warning" : "success");
    }
  } catch (error) {
    setStatus(feedback, error.message, "error");
  } finally {
    busy = false;
    syncing = false;
    renderQuickState();
  }
}

async function clearCredentials() {
  const accepted = await confirmAction({
    title: "清除滴答凭据",
    message: "会删除当前工作区数据目录里的 cookie/CSRF，并关闭定时同步；已经同步的任务笔记不会被删除。",
    confirmLabel: "清除凭据",
    danger: true,
  });
  if (!accepted) return;
  try {
    await api("/api/sync/dida/credentials", { method: "DELETE" });
    toast("滴答凭据已清除。", "success");
    await loadDidaSync();
  } catch (error) {
    setStatus("#dida-feedback", error.message, "error");
  }
}

async function keepLocal(externalId, kind = "task") {
  const habit = String(kind).startsWith("habit");
  const accepted = await confirmAction({
    title: "保留本地",
    message: habit
      ? "会把滴答当前状态记为已同步基线，并在下一次同步把本地习惯版本写回滴答。不会修改本地笔记。"
      : "会把滴答当前状态记为已同步基线，不改本地笔记。之后滴答再修改时会再触发同步。",
    confirmLabel: "保留本地",
  });
  if (!accepted) return;
  try {
    const path = habit
      ? `/api/sync/dida/habit-conflicts/${encodeURIComponent(externalId)}/keep-local`
      : `/api/sync/dida/conflicts/${encodeURIComponent(externalId)}/keep-local`;
    await api(path, { method: "POST" });
    toast("已保留本地版本。", "success");
    await loadDidaSync();
  } catch (error) {
    setStatus("#dida-feedback", error.message, "error");
  }
}

export function initDidaSync() {
  document.addEventListener("torch:config", applyCapability);
  document.querySelectorAll('[data-settings-nav="dida"]').forEach((button) => {
    button.addEventListener("click", () => loadDidaSync());
  });
  $("#dida-sync-form")?.addEventListener("submit", saveSettings);
  $("#dida-test")?.addEventListener("click", testConnection);
  $("#dida-preview")?.addEventListener("click", () => runSync(true));
  $("#dida-run")?.addEventListener("click", () => runSync(false));
  $("#time-dida-sync")?.addEventListener("click", () => runSync(false, "quick"));
  $("#habit-dida-sync")?.addEventListener("click", () => runSync(false, "habit"));
  $("#dida-clear")?.addEventListener("click", clearCredentials);
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-dida-keep-local]");
    if (button) keepLocal(button.dataset.didaKeepLocal, button.dataset.didaKind || "task");
  });
  applyCapability();
}
