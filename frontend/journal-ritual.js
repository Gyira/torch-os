// 今晚复盘：盘点 → 三个问题 → 草稿 → 保存进当天日记。
// 数据全部来自 /api/journal/*；写入口只碰当天那篇，见 docs/日记闭环设计-2026-10-28.md。
import { api, changed, jsonBody, openDialog, toast } from "./core.js";
import { chatMarkup, composeBody, distillMarkup, previewMarkup, reviewMarkup } from "./journal-ritual-model.js";
import { createObsidianNote } from "./task-open.js";

const state = {
  payload: null, day: "", sha: "", content: "", form: {}, pairs: [], distill: [],
  chatAvailable: false, chat: { sessionId: "", messages: [] }, busy: false,
};
let unbind = null;

export async function openJournalReview({ day = "" } = {}) {
  try {
    const suffix = day ? `?day=${encodeURIComponent(day)}` : "";
    const [payload, chat] = await Promise.all([
      api(`/api/journal/review${suffix}`),
      api("/api/journal/chat/status").catch(() => ({ available: false })),
    ]);
    state.payload = payload;
    state.day = payload.digest.day;
    state.sha = payload.note.sha256 || "";
    state.content = "";
    state.form = { answers: [], freeText: "", nextStep: "", mood: "", sealed: false };
    state.pairs = [];
    state.chatAvailable = Boolean(chat?.available);
    state.chat = { sessionId: "", messages: [] };
    state.busy = false;
    renderReview();
    return payload;
  } catch (error) {
    toast(`打不开今晚复盘：${error.message}`, "error");
    return null;
  }
}

function setStatus(message, tone = "") {
  const node = document.querySelector("#jr-status");
  if (!node) return;
  node.textContent = message;
  node.className = `status-message${tone ? ` ${tone}` : ""}`;
}

function questions() {
  return state.payload?.questions || [];
}

function renderReview() {
  bind(openDialog("今晚复盘", reviewMarkup(state.payload, { ...state.form, chatAvailable: state.chatAvailable })));
}

function renderChat() {
  bind(openDialog("今晚复盘 · 聊着写", chatMarkup(state.chat.messages, { draftable: true })));
}

async function chatTurn(mode, message = "") {
  const payload = await api("/api/journal/chat", jsonBody("POST", {
    day: state.day,
    message,
    session_id: state.chat.sessionId,
    mode,
  }));
  if (payload?.session_id) state.chat.sessionId = payload.session_id;
  return payload?.reply || "";
}

async function openChat(button) {
  if (state.chat.messages.length) {
    renderChat();
    return;
  }
  button.disabled = true;
  try {
    const reply = await chatTurn("start");
    state.chat.messages = [{ role: "ai", text: reply }];
    renderChat();
  } catch (error) {
    setStatus(error.message, "error");
    button.disabled = false;
    toast("聊天通道不可用，先用问题卡。", "warning");
  }
}

async function sendChat(button) {
  const text = document.querySelector("#jr-chat-input")?.value.trim() || "";
  if (!text) {
    setStatus("说点什么再发。", "warning");
    return;
  }
  state.chat.messages.push({ role: "user", text });
  button.disabled = true;
  setStatus("等它回一句…");
  try {
    const reply = await chatTurn("turn", text);
    state.chat.messages.push({ role: "ai", text: reply });
    renderChat();
  } catch (error) {
    setStatus(error.message, "error");
    button.disabled = false;
  }
}

async function draftChat(button) {
  button.disabled = true;
  setStatus("正在整理…");
  try {
    const reply = await chatTurn("draft");
    state.form = { ...state.form, freeText: reply };
    renderReview();
    setStatus("整理好了，看看要不要改。", "success");
  } catch (error) {
    setStatus(error.message, "error");
    button.disabled = false;
  }
}

function renderPreview() {
  bind(openDialog("今晚复盘 · 预览", previewMarkup(state.content, state.day)));
}

function collect() {
  const fields = Array.from(document.querySelectorAll("[data-jr-answer]"));
  state.pairs = questions().map((item, index) => ({
    prompt: item.prompt,
    answer: fields[index]?.value || "",
  }));
  state.form = {
    answers: state.pairs.map((pair) => pair.answer),
    freeText: document.querySelector("#jr-free")?.value || "",
    nextStep: document.querySelector("#jr-next")?.value || "",
    mood: document.querySelector("#jr-mood")?.value || "",
    sealed: Boolean(document.querySelector("#jr-sealed")?.checked),
  };
}

async function composeNow(button) {
  collect();
  button.disabled = true;
  setStatus("正在生成…");
  try {
    const body = composeBody({ ...state.form, answers: state.pairs }, state.day);
    const result = await api("/api/journal/compose", jsonBody("POST", body));
    state.content = result.content || "";
    renderPreview();
  } catch (error) {
    setStatus(error.message, "error");
    button.disabled = false;
  }
}

async function writeNow(button) {
  button.disabled = true;
  setStatus("正在写入…");
  try {
    await api("/api/journal/write", jsonBody("POST", {
      day: state.day,
      content: state.content,
      expected_sha: state.sha,
      confirm: true,
    }));
    changed();
    const distill = await api(`/api/journal/distill?day=${encodeURIComponent(state.day)}`)
      .then((payload) => payload?.candidates || [])
      .catch(() => []);
    state.distill = distill;
    const markup = distillMarkup(distill);
    if (markup) {
      bind(openDialog("存一条反思", markup));
      toast("写进今天的日记了。顺手把这段存下来？", "success");
    } else {
      document.querySelector("#detail-dialog")?.close();
      toast("写进今天的日记了。", "success");
    }
  } catch (error) {
    setStatus(`${error.message}（如果这篇在别处改过，关掉重开一次）`, "error");
    button.disabled = false;
  }
}

function vaultName() {
  return state.payload?.note?.vault_name || "";
}

function saveDistill(button) {
  const index = Number(button.dataset.jrIndex || 0);
  const candidate = state.distill[index];
  if (!candidate) return;
  if (!createObsidianNote(candidate.path, vaultName(), candidate.markdown)) {
    toast("没有可用的 Obsidian 库名，请在 Obsidian 里新建这条笔记。", "warning");
    return;
  }
  document.querySelector("#detail-dialog")?.close();
  toast("已放进回顾章节，等你在 Obsidian 里点开保存。", "success");
}

function bind(alive) {
  if (unbind) {
    unbind();
    unbind = null;
  }
  const content = document.querySelector("#dialog-content");
  const dialog = document.querySelector("#detail-dialog");
  if (!content || !dialog) return;

  const onClick = async (event) => {
    const button = event.target.closest?.("[data-jr-action]");
    if (!button || !alive()) return;
    const action = button.dataset.jrAction;
    if (action === "compose") await composeNow(button);
    else if (action === "back") renderReview();
    else if (action === "write") await writeNow(button);
    else if (action === "distill") saveDistill(button);
    else if (action === "done") document.querySelector("#detail-dialog")?.close();
    else if (action === "chat-open") await openChat(button);
    else if (action === "chat-send") await sendChat(button);
    else if (action === "chat-draft") await draftChat(button);
    else if (action === "chat-back") renderReview();
  };
  const onClose = () => {
    if (unbind) {
      unbind();
      unbind = null;
    }
  };

  content.addEventListener("click", onClick);
  dialog.addEventListener("close", onClose);
  unbind = () => {
    content.removeEventListener("click", onClick);
    dialog.removeEventListener("close", onClose);
  };
}
