// 四库前端（2026-10-21）：章节轨 + 条目列表 + 详情 + 快速捕获。
// 工作台完全独立；这里只读写四库接口，不碰 time-module / workbench 的 DOM。
import { $, api, confirmAction, escapeHtml, formatRelativeDate, toast } from "./core.js";
import { renderMarkdown } from "./pillar-markdown.js";
import { bindOpsEvents, mountOps } from "./pillar-ops.js";
import { bindHabitEvents, mountHabits } from "./pillar-habits.js";
import { openSteward } from "./pillar-steward.js";
import { shanghaiDay } from "./journal-model.js";
import { isObsidianHost, obsidianOpenUri, openNoteMessage, openTaskDocument } from "./task-open.js";

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
        <button type="button" class="button secondary" data-steward-open="${view}" title="提案、健康检查与问答">管家</button>
        <button type="button" class="button primary" data-pillar-capture="${view}" title="记一条（按 N）" aria-keyshortcuts="N">＋ 记一条</button>
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
            <button type="button" class="button secondary" data-pillar-capture="${view}" data-capture-chapter="${escapeHtml(chapter)}">＋ 记第一条</button>
          </div>`}
        </div>`}
      </section>
      <aside class="pillar-aside">
        <section class="pillar-card">
          <p class="eyebrow">快速记录</p>
          <div class="pillar-quick">
            ${meta.quick.map((item) => item.ops
              ? `<button type="button" class="pillar-quick-button" data-ops-quick="${escapeHtml(item.ops)}">${escapeHtml(item.label)}</button>`
              : `<button type="button" class="pillar-quick-button" data-pillar-capture="${view}" data-capture-chapter="${item.chapter}" data-capture-type="${item.type}">${escapeHtml(item.label)}</button>`).join("")}
          </div>
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
            <button type="button" class="pillar-quick-button" data-review="weekly">生成本周复盘</button>
            <button type="button" class="pillar-quick-button" data-review="monthly">生成本月复盘</button>
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
          <button type="button" class="text-button" data-pillar-chapter="${view}" data-chapter="inbox">去整理</button>
        </section>` : ""}
        <section class="pillar-card pillar-card-quiet">
          <p class="eyebrow">说明</p>
          <p class="field-help">${escapeHtml(meta.subtitle)}所有内容都是 Vault 里的 Markdown；这里只做浏览与捕获，深度编辑请打开 Obsidian。</p>
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

function updateInboxSelection(host) {
  if (!host) return;
  const boxes = [...host.querySelectorAll("[data-inbox-select]")];
  const checked = boxes.filter((box) => box.checked);
  const apply = host.querySelector("[data-inbox-apply]");
  if (apply) {
    apply.disabled = !checked.length;
    apply.textContent = `整理选中（${checked.length}）`;
  }
  const all = host.querySelector("[data-inbox-select-all]");
  if (all) {
    all.checked = boxes.length > 0 && checked.length === boxes.length;
    all.indeterminate = checked.length > 0 && checked.length < boxes.length;
  }
}

async function applyInboxProposals(ids, trigger) {
  const unique = [...new Set(ids.filter(Boolean))];
  if (!unique.length) return;
  const rows = [...document.querySelectorAll("#knowledge-view [data-inbox-row]")];
  const selectedRows = unique.map((id) => rows.find((row) => row.dataset.inboxRow === id)).filter(Boolean);
  const preview = selectedRows.slice(0, 6).map((row) => {
    const title = row.querySelector("strong")?.textContent?.trim() || "";
    const target = row.dataset.inboxTarget || "";
    return `• ${title} → ${target}`;
  }).join("\n");
  const more = unique.length > 6 ? `\n…另有 ${unique.length - 6} 条` : "";
  const accepted = await confirmAction({
    title: `按建议整理 ${unique.length} 条？`,
    message: `${preview}${more}\n\n只会移动选中的笔记，不会删除；移动后可在目标章节找到。`,
    confirmLabel: "整理选中",
    trigger,
  });
  if (!accepted) return;
  try {
    const result = await api("/api/steward/proposals/apply-batch", {
      method: "POST",
      body: JSON.stringify({ proposal_ids: unique, confirm: true }),
    });
    const applied = Number(result.applied_count) || 0;
    const failed = Number(result.failed_count) || 0;
    if (failed) {
      const firstError = result.failed?.[0]?.error || "请刷新后重试";
      toast(`已整理 ${applied} 条，${failed} 条失败：${firstError}`, "warning");
    } else {
      toast(`已整理 ${applied} 条。`);
    }
    await loadPillar("knowledge", { keepChapter: true });
  } catch (error) {
    toast(error.message, "error");
  }
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
        <p class="field-help">勾选后一次整理；只会移动，不会删除。</p>
      </div>
      <div class="pillar-suggestion-tools">
        <label class="check-label"><input type="checkbox" data-inbox-select-all> 全选</label>
        <button type="button" class="text-button" data-inbox-high${highCount ? "" : " disabled"}>只选高置信（${highCount}）</button>
        <button type="button" class="button secondary" data-inbox-apply disabled>整理选中（0）</button>
      </div>
    </div>
    ${[...groups.entries()].map(([target, group]) => `<div class="pillar-suggestion-group">
      <p class="pillar-suggestion-group-title"><span>${escapeHtml(target)}</span><span>${group.length} 条</span></p>
      ${group.map((item) => `<div class="pillar-suggestion-row" data-inbox-row="${escapeHtml(item.proposal_id)}" data-inbox-target="${escapeHtml(target)}" data-confidence="${escapeHtml(item.confidence)}">
        <label class="pillar-suggestion-check">
          <input type="checkbox" data-inbox-select value="${escapeHtml(item.proposal_id)}">
          <span>
            <span class="pillar-suggestion-title"><strong>${escapeHtml(item.title)}</strong><span class="pillar-confidence ${escapeHtml(item.confidence)}">${escapeHtml(CONFIDENCE_LABEL[item.confidence] || item.confidence)}</span></span>
            <span class="field-help">${escapeHtml(item.reason)}</span>
          </span>
        </label>
        <div class="pillar-suggestion-actions">
          <button type="button" class="small-button" data-note-path="${escapeHtml(item.path)}">打开</button>
          <button type="button" class="small-button" data-inbox-single="${escapeHtml(item.proposal_id)}">移动</button>
        </div>
      </div>`).join("")}
    </div>`).join("")}
  </section>`;
  // 已进入建议卡的条目不再在下方列表重复出现；想读原文用「打开」。
  const suggestedPaths = new Set(items.map((item) => item.path));
  document.querySelectorAll("#knowledge-view .pillar-note-row[data-note-path]").forEach((row) => {
    if (suggestedPaths.has(row.dataset.notePath)) row.hidden = true;
  });
  updateInboxSelection(host);
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

