// 习惯新增/编辑、打卡日志、右键菜单（2026-10-28）。
import { confirmAction, escapeHtml, openDialog, setStatus } from "./core.js";
import { openPopover } from "./tasks-picker.js";
import {
  FREQUENCY_KINDS, HABIT_COLOR_CHOICES, HABIT_ICON_CHOICES, PRIORITY_LABELS,
  TARGET_PRESETS, WEEKDAY_LONG, defaultHabitDraft, frequencyLabel,
  normalizeFrequency, targetLabel, validateHabitDraft,
} from "./habits-model.js";

function closeDialog() {
  const dialog = document.querySelector("#detail-dialog");
  if (dialog?.open) dialog.close();
}

function value(value) {
  return escapeHtml(value ?? "");
}

function frequencyFieldsHtml(kind, draft = {}) {
  const frequency = draft.frequency || {};
  if (kind === "weekly") {
    const checked = new Set((frequency.weekdays || []).map(Number));
    return `<div class="habit-weekday-picker" role="group" aria-label="每周计划日">` +
      WEEKDAY_LONG.map((label, index) => `<label class="habit-weekday-choice"><input type="checkbox" data-weekday="${index}"${checked.has(index) ? " checked" : ""} />${label.slice(1)}</label>`).join("") +
      "</div>";
  }
  if (kind === "monthly") {
    const text = (frequency.month_days || []).join(", ");
    return `<label class="habit-inline-field">每月几号<input id="habit-month-days" value="${value(text)}" placeholder="例如 1, 15" autocomplete="off" /></label><p class="field-help">短月没有的日期会自动跳过。</p>`;
  }
  if (kind === "interval") {
    return `<label class="habit-inline-field">每隔几天<input id="habit-interval" type="number" min="1" max="365" value="${Number(frequency.interval_days) || 2}" /></label>`;
  }
  return `<p class="field-help">每天都会出现在今日任务和四象限。</p>`;
}

function targetOptions(current) {
  const number = Number(current) || 0;
  const options = ['<option value="">永远</option>'];
  for (const preset of TARGET_PRESETS) {
    options.push(`<option value="${preset}"${number === preset ? " selected" : ""}>${preset}天</option>`);
  }
  options.push(`<option value="custom"${number && !TARGET_PRESETS.includes(number) ? " selected" : ""}>自定义</option>`);
  return options.join("");
}

function colorChoices(current) {
  return HABIT_COLOR_CHOICES.map((color) => `<button type="button" class="habit-color-choice${String(current || "").toLowerCase() === color ? " is-selected" : ""}" data-habit-color-choice="${color}" data-habit-choice-color="${color}" aria-label="颜色 ${color}" title="${color}"></button>`).join("");
}

