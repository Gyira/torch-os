import { initDshPane, loadDshPane, suspendDshPane } from "./dsh-pane.js";
import { isObsidianHost } from "./task-open.js";

let provider = "dsh", initialized = false, frame = 0;
try { provider = localStorage.getItem("torch-os:agent-provider") === "chatgpt" ? "chatgpt" : "dsh"; } catch {}
function hostMessage(message) {
  if (isObsidianHost()) window.parent.postMessage({ type: "torch:chatgpt", ...message }, "*");
}
function scheduleBounds() {
  cancelAnimationFrame(frame);
  frame = requestAnimationFrame(() => {
    const stage = document.querySelector("#chatgpt-stage");
    const rect = stage?.getBoundingClientRect();
    const visible = provider === "chatgpt" && rect?.width > 0 && rect?.height > 0
      && !document.hidden && !document.querySelector("dialog[open], .panel-nav-menu:not([hidden])");
    hostMessage({ action: "bounds", visible: Boolean(visible), rect: visible ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : null });
  });
}
export function setAgentProvider(value) {
  provider = value === "chatgpt" ? "chatgpt" : "dsh";
  try { localStorage.setItem("torch-os:agent-provider", provider); } catch {}
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
  pane.innerHTML = `<div class="chatgpt-toolbar"><strong>ChatGPT</strong><span id="chatgpt-status" role="status"></span><button type="button" id="chatgpt-reload">重新载入</button><button type="button" id="chatgpt-external">浏览器打开 ↗</button></div><div id="chatgpt-stage"><div class="chatgpt-placeholder"><h2>与你的 ChatGPT 对话</h2><p>${isObsidianHost() ? "正在连接 Obsidian 内嵌浏览器。首次使用请在网页中登录；若一直未加载，请重载更新后的桌面 Torch 插件。" : "浏览器版请点击上方“浏览器打开”。在桌面 Obsidian 中可使用内嵌对话。"}</p><p>聊完后可复制分享链接，到知识库取回归档。</p></div></div>`;
  root.append(pane);
  tabs.addEventListener("click", (event) => { const button = event.target.closest("[data-agent-provider]"); if (button) { setAgentProvider(button.dataset.agentProvider); loadAgentPane(); } });
  document.querySelector("#chatgpt-reload").onclick = () => hostMessage({ action: "reload" });
  document.querySelector("#chatgpt-external").onclick = () => {
    if (isObsidianHost()) window.parent.postMessage({ type: "torch:open-url", url: "https://chatgpt.com/" }, "*");
    else window.open("https://chatgpt.com/", "_blank", "noopener,noreferrer");
  };
  document.querySelector("#chatgpt-reload").hidden = !isObsidianHost();
  window.addEventListener("message", (event) => {
    if (!isObsidianHost() || event.source !== window.parent || event.data?.type !== "torch:chatgpt-status") return;
    document.querySelector("#chatgpt-status").textContent = String(event.data.message || "");
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
  scheduleBounds();
}
