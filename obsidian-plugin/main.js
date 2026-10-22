/* 薪火OS · Obsidian 插件（0.99.1，免构建的 CommonJS 单文件）。
 *
 * 把整个工作台以 iframe 的方式住进 Obsidian（`?embed=1&panel=1&host=obsidian`）。
 *
 * 2026-10-17 起「在哪个库工作，哪个库就是工作区」：
 *   - 每个库用自己的后端实例（独立 PORT / VAULT_DIR / .torch-os），
 *     插件按库路径算 vault_id，复用一个已经服务同一库的实例，否则启动一个；
 *   - 后端以 TORCH_VAULT_SCOPED=1 启动，界面里不能再切库；
 *   - iframe 指向这个实例，插件里看到的任务/日记/记忆/索引都属于当前库。
 *
 * 另外：
 *   - 主题跟随：把 Obsidian 的计算样式翻译成产品主题桥认识的 --dsw-alias-* token；
 *   - 打开任务文档：iframe 发 torch:open-note，插件用 app.workspace 打开笔记；
 *   - 同步感：视图重新被激活且距上次加载超过 2 分钟时整页重载。
 */

const {
  Plugin, ItemView, Notice, PluginSettingTab, Setting, requestUrl, TFile, normalizePath,
} = require('obsidian');
const { spawn } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const net = require('net');
const path = require('path');

const VIEW_TYPE = 'torch-workbench';
const RIBBON_ICON = 'layout-dashboard';
const STALE_RELOAD_MS = 120000;
const DEFAULT_PORT = 8765;
// 插件自己的实例从 9168 起找端口：8765 留给浏览器/手动模式的 .env 工作区，
// 两者可以同时运行，互不抢端口。
const PLUGIN_PORT_BASE = 9168;
const PORT_ATTEMPTS = 64;

const DEFAULT_SETTINGS = {
  baseUrl: 'http://127.0.0.1:8765',
  repoRoot: '',
  pythonPath: '',
  autoStart: true,
  instancePort: 0,
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

/** 与后端 vault_identity 相同：realpath → 正斜杠 → Windows 小写 → sha256[:16]。 */
function normalizedVaultPath(basePath) {
  let resolved = basePath;
  try {
    resolved = fs.realpathSync.native(basePath);
  } catch {
    resolved = path.resolve(basePath);
  }
  resolved = String(resolved).replace(/\\/g, '/');
  if (process.platform === 'win32') resolved = resolved.toLowerCase();
  return resolved;
}

function vaultIdentity(basePath) {
  return crypto.createHash('sha256').update(normalizedVaultPath(basePath), 'utf8').digest('hex').slice(0, 16);
}

function baseVaultPath(app) {
  const adapter = app?.vault?.adapter;
  if (typeof adapter?.getBasePath === 'function') {
    try { return adapter.getBasePath(); } catch { /* fall through */ }
  }
  return adapter?.basePath || '';
}

function portFromUrl(value, fallback = DEFAULT_PORT) {
  try {
    const parsed = new URL(String(value || ''));
    return Number(parsed.port) || (parsed.protocol === 'https:' ? 443 : 80) || fallback;
  } catch {
    return fallback;
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function withTimeout(promise, ms) {
  let timer = null;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('timeout')), ms);
    }),
  ]).finally(() => { if (timer) clearTimeout(timer); });
}

async function fetchHealth(port) {
  try {
    const response = await withTimeout(
      requestUrl({ url: `http://127.0.0.1:${port}/api/health`, method: 'GET' }), 1500);
    if (response.status !== 200) return null;
    if (typeof response.text === 'string' && response.text) return JSON.parse(response.text);
    return response.json || null;
  } catch {
    return null;
  }
}

/** 端口是否空闲（用于给当前库找一个能启动后端的端口）。 */
function portAvailable(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.once('error', () => resolve(false));
    server.listen(port, '127.0.0.1', () => {
      server.close(() => resolve(true));
    });
  });
}

