// 网页书签栏的数据层（2026-10-28）：纯函数、不碰 DOM，方便 Node 单测。
//
// 书签存在 localStorage 的 `torch-os:bookmark-bar`；personal-data.js 会把它镜像进
// <个人数据库>/.torch-os/personal-data.json —— 和天气城市、小组件顺序同一套规则，
// 书签跟着当前库走：换电脑/换浏览器都还在，换库则各是各的。
//
// 只放行 http/https：javascript:、file:、obsidian: 这类协议不进书签栏，
// 免得一条误粘贴的地址在点击时变成脚本入口。

export const BOOKMARK_STORAGE_KEY = "torch-os:bookmark-bar";
export const BOOKMARK_LIMIT = 24;
export const BOOKMARK_NAME_LIMIT = 24;
export const BOOKMARK_URL_LIMIT = 500;

// 第一次打开时的书签：只放这条工作流真正要用的一站，其余在齿轮里手动加。
export const BOOKMARK_PRESETS = Object.freeze([
  { name: "DeepSeek 网页版", url: "https://chat.deepseek.com/" },
]);

const SCHEME_PATTERN = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;
const HTTP_PATTERN = /^(https?):\/\/([^\s/?#]+)([^\s]*)$/i;
const HOST_PATTERN = /^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$|^\d{1,3}(?:\.\d{1,3}){3}$|^localhost$/;
const ID_PATTERN = /^[A-Za-z0-9:_-]{1,64}$/;

function hashText(text) {
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash * 31 + text.charCodeAt(index)) | 0;
  }
  return (hash >>> 0).toString(36);
}

function bookmarkId(url, used = new Set()) {
  const base = `b-${hashText(url)}`;
  if (!used.has(base)) return base;
  let index = 2;
  while (used.has(`${base}-${index}`)) index += 1;
  return `${base}-${index}`;
}

/**
 * 把用户输入整理成一个可点击的 http(s) 地址；看不懂就返回 ""。
 * 省略协议的（chat.deepseek.com）补 https://；主机名统一小写，裸域名补 "/"。
 */
export function normalizeBookmarkUrl(value) {
  const text = String(value ?? "").trim();
  if (!text || text.length > BOOKMARK_URL_LIMIT) return "";
  const candidate = SCHEME_PATTERN.test(text) ? text : `https://${text}`;
  const match = HTTP_PATTERN.exec(candidate);
  if (!match) return "";
  const protocol = `${match[1].toLowerCase()}:`;
  // 带账号密码的地址（user:pass@host）不接收：书签栏不需要，也容易藏钓鱼。
  if (match[2].includes("@")) return "";
  const authority = match[2].toLowerCase();
  const host = authority.split(":")[0];
  const portMatch = /:(\d{1,5})$/.exec(authority);
  if (!host || host.length > 253 || !HOST_PATTERN.test(host)) return "";
  if (authority !== host && !portMatch) return "";
  if (portMatch && Number(portMatch[1]) > 65535) return "";
  const rest = match[3] || "/";
  return `${protocol}//${authority}${rest}`;
}

/** 网址里给人看的那一段：去掉协议、www. 和端口。 */
export function hostLabel(url) {
  const match = /^https?:\/\/([^/?#]+)/i.exec(String(url ?? ""));
  if (!match) return "";
  return match[1].replace(/^www\./i, "").replace(/:\d+$/, "");
}

/** 书签图标里的那个字：名称首字，没名称就用域名首字。 */
export function siteInitial(name, url) {
  const text = String(name ?? "").trim();
  const chars = Array.from(text || hostLabel(url));
  return (chars[0] || "?").toUpperCase();
}

/** 图标底色序号（0-5）：由网址决定，同一个站永远同一个颜色。 */
export function colorIndex(url) {
  const text = String(url ?? "");
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash * 31 + text.charCodeAt(index)) % 99991;
  }
  return Math.abs(hash) % 6;
}

/** 名称留空时用域名，去掉换行和多余空格，超长截断。 */
export function normalizeBookmarkName(value, url) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim().slice(0, BOOKMARK_NAME_LIMIT);
  return text || hostLabel(url) || "未命名";
}

/**
 * 把任意来源的数组整理成合法书签列表：丢掉坏地址、按网址去重、补 id、限量。
 * 后端/旧版本写坏的数据也走这里，前端不会因为一条脏数据整条栏都渲染不出来。
 */
export function sanitizeBookmarks(items) {
  const source = Array.isArray(items) ? items : [];
  const ids = new Set();
  const urls = new Set();
  const result = [];
  for (const item of source) {
    if (!item || typeof item !== "object") continue;
    const url = normalizeBookmarkUrl(item.url);
    if (!url || urls.has(url)) continue;
    let id = typeof item.id === "string" && ID_PATTERN.test(item.id) ? item.id : "";
    if (!id || ids.has(id)) id = bookmarkId(url, ids);
    ids.add(id);
    urls.add(url);
    result.push({ id, name: normalizeBookmarkName(item.name, url), url });
    if (result.length >= BOOKMARK_LIMIT) break;
  }
  return result;
}