function focusOpsForm(dataset) {
  let attempts = 0;
  const timer = window.setInterval(() => {
    const input = document.querySelector(`#ops-view [data-ops-form="${dataset}"] input[name="date"]`);
    if (input) {
      window.clearInterval(timer);
      input.focus({ preventScroll: true });
      input.scrollIntoView({ block: "center", behavior: "smooth" });
      return;
    }
    attempts += 1;
    if (attempts > 40) window.clearInterval(timer);
  }, 80);
}

function bindPillarEvents() {
  document.addEventListener("click", async (event) => {
    const steward = event.target.closest?.("[data-steward-open]");
    if (steward) {
      openSteward(steward.dataset.stewardOpen, {
        onOpenNote: (path) => openNote(path),
        onApplied: () => loadPillar(steward.dataset.stewardOpen, { keepChapter: true }),
      });
      return;
    }
    const retry = event.target.closest?.("[data-pillar-retry]");
    if (retry) {
      loadPillarModule(retry.dataset.pillarRetry);
      return;
    }
    const opsQuick = event.target.closest?.("[data-ops-quick]");
    if (opsQuick) {
      const dataset = opsQuick.dataset.opsQuick;
      const target = document.querySelector(`#ops-view [data-pillar-chapter="ops"][data-chapter="${dataset}"]`);
      target?.click();
      focusOpsForm(dataset);
      return;
    }
    const capture = event.target.closest?.("[data-pillar-capture]");
    if (capture) {
      openCapture(capture.dataset.pillarCapture,
                  capture.dataset.captureChapter || "",
                  capture.dataset.captureType || "");
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
      try {
        const kind = review.dataset.review;
        const result = kind === "monthly"
          ? await api("/api/reviews/monthly", { method: "POST", body: JSON.stringify({}) })
          : await api("/api/reviews/weekly", { method: "POST", body: JSON.stringify({}) });
        toast(result.created ? "已生成复盘骨架。" : "这周的复盘已经存在，直接打开。");
        openNote(result.path);
      } catch (error) {
        toast(error.message, "error");
      }
      return;
    }
    const inboxHigh = event.target.closest?.("[data-inbox-high]");
    if (inboxHigh) {
      const host = inboxHigh.closest("[data-inbox-suggestions]");
      host?.querySelectorAll("[data-inbox-row]").forEach((row) => {
        const box = row.querySelector("[data-inbox-select]");
        if (box) box.checked = row.dataset.confidence === "high";
      });
      updateInboxSelection(host);
      return;
    }
    const inboxApply = event.target.closest?.("[data-inbox-apply]");
    if (inboxApply) {
      const host = inboxApply.closest("[data-inbox-suggestions]");
      const ids = [...(host?.querySelectorAll("[data-inbox-select]:checked") || [])]
        .map((box) => box.value);
      await applyInboxProposals(ids, inboxApply);
      return;
    }
    const inboxSingle = event.target.closest?.("[data-inbox-single]");
    if (inboxSingle) {
      await applyInboxProposals([inboxSingle.dataset.inboxSingle], inboxSingle);
      return;
    }
    const identity = event.target.closest?.("[data-open-identity]");
    if (identity) {
      if (!identity.dataset.openIdentity) return;
      if (!state.overviews.self?.identity?.exists) {
        try {
          await api("/api/identity", {
            method: "PUT",
            body: JSON.stringify({
              revision: "",
              notes: "在这里写下：我是谁、我相信什么、我要去哪里。\n",
            }),
          });
          await loadPillar("self", { keepChapter: true });
        } catch (error) {
          toast(error.message, "error");
          return;
        }
      }
      openVaultNote(state.overviews.self?.vault_name || "", identity.dataset.openIdentity);
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
  document.addEventListener("change", (event) => {
    const selectAll = event.target.closest?.("[data-inbox-select-all]");
    if (selectAll) {
      const host = selectAll.closest("[data-inbox-suggestions]");
      host?.querySelectorAll("[data-inbox-select]").forEach((box) => { box.checked = selectAll.checked; });
      updateInboxSelection(host);
      return;
    }
    if (event.target.closest?.("[data-inbox-select]")) {
      updateInboxSelection(event.target.closest("[data-inbox-suggestions]"));
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
      ${isTaskNote ? "" : `<section class="pillar-note-tags">
        <p class="eyebrow">标签</p>
        <div class="pillar-tag-list" data-tag-list></div>
        <form class="pillar-tag-form" data-tag-form>
          <input name="tag" maxlength="32" placeholder="添加标签，回车保存" aria-label="添加标签">
          <button type="submit" class="small-button">添加</button>
        </form>
      </section>`}
      <div class="pillar-dialog-foot">
        <div class="pillar-note-actions">
          ${isTaskNote ? "" : `<select data-note-target aria-label="移动到章节">
            ${chapterList(note.pillar).map((item) => `<option value="${escapeHtml(item.key)}"${item.key === note.chapter ? " selected" : ""}>${escapeHtml(item.title)}</option>`).join("")}
          </select>
          <button type="button" class="button secondary" data-move-note>移动</button>`}
          <button type="button" class="button secondary" data-open-note>在 Obsidian 打开</button>
          <button type="button" class="small-button" data-copy-wikilink title="复制可直接粘贴到 Obsidian 的双链">复制双链</button>
          <button type="button" class="small-button" data-copy-path>复制路径</button>
        </div>
        ${isTaskNote ? `<span class="field-help">任务由工作台管理；清单、日期和完成状态请在工作台修改。</span>` : ""}
      </div>`;
    document.body.append(dialog);
    const close = () => {
      dialog.close();
      dialog.remove();
      trigger?.focus?.({ preventScroll: true });
    };
    dialog.querySelector("[data-close-note]")?.addEventListener("click", close);
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
    note.tags = Array.isArray(note.tags) ? note.tags : [];
    const tagList = dialog.querySelector("[data-tag-list]");
    const tagForm = dialog.querySelector("[data-tag-form]");
    let tagSaving = false;
    if (tagList && tagForm) {
    const renderTags = () => {
      tagList.innerHTML = note.tags.length
        ? note.tags.map((tag) => `<span class="pillar-tag">#${escapeHtml(tag)}<button type="button" data-remove-tag="${escapeHtml(tag)}" aria-label="移除标签 ${escapeHtml(tag)}" title="移除标签">×</button></span>`).join("")
        : `<span class="field-help">还没有标签。</span>`;
    };
    const saveTags = async (nextTags) => {
      if (tagSaving) return;
      tagSaving = true;
      const input = tagForm.querySelector('input[name="tag"]');
      const button = tagForm.querySelector('button[type="submit"]');
      if (input) input.disabled = true;
      if (button) button.disabled = true;
      try {
        const updated = await api(`/api/notes/update?path=${encodeURIComponent(note.path)}`, {
          method: "POST",
          body: JSON.stringify({ tags: nextTags }),
        });
        note.tags = Array.isArray(updated.tags) ? updated.tags : [];
        renderTags();
        toast("标签已更新。");
        loadPillar(note.pillar, { keepChapter: true }).catch(() => {});
      } catch (error) {
        toast(error.message, "error");
      } finally {
        tagSaving = false;
        if (input) input.disabled = false;
        if (button) button.disabled = false;
      }
    };
    renderTags();
    tagForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (tagSaving) return;
      const input = tagForm.querySelector('input[name="tag"]');
      const additions = String(input?.value || "").split(/[,，]/)
        .map((item) => item.trim().replace(/^#/, ""))
        .filter(Boolean);
      if (!additions.length) return;
      const next = [...note.tags];
      for (const tag of additions) if (!next.includes(tag)) next.push(tag);
      input.value = "";
      await saveTags(next);
    });
    tagList.addEventListener("click", async (event) => {
      const remove = event.target.closest?.("[data-remove-tag]");
      if (!remove || tagSaving) return;
      await saveTags(note.tags.filter((tag) => tag !== remove.dataset.removeTag));
    });
    }
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
    dialog.querySelector("[data-move-note]")?.addEventListener("click", async () => {
      const chapter = dialog.querySelector("[data-note-target]")?.value;
      if (!chapter || chapter === note.chapter) return;
      try {
        const moved = await api("/api/notes/move", {
          method: "POST",
          body: JSON.stringify({ path: note.path, chapter }),
        });
        close();
        toast(`已移动到「${chapterTitle(moved.pillar, moved.chapter)}」。`);
        await loadPillar(note.pillar, { keepChapter: true });
      } catch (error) {
        toast(error.message, "error");
      }
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

async function openTodayJournal() {
  // 日记只有一份权威文件：直接复用工作台的日记接口，绝不另建同名笔记。
  const day = shanghaiDay();
  try {
    const result = await api("/api/journal/note/generate", {
      method: "POST",
      body: JSON.stringify({ day, mode: "create" }),
    });
    const relpath = result.relpath || `00_人生路/01_日记/${day}.md`;
    if (!String(relpath).startsWith("00_人生路/")) {
      toast("这篇日记还在旧目录，请先在设置 →「库结构与迁移」里迁移。", "warning");
      return;
    }
    toast(result.created ? "已创建今天的日记。" : "今天已经有日记，直接打开。");
    openNote(relpath);
    loadPillar("life", { keepChapter: true }).catch(() => {});
  } catch (error) {
    // 只读模式下不允许生成；已有日记仍然可以打开。
    if (error.status === 409) {
      try {
        const existing = await api(`/api/journal/note?day=${encodeURIComponent(day)}`);
        if (existing.exists && String(existing.relpath || "").startsWith("00_人生路/")) {
          openNote(existing.relpath);
          return;
        }
        if (existing.exists) {
          toast("这篇日记还在旧目录，请先在设置 →「库结构与迁移」里迁移。", "warning");
          return;
        }
      } catch { /* 使用下面统一的错误提示 */ }
    }
    toast(error.message, "error");
  }
}

function openCapture(view, chapter = "", type = "") {
  const meta = PILLAR_META[view];
  const chapters = chapterList(view);
  const defaultChapter = chapter || meta.defaultChapter || chapters[0]?.key || "inbox";
  if (view === "life" && chapter === "journal") {
    openTodayJournal();
    return;
  }
  if (["habit", "health", "finance"].includes(chapter)) {
    toast("习惯、体重和账目请用页面上的专用面板记录。", "warning");
    return;
  }
  // 日记、习惯、体重、账目各有专用入口；普通捕获不再列出这些结构化章节，避免写错格式。
  const structured = new Set(["journal", "habit", "health", "finance"]);
  const captureChapters = chapters.filter((item) => !structured.has(item.key));
  const trigger = document.activeElement;
  const dialog = document.createElement("dialog");
  dialog.className = "pillar-dialog pillar-capture-dialog";
  dialog.innerHTML = `
    <form method="dialog" class="pillar-capture-form">
      <div class="pillar-dialog-head">
        <div><p class="eyebrow">${escapeHtml(meta.title)}</p><h2>记一条</h2></div>
        <button type="button" class="text-button" data-capture-close>关闭</button>
      </div>
      <label class="field"><span>标题</span><input name="title" maxlength="200" required autofocus></label>
      <label class="field"><span>正文</span><textarea name="body" rows="6" maxlength="200000"></textarea></label>
      <label class="field"><span>放到</span>
        <select name="chapter">
          ${captureChapters.map((item) => `<option value="${escapeHtml(item.key)}"${item.key === defaultChapter ? " selected" : ""}>${escapeHtml(item.title)}</option>`).join("")}
        </select>
        <span class="pillar-capture-hint">不确定放哪也没关系，之后可以再移动。</span>
      </label>
      <details class="pillar-capture-more">
        <summary>更多选项（标签、来源）</summary>
        <div class="pillar-capture-grid">
          <label class="field"><span>标签（逗号分隔）</span><input name="tags" maxlength="200"></label>
          <label class="field"><span>来源（链接 / 书名 / 课程）</span><input name="source" maxlength="500"></label>
        </div>
      </details>
      <p class="status-message" data-capture-status role="status"></p>
      <div class="form-actions">
        <button type="button" class="button secondary" data-capture-close>取消</button>
        <button type="submit" class="button primary" title="Ctrl/⌘ Enter 保存">保存</button>
      </div>
    </form>`;
  document.body.append(dialog);
  const close = () => {
    dialog.close();
    dialog.remove();
    trigger?.focus?.({ preventScroll: true });
  };
  dialog.querySelectorAll("[data-capture-close]").forEach((button) => button.addEventListener("click", close));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  dialog.querySelector("form").addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      dialog.querySelector('form button[type="submit"]')?.click();
    }
  });
  dialog.querySelector("form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const title = String(data.get("title") || "").trim();
    if (!title) return;
    const status = dialog.querySelector("[data-capture-status]");
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    status.textContent = "保存中…";
    try {
      const created = await api("/api/capture", {
        method: "POST",
        body: JSON.stringify({
          title,
          body: String(data.get("body") || ""),
          pillar: view,
          chapter: String(data.get("chapter") || defaultChapter),
          note_type: type,
          tags: String(data.get("tags") || "").split(/[,，]/).map((item) => item.trim()).filter(Boolean),
          source: String(data.get("source") || ""),
        }),
      });
      close();
      toast(`已记录到「${chapterTitle(view, created.chapter)}」。`);
      await loadPillar(view, { keepChapter: true });
    } catch (error) {
      status.textContent = error.message;
      status.classList.add("error");
      submit.disabled = false;
    }
  });
  if (typeof dialog.showModal === "function") dialog.showModal();
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
