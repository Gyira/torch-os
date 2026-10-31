// 数据看板的纯渲染模型（2026-10-31）：不碰 DOM、不发请求，node 直测。
// 颜色档位与计数规则都由后端算好；这里只负责把 /api/heatmap/board 的数据排成日历、
// 周表、月历、年卡与汇总面板。

export const ROW_ORDER = Object.freeze([
  "life", "knowledge", "self", "todo_created", "todo_done", "habits",
]);
export const ROW_FALLBACK = Object.freeze({
  life: "人生路",
  knowledge: "知识库",
  self: "自我档案",
  todo_created: "任务创建",
  todo_done: "任务完成",
  habits: "习惯完成",
  total: "总热度",
});
const WEEKDAYS = Object.freeze(["周一", "周二", "周三", "周四", "周五", "周六", "周日"]);
const WEEKDAYS_SHORT = Object.freeze(["一", "二", "三", "四", "五", "六", "日"]);
const MAX_DOC_CHIPS = 12;
const MAX_HABIT_CHIPS = 16;

const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));

export const heatPercent = (score) => {
  const value = Number(score);
  return Number.isFinite(value) ? Math.round(Math.max(0, Math.min(1, value)) * 100) : 0;
};

const levelOf = (value) => Math.max(0, Math.min(4, Number(value) || 0));
const pad2 = (value) => String(value).padStart(2, "0");
const shortDate = (day) => `${Number(String(day).slice(5, 7))}/${Number(String(day).slice(8, 10))}`;

export function heatDateLabel(day) {
  const date = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return String(day || "");
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "UTC", year: "numeric", month: "long", day: "numeric",
  }).format(date);
}

function weekdayLabel(day) {
  const date = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "UTC", weekday: "long" }).format(date);
}

function monthLabel(monthKey) {
  const [year, month] = String(monthKey || "").split("-").map(Number);
  return year && month ? `${year}年${month}月` : String(monthKey || "");
}

function basename(path) {
  const text = String(path || "");
  const index = text.lastIndexOf("/");
  return index >= 0 ? text.slice(index + 1) : text;
}

function rowLabel(key, data) {
  const row = (data?.rows || []).find((item) => item.key === key);
  return row?.label || ROW_FALLBACK[key] || key;
}

function rowUnit(key, data) {
  const row = (data?.rows || []).find((item) => item.key === key);
  return row?.unit || "";
}

export function formatHeatValue(key, row = {}, data = null) {
  if (key === "total") return `${heatPercent(row.score)}%`;
  if (key === "habits") {
    const scheduled = Number(row.scheduled) || 0;
    const skipped = Number(row.skipped) || 0;
    const done = Number(row.done) || 0;
    const effective = Math.max(0, scheduled - skipped);
    if (scheduled <= 0) return "—";
    return effective > 0 ? `${done}/${effective}` : "休";
  }
  const unit = rowUnit(key, data) || "项";
  return `${Number(row.count) || 0} ${unit}`;
}

function levelClass(level) {
  const value = levelOf(level);
  return value ? `heat-level-${value}` : "heat-level-0";
}

function levelDots(level) {
  const value = levelOf(level);
  return [1, 2, 3, 4]
    .map((step) => `<i class="heat-dot${step <= value ? " is-on" : ""}"></i>`)
    .join("");
}

function heatLegend() {
  return `<span class="heat-legend" aria-hidden="true"><span>0</span>` +
    [0, 1, 2, 3, 4].map((level) => `<i class="heat-legend-cell ${levelClass(level)}"></i>`).join("") +
    `<span>100%</span></span>`;
}

/* ===== calendar ===== */

function calendarCells(data, { selected = "", interactive = true } = {}) {
  const days = Array.isArray(data?.days) ? data.days : [];
  if (!days.length) return "";
  const lead = Math.max(0, (Number(days[0].weekday) || 1) - 1);
  const blanks = Array.from({ length: lead }, () => '<span class="heat-cal-cell is-blank" aria-hidden="true"></span>');
  const cells = days.map((day) => {
    const key = String(day.date || "");
    const total = day.rows?.total || {};
    const level = day.future ? 0 : levelOf(total.level);
    const percent = heatPercent(total.score);
    const classes = [
      "heat-cal-cell",
      levelClass(level),
      day.future ? "is-future" : "",
      key === data.today ? "is-today" : "",
      key === selected ? "is-selected" : "",
    ].filter(Boolean).join(" ");
    const label = `${heatDateLabel(key)}，${day.future ? "未来" : `总热度 ${percent}%`}`;
    const number = Number(key.slice(8, 10)) || "";
    if (!interactive || day.future) {
      return `<span class="${classes}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}">` +
        `<span class="heat-cal-num">${number}</span>` +
        (level ? '<span class="heat-cal-mark" aria-hidden="true"></span>' : "") +
        `</span>`;
    }
    return `<button type="button" class="${classes}" data-heatmap-day="${escapeHtml(key)}" ` +
      `title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}">` +
      `<span class="heat-cal-num">${number}</span>` +
      (level ? '<span class="heat-cal-mark" aria-hidden="true"></span>' : "") +
      `</button>`;
  }).join("");
  return blanks.join("") + cells;
}

