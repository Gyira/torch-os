// 四库前端（2026-10-21）：章节轨 + 条目列表 + 详情 + 跳转 Obsidian。
// 2026-10-26 起四库只读：这里不写任何 Markdown，新建/修改都跳回 Obsidian 原生编辑。
// 工作台完全独立；这里只读四库接口，不碰 time-module / workbench 的 DOM。
import {
  $, api, confirmAction, escapeHtml, formatRelativeDate, state as appState, toast,
} from "./core.js";
import { renderMarkdownDocument } from "./pillar-markdown.js";
import { bindOpsEvents, mountOps } from "./pillar-ops.js";
import { bindHabitEvents, mountHabits } from "./pillar-habits.js";
import { initOpsAI, loadOpsAI } from "./ops-ai.js";
import { openSteward } from "./pillar-steward.js";
import { archiveMarkup } from "./archive-markup.js";
import { shanghaiDay } from "./journal-model.js";
import { openJournalReview } from "./journal-ritual.js";
import {
  deleteNoteFile, initNoteEditor, invalidateChapterCache, openNoteEditor,
} from "./note-editor.js";
import { initShareLink, openDeepSeekAsk, openShareLinkDialog } from "./share-link.js";
import { initNotebookCreate, openNotebookCreateDialog } from "./notebook-create.js";
import { openRoleDrawer } from "./roles.js";
import {
  DEFAULT_LIFE_WRITE_CHAPTER, buildNotebookCards, canDeleteNotebook,
  customNotebookName, customNotebookPillar, defaultWriteChapter, greetingForHour,
  journalHeading, knowledgeHeroState, lifeHeroState, mergeNotesByRecency, noteCard,
  noteCards, notebookById, notebookForChapter, notebookTotal, notebooksFor,
  subchaptersFor,
} from "./notebook-model.js";
import {
  createObsidianNote, isObsidianHost, obsidianOpenUri, openNoteMessage, openTaskDocument,
} from "./task-open.js";

const GOAL_CHAPTERS = new Set(["goal_year", "goal_quarter", "goal_month"]);

// 与 backend/app/note_editor.py 的允许清单保持一致；这里只决定“是否显示编辑按钮”，
// 真正写入仍由后端再校验一次。
const WRITABLE_LIFE_CHAPTERS = new Set([
  "reflection", "insight_life", "insight_work", "insight_study",
  "review_week", "review_month", "goal_year", "goal_quarter", "goal_month",
  "project_plan", "work_review_project", "work_review_meeting",
  "work_review_pitfall", "essay",
]);
const WRITABLE_KNOWLEDGE_CHAPTERS = new Set([
  "inbox", "topic", "reading", "course", "concept",
  "method_work", "method_general", "output",
]);

function canWriteChapter(view, chapter) {
  // 自定义本子 = 库根目录下的文件夹；笔记和固定章节一样可应用内编辑。
  if (view === "life") {
    return WRITABLE_LIFE_CHAPTERS.has(chapter)
      || customNotebookPillar(chapter) === "life";
  }
  if (view === "knowledge") {
    return WRITABLE_KNOWLEDGE_CHAPTERS.has(chapter)
      || customNotebookPillar(chapter) === "knowledge";
  }
  return false;
}

/** 日记仍然只能改“今天”这一篇；过去的日记保持只读，回 Obsidian 改。 */
function isTodayJournal(note) {
  const day = String(note?.date || note?.path?.match(/(\d{4}-\d{2}-\d{2})\.md$/)?.[1] || "");
  return note?.chapter === "journal" && day === shanghaiDay();
}

const PILLAR_META = {
  life: {
    title: "人生路",
    subtitle: "把每天过成可回看的成长线索。",
    emptyHint: "例如：今天发生了什么，或此刻在想什么。",
    icon: "行",
    defaultChapter: DEFAULT_LIFE_WRITE_CHAPTER,
    quick: [
      { label: "写点什么", chapter: DEFAULT_LIFE_WRITE_CHAPTER, type: "essay" },
    ],
  },
  self: {
    title: "自我档案",
    subtitle: "把经历沉淀成「我是谁」。",
    emptyHint: "例如：一个刚冒出来的想法，或一段想记住的经历。",
    icon: "我",
    defaultChapter: "idea",
    quick: [
      { label: "记一条灵感", chapter: "idea", type: "idea" },
      { label: "添加一段记忆", chapter: "memory", type: "memory" },
      { label: "提炼一条原则", chapter: "distill_work", type: "principle" },
    ],
  },
  ops: {
    title: "生活运营",
    subtitle: "把身体、钱、事务照顾好，不让它们占用注意力。",
    emptyHint: "例如：今天的体重，或一笔午餐支出。",
    icon: "生",
    defaultChapter: "affairs",
    quick: [
      { label: "记体重", ops: "health" },
      { label: "记一笔", ops: "finance" },
      { label: "添加医疗记录", chapter: "medical", type: "medical" },
    ],
  },
  knowledge: {
    title: "知识库",
    subtitle: "先接住，再提炼，最后变成能用的东西。",
    emptyHint: "例如：一条读到的观点，或一个想试的方法。",
    icon: "知",
    defaultChapter: "inbox",
    quick: [
      { label: "快速收藏", chapter: "inbox", type: "inbox" },
      { label: "新建概念卡", chapter: "concept", type: "concept" },
      { label: "新建方法卡", chapter: "method_general", type: "method" },
    ],
  },
};

const state = {
  manifest: null,
  overviews: {},
  chapter: {},
  notebook: {},
  notes: {},
  search: {},
  searchSeq: {},
  // 每个库当前的页内阅读状态：{ note, backlinks, linkedTasks, doc, scrollY, focusPending }。
  reading: {},
};

async function ensureManifest() {
  if (!state.manifest) state.manifest = await api("/api/pillars");
  return state.manifest;
}

function deletedNotebooksFor(view) {
  return new Set(
    Array.isArray(state.overviews[view]?.deleted_notebooks)
      ? state.overviews[view].deleted_notebooks : []);
}

function hiddenChapterKeysFor(view) {
  const deleted = deletedNotebooksFor(view);
  const hidden = new Set();
  for (const notebook of notebooksFor(view)) {
    if (!deleted.has(notebook.key)) continue;
    for (const chapter of notebook.chapters) hidden.add(chapter);
  }
  return hidden;
}

function chapterList(view) {
  const pillar = state.manifest?.pillars?.find((item) => item.key === view);
  const hidden = hiddenChapterKeysFor(view);
  return (pillar?.chapters || []).filter((chapter) => !hidden.has(chapter.key));
}

function chapterTitle(view, key) {
  const value = String(key || "");
  const custom = customNotebookName(value);
  if (custom) return custom;
  const chapter = chapterList(view).find((item) => item.key === value);
  return chapter?.title || value;
}

function notebookCardsFor(view) {
  const overview = state.overviews[view] || {};
  return buildNotebookCards(
    view, overview.chapters || [], overview.custom_notebooks || [],
    overview.deleted_notebooks || []);
}

/** 本子定义里的 chapters 是 key 字符串；本子卡片里的 chapters 是 {key,...} 对象。 */
function defaultChapterForNotebook(notebook) {
  const first = notebook?.chapters?.[0];
  return typeof first === "string" ? first : String(first?.key || "");
}

function pillarGroups(view) {
  const pillar = state.manifest?.pillars?.find((item) => item.key === view);
  return Array.isArray(pillar?.groups) ? pillar.groups : [];
}

/**
 * 章节轨先按 manifest 的章节顺序排：遇到属于某个分组的第一个章节时，
 * 在该位置放一个分组行并收走它后面的同组章节；不属于任何分组的章节保持原位。
 * 纯函数：不读 DOM、不读 localStorage，Node 测试直接调用。
 */
export function buildRailItems(chapters = [], groups = []) {
  const chapterRows = Array.isArray(chapters) ? chapters : [];
  const groupRows = Array.isArray(groups) ? groups : [];
  const chapterKeys = new Set();
  for (const chapter of chapterRows) {
    if (chapter && typeof chapter.key === "string" && chapter.key) chapterKeys.add(chapter.key);
  }
  const groupByKey = new Map();
  const groupOfChapter = new Map();
  for (const group of groupRows) {
    if (!group || typeof group.key !== "string" || !group.key || groupByKey.has(group.key)) continue;
    groupByKey.set(group.key, group);
    for (const key of Array.isArray(group.chapters) ? group.chapters : []) {
      // 清单里没有的章节不渲染；同一章节只归第一个声明它的分组。
      if (chapterKeys.has(key) && !groupOfChapter.has(key)) groupOfChapter.set(key, group.key);
    }
  }
  const order = [];
  const groupFirstKeys = {};
  const chapterGroupKeys = {};
  const groupedChapters = new Map();
  const seenChapters = new Set();
  for (const chapter of chapterRows) {
    if (!chapter || typeof chapter.key !== "string" || !chapter.key || seenChapters.has(chapter.key)) continue;
    seenChapters.add(chapter.key);
    const groupKey = groupOfChapter.get(chapter.key);
    if (!groupKey) {
      order.push({ type: "chapter", chapter });
      continue;
    }
    chapterGroupKeys[chapter.key] = groupKey;
    if (!groupedChapters.has(groupKey)) {
      groupedChapters.set(groupKey, []);
      groupFirstKeys[groupKey] = chapter.key;
      order.push({ type: "group", groupKey });
    }
    groupedChapters.get(groupKey).push(chapter);
  }
  const items = order.map((entry) => {
    if (entry.type === "chapter") {
      const chapter = entry.chapter;
      return { type: "chapter", key: chapter.key, title: chapter.title || chapter.key, chapter, groupKey: "" };
    }
    const group = groupByKey.get(entry.groupKey);
    return {
      type: "group",
      key: group.key,
      title: group.title || group.key,
      chapters: groupedChapters.get(group.key),
      firstKey: groupFirstKeys[group.key],
    };
  });
  return { items, groupFirstKeys, chapterGroupKeys };
}

const RAIL_GROUPS_STORAGE_PREFIX = "torch:rail-groups:";

function railGroupsStorageKey(view) {
  return `${RAIL_GROUPS_STORAGE_PREFIX}${view}`;
}

/** 折叠过的分组 key；默认全部展开，localStorage 不可用时只是不持久化。 */
function readCollapsedRailGroups(view) {
  try {
    const raw = globalThis.localStorage?.getItem(railGroupsStorageKey(view));
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed.filter((key) => typeof key === "string") : []);
  } catch {
    return new Set();
  }
}

