import { $, api, escapeHtml, jsonBody, renderState, setStatus, setBusy, toast, changed, state } from "./core.js";
import { shanghaiDay } from "./journal-model.js";
import { filterTasks, moveTask, orderTasks } from "./task-view.js";

let goals = [];
let tasks = [];
let loadId = 0;
let sortMode = "default";
let reordering = false;
const pending = new Set();
const TASK_SORT_KEY = "torch-os:task-sort";
const OPEN_STATUSES = ["todo", "in_progress"];
const goalStatus = { active: "进行中", paused: "已暂停", completed: "已完成", archived: "已归档" };
const taskStatus = { todo: "待办", in_progress: "进行中", completed: "已完成", cancelled: "已取消" };
const priorities = { low: "低", medium: "中", high: "高", urgent: "紧急" };
const exportAvailable = () => state.config?.capabilities?.planning_export === true;
const reorderAvailable = () => state.config?.capabilities?.task_reorder === true;

function readStoredSort() {
  try {
    const value = window.localStorage.getItem(TASK_SORT_KEY);
    return ["priority", "due", "updated", "manual"].includes(value) ? value : "default";
  } catch { return "default"; }
}

function storeSort(value) {
  try { window.localStorage.setItem(TASK_SORT_KEY, value); } catch { /* 本页内仍然生效 */ }
}

function effectiveSortMode() {
  return sortMode === "manual" && !reorderAvailable() ? "default" : sortMode;
}

function renderExportControls() {
  const available = exportAvailable();
  ["#export-planning-md", "#export-planning-json"].forEach((selector) => {
    const node = $(selector);
    if (node) node.disabled = !available;
  });
}

function exportPlanning(format) {
  if (!exportAvailable()) return;
  location.assign(`/api/export/planning.${format}`);
  toast(`已开始下载目标与任务导出（${format === "md" ? "Markdown" : "JSON"}），文件在浏览器的下载目录。`, "success");
}

function renderTask(task, location, orderControls = null) {
  const complete = task.status === "completed";
  const goal = goals.find((item) => item.id === task.goal_id);
  const checkboxId = `task-${location}-${task.id}`;
  const orderButtons = orderControls
    ? `<div class="task-order" role="group" aria-label="${escapeHtml(task.title)} 的排序"><button class="small-button" type="button" data-task-move="up" data-task-id="${escapeHtml(task.id)}" ${orderControls.canUp ? "" : "disabled"} aria-label="上移任务：${escapeHtml(task.title)}">↑</button><button class="small-button" type="button" data-task-move="down" data-task-id="${escapeHtml(task.id)}" ${orderControls.canDown ? "" : "disabled"} aria-label="下移任务：${escapeHtml(task.title)}">↓</button></div>`
    : "";
  return `<article class="task-row ${complete ? "completed-task" : ""}"><input id="${escapeHtml(checkboxId)}" type="checkbox" data-task-toggle="${escapeHtml(task.id)}" ${complete ? "checked" : ""} ${task.status === "cancelled" ? "disabled" : ""} /><div class="task-content"><label for="${escapeHtml(checkboxId)}">${escapeHtml(task.title)}</label><p class="meta">${escapeHtml(taskStatus[task.status] || task.status)} · ${escapeHtml(priorities[task.priority] || "中")}优先级${task.due_date ? ` · 截止 ${escapeHtml(task.due_date)}` : ""}${goal ? ` · ${escapeHtml(goal.title)}` : ""}</p>${task.description ? `<p>${escapeHtml(task.description)}</p>` : ""}</div>${orderButtons}</article>`;
}

function renderSortControls() {
  const mode = effectiveSortMode();
  const select = $("#task-sort");
  if (select && select.value !== mode) select.value = mode;
  const manualOption = typeof select?.querySelector === "function" ? select.querySelector('option[value="manual"]') : null;
  if (manualOption) manualOption.disabled = !reorderAvailable();
  $("#task-sort-help").textContent = mode === "manual"
    ? "手动排序只列出未完成任务；用 ↑↓ 调整顺序，保存到本机数据库，其它页面有更新会拒绝并刷新。"
    : "排序方式只保存在本机浏览器；“默认”按服务端的状态与截止日期顺序。";
}

