// 时间管理模块外壳（2026-10-08，替换「今日待办」）：
// 左侧按钮栏（任务 / 日历 / 习惯打卡 / 四象限）+ 任务页（二级侧边栏 + 添加栏 + 任务板）。
// 数据来自 /api/time/*（Markdown 本体 `00-System/任务（tasks）.md`）；写操作一律「POST → 重载 → 提示」，
// 与 workbench.js 里待办/习惯那套写法保持一致。日历页与四象限页在第 3 轮接上（照滴答实测规格），
// 2026-10-11 第 4 轮把日历扩成七种视图（状态：视图/锚点/档位/显示设置，全存本机）。
import { $, state, api, jsonBody, toast, setStatus, confirmAction } from "./core.js";
import { shanghaiDay } from "./journal-model.js";
import {
  INBOX, INBOX_HINT, priorityMeta, filterTasks, groupTasks, sortTasks, boardTitle,
  defaultCalendarState, defaultTaskDate,
} from "./tasks-model.js";
import { renderTaskSidebar } from "./tasks-sidebar.js";
import { renderTaskBoard, openSortMenu, paintAddTools } from "./tasks.js";
import { renderCalendar } from "./tasks-calendar.js";
import { renderQuadrant } from "./tasks-quadrant.js";
import {
  openDatePicker, openPriorityPicker, openTimePicker, openTagPicker, openListPicker,
  toolLabels, closePopover,
} from "./tasks-picker.js";
import {
  openListDialog, openCopyListDialog, openFolderDialog, openTagDialog, confirmDialog,
} from "./tasks-dialogs.js";
import { isObsidianHost, openTaskDocument } from "./task-open.js";
import { isTaskDrag, readTaskDragKey } from "./tasks-drag.js";

const VIEW_KEY = "torch-os:time-view";
const SIDEBAR_KEY = "torch-os:time-sidebar";
const CAL_KEY = "torch-os:time-calendar";

let board = null;          // 后端板面数据 {revision, lists, tags, tasks}
// showDone 默认开：在 Obsidian 里勾完回到工作台，能直接看到勾上的那一行（也可以在工具栏关掉）。
let viewState = { selection: { view: "today" }, group: "time", sort: "time", order: "old", showDone: true };
let collapsed = { views: false, lists: false, tags: false };
let pending = { date: "", time: "", priority: "none", tags: [], list: "" };
let calendar = {};   // 日历页：哪张视图、锚点哪天、档位、周起始、是否显示已完成（见 paintCalendar）
let multi = false;              // 多选模式（点「多选」开）
let picked = new Set();         // 多选模式下选中的任务 key
let activeKey = "";             // 单击选中的那一条（底色变灰一点）
let page = "tasks";

function readStore(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch (error) {
    return fallback;
  }
}

function writeStore(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    // 存不下只是下次打开回到默认视图，不影响使用
  }
}

