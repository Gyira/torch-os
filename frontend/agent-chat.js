// 「与神对话」：工作台内的 agent 聊天页。
// 后端把 dsh 的 ACP 控制面与实时 assistant-stream 合并成 SSE；
// 这个模块只负责渲染：思考面板、正文流、工具卡片、会话与停止。
import { $, api, escapeHtml, setStatus, toast } from "./core.js";
import { renderMarkdown } from "./pillar-markdown.js";

const STORAGE_KEY = "torch-os:agent-conversation";
const STATUS_LABELS = {
  in_progress: "运行中…",
  pending: "等待中…",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const agent = {
  initialized: false,
  loaded: false,
  conversationId: null,
  busy: false,
  controller: null,
  current: null,
  sessions: [],
  lastStatus: null,
};

function storageGet() {
  try { return window.localStorage.getItem(STORAGE_KEY); } catch { return null; }
}
function storageSet(value) {
  try {
    if (value) window.localStorage.setItem(STORAGE_KEY, String(value));
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch { /* localStorage may be unavailable in the Obsidian iframe */ }
}

function messagesRoot() {
  return $("#agent-messages");
}

function emptyState() {
  return $("#agent-empty");
}

function showEmpty(show) {
  const empty = emptyState();
  if (empty) empty.hidden = !show;
}

function scrollToBottom(force = false) {
  const root = messagesRoot();
  if (!root) return;
  const nearBottom = root.scrollHeight - root.scrollTop - root.clientHeight < 90;
  if (force || nearBottom) root.scrollTop = root.scrollHeight;
}

function nowSeconds(start) {
  return Math.max(1, Math.round((performance.now() - start) / 1000));
}

function appendUser(text) {
  const root = messagesRoot();
  if (!root) return;
  const node = document.createElement("div");
  node.className = "agent-msg user";
  node.textContent = text;
  root.appendChild(node);
  showEmpty(false);
  scrollToBottom(true);
}

function makeAssistantShell() {
  const root = messagesRoot();
  if (!root) return null;
  const wrap = document.createElement("div");
  wrap.className = "agent-msg assistant";

  const thinking = document.createElement("details");
  thinking.className = "agent-thinking";
  thinking.open = true;
  thinking.hidden = true;
  const summary = document.createElement("summary");
  summary.textContent = "思考中…";
  const thinkingBody = document.createElement("div");
  thinkingBody.className = "agent-thinking-body";
  thinking.append(summary, thinkingBody);

  const answer = document.createElement("div");
  answer.className = "agent-answer";
  const answerText = document.createElement("span");
  answerText.className = "agent-answer-text";
  const caret = document.createElement("span");
  caret.className = "agent-caret";
  caret.textContent = "▍";
  answer.append(answerText, caret);

  const tools = document.createElement("div");
  tools.className = "agent-tools";

  const meta = document.createElement("div");
  meta.className = "agent-meta field-help";

  wrap.append(thinking, tools, answer, meta);
  root.appendChild(wrap);
  showEmpty(false);
  scrollToBottom(true);

  return {
    wrap, thinking, summary, thinkingBody,
    answer, answerText, caret, tools, meta,
    text: "", thought: "", started: performance.now(), done: false,
    toolCards: new Map(),
  };
}

function toolCard(shell, id, fallbackName) {
  if (!shell || !id) return null;
  let card = shell.toolCards.get(id);
  if (card) return card;
  const details = document.createElement("details");
  details.className = "agent-tool";
  const summary = document.createElement("summary");
  const name = document.createElement("span");
  name.className = "agent-tool-name";
  name.textContent = fallbackName || "工具调用";
  const status = document.createElement("span");
  status.className = "agent-tool-status";
  status.textContent = "准备中…";
  summary.append(name, status);
  const input = document.createElement("pre");
  input.className = "agent-tool-input";
  input.hidden = true;
  const output = document.createElement("pre");
  output.className = "agent-tool-output";
  output.hidden = true;
  details.append(summary, input, output);
  shell.tools.appendChild(details);
  card = { details, name, status, input, output, arguments: "" };
  shell.toolCards.set(id, card);
  scrollToBottom();
  return card;
}

function toolText(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (item && item.type === "text") return item.text || "";
      if (item && item.content) return toolText(item.content);
      return typeof item === "string" ? item : JSON.stringify(item);
    }).filter(Boolean).join("\n");
  }
  if (typeof value === "object" && value.text) return String(value.text);
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function updateTool(shell, event, kind) {
  const id = String(event.id || event.toolCallId || event.tool_call_id || "");
  if (!id) return;
  const card = toolCard(shell, id, event.title || event.name || "工具调用");
  if (!card) return;
  if (kind === "delta") {
    if (event.name) card.name.textContent = event.name;
    if (event.arguments) {
      card.arguments += event.arguments;
      card.input.hidden = false;
      card.input.textContent = card.arguments;
    }
    card.status.textContent = "运行中…";
  } else if (kind === "call") {
    if (event.title) card.name.textContent = event.title;
    if (event.name) card.name.textContent = event.name;
    const status = String(event.status || "in_progress");
    card.status.textContent = STATUS_LABELS[status] || status;
    const input = event.input ?? event.arguments ?? event.rawInput;
    if (input != null) {
      card.input.hidden = false;
      card.input.textContent = toolText(input);
    }
    if (status === "failed" || status === "completed" || status === "cancelled") {
      card.details.open = false;
    } else {
      card.details.open = true;
    }
  } else if (kind === "result") {
    const status = String(event.status || "completed");
    card.status.textContent = STATUS_LABELS[status] || status;
    const output = event.output ?? event.content ?? event.result;
    if (output != null) {
      card.output.hidden = false;
      card.output.textContent = toolText(output);
    }
    card.details.open = false;
  }
  scrollToBottom();
}

