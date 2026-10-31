// 人生路首页的「写作热力图」（2026-10-31，参考 jotsy 的 ExploreHeatmapCard/Grid）。
//
// 数据来自 /api/pillars/life/overview 的 journal_heatmap：最近 26 周、周一起、
// 每天是否写了日记与字数。这里只做纯计算和 HTML 标记；悬停/聚焦说明行与
// 滚动到最近一周由 pillars.js 绑定。格子按字数分四档（<200 / <600 / <1500 / 其余），
// 与 jotsy 的 ExploreHeatBasis.words 一致；今天用强调色描边，聚焦格用正文色描边。

const WEEKDAYS = Object.freeze(["周一", "周二", "周三", "周四", "周五", "周六", "周日"]);
const DEFAULT_WEEKS = 26;
const DAY_MS = 86400000;
const MONTH_GAP_COLUMNS = 4;
const DAY_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "UTC", year: "numeric", month: "long", day: "numeric",
});
const NUMBER_FORMAT = new Intl.NumberFormat("zh-CN");

const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));

const pad = (value) => String(value).padStart(2, "0");

const HEATMAP_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" focusable="false"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>';

function parseDay(value) {
  const text = String(value || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return null;
  const date = new Date(`${text}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function dayKey(date) {
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

function addDays(date, days) {
  return new Date(date.getTime() + days * DAY_MS);
}

function dayLabel(date) {
  return DAY_FORMAT.format(date);
}

function monthLabel(date) {
  return `${date.getUTCMonth() + 1}月`;
}

function formatNumber(value) {
  return NUMBER_FORMAT.format(Number(value) || 0);
}

/** 与 jotsy 的字数档位一致；0 篇为 0 档，1 篇但字数为 0 也算最低档。 */
export function journalHeatmapLevel(entry) {
  const entries = Math.max(0, Number(entry?.entries) || 0);
  if (entries <= 0) return 0;
  const words = Math.max(0, Number(entry?.words) || 0);
  if (words < 200) return 1;
  if (words < 600) return 2;
  if (words < 1500) return 3;
  return 4;
}

function dayCaption(date, entry, hasEntry) {
  const label = dayLabel(date);
  if (!hasEntry) return `${label} · 没有日记`;
  return `${label} · ${Math.max(1, Number(entry?.entries) || 1)} 篇 · ${formatNumber(entry?.words)} 字`;
}

function buildMonthLabels(start, weeks, today) {
  const labels = Array(weeks).fill("");
  let lastColumn = -MONTH_GAP_COLUMNS;
  for (let column = 0; column < weeks; column += 1) {
    const first = addDays(start, column * 7);
    const last = addDays(start, column * 7 + 6);
    let monthStart = null;
    if (column === 0) {
      monthStart = first;
    } else if (first.getUTCMonth() !== last.getUTCMonth() || first.getUTCDate() === 1) {
      monthStart = new Date(Date.UTC(last.getUTCFullYear(), last.getUTCMonth(), 1));
    }
    if (!monthStart || column - lastColumn < MONTH_GAP_COLUMNS) continue;
    // 当前周跨进下个月但下个月还没有可画的格子时（例如今天周六、周日属于
    // 下个月），不要为未来月份单独写一个月份标签。
    let visible = false;
    for (let row = 0; row < 7; row += 1) {
      const date = addDays(start, column * 7 + row);
      if (date > today) continue;
      if (date.getUTCFullYear() === monthStart.getUTCFullYear()
        && date.getUTCMonth() === monthStart.getUTCMonth()) {
        visible = true;
        break;
      }
    }
    if (!visible) continue;
    labels[column] = monthLabel(monthStart);
    lastColumn = column;
  }
  return labels;
}

/**
 * 把接口返回的 {start, weeks, today, first_weekday, days} 变成可渲染的模型：
 * columns[weeks][7]、monthLabels[weeks]、weekdayLabels[7]、summary 等。
 * 数据不合法时返回 null，调用方回退到旧的「今日日记」卡片。
 */
export function buildJournalHeatmap(raw, fallbackToday = "") {
  if (!raw || typeof raw !== "object") return null;
  const firstWeekdayRaw = Number(raw.first_weekday);
  const firstWeekday = firstWeekdayRaw >= 1 && firstWeekdayRaw <= 7 ? firstWeekdayRaw : 1;
  let today = parseDay(raw.today || fallbackToday);
  if (!today) return null;
  let start = parseDay(raw.start);
  if (!start) {
    const offset = (today.getUTCDay() - (firstWeekday % 7) + 7) % 7;
    start = addDays(today, -(offset + 7 * (DEFAULT_WEEKS - 1)));
  }
  const weeks = Math.max(1, Math.min(53, Math.trunc(Number(raw.weeks) || DEFAULT_WEEKS)));
  if (start > today) return null;
  const source = raw.days && typeof raw.days === "object" && !Array.isArray(raw.days) ? raw.days : {};
  const columns = [];
  let writtenDays = 0;
  for (let column = 0; column < weeks; column += 1) {
    const cells = [];
    for (let row = 0; row < 7; row += 1) {
      const date = addDays(start, column * 7 + row);
      const key = dayKey(date);
      if (date > today) {
        cells.push({ key, date, future: true, today: false, entry: null,
          entries: 0, words: 0, level: 0, caption: "", path: "", legacy: false });
        continue;
      }
      const entry = source[key] && typeof source[key] === "object" ? source[key] : null;
      const entries = Math.max(0, Number(entry?.entries) || 0);
      const hasEntry = Boolean(entry) && entries > 0;
      if (hasEntry) writtenDays += 1;
      cells.push({
        key, date, future: false,
        today: key === dayKey(today),
        entry: hasEntry ? entry : null,
        entries: hasEntry ? entries : 0,
        words: hasEntry ? Math.max(0, Number(entry?.words) || 0) : 0,
        level: hasEntry ? journalHeatmapLevel(entry) : 0,
        caption: dayCaption(date, entry, hasEntry),
        path: hasEntry ? String(entry?.path || "") : "",
        legacy: Boolean(hasEntry && entry?.legacy),
      });
    }
    columns.push(cells);
  }
  const weekdayLabels = Array(7).fill("");
  for (const row of [0, 2, 4, 6]) {
    weekdayLabels[row] = WEEKDAYS[(firstWeekday - 1 + row) % 7];
  }
  return {
    start: dayKey(start),
    weeks,
    today: dayKey(today),
    firstWeekday,
    columns,
    monthLabels: buildMonthLabels(start, weeks, today),
    weekdayLabels,
    writtenDays,
    summary: `近 ${weeks} 周里有 ${writtenDays} 天写了日记`,
  };
}

function cellMarkup(cell) {
  if (cell.future) {
    return '<span class="life-heatmap-cell is-blank" aria-hidden="true"></span>';
  }
  const classes = ["life-heatmap-cell"];
  if (cell.today) classes.push("is-today");
  const common = `class="${classes.join(" ")}" data-heat-date="${esc(cell.key)}" data-heat-caption="${esc(cell.caption)}" data-level="${cell.level}"`;
  if (cell.entry && (cell.path || cell.legacy)) {
    return `<button type="button" ${common} data-journal-day="${esc(cell.key)}" data-journal-path="${esc(cell.path)}" data-journal-legacy="${cell.legacy ? "1" : "0"}" aria-label="${esc(cell.caption)}"></button>`;
  }
  if (cell.today && !cell.entry) {
    return `<button type="button" ${common} data-editor-new="life" data-new-chapter="journal" aria-label="${esc(cell.caption)}，开始写今天的日记"></button>`;
  }
  return `<span ${common} aria-hidden="true"></span>`;
}

/** 完整卡片；action 由 pillars.js 用需求上下文拼好（已转义）。 */
export function journalHeatmapMarkup(model, { action = "" } = {}) {
  if (!model) return "";
  const monthSlots = model.monthLabels
    .map((label) => `<span class="life-heatmap-month">${esc(label)}</span>`)
    .join("");
  const weekdaySlots = model.weekdayLabels
    .map((label) => `<span class="life-heatmap-weekday">${esc(label)}</span>`)
    .join("");
  const weeks = model.columns.map((cells) => `<div class="life-heatmap-week">${cells.map(cellMarkup).join("")}</div>`).join("");
  const legend = [0, 1, 2, 3, 4]
    .map((level) => `<i class="life-heatmap-swatch" data-level="${level}"></i>`)
    .join("");
  return `<section class="life-heatmap-card" aria-labelledby="life-heatmap-title">
    <div class="life-heatmap-head">
      <span class="life-heatmap-icon" aria-hidden="true">${HEATMAP_ICON}</span>
      <h4 id="life-heatmap-title" class="life-heatmap-title">写作热力图</h4>
      ${action || ""}
    </div>
    <div class="life-heatmap-layout" data-life-heatmap data-heatmap-summary="${esc(model.summary)}">
      <div class="life-heatmap-weekdays" aria-hidden="true">${weekdaySlots}</div>
      <div class="life-heatmap-scroll">
        <div class="life-heatmap-months" aria-hidden="true">${monthSlots}</div>
        <div class="life-heatmap-weeks">${weeks}</div>
      </div>
    </div>
    <div class="life-heatmap-foot">
      <p class="life-heatmap-caption" id="life-heatmap-caption">${esc(model.summary)}</p>
      <div class="life-heatmap-legend" aria-hidden="true"><span>少</span>${legend}<span>多</span></div>
    </div>
  </section>`;
}
