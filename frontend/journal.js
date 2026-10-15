// 日记模块（工作台 2.0）：日期工具条 + 归档页的月历。
// 结构化表单（0.97–0.99）已退役——日记以「今天的日记」（Markdown 文件）为准，见 journal-note.js；
// 记录模块（今天动过的文件）也在 journal-note.js 里一起渲染。
import { $, api, setStatus } from "./core.js";
import { shanghaiDay, validJournalDay } from "./journal-model.js";
import { buildJournalCalendar, journalCalendarHtml, monthBounds, monthKey, shiftMonth } from "./journal-calendar.js";
import { initBodyRecords } from "./body-records.js";

let calendarId = 0;
let calendarMonth = null;

/** 选定某天：更新日期工具条并广播 journal-day-changed（笔记/记录模块跟着换天）。 */
export function openJournalDay(day) {
  const target = validJournalDay(day) ? day : shanghaiDay();
  const picker = $("#journal-date");
  if (picker) {
    picker.value = target;
    picker.max = shanghaiDay();
  }
  document.dispatchEvent(new CustomEvent("journal-day-changed", { detail: { day: target } }));
  return target;
}

export async function loadJournalCalendar(month) {
  const today = shanghaiDay();
  const target = month || calendarMonth || monthKey(today);
  calendarMonth = target;
  const request = ++calendarId;
  const { start, end } = monthBounds(target);
  const boundedEnd = end > today ? today : end;
  const render = (records, message, level) => {
    const calendar = buildJournalCalendar(target, today, records);
    $("#journal-calendar-grid").innerHTML = journalCalendarHtml(calendar);
    $("#journal-calendar-label").textContent = `${target} · 已记录 ${calendar.recorded} 天`;
    $("#journal-calendar-next").disabled = shiftMonth(target, 1) > monthKey(today);
    $("#journal-calendar-current").disabled = target === monthKey(today);
    setStatus("#journal-calendar-status", message, level);
  };
  try {
    const records = boundedEnd < start ? [] : await api(`/api/journal?from_date=${start}&to_date=${boundedEnd}`);
    if (request !== calendarId) return;
    render(Array.isArray(records) ? records : [], "");
  } catch (error) {
    if (request !== calendarId) return;
    render([], `月历读取失败：${error.message} 已保留其他功能，可点“刷新月历”重试。`, "error");
  }
}

export function initJournal() {
  initBodyRecords();
  const picker = $("#journal-date");
  picker.value = shanghaiDay();
  picker.max = shanghaiDay();
  picker.addEventListener("change", () => {
    if (!validJournalDay(picker.value)) {
      picker.value = shanghaiDay();
      setStatus("#journal-note-status", "请选择有效日期，不能看未来日期。", "error");
      return;
    }
    openJournalDay(picker.value);
  });
  $("#journal-today").addEventListener("click", () => openJournalDay(shanghaiDay()));
  $("#journal-reload").addEventListener("click", () => openJournalDay(picker.value));

  // 归档页的月历（brain → 日记归档 在用）。
  $("#journal-calendar-prev").addEventListener("click", () => loadJournalCalendar(shiftMonth(calendarMonth || monthKey(shanghaiDay()), -1)));
  $("#journal-calendar-next").addEventListener("click", () => loadJournalCalendar(shiftMonth(calendarMonth || monthKey(shanghaiDay()), 1)));
  $("#journal-calendar-current").addEventListener("click", () => loadJournalCalendar(monthKey(shanghaiDay())));
  $("#journal-calendar-refresh").addEventListener("click", () => loadJournalCalendar(calendarMonth || monthKey(shanghaiDay())));
  $("#journal-calendar-grid").addEventListener("click", (event) => {
    const button = event.target.closest("[data-journal-calendar-day]");
    if (!button) return;
    openJournalDay(button.dataset.journalCalendarDay);
    location.hash = "workbench";  // 回到工作台，看那一天的日记
  });
  $("#body-history").addEventListener("click", (event) => {
    const button = event.target.closest("[data-journal-day]");
    if (button) {
      openJournalDay(button.dataset.journalDay);
      location.hash = "identity";
    }
  });
}
