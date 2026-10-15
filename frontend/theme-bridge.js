// 跟随 dsh 的主题：宿主页面把它的实时主题 token 发进来，这里只负责校验与应用。
//
// 三条通道，缺一不可：
//   1. iframe URL 上的 ?theme= 首帧提示（embed-boot.js 在 <head> 同步应用，避免闪白）；
//   2. 宿主在 iframe load 之后、以及每次 theme/change 时 postMessage 的完整 token 包；
//   3. 本模块启动时主动请求一次，兜住“宿主面板先就绪、我们后加载”的竞态。
//
// 宿主只提供 dsh 的别名 token 值；把值翻译成我们的调色板是 styles.css 里
// html[data-torch-host="dsh"] 那一层的职责，所以这里不掺任何设计决定。

export const THEME_MESSAGE = "torch:theme";
export const THEME_REQUEST = "torch:theme-request";
export const HOST_NAME = "dsh";

/** dsh 通过 inspect 声明的别名 token；只接受这些名字，值只当字符串用。 */
export const DSH_TOKENS = Object.freeze([
  "--dsw-alias-bg-base",
  "--dsw-alias-bg-layer-1",
  "--dsw-alias-bg-layer-2",
  "--dsw-alias-bg-overlay",
  "--dsw-alias-border-l1",
  "--dsw-alias-border-l2",
  "--dsw-alias-brand-primary",
  "--dsw-alias-label-primary",
  "--dsw-alias-label-secondary",
  "--dsw-alias-state-error-primary",
  "--dsw-alias-state-idle-primary",
  "--dsw-alias-state-success-primary",
  "--dsw-alias-state-warn-primary",
  "--dsw-specific-sidebar-fill",
]);

/** 没有这几个就说明 dsh 还没把主题渲染出来，此时不能给 <html> 打上宿主标记：
 *  否则 styles.css 的宿主层会引用空 token，颜色会整片失效。 */
const REQUIRED_TOKENS = ["--dsw-alias-bg-base", "--dsw-alias-bg-layer-1", "--dsw-alias-label-primary"];

const MAX_VALUE_LENGTH = 160;

export function isLoopbackOrigin(origin) {
  if (typeof origin !== "string") return false;
  return /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?$/i.test(origin);
}

/** Obsidian 桌面版用 `app://obsidian.md` 来源承载插件页面（0.99.0：整个产品住进 Obsidian）。 */
export const OBSIDIAN_ORIGIN = "app://obsidian.md";

/** 主题只肯听本机宿主（dsh 回环端口）与本地 Obsidian，其它来源一律忽略。 */
export function isAllowedHostOrigin(origin) {
  return isLoopbackOrigin(origin) || origin === OBSIDIAN_ORIGIN;
}

export function isEmbedded(win) {
  return Boolean(win && win.parent && win.parent !== win);
}

/** 把宿主消息规整成 { scheme, tokens }；不是我们的消息就返回 null。 */
export function readTheme(data) {
  if (!data || typeof data !== "object" || data.type !== THEME_MESSAGE) return null;
  const scheme = data.scheme === "dark" ? "dark" : "light";
  const raw = data.tokens && typeof data.tokens === "object" ? data.tokens : {};
  const tokens = {};
  for (const name of DSH_TOKENS) {
    const value = raw[name];
    if (typeof value !== "string") continue;
    const trimmed = value.trim();
    if (!trimmed || trimmed.length > MAX_VALUE_LENGTH) continue;
    tokens[name] = trimmed;
  }
  return { scheme, tokens };
}

/** 只切换明暗、不接管调色板（宿主 token 还没到位时用）。 */
export function applyScheme(element, scheme) {
  if (!element || !element.dataset) return false;
  element.dataset.torchTheme = scheme === "dark" ? "dark" : "light";
  return true;
}

/** 应用宿主的完整主题：写入 --dsw-* 变量并打上宿主标记。 */
export function applyTheme(element, theme) {
  if (!element || !element.dataset || !theme) return false;
  const names = Object.keys(theme.tokens);
  const complete = REQUIRED_TOKENS.every((name) => names.includes(name));
  if (!complete) {
    applyScheme(element, theme.scheme);
    return false;
  }
  for (const [name, value] of Object.entries(theme.tokens)) {
    try {
      element.style.setProperty(name, value);
    } catch { /* 非法值：跳过这一个，其余照常 */ }
  }
  element.dataset.torchTheme = theme.scheme;
  element.dataset.torchHost = HOST_NAME;
  return true;
}

/** 独立打开（非嵌入）时跟随系统明暗；嵌入时一律听宿主的。 */
export function initThemeBridge(win = window, doc = document) {
  const embedded = isEmbedded(win);
  const element = doc.documentElement;
  const onMessage = (event) => {
    if (!event || event.source !== win.parent) return;
    if (!isAllowedHostOrigin(event.origin)) return;
    const theme = readTheme(event.data);
    if (theme) applyTheme(element, theme);
  };
  win.addEventListener("message", onMessage);

  let media = null;
  const followSystem = () => {
    if (element.dataset.torchHost) return; // 宿主已接管，系统偏好不再参与
    applyScheme(element, media && media.matches ? "dark" : "light");
  };
  if (!embedded && typeof win.matchMedia === "function") {
    media = win.matchMedia("(prefers-color-scheme: dark)");
    followSystem();
    media.addEventListener?.("change", followSystem);
  }

  if (embedded) {
    try {
      win.parent.postMessage({ type: THEME_REQUEST }, "*");
    } catch { /* 跨源或宿主已关闭：保持当前主题 */ }
  }

  return () => win.removeEventListener("message", onMessage);
}
