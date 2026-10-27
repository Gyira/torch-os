// 习惯打卡的纯 HTML 片段（2026-10-28）。不 import core/DOM，node 可直接测。
import {
  DEFAULT_HABIT_ICON, ENTRY_STATUS_LABELS, HABIT_STATUS_LABELS, WEEKDAY_CALENDAR, WEEKDAY_LONG,
  frequencyLabel, monthLabel, percentLabel, statusLabel, streakLabel, targetProgress,
} from "./habits-model.js";

const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[character]));

function dayNumber(day) {
  return String(day || "").slice(8).replace(/^0/, "") || "—";
}

function percentValue(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return 0;
  return Math.max(0, Math.min(100, Math.round(Number(value) * 100)));
}

export function weekStripHtml(week, today = "") {
  const days = Array.isArray(week?.days) ? week.days : [];
  if (!days.length) return '<p class="habit-week-empty field-help">正在读取最近 7 天…</p>';
  return `<div class="habit-week-strip" role="list" aria-label="最近 7 天习惯完成情况">` +
    days.map((day) => {
      const percent = percentValue(day.completion_rate);
      const complete = Number(day.scheduled) > 0
        && Number(day.done) >= Math.max(0, Number(day.scheduled) - Number(day.skipped));
      const classes = ["habit-week-cell"];
      if (day.date === today || day.is_today) classes.push("is-today");
      if (complete) classes.push("is-complete");
      if (!Number(day.scheduled)) classes.push("is-empty");
      const label = `${day.date} ${WEEKDAY_LONG[Number(day.weekday) || 0] || ""}`
        + `，计划 ${Number(day.scheduled) || 0}，完成 ${Number(day.done) || 0}`;
      return `<div class="${classes.join(" ")}" role="listitem" title="${esc(label)}" aria-label="${esc(label)}">`
        + `<span class="habit-week-label">${esc(WEEKDAY_LONG[Number(day.weekday) || 0] || "")}</span>`
        + `<span class="habit-week-date">${esc(dayNumber(day.date))}</span>`
        + `<span class="habit-week-ring" data-habit-progress="${percent}" aria-hidden="true"></span>`
        + `</div>`;
    }).join("") + "</div>";
}

export function habitCardHtml(habit, options = {}) {
  const status = habit?.entry_today?.status || "unrecorded";
  const checked = status === "done";
  const skipped = status === "skipped";
  const classes = ["habit-card"];
  if (String(options.selectedId) === String(habit.id)) classes.push("is-selected");
  if (checked) classes.push("is-checked");
  if (skipped) classes.push("is-skipped");
  if (habit.status === "archived") classes.push("is-archived");
  if (habit.ended) classes.push("is-ended");
  const stats = habit.stats || {};
  const icon = habit.icon || DEFAULT_HABIT_ICON;
  const meta = [habit.group, frequencyLabel(habit.frequency)].filter(Boolean).join(" · ");
  const checkLabel = checked ? `取消今天的打卡：${habit.title}` : `打卡：${habit.title}`;
  const badges = [];
  if (habit.status === "archived") badges.push('<span class="habit-card-badge">已归档</span>');
  if (habit.ended) badges.push('<span class="habit-card-badge">已结束</span>');
  if (skipped) badges.push('<span class="habit-card-badge is-skip">休息</span>');
  return `<div class="${classes.join(" ")}" role="listitem" tabindex="0" data-habit-id="${esc(habit.id)}" data-habit-color="${esc(habit.color || "")}" aria-label="${esc(habit.title)}" aria-current="${String(options.selectedId) === String(habit.id) ? "true" : "false"}">`
    + `<button type="button" class="habit-card-check${checked ? " is-checked" : ""}${skipped ? " is-skipped" : ""}" data-habit-check="${esc(habit.id)}" aria-label="${esc(checkLabel)}" title="${esc(checkLabel)}"${habit.status === "archived" || !habit.scheduled_today ? " disabled" : ""}>${checked ? "✓" : skipped ? "—" : ""}</button>`
    + `<span class="habit-card-icon" aria-hidden="true">${esc(icon)}</span>`
    + `<span class="habit-card-body"><span class="habit-card-title">${esc(habit.title)}</span>`
    + `<span class="habit-card-meta">${esc(meta)}${habit.reminder ? ` · ⏰ ${esc(habit.reminder)}` : ""}</span>`
    + `${badges.length ? `<span class="habit-card-badges">${badges.join("")}</span>` : ""}</span>`
    + `<span class="habit-card-stats"><span title="当前连续">🔥 ${esc(streakLabel(stats))}</span><span title="总打卡">🏅 ${Number(stats.total_done) || 0}</span></span>`
    + `<button type="button" class="habit-card-menu" data-habit-menu="${esc(habit.id)}" aria-label="${esc(habit.title)} 的更多操作">…</button>`
    + `</div>`;
}

