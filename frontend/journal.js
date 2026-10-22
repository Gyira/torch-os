// 日记模块（工作台 2.0）：日期工具条。
// 结构化表单与归档页月历（0.97–0.99）已退役——日记以「今天的日记」（Markdown 文件）为准，
// 见 journal-note.js；记录模块（今天动过的文件）也在 journal-note.js 里一起渲染。
import { $, setStatus } from "./core.js";
import { shanghaiDay, validJournalDay } from "./journal-model.js";

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

export function initJournal() {
  const picker = $("#journal-date");
  if (!picker) return;
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
  $("#journal-today")?.addEventListener("click", () => openJournalDay(shanghaiDay()));
  $("#journal-reload")?.addEventListener("click", () => openJournalDay(picker.value));

}
