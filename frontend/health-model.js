// 健康系统纯渲染模型（2026-10-31）：不碰 DOM、不读 localStorage，node --test 直接测。
// 负责数值格式、指标提取、时间序列与 SVG 图表字符串；页面逻辑在 health.js。
// 刻意不 import core.js：那个模块在加载时就会访问 document，测试环境没有 DOM。
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[character]));

export const SECTION_LABELS = Object.freeze({
  sleep: "睡眠", body: "身体", vitals: "体征", nutrition: "饮食",
  activity: "活动", mind: "心理", training: "训练",
});

const DAY_MS = 86400000;

export function todayText(now = new Date()) {
  const offset = now.getTimezoneOffset() * 60000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

export function shiftDay(day, delta) {
  const date = new Date(`${day}T00:00:00`);
  if (Number.isNaN(date.getTime())) return day;
  date.setDate(date.getDate() + delta);
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}

export function lastDates(count, end) {
  const total = Math.max(1, Number(count) || 7);
  const base = end || todayText();
  return Array.from({ length: total }, (_item, index) => shiftDay(base, index - total + 1));
}

export function weekdayLabel(day) {
  const date = new Date(`${day}T00:00:00`);
  if (Number.isNaN(date.getTime())) return "";
  return ["日", "一", "二", "三", "四", "五", "六"][date.getDay()] || "";
}

export function shortDate(day) {
  return /^\d{4}-\d{2}-\d{2}$/.test(String(day || "")) ? String(day).slice(5).replace("-", "/") : String(day || "");
}

export function formatNumber(value, precision = 0, unit = "") {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const digits = Math.max(0, Math.min(3, Number(precision) || 0));
  const text = number.toFixed(digits).replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
  return unit ? `${text} ${unit}` : text;
}

export function formatSigned(value, precision = 1, unit = "") {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const sign = number > 0 ? "+" : "";
  return `${sign}${formatNumber(number, precision, unit)}`;
}

export function formatDuration(minutes) {
  const total = Number(minutes);
  if (!Number.isFinite(total) || total <= 0) return "—";
  const hours = Math.floor(total / 60);
  const rest = Math.round(total % 60);
  if (!hours) return `${rest} 分钟`;
  return rest ? `${hours} 小时 ${rest} 分` : `${hours} 小时`;
}

export function durationBetween(bed, wake) {
  if (!/^\d{2}:\d{2}$/.test(String(bed || "")) || !/^\d{2}:\d{2}$/.test(String(wake || ""))) return null;
  const [bedHour, bedMinute] = bed.split(":").map(Number);
  const [wakeHour, wakeMinute] = wake.split(":").map(Number);
  let minutes = (wakeHour * 60 + wakeMinute) - (bedHour * 60 + bedMinute);
  if (minutes <= 0) minutes += 24 * 60;
  return minutes > 0 && minutes <= 1440 ? minutes : null;
}

export function scoreTone(score) {
  if (score === null || score === undefined || score === "") return "none";
  const value = Number(score);
  if (!Number.isFinite(value)) return "none";
  if (value >= 85) return "great";
  if (value >= 70) return "good";
  if (value >= 55) return "fair";
  return "low";
}

/** 从每日记录里取一个值：body.weight_kg / score.total / training.volume_kg / custom.<名>。 */
export function extractMetric(record, key) {
  if (!record || typeof key !== "string") return null;
  let value;
  if (key === "sleep.score") {
    value = record.score?.derived?.sleep_score;
  } else if (key === "body.bmi") {
    value = record.score?.derived?.bmi;
  } else if (key.startsWith("score.")) {
    value = record.score?.[key.slice(6)];
  } else if (key.startsWith("training.")) {
    value = record.training_totals?.[key.slice(9)];
  } else if (key.startsWith("nutrition.")) {
    value = record.nutrition?.[key.slice(10)] ?? record.sections?.nutrition?.[key.slice(10)];
  } else if (key.startsWith("custom.")) {
    value = record.custom?.[key.slice(7)];
  } else {
    value = record.sections?.[key.split(".")[0]]?.[key.split(".")[1]];
  }
  const number = Number(value);
  return Number.isFinite(number) && value !== null && value !== undefined && value !== ""
    ? number : null;
}

export function pointsFor(records, key) {
  return (Array.isArray(records) ? records : [])
    .map((record) => ({ date: record.date, value: extractMetric(record, key) }))
    .filter((point) => point.value !== null && point.value !== undefined);
}

export function seriesStats(points) {
  const values = (Array.isArray(points) ? points : []).map((point) => Number(point.value))
    .filter((value) => Number.isFinite(value));
  if (!values.length) return { count: 0, latest: null, avg: null, min: null, max: null, delta: null, slope: null };
  const average = values.reduce((sum, value) => sum + value, 0) / values.length;
  let slope = null;
  if (values.length >= 2) {
    const xs = values.map((_value, index) => index);
    const meanX = (values.length - 1) / 2;
    const denominator = xs.reduce((sum, x) => sum + (x - meanX) ** 2, 0);
    if (denominator) {
      slope = xs.reduce((sum, x, index) => sum + (x - meanX) * (values[index] - average), 0) / denominator;
    }
  }
  return {
    count: values.length,
    latest: values[values.length - 1],
    avg: average,
    min: Math.min(...values),
    max: Math.max(...values),
    delta: values[values.length - 1] - values[0],
    slope,
  };
}

export function average(values) {
  const list = (Array.isArray(values) ? values : []).map(Number).filter(Number.isFinite);
  return list.length ? list.reduce((sum, value) => sum + value, 0) / list.length : null;
}

export function stddev(values) {
  const list = (Array.isArray(values) ? values : []).map(Number).filter(Number.isFinite);
  if (list.length < 2) return null;
  const mean = average(list);
  return Math.sqrt(list.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (list.length - 1));
}

/** 把 "HH:MM" 换成分钟，用于比较入睡规律度。 */
export function clockMinutes(value) {
  const match = /^(\d{2}):(\d{2})$/.exec(String(value || ""));
  if (!match) return null;
  const minutes = Number(match[1]) * 60 + Number(match[2]);
  return minutes < 12 * 60 ? minutes + 1440 : minutes; // 凌晨归到前一天之后
}

// ── SVG 图表 ─────────────────────────────────────────────────────────

function scale(value, min, max, from, to) {
  if (!Number.isFinite(value) || max === min) return (from + to) / 2;
  return from + ((value - min) / (max - min)) * (to - from);
}

function niceRange(values, { min = null, max = null, padding = 0.08 } = {}) {
  const list = (values || []).filter((value) => Number.isFinite(value));
  if (!list.length) return { min: min ?? 0, max: max ?? 1 };
  let low = min ?? Math.min(...list);
  let high = max ?? Math.max(...list);
  if (low === high) {
    const bump = Math.abs(low) * 0.05 || 1;
    low -= bump;
    high += bump;
  }
  const span = high - low;
  if (min === null) low -= span * padding;
  if (max === null) high += span * padding;
  return { min: low, max: high };
}

export function sparkline(points, { width = 96, height = 28, color = "var(--accent)" } = {}) {
  const list = (points || []).filter((point) => Number.isFinite(Number(point.value)));
  if (list.length < 2) return `<span class="health-spark-empty">—</span>`;
  const values = list.map((point) => Number(point.value));
  const range = niceRange(values, { padding: 0.12 });
  const step = list.length === 1 ? 0 : width / (list.length - 1);
  const coords = values.map((value, index) => [
    (index * step).toFixed(1),
    scale(value, range.min, range.max, height - 3, 3).toFixed(1),
  ]);
  const line = coords.map(([x, y], index) => `${index ? "L" : "M"}${x},${y}`).join(" ");
  return `<svg class="health-spark" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true" focusable="false"><path d="${line}" fill="none" stroke="${escapeHtml(color)}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}

/**
 * 多序列折线图：统一量纲时按原值画；量纲不同（单位不一致）时各自归一化到 0-100。
 * series = [{ label, points: [{date,value}], unit, color }]
 */
export function lineChart(series, {
  width = 640, height = 220, padding = { top: 16, right: 16, bottom: 26, left: 42 },
  normalize = false, valuePrecision = 1,
} = {}) {
  const list = (Array.isArray(series) ? series : []).filter((item) => item && item.points?.length);
  if (!list.length) {
    return { svg: `<div class="health-chart-empty">还没有足够的数据画图。</div>`, min: null, max: null, normalized: normalize };
  }
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const allDates = [...new Set(list.flatMap((item) => item.points.map((point) => point.date)))].sort();
  const dateIndex = new Map(allDates.map((day, index) => [day, index]));
  const shouldNormalize = normalize || new Set(list.map((item) => item.unit || "")).size > 1;
  const ranges = new Map();
  for (const item of list) {
    const values = item.points.map((point) => Number(point.value));
    ranges.set(item, shouldNormalize ? { min: Math.min(...values), max: Math.max(...values) } : niceRange(values));
  }
  const globalRange = shouldNormalize
    ? { min: 0, max: 100 }
    : niceRange([...list.flatMap((item) => item.points.map((point) => Number(point.value)))]);

  const x = (day) => padding.left + scale(dateIndex.get(day) ?? 0, 0, Math.max(1, allDates.length - 1), 0, innerWidth);
  const y = (value, item) => {
    const range = shouldNormalize ? ranges.get(item) : globalRange;
    return padding.top + scale(Number(value), range.min, range.max, innerHeight, 0);
  };

  const grid = [];
  const labels = [];
  for (let step = 0; step <= 3; step += 1) {
    const ratio = step / 3;
    const lineY = padding.top + innerHeight * ratio;
    const value = globalRange.max - (globalRange.max - globalRange.min) * ratio;
    grid.push(`<line x1="${padding.left}" y1="${lineY.toFixed(1)}" x2="${width - padding.right}" y2="${lineY.toFixed(1)}" class="health-grid-line"/>`);
    labels.push(`<text x="${padding.left - 6}" y="${(lineY + 3).toFixed(1)}" text-anchor="end" class="health-axis-text">${escapeHtml(shouldNormalize ? `${Math.round(value)}%` : formatNumber(value, Math.abs(value) < 10 ? valuePrecision : 0))}</text>`);
  }
  const xTickIndexes = allDates.length > 4 ? [0, Math.floor((allDates.length - 1) / 2), allDates.length - 1] : allDates.map((_day, index) => index);
  const xLabels = xTickIndexes.map((index) => {
    const day = allDates[index];
    return `<text x="${x(day).toFixed(1)}" y="${height - 8}" text-anchor="middle" class="health-axis-text">${escapeHtml(shortDate(day))}</text>`;
  });

  const paths = [];
  let index = 0;
  for (const item of list) {
    const color = item.color || `var(--health-series-${index})`;
    const coords = item.points
      .filter((point) => Number.isFinite(Number(point.value)))
      .map((point) => [x(point.date), y(point.value, item)]);
    if (!coords.length) continue;
    const path = coords.map(([cx, cy], order) => `${order ? "L" : "M"}${cx.toFixed(1)},${cy.toFixed(1)}`).join(" ");
    paths.push(`<path d="${path}" fill="none" stroke="${escapeHtml(color)}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`);
    if (coords.length <= 45) {
      for (const [cx, cy] of coords) {
        paths.push(`<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="2.6" fill="${escapeHtml(color)}"/>`);
      }
    }
    index += 1;
  }

  const legend = list.map((item, order) => {
    const color = item.color || `var(--health-series-${order % 4})`;
    return `<span class="health-legend-item"><svg width="9" height="9" viewBox="0 0 9 9" aria-hidden="true" focusable="false"><circle cx="4.5" cy="4.5" r="4.5" fill="${escapeHtml(color)}"/></svg>${escapeHtml(item.label || "")}</span>`;
  }).join("");
  return {
    svg: `<figure class="health-chart">
      <div class="health-chart-legend">${legend}${shouldNormalize ? '<span class="health-legend-note">量纲不同，已各自按区间归一化</span>' : ""}</div>
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="健康指标趋势图" preserveAspectRatio="xMidYMid meet">
        ${grid.join("")}${labels.join("")}${xLabels.join("")}${paths.join("")}
      </svg>
    </figure>`,
    min: globalRange.min, max: globalRange.max, normalized: shouldNormalize,
  };
}

