// 任务页（2026-10-08）：任务列表的渲染与行内交互、右键菜单、排序弹窗。
// 2026-10-11 日历那一轮：加了三种日历条目（小条 / 时间块 / 日程卡，都复用同一套交互），
// 任务行右侧显示时间，右键菜单多一组「时间」。
// 数据与写回由 time-module.js 编排；这里只画和收事件（照 workbench.js 的分工）。
import { escapeHtml } from "./core.js";
import {
  GROUP_OPTIONS, SORT_OPTIONS, PRIORITIES, priorityMeta, relativeDay, dayOffset, timeLabel, taskColor,
} from "./tasks-model.js";
import { openPopover, openDatePicker, openTagPicker, openTimePicker } from "./tasks-picker.js";

/** 一行任务的 DOM（任务板 / 日历页 / 四象限页共用，所以导出）。 */
export function taskRow(task, options) {
  const { today, lists, multi = false, selectedKeys = new Set(), activeKey = "" } = options;
  const legacy = Boolean(task.legacy);
  const selected = multi ? selectedKeys.has(task.key) : activeKey === task.key;
  const row = document.createElement("li");
  row.className = `time-task${task.done ? " is-done" : ""}${task.pinned ? " is-pinned" : ""}${selected ? " is-selected" : ""}${legacy ? " is-legacy" : ""}`;
  row.dataset.taskKey = task.key;
  row.dataset.taskPath = task.path || "";
  row.tabIndex = 0;
  if (selected) row.setAttribute("aria-selected", "true");

  // 多选模式：行首多一个选择框（旧任务只读，不参与批量）
  if (multi && !legacy) {
    const select = document.createElement("input");
    select.type = "checkbox";
    select.className = "task-select";
    select.checked = selectedKeys.has(task.key);
    select.setAttribute("aria-label", `选择：${task.title}`);
    select.addEventListener("change", () => options.onPick?.(task));
    row.append(select);
  }

  const box = document.createElement("input");
  box.type = "checkbox";
  box.className = "task-check";
  box.checked = Boolean(task.done);
  box.disabled = legacy;
  box.setAttribute("aria-label", legacy ? `旧任务只读：${task.title}`
    : `${task.done ? "取消完成" : "完成"}：${task.title}`);
  if (legacy) box.title = "旧任务仍在 任务（tasks）.md，应用内只读；可在 Obsidian 里修改";
  box.addEventListener("change", () => {
    if (legacy) return;
    options.onToggle(task, box.checked);
  });

  const title = document.createElement("span");
  title.className = "task-title";
  title.textContent = task.title;
  title.title = legacy
    ? "旧任务：在 任务（tasks）.md 中编辑（应用内只读）"
    : "双击可以改字";
  if (!legacy) title.addEventListener("dblclick", () => startRename(title, task, options));

  let legacyBadge = null;
  if (legacy) {
    legacyBadge = document.createElement("span");
    legacyBadge.className = "task-legacy-badge";
    legacyBadge.textContent = "旧";
    legacyBadge.title = "旧任务仍在 任务（tasks）.md，应用内只读；可在 Obsidian 里修改";
  }

  const flags = document.createElement("span");
  flags.className = "task-flags";
  if (task.pinned) {
    const pin = document.createElement("span");
    pin.className = "task-pin";
    pin.textContent = "📌";
    pin.title = "已置顶";
    flags.append(pin);
  }
  const meta = priorityMeta(task.priority);
  if (meta.flag) {
    const flag = document.createElement("span");
    flag.className = `task-flag prio-${task.priority}`;
    flag.textContent = meta.flag;
    flag.title = meta.label;
    flags.append(flag);
  }

  const tags = document.createElement("span");
  tags.className = "task-tags";
  for (const name of task.tags || []) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "task-tag-chip";
    chip.textContent = name;
    chip.title = `只看 #${name}`;
    chip.addEventListener("click", (event) => {
      event.stopPropagation();
      options.onSelectTag?.(name);
    });
    tags.append(chip);
  }

  const where = document.createElement("span");
  where.className = "task-meta";
  const parts = [];
  if (task.list_name) parts.push(task.list_name);
  if (task.date) parts.push(relativeDay(task.date, today));
  else if (task.done && task.done_on) parts.push(`完成于 ${task.done_on.slice(5)}`);
  if (task.time) parts.push(timeLabel(task.time));
  where.textContent = parts.join(" · ");

  const more = document.createElement("button");
  more.type = "button";
  more.className = "task-more";
  more.textContent = "…";
  more.setAttribute("aria-label", `${task.title} 的更多操作`);
  more.addEventListener("click", (event) => {
    event.stopPropagation();
    openTaskMenu(more, task, options);
  });

  let openButton = null;
  if (task.path && options.onOpenDocument) {
    openButton = document.createElement("button");
    openButton.type = "button";
    openButton.className = "task-open";
    openButton.textContent = "📄 文档";
    openButton.title = legacy ? "打开总任务文件（旧任务在 Obsidian 里编辑）" : "打开任务文档";
    openButton.setAttribute("aria-label", `${legacy ? "打开总任务文件" : "打开文档"}：${task.title}`);
    openButton.addEventListener("click", (event) => {
      event.stopPropagation();
      options.onOpenDocument(task);
    });
  }

  // 单击整行 = 选中它（多选模式下是加/取消）；点到勾选框、按钮、标签这些交互件时不动选中态。
  row.addEventListener("click", (event) => {
    if (event.target.closest("input, button, a")) return;
    options.onPick?.(task);
  });
  row.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    openTaskMenu(row, task, options);
  });
  row.addEventListener("keydown", (event) => {
    if (legacy) return;
    if (event.key === "Enter" && event.target === row) {
      event.preventDefault();
      options.onToggle(task, !task.done);
    }
  });

  row.append(box, ...(legacyBadge ? [legacyBadge] : []), title, flags, tags, where,
             ...(openButton ? [openButton] : []), more);
  return row;
}

