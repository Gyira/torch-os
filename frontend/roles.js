// 页面内 AI 角色抽屉（2026-10-29，v1.0 P1；2026-10-31 编辑器宿主 + 选区）。
// 所有角色共用这一套抽屉和对话状态：全局右侧栏，或编辑器 dialog 内的右侧面板/
// 底部悬浮输入条。角色卡由后端 /api/ai/roles 提供，对话走 /api/ai/chat。
import { api, toast } from "./core.js";
import { renderMarkdownDocument } from "./pillar-markdown.js";
import { emptyMarkup, messageMarkup, quickChips, drawerSubtitle } from "./roles-model.js";
import { proposalsMarkup } from "./proposals-model.js";
import { initProposals } from "./proposals.js";

const MAX_SELECTION_CHARS = 4000;
const MAX_DRAFT_CHARS = 12000;

const state = {
  role: null,
  mode: "",
  entry: "",
  selectors: [],
  // 阅读器里的当前文档：打开时带上，之后每轮自动作为 note 选择器发送。
  note: null,
  chips: [],
  threadId: "",
  messages: [],
  busy: false,
  host: "body",
  docked: false,
  selection: null,
  getDraftText: null,
  getSelectors: null,
  onError: null,
  trigger: null,
  draftContext: null,
};
let rolesCache = null;
let initialized = false;
let roleSession = 0;
const drafts = new Map();
function draftKey() {
  if (state.draftContext === "") return ""; // 未保存的新文档没有稳定身份，不跨打开暂存。
  return state.role ? JSON.stringify([state.role.id, state.mode, state.entry, state.draftContext ?? [state.note?.path || "", state.selectors]]) : "";
}
function rememberDraft() {
  const key = draftKey();
  if (!key) return;
  drafts.delete(key);
  const value = el("role-drawer-input")?.value || "";
  if (value) drafts.set(key, value);
  if (drafts.size > 20) drafts.delete(drafts.keys().next().value);
}
function showRoleSurface() {
  const modal = el("role-drawer-modal");
  if (state.host === "body" && modal && !modal.open) modal.showModal();
}

function el(id) { return document.getElementById(id); }
function drawer() { return el("role-drawer"); }
function editorDialog() { return el("note-editor-dialog"); }
function editorSlot() { return el("note-editor-companion-slot"); }

export function initRoleDrawer() {
  if (initialized) return;
  initialized = true;
  initProposals();
  el("role-drawer-close")?.addEventListener("click", closeRoleDrawer);
  const modal = el("role-drawer-modal");
  modal?.addEventListener("cancel", (event) => { event.preventDefault(); closeRoleDrawer(); });
  let backdropPressed = false;
  modal?.addEventListener("pointerdown", (event) => { backdropPressed = event.target === modal; });
  modal?.addEventListener("click", (event) => {
    if (backdropPressed && event.target === modal) closeRoleDrawer();
    backdropPressed = false;
  });
  el("role-drawer-dock")?.addEventListener("click", toggleRoleDock);
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
  el("role-drawer-input")?.addEventListener("input", () => { rememberDraft(); syncSendState(); });
  el("role-drawer-context")?.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-role-clear-selection]")) {
      setRoleSelection(null);
    }
  });
  document.addEventListener("torch:open-role", (event) => {
    const detail = event.detail || {};
    if (detail.roleId) openRoleDrawer(detail);
  });
  // 角色管理页保存后清掉缓存，下一次打开抽屉会读到新的名字、入口与模式。
  document.addEventListener("torch:roles-changed", () => {
    rolesCache = null;
  });
}

export function invalidateRolesCache() {
  rolesCache = null;
}

function mountRoleHost(host) {
  const node = drawer();
  if (!node) return "body";
  const dialog = editorDialog();
  const slot = editorSlot();
  if (host === "editor" && slot && dialog?.open) {
    el("role-drawer-modal")?.close();
    if (node.parentElement !== slot) slot.append(node);
    slot.hidden = false;
    node.classList.add("is-embedded");
    dialog.classList.add("has-companion");
    state.host = "editor";
    const backdrop = el("role-drawer-backdrop");
    if (backdrop) backdrop.hidden = true;
    const dock = el("role-drawer-dock");
    if (dock) dock.hidden = false;
    return "editor";
  }
  const modal = el("role-drawer-modal");
  if (modal && node.parentElement !== modal) modal.append(node);
  if (slot) slot.hidden = true;
  node.classList.remove("is-embedded", "is-docked");
  if (dialog) dialog.classList.remove("has-companion", "has-companion-dock");
  state.host = "body";
  state.docked = false;
  const dock = el("role-drawer-dock");
  if (dock) dock.hidden = true;
  return "body";
}