export function habitCardsHtml(habits, selectedId) {
  const list = Array.isArray(habits) ? habits : [];
  if (!list.length) return habitEmptyHtml();
  return `<div class="habit-card-list" role="list">` +
    list.map((habit) => habitCardHtml(habit, { selectedId })).join("") + "</div>";
}

export function habitEmptyHtml() {
  return `<section class="habit-empty">
    <p class="habit-empty-title">还没有习惯</p>
    <p class="field-help">新建一个每天、每周或每月重复的习惯；它会自动出现在今日任务和四象限。</p>
    <button type="button" class="button primary" data-habit-add>＋ 添加习惯</button>
  </section>`;
}

export function habitStatsHtml(habit) {
  const stats = habit?.stats || {};
  const cells = [
    { label: "月打卡", value: `${Number(stats.month_done) || 0}天` },
    { label: "总打卡", value: `${Number(stats.total_done) || 0}天` },
    { label: "月完成率", value: percentLabel(stats.month_completion_rate) },
    { label: "当前连续", value: streakLabel(stats) },
  ];
  const target = stats.target_days
    ? {
      targetDays: Number(stats.target_days),
      elapsed: Number(stats.target_elapsed_days) || 0,
      percent: Math.round((Number(stats.target_percent) || 0) * 100),
    }
    : targetProgress(habit, habit?.entry_today?.date || "");
  return `<div class="habit-stat-grid" role="list" aria-label="习惯统计">`
    + cells.map((cell) => `<div class="habit-stat" role="listitem"><span class="habit-stat-value">${esc(cell.value)}</span><span class="habit-stat-label">${esc(cell.label)}</span></div>`).join("")
    + `</div>`
    + (target ? `<div class="habit-target"><div class="habit-target-head"><span>坚持 ${target.elapsed} / ${target.targetDays} 天</span><span>${target.percent}%</span></div>`
      + `<div class="habit-target-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${target.percent}"><span data-habit-target-percent="${target.percent}"></span></div></div>` : "");
}

export function habitCalendarHtml(grid) {
  const cells = Array.isArray(grid?.cells) ? grid.cells : [];
  const weekdays = WEEKDAY_CALENDAR.map((label) => `<span class="habit-cal-weekday">${esc(label)}</span>`).join("");
  const body = cells.map((cell) => {
    const classes = ["habit-cal-cell"];
    if (!cell.inMonth) classes.push("is-outside");
    if (cell.future) classes.push("is-future");
    if (cell.today) classes.push("is-today");
    if (!cell.scheduled) classes.push("is-unscheduled");
    if (cell.extra) classes.push("is-extra");
    classes.push(`status-${cell.status}`);
    const status = cell.scheduled ? (ENTRY_STATUS_LABELS[cell.status] || "未记录") : "非计划日";
    const label = `${cell.date} ${status}`;
    return `<button type="button" class="${classes.join(" ")}" data-habit-cal-day="${esc(cell.date)}" aria-label="${esc(label)}" title="${esc(label)}"${cell.future || !cell.inMonth ? " disabled" : ""}>`
      + `<span class="habit-cal-day">${esc(cell.day)}</span><span class="habit-cal-dot" aria-hidden="true"></span></button>`;
  }).join("");
  return `<div class="habit-calendar" role="group" aria-label="${esc(grid?.title || "习惯月历")}">
    <div class="habit-cal-head">${weekdays}</div>
    <div class="habit-cal-grid">${body}</div>
  </div>`;
}