export function initTimeModule() {
  viewState = readStore(VIEW_KEY, viewState);
  // 「最近 7 天」视图已取消；旧的本机状态回落到今天。
  if (viewState.selection?.view === "week") {
    viewState = { ...viewState, selection: { view: "today" } };
  }
  collapsed = readStore(SIDEBAR_KEY, collapsed);
  calendar = readStore(CAL_KEY, calendar);
  // 添加栏的默认值跟着当前视图走：任务看板默认“今天”，清单/标签视图默认不带日期（照需求 ④）。
  syncPendingDefaults();
  paintTools();
  paintBulkBar();

  // 左侧按钮栏：任务 / 日历 / 习惯打卡 / 四象限
  for (const button of document.querySelectorAll("[data-time-page]")) {
    button.addEventListener("click", () => switchPage(button.dataset.timePage));
  }
  // 添加栏
  const input = $("#time-add-input");
  input.addEventListener("focus", () => { $("#time-add-tools").hidden = false; });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      createTask();
    }
  });
  $("#time-add").addEventListener("focusout", (event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) $("#time-add-tools").hidden = true;
  });
  $("#time-add-submit").addEventListener("click", () => createTask());
  $("#time-tool-date").addEventListener("click", () => {
    openDatePicker($("#time-tool-date"), pending.date, shanghaiDay(), (day) => {
      pending = { ...pending, date: day };
      paintTools();
    });
  });
  $("#time-tool-time").addEventListener("click", () => {
    openTimePicker($("#time-tool-time"), pending.time, (time) => {
      pending = { ...pending, time };
      paintTools();
    });
  });
  $("#time-tool-priority").addEventListener("click", () => {
    openPriorityPicker($("#time-tool-priority"), pending.priority, (value) => {
      pending = { ...pending, priority: value };
      paintTools();
    });
  });
  $("#time-tool-tags").addEventListener("click", () => {
    openTagPicker($("#time-tool-tags"), {
      selected: pending.tags, tags: board?.tags || [],
      onSave: (names) => {
        pending = { ...pending, tags: names };
        paintTools();
      },
    });
  });
  $("#time-tool-list").addEventListener("click", () => {
    openListPicker($("#time-tool-list"), {
      lists: board?.lists || [], current: pending.list || INBOX,
      onPick: (name) => {
        pending = { ...pending, list: name };
        paintTools();
      },
    });
  });
  // 多选与批量：多选按钮开/关；全选只选当前看板里看得到的
  $("#time-multi-btn").addEventListener("click", () => {
    if (multi) { exitMulti(); return; }
    multi = true;
    activeKey = "";
    paintBoardPicked();
  });
  $("#time-bulk-clear").addEventListener("click", exitMulti);
  $("#time-bulk-all").addEventListener("click", () => {
    const selection = viewState.selection || { view: "today" };
    // 旧任务是只读的：不进入批量选择（也避免全选后点了才发现被后端拒绝）。
    const visible = filterTasks(board?.tasks || [], selection.view || "today", {
      today: shanghaiDay(), list: selection.list || "", tag: selection.tag || "",
    }).filter((task) => !task.legacy);
    const all = visible.length > 0 && visible.every((task) => picked.has(task.key));
    picked.clear();
    if (!all) for (const task of visible) picked.add(task.key);
    paintBoardPicked();
  });
  $("#time-bulk-date").addEventListener("click", () => {
    openDatePicker($("#time-bulk-date"), "", shanghaiDay(), (day) => {
      bulkAction("date", { date: day }, (count) => (day
        ? `已把 ${count} 项改到 ${day}。` : `已清除 ${count} 项的日期。`));
    });
  });
  $("#time-bulk-priority").addEventListener("click", () => {
    openPriorityPicker($("#time-bulk-priority"), "none", (value) => {
      bulkAction("priority", { priority: value },
        (count) => `已把 ${count} 项改成${priorityMeta(value).label}。`);
    });
  });
  $("#time-bulk-pin").addEventListener("click", () => {
    const rows = (board?.tasks || []).filter((task) => picked.has(task.key));
    const allPinned = rows.length > 0 && rows.every((task) => task.pinned);
    bulkAction("pin", { pinned: !allPinned },
      (count) => (allPinned ? `已取消 ${count} 项的置顶。` : `已把 ${count} 项置顶。`));
  });
  $("#time-bulk-copy").addEventListener("click", () => {
    bulkAction("duplicate", {}, (count) => `已创建 ${count} 个副本。`);
  });
  $("#time-bulk-tag").addEventListener("click", () => {
    openTagPicker($("#time-bulk-tag"), {
      selected: [], tags: board?.tags || [],
      onSave: (names) => {
        if (!names.length) { setStatus("#time-status", "没选标签，批量打标签已取消。", "warning"); return; }
        bulkAction("tag", { tags: names }, (count) => `已给 ${count} 项打上标签。`);
      },
    });
  });
  $("#time-bulk-move").addEventListener("click", () => {
    openListPicker($("#time-bulk-move"), {
      lists: board?.lists || [], current: "",
      onPick: (name) => bulkAction("move", { list_name: name }, (count) => `已移动 ${count} 项到「${name}」。`),
    });
  });
  $("#time-bulk-delete").addEventListener("click", () => {
    confirmDialog({
      title: `删除选中的 ${picked.size} 项任务？`,
      message: "删了就找不回来了（Obsidian 里还有文件历史）。",
      onConfirm: () => bulkAction("delete", {}, (count) => `已删除 ${count} 项。`),
    });
  });
  $("#time-sort-btn").addEventListener("click", () => {
    openSortMenu($("#time-sort-btn"), viewState, (patch) => {
      viewState = { ...viewState, ...patch };
      writeStore(VIEW_KEY, viewState);
      paintBoard();
    });
  });

  // 显示已完成：默认开；在 Obsidian 里勾完回到工作台，能直接看到勾上的那一行。
  const showDoneButton = $("#time-show-done");
  const syncShowDone = () => {
    if (!showDoneButton) return;
    const on = viewState.showDone !== false;
    showDoneButton.classList.toggle("is-active", on);
    showDoneButton.setAttribute("aria-pressed", on ? "true" : "false");
  };
  showDoneButton?.addEventListener("click", () => {
    viewState = { ...viewState, showDone: viewState.showDone === false };
    writeStore(VIEW_KEY, viewState);
    syncShowDone();
    paintSidebar();
    paintBoard();
  });
  syncShowDone();

  bindTaskDropTargets();

  // 外部（Obsidian）改完文件后自动重读：切回工作台/标签页可见时 + 每 30 秒一次；
  // revision 没变就不重画，避免打断正在进行的编辑或弹窗。
  const refreshFromDisk = () => {
    if (state.view === "workbench" && !document.hidden) loadTimeTasks({ silent: true });
  };
  window.addEventListener("focus", refreshFromDisk);
  document.addEventListener("visibilitychange", refreshFromDisk);
  window.setInterval?.(refreshFromDisk, 30000);
  // 从别的模块切回工作台（或从日记切回时间管理）时立刻重读一次。
  document.addEventListener("click", (event) => {
    if (event.target.closest('[data-view="workbench"], [data-pane="workbench-view"]')) {
      loadTimeTasks({ silent: true });
    }
  });
  return loadTimeTasks();
}

