// 人生导师页的纯渲染函数（2026-11-01）。
// 不 import core.js / DOM；node --test 可直接跑。
import { escape, pendingCardMarkup } from "./self-archive-model.js";

export const SIDE_TABS = [
  { key: "questions", label: "今日提问" },
  { key: "pending", label: "待确认" },
  { key: "memory", label: "记忆" },
  { key: "sources", label: "资料" },
];

export function formatWhen(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text.slice(0, 16);
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
    }).format(date);
  } catch {
    return text.slice(0, 16);
  }
}

export function sessionListMarkup({ sessions = [], legacy = [], currentId = "" } = {}) {
  const rows = Array.isArray(sessions) ? sessions : [];
  const old = Array.isArray(legacy) ? legacy : [];
  return `${rows.length ? rows.map((item) => `<button type="button" class="mentor-session${item.id === currentId ? " is-active" : ""}" data-mentor-thread="${escape(item.id)}">
      <span class="mentor-session-title">${escape(item.title || "未命名对话")}</span>
      <span class="mentor-session-meta">${escape(formatWhen(item.updated_at))}</span>
    </button>`).join("") : `<p class="field-help">还没有对话。直接在右边说点什么。</p>`}
    ${old.length ? `<p class="mentor-session-group-label">旧忆灵对话</p>
      ${old.map((item) => `<button type="button" class="mentor-session is-legacy${item.id === currentId ? " is-active" : ""}" data-mentor-thread="${escape(item.id)}" data-mentor-legacy="1">
        <span class="mentor-session-title">${escape(item.title || "旧忆灵对话")}</span>
        <span class="mentor-session-meta">只读</span>
      </button>`).join("")}` : ""}`;
}

export function archiveUpdatesMarkup(updates = []) {
  const rows = Array.isArray(updates) ? updates : [];
  if (!rows.length) return "";
  return `<section class="mentor-archive-updates" aria-label="本轮档案更新">
    <p class="eyebrow">ARCHIVE UPDATES · 本轮档案更新</p>
    ${rows.map((item) => {
      const ok = item?.ok !== false;
      const sources = Array.isArray(item?.sources) ? item.sources : [];
      const action = item?.action === "supersede" ? "修正" : "新增";
      const type = item?.entry_type_label || item?.entry_type || "推断";
      return `<article class="mentor-archive-update${ok ? "" : " is-error"}" data-archive-operation="${escape(item?.operation_id || "")}">
        <header>
          <span>${escape(action)} · ${escape(type)}${item?.section_title ? ` · ${escape(item.section_title)}` : ""}</span>
          <span>${ok ? "已更新" : "未写入"}</span>
        </header>
        <strong>${escape(item?.title || "未命名条目")}</strong>
        ${ok && item?.text ? `<p class="mentor-archive-text">${escape(item.text)}</p>` : ""}
        ${ok && item?.evidence ? `<p class="field-help"><strong>依据：</strong>${escape(item.evidence)}</p>` : ""}
        ${ok && sources.length ? `<p class="field-help">来源：${sources.map(escape).join("、")}</p>` : ""}
        ${item?.error ? `<p class="mentor-archive-error" role="status">${escape(item.error)}</p>` : ""}
        <footer>
          ${ok && item?.path ? `<button type="button" class="text-button" data-mentor-open="${escape(item.path)}">打开当前条目</button>` : ""}
          ${ok && item?.previous_path ? `<button type="button" class="text-button" data-mentor-open="${escape(item.previous_path)}">查看旧版</button>` : ""}
          ${item?.operation_id ? `<span class="meta">操作 ${escape(item.operation_id)}</span>` : ""}
        </footer>
      </article>`;
    }).join("")}
  </section>`;
}

export function messageMarkup(message = {}) {
  const role = message.role === "user" ? "user" : "assistant";
  const sources = Array.isArray(message.sources) ? message.sources : [];
  const updates = Array.isArray(message.archive_updates) ? message.archive_updates : [];
  const attachments = Array.isArray(message.attachments) ? message.attachments : [];
  const time = formatWhen(message.created_at);
  const body = role === "assistant"
    ? `<div class="md-body mentor-message-body">${message.html || escape(message.content || "").replace(/\n/g, "<br>")}</div>`
    : `<div class="mentor-message-body">${escape(message.content || "").replace(/\n/g, "<br>")}</div>`;
  return `<article class="mentor-message is-${role}" data-message-role="${role}">
    <div class="mentor-message-head"><span>${role === "user" ? "我" : "人生导师"}</span><span>${escape(time)}</span></div>
    ${attachments.length ? `<div class="mentor-message-attachments">${attachments.map((item) => `<span class="mentor-message-attachment" title="${escape(item.name || "附件")}">📄 ${escape(item.name || "附件")}${item.chars ? ` · ${Number(item.chars) || 0} 字` : ""}</span>`).join("")}</div>` : ""}
    ${body}
    ${archiveUpdatesMarkup(updates)}
    ${sources.length ? `<details class="mentor-sources"><summary>用了 ${sources.length} 份资料</summary>
      <ul>${sources.map((item) => `<li>${escape(item.title || item.kind || item.id || "")}${item.path ? ` · ${escape(item.path)}` : ""}</li>`).join("")}</ul>
    </details>` : ""}
  </article>`;
}