function finishThinking(shell) {
  if (!shell || shell.thinking.hidden) return;
  shell.summary.textContent = `已思考 ${nowSeconds(shell.started)} 秒`;
  shell.thinking.open = false;
}

function renderStreamingAnswer(shell) {
  if (!shell) return;
  shell.answerText.textContent = shell.text;
  if (!shell.text) shell.answer.classList.add("streaming");
  else shell.answer.classList.remove("streaming");
  scrollToBottom();
}

function renderFinalAnswer(shell, fallback = "") {
  if (!shell) return;
  const candidate = String(fallback || "").trim();
  // The live stream can trail the committed ACP result by a few frames.
  // Prefer the authoritative answer, but keep a complete live text when the
  // backend only returned its "no text" placeholder.
  if (candidate && !candidate.startsWith("（Harness 没有返回文本内容")) {
    shell.text = candidate;
  }
  shell.done = true;
  shell.answer.classList.remove("streaming");
  shell.caret.remove();
  shell.answer.innerHTML = renderMarkdown(shell.text);
  finishThinking(shell);
  scrollToBottom();
}

function currentStatus(text, tone = "neutral") {
  setStatus("#agent-status", text, tone);
}

function setBusy(busy) {
  agent.busy = busy;
  const send = $("#agent-send");
  const stop = $("#agent-stop");
  if (send) send.disabled = busy;
  if (stop) stop.hidden = !busy;
  const input = $("#agent-input");
  if (input) input.setAttribute("aria-busy", busy ? "true" : "false");
}