/** 切模块内的页：任务页有二级侧边栏，其余页没有。 */
export function switchPage(next) {
  page = next;
  closePopover();
  if (next !== "tasks" && multi) exitMulti();
  for (const button of document.querySelectorAll("[data-time-page]")) {
    const active = button.dataset.timePage === next;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  }
  $("#time-sidebar").hidden = next !== "tasks";
  $("#time-tasks-page").hidden = next !== "tasks";
  $("#time-habits-page").hidden = next !== "habits";
  $("#time-calendar-page").hidden = next !== "calendar";
  $("#time-quadrant-page").hidden = next !== "quadrant";
  if (next === "tasks") paintBoard();
  if (next === "calendar") paintCalendar();
  if (next === "quadrant") paintQuadrant();
}

/** 工作台那边拿到 /api/workbench/today 后，把「今日完成度」喂给模块顶部的进度条。 */
export function renderTimeProgress(progress) {
  const percent = Number(progress?.percent);
  const value = Number.isFinite(percent) ? Math.round(percent) : null;
  const fill = $("#time-progress-fill");
  const label = $("#time-progress-value");
  if (fill) fill.style.width = `${value ?? 0}%`;
  if (label) label.textContent = value == null ? "—" : `${value}%`;
  const wrap = $("#time-progress-bar");
  if (wrap) wrap.setAttribute("aria-valuenow", String(value ?? 0));
}

