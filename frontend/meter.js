// Apply numeric widths through CSSOM: HTML style attributes are blocked by our CSP.
export function applyMeters(root) {
  for (const node of root?.querySelectorAll?.("[data-meter-percent]") || []) {
    const value = Number(node.dataset.meterPercent);
    node.style.width = `${Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0}%`;
  }
}
