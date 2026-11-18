import { initDshPane, loadDshPane, suspendDshPane } from "./dsh-pane.js";
import { isObsidianHost } from "./task-open.js";

let provider = "dsh", initialized = false, frame = 0, fallback = 0;
// 宿主只认「可见」的那一帧来创建 webview；切页、开合下拉会让内容区瞬时不可见。
// 2026-11-19 在真机里踩到：Obsidian 内嵌 iframe 的 rAF 会被挂起，回调一次都不跑，
// 于是宿主永远收不到 position，占位文案就一直留着。所以这里既补重试，也用定时器兜底，
// 并在切到 ChatGPT 后“催”宿主直到它回报状态。
let retries = 0, lastKey = "", lastAt = 0;
let hostAnswered = false, pollTimer = 0;
// Obsidian 里这个 iframe 是第三方上下文，localStorage 会被拦（写入抛异常），
// 所以平台偏好还要按 URL 上的 agent= 读回来，宿主那边另存一份。
function queryValue(search, key) {
  for (const part of String(search || "").replace(/^\?/, "").split("&")) {
    if (!part) continue;
    const [name, ...rest] = part.split("=");
    if (name === key) return rest.join("=");
  }
  return "";
}
try {
  const fromHost = queryValue(location.search, "agent");
  provider = (fromHost || localStorage.getItem("torch-os:agent-provider")) === "chatgpt" ? "chatgpt" : "dsh";
} catch {
  provider = queryValue(location.search, "agent") === "chatgpt" ? "chatgpt" : "dsh";
}
function hostMessage(message) {
  if (isObsidianHost()) window.parent.postMessage({ type: "torch:chatgpt", ...message }, "*");
}
function stageRect() {
  const rect = document.querySelector("#chatgpt-stage")?.getBoundingClientRect();
  return rect && rect.width > 0 && rect.height > 0 ? rect : null;
}
function flushBounds() {
  const rect = stageRect();
  const visible = provider === "chatgpt" && Boolean(rect) && !document.hidden
    && !document.querySelector("dialog[open], .panel-nav-menu:not([hidden])");
  // 同一位置的高频重复不重发：宿主每帧重排版时这里一秒能触发几十次。
  const key = visible ? `${rect.x}|${rect.y}|${rect.width}|${rect.height}` : "hidden";
  const now = performance.now();
  if (key !== lastKey || now - lastAt >= 120) {
    lastKey = key; lastAt = now;
    hostMessage({ action: "bounds", visible, rect: visible ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : null });
  }
  if (visible) {
    retries = 0;
    return;
  }
  if (provider === "chatgpt" && retries < 8) {
    retries += 1;
    window.setTimeout(scheduleBounds, 150 * retries);
  }
}
function scheduleBounds() {
  cancelAnimationFrame(frame);
  window.clearTimeout(fallback);
  let done = false;
  frame = requestAnimationFrame(() => {
    if (done) return;
    done = true;
    flushBounds();
  });
  // 隐藏文档里 rAF 会被挂起；定时器兜底，保证宿主至少拿到一帧。
  fallback = window.setTimeout(() => {
    if (done) return;
    done = true;
    flushBounds();
  }, 120);
}
/** 切到 ChatGPT 后催宿主：没收到它的状态回报就一直重报位置（最多约 20 秒）。 */
function watchHost() {
  window.clearInterval(pollTimer);
  hostAnswered = false;
  let ticks = 0;
  pollTimer = window.setInterval(() => {
    if (provider !== "chatgpt" || hostAnswered || ++ticks > 50) {
      window.clearInterval(pollTimer);
      pollTimer = 0;
      return;
    }
    lastKey = "";
    scheduleBounds();
  }, 400);
}
export function setAgentProvider(value) {
  provider = value === "chatgpt" ? "chatgpt" : "dsh";
  try { localStorage.setItem("torch-os:agent-provider", provider); } catch {}
  // 存储被拦时也要让偏好活过下一次 reload：宿主把它记进本机设置，重载时回传到 URL。
  hostMessage({ action: "provider", value: provider });
}
function openExternal() {
  if (isObsidianHost()) window.parent.postMessage({ type: "torch:open-url", url: "https://chatgpt.com/" }, "*");
  else window.open("https://chatgpt.com/", "_blank", "noopener,noreferrer");
}
export function initAgentPane() {
  if (initialized) return;
  initialized = true;
  const root = document.querySelector("#agent-view");
  const tabs = document.createElement("div"); tabs.className = "agent-provider-tabs";
  tabs.setAttribute("role", "group"); tabs.setAttribute("aria-label", "对话平台");
  tabs.innerHTML = '<button type="button" data-agent-provider="chatgpt">ChatGPT</button><button type="button" data-agent-provider="dsh">DSH</button>';
  root.prepend(tabs);
  const pane = document.createElement("section"); pane.id = "chatgpt-pane"; pane.hidden = true;
  const placeholder = isObsidianHost()
    ? "正在连接 Obsidian 内嵌浏览器。首次使用请在网页中登录；若一直空白，多半是本机访问不了 chatgpt.com（需要代理），可在插件设置里填写代理，或点「浏览器打开」。"
    : "浏览器版请点击上方“浏览器打开”。在桌面 Obsidian 中可使用内嵌对话。";
  pane.innerHTML = `<div class="chatgpt-toolbar"><strong>ChatGPT</strong><span id="chatgpt-status" role="status"></span><button type="button" id="chatgpt-reload">重新载入</button><button type="button" id="chatgpt-external">浏览器打开 ↗</button></div><div id="chatgpt-stage"><div class="chatgpt-placeholder"><h2>与你的 ChatGPT 对话</h2><p>${placeholder}</p><p>聊完后可复制分享链接，到知识库取回归档。</p></div><div id="chatgpt-overlay" class="chatgpt-overlay" hidden><h2>ChatGPT 没有显示出来</h2><p id="chatgpt-overlay-text" role="status"></p><div class="chatgpt-overlay-actions"><button type="button" id="chatgpt-overlay-retry">重新载入</button><button type="button" id="chatgpt-overlay-external">浏览器打开 ↗</button></div></div></div>`;
  root.append(pane);
  tabs.addEventListener("click", (event) => { const button = event.target.closest("[data-agent-provider]"); if (button) { setAgentProvider(button.dataset.agentProvider); loadAgentPane(); } });
  document.querySelector("#chatgpt-reload").onclick = () => hostMessage({ action: "reload" });
  document.querySelector("#chatgpt-external").onclick = openExternal;
  document.querySelector("#chatgpt-overlay-retry").onclick = () => hostMessage({ action: "reload" });
  document.querySelector("#chatgpt-overlay-external").onclick = openExternal;
  document.querySelector("#chatgpt-reload").hidden = !isObsidianHost();
  // 宿主报回真实状态：blocked 时用卡面把「为什么没出来」写在内容区，
  // 不能让一片空白顶替掉提示。
  window.addEventListener("message", (event) => {
    if (!isObsidianHost() || event.source !== window.parent) return;
    const data = event.data || {};
    if (data.type !== "torch:chatgpt-state" && data.type !== "torch:chatgpt-status") return;
    document.querySelector("#chatgpt-status").textContent = String(data.message || "");
    if (data.type !== "torch:chatgpt-state") return;
    hostAnswered = true;
    const overlay = document.querySelector("#chatgpt-overlay");
    if (!overlay) return;
    if (data.state === "blocked") {
      document.querySelector("#chatgpt-overlay-text").textContent = String(data.message || "ChatGPT 没有载入成功。");
      overlay.hidden = false;
    } else {
      overlay.hidden = true;
    }
  });
  new ResizeObserver(scheduleBounds).observe(document.querySelector("#chatgpt-stage"));
  new MutationObserver(() => { if (root.hidden) suspendDshPane(); scheduleBounds(); }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["hidden", "open"] });
  window.addEventListener("resize", scheduleBounds);
  document.addEventListener("scroll", scheduleBounds, true);
  document.addEventListener("visibilitychange", scheduleBounds);
}
export function loadAgentPane() {
  initAgentPane();
  document.querySelector("#dsh-shell").hidden = provider !== "dsh";
  document.querySelector("#chatgpt-pane").hidden = provider !== "chatgpt";
  document.querySelectorAll("[data-agent-provider]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.agentProvider === provider)));
  if (provider === "dsh") { initDshPane(); loadDshPane(); }
  else suspendDshPane();
  // 开机就从 URL 带上偏好时也要告诉宿主，否则它存的还是旧值，下次 reload 会翻回去。
  hostMessage({ action: "provider", value: provider });
  retries = 0;
  lastKey = "";
  scheduleBounds();
  if (provider === "chatgpt") watchHost();
  else { window.clearInterval(pollTimer); pollTimer = 0; }
}
