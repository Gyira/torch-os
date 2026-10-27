// 习惯打卡页（2026-10-28）：周条、习惯卡、统计、月历、日志、提醒。
// 数据来自 /api/habits/overview；写操作后重新拉取，并用 data-changed 让任务板/工作台同步。
import { api, changed, jsonBody, setStatus, state, toast } from "./core.js";
import { shanghaiDay } from "./journal-model.js";
import { openTaskDocument } from "./task-open.js";
import {
  entryFor, logsForHabit, monthGrid, monthLabel, shiftMonth,
} from "./habits-model.js";
import {
  habitCardsHtml, habitCountLabel, habitDetailHtml, habitGroupOptions, habitWarningsHtml,
  weekStripHtml,
} from "./habits-markup.js";
import {
  openHabitDialog, openHabitLogDialog, openHabitMenu, confirmDeleteHabit,
} from "./habits-dialogs.js";

const REMINDER_KEY = "torch-os:habit-reminders";
let overview = null;
let selectedId = 0;
let month = "";
let groupFilter = "";
let archived = false;
let onChanged = null;
let loading = false;
let reminderTimer = 0;
let pickerBound = false;
let pendingSelectId = 0;

function today() {
  return overview?.today || shanghaiDay();
}

function currentHabits() {
  const habits = Array.isArray(overview?.habits) ? overview.habits : [];
  return groupFilter ? habits.filter((habit) => habit.group === groupFilter) : habits;
}

function selectedHabit() {
  const habits = currentHabits();
  return habits.find((habit) => Number(habit.id) === Number(selectedId)) || habits[0] || null;
}

function emitChanged() {
  changed();
  onChanged?.();
}

function paintColors() {
  document.querySelectorAll("[data-habit-color]").forEach((node) => {
    const color = node.dataset.habitColor || "";
    if (color) node.style.setProperty("--habit-color", color);
    else node.style.removeProperty("--habit-color");
  });
  // CSP 只允许 'self' 样式：进度/目标条用 CSSOM 写变量，不生成内联 style 属性。
  document.querySelectorAll("[data-habit-progress]").forEach((node) => {
    const value = Math.max(0, Math.min(100, Number(node.dataset.habitProgress) || 0));
    node.style.setProperty("--habit-progress", `${value}%`);
  });
  document.querySelectorAll("[data-habit-target-percent]").forEach((node) => {
    const value = Math.max(0, Math.min(100, Number(node.dataset.habitTargetPercent) || 0));
    node.style.width = `${value}%`;
  });
}

function paint() {
  if (!overview) return;
  const habits = currentHabits();
  if (selectedId && !habits.some((habit) => Number(habit.id) === Number(selectedId))) selectedId = 0;
  if (!selectedId && habits.length) selectedId = habits[0].id;
  const selected = selectedHabit();
  const count = document.querySelector("#habit-page-count");
  if (count) count.textContent = habitCountLabel(habits);
  const week = document.querySelector("#habit-week-strip");
  if (week) week.innerHTML = weekStripHtml(overview.week, today());
  const groupSelect = document.querySelector("#habit-group-filter");
  if (groupSelect) groupSelect.innerHTML = habitGroupOptions(overview.groups || [], groupFilter);
  const cards = document.querySelector("#habit-card-list");
  if (cards) cards.innerHTML = habitCardsHtml(habits, selectedId);
  const detail = document.querySelector("#habit-detail");
  if (detail) {
    detail.innerHTML = selected ? habitDetailHtml(selected, {
      month: overview.month,
      monthTitle: monthLabel(overview.month),
      grid: monthGrid(selected.entries || [], overview.month, today()),
      logs: logsForHabit(overview.logs || [], selected.id),
    }) : habitDetailHtml(null, { month: overview.month });
  }
  const archivedButton = document.querySelector("#habit-archived-toggle");
  if (archivedButton) {
    archivedButton.classList.toggle("is-active", archived);
    archivedButton.setAttribute("aria-pressed", archived ? "true" : "false");
    archivedButton.textContent = archived ? "看进行中" : "已归档";
  }
  const status = document.querySelector("#habit-page-status");
  if (status) {
    status.innerHTML = habitWarningsHtml(overview.warnings || []);
    if (!overview.warnings?.length) status.textContent = "";
  }
  paintColors();
}

