import { $, api, changed, escapeHtml, jsonBody, openDialog, renderState, setStatus } from "./core.js";

const MAX_SELECTION = 20;
const MAX_VISIBLE = 50;
const applyingDocuments = new Set();
let activeSession = null;
const statusLabels = { todo: "待办", in_progress: "进行中", completed: "已完成", cancelled: "已取消" };

function warningsHtml(warnings) {
  return Array.isArray(warnings) && warnings.length ? `<div class="inline-warning"><ul>${warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul></div>` : "";
}

function controls(session) {
  if (!session.current()) return;
  const busy = session.loading || applyingDocuments.has(session.documentId);
  $("#task-import-panel").setAttribute("aria-busy", String(busy));
  $("#task-import-reload").disabled = busy;
  $("#task-import-search").disabled = busy || !session.preview;
  $("#task-import-selected-only").disabled = busy || !session.preview;
  $("#task-import-goal").disabled = busy;
  $("#task-import-clear").disabled = busy || !session.selected.size;
  $("#task-import-apply").disabled = busy || session.stale || !session.preview || !session.selected.size;
  $("#task-import-apply").textContent = applyingDocuments.has(session.documentId) ? "正在复制任务…" : "将所选复制为独立任务";
  $("#task-import-selection").textContent = `已选择 ${session.selected.size} / ${MAX_SELECTION} 项${session.stale ? " · 版本需重新预览" : ""}`;
  $("#task-import-items").querySelectorAll("input").forEach((input) => { input.disabled = busy || session.stale; });
}

function renderItems(session) {
  if (!session.current() || !session.preview) return;
  const query = $("#task-import-search").value.trim().toLocaleLowerCase();
  const selectedOnly = $("#task-import-selected-only").checked;
  const filtered = session.preview.items.filter((item) => (!selectedOnly || session.selected.has(item.key)) && `${item.title} ${item.heading} ${item.description}`.toLocaleLowerCase().includes(query));
  const visible = filtered.slice(0, MAX_VISIBLE);
  $("#task-import-list-status").textContent = `共 ${session.preview.items.length} 项 · 匹配 ${filtered.length} 项 · 显示 ${visible.length} 项${filtered.length > MAX_VISIBLE ? "；列表最多显示 50 项，请搜索缩小范围。" : ""}`;
  if (!visible.length) renderState($("#task-import-items"), "empty", session.preview.items.length ? "没有匹配项，可以清空搜索或关闭“只看已选”。" : "没有识别到 Markdown 复选框待办，原文保持不变。");
  else $("#task-import-items").innerHTML = visible.map((item, index) => {
    const id = `task-import-item-${index}`;
    return `<div class="import-file-row"><input id="${id}" type="checkbox" data-task-import-key="${escapeHtml(item.key)}" ${session.selected.has(item.key) ? "checked" : ""} /><div><label for="${id}">${escapeHtml(item.title)}</label><p class="meta">资料正文第 ${escapeHtml(item.line)} 行${item.heading ? ` · ${escapeHtml(item.heading)}` : ""}</p><p class="meta">${item.checked ? "来源已完成，完成日期未知；复制后不计入今天完成。" : "来源未完成，复制后成为待办。"}</p><details><summary class="text-button">查看原始复选框行</summary><p class="preserve-lines">${escapeHtml(item.description)}</p></details></div></div>`;
  }).join("");
  controls(session);
}

async function loadPreview(session) {
  if (!session.current() || session.loading || applyingDocuments.has(session.documentId)) return;
  session.loading = true;
  controls(session);
  setStatus("#task-import-feedback", "正在核对本地来源并读取待办…");
  try {
    const [previewResult, goalsResult] = await Promise.allSettled([api(`/api/documents/${session.documentId}/tasks/preview`), api("/api/goals")]);
    if (!session.current()) return;
    if (previewResult.status === "rejected") throw previewResult.reason;
    const preview = previewResult.value;
    if (preview.document_id !== Number(session.documentId) || !Number.isInteger(preview.revision) || preview.revision < 1 || !/^[a-f0-9]{64}$/i.test(preview.sha256 || "") || !Array.isArray(preview.items)) throw new Error("来源预览格式异常，请重新预览。");
    session.preview = preview;
    session.selected.clear();
    session.stale = false;
    $("#task-import-result").innerHTML = "";
    $("#task-import-warnings").innerHTML = warningsHtml(preview.warnings);
    const goalSelect = $("#task-import-goal");
    const selectedGoal = goalSelect.value;
    const goals = goalsResult.status === "fulfilled" && Array.isArray(goalsResult.value) ? goalsResult.value : [];
    goalSelect.innerHTML = '<option value="">不关联目标</option>' + goals.filter((goal) => goal.status === "active").map((goal) => `<option value="${escapeHtml(goal.id)}">${escapeHtml(goal.title)}</option>`).join("");
    goalSelect.value = goals.some((goal) => goal.status === "active" && String(goal.id) === selectedGoal) ? selectedGoal : "";
    $("#task-import-goal-feedback").textContent = goalsResult.status === "rejected" ? "目标读取失败；可以不关联目标，或点击重新预览重试。" : "可以为新任务统一选择一个进行中的目标；重复导入不会更改已有任务的目标。";
    setStatus("#task-import-feedback", `预览已更新，共 ${preview.items.length} 项，默认不选择任何条目。`, "success");
    renderItems(session);
  } catch (error) {
    if (!session.current()) return;
    session.stale = true;
    setStatus("#task-import-feedback", `${error.message}${session.preview ? " 原选择保留，请重新预览后再选择和导入。" : " 可点击“重新预览”重试。"}`, "error");
  } finally { session.loading = false; if (session.current()) controls(session); }
}

