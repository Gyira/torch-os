// 年视图 · 月视图 · 多周视图（2026-10-11，照滴答参考图）——同族：日期格子 + 格内任务条。
// 月视图：整月网格，任务条直接铺在格子里（装不下显示 +N）；点格子/日期数字 → 日视图；
//        悬停格子出现「＋」→ 给那天快速添加。
// 多周视图：同月视图，但只画连续 N 周（默认 2），行更高、任务显示更全。
// 年视图：12 个小月历，有任务的日子带热度底纹；点某天 → 回月视图并选中那天。
// 格子与日期串全用 tasks-model.js 的纯函数（monthCells / weekRangeCells / yearMonths）。
import {
  monthCells, weekRangeCells, yearMonths, tasksByDay, sortDayTasks,
} from "./tasks-model.js";
import { taskChip } from "./tasks.js";

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

/** 画格子家族（年 / 月 / 多周）——把容器清空后整块画。 */
export function renderCalendarGrid(container, context) {
  container.replaceChildren();
  const { view } = context.state;
  if (view === "year") renderYear(container, context);
  else renderGrid(container, context, view === "multiweek");
}

/** 月视图与多周视图：同一套格子，只是日期串和格高不同。 */
function renderGrid(container, context, multi) {
  const { tasks, today, state } = context;
  const anchor = state.anchor;
  const days = multi
    ? weekRangeCells(anchor, state.weeks, state.weekStart)
    : monthCells(anchor.slice(0, 7));
  const byDay = tasksByDay(tasks, { showDone: state.showDone });
  const itemOptions = { ...context.taskOptions, colors: context.colors };
  const outsideMonth = anchor.slice(0, 7);
  const limit = multi ? 8 : 4;

  const wrap = document.createElement("div");
  wrap.className = `time-grid${multi ? " is-multi" : ""}`;
  wrap.dataset.weeks = String(Math.ceil(days.length / 7));

  const weekdays = document.createElement("div");
  weekdays.className = "time-grid-weekdays";
  for (let index = 0; index < 7; index += 1) {
    const cell = document.createElement("span");
    cell.textContent = WEEKDAYS[(Number(state.weekStart) + index) % 7];
    weekdays.append(cell);
  }
  wrap.append(weekdays);

  const body = document.createElement("div");
  body.className = "time-grid-body";
  for (const day of days) {
    const cell = document.createElement("div");
    cell.className = "time-cell";
    cell.dataset.day = day;
    if (!multi && day.slice(0, 7) !== outsideMonth) cell.classList.add("is-outside");
    if (day === today) cell.classList.add("is-today");
    const dayTasks = sortDayTasks(byDay[day] || []);
    if (dayTasks.some((task) => !task.done && day < today)) cell.classList.add("is-overdue");

    const head = document.createElement("div");
    head.className = "time-cell-head";
    const num = document.createElement("button");
    num.type = "button";
    num.className = "time-cell-num";
    num.textContent = String(Number(day.slice(8)));
    num.title = `看 ${day} 这一天`;
    num.setAttribute("aria-label", `看 ${day}`);
    num.addEventListener("click", (event) => {
      event.stopPropagation();
      context.onOpenDay(day);
    });
    const add = document.createElement("button");
    add.type = "button";
    add.className = "time-cell-add";
    add.textContent = "＋";
    add.title = `在 ${day} 加任务`;
    add.setAttribute("aria-label", `在 ${day} 加任务`);
    add.addEventListener("click", (event) => {
      event.stopPropagation();
      context.onQuickAdd(day);
    });
    head.append(num, add);

    const list = document.createElement("div");
    list.className = "time-cell-items";
    for (const task of dayTasks.slice(0, limit)) list.append(taskChip(task, itemOptions));
    if (dayTasks.length > limit) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "time-cell-more";
      more.textContent = `+${dayTasks.length - limit}`;
      more.title = `还有 ${dayTasks.length - limit} 项，点进日视图看`;
      more.addEventListener("click", (event) => {
        event.stopPropagation();
        context.onOpenDay(day);
      });
      list.append(more);
    }

    cell.append(head, list);
    // 点格子空白 = 去那一天的日视图（照参考图：月视图看宏观，细节交给日视图）
    cell.addEventListener("click", () => context.onOpenDay(day));
    body.append(cell);
  }
  wrap.append(body);
  container.append(wrap);
}

/** 年视图：12 个小月历（4 列 × 3 行），热度底纹 + 今天描边。 */
function renderYear(container, context) {
  const { tasks, today, state } = context;
  const byDay = tasksByDay(tasks, { showDone: state.showDone });
  const year = Number(state.anchor.slice(0, 4));

  const grid = document.createElement("div");
  grid.className = "time-year";
  for (const month of yearMonths(year)) {
    const box = document.createElement("section");
    box.className = "time-year-month";
    const head = document.createElement("h4");
    head.textContent = month.title;
    box.append(head);

    const week = document.createElement("div");
    week.className = "time-year-week";
    for (let index = 0; index < 7; index += 1) {
      const cell = document.createElement("span");
      cell.textContent = WEEKDAYS[(Number(state.weekStart) + index) % 7];
      week.append(cell);
    }
    box.append(week);

    const cells = document.createElement("div");
    cells.className = "time-year-cells";
    for (const day of month.cells) {
      const count = (byDay[day] || []).length;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "time-year-day";
      button.dataset.day = day;
      if (day.slice(0, 7) !== month.month) button.classList.add("is-outside");
      if (day === today) button.classList.add("is-today");
      if (count) button.classList.add(`heat-${Math.min(count, 3)}`);
      button.textContent = String(Number(day.slice(8)));
      button.title = count ? `${day}：${count} 项任务` : day;
      button.setAttribute("aria-label", `${day}${count ? `，${count} 项任务` : ""}`);
      button.addEventListener("click", () => context.onState({ view: "month", anchor: day }));
      cells.append(button);
    }
    box.append(cells);
    grid.append(box);
  }
  container.append(grid);

  const hint = document.createElement("p");
  hint.className = "time-year-hint field-help";
  hint.textContent = "点某一天回到月视图；颜色越深的格子任务越多。";
  container.append(hint);
}
