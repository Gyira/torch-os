// 应用内 Markdown 编辑器（2026-10-29，v1.0 P2）。
// 只开放人生路/知识库的允许章节；保存走 /api/writing/notes，revision 冲突 409 不覆盖。
// 工具栏只插入 Markdown 语法，预览复用 pillar-markdown；AI 润色先预览、用户点应用才改正文。
import { $, state as appState, api, confirmAction, escapeHtml, toast } from "./core.js";
import { renderMarkdownDocument } from "./pillar-markdown.js";
import {
  TOOLBAR_ACTIONS, applyEditorAction, ensureJournalFrontMatter, isDirty,
  joinFrontMatter, normalizeBody, savePlan, splitFrontMatter, tagsFromInput, tagsToInput,
  wordCount,
} from "./editor-model.js";
import {
  defaultWriteChapter, isCustomNotebookKey, journalHeading, notebookForChapter,
  notebooksFor,
} from "./notebook-model.js";
import { shanghaiDay } from "./journal-model.js";
import {
  filterTemplates, firstEmptyLineOffset, isBlankJournalBody, journalTemplateBody,
  rankedTemplates, resolveTemplate, suggestTemplates, templateById,
  templateEmptyMarkup, templateFilterMarkup, templateFilters, templateRowMarkup,
  templateStripMarkup, templateVariables,
} from "./journal-templates.js";
import {
  closeRoleDrawer, isRoleDrawerEmbedded, isRoleDrawerOpen, openRoleDrawer,
} from "./roles.js";
import { companionForEditor } from "./ai-companions.js";
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
  task: false,
  taskKey: "",
  frontmatter: "",
  preview: false,
  focus: false,
  busy: false,
  template: { dismissed: false, touched: false, applied: null },
};
let chaptersCache = null;
let initialized = false;
let polish = null;
let openingTaskDocument = false;
let templateUndoTimer = 0;
const RECENT_TEMPLATES_KEY = "torch.template.recent.v1";
const templateSheet = {
  ranked: [], filter: "all", query: "", preview: null, shift: 0,
};

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
  el("note-editor-delete")?.addEventListener("click", () => deleteCurrentNote());
  el("note-editor-reload")?.addEventListener("click", () => reloadNote());
  el("note-editor-chapter")?.addEventListener("change", () => {
    if (state.mode === "edit" && !state.journal && !state.task) {
      const target = el("note-editor-chapter")?.value || "";
      setStatus(target && target !== state.chapter ? "保存后会移动到新的本子。" : "");
    }
  });
  el("note-editor-classify")?.addEventListener("click", () => classifyDraft());
  el("note-editor-polish-btn")?.addEventListener("click", () => runPolish());
  el("note-editor-polish-apply")?.addEventListener("click", () => applyPolish());
  el("note-editor-polish-cancel")?.addEventListener("click", () => hidePolish());
  el("note-editor-role")?.addEventListener("click", () => openCompanion());
  document.addEventListener("torch:editor-selection-ask", (event) => {
    if (!el("note-editor-dialog")?.open) return;
    openCompanion(event.detail?.selection || null);
  });
  el("note-editor-obsidian")?.addEventListener("click", () => openInObsidian());
  el("note-editor-preview-toggle")?.addEventListener("click", () => setPreviewMode(!state.preview));
  el("note-editor-focus-toggle")?.addEventListener("click", () => setFocusMode(!state.focus));
  el("note-editor-text")?.addEventListener("input", () => {
    handleTemplateInput();
    updatePreview();
  });
  el("note-editor-title")?.addEventListener("input", () => {
    handleTemplateInput();
    updatePreview();
  });
  el("note-editor-tags")?.addEventListener("input", () => handleTemplateInput());
  el("note-editor-template-strip")?.addEventListener("click", (event) => {
    const chip = event.target.closest?.("[data-template-id]");
    if (chip) {
      applyTemplateById(chip.dataset.templateId);
      return;
    }
    if (event.target.closest?.("#template-open-all")) {
      openTemplateSheet();
      return;
    }
    if (event.target.closest?.("#template-strip-dismiss")) dismissTemplateStrip();
  });
  el("note-editor-template-sheet")?.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-template-close]")) {
      closeTemplateSheet();
      return;
    }
    const filter = event.target.closest?.("[data-template-filter]");
    if (filter) {
      setTemplateFilter(filter.dataset.templateFilter);
      return;
    }
    const row = event.target.closest?.(".template-row");
    if (row) {
      openTemplatePreview(row.dataset.templateId);
      return;
    }
    if (event.target.closest?.("#template-preview-back")) {
      showTemplateList();
      return;
    }
    if (event.target.closest?.("#template-preview-shuffle")) {
      shuffleTemplateQuestion();
      return;
    }
    if (event.target.closest?.("#template-preview-use")) usePreviewedTemplate();
  });
  el("note-editor-template-undo-btn")?.addEventListener("click", () => {
    const applied = state.template.applied;
    if (applied) undoAppliedTemplate(applied);
  });
  el("note-editor-template-undo")?.addEventListener("focusin", () => {
    window.clearTimeout(templateUndoTimer);
    templateUndoTimer = 0;
  });
  el("note-editor-template-undo")?.addEventListener("focusout", (event) => {
    const node = el("note-editor-template-undo");
    if (!node || node.hidden) return;
    if (!node.contains(event.relatedTarget)) hideTemplateUndo();
  });
  el("template-search")?.addEventListener("input", () => {
    templateSheet.query = el("template-search")?.value || "";
    renderTemplateResults();
  });
  el("note-editor-template-sheet")?.addEventListener("keydown", (event) => {
    if (event.key !== "Tab" || !isTemplateSheetOpen()) return;
    const nodes = [...document.querySelectorAll(
      "#note-editor-template-sheet button:not([disabled]), #note-editor-template-sheet input")]
      .filter((node) => node.offsetParent !== null);
    if (!nodes.length) {
      event.preventDefault();
      return;
    }
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    const active = document.activeElement;
    const panel = document.querySelector("#note-editor-template-sheet .template-sheet-panel");
    if (!document.querySelector("#note-editor-template-sheet")?.contains(active)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
      return;
    }
    if (active === panel) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
      return;
    }
    if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    const dialog = el("note-editor-dialog");
    if (!dialog?.open) return;
    // 上面还开着确认框/详情框时，Escape 归那个对话框处理。
    const active = document.activeElement;
    const otherDialog = [...document.querySelectorAll("dialog[open]")]
      .find((node) => node !== dialog && node.contains(active));
    if (otherDialog) return;
    // 部分浏览器里 <dialog> 的 cancel 不可取消：在 keydown 阶段拦下，
    // 模板面板只收起自己；没有面板时走带“放弃修改”确认的正常关闭。
    event.preventDefault();
    event.stopPropagation();
    if (isRoleDrawerOpen() && isRoleDrawerEmbedded()) closeRoleDrawer();
    else if (isTemplateSheetOpen()) closeTemplateSheet();
    else closeEditor();
  }, true);
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
    if (isTemplateSheetOpen()) {
      closeTemplateSheet();
      return;
    }
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
  document.dispatchEvent(new CustomEvent("torch:chapters-changed"));
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

