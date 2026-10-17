import { $, state, api, escapeHtml } from "./core.js";
import { askHarnessToCompose } from "./harness-bridge.js";
import { initMemories, loadMemories } from "./memories.js";
import { initLibrary, loadDocuments } from "./library.js";
import { initGoals, loadGoals } from "./goals.js";
import { initSettings, loadSettings } from "./settings.js";
import { initImports, loadImports } from "./imports.js";
import { initJournal, loadJournalCalendar, openJournalDay } from "./journal.js";
import { initJournalNote, loadJournalNote, loadActivity } from "./journal-note.js";
import { loadBodyRecord, loadBodyHistory } from "./body-records.js";
import { shanghaiDay } from "./journal-model.js";
import { initHabits, loadHabits, beforeLeaveHabits } from "./habits.js";
import { initProfile, loadProfile } from "./profile.js";
import { initInbox, loadSchedulePanel } from "./inbox.js";
import { initVaultAudit } from "./vault-audit.js";
import { initHelp } from "./help.js";
import { NAV_LABELS, applyNavOrder, currentNavOrder } from "./nav-order.js";
import { initBazi, loadBazi } from "./bazi.js";
import { initThemeBridge } from "./theme-bridge.js";
import { initShareImport } from "./share-import.js";
import { initVaultConnection } from "./vault-connection.js";
import { initWorkspaces } from "./workspaces.js";
import { initDataHandbook } from "./data-handbook.js";
import { initToolCatalog } from "./tool-catalog.js";
import { initRestoreDrill } from "./restore-drill.js";
import { initWorkbench, loadWorkbench } from "./workbench.js";
import { initIdentity, loadIdentity } from "./identity.js";
import { initSecondBrain, loadNotes } from "./second-brain.js";

const titles = NAV_LABELS;

// 五大模块（对话在 dsh 里，帮助并进设置）：侧边栏只选模块，模块内再选分区。
// stacked 的模块一页到底，module-nav 只做锚点跳转——工作台的日记必须在流程下面。
const MODULES = {
  workbench: {
    stacked: true,
    panes: [["workbench-view", "待办事项"], ["journal-view", "日记"]],
  },
  identity: {
    panes: [["me-view", "座右铭与三观"], ["profile-view", "画像"], ["memory-view", "记忆"],
            ["goals-view", "目标与任务"], ["habits-view", "习惯"], ["body-view", "身体"], ["bazi-view", "八字参考"]],
  },
  brain: {
    panes: [["documents-view", "资料库"], ["notes-view", "笔记与灵感"], ["archive-view", "日记归档"]],
  },
  settings: { panes: [], ownNav: true },
};
const paneChoice = {};
const PANE_GUARDS = { "habits-view": beforeLeaveHabits };
const PANE_LOADERS = {
  "workbench-view": () => loadWorkbench(),
  "journal-view": () => { loadJournalNote(); loadActivity(); },
  "me-view": () => loadIdentity(),
  "profile-view": () => loadProfile(),
  "memory-view": () => loadMemories(),
  "goals-view": () => loadGoals(),
  "habits-view": () => loadHabits(),
  "body-view": () => { loadBodyRecord(shanghaiDay()); loadBodyHistory(); },
  "bazi-view": () => loadBazi(),
  "documents-view": () => { loadDocuments(); loadImports(); },
  "notes-view": () => loadNotes(),
  "archive-view": () => loadJournalCalendar(),
  "settings-view": () => { loadSettings(); loadSchedulePanel(); },
};

let navigationPending = false;
let firstViewRender = true;

