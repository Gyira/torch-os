// 人生导师页（2026-11-01）：三栏对话 / 会话 / 待确认 / 记忆 / 每日三问。
// 对话走 /api/ai/chat（role_id=mentor）；B4b 起档案更新经后端校验后直接提交，
// 旧待确认卡仍保留处理入口。
import { api, confirmAction, escapeHtml, openDialog, toast } from "./core.js";
import { renderMarkdownDocument } from "./pillar-markdown.js";
import { isObsidianHost, obsidianOpenUri, openNoteMessage } from "./task-open.js";
import {
  emptyChatMarkup, memoryMarkup, messageMarkup, pendingMarkup,
  questionsMarkup, sessionListMarkup, sideTabsMarkup, sourcesMarkup,
} from "./mentor-model.js";

const state = {
  host: null,
  initialized: false,
  active: false,
  overview: null,
  sessions: [],
  legacy: [],
  threadId: "",
  threadTitle: "",
  legacyThread: false,
  messages: [],
  pending: [],
  lastSources: [],
  activeTab: "questions",
  busy: false,
  draftBusy: false,
  newConversation: false,
  editingPending: "",
  questionNotice: { message: "", tone: "" },
  // 没有服务端 ID 的新对话也有稳定的本地身份，草稿和迟到响应都按它归属。
  localConversationId: "",
};

// 页面内草稿：threadId 稳定后沿用同一个身份；未发送的新对话用 local id。
const drafts = new Map();
// 每个会话自己的在途请求；A 的 finally 不能解除 B 的 busy。
const pendingRequests = new Map();
// 服务端在模型返回后才落用户轮次；切换回来时先把在途用户消息补回视图。
const pendingMessages = new Map();
let localConversationSeq = 0;
let requestSeq = 0;
let openSeq = 0;

function el(id) { return document.getElementById(id); }
function shell() { return state.host?.querySelector?.(".mentor-shell"); }

function conversationKey() {
  if (state.threadId) return `thread:${state.threadId}`;
  if (state.localConversationId) return `local:${state.localConversationId}`;
  return "";
}

function ensureConversationIdentity(forceNew = false) {
  if (forceNew || (!state.threadId && !state.localConversationId)) {
    state.localConversationId = `local-${++localConversationSeq}`;
  }
  return conversationKey();
}

function draftKey() {
  const key = conversationKey();
  return key ? `mentor-draft:${key}` : "";
}

function currentInput() {
  return state.host?.querySelector?.("[data-mentor-input]") || null;
}

function rememberDraft() {
  const key = draftKey();
  const input = currentInput();
  if (!key || !input) return;
  const value = String(input.value || "");
  if (value) drafts.set(key, value);
  else drafts.delete(key);
  while (drafts.size > 30) drafts.delete(drafts.keys().next().value);
}

function restoreDraft() {
  const input = currentInput();
  if (!input) return;
  input.value = drafts.get(draftKey()) || "";
}

function migrateConversationIdentity(fromKey, toKey) {
  if (!fromKey || !toKey || fromKey === toKey) return;
  if (drafts.has(fromKey)) {
    drafts.set(toKey, drafts.get(fromKey));
    drafts.delete(fromKey);
  }
  if (pendingRequests.has(fromKey)) {
    pendingRequests.set(toKey, pendingRequests.get(fromKey));
    pendingRequests.delete(fromKey);
  }
}

function isCurrentConversation(key) {
  return Boolean(key) && conversationKey() === key;
}

function beginOpen() {
  return ++openSeq;
}

function renderMarkdown(text) {
  try { return renderMarkdownDocument(String(text || "")).html; }
  catch { return escapeHtml(String(text || "")).replace(/\n/g, "<br>"); }
}

function vaultName() {
  return String(state.overview?.vault_name || el("self-view")?.dataset?.vaultName || "").trim();
}

function openVaultPath(path) {
  const relative = String(path || "").trim();
  if (!relative) return;
  if (isObsidianHost() && globalThis.parent && globalThis.parent !== globalThis) {
    try { globalThis.parent.postMessage(openNoteMessage(relative), "*"); return; } catch { /* fall through */ }
  }
  const uri = obsidianOpenUri(vaultName(), relative);
  if (!uri) { toast("没有可用的 Obsidian 库名。", "warning"); return; }
  try { globalThis.open?.(uri, "_blank", "noopener"); } catch { /* ignore */ }
}

