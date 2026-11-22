// 本地界面层的服务状态桥。
//
// Obsidian 插件用 Node 提供界面，并把 /api/* 代理到 Python；服务没起来时，
// 页面仍应立即可用。这里负责：
//   - 轮询 /__torch/service，把“启动中/失败/未启动”做成紧凑状态条；
//   - 让 api() 在 503 时等待服务就绪并只重试一次；
//   - 服务就绪时通知页面刷新当前数据；
//   - 记录“正在显示上次缓存”，让用户知道数据不是最新的。
//
// 浏览器直接由 Python 服务打开时没有 /__torch/*，这里会进入 direct 模式并完全 no-op。

const HOST_MODE = (() => {
  try {
    return new URLSearchParams(globalThis.location?.search || "").get("host") === "obsidian";
  } catch {
    return false;
  }
})();

let mode = HOST_MODE ? "unknown" : "direct";
let state = { phase: "idle", failure: null, autoStart: true };
let ui = {};
let capabilities = {};
let actionToken = "";
let autoStart = true;
let initialized = false;
let pollTimer = 0;
let pollDelay = 0;
let refreshing = false;
let readyDispatched = false;
let staleSeen = false;
let staleAt = "";
let unavailableSeen = false;
let lastActionError = "";
let migrating = false;
let migrationBusy = false;
let migrationTimer = 0;
const waiters = new Set();

function nowMs() { return Date.now(); }

function setTimer(fn, delay) {
  const timer = window.setTimeout(fn, delay);
  // 浏览器没有 unref；Node 测试里这样不会让进程挂住。
  if (timer && typeof timer.unref === "function") timer.unref();
  return timer;
}

function dispatch(name, detail = {}) {
  try {
    document.dispatchEvent(new CustomEvent(name, { detail }));
  } catch { /* no DOM (tests) */ }
}

function bannerNodes() {
  const banner = document.getElementById("service-banner");
  return {
    banner,
    text: document.getElementById("service-banner-text"),
    actions: document.getElementById("service-banner-actions"),
  };
}

function button(label, action, title = "") {
  const element = document.createElement("button");
  element.type = "button";
  element.className = "service-banner-action";
  element.textContent = label;
  element.dataset.serviceAction = action;
  if (title) element.title = title;
  return element;
}

function setServiceDot(phase) {
  const dot = document.getElementById("service-dot");
  const label = document.getElementById("service-label");
  if (!dot || !label) return;
  dot.classList.toggle("connected", phase === "ready");
  if (phase === "ready") label.textContent = "本地服务已连接";
  else if (phase === "starting") label.textContent = "本地服务启动中…";
  else if (phase === "failed") label.textContent = "本地服务未连接";
}

function elapsedSeconds() {
  const started = Date.parse(state.startedAt || "");
  if (!Number.isFinite(started)) return 0;
  return Math.max(0, Math.round((nowMs() - started) / 1000));
}

function staleLabel() {
  const date = new Date(staleAt || "");
  if (Number.isNaN(date.getTime())) return "显示上次缓存";
  return `显示上次缓存（${new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(date)}）`;
}

function renderBanner() {
  if (mode === "direct") return;
  const { banner, text, actions } = bannerNodes();
  if (!banner || !text || !actions) return;
  const phase = state.phase;
  const failure = state.failure || null;
  const show = migrationBusy || migrating || (staleSeen && phase !== "ready")
    || phase === "starting" || phase === "failed"
    || (phase === "idle" && autoStart === false);
  banner.hidden = !show;
  if (!show) {
    actions.replaceChildren();
    return;
  }
  let message = "";
  let tone = "warn";
  if (migrationBusy) {
    message = "正在预览旧版目录…";
  } else if (migrating) {
    message = "正在整理旧版目录…";
  } else if (phase === "starting") {
    const seconds = elapsedSeconds();
    message = `本地服务启动中…${seconds >= 3 ? ` ${seconds}s` : ""}`;
    if (staleSeen) message = `${staleLabel()} · ${message}`;
    if (seconds >= 6) tone = "warn";
  } else if (phase === "failed") {
    tone = "error";
    message = failure?.message || "本地服务启动失败。";
    if (staleSeen) message = `${staleLabel()} · ${message}`;
    if (lastActionError) message = `${message} ${lastActionError}`;
  } else if (phase === "idle") {
    message = staleSeen ? staleLabel() : "本地服务未启动。";
  }
  banner.dataset.tone = tone;
  text.textContent = message;
  text.title = failure
    ? [failure.message, failure.hint].filter(Boolean).join(" ")
    : "";
  const next = [];
  if (migrationBusy || migrating) {
    // 迁移/预览进行中只显示文案，按钮交给状态轮询更新。
  } else if (phase === "failed") {
    next.push(button("重试", "retry"));
    if (failure?.canMigrate && capabilities.migrate) next.push(button("整理旧目录", "migrate"));
    next.push(button("查看日志", "logs"));
    next.push(button("设置", "settings"));
  } else if (phase === "idle") {
    next.push(button("启动服务", "start"));
    next.push(button("设置", "settings"));
  } else if (elapsedSeconds() >= 6) {
    next.push(button("查看日志", "logs"));
  }
  actions.replaceChildren(...next);
}

