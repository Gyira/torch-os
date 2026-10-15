import { $, api, escapeHtml, formatDate, renderState, setStatus, sourceLocationMarkup } from "./core.js";

// Read-only "认识我" summary: confirmed usable memories, goals, recent rhythm.
// Nothing here writes data, approves memories or calls a model.

let requestId = 0;
const kindLabels = { fact: "事实", experience: "经历", belief: "信念", goal: "目标", preference: "偏好", note: "记录", project: "项目" };
const memoryStatusLabels = { active: "已确认", candidate: "待确认", rejected: "已拒绝", archived: "已归档" };
const goalStatusLabels = { active: "进行中", paused: "已暂停", completed: "已完成", archived: "已归档" };
const quickQuestions = ["我是谁？", "我当前的主要目标是什么？", "我过去反复遇到什么问题？", "根据我的记录，我最近应该优先做什么？"];
const number = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;

function metricCard(label, value, note) {
  return `<article class="profile-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>`;
}

function memoryItem(memory) {
  const kind = kindLabels[memory.kind] || memory.kind || "记忆";
  const date = memory.source_date || memory.valid_from || memory.updated_at;
  const usage = memory.usage || {};
  const uses = number(usage.uses);
  const usageLine = uses
    ? `近 30 天被 ${uses} 次回答引用${usage.last_used_at ? ` · 最近 ${formatDate(usage.last_used_at)}` : ""}`
    : "近 30 天尚未被回答引用";
  return `<article class="profile-memory"><div class="profile-memory-head"><span class="eyebrow">${escapeHtml(kind)}</span><span class="badge active">已确认</span></div><h3>${escapeHtml(memory.title || "未命名记忆")}</h3><p class="preserve-lines">${escapeHtml(memory.content || "")}</p><p class="meta">来源：<code>${escapeHtml(memory.source_path || "未标注")}</code>${date ? ` · 日期：${escapeHtml(formatDate(date))}` : ""}</p><p class="meta usage-note">回答使用：${escapeHtml(usageLine)}</p>${sourceLocationMarkup(memory)}${memory.document_id ? `<button class="text-button" data-document-id="${escapeHtml(memory.document_id)}">查看原文</button>` : ""}</article>`;
}

function usageItem(entry) {
  const question = entry.question || "（未记录触发问题）";
  const status = memoryStatusLabels[entry.status] || entry.status || "未知状态";
  const title = entry.title || "未命名记忆";
  return `<li><p class="usage-question">问：${escapeHtml(question)}</p><p class="meta">用到《${escapeHtml(title)}》· 当前${escapeHtml(status)} · ${escapeHtml(formatDate(entry.used_at))}</p></li>`;
}

function goalItem(goal) {
  const progress = goal.progress == null ? "进度未设置" : `进度 ${escapeHtml(goal.progress)}%`;
  return `<li><strong>${escapeHtml(goal.title || "未命名目标")}</strong><span class="meta">${escapeHtml(goalStatusLabels[goal.status] || goal.status || "未知状态")} · ${progress}</span></li>`;
}

function rhythmLine(label, value, unit) {
  return `<div class="rhythm-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(unit)}</small></div>`;
}

