/* 薪火OS · Obsidian 插件（0.99.0，免构建的 CommonJS 单文件）。
 *
 * 把整个工作台以 iframe 的方式住进 Obsidian（`?embed=1&panel=1`：隐藏产品侧边栏，
 * 以顶部标签条导航——侧边栏功能已随 dsh 移除，2026-10-05）：
 *   - 打开时健康检查本机服务（默认 127.0.0.1:8765），没起来就按设置里的仓库路径拉起
 *     `.venv` 里的 python（桌面版 only）；
 *   - 主题跟随：把 Obsidian 的计算样式翻译成产品主题桥认识的 --dsw-alias-* token
 *     （宿主一侧只管取值，产品侧的调色板映射在 styles.css 的宿主层里）；
 *   - 同步感：视图重新被激活且距上次加载超过 2 分钟时整页重载，读回最新的记录与文件。
 *
 * 与 dsh 壳的关系：两个壳共用同一个本地服务与同一份 Vault——在 Obsidian 里改的
 * 笔记，产品与 dsh 读同一份文件；产品写下的内容，Obsidian 的文件监听会自动刷新。
 */

const { Plugin, ItemView, Notice, PluginSettingTab, Setting, requestUrl } = require('obsidian');
const { spawn } = require('child_process');
const path = require('path');

const VIEW_TYPE = 'torch-workbench';
const RIBBON_ICON = 'layout-dashboard';
const STALE_RELOAD_MS = 120000;

const DEFAULT_SETTINGS = {
  baseUrl: 'http://127.0.0.1:8765',
  repoRoot: '',
  pythonPath: '',
  autoStart: true,
};

/** Obsidian 的计算样式 → 产品主题桥认识的 token（值只当字符串传，不在这里做设计决定）。 */
function themePayload() {
  const style = getComputedStyle(document.body);
  const pick = (...names) => {
    for (const name of names) {
      const value = (style.getPropertyValue(name) || '').trim();
      if (value) return value;
    }
    return '';
  };
  const dark = document.body.classList.contains('theme-dark');
  const tokens = {
    '--dsw-alias-bg-base': pick('--background-primary'),
    '--dsw-alias-bg-layer-1': pick('--background-secondary', '--background-primary'),
    '--dsw-alias-bg-layer-2': pick('--background-secondary-alt', '--background-secondary'),
    '--dsw-alias-bg-overlay': pick('--background-primary-alt', '--background-secondary'),
    '--dsw-alias-border-l1': pick('--background-modifier-border'),
    '--dsw-alias-border-l2': pick('--background-modifier-border-hover', '--background-modifier-border'),
    '--dsw-alias-brand-primary': pick('--interactive-accent'),
    '--dsw-alias-label-primary': pick('--text-normal'),
    '--dsw-alias-label-secondary': pick('--text-muted'),
    '--dsw-alias-state-error-primary': pick('--text-error', '--color-red'),
    '--dsw-alias-state-idle-primary': pick('--text-faint'),
    '--dsw-alias-state-success-primary': pick('--color-green', '--text-success'),
    '--dsw-alias-state-warn-primary': pick('--color-orange', '--text-warning'),
    '--dsw-specific-sidebar-fill': pick('--background-secondary-alt', '--background-secondary'),
  };
  for (const [name, value] of Object.entries(tokens)) {
    if (!value) delete tokens[name];
  }
  return { type: 'torch:theme', scheme: dark ? 'dark' : 'light', tokens };
}

class WorkbenchView extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.frame = null;
    this.lastLoad = 0;
  }

  getViewType() { return VIEW_TYPE; }
  getDisplayText() { return '薪火OS'; }
  getIcon() { return RIBBON_ICON; }

  async onOpen() {
    this.contentEl.empty();
    this.contentEl.addClass('torch-view');
    this.frame = this.contentEl.createEl('iframe', { cls: 'torch-frame' });
    this.frame.setAttribute('title', '薪火OS 工作台');
    this.frame.addEventListener('load', () => {
      this.lastLoad = Date.now();
      this.plugin.pushTheme(this.frame);
    });
    this.registerDomEvent(window, 'message', (event) => {
      if (event && event.source === this.frame?.contentWindow && event.data?.type === 'torch:theme-request') {
        this.plugin.pushTheme(this.frame);
      }
    });
    this.registerEvent(this.app.workspace.on('css-change', () => this.plugin.pushTheme(this.frame)));
    this.registerEvent(this.app.workspace.on('active-leaf-change', (leaf) => {
      if (leaf === this.leaf && this.frame && Date.now() - this.lastLoad > STALE_RELOAD_MS) {
        this.reload();
      }
    }));
    await this.plugin.ensureBackend();
    this.reload();
  }

  reload() {
    if (!this.frame) return;
    const scheme = document.body.classList.contains('theme-dark') ? 'dark' : 'light';
    this.frame.setAttribute('src', `${this.plugin.settings.baseUrl}/?embed=1&panel=1&theme=${scheme}`);
  }
}

class TorchSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    const { containerEl } = this;
    containerEl.empty();
    new Setting(containerEl)
      .setName('本地服务地址')
      .setDesc('默认 http://127.0.0.1:8765，只在你的电脑上。')
      .addText((text) => text.setValue(this.plugin.settings.baseUrl)
        .onChange(async (value) => { this.plugin.settings.baseUrl = value.trim() || DEFAULT_SETTINGS.baseUrl; await this.plugin.saveSettings(); }));
    new Setting(containerEl)
      .setName('自动拉起本地服务')
      .setDesc('打开面板时如果服务没在运行，用下面的仓库路径启动它（桌面版）。')
      .addToggle((toggle) => toggle.setValue(this.plugin.settings.autoStart)
        .onChange(async (value) => { this.plugin.settings.autoStart = value; await this.plugin.saveSettings(); }));
    new Setting(containerEl)
      .setName('仓库路径')
      .setDesc('薪火OS 的仓库目录（包含 .venv 与 backend 的那个），例如 D:/tools/torch-os。')
      .addText((text) => text.setPlaceholder('C:/path/to/torch-os')
        .setValue(this.plugin.settings.repoRoot)
        .onChange(async (value) => { this.plugin.settings.repoRoot = value.trim(); await this.plugin.saveSettings(); }));
    new Setting(containerEl)
      .setName('Python 路径（可选）')
      .setDesc('留空时用 仓库路径/.venv/Scripts/python.exe。')
      .addText((text) => text.setPlaceholder('.venv/Scripts/python.exe')
        .setValue(this.plugin.settings.pythonPath)
        .onChange(async (value) => { this.plugin.settings.pythonPath = value.trim(); await this.plugin.saveSettings(); }));
  }
}

module.exports = class TorchPlugin extends Plugin {
  async onload() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    this.registerView(VIEW_TYPE, (leaf) => new WorkbenchView(leaf, this));
    this.addRibbonIcon(RIBBON_ICON, '打开 薪火OS', () => this.activateView());
    this.addCommand({ id: 'open-workbench', name: '打开工作台', callback: () => this.activateView() });
    this.addSettingTab(new TorchSettingTab(this.app, this));
  }

  onunload() {
    this.app.workspace.detachLeavesOfType(VIEW_TYPE);
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }

  async ping() {
    try {
      const response = await requestUrl({ url: `${this.settings.baseUrl}/api/health`, method: 'GET' });
      return response.status === 200;
    } catch {
      return false;
    }
  }

  async ensureBackend() {
    if (await this.ping()) return true;
    if (!this.settings.autoStart) {
      new Notice('薪火OS 本地服务未运行（已关闭自动拉起，可在插件设置里打开）。');
      return false;
    }
    if (!this.settings.repoRoot) {
      new Notice('薪火OS 本地服务未运行；请在插件设置里填写仓库路径以自动拉起。');
      return false;
    }
    const python = this.settings.pythonPath
      || path.join(this.settings.repoRoot, '.venv', 'Scripts', 'python.exe');
    try {
      const child = spawn(python, ['-m', 'backend.app.run'], {
        cwd: this.settings.repoRoot, detached: true, stdio: 'ignore', windowsHide: true,
      });
      child.unref();
    } catch (error) {
      new Notice(`薪火OS 启动失败：${String(error)}`);
      return false;
    }
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      if (await this.ping()) return true;
    }
    new Notice('薪火OS 启动超时；请检查仓库路径与 Python 环境。');
    return false;
  }

  pushTheme(frame) {
    if (!frame?.contentWindow) return;
    try {
      frame.contentWindow.postMessage(themePayload(), '*');
    } catch { /* 面板正在卸载：忽略 */ }
  }

  async activateView() {
    const existing = this.app.workspace.getLeavesOfType(VIEW_TYPE);
    if (existing.length) {
      this.app.workspace.revealLeaf(existing[0]);
      return;
    }
    const leaf = this.app.workspace.getLeaf('tab');
    await leaf.setViewState({ type: VIEW_TYPE, active: true });
    this.app.workspace.revealLeaf(leaf);
  }
};