function handleEvent(shell, payload) {
  if (!payload || typeof payload !== "object") return;
  switch (payload.type) {
    case "start":
      if (payload.conversation_id && !agent.conversationId) {
        agent.conversationId = payload.conversation_id;
        storageSet(agent.conversationId);
      }
      break;
    case "session":
      if (payload.session_id && shell) shell.meta.dataset.session = String(payload.session_id);
      break;
    case "stream_start":
      currentStatus("思考中…");
      break;
    case "block_start":
      if (payload.blockType === "reasoning" && shell) {
        shell.thinking.hidden = false;
        shell.thinking.open = true;
        shell.summary.textContent = "思考中…";
        currentStatus("思考中…");
      }
      break;
    case "block_end":
      if (payload.blockType === "reasoning") finishThinking(shell);
      break;
    case "thought":
      if (!shell) break;
      if (payload.text) {
        shell.thought += payload.text;
        shell.thinking.hidden = false;
        shell.thinking.open = true;
        shell.thinkingBody.textContent = shell.thought;
        shell.summary.textContent = "思考中…";
        currentStatus("思考中…");
        scrollToBottom();
      }
      break;
    case "text":
      if (!shell || shell.done) break;
      if (payload.text) {
        if (!shell.text) {
          finishThinking(shell);
          currentStatus("生成中…");
        }
        shell.text += payload.text;
        renderStreamingAnswer(shell);
      }
      break;
    case "tool_delta":
      updateTool(shell, payload, "delta");
      currentStatus("调用工具…");
      break;
    case "tool_call":
      updateTool(shell, payload, "call");
      currentStatus("调用工具…");
      break;
    case "tool_result":
      updateTool(shell, payload, "result");
      currentStatus("生成中…");
      break;
    case "done":
      if (payload.conversation_id) {
        agent.conversationId = payload.conversation_id;
        storageSet(agent.conversationId);
      }
      renderFinalAnswer(shell, payload.answer || "");
      if (shell && payload.warning) shell.meta.textContent = payload.warning;
      currentStatus("已连接", "success");
      break;
    case "error":
      if (shell) shell.meta.textContent = payload.message || "对话失败";
      currentStatus("对话失败", "error");
      break;
    case "finish":
      break;
    default:
      break;
  }
}

async function streamChat(message, shell) {
  const controller = new AbortController();
  agent.controller = controller;
  setBusy(true);
  currentStatus("思考中…");
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        conversation_id: agent.conversationId,
        use_harness: true,
      }),
      signal: controller.signal,
      credentials: "same-origin",
    });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      let detail = text;
      try {
        const parsed = JSON.parse(text);
        detail = parsed.detail || text;
        if (Array.isArray(detail)) detail = detail.map((item) => item.msg).filter(Boolean).join("；");
      } catch { /* plain text */ }
      throw new Error(detail || `请求失败（${response.status}）`);
    }
    if (!response.body) throw new Error("本地服务没有返回流。");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const lines = frame.split("\n").filter((line) => line.startsWith("data: "));
        if (!lines.length) continue;
        try {
          handleEvent(shell, JSON.parse(lines.map((line) => line.slice(6)).join("\n")));
        } catch { /* ignore malformed frames */ }
      }
    }
    if (shell && !shell.text) {
      shell.meta.textContent = "这轮没有返回文本；可以换一种说法再试。";
    }
  } catch (error) {
    if (error && error.name === "AbortError") {
      if (shell) shell.meta.textContent = "已停止。";
      currentStatus("已停止");
      renderFinalAnswer(shell);
    } else {
      if (shell) shell.meta.textContent = error.message || "对话失败";
      currentStatus("对话失败", "error");
      renderFinalAnswer(shell);
    }
  } finally {
    agent.controller = null;
    setBusy(false);
    void refreshSessions();
    void loadStatus();
  }
}

function setConversation(id, { load = false } = {}) {
  agent.conversationId = id ? Number(id) : null;
  storageSet(agent.conversationId);
  if (load && agent.conversationId) loadConversation(agent.conversationId);
}

function clearMessages() {
  const root = messagesRoot();
  if (!root) return;
  root.querySelectorAll(".agent-msg").forEach((node) => node.remove());
  showEmpty(true);
}

async function loadConversation(id, { quiet = false } = {}) {
  try {
    const data = await api(`/api/conversations/${id}`);
    if (!data || !data.messages) return false;
    clearMessages();
    for (const message of data.messages) {
      if (message.role === "user") {
        appendUser(message.content || "");
      } else if (message.role === "assistant") {
        const shell = makeAssistantShell();
        if (!shell) continue;
        shell.thinking.hidden = true;
        shell.done = true;
        shell.caret.remove();
        shell.text = message.content || "";
        shell.answer.innerHTML = renderMarkdown(shell.text);
      }
    }
    agent.conversationId = Number(id);
    storageSet(agent.conversationId);
    scrollToBottom(true);
    return true;
  } catch (error) {
    if (!quiet) toast(error.message || "读取会话失败", "error");
    return false;
  }
}

