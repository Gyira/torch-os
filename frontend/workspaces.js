// 工作区（数据库）：一个人 = 一个 Vault + 一个应用数据目录。
// 切换只写本地 .env（DATA_DIR / VAULT_DIR / VAULT_READ_ONLY / WORKSPACE_NAME），重启服务后生效；
// 运行中的进程不会原地换库——否则两个人的索引、记忆与审计会串在一起。
import { $, state, api, escapeHtml, jsonBody, setStatus, toast, confirmAction } from "./core.js";

let loadId = 0;
let busy = false;
let current = null;
let editing = "";

const available = () => state.config?.capabilities?.workspaces === true;

function badge(text, tone) {
  return `<span class="vault-badge vault-badge-${tone}">${escapeHtml(text)}</span>`;
}

function entryByName(name) {
  return (current?.workspaces || []).find((item) => item.name === name) || null;
}

function renderList(payload) {
  current = payload;
  const target = $("#workspace-list");
  if (!target) return;
  const items = payload.workspaces || [];
  target.innerHTML = items.length
    ? items.map((item) => `
      <div class="workspace-row">
        <div class="workspace-info">
          <p class="workspace-name"><strong>${escapeHtml(item.name)}</strong>
            ${item.active ? badge("当前运行", "ok") : ""}
            ${item.read_only ? badge("只读", "warn") : ""}
            ${item.registered ? "" : badge("未登记", "warn")}
          </p>
          <p class="meta">文件夹：${escapeHtml(item.folder)}</p>
          <p class="meta">数据（自动）：${escapeHtml(item.data)}</p>
        </div>
        <div class="section-actions">
          ${item.registered ? `
            <button class="text-button" type="button" data-workspace-activate="${escapeHtml(item.name)}">${item.active ? "重新应用" : "切换到此工作区"}</button>
            <button class="text-button" type="button" data-workspace-edit="${escapeHtml(item.name)}">编辑</button>
            <button class="text-button" type="button" data-workspace-delete="${escapeHtml(item.name)}" ${item.active ? "disabled" : ""}>删除</button>` : ""}
        </div>
      </div>`).join("")
    : `<p class="empty-state">还没有登记的工作区；当前运行的配置会显示在这里。</p>`;
  const note = $("#workspace-note");
  if (note) note.textContent = payload.note || "";
  syncControls();
}

function syncControls() {
  const enabled = available();
  const disabled = busy || !enabled;
  ["#workspace-name", "#workspace-folder", "#workspace-read-only"].forEach((selector) => {
    const node = $(selector);
    if (node) node.disabled = disabled;
  });
  ["#workspace-save", "#workspace-cancel", "#workspace-new"].forEach((selector) => {
    const node = $(selector);
    if (node) node.disabled = disabled;
  });
  document.querySelectorAll("[data-workspace-activate], [data-workspace-edit], [data-workspace-delete]").forEach((node) => {
    node.disabled = disabled || node.disabled;
  });
  const support = $("#workspace-support");
  if (support) {
    support.hidden = enabled;
    support.textContent = state.config
      ? "当前运行的服务尚未支持工作区设置。请更新并重启本地服务，再刷新页面。"
      : "正在检查服务能力；若未恢复，请到设置页重新检查本地服务。";
  }
}

function showForm(entry) {
  editing = entry?.name || "";
  const form = $("#workspace-form");
  if (!form) return;
  form.hidden = false;
  $("#workspace-name").value = entry?.name || "";
  $("#workspace-folder").value = entry?.folder || "";
  $("#workspace-read-only").checked = Boolean(entry?.read_only);
  $("#workspace-name").focus();
  setStatus("#workspace-status", entry ? `正在编辑「${entry.name}」。` : "填写一个新工作区。");
}

function hideForm() {
  const form = $("#workspace-form");
  if (form) form.hidden = true;
  editing = "";
}