function unmountRoleHost() {
  const node = drawer();
  if (!node) return;
  if (node.parentElement !== document.body) document.body.append(node);
  const slot = editorSlot();
  if (slot) slot.hidden = true;
  node.classList.remove("is-embedded", "is-docked");
  const dialog = editorDialog();
  if (dialog) dialog.classList.remove("has-companion", "has-companion-dock");
  state.host = "body";
  state.docked = false;
}

function normalizeSelection(selection) {
  if (!selection || typeof selection !== "object") return null;
  const text = String(selection.text || "").trim();
  if (!text) return null;
  const source = String(selection.source || "").trim().slice(0, 200);
  return {
    text: text.slice(0, MAX_SELECTION_CHARS),
    source,
    chars: Math.min(text.length, MAX_SELECTION_CHARS),
  };
}

/**
 * 阅读器当前文档。浏览器只传标题/路径/字数这类元信息，正文仍由后端按
 * `{entry:"note", path}` 读取——和选区、草稿一样，不让页面自带任意资料。
 */
function normalizeNote(note) {
  if (!note || typeof note !== "object") return null;
  const path = String(note.path || "").trim();
  if (!path) return null;
  const fallback = path.split("/").pop()?.replace(/\.md$/i, "") || "这篇文档";
  const title = (String(note.title || "").trim() || fallback).slice(0, 160);
  return {
    path,
    title,
    chapter: String(note.chapter || ""),
    chars: Math.max(0, Number(note.chars) || 0),
  };
}

function normalizeChips(chips) {
  if (!Array.isArray(chips)) return [];
  return chips
    .map((chip) => String(chip || "").trim())
    .filter(Boolean)
    .slice(0, 4);
}

function noteHint(note) {
  return note
    ? `已自动读取《${note.title}》；AI 只提议，采纳后才写入。`
    : "AI 只提议，采纳后才写入。";
}

export function setRoleSelection(selection) {
  state.selection = normalizeSelection(selection);
  renderRoleContext();
}

export function clearRoleSelection() {
  setRoleSelection(null);
}

export function toggleRoleDock() {
  if (state.host !== "editor") return;
  state.docked = !state.docked;
  applyDockState();
  renderRoleDrawer();
  if (!state.docked) el("role-drawer-input")?.focus();
}

function applyDockState() {
  const node = drawer();
  const dialog = editorDialog();
  const button = el("role-drawer-dock");
  node?.classList.toggle("is-docked", state.docked);
  dialog?.classList.toggle("has-companion-dock",
    state.docked && state.host === "editor");
  if (button) {
    button.textContent = state.docked ? "展开对话" : "收起";
    button.setAttribute("aria-expanded", state.docked ? "false" : "true");
  }
}

function reportRoleError(message) {
  const text = String(message || "AI 角色暂不可用。");
  const status = el("role-drawer-status");
  if (status) status.textContent = text;
  if (typeof state.onError === "function") {
    try { state.onError(text); } catch { /* 宿主自己处理失败 */ }
  } else {
    toast(text, "error");
  }
}

