// 「与神对话」= 完整的 DeepSeek Harness Web UI（/harness/ 同源代理）。
// 这里只负责启动状态、iframe 生命周期和工具栏；聊天本身完全由 dsh 提供。
import { $, api } from "./core.js";

const state = {
  inited: false,
  active: false,
  timer: 0,
  generation: -1,
  loaded: false,
  starting: false,
};

function node(id) {
  return document.getElementById(id);
}

function setStatus(text, tone = "") {
  const label = node("dsh-status-text");
  if (label) label.textContent = text;
  const bar = node("dsh-statusbar");
  if (bar) bar.dataset.tone = tone;
  const dot = bar?.querySelector?.(".dsh-dot");
  if (dot) dot.className = `dsh-dot${tone ? ` ${tone}` : ""}`;
}

function showOverlay(title, text, { command = "", retry = true, link = true } = {}) {
  const overlay = node("dsh-overlay");
  if (!overlay) return;
  overlay.hidden = false;
  const heading = node("dsh-overlay-title");
  const copy = node("dsh-overlay-text");
  if (heading) heading.textContent = title;
  if (copy) copy.textContent = text;
  const pre = node("dsh-overlay-command");
  if (pre) {
    pre.hidden = !command;
    const code = pre.querySelector?.("code");
    if (code) code.textContent = command;
  }
  const retryButton = node("dsh-retry");
  if (retryButton) retryButton.hidden = !retry;
  const linkNode = overlay.querySelector?.(".dsh-overlay-actions a");
  if (linkNode) linkNode.hidden = !link;
}

function hideOverlay() {
  const overlay = node("dsh-overlay");
  if (overlay) overlay.hidden = true;
}

function statusText(status) {
  switch (status.state) {
    case "disabled": return "Harness 已禁用";
    case "missing": return "未找到 dsh 或 npx";
    case "ready": return status.adopted ? "已接入正在运行的 Harness" : "DeepSeek Harness 已连接";
    case "starting": return "正在启动 DeepSeek Harness…";
    case "error": return "dsh 启动失败";
    default: return "dsh 未启动";
  }
}

function fitFrame() {
  const shell = node("dsh-shell");
  if (!shell || !state.active) return;
  // 与神对话现在在待办事项下方：高度取“视口减去吸顶导航和页面底部留白”，
  // 滚到聊天区时正好铺满剩余屏幕，二级菜单的滚动定位也能对齐到顶部。
  const viewport = Number(window.innerHeight) || 900;
  shell.style.height = `${Math.max(420, Math.floor(viewport - 160))}px`;
}

function schedulePoll(delay) {
  if (state.timer) window.clearTimeout(state.timer);
  state.timer = window.setTimeout(() => {
    state.timer = 0;
    if (!state.active || document.hidden) return;
    refreshStatus({ quiet: true });
  }, delay);
}

function loadFrame() {
  const frame = node("dsh-frame");
  if (!frame) return;
  frame.src = "/harness/";
  state.loaded = true;
}

function render(status) {
  const current = status?.state || "stopped";
  setStatus(statusText(status), current === "ready" ? "ready" : current === "error" || current === "missing" ? "error" : "");
  if (current === "ready") {
    hideOverlay();
    const generation = Number(status.generation) || 0;
    if (!state.loaded || (state.generation !== -1 && generation !== state.generation)) {
      state.generation = generation;
      loadFrame();
    } else {
      state.generation = generation;
    }
    fitFrame();
    schedulePoll(8000);
    return;
  }
  state.loaded = false;
  if (current === "starting") {
    showOverlay("正在启动 dsh…",
      "首次使用 npx 时可能需要下载 DeepSeek Harness，请稍候。启动完成后这里会自动打开。",
      { retry: false });
    schedulePoll(1500);
    return;
  }
  if (current === "missing") {
    showOverlay("没有找到 dsh 或 npx",
      "请先安装 Node.js >= 24.9，然后安装 DeepSeek Harness。装好后点「重新检测」。",
      { command: "npm install -g @deepseek-ai/dsh" });
    schedulePoll(15000);
    return;
  }
  if (current === "error") {
    showOverlay("DeepSeek Harness 没有启动",
      status.error || "请检查 Node.js 与 dsh 安装，或查看 dsh-web.log。",
      { command: status.install_hint || "npm install -g @deepseek-ai/dsh" });
    schedulePoll(15000);
    return;
  }
  if (current === "disabled") {
    showOverlay("Harness 已禁用", "当前运行模式不允许启动 dsh。", { retry: false, link: false });
    return;
  }
  // stopped / unknown：交给 loadDshPane 自动启动一次。
  showOverlay("dsh 未启动", "正在请求启动 DeepSeek Harness…", { retry: false });
  schedulePoll(1500);
}

async function startHarness() {
  if (state.starting) return;
  state.starting = true;
  try {
    const status = await api("/api/dsh/start", { method: "POST" });
    render(status);
  } catch (error) {
    setStatus(`启动失败：${error.message}`, "error");
    showOverlay("DeepSeek Harness 没有启动", error.message || "请稍后重试。");
  } finally {
    state.starting = false;
  }
}

export async function refreshStatus({ quiet = false } = {}) {
  try {
    const status = await api("/api/dsh/status");
    render(status);
    return status;
  } catch (error) {
    if (!quiet) {
      setStatus(`状态读取失败：${error.message}`, "error");
      showOverlay("无法读取 Harness 状态", error.message || "请确认本地服务正在运行。");
    }
    schedulePoll(5000);
    return null;
  }
}

export function initDshPane() {
  if (state.inited) return;
  state.inited = true;
  const retry = node("dsh-retry");
  if (retry) retry.addEventListener("click", () => startHarness());
  const restart = node("dsh-restart");
  if (restart) restart.addEventListener("click", async () => {
    try {
      const status = await api("/api/dsh/restart", { method: "POST" });
      state.loaded = false;
      state.generation = -1;
      const frame = node("dsh-frame");
      if (frame) frame.src = "about:blank";
      render(status);
    } catch (error) {
      // 409：另一个 torch 入口还在用这个 dsh；保留当前画面，只提示原因。
      setStatus(`重启失败：${error.message}`, "error");
    }
  });
  const reload = node("dsh-reload");
  if (reload) reload.addEventListener("click", () => {
    const frame = node("dsh-frame");
    if (frame) frame.src = `/harness/?reload=${Date.now()}`;
  });
  const external = node("dsh-open-external");
  if (external) external.addEventListener("click", () => {
    window.open("/harness/", "_blank", "noopener");
  });
  window.addEventListener("resize", fitFrame);
  document.addEventListener("visibilitychange", () => {
    if (state.active && !document.hidden) refreshStatus({ quiet: true });
  });
}

export async function loadDshPane() {
  initDshPane();
  state.active = true;
  fitFrame();
  const status = await refreshStatus();
  if (status && status.state === "stopped") await startHarness();
}

export function isDshPaneActive() {
  return state.active;
}