function renderProfile(data, activity) {
  const memories = data.memories || {};
  const documents = data.documents || {};
  const goals = data.goals || {};
  const rhythm = data.rhythm || {};
  const blocked = memories.active_blocked || {};
  const blockedTotal = Object.values(blocked).reduce((total, value) => total + number(value), 0);
  const rules = Array.isArray(data.usage_rules) ? data.usage_rules : [];
  const excluded = data.excluded || {};
  const relationship = data.relationship_vault || {};
  const activeMemories = Array.isArray(data.active_memories) ? data.active_memories : [];
  const goalItems = Array.isArray(goals.items) ? goals.items : [];
  const usage = data.usage || {};
  const recentUsage = Array.isArray(usage.recent) ? usage.recent : [];
  const pool = data.candidate_pool || {};
  const poolSources = Array.isArray(pool.by_source) ? pool.by_source : [];
  const poolProgress = Array.isArray(pool.by_source_progress)
    ? pool.by_source_progress
    : poolSources.map((item) => ({ path: item.path, candidate: item.count, reviewed: null, total: null }));
  const poolMonths = Array.isArray(pool.by_month) ? pool.by_month : [];
  const poolRecordMonths = Array.isArray(pool.by_record_month) ? pool.by_record_month : [];
  const usageWindow = number(usage.window_days) || 30;

  const cards = `<div class="profile-cards">${metricCard("已确认可用记忆", number(memories.active_usable), "只有这些会参与回答")}${metricCard("待确认", number(memories.candidate), "需要你的审核")}${metricCard("已索引资料", number(documents.available), "本地可追溯来源")}${metricCard("待办任务", number(goals.open_tasks), "来自目标与任务")}</div>`;

  const usagePanel = `<section class="profile-panel"><h2>它如何使用你的记录</h2><ul class="profile-rules">${rules.map((rule) => `<li>${escapeHtml(rule)}</li>`).join("")}</ul><p class="field-help">已确认但暂时不可用的记忆：${escapeHtml(blockedTotal)} 条（来源不可用 ${escapeHtml(number(blocked.source_unavailable))} · 非个人分区 ${escapeHtml(number(blocked.not_personal))} · 来源已变更 ${escapeHtml(number(blocked.stale_source))} · 已过期 ${escapeHtml(number(blocked.expired))}）。候选 ${escapeHtml(number(excluded.candidate))} 条不会进入云端上下文。</p><p class="field-help">${escapeHtml(relationship.note || "关系数据分区尚未接入。")}云端回答默认关闭，开启时才发送本次问题与相关已确认记忆。</p></section>`;

  const memoryPanel = `<section class="profile-panel"><h2>已确认记忆</h2>${activeMemories.length ? activeMemories.map(memoryItem).join("") : '<p class="empty-state">还没有已确认记忆。到“记忆”页确认候选后，这里会显示它们。</p>'}<p class="field-help">此处只列出已确认且来源有效的记忆；修改后需要重新确认的内容会回到候选区。近 ${escapeHtml(usageWindow)} 天内有 ${escapeHtml(number(usage.memories_used))} 条记忆被回答引用过。</p></section>`;

  const usageRecordPanel = `<section class="profile-panel"><h2>回答使用记录</h2>${recentUsage.length ? `<ul class="profile-usage">${recentUsage.map(usageItem).join("")}</ul>` : '<p class="empty-state">还没有保存过的回答引用已确认记忆。到“对话”提问后，这里会列出每次回答实际用到的记忆。</p>'}<p class="field-help">只记录已经保存的回答引用；发送前的预览和本地草稿不会计入。记忆后来被撤回或归档时，历史引用仍保留并标注当前状态。</p></section>`;

  const recordColumn = Array.isArray(pool.by_record_month)
    ? `<div><h3>按原始记录月份</h3>${poolRecordMonths.length ? `<ul>${poolRecordMonths.map((item) => `<li><span>${escapeHtml(item.month)}</span><span>${escapeHtml(number(item.count))} 条</span></li>`).join("")}</ul>` : '<p class="empty-state">还没有能解析出日期的候选。</p>'}<p class="field-help">未标注 ${escapeHtml(number(pool.record_unknown_total))} 条 · ${escapeHtml(pool.record_basis || "按来源元数据或路径中的日期解析")}</p></div>`
    : "";
  const poolPanel = `<section class="profile-panel"><h2>候选池分布</h2><p class="meta">共 ${escapeHtml(number(pool.total))} 条待确认内容（${escapeHtml(pool.basis || "按提取时间统计，非原始记录日期")}）。</p><div class="profile-pool"><div><h3>按来源</h3>${poolProgress.length ? `<ul>${poolProgress.map((item) => `<li><code>${escapeHtml(item.path)}</code><span>${escapeHtml(number(item.candidate))} 待确认${item.total == null ? "" : ` / 共 ${escapeHtml(number(item.total))}（已审核 ${escapeHtml(number(item.reviewed))}）`}</span></li>`).join("")}</ul>` : '<p class="empty-state">没有待确认内容。</p>'}</div><div><h3>按提取月份</h3>${poolMonths.length ? `<ul>${poolMonths.map((item) => `<li><span>${escapeHtml(item.month)}</span><span>${escapeHtml(number(item.count))} 条</span></li>`).join("")}</ul>` : '<p class="empty-state">没有待确认内容。</p>'}</div>${recordColumn}</div><p class="field-help">候选内容不会参与回答或云端上下文；这里的分布只帮你判断先审核哪些来源。</p></section>`;

  const goalPanel = `<section class="profile-panel"><h2>目标快照</h2>${goalItems.length ? `<ul class="profile-goals">${goalItems.map(goalItem).join("")}</ul>` : '<p class="empty-state">还没有进行中的目标。到“目标与任务”写下一个方向。</p>'}<p class="meta">进行中 ${escapeHtml(number(goals.active))} · 已暂停 ${escapeHtml(number(goals.paused))} · 已完成 ${escapeHtml(number(goals.completed))} · 开放任务 ${escapeHtml(number(goals.open_tasks))} · 已完成任务 ${escapeHtml(number(goals.completed_tasks))}</p></section>`;

  const rhythmPanel = `<section class="profile-panel"><h2>最近七天节奏</h2><div class="profile-rhythm">${rhythmLine("日记天数", number(rhythm.journal_days_last_7), "天")}${rhythmLine("身体记录", number(rhythm.body_record_days_last_7), "天")}${rhythmLine("习惯完成", number(rhythm.habit_done_last_7), "次")}${rhythmLine("学习时长", number(rhythm.study_minutes_last_7), "分钟")}${rhythmLine("进行中习惯", number(rhythm.habits_active), "个")}${rhythmLine("进行中科目", number(rhythm.study_subjects_active), "个")}</div><p class="field-help">只统计你填写过的记录；没有记录不代表没有学习或失败，也不据此推断人格。身体记录只计天数，不读取数值。</p></section>`;

  const library = data.library_health || null;
  const AUDIT_LABELS = { too_large: "超大", unreadable: "无法读取", decode_failed: "编码失败", invalid_frontmatter: "front-matter 无效", missing_frontmatter: "缺 front-matter", missing_fields: "字段缺失", duplicate_id: "id 重复", empty_body: "空正文" };
  let libraryPanel = "";
  if (library) {
    const vault = library.vault || {};
    const audit = library.audit || {};
    const codes = Object.entries(audit.by_code || {}).filter(([, value]) => value)
      .map(([code, value]) => `${escapeHtml(AUDIT_LABELS[code] || code)} ${escapeHtml(number(value))}`).join(" · ");
    const recent = (library.recent || []).map((item) => `<li>${escapeHtml(item.title || "未命名")}<span class="meta">${escapeHtml(item.indexed_at || "")}</span></li>`).join("");
    libraryPanel = `<section class="profile-panel" data-panel="library"><h2>资料健康度</h2><div class="profile-rhythm">${rhythmLine("已索引资料", number(library.documents), "篇")}${rhythmLine("Vault Markdown", number(vault.markdown_files), "篇")}${rhythmLine("读写模式", vault.read_only ? "只读" : "可写", "")}${rhythmLine("体检问题", number(audit.issues), "项")}</div>${recent ? `<ul class="profile-usage">${recent}</ul>` : ""}<p class="meta">体检 ${escapeHtml(number(audit.checked))} 篇 · 正常 ${escapeHtml(number(audit.ok))} 篇${codes ? ` · 问题：${codes}` : ""}${audit.truncated ? ` · 另有 ${escapeHtml(number(audit.truncated))} 项未列出` : ""}</p><p class="field-help">${escapeHtml(library.note || "只读摘要：不含正文与具体路径。")}</p></section>`;
  }

  let activityPanel;
  if (activity && activity.window) {
    const days = number(activity.window.days) || 30;
    const journal = activity.journal || {};
    const body = activity.body || {};
    const habits = activity.habits || {};
    const study = activity.study || {};
    const notes = Array.isArray(activity.notes) ? activity.notes : [];
    activityPanel = `<section class="profile-panel" data-panel="activity"><h2>近 ${escapeHtml(days)} 天记录覆盖面</h2><div class="profile-rhythm">${rhythmLine("每日记录", number(journal.days_recorded), "天")}${rhythmLine("身体记录", number(body.days_recorded), "天")}${rhythmLine("习惯记录", number(habits.days_recorded), "天")}${rhythmLine("学习记录", number(study.days_recorded), "天")}${rhythmLine("习惯完成", number(habits.done), "次")}${rhythmLine("学习时长", number(study.minutes), "分钟")}</div><p class="meta">窗口：${escapeHtml(activity.window.start || "")} 至 ${escapeHtml(activity.window.end || "")} · 进行中习惯 ${escapeHtml(number(habits.active_definitions))} 个 · 明确跳过 ${escapeHtml(number(habits.skipped))} 次 · 学习涉及科目 ${escapeHtml(number(study.subjects_recorded))} 个</p>${notes.length ? `<p class="field-help">${notes.map((note) => escapeHtml(note)).join(" ")}</p>` : ""}</section>`;
  } else {
    activityPanel = '<section class="profile-panel" data-panel="activity"><h2>近 30 天记录覆盖面</h2><p class="empty-state">暂时无法读取近 30 天统计。其他部分不受影响，可以点“刷新”重试。</p></section>';
  }

  const quickPanel = `<section class="profile-panel"><h2>可以直接问</h2><div class="profile-quick">${quickQuestions.map((question) => `<button class="button secondary" data-ask-question="${escapeHtml(question)}">${escapeHtml(question)}</button>`).join("")}</div></section>`;

  $("#profile-body").innerHTML = cards + usagePanel + memoryPanel + usageRecordPanel + poolPanel + goalPanel + rhythmPanel + libraryPanel + activityPanel + quickPanel;
}

export async function loadProfile() {
  const request = ++requestId;
  setStatus("#profile-status", "正在读取本地数据…");
  renderState($("#profile-body"), "loading", "正在汇总已确认记忆、目标与最近节奏…");
  try {
    const [data, activity] = await Promise.all([
      api("/api/profile"),
      api("/api/profile/activity?days=30").catch(() => null),
    ]);
    if (request !== requestId) return;
    renderProfile(data, activity);
    setStatus("#profile-status", `已确认可用 ${number(data.memories?.active_usable)} 条 · 待确认 ${number(data.memories?.candidate)} 条 · 近 30 天被引用 ${number(data.usage?.memories_used)} 条`, "success");
  } catch (error) {
    if (request !== requestId) return;
    renderState($("#profile-body"), "error", error.message, "profile");
    setStatus("#profile-status", "读取失败", "error");
  }
}

export function initProfile() {
  $("#profile-refresh").addEventListener("click", loadProfile);
  document.addEventListener("click", (event) => {
    if (event.target.closest('[data-retry="profile"]')) loadProfile();
  });
}