export function openHabitDialog({ habit = null, groups = [], today = "", defaultGroup = "", onSave }) {
  const draft = habit ? {
    ...habit,
    frequency: habit.frequency || { kind: "daily" },
    target_days: habit.target_days ?? null,
    group: habit.group || "",
  } : defaultHabitDraft(today, groups);
  if (!habit && defaultGroup) draft.group = defaultGroup;
  const kind = draft.frequency?.kind || "daily";
  const groupNames = (Array.isArray(groups) ? groups : []).map((group) => group.name).filter(Boolean);
  const form = `<form id="habit-form" class="habit-form" novalidate>
    <div class="habit-form-top">
      <div class="habit-icon-field">
        <span class="habit-icon-preview" id="habit-icon-preview" aria-hidden="true">${value(draft.icon || "🌱")}</span>
        <label>图标<input id="habit-icon" maxlength="16" value="${value(draft.icon || "🌱")}" autocomplete="off" /></label>
      </div>
      <div class="habit-icon-choices" role="group" aria-label="选择图标">${HABIT_ICON_CHOICES.map((icon) => `<button type="button" class="habit-icon-choice" data-habit-icon-choice="${icon}" aria-label="图标 ${icon}">${icon}</button>`).join("")}</div>
    </div>
    <label class="habit-field">名称<input id="habit-title" maxlength="120" value="${value(draft.title || "")}" placeholder="例如：每天阅读 10 分钟" autocomplete="off" /></label>
    <div class="habit-form-row">
      <label class="habit-field">所属分组<input id="habit-group" maxlength="40" list="habit-group-list" value="${value(draft.group || "")}" placeholder="默认" autocomplete="off" /></label>
      <label class="habit-field">优先级<select id="habit-priority">${Object.entries(PRIORITY_LABELS).map(([key, label]) => `<option value="${key}"${(draft.priority || "medium") === key ? " selected" : ""}>${label}</option>`).join("")}</select></label>
    </div>
    <datalist id="habit-group-list">${groupNames.map((name) => `<option value="${value(name)}"></option>`).join("")}</datalist>
    <div class="habit-form-row">
      <label class="habit-field">频率<select id="habit-frequency">${FREQUENCY_KINDS.map((item) => `<option value="${item.value}"${kind === item.value ? " selected" : ""}>${item.label}</option>`).join("")}</select></label>
      <div class="habit-frequency-fields" id="habit-frequency-fields">${frequencyFieldsHtml(kind, draft)}</div>
    </div>
    <div class="habit-form-row">
      <label class="habit-field">目标<input value="当天完成打卡" disabled /></label>
      <label class="habit-field">开始日期<input id="habit-start" type="date" value="${value(draft.start_date || today)}" /></label>
    </div>
    <div class="habit-form-row">
      <label class="habit-field">坚持天数<select id="habit-target">${targetOptions(draft.target_days)}</select></label>
      <label class="habit-field" id="habit-target-custom-wrap"${draft.target_days && !TARGET_PRESETS.includes(Number(draft.target_days)) ? "" : " hidden"}>自定义天数<input id="habit-target-custom" type="number" min="1" max="3650" value="${draft.target_days && !TARGET_PRESETS.includes(Number(draft.target_days)) ? Number(draft.target_days) : ""}" /></label>
    </div>
    <div class="habit-form-row">
      <label class="habit-field">提醒<input id="habit-reminder" type="time" value="${value(draft.reminder || "")}" /></label>
      <label class="habit-checkbox"><input id="habit-auto-log" type="checkbox"${draft.auto_log ? " checked" : ""} /> 自动弹出打卡日志</label>
    </div>
    <div class="habit-color-field"><span>颜色</span><input id="habit-color" type="hidden" value="${value(draft.color || "")}" /><div class="habit-color-choices" role="group" aria-label="选择颜色">${colorChoices(draft.color)}</div><button type="button" class="text-button" id="habit-color-clear">不用颜色</button></div>
    <details class="habit-advanced">
      <summary>更多设置</summary>
      <label class="habit-field">触发提示<input id="habit-cue" maxlength="500" value="${value(draft.cue || "")}" placeholder="例如：早餐后" /></label>
      <label class="habit-field">最低行动<input id="habit-minimum" maxlength="500" value="${value(draft.minimum_action || "")}" placeholder="例如：读一页" /></label>
    </details>
    <p id="habit-dialog-status" class="status-message" role="status" aria-live="polite"></p>
    <div class="form-actions">
      <button type="button" class="button secondary" id="habit-dialog-cancel">取消</button>
      <button type="submit" class="button primary" id="habit-dialog-save">${habit ? "保存修改" : "保存"}</button>
    </div>
  </form>`;
  openDialog(habit ? "编辑习惯" : "添加习惯", form);
  const formNode = document.querySelector("#habit-form");
  if (!formNode) return;
  const fields = {
    title: document.querySelector("#habit-title"),
    icon: document.querySelector("#habit-icon"),
    group: document.querySelector("#habit-group"),
    priority: document.querySelector("#habit-priority"),
    frequency: document.querySelector("#habit-frequency"),
    frequencyFields: document.querySelector("#habit-frequency-fields"),
    start: document.querySelector("#habit-start"),
    target: document.querySelector("#habit-target"),
    targetCustomWrap: document.querySelector("#habit-target-custom-wrap"),
    targetCustom: document.querySelector("#habit-target-custom"),
    reminder: document.querySelector("#habit-reminder"),
    autoLog: document.querySelector("#habit-auto-log"),
    color: document.querySelector("#habit-color"),
    cue: document.querySelector("#habit-cue"),
    minimum: document.querySelector("#habit-minimum"),
    preview: document.querySelector("#habit-icon-preview"),
  };
  const currentFrequency = () => {
    const kind = fields.frequency.value;
    return normalizeFrequency(kind, {
      weekdays: [...document.querySelectorAll("#habit-frequency-fields [data-weekday]")].filter((node) => node.checked).map((node) => Number(node.dataset.weekday)),
      month_days_text: document.querySelector("#habit-month-days")?.value || "",
      interval_days: document.querySelector("#habit-interval")?.value || 1,
    });
  };
  fields.frequency.addEventListener("change", () => {
    fields.frequencyFields.innerHTML = frequencyFieldsHtml(fields.frequency.value, { frequency: currentFrequency() });
  });
  fields.icon.addEventListener("input", () => { fields.preview.textContent = fields.icon.value || "🌱"; });
  document.querySelectorAll("[data-habit-icon-choice]").forEach((button) => {
    button.addEventListener("click", () => {
      fields.icon.value = button.dataset.habitIconChoice || "🌱";
      fields.preview.textContent = fields.icon.value;
    });
  });
  document.querySelectorAll("[data-habit-choice-color]").forEach((button) => {
    button.style.setProperty("--habit-choice", button.dataset.habitChoiceColor || "");
  });
  document.querySelectorAll("[data-habit-color-choice]").forEach((button) => {
    button.addEventListener("click", () => {
      fields.color.value = button.dataset.habitColorChoice || "";
      document.querySelectorAll("[data-habit-color-choice]").forEach((item) => item.classList.toggle("is-selected", item === button));
    });
  });
  document.querySelector("#habit-color-clear")?.addEventListener("click", () => {
    fields.color.value = "";
    document.querySelectorAll("[data-habit-color-choice]").forEach((item) => item.classList.remove("is-selected"));
  });
  fields.target.addEventListener("change", () => {
    fields.targetCustomWrap.hidden = fields.target.value !== "custom";
    if (fields.target.value !== "custom") fields.targetCustom.value = "";
  });
  document.querySelector("#habit-dialog-cancel")?.addEventListener("click", closeDialog);
  formNode.addEventListener("submit", async (event) => {
    event.preventDefault();
    const custom = fields.target.value === "custom" ? Number(fields.targetCustom.value) : null;
    let targetDays = null;
    if (fields.target.value === "custom") targetDays = custom && custom > 0 ? custom : null;
    else if (fields.target.value) targetDays = Number(fields.target.value);
    const payload = {
      title: fields.title.value.trim(),
      icon: fields.icon.value.trim() || "🌱",
      color: fields.color.value,
      group: fields.group.value.trim() || "默认",
      frequency: currentFrequency(),
      goal: "binary",
      start_date: fields.start.value,
      target_days: targetDays,
      reminder: fields.reminder.value,
      auto_log: fields.autoLog.checked,
      priority: fields.priority.value,
      cue: fields.cue.value.trim(),
      minimum_action: fields.minimum.value.trim(),
    };
    if (habit) {
      payload.revision = habit.revision;
      if (habit.sort_order !== undefined) payload.sort_order = habit.sort_order;
    }
    const problem = validateHabitDraft(payload);
    if (problem) {
      setStatus("#habit-dialog-status", problem, "warning");
      return;
    }
    const button = document.querySelector("#habit-dialog-save");
    if (button) button.disabled = true;
    try {
      await onSave(payload);
      closeDialog();
    } catch (error) {
      setStatus("#habit-dialog-status", error.message || "保存失败", "error");
      if (button) button.disabled = false;
    }
  });
}

