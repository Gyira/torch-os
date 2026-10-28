// 运势黄历组件（2026-10-27）：右栏小卡片 + 详细浮窗 + 每日签到。
// 数据层在 almanac.js（纯函数、node 可测）；这里只做 DOM、事件和签到请求。
// 关键交互：未签到只显示签到入口；签到后小卡片只保留干支、农历日期与运势摘要；
// 点卡片打开浮窗，完整黄历和年/月/日切换都在浮窗里。卡片仍可被 widget-drag.js 整卡拖动。
import { $, api, openDialog, toast } from "./core.js";
import { shanghaiDay } from "./journal-model.js";
import {
  ALMANAC_MAX_YEAR, ALMANAC_MIN_YEAR, almanacCardHtml, almanacDialogHtml, almanacForDay,
} from "./almanac.js";

let cardState = null;
let modalState = null;

function pad2(value) {
  return String(value).padStart(2, "0");
}

function parseDay(day) {
  const [year, month, date] = String(day).split("-").map(Number);
  return { year, month, date };
}

function formatDay(year, month, date) {
  return `${year}-${pad2(month)}-${pad2(date)}`;
}

function daysInMonth(year, month) {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function shiftDay(day, delta) {
  const { year, month, date } = parseDay(day);
  const shifted = new Date(Date.UTC(year, month - 1, date + delta));
  return formatDay(shifted.getUTCFullYear(), shifted.getUTCMonth() + 1, shifted.getUTCDate());
}

function inRange(year) {
  return Number(year) >= ALMANAC_MIN_YEAR && Number(year) <= ALMANAC_MAX_YEAR;
}

function clampYear(value) {
  return Math.min(ALMANAC_MAX_YEAR, Math.max(ALMANAC_MIN_YEAR, Number(value) || ALMANAC_MIN_YEAR));
}

function setCard(day, { checkedIn, checkedAt = null } = {}) {
  if (!cardState) return;
  cardState = { ...cardState, day, checkedIn, checkedAt };
  const element = $("#workbench-almanac");
  if (!element) return;
  const data = almanacForDay(day);
  element.innerHTML = almanacCardHtml(data, {
    checkedIn, name: cardState.name, progress: cardState.progress,
  });
  element.classList.toggle("is-locked", !checkedIn);
  element.classList.toggle("is-unlocked", checkedIn);
  element.setAttribute("aria-label", checkedIn ? "今日黄历，点击查看详情" : "今日运势签到");
}

export function renderAlmanac(element, payload) {
  if (!element) return;
  const day = String(payload?.day || shanghaiDay());
  const name = String(payload?.name || "");
  const progress = payload?.progress || null;
  const fortune = payload?.fortune || {};
  cardState = {
    day,
    name,
    progress,
    checkedIn: Boolean(fortune.checked_in),
    checkedAt: fortune.checked_at || null,
  };
  setCard(day, { checkedIn: cardState.checkedIn, checkedAt: cardState.checkedAt });
}

async function checkIn(button) {
  if (!cardState || cardState.checkedIn) return;
  const day = shanghaiDay();
  button.disabled = true;
  button.textContent = "签到中…";
  try {
    const result = await api(`/api/fortune/checkins/${encodeURIComponent(day)}`, { method: "POST" });
    const checkedDay = String(result?.day || day);
    setCard(checkedDay, { checkedIn: true, checkedAt: result?.checked_at || null });
    toast("签到成功，今日运势已解锁。", "success");
    $("#workbench-almanac [data-almanac-unlocked]")?.focus({ preventScroll: true });
  } catch (error) {
    button.disabled = false;
    button.textContent = "签到查看今日运势";
    toast(`签到失败：${error.message}`, "error");
  }
}

function paintDialog() {
  if (!cardState || !modalState) return;
  const today = shanghaiDay();
  const data = almanacForDay(modalState.day);
  const progress = modalState.day === today ? cardState.progress : null;
  $("#detail-dialog")?.classList.add("almanac-mode");
  openDialog(`黄历 · ${data.solar.year}年${data.solar.monthDayLabel}`, almanacDialogHtml(data, {
    name: cardState.name,
    progress,
    viewYear: modalState.viewYear,
    viewMonth: modalState.viewMonth,
    today,
  }));
}

function openDetail() {
  if (!cardState?.checkedIn) {
    $("#workbench-almanac [data-almanac-checkin]")?.focus({ preventScroll: true });
    toast("先签到才能查看今日运势。", "warning");
    return;
  }
  const selected = cardState.day || shanghaiDay();
  const { year, month } = parseDay(selected);
  modalState = { day: selected, viewYear: year, viewMonth: month };
  paintDialog();
}

function selectDay(day) {
  if (!modalState || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(String(day))) return;
  const { year, month } = parseDay(day);
  if (!inRange(year)) return;
  modalState = { day, viewYear: year, viewMonth: month };
  paintDialog();
}

function shiftMonth(delta) {
  if (!modalState) return;
  const moved = new Date(Date.UTC(modalState.viewYear, modalState.viewMonth - 1 + delta, 1));
  const year = moved.getUTCFullYear();
  const month = moved.getUTCMonth() + 1;
  if (!inRange(year)) return;
  const date = Math.min(parseDay(modalState.day).date, daysInMonth(year, month));
  modalState = { day: formatDay(year, month, date), viewYear: year, viewMonth: month };
  paintDialog();
}

function shiftSelectedDay(delta) {
  if (!modalState) return;
  const day = shiftDay(modalState.day, delta);
  const { year, month } = parseDay(day);
  if (!inRange(year)) return;
  modalState = { day, viewYear: year, viewMonth: month };
  paintDialog();
}

function selectMonthFromControls() {
  if (!modalState) return;
  const year = clampYear($("#dialog-content [data-almanac-year]")?.value);
  const month = Math.min(12, Math.max(1, Number($("#dialog-content [data-almanac-month]")?.value) || 1));
  const date = Math.min(parseDay(modalState.day).date, daysInMonth(year, month));
  modalState = { day: formatDay(year, month, date), viewYear: year, viewMonth: month };
  paintDialog();
}

function goToday() {
  const today = shanghaiDay();
  const { year, month } = parseDay(today);
  if (!inRange(year)) return;
  modalState = { day: today, viewYear: year, viewMonth: month };
  paintDialog();
}

function handleDialogClick(event) {
  const nav = event.target?.closest?.("[data-almanac-nav]");
  if (nav && modalState) {
    event.preventDefault();
    const action = nav.dataset.almanacNav;
    if (action === "prev-month") shiftMonth(-1);
    else if (action === "next-month") shiftMonth(1);
    else if (action === "today") goToday();
    else if (action === "prev-day") shiftSelectedDay(-1);
    else if (action === "next-day") shiftSelectedDay(1);
    return;
  }
  const day = event.target?.closest?.("[data-almanac-day]");
  if (day && modalState) {
    event.preventDefault();
    selectDay(day.dataset.almanacDay);
  }
}

function handleDialogChange(event) {
  if (!modalState) return;
  if (event.target?.closest?.("[data-almanac-year], [data-almanac-month]")) {
    selectMonthFromControls();
  }
}

/** 只绑定一次：卡片根节点和 dialog-content 都不会被 innerHTML 换掉。 */
export function initAlmanacWidget() {
  const host = $("#workbench-almanac");
  if (!host || host.dataset.almanacReady === "1") return;
  host.dataset.almanacReady = "1";
  host.addEventListener("click", (event) => {
    const checkin = event.target?.closest?.("[data-almanac-checkin]");
    if (checkin) {
      event.preventDefault();
      event.stopPropagation();
      checkIn(checkin);
      return;
    }
    openDetail();
  });
  host.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    if (event.target?.closest?.("button, select, input, textarea, a")) return;
    event.preventDefault();
    openDetail();
  });

  $("#dialog-content")?.addEventListener("click", handleDialogClick);
  $("#dialog-content")?.addEventListener("change", handleDialogChange);
  $("#detail-dialog")?.addEventListener("close", () => {
    $("#detail-dialog")?.classList.remove("almanac-mode");
  });
  // 跨过上海 0 点时重新拉一次工作台：前一天已签到不能顺延到今天。
  let lastRequestedDay = "";
  if (typeof window.setInterval === "function") {
    window.setInterval(() => {
      if (!cardState) return;
      const today = shanghaiDay();
      if (today !== cardState.day && today !== lastRequestedDay) {
        lastRequestedDay = today;
        document.dispatchEvent(new CustomEvent("data-changed"));
      }
    }, 60_000);
  }
}