// Embedded by the local dsh sidebar panel: hide our own chrome and accept
// navigation from the host so page switching stays instant (no reloads).
// 这些是模块级状态（renderPanelNav 与宿主消息都要用），不属于 switchView。
const embedMode = (location.search || "").split(/[?&]/).includes("embed=1");
if (embedMode) {
  document.documentElement.classList.add("embed-mode");
  if (document.body?.classList) document.body.classList.add("embed-mode");
}
const embedPanel = (location.search || "").split(/[?&]/).includes("panel=1");
if (embedPanel) {
  document.documentElement.classList.add("embed-panel");
  if (document.body?.classList) document.body.classList.add("embed-panel");
}
// 浏览器直开也使用顶部模块导航（不再占左侧应用栏）；内嵌面板同样如此。
// dsh 的 embed=1 不带 panel 时由宿主提供导航，不叠加这条顶栏。
const topNavMode = !embedMode || embedPanel;
if (topNavMode) {
  document.documentElement.classList.add("top-nav-mode");
  if (document.body?.classList) document.body.classList.add("top-nav-mode");
}
// 主题：嵌在 dsh 面板里就跟随 dsh（宿主 token 由 theme-bridge 接收），
// 独立打开时跟随系统明暗。首帧提示已由 embed-boot.js 同步应用。
initThemeBridge();
/** 顶部模块导航：浏览器直开和 Obsidian/dsh 面板共用（复用同一套 hash 路由）。 */
function renderPanelNav() {
  if (!topNavMode) return;
  const main = $("#main-content");
  if (!main) return;
  const nav = document.createElement("nav");
  nav.className = "panel-nav";
  nav.setAttribute("aria-label", "工作台页面");
  for (const view of currentNavOrder()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "panel-nav-item";
    button.dataset.view = view;
    button.textContent = titles[view] || view;
    button.addEventListener("click", () => { switchView(view); });
    nav.appendChild(button);
  }
  main.prepend(nav);
  // 顶部标签条吸顶（2026-10-05）：把它的实际高度写进 --panel-nav-h，
  // 模块内导航（.module-nav）据此下移，两条吸顶条不会互相盖住。
  const syncHeight = () => document.documentElement.style.setProperty("--panel-nav-h", `${nav.offsetHeight}px`);
  syncHeight();
  window.addEventListener("resize", syncHeight);
}
if (embedMode) {
  window.addEventListener("message", (event) => {
    const data = event?.data;
    if (!data || data.type !== "torch:navigate" || typeof data.view !== "string") return;
    if (!Object.hasOwn(titles, data.view)) return;
    // dsh 侧栏的二级菜单会带上 pane。分区可能属于另一个模块（stacked 的工作台不会
    // 自己切模块），所以走 showPane：先切到那个模块，再定位到分区。
    if (typeof data.pane === "string" && data.pane) showPane(data.view, data.pane);
    else switchView(data.view);
  });
}

/** 旧的 data-go-view 目标映射到新的模块 + 分区。 */
const GO_TARGETS = {
  workbench: ["workbench", ""], today: ["workbench", ""], journal: ["workbench", "journal-view"],
  identity: ["identity", ""], profile: ["identity", "profile-view"], memory: ["identity", "memory-view"],
  goals: ["identity", "goals-view"], habits: ["identity", "habits-view"], body: ["identity", "body-view"],
  bazi: ["identity", "bazi-view"], brain: ["brain", ""], documents: ["brain", "documents-view"],
  notes: ["brain", "notes-view"], archive: ["brain", "archive-view"], settings: ["settings", ""],
};

export async function showPane(view, paneId, updateHash = true) {
  if (!(await switchView(view, updateHash))) return false;
  return activatePane(view, paneId);
}

async function goTo(target, updateHash = true) {
  if (target === "help") {
    // 帮助已经并进设置：深链接 #help 仍然要把那个分区打开。
    if (!(await switchView("settings", updateHash))) return false;
    document.querySelector('[data-settings-nav="help"]')?.click();
    return true;
  }
  const mapped = GO_TARGETS[target];
  if (!mapped) return switchView(target);
  const [view, paneId] = mapped;
  return paneId ? showPane(view, paneId, updateHash) : switchView(view, updateHash);
}

