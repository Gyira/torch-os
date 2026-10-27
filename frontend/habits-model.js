// 习惯打卡的纯函数（2026-10-28）：频率、月历、周条、目标、草稿校验。
// 无 DOM 依赖；日历沿用 tasks-model.monthCells 的周日开头（与滴答习惯页一致）。
import { dayOffset, monthCells, monthTitle } from "./tasks-model.js";

export const HABIT_LIST_NAME = "习惯打卡";
export const DEFAULT_HABIT_ICON = "🌱";
export const DEFAULT_HABIT_GROUP = "默认";
export const HABIT_ICON_CHOICES = ["🌱", "☀️", "📚", "🏃", "💧", "🧘", "✍️", "🎯", "🌙", "🥗", "🎵", "🧹"];
export const HABIT_COLOR_CHOICES = ["#3b82f6", "#8b5cf6", "#ef4444", "#f59e0b", "#10b981", "#06b6d4", "#ec4899", "#64748b"];
export const FREQUENCY_KINDS = [
  { value: "daily", label: "每天" },
  { value: "weekly", label: "每周" },
  { value: "monthly", label: "每月" },
  { value: "interval", label: "每隔 N 天" },
];
export const WEEKDAY_SHORT = ["一", "二", "三", "四", "五", "六", "日"];
export const WEEKDAY_LONG = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
export const WEEKDAY_CALENDAR = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
export const ENTRY_STATUS_LABELS = { done: "完成", skipped: "主动休息", unrecorded: "未记录" };
export const HABIT_STATUS_LABELS = { active: "进行中", archived: "已归档" };
export const PRIORITY_LABELS = { high: "高优先级", medium: "中优先级", low: "低优先级", none: "无优先级" };
export const TARGET_PRESETS = [7, 21, 30, 60, 100];

export function isHabitTask(task) {
  return Boolean(task && (task.kind === "habit" || task.virtual === true));
}

export function habitTaskKey(uid, day) {
  return `habit:${uid || ""}:${day || ""}`;
}

function asDate(day) {
  const [year, month, date] = String(day || "").split("-").map(Number);
  if (!year || !month || !date) return null;
  return { year, month, date };
}

export function dayDiff(later, earlier) {
  const left = asDate(later);
  const right = asDate(earlier);
  if (!left || !right) return 0;
  return Math.round((Date.UTC(left.year, left.month - 1, left.date)
    - Date.UTC(right.year, right.month - 1, right.date)) / 86400000);
}

export function frequencyLabel(frequency = {}) {
  const kind = String(frequency.kind || "daily");
  if (kind === "weekly") {
    const days = [...new Set((frequency.weekdays || []).map(Number))]
      .filter((day) => day >= 0 && day <= 6).sort((a, b) => a - b);
    if (days.length === 7) return "每天";
    return days.length ? `每周${days.map((day) => WEEKDAY_LONG[day]).join("、")}` : "每周";
  }
  if (kind === "monthly") {
    const days = [...new Set((frequency.month_days || []).map(Number))]
      .filter((day) => day >= 1 && day <= 31).sort((a, b) => a - b);
    return days.length ? `每月${days.map((day) => `${day}日`).join("、")}` : "每月";
  }
  if (kind === "interval") {
    const interval = Math.max(1, Number(frequency.interval_days) || 1);
    return `每隔${interval}天`;
  }
  return "每天";
}

export function targetLabel(targetDays) {
  const value = Number(targetDays);
  return !value || value <= 0 ? "永远" : `${value}天`;
}

export function targetProgress(habit, today) {
  const target = Number(habit?.target_days) || 0;
  if (!target) return null;
  const start = habit?.start_date || today;
  const elapsed = Math.max(0, Math.min(target, dayDiff(today, start) + 1));
  return {
    targetDays: target,
    elapsed,
    percent: Math.round((elapsed / target) * 100),
    ended: dayDiff(today, start) >= target,
  };
}

export function entryFor(entries, day) {
  return (Array.isArray(entries) ? entries : []).find((entry) => entry.date === day) || null;
}

export function monthGrid(entries, month, today) {
  const cells = monthCells(month).map((date) => {
    const entry = entryFor(entries, date);
    const scheduled = Boolean(entry?.scheduled);
    const status = entry?.status || "unrecorded";
    return {
      date,
      day: Number(date.slice(8)),
      inMonth: date.slice(0, 7) === month,
      scheduled,
      status,
      future: Boolean(today) && date > today,
      today: date === today,
      extra: Boolean(entry) && !scheduled && status === "done",
    };
  });
  return { month, title: monthTitle(month), cells };
}