function shellMarkup() {
  return `<div class="mentor-shell">
    <header class="mentor-head">
      <div>
        <p class="eyebrow">LIFE MENTOR · 人生导师</p>
        <h2>我在这里，先听你说。</h2>
        <p class="field-help">读你的档案、记忆、最近日记与任务；需要更多信息时会问你。档案由我整理，事实与推断分开标注，旧判断保留为成长历史；你可以随时在对话里纠正。</p>
      </div>
      <div class="mentor-head-actions">
        <button type="button" class="button secondary" data-mentor-new>＋ 新对话</button>
        <button type="button" class="button primary" data-mentor-synthesize>更新自我档案</button>
        <button type="button" class="text-button" data-mentor-refresh>刷新</button>
      </div>
    </header>
    <div class="mentor-layout">
      <aside class="mentor-sessions" aria-label="导师会话">
        <header class="mentor-sessions-head"><p class="eyebrow">SESSIONS</p><span class="meta">对话</span></header>
        <div class="mentor-session-list" data-mentor-sessions></div>
      </aside>
      <section class="mentor-chat" aria-label="与人生导师对话">
        <header class="mentor-chat-head">
          <div><p class="eyebrow" data-mentor-chat-eyebrow>MENTOR</p><h3 data-mentor-chat-title>新对话</h3></div>
          <span class="meta" data-mentor-chat-meta></span>
        </header>
        <div class="mentor-messages" data-mentor-messages aria-live="polite"></div>
        <div class="mentor-inline-pending" data-mentor-inline-pending></div>
        <form class="mentor-composer" data-mentor-form>
          <label class="visually-hidden" for="mentor-input">给人生导师发消息</label>
          <textarea id="mentor-input" data-mentor-input rows="3" maxlength="8000" placeholder="想聊什么？也可以直接说「档案里这条不对，因为…」"></textarea>
          <div class="mentor-composer-actions">
            <span class="status-message" data-mentor-status role="status"></span>
            <span class="field-help">Enter 发送 · Shift+Enter 换行</span>
            <button type="submit" class="button primary" data-mentor-send>发送</button>
          </div>
        </form>
      </section>
      <aside class="mentor-side" aria-label="导师上下文">
        <nav class="mentor-side-tabs" data-mentor-tabs></nav>
        <div class="mentor-side-body" data-mentor-side-body></div>
      </aside>
    </div>
  </div>`;
}

function ensureShell() {
  if (!state.host) return false;
  if (shell()) return true;
  state.host.innerHTML = shellMarkup();
  return true;
}

function renderSessions() {
  const host = state.host?.querySelector?.("[data-mentor-sessions]");
  if (!host) return;
  host.innerHTML = sessionListMarkup({
    sessions: state.sessions, legacy: state.legacy, currentId: state.threadId,
  });
}

function renderChatHead() {
  const title = state.host?.querySelector?.("[data-mentor-chat-title]");
  const eyebrow = state.host?.querySelector?.("[data-mentor-chat-eyebrow]");
  const meta = state.host?.querySelector?.("[data-mentor-chat-meta]");
  if (title) title.textContent = state.threadTitle || (state.threadId ? "对话" : "新对话");
  if (eyebrow) eyebrow.textContent = state.legacyThread ? "LEGACY · 旧忆灵" : "MENTOR";
  if (meta) meta.textContent = state.legacyThread ? "只读历史" : (state.threadId ? "已保存" : "尚未开始");
}

function renderMessages(forceScroll = false) {
  const host = state.host?.querySelector?.("[data-mentor-messages]");
  if (!host) return;
  const previousScroll = Number(host.scrollTop) || 0;
  const followBottom = forceScroll
    || host.scrollHeight - host.clientHeight - previousScroll < 64;
  host.innerHTML = state.messages.length
    ? state.messages.map(messageMarkup).join("")
    : emptyChatMarkup();
  host.scrollTop = followBottom ? host.scrollHeight : previousScroll;
}

