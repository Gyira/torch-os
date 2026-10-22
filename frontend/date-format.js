// User-facing date labels shared by the four pillars and global search.
// Kept free of DOM access so it can be unit-tested outside the browser.

export function formatRelativeDate(value) {
  if (!value) return "";
  const text = String(value).trim();
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(text);
  const date = dateOnly ? new Date(`${text}T00:00:00+08:00`) : new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  const tz = { timeZone: "Asia/Shanghai" };
  const monthDay = new Intl.DateTimeFormat("zh-CN", { ...tz, month: "long", day: "numeric" }).format(date);
  if (dateOnly) return monthDay;
  const dayKey = new Intl.DateTimeFormat("en-CA", { ...tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
  const now = new Date();
  const todayKey = new Intl.DateTimeFormat("en-CA", { ...tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  const yesterdayKey = new Intl.DateTimeFormat("en-CA", { ...tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(now.getTime() - 86400000));
  const time = new Intl.DateTimeFormat("zh-CN", { ...tz, hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  if (dayKey === todayKey) return `今天 ${time}`;
  if (dayKey === yesterdayKey) return `昨天 ${time}`;
  const year = new Intl.DateTimeFormat("zh-CN", { ...tz, year: "numeric" }).format(date);
  const currentYear = new Intl.DateTimeFormat("zh-CN", { ...tz, year: "numeric" }).format(now);
  return year === currentYear ? `${monthDay} ${time}` : `${year}${monthDay} ${time}`;
}