function settleWaiters(error) {
  for (const waiter of [...waiters]) {
    waiters.delete(waiter);
    clearTimeout(waiter.timer);
    waiter.signal?.removeEventListener?.("abort", waiter.onAbort);
    if (error) waiter.reject(error);
    else waiter.resolve(true);
  }
}

function waiterError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

function schedule(delay) {
  if (!HOST_MODE) return;
  if (pollTimer && pollDelay === delay) return;
  clearTimeout(pollTimer);
  pollDelay = delay;
  pollTimer = setTimer(() => {
    pollTimer = 0;
    pollDelay = 0;
    refresh().catch(() => schedule(2000));
  }, delay);
}

async function fetchServiceState({ timeoutMs = 1500 } = {}) {
  if (!HOST_MODE) { mode = "direct"; return null; }
  const controller = new AbortController();
  const timer = setTimer(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch("/__torch/service", {
      signal: controller.signal,
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (response.status === 404) { mode = "direct"; return null; }
    if (!response.ok) return null;
    const payload = await response.json();
    if (payload?.mode !== "plugin" || !payload.service) return null;
    mode = "plugin";
    actionToken = String(payload.actionToken || actionToken || "");
    autoStart = payload.autoStart !== false;
    ui = payload.ui || {};
    capabilities = payload.capabilities || {};
    return payload;
  } catch {
    return null;
  } finally {
    window.clearTimeout(timer);
  }
}

function applyState(payload) {
  const previous = state.phase;
  const next = payload.service || {};
  state = {
    phase: String(next.phase || "idle"),
    failure: next.failure || null,
    startedAt: next.startedAt || "",
    autoStart: payload.autoStart !== false,
  };
  autoStart = payload.autoStart !== false;
  if (state.phase === "ready") {
    lastActionError = "";
    staleSeen = false;
    setServiceDot("ready");
    if (!readyDispatched) {
      readyDispatched = true;
      dispatch("torch:service-ready", { previous });
    }
    settleWaiters(null);
    renderBanner();
    schedule(10000);
    return;
  }
  readyDispatched = false;
  if (state.phase === "starting") {
    setServiceDot("starting");
    renderBanner();
    schedule(400);
    return;
  }
  if (state.phase === "failed") {
    setServiceDot("failed");
    const failure = state.failure || {};
    const error = waiterError(failure.code || "service_failed",
      failure.message || "本地服务启动失败。");
    error.failure = failure;
    settleWaiters(error);
    renderBanner();
    schedule(5000);
    return;
  }
  setServiceDot("idle");
  renderBanner();
  schedule(autoStart === false ? 5000 : 500);
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const payload = await fetchServiceState();
    if (!payload) {
      if (mode === "direct") {
        renderBanner();
        settleWaiters(null);
        return;
      }
      schedule(2000);
      return;
    }
    applyState(payload);
  } finally {
    refreshing = false;
  }
}

export function isPluginMode() {
  return mode !== "direct";
}

export function servicePhase() {
  return state.phase;
}

export function noteServiceUnavailable() {
  unavailableSeen = true;
}

export function reportStaleRead(at = "") {
  staleSeen = true;
  staleAt = at || new Date().toISOString();
  if (mode === "plugin") renderBanner();
}

export function waitForService({ timeoutMs = 20000, signal } = {}) {
  if (!HOST_MODE || mode === "direct") return Promise.resolve(true);
  if (state.phase === "ready") return Promise.resolve(true);
  if (state.phase === "failed") {
    const failure = state.failure || {};
    return Promise.reject(waiterError(failure.code || "service_failed",
      failure.message || "本地服务启动失败。"));
  }
  if (state.phase === "idle" && autoStart === false) {
    return Promise.reject(waiterError("service_not_started", "本地服务未启动。"));
  }
  if (signal?.aborted) return Promise.reject(new DOMException("请求已取消", "AbortError"));
  return new Promise((resolve, reject) => {
    const waiter = { resolve, reject, signal, timer: 0, onAbort: null };
    waiter.timer = setTimer(() => {
      waiters.delete(waiter);
      signal?.removeEventListener?.("abort", waiter.onAbort);
      reject(waiterError("service_timeout", "本地服务启动超时。"));
    }, Math.max(1000, Number(timeoutMs) || 20000));
    waiter.onAbort = () => {
      waiters.delete(waiter);
      clearTimeout(waiter.timer);
      reject(new DOMException("请求已取消", "AbortError"));
    };
    signal?.addEventListener?.("abort", waiter.onAbort, { once: true });
    waiters.add(waiter);
    refresh().catch(() => {});
  });
}

async function postAction(path, body = null) {
  if (mode !== "plugin") throw new Error("当前不是插件模式");
  if (!actionToken) await fetchServiceState();
  if (!actionToken) throw new Error("还没有拿到本地操作令牌，请稍后重试。");
  const options = {
    method: "POST",
    cache: "no-store",
    headers: {
      "X-Torch-UI-Token": actionToken,
      Accept: "application/json",
    },
  };
  if (body !== null) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let payload = {};
  try { payload = await response.json(); } catch { /* ignore */ }
  if (!response.ok) {
    throw new Error(typeof payload?.detail === "string" ? payload.detail : `操作失败（${response.status}）`);
  }
  return payload;
}

async function startService() {
  lastActionError = "";
  try {
    await postAction("/__torch/start");
    state = { ...state, phase: "starting", failure: null, startedAt: new Date().toISOString() };
    renderBanner();
    schedule(400);
  } catch (error) {
    lastActionError = String(error?.message || error);
    renderBanner();
  }
}

async function restartService() {
  lastActionError = "";
  try {
    await postAction("/__torch/restart");
    staleSeen = false;
    state = { ...state, phase: "starting", failure: null, startedAt: new Date().toISOString() };
    renderBanner();
    schedule(400);
  } catch (error) {
    lastActionError = String(error?.message || error);
    renderBanner();
  }
}

async function showLogs() {
  try {
    const response = await fetch("/__torch/logs?max=16384", { cache: "no-store" });
    const data = await response.json();
    const { openDialog, escapeHtml } = await import("./core.js");
    openDialog("本地服务日志",
      `<p class="meta">${escapeHtml(data.path || "还没有失败日志")}</p>`
      + `<pre class="source-content">${escapeHtml(data.tail || "没有日志内容")}</pre>`);
  } catch (error) {
    lastActionError = `日志读取失败：${String(error?.message || error)}`;
    renderBanner();
  }
}

function pollMigration() {
  clearTimeout(migrationTimer);
  migrationTimer = setTimer(async () => {
    try {
      const response = await fetch("/__torch/migrate/status", { cache: "no-store" });
      const data = await response.json();
      const job = data?.job || {};
      if (job.state === "running") {
        renderBanner();
        pollMigration();
        return;
      }
      migrating = false;
      if (job.state === "done") {
        lastActionError = "";
        await postAction("/__torch/restart");
        state = { ...state, phase: "starting", failure: null, startedAt: new Date().toISOString() };
        renderBanner();
        schedule(400);
      } else {
        lastActionError = job.error ? `目录整理失败：${job.error}` : "目录整理失败。";
        state = { ...state, phase: "failed", failure: { code: "migrate_failed", message: lastActionError, canRetry: true } };
        renderBanner();
      }
    } catch (error) {
      migrating = false;
      lastActionError = `目录整理状态读取失败：${String(error?.message || error)}`;
      renderBanner();
    }
  }, 1500);
}

async function migrateVault() {
  if (migrating || migrationBusy) return;
  migrationBusy = true;
  lastActionError = "";
  renderBanner();
  try {
    const previewPayload = await postAction("/__torch/migrate/preview", {});
    const preview = previewPayload?.preview || {};
    const directories = Number(preview.directories) || 0;
    const files = Number(preview.files) || 0;
    const { confirmAction } = await import("./core.js");
    const confirmed = await confirmAction({
      title: "整理旧版目录？",
      message: `将重命名 ${directories} 个目录，并更新约 ${files} 个文件的引用。`
        + "脚本会先备份并生成可回滚清单。整理期间请不要在 Obsidian 里编辑这个库，"
        + "如果库在网盘同步目录里，建议先暂停同步。",
      confirmLabel: "开始整理",
      cancelLabel: "先不整理",
    });
    if (!confirmed) return;
    await postAction("/__torch/migrate/apply", {
      confirm: true, directories, files,
    });
    migrating = true;
    pollMigration();
  } catch (error) {
    lastActionError = String(error?.message || error);
    migrating = false;
  } finally {
    migrationBusy = false;
    renderBanner();
  }
}

function onBannerClick(event) {
  const target = event.target.closest?.("[data-service-action]");
  if (!target) return;
  const action = target.dataset.serviceAction;
  if (action === "retry") restartService();
  else if (action === "start") startService();
  else if (action === "logs") showLogs();
  else if (action === "migrate") migrateVault();
  else if (action === "settings") {
    try { window.parent?.postMessage({ type: "torch:open-plugin-settings" }, "*"); } catch { /* ignore */ }
  }
}

export function initServiceGate() {
  if (initialized) return;
  initialized = true;
  if (!HOST_MODE) { mode = "direct"; return; }
  const { banner } = bannerNodes();
  if (banner) banner.addEventListener("click", onBannerClick);
  refresh().catch(() => schedule(1000));
}
