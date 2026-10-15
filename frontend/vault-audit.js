import { $, state, api, escapeHtml, renderState, toast } from "./core.js";

// Read-only Vault structure audit. It renders codes and relative paths only;
// the backend never returns note content and nothing here writes anything.

const CODE_LABELS = {
  missing_frontmatter: "缺少 front-matter",
  invalid_frontmatter: "front-matter 无效",
  missing_fields: "缺少字段",
  duplicate_id: "id 重复",
  empty_body: "正文为空",
  decode_failed: "编码不是 UTF-8",
  too_large: "超过大小上限",
  unreadable: "无法读取",
};

const available = () => state.config?.capabilities?.vault_audit === true;

function renderControls() {
  const node = $("#run-vault-audit");
  if (node) node.disabled = !available();
}

function renderReport(report) {
  const counts = report.counts || {};
  const summary = [`检查 ${Number(report.checked_files) || 0} 个文件`, `正常 ${Number(report.ok_files) || 0}`];
  Object.entries(CODE_LABELS).forEach(([code, label]) => {
    if (Number(counts[code])) summary.push(`${label} ${Number(counts[code])}`);
  });
  const issues = Array.isArray(report.issues) ? report.issues : [];
  const list = issues.length
    ? `<ul class="history-list">${issues.map((item) => `<li><code>${escapeHtml(item.path || "")}</code><span>${escapeHtml(CODE_LABELS[item.code] || item.code || "问题")}：${escapeHtml(item.detail || "")}</span></li>`).join("")}</ul>`
    : '<p class="empty-state">结构检查没有发现问题；这不代表资料内容完整，只是没有命中这些规则。</p>';
  $("#vault-audit-panel").innerHTML = `<p class="meta">${escapeHtml(summary.join(" · "))}</p>` + list +
    (Number(report.truncated) ? `<p class="meta">另有 ${escapeHtml(report.truncated)} 条未展示。</p>` : "") +
    `<p class="field-help">${escapeHtml(report.note || "只读报告：只包含相对路径与问题说明，不包含正文内容。")}</p>`;
}

export async function runVaultAudit() {
  if (!available()) return;
  const button = $("#run-vault-audit");
  if (button.disabled) return;
  button.disabled = true;
  renderState($("#vault-audit-panel"), "loading", "正在只读检查 Vault 文件…");
  try {
    const report = await api("/api/vault/audit");
    renderReport(report);
    toast("资料体检完成。", Number(report.ok_files) === Number(report.checked_files) ? "success" : "info");
  } catch (error) {
    renderState($("#vault-audit-panel"), "error", error.message, "vault-audit");
  } finally {
    button.disabled = !available();
  }
}

export function initVaultAudit() {
  $("#run-vault-audit")?.addEventListener("click", runVaultAudit);
  document.addEventListener("config-changed", renderControls);
  document.addEventListener("click", (event) => {
    if (event.target.closest('[data-retry="vault-audit"]')) runVaultAudit();
  });
  renderControls();
}
