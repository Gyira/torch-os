// One real form, two entrances: move (never clone) the section so listeners, drafts and IDs survive.
import { loadWidgetSettings } from './widget-settings.js';
import { loadSchedulePanel } from './inbox.js';
import { loadDidaSync } from './dida-sync.js';

const SECTIONS = {
  widgets: { title: '布置工作台', load: loadWidgetSettings, immediate: true },
  schedule: { title: '晨晚报与提醒', load: loadSchedulePanel },
  dida: { title: '任务与习惯同步', load: loadDidaSync },
};

export function initContextSettings() {
  const dialog = document.getElementById('context-settings-dialog');
  const content = document.getElementById('context-settings-content');
  const status = document.getElementById('context-settings-status');
  const dirty = new Set();
  let mounted = null;
  let closing = null;
  let generation = 0;
  dialog.addEventListener('input', () => {
    if (!mounted || SECTIONS[mounted.name].immediate) return;
    dirty.add(mounted.name);
    status.textContent = '请使用面板中的保存按钮应用更改；关闭后仍保留本次输入。';
  });
  function restore() {
    generation++;
    closing?.cancel(); closing = null;
    if (!mounted) return;
    const { section, placeholder, hidden, trigger } = mounted;
    placeholder.replaceWith(section); section.hidden = hidden;
    content.inert = false; content.removeAttribute('aria-busy');
    mounted = null;
    trigger?.focus({ preventScroll: true });
  }
  async function close() {
    if (!dialog.open || closing) return;
    if (!matchMedia('(prefers-reduced-motion: reduce)').matches && dialog.animate) {
      closing = dialog.animate([{ opacity: 1, transform: 'translateY(0)' },
        { opacity: 0, transform: 'translateY(8px)' }], { duration: 130, easing: 'ease-in' });
      await closing.finished.catch(() => {});
    }
    if (dialog.open) dialog.close();
  }
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  dialog.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); return; }
    if (event.key !== 'Tab') return;
    const stops = [...dialog.querySelectorAll('button, input, select, textarea, a[href], [tabindex]')]
      .filter(node => !node.disabled && node.tabIndex >= 0 && !node.closest('[inert]') && node.getClientRects().length);
    const first = stops[0], last = stops.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  });
  dialog.addEventListener('close', restore);
  document.getElementById('context-settings-close').addEventListener('click', close);
  // No backdrop-dismiss: avoid losing the user's place while manipulating dense forms.
  document.addEventListener('click', async event => {
    const trigger = event.target.closest('[data-context-settings]');
    if (!trigger || mounted) return;
    const name = trigger.dataset.contextSettings;
    const config = SECTIONS[name];
    if (!config) return;
    const section = document.querySelector(`[data-settings-section="${name}"]`);
    if (!section) return;
    const placeholder = document.createComment(`settings-${name}`);
    mounted = { name, section, placeholder, hidden: section.hidden, trigger };
    section.replaceWith(placeholder); content.replaceChildren(section); section.hidden = false;
    document.getElementById('context-settings-title').textContent = config.title;
    status.textContent = config.immediate ? '调整后立即应用，关闭即可回到工作台。'
      : dirty.has(name) ? '已保留本次输入；请使用面板中的保存按钮应用更改。' : '正在读取设置…';
    dialog.showModal();
    const request = ++generation;
    if (dirty.has(name)) return;
    content.inert = true; content.setAttribute('aria-busy', 'true');
    let loaded = false;
    try {
      loaded = (await config.load()) !== false;
      if (request === generation && !loaded) status.textContent = '未能读取最新设置，请关闭后重试。';
      else if (request === generation && !config.immediate) status.textContent = '修改后点击面板中的保存按钮；关闭不会自动保存。';
    } catch {
      if (request === generation) status.textContent = '暂时无法读取，请关闭后重试。';
    } finally {
      if (request === generation) { content.inert = !loaded; content.removeAttribute('aria-busy'); }
    }
  });
}