function renderSide() {
  const tabs = state.host?.querySelector?.("[data-mentor-tabs]");
  if (tabs) tabs.innerHTML = sideTabsMarkup(state.activeTab);
  const body = state.host?.querySelector?.("[data-mentor-side-body]");
  if (!body) return;
  if (state.activeTab === "pending") {
    body.innerHTML = pendingMarkup(state.pending);
  } else if (state.activeTab === "memory") {
    body.innerHTML = memoryMarkup(
      state.overview?.memory || {}, state.overview?.memory_qa || {});
  } else if (state.activeTab === "sources") {
    body.innerHTML = sourcesMarkup(state.lastSources);
  } else {
    body.innerHTML = questionsMarkup(
      state.overview?.questions || {}, state.draftBusy, state.questionNotice);
  }
}

function renderAll() {
  renderSessions();
  renderChatHead();
  renderMessages();
  renderSide();
}

function setStatus(message, tone = "") {
  const node = state.host?.querySelector?.("[data-mentor-status]");
  if (!node) return;
  node.textContent = message || "";
  node.className = `status-message ${tone}`.trim();
}

function syncComposer(message = "") {
  const key = conversationKey();
  const busy = Boolean(key) && pendingRequests.has(key);
  state.busy = busy;
  const send = state.host?.querySelector?.("[data-mentor-send]");
  const input = state.host?.querySelector?.("[data-mentor-input]");
  if (send) {
    send.disabled = busy || state.legacyThread;
    send.textContent = busy ? "思考中…" : "发送";
    send.setAttribute("aria-busy", String(busy));
  }
  if (input) input.disabled = busy || state.legacyThread;
  setStatus(busy ? "导师正在想…" : message);
}

async function loadOverview({ autoOpen = true } = {}) {
  const token = openSeq;
  const payload = await api("/api/mentor/overview", { timeoutMs: 60000 });
  if (!state.active) return;
  state.overview = payload;
  state.sessions = Array.isArray(payload.threads) ? payload.threads : [];
  state.legacy = Array.isArray(payload.legacy_threads) ? payload.legacy_threads : [];
  state.pending = Array.isArray(payload.pending) ? payload.pending : [];
  if (autoOpen && token === openSeq && !state.threadId
      && !state.newConversation && state.sessions.length) {
    await openThread(state.sessions[0].id, { silent: true });
  }
}

async function openThread(threadId, { silent = false } = {}) {
  if (!threadId) return;
  rememberDraft();
  const token = beginOpen();
  try {
    const payload = await api(`/api/ai/threads/${encodeURIComponent(threadId)}`, {
      timeoutMs: 30000,
    });
    if (token !== openSeq || !state.active) return;
    const turns = Array.isArray(payload.turns) ? payload.turns : [];
    state.threadId = payload.thread?.id || threadId;
    state.localConversationId = "";
    state.threadTitle = payload.thread?.title || "";
    state.legacyThread = String(payload.thread?.role_id || "") !== "mentor";
    state.messages = turns.map((turn) => ({
      role: turn.role,
      content: turn.content,
      html: turn.role === "assistant" ? renderMarkdown(turn.content) : "",
      sources: Array.isArray(turn.sources) ? turn.sources : [],
      created_at: turn.created_at,
    }));
    const inflight = pendingMessages.get(conversationKey());
    if (inflight && !state.messages.some((item) =>
      item.role === "user" && item.content === inflight.content)) {
      state.messages.push({ ...inflight });
    }
    const lastAssistant = [...state.messages].reverse()
      .find((item) => item.role === "assistant");
    state.lastSources = lastAssistant?.sources || [];
    state.newConversation = false;
    restoreDraft();
    renderAll();
    syncComposer(state.legacyThread ? "这是旧忆灵对话，只读。" : "");
  } catch (error) {
    if (token !== openSeq || !state.active) return;
    if (silent) return;
    syncComposer();
    toast(`打不开这个会话：${error.message}`, "error");
  }
}

function newConversation() {
  rememberDraft();
  beginOpen();
  state.threadId = "";
  state.threadTitle = "";
  state.legacyThread = false;
  state.messages = [];
  state.lastSources = [];
  state.newConversation = true;
  ensureConversationIdentity(true);
  restoreDraft();
  renderAll();
  syncComposer();
  currentInput()?.focus?.();
}