export function habitLogsHtml(logs, month = "") {
  const list = Array.isArray(logs) ? logs : [];
  const title = month ? `${monthLabel(month)}打卡日志` : "打卡日志";
  return `<section class="habit-logs" aria-label="${esc(title)}">
    <div class="habit-logs-head"><h4>${esc(title)}</h4><span class="meta">${list.length ? `${list.length} 条` : ""}</span></div>
    ${list.length ? `<ul class="habit-log-list">` + list.map((item) => {
      const status = statusLabel(item.status);
      const note = String(item.note || "").trim();
      return `<li class="habit-log-item"><span class="habit-log-date">${esc(String(item.date || "").slice(5))}</span>`
        + `<span class="habit-log-status status-${esc(item.status || "unrecorded")}">${esc(status)}</span>`
        + `<span class="habit-log-note">${note ? esc(note) : '<span class="field-help">没有备注</span>'}</span>`
        + `<span class="habit-log-time">${esc(String(item.updated_at || "").slice(11, 16))}</span></li>`;
    }).join("") + "</ul>" : '<p class="habit-log-empty field-help">这个月还没有打卡记录。</p>'}
  </section>`;
}

export function habitDetailHtml(habit, options = {}) {
  if (!habit) {
    return `<section class="habit-detail-empty"><p class="habit-empty-title">选择一个习惯</p><p class="field-help">左边点一张习惯卡，这里会显示月历、统计和打卡日志。</p></section>`;
  }
  const badges = [];
  if (habit.status === "archived") badges.push(HABIT_STATUS_LABELS.archived);
  if (habit.ended) badges.push("已结束");
  return `<article class="habit-detail" data-habit-detail="${esc(habit.id)}">
    <header class="habit-detail-head">
      <span class="habit-detail-icon" aria-hidden="true">${esc(habit.icon || DEFAULT_HABIT_ICON)}</span>
      <div><h3>${esc(habit.title)}</h3><p class="meta">${esc([habit.group, frequencyLabel(habit.frequency), habit.reminder ? `提醒 ${habit.reminder}` : ""].filter(Boolean).join(" · "))}</p></div>
      ${badges.length ? `<span class="habit-detail-badges">${badges.map((badge) => `<span class="habit-card-badge">${esc(badge)}</span>`).join("")}</span>` : ""}
    </header>
    ${habitStatsHtml(habit)}
    <div class="habit-calendar-head">
      <button type="button" class="text-button" data-habit-month-prev aria-label="上个月">‹</button>
      <strong>${esc(options.monthTitle || monthLabel(options.month || ""))}</strong>
      <button type="button" class="text-button" data-habit-month-next aria-label="下个月">›</button>
    </div>
    ${habitCalendarHtml(options.grid)}
    ${habitLogsHtml(options.logs, options.month)}
  </article>`;
}

export function habitGroupOptions(groups, current = "") {
  const names = (Array.isArray(groups) ? groups : []).map((group) => String(group.name || "")).filter(Boolean);
  const options = ['<option value="">全部分组</option>'];
  for (const name of names) {
    options.push(`<option value="${esc(name)}"${name === current ? " selected" : ""}>${esc(name)}</option>`);
  }
  return options.join("");
}

export function habitWarningsHtml(warnings) {
  const list = Array.isArray(warnings) ? warnings : [];
  if (!list.length) return "";
  return `<details class="habit-warnings"><summary>${list.length} 条读取提醒</summary><ul>` +
    list.map((item) => `<li>${esc(item.path || "习惯")}：${esc(item.reason || "未知原因")}</li>`).join("") +
    "</ul></details>";
}

export function habitWeekdayLabel(day) {
  const index = new Date(`${day}T00:00:00Z`).getUTCDay();
  return WEEKDAY_LONG[(index + 6) % 7] || "";
}

export function habitCountLabel(habits) {
  const list = Array.isArray(habits) ? habits : [];
  const done = list.filter((habit) => habit.entry_today?.status === "done").length;
  return list.length ? `${done} / ${list.length} 今天已完成` : "";
}
