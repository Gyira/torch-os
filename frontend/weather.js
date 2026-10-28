// 天气小组件（2026-10-27 重做，参考 MinimalistWeather 的极简天气层级）：
// 卡片只留时钟、年月日/星期、城市与今日天气（高/低温）；5 日预报、城市管理、
// 体感/湿度/风/日出日落等详细数据都收进点击后打开的详情浮窗。
// 这里只放无 DOM 依赖的纯函数（node 直测友好）；请求、localStorage 和事件在 weather-widget.js。
// 注意：测试假环境没有 setInterval——走秒用 setTimeout 对齐秒边界链式自调度（x-hub 同款）。

/** selectedKey 的哨兵值：按 IP 自动定位（不是用户添加的常用城市）。 */
export const WEATHER_AUTO_KEY = "__auto__";

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]
  ));
}

function pick(parts, type) {
  const found = parts.find((part) => part.type === type);
  return found ? found.value : "";
}

function num(value) {
  const number = Number(value);
  return value == null || value === "" || !Number.isFinite(number) ? null : number;
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

/** 2026-10-06 + 周二 → 2026年10月6日 周二（用户要求卡片显示“年月日 星期几”）。 */
export function clockDateLabel(pieces) {
  const [year, month, day] = String(pieces?.date || "").split("-");
  const weekday = String(pieces?.weekday || "").trim();
  if (!year || !month || !day) return weekday;
  return `${year}年${Number(month)}月${Number(day)}日${weekday ? ` ${weekday}` : ""}`;
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
    `<p class="clock-date">${esc(clockDateLabel(pieces))}</p>`;
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

/** 常用城市：统一成 {name, latitude, longitude, admin1, country}，坏数据直接丢掉。 */
export function normalizeCity(raw) {
  if (raw == null) return null;
  if (typeof raw === "string") {
    const name = raw.trim();
    return name ? { name, latitude: null, longitude: null, admin1: "", country: "" } : null;
  }
  const name = String(raw.name || "").trim();
  if (!name) return null;
  const latitude = num(raw.latitude);
  const longitude = num(raw.longitude);
  const hasCoords = latitude != null && longitude != null
    && latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180;
  return {
    name,
    latitude: hasCoords ? latitude : null,
    longitude: hasCoords ? longitude : null,
    admin1: String(raw.admin1 || "").trim(),
    country: String(raw.country || "").trim(),
  };
}

/** 常用城市的稳定键：内置城市只用名字；搜索结果带坐标，避免同名异地互相覆盖。 */
export function cityKey(city) {
  const normalized = normalizeCity(city);
  if (!normalized) return "";
  return normalized.latitude != null && normalized.longitude != null
    ? `${normalized.name}|${normalized.latitude.toFixed(3)},${normalized.longitude.toFixed(3)}`
    : normalized.name;
}

export function normalizeFavorites(raw) {
  const list = Array.isArray(raw) ? raw : [];
  const result = [];
  const seen = new Set();
  for (const item of list) {
    const city = normalizeCity(item);
    const key = cityKey(city);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    result.push(city);
  }
  return result;
}

export function addFavorite(favorites, city) {
  const list = normalizeFavorites(favorites);
  const next = normalizeCity(city);
  if (!next) return list;
  const key = cityKey(next);
  if (list.some((item) => cityKey(item) === key)) return list;
  return [...list, next];
}

export function removeFavorite(favorites, key) {
  return normalizeFavorites(favorites).filter((item) => cityKey(item) !== key);
}

export function findFavorite(favorites, key) {
  return normalizeFavorites(favorites).find((item) => cityKey(item) === key) || null;
}

/** 请求参数：内置城市只传名字；搜索结果的异地城市必须带坐标。 */
export function cityRequest(city) {
  const normalized = normalizeCity(city);
  if (!normalized) return { city: "", latitude: null, longitude: null };
  return {
    city: normalized.name,
    latitude: normalized.latitude,
    longitude: normalized.longitude,
  };
}

/** 卡片主体：只显示城市、当前天气和今日高/低温（其余内容进详情浮窗）。 */
export function weatherCardHtml(payload) {
  if (!payload || payload.temperature == null) {
    return '<p class="weather-empty field-help">天气暂时拿不到，点击查看详情重试</p>';
  }
  const today = forecastDays(payload)[0] || null;
  const range = today
    ? `最高 ${value(today.high, "°")} · 最低 ${value(today.low, "°")}`
    : "今日高低温暂缺";
  return [
    '<div class="weather-compact-now">',
    `<span class="weather-compact-icon" aria-hidden="true">${esc(payload.icon || "🌡️")}</span>`,
    `<span class="weather-compact-temp">${value(payload.temperature, "°")}</span>`,
    `<span class="weather-compact-desc">${esc(payload.weather || "")}</span>`,
    "</div>",
    '<div class="weather-compact-foot">',
    `<span class="weather-compact-city">${esc(payload.city || "未知城市")}</span>`,
    `<span class="weather-compact-range">${esc(range)}</span>`,
    "</div>",
  ].join("");
}

/** 卡片的无障碍名称（读屏能直接听到城市、天气与高低温）。 */
export function weatherAriaLabel(payload) {
  if (!payload || payload.temperature == null) return "天气暂时拿不到，点击查看详情重试";
  const today = forecastDays(payload)[0];
  const parts = [
    `天气：${payload.city || "未知城市"}`,
    payload.weather || "",
    `当前 ${value(payload.temperature, "°")}`,
  ];
  if (today) parts.push(`最高 ${value(today.high, "°")}，最低 ${value(today.low, "°")}`);
  return `${parts.filter(Boolean).join("，")}。点击查看详情`;
}

function dayDateLabel(day) {
  const [year, month, date] = String(day?.day || "").split("-");
  if (!year || !month || !date) return String(day?.weekday || "");
  return `${year}年${Number(month)}月${Number(date)}日${day.weekday ? ` ${day.weekday}` : ""}`;
}

function previewOf(previews, key) {
  if (!previews) return null;
  if (typeof previews.get === "function") return previews.get(key) || null;
  return previews[key] || null;
}

function cityPreviewText(preview) {
  if (!preview || preview.temperature == null) return "—";
  return `${preview.icon || "🌡️"} ${value(preview.temperature, "°")}`;
}

function cityRowHtml(city, { active, previews, deletable = true, key = "", subLabel = "" }) {
  const name = String(city?.name || "当前定位");
  const sub = subLabel || [city?.admin1, city?.country].filter(Boolean).join(" · ") || "常用城市";
  const action = deletable
    ? `<button type="button" class="weather-city-remove" data-weather-city-remove="${esc(key)}" aria-label="删除常用城市 ${esc(name)}" title="删除常用城市">×</button>`
    : key === WEATHER_AUTO_KEY
      ? '<button type="button" class="weather-city-remove weather-city-save" data-weather-city-save-current aria-label="把当前定位城市加入常用城市" title="加入常用城市">＋</button>'
      : "";
  return [
    `<li class="weather-city-row${active ? " is-active" : ""}">`,
    `<button type="button" class="weather-city-pick" data-weather-city="${esc(key)}"` +
      `${active ? ' aria-current="true"' : ""} aria-label="切换到 ${esc(name)}">`,
    `<span class="weather-city-name">${esc(name)}</span>`,
    `<span class="weather-city-sub">${esc(sub)}</span>`,
    `<span class="weather-city-preview" data-weather-preview="${esc(key)}">${esc(cityPreviewText(previewOf(previews, key)))}</span>`,
    "</button>",
    action,
    "</li>",
  ].join("");
}

/** 城市搜索结果：本地内置城市 + 在线 geocoding 结果（已添加的置灰）。 */
export function weatherSearchResultsHtml(state, favorites = []) {
  const results = Array.isArray(state?.results) ? state.results : [];
  const saved = normalizeFavorites(favorites);
  const keys = new Set(saved.map((city) => cityKey(city)));
  const names = new Set(saved.map((city) => city.name));
  const rows = results.map((raw, index) => {
    const city = normalizeCity(raw);
    if (!city) return "";
    const added = keys.has(cityKey(city)) || names.has(city.name);
    const sub = [city.admin1, city.country].filter(Boolean).join(" · ")
      || (raw?.local ? "内置城市" : "城市");
    return [
      `<button type="button" class="weather-search-result" data-weather-city-add="${index}"${added ? " disabled" : ""}>`,
      `<span class="weather-search-name">${esc(city.name)}</span>`,
      `<span class="weather-search-sub">${esc(sub)}</span>`,
      `<span class="weather-search-action">${added ? "已添加" : "添加"}</span>`,
      "</button>",
    ].join("");
  }).join("");
  const query = String(state?.query || "").trim();
  let status = "";
  let tone = "";
  if (state?.loading) {
    status = "正在搜索…";
  } else if (state?.error) {
    status = state.error;
    tone = " is-error";
  } else if (query && !rows) {
    status = "没有找到匹配的城市。";
  }
  return `<p class="weather-search-status${tone}" role="status">${esc(status)}</p>` +
    (rows ? `<div class="weather-search-list">${rows}</div>` : "");
}

function searchPlaceholder(count) {
  return count > 0 ? "搜索城市并添加（回车确认）" : "搜索城市并添加";
}

/** 左栏：当前定位（若不在常用里）+ 常用城市 + 搜索添加。天气取不到时也要能用。 */
function cityPanelHtml({ favorites, selectedKey, previews, searchState, currentName = "" }) {
  const saved = normalizeFavorites(favorites);
  const currentInFavorites = saved.some((city) => cityKey(city) === selectedKey);
  const cityRows = [];
  if (currentName && !currentInFavorites && (selectedKey === WEATHER_AUTO_KEY || selectedKey === ""
    || !findFavorite(saved, selectedKey))) {
    cityRows.push(cityRowHtml({ name: currentName }, {
      active: true, previews, deletable: false, key: WEATHER_AUTO_KEY, subLabel: "当前定位",
    }));
  }
  for (const city of saved) {
    const key = cityKey(city);
    cityRows.push(cityRowHtml(city, { active: key === selectedKey, previews, key }));
  }
  return [
    '<aside class="weather-cities" aria-label="常用城市">',
    '<div class="weather-cities-head"><div><p class="eyebrow">CITIES</p><h3>常用城市</h3></div>',
    `<span class="weather-cities-count">${saved.length} 个</span></div>`,
    '<label class="visually-hidden" for="weather-city-search">搜索并添加常用城市</label>',
    '<input id="weather-city-search" class="weather-city-search" data-weather-city-search type="search"',
    ` autocomplete="off" placeholder="${esc(searchPlaceholder(saved.length))}" value="${esc(searchState?.query || "")}" />`,
    '<div class="weather-search-results" data-weather-search-results>',
    weatherSearchResultsHtml(searchState, saved),
    "</div>",
    cityRows.length ? `<ul class="weather-city-list">${cityRows.join("")}</ul>` : "",
    saved.length
      ? ""
      : '<p class="weather-cities-empty field-help">还没有常用城市，搜索上面的城市名添加；删除全部后会按 IP 自动定位。</p>',
    '<p class="weather-cities-note">城市列表保存在当前个人数据库的 .torch-os/personal-data.json；浏览器缓存只是镜像。</p>',
    "</aside>",
  ].join("");
}

/**
 * 详情浮窗：
 * - 左栏：常用城市（切换 / 删除 / 搜索添加）；
 * - 右栏：选中城市与选中日期的天气、5 日预报、8 项详细数据；
 * - selected=0 时主数字用实况温度，其它日期用当天最高温。
 */
export function weatherDetailHtml(payload, options = {}) {
  const favorites = normalizeFavorites(options.favorites);
  const previews = options.previews || null;
  const selectedKey = String(options.selectedKey || "");
  const notice = String(options.notice || "");
  const loading = Boolean(options.loading);
  const searchState = options.searchState || {};

  if (!payload || payload.temperature == null) {
    const empty = [
      `<div class="weather-detail-empty" aria-busy="${loading ? "true" : "false"}">`,
      `<p class="weather-detail-empty-title">${loading ? "正在读取天气…" : "天气暂时拿不到"}</p>`,
      notice ? `<p class="field-help">${esc(notice)}</p>` : "",
      loading ? "" : '<button type="button" class="button secondary" data-weather-retry>重新读取</button>',
      "</div>",
    ].join("");
    // 天气取不到时仍然显示常用城市：可以删掉失效城市或换个城市重试。
    return [
      '<div class="weather-detail">',
      cityPanelHtml({ favorites, selectedKey, previews, searchState }),
      '<div class="weather-main">',
      empty,
      "</div>",
      "</div>",
    ].join("");
  }

  const days = forecastDays(payload);
  const selected = clampSelection(options.selected, days.length);
  const day = days[selected] || null;
  const isToday = selected === 0;
  const heroIcon = isToday ? (payload.icon || "🌡️") : (day?.icon || "🌡️");
  const heroTemp = isToday ? payload.temperature : day?.high;
  const heroDesc = isToday ? (payload.weather || "") : (day?.weather || "");
  const heroDate = day ? dayDateLabel(day) : "";
  const observed = String(payload.observed_at || "").slice(11, 16);
  const range = day ? `最高 ${value(day.high, "°")} · 最低 ${value(day.low, "°")}` : "高低温暂缺";
  const meta = [];
  if (isToday && observed) meta.push(`实况 ${observed}`);
  meta.push(isToday ? "今天" : "预报");

  const dayButtons = days.map((item) => [
    `<button type="button" class="weather-day${item.index === selected ? " is-selected" : ""}"`,
    ` data-weather-day="${item.index}" aria-pressed="${item.index === selected ? "true" : "false"}"`,
    ` aria-label="查看 ${esc(item.label)}${item.date ? ` ${esc(item.date)}` : ""} 的详细天气">`,
    `<span class="weather-day-name">${esc(item.label)}</span>`,
    `<span class="weather-day-icon" aria-hidden="true">${esc(item.icon)}</span>`,
    `<span class="weather-day-desc">${esc(item.weather || "—")}</span>`,
    `<span class="weather-day-range">${esc(`${value(item.high, "°")} / ${value(item.low, "°")}`)}</span>`,
    `<span class="weather-day-rain">降水 ${value(item.precipitation, "%")}</span>`,
    "</button>",
  ].join("")).join("");

  const metrics = [
    ["体感", `${value(day?.apparentHigh, "°")} / ${value(day?.apparentLow, "°")}`],
    ["湿度", value(day?.humidity, "%")],
    ["降水可能性", value(day?.precipitation, "%")],
    ["风向", day?.windDirection || "—"],
    ["风速", value(day?.windSpeed, " km/h", 1)],
    ["气压", value(day?.pressure, " hPa")],
    ["日出", day?.sunrise || "—"],
    ["日落", day?.sunset || "—"],
  ].map(([label, text]) => (
    `<div class="weather-metric"><span>${esc(label)}</span><strong>${esc(text)}</strong></div>`
  )).join("");

  const footerMeta = [];
  if (payload.fetched_at) footerMeta.push(`更新于 ${String(payload.fetched_at).replace("T", " ").slice(0, 16)}`);
  if (payload.cached) footerMeta.push("缓存");
  const sourceLabels = {
    pick: "内置城市", search: "搜索城市", env: "配置城市", ip: "IP 定位", default: "默认城市",
  };
  if (sourceLabels[payload.source]) footerMeta.push(sourceLabels[payload.source]);
  const footNote = payload.note || "";
  const cacheNotice = notice ? `<span class="weather-detail-status status-message warning">${esc(notice)}</span>` : "";

  return [
    '<div class="weather-detail" aria-busy="' + (loading ? "true" : "false") + '">',
    cityPanelHtml({ favorites, selectedKey, previews, searchState, currentName: payload.city }),
    '<div class="weather-main">',
    '<header class="weather-detail-hero">',
    '<div class="weather-hero-text">',
    `<p class="weather-detail-city">${esc(payload.city || "未知城市")}</p>`,
    `<p class="weather-detail-date">${esc(heroDate)} · ${esc(meta.join(" · "))}</p>`,
    `<p class="weather-detail-range">${esc(range)}</p>`,
    "</div>",
    '<div class="weather-hero-now">',
    `<span class="weather-detail-icon" aria-hidden="true">${esc(heroIcon)}</span>`,
    `<span class="weather-detail-temp">${value(heroTemp, "°", isToday ? 1 : 0)}</span>`,
    `<span class="weather-detail-desc">${esc(heroDesc)}</span>`,
    "</div>",
    "</header>",
    days.length
      ? `<section class="weather-detail-days" role="group" aria-label="五日预报">${dayButtons}</section>`
      : '<p class="weather-detail-no-days field-help">这次没有拿到五日预报，只显示实况。</p>',
    `<section class="weather-detail-metrics" aria-label="${esc(day ? dayDateLabel(day) : "选中日期")}的详细天气">${metrics}</section>`,
    '<footer class="weather-detail-foot">',
    `<span class="weather-detail-updated">${esc(footerMeta.join(" · "))}</span>`,
    cacheNotice,
    footNote ? `<span class="weather-detail-source status-message">${esc(footNote)}</span>` : "",
    `<button type="button" class="text-button" data-weather-refresh${loading ? " disabled" : ""}>${loading ? "刷新中…" : "刷新"}</button>`,
    "</footer>",
    "</div>",
    "</div>",
  ].join("");
}

/** 时钟区随秒变化的那两行（供外层一次渲染）。 */
export function renderClock(element, pieces) {
  if (!element) return;
  element.innerHTML = clockHtml(pieces);
}

/** 卡片天气区填充（详情浮窗由 weather-widget.js 直接使用 weatherDetailHtml）。 */
export function renderWeather(element, payload) {
  if (!element) return;
  element.innerHTML = weatherCardHtml(payload);
}