export function barChart(points, {
  width = 640, height = 180, padding = { top: 12, right: 10, bottom: 24, left: 36 },
  unit = "", color = "var(--accent)",
} = {}) {
  const list = (points || []).filter((point) => Number.isFinite(Number(point.value)));
  if (!list.length) return `<div class="health-chart-empty">还没有足够的数据画图。</div>`;
  const values = list.map((point) => Number(point.value));
  const range = niceRange([0, ...values], { min: 0, padding: 0.06 });
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const slot = innerWidth / Math.max(1, list.length);
  const barWidth = Math.max(3, Math.min(28, slot * 0.62));
  const bars = list.map((point, index) => {
    const value = Number(point.value);
    const barHeight = Math.max(1, ((value - range.min) / (range.max - range.min)) * innerHeight);
    const barX = padding.left + slot * index + (slot - barWidth) / 2;
    const barY = padding.top + innerHeight - barHeight;
    return `<rect x="${barX.toFixed(1)}" y="${barY.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${barHeight.toFixed(1)}" rx="2" fill="${escapeHtml(point.color || color)}"><title>${escapeHtml(`${point.date} ${formatNumber(value, 0, unit)}`)}</title></rect>`;
  }).join("");
  const labels = list.map((point, index) => {
    if (list.length > 10 && index % Math.ceil(list.length / 6) !== 0 && index !== list.length - 1) return "";
    return `<text x="${(padding.left + slot * index + slot / 2).toFixed(1)}" y="${height - 8}" text-anchor="middle" class="health-axis-text">${escapeHtml(shortDate(point.date))}</text>`;
  }).join("");
  return `<figure class="health-chart"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="健康指标柱状图" preserveAspectRatio="xMidYMid meet">
    <line x1="${padding.left}" y1="${padding.top + innerHeight}" x2="${width - padding.right}" y2="${padding.top + innerHeight}" class="health-grid-line"/>
    <text x="${padding.left - 6}" y="${padding.top + 8}" text-anchor="end" class="health-axis-text">${escapeHtml(formatNumber(range.max, 0))}</text>
    ${bars}${labels}</svg></figure>`;
}

