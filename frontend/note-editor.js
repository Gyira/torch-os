// 应用内 Markdown 编辑器（2026-10-29，v1.0 P2）。
// 只开放人生路/知识库的允许章节；保存走 /api/writing/notes，revision 冲突 409 不覆盖。
// 工具栏只插入 Markdown 语法，预览复用 pillar-markdown；AI 润色先预览、用户点应用才改正文。
import { $, state as appState, api, confirmAction, escapeHtml, toast } from "./core.js";
import { renderMarkdownDocument } from "./pillar-markdown.js";
import {
  TOOLBAR_ACTIONS, applyEditorAction, ensureJournalFrontMatter, isDirty,
  joinFrontMatter, normalizeBody, splitFrontMatter, tagsFromInput, tagsToInput, wordCount,
} from "./editor-model.js";
import {
  isCustomNotebookKey, journalHeading, notebookForChapter, notebooksFor,
} from "./notebook-model.js";
import { shanghaiDay } from "./journal-model.js";
import { openRoleDrawer } from "./roles.js";
import { openObsidianNote } from "./task-open.js";

const state = {
  open: false,
  mode: "new",
  path: "",
  revision: "",
  chapter: "",
  context: "life",
  title: "",
  body: "",
  savedBody: "",
  tags: [],
  journal: false,
  journalDay: "",
  frontmatter: "",
  preview: false,
  focus: false,
  busy: false,
};
let chaptersCache = null;
let initialized = false;
let polish = null;

function el(id) { return document.getElementById(id); }

function renderToolbar() {
  const host = el("note-editor-toolbar");
  if (!host) return;
  let lastGroup = "";
  host.innerHTML = TOOLBAR_ACTIONS.map((action) => {
    const separator = lastGroup && action.group !== lastGroup
      ? `<span class="editor-tool-sep" aria-hidden="true"></span>` : "";
    lastGroup = action.group || "";
    return `${separator}<button type="button" data-editor-action="${escapeHtml(action.id)}" title="${escapeHtml(action.title)}" aria-label="${escapeHtml(action.title)}">${escapeHtml(action.label)}</button>`;
  }).join("");
}

export function initNoteEditor() {
  if (initialized) return;
  initialized = true;
  renderToolbar();
  el("note-editor-close")?.addEventListener("click", () => closeEditor());
  el("note-editor-cancel")?.addEventListener("click", () => closeEditor());
  el("note-editor-save")?.addEventListener("click", () => saveNote());
  el("note-editor-reload")?.addEventListener("click", () => reloadNote());
  el("note-editor-classify")?.addEventListener("click", () => classifyDraft());
  el("note-editor-polish-btn")?.addEventListener("click", () => runPolish());
  el("note-editor-polish-apply")?.addEventListener("click", () => applyPolish());
  el("note-editor-polish-cancel")?.addEventListener("click", () => hidePolish());
  el("note-editor-role")?.addEventListener("click", () => openCompanion());
  el("note-editor-obsidian")?.addEventListener("click", () => openInObsidian());
  el("note-editor-preview-toggle")?.addEventListener("click", () => setPreviewMode(!state.preview));
  el("note-editor-focus-toggle")?.addEventListener("click", () => setFocusMode(!state.focus));
  el("note-editor-text")?.addEventListener("input", () => updatePreview());
  el("note-editor-title")?.addEventListener("input", () => updatePreview());
  el("note-editor-toolbar")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-editor-action]");
    if (button) applyToolbar(button.dataset.editorAction);
  });
  el("note-editor-text")?.addEventListener("keydown", (event) => {
    if (!(event.ctrlKey || event.metaKey)) return;
    const key = event.key.toLowerCase();
    if (key === "s") {
      event.preventDefault();
      saveNote();
      return;
    }
    const shortcut = { b: "bold", i: "italic", k: "link" }[key];
    if (shortcut) {
      event.preventDefault();
      applyToolbar(shortcut);
    }
  });
  el("note-editor-dialog")?.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeEditor();
  });
}