export function monthLabel(month) {
  return monthTitle(month);
}

export function shiftMonth(month, delta) {
  const [year, number] = String(month || "").split("-").map(Number);
  if (!year || !number) return "";
  const value = new Date(Date.UTC(year, number - 1 + Number(delta || 0), 1));
  return `${value.getUTCFullYear()}-${String(value.getUTCMonth() + 1).padStart(2, "0")}`;
}

export function weekStripDays(week) {
  return Array.isArray(week?.days) ? week.days : [];
}

export function logsForHabit(logs, habitId) {
  return (Array.isArray(logs) ? logs : []).filter((item) => Number(item.habit_id) === Number(habitId));
}

export function groupNames(groups) {
  return (Array.isArray(groups) ? groups : []).map((item) => item.name).filter(Boolean);
}

export function percentLabel(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return `${Math.round(Number(value) * 100)}%`;
}

export function streakLabel(stats = {}) {
  const unit = stats.streak_unit || "天";
  return `${Number(stats.current_streak) || 0}${unit}`;
}

export function statusLabel(status) {
  return ENTRY_STATUS_LABELS[status] || "未记录";
}

export function defaultHabitDraft(today, groups = []) {
  const names = groupNames(groups);
  return {
    title: "",
    icon: DEFAULT_HABIT_ICON,
    color: "",
    group: names.includes(DEFAULT_HABIT_GROUP) ? DEFAULT_HABIT_GROUP : (names[0] || DEFAULT_HABIT_GROUP),
    frequency: { kind: "daily" },
    goal: "binary",
    start_date: today,
    target_days: null,
    reminder: "",
    auto_log: false,
    priority: "medium",
    cue: "",
    minimum_action: "",
    sort_order: null,
  };
}

export function normalizeFrequency(kind, values = {}) {
  if (kind === "weekly") {
    const weekdays = [...new Set((values.weekdays || []).map(Number))]
      .filter((day) => day >= 0 && day <= 6).sort((a, b) => a - b);
    return { kind: "weekly", weekdays };
  }
  if (kind === "monthly") {
    const monthDays = String(values.month_days_text ?? values.month_days ?? "")
      .split(/[,\s，、]+/).map((item) => Number(item))
      .filter((day) => Number.isInteger(day) && day >= 1 && day <= 31);
    return { kind: "monthly", month_days: [...new Set(monthDays)].sort((a, b) => a - b) };
  }
  if (kind === "interval") {
    return { kind: "interval", interval_days: Number(values.interval_days) || 1 };
  }
  return { kind: "daily" };
}

export function validateHabitDraft(draft = {}) {
  const title = String(draft.title || "").trim();
  if (!title) return "请填写习惯名称";
  if (title.length > 120) return "习惯名称最多 120 字";
  const frequency = draft.frequency || { kind: "daily" };
  if (frequency.kind === "weekly" && !(frequency.weekdays || []).length) return "每周计划至少选择一天";
  if (frequency.kind === "monthly" && !(frequency.month_days || []).length) return "每月计划至少选择一天";
  if (frequency.kind === "interval") {
    const interval = Number(frequency.interval_days);
    if (!Number.isInteger(interval) || interval < 1 || interval > 365) return "间隔天数必须在 1–365 之间";
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(draft.start_date || ""))) return "开始日期必须是 YYYY-MM-DD";
  const target = draft.target_days;
  if (target !== null && target !== undefined && target !== "" && (Number(target) < 1 || Number(target) > 3650)) {
    return "坚持天数必须在 1–3650 之间";
  }
  if (draft.reminder && !/^([01]\d|2[0-3]):[0-5]\d$/.test(String(draft.reminder))) return "提醒时间必须是 HH:MM";
  if (draft.color && !/^#[0-9a-fA-F]{6}$/.test(String(draft.color))) return "颜色必须是 #RRGGBB";
  if (String(draft.group || "").length > 40) return "分组最多 40 字";
  return "";
}

export function thisWeekStart(day) {
  const index = new Date(`${day}T00:00:00Z`).getUTCDay();
  return dayOffset(day, -index);
}
