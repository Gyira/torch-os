// 周视图 · 日视图 · 多日视图（2026-10-11，照滴答参考图）——同族：左侧时刻轴 + 全天区 + 按时间摆放的任务块。
// 结构（自上而下）：星期表头 / 全天区（没有时间的任务）/ 可滚动的时刻网格；
// 凌晨的空白时段折成一行（照参考图的「00:00 – 07:00」），里面的任务用小条代替方块；
// 有时间的任务按 开始时间 摆放、块高 = 时长（只填开始 = 1 小时），重叠的并排分列；
// 点某天某时刻的空白 → 快速添加并预填那个时间。摆放算法是 tasks-model.layoutBlocks（纯函数）。
import {
  weekCells, dayCells, tasksByDay, sortDayTasks, layoutBlocks, firstBusyHour,
  timeToMinutes, minutesToLabel,
} from "./tasks-model.js";
import { taskChip, taskBlock } from "./tasks.js";

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];
const HOUR_HEIGHT = 48;   // 每小时多少像素（写进 --hour-h，样式表的行线跟着它走）
const MIN_BLOCK = 20;     // 时间块最矮多高（15 分钟的任务也要点得到）

/** 画时间轴家族（周 / 日 / 多日）。 */
export function renderCalendarTime(container, context) {
  container.replaceChildren();
  const { tasks, today, state } = context;
  const { view } = state;
  const days = view === "day" ? [state.anchor]
    : view === "week" ? weekCells(state.anchor, state.weekStart)
      : dayCells(state.anchor, state.days);
  const byDay = tasksByDay(tasks, { showDone: state.showDone });
  const itemOptions = { ...context.taskOptions, colors: context.colors };

  // 折叠带：从 00:00 到第一个有任务的整点（没有任务 = 折到 7:00）
  const firstHour = firstBusyHour(days.flatMap((day) => byDay[day] || []));
  const bandEnds = firstHour > 0;
  const hoursHeight = (24 - firstHour) * HOUR_HEIGHT;

  const wrap = document.createElement("div");
  wrap.className = `time-timegrid${view === "day" ? " is-day" : ""}`;
  wrap.style.setProperty("--hour-h", `${HOUR_HEIGHT}px`);

  // ---- 星期表头 ----
  const head = document.createElement("div");
  head.className = "time-timegrid-head";
  const corner = document.createElement("span");
  corner.className = "time-timegrid-corner";
  head.append(corner);
  for (const day of days) {
    const cell = document.createElement("div");
    cell.className = `time-day-head${day === today ? " is-today" : ""}`;
    cell.dataset.day = day;
    const week = document.createElement("span");
    week.className = "time-day-week";
    week.textContent = `周${WEEKDAYS[new Date(Date.UTC(
      Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8)))).getUTCDay()]}`;
    const num = document.createElement("button");
    num.type = "button";
    num.className = "time-day-num";
    num.textContent = String(Number(day.slice(8)));
    num.title = `看 ${day} 这一天`;
    num.setAttribute("aria-label", `看 ${day}`);
    num.addEventListener("click", () => context.onOpenDay(day));
    const add = document.createElement("button");
    add.type = "button";
    add.className = "time-day-add";
    add.textContent = "＋";
    add.title = `在 ${day} 加任务`;
    add.setAttribute("aria-label", `在 ${day} 加任务`);
    add.addEventListener("click", () => context.onQuickAdd(day));
    cell.append(week, num, add);
    head.append(cell);
  }
  wrap.append(head);

  // ---- 全天区（没有时间的任务）----
  const allDay = document.createElement("div");
  allDay.className = "time-timegrid-allday";
  const allDayLabel = document.createElement("span");
  allDayLabel.className = "time-allday-label";
  allDayLabel.textContent = "全天";
  allDay.append(allDayLabel);
  for (const day of days) {
    const col = document.createElement("div");
    col.className = "time-allday-col";
    col.dataset.day = day;
    for (const task of sortDayTasks(byDay[day] || [])) {
      if (timeToMinutes(task.time)) continue;
      col.append(taskChip(task, itemOptions));
    }
    allDay.append(col);
  }
  wrap.append(allDay);

  // ---- 可滚动的时刻网格 ----
  const scroll = document.createElement("div");
  scroll.className = "time-timegrid-scroll";
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", "时刻表");

  if (bandEnds) {
    const band = document.createElement("div");
    band.className = "time-band";
    const label = document.createElement("span");
    label.className = "time-band-label";
    // 折叠带自己标出这段区间（照参考图的「00:00 / -07:00」两行写法）：
    // 槽只有 56px 宽，写成一行会挤爆——写成两行，下面第一个时刻标签就不要再重复这个整点（见 axis）
    label.textContent = `00:00 -${minutesToLabel(firstHour * 60)}`;
    label.title = `00:00 - ${minutesToLabel(firstHour * 60)}（空白时段，折起来了）`;
    band.append(label);
    for (const day of days) {
      const cell = document.createElement("div");
      cell.className = "time-band-col";
      for (const task of sortDayTasks(byDay[day] || [])) {
        const span = timeToMinutes(task.time);
        if (!span || Math.floor(span.start / 60) >= firstHour) continue;
        cell.append(taskChip(task, itemOptions));
      }
      band.append(cell);
    }
    scroll.append(band);
  }

  const inner = document.createElement("div");
  inner.className = "time-timegrid-inner";

  const axis = document.createElement("div");
  axis.className = "time-axis";
  for (let hour = firstHour; hour < 24; hour += 1) {
    const label = document.createElement("span");
    label.className = "time-axis-label";
    label.textContent = hour === 12 ? "正午" : minutesToLabel(hour * 60);
    // 折叠带末尾已经写了这个整点（-07:00），这一格留位置但不重复写一遍
    if (bandEnds && hour === firstHour) label.classList.add("is-folded");
    axis.append(label);
  }
  inner.append(axis);

  for (const day of days) {
    const col = document.createElement("div");
    col.className = "time-col";
    col.dataset.day = day;
    col.style.height = `${hoursHeight}px`;
    if (day === today) col.classList.add("is-today");
    for (const block of layoutBlocks(byDay[day] || [])) {
      if (Math.floor(block.start / 60) < firstHour) continue;   // 折叠带里的已经在上面用小条画过
      const item = taskBlock(block.task, itemOptions);
      item.style.top = `${((block.start - firstHour * 60) / 60) * HOUR_HEIGHT}px`;
      item.style.height = `${Math.max(MIN_BLOCK, ((block.end - block.start) / 60) * HOUR_HEIGHT)}px`;
      item.style.left = `${(block.column / block.columns) * 100}%`;
      item.style.width = `${(1 / block.columns) * 100}%`;
      col.append(item);
    }
    // 点空白（半小时对齐）→ 快速添加并预填那个时刻
    col.addEventListener("click", (event) => {
      if (event.target !== col) return;
      const rect = col.getBoundingClientRect();
      const offset = Number(event.clientY ?? 0) - Number(rect.top || 0);
      const raw = firstHour * 60 + (offset / HOUR_HEIGHT) * 60;
      const snapped = Math.max(0, Math.min(23 * 60 + 30, Math.round(raw / 30) * 30));
      context.onQuickAdd(day, minutesToLabel(snapped));
    });
    inner.append(col);
  }
  scroll.append(inner);
  wrap.append(scroll);
  container.append(wrap);

  // 打开时滚到第一个任务前面一点（没有任务就从 8:00 开始看）
  let firstBusy = 24;
  for (const day of days) {
    for (const task of byDay[day] || []) {
      const span = timeToMinutes(task.time);
      if (span) firstBusy = Math.min(firstBusy, Math.floor(span.start / 60));
    }
  }
  const targetHour = firstBusy === 24 ? 8 : Math.min(Math.max(firstHour, firstBusy - 1), 20);
  scroll.scrollTop = Math.max(0, (targetHour - firstHour) * HOUR_HEIGHT);
}
