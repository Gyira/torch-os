// 纪念日数据层（2026-10-27，参考 Apex Dashboard 的 Anniversary widget）：
// 已过去多久、阳历/农历日期、每年提醒与背景配置。这里只放无 DOM 依赖的纯函数，
// DOM、localStorage 和设置弹窗在 anniversary-widget.js。
import LunarLibrary, { Solar, Lunar } from "./vendor/lunar.js";

const { LunarYear } = LunarLibrary;

export const LUNAR_YEAR_MIN = 1901;
export const LUNAR_YEAR_MAX = 2099;
export const MAX_IMAGE_BYTES = 1_200_000;
export const DEFAULT_BACKGROUND = "https://images.pexels.com/photos/10254198/pexels-photo-10254198.jpeg";
export const PRECISIONS = Object.freeze([
  ["ymd", "年 月 日"],
  ["days", "总天数"],
  ["hours", "天 + 小时"],
]);
export const LUNAR_MONTH_NAMES = ["正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊"];
export const LUNAR_DAY_NAMES = [
  "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
  "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
  "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
];

function pad2(value) {
  return String(value).padStart(2, "0");
}

/** 本地日期 → YYYY-MM-DD。 */
export function formatDate(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

/** 本地日期 → YYYY-MM-DDTHH:mm。 */
export function formatDateTime(date) {
  return `${formatDate(date)}T${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

/** 解析 YYYY-MM-DD 或 YYYY-MM-DD HH:mm / YYYY-MM-DDTHH:mm；无效返回 null。 */
export function parseAnniversaryDate(raw) {
  const text = String(raw || "").trim();
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?$/);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4] ?? 0);
  const minute = Number(match[5] ?? 0);
  if (hour > 23 || minute > 59) return null;
  const date = new Date(year, month - 1, day, hour, minute, 0, 0);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) {
    return null; // 2025-02-30 之类会被 Date 自动进位，这里显式拒绝
  }
  return date;
}

/** 规范化历史日期：日期或日期+时间；无效返回空串。 */
export function normalizeStartDate(raw) {
  const date = parseAnniversaryDate(raw);
  if (!date) return "";
  return String(raw).includes("T") || /\s\d{2}:\d{2}$/.test(String(raw))
    ? formatDateTime(date)
    : formatDate(date);
}

/** 一年前的今天（默认纪念日日期），动态生成，避免写死年份。 */
export function oneYearAgoIso(now = new Date()) {
  const date = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  date.setFullYear(date.getFullYear() - 1);
  return formatDate(date);
}

/** 已过去时长：年 月 日 / 总天数 / 天 + 小时。未来日期返回“尚未到来”。 */
export function formatElapsed(start, now = new Date(), precision = "ymd") {
  if (!(start instanceof Date) || Number.isNaN(start.getTime())) return "--";
  const diffMs = now.getTime() - start.getTime();
  if (diffMs < 0) return "尚未到来";
  const totalDays = Math.floor(diffMs / 86400000);
  if (precision === "days") return `${totalDays}天`;
  if (precision === "hours") {
    const hours = Math.floor((diffMs - totalDays * 86400000) / 3600000);
    return `${totalDays}天 ${hours}小时`;
  }
  let years = now.getFullYear() - start.getFullYear();
  let months = now.getMonth() - start.getMonth();
  let days = now.getDate() - start.getDate();
  if (days < 0) {
    months -= 1;
    days += new Date(now.getFullYear(), now.getMonth(), 0).getDate();
  }
  if (months < 0) {
    years -= 1;
    months += 12;
  }
  const parts = [];
  if (years > 0) parts.push(`${years}年`);
  if (months > 0) parts.push(`${months}个月`);
  parts.push(`${Math.max(0, days)}天`);
  return parts.join("");
}

/** 今年的阳历周年日（2/29 在平年自然落到 3/1）。 */
export function anniversaryDateThisYear(start, now = new Date()) {
  return new Date(now.getFullYear(), start.getMonth(), start.getDate());
}

function lunarOf(date) {
  try {
    return Solar.fromYmd(date.getFullYear(), date.getMonth() + 1, date.getDate()).getLunar();
  } catch {
    return null;
  }
}

/** 阳历日期 → 农历年月日（含中文），转换失败返回 null。 */
export function lunarPartsFromSolar(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return null;
  const lunar = lunarOf(date);
  if (!lunar) return null;
  return {
    year: lunar.getYear(),
    month: lunar.getMonth(),
    day: lunar.getDay(),
    yearChinese: lunar.getYearInChinese(),
    monthChinese: lunar.getMonthInChinese(),
    dayChinese: lunar.getDayInChinese(),
  };
}

/** 农历日期显示：农历二〇二五年八月廿八。 */
export function formatLunarStartDate(date) {
  const parts = lunarPartsFromSolar(date);
  return parts ? `农历${parts.yearChinese}年${parts.monthChinese}月${parts.dayChinese}` : null;
}

/** 两个阳历日期之间跨过的农历年数（以农历新年为界）。 */
export function lunarYearsBetween(start, now = new Date()) {
  const first = lunarPartsFromSolar(start);
  const second = lunarPartsFromSolar(now);
  return first && second ? second.year - first.year : null;
}

/** 今年对应的农历周年阳历日；农历月日每年对应的阳历日期会漂移。 */
export function lunarAnniversaryThisYear(start, now = new Date()) {
  const parts = lunarPartsFromSolar(start);
  const current = lunarPartsFromSolar(now);
  if (!parts || !current) return anniversaryDateThisYear(start, now);
  try {
    const solar = Lunar.fromYmd(current.year, parts.month, parts.day).getSolar();
    return new Date(solar.getYear(), solar.getMonth() - 1, solar.getDay());
  } catch {
    return anniversaryDateThisYear(start, now);
  }
}

/** 某农历年的月份选项（含闰月，闰月 month 为负数），按农历顺序排列。 */
export function lunarMonthOptions(year) {
  const numeric = Math.trunc(Number(year));
  if (!Number.isFinite(numeric) || numeric < LUNAR_YEAR_MIN || numeric > LUNAR_YEAR_MAX) return [];
  try {
    return LunarYear.fromYear(numeric).getMonths()
      .filter((month) => month.getYear() === numeric)
      .map((month) => ({
        month: month.getMonth(),
        dayCount: month.getDayCount(),
        label: `${month.getMonth() < 0 ? "闰" : ""}${LUNAR_MONTH_NAMES[Math.abs(month.getMonth()) - 1]}月`,
      }));
  } catch {
    return [];
  }
}

/** 某农历月的日选项（29 或 30 天）。 */
export function lunarDayOptions(dayCount) {
  const count = Math.max(1, Math.min(30, Math.trunc(Number(dayCount)) || 30));
  return Array.from({ length: count }, (_, index) => ({
    day: index + 1,
    label: LUNAR_DAY_NAMES[index],
  }));
}

/** 农历年月日 → 对应阳历日期；无效组合返回 null。 */
export function solarFromLunar(year, month, day) {
  const numericYear = Math.trunc(Number(year));
  const numericMonth = Math.trunc(Number(month));
  const numericDay = Math.trunc(Number(day));
  if (!Number.isFinite(numericYear) || numericYear < LUNAR_YEAR_MIN || numericYear > LUNAR_YEAR_MAX
    || !Number.isFinite(numericMonth) || !Number.isFinite(numericDay)) {
    return null;
  }
  try {
    const solar = Lunar.fromYmd(numericYear, numericMonth, numericDay).getSolar();
    return {
      date: `${solar.getYear()}-${pad2(solar.getMonth())}-${pad2(solar.getDay())}`,
      year: solar.getYear(),
      month: solar.getMonth(),
      day: solar.getDay(),
    };
  } catch {
    return null;
  }
}

/** 阳历/农历纪念日今年是否正好是今天，以及周年数。 */
export function anniversaryMatch(config, now = new Date()) {
  if (config?.annualReminder !== true) return null;
  const start = parseAnniversaryDate(config?.startDate);
  if (!start || start.getTime() > now.getTime()) return null;
  const lunar = config?.calendar === "lunar";
  const target = lunar ? lunarAnniversaryThisYear(start, now) : anniversaryDateThisYear(start, now);
  if (!target) return null;
  const sameDay = target.getFullYear() === now.getFullYear()
    && target.getMonth() === now.getMonth()
    && target.getDate() === now.getDate();
  if (!sameDay) return null;
  const years = lunar
    ? (lunarYearsBetween(start, now) ?? (now.getFullYear() - start.getFullYear()))
    : (now.getFullYear() - start.getFullYear());
  return { years: Math.max(0, years), target };
}

/** 只允许 https、同源路径或位图 data URL；其他协议/可执行内容一律丢弃。 */
export function normalizeBackground(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  if (/[\s"'\\]/.test(text)) return "";
  if (/^data:image\/(?:png|jpe?g|webp|gif|avif);base64,/i.test(text)) return text;
  if (/^\/(?!\/)/.test(text)) return text;
  if (!/^https:\/\//i.test(text)) return "";
  return text;
}

/** 配置归一化：缺字段给默认值，坏日期给空串，未知精度回退到年 月 日。 */
export function normalizeAnniversary(raw, now = new Date()) {
  const source = raw && typeof raw === "object" ? raw : {};
  const name = String(source.name ?? "纪念日").trim().slice(0, 60) || "纪念日";
  const calendar = source.calendar === "lunar" ? "lunar" : "solar";
  const startDate = source.startDate == null ? oneYearAgoIso(now) : normalizeStartDate(source.startDate);
  const precision = PRECISIONS.some(([id]) => id === source.precision) ? source.precision : "ymd";
  const background = source.background == null ? DEFAULT_BACKGROUND : normalizeBackground(source.background);
  return {
    name,
    calendar,
    startDate,
    precision,
    annualReminder: source.annualReminder === true,
    background,
  };
}

/** 卡片要显示的标题、时长、日期行与当日提醒。 */
export function formatAnniversaryCard(config, now = new Date()) {
  const start = parseAnniversaryDate(config?.startDate);
  const title = String(config?.name || "").trim() || "纪念日";
  if (!start) return { title, value: "--", dateText: "未设置日期", reminder: "", start: null };
  const value = formatElapsed(start, now, config?.precision);
  const solarText = formatDate(start);
  const lunarText = config?.calendar === "lunar" ? formatLunarStartDate(start) : null;
  const dateText = lunarText ? `${lunarText}（${solarText}）` : solarText;
  const match = config?.annualReminder ? anniversaryMatch(config, now) : null;
  const reminder = match
    ? (match.years > 0 ? `今天 · 第 ${match.years} 周年` : "就是今天")
    : "";
  return { title, value, dateText, reminder, start };
}
