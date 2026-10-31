// 经期关怀纯模型：月份/日期/日历格子/周期趋势图。
// 不碰 DOM、不读浏览器存储、不发请求，node --test 可直接测。
// 刻意不 import core.js：那个模块加载时会访问 DOM。
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[character]));

const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const PHASE_TONES = new Set(["period", "follicular", "fertile", "luteal", "late", "unknown"]);

export const CYCLE_WEEKDAYS = Object.freeze(["一", "二", "三", "四", "五", "六", "日"]);

export function validMonth(value) {
  const text = String(value || "");
  return MONTH_RE.test(text) ? text : "";
}

export function currentMonth(now = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit",
  }).format(now).slice(0, 7);
}

export function shiftMonth(month, delta) {
  const text = validMonth(month);
  if (!text) return "";
  const [year, monthNumber] = text.split("-").map(Number);
  const shifted = new Date(Date.UTC(year, monthNumber - 1 + Number(delta), 1));
  return `${shifted.getUTCFullYear()}-${String(shifted.getUTCMonth() + 1).padStart(2, "0")}`;
}

export function monthLabel(month) {
  const text = validMonth(month);
  if (!text) return "";
  const [year, monthNumber] = text.split("-");
  return `${year}年${Number(monthNumber)}月`;
}

export function daysInMonth(month) {
  const text = validMonth(month);
  if (!text) return 0;
  const [year, monthNumber] = text.split("-").map(Number);
  return new Date(Date.UTC(year, monthNumber, 0)).getUTCDate();
}

/** 周一开头：返回本月 1 号前面要补几个空格。 */
export function monthLeadingBlanks(month) {
  const text = validMonth(month);
  if (!text) return 0;
  const [year, monthNumber] = text.split("-").map(Number);
  const weekday = new Date(Date.UTC(year, monthNumber - 1, 1)).getUTCDay();
  return (weekday + 6) % 7;
}

export function dayNumber(day) {
  const text = String(day || "");
  return DAY_RE.test(text) ? Number(text.slice(8, 10)) : 0;
}

export function dayLabel(day) {
  const text = String(day || "").slice(0, 10);
  if (!DAY_RE.test(text)) return text || "—";
  const [, monthNumber, dayOfMonth] = text.split("-");
  return `${Number(monthNumber)} 月 ${Number(dayOfMonth)} 日`;
}

export function shortDay(day) {
  const text = String(day || "").slice(0, 10);
  return DAY_RE.test(text) ? text.slice(5) : text;
}

export function daysUntilLabel(days) {
  if (days === null || days === undefined || days === "") return "—";
  const value = Number(days);
  if (!Number.isFinite(value)) return "—";
  if (value === 0) return "预计今天";
  if (value > 0) return `还有 ${value} 天`;
  return `已推迟 ${-value} 天`;
}

export function phaseTone(phase) {
  return PHASE_TONES.has(phase) ? phase : "unknown";
}

export function calendarDayClasses(day = {}) {
  const classes = ["lc-day"];
  if (day.is_period) classes.push("is-period");
  if (day.is_predicted) classes.push("is-predicted");
  if (day.is_fertile) classes.push("is-fertile");
  if (day.is_ovulation) classes.push("is-ovulation");
  if (day.is_today) classes.push("is-today");
  if (day.is_future) classes.push("is-future");
  if (day.has_log) classes.push("has-log");
  if (day.is_ongoing) classes.push("is-ongoing");
  return classes.join(" ");
}

export function calendarMarkers(day = {}) {
  const markers = [];
  if (day.is_period) markers.push({ label: day.is_ongoing ? "经期·未结束" : "经期", tone: "period" });
  else if (day.is_predicted) markers.push({ label: "预计", tone: "predicted" });
  else if (day.is_ovulation) markers.push({ label: "排卵", tone: "ovulation" });
  else if (day.is_fertile) markers.push({ label: "易孕", tone: "fertile" });
  if (day.has_log) markers.push({ label: "记录", tone: "log" });
  return markers;
}

