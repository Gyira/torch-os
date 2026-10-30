// 人生路「新建本子」对话框（2026-10-30）。
// 本子 = 00_人生路 下的文件夹；这里只负责收名字、调后端、刷新页面。
import { api, toast } from "./core.js";

const state = { onCreated: null, busy: false };
let initialized = false;

function el(id) { return document.getElementById(id); }

function setStatus(message, tone = "") {
  const node = el("notebook-create-status");
  if (!node) return;
  node.textContent = message;
  node.className = `status-message ${tone}`.trim();
}

function closeDialog() {
  const dialog = el("notebook-create-dialog");
  if (dialog?.open) dialog.close();
  else dialog?.removeAttribute("open");
  state.onCreated = null;
}

export function initNotebookCreate() {
  if (initialized) return;
  initialized = true;
  el("notebook-create-close")?.addEventListener("click", closeDialog);
  el("notebook-create-cancel")?.addEventListener("click", closeDialog);
  el("notebook-create-dialog")?.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDialog();
  });
  el("notebook-create-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.busy) return;
    const input = el("notebook-create-name");
    const name = String(input?.value || "").trim();
    if (!name) {
      setStatus("先给本子起一个名字。", "warning");
      input?.focus?.();
      return;
    }
    state.busy = true;
    const submit = el("notebook-create-submit");
    if (submit) submit.disabled = true;
    setStatus("正在创建文件夹…");
    try {
      const created = await api("/api/notebooks", {
        method: "POST",
        body: JSON.stringify({ pillar: "life", name }),
      });
      const callback = state.onCreated;
      closeDialog();
      toast(`本子「${created.title}」已创建。`, "success");
      if (typeof callback === "function") callback(created);
    } catch (error) {
      setStatus(error.status === 409 ? "这个本子已经存在，换一个名字吧。" : `创建失败：${error.message}`, "error");
    } finally {
      state.busy = false;
      if (submit) submit.disabled = false;
    }
  });
}

export function openNotebookCreateDialog(onCreated = null) {
  initNotebookCreate();
  state.onCreated = onCreated;
  state.busy = false;
  setStatus("");
  const input = el("notebook-create-name");
  if (input) input.value = "";
  const submit = el("notebook-create-submit");
  if (submit) submit.disabled = false;
  const dialog = el("notebook-create-dialog");
  if (dialog && !dialog.open) {
    try { dialog.showModal(); } catch { dialog.setAttribute("open", ""); }
  }
  window.setTimeout(() => input?.focus?.(), 30);
}

export function closeNotebookCreateDialog() {
  closeDialog();
}
