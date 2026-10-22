// 时间管理模块的弹窗（2026-10-08，照滴答实测规格）：添加/编辑清单、复制清单、新建/重命名文件夹、添加/编辑标签；
// 2026-10-11 日历那一轮加了「快速添加任务」（右上角 ＋）。
// 共用一个小表单构造器（dialog + form），字段类型：text / date / time / swatches（颜色圆点）/ select / radio。
// 颜色是用户数据（存进 00_人生路/08_任务与待办/_系统/任务（tasks）.md 的 front matter），所以直接写内联样式——
// theme-tokens 守卫只扫 styles.css，这里是数据不是主题色。
import { escapeHtml } from "./core.js";
import { INBOX, PRIORITIES } from "./tasks-model.js";

/** 滴答那排圆点：无 / 红 / 橙 / 黄 / 绿 / 青 / 蓝 / 紫。 */
export const PALETTE = ["", "#ef4444", "#f97316", "#eab308", "#22c55e", "#14b8a6", "#3b82f6", "#8b5cf6"];

function field({ type, name, label, value = "", options = [], placeholder = "" }) {
  const wrap = document.createElement("div");
  wrap.className = "field time-dialog-field";
  wrap.dataset.name = name;  // 提交时靠它取字段值（每个分支都要有，别只在颜色那支设）
  if (label) {
    const text = document.createElement("label");
    text.textContent = label;
    text.setAttribute("for", `time-field-${name}`);
    wrap.append(text);
  }
  if (type === "swatches") {
    const row = document.createElement("div");
    row.className = "time-swatches";
    row.setAttribute("role", "radiogroup");
    row.setAttribute("aria-label", label || "颜色");
    let current = value;
    for (const color of PALETTE) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `time-swatch${color ? "" : " is-none"}${color === current ? " is-current" : ""}`;
      button.dataset.color = color;
      button.title = color || "无颜色";
      button.setAttribute("aria-label", color || "无颜色");
      if (color) button.style.setProperty("--swatch", color);
      button.addEventListener("click", () => {
        current = color;
        row.querySelectorAll(".time-swatch").forEach((node) => node.classList.toggle("is-current", node === button));
      });
      row.append(button);
    }
    wrap.append(row);
    wrap.getValue = () => current;
    return wrap;
  }
  if (type === "date" || type === "time") {
    // 原生日期/时间输入（快速添加用）：日历那边点 ＋ 时预填当前视图那一天/那个时刻
    const input = document.createElement("input");
    input.type = type;
    input.id = `time-field-${name}`;
    input.className = "time-dialog-input";
    input.value = value;
    wrap.append(input);
    wrap.getValue = () => input.value;
    return wrap;
  }
  if (type === "select") {
    const select = document.createElement("select");
    select.id = `time-field-${name}`;
    select.className = "time-dialog-select";
    for (const option of options) {
      const node = document.createElement("option");
      node.value = option.value;
      node.textContent = option.label;
      select.append(node);
    }
    select.value = value;
    wrap.append(select);
    wrap.getValue = () => select.value;
    return wrap;
  }
  if (type === "radio") {
    const group = document.createElement("div");
    group.className = "time-radios";
    group.setAttribute("role", "radiogroup");
    group.setAttribute("aria-label", label || name);
    let current = value;   // 用 change 事件跟踪（不靠 querySelector，假 DOM 里也好测）
    for (const option of options) {
      const item = document.createElement("label");
      item.className = "check-label";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = name;
      input.value = option.value;
      input.checked = option.value === current;
      input.addEventListener("change", () => { if (input.checked) current = option.value; });
      item.append(input, document.createTextNode(option.label));
      group.append(item);
    }
    wrap.append(group);
    wrap.getValue = () => current;
    return wrap;
  }
  const input = document.createElement("input");
  input.type = "text";
  input.id = `time-field-${name}`;
  input.className = "time-dialog-input";
  input.maxLength = 40;
  input.value = value;
  input.placeholder = placeholder;
  wrap.append(input);
  wrap.getValue = () => input.value.trim();
  wrap.focus = () => input.focus();
  return wrap;
}

/** 通用小弹窗：title + 若干字段 + 提交/取消。提交回调返回 false 表示不关窗（比如校验失败）。 */
export function formDialog({ title, fields = [], submitLabel = "保存", hint = "", danger = false, onSubmit }) {
  const dialog = document.createElement("dialog");
  dialog.className = "time-dialog";
  const form = document.createElement("form");
  form.className = "time-dialog-form";
  const heading = document.createElement("h2");
  heading.textContent = title;
  const body = document.createElement("div");
  body.className = "time-dialog-body";
  const nodes = fields.map((spec) => field(spec));
  body.append(...nodes);
  if (hint) {
    const note = document.createElement("p");
    note.className = "field-help";
    note.textContent = hint;
    body.append(note);
  }
  const footer = document.createElement("div");
  footer.className = "form-actions";
  const status = document.createElement("span");
  status.className = "status-message";
  status.setAttribute("role", "status");
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "button secondary";
  cancel.textContent = "取消";
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = danger ? "button danger" : "button primary";
  submit.textContent = submitLabel;
  footer.append(status, cancel, submit);
  form.append(heading, body, footer);
  dialog.append(form);
  document.body.append(dialog);

  const close = () => {
    dialog.close();
    dialog.remove();
  };
  cancel.addEventListener("click", close);
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    close();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(nodes.map((node) => [node.dataset.name, node.getValue()]));
    status.textContent = "";
    submit.disabled = true;
    try {
      // setMessage(话, 语气)：校验失败的话要看得见（2026-10-11 用户报“点添加没反应”——
      // 其实是被一句灰色小字拦下了，没人注意到），并且把焦点送回第一个能输入的字段
      const result = await onSubmit(values, (message, tone = "warning") => {
        status.textContent = message;
        status.className = `status-message ${tone}`;
      });
      if (result !== false) close();
      else nodes.find((node) => node.focus)?.focus?.();
    } finally {
      submit.disabled = false;
    }
  });
  dialog.showModal();
  (nodes.find((node) => node.focus)?.focus)?.();
  return dialog;
}

