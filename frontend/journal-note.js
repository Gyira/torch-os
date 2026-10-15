// 日记模块（工作台 2.0）：今天的日记（Markdown：02-Life/journal/YYYY-MM-DD.md）+ 记录模块
// （今天动过的文件，真实 mtime）。Obsidian 就是编辑器：产品只做「按模板起草 + 自动汇总 +
// 可选 AI 润色」，文件优先、绝不覆盖你写过的字。
import { $, api, escapeHtml, jsonBody, setStatus, toast, confirmAction } from "./core.js";
import { shanghaiDay } from "./journal-model.js";

let note = null;
let loadId = 0;
let activityId = 0;

function obsidianLink() {
  const vault = String(note?.vault_name || "").trim();
  const file = String(note?.relpath || "").trim();
  if (!vault || !file) return "";
  return `obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(file)}`;
}

function renderNote() {
  const body = $("#journal-note-body");
  const button = $("#journal-note-generate");
  if (!note) {
    button.disabled = true;
    return;
  }
  button.disabled = false;
  button.textContent = note.exists ? "追加自动汇总" : "创建今天的日记";
  if (!note.exists) {
    body.innerHTML = '<div class="empty-state">这一天还没有日记。点「创建今天的日记」：按模板起草骨架，并把今天动过的文件、完成的事项自动汇总进去；生成后在 Obsidian 里自由修改。</div>';
    return;
  }
  const link = obsidianLink();
  const openLine = link
    ? `<a class="text-button" href="${escapeHtml(link)}">在 Obsidian 里打开 →</a>`
    : `<p class="field-help journal-note-path">在 Obsidian 里打开：${escapeHtml(note.path || note.relpath)}</p>`;
  body.innerHTML = `<p class="meta journal-note-meta">文件 <code>${escapeHtml(note.relpath)}</code> · 最后修改 ${escapeHtml(note.updated_at || "")} · <strong>文件优先</strong>：产品不会改动你写过的字，只会在你确认后把“自动汇总”追加到末尾。</p>` +
    `<pre class="journal-note-text">${escapeHtml(note.content.slice(0, 6000))}</pre>` +
    openLine;
}

export async function loadJournalNote(day) {
  const target = day || $("#journal-date")?.value || "";
  if (!target) return;
  const request = ++loadId;
  try {
    const result = await api(`/api/journal/note?day=${encodeURIComponent(target)}`);
    if (request !== loadId) return;
    note = result;
    setStatus("#journal-note-status", "");
    renderNote();
  } catch (error) {
    if (request !== loadId) return;
    note = null;
    renderNote();
    setStatus("#journal-note-status", `日记读取失败：${error.message}`, "error");
  }
}

async function generate() {
  if (!note) return;
  const button = $("#journal-note-generate");
  const useModel = Boolean($("#journal-note-use-model")?.checked);
  const mode = note.exists ? "append" : "create";
  if (mode === "append") {
    const confirmed = await confirmAction({
      title: "追加自动汇总？",
      message: `${note.relpath} 已经有你写的字（文件优先，不会覆盖）。要把今天的自动汇总追加到文件末尾吗？`,
      confirmLabel: "追加到末尾",
      cancelLabel: "先不改",
      trigger: button,
    });
    if (!confirmed) return;
  }
  button.disabled = true;
  setStatus("#journal-note-status", mode === "append" ? "正在追加…" : (useModel ? "正在按模板生成并请模型润色…" : "正在按模板生成…"));
  try {
    const result = await api("/api/journal/note/generate", jsonBody("POST", { day: note.day, mode, use_model: useModel }));
    note = result;
    renderNote();
    toast(mode === "append" ? "自动汇总已追加到日记末尾。" : "日记已生成，去 Obsidian 里改吧。");
    setStatus("#journal-note-status", mode === "append" ? "已追加自动汇总。" : "已按模板生成。", "success");
  } catch (error) {
    setStatus("#journal-note-status", error.message, "error");
  } finally {
    if (note) button.disabled = false;
  }
}

/** 记录模块：今天动过的文件（后端按真实 mtime 现场扫描，未索引的文件只给路径）。 */
export async function loadActivity(day) {
  const target = day || $("#journal-date")?.value || "";
  if (!target) return;
  const request = ++activityId;
  const box = $("#journal-activity");
  try {
    const result = await api(`/api/vault/activity?day=${encodeURIComponent(target)}`);
    if (request !== activityId) return;
    if (!result.files.length) {
      box.innerHTML = '<div class="empty-state">这一天还没有动过任何文件——写点什么、勾几条待办，这里就有记录了。</div>';
      return;
    }
    box.innerHTML = `<p class="meta" role="status">${escapeHtml(result.day)} · ${Number(result.count) || 0} 个文件</p>` +
      '<ul class="activity-list">' + result.files.map((item) => (
        `<li><span class="activity-time">${escapeHtml(String(item.mtime || "").slice(11, 16))}</span>` +
        `<span class="activity-title">${escapeHtml(item.title)}</span>` +
        `<small class="activity-path">${escapeHtml(item.path)}</small></li>`
      )).join("") + "</ul>";
  } catch (error) {
    if (request === activityId) {
      box.innerHTML = `<div class="empty-state">记录读取失败：${escapeHtml(error.message)}，点「刷新」重试。</div>`;
    }
  }
}

export function initJournalNote() {
  $("#journal-note-generate").addEventListener("click", generate);
  $("#journal-activity-refresh").addEventListener("click", () => loadActivity());
  document.addEventListener("journal-day-changed", (event) => {
    loadJournalNote(event?.detail?.day);
    loadActivity(event?.detail?.day);
  });
}
