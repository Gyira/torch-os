// 薪火管家（二期）：提案、健康检查、带引用问答。
// 所有执行都要在界面上点“确认应用”；默认只用本地检索，勾选后才使用云端模型。
import { api, escapeHtml, toast } from "./core.js";
import { renderMarkdown } from "./pillar-markdown.js";

const CONFIDENCE_LABEL = { high: "高置信", medium: "可参考", low: "低置信" };
const SEVERITY_LABEL = { error: "错误", warning: "需要留意", info: "提示" };

let statusPromise = null;

function modelStatus() {
  if (!statusPromise) {
    statusPromise = api("/api/steward/status").catch(() => ({ model_configured: false }));
  }
  return statusPromise;
}

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
      <button type="button" class="button primary" data-steward-apply="${escapeHtml(item.id)}">确认应用</button>
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

function askMarkup(configured) {
  return `<form class="steward-ask" data-steward-ask-form>
      <label class="field"><span>问四库</span>
        <textarea name="question" rows="3" maxlength="1000" required placeholder="例如：我最近对时间管理有什么想法？"></textarea>
      </label>
      <div class="steward-ask-actions">
        <label class="checkbox-line">
          <input type="checkbox" name="use_cloud"${configured ? "" : " disabled"}>
          <span>本次使用云端模型${configured ? "" : "（尚未配置）"}</span>
        </label>
        <button type="submit" class="button primary">提问</button>
      </div>
      <p class="field-help">默认只在本地检索并摘录原文；勾选后只发送与问题相关的笔记片段。回答只依据列出的笔记，不会替你做任何修改。</p>
    </form>
    <div class="steward-answer" data-steward-answer></div>`;
}

function answerMarkup(result) {
  const citations = result.citations || [];
  const cloud = result.mode === "cloud";
  return `<p class="steward-mode ${cloud ? "cloud" : "local"}">${cloud
    ? `云端模型${result.model ? ` · ${escapeHtml(result.model)}` : ""}`
    : "本地检索 · 未调用模型"}</p>
    ${result.warning ? `<p class="steward-warning">${escapeHtml(result.warning)}</p>` : ""}
    <div class="steward-answer-body">${renderMarkdown(result.answer || "")}</div>
    ${citations.length ? `<section class="steward-citations">
      <p class="eyebrow">引用笔记</p>
      ${citations.map((item, index) => `<button type="button" class="steward-citation" data-steward-open-note="${escapeHtml(item.path)}">
        <span class="steward-citation-index">${index + 1}</span>
        <span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.path)}</small></span>
      </button>`).join("")}
    </section>` : ""}`;
}

export function openSteward(view, { onApplied, onOpenNote } = {}) {
  const trigger = document.activeElement;
  const dialog = document.createElement("dialog");
  dialog.className = "pillar-dialog pillar-steward-dialog";
  dialog.dataset.pillar = view || "";
  dialog.innerHTML = `
    <div class="pillar-dialog-head">
      <div>
        <p class="eyebrow">薪火管家</p>
        <h2>只提议，不替你动手</h2>
        <p class="meta">提案与问答都只是建议；应用前请核对源路径与目标章节。</p>
      </div>
      <button type="button" class="text-button" data-steward-close>关闭</button>
    </div>
    <div class="steward-tabs" role="tablist" aria-label="管家功能">
      <button type="button" class="steward-tab is-active" role="tab" aria-selected="true" data-steward-tab="proposals">提案</button>
      <button type="button" class="steward-tab" role="tab" aria-selected="false" data-steward-tab="health">健康检查</button>
      <button type="button" class="steward-tab" role="tab" aria-selected="false" data-steward-tab="ask">问答</button>
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

  async function loadAsk() {
    const status = await modelStatus();
    if (active !== "ask") return;
    body.innerHTML = askMarkup(Boolean(status.model_configured));
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
    else if (tab === "health") await loadHealth();
    else await loadAsk();
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
    const open = event.target.closest?.("[data-steward-open-note]");
    if (open) { openNote(open.dataset.stewardOpenNote); return; }
    const apply = event.target.closest?.("[data-steward-apply]");
    if (apply) {
      const proposalId = apply.dataset.stewardApply;
      if (!proposalId) return;
      apply.disabled = true;
      apply.textContent = "应用中…";
      try {
        const result = await api("/api/steward/proposals/apply", {
          method: "POST",
          body: JSON.stringify({ proposal_id: proposalId, confirm: true }),
        });
        toast(result.proposal?.action === "distill" ? "已生成原则草稿。" : "已按提案移动。");
        await onApplied?.();
        await loadProposals();
      } catch (error) {
        toast(error.message, "error");
        apply.disabled = false;
        apply.textContent = "确认应用";
      }
    }
  });

  dialog.addEventListener("submit", async (event) => {
    const form = event.target.closest?.("[data-steward-ask-form]");
    if (!form) return;
    event.preventDefault();
    const question = String(new FormData(form).get("question") || "").trim();
    if (!question) return;
    const useCloud = Boolean(form.querySelector('[name="use_cloud"]')?.checked);
    const answerHost = dialog.querySelector("[data-steward-answer]");
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    answerHost.innerHTML = `<p class="field-help">${useCloud ? "正在请求云端模型…" : "正在本地检索…"}</p>`;
    try {
      const result = await api("/api/steward/ask", {
        method: "POST",
        timeoutMs: 60000,
        body: JSON.stringify({ question, use_cloud: useCloud }),
      });
      answerHost.innerHTML = answerMarkup(result);
    } catch (error) {
      answerHost.innerHTML = `<p class="status-message error">${escapeHtml(error.message)}</p>`;
    } finally {
      submit.disabled = false;
    }
  });

  if (typeof dialog.showModal === "function") dialog.showModal();
  selectTab("proposals");
  return dialog;
}