export function openHabitLogDialog({ habit, day, entry = null, onSave }) {
  const status = entry?.status === "unrecorded" && !String(entry?.note || "").trim()
    ? "done" : (entry?.status || "done");
  const note = entry?.note || "";
  const form = `<form id="habit-log-form" class="habit-log-form">
    <p class="meta">${value(habit?.icon || "🌱")} ${value(habit?.title || "习惯")} · ${value(day)}</p>
    <label class="habit-field">状态<select id="habit-log-status">
      <option value="done"${status === "done" ? " selected" : ""}>完成</option>
      <option value="skipped"${status === "skipped" ? " selected" : ""}>主动休息</option>
      <option value="unrecorded"${status === "unrecorded" ? " selected" : ""}>清除记录</option>
    </select></label>
    <label class="habit-field">打卡日志<textarea id="habit-log-note" maxlength="2000" rows="4" placeholder="今天有什么想记下的？">${value(note)}</textarea></label>
    <p id="habit-log-dialog-status" class="status-message" role="status" aria-live="polite"></p>
    <div class="form-actions">
      <button type="button" class="button secondary" id="habit-log-cancel">取消</button>
      <button type="submit" class="button primary" id="habit-log-save">保存</button>
    </div>
  </form>`;
  openDialog("打卡日志", form);
  const formNode = document.querySelector("#habit-log-form");
  if (!formNode) return;
  document.querySelector("#habit-log-cancel")?.addEventListener("click", closeDialog);
  formNode.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = document.querySelector("#habit-log-save");
    if (button) button.disabled = true;
    try {
      await onSave({
        revision: Number(entry?.revision) || 0,
        status: document.querySelector("#habit-log-status").value,
        note: document.querySelector("#habit-log-note").value,
      });
      closeDialog();
    } catch (error) {
      setStatus("#habit-log-dialog-status", error.message || "保存失败", "error");
      if (button) button.disabled = false;
    }
  });
}

