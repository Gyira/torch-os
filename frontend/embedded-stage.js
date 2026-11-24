// Clip native webviews to the visible part of their panel inside the workbench.
export function visibleStageRect(stage) {
  if (!stage || document.hidden || document.querySelector("dialog[open]")) return null;
  const box = stage.getBoundingClientRect();
  const nav = document.querySelector(".top-nav-mode .panel-nav")?.getBoundingClientRect();
  const banner = document.querySelector(".service-banner")?.getBoundingClientRect();
  const x = Math.max(0, box.left);
  const y = Math.max(0, box.top, nav?.bottom || 0, banner?.bottom || 0);
  const width = Math.min(innerWidth, box.right) - x;
  const height = Math.min(innerHeight, box.bottom) - y;
  if (width <= 0 || height <= 0) return null;
  const exclusions = [];
  for (const node of document.querySelectorAll(".panel-nav-menu:not([hidden]), #global-dsh:not([hidden]), #dsh-tab")) {
    const r = node.getBoundingClientRect();
    const left = Math.max(0, r.left - x), top = Math.max(0, r.top - y);
    const right = Math.min(width, r.right - x), bottom = Math.min(height, r.bottom - y);
    if (right > left && bottom > top) exclusions.push({ left, top, right, bottom });
  }
  return { x, y, width, height, exclusions, dim: document.body.classList.contains("dsh-drawer-open") };
}

export function observeStage(stage, report) {
  const sizes = new ResizeObserver(report);
  sizes.observe(stage);
  sizes.observe(document.querySelector("#global-dsh"));
  new IntersectionObserver(report).observe(stage);
  function update() {
    for (const menu of document.querySelectorAll(".panel-nav-menu:not([hidden])")) sizes.observe(menu);
    report();
  }
  new MutationObserver(update).observe(document.body, {
    subtree: true, attributes: true, attributeFilter: ["open", "hidden"],
  });
  const nav = document.querySelector(".panel-nav");
  if (nav) new MutationObserver(report).observe(nav, {
    subtree: true, attributes: true, attributeFilter: ["style"],
  });
  window.addEventListener("resize", report);
  document.addEventListener("scroll", report, true);
  document.addEventListener("visibilitychange", report);
}