function calendarCard(data, { selected = "", title = "", hint = "", interactive = true } = {}) {
  const weekdays = WEEKDAYS_SHORT.map((label) => `<span class="heat-cal-weekday">${label}</span>`).join("");
  const heading = title || monthLabel(String(data?.range?.from || "").slice(0, 7));
  return `<section class="heat-calendar-card">
    <div class="heat-card-head">
      <div><h3>${escapeHtml(heading)}</h3>` +
    (hint ? `<p>${escapeHtml(hint)}</p>` : "") + `</div>${heatLegend()}</div>
    <div class="heat-calendar" role="grid" aria-label="${escapeHtml(heading || "热力日历")}">
      ${weekdays}${calendarCells(data, { selected, interactive })}
    </div>
  </section>`;
}

/* ===== day detail ===== */

function documentList(paths) {
  const items = (Array.isArray(paths) ? paths : []).filter(Boolean);
  if (!items.length) return "";
  const shown = items.slice(0, MAX_DOC_CHIPS);
  const more = items.length - shown.length;
  return `<ul class="heat-doc-list" aria-label="当天新增文档">` +
    shown.map((path) => `<li class="heat-doc-chip" title="${escapeHtml(path)}">` +
      `<span aria-hidden="true">◇</span>${escapeHtml(basename(path))}</li>`).join("") +
    (more > 0 ? `<li class="heat-doc-chip is-more">还有 ${more} 个</li>` : "") +
    `</ul>`;
}

function habitList(row) {
  const done = Array.isArray(row?.done_titles) ? row.done_titles : [];
  const skipped = Array.isArray(row?.skipped_titles) ? row.skipped_titles : [];
  const missed = Array.isArray(row?.missed_titles) ? row.missed_titles : [];
  const group = (title, names, tone) => {
    const items = names.slice(0, MAX_HABIT_CHIPS);
    const more = names.length - items.length;
    if (!items.length) return "";
    return `<div class="heat-habit-group ${tone}"><span class="heat-habit-title">${title}</span>` +
      items.map((name) => `<span class="heat-habit-chip">${escapeHtml(name)}</span>`).join("") +
      (more > 0 ? `<span class="heat-habit-chip is-more">还有 ${more} 个</span>` : "") +
      `</div>`;
  };
  const blocks = [
    group("已完成", done, "is-done"),
    group("主动休息", skipped, "is-skipped"),
    group("未记录", missed, "is-missed"),
  ].filter(Boolean);
  if (!blocks.length) {
    if ((Number(row?.scheduled) || 0) <= 0) {
      return '<p class="heat-detail-empty">这天没有安排习惯。</p>';
    }
    return "";
  }
  return `<div class="heat-habit-list">${blocks.join("")}</div>`;
}

function detailRow(key, row, data) {
  const label = rowLabel(key, data);
  const score = row?.score;
  const percent = key === "habits" && score == null ? 0 : heatPercent(score);
  const value = formatHeatValue(key, row || {}, data);
  const level = levelOf(row?.level);
  const docs = key === "habits" ? "" : documentList(row?.documents);
  const habits = key === "habits" ? habitList(row) : "";
  const note = key === "habits" && (Number(row?.skipped) || 0) > 0
    ? `主动休息 ${Number(row.skipped)} 项，不进完成分母。`
    : "";
  return `<article class="heat-detail-row ${levelClass(level)}">
    <div class="heat-detail-row-head">
      <span class="heat-detail-name">${escapeHtml(label)}</span>
      <span class="heat-detail-value">${escapeHtml(value)}</span>
    </div>
    <div class="heat-meter" role="img" aria-label="${escapeHtml(label)} 完成度 ${percent}%">
      <i style="width:${Math.max(0, Math.min(100, percent))}%"></i>
    </div>
    ${note ? `<p class="heat-detail-note">${escapeHtml(note)}</p>` : ""}
    ${docs}${habits}
  </article>`;
}

