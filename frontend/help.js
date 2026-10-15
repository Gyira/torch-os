import { $, escapeHtml, setStatus } from "./core.js";
import { HELP_SECTIONS } from "./help-content.js";

// Local, static in-app guide. It reads no personal data and calls no API.
let query = "";

function textOf(item) {
  return [item.title, item.body, ...(item.keywords || [])].join("\n").toLowerCase();
}

function visibleSections() {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  return HELP_SECTIONS
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => terms.every((term) => textOf(item).includes(term))),
    }))
    .filter((section) => section.items.length);
}

export function renderHelp() {
  const container = $("#help-body");
  if (!container) return;
  const total = HELP_SECTIONS.reduce((count, section) => count + section.items.length, 0);
  const sections = visibleSections();
  const shown = sections.reduce((count, section) => count + section.items.length, 0);
  if (!shown) {
    container.innerHTML = `<p class="empty-state">没有找到与“${escapeHtml(query.trim())}”匹配的帮助条目。可以换一个词，或清空搜索查看全部。</p><div class="panel-footer"><button class="text-button" data-help-clear>清空搜索</button></div>`;
    setStatus("#help-status", `0 / ${total} 条帮助`, "warning");
    return;
  }
  container.innerHTML = sections.map((section) => `<section class="panel help-section" aria-labelledby="help-${escapeHtml(section.id)}"><h3 id="help-${escapeHtml(section.id)}">${escapeHtml(section.title)}</h3>${section.items.map((item) => `<article class="help-item"><h4>${escapeHtml(item.title)}</h4><p class="preserve-lines">${escapeHtml(item.body)}</p></article>`).join("")}</section>`).join("");
  setStatus("#help-status", query.trim() ? `${shown} / ${total} 条帮助` : `共 ${total} 条帮助，全部本地内容`, "success");
}

export function initHelp() {
  $("#help-search").addEventListener("input", () => {
    query = $("#help-search").value;
    renderHelp();
  });
  $("#help-body").addEventListener("click", (event) => {
    if (!event.target.closest("[data-help-clear]")) return;
    query = "";
    $("#help-search").value = "";
    renderHelp();
  });
  renderHelp();
}