function responseStatus(payload) {
  const updates = Array.isArray(payload.archive_updates) ? payload.archive_updates : [];
  const applied = updates.filter((item) => item && item.ok);
  const failed = updates.filter((item) => item && !item.ok);
  return [
    payload.context_reset ? "历史对话保留，从这一轮起按新的资料范围继续。" : "",
    payload.truncated ? "部分资料因篇幅被截断。" : "",
    applied.length ? `本轮已更新 ${applied.length} 项档案。` : "",
    failed.length ? `${failed.length} 项档案更新失败：${failed[0].error || "请在档案里核对"}` : "",
    ...(payload.action_errors || []),
  ].filter(Boolean).join(" ");
}

async function sendMessage(text) {
  const message = String(text || "").trim();
  const key = conversationKey() || ensureConversationIdentity();
  if (!message || !key || pendingRequests.has(key) || state.legacyThread) return;
  const seq = ++requestSeq;
  pendingRequests.set(key, seq);
  const pendingMessage = { role: "user", content: message, created_at: new Date().toISOString() };
  pendingMessages.set(key, pendingMessage);
  const input = currentInput();
  state.messages.push({ ...pendingMessage });
  // 点快捷问题发送时，输入框里正在写的草稿不能跟着被清掉。
  const submitted = input?.value || "";
  const fromInput = submitted.trim() === message;
  if (input && fromInput) input.value = "";
  rememberDraft();
  syncComposer();
  renderMessages(true);
  let appliedKey = key;
  try {
    const payload = await api("/api/ai/chat", {
      method: "POST",
      body: JSON.stringify({
        role_id: "mentor", mode: "converse", entry: "self.mentor",
        thread_id: state.threadId, message,
        selectors: [],
      }),
      timeoutMs: 300000,
    });
    if (pendingRequests.get(key) !== seq) return;
    if (!isCurrentConversation(key)) {
      // A 的回复留在服务端历史；不写入 B 的消息、threadId 或状态。
      pendingRequests.delete(key);
      pendingMessages.delete(key);
      return;
    }
    if (payload.thread_id && !state.threadId) {
      state.threadId = payload.thread_id;
      state.localConversationId = "";
      const nextKey = conversationKey();
      migrateConversationIdentity(key, nextKey);
      appliedKey = nextKey;
    }
    state.newConversation = false;
    const reply = String(payload.reply || "");
    state.messages.push({
      role: "assistant", content: reply, html: renderMarkdown(reply),
      sources: Array.isArray(payload.sources) ? payload.sources : [],
      archive_updates: Array.isArray(payload.archive_updates) ? payload.archive_updates : [],
      created_at: new Date().toISOString(),
      proposals: payload.proposals || [],
      pending: payload.pending || [],
    });
    if (Array.isArray(payload.sources) && payload.sources.length) {
      state.lastSources = payload.sources;
    }
    // 自主更新是对话内的非阻塞反馈，不再强制切到“待确认”；旧 pending 卡才提示。
    if (Array.isArray(payload.pending) && payload.pending.length) {
      state.pending = [...payload.pending, ...state.pending];
      state.activeTab = "pending";
    }
    pendingRequests.delete(appliedKey);
    pendingMessages.delete(key);
    pendingMessages.delete(appliedKey);
    state.busy = false;
    syncComposer(responseStatus(payload));
    renderMessages(true);
    if (payload.memory_errors?.length) {
      setStatus("回复已收到，但长期记忆保存失败；请检查存储后再处理。", "error");
      toast(`长期记忆保存失败：${payload.memory_errors.join("；")}`, "error");
    } else if (payload.pending?.length) {
      toast(`导师生成了 ${payload.pending.length} 条档案修改，待你确认。`, "success");
    } else if (payload.archive_updates?.length) {
      const failed = payload.archive_updates.filter((item) => item && !item.ok);
      if (failed.length) toast(`有 ${failed.length} 项档案更新失败，详见本轮回复。`, "error");
      else toast(`本轮已更新 ${payload.archive_updates.length} 项档案。`, "success");
    } else if (payload.memory_notes || payload.question_candidates?.length) {
      toast(`已记下 ${payload.memory_notes || 0} 条长期记忆。`, "info");
    }
    document.dispatchEvent(new CustomEvent("torch:self-refresh"));
    try { await loadOverview({ autoOpen: false }); } catch { /* keep current page */ }
    if (state.active && isCurrentConversation(appliedKey)) renderAll();
  } catch (error) {
    if (pendingRequests.get(key) === seq) pendingRequests.delete(key);
    pendingMessages.delete(key);
    if (!isCurrentConversation(key)) return;
    const last = state.messages[state.messages.length - 1];
    if (last?.role === "user" && last.content === message) state.messages.pop();
    if (!state.active) {
      // 离开页面时失败：不要把未发出的消息留在会话视图里，草稿留待重进恢复。
      if (!drafts.get(key)) drafts.set(key, message);
      return;
    }
    state.busy = false;
    syncComposer(`这一轮没有成功：${error.message}`);
    // 只有输入框仍为空才恢复原文；用户后来写的内容不能被失败覆盖。
    if (input && !input.value.trim()) input.value = message;
    rememberDraft();
    toast(`发送失败：${error.message}`, "error");
  } finally {
    if (state.active && isCurrentConversation(appliedKey)) renderMessages();
  }
}

