// 忆灵（Memory Hub）页面（2026-10-29，v1.0 P3）。
// 只读展示画像/观察/卡片；「现在整理」调用 dsh 生成待审卡与身份补丁；
// 身份补丁的采纳走通用 proposal 卡片，后端 revision + 备份保护。
import { $, api, confirmAction, escapeHtml, toast } from "./core.js";
import { renderMarkdownDocument } from "./pillar-markdown.js";
import { proposalsMarkup } from "./proposals-model.js";
import { openRoleDrawer } from "./roles.js";

const state = { overview: null, busy: false };
let initialized = false;

function el(id) { return document.getElementById(id); }

export function initMemoryHub() {
  if (initialized) return;
  initialized = true;
  el("self-hub")?.addEventListener("click", (event) => {
    const synthesize = event.target.closest("[data-memory-synthesize]");
    if (synthesize) { runSynthesis(synthesize); return; }
    const tell = event.target.closest("[data-memory-tell]");
    if (tell) { openEventDialog(); return; }
    const socrates = event.target.closest("[data-memory-socrates]");
    if (socrates) { openSocrates(); return; }
    const keep = event.target.closest("[data-memory-card-keep]");
    if (keep) { keepCard(keep); return; }
    const dismiss = event.target.closest("[data-memory-card-dismiss]");
    if (dismiss) { dismissCard(dismiss); return; }
    const refresh = event.target.closest("[data-memory-refresh]");
    if (refresh) { loadSelfHub(); return; }
  });
  el("memory-event-close")?.addEventListener("click", closeEventDialog);
  el("memory-event-save")?.addEventListener("click", saveEvent);
  el("memory-event-dialog")?.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeEventDialog();
  });
  document.addEventListener("torch:proposals-changed", () => loadSelfHub());
  document.addEventListener("torch:notes-changed", () => {
    if (document.body?.classList?.contains("view-self")) loadSelfHub();
  });
}

export async function loadSelfHub() {
  const host = el("self-hub");
  if (!host) return;
  host.innerHTML = `<div class="self-hub-loading">忆灵正在读取画像…</div>`;
  try {
    const overview = await api("/api/memory-hub/overview", { timeoutMs: 60000 });
    state.overview = overview;
    renderSelfHub(overview);
  } catch (error) {
    host.innerHTML = `<div class="self-hub-error"><p>忆灵暂时读不到画像：${escapeHtml(error.message)}</p><button type="button" class="text-button" data-memory-refresh>重试</button></div>`;
  }
}

function renderSelfHub(overview) {
  const host = el("self-hub");
  if (!host) return;
  const portrait = overview?.portrait || {};
  const stats = overview?.stats || {};
  const cards = Array.isArray(overview?.cards) ? overview.cards : [];
  const patches = Array.isArray(overview?.identity_patches) ? overview.identity_patches : [];
  const observations = Array.isArray(overview?.observations) ? overview.observations : [];
  const drafts = Array.isArray(overview?.identity_drafts) ? overview.identity_drafts : [];
  const lastRun = stats.last_run;
  const portraitHtml = portrait.portrait
    ? renderMarkdownDocument(String(portrait.portrait)).html
    : `<p class="field-help">还没有画像。点「现在整理」，让忆灵读一遍最近的日记、任务和已确认记忆。</p>`;
  host.innerHTML = `
    <header class="self-hub-head">
      <div>
        <p class="eyebrow">忆灵 · MEMORY HUB</p>
        <h2>我现在看到你</h2>
        <p class="field-help">只读画像与观察；身份修改先成为待审补丁，采纳才写入。</p>
      </div>
      <div class="self-hub-actions">
        <button type="button" class="button primary" data-memory-synthesize>现在整理</button>
        <button type="button" class="button secondary" data-memory-tell>告诉忆灵一件事</button>
        <button type="button" class="button secondary" data-memory-socrates>与苏格拉底聊聊</button>
        <button type="button" class="text-button" data-memory-refresh>刷新</button>
      </div>
    </header>
    <div class="self-hub-grid">
      <section class="self-hub-card self-hub-portrait">
        <div class="self-hub-card-head"><p class="eyebrow">画像</p><span class="meta">${escapeHtml(portrait.updated_at || "还没有更新")}</span></div>
        <div class="md-body">${portraitHtml}</div>
      </section>
      <aside class="self-hub-side">
        <section class="self-hub-card">
          <p class="eyebrow">状态</p>
          <dl class="self-hub-stats">
            <div><dt>待审卡</dt><dd>${cards.length}</dd></div>
            <div><dt>身份补丁</dt><dd>${patches.length}</dd></div>
            <div><dt>新事件</dt><dd>${Number(stats.pending_events) || 0}</dd></div>
            <div><dt>上次整理</dt><dd>${lastRun ? escapeHtml(String(lastRun.status || "")) : "还没"}</dd></div>
          </dl>
        </section>
        ${patches.length ? `<section class="self-hub-card">
          <p class="eyebrow">待确认的身份修改</p>
          ${proposalsMarkup(patches)}
        </section>` : ""}
        <section class="self-hub-card">
          <p class="eyebrow">最近的观察</p>
          ${observations.length ? `<ul class="self-hub-observations">${observations.slice(0, 8).map((item) =>
            `<li><span>${escapeHtml(item.title || "")}</span><span class="meta">${escapeHtml(item.date || "")}</span></li>`).join("")}</ul>`
            : `<p class="field-help">还没有观察快照。</p>`}
        </section>
        ${drafts.length ? `<section class="self-hub-card">
          <p class="eyebrow">dsh 身份候选（待复核）</p>
          <ul class="self-hub-observations">${drafts.slice(0, 6).map((item) =>
            `<li><span>${escapeHtml(item.text || "")}</span><span class="meta">${escapeHtml(item.field_label || item.kind || "")}</span></li>`).join("")}</ul>
          <p class="field-help">点“现在整理”时忆灵会复核这些候选；不会直接改身份。</p>
        </section>` : ""}
      </aside>
    </div>
    ${cards.length ? `<section class="self-hub-card self-hub-cards">
      <div class="self-hub-card-head"><p class="eyebrow">复盘卡</p><span class="meta">留下 = 写进忆灵观察；忽略 = 归档</span></div>
      ${cards.map(cardMarkup).join("")}
    </section>` : ""}`;
  host.hidden = false;
}

