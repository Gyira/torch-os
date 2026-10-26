// 薪火管家：确定性提案与健康检查（2026-10-26 起只读）。
// 这里只给建议，不移动/不新建任何笔记；要动手请到 Obsidian。问答统一交给「与神对话」。
import { api, escapeHtml } from "./core.js";

const CONFIDENCE_LABEL = { high: "高置信", medium: "可参考", low: "低置信" };
const SEVERITY_LABEL = { error: "错误", warning: "需要留意", info: "提示" };

function proposalMarkup(item) {
  const confidence = CONFIDENCE_LABEL[item.confidence] || item.confidence;
  const target = [item.target_pillar_title, item.target_title].filter(Boolean).join(" / ");
  return `<article class="steward-proposal">
    <div class="steward-proposal-head">
      <div>
        <p class="eyebrow">${escapeHtml(item.kind_title || item.kind)} · ${escapeHtml(confidence)}</p>
        <h3>${escapeHtml(item.title)}</h3>
        <p class="meta">${escapeHtml(item.path)}</p>
      </div>
      <span class="steward-badge steward-badge-${escapeHtml(item.confidence)}">${escapeHtml(confidence)}</span>
    </div>
    <p class="steward-route"><span>${escapeHtml(item.path)}</span><b aria-hidden="true">→</b><strong>${escapeHtml(target)}</strong></p>
    <p class="field-help">${escapeHtml(item.reason)}</p>
    <div class="steward-proposal-actions">
      <button type="button" class="button secondary" data-steward-open-obsidian="${escapeHtml(item.path)}">在 Obsidian 打开原文</button>
    </div>
  </article>`;
}

function healthMarkup(payload, filter = "all") {
  const counts = payload.counts || {};
  const allRows = Array.isArray(payload.items) ? payload.items : [];
  const actionable = allRows.filter((item) => item.severity !== "info");
  const rows = filter === "issues" ? actionable : allRows;
  const issueCount = actionable.length;
  return `<div class="steward-health-summary">
      <span>已检查 <strong>${Number(payload.checked) || 0}</strong> 篇</span>
      <span class="steward-severity warning">需要留意 <strong>${Number(counts.warning) || 0}</strong></span>
      <span class="steward-severity info">提示 <strong>${Number(counts.info) || 0}</strong></span>
      <button type="button" class="text-button" data-steward-refresh-health>重新检查</button>
    </div>
    <div class="steward-health-filters" role="group" aria-label="健康检查筛选">
      <button type="button" class="steward-filter${filter === "issues" ? " is-active" : ""}" data-steward-health-filter="issues">只看需要留意（${issueCount}）</button>
      <button type="button" class="steward-filter${filter === "all" ? " is-active" : ""}" data-steward-health-filter="all">全部（${allRows.length}）</button>
    </div>
    ${payload.ok ? `<p class="steward-health-ok">四库结构看起来不错，没有发现需要立即处理的问题。</p>` : ""}
    ${rows.length ? `<div class="steward-health-list">${rows.map((item) => `
      <div class="steward-health-row" data-severity="${escapeHtml(item.severity)}">
        <span class="steward-severity-dot" aria-hidden="true"></span>
        <div class="steward-health-main">
          <div class="steward-health-title">
            <button type="button" class="text-button" data-steward-open-note="${escapeHtml(item.path)}">${escapeHtml(item.title || item.path)}</button>
            <span class="steward-severity ${escapeHtml(item.severity)}">${escapeHtml(SEVERITY_LABEL[item.severity] || item.severity)}</span>
          </div>
          <p>${escapeHtml(item.message)}</p>
          <p class="field-help">${escapeHtml(item.suggestion)}</p>
          <p class="meta">${escapeHtml(item.path)} · ${escapeHtml(item.kind)}</p>
        </div>
      </div>`).join("")}</div>` : `<div class="pillar-empty"><p>${filter === "issues" ? "没有需要立即处理的问题。" : "没有发现结构问题。"}</p></div>`}
    <p class="field-help steward-footnote">${escapeHtml(payload.note || "")}${payload.truncated ? " 结果较多，已只显示前一部分。" : ""}</p>`;
}

