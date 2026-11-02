// 全局搜索：查询版本同时约束显示结果和回车操作，旧响应不能恢复已清空的查询。
import { api, escapeHtml, formatRelativeDate } from "./core.js";
import { openNote } from "./pillars.js";

const PILLAR_LABELS = { life: "人生路", self: "自我档案", ops: "生活运营", knowledge: "知识库" };
let searchId = 0;
let closeActiveSearch = null;

export function openGlobalSearch() {
  closeActiveSearch?.();
  const trigger = document.activeElement;
  const id = `global-search-${++searchId}`;
  const dialog = document.createElement("dialog");
  dialog.className = "pillar-dialog pillar-search-dialog";
  dialog.setAttribute("aria-labelledby", `${id}-title`);
  dialog.innerHTML = `
    <div class="pillar-dialog-head">
      <div><p class="eyebrow">全局搜索</p><h2 id="${id}-title">在四库里找</h2></div>
      <button type="button" class="text-button" data-search-close>关闭</button>
    </div>
    <div class="pillar-global-search">
      <label class="visually-hidden" for="${id}-query">搜索标题、正文或标签</label>
      <input id="${id}-query" type="search" role="combobox" aria-autocomplete="list"
        aria-expanded="false" aria-controls="${id}-results" aria-describedby="${id}-hint"
        data-global-query placeholder="输入标题、正文或标签" autocomplete="off" autofocus>
      <p id="${id}-hint" class="field-help">↑↓ 选择，回车打开，Esc 关闭。</p>
      <p class="status-message" data-global-status role="status" aria-atomic="true">输入关键词开始搜索。</p>
      <button type="button" class="text-button" data-search-retry hidden>重试搜索</button>
      <div id="${id}-results" class="pillar-list" data-global-results role="listbox" aria-label="搜索结果" hidden></div>
    </div>`;
  const input = dialog.querySelector("[data-global-query]");
  const status = dialog.querySelector("[data-global-status]");
  const results = dialog.querySelector("[data-global-results]");
  const retry = dialog.querySelector("[data-search-retry]");
  let timer = 0;
  let searchSeq = 0;
  let closed = false;
  let composing = false;
  let resultItems = [];
  let resultQuery = "";
  let activeIndex = -1;

  const clearResults = () => {
    resultItems = [];
    resultQuery = "";
    activeIndex = -1;
    results.replaceChildren();
    results.hidden = true;
    results.removeAttribute("aria-busy");
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    retry.hidden = true;
  };
  const close = () => {
    if (closed) return;
    closed = true;
    searchSeq += 1;
    window.clearTimeout(timer);
    if (dialog.open) dialog.close();
    dialog.remove();
    if (closeActiveSearch === close) closeActiveSearch = null;
    if (trigger?.isConnected) trigger.focus?.({ preventScroll: true });
  };
  closeActiveSearch = close;
  dialog.querySelector("[data-search-close]").addEventListener("click", close);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  dialog.addEventListener("close", close);

  const paintActive = () => {
    results.querySelectorAll("[data-global-note]").forEach((row, index) => {
      const active = index === activeIndex;
      row.classList.toggle("is-selected", active);
      row.setAttribute("aria-selected", String(active));
      if (active) {
        input.setAttribute("aria-activedescendant", row.id);
        row.scrollIntoView({ block: "nearest" });
      }
    });
  };
  const openRow = (index) => {
    const item = resultItems[index];
    if (!item || closed || composing || resultQuery !== input.value.trim()) return;
    close();
    openNote(item.path);
  };
  const search = async () => {
    window.clearTimeout(timer);
    const seq = ++searchSeq;
    clearResults();
    if (closed || composing) return;
    const query = input.value.trim();
    if (!query) { status.textContent = "输入关键词开始搜索。"; return; }
    status.textContent = "搜索中…";
    results.setAttribute("aria-busy", "true");
    const current = () => !closed && seq === searchSeq && query === input.value.trim();
    try {
      const payload = await api(`/api/search?q=${encodeURIComponent(query)}&limit=60`);
      if (!current()) return;
      resultItems = Array.isArray(payload.items) ? payload.items : [];
      resultQuery = query;
      status.textContent = resultItems.length ? `找到 ${resultItems.length} 条` : "没有找到匹配的笔记，试试更短的关键词。";
      results.innerHTML = resultItems.map((item, index) => `
        <button type="button" role="option" tabindex="-1" aria-selected="false" id="${id}-option-${index}"
          class="pillar-note-row" data-global-note="${index}">
          <span class="pillar-note-main">
            <span class="pillar-note-title">${escapeHtml(item.title)}</span>
            <span class="pillar-note-excerpt">${escapeHtml(item.excerpt || "")}</span>
          </span>
          <span class="pillar-note-meta">${escapeHtml(PILLAR_LABELS[item.pillar] || item.pillar)} · ${escapeHtml(formatRelativeDate(item.date))}</span>
        </button>`).join("");
      results.hidden = !resultItems.length;
      input.setAttribute("aria-expanded", String(Boolean(resultItems.length)));
    } catch (error) {
      if (!current()) return;
      status.textContent = error.message;
      retry.hidden = false;
    } finally {
      if (current()) results.removeAttribute("aria-busy");
    }
  };
  const scheduleSearch = () => {
    // 在防抖等待之前就失效旧请求，防止旧结果在这 220ms 内被 Enter 打开。
    searchSeq += 1;
    window.clearTimeout(timer);
    clearResults();
    status.textContent = input.value.trim() ? "等待输入完成…" : "输入关键词开始搜索。";
    if (!composing && input.value.trim()) timer = window.setTimeout(search, 220);
  };
  input.addEventListener("compositionstart", () => { composing = true; scheduleSearch(); });
  input.addEventListener("compositionend", () => { composing = false; scheduleSearch(); });
  input.addEventListener("input", scheduleSearch);
  input.addEventListener("keydown", (event) => {
    if (composing || event.isComposing || event.keyCode === 229) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!resultItems.length) return;
      event.preventDefault();
      activeIndex = event.key === "ArrowDown" ? (activeIndex + 1) % resultItems.length
        : (activeIndex <= 0 ? resultItems.length - 1 : activeIndex - 1);
      paintActive();
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (resultItems.length) openRow(Math.max(0, activeIndex));
      else search();
    }
  });
  retry.addEventListener("click", () => { input.focus(); search(); });
  results.addEventListener("click", (event) => {
    const row = event.target.closest?.("[data-global-note]");
    if (row) openRow(Number(row.dataset.globalNote));
  });
  document.body.append(dialog);
  dialog.showModal();
  input.focus();
}
