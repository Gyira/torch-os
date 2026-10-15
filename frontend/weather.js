// 时钟天气卡（2026-10-05，2026-10-06 加 5 日预报）：时钟在本地走秒、天气来自后端 /api/weather
// （Open-Meteo 实况 + 5 天日级，30 分钟缓存，后端把 WMO 码与风向都翻成中文）。
// 卡片分两半（照 Apex Dashboard 的天气挂件 + 用户要求）：上半区是**选中那一天**的详情
// （默认今天：实况温度 + 该日 8 项），下半区是 5 天缩略条，点哪天下半区就换成那天，右上角“今天”回到今天。
// 保持"无 DOM 依赖的纯函数 + 一个渲染/启动函数"形态（照 almanac.js），node 直测友好。
// 注意：测试假环境没有 setInterval——走秒用 setTimeout 对齐秒边界链式自调度（x-hub 同款）。

function esc(value) {
  return String(value).replace(/[&<>"']/g, (char) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]
  ));
}

function pick(parts, type) {
  const found = parts.find((part) => part.type === type);
  return found ? found.value : "";
}

function num(value) {
  const number = Number(value);
  return value == null || value === "" || Number.isNaN(number) ? null : number;
}

/** 数值 + 单位；拿不到就给 “—”（列表里缺一项也不塌）。 */
function value(value, unit = "", digits = 0) {
  const number = num(value);
  return number == null ? "—" : `${number.toFixed(digits)}${unit}`;
}

/** 上海时区的时钟三段：HH:MM:SS / YYYY-MM-DD / 周X。 */
export function clockPieces(now = Date.now()) {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", hour12: false, hour: "2-digit", minute: "2-digit",
    second: "2-digit", year: "numeric", month: "2-digit", day: "2-digit", weekday: "short",
  }).formatToParts(new Date(now));
  return {
    time: `${pick(parts, "hour")}:${pick(parts, "minute")}:${pick(parts, "second")}`,
    date: `${pick(parts, "year")}-${pick(parts, "month")}-${pick(parts, "day")}`,
    weekday: pick(parts, "weekday"),
  };
}

/** 秒边界对齐的链式计时（可注入调度器，便于测试）。 */
export function startClock(render, schedule = (fn, ms) => setTimeout(fn, ms), now = () => Date.now()) {
  const tick = () => {
    render(clockPieces(now()));
    schedule(tick, 1000 - (now() % 1000));
  };
  tick();
}

export function clockHtml(pieces) {
  return `<p class="clock-time">${esc(pieces.time)}</p>` +
    `<p class="clock-date">${esc(pieces.date)} ${esc(pieces.weekday)}</p>`;
}

/** 后端 forecast → 可点选的日子列表（缺字段给 null；第一天的 label 固定是“今天”）。 */
export function forecastDays(payload) {
  const raw = Array.isArray(payload?.forecast) ? payload.forecast : [];
  return raw.slice(0, 5).map((item, index) => ({
    index,
    label: index === 0 ? "今天" : String(item?.weekday || `第${index + 1}天`),
    day: String(item?.day || ""),
    date: String(item?.day || "").slice(5),
    weekday: String(item?.weekday || ""),
    weather: String(item?.weather || ""),
    icon: String(item?.icon || "🌡️"),
    high: num(item?.high), low: num(item?.low),
    apparentHigh: num(item?.apparent_high), apparentLow: num(item?.apparent_low),
    humidity: num(item?.humidity), precipitation: num(item?.precipitation),
    windDirection: String(item?.wind_direction || ""), windSpeed: num(item?.wind_speed),
    pressure: num(item?.pressure),
    sunrise: String(item?.sunrise || ""), sunset: String(item?.sunset || ""),
  }));
}

/** 选中项钳位到有效范围（数据变短、或传了奇怪的 index 都不会越界）。 */
export function clampSelection(selected, length) {
  if (!length) return 0;
  const index = Math.trunc(Number(selected));
  return Number.isFinite(index) ? Math.min(length - 1, Math.max(0, index)) : 0;
}

/** 城市下拉的选项：后端随天气一起给的 `cities`；当前城市不在表里（IP 定位到表外）时补在最前。
 *  后端没给清单（老版本）就返回空数组 → 上层退回纯文字，不画只有一个选项的空下拉。 */
export function cityOptions(payload) {
  const raw = Array.isArray(payload?.cities) ? payload.cities : [];
  const names = raw.map((name) => String(name || "").trim()).filter(Boolean);
  const current = String(payload?.city || "").trim();
  return current && names.length && !names.includes(current) ? [current, ...names] : names;
}

/** 城市：有清单就是下拉（选完重取那城），没有清单（老后端）就还是纯文字。 */
function cityHtml(payload, names) {
  const current = String(payload?.city || "");
  if (!names.length) return `<span class="weather-city-name">${esc(current)}</span>`;
  return [
    '<select class="weather-city-select" aria-label="选择城市">',
    names.map((name) => `<option value="${esc(name)}"${name === current ? " selected" : ""}>${esc(name)}</option>`).join(""),
    "</select>",
  ].join("");
}

