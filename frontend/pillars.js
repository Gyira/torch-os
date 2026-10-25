// 四库前端（2026-10-21）：章节轨 + 条目列表 + 详情 + 跳转 Obsidian。
// 2026-10-26 起四库只读：这里不写任何 Markdown，新建/修改都跳回 Obsidian 原生编辑。
// 工作台完全独立；这里只读四库接口，不碰 time-module / workbench 的 DOM。
import { $, api, escapeHtml, formatRelativeDate, toast } from "./core.js";
import { renderMarkdown } from "./pillar-markdown.js";
import { bindOpsEvents, mountOps } from "./pillar-ops.js";
import { bindHabitEvents, mountHabits } from "./pillar-habits.js";
import { openSteward } from "./pillar-steward.js";
import { shanghaiDay } from "./journal-model.js";
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
  root.innerHTML = `
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
    </header>
    <div class="pillar-layout">
      <nav class="pillar-rail" aria-label="${escapeHtml(meta.title)}章节">
        <button type="button" class="pillar-rail-item${chapter ? "" : " is-active"}" data-pillar-chapter="${view}" data-chapter="">总览
          <span>${overview?.count ?? 0}</span></button>
        ${chapters.map((item) => {
          const entry = overview?.chapters?.find((row) => row.key === item.key);
          return `<button type="button" class="pillar-rail-item${chapter === item.key ? " is-active" : ""}" data-pillar-chapter="${view}" data-chapter="${escapeHtml(item.key)}">${escapeHtml(item.title)}<span>${entry?.count ?? 0}</span></button>`;
        }).join("")}
      </nav>
      <section class="pillar-content" aria-live="polite">
        <div class="pillar-content-head">
          <div>
            <p class="eyebrow">${escapeHtml(chapter ? chapterTitle(view, chapter) : "最近")}</p>
            <h3>${escapeHtml(searching ? `搜索「${state.search[view]}」` : chapter ? chapterTitle(view, chapter) : "最近记录")}</h3>
          </div>
          ${chapter ? `<button type="button" class="text-button" data-pillar-chapter="${view}" data-chapter="">返回总览</button>` : ""}
        </div>
        ${view === "ops" ? (chapter === "health" || chapter === "finance"
          ? `<div class="pillar-ops-slot" data-ops-table="${chapter}"></div>`
          : `<div class="pillar-ops-slot" data-ops-dashboard></div>`) : ""}
        ${view === "knowledge" && chapter === "inbox" ? `<div class="pillar-suggestions" data-inbox-suggestions></div>` : ""}
        ${view === "life" && chapter === "habit" ? `<div class="pillar-habits" data-habit-panel></div>` : ""}
        ${(view === "ops" && !chapter && !notes.length && !searching)
          || (view === "ops" && (chapter === "health" || chapter === "finance") && !notes.length && !searching)
          || (view === "life" && chapter === "habit" && !notes.length && !searching)
          ? "" : `<div class="pillar-list">
          ${notes.length ? notes.map(noteRow).join("") : `<div class="pillar-empty">
            <p>${searching ? "没有匹配的记录。" : "这里还没有记录。"}</p>
            <p class="field-help">${escapeHtml(searching ? "换个关键词，或清空搜索看看全部。" : meta.emptyHint)}</p>
            <button type="button" class="button secondary" data-pillar-new="${view}" data-new-chapter="${escapeHtml(chapter)}">＋ 在 Obsidian 新建</button>
          </div>`}
        </div>`}
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
        ${view === "self" && overview?.identity ? `<section class="pillar-card pillar-identity-card">
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
          <button type="button" class="small-button" data-note-path="${escapeHtml(item.path)}">在 Obsidian 打开</button>
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
    const steward = event.target.closest?.("[data-steward-open]");
    if (steward) {
      openSteward(steward.dataset.stewardOpen, {
        onOpenNote: (path) => openNote(path),
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
    const chapter = event.target.closest?.("[data-pillar-chapter]");
    if (chapter) {
      const view = chapter.dataset.pillarChapter;
      const fromRail = chapter.classList?.contains("pillar-rail-item");
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
      openReview(review.dataset.review);
      return;
    }
    const identity = event.target.closest?.("[data-open-identity]");
    if (identity) {
      openIdentityCard();
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

export function openNote(path) {
  const trigger = document.activeElement;
  Promise.all([
    api(`/api/notes?path=${encodeURIComponent(path)}`),
    api(`/api/notes/backlinks?path=${encodeURIComponent(path)}`).catch(() => ({ items: [] })),
  ]).then(async ([note, links]) => {
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
    const dialog = document.createElement("dialog");
    dialog.className = "pillar-dialog pillar-note-dialog";
    const vaultName = state.overviews[note.pillar]?.vault_name || "";
    const journalLabel = note.chapter === "journal" ? journalDayLabel(note) : "";
    const displayTitle = journalLabel ? `日记 · ${journalLabel}` : note.title;
    const isTaskNote = note.chapter === "task";
    const wikiLink = `[[${note.path.replace(/\.md$/i, "")}|${String(displayTitle).replace(/\|/g, "｜")}]]`;
    const openLinkedCount = linkedTasks.filter((task) => !task.done).length;
    dialog.innerHTML = `
      <div class="pillar-dialog-head">
        <div>
          <p class="eyebrow">${escapeHtml(chapterTitle(note.pillar, note.chapter))}</p>
          <h2>${escapeHtml(displayTitle)}</h2>
          <p class="meta">${escapeHtml(note.path)}${!journalLabel && note.date ? ` · ${escapeHtml(formatRelativeDate(note.date))}` : ""}</p>
        </div>
        <button type="button" class="text-button" data-close-note>关闭</button>
      </div>
      <article class="pillar-note-body">${renderMarkdown(note.body || "")}</article>
      ${GOAL_CHAPTERS.has(note.chapter) ? `<section class="pillar-linked-tasks">
        <p class="eyebrow">关联任务（${openLinkedCount} 项未完成）</p>
        ${linkedTasks.length ? `<ul>${linkedTasks.map((task) => `<li class="${task.done ? "is-done" : ""}">
          <span>${escapeHtml(task.title)}</span>
          <span class="meta">${task.done ? "已完成" : escapeHtml(task.date || "未排期")}</span>
          <button type="button" class="text-button" data-open-linked-task="${escapeHtml(task.key)}">在 Obsidian 打开</button>
        </li>`).join("")}</ul>` : `<p class="field-help">还没有任务关联到这个目标；可在资料导入时选择它，或直接在任务笔记 frontmatter 写「目标」。</p>`}
        <p class="field-help">任务来自时间管理的一任务一笔记；这里只读，编辑请到工作台或 Obsidian。</p>
      </section>` : ""}
      ${backlinks.length ? `<section class="pillar-backlinks">
        <p class="eyebrow">反向链接</p>
        ${backlinks.map((item) => `<button type="button" class="text-button" data-backlink-path="${escapeHtml(item.path)}">${escapeHtml(item.title)}</button>`).join("")}
      </section>` : ""}
      <div class="pillar-dialog-foot">
        <div class="pillar-note-actions">
          <button type="button" class="button primary" data-open-note>在 Obsidian 打开并编辑</button>
          <button type="button" class="small-button" data-copy-wikilink title="复制可直接粘贴到 Obsidian 的双链">复制双链</button>
          <button type="button" class="small-button" data-copy-path>复制路径</button>
        </div>
        <span class="field-help">这里只是阅读视图；标签、正文和所在章节都在 Obsidian 里改。</span>
      </div>`;
    document.body.append(dialog);
    const close = () => {
      dialog.close();
      dialog.remove();
      trigger?.focus?.({ preventScroll: true });
    };
    dialog.querySelector("[data-close-note]")?.addEventListener("click", close);
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
    dialog.querySelector("[data-open-note]")?.addEventListener("click", () => {
      openVaultNote(vaultName, note.path);
    });
    dialog.querySelector("[data-copy-wikilink]")?.addEventListener("click", async () => {
      const copied = await copyText(wikiLink);
      toast(copied ? "已复制双链，可直接粘贴到 Obsidian。" : "复制失败，请手动复制路径。", copied ? "info" : "warning");
    });
    dialog.querySelector("[data-copy-path]")?.addEventListener("click", async () => {
      const copied = await copyText(note.path);
      toast(copied ? "已复制路径。" : "复制失败，请手动选择路径。", copied ? "info" : "warning");
    });
    dialog.querySelectorAll("[data-open-linked-task]").forEach((button) => {
      button.addEventListener("click", () => {
        const task = linkedTasks.find((item) => item.key === button.dataset.openLinkedTask);
        if (!task) return;
        if (!openTaskDocument(task, { vault_name: vaultName })) {
          toast("没有可打开的任务文档，请刷新后重试。", "warning");
        }
      });
    });
    dialog.querySelectorAll("[data-backlink-path]").forEach((button) => {
      button.addEventListener("click", () => {
        const target = button.dataset.backlinkPath;
        close();
        if (target) openNote(target);
      });
    });
    if (typeof dialog.showModal === "function") dialog.showModal();
  }).catch((error) => toast(error.message, "error"));
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
function openReview(kind) {
  const entry = chapterEntry("life", kind === "monthly" ? "review_month" : "review_week");
  if (!entry) return;
  const today = new Date(`${shanghaiDay()}T00:00:00Z`);
  const monthly = kind === "monthly";
  const label = monthly
    ? `${today.getUTCFullYear()}-${String(today.getUTCMonth() + 1).padStart(2, "0")} 月复盘`
    : (() => { const { year, week } = isoWeek(today); return `${year}-W${String(week).padStart(2, "0")} 周复盘`; })();
  const type = monthly ? "monthly_review" : "weekly_review";
  if (!createObsidianNote(`${entry.path}/${label}.md`, vaultNameFor("life"), noteTemplate(type))) {
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
