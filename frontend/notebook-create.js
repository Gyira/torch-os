// 人生路/知识库「新建本子」对话框（2026-10-30）。
// 本子 = 库根目录下的文件夹；这里只负责收名字、调后端、刷新页面。
import { api, toast } from "./core.js";

const PILLAR_COPY = {
  life: {
    eyebrow: "人生路 · 新本子",
    help: "会在 <code>00_人生路/</code> 下创建同名文件夹。本子就是文件夹，Obsidian 里也能直接看到和编辑。",
  },
  knowledge: {
    eyebrow: "知识库 · 新本子",
    help: "会在 <code>03_知识库/</code> 下创建同名文件夹。本子就是文件夹，Obsidian 里也能直接看到和编辑。",
  },
};

const state = { onCreated: null, busy: false, pillar: "life" };
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
        body: JSON.stringify({ pillar: state.pillar, name }),
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

export function openNotebookCreateDialog(onCreated = null, { pillar = "life" } = {}) {
  initNotebookCreate();
  state.onCreated = onCreated;
  state.busy = false;
  state.pillar = PILLAR_COPY[pillar] ? pillar : "life";
  const copy = PILLAR_COPY[state.pillar];
  const eyebrow = el("notebook-create-eyebrow");
  if (eyebrow) eyebrow.textContent = copy.eyebrow;
  const help = el("notebook-create-help");
  if (help) help.innerHTML = copy.help;
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