export async function loadTimeTasks({ silent = false } = {}) {
  let next;
  try {
    next = await api("/api/time/tasks");
  } catch (error) {
    if (silent) return;  // 自动重读失败时别打扰用户，保留当前画面
    board = null;
    setStatus("#time-status", `任务：${error.message}`, "warning");
    toast(`任务没读出来：${error.message}`, "error");   // 状态行在任务页；别的页也要看得见
    return;
  }
  // 自动重读：文件没变（revision 相同）就不重画，避免打断正在进行的操作。
  if (silent && board && next.revision === board.revision) return;
  board = next;
  const keys = new Set((next.tasks || []).map((task) => task.key));
  if (activeKey && !keys.has(activeKey)) activeKey = "";
  for (const key of [...picked]) if (!keys.has(key)) picked.delete(key);
  setStatus("#time-status", "");
  paintVaultNote();
  paintSidebar();
  paintBoard();
  paintBulkBar();
  // 正好停在日历 / 四象限页时，数据变了也要跟着重画
  if (!$( "#time-calendar-page").hidden) paintCalendar();
  if (!$("#time-quadrant-page").hidden) paintQuadrant();
}

let warningSignature = "";

/** 当前任务库 + 旧任务只读提示。 */
function paintVaultNote() {
  const node = $("#time-vault-note");
  if (!node) return;
  if (!board) {
    node.hidden = true;
    return;
  }
  const source = isObsidianHost() ? "Obsidian 当前库" : "工作区库";
  const bits = [
    `${source}：${board.vault_name || "当前库"}`,
    `新任务保存为 ${board.task_dir || "00-System/任务"}/…md`,
  ];
  if (board.legacy_tasks) {
    bits.push(`旧任务 ${board.legacy_tasks} 条（仍在总文件中，只读）`);
  }
  if (board.read_only) bits.push("只读模式");
  node.textContent = bits.join(" · ");
  node.hidden = false;
  const warnings = Array.isArray(board.warnings) ? board.warnings : [];
  node.title = warnings.length
    ? `有 ${warnings.length} 个任务笔记未能读取：` + warnings.map((item) => `${item.path}（${item.reason}）`).join("；")
    : "";
  const signature = JSON.stringify(warnings);
  if (signature !== warningSignature) {
    warningSignature = signature;
    if (warnings.length) {
      toast(`有 ${warnings.length} 个任务笔记未能读取；打开 ${warnings[0].path} 检查 frontmatter。`, "warning");
    }
  }
}

/** 清掉拖放高亮（侧栏清单 + 四象限面板）。 */
function clearTaskDropTargets() {
  document.querySelectorAll(".is-drop-target").forEach((node) => node.classList.remove("is-drop-target"));
}

/** 侧栏这一行能作为哪个清单的投放目标；收集箱视图也作为收集箱清单目标。 */
function sidebarListTarget(item) {
  if (!item) return "";
  const kind = item.dataset?.timeKind || "";
  const id = item.dataset?.timeId || "";
  if (kind === "list") return id;
  if (kind === "view" && id === "inbox") return INBOX;
  return "";
}

function taskByDragKey(key) {
  if (!key) return null;
  const task = (board?.tasks || []).find((item) => item.key === key);
  return task && !task.legacy ? task : null;
}

function moveTaskByDrop(key, listName) {
  const task = taskByDragKey(key);
  if (!task || task.list_name === listName) return;
  post("/api/time/tasks/update", { key: task.key, list_name: listName },
    listName === INBOX ? "已移到收集箱。" : `已移到「${listName}」。`);
}

function reprioritizeTaskByDrop(key, priority) {
  const task = taskByDragKey(key);
  if (!task || task.priority === priority) return;
  post("/api/time/tasks/update", { key: task.key, priority },
    `已改为${priorityMeta(priority).label}。`);
}

