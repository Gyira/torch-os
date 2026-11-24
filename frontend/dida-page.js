import { isObsidianHost } from "./task-open.js";

let initialized = false;
let lastBounds = "";

function hostMessage(payload) {
  window.parent.postMessage({ type: "torch:dida", ...payload }, "*");
}

export function showDidaPage() {
  if (!isObsidianHost() || initialized) return;
  initialized = true;
  const stage = document.querySelector("#time-dida-stage");
  const status = document.querySelector("#time-dida-message");
  const reload = document.querySelector("#time-dida-reload");
  const external = document.querySelector("#time-dida-external");
  status.textContent = "正在连接桌面内嵌浏览器…";
  reload.hidden = false;
  external.addEventListener("click", event => {
    event.preventDefault();
    window.parent.postMessage({ type: "torch:open-url", url: external.href }, "*");
  });
  window.addEventListener("message", event => {
    if (event.source === window.parent && event.data?.type === "torch:dida-state") {
      status.textContent = event.data.message;
    }
  });

  function reportBounds() {
    const box = stage.getBoundingClientRect();
    const nav = document.querySelector(".top-nav-mode .panel-nav")?.getBoundingClientRect();
    const banner = document.querySelector(".service-banner")?.getBoundingClientRect();
    const x = Math.max(0, box.left);
    const y = Math.max(0, box.top, nav?.bottom || 0, banner?.bottom || 0);
    const width = Math.min(innerWidth, box.right) - x;
    const height = Math.min(innerHeight, box.bottom) - y;
    const obscured = document.hidden || document.querySelector("dialog[open], .panel-nav-menu:not([hidden])");
    const rect = width > 0 && height > 0 && !obscured ? { x, y, width, height } : null;
    const key = JSON.stringify(rect);
    if (key !== lastBounds) {
      lastBounds = key;
      hostMessage({ action: "bounds", rect });
    }
  }
  reload.addEventListener("click", () => {
    hostMessage({ action: "reload" });
    lastBounds = "";
    reportBounds();
  });
  new ResizeObserver(reportBounds).observe(stage);
  new IntersectionObserver(reportBounds).observe(stage);
  new MutationObserver(reportBounds).observe(document.body, {
    subtree: true, attributes: true, attributeFilter: ["hidden", "open"],
  });
  document.addEventListener("scroll", reportBounds, true);
  document.addEventListener("visibilitychange", reportBounds);
  window.addEventListener("resize", reportBounds);
  reportBounds();
}
