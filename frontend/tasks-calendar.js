// 日历页外壳（2026-10-08 第 3 轮做月视图；2026-10-11 第 4 轮扩成七种视图，照滴答参考图）。
// 右上角一组控件：＋ 快速添加 / 视图下拉（年·月·周·日·日程·多日·多周）/ ‹ 今天 › / …（显示设置）。
// 本文件只管外壳与分发——怎么画在各自的视图文件里：
//   tasks-calendar-grid.js    年视图 · 月视图 · 多周视图（日期格 + 格内任务条）
//   tasks-calendar-time.js    周视图 · 日视图 · 多日视图（时刻轴 + 全天区 + 任务块）
//   tasks-calendar-agenda.js  日程视图（按天分组的竖排列表）
// 任务条目的形态（小条 / 时间块 / 日程卡）在 tasks.js（taskChip / taskBlock / taskCard）。
// 视图状态（看哪个视图、锚点哪天、档位、周起始、是否显示已完成）由 time-module 存本机。
import {
  CALENDAR_VIEWS, MULTIDAY_OPTIONS, MULTIWEEK_OPTIONS, calendarTitle, calendarViewLabel,
  defaultCalendarState, shiftAnchor, listColorMap,
} from "./tasks-model.js";
import { renderCalendarGrid } from "./tasks-calendar-grid.js";
import { renderCalendarTime } from "./tasks-calendar-time.js";
import { renderAgenda } from "./tasks-calendar-agenda.js";
import { openPopover } from "./tasks-picker.js";
import { openQuickAdd } from "./tasks-dialogs.js";

/**
 * 画日历页。
 * @param {object} options
 *   tasks / today / lists：后端板面数据
 *   state：{view, anchor, days, weeks, weekStart, showDone}（缺字段用默认值补齐）
 *   taskOptions：交给日历条目那套（勾选 / 改名 / 右键菜单回调）
 *   onState(patch)：更新本机状态并重画（翻页、切视图、改显示设置都走这里）
 *   onAdd({title, date, time, list, priority})：快速添加（写回由 time-module 做）
 */