function dayButton(day, isSelected) {
  // 缩略条一格只有 ~38px：高/低写紧凑一点（23°/17°），不加空格。
  const range = day.high == null && day.low == null
    ? "—" : `${value(day.high, "°")}/${value(day.low, "°")}`;
  return [
    `<button type="button" class="weather-week-day${isSelected ? " is-selected" : ""}"`,
    ` data-weather-day="${day.index}" aria-pressed="${isSelected ? "true" : "false"}"`,
    ` aria-label="查看 ${esc(day.label)}${day.date ? ` ${esc(day.date)}` : ""} 的天气：${esc(day.weather || "")}">`,
    `<span class="weather-week-name">${esc(day.label)}</span>`,
    `<span class="weather-week-icon" aria-hidden="true">${esc(day.icon)}</span>`,
    `<span class="weather-week-range">${esc(range)}</span>`,
    "</button>",
  ].join("");
}

/**
 * 天气卡内容：上半区 = 选中那天（默认今天），下半区 = 5 天缩略条。
 * 今天是实况（大温度 + 实况时间），别的天用当天的最高温当主数字。
 */
export function weatherHtml(payload, options = {}) {
  if (!payload || payload.temperature == null) {
    return '<p class="weather-empty field-help">天气暂时拿不到，点“刷新”重试；不影响其它功能。</p>';
  }
  const days = forecastDays(payload);
  const selected = clampSelection(options.selected, days.length);
  const day = days[selected] || null;
  const isToday = selected === 0;
  const note = payload.note ? `<p class="weather-note field-help">${esc(payload.note)}</p>` : "";
  const parts = [];

  // 主行：今天=实况温度；别的天=当天最高温。城市那一格是选城市的下拉（哪一天都看得到城市）。
  const heroIcon = isToday ? (payload.icon || "🌡️") : (day?.icon || "🌡️");
  const heroTemp = isToday ? payload.temperature : day?.high;
  const heroDesc = isToday ? (payload.weather || "") : (day?.weather || "");
  const cachedTag = isToday && payload.cached ? " · 缓存" : "";
  parts.push(
    '<div class="weather-now">',
    `<span class="weather-icon" aria-hidden="true">${esc(heroIcon)}</span>`,
    `<span class="weather-temp">${value(heroTemp, "°", isToday ? 1 : 0)}</span>`,
    `<span class="weather-city">${cityHtml(payload, cityOptions(payload))}` +
    `<small>${esc(heroDesc)}${cachedTag}</small></span>`,
    "</div>");

  if (day) {
    parts.push(
      `<p class="weather-range">高 ${value(day.high, "°")} · 低 ${value(day.low, "°")}</p>`,
      `<p class="weather-meta">${esc(day.date)} ${esc(day.weekday)}` +
      (isToday ? ` · 实况 ${esc(String(payload.observed_at || "").slice(11, 16) || "—")}` : "") +
      "</p>",
      // 8 项：体感、湿度、降水可能性、风向、风速、气压、日出、日落。
      '<div class="weather-grid weather-metrics">',
      `<div><small>体感</small><strong>${value(day.apparentHigh, "°")} / ${value(day.apparentLow, "°")}</strong></div>`,
      `<div><small>湿度</small><strong>${value(day.humidity, "%")}</strong></div>`,
      `<div><small>降水可能性</small><strong>${value(day.precipitation, "%")}</strong></div>`,
      `<div><small>风向</small><strong>${esc(day.windDirection || "—")}</strong></div>`,
      `<div><small>风速</small><strong>${value(day.windSpeed, " km/h", 1)}</strong></div>`,
      `<div><small>气压</small><strong>${value(day.pressure, " hPa")}</strong></div>`,
      `<div><small>日出</small><strong>${esc(day.sunrise || "—")}</strong></div>`,
      `<div><small>日落</small><strong>${esc(day.sunset || "—")}</strong></div>`,
      "</div>");
  }
  if (days.length) {
    parts.push('<div class="weather-week" role="group" aria-label="选择要看的日期">',
      days.map((item) => dayButton(item, item.index === selected)).join(""), "</div>");
  }
  parts.push(note);
  return parts.join("");
}

/** 时钟区随日期变化的那一行（供外层一次渲染）。 */
export function renderClock(element, pieces) {
  if (!element) return;
  element.innerHTML = clockHtml(pieces);
}

/** 天气卡填充（外层负责 fetch，这里只画；selected 是选中的第几天，0 = 今天）。 */
export function renderWeather(element, payload, selected = 0) {
  if (!element) return;
  element.innerHTML = weatherHtml(payload, { selected });
}
