// 设置 → 工作台小组件（2026-10-29，v1.0 P1）。
// 开关只改本机显隐；顺序仍由 widget-drag.js 维护。两者都走 personal-data.js 镜像到 .torch-os。
import { $ } from "./core.js";
import { WIDGET_MANIFEST } from "./widget-registry.js";
import {
  clearWidgetHidden, readStoredWidgetHidden, resolveWidgetHidden, storeWidgetHidden,
} from "./widget-order.js";

let bound = false;

export function initWidgetSettings() {
  if (bound) return;
  bound = true;
  $("#widget-settings-reset")?.addEventListener("click", () => {
    clearWidgetHidden();
    renderWidgetSettings("已恢复默认：全部小组件显示。");
    document.dispatchEvent(new CustomEvent("torch:widgets-changed"));
  });
  $("#widget-settings-list")?.addEventListener("change", (event) => {
    const input = event.target.closest("[data-widget-toggle]");
    if (!input) return;
    const key = input.dataset.widgetToggle;
    const hidden = new Set(currentHidden());
    if (input.checked) hidden.delete(key);
    else hidden.add(key);
    storeWidgetHidden(Array.from(hidden));
    renderWidgetSettings(input.checked
      ? `“${labelOf(key)}”已显示。`
      : `“${labelOf(key)}”已隐藏，工作台不会再请求它。`);
    document.dispatchEvent(new CustomEvent("torch:widgets-changed", {
      detail: { key, hidden: !input.checked },
    }));
  });
  renderWidgetSettings();
}

export function loadWidgetSettings() {
  renderWidgetSettings();
}

function currentHidden() {
  return resolveWidgetHidden(readStoredWidgetHidden());
}

function labelOf(key) {
  return WIDGET_MANIFEST.find((item) => item.key === key)?.label || key;
}

function renderWidgetSettings(message = "") {
  const list = $("#widget-settings-list");
  if (!list) return;
  const hidden = new Set(currentHidden());
  list.innerHTML = WIDGET_MANIFEST.map((item) => {
    const checked = hidden.has(item.key) ? "" : " checked";
    return `<li data-widget-setting="${item.key}">
      <div><strong>${escapeText(item.label)}</strong><small>${escapeText(item.description)}</small></div>
      <label><input type="checkbox" data-widget-toggle="${item.key}"${checked} />显示</label>
    </li>`;
  }).join("");
  const feedback = $("#widget-settings-feedback");
  if (feedback && message) feedback.textContent = message;
}

function escapeText(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));
}