async function draftQuestions(button) {
  if (state.draftBusy) return;
  state.draftBusy = true;
  if (button) { button.disabled = true; button.textContent = "拟答中…"; }
  try {
    const payload = await api("/api/mentor/questions/draft", {
      method: "POST", body: JSON.stringify({}), timeoutMs: 240000,
    });
    state.overview = { ...(state.overview || {}), questions: payload };
    toast(`已生成 ${payload.drafted || 0} 条导师拟答。`, "success");
  } catch (error) {
    toast(`生成拟答失败：${error.message}`, "error");
  } finally {
    state.draftBusy = false;
    renderSide();
  }
}

function questionOutcome(result, prefix = "") {
  if (result?.archive_saved === false) {
    const detail = result.archive_error ? `：${result.archive_error}` : "";
    const message = `${prefix}回答已保存，但档案整理失败${detail}`;
    state.questionNotice = { message, tone: "error" };
    toast(message, "error");
    return;
  }
  const message = `${prefix}回答已保存，档案已更新。`;
  state.questionNotice = { message, tone: "success" };
  toast(message, "success");
}

async function acceptQuestion(questionId) {
  const card = state.host?.querySelector?.(`[data-mentor-question="${cssEscape(questionId)}"]`);
  const button = card?.querySelector?.("[data-mentor-q-accept]");
  if (button) button.disabled = true;
  try {
    const result = await api(`/api/mentor/questions/${encodeURIComponent(questionId)}/answer`, {
      method: "POST",
      body: JSON.stringify({ confirm: true, text: "", accept_suggested: true }),
      timeoutMs: 60000,
    });
    questionOutcome(result, "已确认；");
    await refreshAfterQuestion();
  } catch (error) {
    state.questionNotice = { message: `写入失败：${error.message}`, tone: "error" };
    toast(`写入失败：${error.message}`, "error");
    if (button) button.disabled = false;
    renderSide();
  }
}

async function submitQuestion(questionId, text, acceptSuggested = false) {
  const answer = String(text || "").trim();
  if (!answer) { toast("先写一句回答。", "warning"); return; }
  try {
    const result = await api(`/api/mentor/questions/${encodeURIComponent(questionId)}/answer`, {
      method: "POST",
      body: JSON.stringify({
        confirm: true, text: answer, accept_suggested: Boolean(acceptSuggested),
      }),
      timeoutMs: 60000,
    });
    questionOutcome(result, "已记录；");
    await refreshAfterQuestion();
  } catch (error) {
    state.questionNotice = { message: `回答失败：${error.message}`, tone: "error" };
    toast(`回答失败：${error.message}`, "error");
    renderSide();
  }
}

async function retryQuestionArchive(questionId) {
  const item = (state.overview?.questions?.items || [])
    .find((row) => String(row.id || "") === String(questionId || ""));
  if (!item) return;
  try {
    const result = await api(`/api/mentor/questions/${encodeURIComponent(questionId)}/answer`, {
      method: "POST",
      body: JSON.stringify({
        confirm: true,
        text: String(item.answer || ""),
        accept_suggested: String(item.answer_mode || "").startsWith("suggested"),
      }),
      timeoutMs: 60000,
    });
    questionOutcome(result, "重试；");
    await refreshAfterQuestion();
  } catch (error) {
    state.questionNotice = { message: `重试档案整理失败：${error.message}`, tone: "error" };
    toast(`重试失败：${error.message}`, "error");
    renderSide();
  }
}