async function loadChapters() {
  if (!chaptersCache) {
    chaptersCache = await api("/api/writing/chapters");
  }
  return chaptersCache;
}

/** 新建/删除本子后让下一次打开编辑器重新读取章节目录。 */
export function invalidateChapterCache() {
  chaptersCache = null;
}

function journalDayFrom(note, options) {
  const explicit = String(options.journalDay || "").trim();
  if (explicit) return explicit.slice(0, 10);
  if (String(options.chapter || "") === "journal") return shanghaiDay();
  if (note?.chapter !== "journal") return "";
  const fromDate = String(note?.date || "").trim().slice(0, 10);
  const fromPath = String(note?.path || "").match(/(\d{4}-\d{2}-\d{2})\.md$/)?.[1] || "";
  return fromDate || fromPath || shanghaiDay();
}

async function openJournalDraft(day) {
  const payload = await api(`/api/journal/note?day=${encodeURIComponent(day)}`);
  if (payload?.exists) {
    const parts = splitFrontMatter(payload.content || "");
    const body = normalizeBody(parts.body);
    return {
      mode: "edit", path: payload.relpath || `00_人生路/01_日记/${day}.md`,
      revision: String(payload.sha256 || ""),
      title: `日记 · ${day}`, body, savedBody: body,
      frontmatter: ensureJournalFrontMatter(day, parts.frontmatter),
    };
  }
  const body = normalizeBody(`${journalHeading(day)}\n\n`);
  return {
    mode: "new", path: `00_人生路/01_日记/${day}.md`, revision: "",
    title: `日记 · ${day}`, body, savedBody: body,
    frontmatter: ensureJournalFrontMatter(day, ""),
  };
}

function applyJournalUi() {
  const dialog = el("note-editor-dialog");
  const paper = el("note-editor-paper");
  dialog?.classList.toggle("is-journal", state.journal);
  paper?.classList.toggle("is-journal", state.journal);
  const title = el("note-editor-title");
  if (title) {
    title.readOnly = state.journal;
    title.setAttribute("aria-readonly", state.journal ? "true" : "false");
  }
  const meta = document.querySelector("#note-editor-paper .editor-meta-row");
  if (meta) meta.hidden = state.journal;
  const classify = el("note-editor-classify");
  if (classify) {
    classify.textContent = "AI 建议章节";
    classify.hidden = state.journal;
  }
}