export async function openRoleDrawer(options = {}) {
  const node = drawer();
  if (!node || state.busy) return false;
  const roleId = String(options.roleId || "").trim();
  if (!roleId) return false;
  const session = ++roleSession;
  const trigger = document.activeElement;
  const previousDraftKey = draftKey();
  rememberDraft();
  const requestedHost = options.host === "editor" ? "editor" : "body";

  // 已经开着同一个角色：保留对话，只更新选区/草稿 getter 和宿主。
  if (!node.hidden && state.role?.id === roleId) {
    state.draftContext = requestedHost === "editor" ? options.draftContext || "" : null;
    state.trigger = state.trigger || document.activeElement;
    state.getDraftText = typeof options.getDraftText === "function"
      ? options.getDraftText : state.getDraftText;
    state.getSelectors = typeof options.getSelectors === "function"
      ? options.getSelectors : state.getSelectors;
    state.onError = typeof options.onError === "function" ? options.onError : state.onError;
    if (Array.isArray(options.selectors)) state.selectors = options.selectors;
    if (options.entry) state.entry = options.entry;
    if (options.mode && state.role.modes?.some((item) => item.id === options.mode)) {
      state.mode = options.mode;
    }
    // 换了一份上下文（另一篇文档，或从文档回到无文档）就换一段对话：旧轮次带的是旧资料。
    const nextNote = normalizeNote(options.note);
    const noteChanged = (nextNote?.path || "") !== (state.note?.path || "") || previousDraftKey !== draftKey();
    if (noteChanged) {
      state.threadId = "";
      state.messages = [];
      state.chips = normalizeChips(options.chips);
      state.selection = null;
      const input = el("role-drawer-input");
      if (input) input.value = "";
    } else if (Array.isArray(options.chips)) {
      state.chips = normalizeChips(options.chips);
    }
    state.note = nextNote;
    if (previousDraftKey !== draftKey()) el("role-drawer-input").value = drafts.get(draftKey()) || "";
    if (options.title) el("role-drawer-title").textContent = options.title;
    el("role-drawer-subtitle").textContent = drawerSubtitle(state.role, state.mode, state.note);
    if (options.hint) {
      el("role-drawer-hint").textContent = options.hint;
    } else if (noteChanged) {
      el("role-drawer-hint").textContent = state.note
        ? noteHint(state.note)
        : (typeof options.getDraftText === "function"
          ? "发送时会带上当前草稿；AI 只提议，采纳后才写入。"
          : "AI 只提议，采纳后才写入。");
    }
    setRoleSelection(options.selection || state.selection);
    const host = mountRoleHost(requestedHost);
    state.docked = host === "editor" ? Boolean(options.dock ?? state.docked) : false;
    applyDockState();
    const backdrop = el("role-drawer-backdrop");
    if (backdrop) backdrop.hidden = host === "editor";
    if (options.prompt && !el("role-drawer-input")?.value) {
      el("role-drawer-input").value = options.prompt;
    }
    renderRoleDrawer();
    showRoleSurface();
    el("role-drawer-input")?.focus();
    return true;
  }

  // 状态检查失败时也要把错误交给宿主（编辑器内 toast 会被 dialog 盖住）。
  state.onError = typeof options.onError === "function" ? options.onError : null;
  let status;
  try {
    status = await api("/api/ai/status");
  } catch (error) {
    if (session !== roleSession) return false;
    reportRoleError(`AI 角色不可用：${error.message}`);
    return false;
  }
  if (session !== roleSession) return false;
  if (!status?.available) {
    reportRoleError(status?.reason || "AI 角色暂不可用，请先在 dsh 里配置 DeepSeek Key。");
    return false;
  }
  let items;
  try {
    if (!rolesCache) {
      const response = await api("/api/ai/roles");
      if (session !== roleSession) return false;
      rolesCache = response.items || [];
    }
    items = rolesCache;
  } catch (error) {
    if (session !== roleSession) return false;
    reportRoleError(`读取角色失败：${error.message}`);
    return false;
  }
  const role = items.find((item) => item.id === roleId);
  if (requestedHost === "editor" && !editorDialog()?.open) return false;
  if (!role) {
    reportRoleError("没有找到这个 AI 角色。");
    return false;
  }
  const modes = Array.isArray(role.modes) ? role.modes : [];
  const mode = options.mode && modes.some((item) => item.id === options.mode)
    ? options.mode
    : (modes[0]?.id || "");
  state.role = role;
  state.draftContext = requestedHost === "editor" ? options.draftContext || "" : null;
  state.mode = mode;
  state.entry = options.entry || (Array.isArray(role.entry) ? role.entry[0] : "") || "";
  state.selectors = Array.isArray(options.selectors) ? options.selectors : [];
  state.note = normalizeNote(options.note);
  state.chips = normalizeChips(options.chips);
  state.threadId = "";
  state.messages = [];
  state.busy = false;
  state.getDraftText = typeof options.getDraftText === "function"
    ? options.getDraftText : null;
  state.getSelectors = typeof options.getSelectors === "function"
    ? options.getSelectors : null;
  state.onError = typeof options.onError === "function" ? options.onError : null;
  state.trigger = trigger;
  state.selection = normalizeSelection(options.selection);
  const host = mountRoleHost(requestedHost);
  state.docked = host === "editor" ? Boolean(options.dock) : false;
  applyDockState();
  node.dataset.layer = String(role.layer || "");
  el("role-drawer-eyebrow").textContent = String(role.layer || "AI").toUpperCase();
  el("role-drawer-title").textContent = options.title || role.name;
  const avatar = el("role-drawer-avatar");
  if (avatar) avatar.textContent = Array.from(String(role.name || "AI"))[0] || "AI";
  el("role-drawer-subtitle").textContent = drawerSubtitle(role, mode, state.note);
  el("role-drawer-status").textContent = "";
  el("role-drawer-preview").textContent = "";
  el("role-drawer-preview").hidden = true;
  el("role-drawer-input").value = drafts.get(draftKey()) || options.prompt || "";
  el("role-drawer-hint").textContent = options.hint
    || (state.note
      ? noteHint(state.note)
      : typeof options.getDraftText === "function"
        ? "发送时会带上当前草稿；AI 只提议，采纳后才写入。"
        : "AI 只提议，采纳后才写入。");
  node.hidden = false;
  const backdrop = el("role-drawer-backdrop");
  if (backdrop) backdrop.hidden = host === "editor";
  document.body?.classList?.add("role-drawer-open");
  renderRoleDrawer();
  showRoleSurface();
  el("role-drawer-input")?.focus();
  return true;
}