/** localStorage 里的字符串 → 书签列表（坏 JSON 当作空列表，不抛错）。 */
export function parseBookmarks(raw) {
  if (typeof raw !== "string" || !raw.trim()) return [];
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    return [];
  }
  const items = Array.isArray(payload) ? payload : Array.isArray(payload?.bookmarks) ? payload.bookmarks : [];
  return sanitizeBookmarks(items);
}

/** 书签列表 → localStorage 里的字符串（永远只写 id/name/url 三个字段）。 */
export function serializeBookmarks(items) {
  return JSON.stringify(sanitizeBookmarks(items).map(({ id, name, url }) => ({ id, name, url })));
}

/** 第一次打开时的默认书签。 */
export function bookmarkDefaults() {
  const ids = new Set();
  return BOOKMARK_PRESETS.map((preset) => {
    const url = normalizeBookmarkUrl(preset.url);
    const id = bookmarkId(url, ids);
    ids.add(id);
    return { id, name: normalizeBookmarkName(preset.name, url), url };
  });
}

/** 读当前库的书签；键不存在时才给默认值（用户删空后是空列表，不会复活）。 */
export function readBookmarks(storage) {
  let raw = null;
  try {
    raw = storage?.getItem?.(BOOKMARK_STORAGE_KEY) ?? null;
  } catch {
    raw = null;
  }
  if (raw === null) return bookmarkDefaults();
  return parseBookmarks(raw);
}

/** 写回 localStorage；存储不可用（隐私模式/配额满）时返回 false，页面这次照常可点。 */
export function writeBookmarks(storage, items) {
  const text = serializeBookmarks(items);
  try {
    storage?.setItem?.(BOOKMARK_STORAGE_KEY, text);
    return true;
  } catch {
    return false;
  }
}

/** 追加一条书签（Edge 的行为：新加的排在最后）。 */
export function addBookmark(items, { name = "", url = "" } = {}) {
  const list = sanitizeBookmarks(items);
  if (list.length >= BOOKMARK_LIMIT) {
    return { list, bookmark: null, error: `书签最多 ${BOOKMARK_LIMIT} 个，先删掉几个再加。` };
  }
  const normalized = normalizeBookmarkUrl(url);
  if (!normalized) {
    return { list, bookmark: null, error: "网址要填 http:// 或 https:// 开头的完整地址，例如 https://chat.deepseek.com。" };
  }
  if (list.some((item) => item.url === normalized)) {
    return { list, bookmark: null, error: "这个网址已经在书签栏里了。" };
  }
  const used = new Set(list.map((item) => item.id));
  const bookmark = {
    id: bookmarkId(normalized, used),
    name: normalizeBookmarkName(name, normalized),
    url: normalized,
  };
  return { list: [...list, bookmark], bookmark, error: "" };
}

/** 改名称/网址；网址非法或撞上别的书签时整条不改，返回原列表。 */
export function updateBookmark(items, id, patch = {}) {
  const list = sanitizeBookmarks(items);
  const index = list.findIndex((item) => item.id === id);
  if (index < 0) return { list, bookmark: null, error: "这条书签已经不在列表里了。" };
  const current = list[index];
  const normalized = Object.hasOwn(patch, "url") ? normalizeBookmarkUrl(patch.url) : current.url;
  if (!normalized) {
    return { list, bookmark: null, error: "网址要填 http:// 或 https:// 开头的完整地址，例如 https://chat.deepseek.com。" };
  }
  if (list.some((item, position) => position !== index && item.url === normalized)) {
    return { list, bookmark: null, error: "这个网址已经在书签栏里了。" };
  }
  const name = Object.hasOwn(patch, "name") ? normalizeBookmarkName(patch.name, normalized) : current.name;
  const bookmark = { id: current.id, name, url: normalized };
  return { list: list.map((item, position) => (position === index ? bookmark : item)), bookmark, error: "" };
}

/** 删一条；已经不在了也算成功（幂等，避免两个窗口各删一次时报错）。 */
export function removeBookmark(items, id) {
  const list = sanitizeBookmarks(items);
  const next = list.filter((item) => item.id !== id);
  return { list: next, removed: next.length !== list.length, error: "" };
}

/** 左右移动：offset 是 -1/+1，到头就停在原地。 */
export function moveBookmark(items, id, offset) {
  const list = sanitizeBookmarks(items);
  const index = list.findIndex((item) => item.id === id);
  if (index < 0) return { list, moved: false, error: "这条书签已经不在列表里了。" };
  const target = index + (Number(offset) || 0);
  if (target < 0 || target >= list.length || target === index) {
    return { list, moved: false, error: "" };
  }
  const next = [...list];
  const [bookmark] = next.splice(index, 1);
  next.splice(target, 0, bookmark);
  return { list: next, moved: true, error: "" };
}