function writeCollapsedRailGroups(view, collapsed) {
  try {
    const keys = [...collapsed];
    if (keys.length) globalThis.localStorage?.setItem(railGroupsStorageKey(view), JSON.stringify(keys));
    else globalThis.localStorage?.removeItem(railGroupsStorageKey(view));
  } catch { /* private mode: 折叠只在本次渲染生效 */ }
}

function setRailGroupExpanded(view, groupKey, expanded) {
  const collapsed = readCollapsedRailGroups(view);
  if (expanded) collapsed.delete(groupKey);
  else collapsed.add(groupKey);
  writeCollapsedRailGroups(view, collapsed);
  const shell = $(`#${view}-view`);
  const toggle = Array.from(shell?.querySelectorAll?.("[data-rail-group-toggle]") || [])
    .find((node) => node.dataset.railGroupToggle === groupKey);
  if (!toggle) return;
  toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
  toggle.closest(".pillar-rail-group")?.classList.toggle("is-collapsed", !expanded);
  const panel = document.getElementById(toggle.getAttribute("aria-controls") || "");
  if (panel) panel.hidden = !expanded;
}

function railChapterMarkup(view, chapter, activeChapter, count) {
  const active = chapter.key === activeChapter;
  return `<button type="button" class="pillar-rail-item${active ? " is-active" : ""}" data-pillar-chapter="${escapeHtml(view)}" data-chapter="${escapeHtml(chapter.key)}">${escapeHtml(chapter.title || chapter.key)}<span>${count}</span></button>`;
}

function railGroupMarkup(view, group, collapsedGroups, activeChapter, countFor) {
  const collapsed = collapsedGroups.has(group.key);
  const panelId = `${view}-rail-group-${group.key}`;
  const total = group.chapters.reduce((sum, chapter) => sum + countFor(chapter.key), 0);
  return `<div class="pillar-rail-group${collapsed ? " is-collapsed" : ""}" data-rail-group="${escapeHtml(group.key)}">
    <button type="button" class="pillar-rail-group-toggle" data-rail-group-toggle="${escapeHtml(group.key)}" data-rail-group-view="${escapeHtml(view)}" aria-expanded="${collapsed ? "false" : "true"}" aria-controls="${escapeHtml(panelId)}">
      <span class="pillar-rail-group-title">${escapeHtml(group.title)}</span>
      <span class="pillar-rail-group-meta"><span class="pillar-rail-group-count">${total}</span><span class="pillar-rail-chevron" aria-hidden="true"></span></span>
    </button>
    <div class="pillar-rail-children" id="${escapeHtml(panelId)}"${collapsed ? " hidden" : ""}>
      ${group.chapters.map((chapter) => railChapterMarkup(view, chapter, activeChapter, countFor(chapter.key))).join("")}
    </div>
  </div>`;
}