export function openSteward(view, { onOpenNote, onOpenObsidian } = {}) {
  const trigger = document.activeElement;
  const dialog = document.createElement("dialog");
  dialog.className = "pillar-dialog pillar-steward-dialog";
  dialog.dataset.pillar = view || "";
  dialog.innerHTML = `
    <div class="pillar-dialog-head">
      <div>
        <p class="eyebrow">薪火管家</p>
        <h2>只读建议与体检</h2>
        <p class="meta">提案和健康检查都只读；这里不会移动或新建笔记，动手请到 Obsidian。问答请到「与神对话」。</p>
      </div>
      <button type="button" class="text-button" data-steward-close>关闭</button>
    </div>
    <div class="steward-tabs" role="tablist" aria-label="管家功能">
      <button type="button" class="steward-tab is-active" role="tab" aria-selected="true" data-steward-tab="proposals">提案</button>
      <button type="button" class="steward-tab" role="tab" aria-selected="false" data-steward-tab="health">健康检查</button>
    </div>
    <div class="steward-body" data-steward-body><p class="field-help">正在读取…</p></div>`;
  document.body.append(dialog);
  const body = dialog.querySelector("[data-steward-body]");
  const tabs = [...dialog.querySelectorAll("[data-steward-tab]")];
  let active = "";
  let healthData = null;
  let healthFilter = "issues";

  const close = () => {
    dialog.close();
    dialog.remove();
    trigger?.focus?.({ preventScroll: true });
  };

  const openNote = (path) => {
    if (!path) return;
    close();
    onOpenNote?.(path);
  };

  async function loadProposals() {
    body.innerHTML = `<p class="field-help">正在整理提案…</p>`;
    try {
      const payload = await api("/api/steward/proposals");
      if (active !== "proposals") return;
      const items = payload.items || [];
      body.innerHTML = `<div class="steward-section-head">
          <p class="field-help">${escapeHtml(payload.note || "")}${payload.truncated ? ` 共 ${Number(payload.total) || items.length} 条，先显示 ${items.length} 条。` : ""}</p>
          <button type="button" class="text-button" data-steward-refresh-proposals>刷新</button>
        </div>
        ${items.length ? items.map(proposalMarkup).join("") : `<div class="pillar-empty"><p>暂时没有需要处理的提案。</p><p class="field-help">收件箱分流与原则提炼都由本地规则判断，不会自动执行。</p></div>`}`;
    } catch (error) {
      if (active === "proposals") body.innerHTML = `<p class="status-message error">${escapeHtml(error.message)}</p>`;
    }
  }

  async function loadHealth() {
    body.innerHTML = `<p class="field-help">正在检查四库结构…</p>`;
    try {
      healthData = await api("/api/steward/health");
      if (active !== "health") return;
      const counts = healthData.counts || {};
      healthFilter = (Number(counts.warning) || Number(counts.error)) ? "issues" : "all";
      body.innerHTML = healthMarkup(healthData, healthFilter);
    } catch (error) {
      healthData = null;
      if (active === "health") body.innerHTML = `<p class="status-message error">${escapeHtml(error.message)}</p>`;
    }
  }

  async function selectTab(tab) {
    if (tab === active) return;
    active = tab;
    tabs.forEach((button) => {
      const selected = button.dataset.stewardTab === tab;
      button.classList.toggle("is-active", selected);
      button.setAttribute("aria-selected", selected ? "true" : "false");
    });
    if (tab === "proposals") await loadProposals();
    else await loadHealth();
  }

  dialog.querySelector("[data-steward-close]")?.addEventListener("click", close);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  tabs.forEach((button) => button.addEventListener("click", () => selectTab(button.dataset.stewardTab)));

  dialog.addEventListener("click", async (event) => {
    const refreshProposals = event.target.closest?.("[data-steward-refresh-proposals]");
    if (refreshProposals) { await loadProposals(); return; }
    const refreshHealth = event.target.closest?.("[data-steward-refresh-health]");
    if (refreshHealth) { await loadHealth(); return; }
    const healthFilterButton = event.target.closest?.("[data-steward-health-filter]");
    if (healthFilterButton) {
      healthFilter = healthFilterButton.dataset.stewardHealthFilter === "all" ? "all" : "issues";
      if (healthData) body.innerHTML = healthMarkup(healthData, healthFilter);
      return;
    }
    const openInObsidian = event.target.closest?.("[data-steward-open-obsidian]");
    if (openInObsidian) {
      const path = openInObsidian.dataset.stewardOpenObsidian;
      if (path) { close(); onOpenObsidian?.(path); }
      return;
    }
    const open = event.target.closest?.("[data-steward-open-note]");
    if (open) { openNote(open.dataset.stewardOpenNote); return; }
  });

  if (typeof dialog.showModal === "function") dialog.showModal();
  selectTab("proposals");
  return dialog;
}