async function skipQuestion(questionId, forever = false) {
  try {
    await api(`/api/mentor/questions/${encodeURIComponent(questionId)}/skip`, {
      method: "POST", body: JSON.stringify({ forever }), timeoutMs: 30000,
    });
    state.questionNotice = {
      message: forever ? "以后不再问这个。" : "已跳过，7 天内不再问。",
      tone: "info",
    };
    toast(forever ? "以后不再问这个。" : "已跳过，7 天内不再问。", "info");
    await refreshAfterQuestion();
  } catch (error) {
    state.questionNotice = { message: `跳过失败：${error.message}`, tone: "error" };
    toast(`跳过失败：${error.message}`, "error");
    renderSide();
  }
}

async function refreshAfterQuestion() {
  try {
    await loadOverview({ autoOpen: false });
  } catch { /* keep page */ }
  renderAll();
  document.dispatchEvent(new CustomEvent("torch:self-refresh"));
  document.dispatchEvent(new CustomEvent("torch:mentor-pending-changed"));
}

async function acceptPending(pendingId) {
  const card = state.host?.querySelector?.(`[data-pending-id="${cssEscape(pendingId)}"]`);
  const button = card?.querySelector?.("[data-pending-accept]");
  if (button) button.disabled = true;
  try {
    await api(`/api/self/pending/${encodeURIComponent(pendingId)}/accept`, {
      method: "POST", body: JSON.stringify({ confirm: true }),
      timeoutMs: 60000,
    });
    state.pending = state.pending.filter((item) => item.pending_id !== pendingId);
    toast("已采纳，档案条目已写入。", "success");
    await afterPendingChange();
  } catch (error) {
    toast(`采纳失败：${error.message}`, "error");
    if (button) button.disabled = false;
  }
}

async function rejectPending(pendingId) {
  const card = state.host?.querySelector?.(`[data-pending-id="${cssEscape(pendingId)}"]`);
  const button = card?.querySelector?.("[data-pending-reject]");
  if (button) button.disabled = true;
  try {
    await api(`/api/self/pending/${encodeURIComponent(pendingId)}/reject`, {
      method: "POST", body: JSON.stringify({ reason: "" }), timeoutMs: 30000,
    });
    state.pending = state.pending.filter((item) => item.pending_id !== pendingId);
    toast("已驳回，卡片归档。", "info");
    await afterPendingChange();
  } catch (error) {
    toast(`驳回失败：${error.message}`, "error");
    if (button) button.disabled = false;
  }
}

function editPending(pendingId) {
  const card = state.pending.find((item) => item.pending_id === pendingId);
  if (!card) return;
  state.editingPending = pendingId;
  openDialog("改后采纳这条档案修改", `
    <p class="field-help">${escapeHtml(card.section_title || "")} · ${escapeHtml(card.title || "")}</p>
    <label class="field">修改后的正文
      <textarea id="mentor-pending-edit" rows="8" maxlength="8000">${escapeHtml(card.new_text || "")}</textarea>
    </label>
    <div class="form-actions">
      <button type="button" class="button primary" data-mentor-pending-save="${escapeHtml(pendingId)}">保存并采纳</button>
    </div>`);
}

async function savePendingEdit(pendingId) {
  const text = String(el("mentor-pending-edit")?.value || "").trim();
  if (!text) { toast("正文不能为空。", "warning"); return; }
  try {
    await api(`/api/self/pending/${encodeURIComponent(pendingId)}/accept`, {
      method: "POST", body: JSON.stringify({ confirm: true, edited_text: text }),
      timeoutMs: 60000,
    });
    state.pending = state.pending.filter((item) => item.pending_id !== pendingId);
    el("detail-dialog")?.close?.();
    toast("已改后采纳。", "success");
    await afterPendingChange();
  } catch (error) {
    toast(`采纳失败：${error.message}`, "error");
  }
}

