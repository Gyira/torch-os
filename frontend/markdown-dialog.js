import { api, escapeHtml, openDialog, toast } from "./core.js";
import { renderMarkdownDocument } from "./pillar-markdown.js";
import { openObsidianNote } from "./task-open.js";

export function showMarkdownNote(note, vaultName) {
  const { html } = renderMarkdownDocument(note.body || "", { notePath: note.path });
  const current = openDialog(note.title || "文档", `<div class="section-actions">
    <button type="button" class="button primary" data-dialog-edit-note title="在 Obsidian 中编辑">编辑</button>
  </div><p class="field-help">${escapeHtml(note.path)}</p><div class="md-body">${html}</div>`);
  document.querySelector("#dialog-content [data-dialog-edit-note]").onclick = () => {
    const refresh = async ({ detail }) => {
      if (!current() || (detail.path !== note.path && detail.previousPath !== note.path)) return;
      if (detail.deleted) { document.querySelector("#detail-dialog").close(); return; }
      try {
        const updated = await api(`/api/notes?path=${encodeURIComponent(detail.path)}`);
        if (current()) showMarkdownNote(updated, vaultName);
      } catch (error) { if (current()) toast(error.message, "warning"); }
    };
    document.addEventListener("torch:notes-changed", refresh, { once: true });
    if (!openObsidianNote(note.path, vaultName, { edit: true })) toast("无法打开文档，请检查 Obsidian 库连接。", "warning");
  };
}
