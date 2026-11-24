import { isObsidianHost } from "./task-open.js";
import { visibleStageRect, observeStage } from "./embedded-stage.js";

let initialized = false, lastKey = "";
function hostMessage(message) {
  if (isObsidianHost()) window.parent.postMessage({ type: "torch:chatgpt", ...message }, "*");
}
function reportBounds() {
  const rect = visibleStageRect(document.querySelector("#chatgpt-stage"));
  const key = JSON.stringify(rect);
  if (key === lastKey) return;
  lastKey = key;
  hostMessage({ action: "bounds", visible: Boolean(rect), rect });
}
function openExternal() {
  if (isObsidianHost()) window.parent.postMessage({ type: "torch:open-url", url: "https://chatgpt.com/" }, "*");
  else window.open("https://chatgpt.com/", "_blank", "noopener,noreferrer");
}
export function initAgentPane() {
  if (initialized) return;
  initialized = true;
  const root = document.querySelector("#agent-view");
  const actions = document.createElement("div");
  actions.className = "chatgpt-nav-actions";
  actions.setAttribute("aria-label", "ChatGPT 操作");
  actions.innerHTML = '<button type="button" class="text-button" id="chatgpt-reload" aria-label="重新载入 ChatGPT" title="重新载入 ChatGPT">↻</button><button type="button" class="text-button" id="chatgpt-external" aria-label="在浏览器打开 ChatGPT" title="在浏览器打开 ChatGPT">↗</button>';
  const nav = document.querySelector(".panel-nav");
  if (nav) nav.insertBefore(actions, nav.querySelector(".panel-nav-help"));
  else root.prepend(actions);
  const hosted = isObsidianHost();
  const stage = document.createElement("div");
  stage.id = "chatgpt-stage";
  stage.innerHTML = '<p class="field-help" id="chatgpt-status" role="status"></p>';
  const status = stage.firstElementChild;
  status.textContent = hosted ? "正在连接 ChatGPT…" : "在浏览器中打开 ChatGPT 即可开始对话。";
  root.append(stage);
  document.querySelector("#chatgpt-reload").hidden = !hosted;
  document.querySelector("#chatgpt-reload").onclick = () => hostMessage({ action: "reload" });
  document.querySelector("#chatgpt-external").onclick = openExternal;
  window.addEventListener("message", event => {
    if (!hosted || event.source !== window.parent || event.data?.type !== "torch:chatgpt-state") return;
    status.textContent = event.data.state === "ready" ? "" : String(event.data.message || "");
  });
  observeStage(stage, reportBounds);
}
export function loadAgentPane() {
  initAgentPane();
  lastKey = "";
  reportBounds();
}
