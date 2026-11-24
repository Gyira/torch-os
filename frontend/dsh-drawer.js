import { loadDshPane, suspendDshPane } from "./dsh-pane.js";
import { toast } from "./core.js";
import { editArchiveEntry } from "./self-entry-editor.js";

const el = id => document.getElementById(id);
let previousFocus;

export function openDshDrawer(context = "") {
  const drawer = el("global-dsh");
  if (drawer.hidden) {
    previousFocus = document.activeElement;
    drawer.hidden = false;
    el("dsh-scrim").hidden = false;
    const tools = el("dsh-tools");
    tools.setAttribute("popover", "manual");
    tools.showPopover?.();
    document.body.classList.add("dsh-drawer-open");
    el("dsh-tab").setAttribute("aria-expanded", "true");
    el("dsh-close").focus({ preventScroll: true });
    loadDshPane();
  }
  if (context) {
    const input = el("dsh-context-text");
    input.value = [input.value, context].filter(Boolean).join("\n\n");
    el("dsh-context").hidden = false;
  }
}

function closeDrawer() {
  el("global-dsh").hidden = true;
  el("dsh-scrim").hidden = true;
  el("dsh-tools").hidePopover?.();
  el("dsh-tools").removeAttribute("popover");
  document.body.classList.remove("dsh-drawer-open");
  el("dsh-tab").setAttribute("aria-expanded", "false");
  suspendDshPane();
  (previousFocus?.getClientRects().length ? previousFocus : el("dsh-tab")).focus({ preventScroll: true });
}

function insertContext() {
  const doc = el("dsh-frame").contentDocument;
  const input = doc && [...doc.querySelectorAll('textarea, [contenteditable="true"]')].find(
    node => node.getClientRects().length && !node.disabled && !node.readOnly);
  if (!input) {
    el("dsh-context-text").select();
    toast("资料已选中，可复制后粘贴到 DSH 输入框。");
    return;
  }
  const text = el("dsh-context-text").value;
  input.focus();
  if (input.tagName === "TEXTAREA") {
    const setter = Object.getOwnPropertyDescriptor(doc.defaultView.HTMLTextAreaElement.prototype, "value").set;
    setter.call(input, [input.value, text].filter(Boolean).join("\n\n"));
    input.dispatchEvent(new doc.defaultView.Event("input", { bubbles: true }));
  } else {
    doc.execCommand("insertText", false, text);
  }
}

function initResize() {
  const drawer = el("global-dsh");
  const grip = el("dsh-grip");
  let percent = 100;
  try { percent = Number(localStorage.getItem("torch:dsh-width")) || 100; } catch {}
  function resize(value) {
    const min = Math.min(100, Math.ceil(320 / Math.max(1, innerWidth) * 100));
    percent = Math.max(min, Math.min(100, value));
    drawer.style.setProperty("--drawer-width", percent + "vw");
    grip.setAttribute("aria-valuemin", String(min));
    grip.setAttribute("aria-valuenow", String(Math.round(percent)));
    grip.setAttribute("aria-valuetext", Math.round(percent) + "%");
  }
  function save() {
    try { localStorage.setItem("torch:dsh-width", String(percent)); } catch {}
  }
  resize(percent);
  window.addEventListener("resize", () => resize(percent));
  let dragId = null;
  function move(event) {
    if (event.pointerId !== dragId) return;
    resize(100 * (innerWidth - event.clientX) / innerWidth);
  }
  function stop(event) {
    if (event.pointerId != null && event.pointerId !== dragId) return;
    dragId = null;
    drawer.classList.remove("resizing");
    window.removeEventListener("pointermove", move, true);
    window.removeEventListener("pointerup", stop, true);
    window.removeEventListener("pointercancel", stop, true);
    window.removeEventListener("blur", stop);
    save();
  }
  grip.onpointerdown = event => {
    if (event.button !== 0 || dragId !== null) return;
    event.preventDefault();
    dragId = event.pointerId;
    drawer.classList.add("resizing");
    grip.setPointerCapture(event.pointerId);
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", stop, true);
    window.addEventListener("pointercancel", stop, true);
    window.addEventListener("blur", stop);
  };
  grip.ondblclick = () => { resize(100); save(); };
  grip.onkeydown = event => {
    const widths = { ArrowLeft: percent + 5, ArrowRight: percent - 5, End: 100, Home: 0 };
    if (!(event.key in widths)) return;
    event.preventDefault();
    resize(widths[event.key]);
    save();
  };
}

export function initDshDrawer() {
  const tools = el("dsh-tools");
  // Keep the same browsing context when a modal puts the rest of Torch outside the focus scope.
  function followModal() {
    const modal = [...document.querySelectorAll("dialog:modal")].at(-1);
    const parent = modal || document.body;
    if (tools.parentElement !== parent && parent.moveBefore) {
      parent.moveBefore(tools, null);
      if (!el("global-dsh").hidden && !tools.matches(":popover-open")) tools.showPopover?.();
    }
  }
  new MutationObserver(followModal).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["open"] });
  followModal();
  el("dsh-tab").onclick = () => openDshDrawer();
  initResize();
  el("dsh-scrim").onclick = closeDrawer;
  el("dsh-close").onclick = closeDrawer;
  el("dsh-dismiss-context").onclick = () => {
    el("dsh-context-text").value = "";
    el("dsh-context").hidden = true;
  };
  el("dsh-insert").onclick = insertContext;
  el("dsh-save-excerpt").onclick = () => {
    const quote = el("dsh-frame").contentDocument?.getSelection()?.toString() || "";
    closeDrawer();
    editArchiveEntry({ text: quote, entry_type: "inference", sources: ["DSH 对话摘录"] });
  };
}