export function closeRoleDrawer() {
  rememberDraft();
  ++roleSession;
  el("role-drawer-modal")?.close();
  const node = drawer();
  if (node) node.hidden = true;
  const backdrop = el("role-drawer-backdrop");
  if (backdrop) backdrop.hidden = true;
  document.body?.classList?.remove("role-drawer-open");
  const trigger = state.trigger;
  if (state.host === "editor") unmountRoleHost();
  state.role = null;
  state.mode = "";
  state.entry = "";
  state.selectors = [];
  state.note = null;
  state.chips = [];
  state.threadId = "";
  state.messages = [];
  state.busy = false;
  state.selection = null;
  state.getDraftText = null;
  state.getSelectors = null;
  state.onError = null;
  state.trigger = null;
  state.docked = false;
  if (trigger?.isConnected !== false) trigger?.focus?.({ preventScroll: true });
}

export function isRoleDrawerOpen() {
  return Boolean(drawer() && !drawer().hidden);
}

export function isRoleDrawerEmbedded() {
  return state.host === "editor";
}

function currentDraftText() {
  if (typeof state.getDraftText !== "function") return "";
  try {
    return String(state.getDraftText() || "").slice(0, MAX_DRAFT_CHARS);
  } catch {
    return "";
  }
}

function withNoteSelector(list) {
  const selectors = Array.isArray(list) ? [...list] : [];
  const notePath = String(state.note?.path || "");
  if (!notePath) return selectors;
  if (selectors.some((item) => String(item?.entry || "") === "note"
    && String(item?.path || "") === notePath)) {
    return selectors;
  }
  selectors.push({ entry: "note", path: notePath });
  return selectors;
}

async function sendRoleMessage(text) {
  const message = String(text || "").trim();
  if (!message || state.busy || !state.role) return;
  const session = roleSession;
  const selection = state.selection;
  const draftText = currentDraftText();
  let selectors = withNoteSelector(state.selectors);
  if (typeof state.getSelectors === "function") {
    try {
      const next = state.getSelectors();
      if (Array.isArray(next)) selectors = withNoteSelector(next);
    } catch { /* 宿主暂时不可用：保留打开时的选择器 */ }
  }
  state.busy = true;
  state.messages.push({
    role: "user",
    content: message,
    context: (selection || state.note) ? {
      selection: selection ? { source: selection.source, chars: selection.chars } : null,
      note: state.note ? { title: state.note.title, chars: state.note.chars } : null,
    } : null,
  });
  const input = el("role-drawer-input");
  const submittedInput = input?.value || "";
  const fromInput = submittedInput.trim() === message;
  if (input && fromInput) input.value = "";
  if (el("role-drawer-chips")?.contains(document.activeElement)) input?.focus();
  rememberDraft();
  setBusy(true, "AI 正在想…关闭后将不再显示本轮回复。");
  renderRoleDrawer(true);
  try {
    const payload = await api("/api/ai/chat", {
      method: "POST",
      body: JSON.stringify({
        role_id: state.role.id,
        mode: state.mode,
        entry: state.entry,
        selectors,
        thread_id: state.threadId,
        message,
        selection_text: selection?.text || "",
        selection_source: selection?.source || "",
        selection_path: selection ? (state.note?.path || "") : "",
        draft_text: draftText,
      }),
      timeoutMs: 180000,
    });
    if (session !== roleSession) return;
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
    setBusy(false, [payload.context_reset ? "历史对话保留，从这一轮起按新的资料范围继续。" : "",
      payload.truncated ? "部分资料因篇幅被截断。" : "",
      ...(payload.action_errors || [])].filter(Boolean).join(" "));
    if (payload.hub_events) {
      // 忆灵事件只计数；具体卡片在自我档案里审阅（P3）。
      el("role-drawer-hint").textContent = `已告诉忆灵 ${payload.hub_events} 条观察；AI 只提议，采纳后才写入。`;
    }
  } catch (error) {
    if (session !== roleSession) return;
    if (fromInput && input && !input.value) input.value = submittedInput;
    rememberDraft();
    setBusy(false, "");
    reportRoleError(`这一轮没有成功：${error.message}`);
  } finally {
    if (session === roleSession) {
      state.busy = false;
      renderRoleDrawer();
    }
  }
}