export async function loadHabitsPage({ silent = false, force = false } = {}) {
  if (!month) month = shanghaiDay().slice(0, 7);
  if (loading) return;
  loading = true;
  try {
    const next = await api(`/api/habits/overview?month=${encodeURIComponent(month)}&include_archived=${archived ? "true" : "false"}`);
    if (silent && !force && overview
        && next.habits_revision === overview.habits_revision
        && next.month === overview.month
        && next.today === overview.today) {
      return;
    }
    overview = next;
    if (groupFilter && !(next.groups || []).some((group) => group.name === groupFilter)) {
      groupFilter = "";
    }
    if (pendingSelectId && next.habits.some((habit) => Number(habit.id) === Number(pendingSelectId))) {
      selectedId = pendingSelectId;
    }
    pendingSelectId = 0;
    if (!selectedId || !next.habits.some((habit) => Number(habit.id) === Number(selectedId))) {
      selectedId = next.habits[0]?.id || 0;
    }
    paint();
  } catch (error) {
    setStatus("#habit-page-status", `习惯读取失败：${error.message}`, "error");
  } finally {
    loading = false;
  }
}

export function selectHabit(habitId) {
  selectedId = Number(habitId) || 0;
  paint();
  document.querySelector("#habit-detail")?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
}

/** 从任务板/提醒跳过来：先记下目标，等 overview 加载完再选中它。 */
export function focusHabit(habitId) {
  pendingSelectId = Number(habitId) || 0;
  if (overview) {
    selectHabit(pendingSelectId);
    pendingSelectId = 0;
  }
  loadHabitsPage({ force: true });
}

async function toggleHabit(habit, done, day = today()) {
  const entry = entryFor(habit.entries || [], day) || (day === today() ? habit.entry_today : null);
  const revision = Number(entry?.revision) || 0;
  try {
    const result = await api(`/api/habits/${habit.id}/toggle`, jsonBody("POST", {
      day, done, revision,
    }));
    await loadHabitsPage({ force: true });
    emitChanged();
    if (done && habit.auto_log) {
      openHabitLogDialog({
        habit, day, entry: result,
        onSave: async (payload) => {
          try {
            await api(`/api/habits/${habit.id}/entries/${day}`, jsonBody("PUT", payload));
            await loadHabitsPage({ force: true });
            emitChanged();
            toast("打卡日志已保存。");
          } catch (error) {
            toast(`日志没保存成功：${error.message}`, "error");
            throw error;
          }
        },
      });
    } else {
      toast(done ? `已打卡：${habit.title}` : `已取消打卡：${habit.title}`);
    }
  } catch (error) {
    setStatus("#habit-page-status", `打卡失败：${error.message}`, "error");
    await loadHabitsPage({ force: true });
  }
}

function openHabitEditor(habit = null) {
  openHabitDialog({
    habit,
    groups: overview?.groups || [],
    today: today(),
    defaultGroup: groupFilter,
    onSave: async (payload) => {
      let result;
      try {
        if (habit) result = await api(`/api/habits/${habit.id}`, jsonBody("PATCH", payload));
        else result = await api("/api/habits", jsonBody("POST", payload));
      } catch (error) {
        toast(`习惯没保存成功：${error.message}`, "error");
        throw error;
      }
      if (!habit && result?.id) {
        selectedId = result.id;
        groupFilter = "";
        archived = false;
      }
      await loadHabitsPage({ force: true });
      emitChanged();
      toast(habit ? "习惯已更新。" : "习惯已创建。");
    },
  });
}