function journalDayLabel(note) {
  const value = String(note.title || note.date || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return "";
  try {
    const date = new Date(`${value}T00:00:00Z`);
    const monthDay = new Intl.DateTimeFormat("zh-CN", {
      month: "long", day: "numeric", timeZone: "UTC",
    }).format(date);
    const weekday = new Intl.DateTimeFormat("zh-CN", {
      weekday: "short", timeZone: "UTC",
    }).format(date);
    return `${monthDay} ${weekday}`;
  } catch {
    return "";
  }
}

function noteRow(note) {
  const journal = note.chapter === "journal";
  const dayLabel = journal ? journalDayLabel(note) : "";
  const title = dayLabel ? `日记 · ${dayLabel}` : note.title;
  const excerpt = journal ? "打开这篇日记" : (note.excerpt || "");
  const meta = journal ? "" : `${escapeHtml(formatRelativeDate(note.date))}${note.tags?.length ? ` · ${escapeHtml(note.tags.slice(0, 3).join(" "))}` : ""}`;
  return `<button type="button" class="pillar-note-row" data-note-path="${escapeHtml(note.path)}">
    <span class="pillar-note-main">
      <span class="pillar-note-title">${escapeHtml(title)}</span>
      <span class="pillar-note-excerpt">${escapeHtml(excerpt)}</span>
    </span>
    ${meta ? `<span class="pillar-note-meta">${meta}</span>` : ""}
  </button>`;
}

function readerNoteTitle(note) {
  const journalLabel = note.chapter === "journal" ? journalDayLabel(note) : "";
  return journalLabel ? `日记 · ${journalLabel}` : note.title;
}

/** 页内阅读视图：渲染后的 Markdown 正文 + 目录 + 关联任务/反向链接，编辑统一回 Obsidian。 */
function readerMarkup(view, reading, railItems) {
  const { note } = reading;
  const meta = PILLAR_META[view];
  const displayTitle = readerNoteTitle(note);
  const journalLabel = note.chapter === "journal" ? journalDayLabel(note) : "";
  if (!reading.doc) {
    reading.doc = renderMarkdownDocument(note.body || "", { notePath: note.path });
  }
  const doc = reading.doc;
  const toc = doc.headings.filter((item) => item.level >= 2 && item.level <= 4);
  const tags = Array.isArray(note.tags) ? note.tags : [];
  const backlinks = Array.isArray(reading.backlinks) ? reading.backlinks : [];
  const linkedTasks = Array.isArray(reading.linkedTasks) ? reading.linkedTasks : [];
  const openLinkedCount = linkedTasks.filter((task) => !task.done).length;
  const canWrite = canWriteChapter(view, note.chapter) || isTodayJournal(note);
  // 日记有专用写入/撤销语义，不在这里删除；可写章节都可以软删除。
  const canDelete = canWrite && note.chapter !== "journal";
  const date = !journalLabel && note.date
    ? ` · ${escapeHtml(formatRelativeDate(note.date))}`
    : "";
  const tagsMarkup = tags.length
    ? `<div class="pillar-reader-tags">${tags.map((tag) => `<span class="pillar-reader-tag">${escapeHtml(tag)}</span>`).join("")}</div>`
    : "";
  const tocMarkup = toc.length >= 2
    ? `<aside class="pillar-reader-toc" aria-label="目录"><p class="eyebrow">目录</p>${toc.map((item) => `<a class="${item.level >= 3 ? "is-sub" : ""}" href="#${escapeHtml(item.id)}">${escapeHtml(item.text)}</a>`).join("")}</aside>`
    : "";
  const linkedSection = GOAL_CHAPTERS.has(note.chapter)
    ? `<section class="pillar-reader-section">
        <div class="pillar-reader-section-head"><h2>关联任务</h2><span class="meta">${openLinkedCount} 项未完成</span></div>
        ${linkedTasks.length ? `<ul class="pillar-reader-tasks">${linkedTasks.map((task) => `<li class="${task.done ? "is-done" : ""}">
          <span class="pillar-reader-task-title">${escapeHtml(task.title)}</span>
          <span class="meta">${task.done ? "已完成" : escapeHtml(task.date || "未排期")}</span>
          <button type="button" class="text-button" data-open-linked-task="${escapeHtml(task.key)}">在 Obsidian 打开</button>
        </li>`).join("")}</ul>` : `<p class="field-help">还没有任务关联到这个目标；可在资料导入时选择它，或直接在任务笔记 frontmatter 写「目标」。</p>`}
        <p class="field-help">任务来自时间管理的一任务一笔记；这里只读，编辑请到工作台或 Obsidian。</p>
      </section>`
    : "";
  const backlinksSection = backlinks.length
    ? `<section class="pillar-reader-section">
        <h2>反向链接</h2>
        <div class="pillar-reader-links">${backlinks.map((item) => `<button type="button" class="pillar-reader-link" data-backlink-path="${escapeHtml(item.path)}">${escapeHtml(item.title)}</button>`).join("")}</div>
      </section>`
    : "";
  return `
    <div class="pillar-layout is-reading">
      <nav class="pillar-rail" aria-label="${escapeHtml(meta.title)}章节">${railItems}</nav>
      <section class="pillar-reader" aria-label="${escapeHtml(displayTitle)}">
        <div class="pillar-reader-bar">
          <button type="button" class="pillar-reader-back" data-reader-back="${view}"><span aria-hidden="true">←</span> 返回列表</button>
          <p class="pillar-reader-crumbs">${escapeHtml(meta.title)} · ${escapeHtml(chapterTitle(view, note.chapter))}</p>
          <div class="pillar-reader-actions">
            ${canWrite ? `<button type="button" class="button primary" data-editor-edit="${view}">编辑</button>` : ""}
            <button type="button" class="${canWrite ? "small-button" : "button primary"}" data-reader-open-obsidian="${view}">在 Obsidian 打开并编辑</button>
            ${canDelete ? `<button type="button" class="small-button reject" data-reader-delete="${view}">删除</button>` : ""}
            <button type="button" class="small-button" data-reader-copy-wikilink="${view}" title="复制可直接粘贴到 Obsidian 的双链">复制双链</button>
            <button type="button" class="small-button" data-reader-copy-path="${view}">复制路径</button>
          </div>
        </div>
        <div class="pillar-reader-grid">
          <article class="pillar-reader-article">
            <header class="pillar-reader-head">
              <p class="eyebrow">${escapeHtml(chapterTitle(view, note.chapter))}</p>
              <h1 tabindex="-1">${escapeHtml(displayTitle)}</h1>
              <p class="pillar-reader-meta">${escapeHtml(note.path)}${date}</p>
              ${tagsMarkup}
            </header>
            <div class="md-body">${doc.html || `<p class="pillar-reader-empty">这篇笔记还没有正文。在 Obsidian 里写点什么吧。</p>`}</div>
            ${linkedSection}
            ${backlinksSection}
            <footer class="pillar-reader-foot">
              <p class="field-help">${canWrite ? "这篇可以应用内编辑；也可以随时回 Obsidian 改，保存时会用 revision 防止互相覆盖。" : "这是只读阅读视图；正文、标签和所在章节都在 Obsidian 里改。"}</p>
              ${canWrite ? `<button type="button" class="button primary" data-editor-edit="${view}">应用内编辑</button>` : ""}
              <button type="button" class="${canWrite ? "small-button" : "button primary"}" data-reader-open-obsidian="${view}">在 Obsidian 打开并编辑</button>
            </footer>
          </article>
          ${tocMarkup}
        </div>
      </section>
    </div>`;
}

function weekMarkup(week, today) {
  const days = Array.isArray(week) ? week : [];
  if (!days.length) return "";
  return `<div class="pillar-week life-week" role="group" aria-label="最近 7 天日记">
    ${days.map((day) => `<button type="button" class="pillar-week-day${day.exists ? " is-done" : ""}${day.date === today ? " is-today" : ""}" data-journal-day="${escapeHtml(day.date)}" data-journal-path="${escapeHtml(day.path || "")}" data-journal-legacy="${day.legacy ? "1" : "0"}" title="${escapeHtml(`${day.date} ${day.weekday}${day.exists ? " · 已写" : " · 未写"}`)}" aria-label="${escapeHtml(`${day.date} ${day.weekday}，${day.exists ? "已写日记" : "未写日记"}`)}">
      <span>${escapeHtml(String(day.weekday || "").replace("周", ""))}</span><i aria-hidden="true"></i>
    </button>`).join("")}
  </div>`;
}

function noteCardMarkup(view, note) {
  const card = noteCard(note, {
    pillar: view,
    chapterTitle: chapterTitle(view, note.chapter),
  });
  const tags = card.tags.map((tag) => `<span class="note-card-tag">#${escapeHtml(tag)}</span>`).join("");
  return `<button type="button" class="note-card${card.journal ? " is-journal" : ""}" data-note-path="${escapeHtml(card.path)}">
    <span class="note-card-top">
      <span class="note-card-date">${escapeHtml(card.dateLabel || "未标日期")}</span>
      ${tags ? `<span class="note-card-tags">${tags}</span>` : ""}
    </span>
    <span class="note-card-title">${escapeHtml(card.title)}</span>
    <span class="note-card-excerpt">${escapeHtml(card.excerpt || "还没有正文，点开继续写。")}</span>
    <span class="note-card-foot">
      <span>${escapeHtml(card.chapterTitle || "")}</span>
      <span class="note-card-open">打开 <i aria-hidden="true">→</i></span>
    </span>
  </button>`;
}

function notebookCardMarkup(view, notebook) {
  const latest = notebook.latest ? noteCard(notebook.latest, {
    pillar: view,
    chapterTitle: chapterTitle(view, notebook.latest.chapter),
  }) : null;
  return `<button type="button" class="notebook-card tone-${escapeHtml(notebook.tone || "slate")}${notebook.empty ? " is-empty" : ""}" data-notebook="${escapeHtml(notebook.key)}" data-notebook-view="${escapeHtml(view)}">
    <span class="notebook-card-top">
      <span class="notebook-icon" aria-hidden="true">${escapeHtml(notebook.icon || "本")}</span>
      <span class="notebook-count">${Number(notebook.count) || 0} 篇</span>
    </span>
    <span class="notebook-title">${escapeHtml(notebook.title)}</span>
    <span class="notebook-desc">${escapeHtml(notebook.description || "")}</span>
    <span class="notebook-latest">${latest ? `最近：${escapeHtml(latest.title)}` : "还没有记录，打开写下第一篇"}</span>
    <span class="notebook-open">打开本子 <i aria-hidden="true">→</i></span>
  </button>`;
}

function companionCardMarkup(view) {
  const knowledge = view === "knowledge";
  const roleId = knowledge ? "academic_mentor" : "journal_companion";
  const mode = knowledge ? "tutor" : "discuss";
  const entry = knowledge ? "knowledge.editor" : "life.editor";
  const title = knowledge ? "学术导师" : "写作伙伴";
  const copy = knowledge
    ? "把一篇回答拆成能学的路径，或者指出你还没想清楚的地方。"
    : "写不出来时，先聊两句。它读得到你最近写的东西。";
  return `<section class="pillar-card home-companion">
    <p class="eyebrow">AI · ${escapeHtml(title)}</p>
    <p class="home-card-title">${knowledge ? "让回答变成能用的笔记。" : "先聊两句，再动笔。"}</p>
    <p class="field-help">${escapeHtml(copy)}</p>
    <button type="button" class="pillar-quick-button" data-role-open="${escapeHtml(roleId)}" data-role-mode="${escapeHtml(mode)}" data-role-entry="${escapeHtml(entry)}" data-role-title="${escapeHtml(title)}">打开${escapeHtml(title)}</button>
  </section>`;
}

function lifeToolsCard(hero, { journalEnabled = true, reviewEnabled = true } = {}) {
  return `<section class="pillar-card home-tools">
    <p class="eyebrow">今天也可以</p>
    <div class="pillar-quick">
      <button type="button" class="pillar-quick-button" data-pillar-chapter="life" data-chapter="task">任务与待办</button>
      <button type="button" class="pillar-quick-button" data-pillar-chapter="life" data-chapter="habit">习惯追踪</button>
      ${reviewEnabled ? `<button type="button" class="pillar-quick-button" data-journal-review>今晚复盘 · 8 分钟</button>` : ""}
    </div>
    <p class="field-help">${journalEnabled
      ? (hero.written ? "今天已经写过一页，剩下的交给时间。" : "先写一句，今天就有了落点。")
      : "灵感来了就写点什么，写完再分类。"}</p>
  </section>`;
}

function lifeHomeMarkup() {
  const overview = state.overviews.life || {};
  const hero = lifeHeroState(overview, shanghaiDay());
  const deleted = deletedNotebooksFor("life");
  const journalEnabled = !deleted.has("journal");
  const reviewEnabled = journalEnabled && !deleted.has("reflection");
  const cards = notebookCardsFor("life");
  const recent = (overview.recent || [])
    .filter((note) => !["task", "habit"].includes(note.chapter))
    .slice(0, 6);
  const greeting = greetingForHour(new Date().getHours());
  return `<div class="pillar-home life-home">
    <section class="life-hero${journalEnabled ? "" : " is-solo"}" aria-labelledby="life-hero-title">
      <div class="life-hero-main">
        <p class="eyebrow">${escapeHtml(greeting)} · ${escapeHtml(hero.dayLabel)}</p>
        <h3 id="life-hero-title">${escapeHtml(hero.headline)}</h3>
        <p class="life-hero-copy">${escapeHtml(hero.copy)}</p>
        <div class="life-hero-actions">
          <button type="button" class="button primary hero-button" data-editor-new="life" data-new-chapter="${DEFAULT_LIFE_WRITE_CHAPTER}">✍ 写点什么</button>
          ${reviewEnabled ? `<button type="button" class="text-button" data-journal-review>今晚复盘</button>` : ""}
        </div>
        ${journalEnabled ? `<p class="life-streak">${hero.weekDone ? `近 7 天写了 ${hero.weekDone} 天` : "最近 7 天还没有记录"}${hero.streak ? ` · 连续 ${hero.streak} 天` : ""}</p>` : ""}
      </div>
      ${journalEnabled ? `<div class="life-today-card">
        <div class="life-today-head">
          <div><p class="eyebrow">今日日记</p><p class="life-today-state">${hero.written ? "已写" : "还没写"}</p></div>
          <span class="life-today-dot${hero.written ? " is-done" : ""}" aria-hidden="true"></span>
        </div>
        <p class="field-help">${hero.written ? `最近更新 ${escapeHtml(formatRelativeDate(hero.updatedAt))}` : "写一句今天的天气、心情，或者一件小事。"}</p>
        ${weekMarkup(hero.week, hero.day)}
        ${hero.todayPath
          ? `<button type="button" class="pillar-quick-button" data-journal-day="${escapeHtml(hero.day)}" data-journal-path="${escapeHtml(hero.todayPath)}" data-journal-legacy="0">打开今天的日记</button>`
          : `<button type="button" class="pillar-quick-button" data-editor-new="life" data-new-chapter="journal">${hero.written ? "继续写今天的日记" : "开始写今天的日记"}</button>`}
      </div>` : ""}
    </section>
    <div class="pillar-home-body">
      <main class="pillar-home-main">
        <section class="notebook-section">
          <div class="notebook-section-head">
            <div><p class="eyebrow">NOTEBOOKS</p><h3>我的本子</h3></div>
            <div class="notebook-section-actions">
              <span class="meta">${notebookTotal(cards)} 篇记录</span>
              <button type="button" class="button secondary small-create" data-notebook-create="life">＋ 新建本子</button>
            </div>
          </div>
          <div class="notebook-grid">
            ${cards.map((card) => notebookCardMarkup("life", card)).join("")}
            <button type="button" class="notebook-card notebook-create-card" data-notebook-create="life">
              <span class="notebook-create-plus" aria-hidden="true">＋</span>
              <span class="notebook-title">新建本子</span>
              <span class="notebook-desc">在 00_人生路 下建一个文件夹</span>
            </button>
          </div>
        </section>
        <section class="recent-section">
          <div class="notebook-section-head">
            <div><p class="eyebrow">RECENT</p><h3>最近写过</h3></div>
          </div>
          ${recent.length
            ? `<div class="note-card-grid">${recent.map((note) => noteCardMarkup("life", note)).join("")}</div>`
            : `<div class="pillar-home-empty"><p>还没有记录。</p><p class="field-help">灵感来了就先写，写完再决定放进哪个本子。</p><button type="button" class="button primary" data-editor-new="life" data-new-chapter="${DEFAULT_LIFE_WRITE_CHAPTER}">写点什么</button></div>`}
        </section>
      </main>
      <aside class="pillar-home-aside">
        ${companionCardMarkup("life")}
        ${lifeToolsCard(hero, { journalEnabled, reviewEnabled })}
      </aside>
    </div>
  </div>`;
}

function knowledgeHomeMarkup() {
  const overview = state.overviews.knowledge || {};
  const hero = knowledgeHeroState(overview);
  const cards = notebookCardsFor("knowledge");
  const recent = (overview.recent || []).slice(0, 6);
  return `<div class="pillar-home knowledge-home">
    <section class="knowledge-hero" aria-labelledby="knowledge-hero-title">
      <div class="knowledge-hero-main">
        <p class="eyebrow">ASK · 问答</p>
        <h3 id="knowledge-hero-title">今天想弄明白什么？</h3>
        <p class="knowledge-hero-copy">先打开 DeepSeek 提问；把值得留下的回答，用公开分享链接带回来。</p>
        <div class="knowledge-hero-actions">
          <button type="button" class="button primary hero-button" data-ask-deepseek>打开 DeepSeek 提问 ↗</button>
          <button type="button" class="button secondary" data-share-link>粘贴分享链接</button>
        </div>
        <form class="ask-inline-form" data-share-quick-form>
          <label class="sr-only" for="knowledge-share-url">DeepSeek 分享链接</label>
          <input id="knowledge-share-url" data-share-quick-url type="url" maxlength="2048" autocomplete="off" placeholder="https://chat.deepseek.com/share/...">
          <button type="submit" class="button secondary">取回并归档</button>
        </form>
        <p class="field-help">${escapeHtml(hero.copy)}</p>
      </div>
      <div class="knowledge-steps">
        <p class="eyebrow">HOW IT WORKS</p>
        <ol>
          <li><span>1</span>在 DeepSeek 网页版问清楚</li>
          <li><span>2</span>复制这条回答的公开分享链接</li>
          <li><span>3</span>粘贴回来，预览后归档或让导师整理</li>
        </ol>
      </div>
    </section>
    <div class="pillar-home-body">
      <main class="pillar-home-main">
        <section class="notebook-section">
          <div class="notebook-section-head">
            <div><p class="eyebrow">NOTEBOOKS</p><h3>知识本子</h3></div>
            <div class="notebook-section-actions">
              <span class="meta">${notebookTotal(cards)} 篇记录</span>
              <button type="button" class="button secondary small-create" data-notebook-create="knowledge">＋ 新建本子</button>
            </div>
          </div>
          <div class="notebook-grid">
            ${cards.map((card) => notebookCardMarkup("knowledge", card)).join("")}
            <button type="button" class="notebook-card notebook-create-card" data-notebook-create="knowledge">
              <span class="notebook-create-plus" aria-hidden="true">＋</span>
              <span class="notebook-title">新建本子</span>
              <span class="notebook-desc">在 03_知识库 下建一个文件夹</span>
            </button>
          </div>
        </section>
        <section class="recent-section">
          <div class="notebook-section-head">
            <div><p class="eyebrow">RECENT</p><h3>最近入库</h3></div>
          </div>
          ${recent.length
            ? `<div class="note-card-grid">${recent.map((note) => noteCardMarkup("knowledge", note)).join("")}</div>`
            : `<div class="pillar-home-empty"><p>还没有知识笔记。</p><p class="field-help">先在 DeepSeek 问一个真问题，再把回答带回来。</p><button type="button" class="button primary" data-ask-deepseek>去提问</button></div>`}
        </section>
      </main>
      <aside class="pillar-home-aside">
        ${companionCardMarkup("knowledge")}
        <section class="pillar-card home-inbox">
          <p class="eyebrow">收件箱</p>
          <p class="home-card-stat">${Number(overview.inbox_count) || 0}<small> 条待分流</small></p>
          <p class="field-help">先接住，再决定它属于哪个本子。</p>
          <button type="button" class="pillar-quick-button" data-notebook="inbox">打开收件箱</button>
        </section>
      </aside>
    </div>
  </div>`;
}

function notebookPageMarkup(view) {
  const overview = state.overviews[view] || {};
  const chapter = state.chapter[view] || "";
  const notebook = notebookById(view, state.notebook[view] || "");
  const notebookCard = notebook
    ? notebookCardsFor(view).find((card) => card.key === notebook.key)
    : null;
  const searching = Boolean(state.search[view]?.trim());
  const notes = state.notes[view] || [];
  const title = searching
    ? `搜索「${state.search[view].trim()}」`
    : (notebook?.title || (chapter ? chapterTitle(view, chapter) : "全部笔记"));
  const eyebrow = searching ? "SEARCH" : (notebook?.eyebrow || "CHAPTER");
  const icon = notebook?.icon || "·";
  const tone = notebook?.tone || "slate";
  const description = searching
    ? "在本库所有本子里查找标题、正文和标签。"
    : (notebook?.description || "这个章节里的全部记录。");
  const chapters = notebook
    ? subchaptersFor(view, notebook.key, [
      ...(overview.chapters || []),
      ...(overview.custom_notebooks || []),
    ])
    : [];
  const total = notebookCard?.count ?? notes.length;
  const newChapter = chapter || defaultChapterForNotebook(notebook)
    || defaultWriteChapter(view);
  const unit = view === "life" ? "人生路" : "知识库";
  const direct = !notebook && Boolean(chapter);
  const isHabit = view === "life" && chapter === "habit";
  const isTask = view === "life" && chapter === "task";
  const isUtility = isHabit || isTask;
  const isInbox = view === "knowledge" && (chapter === "inbox" || notebook?.key === "inbox");
  const isJournal = notebook?.key === "journal";
  const hero = view === "life" ? lifeHeroState(overview, shanghaiDay()) : null;
  const lifeDeleted = deletedNotebooksFor("life");
  const lifeJournalEnabled = !lifeDeleted.has("journal");
  const lifeReviewEnabled = lifeJournalEnabled && !lifeDeleted.has("reflection");
  return `<div class="pillar-home notebook-page">
    <div class="pillar-home-body">
      <main class="pillar-home-main notebook-main">
        <header class="notebook-page-head">
          <div class="notebook-page-title">
            <button type="button" class="pillar-reader-back" data-notebook-back="${escapeHtml(view)}"><span aria-hidden="true">←</span> 全部本子</button>
            <span class="notebook-page-icon tone-${escapeHtml(tone)}" aria-hidden="true">${escapeHtml(icon)}</span>
            <div class="notebook-page-heading">
              <p class="eyebrow">${escapeHtml(unit)} · ${escapeHtml(eyebrow)}</p>
              <h3 tabindex="-1">${escapeHtml(title)}</h3>
              <p class="field-help">${escapeHtml(description)}${searching || isHabit ? "" : ` · ${total} 篇`}</p>
            </div>
          </div>
          <div class="notebook-page-actions">
            ${isUtility ? "" : `<button type="button" class="button primary" data-editor-new="${escapeHtml(view)}" data-new-chapter="${escapeHtml(newChapter)}">＋ 写一条</button>
            <button type="button" class="text-button" data-pillar-new="${escapeHtml(view)}" data-new-chapter="${escapeHtml(newChapter)}">Obsidian</button>
            ${canDeleteNotebook(view, notebook) ? `<button type="button" class="small-button reject" data-notebook-delete="${escapeHtml(notebook.key)}" data-notebook-delete-pillar="${escapeHtml(view)}">删除本子</button>` : ""}`}
          </div>
        </header>
        ${chapters.length > 1 && !searching ? `<div class="notebook-chips" role="tablist" aria-label="本子内章节">
          <button type="button" class="notebook-chip${chapter ? "" : " is-active"}" data-pillar-chapter="${escapeHtml(view)}" data-chapter="">全部 <span>${Number(total) || 0}</span></button>
          ${chapters.map((item) => `<button type="button" class="notebook-chip${item.key === chapter ? " is-active" : ""}" data-pillar-chapter="${escapeHtml(view)}" data-chapter="${escapeHtml(item.key)}">${escapeHtml(item.title)} <span>${item.count}</span></button>`).join("")}
        </div>` : ""}
        ${isJournal && !searching ? `<section class="journal-notebook-banner">
          <div><p class="eyebrow">TODAY</p><h3>${escapeHtml(journalHeading(shanghaiDay()))}</h3><p class="field-help">${hero.written ? "今天已经写过一页，可以继续补充。" : "今天还没写。先写一句，今天就有了落点。"}</p></div>
          <button type="button" class="button primary" data-editor-new="life" data-new-chapter="journal">${hero.written ? "继续写今天的日记" : "写今天的日记"}</button>
        </section>` : ""}
        ${direct && chapter === "task" ? `<p class="notebook-hint">任务由工作台管理；这里浏览任务笔记，编辑请回工作台或 Obsidian。</p>` : ""}
        ${isInbox ? `<div class="pillar-suggestions" data-inbox-suggestions></div>` : ""}
        ${isHabit ? `<div class="pillar-habits" data-habit-panel></div>` : ""}
        ${isHabit ? "" : (notes.length
          ? `<div class="note-card-grid notebook-note-grid">${notes.map((note) => noteCardMarkup(view, note)).join("")}</div>`
          : `<div class="pillar-home-empty notebook-empty"><p>${searching ? "没有匹配的记录。" : isTask ? "任务由工作台管理。" : "这个本子还没有记录。"}</p><p class="field-help">${searching ? "换个关键词，或清空搜索看看全部。" : isTask ? "这里浏览任务笔记；新建和编辑请到工作台或 Obsidian。" : "写下第一篇，本子就开始有自己的样子。"}</p>${searching || isTask ? "" : `<button type="button" class="button primary" data-editor-new="${escapeHtml(view)}" data-new-chapter="${escapeHtml(newChapter)}">写第一篇</button>`}</div>`)}
      </main>
      <aside class="pillar-home-aside">
        ${companionCardMarkup(view)}
        ${view === "life" ? lifeToolsCard(hero, {
          journalEnabled: lifeJournalEnabled,
          reviewEnabled: lifeReviewEnabled,
        }) : ""}
        <section class="pillar-card home-stats">
          <p class="eyebrow">本子</p>
          <p class="home-card-stat">${Number(total) || 0}<small> 篇</small></p>
          <p class="field-help">${notebook ? escapeHtml(notebook.description || "") : "当前章节的笔记。"}</p>
          ${chapters.length > 1 ? `<div class="home-chip-list">${chapters.map((item) => `<span>${escapeHtml(item.title)} ${item.count}</span>`).join("")}</div>` : ""}
        </section>
        ${view === "life" && lifeJournalEnabled && (isJournal || notebook?.key === "reflection") ? `<section class="pillar-card home-week-card">
          <p class="eyebrow">最近 7 天</p>
          ${weekMarkup(hero.week, hero.day)}
          <p class="field-help">近 7 天写了 ${hero.weekDone} 天${hero.streak ? ` · 连续 ${hero.streak} 天` : ""}。</p>
        </section>` : ""}
        ${view === "knowledge" ? `<section class="pillar-card home-inbox">
          <p class="eyebrow">收件箱</p>
          <p class="home-card-stat">${Number(overview.inbox_count) || 0}<small> 条待分流</small></p>
          <button type="button" class="pillar-quick-button" data-notebook="inbox">打开收件箱</button>
        </section>` : ""}
      </aside>
    </div>
  </div>`;
}

function renderShell(view) {
  const root = $(`#${view}-view .pillar-shell`);
  if (!root) return null;
  const meta = PILLAR_META[view];
  const overview = state.overviews[view];
  const chapters = chapterList(view);
  const chapter = state.chapter[view] || "";
  const notes = state.notes[view] || overview?.recent || [];
  const searching = Boolean(state.search[view]?.trim());
  const week = overview?.today?.week || [];
  const rail = buildRailItems(chapters, pillarGroups(view));
  const collapsedGroups = readCollapsedRailGroups(view);
  const activeGroupKey = chapter ? (rail.chapterGroupKeys[chapter] || "") : "";
  // 当前章节永远不能藏在折叠分组里：加载任意视图/章节时自动展开它的分组。
  if (activeGroupKey && collapsedGroups.delete(activeGroupKey)) {
    writeCollapsedRailGroups(view, collapsedGroups);
  }
  const chapterCounts = new Map((overview?.chapters || []).map((row) => [row.key, Number(row.count) || 0]));
  const countFor = (key) => chapterCounts.get(key) ?? 0;
  const railItems = rail.items.map((item) => (item.type === "group"
    ? railGroupMarkup(view, item, collapsedGroups, chapter, countFor)
    : railChapterMarkup(view, item, chapter, countFor(item.key)))).join("");
  const activeNotebook = notebookById(view, state.notebook[view] || "");
  const headNewChapter = chapter || defaultChapterForNotebook(activeNotebook)
    || defaultWriteChapter(view);
  const lifeHome = view === "life" && !chapter && !activeNotebook;
  const headWriteLabel = lifeHome ? "✍ 写点什么" : "＋ 写一条";
  const headWriteTitle = lifeHome
    ? "记下灵感；默认先放随笔，保存前也可以改本子"
    : "应用内写一条（Markdown 工具栏+预览）";
  const headUtility = view === "life" && ["task", "habit"].includes(chapter);
  const headHtml = `
    <header class="pillar-head">
      <div class="pillar-head-main">
        <span class="pillar-icon" aria-hidden="true">${escapeHtml(meta.icon)}</span>
        <div>
          <p class="eyebrow">四库 · ${escapeHtml(meta.title)}</p>
          <h2>${escapeHtml(meta.title)}</h2>
          <p class="pillar-subtitle">${escapeHtml(meta.subtitle)}</p>
        </div>
      </div>
      <div class="pillar-head-actions">
        <label class="pillar-search"><span class="sr-only">搜索${escapeHtml(meta.title)}</span>
          <input type="search" data-pillar-search="${view}" value="${escapeHtml(state.search[view] || "")}" placeholder="搜索标题、正文、标签">
        </label>
        ${view === "knowledge" ? `<button type="button" class="button secondary" data-share-link title="DeepSeek 问答：外链打开，分享链接取回归档">问答</button>` : ""}
        <button type="button" class="button secondary" data-steward-open="${view}" title="提案与健康检查（只读）">管家</button>
        ${view === "life" || view === "knowledge"
          ? (headUtility ? "" : `<button type="button" class="button primary" data-editor-new="${view}" data-new-chapter="${escapeHtml(headNewChapter)}" title="${escapeHtml(headWriteTitle)}" aria-keyshortcuts="N">${escapeHtml(headWriteLabel)}</button>
             <button type="button" class="text-button" data-pillar-new="${view}" data-new-chapter="${escapeHtml(headNewChapter)}" title="在 Obsidian 新建笔记">Obsidian</button>`)
          : `<button type="button" class="button primary" data-pillar-new="${view}" title="在 Obsidian 新建笔记（按 N）" aria-keyshortcuts="N">＋ 在 Obsidian 新建</button>`}
      </div>
    </header>`;
  const reading = state.reading[view];
  // 自我档案的忆灵面板只在列表/总览显示；进入阅读器时让出整页空间。
  const selfHub = document.getElementById("self-hub");
  if (selfHub) selfHub.hidden = view === "self" && Boolean(reading);
  // 阅读视图单独放宽容器：列表/总览 1680，文章阅读用更宽的 1840。
  root.classList.toggle("is-reading", Boolean(reading));
  if (reading) {
    root.innerHTML = `${headHtml}${readerMarkup(view, reading, railItems)}`;
    if (reading.focusPending) {
      reading.focusPending = false;
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
      root.querySelector(".pillar-reader-head h1")?.focus?.({ preventScroll: true });
    }
    return root;
  }
  if (view === "life" || view === "knowledge") {
    const home = !activeNotebook && !chapter && !searching;
    root.innerHTML = `${headHtml}${home
      ? (view === "life" ? lifeHomeMarkup() : knowledgeHomeMarkup())
      : notebookPageMarkup(view)}`;
    if (view === "knowledge" && (chapter === "inbox" || activeNotebook?.key === "inbox")) {
      mountInboxSuggestions().catch((error) => toast(error.message, "error"));
    }
    if (view === "life" && chapter === "habit") {
      mountHabits().catch((error) => toast(error.message, "error"));
    }
    return root;
  }
  // 自我档案总览不是「一堆笔记的最近列表」，而是一份档案：封面 / 目录 / 时间线。
  const archiveView = view === "self" && !chapter && !searching;
  const hideList = (view === "ops" && !chapter && !notes.length && !searching)
    || (view === "ops" && (chapter === "health" || chapter === "finance") && !notes.length && !searching)
    || (view === "life" && chapter === "habit" && !notes.length && !searching);
  root.innerHTML = `${headHtml}
    ${view === "ops" ? `<div class="ops-ai-slot" data-ops-ai></div>` : ""}
    <div class="pillar-layout">
      <nav class="pillar-rail" aria-label="${escapeHtml(meta.title)}章节">
        <button type="button" class="pillar-rail-item${chapter ? "" : " is-active"}" data-pillar-chapter="${view}" data-chapter="">总览
          <span>${overview?.count ?? 0}</span></button>
        ${railItems}
      </nav>
      <section class="pillar-content" aria-live="polite">
        <div class="pillar-content-head">
          <div>
            <p class="eyebrow">${escapeHtml(chapter ? chapterTitle(view, chapter) : archiveView ? "档案" : "最近")}</p>
            <h3>${escapeHtml(searching ? `搜索「${state.search[view]}」` : chapter ? chapterTitle(view, chapter) : archiveView ? "档案总览" : "最近记录")}</h3>
          </div>
          ${chapter ? `<button type="button" class="text-button" data-pillar-chapter="${view}" data-chapter="">返回总览</button>` : ""}
        </div>
        ${view === "ops" ? (chapter === "health" || chapter === "finance"
          ? `<div class="pillar-ops-slot" data-ops-table="${chapter}"></div>`
          : `<div class="pillar-ops-slot" data-ops-dashboard></div>`) : ""}
        ${view === "knowledge" && chapter === "inbox" ? `<div class="pillar-suggestions" data-inbox-suggestions></div>` : ""}
        ${view === "life" && chapter === "habit" ? `<div class="pillar-habits" data-habit-panel></div>` : ""}
        ${archiveView ? archiveMarkup({
          identity: overview?.identity || {},
          chapters: overview?.chapters || [],
          timeline: overview?.timeline || [],
          noteCount: overview?.count || 0,
        }) : (hideList ? "" : `<div class="pillar-list">
          ${notes.length ? notes.map(noteRow).join("") : `<div class="pillar-empty">
            <p>${searching ? "没有匹配的记录。" : "这里还没有记录。"}</p>
            <p class="field-help">${escapeHtml(searching ? "换个关键词，或清空搜索看看全部。" : meta.emptyHint)}</p>
            ${view === "life" || view === "knowledge"
              ? `<button type="button" class="button secondary" data-editor-new="${view}" data-new-chapter="${escapeHtml(chapter || defaultWriteChapter(view))}">＋ 写一条</button>`
              : `<button type="button" class="button secondary" data-pillar-new="${view}" data-new-chapter="${escapeHtml(chapter)}">＋ 在 Obsidian 新建</button>`}
          </div>`}
        </div>`)}
      </section>
      <aside class="pillar-aside">
        <section class="pillar-card">
          <p class="eyebrow">${view === "life" || view === "knowledge" ? "写一条" : "在 Obsidian 写"}</p>
          <div class="pillar-quick">
            ${meta.quick.map((item) => item.ops
              ? `<button type="button" class="pillar-quick-button" data-ops-open="${escapeHtml(item.ops)}">${escapeHtml(item.label)}</button>`
              : (view === "life" || view === "knowledge") && !["journal", "habit"].includes(item.chapter)
                ? `<button type="button" class="pillar-quick-button" data-editor-new="${view}" data-new-chapter="${escapeHtml(item.chapter)}" data-new-type="${escapeHtml(item.type)}">${escapeHtml(item.label)}</button>`
                : `<button type="button" class="pillar-quick-button" data-pillar-new="${view}" data-new-chapter="${item.chapter}" data-new-type="${item.type}">${escapeHtml(item.label)}</button>`).join("")}
          </div>
          <p class="field-help">${view === "life" || view === "knowledge"
            ? "应用内可直接写允许章节；任务、习惯、日记和身份仍走各自的专用入口。"
            : "这里只读；点按钮会在 Obsidian 里新建/打开对应笔记。"}</p>
        </section>
        ${view === "self" && overview?.identity && !archiveView ? `<section class="pillar-card pillar-identity-card">
          <p class="eyebrow">身份卡</p>
          <p class="pillar-stat">${escapeHtml(overview.identity.name || "还没有填写姓名")}</p>
          <p class="pillar-subtitle">${escapeHtml(overview.identity.motto || "还没有座右铭")}</p>
          ${overview.identity.worldview ? `<p class="field-help">三观：${escapeHtml(overview.identity.worldview.slice(0, 90))}</p>` : ""}
          <button type="button" class="text-button" data-open-identity="${escapeHtml(overview.identity.path || "")}">${overview.identity.exists ? "在 Obsidian 打开身份卡" : "去写身份卡"}</button>
        </section>` : ""}
        ${view === "life" && overview?.today ? `<section class="pillar-card">
          <p class="eyebrow">今天</p>
          <p class="pillar-stat">${overview.today.journal_written ? "日记已写" : "日记还没写"}</p>
          <p class="field-help">${overview.today.journal_written ? `最近更新 ${escapeHtml(formatRelativeDate(overview.today.journal_updated_at))}` : "先写一句也算记录。"}</p>
          <div class="pillar-quick">
            <button type="button" class="pillar-quick-button" data-journal-review>今晚复盘（8 分钟）</button>
            <button type="button" class="pillar-quick-button" data-editor-new="life" data-new-chapter="${DEFAULT_LIFE_WRITE_CHAPTER}">写点什么</button>
            <button type="button" class="pillar-quick-button" data-review="weekly">打开本周复盘</button>
            <button type="button" class="pillar-quick-button" data-review="monthly">打开本月复盘</button>
          </div>
        </section>` : ""}
        ${view === "life" && week.length ? `<section class="pillar-card">
          <p class="eyebrow">最近 7 天</p>
          <div class="pillar-week" role="group" aria-label="最近 7 天日记">
            ${week.map((day) => `<button type="button" class="pillar-week-day${day.exists ? " is-done" : ""}${day.date === overview?.today?.date ? " is-today" : ""}" data-journal-day="${escapeHtml(day.date)}" data-journal-path="${escapeHtml(day.path || "")}" data-journal-legacy="${day.legacy ? "1" : "0"}" title="${escapeHtml(`${day.date} ${day.weekday}${day.exists ? " · 已写" : " · 未写"}`)}" aria-label="${escapeHtml(`${day.date} ${day.weekday}，${day.exists ? "已写日记" : "未写日记"}`)}">
              <span>${escapeHtml(String(day.weekday || "").replace("周", ""))}</span><i aria-hidden="true"></i>
            </button>`).join("")}
          </div>
          <p class="field-help">近 7 天写了 ${week.filter((day) => day.exists).length} 天；点有记录的日期打开当天日记。</p>
        </section>` : ""}
        ${view === "knowledge" ? `<section class="pillar-card">
          <p class="eyebrow">DeepSeek 问答</p>
          <p class="field-help">在浏览器问完，复制公开分享链接回来；可归档成 md，或让学术导师整理成提案。</p>
          <button type="button" class="pillar-quick-button" data-share-link>打开问答 / 归档分享</button>
        </section>` : ""}
        ${view === "knowledge" && overview ? `<section class="pillar-card">
          <p class="eyebrow">收件箱</p>
          <p class="pillar-stat">${overview.inbox_count ?? 0} 条待分流</p>
          <button type="button" class="text-button" data-pillar-chapter="${view}" data-chapter="inbox">去看看</button>
        </section>` : ""}
        <section class="pillar-card pillar-card-quiet">
          <p class="eyebrow">说明</p>
          <p class="field-help">${escapeHtml(meta.subtitle)}${view === "life" || view === "knowledge"
            ? "允许章节可在应用内写（保存用 revision 防覆盖）；日记、任务、习惯、身份和生活运营仍走各自入口。"
            : "这里只读浏览；新建与修改都在 Obsidian 里完成。"}</p>
        </section>
      </aside>
    </div>`;
  if (view === "ops") {
    mountOps(view).catch((error) => toast(error.message, "error"));
    loadOpsAI();
  }
  if (view === "knowledge" && chapter === "inbox") {
    mountInboxSuggestions().catch((error) => toast(error.message, "error"));
  }
  if (view === "life" && chapter === "habit") {
    mountHabits().catch((error) => toast(error.message, "error"));
  }
  return root;
}

const CONFIDENCE_LABEL = { high: "高置信", medium: "可参考", low: "低置信" };

async function mountInboxSuggestions() {
  const host = document.querySelector("#knowledge-view [data-inbox-suggestions]");
  if (!host) return;
  const payload = await api("/api/steward/inbox-suggestions");
  const items = (payload.items || []).filter((item) => item.chapter !== "inbox");
  if (!items.length) { host.innerHTML = ""; return; }
  const highCount = items.filter((item) => item.confidence === "high").length;
  const groups = new Map();
  for (const item of items) {
    const target = `${item.pillar_title} / ${item.chapter_title}`;
    if (!groups.has(target)) groups.set(target, []);
    groups.get(target).push(item);
  }
  host.innerHTML = `<section class="pillar-suggestion-card">
    <div class="pillar-suggestion-head">
      <div>
        <p class="eyebrow">整理建议 · ${items.length} 条</p>
        <p class="field-help">这里只读：建议只是提示，移动/改名请到 Obsidian 里做。</p>
      </div>
    </div>
    ${[...groups.entries()].map(([target, group]) => `<div class="pillar-suggestion-group">
      <p class="pillar-suggestion-group-title"><span>${escapeHtml(target)}</span><span>${group.length} 条</span></p>
      ${group.map((item) => `<div class="pillar-suggestion-row" data-inbox-row="${escapeHtml(item.proposal_id)}" data-inbox-target="${escapeHtml(target)}" data-confidence="${escapeHtml(item.confidence)}">
        <div>
          <p class="pillar-suggestion-title"><strong>${escapeHtml(item.title)}</strong><span class="pillar-confidence ${escapeHtml(item.confidence)}">${escapeHtml(CONFIDENCE_LABEL[item.confidence] || item.confidence)}</span></p>
          <p class="field-help">${escapeHtml(item.reason)}</p>
        </div>
        <div class="pillar-suggestion-actions">
          <button type="button" class="small-button" data-obsidian-note-path="${escapeHtml(item.path)}" data-obsidian-pillar="knowledge">在 Obsidian 打开</button>
        </div>
      </div>`).join("")}
    </div>`).join("")}
  </section>`;
  // 已进入建议卡的条目不再在下方列表重复出现；想读原文用「打开」。
  const suggestedPaths = new Set(items.map((item) => item.path));
  document.querySelectorAll("#knowledge-view .pillar-note-row[data-note-path], #knowledge-view .note-card[data-note-path]").forEach((row) => {
    if (suggestedPaths.has(row.dataset.notePath)) row.hidden = true;
  });
}

async function loadPillar(view, { keepChapter = false } = {}) {
  await ensureManifest();
  if (!keepChapter) {
    // 模块切换时保留用户上次看的章节；首次进入看总览。
    if (!(view in state.chapter)) state.chapter[view] = "";
  }
  if (!(view in state.notebook)) state.notebook[view] = "";
  const overview = await api(`/api/pillars/${view}/overview`);
  state.overviews[view] = overview;
  let notebook = notebookById(view, state.notebook[view] || "");
  if (!notebook) state.notebook[view] = "";
  // 自定义本子可能在 Obsidian 里被删掉：回首页，不要让用户停在一个不存在的本子里。
  if (notebook?.custom
      && !(overview.custom_notebooks || []).some((row) => row.key === notebook.key)) {
    state.notebook[view] = "";
    state.chapter[view] = "";
    notebook = null;
  }
  // 内置本子被删除后从首页隐藏；停在这个本子的阅读/列表状态一并退出。
  const deletedNotebooks = deletedNotebooksFor(view);
  if (notebook && deletedNotebooks.has(notebook.key)) {
    state.notebook[view] = "";
    state.chapter[view] = "";
    state.reading[view] = null;
    notebook = null;
  }
  const hiddenChapters = hiddenChapterKeysFor(view);
  if (state.chapter[view] && hiddenChapters.has(state.chapter[view])) {
    state.chapter[view] = "";
  }
  if (state.reading[view]?.note && hiddenChapters.has(state.reading[view].note.chapter)) {
    state.reading[view] = null;
  }
  if (state.search[view]) {
    state.notes[view] = (await api(`/api/search?pillar=${view}&q=${encodeURIComponent(state.search[view])}`)).items;
  } else if (notebook && state.chapter[view]) {
    state.notes[view] = (await api(`/api/pillars/${view}/notes?chapter=${encodeURIComponent(state.chapter[view])}`)).items;
  } else if (notebook) {
    // 一个本子可能由多个章节组成（反思/周复盘/月复盘）。分别读取后按日期合并，
    // 用户看到的是“一个本子”，而不是三个孤立章节。
    const groups = await Promise.all(notebook.chapters.map((key) => api(
      `/api/pillars/${view}/notes?chapter=${encodeURIComponent(key)}`).then((payload) => payload.items)));
    state.notes[view] = mergeNotesByRecency(groups);
  } else if (state.chapter[view]) {
    state.notes[view] = (await api(`/api/pillars/${view}/notes?chapter=${encodeURIComponent(state.chapter[view])}`)).items;
  } else {
    state.notes[view] = overview.recent || [];
  }
  renderShell(view);
}

function bindPillarEvents() {
  document.addEventListener("click", async (event) => {
    const readerBack = event.target.closest?.("[data-reader-back]");
    if (readerBack) {
      closeReader(readerBack.dataset.readerBack || appState.view);
      return;
    }
    const readerOpen = event.target.closest?.("[data-reader-open-obsidian]");
    if (readerOpen) {
      const view = readerOpen.dataset.readerOpenObsidian || appState.view;
      const reading = state.reading[view];
      if (reading) openVaultNote(state.overviews[view]?.vault_name || "", reading.note.path);
      return;
    }
    const readerDelete = event.target.closest?.("[data-reader-delete]");
    if (readerDelete) {
      const view = readerDelete.dataset.readerDelete || appState.view;
      const reading = state.reading[view];
      if (!reading?.note) return;
      readerDelete.disabled = true;
      try {
        const result = await deleteNoteFile(reading.note);
        if (!result) return;
        state.reading[view] = null;
        await loadPillar(view);
        toast("已删除，可在 .torch-os/笔记回收站 找回。", "success", { timeout: 8000 });
      } catch (error) {
        if (error.status === 409) {
          toast("这篇笔记已在别处修改，未删除。请刷新后重试。", "warning");
        } else if (error.status === 404) {
          toast("这篇笔记已经不在了，可能已在 Obsidian 删除。", "warning");
        } else {
          toast(`删除失败：${error.message}`, "error");
        }
      } finally {
        readerDelete.disabled = false;
      }
      return;
    }
    const readerCopyLink = event.target.closest?.("[data-reader-copy-wikilink]");
    if (readerCopyLink) {
      const view = readerCopyLink.dataset.readerCopyWikilink || appState.view;
      const reading = state.reading[view];
      if (!reading) return;
      const copied = await copyText(readerWikiLink(reading.note, readerNoteTitle(reading.note)));
      toast(copied ? "已复制双链，可直接粘贴到 Obsidian。" : "复制失败，请手动复制路径。", copied ? "info" : "warning");
      return;
    }
    const readerCopyPath = event.target.closest?.("[data-reader-copy-path]");
    if (readerCopyPath) {
      const view = readerCopyPath.dataset.readerCopyPath || appState.view;
      const reading = state.reading[view];
      if (!reading) return;
      const copied = await copyText(reading.note.path);
      toast(copied ? "已复制路径。" : "复制失败，请手动选择路径。", copied ? "info" : "warning");
      return;
    }
    const wiki = event.target.closest?.("[data-md-note]");
    if (wiki) {
      await openWikiTarget(wiki.dataset.mdNote);
      return;
    }
    const linkedTask = event.target.closest?.("[data-open-linked-task]");
    if (linkedTask) {
      const view = appState.view;
      const reading = state.reading[view];
      const task = reading?.linkedTasks?.find((item) => item.key === linkedTask.dataset.openLinkedTask);
      if (!task) return;
      if (!openTaskDocument(task, { vault_name: state.overviews[view]?.vault_name || "" })) {
        toast("没有可打开的任务文档，请刷新后重试。", "warning");
      }
      return;
    }
    const backlink = event.target.closest?.("[data-backlink-path]");
    if (backlink) {
      await openNote(backlink.dataset.backlinkPath);
      return;
    }
    const obsidianNote = event.target.closest?.("[data-obsidian-note-path]");
    if (obsidianNote) {
      const view = obsidianNote.dataset.obsidianPillar || appState.view;
      openVaultNote(state.overviews[view]?.vault_name || "", obsidianNote.dataset.obsidianNotePath);
      return;
    }
    const steward = event.target.closest?.("[data-steward-open]");
    if (steward) {
      const stewardView = steward.dataset.stewardOpen;
      openSteward(stewardView, {
        onOpenNote: (path) => openNote(path),
        onOpenObsidian: (path) => openVaultNote(vaultNameFor(stewardView), path),
      });
      return;
    }
    const retry = event.target.closest?.("[data-pillar-retry]");
    if (retry) {
      loadPillarModule(retry.dataset.pillarRetry);
      return;
    }
    const opsQuick = event.target.closest?.("[data-ops-open]");
    if (opsQuick) {
      openOpsTable(opsQuick.dataset.opsOpen);
      return;
    }
    const createNotebook = event.target.closest?.("[data-notebook-create]");
    if (createNotebook) {
      const view = createNotebook.dataset.notebookCreate || appState.view;
      openNotebookCreateDialog((created) => {
        invalidateChapterCache();
        state.reading[view] = null;
        state.notebook[view] = created.key;
        state.chapter[view] = "";
        state.search[view] = "";
        state.searchSeq[view] = (state.searchSeq[view] || 0) + 1;
        loadPillar(view)
          .then(() => window.scrollTo({ top: 0, left: 0, behavior: "smooth" }))
          .catch((error) => toast(error.message, "error"));
      }, { pillar: view });
      return;
    }
    const deleteNotebook = event.target.closest?.("[data-notebook-delete]");
    if (deleteNotebook) {
      const key = deleteNotebook.dataset.notebookDelete || "";
      const view = deleteNotebook.dataset.notebookDeletePillar || appState.view;
      const notebook = notebookById(view, key);
      const card = notebookCardsFor(view).find((item) => item.key === key);
      if (!notebook || !card || !canDeleteNotebook(view, notebook)) return;
      const count = Number(card.count) || 0;
      const builtinHint = notebook.custom ? "" : "这个内置本子会从首页隐藏，相关入口也会停用；";
      const ok = await confirmAction({
        title: "删除这个本子？",
        message: `${builtinHint}「${notebook.title}」和里面的 ${count} 篇文档会一起移入应用回收站（.torch-os/本子回收站），之后仍可在文件管理器里找回。`,
        confirmLabel: "删除本子", danger: true,
      });
      if (!ok) return;
      deleteNotebook.disabled = true;
      try {
        const result = await api("/api/notebooks/delete", {
          method: "POST",
          body: JSON.stringify({ pillar: view, key, confirm: true }),
          timeoutMs: 60000,
        });
        invalidateChapterCache();
        state.reading[view] = null;
        state.notebook[view] = "";
        state.chapter[view] = "";
        state.search[view] = "";
        state.searchSeq[view] = (state.searchSeq[view] || 0) + 1;
        await loadPillar(view);
        toast(
          `本子「${result.title || notebook.title}」已移入回收站（${Number(result.count) || 0} 篇文档）。`,
          "success", { timeout: 8000 });
      } catch (error) {
        if (error.status === 409) {
          toast(`删除失败：${error.message}`, "warning");
        } else if (error.status === 404) {
          toast("这个本子已经不在了。", "warning");
        } else {
          toast(`删除本子失败：${error.message}`, "error");
        }
      } finally {
        deleteNotebook.disabled = false;
      }
      return;
    }
    const notebookButton = event.target.closest?.("[data-notebook]");
    if (notebookButton) {
      const view = notebookButton.dataset.notebookView || appState.view;
      state.reading[view] = null;
      state.notebook[view] = notebookButton.dataset.notebook || "";
      state.chapter[view] = "";
      state.search[view] = "";
      state.searchSeq[view] = (state.searchSeq[view] || 0) + 1;
      try {
        await loadPillar(view);
        document.querySelector(`#${view}-view .notebook-page h3`)?.focus?.({ preventScroll: true });
      } catch (error) {
        toast(error.message, "error");
      }
      return;
    }
    const notebookBack = event.target.closest?.("[data-notebook-back]");
    if (notebookBack) {
      const view = notebookBack.dataset.notebookBack || appState.view;
      state.reading[view] = null;
      state.notebook[view] = "";
      state.chapter[view] = "";
      state.search[view] = "";
      state.searchSeq[view] = (state.searchSeq[view] || 0) + 1;
      try {
        await loadPillar(view);
      } catch (error) {
        toast(error.message, "error");
      }
      return;
    }
    const askDeepSeek = event.target.closest?.("[data-ask-deepseek]");
    if (askDeepSeek) {
      openDeepSeekAsk();
      return;
    }
    const roleButton = event.target.closest?.("[data-role-open]");
    if (roleButton) {
      const knowledge = roleButton.dataset.roleOpen === "academic_mentor";
      openRoleDrawer({
        roleId: roleButton.dataset.roleOpen,
        mode: roleButton.dataset.roleMode || undefined,
        entry: roleButton.dataset.roleEntry || undefined,
        title: roleButton.dataset.roleTitle || undefined,
        selectors: knowledge ? [] : [{ entry: "journal" }],
        prompt: knowledge
          ? "帮我看看这篇回答可以怎么学，先问我一个关键问题。"
          : "看看我最近写的东西，问我一个我没写出来的问题。",
      });
      return;
    }
    const editorNew = event.target.closest?.("[data-editor-new]");
    if (editorNew) {
      const view = editorNew.dataset.editorNew || appState.view;
      const notebook = notebookById(view, state.notebook[view] || "");
      const chapter = editorNew.dataset.newChapter || state.chapter[view]
        || defaultChapterForNotebook(notebook)
        || defaultWriteChapter(view);
      const generalLifeWrite = view === "life" && !notebook && !state.chapter[view];
      openNoteEditor({
        context: view,
        chapter,
        title: "",
        body: "",
        heading: generalLifeWrite ? "写点什么" : "",
      }).catch((error) => toast(`打不开编辑器：${error.message}`, "error"));
      return;
    }
    const editorEdit = event.target.closest?.("[data-editor-edit]");
    if (editorEdit) {
      const view = editorEdit.dataset.editorEdit || appState.view;
      const reading = state.reading[view];
      if (reading?.note) {
        openNoteEditor({ note: reading.note })
          .catch((error) => toast(`打不开编辑器：${error.message}`, "error"));
      }
      return;
    }
    const shareLink = event.target.closest?.("[data-share-link]");
    if (shareLink) {
      openShareLinkDialog();
      return;
    }
    const create = event.target.closest?.("[data-pillar-new]");
    if (create) {
      openNewNote(create.dataset.pillarNew,
                  create.dataset.newChapter || "",
                  create.dataset.newType || "");
      return;
    }
    const railGroup = event.target.closest?.("[data-rail-group-toggle]");
    if (railGroup) {
      const view = railGroup.dataset.railGroupView;
      const key = railGroup.dataset.railGroupToggle;
      if (view && key) {
        // 分组标题只做折叠/展开，不改变当前章节。
        setRailGroupExpanded(view, key, railGroup.getAttribute("aria-expanded") === "false");
      }
      return;
    }
    const chapter = event.target.closest?.("[data-pillar-chapter]");
    if (chapter) {
      const view = chapter.dataset.pillarChapter;
      const focusCurrent = chapter.classList?.contains("pillar-rail-item")
        || chapter.classList?.contains("notebook-chip");
      state.reading[view] = null;
      state.chapter[view] = chapter.dataset.chapter || "";
      state.search[view] = "";
      state.searchSeq[view] = (state.searchSeq[view] || 0) + 1;
      try {
        await loadPillar(view, { keepChapter: true });
        // 重绘后把焦点放回当前项，键盘用户不会回到页面开头。
        if (focusCurrent) {
          document.querySelector(`#${view}-view .pillar-rail-item.is-active, #${view}-view .notebook-chip.is-active`)
            ?.focus?.({ preventScroll: true });
        }
      } catch (error) {
        toast(error.message, "error");
      }
      return;
    }
    const review = event.target.closest?.("[data-review]");
    if (review) {
      review.disabled = true;
      try {
        await openReview(review.dataset.review);
      } finally {
        review.disabled = false;
      }
      return;
    }
    const identity = event.target.closest?.("[data-open-identity]");
    if (identity) {
      openIdentityCard();
      return;
    }
    const reviewButton = event.target.closest?.("[data-journal-review]");
    if (reviewButton) {
      reviewButton.disabled = true;
      try {
        await openJournalReview();
      } finally {
        reviewButton.disabled = false;
      }
      return;
    }
    const journalDay = event.target.closest?.("[data-journal-day]");
    if (journalDay) {
      const path = journalDay.dataset.journalPath;
      if (path) {
        openNote(path);
        return;
      }
      if (journalDay.dataset.journalLegacy === "1") {
        toast("这篇日记还在旧目录，请先在设置 →「库结构与迁移」里迁移。", "warning");
      } else {
        toast(`${journalDay.dataset.journalDay} 还没有日记。`, "info");
      }
      return;
    }
    const row = event.target.closest?.("[data-note-path]");
    if (row) {
      openNote(row.dataset.notePath);
    }
  });
  document.addEventListener("input", (event) => {
    const search = event.target.closest?.("[data-pillar-search]");
    if (!search) return;
    const view = search.dataset.pillarSearch;
    state.search[view] = search.value;
    window.clearTimeout(search._timer);
    const seq = (state.searchSeq[view] || 0) + 1;
    state.searchSeq[view] = seq;
    search._timer = window.setTimeout(async () => {
      try {
        state.reading[view] = null;
        state.chapter[view] = "";
        state.notebook[view] = "";
        const query = state.search[view];
        const items = query.trim()
          ? (await api(`/api/search?pillar=${view}&q=${encodeURIComponent(query)}`)).items
          : (state.overviews[view]?.recent || []);
        // 输入很快时，较早的响应不能覆盖较新的结果。
        if (state.searchSeq[view] !== seq) return;
        state.notes[view] = items;
        renderShell(view);
        const next = $(`[data-pillar-search="${view}"]`);
        if (next) { next.focus(); next.setSelectionRange?.(next.value.length, next.value.length); }
      } catch (error) {
        if (state.searchSeq[view] === seq) toast(error.message, "error");
      }
    }, 220);
  });
  document.addEventListener("submit", (event) => {
    const form = event.target.closest?.("[data-share-quick-form]");
    if (!form) return;
    event.preventDefault();
    const input = form.querySelector("[data-share-quick-url]");
    const url = String(input?.value || "").trim();
    if (!url) {
      toast("先粘贴 DeepSeek 的公开分享链接。", "warning");
      input?.focus?.();
      return;
    }
    openShareLinkDialog({ url, preview: true });
  });
  // Esc 退出页内阅读；有弹窗时让弹窗先处理。
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    if (document.querySelector("dialog[open]")) return;
    const view = appState.view;
    if (view && state.reading[view]) {
      event.preventDefault();
      closeReader(view);
    }
  });
}

