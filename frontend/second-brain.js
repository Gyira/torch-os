// 第二大脑：对接 Obsidian 库的笔记入口。快速记一条就写进 Vault 的 90-Inbox，
// 是一篇普通 Markdown，Obsidian 立刻可见，dsh 也能读。
import { $, api, escapeHtml, jsonBody, setStatus, toast } from "./core.js";

const KIND_LABELS = { note: "笔记", idea: "灵感", memo: "备忘录" };

export async function loadNotes() {
  const container = $("#note-quick-list");
  try {
    const documents = await api("/api/documents");
    const recent = [...documents]
      .sort((left, right) => String(right.indexed_at || "").localeCompare(String(left.indexed_at || "")))
      .slice(0, 12);
    if (!recent.length) {
      container.innerHTML = '<div class="empty-state">Vault 里还没有笔记。上面写一条就会出现在这里。</div>';
      return;
    }
    container.innerHTML = `<ul class="note-list">${recent.map((item) => `<li><div><strong>${escapeHtml(item.title || "(无标题)")}</strong><p class="meta">${escapeHtml(item.path || "")}</p></div><span class="meta">${escapeHtml(String(item.indexed_at || "").slice(0, 10))}</span></li>`).join("")}</ul>`;
  } catch (error) {
    container.innerHTML = `<div class="state-message error-state"><strong>读取失败</strong><p>${escapeHtml(error.message)}</p></div>`;
  }
}

async function saveNote(event) {
  event.preventDefault();
  const title = $("#note-quick-title").value.trim();
  const content = $("#note-quick-content").value.trim();
  const kind = $("#note-quick-kind").value || "note";
  if (!title || !content) {
    setStatus("#note-quick-status", "标题和内容都要写一点。", "error");
    return;
  }
  const button = $("#note-quick-save");
  button.disabled = true;
  try {
    await api("/api/notes", jsonBody("POST", { title, content, type: kind }));
    $("#note-quick-title").value = "";
    $("#note-quick-content").value = "";
    toast(`已保存为${KIND_LABELS[kind] || "笔记"}，写进了 Vault 的 90-Inbox。`);
    setStatus("#note-quick-status", "已写入 Vault；扫描后它会变成待确认的记忆候选。", "success");
    await loadNotes();
  } catch (error) {
    setStatus("#note-quick-status", `保存失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

export function initSecondBrain() {
  $("#note-quick-form").addEventListener("submit", saveNote);
  $("#note-quick-refresh").addEventListener("click", () => loadNotes());
}
