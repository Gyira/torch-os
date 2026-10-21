// 全局搜索：跨四库检索标题/正文/标签，点结果直接打开详情。
import { api, escapeHtml, toast } from "./core.js";
import { openNote } from "./pillars.js";

const PILLAR_LABELS = { life: "人生路", self: "自我档案", ops: "生活运营", knowledge: "知识库" };

export function openGlobalSearch() {
  const dialog = document.createElement("dialog");
  dialog.className = "pillar-dialog pillar-search-dialog";
  dialog.innerHTML = `
    <div class="pillar-dialog-head">
      <div><p class="eyebrow">全局搜索</p><h2>在四库里找</h2></div>
      <button type="button" class="text-button" data-search-close>关闭</button>
    </div>
    <div class="pillar-global-search">
      <input type="search" data-global-query placeholder="输入标题、正文或标签，回车搜索" autofocus>
      <p class="status-message" data-global-status role="status"></p>
      <div data-global-results></div>
    </div>`;
  document.body.append(dialog);
  const close = () => { dialog.close(); dialog.remove(); };
  dialog.querySelector("[data-search-close]").addEventListener("click", close);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  const input = dialog.querySelector("[data-global-query]");
  const status = dialog.querySelector("[data-global-status]");
  const results = dialog.querySelector("[data-global-results]");
  let timer = 0;
  const search = async () => {
    const query = input.value.trim();
    if (!query) { results.innerHTML = ""; status.textContent = ""; return; }
    status.textContent = "搜索中…";
    try {
      const payload = await api(`/api/search?q=${encodeURIComponent(query)}&limit=60`);
      const items = Array.isArray(payload.items) ? payload.items : [];
      status.textContent = items.length ? `找到 ${items.length} 条` : "没有找到匹配的笔记。";
      results.innerHTML = items.length ? `<div class="pillar-list">${items.map((item) => `
        <button type="button" class="pillar-note-row" data-global-note="${escapeHtml(item.path)}">
          <span class="pillar-note-main">
            <span class="pillar-note-title">${escapeHtml(item.title)}</span>
            <span class="pillar-note-excerpt">${escapeHtml(item.excerpt || "")}</span>
          </span>
          <span class="pillar-note-meta">${escapeHtml(PILLAR_LABELS[item.pillar] || item.pillar)} · ${escapeHtml(item.date || "")}</span>
        </button>`).join("")}</div>` : "";
    } catch (error) {
      status.textContent = error.message;
      results.innerHTML = "";
    }
  };
  input.addEventListener("input", () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(search, 220);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); search(); }
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