function openEntryEditor(habit, day) {
  if (!day) return;
  if (day > today()) {
    toast("未来日期不能打卡。", "warning");
    return;
  }
  const entry = entryFor(habit.entries || [], day);
  const entryToday = day === today() ? habit.entry_today : null;
  const existing = entry || entryToday;
  const scheduled = entry ? Boolean(entry.scheduled)
    : Boolean(day === today() && habit.scheduled_today);
  const hasRecord = Boolean(existing && (Number(existing.revision) > 0 || String(existing.note || "").trim()));
  if (!hasRecord && !scheduled) {
    toast("这天不在习惯计划内，不需要打卡。", "warning");
    return;
  }
  if (!hasRecord && habit.status === "archived") {
    toast("习惯已归档，不能新增记录；已有历史记录仍可纠正。", "warning");
    return;
  }
  openHabitLogDialog({
    habit, day, entry: existing,
    onSave: async (payload) => {
      try {
        await api(`/api/habits/${habit.id}/entries/${day}`, jsonBody("PUT", payload));
        await loadHabitsPage({ force: true });
        emitChanged();
        toast("打卡记录已保存。");
      } catch (error) {
        toast(`记录没保存成功：${error.message}`, "error");
        throw error;
      }
    },
  });
}

async function archiveHabit(habit, nextArchived) {
  try {
    await api(`/api/habits/${habit.id}`, jsonBody("PATCH", {
      revision: habit.revision, status: nextArchived ? "archived" : "active",
    }));
    await loadHabitsPage({ force: true });
    emitChanged();
    toast(nextArchived ? "习惯已归档。" : "习惯已取消归档。");
  } catch (error) {
    setStatus("#habit-page-status", `归档失败：${error.message}`, "error");
    await loadHabitsPage({ force: true });
  }
}

async function deleteHabit(habit) {
  await confirmDeleteHabit(habit, async () => {
    try {
      await api(`/api/habits/${habit.id}/delete`, jsonBody("POST", { revision: habit.revision }));
      selectedId = 0;
      await loadHabitsPage({ force: true });
      emitChanged();
      toast("习惯已移到 .torch-os/habit-trash。");
    } catch (error) {
      setStatus("#habit-page-status", `删除失败：${error.message}`, "error");
      await loadHabitsPage({ force: true });
    }
  });
}

async function skipHabit(habit, skipped) {
  const entry = habit.entry_today;
  const revision = Number(entry?.revision) || 0;
  try {
    await api(`/api/habits/${habit.id}/entries/${today()}`, jsonBody("PUT", {
      revision, status: skipped ? "skipped" : "unrecorded", note: entry?.note || "",
    }));
    await loadHabitsPage({ force: true });
    emitChanged();
    toast(skipped ? "今天标记为主动休息。" : "已取消主动休息。");
  } catch (error) {
    setStatus("#habit-page-status", `更新失败：${error.message}`, "error");
    await loadHabitsPage({ force: true });
  }
}

function openDocument(habit) {
  if (!habit?.path) {
    toast("没有可打开的习惯笔记。", "warning");
    return;
  }
  if (!openTaskDocument({ path: habit.path }, { vault_name: overview?.vault_name || "" })) {
    toast("没有可用的 Obsidian 库名，请在 Obsidian 里打开。", "warning");
  }
}

function openMenu(anchor, habit) {
  openHabitMenu(anchor, habit, {
    edit: openHabitEditor,
    archive: archiveHabit,
    delete: deleteHabit,
    skip: skipHabit,
    openDocument,
  });
}

function findHabit(id) {
  return (overview?.habits || []).find((habit) => Number(habit.id) === Number(id)) || null;
}

