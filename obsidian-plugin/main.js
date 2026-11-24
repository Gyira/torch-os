/* 薪火OS · Obsidian 插件（2.3.0，免构建的 CommonJS 单文件）。
 *
 * 把整个工作台以 iframe 的方式住进 Obsidian（`?embed=1&panel=1&host=obsidian`）。
 *
 * 2.3.0 起「界面先出、服务后台启动」：
 *   - LocalUiServer 只监听 127.0.0.1，直接提供仓库里的 frontend/ 静态界面，
 *     并把 /api/* 与 /harness/*（含 WebSocket）代理到当前库的 Python 后端；
 *   - iframe origin 固定为 http://127.0.0.1:<uiPort>，后端地址对页面不可见；
 *   - BackendController 是 idle → starting → ready | failed 状态机，打开工作台后
 *     后台启动 Python；监听子进程退出并读取本次新增的日志，快速给出具体失败；
 *   - 服务未就绪时常用只读接口回放本机 UiCache，并标注“显示上次缓存”；
 *   - 失败提示可一键预览/确认整理旧编号目录，调用 scripts/unnumber_vault.py。
 *
 * 2026-10-17 起「在哪个库工作，哪个库就是工作区」：
 *   - 每个库用自己的后端实例（独立 PORT / VAULT_DIR / .torch-os），
 *     插件按库路径算 vault_id，复用一个已经服务同一库的实例，否则启动一个；
 *   - 后端以 TORCH_VAULT_SCOPED=1 启动，界面里不能再切库；
 *   - 插件里看到的任务/日记/记忆/索引都属于当前库。
 *
 * 另外：
 *   - 主题跟随：把 Obsidian 的计算样式翻译成产品主题桥认识的 --dsw-alias-* token；
 *   - 打开任务文档：iframe 发 torch:open-note，插件用 app.workspace 打开笔记；
 *   - 网页面板在工作台打开时预载，切换时复用同一个页面。
 */

const {
  Plugin, ItemView, Notice, PluginSettingTab, Setting, TFile, normalizePath,
} = require('obsidian');
const { spawn } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const net = require('net');
const os = require('os');
const path = require('path');

const VIEW_TYPE = 'torch-workbench';
const RIBBON_ICON = 'layout-dashboard';
const DEFAULT_PORT = 8765;
const CHATGPT_URL = 'https://chatgpt.com/';
// ChatGPT 住在自己的持久分区里：登录态由 Electron 保存，宿主主会话不受影响。
const CHATGPT_PARTITION = 'persist:torch-chatgpt';
// 插件自己的实例从 9168 起找端口：8765 留给浏览器/手动模式的 .env 工作区，
// 两者可以同时运行，互不抢端口。
const PLUGIN_PORT_BASE = 9168;
const PORT_ATTEMPTS = 64;
// 工作台界面由插件自己的 Node 服务提供，和后端端口分开。
// 从 9400 起，避开后端 9168 段与手动模式的 8765。
const UI_PORT_BASE = 9400;
const UI_PORT_ATTEMPTS = 32;
const SERVICE_START_DEADLINE_MS = 20000;
const SERVICE_HEALTH_POLL_MS = 250;
const SERVICE_HEALTH_TIMEOUT_MS = 750;
const SERVICE_PROBE_TIMEOUT_MS = 500;
const UI_CACHE_ENTRY_LIMIT = 1024 * 1024;
const UI_CACHE_TOTAL_LIMIT = 32 * 1024 * 1024;
const UI_CACHE_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const UI_CACHE_MAX_ENTRIES = 500;
const ACTION_TOKEN = crypto.randomBytes(18).toString('hex');

const DEFAULT_SETTINGS = {
  baseUrl: 'http://127.0.0.1:8765',
  repoRoot: '',
  pythonPath: '',
  autoStart: true,
  instancePort: 0,
  uiPort: 0,
  chatgptProxy: '',
  agentProvider: 'dsh',
};

// ── 本机工具链发现 ─────────────────────────────────────────────────────
// repoRoot / pythonPath / instancePort 是“这台电脑”的属性，不能写进随库同步的
// data.json：A 电脑的 D:\tools\torch-os 到了 B 电脑可能不存在。它们只存本机
// LOCALAPPDATA（或对应平台配置目录），data.json 只保留 baseUrl / autoStart。
const DEVICE_SETTING_KEYS = ['repoRoot', 'pythonPath', 'instancePort', 'uiPort', 'storageConfigPath', 'chatgptProxy', 'agentProvider'];
const REPO_MARKERS = ['backend/app/run.py', 'backend/app/main.py'];

function localConfigDir() {
  if (process.platform === 'win32') {
    const base = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
    return path.join(base, 'torch-os', 'obsidian-plugin');
  }
  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'torch-os', 'obsidian-plugin');
  }
  const base = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config');
  return path.join(base, 'torch-os', 'obsidian-plugin');
}

function deviceConfigPath(vaultId) {
  const safe = String(vaultId || 'unknown').replace(/[^0-9A-Za-z_.-]+/g, '-');
  return path.join(localConfigDir(), `${safe || 'unknown'}.json`);
}

function readDeviceSettings(vaultId) {
  try {
    const value = JSON.parse(fs.readFileSync(deviceConfigPath(vaultId), 'utf8'));
    if (!value || typeof value !== 'object') return {};
    const result = {};
    for (const key of DEVICE_SETTING_KEYS) {
      if (Object.prototype.hasOwnProperty.call(value, key)) result[key] = value[key];
    }
    return result;
  } catch {
    return {};
  }
}