class WorkbenchView extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.frame = null;
    this.lastLoad = 0;
    this.offline = null;
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
      if (!event || event.source !== this.frame?.contentWindow) return;
      const data = event.data;
      if (data?.type === 'torch:theme-request') { this.plugin.pushTheme(this.frame); return; }
      if (data?.type === 'torch:open-note') { this.plugin.openNote(data.path, event.origin); }
    });
    this.registerEvent(this.app.workspace.on('css-change', () => this.plugin.pushTheme(this.frame)));
    this.registerEvent(this.app.workspace.on('active-leaf-change', (leaf) => {
      if (leaf === this.leaf && this.frame && Date.now() - this.lastLoad > STALE_RELOAD_MS) {
        this.reload();
      }
    }));
    const ready = await this.plugin.ensureBackend();
    if (!ready) {
      this.showOffline('无法启动当前 Obsidian 库的工作台。请检查插件设置里的仓库路径、Python 路径，或查看库内 .torch-os/logs 的启动日志。');
      return;
    }
    this.hideOffline();
    this.reload();
  }

  reload() {
    if (!this.frame || !this.plugin.instanceUrl) return;
    const scheme = document.body.classList.contains('theme-dark') ? 'dark' : 'light';
    const url = new URL(`${this.plugin.instanceUrl.replace(/\/+$/, '')}/`);
    url.searchParams.set('embed', '1');
    url.searchParams.set('panel', '1');
    url.searchParams.set('host', 'obsidian');
    url.searchParams.set('theme', scheme);
    this.frame.setAttribute('src', url.toString());
  }

  showOffline(message) {
    if (this.frame) this.frame.hidden = true;
    if (!this.offline) {
      this.offline = this.contentEl.createEl('div', { cls: 'torch-offline' });
    }
    this.offline.setText(message);
  }

  hideOffline() {
    if (this.frame) this.frame.hidden = false;
    if (this.offline) {
      this.offline.remove();
      this.offline = null;
    }
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
    const vaultName = this.app.vault.getName();
    new Setting(containerEl)
      .setName('当前 Obsidian 库')
      .setDesc(`${vaultName} · ${this.plugin.instanceUrl || '尚未启动后端实例'}。每个库使用独立的后端端口与 .torch-os 数据目录。`)
      .addButton((button) => button.setButtonText('重新连接').onClick(async () => {
        this.plugin.instanceUrl = '';
        this.plugin.ensurePromise = null;
        const ready = await this.plugin.ensureBackend();
        new Notice(ready ? '已连接当前库的后端实例。' : '连接失败，请查看库内 .torch-os/logs。');
        this.display();
      }));
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
    new Setting(containerEl)
      .setName('自动拉起本地服务')
      .setDesc('打开面板时如果当前库还没有对应的后端实例，就用仓库里的 Python 启动一个（桌面版）。')
      .addToggle((toggle) => toggle.setValue(this.plugin.settings.autoStart)
        .onChange(async (value) => { this.plugin.settings.autoStart = value; await this.plugin.saveSettings(); }));
    new Setting(containerEl)
      .setName('候选服务地址')
      .setDesc('自动模式会先探测这个地址；只有它服务的正是当前 Obsidian 库时才复用，否则另起当前库的实例'
        + '（插件自己的实例从端口 9168 起找，把 8765 留给浏览器/手动模式）。')
      .addText((text) => text.setPlaceholder('http://127.0.0.1:8765')
        .setValue(this.plugin.settings.baseUrl)
        .onChange(async (value) => { this.plugin.settings.baseUrl = value.trim() || 'http://127.0.0.1:8765'; await this.plugin.saveSettings(); }));
  }
}