export function renderCalendar(container, options = {}) {
  const {
    tasks = [], today = "", lists = [], state = {}, taskOptions = {},
    onState = () => {}, onAdd = () => {},
  } = options;
  const current = { ...defaultCalendarState(today), ...state };
  if (!current.anchor) current.anchor = today;

  container.replaceChildren();
  container.classList.add("time-cal-page");

  const head = document.createElement("header");
  head.className = "time-head time-cal-head";
  const title = document.createElement("h3");
  title.id = "time-cal-title";
  title.textContent = calendarTitle(current);

  const controls = document.createElement("div");
  controls.className = "time-cal-controls";

  const add = document.createElement("button");
  add.type = "button";
  add.className = "text-button time-cal-add-btn";
  add.textContent = "＋";
  add.title = "添加任务";
  add.setAttribute("aria-label", "添加任务");
  add.addEventListener("click", () => quickAdd(current.anchor));

  // 视图一排按钮（2026-10-11 用户反馈：下拉太麻烦，直接做成能点的一排，照参考图底部那排）
  const views = document.createElement("div");
  views.className = "time-cal-views";
  views.setAttribute("role", "tablist");
  views.setAttribute("aria-label", "日历视图");
  for (const view of CALENDAR_VIEWS) {
    const active = current.view === view.id;
    const button = document.createElement("button");
    button.type = "button";
    button.className = `time-cal-view${active ? " is-active" : ""}`;
    button.dataset.calView = view.id;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", active ? "true" : "false");
    // 短名（年/月/周/日/日程/多日/多周）：再加一排档位也不至于把右上角控件挤到第二行；
    // 全名留在悬停提示与朗读里（照参考图的“年视图/月视图/…”，但更紧凑）
    button.textContent = view.label.replace(/视图$/, "");
    button.title = active ? calendarViewLabel(current) : calendarViewLabel({ ...current, view: view.id });
    button.setAttribute("aria-label", view.label);
    button.addEventListener("click", () => onState({ view: view.id }));
    views.append(button);
    if (!active) continue;
    // 多日 / 多周：档位小按钮直接跟在旁边（2/3/5/7 天、2/3/4 周），同样是点一下就切
    const multi = view.id === "multiday" ? "days" : view.id === "multiweek" ? "weeks" : "";
    if (!multi) continue;
    const box = document.createElement("div");
    box.className = "time-cal-counts";
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", view.id === "multiday" ? "显示几天" : "显示几周");
    for (const value of multi === "days" ? MULTIDAY_OPTIONS : MULTIWEEK_OPTIONS) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = `time-cal-count${current[multi] === value ? " is-active" : ""}`;
      chip.textContent = `${value} ${multi === "days" ? "天" : "周"}`;
      chip.addEventListener("click", () => onState({ view: view.id, [multi]: value }));
      box.append(chip);
    }
    views.append(box);
  }

  const nav = document.createElement("div");
  nav.className = "time-cal-nav";
  const back = document.createElement("button");
  back.type = "button";
  back.className = "text-button";
  back.textContent = "‹";
  back.setAttribute("aria-label", "上一页");
  back.addEventListener("click", () => step(-1));
  const todayButton = document.createElement("button");
  todayButton.type = "button";
  todayButton.className = "text-button";
  todayButton.textContent = "今天";
  todayButton.addEventListener("click", () => onState({ anchor: today }));
  const forward = document.createElement("button");
  forward.type = "button";
  forward.className = "text-button";
  forward.textContent = "›";
  forward.setAttribute("aria-label", "下一页");
  forward.addEventListener("click", () => step(1));
  nav.append(back, todayButton, forward);

  const more = document.createElement("button");
  more.type = "button";
  more.className = "text-button";
  more.textContent = "…";
  more.title = "显示设置";
  more.setAttribute("aria-label", "显示设置");
  more.addEventListener("click", () => openMoreMenu(more));

  controls.append(add, nav, more);
  head.append(title, views, controls);
  container.append(head);

  const host = document.createElement("section");
  host.className = "time-cal-host";
  host.setAttribute("aria-label", calendarViewLabel(current));
  container.append(host);

  // 视图渲染器共用的上下文
  const context = {
    tasks, today, lists, state: current, taskOptions,
    colors: listColorMap(lists),
    onState,
    onOpenDay: (day) => onState({ view: "day", anchor: day }),
    onQuickAdd: (day, time = "") => quickAdd(day, time),
  };
  if (current.view === "year" || current.view === "month" || current.view === "multiweek") {
    renderCalendarGrid(host, context);
  } else if (current.view === "agenda") {
    renderAgenda(host, context);
  } else {
    renderCalendarTime(host, context);
  }

  function step(direction) {
    onState({ anchor: shiftAnchor(current.anchor, current.view, direction, current) });
  }

  function quickAdd(day, time = "") {
    openQuickAdd({ day, time, lists, onSave: (draft) => onAdd(draft) });
  }

  /** 「…」显示设置：显示已完成任务 / 周起始日（周日或周一开头）。 */
  function openMoreMenu(anchor) {
    openPopover(anchor, (layer, close) => {
      const group = (label) => {
        const head = document.createElement("p");
        head.className = "time-popover-group";
        head.textContent = label;
        layer.append(head);
      };
      const row = (label, marked, onClick) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `time-popover-row${marked ? " is-current" : ""}`;
        button.innerHTML = `<span class="time-popover-label">${label}</span>` +
          `<span class="time-popover-check" aria-hidden="true">${marked ? "✓" : ""}</span>`;
        button.addEventListener("click", () => { close(); onClick(); });
        layer.append(button);
      };
      group("显示");
      row("显示已完成任务", current.showDone, () => onState({ showDone: !current.showDone }));
      group("周起始日");
      row("周日开头", current.weekStart === 0, () => onState({ weekStart: 0 }));
      row("周一开头", current.weekStart === 1, () => onState({ weekStart: 1 }));
    });
  }
}