async function fetchTaskDocument(task) {
  const key = String(task?.key || "").trim();
  const path = String(task?.path || "").trim();
  if (!key && !path) throw new Error("缺少任务文档的 key 或路径。");
  const query = key
    ? `key=${encodeURIComponent(key)}`
    : `path=${encodeURIComponent(path)}`;
  return api(`/api/time/tasks/document?${query}`);
}

function applyTaskDocument(doc) {
  state.mode = "edit";
  state.taskKey = String(doc?.key || state.taskKey);
  state.path = String(doc?.path || "");
  state.revision = String(doc?.revision || "");
  state.title = String(doc?.title || "");
  state.body = normalizeBody(doc?.body ?? "");
  state.savedBody = state.body;
}

function applyJournalUi() {
  const dialog = el("note-editor-dialog");
  const paper = el("note-editor-paper");
  dialog?.classList.toggle("is-journal", state.journal);
  dialog?.classList.toggle("is-task", state.task);
  paper?.classList.toggle("is-journal", state.journal);
  const title = el("note-editor-title");
  if (title) {
    title.readOnly = state.journal;
    title.placeholder = state.task ? "任务标题" : "标题";
    title.setAttribute("aria-label", state.task ? "任务标题" : "笔记标题");
    title.setAttribute("aria-readonly", state.journal ? "true" : "false");
  }
  const text = el("note-editor-text");
  if (text) text.placeholder = state.journal ? "开始记录…" : "写点什么……";
  const meta = document.querySelector("#note-editor-paper .editor-meta-row");
  if (meta) meta.hidden = state.journal || state.task;
  const classify = el("note-editor-classify");
  if (classify) {
    classify.textContent = "AI 建议章节";
    classify.hidden = state.journal || state.task;
  }
}

function templateDay() {
  return state.journal ? (state.journalDay || shanghaiDay()) : shanghaiDay();
}

function templateContentPresent() {
  if (state.journal) {
    return !isBlankJournalBody(currentBody(), state.journalDay || shanghaiDay());
  }
  return Boolean(currentTitle() || currentBody().trim() || currentTags().length);
}

/** 模板只出现在人生路的空白新写作里：已有笔记、知识库、已开始写的都不可用。 */
function templateEligible() {
  if (!state.open || state.busy || String(state.context || "") !== "life") return false;
  if (state.journal) return !templateContentPresent();
  return state.mode === "new" && !templateContentPresent();
}

