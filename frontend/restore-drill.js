// 一键恢复演练：生成备份 → 恢复到独立目录 → 只读验证 → 生成报告（不覆盖当前库）。
import { $, state, api, escapeHtml, formatDate, jsonBody, setStatus, toast, confirmAction } from "./core.js";

let busy = false;

const available = () => state.config?.capabilities?.restore_drill === true;

function syncControls() {
  const enabled = available();
  const run = $("#run-restore-drill");
  if (run) run.disabled = busy || !enabled;
  const download = $("#download-drill-report");
  if (download && busy) download.disabled = true;
  const support = $("#drill-support");
  if (support) {
    support.hidden = enabled;
    support.textContent = state.config
      ? "当前运行的服务尚未支持一键恢复演练。请更新并重启本地服务，再刷新页面。"
      : "正在检查服务能力；若未恢复，请到设置页重新检查本地服务。";
  }
}

function renderDrill(payload) {
  const backup = payload.backup || {};
  const copy = payload.copy || {};
  const verification = payload.verification || {};
  const copyName = String(copy.target_dir || "").split(/[\\/]/).filter(Boolean).pop() || "（未命名副本）";
  $("#drill-summary").innerHTML = `
    <div class="settings-list">
      <div><span>备份</span><strong>${escapeHtml(backup.filename || "")}${backup.verified ? "（verified）" : ""}</strong></div>
      <div><span>恢复副本</span><strong>${escapeHtml(copyName)}</strong></div>
      <div><span>只读验证</span><strong>${verification.ok ? "ok" : "失败"} · integrity ${escapeHtml(verification.sqlite_integrity || "-")}</strong></div>
      <div><span>文件</span><strong>${escapeHtml(verification.file_count ?? "-")} 个（Vault ${escapeHtml(verification.vault_files ?? "-")} 篇）</strong></div>
    </div>
    <p class="field-help">${escapeHtml(payload.note || "")}</p>`;
  const download = $("#download-drill-report");
  if (download) download.disabled = false;
}

export async function loadDrillReport() {
  const download = $("#download-drill-report");
  if (!download || !available()) return;
  try {
    const response = await fetch("/api/backups/drill/last.md", { credentials: "same-origin" });
    if (response.ok) {
      download.disabled = false;
      if ($("#drill-summary").innerText.includes("还没有运行过演练")) {
        $("#drill-summary").innerHTML = '<p class="meta">已找到上一次演练留下的报告，可以直接下载查看。</p>';
      }
    }
  } catch { /* 下载按钮保持关闭 */ }
}

export async function loadDrillReports() {
  const target = $("#drill-reports");
  if (!target || !available()) return;
  try {
    const payload = await api("/api/backups/drill/reports");
    if (!payload.count) {
      target.hidden = true;
      target.innerHTML = "";
      return;
    }
    target.hidden = false;
    target.innerHTML = `
      <p class="meta">最近 ${payload.count} 份演练报告（${escapeHtml(payload.note || "")}）</p>
      <ul class="extract-list">${payload.reports.map((item) => `
        <li>${escapeHtml(item.name)} · ${escapeHtml(formatDate(item.created_at))} · ${escapeHtml(item.bytes)} 字节
          <button class="text-button" type="button" data-drill-report="${escapeHtml(item.name)}">下载</button></li>`).join("")}</ul>`;
  } catch {
    target.hidden = true;
  }
}

async function runDrill() {
  if (busy || !available()) return;
  const accepted = await confirmAction({
    title: "运行恢复演练？",
    message: "会生成一份新备份、恢复到 data/restored 下的独立目录并做只读验证，然后生成 Markdown 报告。不会覆盖当前库、不会删除任何备份、不会切换数据。",
    confirmLabel: "运行演练",
  });
  if (!accepted) return;
  busy = true;
  syncControls();
  setStatus("#drill-status", "正在演练：备份 → 恢复 → 验证…");
  try {
    const payload = await api("/api/backups/drill", jsonBody("POST", { confirm: true }));
    renderDrill(payload);
    await loadDrillReports();
    setStatus("#drill-status", payload.ok ? "演练通过：备份可恢复，副本验证 ok。" : "演练完成，但验证未通过，请查看报告。", payload.ok ? "success" : "error");
    toast(payload.ok ? "恢复演练通过" : "演练有异常，请查看报告", payload.ok ? "success" : "error");
  } catch (error) {
    setStatus("#drill-status", `演练失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncControls();
  }
}

function downloadReport() {
  if (!available()) return;
  location.assign("/api/backups/drill/last.md");
  toast("已开始下载最近一次演练报告（Markdown）。", "success");
}

function downloadNamedReport(name) {
  if (!available() || !name) return;
  location.assign(`/api/backups/drill/reports/${encodeURIComponent(name)}`);
  toast("已开始下载这份演练报告。", "success");
}

export function initRestoreDrill() {
  $("#run-restore-drill")?.addEventListener("click", runDrill);
  $("#download-drill-report")?.addEventListener("click", downloadReport);
  document.addEventListener("click", (event) => {
    const report = event.target.closest("[data-drill-report]");
    if (report) downloadNamedReport(report.dataset.drillReport);
  });
  document.addEventListener("config-changed", () => { syncControls(); loadDrillReport(); loadDrillReports(); });
  syncControls();
  loadDrillReports();
}
