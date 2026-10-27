// 工作台天气小组件控制层（2026-10-27 重做）：
// 卡片只显示时钟 / 年月日星期 / 城市 / 今日天气与高低温；点卡片打开详情浮窗。
// 浮窗里能看 5 日预报、切换天气日期、查看 8 项详细数据，并搜索/添加/删除常用城市。
// 城市列表与选中城市写入 .torch-os/personal-data.json（localStorage 只是缓存）；普通取天气仍走后端内置城市表或已保存坐标。
// 页面每次打开都会强制刷新一次天气（后端另有 30 分钟缓存，刷新失败时回退到本机上次数据）。
import { $, api, openDialog } from "./core.js";
import {
  WEATHER_AUTO_KEY, addFavorite, cityKey, cityRequest, findFavorite,
  normalizeCity, normalizeFavorites, removeFavorite, renderClock, renderWeather, startClock,
  weatherAriaLabel, weatherDetailHtml, weatherSearchResultsHtml,
} from "./weather.js";

const CARD_ID = "workbench-weather-card";
const CONTENT_ID = "workbench-weather";
const CLOCK_ID = "workbench-clock";
const FAVORITES_KEY = "torch-os:weather-cities";
const READY_KEY = "torch-os:weather-cities-ready";
const SELECTED_KEY = "torch-os:weather-city";   // 兼容旧版：以前这里存的是城市名
const CACHE_KEY = "torch-os:weather";
const MAX_FAVORITES = 8;
const PREVIEW_TTL = 10 * 60 * 1000;
const STALE_MS = 30 * 60 * 1000;
const RETRY_MS = 60 * 1000;
const SEARCH_DEBOUNCE_MS = 280;
const TICK_MS = 5 * 60 * 1000;

let favorites = [];
let selectedKey = WEATHER_AUTO_KEY;
let payload = null;
let selectedDay = 0;
let previews = new Map();   // cityKey → payload（常用城市列表里的天气预览）
let previewAt = new Map();  // cityKey → 上次预览时间
let notice = "";
let loading = false;
let requestToken = 0;
let previewToken = 0;
let lastLoadAt = 0;
let lastAttemptAt = 0;
let initialized = false;
let clockStarted = false;
let dialogOpen = false;
let searchState = { query: "", results: [], loading: false, error: "" };
let searchTimer = 0;
let searchToken = 0;

function storage() {
  try { return window.localStorage; } catch { return null; }
}