function refreshTemplateStrip() {
  const strip = el("note-editor-template-strip");
  if (!strip) return;
  const visible = templateEligible()
    && !state.template.dismissed
    && !state.template.touched
    && !state.template.applied;
  if (!visible) {
    strip.hidden = true;
    return;
  }
  const suggestions = suggestTemplates({
    day: templateDay(),
    recent: readRecentTemplates(),
    now: new Date(),
  });
  if (!suggestions.length) {
    strip.hidden = true;
    return;
  }
  const chips = el("template-strip-chips");
  if (chips) chips.innerHTML = templateStripMarkup(suggestions);
  strip.hidden = false;
}

function handleTemplateInput() {
  if (!state.open) return;
  if (state.template.applied) {
    hideTemplateUndo();
    return;
  }
  if (state.template.touched) return;
  if (!templateContentPresent()) return;
  state.template.touched = true;
  refreshTemplateStrip();
}

function dismissTemplateStrip() {
  state.template.dismissed = true;
  refreshTemplateStrip();
  (state.journal ? el("note-editor-text") : el("note-editor-title"))?.focus?.();
}

function showTemplateUndo(applied) {
  const node = el("note-editor-template-undo");
  const text = el("note-editor-template-undo-text");
  if (!node) return;
  if (text) text.textContent = `已套用「${applied.name}」`;
  node.hidden = false;
  window.clearTimeout(templateUndoTimer);
  templateUndoTimer = window.setTimeout(hideTemplateUndo, 6000);
}

function hideTemplateUndo() {
  window.clearTimeout(templateUndoTimer);
  templateUndoTimer = 0;
  const node = el("note-editor-template-undo");
  if (node) node.hidden = true;
}

function readRecentTemplates() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(RECENT_TEMPLATES_KEY) || "[]");
    return Array.isArray(parsed)
      ? parsed.filter((id) => typeof id === "string").slice(0, 10)
      : [];
  } catch {
    return [];
  }
}

function rememberRecentTemplate(id) {
  const next = [id, ...readRecentTemplates().filter((item) => item !== id)].slice(0, 10);
  try {
    window.localStorage.setItem(RECENT_TEMPLATES_KEY, JSON.stringify(next));
  } catch {
    // 本地存储不可用时只影响建议顺序，套用照常进行。
  }
}

function isTemplateSheetOpen() {
  const sheet = el("note-editor-template-sheet");
  return Boolean(sheet && !sheet.hidden);
}

function openTemplateSheet() {
  if (!templateEligible() || state.template.applied || isTemplateSheetOpen()) return;
  templateSheet.ranked = rankedTemplates({ day: templateDay(), now: new Date() });
  templateSheet.filter = "all";
  templateSheet.query = "";
  templateSheet.preview = null;
  templateSheet.shift = 0;
  const search = el("template-search");
  if (search) search.value = "";
  showTemplateListStep();
  renderTemplateFilters();
  renderTemplateResults();
  const sheet = el("note-editor-template-sheet");
  if (sheet) sheet.hidden = false;
  const shell = document.querySelector("#note-editor-dialog .editor-shell");
  if (shell) shell.inert = true;
  const results = el("template-results");
  if (results) results.scrollTop = 0;
  el("template-open-all")?.setAttribute("aria-expanded", "true");
  const panel = document.querySelector("#note-editor-template-sheet .template-sheet-panel");
  window.setTimeout(() => {
    if (isTemplateSheetOpen()) panel?.focus?.();
  }, 30);
}

function closeTemplateSheet({ restoreFocus = true } = {}) {
  const sheet = el("note-editor-template-sheet");
  if (!sheet || sheet.hidden) return;
  sheet.hidden = true;
  templateSheet.preview = null;
  const shell = document.querySelector("#note-editor-dialog .editor-shell");
  if (shell) shell.inert = false;
  el("template-open-all")?.setAttribute("aria-expanded", "false");
  document.querySelector("#note-editor-template-sheet .template-sheet-panel")
    ?.setAttribute("aria-labelledby", "template-sheet-title");
  if (restoreFocus) el("template-open-all")?.focus?.();
}

function showTemplateListStep() {
  const list = el("template-list-step");
  const preview = el("template-preview-step");
  if (list) list.hidden = false;
  if (preview) preview.hidden = true;
  document.querySelector("#note-editor-template-sheet .template-sheet-panel")
    ?.setAttribute("aria-labelledby", "template-sheet-title");
}

function renderTemplateFilters() {
  const host = el("template-filters");
  if (!host) return;
  const filters = templateFilters(templateSheet.ranked);
  if (!filters.some((filter) => filter.key === templateSheet.filter)) templateSheet.filter = "all";
  host.innerHTML = templateFilterMarkup(filters, templateSheet.filter);
}

