// Decoration follows committed navigation state; it never delays routing or moves hit targets.
const GROUPS = '.panel-nav-scroll, #module-nav, .time-rail, .settings-nav, .love-nav';
const SELECTED = ':scope > button.active, :scope > .panel-nav-family > button.active, :scope > button.is-active, :scope > button[aria-selected="true"]';

export function initMaterialMotion() {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const groups = new Map();
  let frame = 0;
  let resizing = false;
  const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
  function update() {
    frame = 0;
    for (const [nav, record] of groups) {
      if (!nav.isConnected) {
        record.observer.disconnect(); record.resize.disconnect(); record.animation?.cancel();
        groups.delete(nav);
      }
    }
    document.querySelectorAll(GROUPS).forEach(nav => {
      if (groups.has(nav)) return;
      const pill = document.createElement('span');
      pill.className = 'material-indicator';
      pill.setAttribute('aria-hidden', 'true');
      const observer = new MutationObserver(schedule);
      observer.observe(nav, { subtree: true, childList: true, attributes: true,
        attributeFilter: ['class', 'aria-selected', 'aria-current', 'hidden'] });
      const resize = new ResizeObserver(() => { resizing = true; schedule(); });
      resize.observe(nav);
      nav.classList.add('material-track');
      nav.prepend(pill);
      groups.set(nav, { pill, observer, resize, animation: null, target: '' });
    });
    for (const [nav, record] of groups) {
      const active = nav.querySelector(SELECTED);
      if (!active || !active.getClientRects().length) {
        record.pill.hidden = true; record.target = ''; continue;
      }
      // Keep the selected top-level destination visible when the window narrows.
      // Scroll this strip only; do not disturb document or editor scroll positions.
      if (resizing && nav.classList.contains('panel-nav-scroll') && nav.scrollWidth > nav.clientWidth) {
        const item = active.getBoundingClientRect(), strip = nav.getBoundingClientRect();
        if (item.left < strip.left || item.right > strip.right) nav.scrollLeft += item.left - strip.left - (strip.width - item.width) / 2;
      }
      const box = active.getBoundingClientRect();
      const parent = nav.getBoundingClientRect();
      const x = box.left - parent.left + nav.scrollLeft - nav.clientLeft;
      const y = box.top - parent.top + nav.scrollTop - nav.clientTop;
      const target = [x, y, box.width, box.height].join(':');
      if (record.target === target) continue;
      const previous = record.target && !record.pill.hidden ? record.pill.getBoundingClientRect() : null;
      record.animation?.cancel();
      record.pill.hidden = false;
      Object.assign(record.pill.style, { width: `${box.width}px`, height: `${box.height}px`,
        transform: `translate(${x}px, ${y}px)` });
      nav.classList.add('material-ready');
      if (previous && !resizing && !reduced.matches && record.pill.animate) {
        // FLIP: interrupt from the current painted rectangle. Scale the surface only.
        const dx = previous.left - box.left, dy = previous.top - box.top;
        const sx = previous.width / box.width, sy = previous.height / box.height;
        const alongX = Math.abs(dx) >= Math.abs(dy);
        const stretch = 1 + Math.min(.09, Math.hypot(dx, dy) / 3000);
        record.animation = record.pill.animate([
          { transform: `translate(${x + dx}px, ${y + dy}px) scale(${sx}, ${sy})` },
          { transform: `translate(${x + dx * .18}px, ${y + dy * .18}px) scale(${alongX ? stretch : 1}, ${alongX ? 1 : stretch})`, offset: .65 },
          { transform: `translate(${x}px, ${y}px) scale(1, 1)` },
        ], { duration: 340, easing: 'cubic-bezier(.22,.8,.24,1)' });
      }
      record.target = target;
    }
    resizing = false;
  }
  // Only observe insertion/removal globally; local observers handle selection state.
  const tree = new MutationObserver(schedule);
  tree.observe(document.getElementById('main-content'), { childList: true, subtree: true });
  const onResize = () => { resizing = true; schedule(); };
  window.addEventListener('resize', onResize, { passive: true });
  const onReduce = () => { if (reduced.matches) groups.forEach(r => r.animation?.cancel()); schedule(); };
  reduced.addEventListener('change', onReduce);
  schedule();
  return () => {
    cancelAnimationFrame(frame); tree.disconnect();
    groups.forEach(r => { r.observer.disconnect(); r.resize.disconnect(); r.animation?.cancel(); r.pill.remove(); });
    groups.forEach((_, nav) => nav.classList.remove('material-track', 'material-ready'));
    window.removeEventListener('resize', onResize); reduced.removeEventListener('change', onReduce);
  };
}