async function copyText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* fall through to the legacy path */ }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    return copied;
  } catch {
    return false;
  }
}

export async function openNote(path) {
  try {
    const [note, links] = await Promise.all([
      api(`/api/notes?path=${encodeURIComponent(path)}`),
      api(`/api/notes/backlinks?path=${encodeURIComponent(path)}`).catch(() => ({ items: [] })),
    ]);
    const backlinks = Array.isArray(links.items) ? links.items : [];
    let linkedTasks = [];
    if (GOAL_CHAPTERS.has(note.chapter)) {
      try {
        const board = await api("/api/time/tasks");
        linkedTasks = (board.tasks || []).filter((task) => task.goal === note.path);
      } catch {
        linkedTasks = [];
      }
    }
    const view = note.pillar;
    if (!PILLAR_META[view]) {
      toast("这篇笔记不在四库里，请在 Obsidian 里打开。", "warning");
      return;
    }
    state.reading[view] = {
      note, backlinks, linkedTasks,
      doc: null,
      focusPending: true,
      scrollY: typeof window.scrollY === "number" ? window.scrollY : 0,
    };
    if (appState.view === view) {
      renderShell(view);
      return;
    }
    // 全局搜索/管家可能从别的模块打开笔记：交给 app.js 切模块，加载器再按 reading 渲染。
    const { switchView } = await import("./app.js");
    const switched = await switchView(view);
    if (!switched) state.reading[view] = null;
  } catch (error) {
    toast(error.message, "error");
  }
}