function renderTemplateResults() {
  const host = el("template-results");
  if (!host) return;
  const items = filterTemplates(templateSheet.ranked, {
    filter: templateSheet.filter,
    query: templateSheet.query,
  });
  host.innerHTML = items.length
    ? items.map((item) => templateRowMarkup(item.template, item.recommended)).join("")
    : templateEmptyMarkup(templateSheet.query);
  const query = String(templateSheet.query || "").trim();
  const status = el("template-list-status");
  if (status) {
    status.textContent = items.length
      ? `共 ${items.length} 个模板`
      : (query ? `没有找到与「${query}」相关的模板` : "这里还没有模板");
  }
}

function setTemplateFilter(filter) {
  templateSheet.filter = String(filter || "all");
  const nodes = [...(el("template-filters")?.querySelectorAll("[data-template-filter]") || [])];
  if (!nodes.some((node) => node.dataset.templateFilter === templateSheet.filter)) {
    templateSheet.filter = "all";
  }
  for (const node of nodes) {
    node.setAttribute("aria-pressed", node.dataset.templateFilter === templateSheet.filter ? "true" : "false");
  }
  renderTemplateResults();
  nodes.find((node) => node.dataset.templateFilter === templateSheet.filter)?.focus?.();
}

function openTemplatePreview(id) {
  const template = templateById(id);
  if (!template) return;
  templateSheet.preview = template;
  templateSheet.shift = 0;
  renderTemplatePreview();
  const list = el("template-list-step");
  const preview = el("template-preview-step");
  if (list) list.hidden = true;
  if (preview) preview.hidden = false;
  document.querySelector("#note-editor-template-sheet .template-sheet-panel")
    ?.setAttribute("aria-labelledby", "template-preview-name");
  el("template-search")?.blur?.();
  el("template-preview-name")?.focus?.();
}

function renderTemplatePreview() {
  const template = templateSheet.preview;
  if (!template) return;
  const variables = templateVariables(templateDay(), {
    now: new Date(), questionShift: templateSheet.shift,
  });
  const resolved = resolveTemplate(template, variables);
  const name = el("template-preview-name");
  if (name) name.textContent = resolved.name;
  const title = el("template-preview-title");
  if (title) title.textContent = resolved.title;
  const body = el("template-preview-body");
  if (body) body.innerHTML = renderMarkdownDocument(resolved.body).html;
  const hint = el("template-preview-hint");
  if (hint) {
    hint.textContent = state.journal
      ? "日期与问题已按这篇日记填好；模板标题会写进正文第一行。"
      : "日期与问题已按这篇记录填好。";
  }
  const card = document.querySelector("#template-preview-step .template-preview-card");
  if (card) card.scrollTop = 0;
  const shuffle = el("template-preview-shuffle");
  if (shuffle) shuffle.hidden = !resolved.usesQuestion;
}

function showTemplateList() {
  templateSheet.preview = null;
  showTemplateListStep();
  renderTemplateResults();
  el("template-search")?.focus?.();
}

function shuffleTemplateQuestion() {
  templateSheet.shift += 1;
  renderTemplatePreview();
}

function usePreviewedTemplate() {
  const template = templateSheet.preview;
  if (template) applyTemplate(template, templateSheet.shift);
}

function applyTemplateById(id) {
  const template = templateById(id);
  if (template) applyTemplate(template, 0);
}

function applyTemplate(template, questionShift = 0) {
  if (!template || state.busy) return;
  if (!templateEligible()) {
    closeTemplateSheet();
    setStatus("已经开始写了，模板只能套用在空白页上。", "warning");
    toast("已经开始写了，模板只能套用在空白页上。", "warning");
    return;
  }
  const variables = templateVariables(templateDay(), {
    now: new Date(), questionShift,
  });
  const resolved = resolveTemplate(template, variables);
  const titleInput = el("note-editor-title");
  const textInput = el("note-editor-text");
  const tagsInput = el("note-editor-tags");
  if (!textInput) return;
  const before = {
    title: titleInput?.value || "",
    body: textInput.value,
    tags: tagsInput?.value || "",
  };
  const prefix = state.journal ? `# ${resolved.title}\n\n` : "";
  const body = state.journal
    ? journalTemplateBody(resolved.title, resolved.body)
    : resolved.body;
  if (state.preview) setPreviewMode(false);
  if (state.journal) {
    textInput.value = body;
  } else {
    if (titleInput) titleInput.value = resolved.title;
    textInput.value = body;
    if (tagsInput) {
      tagsInput.value = tagsToInput([...new Set([
        ...tagsFromInput(before.tags), ...(resolved.tags || []),
      ])]);
    }
  }
  const applied = {
    id: template.id,
    name: template.name,
    title: titleInput?.value || "",
    body: textInput.value,
    tags: tagsInput?.value || "",
    before,
  };
  state.template.applied = applied;
  state.template.touched = true;
  rememberRecentTemplate(template.id);
  closeTemplateSheet({ restoreFocus: false });
  refreshTemplateStrip();
  const caret = prefix.length + firstEmptyLineOffset(resolved.body);
  textInput.focus?.();
  textInput.setSelectionRange?.(caret, caret);
  updatePreview();
  setStatus(`已套用「${template.name}」，保存后写入。`, "success");
  showTemplateUndo(applied);
}

