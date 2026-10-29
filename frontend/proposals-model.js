// AI 提案卡片的纯渲染模型（2026-10-29，v1.0 P1）。
// 所有 AI 输出都当不可信文本处理：先 escape，再拼结构；不执行任何自然语言指令。

const KIND_LABELS = {
  identity_patch: "身份修改",
  new_note: "新笔记",
  archive_note: "移动笔记",
  new_task: "新任务",
  new_habit: "新习惯",
  health_report: "健康报告",
  finance_report: "财务报告",
  life_advice: "生活建议",
};

export function escapeProposalText(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));
}

export function kindLabel(kind) {
  return KIND_LABELS[String(kind || "")] || String(kind || "建议");
}

function changesMarkup(changes) {
  const rows = (Array.isArray(changes) ? changes : [])
    .filter((change) => change && (change.field || change.to))
    .map((change) => `<li><strong>${escapeProposalText(change.field || "")}</strong>`
      + `<span class="proposal-from">${escapeProposalText(change.from || "（空）")}</span>`
      + `<span class="proposal-arrow">→</span>`
      + `<span class="proposal-to">${escapeProposalText(change.to || "")}</span></li>`)
    .join("");
  return rows ? `<ul class="proposal-diff">${rows}</ul>` : "";
}

function sourcesMarkup(sources) {
  const items = (Array.isArray(sources) ? sources : [])
    .filter(Boolean)
    .map((source) => `<li>${escapeProposalText(source)}</li>`)
    .join("");
  return items ? `<details class="proposal-sources"><summary>查看来源</summary>`
    + `<ul>${items}</ul></details>` : "";
}

export function proposalCanApply(proposal) {
  return proposal?.status === "pending" && proposal?.can_apply !== false;
}

export function proposalMarkup(proposal) {
  const status = String(proposal?.status || "pending");
  const kind = kindLabel(proposal?.kind);
  const requiresDialogue = proposal?.requires_dialogue && !proposal?.verdict_ready;
  const applyButton = requiresDialogue
    ? `<button type="button" class="text-button" data-proposal-action="discuss" data-proposal-id="${escapeProposalText(proposal?.id)}">先和苏格拉底确认</button>`
    : `<button type="button" class="small-button" data-proposal-action="apply" data-proposal-id="${escapeProposalText(proposal?.id)}">采纳</button>`;
  const actions = status === "pending"
    ? `<div class="proposal-actions">${applyButton}`
      + `<button type="button" class="text-button" data-proposal-action="reject" data-proposal-id="${escapeProposalText(proposal?.id)}">忽略</button></div>`
    : "";
  return `<article class="proposal-card" data-proposal-id="${escapeProposalText(proposal?.id)}" data-proposal-status="${escapeProposalText(status)}">
    <header class="proposal-head">
      <div><span class="eyebrow">${escapeProposalText(kind)}</span>
      <h4>${escapeProposalText(proposal?.title || "AI 建议")}</h4></div>
      <span class="proposal-status">${status === "applied" ? "已采纳" : status === "rejected" ? "已忽略" : status === "pending" ? "待确认" : escapeProposalText(status)}</span>
    </header>
    ${proposal?.reason ? `<p class="proposal-reason">${escapeProposalText(proposal.reason)}</p>` : ""}
    ${changesMarkup(proposal?.payload?.changes)}
    ${sourcesMarkup(proposal?.sources)}
    ${actions}
  </article>`;
}

export function proposalsMarkup(proposals) {
  return (Array.isArray(proposals) ? proposals : []).map(proposalMarkup).join("");
}
