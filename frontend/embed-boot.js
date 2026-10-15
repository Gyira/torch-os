// Runs before the body paints so the dsh panel never flashes our own sidebar
// or the wrong theme.
const bootParams = (location.search || '').split(/[?&]/);
const embedded = bootParams.includes('embed=1');
if (embedded) {
  document.documentElement.classList.add('embed-mode');
}
if (bootParams.includes('panel=1')) {
  document.documentElement.classList.add('embed-panel');
}

// 首帧主题：宿主在 iframe URL 上给的 ?theme= 优先（dsh 面板切页时即时生效）；
// 独立打开时才跟随系统明暗。宿主的完整 token 包稍后由 theme-bridge.js 应用。
let bootScheme = null;
for (const param of bootParams) {
  const [key, value] = param.split('=');
  if (key === 'theme' && (value === 'dark' || value === 'light')) bootScheme = value;
}
if (!bootScheme && !embedded && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-color-scheme: dark)').matches) {
  bootScheme = 'dark';
}
if (bootScheme) {
  document.documentElement.dataset.torchTheme = bootScheme;
}
