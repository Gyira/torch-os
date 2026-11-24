import { $, state, api, escapeHtml } from "./core.js";
import { initSettings, loadSettings } from "./settings.js";
import { loadAgentPane } from "./agent-pane.js";
import { initLovePane, loadLovePane } from "./love.js";
import { initHealthPane, loadHealthPane } from "./health.js";
import { NAV_LABELS, applyNavOrder, currentNavOrder, visiblePaneIds } from "./nav-order.js";
import { initThemeBridge } from "./theme-bridge.js";
import { initVaultConnection } from "./vault-connection.js";
import { initVaultAdmin } from "./vault-admin.js";
import { initWorkspaces } from "./workspaces.js";
import { initDataHandbook } from "./data-handbook.js";
import { initToolCatalog } from "./tool-catalog.js";
import { initRestoreDrill } from "./restore-drill.js";
import { initWorkbench, loadWorkbench } from "./workbench.js";
import { initDidaSync } from "./dida-sync.js";
import { initPillars, loadPillarModule, openLibraryNotebook } from "./pillars.js";
import { buildNotebookNav } from "./notebook-nav.js";
import { initSelf, loadSelf } from "./self.js";
import { initMentorStrip } from "./mentor-strip.js";
import { openGlobalSearch } from "./global-search.js";
import { initKeyboardShortcuts } from "./shortcuts.js";
import { initPersonalData } from "./personal-data.js";
import { initRoleDrawer } from "./roles.js";
import { initDshDrawer, openDshDrawer } from "./dsh-drawer.js";
import { initSelectionAsk } from "./selection-ask.js";
import { initRecycleCenter, loadRecycleCenter } from "./recycle-center.js";
import { initMaterialMotion } from "./material-motion.js";
import { initDataCenter, loadDataCenter } from "./data-center.js";
import { initContextSettings } from "./context-settings.js";
import { initServiceGate } from "./service-gate.js";

const titles = NAV_LABELS;

// Keep existing element identities and event bindings while giving these pages their own route.
const mainPages = $("#main-content");
for (const name of ["agent", "recycle"]) {
  const page = $(`#${name}-view`);
  if (page && mainPages) {
    page.classList.remove("pane", "workbench-agent", "workbench-recycle");
    page.classList.add("view", `standalone-${name}`);
    page.dataset.module = name;
    mainPages.append(page);
  }
}
initDataCenter();