export async function loadWorkspaces() {
  if (!available()) { syncControls(); return; }
  const request = ++loadId;
  try {
    const payload = await api("/api/workspaces");
    if (request !== loadId) return;
    renderList(payload);
    setStatus("#workspace-status", `当前工作区：${payload.active || "未识别"}。`);
  } catch (error) {
    if (request !== loadId) return;
    setStatus("#workspace-status", `读取失败：${error.message}`, "error");
  }
}

async function saveWorkspace(event) {
  event.preventDefault();
  if (busy || !available()) return;
  const name = String($("#workspace-name").value || "").trim();
  const folder = String($("#workspace-folder").value || "").trim();
  const readOnly = Boolean($("#workspace-read-only").checked);
  if (!name || !folder) {
    setStatus("#workspace-status", "名字和用户文件夹都要填。", "error");
    return;
  }
  const wasEditing = Boolean(editing);
  const originalName = editing;
  busy = true;
  syncControls();
  try {
    const payload = await api("/api/workspaces", jsonBody("POST", {
      name, folder, read_only: readOnly, original_name: originalName,
    }));
    hideForm();
    await loadWorkspaces();
    setStatus("#workspace-status", payload.note || "已保存。", "success");
    toast(wasEditing ? "工作区已更新" : "工作区已保存");
  } catch (error) {
    setStatus("#workspace-status", `保存失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncControls();
  }
}

async function activateWorkspace(name) {
  if (busy || !available()) return;
  const entry = entryByName(name);
  if (!entry) return;
  const accepted = await confirmAction({
    title: `切换到「${entry.name}」？`,
    message: `将把 DATA_DIR、VAULT_DIR 与读写模式写入本地 .env，重启本地服务后生效。`
      + "数据目录固定在这个文件夹的 .torch-os 里；不同文件夹的索引、记忆、备份互不相通，当前数据不会被删除。",
    confirmLabel: "写入本地 .env",
  });
  if (!accepted) return;
  busy = true;
  syncControls();
  try {
    const payload = await api("/api/workspaces/activate", jsonBody("POST", { name }));
    setStatus("#workspace-status", payload.note || "已写入本地 .env；重启服务后生效。", "success");
    toast("已保存切换；重启服务后生效");
  } catch (error) {
    setStatus("#workspace-status", `切换失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncControls();
  }
}

async function deleteWorkspace(name) {
  if (busy || !available()) return;
  const accepted = await confirmAction({
    title: `从列表移除「${name}」？`,
    message: "只移除注册表条目，磁盘上的 Vault 与数据目录都不会删除。正在使用的工作区不能删除。",
    confirmLabel: "移除",
  });
  if (!accepted) return;
  busy = true;
  syncControls();
  try {
    await api("/api/workspaces/delete", jsonBody("POST", { name }));
    await loadWorkspaces();
    setStatus("#workspace-status", "已从列表移除；磁盘数据没有删除。", "success");
    toast("已移除工作区条目");
  } catch (error) {
    setStatus("#workspace-status", `移除失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncControls();
  }
}

export function initWorkspaces() {
  $("#workspace-form")?.addEventListener("submit", saveWorkspace);
  $("#workspace-new")?.addEventListener("click", () => showForm(null));
  $("#workspace-cancel")?.addEventListener("click", () => { hideForm(); setStatus("#workspace-status", ""); });
  $("#workspace-list")?.addEventListener("click", (event) => {
    const activate = event.target.closest("[data-workspace-activate]");
    if (activate) return activateWorkspace(activate.dataset.workspaceActivate);
    const edit = event.target.closest("[data-workspace-edit]");
    if (edit) { showForm(entryByName(edit.dataset.workspaceEdit)); return; }
    const remove = event.target.closest("[data-workspace-delete]");
    if (remove) return deleteWorkspace(remove.dataset.workspaceDelete);
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest('[data-settings-nav="obsidian"]')) loadWorkspaces();
  });
  document.addEventListener("config-changed", () => {
    syncControls();
    const section = document.querySelector('[data-settings-section="obsidian"]');
    if (state.view === "settings" && section && section.hidden === false) loadWorkspaces();
  });
  syncControls();
}