function undoAppliedTemplate(applied) {
  if (!applied || state.template.applied !== applied) return;
  hideTemplateUndo();
  const titleInput = el("note-editor-title");
  const textInput = el("note-editor-text");
  const tagsInput = el("note-editor-tags");
  const pristine = (titleInput?.value || "") === applied.title
    && normalizeBody(textInput?.value || "") === normalizeBody(applied.body)
    && (tagsInput?.value || "") === applied.tags;
  if (!pristine) {
    setStatus("已经开始写了，模板内容保留。", "warning");
    toast("已经开始写了，模板内容保留。", "warning");
    return;
  }
  if (titleInput) titleInput.value = applied.before.title;
  if (textInput) textInput.value = applied.before.body;
  if (tagsInput) tagsInput.value = applied.before.tags;
  state.template.applied = null;
  state.template.touched = false;
  state.template.dismissed = false;
  refreshTemplateStrip();
  updatePreview();
  setStatus("已撤销模板。", "success");
  if (state.journal) textInput?.focus?.();
  else titleInput?.focus?.();
}

function resetTemplateSession() {
  closeTemplateSheet({ restoreFocus: false });
  hideTemplateUndo();
  state.template = { dismissed: false, touched: false, applied: null };
  templateSheet.ranked = [];
  templateSheet.filter = "all";
  templateSheet.query = "";
  templateSheet.preview = null;
  templateSheet.shift = 0;
}

