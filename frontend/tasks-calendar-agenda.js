// 日程视图（2026-10-11，照滴答参考图）：从锚点那天起连续 14 天，按天分组的竖排列表。
// 每天：日期 + 星期 + 「＋」→ 每行 = 左侧时刻 + 竖线上的圆点 + 任务卡（时间区间 + 标题，按清单颜色描边）。
// 没时间的任务排当天最上面（标「全天」）；只列有任务的天，但今天永远留着（哪怕空着）。
import { dayCells, tasksByDay, sortDayTasks, timeToMinutes, clockLabel } from "./tasks-model.js";
import { taskCard } from "./tasks.js";

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const AGENDA_DAYS = 14;   // 一次连续看两周；箭头按周翻

/** 画日程视图。 */
export function renderAgenda(container, context) {
  container.replaceChildren();
  const { tasks, today, state } = context;
  const days = dayCells(state.anchor, AGENDA_DAYS);
  const byDay = tasksByDay(tasks, { showDone: state.showDone });
  const itemOptions = { ...context.taskOptions, colors: context.colors };

  const wrap = document.createElement("div");
  wrap.className = "time-agenda";
  for (const day of days) {
    const dayTasks = sortDayTasks(byDay[day] || []);
    if (!dayTasks.length && day !== today) continue;

    const section = document.createElement("section");
    section.className = `time-agenda-day${day === today ? " is-today" : ""}`;
    section.dataset.day = day;

    const head = document.createElement("header");
    head.className = "time-agenda-head";
    const num = document.createElement("strong");
    num.className = "time-agenda-num";
    num.textContent = String(Number(day.slice(8)));
    const week = document.createElement("span");
    week.className = "time-agenda-week";
    week.textContent = `周${WEEKDAYS[new Date(Date.UTC(
      Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8)))).getUTCDay()].slice(1)}`;
    const add = document.createElement("button");
    add.type = "button";
    add.className = "time-agenda-add";
    add.textContent = "＋";
    add.title = `在 ${day} 加任务`;
    add.setAttribute("aria-label", `在 ${day} 加任务`);
    add.addEventListener("click", () => context.onQuickAdd(day));
    head.append(num, week, add);
    section.append(head);

    const body = document.createElement("div");
    body.className = "time-agenda-body";
    if (!dayTasks.length) {
      const empty = document.createElement("p");
      empty.className = "time-agenda-empty field-help";
      empty.textContent = "今天还没有任务。";
      body.append(empty);
    }
    for (const task of dayTasks) {
      const span = timeToMinutes(task.time);
      const row = document.createElement("div");
      row.className = "time-agenda-row";
      const label = document.createElement("span");
      label.className = "time-agenda-time";
      label.textContent = span ? clockLabel(span.start) : "全天";
      const track = document.createElement("div");
      track.className = "time-agenda-track";
      const dot = document.createElement("span");
      dot.className = "time-agenda-dot";
      track.append(dot, taskCard(task, itemOptions));
      row.append(label, track);
      body.append(row);
    }
    section.append(body);
    wrap.append(section);
  }
  container.append(wrap);
}