/** 任务行拖到侧栏清单 / 四象限面板：事件委托在容器上，重画列表也不丢监听。 */
function bindTaskDropTargets() {
  const sidebar = $("#time-sidebar");
  const quadrant = $("#time-quadrant-page");

  const mark = (node) => {
    clearTaskDropTargets();
    if (node) node.classList.add("is-drop-target");
  };

  sidebar?.addEventListener("dragover", (event) => {
    if (!isTaskDrag(event)) return;
    const item = event.target.closest?.(".time-side-item");
    const listName = sidebarListTarget(item);
    if (!listName) { clearTaskDropTargets(); return; }
    event.preventDefault();
    if (event.dataTransfer) {
      try { event.dataTransfer.dropEffect = "move"; } catch { /* 某些浏览器/合成事件不允许改 */ }
    }
    mark(item.closest(".time-side-row") || item);
  });
  sidebar?.addEventListener("dragleave", (event) => {
    if (!sidebar.contains(event.relatedTarget)) clearTaskDropTargets();
  });
  sidebar?.addEventListener("drop", (event) => {
    if (!isTaskDrag(event)) return;
    const item = event.target.closest?.(".time-side-item");
    const listName = sidebarListTarget(item);
    if (!listName) { clearTaskDropTargets(); return; }
    event.preventDefault();
    const key = readTaskDragKey(event);
    clearTaskDropTargets();
    moveTaskByDrop(key, listName);
  });

  quadrant?.addEventListener("dragover", (event) => {
    if (!isTaskDrag(event)) return;
    const panel = event.target.closest?.(".time-quad-panel");
    const priority = panel?.dataset?.priority || "";
    if (!priority) { clearTaskDropTargets(); return; }
    event.preventDefault();
    if (event.dataTransfer) {
      try { event.dataTransfer.dropEffect = "move"; } catch { /* 某些浏览器/合成事件不允许改 */ }
    }
    mark(panel);
  });
  quadrant?.addEventListener("dragleave", (event) => {
    if (!quadrant.contains(event.relatedTarget)) clearTaskDropTargets();
  });
  quadrant?.addEventListener("drop", (event) => {
    if (!isTaskDrag(event)) return;
    const panel = event.target.closest?.(".time-quad-panel");
    const priority = panel?.dataset?.priority || "";
    if (!priority) { clearTaskDropTargets(); return; }
    event.preventDefault();
    const key = readTaskDragKey(event);
    clearTaskDropTargets();
    reprioritizeTaskByDrop(key, priority);
  });

  document.addEventListener("dragend", clearTaskDropTargets);
}

/** 打开任务文档：插件 iframe 里走宿主桥，浏览器里走 obsidian://。 */
function openTask(task) {
  if (!task) return;
  if (!openTaskDocument(task, board)) {
    setStatus("#time-status", "没有可打开的任务文档；刷新后重试。", "warning");
  }
}

/** 换视图（侧栏点选、置顶瓦片、标签 chip 都走这里）。 */
function selectView(selection) {
  if (multi) { picked.clear(); }
  activeKey = "";
  viewState = { ...viewState, selection };
  writeStore(VIEW_KEY, viewState);
  syncPendingDefaults();
  paintSidebar();
  paintBoard();
  paintTools();
}

function paintSidebar() {
  renderTaskSidebar($("#time-sidebar"), {
    tasks: board?.tasks || [],
    lists: board?.lists || [],
    folders: board?.folders || [],
    tags: board?.tags || [],
    selection: viewState.selection,
    showDone: viewState.showDone !== false,
    collapsed,
    onSelect: selectView,
    onToggleGroup: (name) => {
      collapsed = { ...collapsed, [name]: !collapsed[name] };
      writeStore(SIDEBAR_KEY, collapsed);
      paintSidebar();
    },
    // 清单行的「＋」：把添加栏的目标清单换过去，并聚焦（照滴答：往这个清单里加任务）
    onQuickAdd: (listName) => {
      pending = { ...pending, list: listName };
      paintTools();
      $("#time-add-input").focus();
    },
    onAddList: () => openListDialog({
      folders: board?.folders || [],
      onSave: (values) => post("/api/time/lists",
        { name: values.new_name, color: values.color, folder: values.folder }, "清单已创建。"),
    }),
    onListAction: (action, entry) => listAction(action, entry),
    onAddTag: () => openTagDialog({
      onSave: (values) => post("/api/time/tags",
        { name: values.new_name, color: values.color }, "标签已创建。"),
    }),
    onTagAction: (action, tag) => tagAction(action, tag),
    onFolderAction: (action, name) => folderAction(action, name),
  });
}