export async function openNoteEditor(options = {}) {
  initNoteEditor();
  const note = options.note || null;
  const journalDay = journalDayFrom(note, options);
  state.open = true;
  state.journal = Boolean(journalDay);
  state.journalDay = journalDay;
  state.preview = false;
  state.focus = false;
  polish = null;
  hidePolish();
  if (state.journal) {
    state.context = "life";
    state.chapter = "journal";
    state.tags = [];
    try {
      const draft = await openJournalDraft(journalDay);
      Object.assign(state, draft);
      state.savedBody = draft.savedBody;
    } catch (error) {
      toast(`今天的日记读取失败：${error.message}`, "error");
      state.mode = "new";
      state.path = `00_人生路/01_日记/${journalDay}.md`;
      state.revision = "";
      state.title = `日记 · ${journalDay}`;
      state.body = normalizeBody(`${journalHeading(journalDay)}\n\n`);
      state.savedBody = state.body;
      state.frontmatter = ensureJournalFrontMatter(journalDay, "");
    }
    if (el("note-editor-chapter")) el("note-editor-chapter").innerHTML = "";
  } else {
    state.mode = note ? "edit" : "new";
    state.path = String(note?.path || "");
    state.revision = String(note?.revision || "");
    state.context = String(note?.pillar || options.context || "life") === "knowledge" ? "knowledge" : "life";
    state.chapter = String(note?.chapter || options.chapter
      || (state.context === "life" ? "reflection" : "topic"));
    state.title = String(note?.title || options.title || "");
    state.body = normalizeBody(note?.body ?? options.body ?? "");
    state.savedBody = state.body;
    state.frontmatter = "";
    state.tags = Array.isArray(note?.tags) ? note.tags : [];
    try {
      const chapters = await loadChapters();
      fillChapters(chapters[state.context] || [], state.context);
    } catch (error) {
      toast(`本子列表读取失败：${error.message}`, "error");
    }
    const chapterSelect = el("note-editor-chapter");
    if (chapterSelect) chapterSelect.value = state.chapter;
  }
  const heading = el("note-editor-heading");
  if (heading) heading.textContent = state.journal ? "今天的日记" : (note ? "编辑笔记" : "写一条");
  const eyebrow = el("note-editor-eyebrow");
  if (eyebrow) eyebrow.textContent = state.journal
    ? `人生路 · ${state.journalDay}`
    : (state.context === "knowledge" ? "知识库 · 写作" : "人生路 · 写作");
  el("note-editor-title").value = state.title;
  el("note-editor-tags").value = tagsToInput(state.tags);
  el("note-editor-text").value = state.body;
  applyJournalUi();
  setPreviewMode(false);
  setFocusMode(false);
  setStatus("");
  updatePreview();
  const dialog = el("note-editor-dialog");
  if (dialog && !dialog.open) {
    try { dialog.showModal(); } catch { dialog.setAttribute("open", ""); }
  }
  window.setTimeout(() => {
    (state.journal || state.mode === "edit" ? el("note-editor-text") : el("note-editor-title"))?.focus?.();
  }, 30);
}

function fillChapters(chapters, context) {
  const select = el("note-editor-chapter");
  if (!select) return;
  const list = Array.isArray(chapters) ? chapters : [];
  const byNotebook = new Map(notebooksFor(context).map((book) => [book.key, []]));
  const extras = [];
  for (const chapter of list) {
    const book = notebookForChapter(context, chapter.key);
    if (book && byNotebook.has(book.key)) byNotebook.get(book.key).push(chapter);
    else extras.push(chapter);
  }
  const option = (chapter) => `<option value="${escapeAttr(chapter.key)}">${escapeAttr(chapter.title)}</option>`;
  const custom = extras.filter((chapter) => isCustomNotebookKey(chapter.key));
  const other = extras.filter((chapter) => !isCustomNotebookKey(chapter.key));
  select.innerHTML = [
    ...notebooksFor(context).map((book) => {
      const items = byNotebook.get(book.key) || [];
      return items.length ? `<optgroup label="${escapeAttr(book.title)}">${items.map(option).join("")}</optgroup>` : "";
    }),
    custom.length ? `<optgroup label="我的本子">${custom.map(option).join("")}</optgroup>` : "",
    other.map(option).join(""),
  ].join("");
}

function currentBody() {
  return normalizeBody(el("note-editor-text")?.value || "");
}

function currentTitle() {
  return String(el("note-editor-title")?.value || "").trim();
}

function currentTags() {
  return tagsFromInput(el("note-editor-tags")?.value || "");
}

function dirty() {
  if (state.journal) return isDirty(currentBody(), state.savedBody);
  return state.mode === "new"
    ? Boolean(currentTitle() || currentBody() || currentTags().length)
    : isDirty(currentBody(), state.savedBody) || currentTitle() !== state.title
      || tagsToInput(currentTags()) !== tagsToInput(state.tags);
}

async function closeEditor({ force = false } = {}) {
  if (!force && dirty()) {
    const leave = await confirmAction({
      title: "放弃未保存的修改？",
      message: "正文还没有保存。放弃后这次输入会丢失；也可以先点“保存”。",
      confirmLabel: "放弃修改", danger: true,
    });
    if (!leave) return;
  }
  const dialog = el("note-editor-dialog");
  if (dialog?.open) dialog.close();
  else dialog?.removeAttribute("open");
  state.open = false;
}