/** 双击标题改字：就地换成输入框，回车/失焦保存，Esc 取消。 */
function startRename(title, task, options) {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "task-rename";
  input.value = task.title;
  input.maxLength = 200;
  input.setAttribute("aria-label", "改任务标题");
  title.replaceWith(input);
  input.focus();
  input.select();
  let settled = false;
  const finish = (save) => {
    if (settled) return;
    settled = true;
    if (save && input.value.trim() && input.value.trim() !== task.title) {
      options.onRename(task, input.value.trim());
    } else {
      input.replaceWith(title);
    }
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") finish(true);
    if (event.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
}

/** 画整块任务板（分组已由 tasks-model.groupTasks 算好）。 */
export function renderTaskBoard(container, groups, options) {
  container.replaceChildren();
  const total = groups.reduce((sum, groupData) => sum + groupData.tasks.length, 0);
  if (!total) {
    const empty = document.createElement("div");
    empty.className = "time-empty";
    const scoped = options.selection?.list || options.selection?.tag;
    empty.innerHTML = scoped
      ? '<p class="time-empty-title">这里还没有任务</p><p class="field-help">在上面输入框里添加，新任务会直接进这里。</p>'
      : '<p class="time-empty-title">没有任务</p><p class="field-help">点击上方输入框即可添加。</p>';
    container.append(empty);
    return;
  }
  for (const groupData of groups) {
    if (!groupData.tasks.length) continue;
    const section = document.createElement("section");
    section.className = "task-group";
    if (groupData.label) {
      const head = document.createElement("h4");
      head.className = "task-group-head";
      head.textContent = groupData.label;
      const count = document.createElement("span");
      count.className = "task-group-count";
      count.textContent = String(groupData.tasks.length);
      head.append(count);
      section.append(head);
    }
    const list = document.createElement("ul");
    list.className = "task-list";
    for (const task of groupData.tasks) list.append(taskRow(task, options));
    section.append(list);
    container.append(section);
  }
}

function menuRow(layer, label, onClick, extra = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "time-popover-row";
  button.innerHTML = `<span class="time-popover-label">${label}</span>${extra}`;
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    onClick();
  });
  layer.append(button);
  return button;
}

function menuGroup(layer, label) {
  const head = document.createElement("p");
  head.className = "time-popover-group";
  head.textContent = label;
  layer.append(head);
}

/**
 * 任务右键菜单（图 19，只做点名的那些）：日期 / 优先级 / 置顶 / 移动到 / 标签 / 创建副本 / 删除。
 * 「移动到」在菜单里就地展开清单树，不再套一层子菜单（更少点击）。
 */