function syncSendState() {
  const send = el("role-drawer-send");
  if (!send) return;
  send.disabled = state.busy || !el("role-drawer-input")?.value.trim();
  send.setAttribute("aria-busy", String(state.busy));
  send.textContent = state.busy ? "思考中…" : "发送";
}

function setBusy(busy, message) {
  state.busy = busy;
  syncSendState();
  const status = el("role-drawer-status");
  if (status) status.textContent = message || "";
}

function renderRoleContext() {
  const host = el("role-drawer-context");
  if (!host) return;
  const chips = [];
  if (state.note) {
    const chars = Number(state.note.chars) || 0;
    const size = chars ? ` · ${chars.toLocaleString("zh-CN")} 字` : "";
    chips.push(`<span class="role-context-chip is-note" title="每轮发送时会自动读取这篇文档；超出上下文预算会截断">
      <span>正在读《${escapeAttr(state.note.title)}》${escapeAttr(size)}</span>
    </span>`);
  }
  if (state.selection) {
    const chars = Number(state.selection.chars) || 0;
    const label = chars ? `引用了选文 · ${chars} 字` : "引用了选文";
    chips.push(`<span class="role-context-chip" title="${escapeAttr(state.selection.source || "")}">
      <span>${escapeAttr(label)}</span>
      <button type="button" class="role-context-clear" data-role-clear-selection aria-label="移除引用的选文">×</button>
    </span>`);
  }
  host.innerHTML = chips.join("");
  host.hidden = !chips.length;
}

function renderRoleDrawer(forceScroll = false) {
  syncSendState();
  const messages = el("role-drawer-messages");
  if (!messages) return;
  const previousScroll = messages.scrollTop;
  const followBottom = forceScroll || messages.scrollHeight - messages.clientHeight - messages.scrollTop < 64;
  let markup;
  if (!state.messages.length) {
    markup = emptyMarkup(state.role?.name || "AI 角色", state.note);
  } else {
    markup = state.messages.map((message) => {
      const proposals = message.proposals?.length
        ? `<div class="role-proposals">${proposalsMarkup(message.proposals)}</div>`
        : "";
      return messageMarkup(message) + proposals;
    }).join("");
  }
  if (messages.innerHTML !== markup) messages.innerHTML = markup;
  messages.scrollTop = followBottom ? messages.scrollHeight : previousScroll;
  const chips = el("role-drawer-chips");
  if (chips) {
    // 带着文档时，先把「贴着这篇文档」的问题放在前面；不再混入泛泛的角色默认问题。
    const docChips = Array.isArray(state.chips) ? state.chips : [];
    const values = docChips.length ? docChips : quickChips(state.role?.id, state.mode);
    const signature = JSON.stringify(values);
    if (chips.dataset.values !== signature) {
      chips.dataset.values = signature;
      chips.innerHTML = values.map((value) =>
        `<button type="button" class="role-chip" data-role-chip="${escapeAttr(value)}">${escapeAttr(value)}</button>`
      ).join("");
      chips.querySelectorAll("[data-role-chip]").forEach((button) => {
        button.addEventListener("click", () => sendRoleMessage(button.dataset.roleChip));
      });
    }
    chips.querySelectorAll("[data-role-chip]").forEach((button) => {
      button.disabled = state.busy;
    });
  }
  renderRoleContext();
  const preview = el("role-drawer-preview");
  if (preview) {
    const last = [...state.messages].reverse().find((item) =>
      item.role === "assistant" && String(item.content || "").trim());
    if (state.docked && last) {
      const line = String(last.content).replace(/\s+/g, " ").trim();
      preview.textContent = line.length > 90 ? `${line.slice(0, 89)}…` : line;
      preview.hidden = false;
    } else {
      preview.textContent = "";
      preview.hidden = true;
    }
  }
}

function escapeAttr(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));
}