function menuRow(layer, label, onClick, extra = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "time-popover-row";
  button.innerHTML = `<span class="time-popover-label">${escapeHtml(label)}</span>${extra}`;
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    onClick();
  });
  layer.append(button);
}

export function openHabitMenu(anchor, habit, actions = {}) {
  openPopover(anchor, (layer, close) => {
    if (habit.path && actions.openDocument) {
      menuRow(layer, "📄 打开习惯笔记", () => { close(); actions.openDocument(habit); });
    }
    menuRow(layer, "✏️ 编辑", () => { close(); actions.edit?.(habit); });
    if (habit.status === "active" && habit.scheduled_today) {
      const skipped = habit.entry_today?.status === "skipped";
      menuRow(layer, skipped ? "↩️ 取消主动休息" : "😴 今天主动休息", () => {
        close();
        actions.skip?.(habit, !skipped);
      });
    }
    if (habit.status === "archived") {
      menuRow(layer, "📤 取消归档", () => { close(); actions.archive?.(habit, false); });
    } else {
      menuRow(layer, "📦 归档", () => { close(); actions.archive?.(habit, true); });
    }
    menuRow(layer, "🗑 删除", () => { close(); actions.delete?.(habit); });
  });
}

export async function confirmDeleteHabit(habit, onConfirm) {
  const confirmed = await confirmAction({
    title: `删除习惯「${habit.title}」？`,
    message: "习惯笔记和全部打卡记录会先移到当前个人数据库的 .torch-os/habit-trash，不会立刻彻底删除；确认后这个习惯会从今日任务和四象限消失。",
    confirmLabel: "删除习惯",
    danger: true,
  });
  if (confirmed) await onConfirm();
}

export { frequencyLabel, targetLabel };