// 顶层页面独立路由；生活运营保留系统分区，AI 角色管理归入设置中心。
const MODULES = {
  workbench: { panes: [], ownNav: true },
  agent: { panes: [], ownNav: true },
  recycle: { panes: [], ownNav: true },
  // 四库：模块内部由 pillars.js 自己的章节轨导航，不再使用通用二级菜单。
  life: { panes: [], ownNav: true },
  self: { panes: [], ownNav: true },
  // 生活运营保留自己的章节轨，同时在模块顶部多「恋爱系统 / 健康系统」两个二级分区。
  ops: { stacked: false, panes: [["ops-view", "生活运营"], ["love-view", "恋爱系统"], ["health-view", "健康系统"]] },
  knowledge: { panes: [], ownNav: true },
  settings: { panes: [], ownNav: true },
  data: { panes: [], ownNav: true },
};
const paneChoice = {};
const PANE_GUARDS = {};
const MODULE_GUARDS = {};
const PANE_LOADERS = {
  "workbench-view": () => loadWorkbench(),
  "agent-view": loadAgentPane,
  "recycle-view": () => { initRecycleCenter(); loadRecycleCenter(); },
  "love-view": () => { initLovePane(); loadLovePane(); },
  "health-view": () => { initHealthPane(); loadHealthPane(); },
  "life-view": () => loadPillarModule("life"),
  "self-view": () => { initSelf(); loadSelf(); },
  "ops-view": () => loadPillarModule("ops"),
  "knowledge-view": () => loadPillarModule("knowledge"),
  "settings-view": loadDataCenter,
  "data-view": loadDataCenter,
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

// 顶栏「生活运营」是一个悬停展开的分组（2026-11-18 用户反馈）：健康／恋爱不再各占一个
// 顶栏按钮，而是收进「生活运营」的下拉层；鼠标停上去就展开，方向键也能打开。
const OPS_MENU_ITEMS = [["health", "健康系统"], ["love", "恋爱系统"]];
const OPS_MENU_CLOSE_DELAY = 180; // 穿过按钮与下拉层之间的空隙时不闪断。
const OPS_MENU_ID = "panel-nav-ops-menu";
let opsMenuCloseTimer = 0;

function opsNavFamily() {
  return document.querySelector('.panel-nav-family[data-nav-family="ops"]');
}

function opsNavMenu() {
  return document.getElementById(OPS_MENU_ID);
}

/**
 * 下拉层贴着「生活运营」按钮下沿打开；右侧放不下时向左收，窄屏也不会溢出。
 * 绝对定位的包含块是顶栏（.panel-nav）的 padding box：先扣掉边框宽度再换算坐标。
 */
function positionOpsMenu() {
  const family = opsNavFamily();
  const menu = opsNavMenu();
  const nav = family?.closest(".panel-nav");
  const trigger = family?.querySelector(".panel-nav-trigger");
  if (!nav || !menu || !trigger || menu.hidden) return;
  const triggerBox = trigger.getBoundingClientRect();
  const navBox = nav.getBoundingClientRect();
  const originLeft = navBox.left + (nav.clientLeft || 0);
  const originTop = navBox.top + (nav.clientTop || 0);
  const width = menu.offsetWidth || 168;
  const limit = Math.max(8, nav.clientWidth - width - 8);
  menu.style.left = `${Math.round(Math.min(Math.max(8, triggerBox.left - originLeft), limit))}px`;
  menu.style.top = `${Math.round(triggerBox.bottom - originTop)}px`;
}

function openOpsMenu() {
  document.dispatchEvent(new CustomEvent("torch:nav-menu-open", { detail: "ops" }));
  const family = opsNavFamily();
  const menu = opsNavMenu();
  const trigger = family?.querySelector(".panel-nav-trigger");
  if (!family || !menu || !trigger) return;
  window.clearTimeout(opsMenuCloseTimer);
  opsMenuCloseTimer = 0;
  if (menu.hidden) {
    menu.hidden = false;
    family.classList.add("menu-open");
    trigger.setAttribute("aria-expanded", "true");
  }
  positionOpsMenu();
}

function closeOpsMenu({ restoreFocus = false } = {}) {
  const family = opsNavFamily();
  const menu = opsNavMenu();
  const trigger = family?.querySelector(".panel-nav-trigger");
  window.clearTimeout(opsMenuCloseTimer);
  opsMenuCloseTimer = 0;
  if (!menu || menu.hidden) return;
  const hadFocus = menu.contains(document.activeElement);
  menu.hidden = true;
  family?.classList.remove("menu-open");
  trigger?.setAttribute("aria-expanded", "false");
  if (trigger && (restoreFocus || hadFocus)) trigger.focus({ preventScroll: true });
}

function scheduleOpsMenuClose() {
  window.clearTimeout(opsMenuCloseTimer);
  opsMenuCloseTimer = window.setTimeout(() => { opsMenuCloseTimer = 0; closeOpsMenu(); }, OPS_MENU_CLOSE_DELAY);
}

/**
 * 「生活运营」按钮 + 下拉层。下拉层是顶栏的兄弟节点而不是标签条的子孙：标签条要横向
 * 滚动，overflow 会把绝对定位的下拉层裁掉。
 */
function buildOpsFamily(trigger) {
  const family = document.createElement("div");
  family.className = "panel-nav-family";
  family.dataset.navFamily = "ops";
  family.setAttribute("role", "group");
  family.setAttribute("aria-label", "生活运营及常用系统");
  trigger.classList.add("panel-nav-trigger");
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute("aria-controls", OPS_MENU_ID);
  const caret = document.createElement("span");
  caret.className = "panel-nav-caret";
  caret.setAttribute("aria-hidden", "true");
  trigger.append(caret);
  family.append(trigger);

  const menu = document.createElement("div");
  menu.className = "panel-nav-menu";
  menu.id = OPS_MENU_ID;
  menu.hidden = true;
  menu.setAttribute("role", "group");
  menu.setAttribute("aria-label", "生活运营下的系统");
  const card = document.createElement("div");
  card.className = "panel-nav-menu-card";
  for (const [target, label] of OPS_MENU_ITEMS) {
    const shortcut = document.createElement("button");
    shortcut.type = "button";
    shortcut.className = "panel-nav-item panel-nav-system";
    // 走 data-go-view 而不是 data-view：下拉项不是顶栏一级模块，不参与导航排序，
    // 也不该被一级按钮的选中项循环当成模块。
    shortcut.dataset.goView = target;
    shortcut.dataset.system = target;
    shortcut.textContent = label;
    shortcut.setAttribute("aria-label", `打开${label}`);
    card.append(shortcut);
  }
  menu.append(card);
  return { node: family, menu };
}

/** 悬停、方向键、Esc、焦点移出：下拉层的全部打开／收起入口。 */
function bindOpsMenu() {
  document.addEventListener("torch:nav-menu-open", (event) => { if (event.detail !== "ops") closeOpsMenu(); });
  const family = opsNavFamily();
  const menu = opsNavMenu();
  const trigger = family?.querySelector(".panel-nav-trigger");
  if (!family || !menu || !trigger) return;
  const items = () => Array.from(menu.querySelectorAll(".panel-nav-system"));
  const cancelClose = () => { window.clearTimeout(opsMenuCloseTimer); opsMenuCloseTimer = 0; };
  // 只有真悬停才自动展开：触摸设备上 pointerenter 会在点按瞬间闪现，随即又被收起。
  family.addEventListener("pointerenter", (event) => { if (event.pointerType !== "touch") openOpsMenu(); });
  family.addEventListener("pointerleave", scheduleOpsMenuClose);
  menu.addEventListener("pointerenter", (event) => { if (event.pointerType !== "touch") cancelClose(); });
  menu.addEventListener("pointerleave", scheduleOpsMenuClose);
  const onKeydown = (event) => {
    const open = !menu.hidden;
    if (event.key === "Escape") {
      if (!open) return;
      event.preventDefault();
      closeOpsMenu({ restoreFocus: true });
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      // 顶栏方向键的次序由 shortcuts.js 统一处理；这里把「生活运营」这一格变成展开／收起。
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      const list = items();
      if (!open) {
        openOpsMenu();
        list[step > 0 ? 0 : list.length - 1]?.focus?.({ preventScroll: true });
        return;
      }
      const index = list.indexOf(document.activeElement);
      const next = index < 0 ? (step > 0 ? 0 : list.length - 1) : (index + step + list.length) % list.length;
      list[next]?.focus?.({ preventScroll: true });
      return;
    }
    if (event.key === "Tab") {
      window.setTimeout(() => {
        if (!family.contains(document.activeElement) && !menu.contains(document.activeElement)) closeOpsMenu();
      }, 0);
    }
  };
  family.addEventListener("keydown", onKeydown);
  menu.addEventListener("keydown", onKeydown);
  // 点完系统入口就收起；焦点离开按钮与下拉层（Tab、点到别处）也收起。
  menu.addEventListener("click", (event) => { if (event.target.closest("[data-go-view]")) closeOpsMenu(); });
  const onFocusOut = (event) => {
    if (event.relatedTarget && (family.contains(event.relatedTarget) || menu.contains(event.relatedTarget))) return;
    window.setTimeout(() => {
      if (!family.contains(document.activeElement) && !menu.contains(document.activeElement)) closeOpsMenu();
    }, 0);
  };
  family.addEventListener("focusout", onFocusOut);
  menu.addEventListener("focusout", onFocusOut);
  // 键盘打开后没有"指针移开"这回事：点页面上别的地方也要收起。
  document.addEventListener("pointerdown", (event) => {
    if (menu.hidden) return;
    if (family.contains(event.target) || menu.contains(event.target)) return;
    closeOpsMenu();
  });
}

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
  let opsMenu = null;
  const notebookMenus = [];
  for (const view of currentNavOrder()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "panel-nav-item";
    button.dataset.view = view;
    button.textContent = titles[view] || view;
    // One delegated handler below owns navigation; a second handler doubles data loads.
    if (view === "ops") {
      const family = buildOpsFamily(button);
      opsMenu = family.menu;
      scroll.append(family.node);
    } else if (view === "self") {
      const family = buildNotebookNav(button, view, (_view, key) => goTo(key), [{key:"mentor",title:"经历与记忆"},{key:"bazi",title:"八字系统"}]);
      family.menu.setAttribute("aria-label", "自我档案系统");
      scroll.append(family.node); notebookMenus.push(family.menu);
    } else if (["life", "knowledge"].includes(view)) {
      const family = buildNotebookNav(button, view, async (target, key) => {
        try { if (await goTo(target)) await openLibraryNotebook(target, key); }
        catch (error) { toast(error.message, "error"); }
      });
      scroll.append(family.node); notebookMenus.push(family.menu);
    } else scroll.appendChild(button);
  }
  nav.appendChild(scroll);
  if (opsMenu) nav.append(opsMenu);
  nav.append(...notebookMenus);
  const help = document.createElement("button");
  help.type = "button";
  help.className = "panel-nav-help";
  help.textContent = "⚙";
  help.dataset.contextSettings = "appearance";
  help.setAttribute("aria-haspopup", "dialog");
  help.setAttribute("aria-label", "调整导航顺序");
  help.title = "调整导航顺序";

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
  if (opsMenu) {
    bindOpsMenu();
    // 标签条横向滚动、窗口缩放时，展开中的下拉层要跟着按钮走。
    scroll.addEventListener("scroll", () => { if (!opsMenu.hidden) positionOpsMenu(); }, { passive: true });
  }
  // 顶部标签条吸顶（2026-10-05）：把它的实际高度写进 --panel-nav-h，
  // 模块内导航（.module-nav）据此下移，两条吸顶条不会互相盖住。
  const syncHeight = () => {
    document.documentElement.style.setProperty("--panel-nav-h", `${nav.offsetHeight}px`);
    if (opsMenu && !opsMenu.hidden) positionOpsMenu();
  };
  syncHeight();
  window.addEventListener("resize", syncHeight);
}
if (embedMode) {
  window.addEventListener("message", (event) => {
    const data = event?.data;
    if (!data || data.type !== "torch:navigate" || typeof data.view !== "string") return;
    if (!Object.hasOwn(titles, data.view) && data.view !== "roles") return;
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
  life: ["life", ""], ops: ["ops", "ops-view"],
  health: ["ops", "health-view"], love: ["ops", "love-view"],
  brain: ["knowledge", ""], knowledge: ["knowledge", ""], documents: ["knowledge", ""],
  notes: ["knowledge", ""], archive: ["knowledge", ""], settings: ["settings", ""],
  agent: ["agent", ""], dsh: ["agent", ""],
  roles: ["settings", ""], role: ["settings", ""], ai: ["settings", ""],
  recycle: ["recycle", ""],
};

export async function showPane(view, paneId, updateHash = true) {
  if (paneId === "agent-view" || paneId === "recycle-view") return switchView(paneId.replace("-view", ""), updateHash);
  const previous = paneChoice[view];
  if (MODULES[view]?.panes?.some(([id]) => id === paneId)) paneChoice[view] = paneId;
  if (!(await switchView(view, updateHash))) { paneChoice[view] = previous; return false; }
  return true;
}

async function goTo(target, updateHash = true) {
  if (target === "dsh") { openDshDrawer(); return true; }
  if (target === "chatgpt") return switchView("agent", updateHash);
  if (["roles", "role", "ai"].includes(target)) return switchView("agent", updateHash);
  if (["settings", "data"].includes(target)) return switchView("data", updateHash);
  if (target === "help") return switchView("workbench", updateHash);
  if (["bazi", "mentor"].includes(target)) {
    if (!(await switchView("self", updateHash))) return false;
    document.dispatchEvent(new CustomEvent("torch:self-page", {detail:{page:target, sub:target === "bazi" ? "chart" : "memory"}}));
    return true;
  }
  const mapped = GO_TARGETS[target];
  if (!mapped) return switchView(target);
  const [view, paneId] = mapped;
  return paneId ? showPane(view, paneId, updateHash) : switchView(view, updateHash);
}

export async function switchView(view, updateHash = true) {
  if (view === "roles") return switchView("agent", updateHash);
  if (view === "settings") return switchView("data", updateHash);
  if (!Object.hasOwn(titles, view)) return false;
  if (navigationPending) return false;
  if (state.view !== view && !(await confirmLeaving(state.view))) {
    if (location.hash !== `#${state.view}`) history.replaceState(null, "", `#${state.view}`);
    return false;
  }
  state.view = view;
  document.dispatchEvent(new CustomEvent("torch:nav-menu-open", { detail: null }));
  // 离开生活运营（或它的下拉分区）时把下拉层收起来，别让它悬在别的页面上。
  if (view !== "ops") closeOpsMenu();
  // 工作台不再摆顶部大标题（2026-10-05 用户反馈）：给 body 打一个 view-* 类，样式据此隐藏
  // 工作台的标题块。**不要用 data-view**——页面的点击委托按 `closest("[data-view]")` 找导航
  // 按钮，body 带上它会吞掉一切点击并触发一次“切到当前模块”（2026-10-05 实测踩到）。
  if (document.body?.classList) {
    Object.keys(MODULES).forEach((name) => document.body.classList.remove(`view-${name}`));
    document.body.classList.add(`view-${view}`);
  }
  document.querySelectorAll(".nav-item, .panel-nav-item").forEach((button) => { const active = button.dataset.view === (view === "roles" ? "agent" : view); button.classList.toggle("active", active); if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current"); });
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
  const wanted = visiblePaneIds(module, paneChoice[view]);
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
  // Pane selection determines the one active top-level button, including health/love.
  document.querySelector(".panel-nav-scroll .panel-nav-item.active")?.scrollIntoView?.({ block: "nearest", inline: "center" });
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
  return visiblePaneIds(module, paneChoice[view] || module.panes[0]?.[0] || "");
}

/** 离开可见分区前先问一次未保存的草稿（日记、习惯、角色管理）。 */
async function confirmLeaving(view) {
  const moduleGuard = MODULE_GUARDS[view];
  if (moduleGuard) {
    navigationPending = true;
    let allowed = false;
    try { allowed = await moduleGuard(); } finally { navigationPending = false; }
    if (!allowed) return false;
  }
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
  if (!module || module.ownNav || !module.panes.length || (topNavMode && view === "ops")) {
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
  const opsFamily = opsNavFamily();
  const opsButton = opsFamily?.querySelector(".panel-nav-trigger");
  if (opsButton) {
    // 健康／恋爱现在收在「生活运营」下拉里：顶栏只高亮这个入口，具体系统
    // 在下拉层里标 aria-current，展开时才看得到。
    const inOps = view === "ops";
    opsButton.classList.toggle("active", inOps);
    if (inOps && current === "ops-view") opsButton.setAttribute("aria-current", "page");
    else opsButton.removeAttribute("aria-current");
    opsFamily.classList.toggle("has-current-system", inOps && current !== "ops-view");
  }
  document.querySelectorAll(".panel-nav-system").forEach((button) => {
    const active = view === "ops" && current === `${button.dataset.system}-view`;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
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
    const wasPage = (module.pagePanes || []).includes(paneChoice[view]);
    const isPage = (module.pagePanes || []).includes(paneId);
    paneChoice[view] = paneId;
    renderSidebarState(view);
    renderModuleNav(view);
    const ids = visiblePaneIds(module, paneId);
    // 工作台根是 .view：切到独立的回收中心时根和对话区一起隐藏，切回时再一起显示。
    document.querySelectorAll(`[data-module="${view}"], #${view}-view`).forEach((pane) => {
      const on = ids.includes(pane.id);
      pane.hidden = !on;
      if (pane.classList?.contains("view")) pane.classList.toggle("active-view", on);
    });
    // 两个同页分区之间只滚动定位；只有进出独立页面时才重新加载分区数据。
    if (wasPage || isPage) runModuleLoaders(view, ids);
    if (scroll) {
      if (module.pagePanes?.includes(paneId)) {
        window.scrollTo({ top: 0, left: 0, behavior: "auto" });
      } else {
        document.getElementById(paneId)?.scrollIntoView({ behavior: "auto", block: "start" });
      }
    }
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
  // 回收中心是独立页面，不参与工作台的滚动高亮。
  if ((module.pagePanes || []).includes(paneChoice.workbench)) return;
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

// 顶层数据规则：小组件偏好等个性化数据以当前个人数据库的 .torch-os/personal-data.json 为准。
// 首屏不等待它：先用 localStorage 里的本机缓存把界面画出来，后台读完再补一次。
initServiceGate();
let personalDataSynced = false;
const refreshAfterPersonal = (result) => {
  if (!result?.synced) return;
  personalDataSynced = true;
  applyNavOrder();
  if (state.view === "workbench") loadWorkbench();
};
initPersonalData({ waitForService: false, timeoutMs: 1500 })
  .then(refreshAfterPersonal)
  .catch(() => null);
document.addEventListener("torch:service-ready", () => {
  loadSettings();
  if (!personalDataSynced) {
    initPersonalData({ waitForService: false, timeoutMs: 3000 })
      .then(refreshAfterPersonal)
      .catch(() => {});
  }
  if (state.view === "workbench") loadWorkbench();
});
initSettings();
initWorkbench(); initPillars();
initMentorStrip();
initRoleDrawer();
initDshDrawer();
initSelectionAsk();
initKeyboardShortcuts({ navigate: (target) => goTo(target) });
bindSidebarPanes();

initVaultConnection();
initVaultAdmin();
initWorkspaces();
initDataHandbook();
initToolCatalog();
initRestoreDrill();
initDidaSync();
applyNavOrder();
renderPanelNav();
initMaterialMotion();
initContextSettings();
document.addEventListener("click", async (event) => {
  const navigation = event.target.closest("[data-view], [data-go-view]");
  if (navigation) {
    await goTo(navigation.dataset.view || navigation.dataset.goView);
  }
});
// 生活运营等页面不直接 import app.js（避免循环依赖），用事件请求切换模块内分区。
document.addEventListener("torch:show-pane", async (event) => {
  const detail = event.detail || {};
  if (!detail.view || !detail.pane) return;
  const ok = await showPane(detail.view, detail.pane);
  if (ok && detail.focus) {
    document.querySelector(detail.focus)?.scrollIntoView({ behavior: "auto", block: "start" });
  }
});
// 工作台常驻条 / 其他模块跳进自我档案的某个子页（人生导师、八字、本子）。
document.addEventListener("torch:open-self-page", async (event) => {
  const detail = event.detail || {};
  if (!(await switchView("self"))) return;
  // 等 self.js 完成首帧（initSelf 绑定事件）再切子页，避免事件早到被丢掉。
  window.setTimeout(() => {
    document.dispatchEvent(new CustomEvent("torch:self-page", {
      detail: { page: detail.page || "archive", sub: detail.sub || "" },
    }));
  }, 0);
});
// 滴答同步完成后（可能在设置页触发）让工作台任务立即重读一次。
document.addEventListener("torch:tasks-changed", () => {
  if (state.view !== "workbench") return;
  import("./time-module.js").then(({ loadTimeTasks }) => loadTimeTasks()).catch(() => {});
});
// 深链接与前进后退能去的地方：模块名、GO_TARGETS 里的旧名字，以及并进设置区的 help。
// 0.96 把帮助移进设置后，门只认前两类，#help 会被静默忽略——探针因此在帮助步骤超时。
const navigable = (view) => ["mentor", "chatgpt"].includes(view) || view === "help" || Object.hasOwn(titles, view) || Object.hasOwn(GO_TARGETS, view);
window.addEventListener("hashchange", async () => { const view = location.hash.slice(1); if (navigable(view)) await goTo(view, false); });
window.addEventListener("popstate", async () => { const view = location.hash.slice(1); if (!view || navigable(view)) await goTo(view || "workbench", false); });
window.addEventListener("online", loadSettings);
// 同页锚点的滚动高亮：只监听，不改变路由或滚动位置。
window.addEventListener("scroll", scheduleWorkbenchAnchor, { passive: true });
window.addEventListener("resize", scheduleWorkbenchAnchor);
loadSettings();
const bootView = location.hash.slice(1);
goTo(navigable(bootView) ? bootView : "workbench");