export function emptyChatMarkup() {
  return `<div class="mentor-empty">
    <p class="eyebrow">LIFE MENTOR</p>
    <h3>我在这里，先听你说。</h3>
    <p class="field-help">我会读你的档案、记忆、最近日记和任务；需要更多信息时会问你。你也可以用输入框左下角的回形针，附加文本、PDF 或 Word。你觉得档案哪条不对，也可以直接跟我辩。</p>
    <div class="mentor-chips">
      <button type="button" class="mentor-chip" data-mentor-chip="我最近在纠结一个选择，帮我梳理一下">我最近在纠结一个选择</button>
      <button type="button" class="mentor-chip" data-mentor-chip="帮我看看自我档案里最近写的东西对不对">帮我看看档案</button>
      <button type="button" class="mentor-chip" data-mentor-chip="我们聊聊最近的状态和反复出现的模式">聊聊最近的状态</button>
    </div>
  </div>`;
}

export function questionCardMarkup(item = {}, index = 0) {
  const status = String(item.status || "pending");
  const suggested = String(item.suggested || "").trim();
  const sources = Array.isArray(item.suggested_sources) ? item.suggested_sources : [];
  const answered = status === "answered";
  const skipped = status === "skipped" || status === "declined";
  const archiveStatus = String(item.archive_status || "");
  const archiveFailed = answered && (archiveStatus === "failed" || archiveStatus === "pending");
  const archiveNote = !answered ? ""
    : archiveStatus === "done" ? `<p class="mentor-answer-archive is-ok">已整理进档案${item.archive_entry_id ? ` · ${escape(item.archive_entry_id)}` : ""}</p>`
      : archiveFailed ? `<p class="mentor-answer-archive is-error">回答已保存，但档案整理${archiveStatus === "pending" ? "尚未完成" : "失败"}${item.archive_error ? `：${escape(item.archive_error)}` : ""}</p>`
        : "";
  return `<article class="mentor-question${answered ? " is-answered" : ""}" data-mentor-question="${escape(item.id || "")}" data-status="${escape(status)}">
    <header>
      <span class="eyebrow">${escape(item.section_title || "每日三问")} · ${index + 1}</span>
      <span class="meta">${escape(status === "pending" ? "待回答" : answered ? "已回答" : status === "declined" ? "不再问" : "已跳过")}</span>
    </header>
    <h4>${escape(item.prompt || "")}</h4>
    ${item.hint ? `<p class="field-help">${escape(item.hint)}</p>` : ""}
    ${suggested ? `<div class="mentor-suggested"><p class="eyebrow">导师拟答</p><p>${escape(suggested)}</p>
      ${sources.length ? `<p class="field-help">来源：${sources.map(escape).join("、")}</p>` : ""}</div>` : ""}
    ${answered ? `<p class="mentor-answer"><strong>我的回答：</strong>${escape(item.answer || "")}</p>${archiveNote}` : ""}
    ${archiveFailed ? `<div class="mentor-question-actions">
      <button type="button" class="button secondary" data-mentor-q-retry="${escape(item.id || "")}">重试整理档案</button>
    </div>` : ""}
    ${skipped ? "" : answered ? "" : `<div class="mentor-question-actions">
      ${suggested ? `<button type="button" class="button primary" data-mentor-q-accept="${escape(item.id || "")}">确认写入档案</button>` : ""}
      <button type="button" class="button secondary" data-mentor-q-edit="${escape(item.id || "")}">${suggested ? "改后回答" : "自己回答"}</button>
      <button type="button" class="text-button" data-mentor-q-skip="${escape(item.id || "")}">跳过</button>
      <button type="button" class="text-button" data-mentor-q-never="${escape(item.id || "")}">不再问</button>
    </div>
    <div class="mentor-question-editor" hidden>
      <textarea rows="3" maxlength="4000" placeholder="写下你的回答…">${escape(suggested)}</textarea>
      <div class="form-actions"><button type="button" class="button primary" data-mentor-q-submit="${escape(item.id || "")}"${suggested ? ' data-mentor-q-suggested="1"' : ""}>${suggested ? "改后写入档案" : "提交回答"}</button></div>
    </div>`}
  </article>`;
}

