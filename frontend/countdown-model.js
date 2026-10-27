// 工作台倒计时卡的数据模型（无 DOM 依赖，方便 Node 测试）。
// 仿照 Apex Dashboard 的 countdown：目标日期、显示单位、提前提醒、标签与背景。
export const COUNTDOWN_STORAGE_KEY = "torch-os:countdown:v1";
export const COUNTDOWN_THEMES = [
  ["sunset", "夕阳云层"],
  ["dusk", "暮色蓝调"],
  ["aurora", "极光"],
];
const THEME_IDS = new Set(COUNTDOWN_THEMES.map(([id]) => id));
const MODES = new Set(["days", "hours", "minutes"]);
const UNIT_LABELS = { days: "天", hours: "小时", minutes: "分钟" };
const UNIT_MS = { days: 86400000, hours: 3600000, minutes: 60000 };

function pad(value) {
  return String(value).padStart(2, "0");
}

export function targetInputValue(year, month = 1, day = 1, hour = 0, minute = 0) {
  return `${year}-${pad(month)}-${pad(day)}T${pad(hour)}:${pad(minute)}`;
}

/** 默认：下一个元旦零点，标签沿用参考项目截图里的 New Year Countdown。 */
export function defaultCountdown(now = new Date()) {
  return {
    label: "New Year Countdown",
    targetDate: targetInputValue(now.getFullYear() + 1),
    displayMode: "days",
    reminderDays: 0,
    theme: "sunset",
    image: "",
  };
}

export function parseTarget(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  // datetime-local 不带时区，按本地时间解释；也接受只有日期的值。
  const target = new Date(text.includes("T") ? text : `${text}T00:00`);
  return Number.isNaN(target.getTime()) ? null : target;
}

export function formatTarget(value) {
  const target = parseTarget(value);
  if (!target) return "未设置";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Shanghai",
  }).format(target);
}

export function normalizeCountdown(raw, now = new Date()) {
  const fallback = defaultCountdown(now);
  const source = raw && typeof raw === "object" ? raw : {};
  const label = String(source.label ?? fallback.label).trim().slice(0, 80) || fallback.label;
  const targetDate = parseTarget(source.targetDate) ? String(source.targetDate) : fallback.targetDate;
  const displayMode = MODES.has(source.displayMode) ? source.displayMode : fallback.displayMode;
  const reminder = Number(source.reminderDays);
  const reminderDays = Number.isFinite(reminder)
    ? Math.max(0, Math.min(365, Math.round(reminder)))
    : fallback.reminderDays;
  const theme = THEME_IDS.has(source.theme) ? source.theme : fallback.theme;
  const image = /^(?:data:image\/|\/|https?:\/\/)/i.test(String(source.image || ""))
    ? String(source.image)
    : "";
  return { label, targetDate, displayMode, reminderDays, theme, image };
}

/** 计算卡片当前值；过期时 value 固定为 0，交给界面显示“已到期”。 */
export function countdownState(config, now = new Date()) {
  const normalized = normalizeCountdown(config, now);
  const target = parseTarget(normalized.targetDate);
  if (!target) {
    return {
      expired: true, value: 0, unit: UNIT_LABELS[normalized.displayMode],
      days: 0, reminderActive: false, target: normalized.targetDate,
      unitLabel: UNIT_LABELS[normalized.displayMode], label: normalized.label,
    };
  }
  const diff = target.getTime() - now.getTime();
  const expired = diff <= 0;
  const safeDiff = Math.max(0, diff);
  const unit = UNIT_LABELS[normalized.displayMode];
  const value = Math.ceil(safeDiff / UNIT_MS[normalized.displayMode]);
  const days = Math.ceil(safeDiff / UNIT_MS.days);
  return {
    expired,
    value: expired ? 0 : value,
    unit,
    unitLabel: unit,
    days,
    reminderActive: !expired && normalized.reminderDays > 0 && days <= normalized.reminderDays,
    target: normalized.targetDate,
    label: normalized.label,
  };
}

export function loadCountdown(storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(COUNTDOWN_STORAGE_KEY);
    if (!raw) return defaultCountdown();
    return normalizeCountdown(JSON.parse(raw));
  } catch {
    return defaultCountdown();
  }
}

export function saveCountdown(storage, config) {
  const normalized = normalizeCountdown(config);
  try {
    storage?.setItem(COUNTDOWN_STORAGE_KEY, JSON.stringify(normalized));
  } catch (error) {
    throw new Error("保存失败：图片可能太大，或浏览器本地存储已满。");
  }
  return normalized;
}
