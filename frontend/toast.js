// 单条可见提示 + 操作队列。保留现有 toast(message, tone, action) 调用方式。
let current = null;
const queue = [];
let timer = 0;
let initialized = false;
let toastRoot = null;

const node = () => toastRoot || (toastRoot = document.getElementById("toast"));

function placeToast() {
  const root = node();
  if (!root) return;
  const focusedDialog = document.activeElement?.closest?.("dialog[open]");
  const host = focusedDialog || [...document.querySelectorAll("dialog[open]")].at(-1) || document.body;
  if (root.parentElement !== host) host.append(root);
}

function pause() {
  window.clearTimeout(timer);
  timer = 0;
  if (current?.started) {
    current.remaining = Math.max(0, current.remaining - (Date.now() - current.started));
    current.started = 0;
  }
}

function resume() {
  const root = node();
  if (!current || current.persistent || current.busy || timer
      || root?.matches(":hover") || root?.contains(document.activeElement)) return;
  current.started = Date.now();
  timer = window.setTimeout(dismiss, current.remaining);
}

function dismiss() {
  const root = node();
  pause();
  if (root?.contains(document.activeElement) && current?.trigger?.isConnected) {
    current.trigger.focus?.({ preventScroll: true });
  }
  current = null;
  if (root) {
    root.classList.remove("show", "has-action");
    root.inert = true;
    root.replaceChildren();
    root.removeAttribute("aria-busy");
  }
  if (queue.length) show(queue.shift());
}

function show(entry) {
  const root = node();
  if (!root) return;
  current = entry;
  placeToast();
  root.inert = false;
  root.replaceChildren();
  const message = document.createElement("span");
  message.className = "toast-message";
  message.textContent = entry.message;
  root.append(message);
  if (entry.action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "toast-action";
    button.textContent = entry.action.label;
    button.addEventListener("click", async () => {
      if (entry.busy) return;
      entry.busy = true;
      pause();
      root.setAttribute("aria-busy", "true");
      button.setAttribute("aria-disabled", "true");
      button.textContent = "处理中…";
      root.querySelector(".toast-dismiss")?.setAttribute("aria-disabled", "true");
      try {
        await entry.action.onAction();
      } catch (error) {
        queue.unshift(makeEntry(`${entry.action.errorMessage || "操作失败"}：${error.message}`, "error"));
      } finally {
        if (current === entry) dismiss();
      }
    });
    root.append(button);
  }
  const close = document.createElement("button");
  close.type = "button";
  close.className = "toast-dismiss";
  close.textContent = "×";
  close.setAttribute("aria-label", "关闭提示");
  close.addEventListener("click", () => { if (!entry.busy) dismiss(); });
  root.append(close);
  root.dataset.tone = entry.tone;
  root.classList.toggle("has-action", Boolean(entry.action));
  root.classList.add("show");
  resume();
}

function makeEntry(message, tone = "info", action = null) {
  const validAction = action?.label && typeof action.onAction === "function" ? action : null;
  const timeout = Number(action?.timeout);
  return {
    message: String(message), tone, action: validAction,
    trigger: document.activeElement,
    // 错误与默认操作提示不倒计时；调用方显式设定的撤销时限至少为 10 秒。
    persistent: tone === "error" || Boolean(validAction && !(timeout > 0)),
    remaining: timeout > 0 ? Math.max(validAction ? 10000 : 5000, timeout) : 5000,
    started: 0, busy: false,
  };
}

export function toast(message, tone = "info", action = null) {
  const root = node();
  if (!root) return;
  if (!initialized) {
    initialized = true;
    root.setAttribute("aria-atomic", "true");
    root.addEventListener("mouseenter", pause);
    root.addEventListener("mouseleave", resume);
    root.addEventListener("focusin", pause);
    root.addEventListener("focusout", () => queueMicrotask(resume));
    // 原生模态框位于 top layer，提示必须跟随进入该层，不能只提高 z-index。
    new MutationObserver(() => { if (current) placeToast(); }).observe(document.body,
      { subtree: true, attributes: true, attributeFilter: ["open"] });
    document.addEventListener("close", () => { if (current) placeToast(); }, true);
  }
  const entry = makeEntry(message, tone, action);
  if (!entry.action && [current, ...queue].some((item) => item && !item.action
      && item.message === entry.message && item.tone === entry.tone)) return;
  if (!current) { show(entry); return; }
  if (!entry.action && tone !== "error" && !current.action && !current.persistent
      && !root.contains(document.activeElement) && !root.matches(":hover")) {
    pause();
    show(entry);
    return;
  }
  // 普通消息只合并队列尾部的普通消息；错误和撤销操作绝不被覆盖。
  const last = queue.at(-1);
  if (!entry.action && tone !== "error" && last && !last.action && !last.persistent) queue.pop();
  if (tone === "error") queue.unshift(entry);
  else queue.push(entry);
}
