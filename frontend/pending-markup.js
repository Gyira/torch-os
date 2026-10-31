// 待确认中枢的纯渲染层：不 import core.js（它加载时会碰 document），
// 所以这份文件可以直接被 node --test 引入。

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPES[char]);

export const TABS = [
  { key: "draft", label: "草稿", hint: "定时生成的早报/晚报/周报，读过或丢弃即可" },
  { key: "fact", label: "事实", hint: "采纳才会写进身份卡或记忆与自传" },
  { key: "triage", label: "收件箱", hint: "收件箱里还没归类的内容，选个章节搬过去" },
  { key: "love", label: "恋爱记忆", hint: "每日记忆草稿，写入四库或丢弃" },
];

export const ACTION_LABEL = {
  read: "标记已读", dismiss: "丢弃", accept: "采纳", reject: "拒绝",
  move: "移过去", write: "写入四库", discard: "丢弃",
};
const DESTRUCTIVE = new Set(["dismiss", "reject", "discard"]);

export function pendingStripMarkup(summary) {
  const total = Number(summary?.total) || 0;
  if (!total) return "";
  const parts = TABS
    .map((tab) => ({ label: tab.label, count: Number(summary?.[tab.key]) || 0 }))
    .filter((part) => part.count > 0)
    .map((part) => `${part.label} ${part.count}`)
    .join(" · ");
  return `<button type="button" class="pending-strip-button" data-pending-open>
      <span class="pending-strip-count">${total}</span>
      <span class="pending-strip-text">待确认</span>
      <span class="pending-strip-parts">${escape(parts)}</span>
    </button>`;
}

function chapterSelectMarkup(item, chapters) {
  if (!Array.isArray(chapters) || !chapters.length) return "";
  const options = chapters.map((chapter) => {
    const label = [chapter.pillar, chapter.title].filter(Boolean).join(" / ");
    return `<option value="${escape(chapter.key)}">${escape(label)}</option>`;
  }).join("");
  return `<select class="pending-chapter-select" data-pending-chapter aria-label="搬到哪个章节"><option value="">选择章节…</option>${options}</select>`;
}

export function pendingItemMarkup(item, options = {}) {
  const actions = (item.actions || []).map((action) => {
    const style = DESTRUCTIVE.has(action) ? "text-button" : "button secondary";
    return `<button type="button" class="${style}" data-pending-action="${escape(action)}">${escape(ACTION_LABEL[action] || action)}</button>`;
  }).join("");
  const lines = (item.lines || []).slice(0, 6).map((line) => `<li>${escape(line)}</li>`).join("");
  const select = item.kind === "triage" ? chapterSelectMarkup(item, options.chapters) : "";
  return `<article class="pending-item" data-pending-item data-pending-kind="${escape(item.kind)}" data-pending-id="${escape(item.id)}">
      <p class="eyebrow">${escape(item.source || "")}</p>
      <h3 class="pending-item-title">${escape(item.title || "")}</h3>
      ${item.preview ? `<p class="pending-item-preview">${escape(item.preview)}</p>` : ""}
      ${lines ? `<ul class="pending-item-lines">${lines}</ul>` : ""}
      <div class="pending-item-actions">${actions}${select}</div>
    </article>`;
}

export function pendingListMarkup(items, kind, options = {}) {
  const list = Array.isArray(items) ? items : [];
  if (!list.length) {
    const tab = TABS.find((row) => row.key === kind);
    return `<div class="pillar-empty"><p>这里没有待确认的内容。</p><p class="field-help">${escape(tab?.hint || "")}</p></div>`;
  }
  return `<div class="pending-list">${list.map((item) => pendingItemMarkup(item, options)).join("")}</div>`;
}