function setStatus(message, tone = "") {
  const node = el("note-editor-status");
  if (!node) return;
  node.textContent = message;
  node.className = `status-message ${tone}`.trim();
}

function setPreviewMode(show) {
  state.preview = Boolean(show);
  const textarea = el("note-editor-text");
  const preview = el("note-editor-preview");
  const button = el("note-editor-preview-toggle");
  const paper = el("note-editor-paper");
  if (textarea) textarea.hidden = state.preview;
  if (preview) preview.hidden = !state.preview;
  if (button) {
    button.setAttribute("aria-pressed", state.preview ? "true" : "false");
    button.textContent = state.preview ? "继续写" : "预览";
  }
  paper?.classList.toggle("is-preview", state.preview);
  if (state.preview) updatePreview();
}

function setFocusMode(on) {
  state.focus = Boolean(on);
  const paper = el("note-editor-paper");
  const dialog = el("note-editor-dialog");
  const button = el("note-editor-focus-toggle");
  paper?.classList.toggle("is-focus", state.focus);
  dialog?.classList.toggle("is-focus-editor", state.focus);
  if (button) {
    button.setAttribute("aria-pressed", state.focus ? "true" : "false");
    button.textContent = state.focus ? "退出专注" : "专注";
  }
}

function updatePreview() {
  const body = currentBody();
  const title = currentTitle();
  const preview = el("note-editor-preview");
  if (preview) {
    const doc = renderMarkdownDocument(body || "（还没有正文）");
    preview.innerHTML = body ? doc.html : `<p class="field-help">（还没有正文）</p>`;
  }
  const count = el("note-editor-count");
  if (count) {
    const words = wordCount(body);
    const saved = state.mode === "edit"
      && !isDirty(body, state.savedBody)
      && (state.journal || title === state.title);
    const reading = words ? ` · 约 ${Math.max(1, Math.round(words / 300))} 分钟` : "";
    count.textContent = `${words} 字${reading} · ${saved ? "已保存" : "有未保存修改"}`;
  }
}

function applyToolbar(action) {
  if (state.preview) setPreviewMode(false);
  const textarea = el("note-editor-text");
  if (!textarea) return;
  const result = applyEditorAction(textarea.value, textarea.selectionStart, textarea.selectionEnd, action);
  textarea.value = result.text;
  textarea.focus();
  textarea.setSelectionRange(result.selectionStart, result.selectionEnd);
  updatePreview();
}

async function saveJournal(body) {
  const day = state.journalDay || shanghaiDay();
  const frontmatter = ensureJournalFrontMatter(day, state.frontmatter);
  const content = joinFrontMatter(frontmatter, body);
  state.busy = true;
  setStatus("正在写今天的日记…");
  try {
    const result = await api("/api/journal/write", {
      method: "POST",
      body: JSON.stringify({
        day, content, expected_sha: state.revision || "", confirm: true,
      }),
      timeoutMs: 60000,
    });
    state.mode = "edit";
    state.path = result.path || state.path;
    state.revision = String(result.sha256 || "");
    state.frontmatter = splitFrontMatter(content).frontmatter;
    state.savedBody = body;
    setStatus("已写进今天的日记。", "success");
    toast("今天的日记已保存。", "success");
    document.dispatchEvent(new CustomEvent("torch:notes-changed", {
      detail: { path: state.path, chapter: "journal", pillar: "life" },
    }));
    updatePreview();
    return true;
  } catch (error) {
    if (error.status === 409) {
      setStatus("今天的日记在别处被改过，未覆盖。请点“重新载入”看最新版本。", "error");
    } else {
      setStatus(`保存失败：${error.message}`, "error");
    }
    return false;
  } finally {
    state.busy = false;
  }
}

