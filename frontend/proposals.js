// 提案卡片动作：AI 只提议，用户点“采纳”才调用 apply。
// 任何写入都走后端；前端不做乐观写入，成功后广播 torch:proposals-changed。
import { api, toast } from "./core.js";
import { proposalsMarkup } from "./proposals-model.js";

let bound = false;

export function renderProposals(container, proposals) {
  if (!container) return;
  container.innerHTML = proposalsMarkup(proposals);
}

export function initProposals() {
  if (bound) return;
  bound = true;
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-proposal-action]");
    if (!button) return;
    const proposalId = button.dataset.proposalId;
    const action = button.dataset.proposalAction;
    if (!proposalId) return;
    if (action === "discuss") {
      toast("身份修改需要先在自我档案里和苏格拉底对话。", "info");
      return;
    }
    if (action === "apply") {
      await applyProposal(proposalId, button);
    } else if (action === "reject") {
      await rejectProposal(proposalId, button);
    }
  });
}

async function applyProposal(proposalId, button) {
  if (button.disabled) return;
  button.disabled = true;
  const original = button.textContent;
  button.textContent = "应用中…";
  try {
    const result = await api(`/api/proposals/${encodeURIComponent(proposalId)}/apply`, {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
      timeoutMs: 120000,
    });
    toast(result?.status === "applied" ? "已采纳，写进库里了。" : "提案已处理。", "success");
    document.dispatchEvent(new CustomEvent("torch:proposals-changed", {
      detail: { proposalId, result },
    }));
  } catch (error) {
    toast(`采纳失败：${error.message}`, "error");
    button.disabled = false;
    button.textContent = original;
  }
}

async function rejectProposal(proposalId, button) {
  if (button.disabled) return;
  button.disabled = true;
  try {
    await api(`/api/proposals/${encodeURIComponent(proposalId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason: "" }),
      timeoutMs: 60000,
    });
    toast("已忽略这条建议。", "info");
    document.dispatchEvent(new CustomEvent("torch:proposals-changed", {
      detail: { proposalId, rejected: true },
    }));
  } catch (error) {
    toast(`忽略失败：${error.message}`, "error");
    button.disabled = false;
  }
}
