import { $, state, api, escapeHtml as esc, jsonBody, renderState, setStatus, confirmAction, changed } from "./core.js";
import { shanghaiDay, shiftDay, validJournalDay, rateLabel, habitLabels, weekdayLabels } from "./growth-model.js";
import { buildHabitGrid, habitGridHtml, HABIT_GRID_DAYS } from "./habit-grid.js";

let habits = [];
let summaries = new Map();
let loadId = 0;
let busy = false;
let loading = false;
let editor = null;
let createBaseline = "";
const drafts = new Map();
const conflicts = new Set();
const gridOpen = new Set();
const gridCache = new Map();
const gridPending = new Set();
const available = () => state.config?.capabilities?.habits === true;
const draftKey = (id, day) => `${id}:${day}`;
const editFields = ["title", "cue", "minimum_action"];
const normalized = (value) => String(value ?? "").trim();

function creationSnapshot() {
  return JSON.stringify({ title: normalized($("#habit-title").value), cue: normalized($("#habit-cue").value), minimum: normalized($("#habit-minimum").value), start: $("#habit-start").value, weekdays: [...document.querySelectorAll("[name=habit-weekday]:checked")].map((input) => input.value) });
}

function editorDirty() { return editor && editFields.some((field) => normalized(editor.values[field]) !== normalized(editor.original[field])); }
function hasDrafts() { return drafts.size > 0 || editorDirty() || creationSnapshot() !== createBaseline; }

function rememberNote(id, day, value) {
  const key = draftKey(id, day);
  const entry = summaries.get(Number(id))?.summary?.entries.find((item) => item.date === day);
  const original = drafts.get(key)?.original ?? entry?.note ?? "";
  if (normalized(value) === normalized(original)) drafts.delete(key);
  else drafts.set(key, { value, original });
}

export async function beforeLeaveHabits() {
  if (busy) { setStatus("#habits-status", "习惯正在保存或重新读取，请完成后再离开。", "error"); return false; }
  if (!hasDrafts()) return true;
  const accepted = await confirmAction({ title: "暂时离开习惯？", message: "还有未保存的习惯、备注或编辑内容。离开后会在当前页面内保留草稿；刷新或关闭页面会丢失草稿。", confirmLabel: "保留草稿并离开", cancelLabel: "继续编辑" });
  return accepted && !busy;
}

function syncControls() {
  const enabled = available();
  $("#habit-fields").disabled = busy || !enabled;
  $("#habit-create").disabled = busy || !enabled;
  $("#habit-date").disabled = busy || loading || !enabled;
  $("#habit-filter").disabled = busy || loading || !enabled;
  $("#habit-refresh").disabled = busy || loading || !enabled;
  $("#habit-support").hidden = enabled;
  $("#habit-support").textContent = state.config ? "当前运行的服务尚未支持习惯。请更新并重启本地服务，再刷新页面。" : "正在检查服务能力；若未恢复，请到设置页重新检查本地服务。";
  document.querySelectorAll("[data-habit-action]").forEach((button) => { button.disabled = busy || loading || !enabled || button.dataset.unavailable === "true"; });
  document.querySelectorAll("[data-habit-note]").forEach((input) => { input.disabled = busy || loading || !enabled || input.dataset.unavailable === "true"; });
  document.querySelectorAll("[data-habit-edit]").forEach((input) => { input.disabled = busy || !enabled; });
  const save = $("#habit-edit-save");
  if (save) save.disabled = busy || loading || !enabled || Boolean(editor?.conflicted);
}

function renderEditor() {
  if (!editor) return "";
  const { original, values } = editor;
  return `<form class="panel form-panel" data-habit-editor aria-describedby="habit-edit-help habit-edit-feedback"><h3>编辑习惯</h3><p class="field-help" id="habit-edit-help">只修改名称、触发提示和最低行动。开始日期与每周计划日保持不变，不改变历史记录与统计。${original.status === "archived" ? " 此习惯已归档，保存修改后仍保持归档。" : ""}</p><div class="field"><label for="habit-edit-title">习惯名称</label><input id="habit-edit-title" data-habit-edit="title" required maxlength="120" value="${esc(values.title)}" /></div><div class="field"><label for="habit-edit-cue">触发提示（可选）</label><input id="habit-edit-cue" data-habit-edit="cue" maxlength="500" value="${esc(values.cue)}" /></div><div class="field"><label for="habit-edit-minimum_action">最低行动</label><input id="habit-edit-minimum_action" data-habit-edit="minimum_action" required maxlength="500" value="${esc(values.minimum_action)}" /></div><p class="meta">从 ${esc(original.start_date)} 开始 · ${original.weekdays.map((value) => esc(weekdayLabels[value])).join("、")}</p><div class="growth-actions"><button type="submit" class="button" id="habit-edit-save">保存修改</button><button type="button" class="small-button" data-habit-action="cancel-edit">取消编辑</button><button type="button" class="small-button" data-habit-action="reload-edit">重新读取习惯</button></div><p id="habit-edit-feedback" class="status-message ${editor.conflicted ? "error" : ""}" role="status">${esc(editor.message || "编辑草稿只保留在当前页面，保存后才生效。")}</p></form>`;
}