/** 清单卡上的「…」：编辑 / 置顶 / 复制 / 删除（照滴答实测，去掉共享与归档）。 */
function listAction(action, entry) {
  if (action === "edit") {
    openListDialog({
      list: entry, folders: board?.folders || [],
      onSave: (values) => post("/api/time/lists/update", {
        name: entry.name, new_name: values.new_name, color: values.color, folder: values.folder,
      }, "清单已更新。"),
    });
    return;
  }
  if (action === "pin") {
    post("/api/time/lists/update", { name: entry.name, pinned: !entry.pinned },
      entry.pinned ? "已取消置顶。" : "已置顶（看模块左上角）。");
    return;
  }
  if (action === "duplicate") {
    openCopyListDialog({
      name: entry.name,
      onConfirm: (scope) => post("/api/time/lists/duplicate",
        { name: entry.name, scope }, "清单已复制（副本插在原清单上方）。"),
    });
    return;
  }
  if (action === "delete") {
    confirmDialog({
      title: `删除清单「${entry.name}」？`,
      message: "新任务笔记会移到当前库的 .torch-os/task-trash（清单定义与原任务文件同时备份）；"
        + "这个清单里的旧任务行也会从 任务（tasks）.md 移除。可在文件系统回收站里找回。",
      onConfirm: async () => {
        if (viewState.selection?.list === entry.name) selectView({ view: "today" });
        await post("/api/time/lists/delete", { name: entry.name }, "清单已删除。");
      },
    });
  }
}

/** 标签卡上的「…」：编辑 / 置顶 / 删除（重命名会同步改任务行里的 #标签）。 */
function tagAction(action, tag) {
  if (action === "edit") {
    openTagDialog({
      tag,
      onSave: (values) => post("/api/time/tags/update", {
        name: tag.name, new_name: values.new_name, color: values.color,
      }, "标签已更新（用到它的任务也一起改了）。"),
    });
    return;
  }
  if (action === "pin") {
    post("/api/time/tags/update", { name: tag.name, pinned: !tag.pinned },
      tag.pinned ? "已取消置顶。" : "已置顶（看模块左上角）。");
    return;
  }
  if (action === "delete") {
    confirmDialog({
      title: `删除标签「${tag.name}」？`,
      message: "会把所有任务上的这个标签摘掉（任务本身保留）。",
      onConfirm: async () => {
        if (viewState.selection?.tag === tag.name) selectView({ view: "today" });
        await post("/api/time/tags/delete", { name: tag.name }, "标签已删除。");
      },
    });
  }
}

/** 文件夹那一行：重命名 / 解散（解散只把清单放回根级，不删清单——这点和滴答不同，见规格文档）。 */
function folderAction(action, name) {
  if (action === "rename") {
    openFolderDialog({
      name,
      onSave: (newName) => post("/api/time/folders/update", { name, new_name: newName },
        "文件夹已重命名。"),
    });
    return;
  }
  if (action === "dissolve") {
    confirmDialog({
      title: `解散文件夹「${name}」？`,
      message: "里面的清单回到根级，清单和任务都不删。",
      confirmLabel: "解散",
      onConfirm: () => post("/api/time/folders/delete", { name }, "文件夹已解散。"),
    });
  }
}

function paintTools() {
  paintAddTools(toolLabels(pending, shanghaiDay()));
}

/** 视图变了就把添加栏默认值对齐：看板默认今天，清单/标签默认不带日期（该清单/该标签也预填上）。 */
function syncPendingDefaults() {
  const selection = viewState.selection || { view: "today" };
  pending = {
    ...pending,
    // 今天视图默认今天；收集箱/清单/标签等视图不预填日期（2026-10-17 用户规则）。
    date: defaultTaskDate(selection, shanghaiDay()),
    list: selection.list || "",
    tags: selection.tag ? [selection.tag] : [],
  };
}