async function saveNote() {
  if (state.busy) return;
  const body = currentBody();
  if (state.journal) {
    await saveJournal(body);
    return;
  }
  let title = currentTitle();
  if (!title) {
    title = String(body.split("\n").find((line) => line.trim()) || "").replace(/^#+\s*/, "").trim().slice(0, 40) || "未命名";
    el("note-editor-title").value = title;
  }
  const chapter = el("note-editor-chapter")?.value || state.chapter;
  state.busy = true;
  setStatus("正在保存…");
  try {
    let note;
    if (state.mode === "new") {
      note = await api("/api/writing/notes", {
        method: "POST",
        body: JSON.stringify({
          chapter, title, body, tags: currentTags(), source: "torch 编辑器",
        }),
        timeoutMs: 60000,
      });
    } else {
      note = await api("/api/writing/notes", {
        method: "PUT",
        body: JSON.stringify({
          path: state.path, expected_revision: state.revision,
          body, title, tags: currentTags(),
        }),
        timeoutMs: 60000,
      });
    }
    state.mode = "edit";
    state.path = note.path;
    state.revision = note.revision;
    state.chapter = note.chapter;
    state.title = note.title;
    state.body = normalizeBody(note.body);
    state.savedBody = state.body;
    state.tags = Array.isArray(note.tags) ? note.tags : [];
    setStatus("已保存。", "success");
    toast("笔记已保存。", "success");
    document.dispatchEvent(new CustomEvent("torch:notes-changed", {
      detail: { path: note.path, chapter: note.chapter, pillar: note.pillar },
    }));
    updatePreview();
  } catch (error) {
    if (error.status === 409) {
      setStatus("这篇笔记已在 Obsidian 或别处修改，未覆盖。请复制你的改动后点“重新载入”。", "error");
    } else {
      setStatus(`保存失败：${error.message}`, "error");
    }
  } finally {
    state.busy = false;
  }
}

async function reloadNote() {
  if (!state.path) return;
  if (dirty()) {
    const ok = await confirmAction({
      title: "重新载入并丢弃本地修改？",
      message: "会读取磁盘上的最新版本；当前编辑器里的改动将丢失。",
      confirmLabel: "重新载入", danger: true,
    });
    if (!ok) return;
  }
  if (state.journal) {
    try {
      const draft = await openJournalDraft(state.journalDay || shanghaiDay());
      Object.assign(state, draft);
      state.savedBody = draft.savedBody;
      el("note-editor-title").value = state.title;
      el("note-editor-text").value = state.body;
      setStatus("已重新载入今天的日记。", "success");
      updatePreview();
    } catch (error) {
      setStatus(`重新载入失败：${error.message}`, "error");
    }
    return;
  }
  try {
    const note = await api(`/api/notes?path=${encodeURIComponent(state.path)}`);
    state.revision = note.revision;
    state.title = note.title;
    state.body = normalizeBody(note.body);
    state.savedBody = state.body;
    state.tags = Array.isArray(note.tags) ? note.tags : [];
    el("note-editor-title").value = state.title;
    el("note-editor-tags").value = tagsToInput(state.tags);
    el("note-editor-text").value = state.body;
    if (el("note-editor-chapter") && note.chapter) el("note-editor-chapter").value = note.chapter;
    setStatus("已重新载入磁盘版本。", "success");
    updatePreview();
  } catch (error) {
    setStatus(`重新载入失败：${error.message}`, "error");
  }
}

async function classifyDraft() {
  const body = currentBody();
  if (!body.trim()) {
    toast("先写点内容，再让 AI 判类。", "warning");
    return;
  }
  setStatus("AI 正在判断类型…");
  try {
    const result = await api("/api/writing/classify", {
      method: "POST",
      body: JSON.stringify({ text: body, context: state.context }),
      timeoutMs: 120000,
    });
    const card = result?.cards?.[0];
    if (!card) {
      setStatus("没有拿到分类建议，保留当前章节。", "warning");
      return;
    }
    if (card.chapter === "journal") {
      setStatus(state.journal
        ? "AI 建议这一篇就留在今天的日记里；保存后会写进今天。"
        : "AI 建议写进今天的日记；这篇仍会保存到当前本子。想写日记请回人生路的日记本子。",
      state.journal ? "success" : "warning");
    } else {
      if (card.chapter && el("note-editor-chapter")) {
        el("note-editor-chapter").value = card.chapter;
      }
      if (!currentTitle() && card.title) {
        el("note-editor-title").value = card.title;
      }
      setStatus(`建议：${card.label || card.chapter} · ${card.reason || "AI 判类"}`, "success");
    }
    updatePreview();
  } catch (error) {
    setStatus(`AI 判类失败：${error.message}`, "error");
  }
}

function selectionTarget() {
  const textarea = el("note-editor-text");
  if (!textarea) return { text: "", start: 0, end: 0, whole: true };
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const selected = textarea.value.slice(start, end);
  return selected.trim()
    ? { text: selected, start, end, whole: false }
    : { text: textarea.value, start: 0, end: textarea.value.length, whole: true };
}

async function runPolish() {
  const target = selectionTarget();
  if (!target.text.trim()) {
    toast("先写点内容，再让 AI 润色。", "warning");
    return;
  }
  setStatus("AI 正在润色…");
  try {
    const result = await api("/api/writing/polish", {
      method: "POST",
      body: JSON.stringify({
        text: target.text, path: state.path, context: state.context,
      }),
      timeoutMs: 180000,
    });
    polish = { ...target, polished: result.polished, changes: result.changes || [] };
    const panel = el("note-editor-polish");
    const content = el("note-editor-polish-text");
    if (content) content.innerHTML = renderMarkdownDocument(result.polished).html;
    const changes = el("note-editor-polish-changes");
    if (changes) changes.textContent = (result.changes || []).length
      ? `改动：${result.changes.join("、")}` : "改动：只做了轻微整理";
    if (panel) panel.hidden = false;
    setStatus("润色结果只是预览；点“应用到正文”才会替换。", "success");
  } catch (error) {
    setStatus(`AI 润色失败：${error.message}`, "error");
  }
}

function applyPolish() {
  if (!polish) return;
  const textarea = el("note-editor-text");
  if (!textarea) return;
  if (polish.whole) {
    textarea.value = polish.polished;
  } else {
    const before = textarea.value.slice(0, polish.start);
    const after = textarea.value.slice(polish.end);
    textarea.value = before + polish.polished + after;
    textarea.setSelectionRange(polish.start, polish.start + polish.polished.length);
  }
  hidePolish();
  updatePreview();
  setStatus("已应用到正文；保存后写入。", "success");
}

function hidePolish() {
  polish = null;
  const panel = el("note-editor-polish");
  if (panel) panel.hidden = true;
}

function openCompanion() {
  if (dirty()) {
    toast("先把当前修改保存，写作伙伴才能读到全文。", "warning");
    return;
  }
  // <dialog> 在浏览器顶层（top layer），固定定位的角色抽屉会被它盖住；
  // 已保存时先收起编辑器，再打开角色抽屉。
  const dialog = el("note-editor-dialog");
  if (dialog?.open) dialog.close();
  state.open = false;
  openRoleDrawer({
    roleId: "journal_companion", mode: "discuss",
    entry: state.context === "knowledge" ? "knowledge.editor" : "life.editor",
    title: state.context === "knowledge" ? "学术导师 · 聊聊" : "写作伙伴",
    selectors: state.journal
      ? [{ entry: "journal" }]
      : (state.path ? [{ entry: "note", path: state.path }] : [{ entry: "journal" }]),
    prompt: "看看我写的这段，问我一个我没写出来的问题。",
  });
}

function openInObsidian() {
  if (!state.path) {
    toast("先保存，才能在 Obsidian 里打开。", "warning");
    return;
  }
  if (!openObsidianNote(state.path, appState.config?.vault_name || "")) {
    toast("没有可用的 Obsidian 库名，请手动打开。", "warning");
  }
}

function escapeAttr(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));
}