export function dayAriaLabel(day = {}) {
  const parts = [String(day.date || "")];
  if (day.is_today) parts.push("今天");
  if (day.is_period) parts.push(day.is_ongoing ? "经期未结束" : "经期");
  else if (day.is_predicted) parts.push("预计经期");
  if (day.is_ovulation) parts.push("排卵日");
  else if (day.is_fertile) parts.push("易孕期");
  if (day.has_log) parts.push("有身体记录");
  return parts.filter(Boolean).join("，");
}

export function formatSleep(value) {
  const number = Number(value);
  return Number.isFinite(number) && value !== null && value !== "" ? `${number} 小时` : "—";
}

export function formatWater(value) {
  const number = Number(value);
  return Number.isFinite(number) && value !== null && value !== "" ? `${Math.round(number)} ml` : "—";
}

/** 最近周期长度折线：正常带 21–35 天、28 天参考线，点和值都直接标出来。 */
export function cycleTrendChart(items, { width = 640, height = 190 } = {}) {
  const points = (Array.isArray(items) ? items : [])
    .map((item) => ({ date: String(item?.date || ""), value: Number(item?.length) }))
    .filter((item) => DAY_RE.test(item.date) && Number.isFinite(item.value))
    .sort((left, right) => left.date.localeCompare(right.date))
    .slice(-8);
  if (points.length < 2) return "";

  const padding = { top: 20, right: 16, bottom: 26, left: 36 };
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const low = Math.min(15, ...points.map((point) => point.value - 2));
  const high = Math.max(45, ...points.map((point) => point.value + 2));
  const span = high - low || 1;
  const x = (index) => padding.left + (innerWidth * index) / (points.length - 1);
  const y = (value) => padding.top + innerHeight - ((value - low) / span) * innerHeight;

  const bandTop = y(35);
  const bandBottom = y(21);
  const grid = [21, 28, 35].map((value) => {
    const lineY = y(value);
    return `<line x1="${padding.left}" y1="${lineY.toFixed(1)}" x2="${width - padding.right}" y2="${lineY.toFixed(1)}" class="lc-chart-grid${value === 28 ? " is-ref" : ""}"/>`
      + `<text x="${padding.left - 6}" y="${(lineY + 3).toFixed(1)}" text-anchor="end" class="lc-chart-text">${value}</text>`;
  }).join("");

  const coords = points.map((point, index) => [x(index), y(point.value)]);
  const line = coords.map(([cx, cy], index) => `${index ? "L" : "M"}${cx.toFixed(1)},${cy.toFixed(1)}`).join(" ");
  const dots = points.map((point, index) => {
    const [cx, cy] = coords[index];
    return `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="3.2" class="lc-chart-dot">`
      + `<title>${escapeHtml(`${point.date} · ${point.value} 天`)}</title></circle>`
      + `<text x="${cx.toFixed(1)}" y="${(cy - 8).toFixed(1)}" text-anchor="middle" class="lc-chart-value">${escapeHtml(point.value)}</text>`;
  }).join("");

  const labelIndexes = points.length > 4
    ? [0, Math.floor((points.length - 1) / 2), points.length - 1]
    : points.map((_point, index) => index);
  const xLabels = [...new Set(labelIndexes)].map((index) => (
    `<text x="${x(index).toFixed(1)}" y="${height - 8}" text-anchor="middle" class="lc-chart-text">${escapeHtml(shortDay(points[index].date))}</text>`
  )).join("");

  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="最近周期长度趋势图" preserveAspectRatio="xMidYMid meet" class="lc-chart">`
    + `<rect x="${padding.left}" y="${bandTop.toFixed(1)}" width="${innerWidth}" height="${Math.max(1, bandBottom - bandTop).toFixed(1)}" class="lc-chart-band"/>`
    + grid
    + `<polyline points="${coords.map(([cx, cy]) => `${cx.toFixed(1)},${cy.toFixed(1)}`).join(" ")}" class="lc-chart-line"/>`
    + dots + xLabels + "</svg>";
}