function renderTasks() {
  const filter = $("#task-filter").value;
  const today = shanghaiDay();
  const manual = effectiveSortMode() === "manual";
  const openTasks = tasks.filter((task) => OPEN_STATUSES.includes(task.status));
  $("#task-filter").disabled = manual || reordering;
  const selected = orderTasks(manual ? openTasks : filterTasks(tasks, filter, today), effectiveSortMode());
  const emptyText = manual
    ? "没有未完成的任务可以手动排序。"
    : filter === "completed"
    ? "还没有已完成任务。完成一件小事后，在清单里勾选它。"
    : filter === "due_today"
      ? "今天没有到期的待办任务。"
      : filter === "overdue"
        ? "没有已逾期的待办任务。"
        : "当前筛选下没有任务。可以从上方创建一个具体的小行动。";
  if (!selected.length) renderState($("#task-list"), "empty", emptyText);
  else $("#task-list").innerHTML = selected.map((task, index) => renderTask(task, "all", manual ? {
    canUp: index > 0 && !reordering, canDown: index < selected.length - 1 && !reordering,
  } : null)).join("");
  // 工作台 2.0 起不再挂「下一步行动」面板；元素缺失时安静跳过。
  const todayBox = $("#today-tasks");
  if (todayBox) {
    if (!openTasks.length) renderState(todayBox, "empty", "当前没有待办任务。去“目标与任务”安排一个小行动吧。");
    else todayBox.innerHTML = openTasks.slice(0, 5).map((task) => renderTask(task, "today")).join("");
  }
  renderSortControls();
}

async function reorderTask(button) {
  if (reordering) return;
  const id = button.dataset.taskId;
  const current = orderTasks(tasks.filter((task) => OPEN_STATUSES.includes(task.status)), "manual");
  const moved = moveTask(current, id, button.dataset.taskMove === "up" ? -1 : 1);
  if (moved.every((task, index) => task.id === current[index].id)) return;
  const items = moved.map((task) => ({ id: task.id, updated_at: task.updated_at }));
  reordering = true;
  renderTasks();
  setStatus("#task-feedback", "正在保存手动顺序…");
  try {
    await api("/api/tasks/reorder", jsonBody("POST", { items }));
    setStatus("#task-feedback", "手动顺序已保存到本机数据库。", "success");
  } catch (error) {
    setStatus("#task-feedback", error.status === 409
      ? `${error.message} 已重新读取最新任务，请核对顺序后再调整。`
      : `排序未保存：${error.message} 已重新读取任务。`, "error");
  } finally {
    reordering = false;
    await loadGoals();
  }
}

function renderGoals() {
  const select = $("#task-goal");
  const selectedGoal = select.value;
  select.innerHTML = '<option value="">独立任务</option>' + goals.filter((goal) => goal.status === "active").map((goal) => `<option value="${escapeHtml(goal.id)}">${escapeHtml(goal.title)}</option>`).join("");
  if ([...select.options].some((option) => option.value === selectedGoal)) select.value = selectedGoal;
  if (!goals.length) return renderState($("#goal-list"), "empty", "还没有目标。写下一个对你重要的方向，再安排下一步行动。");
  $("#goal-list").innerHTML = goals.map((goal) => {
    const related = tasks.filter((task) => task.goal_id === goal.id && task.status !== "cancelled");
    const completed = related.filter((task) => task.status === "completed").length;
    const ratio = related.length ? Math.round(completed / related.length * 100) : Math.max(0, Math.min(100, Number(goal.progress) || 0));
    return `<article class="panel goal-card"><div class="memory-heading"><span class="eyebrow">${escapeHtml(goal.category || "个人目标")}</span><span class="badge">${escapeHtml(goalStatus[goal.status] || goal.status)}</span></div><h3>${escapeHtml(goal.title)}</h3><p>${escapeHtml(goal.description || "还没有目标说明。")}</p><div class="progress-label"><span>${related.length ? `任务 ${completed}/${related.length}` : "记录进度"}</span><span>${ratio}%</span></div><progress max="100" value="${ratio}" aria-label="${escapeHtml(goal.title)}的任务完成比例">${ratio}%</progress>${goal.target_date ? `<p class="meta">目标日期：${escapeHtml(goal.target_date)}</p>` : ""}</article>`;
  }).join("");
}