function writeDeviceSettings(vaultId, settings = {}) {
  const target = deviceConfigPath(vaultId);
  const payload = {
    version: 1,
    vaultId: String(vaultId || 'unknown'),
    updatedAt: new Date().toISOString(),
  };
  for (const key of DEVICE_SETTING_KEYS) {
    payload[key] = settings[key] === undefined || settings[key] === null
      ? (key === 'instancePort' ? 0 : '')
      : settings[key];
  }
  try {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    const temporary = `${target}.tmp-${process.pid}-${Date.now()}`;
    fs.writeFileSync(temporary, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
    try {
      fs.renameSync(temporary, target);
    } catch {
      // Windows 上目标被短暂占用时，退回覆盖写；配置很小，不涉及 SQLite。
      fs.copyFileSync(temporary, target);
      fs.rmSync(temporary, { force: true });
    }
    return target;
  } catch {
    return '';
  }
}

function isRepoRoot(value) {
  const text = String(value || '').trim();
  if (!text) return false;
  try {
    const root = path.resolve(text);
    if (!fs.statSync(root).isDirectory()) return false;
    return REPO_MARKERS.some((relative) => fs.existsSync(path.join(root, ...relative.split('/'))));
  } catch {
    return false;
  }
}

function findPython(repoRoot) {
  const root = String(repoRoot || '').trim();
  if (!root) return '';
  const candidates = process.platform === 'win32'
    ? [path.join(root, '.venv', 'Scripts', 'python.exe'),
       path.join(root, 'venv', 'Scripts', 'python.exe')]
    : [path.join(root, '.venv', 'bin', 'python'),
       path.join(root, 'venv', 'bin', 'python'),
       path.join(root, '.venv', 'bin', 'python3')];
  for (const candidate of candidates) {
    try {
      if (fs.statSync(candidate).isFile()) return candidate;
    } catch { /* try next */ }
  }
  return '';
}

function resolvePython(repoRoot, explicit = '') {
  const manual = String(explicit || '').trim();
  if (manual) {
    try {
      if (fs.statSync(path.resolve(manual)).isFile()) return path.resolve(manual);
    } catch { /* fall through to repo venv */ }
  }
  return findPython(repoRoot);
}

function repoCandidates({ settings = {}, vaultPath = '', extraCandidates = [] } = {}) {
  const home = os.homedir();
  const values = [
    ...extraCandidates,
    process.env.TORCH_REPO_ROOT,
    process.env.TORCH_OS_ROOT,
    settings.repoRoot,
    path.join(home, 'torch-os'),
    path.join(home, 'Torchos'),
    path.join(home, 'Desktop', 'torch-os'),
    path.join(home, 'Desktop', 'Torchos'),
    path.join(home, 'Documents', 'torch-os'),
    path.join(home, 'Documents', 'Torchos'),
    path.join(home, 'repos', 'torch-os'),
    path.join(home, 'source', 'repos', 'torch-os'),
  ];
  if (process.platform === 'win32') {
    values.push('C:\\tools\\torch-os', 'D:\\tools\\torch-os', 'D:\\torch-os');
  } else {
    values.push('/opt/torch-os', path.join(home, 'torch-os'));
  }
  if (vaultPath) {
    const parent = path.dirname(path.resolve(vaultPath));
    const grandparent = path.dirname(parent);
    values.push(
      path.join(parent, 'torch-os'), path.join(parent, 'Torchos'),
      path.join(grandparent, 'torch-os'), path.join(grandparent, 'Torchos'),
    );
  }
  const seen = new Set();
  const result = [];
  for (const value of values) {
    const text = String(value || '').trim();
    if (!text) continue;
    const resolved = path.resolve(text);
    const key = process.platform === 'win32' ? resolved.toLowerCase() : resolved;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(resolved);
  }
  return result;
}

function discoverNearbyRepos({ vaultPath = '', extraCandidates = [] } = {}) {
  const roots = [
    os.homedir(),
    path.join(os.homedir(), 'Desktop'),
    path.join(os.homedir(), 'Documents'),
    ...extraCandidates.map((item) => path.dirname(path.resolve(String(item || '')))),
  ];
  if (vaultPath) {
    roots.push(path.dirname(path.resolve(vaultPath)));
    roots.push(path.dirname(path.dirname(path.resolve(vaultPath))));
  }
  if (process.platform === 'win32') roots.push('C:\\tools', 'D:\\tools');
  const found = [];
  const seen = new Set();
  for (const root of roots) {
    let entries = [];
    try {
      entries = fs.readdirSync(root, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries.slice(0, 400)) {
      if (!entry.isDirectory()) continue;
      const candidate = path.join(root, entry.name);
      const key = process.platform === 'win32' ? candidate.toLowerCase() : candidate;
      if (seen.has(key)) continue;
      seen.add(key);
      if (isRepoRoot(candidate)) found.push(candidate);
    }
  }
  return found;
}

function pickRepo(candidates = []) {
  let fallback = '';
  for (const candidate of candidates) {
    if (!isRepoRoot(candidate)) continue;
    if (!fallback) fallback = candidate;
    // 优先选带 .venv 的仓库，避免先命中一个没有 Python 的旧副本。
    if (findPython(candidate)) return candidate;
  }
  return fallback;
}

function findRepoRoot({ settings = {}, vaultPath = '', extraCandidates = [] } = {}) {
  const direct = pickRepo(repoCandidates({ settings, vaultPath, extraCandidates }));
  if (direct) return direct;
  return pickRepo(discoverNearbyRepos({ vaultPath, extraCandidates }));
}

function resolveToolchain({ settings = {}, vaultPath = '', extraCandidates = [] } = {}) {
  let repoRoot = isRepoRoot(settings.repoRoot) ? path.resolve(settings.repoRoot) : '';
  let pythonPath = repoRoot ? resolvePython(repoRoot, settings.pythonPath) : '';
  if (!repoRoot || !pythonPath) {
    // 旧路径存在但 .venv 已失效时，继续找本机其他带 Python 的仓库。
    const found = findRepoRoot({ settings, vaultPath, extraCandidates });
    if (found) {
      repoRoot = found;
      pythonPath = resolvePython(found, settings.pythonPath);
    }
  }
  return { repoRoot, pythonPath };
}

function sharedSettings(settings = {}) {
  return {
    baseUrl: settings.baseUrl || DEFAULT_SETTINGS.baseUrl,
    autoStart: settings.autoStart !== false,
  };
}

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

/**
 * ChatGPT 只认自己的域名。DNS 被污染或运营商重定向时，guest 里停下来的 host
 * 会是别的地方（实测见过被丢到 iana.org 一类页面），这时不能再报“已载入”。
 */
function isChatgptUrl(value) {
  try {
    const host = new URL(String(value || '')).hostname.toLowerCase();
    return host === 'chatgpt.com' || host.endsWith('.chatgpt.com')
      || host === 'chat.openai.com' || host === 'openai.com' || host.endsWith('.openai.com');
  } catch {
    return false;
  }
}

/**
 * 登录页不是故障现场（2026-11-19 用户实测踩到）：点「使用 Google 登录」本来就会
 * 跳到 accounts.google.com 走 OAuth，把这种正常跳转判成 DNS 劫持会直接盖掉登录页。
 * 这些域一律放行，登录完自己会回到 chatgpt.com。
 */
const AUTH_HOSTS = [
  'accounts.google.com', 'accounts.youtube.com', 'appleid.apple.com',
  'login.microsoftonline.com', 'login.live.com', 'login.microsoft.com',
  'auth.openai.com', 'auth0.openai.com', 'login.openai.com',
];
function isSigninUrl(value) {
  try {
    const host = new URL(String(value || '')).hostname.toLowerCase();
    return AUTH_HOSTS.some((item) => host === item || host.endsWith(`.${item}`));
  } catch {
    return false;
  }
}

/** 设置里的代理：可写 127.0.0.1:7892、http://127.0.0.1:7892，也可直接写 http=a:1;https=a:1。 */
function proxyRulesOf(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  if (text.includes('=')) return text;
  const hostport = text.replace(/^[a-z0-9+.-]+:\/\//i, '').replace(/\/+$/, '');
  return hostport ? `http=${hostport};https=${hostport}` : '';
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

// ── 本地界面层：静态文件 + 反向代理 + 启动状态 ───────────────────────
// 产品界面由插件自己的 Node 服务提供，不再等 Python。界面先画出来，
// Python 在后台启动；只有需要数据/网络能力时才通过 /api 代理由它处理。

const MIME_TYPES = Object.freeze({
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.txt': 'text/plain; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.wasm': 'application/wasm',
});
const STATIC_EXTENSIONS = new Set(Object.keys(MIME_TYPES));
const UI_CSP = "default-src 'self'; script-src 'self'; style-src 'self'; "
  + "img-src 'self' data: https:; connect-src 'self'; "
  + "frame-ancestors 'self' http://127.0.0.1:* http://localhost:* app:; "
  + "base-uri 'self'; form-action 'self'";
const HOP_BY_HOP = new Set([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'transfer-encoding', 'upgrade',
]);
const CACHEABLE_PATHS = [
  /^\/api\/workbench\/today(?:\?|$)/,
  /^\/api\/time\/tasks(?:\?|$)/,
  /^\/api\/goals(?:\?|$)/,
  /^\/api\/config\/status(?:\?|$)/,
  /^\/api\/habits\/overview(?:\?|$)/,
  /^\/api\/self\/overview(?:\?|$)/,
  /^\/api\/pillars(?:\?|$|\/[^/?]+\/overview(?:\?|$))/,
  /^\/api\/backups(?:\?|$)/,
  /^\/api\/audit\/summary(?:\?|$)/,
  /^\/api\/banner\/stats(?:\?|$)/,
  /^\/api\/love\/schedule\/(?:upcoming|month)(?:\?|$)/,
  /^\/api\/sync\/dida\/(?:habit-links|status)(?:\?|$)/,
  /^\/api\/vault\/connection(?:\?|$)/,
];

function httpGet(port, pathName, { timeoutMs = 1500, headers = {} } = {}) {
  return new Promise((resolve) => {
    let finished = false;
    const finish = (value) => { if (!finished) { finished = true; resolve(value); } };
    let request;
    try {
      request = http.request({
        host: '127.0.0.1', port, path: pathName, method: 'GET',
        agent: false,
        headers: { Accept: 'application/json', ...headers },
      }, (response) => {
        const chunks = [];
        let size = 0;
        response.on('data', (chunk) => {
          size += chunk.length;
          if (size <= 512 * 1024) chunks.push(chunk);
        });
        response.on('end', () => finish({
          status: response.statusCode || 0,
          headers: response.headers,
          body: Buffer.concat(chunks).toString('utf8'),
        }));
        response.on('error', () => finish(null));
      });
    } catch {
      finish(null);
      return;
    }
    request.setTimeout(timeoutMs, () => request.destroy(new Error('timeout')));
    request.on('error', () => finish(null));
    request.end();
  });
}

/** 后端健康检查；直接用 Node http，避免 Obsidian 请求层开销。 */
async function probeHealth(port, { timeoutMs = SERVICE_HEALTH_TIMEOUT_MS } = {}) {
  if (!Number.isInteger(Number(port)) || Number(port) <= 0) return null;
  const result = await httpGet(Number(port), '/api/health', { timeoutMs });
  if (!result || result.status !== 200) return null;
  try {
    const data = JSON.parse(result.body);
    return data?.status === 'ok' && data?.service === 'torch-os' ? data : null;
  } catch {
    return null;
  }
}

function fileSize(logPath) {
  try { return fs.statSync(logPath).size; } catch { return 0; }
}

/** 只读本次启动之后写入的日志，避免把上一次的 traceback 当成新错误。 */
function readLogSegment(logPath, startOffset = 0, maxBytes = 65536) {
  if (!logPath) return '';
  let fd;
  try {
    fd = fs.openSync(logPath, 'r');
    const size = fs.fstatSync(fd).size;
    const from = Math.min(size, Math.max(0, Math.max(Number(startOffset) || 0, size - maxBytes)));
    const length = Math.max(0, size - from);
    if (!length) return '';
    const buffer = Buffer.alloc(length);
    fs.readSync(fd, buffer, 0, length, from);
    let text = buffer.toString('utf8');
    if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
    return text.replace(/\0/g, '');
  } catch {
    return '';
  } finally {
    if (fd !== undefined) {
      try { fs.closeSync(fd); } catch { /* already closed */ }
    }
  }
}

function lastMeaningfulLine(text) {
  const lines = String(text || '').replace(/\r/g, '').split('\n')
    .map((line) => line.trim()).filter(Boolean);
  const errorLine = [...lines].reverse().find((line) => (
    /(?:Error|Exception|WinError|Traceback|refused|failed|失败|拒绝|无法|超时)/i.test(line)
  ));
  return String(errorLine || lines[lines.length - 1] || '').slice(0, 600);
}

/** 把后端日志/退出信息翻译成用户能行动的失败。 */
function classifyBackendFailure(text, { exitCode = null, signal = '', spawnError = null } = {}) {
  const raw = String(text || '');
  const tail = raw.slice(-8000);
  const detail = lastMeaningfulLine(tail);
  const base = { canRetry: true, canMigrate: false };
  if (spawnError) {
    const code = String(spawnError.code || '');
    if (code === 'ENOENT') {
      return {
        ...base, code: 'python_missing',
        message: '找不到可用的 Python。',
        hint: '在仓库运行 安装.cmd，或在插件设置里填写 Python 路径（只保存在本机）。',
        detail: String(spawnError.message || code),
      };
    }
    return {
      ...base, code: 'exited',
      message: `无法启动 Python：${String(spawnError.message || code || '未知错误')}`,
      hint: '检查插件设置里的 Python 路径和仓库路径。',
      detail,
    };
  }
  if (/带编号的业务目录|unnumber_vault/.test(tail)) {
    return {
      ...base, code: 'numbered_vault', canMigrate: true,
      message: '当前库仍使用旧编号目录（00_人生路 / 01_自我档案 / 02_生活运营 / 03_知识库），后端已拒绝启动。',
      hint: '先整理旧目录，再重启本地服务。目录迁移会先备份并生成可回滚清单。',
      detail,
    };
  }
  if (/No module named|ModuleNotFoundError/i.test(tail)) {
    return {
      ...base, code: 'module_missing',
      message: 'Python 依赖不完整，本地服务无法加载。',
      hint: '在仓库运行 安装.cmd（或 .venv 里安装 requirements.txt）后重试。',
      detail,
    };
  }
  if (/Address already in use|WinError 10048|EADDRINUSE/i.test(tail)) {
    return {
      ...base, code: 'port_conflict',
      message: '本地服务端口被占用。',
      hint: '点重试会让插件换一个空闲端口；若反复出现，检查是否有旧实例卡住。',
      detail,
    };
  }
  if (/database is locked|file is not a database|unable to open database/i.test(tail)) {
    return {
      ...base, code: 'database',
      message: '索引数据库被占用或损坏，本地服务无法启动。',
      hint: '关闭其它正在使用这个库的 Obsidian/终端实例后重试；仍失败时检查 .torch-os 的数据库文件和备份。',
      detail,
    };
  }
  if (/PermissionError|WinError 5|拒绝访问|Access is denied/i.test(tail)) {
    return {
      ...base, code: 'permission',
      message: '访问库或数据目录被拒绝。',
      hint: '检查文件夹权限、只读属性；若库在网盘同步目录里，先暂停同步再试。',
      detail,
    };
  }
  if (/存储配置|拆分|工作区身份|笔记库身份|设备配置/.test(tail)) {
    return {
      ...base, code: 'config_invalid',
      message: '本机的存储/工作区配置无效。',
      hint: '检查插件设置里的“拆分存储配置”，或暂时清空它。',
      detail,
    };
  }
  if (/会话已失效|拒绝跨|401/.test(tail)) {
    return {
      ...base, code: 'session_failed',
      message: '本地服务已启动，但工作台会话没有建立。',
      hint: '点重试重新连接；仍失败时查看日志。',
      detail,
    };
  }
  const exitText = exitCode === null ? '' : `（退出码 ${exitCode}${signal ? `，${signal}` : ''}）`;
  return {
    ...base, code: 'exited',
    message: `本地服务启动后立即退出${exitText}。`,
    hint: '点“查看日志”看具体错误；修好后点重试。',
    detail: detail || '日志里没有更多信息。',
  };
}

function mimeTypeOf(filePath) {
  return MIME_TYPES[path.extname(filePath).toLowerCase()] || 'application/octet-stream';
}

/** /assets/foo → <frontend>/foo；/ → <frontend>/index.html。返回 null 表示不是可服务的静态文件。 */
function safeStaticPath(frontendDir, rawPathname) {
  let pathname;
  try { pathname = decodeURIComponent(String(rawPathname || '/')); } catch { return null; }
  if (!pathname.startsWith('/') || pathname.includes('\0') || pathname.includes('\\')) return null;
  let relative;
  if (pathname === '/' || pathname === '/index.html') relative = 'index.html';
  else if (pathname.startsWith('/assets/')) relative = pathname.slice('/assets/'.length);
  else return null;
  if (!relative || relative.split('/').some((part) => !part || part === '.' || part === '..')) return null;
  const root = path.resolve(frontendDir);
  const resolved = path.resolve(root, relative);
  if (resolved !== root && !resolved.startsWith(root + path.sep)) return null;
  if (!STATIC_EXTENSIONS.has(path.extname(resolved).toLowerCase())) return null;
  try {
    const realRoot = fs.realpathSync.native(root);
    const real = fs.realpathSync.native(resolved);
    if (real !== realRoot && !real.startsWith(realRoot + path.sep)) return null;
    if (!fs.statSync(real).isFile()) return null;
    return real;
  } catch {
    return null;
  }
}

function isLoopbackHostname(value) {
  return value === '127.0.0.1' || value === 'localhost' || value === '[::1]' || value === '::1';
}

/** 校验 Host，防止 DNS rebinding；必须正好是当前 UI 端口。 */
function isAllowedUiHost(hostHeader, port) {
  const text = String(hostHeader || '').trim();
  if (!text || !port) return false;
  try {
    const parsed = new URL(`http://${text}`);
    return isLoopbackHostname(parsed.hostname) && Number(parsed.port) === Number(port);
  } catch {
    return false;
  }
}

function isAllowedUiOrigin(origin, port) {
  if (!origin) return true;
  try {
    const parsed = new URL(String(origin));
    return isLoopbackHostname(parsed.hostname) && Number(parsed.port) === Number(port);
  } catch {
    return false;
  }
}

function resolveVaultDataDir(binding, settings = {}) {
  let dataDir = path.join(binding.path, '.torch-os');
  const storageConfigPath = String(settings.storageConfigPath || '').trim();
  if (storageConfigPath) {
    const storage = JSON.parse(fs.readFileSync(storageConfigPath, 'utf8'));
    if (storage.version !== 1 || !path.isAbsolute(storage.state) ||
        normalizedVaultPath(storage.notes) !== normalizedVaultPath(binding.path)) {
      throw new Error('存储配置不属于当前库');
    }
    dataDir = path.join(storage.state, 'vault-state');
  }
  return { dataDir, storageConfigPath };
}

function isCacheableApiPath(value) {
  const text = String(value || '');
  if (!text.startsWith('/api/')) return false;
  const pathname = text.split('?', 1)[0];
  if (pathname === '/api/personal-data' || pathname === '/api/health') return false;
  return CACHEABLE_PATHS.some((pattern) => pattern.test(text));
}

/** 上次成功读取的只读页面数据。只放在本机配置目录，不进库/网盘。 */
class UiCache {
  constructor({ dir, now = () => Date.now(), ttlMs = UI_CACHE_TTL_MS,
    entryLimit = UI_CACHE_ENTRY_LIMIT, totalLimit = UI_CACHE_TOTAL_LIMIT,
    maxEntries = UI_CACHE_MAX_ENTRIES } = {}) {
    this.dir = dir;
    this.now = now;
    this.ttlMs = ttlMs;
    this.entryLimit = entryLimit;
    this.totalLimit = totalLimit;
    this.maxEntries = maxEntries;
    this.ready = false;
    this.lastPrune = 0;
  }

  async init() {
    if (!this.dir) return this;
    try {
      await fs.promises.mkdir(this.dir, { recursive: true, mode: 0o700 });
      this.ready = true;
      this.lastPrune = this.now();
      await this.prune();
    } catch {
      this.ready = false;
    }
    return this;
  }

  key(url) { return crypto.createHash('sha256').update(String(url)).digest('hex'); }

  pathFor(url) { return path.join(this.dir, `${this.key(url)}.json`); }

  async get(url) {
    if (!this.ready || !isCacheableApiPath(url)) return null;
    try {
      const raw = await fs.promises.readFile(this.pathFor(url), 'utf8');
      const entry = JSON.parse(raw);
      if (!entry || entry.key !== url || typeof entry.body !== 'string') return null;
      const savedAt = Date.parse(entry.savedAt || '');
      if (!Number.isFinite(savedAt) || this.now() - savedAt > this.ttlMs) return null;
      return entry;
    } catch {
      return null;
    }
  }

  async set(url, bodyBuffer, contentType = 'application/json; charset=utf-8') {
    if (!this.ready || !isCacheableApiPath(url) || !Buffer.isBuffer(bodyBuffer)) return false;
    if (bodyBuffer.length > this.entryLimit) return false;
    const entry = {
      v: 1, key: url, savedAt: new Date(this.now()).toISOString(),
      contentType, body: bodyBuffer.toString('utf8'),
    };
    const target = this.pathFor(url);
    const temporary = `${target}.tmp-${process.pid}-${Date.now()}`;
    try {
      await fs.promises.writeFile(temporary, JSON.stringify(entry), { mode: 0o600 });
      await fs.promises.rename(temporary, target);
      if (this.now() - this.lastPrune > 60000) {
        this.lastPrune = this.now();
        await this.prune();
      }
      return true;
    } catch {
      try { await fs.promises.rm(temporary, { force: true }); } catch { /* ignore */ }
      return false;
    }
  }

  async clear() {
    if (!this.dir) return;
    try {
      const names = await fs.promises.readdir(this.dir);
      await Promise.all(names.filter((name) => name.endsWith('.json') || name.includes('.tmp-'))
        .map((name) => fs.promises.rm(path.join(this.dir, name), { force: true })));
    } catch { /* ignore */ }
  }

  async stats() {
    if (!this.dir) return { entries: 0, bytes: 0 };
    try {
      const names = (await fs.promises.readdir(this.dir)).filter((name) => name.endsWith('.json'));
      let bytes = 0;
      for (const name of names) {
        try { bytes += (await fs.promises.stat(path.join(this.dir, name))).size; } catch { /* ignore */ }
      }
      return { entries: names.length, bytes };
    } catch {
      return { entries: 0, bytes: 0 };
    }
  }

  async prune() {
    if (!this.ready) return;
    let names;
    try { names = (await fs.promises.readdir(this.dir)).filter((name) => name.endsWith('.json')); }
    catch { return; }
    const rows = [];
    for (const name of names) {
      const file = path.join(this.dir, name);
      try {
        const stat = await fs.promises.stat(file);
        rows.push({ file, size: stat.size, mtime: stat.mtimeMs });
      } catch { /* ignore */ }
    }
    rows.sort((a, b) => a.mtime - b.mtime);
    const expiredBefore = new Date(this.now() - this.ttlMs).getTime();
    let total = 0;
    for (const row of rows) total += row.size;
    const remove = new Set();
    for (const row of rows) {
      if (row.mtime < expiredBefore) remove.add(row.file);
    }
    for (const row of rows) {
      const remaining = rows.length - remove.size;
      if (remaining <= this.maxEntries && total <= this.totalLimit) break;
      if (remove.has(row.file)) { total -= row.size; continue; }
      remove.add(row.file);
      total -= row.size;
    }
    await Promise.all([...remove].map((file) => fs.promises.rm(file, { force: true }).catch(() => {})));
  }
}

function uniquePorts(values) {
  const result = [];
  for (const value of values) {
    const port = Number(value);
    if (Number.isInteger(port) && port > 0 && port < 65536 && !result.includes(port)) result.push(port);
  }
  return result;
}

// ── 后端状态机 ─────────────────────────────────────────────────────
class BackendController {
  constructor({
    getBinding, getSettings, resolveToolchain, saveDeviceSettings,
    probe = probeHealth, portFree = portAvailable, spawnProcess = spawn,
    sleep: sleepImpl = sleep, readLog = readLogSegment, onState = () => {},
  } = {}) {
    this.getBinding = getBinding;
    this.getSettings = getSettings;
    this.resolveToolchain = resolveToolchain;
    this.saveDeviceSettings = saveDeviceSettings;
    this.probe = probe;
    this.portFree = portFree;
    this.spawnProcess = spawnProcess;
    this.sleep = sleepImpl;
    this.readLog = readLog;
    this.onState = onState;
    this.state = {
      phase: 'idle', port: 0, url: '', vaultId: '', attempt: 0,
      startedAt: '', readyAt: '', elapsedMs: 0, failure: null,
    };
    this.ensurePromise = null;
    this.generation = 0;
    this.child = null;
    this.ownedPid = null;
    this.exitInfo = null;
    this.startedMs = 0;
    this.logPath = '';
    this.logStartOffset = 0;
    this.heartbeat = null;
    this.heartbeatFailures = 0;
    this.autoRetryAfterReady = false;
    this.disposed = false;
  }

  snapshot() {
    return {
      ...this.state,
      failure: this.state.failure ? { ...this.state.failure } : null,
      autoStart: this.getSettings?.()?.autoStart !== false,
    };
  }

  _set(patch) {
    if (this.disposed) return;
    this.state = { ...this.state, ...patch };
    this.onState(this.snapshot());
  }

  _fail(failure) {
    this._set({
      phase: 'failed',
      failure: { canRetry: true, canMigrate: false, ...failure },
      elapsedMs: this.startedMs ? Date.now() - this.startedMs : 0,
      readyAt: '',
    });
    this._clearHeartbeat();
  }

  async ensure({ manual = false, force = false } = {}) {
    if (force) return this.restart({ manual: true });
    if (this.ensurePromise) return this.ensurePromise;
    if (this.state.phase === 'starting') return this.snapshot();
    if (this.state.phase === 'ready') return this.snapshot();
    if (this.state.phase === 'failed' && !manual) return this.snapshot();
    this.ensurePromise = this._runStart(manual).finally(() => { this.ensurePromise = null; });
    return this.ensurePromise;
  }

  async restart({ manual = true } = {}) {
    this.generation += 1;
    this._stopOwnedChild();
    this._clearHeartbeat();
    this.exitInfo = null;
    this.autoRetryAfterReady = false;
    this._set({
      phase: 'idle', port: 0, url: '', failure: null,
      startedAt: '', readyAt: '', elapsedMs: 0,
    });
    this.ensurePromise = null;
    return this.ensure({ manual });
  }

  dispose() {
    this.disposed = true;
    this.generation += 1;
    this._clearHeartbeat();
    this.child = null;
    this.ownedPid = null;
  }

  _stopOwnedChild() {
    if (!this.ownedPid) return;
    try { process.kill(this.ownedPid); } catch { /* already gone */ }
    this.ownedPid = null;
    this.child = null;
  }

  _clearHeartbeat() {
    if (this.heartbeat) {
      clearInterval(this.heartbeat);
      this.heartbeat = null;
    }
    this.heartbeatFailures = 0;
  }

  _startHeartbeat() {
    this._clearHeartbeat();
    this.heartbeat = setInterval(async () => {
      if (this.disposed || this.state.phase !== 'ready') return;
      const health = await this.probe(this.state.port, { timeoutMs: 1000 });
      if (health?.vault_id === this.state.vaultId) {
        this.heartbeatFailures = 0;
        return;
      }
      this.heartbeatFailures += 1;
      if (this.heartbeatFailures < 2) return;
      this._fail({
        code: 'backend_unreachable',
        message: '本地服务已断开。',
        hint: '点重试重新启动；已填写的表单内容会留在页面上。',
        canRetry: true,
      });
    }, 10000);
    if (typeof this.heartbeat.unref === 'function') this.heartbeat.unref();
  }

  _healthCandidates() {
    const settings = this.getSettings() || {};
    return uniquePorts([
      this.state.port,
      settings.instancePort,
      portFromUrl(settings.baseUrl),
      DEFAULT_PORT,
      ...Array.from({ length: 16 }, (_, index) => PLUGIN_PORT_BASE + index),
    ]);
  }

  async _discover(binding) {
    const ports = this._healthCandidates();
    const results = await Promise.all(ports.map(async (port) => ({
      port, health: await this.probe(port, { timeoutMs: SERVICE_PROBE_TIMEOUT_MS }),
    })));
    return results.find((row) => row.health?.vault_id === binding.id) || null;
  }

  async _pickBackendPort() {
    const settings = this.getSettings() || {};
    const manualPort = portFromUrl(settings.baseUrl);
    const stored = Number(settings.instancePort) || 0;
    const preferred = stored > 0 && stored !== manualPort ? stored : PLUGIN_PORT_BASE;
    for (let offset = 0; offset < PORT_ATTEMPTS; offset += 1) {
      const port = preferred + offset;
      if (port > 65535) break;
      if (await this.portFree(port)) return port;
    }
    return 0;
  }

  _onChildExit(gen, code, signal) {
    if (gen !== this.generation || this.disposed) return;
    this.exitInfo = { exitCode: code, signal: signal || '' };
    this.child = null;
    this.ownedPid = null;
    if (this.state.phase === 'starting') {
      const text = this.readLog(this.logPath, this.logStartOffset, 65536);
      const failure = classifyBackendFailure(text, this.exitInfo);
      this._fail({ ...failure, logPath: this.logPath, exitCode: code });
    } else if (this.state.phase === 'ready') {
      if (this.autoRetryAfterReady) {
        this._fail({
          code: 'backend_exited',
          message: '本地服务意外退出。',
          hint: '点重试重新启动；也可以查看日志定位原因。',
          logPath: this.logPath,
          exitCode: code,
          canRetry: true,
        });
        return;
      }
      this.autoRetryAfterReady = true;
      this._set({ phase: 'starting', failure: null, startedAt: new Date().toISOString() });
      setTimeout(() => {
        if (gen !== this.generation) return;
        this.ensurePromise = this._runStart(true).finally(() => { this.ensurePromise = null; });
      }, 300);
    }
  }

  async _runStart(manual) {
    if (this.disposed) return this.snapshot();
    const binding = this.getBinding?.();
    if (!binding) {
      this._fail({ code: 'vault_missing', message: '拿不到当前 Obsidian 库路径。', hint: '请只在桌面版 Obsidian 里使用。' });
      return this.snapshot();
    }
    const gen = ++this.generation;
    this.startedMs = Date.now();
    this.exitInfo = null;
    this.logPath = '';
    this.logStartOffset = 0;
    this._set({
      phase: 'starting', failure: null, port: 0, url: '', vaultId: binding.id,
      startedAt: new Date(this.startedMs).toISOString(), readyAt: '', elapsedMs: 0,
      attempt: this.state.attempt + 1,
    });
    try {
      const found = await this._discover(binding);
      if (gen !== this.generation) return this.snapshot();
      if (found) {
        this._adopt(found.port);
        return this.snapshot();
      }
      const settings = this.getSettings() || {};
      if (!manual && settings.autoStart === false) {
        this._fail({
          code: 'not_started', message: '本地服务未启动。',
          hint: '点“启动本地服务”后重试。',
        });
        return this.snapshot();
      }
      const toolchain = this.resolveToolchain?.() || {};
      if (!toolchain.repoRoot) {
        this._fail({
          code: 'repo_missing', message: '这台电脑上没找到 torch-os 仓库。',
          hint: '在插件设置里填写仓库路径（只保存在本机），或设置 TORCH_REPO_ROOT。',
        });
        return this.snapshot();
      }
      if (!toolchain.pythonPath) {
        this._fail({
          code: 'python_missing', message: '找到了仓库，但缺少可用的 .venv Python。',
          hint: '先在仓库运行 安装.cmd，或在插件设置里填写 Python 路径。',
        });
        return this.snapshot();
      }
      const { dataDir, storageConfigPath } = resolveVaultDataDir(binding, settings);
      const port = await this._pickBackendPort();
      if (!port) {
        this._fail({ code: 'no_port', message: '找不到可用的本机端口来启动服务。', hint: '关闭占用 9168–9231 的程序后重试。' });
        return this.snapshot();
      }
      const logDir = path.join(dataDir, 'logs');
      fs.mkdirSync(logDir, { recursive: true });
      this.logPath = path.join(logDir, `torch-backend-${port}.log`);
      this.logStartOffset = fileSize(this.logPath);
      const logFd = fs.openSync(this.logPath, 'a');
      let child;
      try {
        child = this.spawnProcess(toolchain.pythonPath, ['-m', 'backend.app.run'], {
          cwd: toolchain.repoRoot,
          detached: true,
          windowsHide: true,
          stdio: ['ignore', logFd, logFd],
          env: {
            ...process.env,
            HOST: '127.0.0.1',
            PORT: String(port),
            VAULT_DIR: binding.path,
            DATA_DIR: dataDir,
            TORCH_STORAGE_CONFIG: storageConfigPath,
            TORCH_VAULT_SCOPED: '1',
            WORKSPACE_NAME: binding.name,
            VAULT_READ_ONLY: '0',
            ALLOW_REMOTE_API: '0',
            PYTHONUTF8: '1',
            PYTHONIOENCODING: 'utf-8',
            PYTHONUNBUFFERED: '1',
          },
        });
      } finally {
        try { fs.closeSync(logFd); } catch { /* already closed */ }
      }
      this.child = child;
      this.ownedPid = child.pid || null;
      const spawned = new Promise((resolve, reject) => {
        child.once('spawn', resolve);
        child.once('error', reject);
      });
      child.once('exit', (code, signal) => this._onChildExit(gen, code, signal));
      try {
        await spawned;
      } catch (spawnError) {
        this._fail({
          ...classifyBackendFailure('', { spawnError }),
          logPath: this.logPath,
        });
        return this.snapshot();
      }
      if (typeof child.unref === 'function') child.unref();
      const deadline = Date.now() + SERVICE_START_DEADLINE_MS;
      while (Date.now() < deadline) {
        if (gen !== this.generation || this.state.phase !== 'starting') break;
        if (this.exitInfo) break;
        const health = await this.probe(port, { timeoutMs: SERVICE_HEALTH_TIMEOUT_MS });
        if (health?.vault_id === binding.id) {
          this._adopt(port);
          return this.snapshot();
        }
        await this.sleep(SERVICE_HEALTH_POLL_MS);
      }
      if (gen !== this.generation) return this.snapshot();
      if (this.state.phase !== 'starting') return this.snapshot();
      if (this.exitInfo) {
        const exitedText = this.readLog(this.logPath, this.logStartOffset, 65536);
        const exitedFailure = classifyBackendFailure(exitedText, this.exitInfo);
        this._fail({ ...exitedFailure, logPath: this.logPath, exitCode: this.exitInfo.exitCode });
        return this.snapshot();
      }
      const text = this.readLog(this.logPath, this.logStartOffset, 65536);
      this._fail({
        code: 'timeout', message: '本地服务启动超时（20 秒）。',
        hint: '点重试；仍失败时查看日志末尾。',
        logPath: this.logPath, detail: lastMeaningfulLine(text),
      });
      return this.snapshot();
    } catch (error) {
      const message = String(error?.message || error || '未知错误');
      const configLike = /存储配置|拆分|工作区身份|笔记库身份|设备配置/.test(message);
      this._fail({
        code: configLike ? 'config_invalid' : 'start_failed',
        message: configLike ? `本地数据配置无效：${message}` : `本地服务启动失败：${message}`,
        hint: configLike
          ? '检查插件设置里的“拆分存储配置”，或暂时清空它。'
          : '检查插件设置里的仓库/Python 路径和日志。',
        logPath: this.logPath,
        canRetry: true,
      });
      return this.snapshot();
    }
  }

  _adopt(port) {
    this.autoRetryAfterReady = false;
    this.exitInfo = null;
    const settings = this.getSettings?.() || {};
    if (Number(settings.instancePort) !== port) {
      settings.instancePort = port;
      this.saveDeviceSettings?.();
    }
    this._set({
      phase: 'ready', port, url: `http://127.0.0.1:${port}`, failure: null,
      readyAt: new Date().toISOString(),
      elapsedMs: this.startedMs ? Date.now() - this.startedMs : 0,
    });
    this._startHeartbeat();
  }

  markRequestFailure(failure = null) {
    if (this.state.phase !== 'ready') return;
    this._fail(failure || {
      code: 'backend_unreachable',
      message: '本地服务连接断开。',
      hint: '点重试重新连接；已填写的表单内容会留在页面上。',
      canRetry: true,
    });
  }
}

class LocalUiServer {
  constructor({
    frontendDir, vaultId = '', vaultName = '', controller, cache = null,
    migrator = null, actionToken = ACTION_TOKEN, onStaticRoot = null,
  } = {}) {
    this.frontendDir = frontendDir;
    this.vaultId = vaultId;
    this.vaultName = vaultName;
    this.controller = controller;
    this.cache = cache;
    this.migrator = migrator;
    this.actionToken = actionToken;
    this.onStaticRoot = onStaticRoot;
    this.server = null;
    this.port = 0;
    this.url = '';
    this.cookie = '';
    this.cookiePort = 0;
    this.cookiePromise = null;
    this.cookiePromisePort = 0;
    this.sockets = new Set();
    this.upgraded = new Set();
  }

  async listen({ preferredPort = 0, attempts = UI_PORT_ATTEMPTS } = {}) {
    const candidates = [];
    const preferred = Number(preferredPort);
    if (Number.isInteger(preferred) && preferred > 0) candidates.push(preferred);
    const hash = parseInt(String(this.vaultId || '').slice(0, 8), 16) || 0;
    for (let index = 0; index < attempts; index += 1) {
      candidates.push(UI_PORT_BASE + ((hash + index) % attempts));
    }
    candidates.push(0);
    const seen = new Set();
    let lastError = null;
    for (const port of candidates) {
      if (seen.has(port)) continue;
      seen.add(port);
      try {
        await this._listenOn(port);
        return this;
      } catch (error) {
        lastError = error;
        if (error?.code !== 'EADDRINUSE') throw error;
      }
    }
    throw lastError || new Error('无法找到可用的本地界面端口');
  }

  _listenOn(port) {
    return new Promise((resolve, reject) => {
      const server = http.createServer((req, res) => {
        this.handleRequest(req, res).catch(() => {
          if (!res.headersSent) this._sendJson(res, 500, { detail: '本地界面服务出错' });
          else try { res.destroy(); } catch { /* ignore */ }
        });
      });
      server.on('upgrade', (req, socket, head) => this.handleUpgrade(req, socket, head));
      server.on('connection', (socket) => {
        this.sockets.add(socket);
        socket.on('close', () => this.sockets.delete(socket));
      });
      const onError = (error) => {
        server.removeListener('listening', onListening);
        try { server.close(); } catch { /* ignore */ }
        reject(error);
      };
      const onListening = () => {
        server.removeListener('error', onError);
        server.on('error', () => { /* keep serving */ });
        this.server = server;
        this.port = server.address().port;
        this.url = `http://127.0.0.1:${this.port}`;
        resolve();
      };
      server.once('error', onError);
      server.once('listening', onListening);
      server.listen(port, '127.0.0.1');
    });
  }

  close() {
    this._resetConnection();
    for (const socket of this.upgraded) {
      try { socket.destroy(); } catch { /* ignore */ }
    }
    this.upgraded.clear();
    for (const socket of this.sockets) {
      try { socket.destroy(); } catch { /* ignore */ }
    }
    this.sockets.clear();
    const server = this.server;
    this.server = null;
    if (!server) return Promise.resolve();
    return new Promise((resolve) => {
      const timer = setTimeout(resolve, 1200);
      if (typeof timer.unref === 'function') timer.unref();
      try {
        server.close(() => { clearTimeout(timer); resolve(); });
      } catch {
        clearTimeout(timer);
        resolve();
      }
    });
  }

  _resetConnection() {
    this.cookie = '';
    this.cookiePort = 0;
    this.cookiePromise = null;
  }

  async handleRequest(req, res) {
    if (!isAllowedUiHost(req.headers.host, this.port)) {
      this._sendJson(res, 403, { detail: 'Host 校验失败' });
      return;
    }
    let url;
    try { url = new URL(req.url || '/', 'http://127.0.0.1'); }
    catch {
      this._sendJson(res, 400, { detail: '无效的请求地址' });
      return;
    }
    if (url.pathname.startsWith('/__torch/')) {
      await this.handleService(req, res, url);
      return;
    }
    if (url.pathname === '/api' || url.pathname.startsWith('/api/')
        || url.pathname === '/harness' || url.pathname.startsWith('/harness/')) {
      await this.handleProxy(req, res, url);
      return;
    }
    await this.serveStatic(req, res, url);
  }

  async serveStatic(req, res, url) {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      this._sendJson(res, 405, { detail: '只支持 GET/HEAD' });
      return;
    }
    const filePath = safeStaticPath(this.frontendDir, url.pathname);
    if (!filePath) {
      this._sendJson(res, 404, { detail: '找不到这个界面文件' });
      return;
    }
    let stat;
    try { stat = await fs.promises.stat(filePath); }
    catch {
      this._sendJson(res, 404, { detail: '找不到这个界面文件' });
      return;
    }
    res.writeHead(200, {
      'Content-Type': mimeTypeOf(filePath),
      'Content-Length': stat.size,
      'Cache-Control': 'no-store',
      'Content-Security-Policy': UI_CSP,
      'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'no-referrer',
    });
    if (url.pathname === '/' || url.pathname === '/index.html') {
      res.once('finish', () => {
        if (req.method === 'GET') {
          try { this.onStaticRoot?.(); } catch { /* ignore */ }
        }
      });
    }
    if (req.method === 'HEAD') { res.end(); return; }
    const stream = fs.createReadStream(filePath);
    stream.on('error', () => { if (!res.headersSent) this._sendJson(res, 500, { detail: '读取界面文件失败' }); else try { res.destroy(); } catch { /* ignore */ } });
    stream.pipe(res);
  }

  _sendJson(res, status, payload, extraHeaders = {}) {
    if (res.headersSent) { try { res.destroy(); } catch { /* ignore */ } return; }
    const body = Buffer.from(JSON.stringify(payload));
    res.writeHead(status, {
      'Content-Type': 'application/json; charset=utf-8',
      'Content-Length': body.length,
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'no-referrer',
      ...extraHeaders,
    });
    res.end(body);
  }

  _sendCached(res, entry) {
    if (res.headersSent) return;
    const body = Buffer.from(String(entry.body || ''), 'utf8');
    const service = this.controller?.snapshot?.() || { phase: 'unknown' };
    const age = Math.max(0, Math.round((Date.now() - Date.parse(entry.savedAt || '')) / 1000));
    res.writeHead(200, {
      'Content-Type': entry.contentType || 'application/json; charset=utf-8',
      'Content-Length': body.length,
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      'X-Torch-Cache': 'stale',
      'X-Torch-Cache-At': entry.savedAt || '',
      'X-Torch-Cache-Age': String(Number.isFinite(age) ? age : 0),
      'X-Torch-Service': service.phase || 'unknown',
    });
    res.end(body);
  }

  _sendServiceUnavailable(res, snapshot) {
    if (res.headersSent) return;
    const phase = snapshot?.phase || 'idle';
    const failure = snapshot?.failure;
    let code = 'service_starting';
    let message = '本地数据服务正在启动…';
    let hint = '';
    if (phase === 'failed') {
      code = failure?.code || 'service_failed';
      message = failure?.message || '本地服务启动失败。';
      hint = failure?.hint || '点重试；仍失败时查看日志。';
    } else if (phase === 'idle') {
      code = 'service_not_started';
      message = '本地服务未启动。';
      hint = '点“启动本地服务”后重试。';
    }
    this._sendJson(res, 503, {
      detail: { code, message, hint },
      service: snapshot,
    }, {
      'X-Torch-Service': phase,
      'Retry-After': '0.3',
    });
  }

  async handleProxy(req, res, url) {
    const method = String(req.method || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD' && !isAllowedUiOrigin(req.headers.origin, this.port)) {
      this._sendJson(res, 403, { detail: '拒绝跨来源写操作' });
      return;
    }
    // Obsidian 的 iframe fetch 会带 cross-site + same-origin；真实跨站网页的 fetch
    // 是 cors，不能读响应也不能借用代理里的后端会话。no-cors 是图片/媒体这类
    // 子资源（读不到响应），放行以免 love 相册等 /api 媒体图被误伤。
    const site = String(req.headers['sec-fetch-site'] || '');
    const mode = String(req.headers['sec-fetch-mode'] || '');
    if (site === 'cross-site' && mode !== 'same-origin' && mode !== 'no-cors') {
      this._sendJson(res, 403, { detail: '拒绝跨站点数据请求' });
      return;
    }
    let snapshot = this.controller.snapshot();
    if (snapshot.phase !== 'ready') {
      if (snapshot.autoStart && snapshot.phase !== 'failed') {
        this.controller.ensure().catch(() => {});
        snapshot = this.controller.snapshot();
      }
      if (method === 'GET' && this.cache) {
        const cached = await this.cache.get(req.url);
        if (cached) {
          this._sendCached(res, cached);
          return;
        }
      }
      this._sendServiceUnavailable(res, snapshot);
      return;
    }
    try {
      const cookie = await this._backendCookie(snapshot);
      await this._forward(req, res, snapshot, cookie, true);
    } catch (error) {
      if (method === 'GET' && this.cache) {
        const cached = await this.cache.get(req.url);
        if (cached) {
          this._sendCached(res, cached);
          return;
        }
      }
      if (error?.code === 'session_failed') {
        this.controller.markRequestFailure?.({
          code: 'session_failed',
          message: '工作台会话没有建立。',
          hint: '点重试重新连接；仍失败时查看日志。',
          canRetry: true,
        });
      }
      if (!res.headersSent) {
        this._sendJson(res, 502, {
          detail: {
            code: error?.code === 'session_failed' ? 'session_failed' : 'backend_unreachable',
            message: error?.code === 'session_failed'
              ? '工作台会话没有建立，请重试。'
              : '本地服务暂时不可用。',
            hint: '点重试重新连接；仍失败时查看日志。',
          },
        });
      } else {
        try { res.destroy(); } catch { /* ignore */ }
      }
    }
  }

  async _backendCookie(backend) {
    if (!backend?.port) throw Object.assign(new Error('本地服务端口未就绪'), { code: 'session_failed' });
    if (this.cookie && this.cookiePort === backend.port) return this.cookie;
    if (this.cookiePromise && this.cookiePromisePort === backend.port) return this.cookiePromise;
    this.cookiePromisePort = backend.port;
    this.cookiePromise = (async () => {
      const result = await httpGet(backend.port, '/', {
        timeoutMs: 4000,
        headers: { 'Sec-Fetch-Site': 'same-origin' },
      });
      if (!result || result.status !== 200) {
        throw Object.assign(new Error('无法建立本地会话'), { code: 'session_failed' });
      }
      const raw = result.headers['set-cookie'];
      const values = Array.isArray(raw) ? raw : (raw ? [raw] : []);
      let cookie = '';
      for (const value of values) {
        const match = String(value).match(/^(torch_session_[0-9a-fA-F]+=[^;]+)/);
        if (match) { cookie = match[1]; break; }
      }
      if (!cookie) throw Object.assign(new Error('本地会话 Cookie 缺失'), { code: 'session_failed' });
      this.cookie = cookie;
      this.cookiePort = backend.port;
      return cookie;
    })().finally(() => {
      if (this.cookiePromisePort === backend.port) {
        this.cookiePromise = null;
        this.cookiePromisePort = 0;
      }
    });
    return this.cookiePromise;
  }

  _proxyHeaders(req, backend) {
    const headers = {};
    for (const [name, value] of Object.entries(req.headers)) {
      const lower = name.toLowerCase();
      if (HOP_BY_HOP.has(lower) || lower === 'host' || lower === 'cookie'
          || lower === 'authorization' || lower === 'x-api-token'
          || lower === 'x-torch-ui-token' || lower.startsWith('sec-fetch-')) {
        continue;
      }
      if (lower === 'origin') {
        headers.origin = backend.url;
        continue;
      }
      headers[name] = value;
    }
    headers.host = `127.0.0.1:${backend.port}`;
    if (req.headers.origin) headers.origin = backend.url;
    return headers;
  }

  async _forward(req, res, backend, cookie, retry) {
    const headers = this._proxyHeaders(req, backend);
    headers.cookie = cookie;
    const method = String(req.method || 'GET').toUpperCase();
    const capture = method === 'GET' && isCacheableApiPath(req.url) && this.cache;
    const chunks = [];
    let captured = 0;
    let overflow = false;
    const upstream = http.request({
      host: '127.0.0.1',
      port: backend.port,
      path: req.url,
      method,
      agent: false,
      headers,
    }, (upstreamRes) => {
      if (retry && upstreamRes.statusCode === 401 && (method === 'GET' || method === 'HEAD')) {
        upstreamRes.resume();
        this.cookie = '';
        this.cookiePort = 0;
        this._backendCookie(backend)
          .then((nextCookie) => this._forward(req, res, backend, nextCookie, false))
          .catch(() => {
            if (!res.headersSent) this._sendServiceUnavailable(res, this.controller.snapshot());
            else try { res.destroy(); } catch { /* ignore */ }
          });
        return;
      }
      const responseHeaders = {};
      for (const [name, value] of Object.entries(upstreamRes.headers)) {
        const lower = name.toLowerCase();
        if (HOP_BY_HOP.has(lower) || lower === 'set-cookie') continue;
        responseHeaders[name] = value;
      }
      if (responseHeaders.location) {
        const location = String(responseHeaders.location);
        if (location.startsWith(backend.url)) {
          responseHeaders.location = this.url + location.slice(backend.url.length);
        }
      }
      res.writeHead(upstreamRes.statusCode || 502, responseHeaders);
      const contentType = String(upstreamRes.headers['content-type'] || '');
      const canCache = capture && upstreamRes.statusCode === 200
        && contentType.includes('application/json')
        && !upstreamRes.headers['content-encoding'];
      if (canCache) {
        upstreamRes.on('data', (chunk) => {
          if (overflow) return;
          captured += chunk.length;
          if (captured > UI_CACHE_ENTRY_LIMIT) { overflow = true; chunks.length = 0; return; }
          chunks.push(chunk);
        });
      }
      if (method !== 'GET' && method !== 'HEAD' && (upstreamRes.statusCode || 0) < 400
          && String(req.url).startsWith('/api/')
          && !String(req.url).startsWith('/api/personal-data')) {
        this.cache?.clear().catch(() => {});
      }
      upstreamRes.on('end', () => {
        if (canCache && !overflow && chunks.length && this.cache) {
          this.cache.set(req.url, Buffer.concat(chunks), contentType).catch(() => {});
        }
      });
      upstreamRes.on('error', () => { try { res.destroy(); } catch { /* ignore */ } });
      upstreamRes.pipe(res);
    });
    upstream.on('error', (error) => {
      const aborted = req.aborted || res.destroyed || res.writableEnded;
      if (!aborted && (error?.code === 'ECONNREFUSED' || error?.code === 'ECONNRESET')) {
        this.controller.markRequestFailure?.();
      }
      if (!aborted && !res.headersSent) this._sendServiceUnavailable(res, this.controller.snapshot());
      else if (!aborted) try { res.destroy(); } catch { /* ignore */ }
    });
    res.on('close', () => { if (!res.writableEnded) upstream.destroy(); });
    req.on('aborted', () => upstream.destroy());
    req.on('error', () => upstream.destroy());
    if (method === 'GET' || method === 'HEAD') upstream.end();
    else req.pipe(upstream);
  }

  handleUpgrade(req, socket, head) {
    let url;
    try { url = new URL(req.url || '/', 'http://127.0.0.1'); }
    catch { try { socket.destroy(); } catch { /* ignore */ } return; }
    const reject = (status, message) => {
      try {
        socket.write(`HTTP/1.1 ${status} ${message}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
        socket.destroy();
      } catch { /* ignore */ }
    };
    if (!isAllowedUiHost(req.headers.host, this.port)) return reject(403, 'Forbidden');
    if (!isAllowedUiOrigin(req.headers.origin, this.port)) return reject(403, 'Forbidden');
    if (!(url.pathname === '/harness' || url.pathname.startsWith('/harness/'))) {
      return reject(404, 'Not Found');
    }
    const backend = this.controller.snapshot();
    if (backend.phase !== 'ready' || !backend.port) return reject(503, 'Service Unavailable');
    const headers = this._proxyHeaders(req, backend);
    headers.connection = 'Upgrade';
    headers.upgrade = req.headers.upgrade || 'websocket';
    const upstream = http.request({
      host: '127.0.0.1',
      port: backend.port,
      path: req.url,
      method: 'GET',
      agent: false,
      headers,
    });
    upstream.on('upgrade', (upstreamRes, upstreamSocket, upstreamHead) => {
      const lines = [`HTTP/1.1 ${upstreamRes.statusCode} ${upstreamRes.statusMessage || 'Switching Protocols'}`];
      for (let index = 0; index < upstreamRes.rawHeaders.length; index += 2) {
        lines.push(`${upstreamRes.rawHeaders[index]}: ${upstreamRes.rawHeaders[index + 1]}`);
      }
      try {
        socket.write(`${lines.join('\r\n')}\r\n\r\n`);
        if (upstreamHead?.length) socket.write(upstreamHead);
        if (head?.length) upstreamSocket.write(head);
        socket.pipe(upstreamSocket);
        upstreamSocket.pipe(socket);
      } catch { /* ignore */ }
      this.upgraded.add(socket);
      this.upgraded.add(upstreamSocket);
      socket.on('close', () => {
        this.upgraded.delete(socket);
        try { upstreamSocket.destroy(); } catch { /* ignore */ }
      });
      upstreamSocket.on('close', () => {
        this.upgraded.delete(upstreamSocket);
        try { socket.destroy(); } catch { /* ignore */ }
      });
      socket.on('error', () => { try { upstreamSocket.destroy(); } catch { /* ignore */ } });
      upstreamSocket.on('error', () => { try { socket.destroy(); } catch { /* ignore */ } });
    });
    upstream.on('response', (upstreamRes) => {
      const lines = [`HTTP/1.1 ${upstreamRes.statusCode} ${upstreamRes.statusMessage || ''}`];
      for (let index = 0; index < upstreamRes.rawHeaders.length; index += 2) {
        lines.push(`${upstreamRes.rawHeaders[index]}: ${upstreamRes.rawHeaders[index + 1]}`);
      }
      try { socket.write(`${lines.join('\r\n')}\r\n\r\n`); } catch { /* ignore */ }
      upstreamRes.pipe(socket);
    });
    upstream.on('error', () => reject(502, 'Bad Gateway'));
    upstream.end();
  }

  async handleService(req, res, url) {
    const method = String(req.method || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD') {
      if (!isAllowedUiOrigin(req.headers.origin, this.port)
          || req.headers['x-torch-ui-token'] !== this.actionToken) {
        this._sendJson(res, 403, { detail: '操作令牌无效' });
        return;
      }
    }
    const pathname = url.pathname;
    if (method === 'GET' && pathname === '/__torch/service') {
      const snapshot = this.controller.snapshot();
      this._sendJson(res, 200, {
        ok: true, mode: 'plugin', service: snapshot,
        actionToken: this.actionToken, autoStart: snapshot.autoStart,
        ui: { port: this.port, origin: this.url, vaultId: this.vaultId, vaultName: this.vaultName },
        capabilities: { migrate: Boolean(this.migrator) },
      });
      return;
    }
    if (method === 'GET' && pathname === '/__torch/info') {
      this._sendJson(res, 200, { ok: true, vaultId: this.vaultId, port: this.port });
      return;
    }
    if (method === 'GET' && pathname === '/__torch/logs') {
      const max = Math.min(65536, Math.max(4096, Number(url.searchParams.get('max')) || 16384));
      const snapshot = this.controller.snapshot();
      const logPath = snapshot.failure?.logPath || this.controller.logPath || '';
      this._sendJson(res, 200, {
        ok: true, path: logPath, tail: readLogSegment(logPath, 0, max),
      });
      return;
    }
    if (method === 'GET' && pathname === '/__torch/cache') {
      const stats = await this.cache?.stats?.() || { entries: 0, bytes: 0 };
      this._sendJson(res, 200, { ok: true, ...stats });
      return;
    }
    if (method === 'POST' && pathname === '/__torch/cache/clear') {
      await this.cache?.clear?.();
      this._sendJson(res, 200, { ok: true });
      return;
    }
    if (method === 'POST' && pathname === '/__torch/start') {
      this.controller.ensure({ manual: true }).catch(() => {});
      this._sendJson(res, 202, { ok: true, service: this.controller.snapshot() });
      return;
    }
    if (method === 'POST' && pathname === '/__torch/restart') {
      this._resetConnection();
      await this.cache?.clear?.();
      this.controller.restart().catch(() => {});
      this._sendJson(res, 202, { ok: true, service: this.controller.snapshot() });
      return;
    }
    if (this.migrator && pathname === '/__torch/migrate/preview' && method === 'POST') {
      try {
        const snapshot = this.controller.snapshot();
        if (snapshot.phase === 'ready' || snapshot.phase === 'starting') {
          this._sendJson(res, 409, { detail: '本地服务正在运行，请先停止服务再整理目录。' });
          return;
        }
        const preview = await this.migrator.preview();
        this._sendJson(res, 200, { ok: true, preview });
      } catch (error) {
        this._sendJson(res, 409, { detail: String(error?.message || error) });
      }
      return;
    }
    if (this.migrator && pathname === '/__torch/migrate/apply' && method === 'POST') {
      try {
        const body = await this._readJsonBody(req);
        const snapshot = this.controller.snapshot();
        if (snapshot.phase === 'ready' || snapshot.phase === 'starting') {
          this._sendJson(res, 409, { detail: '本地服务正在运行，请先停止服务再整理目录。' });
          return;
        }
        const job = this.migrator.apply(body);
        this._resetConnection();
        await this.cache?.clear?.();
        this._sendJson(res, 202, { ok: true, job });
      } catch (error) {
        this._sendJson(res, 409, { detail: String(error?.message || error) });
      }
      return;
    }
    if (this.migrator && pathname === '/__torch/migrate/status' && method === 'GET') {
      this._sendJson(res, 200, { ok: true, job: this.migrator.status() });
      return;
    }
    this._sendJson(res, 404, { detail: '未知的本地界面操作' });
  }

  _readJsonBody(req, limit = 64 * 1024) {
    return new Promise((resolve, reject) => {
      const chunks = [];
      let size = 0;
      req.on('data', (chunk) => {
        size += chunk.length;
        if (size > limit) {
          reject(new Error('请求体过大'));
          req.destroy();
          return;
        }
        chunks.push(chunk);
      });
      req.on('end', () => {
        try {
          const text = Buffer.concat(chunks).toString('utf8') || '{}';
          resolve(JSON.parse(text));
        } catch {
          reject(new Error('请求体不是有效 JSON'));
        }
      });
      req.on('error', reject);
    });
  }
}

class VaultMigrator {
  constructor({
    pythonPath, repoRoot, vaultPath, databasePath,
    spawnProcess = spawn, now = () => Date.now(),
  } = {}) {
    this.pythonPath = pythonPath;
    this.repoRoot = repoRoot;
    this.vaultPath = vaultPath;
    this.databasePath = databasePath;
    this.spawnProcess = spawnProcess;
    this.now = now;
    this.lastPreview = null;
    this.job = { state: 'idle', startedAt: '', finishedAt: '', result: null, error: '' };
  }

  _args(mode) {
    const args = ['scripts/unnumber_vault.py', '--vault', this.vaultPath];
    if (this.databasePath) args.push('--database', this.databasePath);
    if (mode === 'apply') args.push('--apply');
    return args;
  }

  _run(args, timeoutMs) {
    return new Promise((resolve) => {
      let done = false;
      let stdout = '';
      let stderr = '';
      const finish = (value) => { if (!done) { done = true; resolve(value); } };
      let child;
      try {
        child = this.spawnProcess(this.pythonPath, args, {
          cwd: this.repoRoot,
          windowsHide: true,
          env: {
            ...process.env,
            PYTHONUTF8: '1',
            PYTHONIOENCODING: 'utf-8',
            PYTHONUNBUFFERED: '1',
          },
        });
      } catch (error) {
        finish({ code: -1, stdout: '', stderr: String(error?.message || error), timedOut: false });
        return;
      }
      const timer = setTimeout(() => {
        try { child.kill(); } catch { /* ignore */ }
        finish({ code: -1, stdout, stderr: `${stderr}\n超时`, timedOut: true });
      }, timeoutMs);
      if (typeof timer.unref === 'function') timer.unref();
      child.stdout?.on('data', (chunk) => { if (stdout.length < 200000) stdout += chunk.toString('utf8'); });
      child.stderr?.on('data', (chunk) => { if (stderr.length < 200000) stderr += chunk.toString('utf8'); });
      child.on('error', (error) => {
        clearTimeout(timer);
        finish({ code: -1, stdout, stderr: `${stderr}\n${error?.message || error}`, timedOut: false });
      });
      child.on('close', (code) => {
        clearTimeout(timer);
        finish({ code, stdout, stderr, timedOut: false });
      });
    });
  }

  async preview() {
    const result = await this._run(this._args('preview'), 60000);
    if (result.code !== 0) throw new Error(this._errorText(result, '目录预览失败'));
    const start = result.stdout.indexOf('{');
    const end = result.stdout.lastIndexOf('}');
    if (start < 0 || end <= start) throw new Error('目录预览没有返回可解析的结果');
    let data;
    try { data = JSON.parse(result.stdout.slice(start, end + 1)); }
    catch { throw new Error('目录预览结果不是有效 JSON'); }
    const preview = {
      directories: Number(data.directories) || 0,
      files: Number(data.files) || 0,
      at: new Date(this.now()).toISOString(),
    };
    this.lastPreview = preview;
    return preview;
  }

  apply({ confirm, directories, files } = {}) {
    if (confirm !== true) throw new Error('需要显式确认后才会整理目录');
    if (!this.lastPreview) throw new Error('请先运行目录预览');
    if (Date.now() - Date.parse(this.lastPreview.at) > 30 * 60 * 1000) {
      throw new Error('目录预览已过期，请重新预览');
    }
    if (Number(directories) !== this.lastPreview.directories
        || Number(files) !== this.lastPreview.files) {
      throw new Error('目录数量与最近一次预览不一致，请重新预览');
    }
    if (this.job.state === 'running') throw new Error('目录整理已经在进行中');
    this.job = {
      state: 'running', startedAt: new Date(this.now()).toISOString(),
      finishedAt: '', result: null, error: '',
    };
    this._run(this._args('apply'), 30 * 60 * 1000).then((result) => {
      if (result.code !== 0) {
        this.job = {
          ...this.job, state: 'failed', finishedAt: new Date(this.now()).toISOString(),
          error: this._errorText(result, '目录整理失败'),
        };
        return;
      }
      const manifestPath = result.stdout.trim().split(/\r?\n/).filter(Boolean).pop() || '';
      let manifest = null;
      let backup = '';
      try {
        if (manifestPath && fs.existsSync(manifestPath)) {
          manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
          backup = String(manifest.backup || '');
        }
      } catch { /* keep the raw path */ }
      this.job = {
        ...this.job, state: 'done', finishedAt: new Date(this.now()).toISOString(),
        result: {
          manifest: manifestPath, backup,
          status: manifest?.status || 'done',
          directories: this.lastPreview?.directories || 0,
          files: this.lastPreview?.files || 0,
        },
      };
    });
    return this.status();
  }

  status() {
    return {
      ...this.job,
      preview: this.lastPreview ? { ...this.lastPreview } : null,
    };
  }

  _errorText(result, prefix) {
    const detail = lastMeaningfulLine(result.stderr) || lastMeaningfulLine(result.stdout) || '';
    return `${prefix}${detail ? `：${detail}` : ''}`;
  }
}

// The native guest sits above Torch's iframe. Cut only the menu/handle/drawer
// hit areas out of its surface, keeping its actual viewport and scroll position.
function clipGuest(guest, rect, width, height, scale = 1) {
  guest.style.pointerEvents = rect.dim ? 'none' : '';
  guest.style.filter = rect.dim ? 'blur(4px) brightness(.92)' : '';
  const holes = (Array.isArray(rect.exclusions) ? rect.exclusions : []).slice(0, 12)
    .filter(r => r && [r.left, r.right, r.top, r.bottom].every(Number.isFinite))
    .map(r => ({left: Math.max(0, r.left), right: Math.min(width, r.right),
                top: Math.max(0, r.top), bottom: Math.min(height, r.bottom)}))
    .filter(r => r.right > r.left && r.bottom > r.top);
  if (!holes.length) { guest.style.clipPath = ''; return; }
  const edges = [...new Set([0, height, ...holes.flatMap(r => [r.top, r.bottom])])].sort((a, b) => a - b);
  const pieces = [];
  function strip(left, top, right, bottom) {
    if (right > left) pieces.push(`M${left / scale} ${top / scale}H${right / scale}V${bottom / scale}H${left / scale}Z`);
  }
  for (let i = 1; i < edges.length; i++) {
    const top = edges[i - 1], bottom = edges[i];
    const covered = holes.filter(r => r.top < bottom && r.bottom > top).sort((a, b) => a.left - b.left);
    let x = 0;
    for (const r of covered) { strip(x, top, r.left, bottom); x = Math.max(x, r.right); }
    strip(x, top, width, bottom);
  }
  guest.style.clipPath = pieces.length ? `path("${pieces.join(' ')}")` : 'inset(50%)';
}

class WorkbenchView extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.frame = null;
    this.offline = null;
    this.chatgptPhase = '';
    this.chatgptSeen = false;
    this.chatgptFailure = null;
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
      this.plugin.pushTheme(this.frame);
    });
    this.registerDomEvent(window, 'message', async (event) => {
      if (!event || event.source !== this.frame?.contentWindow) return;
      const data = event.data;
      if (data?.type === 'torch:dida') {
        if (this.uiOrigin() && event.origin === this.uiOrigin()) this.didaMessage(data);
        return;
      }
      if (data?.type === 'torch:chatgpt') {
        if (!this.uiOrigin() || event.origin !== this.uiOrigin()) return;
        this.chatgptMessage(data);
        return;
      }
      if (data?.type === 'torch:theme-request') { this.plugin.pushTheme(this.frame); return; }
      if (data?.type === 'torch:open-note' || data?.type === 'torch:new-note') {
        const creating = data.type === 'torch:new-note';
        const file = creating ? await this.plugin.createNote(data.path, data.content, event.origin)
          : await this.plugin.openNote(data.path, event.origin, data.edit === true);
        if (file && (creating || data.edit)) this.editedNote = { file, previousPath: data.path };
      }
      if (data?.type === 'torch:open-url') { this.plugin.openExternalUrl(data.url, event.origin); }
      if (data?.type === 'torch:open-plugin-settings') { this.plugin.openPluginSettings(); }
    });
    this.registerEvent(this.app.workspace.on('css-change', () => this.plugin.pushTheme(this.frame)));
    this.registerEvent(this.app.workspace.on('active-leaf-change', (leaf) => {
      if (leaf === this.leaf && this.editedNote) {
        const { file, previousPath } = this.editedNote;
        this.editedNote = null;
        this.frame?.contentWindow?.postMessage({ type: 'torch:note-returned', path: file.path, previousPath,
          deleted: !this.app.vault.getAbstractFileByPath(file.path) }, this.uiOrigin());
      }
      if (this.didaGuest) this.positionDida();
      this.positionChatgpt();
    }));
    try {
      await this.plugin.ensureUiServer();
    } catch (error) {
      this.showOffline(this.plugin.uiSetupMessage(error));
      return;
    }
    if (!this.frame) return;
    this.hideOffline();
    this.reload();
    this.ensureDidaGuest();
    this.ensureChatgptGuest();
  }

  reload() {
    this.didaRect = null;
    this.positionDida();
    this.chatgptBounds = null;
    if (this.chatgpt) this.chatgpt.hidden = true;
    if (!this.frame || !this.plugin.uiUrl) return;
    const scheme = document.body.classList.contains('theme-dark') ? 'dark' : 'light';
    const url = new URL(`${this.plugin.uiUrl.replace(/\/+$/, '')}/`);
    url.searchParams.set('embed', '1');
    url.searchParams.set('panel', '1');
    url.searchParams.set('host', 'obsidian');
    url.searchParams.set('theme', scheme);
    url.searchParams.set('agent', this.plugin.settings.agentProvider === 'chatgpt' ? 'chatgpt' : 'dsh');
    this.frame.setAttribute('src', url.toString());
  }

  showOffline(message) {
    this.didaRect = null;
    this.positionDida();
    this.chatgptBounds = null;
    if (this.chatgpt) this.chatgpt.hidden = true;
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

  uiOrigin() {
    try { return new URL(this.plugin.uiUrl).origin; } catch { return ''; }
  }

  closeDida() {
    this.didaGuest?.remove();
    this.didaGuest = null;
    this.didaReady = false;
    this.didaStatus = '';
    this.didaRect = null;
  }

  positionDida() {
    const guest = this.didaGuest;
    if (!guest) return;
    const rect = this.didaRect;
    guest.hidden = !this.didaReady || !rect || this.frame.hidden || this.app.workspace.activeLeaf !== this.leaf;
    if (guest.hidden) return;
    const box = this.frame.getBoundingClientRect();
    const outer = this.contentEl.getBoundingClientRect();
    const width = Math.min(rect.width, box.width - rect.x);
    const height = Math.min(rect.height, box.height - rect.y);
    if (width <= 0 || height <= 0) { guest.hidden = true; return; }
    // Keep Dida's sidebar usable in a short pane while retaining the host hit area.
    const scale = Math.max(0.85, Math.min(1, height / 740));
    clipGuest(guest, rect, width, height, scale);
    Object.assign(guest.style, {
      left: `${box.left - outer.left + rect.x}px`, top: `${box.top - outer.top + rect.y}px`,
      width: `${width / scale}px`, height: `${height / scale}px`,
      transformOrigin: 'top left', transform: `scale(${scale})`,
    });
  }

  didaMessage(data) {
    if (data.action === 'reload') {
      const rect = this.didaRect;
      this.closeDida();
      this.didaRect = rect;
      this.ensureDidaGuest();
      return;
    }
    const wasVisible = Boolean(this.didaRect);
    if (data.action !== 'bounds') return;
    const rect = data.rect;
    this.didaRect = rect && ['x', 'y', 'width', 'height'].every(key => Number.isFinite(rect[key]) && rect[key] >= 0)
      && rect.width && rect.height ? rect : null;
    if (this.didaRect) {
      this.ensureDidaGuest();
      if (!wasVisible) this.frame.contentWindow?.postMessage({ type: 'torch:dida-state', message: this.didaStatus }, this.uiOrigin());
    }
    this.positionDida();
  }

  ensureDidaGuest() {
    if (this.didaGuest) return;
    const guest = document.createElement('webview');
    this.didaGuest = guest;
    guest.hidden = true;
    guest.className = 'torch-dida-webview';
    guest.setAttribute('title', '滴答清单');
    guest.setAttribute('partition', 'persist:torch-dida');
    guest.setAttribute('webpreferences', 'contextIsolation=yes,sandbox=yes,nodeIntegration=no');
    const status = (ready, message) => {
      if (this.didaGuest !== guest) return;
      this.didaReady = ready;
      this.didaStatus = message;
      this.positionDida();
      this.frame.contentWindow?.postMessage({ type: 'torch:dida-state', message }, this.uiOrigin());
    };
    let failed = false;
    guest.addEventListener('did-start-navigation', event => {
      if (event.isMainFrame && !event.isInPlace) failed = false;
    });
    guest.addEventListener('did-navigate', event => {
      if (event.httpResponseCode >= 400) {
        failed = true;
        status(false, `滴答清单返回 HTTP ${event.httpResponseCode}，请重新载入或用浏览器打开。`);
      }
    });
    guest.addEventListener('dom-ready', () => {
      if (!failed && guest.getURL().startsWith('https://')) status(true, '');
    });
    guest.addEventListener('did-fail-load', event => {
      if (event.errorCode === -3 || event.isMainFrame === false) return;
      failed = true;
      status(false, `滴答清单加载失败（${event.errorDescription || event.errorCode}），请重新载入或用浏览器打开。`);
    });
    guest.setAttribute('src', 'https://dida365.com/webapp/');
    this.contentEl.appendChild(guest);
    status(false, '正在载入滴答清单…');
  }

  /** 把状态发回 iframe；内容区自己决定要不要把说明卡盖上来。 */
  chatgptState(state, message = '') {
    this.chatgptPhase = state;
    this.chatgptStateMessage = message;
    this.positionChatgpt();
    try {
      this.frame?.contentWindow?.postMessage(
        { type: 'torch:chatgpt-state', state, message },
        this.uiOrigin());
    } catch {}
  }

  chatgptStatus(message) {
    try { this.frame?.contentWindow?.postMessage({ type: 'torch:chatgpt-status', message }, this.uiOrigin()); } catch {}
  }

  /** ChatGPT 那个持久分区的会话（登录态就存在这里）。 */
  chatgptSession() {
    try {
      const electron = require('electron');
      return (electron.session || electron.remote?.session)?.fromPartition(CHATGPT_PARTITION);
    } catch { return null; }
  }

  /** 代理只作用于 ChatGPT 分区：宿主主会话和 iframe 都保持原样。 */
  applyChatgptProxy() {
    const session = this.chatgptSession();
    if (!session) return Promise.resolve(false);
    const rules = proxyRulesOf(this.plugin.settings?.chatgptProxy);
    try {
      const applied = rules ? session.setProxy({ proxyRules: rules }) : session.setProxy({ mode: 'system' });
      return Promise.resolve(applied).then(() => true).catch(() => false);
    } catch {
      return Promise.resolve(false);
    }
  }

  armChatgptTimer() {
    window.clearTimeout(this.chatgptTimer);
    this.chatgptTimer = window.setTimeout(() => {
      // 已经进过 ChatGPT 或正停在登录页：这不是“打不开”，别弹失败卡片。
      if (this.chatgptPhase === 'ready' || this.chatgptSeen || !this.chatgpt) return;
      this.blockChatgpt(this.chatgpt, 'ChatGPT 网页 25 秒内没有就绪。本机可能访问不了 chatgpt.com（DNS 污染或需要代理）。');
    }, 25000);
  }

  /** 载入失败或被重定向：收起 guest，把原因写回 iframe，别让一片空白顶掉提示。 */
  blockChatgpt(guest, message) {
    window.clearTimeout(this.chatgptTimer);
    if (guest) guest.hidden = true;
    this.chatgptState('blocked', message);
  }

  /**
   * 判断这一帧到底算不算“打不开”。只有「一次都没到过 ChatGPT、落地域名又不认识」
   * 才判劫持；登录跳转（Google/Apple/Microsoft 的 OAuth）和站内跳转都放行。
   */
  onChatgptDomReady(guest) {
    let url = '';
    try { url = guest.getURL ? guest.getURL() : ''; } catch { url = ''; }

    // 刚报过主框架加载失败：Chromium 的错误页也会触发 dom-ready，别把失败洗成成功。
    const failure = this.chatgptFailure;
    if (failure && failure.url === url && Date.now() - failure.at < 3000) return;

    if (isChatgptUrl(url)) {
      window.clearTimeout(this.chatgptTimer);
      this.chatgptSeen = true;
      this.chatgptState('ready', '网页已载入；请在 ChatGPT 中登录或开始对话。');
      return;
    }
    if (isSigninUrl(url)) {
      // OAuth 登录页：用户要在这里点，不能盖卡片、不能收起来。
      window.clearTimeout(this.chatgptTimer);
      this.chatgptSeen = true;
      this.chatgptState('ready', '正在登录…登录完成后会自动回到 ChatGPT。');
      return;
    }
    if (this.chatgptSeen) {
      // 已经进过 ChatGPT，之后是用户自己点出去的页面：不拦。
      this.chatgptState('ready', '');
      return;
    }
    let host = '';
    try { host = new URL(url).host; } catch { host = ''; }
    this.blockChatgpt(guest, host
      ? `网络把 chatgpt.com 带到了 ${host}：本机大概无法直连 ChatGPT（DNS 被污染或需要代理）。请在插件设置里填写代理，或用「浏览器打开」。`
      : 'ChatGPT 网页没有载入成功：本机可能访问不了 chatgpt.com（需要代理）。可以填代理后用「重新载入」，或用「浏览器打开」。');
  }

  chatgptMessage(data) {
    if (data.action === 'provider') {
      // iframe 在 Obsidian 里是第三方上下文，localStorage 会被拦；偏好放本机设置里，
      // 下次 reload() 再用 ?agent= 传回去。
      const value = data.value === 'chatgpt' ? 'chatgpt' : 'dsh';
      if (this.plugin.settings.agentProvider !== value) {
        this.plugin.settings.agentProvider = value;
        this.plugin.saveDeviceSettings();
      }
      return;
    }
    if (data.action === 'reload') {
      if (!this.chatgpt) { this.chatgptBounds = null; return; }
      this.chatgptState('loading', '正在重新载入 ChatGPT…');
      this.chatgpt.hidden = true;
      // 被劫持/重定向过的 guest 还停在别人的页面上：reload() 只会重载那个地址，
      // 所以这里先回到 chatgpt.com，确实还在 ChatGPT 上才用 reload() 保住当前会话。
      let current = '';
      try { current = this.chatgpt.getURL ? this.chatgpt.getURL() : ''; } catch { current = ''; }
      try {
        // 停在登录页时重载登录页；停在别的（被劫持的）站点才拉回 chatgpt.com。
        if (isChatgptUrl(current) || isSigninUrl(current)) this.chatgpt.reload();
        else this.chatgpt.setAttribute('src', CHATGPT_URL);
      } catch {
        this.blockChatgpt(this.chatgpt, '无法重新载入，请使用浏览器打开。');
      }
      this.armChatgptTimer();
      return;
    }
    if (data.action !== 'bounds') return;
    const wasVisible = this.chatgptBounds?.visible;
    this.chatgptBounds = data;
    if (data.visible) {
      this.ensureChatgptGuest();
      if (!wasVisible) this.chatgptState(this.chatgptPhase, this.chatgptStateMessage);
    }
    this.positionChatgpt();
  }

  ensureChatgptGuest() {
    if (this.chatgpt) return;
    // Host-owned guest browser: no preload, no page-script injection; Obsidian
    // forces sandbox + no node integration for every guest it attaches.
    const guest = document.createElement('webview');
    guest.hidden = true;
    guest.className = 'torch-chatgpt-webview';
    guest.setAttribute('partition', CHATGPT_PARTITION);
    guest.setAttribute('webpreferences', 'contextIsolation=yes,sandbox=yes,nodeIntegration=no');
    guest.setAttribute('title', 'ChatGPT 网页');
    guest.addEventListener('dom-ready', () => { if (this.chatgpt === guest) this.onChatgptDomReady(guest); });
    guest.addEventListener('did-start-loading', () => {
      if (this.chatgpt === guest && this.chatgptPhase !== 'ready') this.chatgptState('loading', '正在载入 ChatGPT…');
    });
    guest.addEventListener('did-fail-load', (event) => {
      if (this.chatgpt !== guest || event.errorCode === -3 || event.isMainFrame === false) return;
      this.chatgptFailure = { url: String(event.validatedURL || ''), at: Date.now() };
      const reason = `ChatGPT 加载失败（${event.errorDescription || event.errorCode}）。本机可能访问不了 chatgpt.com。`;
      // 已经正常进过 ChatGPT 时（比如正停在登录页）只提示，不把页面收走。
      if (this.chatgptSeen) this.chatgptState('loading', reason);
      else this.blockChatgpt(guest, reason);
    });
    guest.addEventListener('new-window', (event) => {
      event.preventDefault?.();
      try {
        const url = new URL(event.url);
        // 登录弹窗（Google/Apple 等）留在同一分区里，换掉也会丢登录态；只放行 https。
        if (url.protocol === 'https:' && isSigninUrl(url.toString())) {
          guest.setAttribute('src', url.toString());
          return;
        }
        if (url.protocol === 'https:') this.plugin.openExternalUrl(url.toString(), this.uiOrigin());
      } catch {}
    });
    this.contentEl.appendChild(guest);
    this.chatgpt = guest;
    this.chatgptFailure = null;
    this.chatgptState('loading', '正在载入 ChatGPT；登录或验证遇到限制时可使用浏览器打开。');
    if (proxyRulesOf(this.plugin.settings?.chatgptProxy)) {
      // 先让分区拿到代理再导航，否则第一个请求已经走了直连。
      this.applyChatgptProxy().finally(() => { if (this.chatgpt === guest) guest.setAttribute('src', CHATGPT_URL); });
    } else {
      guest.setAttribute('src', CHATGPT_URL);
    }
    this.armChatgptTimer();
  }

  positionChatgpt() {
    if (!this.chatgpt) return;
    const rect = this.chatgptBounds?.rect;
    if (!this.chatgptBounds?.visible || this.frame?.hidden || !rect ||
        !['x', 'y', 'width', 'height'].every(key => Number.isFinite(rect[key]))) {
      this.chatgpt.hidden = true;
      return;
    }
    const outer = this.contentEl.getBoundingClientRect(), box = this.frame.getBoundingClientRect();
    const x = Math.max(box.left, box.left + rect.x), y = Math.max(box.top, box.top + rect.y);
    const right = Math.min(box.right, box.left + rect.x + rect.width);
    const bottom = Math.min(box.bottom, box.top + rect.y + rect.height);
    clipGuest(this.chatgpt, rect, Math.max(0, right - x), Math.max(0, bottom - y));
    Object.assign(this.chatgpt.style, { left: `${x - outer.left}px`, top: `${y - outer.top}px`, width: `${Math.max(0, right - x)}px`, height: `${Math.max(0, bottom - y)}px` });
    this.chatgpt.hidden = this.chatgptPhase === 'blocked'
      || right <= x || bottom <= y || this.app.workspace.activeLeaf !== this.leaf;
  }

  async onClose() {
    this.closeDida();
    window.clearTimeout(this.chatgptTimer);
    this.frame = null;
    this.chatgpt?.remove(); this.chatgpt = null; this.chatgptBounds = null; this.chatgptPhase = '';
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
    const snapshot = this.plugin.serviceSnapshot();
    const failure = snapshot.failure;
    const stateText = snapshot.phase === 'ready'
      ? `本地服务已连接（${snapshot.url}）`
      : snapshot.phase === 'starting' ? '本地服务正在后台启动…'
        : snapshot.phase === 'failed' ? `本地服务未启动：${failure?.message || '未知原因'}`
          : '本地服务尚未启动';
    new Setting(containerEl)
      .setName('当前 Obsidian 库')
      .setDesc(`${vaultName} · 界面 ${this.plugin.uiUrl || '尚未启动'} · ${stateText}。每个库使用独立的数据目录。`)
      .addButton((button) => button.setButtonText('重新连接').onClick(async () => {
        try { await this.plugin.ensureUiServer(); } catch { /* 设置页会显示具体失败 */ }
        await this.plugin.restartBackend();
        const view = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0]?.view;
        view?.reload?.();
        new Notice('已开始重新连接本地服务。');
        this.display();
      }))
      .addButton((button) => button.setButtonText('打开日志').onClick(() => {
        const logPath = failure?.logPath;
        if (!logPath) { new Notice('还没有失败日志；启动失败后这里会出现日志路径。'); return; }
        try { require('electron').shell.openPath(logPath); } catch { new Notice(logPath); }
      }));
    const failureText = failure
      ? `最近失败：${failure.message}${failure.hint ? ` ${failure.hint}` : ''}`
      : '';
    if (failureText) {
      containerEl.createEl('p', { cls: 'setting-item-description', text: failureText });
    }
    new Setting(containerEl)
      .setName('仓库路径（仅本机）')
      .setDesc('薪火OS 的仓库目录（包含 .venv 与 backend 的那个）。只保存在这台电脑；'
        + '换设备会自动查找，不会用另一台设备的路径覆盖这里。')
      .addText((text) => text.setPlaceholder('例如 C:/Users/你/Desktop/torch-os')
        .setValue(this.plugin.settings.repoRoot)
        .onChange((value) => {
          this.plugin.settings.repoRoot = value.trim();
          this.plugin.settings.pythonPath = resolvePython(
            this.plugin.settings.repoRoot, this.plugin.settings.pythonPath);
          this.plugin.saveDeviceSettings();
        }))
      .addButton((button) => button
        .setButtonText('自动检测')
        .setTooltip('在本机常见位置查找包含 backend/app/run.py 的 torch-os 仓库')
        .onClick(() => {
          const found = findRepoRoot({
            settings: { ...this.plugin.settings, repoRoot: '' },
            vaultPath: baseVaultPath(this.app),
          });
          if (!found) {
            new Notice('没有找到 torch-os 仓库。请手动填写仓库路径；该路径只保存在本机。');
            return;
          }
          this.plugin.settings.repoRoot = found;
          this.plugin.settings.pythonPath = resolvePython(found, '');
          this.plugin.saveDeviceSettings();
          new Notice(`已找到仓库：${found}`);
          this.display();
        }));
    new Setting(containerEl)
      .setName('Python 路径（可选，仅本机）')
      .setDesc('留空时自动用 仓库路径/.venv/Scripts/python.exe（macOS/Linux 为 .venv/bin/python）。')
      .addText((text) => text.setPlaceholder('.venv/Scripts/python.exe')
        .setValue(this.plugin.settings.pythonPath)
        .onChange((value) => {
          this.plugin.settings.pythonPath = value.trim();
          this.plugin.saveDeviceSettings();
        }));
    containerEl.createEl('p', {
      cls: 'setting-item-description',
      text: '仓库路径、Python 路径和启动端口只保存在本机（'
        + localConfigDir() + '），不会随库同步。两台设备各自自动发现或手动指定一次即可。',
    });
    new Setting(containerEl)
      .setName('ChatGPT 内嵌浏览器代理（可选，仅本机）')
      .setDesc('国内网络直连不了 chatgpt.com（DNS 会被污染或被重定向到别的站）。填本机代理端口，'
        + '例如 127.0.0.1:7892 或 http://127.0.0.1:7892；代理只作用于 ChatGPT 那个网页分区，'
        + '留空表示跟随 Windows 系统代理。')
      .addText((text) => text.setPlaceholder('127.0.0.1:7892')
        .setValue(this.plugin.settings.chatgptProxy || '')
        .onChange((value) => {
          this.plugin.settings.chatgptProxy = value.trim();
          this.plugin.saveDeviceSettings();
          const view = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0]?.view;
          if (view?.applyChatgptProxy) view.applyChatgptProxy();
        }));
    new Setting(containerEl).setName('拆分存储配置（可选，仅本机）')
      .setDesc('完成迁移演练后填写 device-storage.json；旧库留空。无效配置会停止启动。')
      .addText(text => text.setValue(this.plugin.settings.storageConfigPath || '').onChange(value => {
        this.plugin.settings.storageConfigPath = value.trim(); this.plugin.saveDeviceSettings();
      }));
    new Setting(containerEl)
      .setName('本地界面缓存（仅本机）')
      .setDesc('服务未就绪时先显示上次读取的只读页面数据；缓存只放本机配置目录，不写入库、不随网盘同步。')
      .addButton((button) => button.setButtonText('清除缓存').onClick(async () => {
        await this.plugin.clearUiCache();
        new Notice('已清除本地界面缓存。');
        this.display();
      }));
    containerEl.createEl('p', {
      cls: 'setting-item-description torch-ui-cache-size',
      text: '当前缓存：读取中…',
    });
    this.plugin.uiCacheStats().then((stats) => {
      if (!this.containerEl?.isConnected) return;
      const bytes = Number(stats?.bytes) || 0;
      const size = bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB`
        : `${Math.max(0, Math.round(bytes / 1024))} KB`;
      const node = this.containerEl.querySelector('.torch-ui-cache-size');
      if (node) node.textContent = `当前缓存：${Number(stats?.entries) || 0} 条 · ${size}`;
    }).catch(() => {});
    new Setting(containerEl)
      .setName('自动拉起本地服务')
      .setDesc('打开工作台后界面立即显示，本地服务在后台启动；关闭后只影响数据加载，不阻塞导航。')
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
    const synced = (await this.loadData()) || {};
    this.settings = Object.assign({}, DEFAULT_SETTINGS, synced);
    this.instanceUrl = '';
    this.uiUrl = '';
    this.ensurePromise = null;
    this.uiServer = null;
    this.uiPromise = null;
    this.deviceKey = '';
    try { this.deviceKey = this.vaultBinding()?.id || ''; } catch { /* vault 还没就绪 */ }
    this.hydrateDeviceSettings(synced);
    this.controller = new BackendController({
      getBinding: () => this.vaultBinding(),
      getSettings: () => this.settings,
      resolveToolchain: () => this.resolveToolchain(),
      saveDeviceSettings: () => this.saveDeviceSettings(),
      onState: (state) => {
        this.instanceUrl = state.phase === 'ready' ? state.url : '';
      },
    });
    this.registerView(VIEW_TYPE, (leaf) => new WorkbenchView(leaf, this));
    this.addRibbonIcon(RIBBON_ICON, '打开 薪火OS', () => this.activateView());
    this.addCommand({ id: 'open-workbench', name: '打开工作台', callback: () => this.activateView() });
    this.addSettingTab(new TorchSettingTab(this.app, this));
  }

  onunload() {
    this.app.workspace.detachLeavesOfType(VIEW_TYPE);
    this.controller?.dispose();
    this.uiServer?.close();
    this.uiServer = null;
    this.uiUrl = '';
  }

  /**
   * 机器相关设置只认本机文件。首次运行或换设备时，同步过来的 repoRoot 只有
   * 在“这台电脑上真实存在”时才采用；否则清空并自动查找。
   */
  hydrateDeviceSettings(synced = {}) {
    const device = this.deviceKey ? readDeviceSettings(this.deviceKey) : {};
    this.settings.storageConfigPath = String(device.storageConfigPath || '');
    const hasLocalRepo = Object.prototype.hasOwnProperty.call(device, 'repoRoot');
    if (hasLocalRepo) {
      this.settings.repoRoot = String(device.repoRoot || '');
      this.settings.pythonPath = String(device.pythonPath || '');
      this.settings.instancePort = Number(device.instancePort) || 0;
      this.settings.uiPort = Number(device.uiPort) || 0;
      if (this.settings.repoRoot && !isRepoRoot(this.settings.repoRoot)) {
        this.settings.repoRoot = '';
        this.settings.pythonPath = '';
      }
    } else {
      // 旧版 data.json 或安装器留下的提示值：仅当本机存在时才迁移。
      this.settings.repoRoot = isRepoRoot(synced?.repoRoot) ? path.resolve(synced.repoRoot) : '';
      this.settings.pythonPath = resolvePython(this.settings.repoRoot, synced?.pythonPath || '');
      this.settings.instancePort = 0;
      this.settings.uiPort = 0;
    }
    if (!this.settings.repoRoot) {
      const found = findRepoRoot({
        settings: this.settings,
        vaultPath: this.vaultBinding()?.path || '',
      });
      if (found) {
        this.settings.repoRoot = found;
        this.settings.pythonPath = resolvePython(found, this.settings.pythonPath);
      }
    } else if (!this.settings.pythonPath) {
      this.settings.pythonPath = resolvePython(this.settings.repoRoot, '');
    }
    this.saveDeviceSettings();
  }

  saveDeviceSettings() {
    if (!this.deviceKey) {
      try { this.deviceKey = this.vaultBinding()?.id || ''; } catch { /* ignore */ }
    }
    if (!this.deviceKey) return '';
    return writeDeviceSettings(this.deviceKey, this.settings);
  }

  /** 共享设置写回 data.json；机器相关字段永远不会写进去。 */
  async saveSettings() {
    let current = {};
    try { current = (await this.loadData()) || {}; } catch { /* use defaults */ }
    const next = { ...current, ...sharedSettings(this.settings) };
    for (const key of DEVICE_SETTING_KEYS) delete next[key];
    await this.saveData(next);
    this.saveDeviceSettings();
  }

  /** 当前电脑实际可用的仓库与 Python；必要时自动发现并记住。 */
  resolveToolchain() {
    const toolchain = resolveToolchain({
      settings: this.settings,
      vaultPath: this.vaultBinding()?.path || '',
    });
    let changed = false;
    if (toolchain.repoRoot && toolchain.repoRoot !== this.settings.repoRoot) {
      this.settings.repoRoot = toolchain.repoRoot;
      changed = true;
    }
    if (toolchain.pythonPath && toolchain.pythonPath !== this.settings.pythonPath) {
      this.settings.pythonPath = toolchain.pythonPath;
      changed = true;
    }
    if (changed) this.saveDeviceSettings();
    return toolchain;
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

  /** 兼容设置页/旧代码：等待当前库的后端就绪。 */
  async ensureBackend({ manual = true } = {}) {
    const state = await this.controller.ensure({ manual });
    return state?.phase === 'ready';
  }

  async restartBackend() {
    return this.controller.restart();
  }

  serviceSnapshot() {
    return this.controller?.snapshot() || { phase: 'idle', failure: null };
  }

  /** 启动（或复用）本地界面服务；只碰 Node，不碰 Python。 */
  async ensureUiServer() {
    if (this.uiServer?.port) return this.uiServer;
    if (this.uiPromise) return this.uiPromise;
    this.uiPromise = (async () => {
      const binding = this.vaultBinding();
      if (!binding) throw new Error('拿不到当前 Obsidian 库路径（仅桌面版支持）');
      const toolchain = this.resolveToolchain();
      if (!toolchain.repoRoot) {
        throw new Error('还没找到 torch-os 仓库。请在插件设置里填写仓库路径（只保存在本机）');
      }
      const frontendDir = path.join(toolchain.repoRoot, 'frontend');
      if (!fs.existsSync(path.join(frontendDir, 'index.html'))) {
        throw new Error(`仓库里缺少 frontend/index.html：${frontendDir}`);
      }
      let dataDir = path.join(binding.path, '.torch-os');
      try {
        ({ dataDir } = resolveVaultDataDir(binding, this.settings));
      } catch {
        // 拆分存储配置坏了也不该挡住界面；控制器会把这个原因报成具体失败。
      }
      const cache = new UiCache({
        dir: path.join(localConfigDir(), 'ui-cache', binding.id),
      });
      await cache.init();
      let databasePath = path.join(dataDir, 'torch_os.sqlite3');
      let migrator = null;
      if (toolchain.pythonPath) {
        migrator = new VaultMigrator({
          pythonPath: toolchain.pythonPath,
          repoRoot: toolchain.repoRoot,
          vaultPath: binding.path,
          databasePath,
        });
      }
      const server = new LocalUiServer({
        frontendDir,
        vaultId: binding.id,
        vaultName: binding.name,
        controller: this.controller,
        cache,
        migrator,
        onStaticRoot: () => {
          if (this.settings.autoStart !== false) this.controller.ensure().catch(() => {});
        },
      });
      await server.listen({ preferredPort: Number(this.settings.uiPort) || 0 });
      this.uiServer = server;
      this.uiUrl = server.url;
      if (Number(this.settings.uiPort) !== server.port) {
        this.settings.uiPort = server.port;
        this.saveDeviceSettings();
      }
      return server;
    })().finally(() => { this.uiPromise = null; });
    return this.uiPromise;
  }

  uiSetupMessage(error) {
    return `本地界面还没准备好：${String(error?.message || error)}。`
      + '请在插件设置里检查“仓库路径”（需要包含 frontend 目录），然后重新打开工作台。';
  }

  openPluginSettings() {
    try {
      this.app.setting?.open?.();
      this.app.setting?.openTabById?.(this.manifest?.id || 'torch');
    } catch { /* 旧版 Obsidian 没有这个入口 */ }
  }

  uiCacheDir() {
    const binding = this.vaultBinding();
    return binding ? path.join(localConfigDir(), 'ui-cache', binding.id) : '';
  }

  async uiCacheStats() {
    const dir = this.uiCacheDir();
    if (!dir) return { entries: 0, bytes: 0 };
    try {
      const cache = new UiCache({ dir });
      await cache.init();
      return await cache.stats();
    } catch { return { entries: 0, bytes: 0 }; }
  }

  async clearUiCache() {
    const dir = this.uiCacheDir();
    if (!dir) return;
    try {
      const cache = new UiCache({ dir });
      await cache.init();
      await cache.clear();
    } catch { /* ignore */ }
  }

  /** 在新标签打开当前库的 Markdown，编辑请求使用 Obsidian 的编辑模式。 */
  async openNote(pathValue, origin, edit = false) {
    if (!this.uiUrl || !this.frameOriginMatches(origin)) return;
    if (typeof pathValue !== 'string' || !pathValue || pathValue.length > 1024) return;
    if (pathValue.includes('\\') || pathValue.startsWith('/') || pathValue.split('/').includes('..')) {
      return;
    }
    const normalized = normalizePath(pathValue);
    const isTask = (normalized.startsWith('生活运营/待办事项/')
      || normalized.startsWith('人生路/07_任务与待办/')
      || normalized.startsWith('人生路/08_任务与待办/')
      || normalized.startsWith('00-System/任务/')) && normalized.endsWith('.md');
    const isConfig = normalized === '生活运营/待办事项/_系统/任务（tasks）.md'
      || normalized === '生活运营/待办事项/_系统/待办（todos）.md'
      || normalized === '人生路/07_任务与待办/_系统/任务（tasks）.md'
      || normalized === '人生路/07_任务与待办/_系统/待办（todos）.md'
      || normalized === '人生路/08_任务与待办/_系统/任务（tasks）.md'
      || normalized === '人生路/08_任务与待办/_系统/待办（todos）.md'
      || normalized === '00-System/任务（tasks）.md'
      || normalized === '00-System/任务.md';
    const isPillar = /^(人生路|自我档案|生活运营|知识库)\//.test(normalized)
      && normalized.endsWith('.md');
    if (!isTask && !isConfig && !isPillar) return;
    const file = this.app.vault.getAbstractFileByPath(normalized);
    if (!(file instanceof TFile)) {
      new Notice(`笔记不存在：${normalized}。刷新后重试。`);
      return;
    }
    try {
      const leaf = this.app.workspace.getLeaf('tab');
      await leaf.openFile(file, edit ? { state: { mode: 'source' } } : undefined);
      try { this.app.workspace.setActiveLeaf(leaf, { focus: true }); } catch { /* 旧版本没有 options */ }
      return file;
    } catch (error) {
      new Notice(`打开文档失败：${String(error?.message || error)}`);
    }
  }

  /**
   * 在 Obsidian 里新建并打开四库笔记（2026-10-26）。
   * 四库改为只读浏览：内容在 Obsidian 里写，这里只负责建空白文件并打开。
   */
  async createNote(pathValue, contentValue, origin) {
    if (!this.uiUrl || !this.frameOriginMatches(origin)) return;
    if (typeof pathValue !== 'string' || !pathValue || pathValue.length > 1024) return;
    if (pathValue.includes('\\') || pathValue.startsWith('/') || pathValue.split('/').includes('..')) {
      return;
    }
    const normalized = normalizePath(pathValue);
    const isPillar = /^(人生路|自我档案|生活运营|知识库)\//.test(normalized)
      && normalized.endsWith('.md');
    if (!isPillar) return;
    const content = typeof contentValue === 'string' ? contentValue.slice(0, 20000) : '';
    const existing = this.app.vault.getAbstractFileByPath(normalized);
    if (existing instanceof TFile) {
      try {
        const leaf = this.app.workspace.getLeaf('tab');
        await leaf.openFile(existing, { state: { mode: 'source' } });
        try { this.app.workspace.setActiveLeaf(leaf, { focus: true }); } catch { /* 旧版本没有 options */ }
        return existing;
      } catch (error) {
        new Notice(`打开笔记失败：${String(error?.message || error)}`);
      }
      return;
    }
    const folder = normalized.split('/').slice(0, -1).join('/');
    try {
      if (folder && !this.app.vault.getAbstractFileByPath(folder)) {
        await this.app.vault.createFolder(folder);
      }
      const file = await this.app.vault.create(normalized, content);
      const leaf = this.app.workspace.getLeaf('tab');
      await leaf.openFile(file, { state: { mode: 'source' } });
      try { this.app.workspace.setActiveLeaf(leaf, { focus: true }); } catch { /* 旧版本没有 options */ }
      return file;
    } catch (error) {
      new Notice(`新建笔记失败：${String(error?.message || error)}`);
    }
  }

  /**
   * 书签栏点击（2026-10-28）：只放行 http(s)，交给系统默认浏览器打开。
   * 在 Obsidian 里塞一个内嵌网页会丢掉 Edge 的登录态，也会被 DeepSeek 的
   * 防 iframe 规则挡住，所以这里只做“把地址递给浏览器”这一件事。
   */
  openExternalUrl(value, origin) {
    if (!this.uiUrl || !this.frameOriginMatches(origin)) return;
    let url;
    try {
      url = new URL(String(value || ''));
    } catch {
      return;
    }
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return;
    const target = url.toString();
    try {
      require('electron').shell.openExternal(target);
      return;
    } catch {
      // 拿不到 electron 时退回 window.open（Obsidian 自己的外链处理会转给系统浏览器）
    }
    try { window.open(target, '_blank', 'noopener'); } catch { /* 什么也做不了 */ }
  }

  frameOriginMatches(origin) {
    try {
      return Boolean(this.uiUrl) && origin === new URL(this.uiUrl).origin;
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

// 供 node --test 覆盖纯路径发现逻辑；Obsidian 运行时不会读取这个属性。
module.exports._test = {
  WorkbenchView,
  LocalUiServer,
  BackendController,
  UiCache,
  VaultMigrator,
  probeHealth,
  readLogSegment,
  classifyBackendFailure,
  safeStaticPath,
  isAllowedUiHost,
  isAllowedUiOrigin,
  isCacheableApiPath,
  resolveVaultDataDir,
  localConfigDir,
  deviceConfigPath,
  readDeviceSettings,
  writeDeviceSettings,
  isRepoRoot,
  findPython,
  resolvePython,
  repoCandidates,
  discoverNearbyRepos,
  pickRepo,
  findRepoRoot,
  resolveToolchain,
  sharedSettings,
  UI_CSP,
};