/** 关闭页内阅读，回到进入前的列表位置。 */
function closeReader(view) {
  const reading = state.reading[view];
  if (!reading) return;
  const targetPath = reading.note.path;
  state.reading[view] = null;
  renderShell(view);
  const row = Array.from(document.querySelectorAll(
    `#${view}-view .pillar-note-row[data-note-path], #${view}-view .note-card[data-note-path]`))
    .find((node) => node.dataset.notePath === targetPath);
  row?.focus?.({ preventScroll: true });
  if (typeof window.scrollTo === "function") {
    window.scrollTo({ top: reading.scrollY || 0, left: 0, behavior: "auto" });
  }
}

function readerWikiLink(note, displayTitle) {
  return `[[${note.path.replace(/\.md$/i, "")}|${String(displayTitle).replace(/\|/g, "｜")}]]`;
}

/** 双链只给了标题：先在四库里找同名笔记，找不到就提示去 Obsidian。 */
async function openWikiTarget(target) {
  const raw = String(target || "").trim();
  if (!raw) return;
  const name = raw.split("#")[0].trim();
  if (!name) return;
  try {
    const payload = await api(`/api/search?q=${encodeURIComponent(name)}&limit=20`);
    const items = Array.isArray(payload.items) ? payload.items : [];
    const exact = items.find((item) => item.title === name
      || item.path === name
      || item.path === `${name}.md`
      || item.path.endsWith(`/${name}.md`));
    if (exact) { await openNote(exact.path); return; }
  } catch { /* fall through to a direct path attempt */ }
  if (/\.md$/i.test(name)) { await openNote(name); return; }
  toast(`四库里没有找到「${name}」，可以在 Obsidian 里打开它。`, "warning");
}