/** 添加 / 编辑清单（图 6、图 10）：名称 + 颜色 + 文件夹（可选，可就地新建）。 */
export function openListDialog({ list = null, folders = [], onSave }) {
  const editing = Boolean(list);
  const NEW_FOLDER = "__new__";
  const options = [{ value: "", label: "无" },
    ...folders.map((name) => ({ value: name, label: name })),
    { value: NEW_FOLDER, label: "＋ 新建文件夹…" }];
  const dialog = formDialog({
    title: editing ? "编辑清单" : "添加清单",
    submitLabel: editing ? "保存" : "添加",
    fields: [
      { type: "text", name: "name", label: "名称", value: list?.name || "", placeholder: "清单名称" },
      { type: "swatches", name: "color", label: "清单颜色", value: list?.color || "" },
      { type: "select", name: "folder", label: "文件夹", value: list?.folder || "", options },
      { type: "text", name: "new_folder", label: "新文件夹名称（选了「新建文件夹」才填）", value: "", placeholder: "例如：工作" },
    ],
    onSubmit: async (values, setMessage) => {
      if (!values.name) { setMessage("请先填清单名称。"); return false; }
      const folder = values.folder === NEW_FOLDER ? (values.new_folder || "").trim() : values.folder;
      if (values.folder === NEW_FOLDER && !folder) { setMessage("新建文件夹需要填个名字。"); return false; }
      await onSave({ name: list?.name || "", new_name: values.name, color: values.color, folder });
    },
  });
  return dialog;
}

/** 复制清单（图 11）：三个范围单选。 */
export function openCopyListDialog({ name, onConfirm }) {
  return formDialog({
    title: "复制清单",
    submitLabel: "确认",
    hint: "请确认复制范围",
    fields: [{
      type: "radio", name: "scope", label: "复制范围", value: "undone",
      options: [
        { value: "undone", label: "只复制未完成任务" },
        { value: "all_keep", label: "复制全部任务，保留已完成状态" },
        { value: "all_reset", label: "复制全部任务，不保留已完成状态" },
      ],
    }],
    onSubmit: async (values) => onConfirm(values.scope),
  });
}

/** 新建 / 重命名文件夹（实测：只有名称、没有颜色）。 */
export function openFolderDialog({ name = "", onSave }) {
  return formDialog({
    title: name ? "重命名文件夹" : "添加文件夹",
    fields: [{ type: "text", name: "name", label: "名称", value: name, placeholder: "文件夹名称" }],
    onSubmit: async (values, setMessage) => {
      if (!values.name) { setMessage("请先填文件夹名称。"); return false; }
      if (values.name === name) return undefined;
      await onSave(values.name);
    },
  });
}

/** 添加 / 编辑标签（图 13、图 15）：名称 + 颜色（不做上级标签）。 */
export function openTagDialog({ tag = null, onSave }) {
  const editing = Boolean(tag);
  return formDialog({
    title: editing ? "编辑标签" : "添加标签",
    submitLabel: editing ? "保存" : "保存",
    fields: [
      { type: "text", name: "name", label: "名称", value: tag?.name || "", placeholder: "标签名称" },
      { type: "swatches", name: "color", label: "颜色", value: tag?.color || "" },
    ],
    onSubmit: async (values, setMessage) => {
      if (!values.name) { setMessage("请先填标签名称。"); return false; }
      await onSave({ name: tag?.name || "", new_name: values.name, color: values.color });
    },
  });
}

/**
 * 快速添加任务（2026-10-11，日历右上角的 ＋）：标题 + 日期 + 时间 + 清单 + 优先级。
 * 五个字段都能改；日期/时间预填当前视图那一天（照参考图的 ＋）。
 */
export function openQuickAdd({ day = "", time = "", lists = [], onSave }) {
  return formDialog({
    title: "添加任务",
    submitLabel: "添加",
    fields: [
      { type: "text", name: "title", label: "标题", value: "", placeholder: "准备做什么？" },
      { type: "date", name: "date", label: "日期", value: day },
      { type: "time", name: "time", label: "时间（可选，留空 = 全天）", value: time },
      {
        type: "select", name: "list", label: "清单", value: lists[0]?.name || INBOX,
        options: lists.map((entry) => ({ value: entry.name, label: entry.folder ? `${entry.folder} / ${entry.name}` : entry.name })),
      },
      {
        type: "select", name: "priority", label: "优先级", value: "none",
        options: PRIORITIES.map((item) => ({ value: item.value, label: item.label })),
      },
    ],
    onSubmit: async (values, setMessage) => {
      if (!values.title) { setMessage("标题还没写——写点什么再点「添加」。"); return false; }
      const saved = await onSave({
        title: values.title, date: values.date, time: values.time,
        list: values.list || INBOX, priority: values.priority || "none",
      });
      if (saved === false) {   // 写回失败：内容留着，别关窗
        setMessage("没保存成功——原因见右下角；改完再点一次「添加」。");
        return false;
      }
      return undefined;
    },
  });
}

/** 二次确认（删除清单/标签/文件夹、解散文件夹）：照滴答的「关闭 / 删除」两键。 */
export function confirmDialog({ title, message, confirmLabel = "删除", danger = false, onConfirm }) {
  return formDialog({
    title,
    submitLabel: confirmLabel,
    hint: message,
    danger,
    fields: [],
    onSubmit: async () => onConfirm(),
  });
}