export async function loadGoals() {
  const request = ++loadId;
  setStatus("#goals-status", "加载中…");
  try {
    const results = await Promise.allSettled([api("/api/goals"), api("/api/tasks")]);
    if (request !== loadId) return;
    const [goalResult, taskResult] = results;
    if (goalResult.status === "fulfilled") { goals = goalResult.value; renderGoals(); }
    else renderState($("#goal-list"), "error", goalResult.reason.message, "goals");
    if (taskResult.status === "fulfilled") { tasks = taskResult.value; renderTasks(); if (goalResult.status === "fulfilled") renderGoals(); }
    else {
      renderState($("#task-list"), "error", taskResult.reason.message, "goals");
      const todayBox = $("#today-tasks");
      if (todayBox) renderState(todayBox, "error", taskResult.reason.message, "goals");
    }
    setStatus("#goals-status", results.every((item) => item.status === "fulfilled") ? `${goals.length} 个目标 · ${tasks.length} 项任务` : "部分数据读取失败", results.every((item) => item.status === "fulfilled") ? "success" : "error");
  } catch (error) { setStatus("#goals-status", error.message, "error"); }
}

async function submitGoal(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (form.querySelector("button[type=submit]").disabled) return;
  const title = $("#goal-title").value.trim();
  if (!title) return setStatus("#goal-feedback", "请填写目标名称。", "error");
  setBusy(form, true);
  try {
    await api("/api/goals", jsonBody("POST", { title, description: $("#goal-description").value.trim(), category: $("#goal-category").value, target_date: $("#goal-date").value || null }));
    form.reset();
    setStatus("#goal-feedback", "目标已保存", "success");
    toast("目标已创建，可以为它安排一个行动。", "success");
    await loadGoals();
  } catch (error) { setStatus("#goal-feedback", error.message, "error"); }
  finally { setBusy(form, false); }
}

async function submitTask(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (form.querySelector("button[type=submit]").disabled) return;
  const title = $("#task-title").value.trim();
  if (!title) return setStatus("#task-feedback", "请填写下一步行动。", "error");
  setBusy(form, true);
  try {
    await api("/api/tasks", jsonBody("POST", { title, goal_id: $("#task-goal").value ? Number($("#task-goal").value) : null, priority: $("#task-priority").value, due_date: $("#task-date").value || null }));
    form.reset();
    setStatus("#task-feedback", "任务已保存", "success");
    toast("任务已加入清单。", "success");
    await loadGoals();
    changed();
  } catch (error) { setStatus("#task-feedback", error.message, "error"); }
  finally { setBusy(form, false); }
}

async function toggleTask(checkbox) {
  const id = checkbox.dataset.taskToggle;
  if (pending.has(id)) return;
  pending.add(id);
  const previous = !checkbox.checked;
  document.querySelectorAll(`[data-task-toggle="${id}"]`).forEach((item) => { item.disabled = true; });
  try {
    const updated = await api(`/api/tasks/${encodeURIComponent(id)}`, jsonBody("PATCH", { status: checkbox.checked ? "completed" : "todo" }));
    tasks = tasks.map((item) => String(item.id) === id ? updated : item);
    renderTasks(); renderGoals();
    toast(updated.status === "completed" ? "任务已完成。今天又前进了一步。" : "任务已恢复为待办。", "success");
  } catch (error) { checkbox.checked = previous; toast(`任务更新失败：${error.message}`, "error"); }
  finally { pending.delete(id); document.querySelectorAll(`[data-task-toggle="${id}"]`).forEach((item) => { item.disabled = false; }); }
}

export function initGoals() {
  sortMode = readStoredSort();
  renderSortControls();
  $("#goal-form").addEventListener("submit", submitGoal);
  $("#task-form").addEventListener("submit", submitTask);
  $("#refresh-goals").addEventListener("click", loadGoals);
  $("#task-filter").addEventListener("change", renderTasks);
  $("#task-sort").addEventListener("change", () => {
    const value = $("#task-sort").value;
    sortMode = value === "manual" && !reorderAvailable() ? "default" : value;
    storeSort(sortMode);
    renderTasks();
  });
  $("#task-list").addEventListener("click", (event) => {
    const move = event.target.closest("[data-task-move]");
    if (move) return reorderTask(move);
    return null;
  });
  $("#export-planning-md")?.addEventListener("click", () => exportPlanning("md"));
  $("#export-planning-json")?.addEventListener("click", () => exportPlanning("json"));
  renderExportControls();
  document.addEventListener("config-changed", () => { renderExportControls(); renderTasks(); });
  document.addEventListener("change", (event) => { if (event.target.matches("[data-task-toggle]")) toggleTask(event.target); });
  document.addEventListener("click", (event) => { if (event.target.closest('[data-retry="goals"]')) loadGoals(); });
}