function readJson(key, fallback) {
  try {
    const raw = storage()?.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key, value) {
  try { storage()?.setItem(key, JSON.stringify(value)); } catch { /* 隐私模式/配额满：本次页面仍可用 */ }
}

function readSelected() {
  try { return String(storage()?.getItem(SELECTED_KEY) || "").trim(); } catch { return ""; }
}

function writeSelected() {
  try { storage()?.setItem(SELECTED_KEY, selectedKey || ""); } catch { /* 同上 */ }
}

function favoritesReady() {
  if (favorites.length) return true;
  try { return Boolean(storage()?.getItem(READY_KEY)); } catch { return false; }
}

function markFavoritesReady() {
  try { storage()?.setItem(READY_KEY, "1"); } catch { /* 同上 */ }
}

function writeFavorites() {
  writeJson(FAVORITES_KEY, favorites);
}

function rememberPreview(key, data) {
  if (!data || data.temperature == null) return;
  const normalized = key || WEATHER_AUTO_KEY;
  previews.set(normalized, data);
  previewAt.set(normalized, Date.now());
}

function readCache() {
  const cached = readJson(CACHE_KEY, null);
  if (cached?.payload?.temperature != null) return cached;
  if (cached?.temperature != null) return { key: "", payload: cached }; // 旧版缓存
  return null;
}

function writeCache() {
  if (!payload) return;
  writeJson(CACHE_KEY, { key: selectedKey, payload });
}

/** 启动时恢复城市列表、选中城市和上次天气；旧版只存城市名也能平滑迁移。 */
function restoreState() {
  favorites = normalizeFavorites(readJson(FAVORITES_KEY, []));
  if (favorites.length) markFavoritesReady();
  selectedKey = readSelected();
  if (!favorites.length && selectedKey && selectedKey !== WEATHER_AUTO_KEY) {
    const legacy = normalizeCity(selectedKey);
    if (legacy) {
      favorites = [legacy];
      markFavoritesReady();
      writeFavorites();
      selectedKey = cityKey(legacy);
    }
  }
  if (favorites.length) {
    if (selectedKey !== WEATHER_AUTO_KEY) {
      const match = findFavorite(favorites, selectedKey)
        || favorites.find((city) => city.name === selectedKey);
      selectedKey = match ? cityKey(match) : cityKey(favorites[0]);
    }
  } else {
    selectedKey = WEATHER_AUTO_KEY;
  }
  writeSelected();

  const cached = readCache();
  if (cached && (cached.key === selectedKey || !cached.key)) {
    payload = cached.payload;
    rememberPreview(selectedKey, payload);
  }
}

function seedFavorites(data) {
  const city = normalizeCity({
    name: data?.city, latitude: data?.latitude, longitude: data?.longitude,
  });
  if (city) {
    favorites = [city];
    selectedKey = cityKey(city);
    rememberPreview(selectedKey, data);
  } else {
    favorites = [];
    selectedKey = WEATHER_AUTO_KEY;
  }
  markFavoritesReady();
  writeFavorites();
  writeSelected();
}

function targetCity() {
  if (!selectedKey || selectedKey === WEATHER_AUTO_KEY) return null;
  return findFavorite(favorites, selectedKey);
}

function weatherUrl(force, city) {
  const params = new URLSearchParams();
  const request = cityRequest(city);
  if (request.city) {
    params.set("city", request.city);
    if (request.latitude != null && request.longitude != null) {
      params.set("latitude", String(request.latitude));
      params.set("longitude", String(request.longitude));
    }
  }
  if (force) params.set("refresh", "1");
  const query = params.toString();
  return `/api/weather${query ? `?${query}` : ""}`;
}

function paintCard() {
  const host = document.getElementById(CONTENT_ID);
  if (!host) return;
  if (!payload && loading) {
    host.innerHTML = '<p class="weather-empty field-help">正在读取天气…</p>';
  } else {
    renderWeather(host, payload);
  }
  const card = document.getElementById(CARD_ID);
  if (card) {
    card.setAttribute("aria-label", weatherAriaLabel(payload));
    card.setAttribute("aria-busy", loading ? "true" : "false");
    card.title = notice || "点击查看 5 日预报、城市切换与详细天气";
  }
}

function paintDialog() {
  if (!dialogOpen) return;
  $("#detail-dialog")?.classList.add("weather-mode");
  openDialog("天气详情", weatherDetailHtml(payload, {
    selected: selectedDay,
    favorites,
    selectedKey,
    previews,
    notice,
    loading,
    searchState,
  }));
}

async function loadWeather({ force = false, city = undefined } = {}) {
  const requested = city !== undefined ? normalizeCity(city) : targetCity();
  const targetKey = requested ? cityKey(requested) : WEATHER_AUTO_KEY;
  const sameCity = targetKey === selectedKey;
  const token = ++requestToken;
  loading = true;
  notice = "";
  lastAttemptAt = Date.now();
  paintCard();
  paintDialog();

  try {
    const data = await api(weatherUrl(force, requested));
    if (token !== requestToken) return;
    payload = data;
    if (!favoritesReady()) {
      seedFavorites(data);
    } else {
      selectedKey = targetKey && findFavorite(favorites, targetKey) ? targetKey : WEATHER_AUTO_KEY;
    }
    if (!sameCity) selectedDay = 0; // 换城市回到今天；同一城市刷新保留正在看的日期
    rememberPreview(selectedKey, payload);
    writeSelected();
    writeCache();
    lastLoadAt = Date.now();
    notice = "";
  } catch (error) {
    if (token !== requestToken) return;
    if (!payload) {
      const cached = readCache();
      if (cached) {
        payload = cached.payload;
        rememberPreview(selectedKey, payload);
      }
    }
    notice = payload
      ? `天气：${error.message}（显示上次读到的数据）`
      : `天气：${error.message}`;
    // 旧版存的城市名已不在内置表/搜索坐标失效：忘掉这个城市，自动改取剩下的或当前定位。
    if (error.status === 422 && requested) {
      const key = cityKey(requested);
      if (findFavorite(favorites, key)) {
        favorites = removeFavorite(favorites, key);
        writeFavorites();
        selectedKey = favorites.length ? cityKey(favorites[0]) : WEATHER_AUTO_KEY;
        writeSelected();
        loading = false;
        const next = favorites.length ? favorites[0] : null;
        window.setTimeout(() => loadWeather({ city: next }), 0);
        return;
      }
    }
    loading = false;
    paintCard();
    paintDialog();
    return;
  } finally {
    if (token === requestToken) {
      loading = false;
      paintCard();
      paintDialog();
    }
  }
}

function localResults(query) {
  const text = String(query || "").trim();
  if (!text) return [];
  // 只从后端随天气给的内置城市表里挑：IP 定位到表外城市时没有坐标，不能按名字硬取。
  const names = Array.isArray(payload?.cities) ? payload.cities : [];
  return names
    .map((name) => String(name || "").trim())
    .filter((name) => name && name.includes(text))
    .slice(0, 6)
    .map((name) => {
      const city = normalizeCity(name);
      return city ? { ...city, local: true } : null;
    })
    .filter(Boolean);
}

function mergeSearchResults(local, online) {
  const result = [...local];
  const names = new Set(result.map((city) => city.name));
  const keys = new Set(result.map((city) => cityKey(city)));
  for (const city of online) {
    const key = cityKey(city);
    if (!key || names.has(city.name) || keys.has(key)) continue;
    names.add(city.name);
    keys.add(key);
    result.push(city);
  }
  return result.slice(0, 8);
}

async function runSearch(query) {
  const text = String(query || "").trim();
  const token = ++searchToken;
  searchState = { query: text, results: localResults(text), loading: false, error: "" };
  renderSearchResults();
  if (!text) return;
  // 内置城市里已经有完全匹配：不为了同一个名字再发一次在线请求。
  if (searchState.results.some((city) => city.name === text)) return;
  searchState = { ...searchState, loading: true };
  renderSearchResults();
  try {
    const data = await api(`/api/weather/cities?q=${encodeURIComponent(text)}`);
    if (token !== searchToken) return;
    const online = (Array.isArray(data?.results) ? data.results : [])
      .map((item) => normalizeCity(item)).filter(Boolean);
    searchState = {
      query: text, results: mergeSearchResults(localResults(text), online),
      loading: false, error: "",
    };
  } catch (error) {
    if (token !== searchToken) return;
    searchState = {
      query: text, results: localResults(text), loading: false,
      error: `在线搜索不可用：${error.message}`,
    };
  }
  renderSearchResults();
}

function renderSearchResults() {
  if (!dialogOpen) return;
  const container = document.querySelector("#dialog-content [data-weather-search-results]");
  if (!container) return;
  container.innerHTML = weatherSearchResultsHtml(searchState, favorites);
}

function onSearchInput(value) {
  const query = String(value || "");
  const text = query.trim();
  window.clearTimeout(searchTimer);
  searchToken += 1;
  searchState = { query, results: text ? localResults(text) : [], loading: false, error: "" };
  renderSearchResults();
  if (!text) return;
  searchTimer = window.setTimeout(() => runSearch(query), SEARCH_DEBOUNCE_MS);
}

function updatePreviewRows() {
  if (!dialogOpen) return;
  for (const node of document.querySelectorAll("#dialog-content [data-weather-preview]")) {
    const key = node.dataset.weatherPreview || "";
    const data = previews.get(key);
    node.textContent = data && data.temperature != null
      ? `${data.icon || "🌡️"} ${Math.round(Number(data.temperature))}°`
      : "—";
  }
}

async function fetchPreviews() {
  if (!dialogOpen) return;
  const token = ++previewToken;
  const now = Date.now();
  const misses = favorites.filter((city) => {
    const key = cityKey(city);
    if (key === selectedKey && payload) return false;
    return now - (previewAt.get(key) || 0) > PREVIEW_TTL;
  }).slice(0, MAX_FAVORITES);
  await Promise.allSettled(misses.map(async (city) => {
    const key = cityKey(city);
    try {
      const data = await api(weatherUrl(false, city));
      if (token !== previewToken || !dialogOpen) return;
      rememberPreview(key, data);
      updatePreviewRows();
    } catch { /* 预览失败就保持 “—”，不打扰用户 */ }
  }));
}

function openDetail() {
  dialogOpen = true;
  selectedDay = 0;
  window.clearTimeout(searchTimer);
  searchToken += 1;
  searchState = { query: "", results: [], loading: false, error: "" };
  paintDialog();
  if (!payload && !loading) loadWeather({ city: targetCity() });
  fetchPreviews();
}

function closeDetail() {
  if (!dialogOpen) return;
  dialogOpen = false;
  previewToken += 1;
  searchToken += 1;
  window.clearTimeout(searchTimer);
  $("#detail-dialog")?.classList.remove("weather-mode");
}

function selectCity(key) {
  if (key === selectedKey) {
    paintDialog();
    return undefined;
  }
  if (!key || key === WEATHER_AUTO_KEY) return loadWeather({ city: null });
  const city = findFavorite(favorites, key);
  if (!city) return undefined;
  return loadWeather({ city });
}

function addCity(raw) {
  const city = normalizeCity(raw);
  if (!city) return;
  // 同名城市已经在列表里（可能来自内置表、也可能来自搜索结果）：直接切过去，不重复添加。
  const existing = findFavorite(favorites, cityKey(city))
    || favorites.find((item) => item.name === city.name);
  if (existing) {
    window.clearTimeout(searchTimer);
    searchToken += 1;
    searchState = { query: "", results: [], loading: false, error: "" };
    loadWeather({ city: existing });
    return;
  }
  if (favorites.length >= MAX_FAVORITES) {
    notice = `最多保存 ${MAX_FAVORITES} 个常用城市，先删除一个再添加。`;
    paintDialog();
    return;
  }
  favorites = addFavorite(favorites, city);
  markFavoritesReady();
  writeFavorites();
  window.clearTimeout(searchTimer);
  searchToken += 1;
  searchState = { query: "", results: [], loading: false, error: "" };
  loadWeather({ city });
}

function removeCity(key) {
  const removed = findFavorite(favorites, key);
  if (!removed) return;
  favorites = removeFavorite(favorites, key);
  writeFavorites();
  previews.delete(key);
  previewAt.delete(key);
  if (selectedKey !== key) {
    paintDialog();
    return;
  }
  if (favorites.length) {
    selectedKey = cityKey(favorites[0]);
    writeSelected();
    loadWeather({ city: favorites[0] });
  } else {
    selectedKey = WEATHER_AUTO_KEY;
    writeSelected();
    loadWeather({ city: null });
  }
}

function refreshWeather() {
  return loadWeather({ force: true, city: targetCity() });
}

function handleDialogClick(event) {
  const target = event.target;
  const day = target?.closest?.("[data-weather-day]");
  if (day) {
    selectedDay = Number(day.dataset.weatherDay) || 0;
    paintDialog();
    return;
  }
  const saveCurrent = target?.closest?.("[data-weather-city-save-current]");
  if (saveCurrent) {
    if (payload?.city) {
      addCity({ name: payload.city, latitude: payload.latitude, longitude: payload.longitude });
    }
    return;
  }
  const remove = target?.closest?.("[data-weather-city-remove]");
  if (remove) {
    removeCity(remove.dataset.weatherCityRemove || "");
    return;
  }
  const city = target?.closest?.("[data-weather-city]");
  if (city) {
    selectCity(city.dataset.weatherCity || "");
    return;
  }
  const add = target?.closest?.("[data-weather-city-add]");
  if (add) {
    const index = Number(add.dataset.weatherCityAdd);
    const result = Number.isInteger(index) ? searchState.results[index] : null;
    if (result) addCity(result);
    return;
  }
  if (target?.closest?.("[data-weather-refresh], [data-weather-retry]")) {
    refreshWeather();
  }
}

function handleDialogInput(event) {
  const input = event.target?.closest?.("[data-weather-city-search]");
  if (input) onSearchInput(input.value);
}

function handleDialogKeydown(event) {
  const input = event.target?.closest?.("[data-weather-city-search]");
  if (!input || event.key !== "Enter") return;
  event.preventDefault();
  if (searchState.results.length) {
    addCity(searchState.results[0]);
  } else {
    window.clearTimeout(searchTimer);
    runSearch(input.value);
  }
}

function bindDialog() {
  const content = $("#dialog-content");
  content?.addEventListener("click", handleDialogClick);
  content?.addEventListener("input", handleDialogInput);
  content?.addEventListener("keydown", handleDialogKeydown);
  $("#detail-dialog")?.addEventListener("close", closeDetail);
}

function bindCard() {
  const card = document.getElementById(CARD_ID);
  if (!card || card.dataset.weatherReady === "1") return;
  card.dataset.weatherReady = "1";
  card.addEventListener("click", (event) => {
    if (event.target?.closest?.("button, a, input, select, textarea")) return;
    openDetail();
  });
  card.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    if (event.target?.closest?.("button, a, input, select, textarea")) return;
    event.preventDefault();
    openDetail();
  });
}

