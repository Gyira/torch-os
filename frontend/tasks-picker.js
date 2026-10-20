// 时间管理模块的四个弹层（2026-10-08）：日期、优先级、标签、清单。
// 添加栏、任务右键菜单、移动到 都用同一套（照参考图 2/4/3/5：一套组件多处复用）。
// 全部无框架：一个浮动层 + 外点/Esc 关闭；返回 close() 方便调用方收尾。
import { escapeHtml } from "./core.js";
import {
  PRIORITIES, INBOX, dayOffset, priorityMeta,
} from "./tasks-model.js";

let activePopover = null;

/** 关掉当前弹层（供外部在切换页面 / 重渲染前调用）。 */
export function closePopover() {
  if (activePopover) activePopover();
}

/** 浮动层：贴着锚点显示，点外面或按 Esc 关掉；同一时刻只有一个。 */
export function openPopover(anchor, build) {
  closePopover();
  const layer = document.createElement("div");
  layer.className = "time-popover";
  layer.setAttribute("role", "dialog");
  const closer = () => {
    layer.remove();
    document.removeEventListener("mousedown", onOutside, true);
    document.removeEventListener("keydown", onKey, true);
    if (activePopover === closer) activePopover = null;
  };
  const onOutside = (event) => {
    if (!layer.contains(event.target)) closer();
  };
  const onKey = (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      closer();
    }
  };
  build(layer, closer);
  document.body.append(layer);
  const anchorRect = anchor.getBoundingClientRect();
  const rect = layer.getBoundingClientRect();
  const left = Math.min(anchorRect.left, window.innerWidth - rect.width - 8);
  const top = anchorRect.bottom + rect.height + 8 > window.innerHeight
    ? Math.max(8, anchorRect.top - rect.height - 6)
    : anchorRect.bottom + 6;
  layer.style.left = `${Math.max(8, left)}px`;
  layer.style.top = `${top}px`;
  document.addEventListener("mousedown", onOutside, true);
  document.addEventListener("keydown", onKey, true);
  activePopover = closer;
  return closer;
}

function rowButton(label, value, current, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `time-popover-row${value === current ? " is-current" : ""}`;
  button.innerHTML = `${label}<span class="time-popover-check" aria-hidden="true">${value === current ? "✓" : ""}</span>`;
  button.addEventListener("click", onClick);
  return button;
}

/** 优先级下拉（图 4）：高 / 中 / 低 / 无。 */
export function openPriorityPicker(anchor, current, onPick) {
  return openPopover(anchor, (layer, close) => {
    for (const item of PRIORITIES) {
      const label = `<span class="task-flag prio-${item.value}" aria-hidden="true">${item.flag || "🏳"}</span>${escapeHtml(item.label)}`;
      layer.append(rowButton(label, item.value, current, () => {
        close();
        onPick(item.value);
      }));
    }
  });
}