export function openTaskMenu(anchor, task, options) {
  const { lists = [], tags = [], today, actions = {} } = options;
  openPopover(anchor, (layer, close) => {
    if (task.path && actions.openDocument) {
      menuRow(layer, task.legacy ? "📄 打开总任务文件" : "📄 打开文档", () => {
        close();
        actions.openDocument(task);
      });
    }
    // 旧任务只在 Obsidian 的总文件里编辑；应用内不提供任何写入入口。
    if (task.legacy) return;
    menuGroup(layer, "日期");
    const quick = document.createElement("div");
    quick.className = "time-popover-quick";
    const picks = [
      ["今天", today], ["明天", dayOffset(today, 1)], ["下周", dayOffset(today, 7)],
      ["选日期…", null], ["清除", ""],
    ];
    for (const [label, value] of picks) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `time-popover-quick-item${task.date === value ? " is-current" : ""}`;
      button.textContent = label;
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        if (value === null) {
          close();
          openDatePicker(anchor, task.date, today, (picked) => actions.setDate(task, picked));
        } else {
          close();
          actions.setDate(task, value);
        }
      });
      quick.append(button);
    }
    layer.append(quick);

    menuGroup(layer, "时间");
    const times = document.createElement("div");
    times.className = "time-popover-quick";
    const timePicks = [
      ["上午 9:00", "09:00"], ["中午 12:00", "12:00"], ["下午 14:00", "14:00"],
      ["晚上 19:00", "19:00"], ["选时间…", null], ["清除", ""],
    ];
    for (const [label, value] of timePicks) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `time-popover-quick-item${task.time === value ? " is-current" : ""}`;
      button.textContent = label;
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        if (value === null) {
          close();
          openTimePicker(anchor, task.time, (picked) => actions.setTime(task, picked));
        } else {
          close();
          actions.setTime(task, value);
        }
      });
      times.append(button);
    }
    layer.append(times);

    menuGroup(layer, "优先级");
    const flags = document.createElement("div");
    flags.className = "time-popover-flags";
    for (const item of PRIORITIES) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `time-popover-flag${task.priority === item.value ? " is-current" : ""}`;
      button.innerHTML = `<span class="task-flag prio-${item.value}" aria-hidden="true">${item.flag || "🏳"}</span>`;
      button.title = item.label;
      button.setAttribute("aria-label", item.label);
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        close();
        actions.setPriority(task, item.value);
      });
      flags.append(button);
    }
    layer.append(flags);

    menuRow(layer, `📌 ${task.pinned ? "取消置顶" : "置顶"}`, () => {
      close();
      actions.setPinned(task, !task.pinned);
    });
    const moveBox = document.createElement("div");
    moveBox.className = "time-popover-inline";
    moveBox.hidden = true;
    menuRow(layer, "📁 移动到", () => {
      moveBox.hidden = !moveBox.hidden;
    }, '<span class="time-popover-check" aria-hidden="true">›</span>');
    for (const entry of lists) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `time-popover-row is-nested${entry.name === task.list_name ? " is-current" : ""}`;
      button.innerHTML = `<span class="time-popover-label">${escapeHtml(entry.folder ? `${entry.folder} / ${entry.name}` : entry.name)}</span>` +
        `<span class="time-popover-check" aria-hidden="true">${entry.name === task.list_name ? "✓" : ""}</span>`;
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        close();
        actions.move(task, entry.name);
      });
      moveBox.append(button);
    }
    layer.append(moveBox);

    menuRow(layer, "🏷 标签", () => {
      close();
      openTagPicker(anchor, {
        selected: task.tags || [], tags,
        onSave: (names) => actions.setTags(task, names),
      });
    }, task.tags?.length ? `<span class="time-popover-note">${task.tags.length}</span>` : "");
    menuRow(layer, "⧉ 创建副本", () => {
      close();
      actions.duplicate(task);
    });
    menuRow(layer, "🗑 删除", () => {
      close();
      actions.remove(task);
    });
  });
}