/** 任务行那一套回调：任务板 / 日历页 / 四象限页三处共用。withSelection 只在任务页开。 */
function taskOptions(selection, options = {}) {
  return {
    today: shanghaiDay(),
    lists: board?.lists || [],
    selection,
    draggable: Boolean(options.draggable),
    ...(options.withSelection ? { multi, selectedKeys: picked, activeKey, onPick: onPickTask } : {}),
    onOpenDocument: (task) => openTask(task),
    onToggle: (task, done) => {
      if (task.legacy) return;
      patch(task, { done });
    },
    onRename: (task, title) => {
      if (task.legacy) return;
      patch(task, { title });
    },
    onSelectTag: (tag) => selectView({ tag }),
    actions: {
      openDocument: (task) => openTask(task),
      setDate: (task, date) => patch(task, { date }),
      setTime: (task, time) => patch(task, { time }),
      setPriority: (task, priority) => patch(task, { priority }),
      setTags: (task, tags) => patch(task, { tags }),
      setPinned: (task, pinned) => patch(task, { pinned }),
      move: (task, list) => patch(task, { list_name: list }),
      duplicate: (task) => {
        if (task.legacy) return;
        post("/api/time/tasks/duplicate", { key: task.key }, "已创建副本。");
      },
      remove: async (task) => {
        if (task.legacy) return;
        const ok = await confirmAction(`删除任务“${task.title}”？`, { confirmLabel: "删除" });
        if (!ok) return;
        post("/api/time/tasks/delete", { key: task.key }, "任务已移到回收站（.torch-os/task-trash）。");
      },
    },
  };
}

function paintBoard() {
  const today = shanghaiDay();
  const selection = viewState.selection || { view: "today" };
  const tasks = board?.tasks || [];
  $("#time-board-title").textContent = boardTitle(selection);
  // 收集箱的说明文本：只在选中它时出现，其它视图不占地方。
  const boardHint = $("#time-board-hint");
  if (boardHint) {
    const inboxSelected = !selection.list && !selection.tag && (selection.view || "today") === "inbox";
    boardHint.hidden = !inboxSelected;
    boardHint.textContent = inboxSelected ? INBOX_HINT : "";
  }
  const picked = filterTasks(tasks, selection.view || "today", {
    today,
    list: selection.list || "",
    tag: selection.tag || "",
    showDone: viewState.showDone !== false,
  });
  const sorted = sortTasks(picked, viewState.sort, viewState.order);
  const groups = groupTasks(sorted, viewState.group, {
    today,
    listOrder: (board?.lists || []).map((entry) => entry.name),
  });
  renderTaskBoard($("#time-board"), groups, taskOptions(selection, { withSelection: true, draggable: true }));
}

/** 日历页：七种视图的状态（视图 / 锚点 / 档位 / 周起始 / 显示已完成）都记在本机。 */
function paintCalendar() {
  const today = shanghaiDay();
  calendar = { ...defaultCalendarState(today), ...calendar };
  if (!calendar.anchor) calendar.anchor = today;
  renderCalendar($("#time-calendar-page"), {
    tasks: board?.tasks || [],
    today,
    lists: board?.lists || [],
    state: calendar,
    taskOptions: taskOptions({}),
    onState: (patch) => {
      calendar = { ...calendar, ...patch };
      writeStore(CAL_KEY, calendar);
      paintCalendar();
    },
    // 日历里的快速添加（右上角 ＋、格子里的 ＋、时间轴点空白）都走这里；
    // 把成功与否回给弹窗（false → 弹窗留着、内容不丢）
    onAdd: ({ title, date, time, list, priority }) => post("/api/time/tasks", {
      title, date, time, list_name: list || INBOX, priority: priority || "none",
    }, date ? `已加到 ${date}。` : "已添加。"),
  });
}

/** 四象限页：按优先级分四格（改优先级会换格）。 */
function paintQuadrant() {
  renderQuadrant($("#time-quadrant-page"), {
    tasks: board?.tasks || [],
    taskOptions: taskOptions({}, { draggable: true }),
  });
}

