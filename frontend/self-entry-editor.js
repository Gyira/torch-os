import { api, toast } from "./core.js";
import { BOOKS, CONFIDENCE_LABELS, ENTRY_TYPE_LABELS, escape } from "./self-archive-model.js";

function options(items, selected) {
  return Object.entries(items).map(([key, label]) =>
    `<option value="${escape(key)}"${key === selected ? " selected" : ""}>${escape(label)}</option>`).join("");
}

export function editArchiveEntry(entry = {}) {
  const dialog = document.createElement("dialog");
  dialog.className = "self-entry-dialog";
  dialog.innerHTML = `<h2>记一条</h2><form id="self-entry-form" class="settings-form">
    <label>本子<select name="book_key">${options(Object.fromEntries(BOOKS.map(b => [b.key, b.title])), entry.section)}</select></label>
    <label>标题<input name="title" maxlength="120" required value="${escape(entry.title || "")}"></label>
    <label>内容<textarea name="text" rows="5" maxlength="8000" required>${escape(entry.text || "")}</textarea></label>
    <label>类型<select name="entry_type">${options(ENTRY_TYPE_LABELS, entry.entry_type || "narrative")}</select></label>
    <label>确信程度<select name="confidence">${options(CONFIDENCE_LABELS, entry.confidence || "medium")}</select></label>
    ${[["evidence","依据"],["counterexample","反例"],["verify","待验证"]].map(([key,label]) =>
      `<label>${label}<textarea name="${key}" maxlength="2000" rows="2">${escape(entry[key] || "")}</textarea></label>`).join("")}
    <label>来源（每行一项）<textarea name="sources" rows="2">${escape((entry.sources || []).join("\n"))}</textarea></label>
    <p role="status"></p><button type="submit" class="button primary">保存</button><button type="button" data-cancel>取消</button></form>`;
  document.body.append(dialog);
  dialog.showModal();
  dialog.querySelector("[data-cancel]").onclick = () => dialog.close();
  dialog.addEventListener("close", () => {
    const tools = dialog.querySelector("#dsh-tools");
    if (tools) document.body.moveBefore(tools, null);
    dialog.remove();
  }, { once: true });
  const form = dialog.querySelector("#self-entry-form");
  const op = "manual-" + crypto.randomUUID();
  form.onsubmit = async event => {
    event.preventDefault();
    const button = form.querySelector("button");
    if (button.disabled) return;
    button.disabled = true;
    const values = Object.fromEntries(new FormData(form));
    const payload = { ...values, book_key: form.elements.book_key.value,
      operation_id: op, sources: values.sources.split("\n").map(s => s.trim()).filter(Boolean) };
    try {
      await api("/api/self/entries", {
        method: "POST", body: JSON.stringify(payload),
      });
      if (dialog.open) dialog.close();
      document.dispatchEvent(new CustomEvent("torch:self-refresh"));
      toast("已保存到自我档案。");
    } catch (error) { form.querySelector('[role="status"]').textContent = error.message; }
    finally { button.disabled = false; }
  };
}
