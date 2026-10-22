// 全局搜索：跨四库检索标题/正文/标签，点结果直接打开详情。
import { api, escapeHtml, formatRelativeDate, toast } from "./core.js";
import { openNote } from "./pillars.js";

const PILLAR_LABELS = { life: "人生路", self: "自我档案", ops: "生活运营", knowledge: "知识库" };

export function initGlobalSearchShortcut() {
  document.addEventListener("keydown", (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
    if (String(event.key).toLowerCase() !== "k") return;
    event.preventDefault();
    if (document.querySelector("dialog[open]")) return;
    openGlobalSearch();
  });
}

export function openGlobalSearch() {
  const trigger = document.activeElement;
  const dialog = document.createElement("dialog");
  dialog.className = "pillar-dialog pillar-search-dialog";
  dialog.innerHTML = `
    <div class="pillar-dialog-head">
      <div><p class="eyebrow">全局搜索</p><h2>在四库里找</h2></div>
      <button type="button" class="text-button" data-search-close>关闭</button>
    </div>
    <div class="pillar-global-search">
      <input type="search" data-global-query placeholder="输入标题、正文或标签，↑↓ 选择，回车打开" autofocus>
      <p class="status-message" data-global-status role="status"></p>
      <div data-global-results></div>
    </div>`;
  document.body.append(dialog);
  const close = () => {
    dialog.close();
    dialog.remove();
    trigger?.focus?.({ preventScroll: true });
  };
  dialog.querySelector("[data-search-close]").addEventListener("click", close);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  const input = dialog.querySelector("[data-global-query]");
  const status = dialog.querySelector("[data-global-status]");
  const results = dialog.querySelector("[data-global-results]");
  let timer = 0;
  let searchSeq = 0;
  let resultItems = [];
  let activeIndex = -1;
  const paintActive = () => {
    results.querySelectorAll("[data-global-note]").forEach((row, index) => {
      const active = index === activeIndex;
      row.classList.toggle("is-selected", active);
      if (active) {
        row.setAttribute("aria-current", "true");
        row.scrollIntoView({ block: "nearest" });
      } else {
        row.removeAttribute("aria-current");
      }
    });
  };
  const openRow = (index) => {
    const item = resultItems[index];
    if (!item) return;
    close();
    openNote(item.path);
  };
  const search = async () => {
    const query = input.value.trim();
    if (!query) { results.innerHTML = ""; status.textContent = ""; resultItems = []; activeIndex = -1; return; }
    const seq = ++searchSeq;
    status.textContent = "搜索中…";
    try {
      const payload = await api(`/api/search?q=${encodeURIComponent(query)}&limit=60`);
      if (seq !== searchSeq) return;
      resultItems = Array.isArray(payload.items) ? payload.items : [];
      activeIndex = -1;
      status.textContent = resultItems.length ? `找到 ${resultItems.length} 条` : "没有找到匹配的笔记。";
      results.innerHTML = resultItems.length ? `<div class="pillar-list">${resultItems.map((item) => `
        <button type="button" class="pillar-note-row" data-global-note="${escapeHtml(item.path)}">
          <span class="pillar-note-main">
            <span class="pillar-note-title">${escapeHtml(item.title)}</span>
            <span class="pillar-note-excerpt">${escapeHtml(item.excerpt || "")}</span>
          </span>
          <span class="pillar-note-meta">${escapeHtml(PILLAR_LABELS[item.pillar] || item.pillar)} · ${escapeHtml(formatRelativeDate(item.date))}</span>
        </button>`).join("")}</div>` : "";
    } catch (error) {
      if (seq !== searchSeq) return;
      status.textContent = error.message;
      results.innerHTML = "";
      resultItems = [];
      activeIndex = -1;
    }
  };
  input.addEventListener("input", () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(search, 220);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!resultItems.length) return;
      event.preventDefault();
      activeIndex = event.key === "ArrowDown"
        ? (activeIndex + 1) % resultItems.length
        : (activeIndex <= 0 ? resultItems.length - 1 : activeIndex - 1);
      paintActive();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (activeIndex >= 0) openRow(activeIndex);
      else if (resultItems.length) openRow(0);
      else search();
    }
  });
  results.addEventListener("click", (event) => {
    const row = event.target.closest?.("[data-global-note]");
    if (!row) return;
    close();
    openNote(row.dataset.globalNote);
  });
  if (typeof dialog.showModal === "function") dialog.showModal();
  input.focus();
}