function detailPanel(data) {
  const detail = data?.detail;
  if (!detail) {
    return '<section class="heat-detail heat-detail-empty"><p>这一天还没有数据。</p></section>';
  }
  const total = detail.rows?.total || {};
  const level = levelOf(total.level);
  const percent = heatPercent(total.score);
  const rows = ROW_ORDER.map((key) => detailRow(key, detail.rows?.[key] || {}, data)).join("");
  return `<section class="heat-detail" aria-labelledby="heat-detail-title">
    <header class="heat-detail-head">
      <div>
        <p class="eyebrow">DAILY · ${escapeHtml(detail.date)}</p>
        <h3 id="heat-detail-title">${escapeHtml(heatDateLabel(detail.date))} · ${escapeHtml(weekdayLabel(detail.date))}</h3>
      </div>
      <div class="heat-total-badge ${levelClass(level)}">
        <span>总热度</span><strong>${percent}%</strong><span class="heat-dots">${levelDots(level)}</span>
      </div>
    </header>
    <div class="heat-detail-rows">${rows}</div>
  </section>`;
}

/* ===== week ===== */

function weekValue(key, row, data) {
  if (key === "total") return heatPercent(row?.score) ? `${heatPercent(row.score)}%` : "—";
  if (key === "habits") return formatHeatValue(key, row, data);
  const count = Number(row?.count) || 0;
  return count ? String(count) : "·";
}

function weekView(data) {
  const days = Array.isArray(data?.days) ? data.days : [];
  const head = `<div class="heat-week-corner" aria-hidden="true">该周</div>` +
    days.map((day) => {
      const classes = ["heat-week-dayhead", day.date === data.today ? "is-today" : ""].filter(Boolean).join(" ");
      return `<div class="${classes}"><strong>${escapeHtml(shortDate(day.date))}</strong>` +
        `<span>${escapeHtml(WEEKDAYS[(Number(day.weekday) || 1) - 1])}</span></div>`;
    }).join("");
  const rows = [...ROW_ORDER, "total"].map((key) => {
    const label = key === "total" ? "总热度" : rowLabel(key, data);
    const cells = days.map((day) => {
      const row = day.rows?.[key] || {};
      const level = day.future ? 0 : levelOf(row.level);
      const title = `${heatDateLabel(day.date)} · ${label} ${formatHeatValue(key, row, data)}`;
      const classes = [
        "heat-week-cell", levelClass(level),
        day.date === data.today ? "is-today" : "",
        day.future ? "is-future" : "",
      ].filter(Boolean).join(" ");
      return `<span class="${classes}" title="${escapeHtml(title)}">${escapeHtml(weekValue(key, row, data))}</span>`;
    }).join("");
    return `<div class="heat-week-row${key === "total" ? " is-total" : ""}">` +
      `<span class="heat-week-label">${escapeHtml(label)}</span>${cells}</div>`;
  }).join("");
  return `<section class="heat-week-card" aria-label="本周热力">
    <div class="heat-week-grid">${head}${rows}</div>
  </section>`;
}

/* ===== summary ===== */

function summaryValue(key, summary, data) {
  if (key === "total") return `${heatPercent(summary?.score)}%`;
  if (key === "habits") {
    const scheduled = Number(summary?.scheduled) || 0;
    const skipped = Number(summary?.skipped) || 0;
    const done = Number(summary?.done) || 0;
    const effective = Math.max(0, scheduled - skipped);
    return scheduled ? `${done}/${effective}` : "—";
  }
  return `${Number(summary?.count) || 0} ${rowUnit(key, data) || "项"}`;
}

function summaryPanel(data) {
  const summary = data?.summary;
  if (!summary) return "";
  const total = summary.rows?.total || {};
  const best = total.best;
  const cards = [...ROW_ORDER, "total"].map((key) => {
    const row = summary.rows?.[key] || {};
    const label = key === "total" ? "总热度" : rowLabel(key, data);
    const percent = heatPercent(row.score);
    const value = summaryValue(key, row, data);
    const active = Number(row.active_days) || 0;
    const detail = key === "total"
      ? `最热 ${best ? shortDate(best.date) : "—"} · 活跃 ${active} 天`
      : `活跃 ${active} 天`;
    return `<article class="heat-summary-row${key === "total" ? " is-total" : ""}">
      <div class="heat-summary-row-head"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
      <div class="heat-summary-meter"><i style="width:${percent}%"></i></div>
      <div class="heat-summary-meta"><span>${escapeHtml(detail)}</span><span>${percent}%</span></div>
    </article>`;
  }).join("");
  return `<section class="heat-summary" aria-label="周期汇总">
    <div class="heat-summary-head">
      <div><p class="eyebrow">SUMMARY</p><h3>${escapeHtml(data.period?.label || "本周期")} · 热度汇总</h3></div>
      <span class="heat-summary-days">${Number(summary.days) || 0} 天</span>
    </div>
    <div class="heat-summary-grid">${cards}</div>
  </section>`;
}