/** 热力图：value 越高颜色越深；缺数据是空格。 */
export function heatmap(points, { days = 60, end = todayText(), max = 5 } = {}) {
  const byDate = new Map((points || []).map((point) => [point.date, Number(point.value)]));
  const dates = lastDates(days, end);
  const cells = dates.map((day) => {
    const value = byDate.get(day);
    if (!Number.isFinite(value)) {
      return `<span class="health-heat-cell empty" title="${escapeHtml(day)} 没有记录"></span>`;
    }
    const level = Math.max(1, Math.min(4, Math.ceil((value / (Number(max) || 5)) * 4)));
    return `<span class="health-heat-cell level-${level}" title="${escapeHtml(`${day} ${formatNumber(value, 0)}`)}"></span>`;
  });
  return `<div class="health-heatmap" aria-label="最近 ${days} 天热力图">${cells.join("")}</div>`;
}

/**
 * 健康指数圆环：用 SVG stroke-dasharray（表现属性，不是内联 style，CSP 允许）。
 * 无数据时显示灰色空环与「—」。
 */
export function ringMarkup(score, { size = 92, label = "健康指数" } = {}) {
  const total = score === null || score === undefined || score === "" ? null : Number(score);
  const has = Number.isFinite(total);
  const value = has ? Math.max(0, Math.min(100, total)) : 0;
  const tone = has ? scoreTone(value) : "none";
  return `<div class="health-ring ${tone}">
    <svg viewBox="0 0 100 100" width="${size}" height="${size}" aria-hidden="true" focusable="false">
      <circle class="health-ring-track" cx="50" cy="50" r="42" fill="none" stroke-width="9" pathLength="100"/>
      <circle class="health-ring-arc" cx="50" cy="50" r="42" fill="none" stroke-width="9" pathLength="100"
        stroke-dasharray="${value} 100" stroke-linecap="round" transform="rotate(-90 50 50)"/>
    </svg>
    <span class="health-ring-value">${has ? Math.round(value) : "—"}</span>
    <span class="health-ring-label">${escapeHtml(label)}</span>
  </div>`;
}

