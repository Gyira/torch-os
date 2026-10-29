// 页面内 AI 角色抽屉（2026-10-29，v1.0 P1）。
// 所有角色共用这一套抽屉：角色卡由后端 /api/ai/roles 提供，对话走 /api/ai/chat。
// 「与神对话」的 dsh iframe 仍是总入口；这里是带范围、带提案的任务型对话。
import { $, api, toast } from "./core.js";
import { renderMarkdownDocument } from "./pillar-markdown.js";
import { emptyMarkup, messageMarkup, quickChips, drawerSubtitle } from "./roles-model.js";
import { proposalsMarkup } from "./proposals-model.js";
import { initProposals } from "./proposals.js";

const state = {
  role: null,
  mode: "",
  entry: "",
  selectors: [],
  threadId: "",
  messages: [],
  busy: false,
};
let rolesCache = null;
let initialized = false;

function el(id) { return document.getElementById(id); }

export function initRoleDrawer() {
  if (initialized) return;
  initialized = true;
  initProposals();
  el("role-drawer-close")?.addEventListener("click", closeRoleDrawer);
  el("role-drawer-backdrop")?.addEventListener("click", closeRoleDrawer);
  el("role-drawer-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    sendRoleMessage(el("role-drawer-input")?.value || "");
  });
  el("role-drawer-input")?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    if (event.isComposing) return; // 中文输入法选词时不要误发
    event.preventDefault();
    sendRoleMessage(event.target.value || "");
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !el("role-drawer")?.hidden) closeRoleDrawer();
  });
  document.addEventListener("torch:open-role", (event) => {
    const detail = event.detail || {};
    if (detail.roleId) openRoleDrawer(detail);
  });
}

export async function openRoleDrawer(options = {}) {
  if (state.busy) return;
  let status;
  try {
    status = await api("/api/ai/status");
  } catch (error) {
    toast(`AI 角色不可用：${error.message}`, "error");
    return;
  }
  if (!status?.available) {
    toast(status?.reason || "AI 角色暂不可用，请先在 dsh 里配置 DeepSeek Key。", "error");
    return;
  }
  let items;
  try {
    if (!rolesCache) {
      rolesCache = (await api("/api/ai/roles")).items || [];
    }
    items = rolesCache;
  } catch (error) {
    toast(`读取角色失败：${error.message}`, "error");
    return;
  }
  const role = items.find((item) => item.id === options.roleId);
  if (!role) {
    toast("没有找到这个 AI 角色。", "error");
    return;
  }
  const modes = Array.isArray(role.modes) ? role.modes : [];
  const mode = options.mode && modes.some((item) => item.id === options.mode)
    ? options.mode
    : (modes[0]?.id || "");
  state.role = role;
  state.mode = mode;
  state.entry = options.entry || (Array.isArray(role.entry) ? role.entry[0] : "") || "";
  state.selectors = Array.isArray(options.selectors) ? options.selectors : [];
  state.threadId = "";
  state.messages = [];
  state.busy = false;
  el("role-drawer-eyebrow").textContent = String(role.layer || "AI").toUpperCase();
  el("role-drawer-title").textContent = options.title || role.name;
  el("role-drawer-subtitle").textContent = drawerSubtitle(role, mode);
  el("role-drawer-status").textContent = "";
  el("role-drawer-input").value = options.prompt || "";
  el("role-drawer").hidden = false;
  el("role-drawer-backdrop").hidden = false;
  document.body?.classList?.add("role-drawer-open");
  renderRoleDrawer();
  el("role-drawer-input")?.focus();
}

export function closeRoleDrawer() {
  const drawer = el("role-drawer");
  if (drawer) drawer.hidden = true;
  const backdrop = el("role-drawer-backdrop");
  if (backdrop) backdrop.hidden = true;
  document.body?.classList?.remove("role-drawer-open");
}

async function sendRoleMessage(text) {
  const message = String(text || "").trim();
  if (!message || state.busy || !state.role) return;
  state.busy = true;
  state.messages.push({ role: "user", content: message });
  const input = el("role-drawer-input");
  if (input) input.value = "";
  setBusy(true, "AI 正在想…");
  renderRoleDrawer();
  try {
    const payload = await api("/api/ai/chat", {
      method: "POST",
      body: JSON.stringify({
        role_id: state.role.id,
        mode: state.mode,
        entry: state.entry,
        selectors: state.selectors,
        thread_id: state.threadId,
        message,
      }),
      timeoutMs: 180000,
    });
    state.threadId = payload.thread_id || "";
    const reply = String(payload.reply || "");
    const rendered = renderMarkdownDocument(reply);
    state.messages.push({
      role: "assistant",
      content: reply,
      html: rendered.html,
      sources: payload.sources || [],
      proposals: payload.proposals || [],
    });
    setBusy(false, payload.truncated ? "部分资料因篇幅被截断。" : "");
    if (payload.hub_events) {
      // 忆灵事件只计数；具体卡片在自我档案里审阅（P3）。
      el("role-drawer-hint").textContent = `已告诉忆灵 ${payload.hub_events} 条观察；AI 只提议，采纳后才写入。`;
    }
  } catch (error) {
    setBusy(false, "");
    toast(`这一轮没有成功：${error.message}`, "error");
  } finally {
    state.busy = false;
    renderRoleDrawer();
    el("role-drawer-input")?.focus();
  }
}

function setBusy(busy, message) {
  const send = el("role-drawer-send");
  if (send) {
    send.disabled = busy;
    send.textContent = busy ? "思考中…" : "发送";
  }
  const status = el("role-drawer-status");
  if (status) status.textContent = message || "";
}

function renderRoleDrawer() {
  const messages = el("role-drawer-messages");
  if (!messages) return;
  if (!state.messages.length) {
    messages.innerHTML = emptyMarkup(state.role?.name || "AI 角色");
  } else {
    messages.innerHTML = state.messages.map((message) => {
      const proposals = message.proposals?.length
        ? `<div class="role-proposals">${proposalsMarkup(message.proposals)}</div>`
        : "";
      return messageMarkup(message) + proposals;
    }).join("");
  }
  messages.scrollTop = messages.scrollHeight;
  const chips = el("role-drawer-chips");
  if (chips) {
    const values = quickChips(state.role?.id, state.mode);
    chips.innerHTML = values.map((value) =>
      `<button type="button" class="role-chip" data-role-chip="${escapeAttr(value)}">${escapeAttr(value)}</button>`
    ).join("");
    chips.querySelectorAll("[data-role-chip]").forEach((button) => {
      button.addEventListener("click", () => sendRoleMessage(button.dataset.roleChip));
    });
  }
}

function escapeAttr(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));
}