/** 单击一行：多选模式下是加/取消，普通模式下是「选中这一条」（再点一次取消）。 */
function onPickTask(task) {
  if (task.legacy) return;   // 旧任务只读，不参与选中/批量
  if (multi) {
    if (picked.has(task.key)) picked.delete(task.key);
    else picked.add(task.key);
  } else {
    activeKey = activeKey === task.key ? "" : task.key;
  }
  paintBoardPicked();
}

/** 只重画任务板 + 批量条（不重新拉数据）。 */
function paintBoardPicked() {
  paintBoard();
  paintBulkBar();
}

function paintBulkBar() {
  const bar = $("#time-bulk");
  const button = $("#time-multi-btn");
  button.classList.toggle("is-active", multi);
  button.setAttribute("aria-pressed", multi ? "true" : "false");
  button.textContent = multi ? "退出多选" : "多选";
  bar.hidden = !multi;
  $("#time-bulk-count").textContent = `已选 ${picked.size} 项`;
  for (const id of ["#time-bulk-date", "#time-bulk-priority", "#time-bulk-tag",
                    "#time-bulk-move", "#time-bulk-pin", "#time-bulk-copy", "#time-bulk-delete"]) {
    $(id).disabled = picked.size === 0;
  }
  // 选中的都置顶了就显示“取消置顶”，否则显示“置顶”（一键切换）
  const rows = (board?.tasks || []).filter((task) => picked.has(task.key));
  const allPinned = rows.length > 0 && rows.every((task) => task.pinned);
  $("#time-bulk-pin").textContent = allPinned ? "取消置顶" : "置顶";
}

/** 退出多选（清空选择）。 */
function exitMulti() {
  multi = false;
  picked.clear();
  paintBoardPicked();
}

/** 批量动作：删除 / 移动到 / 打标签——一次请求写回，跑完退出多选。 */
async function bulkAction(action, payload = {}, message) {
  if (!picked.size) return;
  try {
    const result = await api("/api/time/tasks/bulk", jsonBody("POST", {
      keys: [...picked], action, ...payload, revision: board?.revision || "",
    }));
    toast(message(result?.count ?? picked.size));
    exitMulti();
    await loadTimeTasks();
  } catch (error) {
    setStatus("#time-status", `批量操作：${error.message}`, "warning");
    await loadTimeTasks();
  }
}

async function patch(task, changes) {
  if (task.legacy) {
    toast("旧任务仍在 任务（tasks）.md，应用内只读；请在 Obsidian 里修改。", "warning");
    return false;
  }
  return post("/api/time/tasks/update", { key: task.key, ...changes });
}

/** 写操作「POST → 重载 → 提示」。返回 false = 没保存成功（快速添加据此把弹窗留着，别让内容白填）。 */
async function post(path, payload, message) {
  try {
    await api(path, jsonBody("POST", { ...payload, revision: board?.revision || "" }));
    await loadTimeTasks();
    if (message) toast(message);
    return true;
  } catch (error) {
    setStatus("#time-status", `任务：${error.message}`, "warning");
    // 日历页没有状态行：失败得弹出来，否则看起来就是“点了添加没反应”（2026-10-11 用户报）
    toast(`没保存成功：${error.message}`, "error");
    await loadTimeTasks();
    return false;
  }
}

async function createTask() {
  const input = $("#time-add-input");
  const title = input.value.trim();
  if (!title) return;
  const list = pending.list || (viewState.selection?.list || "");
  try {
    await api("/api/time/tasks", jsonBody("POST", {
      title,
      list_name: list || INBOX,
      date: pending.date,
      time: pending.time,
      priority: pending.priority,
      tags: pending.tags,
      revision: board?.revision || "",
    }));
    input.value = "";
    await loadTimeTasks();
    input.focus();
  } catch (error) {
    setStatus("#time-status", `任务：${error.message}`, "warning");
  }
}