async function restoreConversation() {
  const stored = storageGet();
  const storedId = stored ? Number(stored) : null;
  if (Number.isInteger(storedId) && storedId > 0) {
    agent.conversationId = storedId;
    if (await loadConversation(storedId, { quiet: true })) return;
    agent.conversationId = null;
    storageSet(null);
  }
  // No usable local pointer (new browser, cleared storage, moved vault):
  // continue the most recently active conversation from the backend.
  await refreshSessions();
  const latest = agent.sessions[0];
  if (latest && Number.isInteger(Number(latest.id))) {
    await loadConversation(latest.id, { quiet: true });
  }
}

async function refreshSessions() {
  try {
    const sessions = await api("/api/conversations");
    agent.sessions = Array.isArray(sessions) ? sessions : [];
    renderSessions();
  } catch { /* status is enough; keep the chat usable */ }
}

function renderSessions() {
  const list = $("#agent-history-list");
  if (!list) return;
  list.innerHTML = "";
  if (!agent.sessions.length) {
    const empty = document.createElement("span");
    empty.className = "field-help";
    empty.textContent = "还没有会话。";
    list.appendChild(empty);
    return;
  }
  for (const session of agent.sessions.slice(0, 30)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "agent-history-item";
    if (Number(session.id) === Number(agent.conversationId)) button.classList.add("is-active");
    button.textContent = session.title || `会话 ${session.id}`;
    button.addEventListener("click", async () => {
      list.hidden = true;
      const toggle = $("#agent-history-toggle");
      if (toggle) toggle.setAttribute("aria-expanded", "false");
      await loadConversation(session.id);
    });
    list.appendChild(button);
  }
}

async function loadStatus() {
  try {
    const status = await api("/api/harness/status");
    agent.lastStatus = status;
    if (status && status.available) {
      const model = status.model || "DeepSeek V4 Flash";
      const profile = status.profile ? ` · ${status.profile}` : "";
      currentStatus(`${status.running ? "已连接" : "就绪"} · ${model}${profile}`, "success");
      const hint = $("#agent-hint");
      if (hint) hint.textContent = status.live && status.live.ok
        ? "实时思考与逐字输出已连接；当前只读，不会改动你的数据。"
        : "dsh 已就绪；首次对话会启动实时流；当前只读。";
    } else {
      currentStatus("dsh 未连接", "error");
    }
  } catch {
    currentStatus("dsh 未连接", "error");
  }
}

export function initAgentChat() {
  if (agent.initialized) return;
  const form = $("#agent-form");
  const input = $("#agent-input");
  if (!form || !input) return;
  agent.initialized = true;

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = String(input.value || "").trim();
    if (!text || agent.busy) return;
    input.value = "";
    input.style.height = "";
    appendUser(text);
    const shell = makeAssistantShell();
    agent.current = shell;
    void streamChat(text, shell);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(180, input.scrollHeight)}px`;
  });

  $("#agent-stop")?.addEventListener("click", () => {
    if (!agent.busy) return;
    if (agent.controller) agent.controller.abort();
    if (agent.conversationId) {
      void api("/api/agent/cancel", {
        method: "POST",
        body: JSON.stringify({ conversation_id: agent.conversationId }),
      }).catch(() => {});
    }
  });

  $("#agent-new")?.addEventListener("click", () => {
    if (agent.busy && agent.controller) agent.controller.abort();
    agent.conversationId = null;
    storageSet(null);
    clearMessages();
    currentStatus(agent.lastStatus?.available ? "已连接" : "dsh 未连接", agent.lastStatus?.available ? "success" : "error");
    input.focus();
  });

  $("#agent-history-toggle")?.addEventListener("click", async (event) => {
    const list = $("#agent-history-list");
    if (!list) return;
    const open = list.hidden;
    list.hidden = !open;
    event.currentTarget.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) await refreshSessions();
  });

  document.querySelectorAll("[data-agent-suggestion]").forEach((button) => {
    button.addEventListener("click", () => {
      const text = button.dataset.agentSuggestion || "";
      input.value = text;
      input.focus();
    });
  });
}

export async function loadAgentChat() {
  initAgentChat();
  if (!agent.loaded) {
    agent.loaded = true;
    await restoreConversation();
  } else {
    await refreshSessions();
  }
  await loadStatus();
}