function openVaultNote(vaultName, path) {
  if (isObsidianHost() && globalThis.parent && globalThis.parent !== globalThis) {
    try { globalThis.parent.postMessage(openNoteMessage(path), "*"); return; } catch { /* fall through */ }
  }
  const uri = obsidianOpenUri(vaultName, path);
  if (!uri) { toast("没有可用的 Obsidian 库名，请在 Obsidian 里打开。", "warning"); return; }
  try { globalThis.open?.(uri, "_blank", "noopener"); } catch { /* ignore */ }
}


function chapterEntry(view, key) {
  return chapterList(view).find((item) => item.key === key) || null;
}

function vaultNameFor(view) {
  return state.overviews[view]?.vault_name || state.overviews.life?.vault_name || "";
}

function localStamp() {
  const now = new Date(Date.now() + (new Date().getTimezoneOffset() + 480) * 60000);
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
}

function noteTemplate(type) {
  const kind = type ? `type: ${type}\n` : "";
  return `---\n${kind}创建: ${shanghaiDay()}\n---\n\n# \n`;
}

/** 四库新建：只在 Obsidian 里建文件，插件不写盘（2026-10-26 新原则）。 */
function openNewNote(view, chapter = "", type = "") {
  const meta = PILLAR_META[view];
  if (!meta) return;
  const custom = customNotebookName(chapter);
  if (custom && customNotebookPillar(chapter) === view) {
    const root = view === "knowledge" ? "03_知识库" : "00_人生路";
    const target = `${root}/${custom}/未命名-${localStamp()}.md`;
    if (!createObsidianNote(target, vaultNameFor(view), noteTemplate(type || "note"))) {
      toast("没有可用的 Obsidian 库名，请在 Obsidian 里新建。", "warning");
    }
    return;
  }
  const entry = chapterEntry(view, chapter)
    || chapterEntry(view, meta.defaultChapter)
    || chapterList(view)[0];
  if (!entry) {
    toast("还没拿到库结构，请刷新后重试。", "warning");
    return;
  }
  if (entry.key === "journal") {
    openTodayJournal();
    return;
  }
  if (entry.key === "habit") {
    openVaultNote(vaultNameFor(view), `${entry.path}/习惯定义.md`);
    return;
  }
  const target = `${entry.path}/未命名-${localStamp()}.md`;
  if (!createObsidianNote(target, vaultNameFor(view), noteTemplate(type || entry.default_type))) {
    toast("没有可用的 Obsidian 库名，请在 Obsidian 里新建。", "warning");
  }
}

