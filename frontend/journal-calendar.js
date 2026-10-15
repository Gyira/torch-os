import { escapeHtml as esc } from "./core.js";
import { shiftDay } from "./journal-model.js";

// Read-only month calendar for daily records. Pure functions; no requests, no state.
const WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"];

export function monthKey(day) {
  return String(day || "").slice(0, 7);
}

export function shiftMonth(month, delta) {
  const [year, number] = String(month).split("-").map(Number);
  const index = year * 12 + (number - 1) + Number(delta);
  const nextYear = Math.floor(index / 12);
  const nextNumber = index - nextYear * 12 + 1;
  return `${String(nextYear).padStart(4, "0")}-${String(nextNumber).padStart(2, "0")}`;
}

export function monthBounds(month) {
  const [year, number] = String(month).split("-").map(Number);
  const daysInMonth = new Date(Date.UTC(year, number, 0)).getUTCDate();
  return { start: `${month}-01`, end: `${month}-${String(daysInMonth).padStart(2, "0")}` };
}

function mondayIndex(day) {
  return (new Date(`${day}T00:00:00Z`).getUTCDay() + 6) % 7;
}

export function buildJournalCalendar(month, today, recordedDays = []) {
  const { start, end } = monthBounds(month);
  const recorded = new Set((Array.isArray(recordedDays) ? recordedDays : [])
    .map((item) => (typeof item === "string" ? item : item?.date))
    .filter(Boolean));
  const weeks = [];
  let week = new Array(mondayIndex(start)).fill(null);
  let day = start;
  while (day <= end) {
    week.push({
      date: day,
      day: Number(day.slice(8)),
      recorded: recorded.has(day),
      today: day === today,
      future: day > today,
    });
    if (week.length === 7) {
      weeks.push(week);
      week = [];
    }
    day = shiftDay(day, 1);
  }
  if (week.length) {
    while (week.length < 7) week.push(null);
    weeks.push(week);
  }
  const recordedInMonth = [...recorded].filter((date) => date >= start && date <= end).length;
  return { month, start, end, today, weeks, recorded: recordedInMonth };
}

function cellLabel(cell) {
  const parts = [cell.date, cell.recorded ? "已记录" : "未记录"];
  if (cell.today) parts.push("今天");
  if (cell.future) parts.push("未来日期");
  return parts.join(" · ");
}

export function journalCalendarHtml(calendar) {
  const head = `<div class="journal-calendar-weekdays" aria-hidden="true">${WEEKDAY_LABELS.map((label) => `<span>${label}</span>`).join("")}</div>`;
  const cells = calendar.weeks.flat().map((cell) => {
    if (!cell) return '<span class="journal-calendar-cell blank" aria-hidden="true"></span>';
    if (cell.future) return `<span class="journal-calendar-cell future" aria-label="${esc(cellLabel(cell))}">${cell.day}</span>`;
    const classes = ["journal-calendar-cell"];
    if (cell.recorded) classes.push("recorded");
    if (cell.today) classes.push("today");
    return `<button class="${classes.join(" ")}" type="button" data-journal-calendar-day="${esc(cell.date)}" aria-label="${esc(cellLabel(cell))}">${cell.day}</button>`;
  }).join("");
  return head + `<div class="journal-calendar-grid" role="group" aria-label="${esc(calendar.month)} 的每日记录月历">${cells}</div>`;
}
