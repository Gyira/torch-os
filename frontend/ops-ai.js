// 生活运营 AI 卡片（2026-10-29，v1.0 P4）。
// 只展示健康/财务/恋爱的只读摘要；报告由后端生成并落到 06–08 章，
// 生活助理建议里的任务/习惯只以提案卡出现，采纳后才创建。
import { api, confirmAction, escapeHtml, toast } from "./core.js";
import { proposalsMarkup } from "./proposals-model.js";

const DOMAINS = {
  life: { label: "生活助理汇总", hint: "健康/财务/恋爱/任务放在一起看" },
  health: { label: "私人医生评估", hint: "生成健康报告，落到 06_健康报告" },
  finance: { label: "理财专家评估", hint: "生成财务报告，落到 07_财务报告" },
  love: { label: "恋爱大师建议", hint: "基于分析摘要，不读原始聊天" },
};

let mounted = false;

export function initOpsAI() {
  if (mounted) return;
  mounted = true;
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-ops-ai-report]");
    if (button) {
      await runReport(button);
      return;
    }
    const refresh = event.target.closest("[data-ops-ai-refresh]");
    if (refresh) loadOpsAI();
  });
  document.addEventListener("torch:proposals-changed", () => {
    if (document.body?.classList?.contains("view-ops")) loadOpsAI();
  });
}

export async function loadOpsAI() {
  const host = document.querySelector("[data-ops-ai]");
  if (!host) return;
  host.innerHTML = `<div class="field-help">生活助理正在读取健康/财务/恋爱摘要…</div>`;
  try {
    const data = await api("/api/ops/ai/overview", { timeoutMs: 60000 });
    renderOpsAI(host, data);
  } catch (error) {
    host.innerHTML = `<div class="field-help">生活运营摘要读不到：${escapeHtml(error.message)}
      <button type="button" class="text-button" data-ops-ai-refresh>重试</button></div>`;
  }
}

function renderOpsAI(host, data) {
  const health = data?.health || {};
  const finance = data?.finance || {};
  const love = data?.love || {};
  const reports = Array.isArray(data?.reports) ? data.reports : [];
  host.innerHTML = `
    <section class="ops-ai-card">
      <header class="ops-ai-head">
        <div><p class="eyebrow">AI LIFE ASSISTANT</p><h3>生活运营 AI</h3>
          <p class="field-help">报告会落到数据层；建议只生成待采纳的任务/习惯提案。</p></div>
        <div class="ops-ai-actions">
          ${Object.entries(DOMAINS).map(([domain, item]) =>
            `<button type="button" class="button secondary" data-ops-ai-report="${domain}" title="${escapeHtml(item.hint)}">${escapeHtml(item.label)}</button>`).join("")}
          <button type="button" class="text-button" data-ops-ai-refresh>刷新</button>
        </div>
      </header>
      <div class="ops-ai-grid">
        <article class="ops-ai-domain"><p class="eyebrow">健康</p>
          <p class="ops-ai-stat">${health.stats?.rows ?? 0} 条月度记录</p>
          <p class="field-help">${escapeHtml((health.text || "还没有健康数据").split("\n")[0])}</p></article>
        <article class="ops-ai-domain"><p class="eyebrow">财务</p>
          <p class="ops-ai-stat">${finance.stats?.rows ?? 0} 条记录</p>
          <p class="field-help">${escapeHtml((finance.text || "还没有财务数据").split("\n")[0])}</p></article>
        <article class="ops-ai-domain"><p class="eyebrow">恋爱</p>
          <p class="ops-ai-stat">${love.stats?.projects ?? 0} 个项目</p>
          <p class="field-help">${love.stats?.analyses ? `${love.stats.analyses} 份分析摘要` : "还没有 AI 分析摘要"}</p></article>
        <article class="ops-ai-domain"><p class="eyebrow">家庭</p>
          <p class="ops-ai-stat">后续版本</p>
          <p class="field-help">家庭物资库存与智能家居控制只做占位，不在 v1.0 发设备请求。</p></article>
      </div>
      ${reports.length ? `<details class="ops-ai-reports"><summary>最近报告（${reports.length}）</summary>
        <ul>${reports.map((report) => `<li><span>${escapeHtml(report.title || "")}</span>
          <span class="meta">${escapeHtml(report.chapter || "")}</span></li>`).join("")}</ul></details>` : ""}
      <div class="ops-ai-result" data-ops-ai-result></div>
    </section>`;
}

async function runReport(button) {
  const domain = button.dataset.opsAiReport;
  const item = DOMAINS[domain];
  if (!item || button.disabled) return;
  const ok = await confirmAction({
    title: `生成${item.label}？`,
    message: `会读取只读摘要并调用 dsh（消耗 DeepSeek token），生成的报告写进生活运营数据层；建议只作为待采纳提案。`,
    confirmLabel: "开始生成",
  });
  if (!ok) return;
  button.disabled = true;
  const resultHost = document.querySelector("[data-ops-ai-result]");
  if (resultHost) resultHost.innerHTML = `<p class="status-message">正在生成…</p>`;
  try {
    const result = await api("/api/ops/ai/report", {
      method: "POST",
      body: JSON.stringify({ domain, confirm: true }),
      timeoutMs: 240000,
    });
    if (resultHost) {
      resultHost.innerHTML = `<article class="ops-ai-output">
        <p class="eyebrow">${escapeHtml(result.role || domain)}</p>
        <p><strong>已保存：</strong>${escapeHtml(result.note?.path || "")}</p>
        <div class="md-body">${escapeHtml(result.text || "").replace(/\n/g, "<br>")}</div>
        ${result.proposals?.length ? `<div class="ops-ai-proposals">${proposalsMarkup(result.proposals)}</div>` : ""}
      </article>`;
    }
    toast("报告已写入生活运营数据层。", "success");
    document.dispatchEvent(new CustomEvent("torch:notes-changed", {
      detail: { pillar: "ops", chapter: result.note?.chapter },
    }));
  } catch (error) {
    if (resultHost) resultHost.innerHTML = `<p class="status-message error">生成失败：${escapeHtml(error.message)}</p>`;
    toast(`生成失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}
