import { escapeHtml as esc } from "./core.js";
import { habitLabels, shiftDay } from "./growth-model.js";

// Read-only 30-day grid for one habit. Pure functions; no requests, no state.
export const HABIT_GRID_DAYS = 30;

function mondayIndex(day) {
  return (new Date(`${day}T00:00:00Z`).getUTCDay() + 6) % 7;
}

export function buildHabitGrid(entries, fromDay, toDay) {
  const byDate = new Map((Array.isArray(entries) ? entries : []).map((item) => [item.date, item]));
  const weeks = [];
  let week = new Array(mondayIndex(fromDay)).fill(null);
  let day = fromDay;
  while (day <= toDay) {
    const entry = byDate.get(day);
    week.push({
      date: day,
      status: entry?.status || "unrecorded",
      scheduled: Boolean(entry?.scheduled),
    });
    if (week.length === 7) { weeks.push(week); week = []; }
    day = shiftDay(day, 1);
  }
  if (week.length) {
    while (week.length < 7) week.push(null);
    weeks.push(week);
  }
  return { from: fromDay, to: toDay, weeks };
}

function cellLabel(cell) {
  return `${cell.date} ${cell.scheduled ? habitLabels[cell.status] || "未记录" : "非计划日"}`;
}

export function habitGridHtml(grid) {
  const cells = grid.weeks.flat();
  return `<p class="meta">近 30 天（${esc(grid.from)} 至 ${esc(grid.to)}）：每格一天，只表示你填写过的记录，空白不等于失败。</p>` +
    `<div class="habit-grid" role="list" aria-label="最近 30 天的习惯记录">` +
    cells.map((cell) => cell
      ? `<span class="habit-grid-cell ${cell.scheduled ? esc(cell.status) : "unscheduled"}" role="listitem" title="${esc(cellLabel(cell))}" aria-label="${esc(cellLabel(cell))}"></span>`
      : '<span class="habit-grid-cell blank" aria-hidden="true"></span>').join("") +
    `</div>`;
}
