// Obsidian 库连接：显示当前目录与形态、只读预览会索引什么、把新路径与只读模式写进本地 .env。
import { $, state, api, escapeHtml, jsonBody, setStatus, toast, confirmAction } from "./core.js";

let loadId = 0;
let busy = false;
let current = null;

const available = () => state.config?.capabilities?.vault_connection === true;

function formatBytes(value) {
  const size = Number(value) || 0;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function badge(text, tone) {
  return `<span class="vault-badge vault-badge-${tone}">${escapeHtml(text)}</span>`;
}

function renderConnection(payload) {
  current = payload;
  const target = $("#vault-connection-body");
  if (!target) return;
  const badges = [
    payload.exists ? badge("目录存在", "ok") : badge("目录不存在", "error"),
    payload.looks_like_obsidian ? badge("看起来是 Obsidian 库", "ok") : badge("没有 .obsidian 文件夹", "warn"),
    payload.read_only ? badge("只读安全模式", "ok") : badge("当前可写", "warn"),
  ].join(" ");
  target.innerHTML = `
    <p class="preserve-lines vault-path">${escapeHtml(payload.path || "")}</p>
    <p class="meta">${badges}</p>
    <div class="settings-list">
      <div><span>Markdown 文件</span><strong>${Number(payload.markdown_files) || 0}${payload.truncated ? "+" : ""}</strong></div>
      <div><span>合计大小</span><strong>${escapeHtml(formatBytes(payload.total_bytes))}</strong></div>
      <div><span>跳过的目录</span><strong>${escapeHtml((payload.skipped_directories || []).join("、") || "无")}</strong></div>
    </div>
    <p class="field-help">${escapeHtml(payload.note || "")}</p>`;
  const pathInput = $("#vault-path");
  if (pathInput && document.activeElement !== pathInput) pathInput.value = payload.path || "";
  const toggle = $("#vault-read-only");
  if (toggle) toggle.checked = payload.read_only !== false;
  $("#vault-restart-note").textContent = payload.restart_required_for_changes
    ? "改路径或改只读模式会写入本地 .env，需要重启本地服务后生效。"
    : "";
  syncControls();
}

function syncControls() {
  const enabled = available();
  const disabled = busy || !enabled;
  ["#vault-path", "#vault-read-only"].forEach((selector) => { const node = $(selector); if (node) node.disabled = disabled; });
  const save = $("#vault-save");
  if (save) save.disabled = disabled;
  const preview = $("#vault-preview-scan");
  if (preview) preview.disabled = disabled;
  const report = $("#vault-onboarding");
  if (report) report.disabled = disabled;
  const support = $("#vault-connection-support");
  if (support) {
    support.hidden = enabled;
    support.textContent = state.config
      ? "当前运行的服务尚未支持 Vault 连接设置。请更新并重启本地服务，再刷新页面。"
      : "正在检查服务能力；若未恢复，请到设置页重新检查本地服务。";
  }
}

export async function loadVaultConnection() {
  if (!available()) { syncControls(); return; }
  const request = ++loadId;
  try {
    const payload = await api("/api/vault/connection");
    if (request !== loadId) return;
    renderConnection(payload);
    setStatus("#vault-connection-status", "已读取当前连接状态。");
  } catch (error) {
    if (request !== loadId) return;
    setStatus("#vault-connection-status", `读取失败：${error.message}`, "error");
  }
}

async function previewScan() {
  if (busy || !available()) return;
  busy = true;
  syncControls();
  setStatus("#vault-connection-status", "正在做只读扫描预览…");
  try {
    const payload = await api("/api/vault/scan-preview", jsonBody("POST", {}));
    const target = $("#vault-scan-preview");
    target.hidden = false;
    const samples = (payload.samples?.new || []).slice(0, 5).map((path) => `<li>${escapeHtml(path)}</li>`).join("");
    target.innerHTML = `
      <p class="meta">扫描到 ${payload.scanned} 篇；新增 ${payload.new}、变化 ${payload.changed}、未变 ${payload.unchanged}、索引里已消失 ${payload.missing}、按规则跳过 ${payload.excluded}、无法读取 ${payload.unreadable}。</p>
      <p class="meta">真正扫描时会索引 ${payload.would_index} 篇${payload.truncated ? "（预览达到数量上限）" : ""}。</p>
      ${samples ? `<ul class="extract-list">${samples}</ul>` : ""}
      <p class="field-help">${escapeHtml(payload.note || "")}</p>`;
    setStatus("#vault-connection-status", payload.would_index ? `预览：会索引 ${payload.would_index} 篇。` : "预览：索引已是最新。", "success");
  } catch (error) {
    setStatus("#vault-connection-status", `预览失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncControls();
  }
}

const CHECK_LABELS = { ok: "通过", info: "提示", warn: "注意", error: "问题" };

async function onboarding() {
  if (busy || !available()) return;
  busy = true;
  syncControls();
  setStatus("#vault-connection-status", "正在生成只读演练报告…");
  try {
    const payload = await api("/api/vault/onboarding.json");
    const target = $("#vault-report");
    target.hidden = false;
    const checks = payload.checks.map((item) => `<li><strong>${escapeHtml(CHECK_LABELS[item.level] || item.level)}</strong> · ${escapeHtml(item.label)}：${escapeHtml(item.detail)}</li>`).join("");
    const folders = Object.entries(payload.diff.top_level || {}).map(([name, count]) => `<li>${escapeHtml(name)}：${escapeHtml(count)} 篇</li>`).join("");
    const steps = (payload.next_steps || []).map((step) => `<li>${escapeHtml(step)}</li>`).join("");
    target.innerHTML = `
      <p class="meta">生成时间 ${escapeHtml(payload.generated_at)} · 只有统计，不含任何正文</p>
      <ul class="extract-list">${checks}</ul>
      ${folders ? `<h4>目录分布（前 10）</h4><ul class="extract-list">${folders}</ul>` : ""}
      <h4>建议步骤</h4><ol class="extract-list">${steps}</ol>
      <div class="section-actions"><button id="vault-report-download" class="button secondary" type="button">下载 Markdown 报告</button></div>`;
    $("#vault-report-download")?.addEventListener("click", () => {
      location.assign("/api/vault/onboarding.md");
      toast("已开始下载演练报告（Markdown），文件在浏览器下载目录。", "success");
    });
    setStatus("#vault-connection-status", payload.diff.would_index ? `演练报告就绪：真正扫描会索引 ${payload.diff.would_index} 篇。` : "演练报告就绪：索引已是最新。", "success");
  } catch (error) {
    setStatus("#vault-connection-status", `报告生成失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncControls();
  }
}

async function saveConnection(event) {
  event.preventDefault();
  if (busy || !available()) return;
  const path = String($("#vault-path").value || "").trim();
  const readOnly = Boolean($("#vault-read-only").checked);
  if (!path) {
    setStatus("#vault-connection-status", "请填写 Obsidian 库的完整绝对路径。", "error");
    return;
  }
  const accepted = await confirmAction({
    title: "更换 Vault 目录？",
    message: `将把 VAULT_DIR 写入本地 .env，并设置${readOnly ? "只读安全模式" : "可写模式"}；重启本地服务后生效。${readOnly ? "只读模式下扫描照常，所有写入都会被拒绝。" : "可写模式下应用会向 90-Inbox 写入新笔记。"}`,
    confirmLabel: "写入本地 .env",
  });
  if (!accepted) return;
  busy = true;
  syncControls();
  try {
    const payload = await api("/api/vault/connection", jsonBody("POST", { path, read_only: readOnly }));
    renderConnection(payload);
    setStatus("#vault-connection-status", payload.note || "已保存；重启本地服务后生效。", "success");
    toast("已写入本地 .env；重启服务后生效");
  } catch (error) {
    setStatus("#vault-connection-status", `保存失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncControls();
  }
}

export function initVaultConnection() {
  $("#vault-form")?.addEventListener("submit", saveConnection);
  $("#vault-preview-scan")?.addEventListener("click", previewScan);
  $("#vault-onboarding")?.addEventListener("click", onboarding);
  document.addEventListener("click", (event) => {
    if (event.target.closest('[data-settings-nav="obsidian"]')) loadVaultConnection();
  });
  document.addEventListener("config-changed", () => {
    syncControls();
    const section = document.querySelector('[data-settings-section="obsidian"]');
    if (state.view === "settings" && section && section.hidden === false) loadVaultConnection();
  });
  syncControls();
}