async function afterPendingChange() {
  try { await loadOverview({ autoOpen: false }); } catch { /* keep page */ }
  renderAll();
  document.dispatchEvent(new CustomEvent("torch:self-refresh"));
  document.dispatchEvent(new CustomEvent("torch:mentor-pending-changed"));
}

async function runSynthesis(button) {
  const ok = await confirmAction({
    title: "更新自我档案？",
    message: "会读取档案、记忆、最近日记、任务和知识库，生成画像并更新有依据的档案条目；事实与推断分开标注，旧判断保留为成长历史。可能耗时 1–3 分钟。",
    confirmLabel: "开始整理",
  });
  if (!ok) return;
  button.disabled = true;
  button.textContent = "整理中…";
  try {
    const result = await api("/api/self/synthesize", {
      method: "POST", body: JSON.stringify({ confirm: true, range_days: 30 }),
      timeoutMs: 360000,
    });
    const updates = Array.isArray(result.archive_updates) ? result.archive_updates : [];
    const failed = updates.filter((item) => item && !item.ok).length;
    const pendingCount = result.patches?.length || 0;
    toast(updates.length
      ? `画像已更新；${updates.length - failed} 项档案已更新${failed ? `，${failed} 项失败` : ""}。`
      : `画像已更新；${pendingCount} 条补丁待确认。`,
    failed ? "error" : "success");
    if (pendingCount) state.activeTab = "pending";
    await loadOverview({ autoOpen: false });
    renderAll();
    document.dispatchEvent(new CustomEvent("torch:self-refresh"));
  } catch (error) {
    toast(`整理失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = "更新自我档案";
  }
}

function cssEscape(value) {
  return String(value || "").replace(/["\\]/g, "\\$&");
}

function openQuestionEditor(questionId) {
  const card = state.host?.querySelector?.(`[data-mentor-question="${cssEscape(questionId)}"]`);
  const editor = card?.querySelector?.(".mentor-question-editor");
  if (!editor) return;
  editor.hidden = false;
  editor.querySelector("textarea")?.focus?.();
}

async function handleClick(event) {
  const thread = event.target.closest?.("[data-mentor-thread]");
  if (thread) { await openThread(thread.dataset.mentorThread); return; }
  if (event.target.closest?.("[data-mentor-new]")) { newConversation(); return; }
  const synthesize = event.target.closest?.("[data-mentor-synthesize]");
  if (synthesize) { await runSynthesis(synthesize); return; }
  if (event.target.closest?.("[data-mentor-refresh]")) {
    try { await loadOverview({ autoOpen: false }); await refreshCurrentThread(); }
    catch (error) { toast(`刷新失败：${error.message}`, "error"); }
    return;
  }
  const tab = event.target.closest?.("[data-mentor-tab]");
  if (tab) { state.activeTab = tab.dataset.mentorTab || "questions"; renderSide(); return; }
  const chip = event.target.closest?.("[data-mentor-chip]");
  if (chip) { sendMessage(chip.dataset.mentorChip); return; }
  const draft = event.target.closest?.("[data-mentor-draft-questions]");
  if (draft) { await draftQuestions(draft); return; }
  const acceptQ = event.target.closest?.("[data-mentor-q-accept]");
  if (acceptQ) { await acceptQuestion(acceptQ.dataset.mentorQAccept); return; }
  const retryQ = event.target.closest?.("[data-mentor-q-retry]");
  if (retryQ) { await retryQuestionArchive(retryQ.dataset.mentorQRetry); return; }
  const editQ = event.target.closest?.("[data-mentor-q-edit]");
  if (editQ) { openQuestionEditor(editQ.dataset.mentorQEdit); return; }
  const submitQ = event.target.closest?.("[data-mentor-q-submit]");
  if (submitQ) {
    const card = submitQ.closest?.("[data-mentor-question]");
    await submitQuestion(
      submitQ.dataset.mentorQSubmit,
      card?.querySelector?.("textarea")?.value || "",
      submitQ.dataset.mentorQSuggested === "1");
    return;
  }
  const skipQ = event.target.closest?.("[data-mentor-q-skip]");
  if (skipQ) { await skipQuestion(skipQ.dataset.mentorQSkip, false); return; }
  const neverQ = event.target.closest?.("[data-mentor-q-never]");
  if (neverQ) { await skipQuestion(neverQ.dataset.mentorQNever, true); return; }
  const acceptP = event.target.closest?.("[data-pending-accept]");
  if (acceptP) { await acceptPending(acceptP.dataset.pendingAccept); return; }
  const editP = event.target.closest?.("[data-pending-edit]");
  if (editP) { editPending(editP.dataset.pendingEdit); return; }
  const rejectP = event.target.closest?.("[data-pending-reject]");
  if (rejectP) { await rejectPending(rejectP.dataset.pendingReject); return; }
  const saveP = event.target.closest?.("[data-mentor-pending-save]");
  if (saveP) { await savePendingEdit(saveP.dataset.mentorPendingSave); return; }
  const open = event.target.closest?.("[data-mentor-open]");
  if (open) { openVaultPath(open.dataset.mentorOpen); return; }
}

async function refreshCurrentThread() {
  if (state.threadId) await openThread(state.threadId, { silent: true });
  else renderAll();
}

function bindEvents() {
  state.host?.addEventListener?.("click", (event) => {
    handleClick(event).catch((error) => toast(error.message, "error"));
  });
  state.host?.addEventListener?.("submit", (event) => {
    const form = event.target.closest?.("[data-mentor-form]");
    if (!form) return;
    event.preventDefault();
    sendMessage(form.querySelector("[data-mentor-input]")?.value || "");
  });
  state.host?.addEventListener?.("keydown", (event) => {
    const input = event.target.closest?.("[data-mentor-input]");
    if (!input) return;
    if (event.key !== "Enter" || event.shiftKey) return;
    if (event.isComposing) return;
    event.preventDefault();
    sendMessage(input.value || "");
  });
  document.addEventListener("torch:mentor-prefill", (event) => {
    const detail = event.detail || {};
    const input = state.host?.querySelector?.("[data-mentor-input]");
    if (input && detail.prefill) {
      input.value = detail.prefill;
      rememberDraft();
      input.focus();
      setStatus(detail.entryTitle ? `正在和导师讨论「${detail.entryTitle}」` : "");
    }
  });
  // 改后采纳的保存按钮在全局 detail-dialog 里，不在 #self-main 内。
  document.addEventListener("click", (event) => {
    const save = event.target.closest?.("[data-mentor-pending-save]");
    if (save) {
      savePendingEdit(save.dataset.mentorPendingSave)
        .catch((error) => toast(error.message, "error"));
    }
  });
}

export function initMentor(host) {
  if (state.initialized) return;
  state.host = host;
  state.initialized = true;
  bindEvents();
}

/**
 * 离开导师页：保留会话、草稿和未完成请求，只让页面级异步回调失效。
 * 后台回复仍写入服务端历史；重新进入时从当前会话重新读取。
 */
export function leaveMentor() {
  if (!state.initialized) return;
  rememberDraft();
  state.active = false;
  beginOpen();
}

export async function loadMentor(host, sub = "chat", options = {}) {
  if (!host) return;
  if (!state.initialized || state.host !== host) {
    state.host = host;
    if (!state.initialized) { state.initialized = true; bindEvents(); }
  }
  state.active = true;
  ensureShell();
  ensureConversationIdentity();
  restoreDraft();
  if (["questions", "pending", "memory", "sources"].includes(sub)) {
    state.activeTab = sub;
  }
  try {
    await loadOverview({ autoOpen: true });
  } catch (error) {
    if (state.active) toast(`读不到导师数据：${error.message}`, "error");
  }
  if (!state.active) return;
  if (!state.threadId && !state.newConversation && !state.sessions.length) {
    ensureConversationIdentity();
  }
  restoreDraft();
  renderAll();
  syncComposer(state.legacyThread ? "这是旧忆灵对话，只读。" : "");
  if (options.prefill) {
    document.dispatchEvent(new CustomEvent("torch:mentor-prefill", {
      detail: { prefill: options.prefill, entryTitle: options.entryTitle || "" },
    }));
  }
}

export function openMentorChat(options = {}) {
  document.dispatchEvent(new CustomEvent("torch:mentor-prefill", { detail: options }));
}
