// 四库前端（2026-10-21）：章节轨 + 条目列表 + 详情 + 跳转 Obsidian。
// 2026-10-26 起四库只读：这里不写任何 Markdown，新建/修改都跳回 Obsidian 原生编辑。
// 工作台完全独立；这里只读四库接口，不碰 time-module / workbench 的 DOM。
import { $, api, escapeHtml, formatRelativeDate, state as appState, toast } from "./core.js";
import { renderMarkdownDocument } from "./pillar-markdown.js";
import { bindOpsEvents, mountOps } from "./pillar-ops.js";
import { bindHabitEvents, mountHabits } from "./pillar-habits.js";
import { openSteward } from "./pillar-steward.js";
import { identityInboxMarkup } from "./identity-inbox.js";
import { archiveMarkup } from "./archive-markup.js";
import { shanghaiDay } from "./journal-model.js";
import { openJournalReview } from "./journal-ritual.js";
import {
  createObsidianNote, isObsidianHost, obsidianOpenUri, openNoteMessage, openTaskDocument,
} from "./task-open.js";

const GOAL_CHAPTERS = new Set(["goal_year", "goal_quarter", "goal_month"]);

const PILLAR_META = {
  life: {
    title: "人生路",
    subtitle: "把每天过成可回看的成长线索。",
    emptyHint: "例如：今天发生了什么，或此刻在想什么。",
    icon: "行",
    defaultChapter: "reflection",
    quick: [
      { label: "写今日日记", chapter: "journal", type: "journal" },
      { label: "记一条反思", chapter: "reflection", type: "reflection" },
      { label: "记一条心得", chapter: "insight_life", type: "insight" },
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

function chapterList(view) {
  const pillar = state.manifest?.pillars?.find((item) => item.key === view);
  return pillar?.chapters || [];
}

function chapterTitle(view, key) {
  const chapter = chapterList(view).find((item) => item.key === key);
  return chapter?.title || key;
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
            <button type="button" class="button primary" data-reader-open-obsidian="${view}">在 Obsidian 打开并编辑</button>
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
              <p class="field-help">这是只读阅读视图；正文、标签和所在章节都在 Obsidian 里改。</p>
              <button type="button" class="button primary" data-reader-open-obsidian="${view}">在 Obsidian 打开并编辑</button>
            </footer>
          </article>
          ${tocMarkup}
        </div>
      </section>
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
        <button type="button" class="button secondary" data-steward-open="${view}" title="提案与健康检查（只读）">管家</button>
        <button type="button" class="button primary" data-pillar-new="${view}" title="在 Obsidian 新建笔记（按 N）" aria-keyshortcuts="N">＋ 在 Obsidian 新建</button>
      </div>
    </header>`;
  const reading = state.reading[view];
  if (reading) {
    root.innerHTML = `${headHtml}${readerMarkup(view, reading, railItems)}`;
    if (reading.focusPending) {
      reading.focusPending = false;
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
      root.querySelector(".pillar-reader-head h1")?.focus?.({ preventScroll: true });
    }
    return root;
  }
  // 自我档案总览不是「一堆笔记的最近列表」，而是一份档案：封面 / 目录 / 时间线。
  const archiveView = view === "self" && !chapter && !searching;
  const hideList = (view === "ops" && !chapter && !notes.length && !searching)
    || (view === "ops" && (chapter === "health" || chapter === "finance") && !notes.length && !searching)
    || (view === "life" && chapter === "habit" && !notes.length && !searching);
  root.innerHTML = `${headHtml}
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
            <button type="button" class="button secondary" data-pillar-new="${view}" data-new-chapter="${escapeHtml(chapter)}">＋ 在 Obsidian 新建</button>
          </div>`}
        </div>`)}
      </section>
      <aside class="pillar-aside">
        <section class="pillar-card">
          <p class="eyebrow">在 Obsidian 写</p>
          <div class="pillar-quick">
            ${meta.quick.map((item) => item.ops
              ? `<button type="button" class="pillar-quick-button" data-ops-open="${escapeHtml(item.ops)}">${escapeHtml(item.label)}</button>`
              : `<button type="button" class="pillar-quick-button" data-pillar-new="${view}" data-new-chapter="${item.chapter}" data-new-type="${item.type}">${escapeHtml(item.label)}</button>`).join("")}
          </div>
          <p class="field-help">这里只读；点按钮会在 Obsidian 里新建/打开对应笔记。</p>
        </section>
        ${view === "self" && overview?.identity && !archiveView ? `<section class="pillar-card pillar-identity-card">
          <p class="eyebrow">身份卡</p>
          <p class="pillar-stat">${escapeHtml(overview.identity.name || "还没有填写姓名")}</p>
          <p class="pillar-subtitle">${escapeHtml(overview.identity.motto || "还没有座右铭")}</p>
          ${overview.identity.worldview ? `<p class="field-help">三观：${escapeHtml(overview.identity.worldview.slice(0, 90))}</p>` : ""}
          <button type="button" class="text-button" data-open-identity="${escapeHtml(overview.identity.path || "")}">${overview.identity.exists ? "在 Obsidian 打开身份卡" : "去写身份卡"}</button>
        </section>` : ""}
        ${view === "self" ? identityInboxMarkup(overview?.drafts || []) : ""}
        ${view === "life" && overview?.today ? `<section class="pillar-card">
          <p class="eyebrow">今天</p>
          <p class="pillar-stat">${overview.today.journal_written ? "日记已写" : "日记还没写"}</p>
          <p class="field-help">${overview.today.journal_written ? `最近更新 ${escapeHtml(formatRelativeDate(overview.today.journal_updated_at))}` : "先写一句也算记录。"}</p>
          <div class="pillar-quick">
            <button type="button" class="pillar-quick-button" data-journal-review>今晚复盘（8 分钟）</button>
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
        ${view === "knowledge" && overview ? `<section class="pillar-card">
          <p class="eyebrow">收件箱</p>
          <p class="pillar-stat">${overview.inbox_count ?? 0} 条待分流</p>
          <button type="button" class="text-button" data-pillar-chapter="${view}" data-chapter="inbox">去看看（只读）</button>
        </section>` : ""}
        <section class="pillar-card pillar-card-quiet">
          <p class="eyebrow">说明</p>
          <p class="field-help">${escapeHtml(meta.subtitle)}四库在这里只读浏览；新建与修改都在 Obsidian 里完成，插件只负责跳过去。</p>
        </section>
      </aside>
    </div>`;
  if (view === "ops") {
    mountOps(view).catch((error) => toast(error.message, "error"));
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
  document.querySelectorAll("#knowledge-view .pillar-note-row[data-note-path]").forEach((row) => {
    if (suggestedPaths.has(row.dataset.notePath)) row.hidden = true;
  });
}

async function loadPillar(view, { keepChapter = false } = {}) {
  await ensureManifest();
  if (!keepChapter) {
    // 模块切换时保留用户上次看的章节；首次进入看总览。
    if (!(view in state.chapter)) state.chapter[view] = "";
  }
  const overview = await api(`/api/pillars/${view}/overview`);
  state.overviews[view] = overview;
  if (state.chapter[view]) {
    state.notes[view] = (await api(`/api/pillars/${view}/notes?chapter=${encodeURIComponent(state.chapter[view])}`)).items;
  } else if (state.search[view]) {
    state.notes[view] = (await api(`/api/search?pillar=${view}&q=${encodeURIComponent(state.search[view])}`)).items;
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
      const fromRail = chapter.classList?.contains("pillar-rail-item");
      state.reading[view] = null;
      state.chapter[view] = chapter.dataset.chapter || "";
      state.search[view] = "";
      state.searchSeq[view] = (state.searchSeq[view] || 0) + 1;
      try {
        if (state.chapter[view]) {
          state.notes[view] = (await api(`/api/pillars/${view}/notes?chapter=${encodeURIComponent(state.chapter[view])}`)).items;
        } else {
          state.notes[view] = state.overviews[view]?.recent || [];
        }
        renderShell(view);
        // 章节轨会被整块重绘：从键盘切章节后把焦点放回新的当前项，
        // 否则用户下一次按 Tab 会从页面开头重新开始。
        if (fromRail) {
          document.querySelector(`#${view}-view .pillar-rail-item.is-active`)?.focus?.({ preventScroll: true });
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
    const draftAccept = event.target.closest?.("[data-identity-draft-accept]");
    if (draftAccept) {
      const name = draftAccept.dataset.identityDraftAccept;
      draftAccept.disabled = true;
      try {
        const result = await api(`/api/identity/drafts/${encodeURIComponent(name)}/accept`, { method: "POST" });
        toast(result.target === "identity" ? `已写进身份卡：${result.field_label || ""}` : "已写进 02_记忆与自传。", "success");
        await loadPillarModule("self");
      } catch (error) {
        toast(error.message, "error");
        draftAccept.disabled = false;
      }
      return;
    }
    const draftReject = event.target.closest?.("[data-identity-draft-reject]");
    if (draftReject) {
      const name = draftReject.dataset.identityDraftReject;
      draftReject.disabled = true;
      try {
        await api(`/api/identity/drafts/${encodeURIComponent(name)}/reject`, { method: "POST" });
        toast("已丢弃；四库没有改动。", "info");
        await loadPillarModule("self");
      } catch (error) {
        toast(error.message, "error");
        draftReject.disabled = false;
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
  const row = Array.from(document.querySelectorAll(`#${view}-view .pillar-note-row[data-note-path]`))
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
}

export async function loadPillarModule(view) {
  try {
    await loadPillar(view);
  } catch (error) {
    const root = $(`#${view}-view .pillar-shell`);
    if (root) root.innerHTML = `<div class="pillar-empty"><p>加载失败：${escapeHtml(error.message)}</p><button type="button" class="button secondary" data-pillar-retry="${escapeHtml(view)}">重试</button></div>`;
  }
}

export function currentPillar() {
  return state;
}