/** 体重/账目：直接打开 Obsidian 里的当月表，插件里只读。 */
async function openOpsTable(dataset) {
  const entry = chapterEntry("ops", dataset);
  if (!entry) return;
  const now = new Date(Date.now() + (new Date().getTimezoneOffset() + 480) * 60000);
  const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const target = `${entry.path}/${month}.md`;
  try {
    await api(`/api/notes?path=${encodeURIComponent(target)}`);
  } catch {
    toast(`这个月还没有表。在 Obsidian 里复制上个月的文件，改名成 ${month} 即可。`, "warning");
    return;
  }
  openVaultNote(vaultNameFor("ops"), target);
}

function isoWeek(date) {
  const target = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const day = target.getUTCDay() || 7;
  target.setUTCDate(target.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(target.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((target - yearStart) / 86400000 + 1) / 7);
  return { year: target.getUTCFullYear(), week };
}

/** 周/月复盘：打开已有那篇；没有就让 Obsidian 新建一个同名骨架。 */
async function openReview(kind) {
  const entry = chapterEntry("life", kind === "monthly" ? "review_month" : "review_week");
  if (!entry) return;
  const today = new Date(`${shanghaiDay()}T00:00:00Z`);
  const monthly = kind === "monthly";
  const label = monthly
    ? `${today.getUTCFullYear()}-${String(today.getUTCMonth() + 1).padStart(2, "0")} 月复盘`
    : (() => { const { year, week } = isoWeek(today); return `${year}-W${String(week).padStart(2, "0")} 周复盘`; })();
  const type = monthly ? "monthly_review" : "weekly_review";
  let content = noteTemplate(type);
  if (!monthly) {
    // 周复盘不再是一张白纸：把最近 7 天日记的摘要和三个问题先摆好。
    try {
      const payload = await api(`/api/journal/weekly?day=${shanghaiDay()}`);
      if (payload?.markdown) content = payload.markdown;
    } catch { /* 拉不到就退回空骨架 */ }
  }
  if (!createObsidianNote(`${entry.path}/${label}.md`, vaultNameFor("life"), content)) {
    toast("没有可用的 Obsidian 库名，请在 Obsidian 里新建复盘。", "warning");
  }
}

/** 身份卡：有就打开，没有就让 Obsidian 建一份空骨架。 */
function openIdentityCard() {
  const identity = state.overviews.self?.identity || {};
  const target = identity.path || "01_自我档案/01_内核与三观/身份（identity）.md";
  const content = identity.exists
    ? ""
    : "---\ntype: identity\n---\n\n# 我是谁\n\n## 三观\n\n## 我要去哪里\n";
  if (!createObsidianNote(target, vaultNameFor("self"), content)) {
    toast("没有可用的 Obsidian 库名，请在 Obsidian 里打开身份文件。", "warning");
  }
}

/** 今日日记：存在就打开，不存在就让 Obsidian 新建，插件不写盘。 */
async function openTodayJournal() {
  const day = shanghaiDay();
  let target = `00_人生路/01_日记/${day}.md`;
  try {
    const existing = await api(`/api/journal/note?day=${encodeURIComponent(day)}`);
    if (existing?.exists) target = existing.relpath || target;
  } catch { /* 查询失败时按默认路径打开 */ }
  if (!String(target).startsWith("00_人生路/")) {
    toast("这篇日记还在旧目录，请先在设置 →「库结构与迁移」里迁移。", "warning");
    return;
  }
  const content = noteTemplate("journal").replace("# \n", `# ${day}\n`);
  if (!createObsidianNote(target, vaultNameFor("life"), content)) {
    toast("没有可用的 Obsidian 库名，请在 Obsidian 里打开。", "warning");
  }
}

export function initPillars() {
  bindPillarEvents();
  bindOpsEvents();
  bindHabitEvents();
  initNoteEditor();
  initNotebookCreate();
  initShareLink();
  initOpsAI();
  // 编辑器保存/移动/删除后刷新列表；正在阅读同一篇时保持阅读位置但重读最新内容。
  document.addEventListener("torch:notes-changed", (event) => {
    const detail = event.detail || {};
    const view = detail.pillar || appState.view;
    if (!["life", "knowledge"].includes(view) || appState.view !== view) return;
    const reading = state.reading[view];
    const matchesReading = reading?.note && detail.path
      && (reading.note.path === detail.path
        || (detail.previousPath && reading.note.path === detail.previousPath));
    if (matchesReading) {
      if (detail.deleted) {
        state.reading[view] = null;
        loadPillarModule(view);
      } else if (detail.path) {
        openNote(detail.path);
      }
      return;
    }
    if (!reading) loadPillarModule(view);
  });
}

export async function loadPillarModule(view) {
  try {
    await loadPillar(view);
  } catch (error) {
    const root = $(`#${view}-view .pillar-shell`);
    if (root) {
      root.classList.remove("is-reading");
      root.innerHTML = `<div class="pillar-empty"><p>加载失败：${escapeHtml(error.message)}</p><button type="button" class="button secondary" data-pillar-retry="${escapeHtml(view)}">重试</button></div>`;
    }
  }
}

export function currentPillar() {
  return state;
}
