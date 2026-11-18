import { api } from "./core.js";
import { buildNotebookCards } from "./notebook-model.js";

/** Notebook menus share the Ops menu surface; the primary button still opens its home. */
export function buildNotebookNav(trigger, view, navigate, choices = null) {
  const family = document.createElement("div");
  family.className = "panel-nav-family"; family.dataset.navFamily = view;
  const menu = document.createElement("div");
  menu.className = "panel-nav-menu notebook-nav-menu";
  menu.id = `panel-nav-${view}-menu`; menu.hidden = true;
  menu.setAttribute("role", "group"); menu.setAttribute("aria-label", `${trigger.textContent}本子`);
  trigger.classList.add("panel-nav-trigger");
  trigger.setAttribute("aria-controls", menu.id); trigger.setAttribute("aria-expanded", "false");
  const caret = document.createElement("span"); caret.className = "panel-nav-caret"; caret.setAttribute("aria-hidden", "true"); trigger.append(caret);
  family.append(trigger);
  const card = document.createElement("div"); card.className = "panel-nav-menu-card"; menu.append(card);
  let timer = 0, cache = null, pending = null, generation = 0;
  function position() {
    const nav = family.closest(".panel-nav");
    if (!nav || menu.hidden) return;
    const rect = trigger.getBoundingClientRect(), box = nav.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(rect.left - box.left - nav.clientLeft, nav.clientWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${rect.bottom - box.top - nav.clientTop}px`;
  }
  function close(focus = false) {
    clearTimeout(timer); menu.hidden = true; trigger.setAttribute("aria-expanded", "false"); family.classList.remove("menu-open");
    if (focus) trigger.focus({ preventScroll: true });
  }
  function render(payload) {
    const books = choices || buildNotebookCards(view, payload.chapters, payload.custom_notebooks, payload.deleted_notebooks);
    card.replaceChildren();
    for (const book of books) {
      const button = document.createElement("button"); button.type = "button";
      button.className = "panel-nav-item panel-nav-system"; button.dataset.navNotebook = book.key;
      const label = document.createElement("span"); label.textContent = payload.notebook_titles?.[book.key] || book.title;
      const count = document.createElement("small"); count.textContent = choices ? "" : `${book.count} 篇`;
      button.append(label, count);
      button.addEventListener("click", async () => {
        close(); await navigate(view, book.key);
      });
      card.append(button);
    }
  }
  async function open(focus = false) {
    clearTimeout(timer);
    document.dispatchEvent(new CustomEvent("torch:nav-menu-open", { detail: view }));
    menu.hidden = false; family.classList.add("menu-open"); trigger.setAttribute("aria-expanded", "true");
    if (choices) render({});
    else if (cache) render(cache);
    else {
      card.textContent = "正在读取本子…"; position();
      const version = generation;
      try {
        if (!pending) pending = api(`/api/pillars/${view}/overview`).finally(() => { pending = null; });
        const payload = await pending;
        if (version !== generation) return;
        cache = payload; render(payload);
      } catch {
        card.textContent = "读取失败，请重新展开重试。";
      }
    }
    if (menu.hidden) return;
    position(); if (focus) card.querySelector("button")?.focus({ preventScroll: true });
  }
  const later = () => { timer = setTimeout(() => close(), 180); };
  family.addEventListener("pointerenter", (event) => { if (event.pointerType !== "touch") open(); });
  family.addEventListener("pointerleave", later);
  menu.addEventListener("pointerenter", () => clearTimeout(timer)); menu.addEventListener("pointerleave", later);
  family.addEventListener("click", (event) => {
    if (event.target === caret) { event.stopPropagation(); if (menu.hidden) open(true); else close(); }
    else close();
  });
  const keys = (event) => {
    if (event.key === "Escape" && !menu.hidden) { event.preventDefault(); close(true); }
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    if (menu.hidden) { open(true); return; }
    const buttons = [...card.querySelectorAll("button")], index = buttons.indexOf(document.activeElement);
    buttons[(index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length]?.focus();
  };
  family.addEventListener("keydown", keys); menu.addEventListener("keydown", keys);
  document.addEventListener("pointerdown", (event) => { if (!family.contains(event.target) && !menu.contains(event.target)) close(); });
  document.addEventListener("focusin", (event) => { if (!family.contains(event.target) && !menu.contains(event.target)) close(); });
  document.addEventListener("torch:nav-menu-open", (event) => { if (event.detail !== view) close(); });
  document.addEventListener("data-changed", () => { cache = null; generation++; pending = null; close(); });
  document.addEventListener("torch:library-rendered", (event) => {
    if (event.detail?.view === view && event.detail.overview) {
      cache = event.detail.overview; generation++;
      if (!menu.hidden) { render(cache); position(); }
    }
  });
  document.addEventListener("torch:notes-changed", () => { cache = null; generation++; pending = null; });
  window.addEventListener("resize", position);
  document.addEventListener("scroll", position, true);
  return { node: family, menu };
}
