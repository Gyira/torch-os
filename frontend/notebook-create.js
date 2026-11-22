// 人生路/知识库「新建本子」对话框（2026-10-30；2026-11-22 支持子本子）。
// 本子 = 库根目录下的文件夹；子本子 = 父本子文件夹下的子文件夹。
// 这里只负责收名字、调后端、刷新页面——创建后停在当前页面，不直接跳进新本子。
import { api, escapeHtml, toast } from "./core.js";
import { customNotebookName } from "./notebook-model.js";

const PILLAR_COPY = {
  life: {
    unit: "人生路",
    eyebrow: "人生路 · 新本子",
    help: "会在 <code>人生路/</code> 下创建同名文件夹。本子就是文件夹，Obsidian 里也能直接看到和编辑。",
    heading: "新建本子",
    submit: "创建本子",
  },
  knowledge: {
    unit: "知识库",
    eyebrow: "知识库 · 新本子",
    help: "会在 <code>知识库/</code> 下创建同名文件夹。本子就是文件夹，Obsidian 里也能直接看到和编辑。",
    heading: "新建本子",
    submit: "创建本子",
  },
};

const state = { onCreated: null, busy: false, pillar: "life", parent: "" };
let initialized = false;

/** 子本子的文案带上父路径，免得用户不知道文件夹会建在哪。 */
function dialogCopy(pillar, parentName) {
  const copy = PILLAR_COPY[pillar] || PILLAR_COPY.life;
  if (!parentName) return copy;
  return {
    unit: copy.unit,
    eyebrow: `${copy.unit} · 新子本子`,
    help: `会在 <code>${escapeHtml(copy.unit)}/${escapeHtml(parentName)}/</code> 下创建同名子文件夹。子本子就是文件夹，文档可以移动进去。`,
    heading: "新建子本子",
    submit: "创建子本子",
  };
}

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
        body: JSON.stringify({
          pillar: state.pillar, name, parent: state.parent,
        }),
      });
      const callback = state.onCreated;
      closeDialog();
      toast(state.parent
        ? `子本子「${created.title}」已创建。`
        : `本子「${created.title}」已创建。`, "success");
      if (typeof callback === "function") callback(created);
    } catch (error) {
      setStatus(error.status === 409 ? "这个本子已经存在，换一个名字吧。" : `创建失败：${error.message}`, "error");
    } finally {
      state.busy = false;
      if (submit) submit.disabled = false;
    }
  });
}

export function openNotebookCreateDialog(onCreated = null, { pillar = "life", parent = "" } = {}) {
  initNotebookCreate();
  state.onCreated = onCreated;
  state.busy = false;
  state.pillar = PILLAR_COPY[pillar] ? pillar : "life";
  state.parent = customNotebookName(parent) || "";
  const copy = dialogCopy(state.pillar, state.parent);
  const eyebrow = el("notebook-create-eyebrow");
  if (eyebrow) eyebrow.textContent = copy.eyebrow;
  const heading = el("notebook-create-title");
  if (heading) heading.textContent = copy.heading;
  const help = el("notebook-create-help");
  if (help) help.innerHTML = copy.help;
  setStatus("");
  const input = el("notebook-create-name");
  if (input) input.value = "";
  const submit = el("notebook-create-submit");
  if (submit) {
    submit.disabled = false;
    submit.textContent = copy.submit;
  }
  const dialog = el("notebook-create-dialog");
  if (dialog && !dialog.open) {
    try { dialog.showModal(); } catch { dialog.setAttribute("open", ""); }
  }
  window.setTimeout(() => input?.focus?.(), 30);
}

export function closeNotebookCreateDialog() {
  closeDialog();
}
