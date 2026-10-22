// 设置 → 库结构与迁移：只读预览、备份后执行、按 run_id 回滚。
import { $, api, escapeHtml, confirmAction, toast } from "./core.js";

let preview = null;
let busy = false;

function setStatus(message, tone = "") {
  const node = $("#migration-status");
  if (!node) return;
  node.textContent = message;
  node.className = `status-message${tone ? ` ${tone}` : ""}`;
}

function renderLayout(status) {
  const node = $("#migration-layout");
  if (!node) return;
  const layout = status?.layout || {};
  const stateLabels = {
    v1: "已是四库布局",
    legacy: "旧布局",
    mixed: "混合布局（部分迁移）",
    empty: "空库",
  };
  node.innerHTML = `<div class="panel form-panel">
    <p class="meta">当前状态：<strong>${escapeHtml(stateLabels[layout.state] || layout.state || "未知")}</strong>（${escapeHtml(status?.layout_version || "")}）</p>
    <p class="field-help">四库：00_人生路 / 01_自我档案 / 02_生活运营 / 03_知识库。任务与待办在人生路的「任务与待办」章节；滴答同步不参与迁移。</p>
  </div>`;
}

function renderRuns(status) {
  const node = $("#migration-runs");
  if (!node) return;
  const runs = Array.isArray(status?.runs) ? status.runs : [];
  if (!runs.length) {
    node.innerHTML = `<p class="field-help">还没有迁移记录。</p>`;
    return;
  }
  node.innerHTML = `<h3>迁移记录</h3><ul class="history-list">${runs.map((run) => `
    <li><code>${escapeHtml(run.run_id)}</code>
      <span>${escapeHtml(run.status || "")} · ${escapeHtml(String(run.started_at || "").slice(0, 19))}${run.error ? ` · ${escapeHtml(run.error)}` : ""}</span>
      ${run.status === "done" ? `<button class="text-button" type="button" data-migration-rollback="${escapeHtml(run.run_id)}">回滚</button>` : ""}
    </li>`).join("")}</ul>`;
}

function renderPreview(plan) {
  const node = $("#migration-preview-panel");
  if (!node) return;
  preview = plan;
  const items = Array.isArray(plan.items) ? plan.items : [];
  const moves = items.filter((item) => item.action === "move");
  const keeps = items.filter((item) => item.action !== "move");
  const conflicts = Array.isArray(plan.conflicts) ? plan.conflicts : [];
  const stats = plan.stats || {};
  node.innerHTML = `
    <div class="panel form-panel">
      <p class="meta">计划 <code>${escapeHtml(plan.plan_hash)}</code> · 移动 ${escapeHtml(stats.moves ?? moves.length)} · 原地保留 ${escapeHtml(keeps.length)} · 冲突 ${escapeHtml(conflicts.length)}</p>
      ${conflicts.length ? `<div class="inline-error"><p>存在冲突，迁移已暂停：</p><ul>${conflicts.map((item) => `<li>${escapeHtml(item.source)} → ${escapeHtml(item.target || "")}：${escapeHtml(item.reason || "")}</li>`).join("")}</ul></div>` : ""}
      <h3>将移动</h3>
      <ul class="history-list">${moves.map((item) => `<li><code>${escapeHtml(item.source)}</code><span>→ ${escapeHtml(item.target)} · ${escapeHtml(item.reason || "")}</span></li>`).join("") || '<li class="empty-state">没有需要移动的文件。</li>'}</ul>
      <details><summary>原地保留（${keeps.length}）</summary><ul class="history-list">${keeps.map((item) => `<li><code>${escapeHtml(item.source)}</code><span>${escapeHtml(item.reason || "")}</span></li>`).join("")}</ul></details>
      <div class="form-actions">
        <span class="field-help">执行前会自动创建并校验完整快照；移动只发生在本库内，不删除文件。</span>
        <button id="migration-apply" class="button primary" type="button" ${conflicts.length || !moves.length ? "disabled" : ""}>备份并迁移</button>
      </div>
    </div>`;
  node.hidden = false;
  $("#migration-apply")?.addEventListener("click", applyMigration);
}

async function loadVaultMigration() {
  if (busy) return;
  setStatus("正在读取库结构…");
  try {
    const status = await api("/api/migration/status");
    renderLayout(status);
    renderRuns(status);
    setStatus("预览只读；确认后再备份并迁移。");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function previewMigration() {
  if (busy) return;
  busy = true;
  setStatus("正在生成只读预览…");
  try {
    const plan = await api("/api/migration/preview");
    renderPreview(plan);
    setStatus(plan.conflicts?.length ? "发现冲突，请先处理。" : "预览完成；没有写入任何文件。",
      plan.conflicts?.length ? "warning" : "success");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    busy = false;
  }
}

async function applyMigration() {
  if (busy || !preview) return;
  const ok = await confirmAction({
    title: "备份并迁移到四库？",
    message: "会先创建并校验完整快照，然后移动文件、改写内部链接并重建索引。任务与滴答数据不移动；之后可以在迁移记录里回滚。",
    confirmLabel: "备份并迁移",
  });
  if (!ok) return;
  busy = true;
  setStatus("正在备份并迁移，请不要关闭页面…");
  try {
    const result = await api("/api/migration/apply", {
      method: "POST",
      body: JSON.stringify({ plan_hash: preview.plan_hash, confirm: true }),
    });
    toast(`迁移完成：移动 ${result.stats?.moves ?? 0} 个文件。`, "success");
    setStatus(`迁移完成（run ${result.run_id}）。`, "success");
    $("#migration-preview-panel").hidden = true;
    preview = null;
    await loadVaultMigration();
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    busy = false;
  }
}

async function rollbackMigration(runId) {
  if (busy) return;
  const ok = await confirmAction({
    title: `回滚迁移 ${runId}？`,
    message: "会按迁移日志反向移动文件，并从快照恢复被改写的链接。回滚前请确认没有在同一批文件上继续编辑。",
    confirmLabel: "回滚",
    danger: true,
  });
  if (!ok) return;
  busy = true;
  setStatus("正在回滚…");
  try {
    const result = await api("/api/migration/rollback", {
      method: "POST",
      body: JSON.stringify({ run_id: runId, confirm: true }),
    });
    toast(`已回滚：移动 ${result.moves} 个文件，恢复 ${result.links} 个链接文件。`, "success");
    setStatus("回滚完成。", "success");
    await loadVaultMigration();
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    busy = false;
  }
}

export function initVaultMigration() {
  $("#migration-preview")?.addEventListener("click", previewMigration);
  $("#migration-refresh")?.addEventListener("click", loadVaultMigration);
  document.addEventListener("click", (event) => {
    const rollback = event.target.closest?.("[data-migration-rollback]");
    if (rollback) rollbackMigration(rollback.dataset.migrationRollback);
  });
}

export { loadVaultMigration };
