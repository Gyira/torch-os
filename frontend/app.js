import { $, state, api, escapeHtml } from "./core.js";
import { initSettings, loadSettings } from "./settings.js";
import { initDshPane, loadDshPane } from "./dsh-pane.js";
import { initLovePane, loadLovePane } from "./love.js";
import { initInbox, loadSchedulePanel } from "./inbox.js";
import { initHelp } from "./help.js";
import { NAV_LABELS, applyNavOrder, currentNavOrder } from "./nav-order.js";
import { initThemeBridge } from "./theme-bridge.js";
import { initVaultConnection } from "./vault-connection.js";
import { initVaultAdmin } from "./vault-admin.js";
import { initWorkspaces } from "./workspaces.js";
import { initDataHandbook } from "./data-handbook.js";
import { initToolCatalog } from "./tool-catalog.js";
import { initRestoreDrill } from "./restore-drill.js";
import { initWorkbench, loadWorkbench } from "./workbench.js";
import { initDidaSync } from "./dida-sync.js";
import { initPillars, loadPillarModule } from "./pillars.js";
import { openGlobalSearch } from "./global-search.js";
import { initKeyboardShortcuts } from "./shortcuts.js";

const titles = NAV_LABELS;

// 五大模块（工作台内含「与神对话」，帮助并进设置）：侧边栏只选模块，模块内再选分区。
// 工作台的两个分区同页上下排列：待办事项在上，完整的 dsh 界面在下；二级菜单只做滚动定位。
const MODULES = {
  // 待办事项和与神对话现在同页上下排列；二级菜单不再切页，只做滚动定位。
  workbench: {
    stacked: true,
    panes: [["workbench-view", "待办事项"], ["agent-view", "与神对话"]],
  },
  // 四库：模块内部由 pillars.js 自己的章节轨导航，不再使用通用二级菜单。
  life: { panes: [], ownNav: true },
  self: { panes: [], ownNav: true },
  // 生活运营保留自己的章节轨，同时在模块顶部多一个「恋爱系统」二级分区。
  ops: { stacked: false, panes: [["ops-view", "生活运营"], ["love-view", "恋爱系统"]] },
  knowledge: { panes: [], ownNav: true },
  settings: { panes: [], ownNav: true },
};
const paneChoice = {};
const PANE_GUARDS = {};
const PANE_LOADERS = {
  "workbench-view": () => loadWorkbench(),
  "agent-view": () => { initDshPane(); loadDshPane(); },
  "love-view": () => { initLovePane(); loadLovePane(); },
  "life-view": () => loadPillarModule("life"),
  "self-view": () => loadPillarModule("self"),
  "ops-view": () => loadPillarModule("ops"),
  "knowledge-view": () => loadPillarModule("knowledge"),
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
  const scroll = document.createElement("div");
  scroll.className = "panel-nav-scroll";
  for (const view of currentNavOrder()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "panel-nav-item";
    button.dataset.view = view;
    button.textContent = titles[view] || view;
    button.addEventListener("click", () => { switchView(view); });
    scroll.appendChild(button);
  }
  nav.appendChild(scroll);
  const help = document.createElement("button");
  help.type = "button";
  help.className = "panel-nav-help";
  help.textContent = "?";
  help.setAttribute("aria-label", "打开帮助");
  help.title = "打开帮助：四库怎么用、数据边界与快捷键（按 ? 打开快捷键总览）";
  help.addEventListener("click", () => { goTo("help"); });
  nav.appendChild(help);
  const search = document.createElement("button");
  search.type = "button";
  search.className = "panel-nav-search";
  search.textContent = "搜索";
  search.setAttribute("aria-label", "在四库中搜索");
  search.setAttribute("aria-keyshortcuts", "Control+K Meta+K");
  search.title = "在四库中搜索（Ctrl/⌘ K）";
  search.addEventListener("click", () => { openGlobalSearch(); });
  nav.appendChild(search);
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
  workbench: ["workbench", ""], today: ["workbench", ""],
  identity: ["self", ""], self: ["self", ""], profile: ["self", ""], memory: ["self", ""],
  bazi: ["self", ""], goals: ["life", ""], habits: ["life", ""],
  life: ["life", ""], ops: ["ops", ""],
  brain: ["knowledge", ""], knowledge: ["knowledge", ""], documents: ["knowledge", ""],
  notes: ["knowledge", ""], archive: ["knowledge", ""], settings: ["settings", ""],
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
    Object.keys(MODULES).forEach((name) => document.body.classList.remove(`view-${name}`));
    document.body.classList.add(`view-${view}`);
  }
  document.querySelectorAll(".nav-item, .panel-nav-item").forEach((button) => { const active = button.dataset.view === view; button.classList.toggle("active", active); if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current"); });
  // 小屏顶部标签栏是横向滚动：切页后把当前标签带到可见范围。
  document.querySelector(".panel-nav-scroll .panel-nav-item.active")?.scrollIntoView?.({ block: "nearest", inline: "center" });
  // 两级导航：侧边栏选模块，模块内再用 #module-nav 选分区。
  const module = MODULES[view];
  const panes = module?.panes || [];
  // 同页锚点模式每次进入模块都会滚回顶部，菜单高亮也回到第一段；
  // 普通模块则保留上次选择的有效分区。
  if (module?.stacked) {
    paneChoice[view] = panes[0]?.[0] || "";
  } else if (!(paneChoice[view] && panes.some(([id]) => id === paneChoice[view]))) {
    paneChoice[view] = panes[0]?.[0] || "";
  }
  const wanted = module?.stacked ? panes.map(([id]) => id) : [paneChoice[view]].filter(Boolean);
  document.querySelectorAll(".view, .pane").forEach((section) => {
    const shown = wanted.includes(section.id);
    // 模块根也是某个分区时（工作台的 #workbench-view），只有它被选中才显示；
    // 否则切到「与神对话」会把任务页一起留在页面上。
    const root = section.id === `${view}-view`;
    const active = root && (!panes.length || shown);
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
  const current = paneChoice[view] || module.panes[0]?.[0] || "";
  bar.hidden = false;
  bar.innerHTML = module.panes.map(([id, label]) => {
    const active = id === current;
    return `<button type="button" class="module-nav-item${active ? " active" : ""}" data-pane="${id}" aria-current="${active ? "true" : "false"}">${escapeHtml(label)}</button>`;
  }).join("");
  bar.querySelectorAll("[data-pane]").forEach((button) => button.addEventListener("click", () => {
    activatePane(view, button.dataset.pane);
  }));
}

/** 只同步二级菜单的 active/aria-current，不重建按钮（滚动监听会频繁调用）。 */
function syncModuleNavActive(view) {
  const module = MODULES[view];
  if (!module || !module.panes.length) return;
  const current = paneChoice[view] || module.panes[0]?.[0] || "";
  document.querySelectorAll("#module-nav [data-pane]").forEach((button) => {
    const active = button.dataset.pane === current;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "true" : "false");
  });
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
    syncModuleNavActive(view);
    if (scroll) document.getElementById(paneId)?.scrollIntoView({ behavior: "auto", block: "start" });
    return true;
  }
  if (paneId !== paneChoice[view] && !(await confirmLeaving(view))) return false;
  paneChoice[view] = paneId;
  renderSidebarState(view);
  renderModuleNav(view);
  // 工作台根是 .view 而不是 .pane；两个分区切换时根也必须一起隐藏/显示。
  document.querySelectorAll(`[data-module="${view}"], #${view}-view`).forEach((pane) => {
    const on = pane.id === paneId;
    pane.hidden = !on;
    if (pane.classList?.contains("view")) pane.classList.toggle("active-view", on);
  });
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

/**
 * 工作台两个分区在同一页：滚动到哪一段，二级菜单就高亮哪一项。
 * 阈值取“导航栏下沿 + 24”和视口 40% 的较大值，dsh 很长时滑到底也能正确高亮。
 */
let workbenchAnchorTimer = 0;

function syncWorkbenchAnchor() {
  if (state.view !== "workbench") return;
  const module = MODULES.workbench;
  const panes = module?.panes || [];
  if (!module?.stacked || !panes.length) return;
  const nav = document.getElementById("module-nav");
  const navBottom = nav?.getBoundingClientRect?.().bottom || 0;
  const viewport = Number(window.innerHeight) || 900;
  const threshold = Math.max(navBottom + 24, Math.floor(viewport * 0.4));
  let current = panes[0][0];
  for (const [id] of panes) {
    const section = document.getElementById(id);
    if (!section || section.hidden) continue;
    const rect = section.getBoundingClientRect?.();
    if (rect && rect.top <= threshold) current = id;
  }
  if (paneChoice.workbench === current) return;
  paneChoice.workbench = current;
  syncModuleNavActive("workbench");
  renderSidebarState("workbench");
}

function scheduleWorkbenchAnchor() {
  if (state.view !== "workbench" || workbenchAnchorTimer) return;
  workbenchAnchorTimer = window.setTimeout(() => {
    workbenchAnchorTimer = 0;
    syncWorkbenchAnchor();
  }, 100);
}

initSettings(); initInbox();
initWorkbench(); initPillars();
initKeyboardShortcuts({ navigate: (target) => goTo(target) });
bindSidebarPanes();
initHelp();
initVaultConnection();
initVaultAdmin();
initWorkspaces();
initDataHandbook();
initToolCatalog();
initRestoreDrill();
initDidaSync();
applyNavOrder();
renderPanelNav();
document.addEventListener("click", async (event) => {
  const navigation = event.target.closest("[data-view], [data-go-view]");
  if (navigation) {
    await goTo(navigation.dataset.view || navigation.dataset.goView);
  }
});
// 滴答同步完成后（可能在设置页触发）让工作台任务立即重读一次。
document.addEventListener("torch:tasks-changed", () => {
  if (state.view !== "workbench") return;
  import("./time-module.js").then(({ loadTimeTasks }) => loadTimeTasks()).catch(() => {});
});
// 深链接与前进后退能去的地方：模块名、GO_TARGETS 里的旧名字，以及并进设置区的 help。
// 0.96 把帮助移进设置后，门只认前两类，#help 会被静默忽略——探针因此在帮助步骤超时。
const navigable = (view) => view === "help" || Object.hasOwn(titles, view) || Object.hasOwn(GO_TARGETS, view);
window.addEventListener("hashchange", async () => { const view = location.hash.slice(1); if (navigable(view)) await goTo(view, false); });
window.addEventListener("popstate", async () => { const view = location.hash.slice(1); if (!view || navigable(view)) await goTo(view || "workbench", false); });
window.addEventListener("online", loadSettings);
// 同页锚点的滚动高亮：只监听，不改变路由或滚动位置。
window.addEventListener("scroll", scheduleWorkbenchAnchor, { passive: true });
window.addEventListener("resize", scheduleWorkbenchAnchor);
loadSettings();
const bootView = location.hash.slice(1);
goTo(Object.hasOwn(titles, bootView) || GO_TARGETS[bootView] ? bootView : "workbench");