export function memoryMarkup(memory = {}, qa = {}) {
  const core = String(memory?.core?.text || "").trim();
  const timeline = Array.isArray(memory?.timeline_recent) ? memory.timeline_recent : [];
  const paths = memory?.paths || {};
  const qaText = String(qa?.text || "").trim();
  return `<section class="mentor-memory">
    <header class="mentor-side-section-head"><div><p class="eyebrow">CORE MEMORY</p><h3>核心记忆</h3></div>
      <span class="meta">${escape(memory?.core?.updated_at || "")}</span></header>
    ${core ? `<div class="md-body">${escape(core).replace(/\n/g, "<br>")}</div>` : `<p class="field-help">还没有核心记忆。全量整理后由导师写入。</p>`}
    <header class="mentor-side-section-head"><p class="eyebrow">TIMELINE</p><h3>记忆时间线</h3></header>
    ${timeline.length ? `<ul class="mentor-timeline">${timeline.slice().reverse().map((item) => `<li><time>${escape(item.date || "")}</time><span>${escape(item.text || "")}</span></li>`).join("")}</ul>` : `<p class="field-help">还没有记忆条目。</p>`}
    <header class="mentor-side-section-head"><p class="eyebrow">Q&amp;A</p><h3>问答记忆</h3></header>
    ${qaText ? `<details><summary>最近问答（${qaText.length} 字）</summary><pre class="mentor-memory-qa">${escape(qaText)}</pre></details>` : `<p class="field-help">还没有问答记录。</p>`}
    <div class="mentor-memory-paths">
      <button type="button" class="text-button" data-mentor-open="${escape(paths.core || "")}">在 Obsidian 打开核心记忆</button>
      <button type="button" class="text-button" data-mentor-open="${escape(paths.timeline || "")}">打开时间线</button>
      <button type="button" class="text-button" data-mentor-open="${escape(paths.qa || "")}">打开问答记忆</button>
    </div>
  </section>`;
}

export function pendingMarkup(cards = []) {
  const rows = Array.isArray(cards) ? cards : [];
  return rows.length
    ? rows.map((card) => pendingCardMarkup(card)).join("")
    : `<div class="mentor-side-empty"><p>没有待确认的档案修改。</p>
        <p class="field-help">新的档案更新会在对话里显示事实/推断、依据与历史；这里只保留旧模式生成的待确认卡。</p></div>`;
}

export function questionsMarkup(questions = {}, draftBusy = false, notice = {}) {
  const items = Array.isArray(questions?.items) ? questions.items : [];
  const pending = items.filter((item) => item.status === "pending").length;
  const message = String(notice?.message || "");
  return `<section class="mentor-questions">
    <header class="mentor-side-section-head">
      <div><p class="eyebrow">DAILY QUESTIONS</p><h3>今日三问</h3></div>
      <span class="meta">${pending} 条待回答</span>
    </header>
    <p class="field-help">每天 3 条；愿意答就答，不愿意可以跳过或不再问。导师会先从三库找答案，找得到就给你拟答。</p>
    <p class="status-message mentor-question-status ${escape(notice?.tone || "")}" data-mentor-question-status role="status" aria-live="polite"${message ? "" : " hidden"}>${escape(message)}</p>
    <div class="form-actions"><button type="button" class="button secondary" data-mentor-draft-questions ${draftBusy ? "disabled" : ""}>${draftBusy ? "拟答中…" : "生成导师拟答"}</button></div>
    ${items.map((item, index) => questionCardMarkup(item, index)).join("")}
  </section>`;
}

export function sourcesMarkup(sources = []) {
  const rows = Array.isArray(sources) ? sources : [];
  return rows.length
    ? `<section class="mentor-sources-panel">${rows.map((item) => `<article class="mentor-source-item">
        <p class="eyebrow">${escape(item.kind || item.id || "资料")}</p>
        <strong>${escape(item.title || "")}</strong>
        ${item.path ? `<p class="field-help">${escape(item.path)}</p>` : ""}
      </article>`).join("")}</section>`
    : `<div class="mentor-side-empty"><p>这次对话还没有可显示的资料。</p></div>`;
}

export function sideTabsMarkup(active = "questions") {
  return SIDE_TABS.map((tab) => `<button type="button" class="mentor-side-tab${tab.key === active ? " is-active" : ""}" data-mentor-tab="${tab.key}" aria-current="${tab.key === active ? "true" : "false"}">${escape(tab.label)}</button>`).join("");
}