/* ===== year ===== */

function groupMonths(days) {
  const groups = new Map();
  for (const day of Array.isArray(days) ? days : []) {
    const key = String(day.date || "").slice(0, 7);
    if (!/^\d{4}-\d{2}$/.test(key)) continue;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(day);
  }
  return groups;
}

function miniMonth(monthDays, today) {
  const lead = Math.max(0, (Number(monthDays[0]?.weekday) || 1) - 1);
  const blanks = Array.from({ length: lead }, () => '<span class="heat-mini-cell is-blank"></span>');
  const cells = monthDays.map((day) => {
    const level = day.future ? 0 : levelOf(day.rows?.total?.level);
    const percent = heatPercent(day.rows?.total?.score);
    const classes = [
      "heat-mini-cell", levelClass(level),
      day.date === today ? "is-today" : "",
      day.future ? "is-future" : "",
    ].filter(Boolean).join(" ");
    return `<span class="${classes}" title="${escapeHtml(`${heatDateLabel(day.date)} · ${percent}%`)}"></span>`;
  }).join("");
  return blanks.join("") + cells;
}

function yearView(data) {
  const groups = groupMonths(data?.days);
  const months = [];
  const year = String(data?.anchor || "").slice(0, 4) || String(data?.period?.key || "");
  for (let month = 1; month <= 12; month += 1) {
    const key = `${year}-${pad2(month)}`;
    const monthDays = groups.get(key) || [];
    if (!monthDays.length) {
      months.push(`<div class="heat-month-card is-empty"><span class="heat-month-head"><strong>${month}月</strong><span>—</span></span>` +
        `<span class="heat-month-empty">本月还没有数据</span></div>`);
      continue;
    }
    const usable = monthDays.filter((day) => !day.future);
    const active = usable.filter((day) => levelOf(day.rows?.total?.level) > 0).length;
    const average = usable.length
      ? heatPercent(usable.reduce((sum, day) => sum + (Number(day.rows?.total?.score) || 0), 0) / usable.length)
      : 0;
    const level = Math.min(4, Math.round(average / 25));
    const futureMonth = monthDays.every((day) => day.future);
    const label = `${monthLabel(key)}，平均热度 ${average}%`;
    const head = `<span class="heat-month-head"><strong>${month}月</strong><span>${average}%</span></span>` +
      `<span class="heat-month-grid">${miniMonth(monthDays, data.today)}</span>` +
      `<span class="heat-month-foot">${active} 天有热度</span>`;
    months.push(futureMonth
      ? `<div class="heat-month-card ${levelClass(level)} is-future" aria-disabled="true" aria-label="${escapeHtml(label)}">${head}</div>`
      : `<button type="button" class="heat-month-card ${levelClass(level)}" data-heatmap-month="${escapeHtml(key)}" ` +
        `aria-label="${escapeHtml(label)}">${head}</button>`);
  }
  return `<section class="heat-year" aria-label="年度热力">
    <div class="heat-year-head"><div><p class="eyebrow">YEAR · ${escapeHtml(year)}</p><h3>十二个月的热度</h3></div>${heatLegend()}</div>
    <div class="heat-year-grid">${months.join("")}</div>
  </section>`;
}

/* ===== overall body ===== */

export function heatmapBoardBody(data) {
  if (!data) return "";
  if (data.view === "week") return weekView(data) + summaryPanel(data);
  if (data.view === "year") return yearView(data) + summaryPanel(data);
  if (data.view === "month") return `<div class="heatmap-body is-month">${calendarCard(data, {
    title: data.period?.label || "",
    hint: "点任意一天查看当天明细",
  })}</div>` + summaryPanel(data);
  return `<div class="heatmap-body is-day">
    ${calendarCard(data, {
      selected: data.anchor,
      title: data.period?.label || "",
      hint: "点任意一天切换明细",
    })}
    ${detailPanel(data)}
  </div>`;
}