export function initWeatherWidget() {
  if (initialized) return false;
  initialized = true;
  restoreState();
  bindCard();
  bindDialog();
  paintCard();
  if (!clockStarted) {
    clockStarted = true;
    startClock((pieces) => renderClock(document.getElementById(CLOCK_ID), pieces),
      (fn, ms) => window.setTimeout(fn, ms));
  }
  // 用户要求：每次打开系统都自动更新一次天气；后端仍会按 30 分钟缓存兜底。
  loadWeather({ force: true });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden || loading) return;
    if (Date.now() - lastLoadAt > STALE_MS) loadWeather({ force: true, city: targetCity() });
  });
  if (typeof window.setInterval === "function") {
    window.setInterval(() => {
      if (document.hidden || loading) return;
      if (Date.now() - lastLoadAt < STALE_MS) return;
      loadWeather({ city: targetCity() });
    }, TICK_MS);
  }
  return true;
}

/** 工作台每次重画/切回时调用：首次启动由 initWeatherWidget 负责，之后只在过期时补一次。 */
export function loadWeatherWidget({ force = false } = {}) {
  if (!initialized) {
    initWeatherWidget();
    return undefined;
  }
  if (force) return loadWeather({ force: true, city: targetCity() });
  if (!payload) {
    if (loading) return undefined;
    if (lastAttemptAt && Date.now() - lastAttemptAt < RETRY_MS) return undefined;
    return loadWeather({ city: targetCity() });
  }
  paintCard();
  if (Date.now() - lastLoadAt > STALE_MS && !loading) {
    if (lastAttemptAt && Date.now() - lastAttemptAt < RETRY_MS) return undefined;
    return loadWeather({ city: targetCity() });
  }
  return undefined;
}