function beginEditor(habit) {
  editor = { original: { ...habit }, values: Object.fromEntries(editFields.map((field) => [field, habit[field] || ""])), conflicted: false, message: "" };
}

async function closeEditor(button) {
  if (editorDirty() && !await confirmAction({ title: "放弃这份习惯编辑？", message: "未保存的名称、触发提示和最低行动将被丢弃，已保存内容与当天备注不受影响。", confirmLabel: "放弃编辑", trigger: button })) return false;
  if (busy) return false;
  editor = null;
  return true;
}

async function reloadEditor(button) {
  if (!editor || busy || loading) return;
  if (!await confirmAction({ title: "重新读取这个习惯？", message: "将读取最新保存版本并替换当前编辑草稿。当天备注草稿仍保留。", confirmLabel: "重新读取并替换草稿", trigger: button })) return;
  if (!editor || busy || loading) return;
  const id = editor.original.id;
  busy = true; syncControls();
  try {
    const fresh = await api(`/api/habits/${encodeURIComponent(id)}`);
    habits = habits.map((item) => item.id === fresh.id ? fresh : item);
    beginEditor(fresh);
    editor.message = "已读取最新版本，请核对后再编辑保存。";
    renderHabits($("#habit-date").value);
  } catch (error) {
    editor.message = `${error.message} 编辑草稿和版本仍保留。`;
    setStatus("#habit-edit-feedback", editor.message, "error");
  } finally { busy = false; syncControls(); }
}

async function saveEditor(event) {
  event.preventDefault();
  if (!editor || busy || loading || editor.conflicted || !available()) return;
  const payload = { revision: editor.original.revision, ...Object.fromEntries(editFields.map((field) => [field, normalized(editor.values[field])])) };
  if (!payload.title || payload.title.length > 120 || !payload.minimum_action || payload.minimum_action.length > 500 || payload.cue.length > 500) { setStatus("#habit-edit-feedback", "请填写 1–120 字的名称与 1–500 字的最低行动；触发提示最多 500 字。", "error"); return; }
  busy = true; syncControls();
  try {
    const saved = await api(`/api/habits/${encodeURIComponent(editor.original.id)}`, jsonBody("PATCH", payload));
    habits = habits.map((item) => item.id === saved.id ? saved : item);
    editor = null;
    renderHabits($("#habit-date").value);
    setStatus("#habits-status", "习惯说明已保存，开始日期、计划日和历史记录保持不变。", "success");
    changed();
  } catch (error) {
    editor.conflicted = error.status === 409;
    editor.message = `${error.message} ${editor.conflicted ? "编辑草稿已保留；请使用“重新读取习惯”核对最新版本，再编辑保存。" : "编辑草稿已保留，可以重试。"}`;
    setStatus("#habit-edit-feedback", editor.message, "error");
  } finally { busy = false; syncControls(); }
}