/** 排序弹窗（图 16/17）：分组 / 排序 / 顺序——三项都是本机视图状态，不动数据。 */
export function openSortMenu(anchor, state, onPick) {
  openPopover(anchor, (layer, close) => {
    const section = (label, options, current, key) => {
      menuGroup(layer, label);
      for (const option of options) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `time-popover-row${option.value === current ? " is-current" : ""}`;
        button.innerHTML = `<span class="time-popover-label">${escapeHtml(option.label)}</span>` +
          `<span class="time-popover-check" aria-hidden="true">${option.value === current ? "✓" : ""}</span>`;
        button.addEventListener("click", () => {
          close();
          onPick({ [key]: option.value });
        });
        layer.append(button);
      }
    };
    section("分组", GROUP_OPTIONS.map((item) => ({ value: item.id, label: item.label })), state.group, "group");
    section("排序", SORT_OPTIONS.map((item) => ({ value: item.id, label: item.label })), state.sort, "sort");
    section("顺序", [{ value: "old", label: "从旧到新" }, { value: "new", label: "从新到旧" }], state.order, "order");
  });
}

/** 添加栏五个工具按钮的当前状态文字。 */
export function paintAddTools(labels) {
  const set = (selector, text) => {
    const button = document.querySelector(selector);
    if (button) button.textContent = text;
  };
  set("#time-tool-date", `📅 ${labels.date}`);
  set("#time-tool-time", `⏰ ${labels.time}`);
  set("#time-tool-priority", `🏳 ${labels.priority}`);
  set("#time-tool-tags", `🏷 ${labels.tags}`);
  set("#time-tool-list", `📥 ${labels.list}`);
}

/**
 * 日历里的任务条目（2026-10-11）：小条（月/多周格子里）、时间块（周/日/多日）、日程卡（日程视图）。
 * 三种形状共用同一套交互：勾选、双击改名、右键菜单（和任务行同一个 openTaskMenu）、按清单颜色着色。
 * 颜色是用户数据（清单定义里的），走内联变量 --item-color；theme-tokens 守卫只扫 styles.css。
 */
function taskItem(task, options, shape) {
  const { colors = {}, onToggle } = options;
  const legacy = Boolean(task.legacy);
  const item = document.createElement("div");
  item.className = `time-item time-${shape}${task.done ? " is-done" : ""}${legacy ? " is-legacy" : ""}`;
  item.dataset.taskKey = task.key;
  item.dataset.taskPath = task.path || "";
  const color = taskColor(task, colors);
  if (color) item.style.setProperty("--item-color", color);
  item.title = [task.title, timeLabel(task.time), task.list_name, legacy ? "旧任务（只读）" : ""]
    .filter(Boolean).join(" · ");

  const box = document.createElement("input");
  box.type = "checkbox";
  box.className = "time-item-check";
  box.checked = Boolean(task.done);
  box.disabled = legacy;
  box.setAttribute("aria-label", legacy ? `旧任务只读：${task.title}`
    : `${task.done ? "取消完成" : "完成"}：${task.title}`);
  box.addEventListener("change", () => { if (!legacy) onToggle?.(task, box.checked); });
  box.addEventListener("click", (event) => event.stopPropagation());

  const title = document.createElement("span");
  title.className = "time-item-title";
  title.textContent = task.title;
  title.title = legacy ? "旧任务：应用内只读" : "双击可以改字";
  if (!legacy) title.addEventListener("dblclick", () => startRename(title, task, options));

  const time = timeLabel(task.time);
  const when = document.createElement("span");
  when.className = "time-item-time";
  when.textContent = time;
  if (shape === "card") {
    item.append(box, when, title);
  } else {
    item.append(box, title, when);
  }
  item.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    openTaskMenu(item, task, options);
  });
  item.addEventListener("click", (event) => {
    if (event.target?.closest?.("input, button, a")) return;
    // 日历条目住在格子 / 列里：点条目本身不能顺带触发「切到日视图 / 那一刻加任务」
    event.stopPropagation?.();
    options.onPick?.(task);
  });
  return item;
}

/** 月视图 / 多周视图格子里的小条。 */
export function taskChip(task, options) {
  return taskItem(task, options, "chip");
}

/** 周 / 日 / 多日视图里的时间块。 */
export function taskBlock(task, options) {
  return taskItem(task, options, "block");
}

/** 日程视图里的卡片。 */
export function taskCard(task, options) {
  return taskItem(task, options, "card");
}