export async function openNoteEditor(options = {}) {
  if (openingTaskDocument) return;
  initNoteEditor();
  if (isRoleDrawerOpen()) closeRoleDrawer();
  document.dispatchEvent(new CustomEvent("torch:editor-opened"));
  resetTemplateSession();
  const note = options.note || null;
  const task = options.task || null;
  const headingOverride = String(options.heading || "");
  const journalDay = journalDayFrom(note, options);
  state.open = true;
  state.busy = false;
  state.journal = Boolean(journalDay) && !task;
  state.journalDay = state.journal ? journalDay : "";
  state.task = Boolean(task);
  state.taskKey = String(task?.key || "");
  state.preview = false;
  state.focus = false;
  polish = null;
  hidePolish();
  if (state.task) {
    state.context = "ops";
    state.chapter = "";
    state.tags = [];
    state.frontmatter = "";
    state.mode = "edit";
    openingTaskDocument = true;
    try {
      applyTaskDocument(await fetchTaskDocument(task));
    } catch (error) {
      state.open = false;
      state.task = false;
      state.taskKey = "";
      throw error;
    } finally {
      openingTaskDocument = false;
    }
    if (el("note-editor-chapter")) el("note-editor-chapter").innerHTML = "";
  } else if (state.journal) {
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
      || defaultWriteChapter(state.context));
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
  if (heading) {
    heading.textContent = state.journal
      ? "今天的日记"
      : state.task ? "任务文档" : (note ? "编辑笔记" : (headingOverride || "写一条"));
  }
  const eyebrow = el("note-editor-eyebrow");
  if (eyebrow) eyebrow.textContent = state.journal
    ? `人生路 · ${state.journalDay}`
    : state.task ? "生活运营 · 待办事项 · 任务文档"
      : (state.context === "knowledge" ? "知识库 · 写作" : "人生路 · 写作");
  el("note-editor-title").value = state.title;
  el("note-editor-tags").value = tagsToInput(state.tags);
  el("note-editor-text").value = state.body;
  applyJournalUi();
  setPreviewMode(false);
  setFocusMode(false);
  syncDeleteButton();
  setStatus(state.task
    ? "标题就是任务板上的名字；正文保存在任务文档里，日期和优先级仍在任务板。"
    : "");
  updatePreview();
  refreshTemplateStrip();
  syncCompanionUi();
  const dialog = el("note-editor-dialog");
  if (dialog && !dialog.open) {
    try { dialog.showModal(); } catch { dialog.setAttribute("open", ""); }
  }
  window.setTimeout(() => {
    if (!state.open || isTemplateSheetOpen()) return;
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
  if (state.task) return isDirty(currentBody(), state.savedBody) || currentTitle() !== state.title;
  return state.mode === "new"
    ? Boolean(currentTitle() || currentBody() || currentTags().length)
    : isDirty(currentBody(), state.savedBody) || currentTitle() !== state.title
      || tagsToInput(currentTags()) !== tagsToInput(state.tags);
}

function applyNote(note) {
  state.mode = "edit";
  state.path = String(note.path || state.path);
  state.revision = String(note.revision || "");
  state.chapter = String(note.chapter || state.chapter);
  state.title = String(note.title || state.title);
  state.body = normalizeBody(note.body ?? state.body);
  state.savedBody = state.body;
  state.tags = Array.isArray(note.tags) ? note.tags : state.tags;
  if (note.pillar) state.context = note.pillar === "knowledge" ? "knowledge" : "life";
}

/**
 * 保存成功后把「编辑器里的正文」记为已保存版本。
 * 后端会去掉正文首尾空白并补文件结尾换行，parse 回来可能比 textarea 少一个换行；
 * 若直接拿服务端 body 当 savedBody，会立刻被 dirty() 判成未保存，返回时误弹确认框。
 * 与任务文档保持一致：savedBody 记提交的规范化正文，不回写 textarea（避免光标跳动）。
 */
function applySavedNote(note, submittedBody = null) {
  applyNote(note);
  if (submittedBody !== null) {
    const normalized = normalizeBody(submittedBody);
    state.body = normalized;
    state.savedBody = normalized;
  }
  const titleInput = el("note-editor-title");
  if (titleInput && titleInput.value !== state.title) titleInput.value = state.title;
  const tagsInput = el("note-editor-tags");
  if (tagsInput) tagsInput.value = tagsToInput(state.tags);
  syncChapterSelect();
}

function syncChapterSelect() {
  const select = el("note-editor-chapter");
  if (select && state.chapter) select.value = state.chapter;
}

function syncDeleteButton() {
  const button = el("note-editor-delete");
  if (!button) return;
  button.hidden = state.journal || state.task || state.mode !== "edit" || !state.path;
  button.disabled = state.busy;
}

function dispatchNotesChanged(note, previousPath = "", deleted = false) {
  document.dispatchEvent(new CustomEvent("torch:notes-changed", {
    detail: {
      path: note.path,
      previousPath,
      deleted,
      chapter: note.chapter || state.chapter,
      pillar: note.pillar || state.context,
    },
  }));
}

/** 删除确认 + 软删除请求；调用方负责刷新页面和处理错误。 */
export async function deleteNoteFile(note, { dirtyNote = false } = {}) {
  const title = String(note?.title || "这篇笔记");
  const ok = await confirmAction({
    title: "删除这篇笔记？",
    message: `《${title}》会移入应用回收站（.torch-os/笔记回收站），之后仍可在文件管理器里找回。${dirtyNote ? "当前未保存的修改也会一起丢弃。" : ""}`,
    confirmLabel: "删除", danger: true,
  });
  if (!ok) return null;
  return api("/api/writing/notes/delete", {
    method: "POST",
    body: JSON.stringify({ path: note.path, expected_revision: note.revision || "" }),
    timeoutMs: 60000,
  });
}

async function deleteCurrentNote() {
  if (state.busy || state.journal || state.task || state.mode !== "edit" || !state.path) return;
  state.busy = true;
  syncDeleteButton();
  setStatus("正在删除…");
  try {
    const result = await deleteNoteFile({
      path: state.path, revision: state.revision,
      title: state.title || currentTitle(),
    }, { dirtyNote: dirty() });
    if (!result) {
      setStatus("");
      return;
    }
    const deletedPath = state.path;
    const chapter = state.chapter;
    const pillar = state.context;
    await closeEditor({ force: true });
    dispatchNotesChanged(
      { path: deletedPath, chapter, pillar }, deletedPath, true);
    toast("已删除，可在 .torch-os/笔记回收站 找回。", "success", { timeout: 8000 });
  } catch (error) {
    if (error.status === 409) {
      setStatus("这篇笔记已在别处修改，未删除。请重新载入核对后再试。", "error");
    } else if (error.status === 404) {
      setStatus("这篇笔记已经不在了，可能已在 Obsidian 删除。", "error");
    } else {
      setStatus(`删除失败：${error.message}`, "error");
    }
  } finally {
    state.busy = false;
    syncDeleteButton();
  }
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
  if (isRoleDrawerOpen() && isRoleDrawerEmbedded()) closeRoleDrawer();
  resetTemplateSession();
  const dialog = el("note-editor-dialog");
  if (dialog?.open) dialog.close();
  else dialog?.removeAttribute("open");
  state.open = false;
  document.dispatchEvent(new CustomEvent("torch:editor-closed"));
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
  handleTemplateInput();
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
    hideTemplateUndo();
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

function taskDocumentTitle(body) {
  return String(body.split("\n").find((line) => line.trim()) || "")
    .replace(/^#+\s*/, "").trim().slice(0, 40) || "未命名任务";
}

/**
 * 任务文档保存：走任务专用接口，只校验该文件的 revision。
 * 文件层会补齐结尾换行，所以 savedBody 记提交的规范化正文，不回写 textarea（避免光标跳动）。
 */
async function saveTaskDocument(body) {
  if (!dirty()) {
    setStatus("没有新的修改。", "success");
    return true;
  }
  let title = currentTitle();
  if (!title) {
    title = taskDocumentTitle(body);
    el("note-editor-title").value = title;
  }
  const previousPath = state.path;
  const normalized = normalizeBody(body);
  state.busy = true;
  syncDeleteButton();
  setStatus("正在保存任务文档…");
  try {
    const result = await api("/api/time/tasks/document", {
      method: "POST",
      body: JSON.stringify({
        key: state.taskKey, expected_revision: state.revision,
        title, body: normalized,
      }),
      timeoutMs: 60000,
    });
    state.taskKey = String(result?.key || state.taskKey);
    state.path = String(result?.path || state.path);
    state.revision = String(result?.revision || "");
    state.title = String(result?.title || title);
    state.body = normalized;
    state.savedBody = normalized;
    el("note-editor-title").value = state.title;
    const renamed = state.path !== previousPath;
    setStatus(renamed ? "已保存；标题变化已同步到任务板。" : "已保存。", "success");
    toast("任务文档已保存。", "success");
    document.dispatchEvent(new CustomEvent("torch:tasks-changed"));
    dispatchNotesChanged(
      { path: state.path, chapter: "task", pillar: "ops" }, previousPath);
    updatePreview();
    return true;
  } catch (error) {
    if (error.status === 409 && !/只读/.test(error.message || "")) {
      setStatus("任务文档已在别处修改或已移动，未覆盖。请从任务板重新打开。", "error");
    } else {
      setStatus(`保存失败：${error.message}`, "error");
    }
    return false;
  } finally {
    state.busy = false;
    syncDeleteButton();
  }
}

async function saveNote() {
  if (state.busy) return;
  const body = currentBody();
  if (state.journal) {
    await saveJournal(body);
    return;
  }
  if (state.task) {
    await saveTaskDocument(body);
    return;
  }
  const previousPath = state.path;
  let title = currentTitle();
  if (!title) {
    title = String(body.split("\n").find((line) => line.trim()) || "").replace(/^#+\s*/, "").trim().slice(0, 40) || "未命名";
    el("note-editor-title").value = title;
  }
  const chapter = el("note-editor-chapter")?.value || state.chapter;
  const plan = savePlan({
    mode: state.mode, dirty: dirty(),
    currentChapter: state.chapter, targetChapter: chapter,
  });
  state.busy = true;
  syncDeleteButton();
  setStatus(plan.move ? "正在保存并移动…" : "正在保存…");
  try {
    let note;
    let moved = false;
    if (state.mode === "new") {
      note = await api("/api/writing/notes", {
        method: "POST",
        body: JSON.stringify({
          chapter, title, body, tags: currentTags(), source: "torch 编辑器",
        }),
        timeoutMs: 60000,
      });
    } else {
      if (plan.saveContent) {
        note = await api("/api/writing/notes", {
          method: "PUT",
          body: JSON.stringify({
            path: state.path, expected_revision: state.revision,
            body, title, tags: currentTags(),
          }),
          timeoutMs: 60000,
        });
      } else {
        note = {
          path: state.path, revision: state.revision, chapter: state.chapter,
          title: state.title || title, body: state.savedBody, tags: state.tags,
          pillar: state.context,
        };
      }
      // 先把正文落盘，再移动文件；移动失败时内容已经保存，只需要重试移动。
      applySavedNote(note, body);
      if (plan.move) {
        try {
          note = await api("/api/writing/notes/move", {
            method: "POST",
            body: JSON.stringify({
              path: note.path, target_chapter: plan.targetChapter,
              expected_revision: note.revision,
            }),
            timeoutMs: 60000,
          });
          moved = true;
        } catch (error) {
          syncChapterSelect();
          dispatchNotesChanged(note, previousPath);
          setStatus(`内容已保存，但移动失败：${error.message}`, "error");
          toast("内容已保存；本子没有移动。", "warning");
          updatePreview();
          return;
        }
      }
    }
    applySavedNote(note, body);
    setStatus(moved ? "已保存并移动到新本子。" : "已保存。", "success");
    toast(moved ? "笔记已保存并移动。" : "笔记已保存。", "success");
    dispatchNotesChanged(note, previousPath);
    updatePreview();
    refreshTemplateStrip();
    hideTemplateUndo();
  } catch (error) {
    if (error.status === 409) {
      setStatus("这篇笔记已在 Obsidian 或别处修改，未覆盖。请复制你的改动后点“重新载入”。", "error");
    } else {
      setStatus(`保存失败：${error.message}`, "error");
    }
  } finally {
    state.busy = false;
    syncDeleteButton();
  }
}

async function reloadNote() {
  if (!state.path && !state.taskKey) return;
  if (dirty()) {
    const ok = await confirmAction({
      title: "重新载入并丢弃本地修改？",
      message: "会读取磁盘上的最新版本；当前编辑器里的改动将丢失。",
      confirmLabel: "重新载入", danger: true,
    });
    if (!ok) return;
  }
  resetTemplateSession();
  if (state.journal) {
    try {
      const draft = await openJournalDraft(state.journalDay || shanghaiDay());
      Object.assign(state, draft);
      state.savedBody = draft.savedBody;
      el("note-editor-title").value = state.title;
      el("note-editor-text").value = state.body;
      setStatus("已重新载入今天的日记。", "success");
      updatePreview();
      refreshTemplateStrip();
    } catch (error) {
      setStatus(`重新载入失败：${error.message}`, "error");
    }
    return;
  }
  if (state.task) {
    try {
      applyTaskDocument(await fetchTaskDocument({
        key: state.taskKey, path: state.path,
      }));
      el("note-editor-title").value = state.title;
      el("note-editor-text").value = state.body;
      setStatus("已重新载入任务文档。", "success");
      updatePreview();
    } catch (error) {
      setStatus(`重新载入失败：${error.message}。请关闭后从任务板重新打开。`, "error");
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
    state.chapter = String(note.chapter || state.chapter);
    if (note.pillar) state.context = note.pillar === "knowledge" ? "knowledge" : "life";
    el("note-editor-title").value = state.title;
    el("note-editor-tags").value = tagsToInput(state.tags);
    el("note-editor-text").value = state.body;
    syncChapterSelect();
    syncDeleteButton();
    setStatus("已重新载入磁盘版本。", "success");
    updatePreview();
    refreshTemplateStrip();
  } catch (error) {
    setStatus(`重新载入失败：${error.message}`, "error");
  }
}

async function classifyDraft() {
  if (state.task) return;
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
  handleTemplateInput();
  updatePreview();
  setStatus("已应用到正文；保存后写入。", "success");
}

function hidePolish() {
  polish = null;
  const panel = el("note-editor-polish");
  if (panel) panel.hidden = true;
}

function syncCompanionUi() {
  const dialog = el("note-editor-dialog");
  if (dialog) dialog.dataset.editorContext = state.context || "life";
  const companion = companionForEditor(state.context);
  const button = el("note-editor-role");
  if (button) {
    const label = `和${companion.title}聊聊`;
    button.textContent = label;
    button.setAttribute("aria-label", label);
    button.dataset.roleTitle = companion.title;
  }
  // 任务文档不做话题判类/润色；它们是任务板上的文档，不是散文。
  const classify = el("note-editor-classify");
  const polishButton = el("note-editor-polish-btn");
  if (classify) classify.hidden = Boolean(state.journal || state.task);
  if (polishButton) polishButton.hidden = Boolean(state.task);
  if (state.task) hidePolish();
}

function openCompanion(selection = null) {
  if (!selection) {
    const textarea = el("note-editor-text");
    const start = Number(textarea?.selectionStart);
    const end = Number(textarea?.selectionEnd);
    if (textarea && !textarea.hidden && Number.isFinite(start) && end > start) {
      const text = String(textarea.value || "").slice(start, end).trim();
      if (text) {
        selection = { text: text.slice(0, 4000), source: `${state.context}.editor` };
      }
    }
  }
  const companion = companionForEditor(state.context);
  const changed = dirty();
  const selectorsForCompanion = () => {
    const list = [];
    if (dirty()) return list;
    if (state.journal && state.journalDay) {
      list.push({ entry: "journal", day: state.journalDay });
    } else if (state.path) {
      list.push({ entry: "note", path: state.path });
    }
    return list;
  };
  const selectors = selectorsForCompanion();
  const quote = selection && String(selection.text || "").trim() ? selection : null;
  openRoleDrawer({
    roleId: companion.roleId,
    mode: companion.mode,
    entry: companion.entry,
    title: companion.title,
    host: "editor",
    selectors,
    getSelectors: selectorsForCompanion,
    getDraftText: () => (dirty() ? currentBody() : ""),
    selection: quote,
    prompt: quote ? "帮我看看这段。" : "",
    hint: changed
      ? "发送时会带上当前草稿；AI 只提议，采纳后才写入。"
      : "AI 只提议，采纳后才写入。",
    onError: (message) => setStatus(message, "error"),
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