function renderHabit(habit, result, day) {
  const key = draftKey(habit.id, day);
  const summary = result?.summary;
  const entry = summary?.entries?.find((item) => item.date === day);
  const writable = Boolean(entry?.scheduled && (habit.status === "active" || entry.revision > 0) && !conflicts.has(key));
  const text = drafts.has(key) ? drafts.get(key).value : entry?.note || "";
  const id = esc(habit.id);
  const week = summary?.week && Number.isInteger(summary.week.remaining) && Number.isInteger(summary.week.scheduled)
    ? `<p class="growth-counts habit-week" title="${esc(`${summary.week.start} 至 ${summary.week.end}；${summary.week.note || ""}`)}">本周还剩 <strong>${esc(summary.week.remaining)}</strong> 个计划日 · 本周共 <strong>${esc(summary.week.scheduled)}</strong> 个计划日</p>` : "";
  const counts = summary ? `<p class="growth-counts">近 7 天 · 已完成 <strong>${esc(summary.done)}</strong> · 主动休息 <strong>${esc(summary.skipped)}</strong> · 未记录 <strong>${esc(summary.unrecorded)}</strong> · 完成率 <strong>${rateLabel(summary.completion_rate, 100)}</strong></p><ol class="habit-days" aria-label="最近七天的记录">${summary.entries.map((item) => `<li class="habit-day ${item.scheduled ? esc(item.status) : "unscheduled"}"><span>${esc(item.date.slice(5))}</span><strong>${item.scheduled ? esc(habitLabels[item.status] || "未记录") : "非计划日"}</strong></li>`).join("")}</ol>` : `<p class="inline-error">${esc(result?.error || "统计暂未读取，请点击重新读取。")}</p>`;
  const status = entry ? entry.scheduled ? habitLabels[entry.status] || "未记录" : "非计划日" : "尚未读取";
  const open = gridOpen.has(habit.id);
  return `<article class="panel growth-card" data-habit-card="${id}"><div class="growth-card-head"><div><span class="badge ${habit.status === "archived" ? "archived" : "active"}">${habit.status === "archived" ? "已归档" : "进行中"}</span><h3>${esc(habit.title)}</h3></div><div class="growth-actions"><button class="small-button" data-habit-action="edit" data-habit-id="${id}">编辑习惯</button><button class="small-button" data-habit-action="archive" data-habit-id="${id}" data-unavailable="${editor?.original.id === habit.id}">${habit.status === "archived" ? "恢复习惯" : "归档习惯"}</button></div></div><p class="preserve-lines">最低行动：${esc(habit.minimum_action)}</p><p class="meta preserve-lines">${habit.cue ? `触发提示：${esc(habit.cue)} · ` : ""}从 ${esc(habit.start_date)} 开始 · ${habit.weekdays.map((value) => esc(weekdayLabels[value])).join("、")}</p>${week}${counts}<div class="growth-actions"><button class="small-button" data-habit-grid="${id}" aria-expanded="${String(open)}">${open ? "收起近 30 天" : "近 30 天网格"}</button></div><div data-habit-grid-panel="${id}" ${open ? "" : "hidden"}>${open ? gridHtmlFor(habit.id, day) : ""}</div><p class="habit-current"><strong>${esc(day)}</strong> · ${esc(status)}</p><div class="field"><label for="habit-note-${id}">当天备注（可选）</label><textarea id="habit-note-${id}" rows="2" maxlength="2000" data-habit-note="${id}" data-unavailable="${!writable}" aria-describedby="habit-feedback-${id}">${esc(text)}</textarea></div><div class="growth-actions">${Object.entries(habitLabels).map(([statusKey, label]) => `<button class="small-button ${statusKey === "done" ? "confirm" : ""}" data-habit-action="${statusKey}" data-habit-id="${id}" data-unavailable="${!writable}">${statusKey === "unrecorded" ? "恢复未记录" : label}</button>`).join("")}</div><p class="status-message growth-feedback" id="habit-feedback-${id}" role="status">${conflicts.has(key) ? "记录版本已变化，请点击上方“重新读取”后核对；备注草稿仍保留。" : !entry?.scheduled && entry ? "当天不在计划内，不计入完成率。" : habit.status === "archived" ? "归档后保留历史；只能纠正已经存在的记录。" : "未记录只表示没有记录，不等于失败。"}</p></article>`;
}

function gridHtmlFor(habitId, day) {
  const cached = gridCache.get(habitId);
  if (!cached) return '<p class="meta">正在读取近 30 天记录…</p>';
  if (cached.error) return `<p class="inline-error">${esc(cached.error)}</p>`;
  const grid = buildHabitGrid(cached.summary?.entries, shiftDay(day, -(HABIT_GRID_DAYS - 1)), day);
  return habitGridHtml(grid);
}

async function toggleHabitGrid(habitId) {
  if (!Number.isInteger(habitId)) return;
  if (!available() || busy || loading) return;
  if (gridOpen.has(habitId)) {
    gridOpen.delete(habitId);
    renderHabits($("#habit-date").value);
    return;
  }
  gridOpen.add(habitId);
  renderHabits($("#habit-date").value);
  return ensureHabitGrid(habitId);
}