/** 进度条：用原生 <progress>，宽度由浏览器画，不写内联样式。 */
export function progressMarkup(percent, { className = "", label = "" } = {}) {
  const value = Math.max(0, Math.min(100, Number(percent) || 0));
  return `<progress class="health-progress${className ? ` ${className}` : ""}" max="100" value="${value.toFixed(1)}" aria-label="${escapeHtml(label)}">${Math.round(value)}%</progress>`;
}

export function macroProgress(totals, targets) {
  const rows = [
    { key: "protein_g", label: "蛋白质", unit: "g", cls: "protein" },
    { key: "carbs_g", label: "碳水", unit: "g", cls: "carbs" },
    { key: "fat_g", label: "脂肪", unit: "g", cls: "fat" },
  ];
  return rows.map((row) => {
    const value = Number(totals?.[row.key]) || 0;
    const target = Number(targets?.[row.key]) || 0;
    return { ...row, value, target, percent: target ? Math.min(100, (value / target) * 100) : 0 };
  });
}

export function missingFor(record) {
  const items = [];
  const sections = record?.sections || {};
  if (!sections.sleep?.duration_min) items.push({ key: "sleep", label: "睡眠" });
  if (!sections.body?.weight_kg) items.push({ key: "body", label: "体重" });
  if (!sections.nutrition?.water_ml) items.push({ key: "nutrition", label: "饮水" });
  if (!sections.activity?.steps) items.push({ key: "activity", label: "步数" });
  if (!sections.mind?.mood) items.push({ key: "mind", label: "情绪" });
  return items;
}

export function insightTone(type) {
  return { sleep_training: "sleep", steps_mood: "mind", protein_weight: "nutrition", training_hr: "vitals", weekend_sleep: "sleep" }[type] || "neutral";
}
