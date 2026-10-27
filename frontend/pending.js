// 待确认中枢（2026-10-27）：草稿 / 事实 / 分流 三处积压收成一个入口。
// 工作台顶部一个常驻数字，点开是带 tab 的弹层；每种类型保留自己的动作。
// 分工：草稿只标已读/丢弃，事实才写四库，分流只跳去知识库——不把三种决策混成一个按钮。
// 渲染在 pending-markup.js（纯函数、可在 node 里单测），这里只管取数和动作。
import { api, confirmAction, jsonBody, toast } from "./core.js";
import { pendingListMarkup, pendingStripMarkup, TABS } from "./pending-markup.js";

export { pendingItemMarkup, pendingListMarkup, pendingStripMarkup } from "./pending-markup.js";

async function refreshStrip() {
  const host = document.querySelector("[data-pending-strip]");
  if (!host) return null;
  try {
    const summary = await api("/api/pending/summary");
    host.innerHTML = pendingStripMarkup(summary);
    host.hidden = !Number(summary?.total);
    return summary;
  } catch {
    host.hidden = true;
    return null;
  }
}

export async function loadPendingStrip() {
  return refreshStrip();
}

export function openPendingHub() {
  const trigger = document.activeElement;
  const dialog = document.createElement("dialog");
  dialog.className = "pillar-dialog pending-dialog";
  dialog.innerHTML = `
    <div class="pillar-dialog-head">
      <div>
        <p class="eyebrow">待确认</p>
        <h2>一处看完全部待确认</h2>
        <p class="meta">草稿只做已读或丢弃；事实采纳后才写四库；分流去知识库处理。动作不会互相顶替。</p>
      </div>
      <button type="button" class="text-button" data-pending-close>关闭</button>
    </div>
    <div class="pending-tabs" role="tablist" aria-label="待确认类型"></div>
    <div class="pending-body" data-pending-body><p class="field-help">正在读取…</p></div>`;
  document.body.append(dialog);

  const tabsHost = dialog.querySelector(".pending-tabs");
  const body = dialog.querySelector("[data-pending-body]");
  let active = "draft";
  let counts = {};
  let chapters = [];

  const close = () => {
    dialog.close();
    dialog.remove();
    trigger?.focus?.({ preventScroll: true });
  };

  function renderTabs() {
    tabsHost.innerHTML = TABS.map((tab) => {
      const selected = tab.key === active;
      return `<button type="button" class="pending-tab${selected ? " is-active" : ""}" role="tab"
        aria-selected="${selected ? "true" : "false"}" data-pending-tab="${tab.key}">
        ${tab.label}<span>${Number(counts[tab.key]) || 0}</span></button>`;
    }).join("");
  }

  async function loadTab(kind) {
    active = kind;
    renderTabs();
    body.innerHTML = `<p class="field-help">正在读取…</p>`;
    try {
      const payload = await api(`/api/pending/items?kind=${encodeURIComponent(kind)}`);
      if (active !== kind) return;
      chapters = Array.isArray(payload.chapters) ? payload.chapters : chapters;
      body.innerHTML = pendingListMarkup(payload.items, kind, { chapters });
    } catch (error) {
      if (active === kind) body.innerHTML = `<p class="status-message error">${error.message}</p>`;
    }
  }

  async function reload() {
    counts = (await refreshStrip()) || {};
    await loadTab(active);
  }

  dialog.querySelector("[data-pending-close]")?.addEventListener("click", close);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });

  dialog.addEventListener("click", async (event) => {
    const tab = event.target.closest?.("[data-pending-tab]");
    if (tab) { await loadTab(tab.dataset.pendingTab); return; }

    const button = event.target.closest?.("[data-pending-action]");
    if (!button) return;
    const item = button.closest?.("[data-pending-item]");
    if (!item) return;
    const { pendingKind: kind, pendingId: id } = item.dataset;
    const action = button.dataset.pendingAction;

    if (kind === "triage") {
      const chapter = item.querySelector("[data-pending-chapter]")?.value || "";
      if (!chapter) {
        toast("先选一个要搬进去的章节。", "warning");
        return;
      }
      button.disabled = true;
      try {
        const result = await api("/api/pending/triage", jsonBody("POST", { path: id, chapter }));
        toast(`已搬到 ${result?.note?.path || "新章节"}。`, "success");
        await reload();
      } catch (error) {
        toast(error.message, "error");
        button.disabled = false;
      }
      return;
    }

    if (kind === "love") {
      const endpoint = `/api/love/memory/drafts/${encodeURIComponent(id)}`;
      button.disabled = true;
      try {
        if (action === "write") {
          await api(`${endpoint}/write`, jsonBody("POST", { overwrite: false }));
          toast("已写入四库。", "success");
        } else {
          const ok = await confirmAction({
            title: "丢弃这篇记忆草稿？",
            message: "只删除应用里的草稿，不删除四库中的笔记；原始聊天也不受影响。",
            confirmLabel: "丢弃草稿", cancelLabel: "保留", danger: true,
          });
          if (!ok) { button.disabled = false; return; }
          await api(endpoint, { method: "DELETE" });
          toast("草稿已丢弃。", "info");
        }
        await reload();
      } catch (error) {
        toast(error.message, "error");
        button.disabled = false;
      }
      return;
    }

    button.disabled = true;
    try {
      if (kind === "draft") {
        await api("/api/pending/drafts", jsonBody("POST", { id: Number(id), action }));
        toast(action === "read" ? "已标记已读。" : "已丢弃；草稿还在库里，没有删。", "success");
      } else {
        const result = await api("/api/pending/facts", jsonBody("POST", { id, action }));
        const target = result?.result?.field_label || result?.result?.title || "";
        toast(action === "accept" ? `已写进自我档案${target ? `：${target}` : ""}。` : "已拒绝，四库没有改动。", "success");
      }
      await reload();
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
    }
  });

  if (typeof dialog.showModal === "function") dialog.showModal();
  (async () => {
    counts = (await refreshStrip()) || {};
    // 直接落在第一个有内容的房间：四处都空时退回草稿。
    const first = TABS.find((tab) => Number(counts[tab.key]) > 0)?.key || "draft";
    await loadTab(first);
  })();
  return dialog;
}

export function initPending() {
  document.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-pending-open]")) openPendingHub();
  });
}