async function ensureHabitGrid(habitId) {
  if (gridCache.has(habitId) || gridPending.has(habitId) || loading) return;
  gridPending.add(habitId);
  const day = $("#habit-date").value || shanghaiDay();
  const request = loadId;
  try {
    const summary = await api(`/api/habits/${encodeURIComponent(habitId)}/summary?from_date=${shiftDay(day, -(HABIT_GRID_DAYS - 1))}&to_date=${day}`);
    gridCache.set(habitId, { summary });
  } catch (error) {
    gridCache.set(habitId, { error: error.message });
  } finally {
    gridPending.delete(habitId);
  }
  if (request === loadId && gridOpen.has(habitId)) renderHabits($("#habit-date").value);
}

function renderHabits(day) {
  const filter = $("#habit-filter").value || "active";
  const visible = habits.filter((habit) => habit.status === filter);
  if (!visible.length) renderState($("#habit-list"), "empty", filter === "archived" ? "没有归档的习惯。归档会保留已有记录。" : "从一个很小的行动开始，例如睡前读一页书。创建后，在计划日记下完成或休息。");
  else $("#habit-list").innerHTML = visible.map((habit) => renderHabit(habit, summaries.get(habit.id), day)).join("");
  if (editor) $("#habit-list").innerHTML = renderEditor() + $("#habit-list").innerHTML;
  syncControls();
}

export async function loadHabits() {
  syncControls();
  if (!available()) return;
  const day = $("#habit-date").value || shanghaiDay();
  if (!validJournalDay(day)) { setStatus("#habits-status", "请选择今天或过去的有效日期。", "error"); return; }
  const request = ++loadId;
  loading = true; syncControls();
  $("#habit-date").max = shanghaiDay();
  setStatus("#habits-status", "正在读取习惯与记录…");
  try {
    const items = await api("/api/habits");
    const results = await Promise.allSettled(items.map((habit) => api(`/api/habits/${encodeURIComponent(habit.id)}/summary?from_date=${shiftDay(day, -6)}&to_date=${day}`)));
    if (request !== loadId) return;
    habits = items;
    summaries = new Map(items.map((habit, index) => [habit.id, results[index].status === "fulfilled" ? { summary: results[index].value } : { error: results[index].reason.message }]));
    for (const habit of habits) if (summaries.get(habit.id)?.summary) conflicts.delete(draftKey(habit.id, day));
    renderHabits(day);
    const failed = results.some((result) => result.status === "rejected");
    setStatus("#habits-status", failed ? "部分记录读取失败，相关打卡暂不可用；请重新读取。" : `${day} · ${habits.filter((habit) => habit.status === "active").length} 个进行中的习惯`, failed ? "error" : "success");
  } catch (error) {
    if (request !== loadId) return;
    if (habits.length || editor) renderHabits(day);
    else renderState($("#habit-list"), "error", error.message, "habits");
    setStatus("#habits-status", "读取失败，输入草稿仍保留。", "error");
  } finally { if (request === loadId) { loading = false; syncControls(); } }
  if (request === loadId) gridOpen.forEach((habitId) => { if (!gridCache.has(habitId)) ensureHabitGrid(habitId); });
}

async function createHabit(event) {
  event.preventDefault();
  if (busy || !available()) return;
  const weekdays = [...document.querySelectorAll("[name=habit-weekday]:checked")].map((input) => Number(input.value));
  const payload = { title: $("#habit-title").value.trim(), cue: $("#habit-cue").value.trim(), minimum_action: $("#habit-minimum").value.trim(), start_date: $("#habit-start").value, weekdays };
  if (!payload.title || !payload.minimum_action || !weekdays.length || !validJournalDay(payload.start_date)) { setStatus("#habit-create-feedback", "请填写名称、最低行动、有效开始日期，并至少选择一天。", "error"); return; }
  busy = true; syncControls();
  try {
    await api("/api/habits", jsonBody("POST", payload));
    $("#habit-form").reset(); $("#habit-start").value = shanghaiDay();
    createBaseline = creationSnapshot();
    $("#habit-filter").value = "active";
    setStatus("#habit-create-feedback", "习惯已创建，可以从一个计划日开始记录。", "success");
    await loadHabits(); changed();
  } catch (error) { setStatus("#habit-create-feedback", `${error.message} 输入已保留。`, "error"); }
  finally { busy = false; syncControls(); }
}