export async function switchView(view, updateHash = true) {
  if (!Object.hasOwn(titles, view)) return false;
  if (navigationPending) return false;
  if (state.view !== view && !(await confirmLeaving(state.view))) {
    if (location.hash !== `#${state.view}`) history.replaceState(null, "", `#${state.view}`);
    return false;
  }
  state.view = view;
  // 工作台不再摆顶部大标题（2026-10-05 用户反馈）：给 body 打一个 view-* 类，样式据此隐藏
  // 工作台的标题块。**不要用 data-view**——页面的点击委托按 `closest("[data-view]")` 找导航
  // 按钮，body 带上它会吞掉一切点击并触发一次“切到当前模块”（2026-10-05 实测踩到）。
  if (document.body?.classList) {
    ["workbench", "identity", "brain", "settings"].forEach((name) => document.body.classList.remove(`view-${name}`));
    document.body.classList.add(`view-${view}`);
  }
  document.querySelectorAll(".nav-item, .panel-nav-item").forEach((button) => { const active = button.dataset.view === view; button.classList.toggle("active", active); if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current"); });
  // 两级导航：侧边栏选模块，模块内再用 #module-nav 选分区。
  const module = MODULES[view];
  const panes = module?.panes || [];
  if (!module?.stacked && !(paneChoice[view] && panes.some(([id]) => id === paneChoice[view]))) {
    paneChoice[view] = panes[0]?.[0] || "";
  }
  const wanted = module?.stacked ? panes.map(([id]) => id) : [paneChoice[view]].filter(Boolean);
  document.querySelectorAll(".view, .pane").forEach((section) => {
    const active = section.id === `${view}-view`;
    const shown = wanted.includes(section.id);
    section.hidden = !(active || shown);
    section.classList.toggle("active-view", active);
  });
  renderSidebarState(view);
  renderModuleNav(view);
  runModuleLoaders(view, wanted);
  const heading = $("#page-title");
  heading.textContent = titles[view];
  // Move focus so keyboard/screen-reader users land on the new page title;
  // the very first render keeps the browser's default focus.
  if (!firstViewRender) heading.focus({ preventScroll: true });
  firstViewRender = false;
  document.title = `薪火OS · ${titles[view]}`;
  if (embedMode) {
    try { window.parent?.postMessage({ type: "torch:view", view }, "*"); } catch { /* not embedded */ }
  }
  if (updateHash && location.hash !== `#${view}`) history.pushState(null, "", `#${view}`);
  else if (!updateHash && location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
  window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  return true;
}

/** 当前模块里真正看得见的分区（stacked 模块同时显示全部）。 */
function visiblePanesOf(view) {
  const module = MODULES[view];
  if (!module) return [];
  if (module.stacked) return module.panes.map(([id]) => id);
  const chosen = paneChoice[view] || module.panes[0]?.[0] || "";
  return chosen ? [chosen] : [];
}

/** 离开可见分区前先问一次未保存的草稿（日记、习惯）。 */
async function confirmLeaving(view) {
  for (const id of visiblePanesOf(view)) {
    const guard = PANE_GUARDS[id];
    if (!guard) continue;
    navigationPending = true;
    let allowed = false;
    try { allowed = await guard(); } finally { navigationPending = false; }
    if (!allowed) return false;
  }
  return true;
}

/** 模块内导航：分区按钮 + 当前分区的加载。 */
function renderModuleNav(view) {
  const bar = $("#module-nav");
  if (!bar) return;
  const module = MODULES[view];
  // 顶层应用侧栏已经统一收起（浏览器和 Obsidian 都是顶部导航），模块内分区改由这条顶栏承载。
  if (!module || module.ownNav || !module.panes.length) {
    bar.hidden = true;
    bar.replaceChildren();
    return;
  }
  const current = module.stacked ? "" : paneChoice[view];
  bar.hidden = false;
  bar.innerHTML = module.panes.map(([id, label]) => `<button type="button" class="module-nav-item${module.stacked ? "" : (id === current ? " active" : "")}" data-pane="${id}"${module.stacked ? "" : ` aria-current="${id === current ? "true" : "false"}"`}>${escapeHtml(label)}</button>`).join("");
  bar.querySelectorAll("[data-pane]").forEach((button) => button.addEventListener("click", () => {
    activatePane(view, button.dataset.pane);
  }));
}

/** 侧边栏二级菜单：只展开当前模块的分区，并标出正在看的那一项。 */
function renderSidebarState(view) {
  const module = MODULES[view];
  const current = module?.stacked ? (paneChoice[view] || module.panes[0]?.[0]) : paneChoice[view];
  document.querySelectorAll(".nav-sublist").forEach((list) => { list.hidden = list.dataset.sublist !== view; });
  document.querySelectorAll(".nav-item").forEach((button) => {
    const own = button.dataset.view === view;
    // 有没有二级菜单由数据决定，不去问 DOM（假 DOM 与旧浏览器都可能没有 hasAttribute）。
    if ((MODULES[button.dataset.view]?.panes || []).length) {
      button.setAttribute("aria-expanded", own ? "true" : "false");
    }
  });
  document.querySelectorAll(".nav-subitem").forEach((button) => {
    const inView = button.closest(".nav-sublist")?.dataset.sublist === view;
    const active = inView && button.dataset.pane === current;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "true" : "false");
  });
}

/** 切换模块内的分区：stacked 模块滚动定位，其余模块换显示并加载。 */
export async function activatePane(view, paneId, { scroll = true } = {}) {
  const module = MODULES[view];
  if (!module || !module.panes.some(([id]) => id === paneId)) return false;
  if (module.stacked) {
    paneChoice[view] = paneId;
    renderSidebarState(view);
    if (scroll) document.getElementById(paneId)?.scrollIntoView({ behavior: "auto", block: "start" });
    return true;
  }
  if (paneId !== paneChoice[view] && !(await confirmLeaving(view))) return false;
  paneChoice[view] = paneId;
  renderSidebarState(view);
  renderModuleNav(view);
  document.querySelectorAll(".pane").forEach((pane) => { pane.hidden = pane.dataset.module !== view || pane.id !== paneId; });
  runModuleLoaders(view, [paneId]);
  if (scroll) window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  return true;
}

/** 侧边栏二级菜单的点击只绑定一次。 */
function bindSidebarPanes() {
  document.querySelectorAll(".nav-subitem").forEach((button) => button.addEventListener("click", () => {
    const list = button.closest(".nav-sublist");
    if (list) activatePane(list.dataset.sublist, button.dataset.pane);
  }));
}

function runModuleLoaders(view, visiblePanes) {
  const module = MODULES[view];
  if (!module) return;
  // 设置页自带分区导航（ownNav），模块本身没有二级分区：进入模块就跑它自己的加载器
  // （`settings-view` → loadSettings + loadSchedulePanel）。0.96.0 重构漏掉了这条，
  // 导致设置页的调度状态一直是空的、真实浏览器探针从那时起在 `#schedule-status` 超时。
  if (!module.panes.length) {
    const own = PANE_LOADERS[`${view}-view`];
    if (own) own();
    return;
  }
  for (const [id] of module.panes) {
    if (!visiblePanes.includes(id)) continue;
    const loader = PANE_LOADERS[id];
    if (loader) loader();
  }
}

initMemories(); initLibrary(); initGoals(); initSettings(); initImports(); initJournal(); initJournalNote(); initHabits(); initProfile(); initInbox(); initVaultAudit();
initWorkbench(); initIdentity(); initSecondBrain();
bindSidebarPanes();
initHelp();
initBazi();
initShareImport();
initVaultConnection();
initWorkspaces();
initDataHandbook();
initToolCatalog();
initRestoreDrill();
applyNavOrder();
renderPanelNav();
// 对话统一交给 dsh（用户 2026-10-05 定调）：产品里不再有自己的对话页；
// 页面上的快捷问题入口只把问题交给宿主 dsh 的输入框。
document.addEventListener("click", async (event) => {
  const navigation = event.target.closest("[data-view], [data-go-view]");
  if (navigation) {
    const switched = await goTo(navigation.dataset.view || navigation.dataset.goView);
    if (switched && navigation.hasAttribute("data-journal-today")) openJournalDay(shanghaiDay());
  }
  const ask = event.target.closest("[data-ask-question]");
  if (ask) askHarnessToCompose(ask.dataset.askQuestion || "");
});
document.addEventListener("data-changed", () => { if (state.view === "identity") { loadMemories(); loadProfile(); } });
// 深链接与前进后退能去的地方：模块名、GO_TARGETS 里的旧名字，以及并进设置区的 help。
// 0.96 把帮助移进设置后，门只认前两类，#help 会被静默忽略——探针因此在帮助步骤超时。
const navigable = (view) => view === "help" || Object.hasOwn(titles, view) || Object.hasOwn(GO_TARGETS, view);
window.addEventListener("hashchange", async () => { const view = location.hash.slice(1); if (navigable(view)) await goTo(view, false); });
window.addEventListener("popstate", async () => { const view = location.hash.slice(1); if (!view || navigable(view)) await goTo(view || "workbench", false); });
window.addEventListener("online", loadSettings);
loadSettings();
const bootView = location.hash.slice(1);
goTo(Object.hasOwn(titles, bootView) || GO_TARGETS[bootView] ? bootView : "workbench");