function handleClick(event) {
  const target = event.target;
  const add = target.closest?.("[data-habit-add]");
  if (add) { event.preventDefault(); openHabitEditor(null); return; }
  const check = target.closest?.("[data-habit-check]");
  if (check) {
    event.preventDefault();
    event.stopPropagation();
    const habit = findHabit(check.dataset.habitCheck);
    if (habit) toggleHabit(habit, !(habit.entry_today?.status === "done"));
    return;
  }
  const menu = target.closest?.("[data-habit-menu]");
  if (menu) {
    event.preventDefault();
    event.stopPropagation();
    const habit = findHabit(menu.dataset.habitMenu);
    if (habit) openMenu(menu, habit);
    return;
  }
  const calendarDay = target.closest?.("[data-habit-cal-day]");
  if (calendarDay) {
    event.preventDefault();
    event.stopPropagation();
    const habit = selectedHabit();
    if (habit) openEntryEditor(habit, calendarDay.dataset.habitCalDay || "");
    return;
  }
  if (target.closest?.("[data-habit-month-prev]")) {
    event.preventDefault();
    month = shiftMonth(overview?.month || month, -1);
    loadHabitsPage({ force: true });
    return;
  }
  if (target.closest?.("[data-habit-month-next]")) {
    event.preventDefault();
    month = shiftMonth(overview?.month || month, 1);
    loadHabitsPage({ force: true });
    return;
  }
  if (target.closest?.("#habit-archived-toggle")) {
    event.preventDefault();
    archived = !archived;
    loadHabitsPage({ force: true });
    return;
  }
  const card = target.closest?.("[data-habit-id]");
  if (card && !target.closest?.("button")) {
    event.preventDefault();
    selectHabit(card.dataset.habitId);
  }
}

function handleKeydown(event) {
  if (event.key !== "Enter" && event.key !== " ") return;
  const card = event.target.closest?.("[data-habit-id]");
  if (!card || event.target.closest?.("button")) return;
  event.preventDefault();
  selectHabit(card.dataset.habitId);
}

function reminderMap() {
  try {
    const raw = window.localStorage.getItem(REMINDER_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function saveReminderMap(map) {
  try {
    window.localStorage.setItem(REMINDER_KEY, JSON.stringify(map));
  } catch {
    // 存不下只影响本次提醒，不影响数据
  }
}

function nowTime() {
  try {
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit",
      hour12: false, hourCycle: "h23",
    }).format(new Date());
  } catch {
    return "";
  }
}

function checkReminders() {
  if (!overview) return;
  const day = today();
  const current = nowTime();
  if (!current) return;
  const map = reminderMap();
  const activeUids = new Set((overview.habits || []).map((habit) => String(habit.uid)));
  let changedMap = false;
  for (const uid of Object.keys(map)) {
    if (!activeUids.has(uid) || map[uid] !== day) { delete map[uid]; changedMap = true; }
  }
  for (const habit of overview.habits || []) {
    if (habit.status !== "active" || !habit.reminder || !habit.scheduled_today) continue;
    const status = habit.entry_today?.status || "unrecorded";
    if (status === "done" || status === "skipped") continue;
    if (habit.reminder > current) continue;
    if (map[String(habit.uid)] === day) continue;
    map[String(habit.uid)] = day;
    changedMap = true;
    toast(`该打卡了：${habit.title}`, "info", {
      label: "去打卡",
      timeout: 12000,
      onAction: () => {
        document.querySelector('[data-time-page="habits"]')?.click();
        focusHabit(habit.id);
      },
    });
  }
  if (changedMap) saveReminderMap(map);
}

export function initHabitsPage(options = {}) {
  onChanged = typeof options.onChanged === "function" ? options.onChanged : null;
  if (pickerBound) return;
  pickerBound = true;
  month = shanghaiDay().slice(0, 7);
  const page = document.querySelector("#time-habits-page");
  page?.addEventListener("click", handleClick);
  page?.addEventListener("keydown", handleKeydown);
  document.querySelector("#habit-group-filter")?.addEventListener("change", (event) => {
    groupFilter = event.target.value || "";
    paint();
  });
  document.addEventListener("data-changed", () => {
    if (state.view === "workbench" && !document.hidden) loadHabitsPage({ silent: true });
  });
  reminderTimer = window.setInterval?.(checkReminders, 60000) || 0;
  loadHabitsPage({ silent: true }).then(checkReminders).catch(() => {});
  if (reminderTimer === 0) {
    window.setTimeout?.(checkReminders, 3000);
  }
}