async function applySelection(session) {
  if (!session.current() || session.loading || session.stale || !session.preview || !session.selected.size || applyingDocuments.has(session.documentId)) return;
  const preview = session.preview;
  const selectedKeys = [...session.selected];
  const goalId = $("#task-import-goal").value ? Number($("#task-import-goal").value) : null;
  applyingDocuments.add(session.documentId);
  controls(session);
  $("#task-import-result").innerHTML = "";
  setStatus("#task-import-feedback", "正在将所选条目复制为独立任务…");
  try {
    const result = await api(`/api/documents/${session.documentId}/tasks/apply`, jsonBody("POST", { revision: preview.revision, sha256: preview.sha256, selected_keys: selectedKeys, goal_id: goalId, confirm: true }));
    changed();
    if (!session.current()) return;
    const created = Number(result.created_count) || 0;
    const existing = Number(result.existing_count) || 0;
    $("#task-import-result").innerHTML = `<h3>已创建 ${created} 项 · 已有 ${existing} 项</h3>${warningsHtml(result.warnings)}${(result.tasks || []).map((task) => `<article class="document-card"><div><h3>${escapeHtml(task.title)}</h3><p class="meta">${escapeHtml(statusLabels[task.status] || task.status)}${task.status === "completed" && !task.completed_at ? " · 完成日期未知，不计入今天完成" : ""}</p>${task.description ? `<details><summary class="text-button">查看任务描述</summary><p class="preserve-lines">${escapeHtml(task.description)}</p></details>` : ""}</div></article>`).join("")}<p class="field-help">任务已独立保存；可以关闭本窗口后，在“目标与任务”中管理。不会反向同步或修改 Markdown。</p>`;
    setStatus("#task-import-feedback", "本批次处理完成。重复选择会返回已有任务，保留其当前编辑和状态。", "success");
  } catch (error) {
    if (!session.current()) return;
    if (error.status === 409) session.stale = true;
    setStatus("#task-import-feedback", error.status === 409 ? `${error.message} 当前选择已保留，但不能继续提交旧版本；请重新预览并重新选择。` : `${error.message} 当前选择已保留，可以重试。`, "error");
  } finally {
    applyingDocuments.delete(session.documentId);
    if (session.current()) controls(session);
    else if (activeSession?.documentId === session.documentId && activeSession.current()) controls(activeSession);
  }
}

export async function openTaskImport(documentData) {
  const documentId = String(documentData.id);
  if (!/^\d+$/.test(documentId)) return;
  const current = openDialog("预览待办清单", `<div id="task-import-panel"><h3>${escapeHtml(documentData.title || "本地资料")}</h3><p class="field-help">复制为独立任务，不反向同步，不修改原文，也不调用云端模型。来源已勾选的条目完成日期未知，不计入今天完成。行号指去除文首元数据及首尾空白后的资料正文行。</p><button id="task-import-reload" class="text-button">重新预览</button><p class="field-help">重新预览成功后会清空当前选择，请按最新来源重新选择。</p><div id="task-import-feedback" class="status-message" role="status" aria-live="polite"></div><div id="task-import-warnings"></div><div class="import-filter-row"><div class="field"><label for="task-import-search">搜索待办、标题或原始行</label><input id="task-import-search" type="search" placeholder="缩小清单范围" /></div><label class="check-label" for="task-import-selected-only"><input id="task-import-selected-only" type="checkbox" />只看已选</label></div><p id="task-import-list-status" class="meta" role="status"></p><div id="task-import-items" class="import-file-list"></div><div class="field"><label for="task-import-goal">新任务关联目标（可选）</label><select id="task-import-goal" aria-describedby="task-import-goal-feedback"><option value="">不关联目标</option></select><span id="task-import-goal-feedback" class="field-help" role="status"></span></div><div class="form-actions import-actions"><span id="task-import-selection" class="meta" role="status">已选择 0 / 20 项</span><div class="section-actions"><button id="task-import-clear" class="text-button" disabled>清空选择</button><button id="task-import-apply" class="button primary" disabled>将所选复制为独立任务</button></div></div><div id="task-import-result" role="status"></div></div>`);
  const session = { documentId, current, preview: null, selected: new Set(), loading: false, stale: false };
  activeSession = session;
  $("#task-import-reload").addEventListener("click", () => loadPreview(session));
  $("#task-import-search").addEventListener("input", () => renderItems(session));
  $("#task-import-selected-only").addEventListener("change", () => renderItems(session));
  $("#task-import-clear").addEventListener("click", () => { if (!session.current() || session.loading || applyingDocuments.has(documentId)) return; session.selected.clear(); renderItems(session); });
  $("#task-import-apply").addEventListener("click", () => applySelection(session));
  $("#task-import-items").addEventListener("change", (event) => {
    if (!session.current()) return;
    const input = event.target.closest("[data-task-import-key]");
    if (!input) return;
    const key = input.dataset.taskImportKey;
    if (session.loading || session.stale || applyingDocuments.has(documentId) || !session.preview?.items.some((item) => item.key === key)) { input.checked = session.selected.has(key); return; }
    if (!input.checked) session.selected.delete(key);
    else if (session.selected.size < MAX_SELECTION) session.selected.add(key);
    else { input.checked = false; setStatus("#task-import-feedback", "每批最多选择 20 项，请先处理当前选择。", "warning"); }
    if ($("#task-import-selected-only").checked && !input.checked) renderItems(session);
    else controls(session);
  });
  if (applyingDocuments.has(documentId)) {
    controls(session);
    setStatus("#task-import-feedback", "同一来源的任务正在复制，请完成后点击“重新预览”，核对结果再选择。", "warning");
  } else await loadPreview(session);
}