module.exports = class TorchPlugin extends Plugin {
  async onload() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    this.instanceUrl = '';
    this.ensurePromise = null;
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

  vaultBinding() {
    const basePath = baseVaultPath(this.app);
    if (!basePath) return null;
    return {
      path: basePath,
      name: this.app.vault.getName(),
      id: vaultIdentity(basePath),
    };
  }

  async ensureBackend() {
    if (this.instanceUrl) {
      const health = await fetchHealth(portFromUrl(this.instanceUrl));
      if (health?.vault_id === this.vaultBinding()?.id) return true;
      this.instanceUrl = '';
    }
    if (this.ensurePromise) return this.ensurePromise;
    this.ensurePromise = this.prepareBackend()
      .catch((error) => {
        new Notice(`薪火OS 启动失败：${String(error?.message || error)}`);
        return false;
      })
      .finally(() => { this.ensurePromise = null; });
    return this.ensurePromise;
  }

  async prepareBackend() {
    const binding = this.vaultBinding();
    if (!binding) {
      new Notice('薪火OS 只在 Obsidian 桌面版、且能拿到库路径时自动启动。');
      return false;
    }
    const candidates = [];
    const addCandidate = (port) => {
      const value = Number(port);
      if (Number.isInteger(value) && value > 0 && value < 65536 && !candidates.includes(value)) {
        candidates.push(value);
      }
    };
    addCandidate(this.settings.instancePort);
    addCandidate(portFromUrl(this.settings.baseUrl));
    addCandidate(DEFAULT_PORT);
    addCandidate(portFromUrl(this.instanceUrl, 0));

    for (const port of candidates) {
      const health = await fetchHealth(port);
      if (health?.vault_id === binding.id) {
        this.adoptInstance(port);
        return true;
      }
    }

    if (!this.settings.autoStart) {
      new Notice('薪火OS 当前库还没有运行中的后端实例。请手动启动服务，或在插件设置里打开自动拉起。');
      return false;
    }
    if (!this.settings.repoRoot) {
      new Notice('薪火OS 无法自动启动：请在插件设置里填写仓库路径。');
      return false;
    }

    const manualPort = portFromUrl(this.settings.baseUrl);
    const storedPort = Number(this.settings.instancePort) || 0;
    // 之前复用过 8765 上的手动实例，不代表新实例也要占 8765。
    const preferred = storedPort > 0 && storedPort !== manualPort ? storedPort : PLUGIN_PORT_BASE;
    let port = 0;
    for (let offset = 0; offset < PORT_ATTEMPTS; offset += 1) {
      const candidate = preferred + offset;
      if (candidate > 65535) break;
      if (await portAvailable(candidate)) {
        port = candidate;
        break;
      }
    }
    if (!port) {
      new Notice('薪火OS 找不到可用的本机端口来启动当前库的服务。');
      return false;
    }

    const python = this.settings.pythonPath
      || path.join(this.settings.repoRoot, '.venv', 'Scripts', 'python.exe');
    const dataDir = path.join(binding.path, '.torch-os');
    let logFsPath = '';
    let logFd = null;
    try {
      const logDir = path.join(dataDir, 'logs');
      fs.mkdirSync(logDir, { recursive: true });
      logFsPath = path.join(logDir, `torch-backend-${port}.log`);
      logFd = fs.openSync(logFsPath, 'a');
      const child = spawn(python, ['-m', 'backend.app.run'], {
        cwd: this.settings.repoRoot,
        detached: true,
        windowsHide: true,
        stdio: ['ignore', logFd, logFd],
        env: {
          ...process.env,
          HOST: '127.0.0.1',
          PORT: String(port),
          VAULT_DIR: binding.path,
          DATA_DIR: dataDir,
          TORCH_VAULT_SCOPED: '1',
          WORKSPACE_NAME: binding.name,
          VAULT_READ_ONLY: '0',
        },
      });
      child.unref();
    } catch (error) {
      if (logFd !== null) fs.closeSync(logFd);
      new Notice(`薪火OS 启动后端失败：${String(error?.message || error)}`);
      return false;
    } finally {
      if (logFd !== null) {
        try { fs.closeSync(logFd); } catch { /* already closed */ }
      }
    }

    for (let attempt = 0; attempt < 30; attempt += 1) {
      await sleep(1000);
      const health = await fetchHealth(port);
      if (health?.vault_id === binding.id) {
        this.adoptInstance(port);
        return true;
      }
    }
    new Notice(`薪火OS 启动超时；请查看 ${logFsPath || '当前库 .torch-os/logs' }。`);
    return false;
  }

  adoptInstance(port) {
    this.instanceUrl = `http://127.0.0.1:${port}`;
    if (Number(this.settings.instancePort) !== port) {
      this.settings.instancePort = port;
      this.saveSettings().catch(() => {});
    }
  }

  /** 打开任务文档：只接受当前库里的任务笔记或旧的 任务（tasks）.md。 */
  async openNote(pathValue, origin) {
    if (!this.instanceUrl || !this.frameOriginMatches(origin)) return;
    if (typeof pathValue !== 'string' || !pathValue || pathValue.length > 1024) return;
    if (pathValue.includes('\\') || pathValue.startsWith('/') || pathValue.split('/').includes('..')) {
      return;
    }
    const normalized = normalizePath(pathValue);
    const isTask = (normalized.startsWith('00_人生路/08_任务与待办/任务/')
      || normalized.startsWith('00-System/任务/')) && normalized.endsWith('.md');
    const isConfig = normalized === '00_人生路/08_任务与待办/_系统/任务（tasks）.md'
      || normalized === '00_人生路/08_任务与待办/_系统/待办（todos）.md'
      || normalized === '00-System/任务（tasks）.md'
      || normalized === '00-System/任务.md';
    const isPillar = /^(00_人生路|01_自我档案|02_生活运营|03_知识库)\//.test(normalized)
      && normalized.endsWith('.md');
    if (!isTask && !isConfig && !isPillar) return;
    const file = this.app.vault.getAbstractFileByPath(normalized);
    if (!(file instanceof TFile)) {
      new Notice(`笔记不存在：${normalized}。刷新后重试。`);
      return;
    }
    try {
      const leaf = this.app.workspace.getLeaf('tab');
      await leaf.openFile(file);
      try { this.app.workspace.setActiveLeaf(leaf, { focus: true }); } catch { /* 旧版本没有 options */ }
    } catch (error) {
      new Notice(`打开任务文档失败：${String(error?.message || error)}`);
    }
  }

  frameOriginMatches(origin) {
    try {
      return origin === new URL(this.instanceUrl).origin;
    } catch {
      return false;
    }
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