/** 日期组件（图 2 的简化版）：今天/明天/下周 + 月历 + 清除/确定。 */
export function openDatePicker(anchor, current, today, onPick) {
  let picked = current || "";
  return openPopover(anchor, (layer, close) => {
    let month = (picked || today).slice(0, 7);

    const quick = document.createElement("div");
    quick.className = "time-popover-quick";
    for (const [label, value] of [["今天", today], ["明天", dayOffset(today, 1)], ["下周", dayOffset(today, 7)]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "time-popover-quick-item";
      button.textContent = label;
      button.addEventListener("click", () => { picked = value; close(); onPick(value); });
      quick.append(button);
    }
    layer.append(quick);

    const grid = document.createElement("div");
    grid.className = "time-calendar";
    layer.append(grid);

    const draw = () => {
      const [year, monthIndex] = month.split("-").map(Number);
      const first = new Date(Date.UTC(year, monthIndex - 1, 1));
      const startWeekday = first.getUTCDay();
      const daysInMonth = new Date(Date.UTC(year, monthIndex, 0)).getUTCDate();
      const cells = [];
      cells.push(`<div class="time-calendar-head"><button type="button" data-month="-1" aria-label="上个月">‹</button>` +
        `<span>${year}年${monthIndex}月</span><button type="button" data-month="1" aria-label="下个月">›</button></div>`);
      cells.push('<div class="time-calendar-week">' + ["日", "一", "二", "三", "四", "五", "六"]
        .map((day) => `<span>${day}</span>`).join("") + "</div>");
      cells.push('<div class="time-calendar-grid">');
      for (let blank = 0; blank < startWeekday; blank += 1) cells.push('<span class="time-calendar-blank"></span>');
      for (let day = 1; day <= daysInMonth; day += 1) {
        const value = `${month}-${String(day).padStart(2, "0")}`;
        const classes = ["time-calendar-day"];
        if (value === today) classes.push("is-today");
        if (value === picked) classes.push("is-picked");
        cells.push(`<button type="button" class="${classes.join(" ")}" data-day="${value}">${day}</button>`);
      }
      cells.push("</div>");
      grid.innerHTML = cells.join("");
      grid.querySelectorAll("[data-month]").forEach((button) => {
        button.addEventListener("click", () => {
          const [y, m] = month.split("-").map(Number);
          const next = new Date(Date.UTC(y, m - 1 + Number(button.dataset.month), 1));
          month = next.toISOString().slice(0, 7);
          draw();
        });
      });
      grid.querySelectorAll("[data-day]").forEach((button) => {
        button.addEventListener("click", () => {
          picked = button.dataset.day;
          draw();
        });
      });
    };
    draw();

    const footer = document.createElement("div");
    footer.className = "time-popover-footer";
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "button secondary";
    clear.textContent = "清除";
    clear.addEventListener("click", () => { picked = ""; close(); onPick(""); });
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = "button primary";
    confirm.textContent = "确定";
    confirm.addEventListener("click", () => { close(); onPick(picked); });
    footer.append(clear, confirm);
    layer.append(footer);
  });
}

/**
 * 标签组件（图 3 / 21）：输入即搜、没有就“创建标签 X”、已建标签列表、保存/取消。
 * 保存时回调选中的标签名数组（新标签由调用方写进任务行——第 1 轮不需要先建定义）。
 */
export function openTagPicker(anchor, { selected = [], tags = [], onSave }) {
  const chosen = new Set(selected);
  return openPopover(anchor, (layer, close) => {
    const search = document.createElement("input");
    search.type = "search";
    search.className = "time-popover-search";
    search.placeholder = "输入标签";
    search.setAttribute("aria-label", "搜索或创建标签");

    const list = document.createElement("div");
    list.className = "time-popover-list";

    const names = tags.map((tag) => tag.name);
    const draw = () => {
      const query = search.value.trim().replace(/^#/, "");
      const matches = query ? names.filter((name) => name.toLowerCase().includes(query.toLowerCase())) : names;
      const rows = matches.map((name) => {
        const tag = tags.find((item) => item.name === name) || { name, color: "" };
        const button = document.createElement("button");
        button.type = "button";
        button.className = `time-popover-row${chosen.has(name) ? " is-current" : ""}`;
        // 颜色走 CSSOM：CSP 的 style-src 'self' 会拦掉 innerHTML 里的 style 属性。
        const dot = document.createElement("span");
        dot.className = "task-tag-dot";
        dot.setAttribute("aria-hidden", "true");
        if (tag.color) dot.style.setProperty("--tag-color", tag.color);
        const check = document.createElement("span");
        check.className = "time-popover-check";
        check.setAttribute("aria-hidden", "true");
        check.textContent = chosen.has(name) ? "✓" : "";
        button.append(dot, document.createTextNode(name), check);
        button.addEventListener("click", () => {
          if (chosen.has(name)) chosen.delete(name);
          else chosen.add(name);
          draw();
        });
        return button;
      });
      if (query && !names.includes(query)) {
        const create = document.createElement("button");
        create.type = "button";
        create.className = "time-popover-row is-create";
        create.textContent = `创建标签 “${query}”`;
        create.addEventListener("click", () => { chosen.add(query); search.value = ""; draw(); });
        rows.unshift(create);
      }
      if (!rows.length) {
        const empty = document.createElement("p");
        empty.className = "time-popover-empty field-help";
        empty.textContent = names.length ? "没找到匹配的标签。" : "还没有标签：输入名字就能建一个。";
        rows.push(empty);
      }
      list.replaceChildren(...rows);
    };
    search.addEventListener("input", draw);
    search.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      const query = search.value.trim().replace(/^#/, "");
      if (query) {
        chosen.add(query);
        search.value = "";
        draw();
      }
    });
    draw();

    const footer = document.createElement("div");
    footer.className = "time-popover-footer";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "button secondary";
    cancel.textContent = "取消";
    cancel.addEventListener("click", () => close());
    const save = document.createElement("button");
    save.type = "button";
    save.className = "button primary";
    save.textContent = "保存";
    save.addEventListener("click", () => { close(); onSave([...chosen]); });
    footer.append(cancel, save);

    layer.append(search, list, footer);
    search.focus();
  });
}

/** 清单选择（图 5）：收集箱 + 文件夹 > 清单的树，选中即回调。 */
export function openListPicker(anchor, { lists = [], current = "", onPick }) {
  return openPopover(anchor, (layer, close) => {
    const search = document.createElement("input");
    search.type = "search";
    search.className = "time-popover-search";
    search.placeholder = "搜索清单";
    search.setAttribute("aria-label", "搜索清单");
    const list = document.createElement("div");
    list.className = "time-popover-list";

    const draw = () => {
      const query = search.value.trim().toLowerCase();
      const rows = [];
      const push = (name, folder) => {
        if (query && !`${folder}${name}`.toLowerCase().includes(query)) return;
        const button = document.createElement("button");
        button.type = "button";
        button.className = `time-popover-row${name === current ? " is-current" : ""}`;
        button.innerHTML = `${folder ? '<span class="time-popover-folder" aria-hidden="true">▸</span>' : '<span class="time-popover-folder" aria-hidden="true">📥</span>'}` +
          `${escapeHtml(name)}<span class="time-popover-check" aria-hidden="true">${name === current ? "✓" : ""}</span>`;
        button.addEventListener("click", () => { close(); onPick(name); });
        rows.push(button);
      };
      const folders = [...new Set(lists.map((item) => item.folder).filter(Boolean))];
      for (const item of lists.filter((entry) => !entry.folder)) push(item.name, "");
      for (const folder of folders) {
        const head = document.createElement("p");
        head.className = "time-popover-group";
        head.textContent = folder;
        rows.push(head);
        for (const item of lists.filter((entry) => entry.folder === folder)) push(item.name, folder);
      }
      if (!rows.length) {
        const empty = document.createElement("p");
        empty.className = "time-popover-empty field-help";
        empty.textContent = "没有匹配的清单。";
        rows.push(empty);
      }
      list.replaceChildren(...rows);
    };
    search.addEventListener("input", draw);
    draw();
    layer.append(search, list);
    search.focus();
  });
}

/**
 * 时间组件（2026-10-11，日历那一轮）：快捷时刻（上午/中午/下午/晚上）+ 起止输入 + 清除/确定。
 * 结果是 `HH:MM` 或 `HH:MM-HH:MM`；只填开始 = 1 小时（时间轴上的块高）；清除 = 回到「全天」。
 */
export function openTimePicker(anchor, current, onPick) {
  const parsed = /^(\d{1,2}:\d{2})(?:-(\d{1,2}:\d{2}))?$/.exec(String(current || "").trim());
  let start = parsed ? parsed[1] : "";
  let end = parsed && parsed[2] ? parsed[2] : "";
  return openPopover(anchor, (layer, close) => {
    const quick = document.createElement("div");
    quick.className = "time-popover-quick";
    for (const [label, value] of [["上午 9:00", "09:00"], ["中午 12:00", "12:00"],
      ["下午 14:00", "14:00"], ["晚上 19:00", "19:00"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `time-popover-quick-item${start === value && !end ? " is-current" : ""}`;
      button.textContent = label;
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        close();
        onPick(value);
      });
      quick.append(button);
    }
    layer.append(quick);

    const row = document.createElement("div");
    row.className = "time-popover-times";
    const startInput = document.createElement("input");
    startInput.type = "time";
    startInput.className = "time-popover-input";
    startInput.value = start;
    startInput.setAttribute("aria-label", "开始时间");
    const endInput = document.createElement("input");
    endInput.type = "time";
    endInput.className = "time-popover-input";
    endInput.value = end;
    endInput.setAttribute("aria-label", "结束时间（留空 = 1 小时）");
    row.append(startInput, endInput);
    layer.append(row);

    const hint = document.createElement("p");
    hint.className = "time-popover-empty field-help";
    hint.textContent = "结束时间留空 = 默认 1 小时。";
    layer.append(hint);

    const footer = document.createElement("div");
    footer.className = "time-popover-footer";
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "button secondary";
    clear.textContent = "清除";
    clear.addEventListener("click", () => { close(); onPick(""); });
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = "button primary";
    confirm.textContent = "确定";
    confirm.addEventListener("click", () => {
      const startValue = startInput.value;
      const endValue = endInput.value;
      if (!startValue) { close(); onPick(""); return; }
      if (endValue && endValue <= startValue) {
        hint.textContent = "结束时间要晚于开始时间。";
        return;
      }
      close();
      onPick(endValue ? `${startValue}-${endValue}` : startValue);
    });
    footer.append(clear, confirm);
    layer.append(footer);
  });
}

/** 任务工具行上的文字：日期 / 时间 / 优先级 / 标签 / 清单 的当前状态。 */
export function toolLabels(pending, today) {
  const priority = priorityMeta(pending.priority);
  const time = String(pending.time || "");
  return {
    date: pending.date ? (pending.date === today ? "今天" : pending.date.slice(5)) : "日期",
    time: time ? time.replace(/^0/, "") : "时间",
    priority: priority.label,
    tags: pending.tags.length ? `${pending.tags.length} 个标签` : "标签",
    list: pending.list || INBOX,
  };
}