async function applyHabit(button) {
  if (busy || loading || button.disabled || !available()) return;
  const action = button.dataset.habitAction;
  if (action === "cancel-edit") { if (await closeEditor(button)) renderHabits($("#habit-date").value); return; }
  if (action === "reload-edit") return reloadEditor(button);
  const habit = habits.find((item) => String(item.id) === button.dataset.habitId);
  if (!habit) return;
  if (action === "edit") {
    if (editor?.original.id === habit.id) { $("#habit-edit-title")?.focus(); return; }
    if (editor && !await closeEditor(button)) return;
    beginEditor(habit); renderHabits($("#habit-date").value); $("#habit-edit-title")?.focus(); return;
  }
  const day = $("#habit-date").value;
  const key = draftKey(habit.id, day);
  const entry = summaries.get(habit.id)?.summary?.entries.find((item) => item.date === day);
  if (action === "archive") {
    if (editor?.original.id === habit.id) return;
    const archive = habit.status === "active";
    if (!await confirmAction({ title: archive ? "归档这个习惯？" : "恢复这个习惯？", message: archive ? "已有记录会保留。归档后不再新增打卡，仍可查看和纠正已保存记录。" : "恢复后可以继续在计划日记录，过去的记录保持不变。", confirmLabel: archive ? "归档并保留历史" : "恢复习惯", trigger: button })) return;
  } else if (!entry?.scheduled || conflicts.has(key)) return;
  busy = true; syncControls();
  try {
    if (action === "archive") await api(`/api/habits/${habit.id}`, jsonBody("PATCH", { revision: habit.revision, status: habit.status === "active" ? "archived" : "active" }));
    else {
      const note = $(`#habit-note-${habit.id}`).value;
      rememberNote(habit.id, day, note);
      await api(`/api/habits/${habit.id}/entries/${day}`, jsonBody("PUT", { revision: entry.revision, status: action, note }));
      drafts.delete(key);
    }
    await loadHabits();
    gridCache.delete(habit.id);
    if (gridOpen.has(habit.id)) ensureHabitGrid(habit.id);
    setStatus("#habits-status", action === "archive" ? "习惯状态已更新，已有历史保留。" : `${day} 已保存为“${habitLabels[action]}”。`, "success");
    changed();
  } catch (error) {
    if (error.status === 409) { conflicts.add(key); renderHabits(day); }
    setStatus(`#habit-feedback-${habit.id}`, error.status === 409 ? `${error.message} 请点击“重新读取”后核对；备注草稿已保留。` : `${error.message} 备注草稿已保留，可以重试。`, "error");
  } finally { busy = false; syncControls(); }
}

export function initHabits() {
  $("#habit-date").value = shanghaiDay(); $("#habit-date").max = shanghaiDay();
  $("#habit-start").value = shanghaiDay(); $("#habit-start").max = shanghaiDay();
  createBaseline = creationSnapshot();
  $("#habit-form").addEventListener("submit", createHabit);
  $("#habit-date").addEventListener("change", loadHabits);
  $("#habit-date").addEventListener("change", () => { gridOpen.clear(); gridCache.clear(); gridPending.clear(); });
  $("#habit-filter").addEventListener("change", () => renderHabits($("#habit-date").value));
  $("#habit-refresh").addEventListener("click", loadHabits);
  $("#habit-list").addEventListener("input", (event) => {
    const input = event.target.closest("[data-habit-note]");
    if (input) rememberNote(input.dataset.habitNote, $("#habit-date").value, input.value);
    const field = event.target.closest("[data-habit-edit]");
    if (field && editor && editFields.includes(field.dataset.habitEdit)) editor.values[field.dataset.habitEdit] = field.value;
  });
  $("#habit-list").addEventListener("submit", (event) => { if (event.target.closest("[data-habit-editor]")) return saveEditor(event); });
  $("#habit-list").addEventListener("click", (event) => {
    const grid = event.target.closest("[data-habit-grid]");
    if (grid && grid.dataset?.habitGrid) return toggleHabitGrid(Number(grid.dataset.habitGrid));
    const button = event.target.closest("[data-habit-action]");
    if (button) return applyHabit(button);
    if (event.target.closest('[data-retry="habits"]')) return loadHabits();
  });
  document.addEventListener("config-changed", () => { syncControls(); if (state.view === "self") loadHabits(); });
  window.addEventListener("beforeunload", (event) => {
    if (busy || hasDrafts()) { event.preventDefault(); event.returnValue = ""; }
  });
  syncControls();
}