function cardMarkup(card) {
  const sources = Array.isArray(card.sources) && card.sources.length
    ? `<p class="field-help">来源：${card.sources.map((source) => escapeHtml(source)).join("、")}</p>`
    : "";
  return `<article class="memory-card" data-card-id="${escapeHtml(card.id)}">
    <div class="memory-card-head"><div><span class="eyebrow">${escapeHtml(card.kind || "观察")}</span>
      <h4>${escapeHtml(card.title || "忆灵观察")}</h4></div>
      <span class="meta">${escapeHtml(String(card.confidence || ""))}</span></div>
    <div class="md-body">${renderMarkdownDocument(String(card.text || "")).html}</div>
    ${sources}
    <div class="memory-card-actions">
      <button type="button" class="small-button" data-memory-card-keep="${escapeHtml(card.id)}">留下</button>
      <button type="button" class="text-button" data-memory-card-dismiss="${escapeHtml(card.id)}">忽略</button>
    </div>
  </article>`;
}

async function runSynthesis(button) {
  if (state.busy) return;
  const ok = await confirmAction({
    title: "让忆灵整理现在的画像？",
    message: "会读取最近记录并调用 dsh（可能耗时 1–2 分钟，消耗你的 DeepSeek token）。完成后只生成待审卡片/补丁，不会自动改身份。",
    confirmLabel: "开始整理",
  });
  if (!ok) return;
  state.busy = true;
  button.disabled = true;
  const status = document.createElement("span");
  status.className = "status-message";
  status.textContent = "忆灵正在整理…";
  button.insertAdjacentElement("afterend", status);
  try {
    const result = await api("/api/memory-hub/synthesize", {
      method: "POST",
      body: JSON.stringify({ confirm: true, range_days: 14 }),
      timeoutMs: 240000,
    });
    toast(`整理完成：${result.cards?.length || 0} 张卡、${result.identity_patches?.length || 0} 个身份补丁。`, "success");
    await loadSelfHub();
  } catch (error) {
    toast(`整理失败：${error.message}`, "error");
    status.textContent = `整理失败：${error.message}`;
    status.classList.add("error");
  } finally {
    state.busy = false;
    button.disabled = false;
  }
}

function openSocrates() {
  openRoleDrawer({
    roleId: "yiling", mode: "converse", entry: "self.socrates",
    title: "苏格拉底", selectors: [{ entry: "identity" }],
    prompt: "我想聊聊最近的状态；如果你觉得画像不对，请追问我，直到你被说服。",
  });
}

function openEventDialog() {
  const dialog = el("memory-event-dialog");
  if (!dialog) return;
  el("memory-event-text").value = "";
  el("memory-event-source").value = "";
  el("memory-event-status").textContent = "";
  try { dialog.showModal(); } catch { dialog.setAttribute("open", ""); }
  el("memory-event-text")?.focus?.();
}

function closeEventDialog() {
  const dialog = el("memory-event-dialog");
  if (dialog?.open) dialog.close();
  else dialog?.removeAttribute("open");
}

async function saveEvent() {
  const summary = String(el("memory-event-text")?.value || "").trim();
  if (!summary) {
    el("memory-event-status").textContent = "写一句再投递。";
    return;
  }
  const button = el("memory-event-save");
  button.disabled = true;
  el("memory-event-status").textContent = "投递中…";
  try {
    await api("/api/memory-hub/events", {
      method: "POST",
      body: JSON.stringify({
        confirm: true, kind: el("memory-event-kind")?.value || "observation",
        summary, source_ref: String(el("memory-event-source")?.value || "").trim(),
        source_kind: "manual",
      }),
      timeoutMs: 60000,
    });
    toast("已投递给忆灵；下次整理会看到。", "success");
    closeEventDialog();
    await loadSelfHub();
  } catch (error) {
    el("memory-event-status").textContent = `投递失败：${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function keepCard(button) {
  const cardId = button.dataset.memoryCardKeep;
  button.disabled = true;
  try {
    await api(`/api/memory-hub/cards/${encodeURIComponent(cardId)}/keep`, {
      method: "POST",
      body: JSON.stringify({ confirm: true, target: "observation" }),
      timeoutMs: 60000,
    });
    toast("已留下；写进自我档案的忆灵观察。", "success");
    await loadSelfHub();
  } catch (error) {
    toast(`留下失败：${error.message}`, "error");
    button.disabled = false;
  }
}

async function dismissCard(button) {
  const cardId = button.dataset.memoryCardDismiss;
  button.disabled = true;
  try {
    await api(`/api/memory-hub/cards/${encodeURIComponent(cardId)}/dismiss`, {
      method: "POST", body: JSON.stringify({ reason: "" }), timeoutMs: 60000,
    });
    toast("已忽略这张卡。", "info");
    await loadSelfHub();
  } catch (error) {
    toast(`忽略失败：${error.message}`, "error");
    button.disabled = false;
  }
}
