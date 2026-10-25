// 个人数据库管理：展示四库模板、按模板创建新的 Obsidian 库，并列出 / 打开已有数据库。
// 模板的唯一来源是后端 backend/app/vault_layout.py；这里只渲染接口返回值，不复制路径表。
import { $, state, api, escapeHtml, jsonBody, setStatus, toast, confirmAction } from "./core.js";

const INVALID_NAME = /[\\/:*?"<>|\x00-\x1f]/;
const RESERVED_NAMES = new Set([
  "CON", "PRN", "AUX", "NUL",
  ...Array.from({ length: 9 }, (_, index) => `COM${index + 1}`),
  ...Array.from({ length: 9 }, (_, index) => `LPT${index + 1}`),
]);

let templateCache = null;
let templatePromise = null;
let templateRequest = 0;
let listRequest = 0;
let listData = null;
let busy = false;

const available = () => state.config?.capabilities?.vault_admin === true;
const scoped = () => state.config?.vault_scoped === true;

/** Windows 目标路径：父文件夹 + 名称，去掉父文件夹末尾分隔符。 */
export function targetPath(parent, name) {
  const base = String(parent ?? "").trim().replace(/[\\/]+$/, "");
  const leaf = String(name ?? "").trim();
  if (!base || !leaf) return "";
  return `${base}\\${leaf}`;
}

/** 与后端 vault_admin._validate_name 对齐的本地提示；通过时返回空字符串。 */
export function vaultNameProblem(name) {
  const value = String(name ?? "").trim();
  if (!value) return "请填写名称。";
  if (value.length > 40) return "名称最多 40 个字符。";
  if (INVALID_NAME.test(value)) return '名称不能包含 \\ / : * ? " < > | 这些字符。';
  if (/[ .]$/.test(value)) return "名称不能以空格或句点结尾。";
  if (RESERVED_NAMES.has(value.toUpperCase())) return "这是 Windows 保留名，换一个名称。";
  return "";
}

/** obsidian://open?vault=...；只取文件夹名，不做任何本地路径假设。 */
export function vaultOpenUri(vault) {
  const raw = String(vault ?? "").trim().replace(/[\\/]+$/, "");
  if (!raw) return "";
  const name = raw.split(/[\\/]/).filter(Boolean).pop() || raw;
  return `obsidian://open?vault=${encodeURIComponent(name)}`;
}

/** 把 /api/vault/template 的 pillars 归一化为便于渲染的树。 */
export function templateTree(template) {
  const pillars = Array.isArray(template?.pillars) ? template.pillars : [];
  return pillars.map((pillar) => ({
    key: String(pillar?.key ?? ""),
    title: String(pillar?.title ?? pillar?.key ?? ""),
    dir: String(pillar?.dir ?? ""),
    chapters: (Array.isArray(pillar?.chapters) ? pillar.chapters : []).map((chapter) => ({
      key: String(chapter?.key ?? ""),
      title: String(chapter?.title ?? chapter?.key ?? ""),
      path: String(chapter?.path ?? ""),
      default_type: String(chapter?.default_type ?? ""),
    })),
  }));
}

/** POST /api/vault/create 的请求体；创建只写新文件夹。 */
export function createVaultPayload(parentDir, name, registerWorkspace = true) {
  return {
    parent_dir: String(parentDir ?? "").trim(),
    name: String(name ?? "").trim(),
    register_workspace: registerWorkspace !== false,
  };
}

function badge(text, tone) {
  return `<span class="vault-badge vault-badge-${tone}">${escapeHtml(text)}</span>`;
}

function layoutLabel(value) {
  const raw = String(value || "").trim();
  if (!raw) return "v1";
  const stripped = raw.replace(/^pillars-/, "");
  return stripped.startsWith("v") ? stripped : `v${stripped}`;
}

function parentDirectory(value) {
  const raw = String(value || "").trim().replace(/[\\/]+$/, "");
  const index = Math.max(raw.lastIndexOf("\\"), raw.lastIndexOf("/"));
  return index > 0 ? raw.slice(0, index) : "";
}

function sectionVisible() {
  const section = document.querySelector('[data-settings-section="obsidian"]');
  return Boolean(section && section.hidden === false);
}

function renderCurrent(template) {
  const target = $("#vault-admin-current");
  if (!target) return;
  const name = state.config?.vault_name || "当前库";
  const path = template?.current?.path || state.config?.vault_dir || "";
  target.innerHTML = `
    <div class="workspace-row">
      <div class="workspace-info">
        <p class="workspace-name"><strong>${escapeHtml(name)}</strong>${badge("当前运行", "ok")}</p>
        <p class="meta">路径：<span class="preserve-lines">${escapeHtml(path || "尚未读取到路径")}</span></p>
      </div>
      <div class="section-actions">
        <button class="text-button" type="button" data-vault-copy="${escapeHtml(path)}" ${path ? "" : "disabled"}>复制路径</button>
      </div>
    </div>`;
}

function renderTemplateLoading() {
  const details = $("#vault-template");
  if (details && available()) details.hidden = false;
  const summary = $("#vault-template-summary");
  if (summary) summary.textContent = "正在读取…";
  const body = $("#vault-template-body");
  if (body) body.innerHTML = '<p class="empty-state">正在读取模板…</p>';
}

function renderTemplateError(error) {
  const details = $("#vault-template");
  if (details && available()) details.hidden = false;
  const summary = $("#vault-template-summary");
  if (summary) summary.textContent = "读取失败";
  const body = $("#vault-template-body");
  if (body) body.innerHTML = `
    <p class="inline-error">读取模板失败：${escapeHtml(error?.message || "未知错误")}</p>
    <p class="field-help">当前运行的服务可能还没有 <code>/api/vault/template</code>。可以先填写下方的父文件夹与名称，提交时由服务端判断是否支持；本页不会改动当前库。</p>
    <div class="section-actions"><button class="text-button" type="button" data-vault-template-retry>重新读取模板</button></div>`;
}

function renderTemplate(template) {
  const details = $("#vault-template");
  if (!details) return;
  details.hidden = false;
  const tree = templateTree(template);
  const flatCount = Array.isArray(template?.chapters) ? template.chapters.length : 0;
  const chapterCount = flatCount || tree.reduce((total, pillar) => total + pillar.chapters.length, 0);
  const summary = $("#vault-template-summary");
  if (summary) summary.textContent = `${chapterCount} 个章节 / layout ${layoutLabel(template?.layout_version)}`;
  const extras = Array.isArray(template?.extra_directories) ? template.extra_directories : [];
  const seeds = Array.isArray(template?.seed_files) ? template.seed_files : [];
  const obsidian = template?.obsidian || {};
  const plugin = obsidian.plugin || {};
  const corePreset = obsidian["core-plugins.json"];
  // 后端预设既可能是启用列表（旧契约），也可能是 {插件: true/false}（Obsidian 实际格式），两种都接。
  const corePlugins = Array.isArray(corePreset)
    ? corePreset
    : corePreset && typeof corePreset === "object"
      ? Object.entries(corePreset).filter(([, enabled]) => enabled !== false).map(([id]) => id)
      : [];
  const corePluginText = corePlugins.length
    ? `${corePlugins.length} 个已启用：${corePlugins.slice(0, 6).join("、")}${corePlugins.length > 6 ? " 等" : ""}`
    : "无";
  const communityPlugins = Array.isArray(obsidian["community-plugins.json"]) ? obsidian["community-plugins.json"] : [];
  const pluginFiles = Array.isArray(plugin.files) ? plugin.files : [];
  const treeHtml = tree.map((pillar) => `
    <section class="vault-tree-group">
      <p class="vault-tree-pillar"><strong>${escapeHtml(pillar.title)}</strong><code>${escapeHtml(pillar.dir)}</code></p>
      <ul class="vault-tree-chapters">${pillar.chapters.map((chapter) => `<li><span>${escapeHtml(chapter.title)}</span><code>${escapeHtml(chapter.path)}</code></li>`).join("")}</ul>
    </section>`).join("");
  const body = $("#vault-template-body");
  if (body) body.innerHTML = `
    <div class="vault-tree">${treeHtml}</div>
    <p class="field-help"><strong>额外目录</strong>：${extras.length ? extras.map((item) => `<code>${escapeHtml(item)}</code>`).join("、") : "无"}</p>
    <p class="field-help"><strong>种子文件</strong>：${seeds.length ? seeds.map((item) => `<code>${escapeHtml(item)}</code>`).join("、") : "无"}</p>
    <p class="field-help"><strong>.obsidian 预设</strong>：app.json、appearance.json、core-plugins.json（${escapeHtml(corePluginText)}）与 community-plugins.json（${communityPlugins.length ? escapeHtml(communityPlugins.join("、")) : "无"}）；插件 ${escapeHtml(plugin.id || "torch")} ${escapeHtml(plugin.version || "版本未知")}（${pluginFiles.length ? escapeHtml(pluginFiles.join("、")) : "文件未知"}）。</p>
    <p class="field-help">模板来自 <code>${escapeHtml(template?.template_source || "backend/app/vault_layout.py")}</code>；改这里的章节表，创建新库与读取映射会一起变化，本页不复制路径表。</p>
    ${template?.note ? `<p class="field-help">${escapeHtml(template.note)}</p>` : ""}
    <div class="section-actions"><button class="text-button" type="button" data-vault-template-retry>重新读取模板</button></div>`;
  applyDefaultParent(template);
  syncCreateForm();
}

function applyDefaultParent(template) {
  const input = $("#vault-create-parent");
  if (!input || String(input.value || "").trim()) return;
  const fallback = parentDirectory(template?.current?.path);
  if (!fallback) return;
  input.value = fallback;
  updateCreatePreview();
}

function updateCreatePreview() {
  const node = $("#vault-create-preview");
  if (!node) return;
  if (!available()) return;
  const parent = String($("#vault-create-parent")?.value || "").trim();
  const name = String($("#vault-create-name")?.value || "").trim();
  const problem = vaultNameProblem(name);
  if (!parent && !name) {
    node.className = "field-help";
    node.textContent = "填写父文件夹和名称后显示目标路径。";
    return;
  }
  if (!parent) {
    node.className = "field-help inline-error";
    node.textContent = "请填写父文件夹（完整路径，例如 C:\\Users\\你\\Desktop）。";
    return;
  }
  if (problem) {
    node.className = "field-help inline-error";
    node.textContent = `${problem} 目标文件夹：${targetPath(parent, name) || "—"}`;
    return;
  }
  node.className = "field-help";
  node.textContent = `将创建：${targetPath(parent, name)}`;
}

function syncCreateForm() {
  const form = $("#vault-create-form");
  if (!form) return;
  form.hidden = !available();
  const disabled = busy || !available();
  const submit = $("#vault-create-submit");
  if (submit) {
    submit.disabled = disabled;
    submit.textContent = busy ? "创建中…" : "创建个人数据库";
  }
  ["#vault-create-parent", "#vault-create-name"].forEach((selector) => {
    const node = $(selector);
    if (node) node.disabled = disabled;
  });
  form.setAttribute?.("aria-busy", String(busy));
}

function syncControls() {
  const enabled = available();
  const card = $("#vault-admin-card");
  // 卡片本身保留：能力不足时展示一行支持说明，而不是让设置页突然少一块。
  if (card) card.hidden = false;
  const support = $("#vault-admin-support");
  if (support) {
    support.hidden = enabled;
    support.textContent = state.config
      ? "当前运行的服务尚未支持个人数据库管理。请更新并重启本地服务后再刷新页面；本页不会改动当前库。"
      : "正在检查服务能力；若未恢复，请到设置页重新检查本地服务。";
  }
  ["#vault-admin-current", "#vault-template", "#vault-database-section"].forEach((selector) => {
    const node = $(selector);
    if (node) node.hidden = !enabled;
  });
  if (!enabled) {
    const result = $("#vault-create-result");
    if (result) result.hidden = true;
  }
  syncCreateForm();
  if (enabled) updateCreatePreview();
}

async function loadTemplate({ force = false } = {}) {
  if (!available()) return null;
  if (templateCache && !force) {
    renderTemplate(templateCache);
    renderCurrent(templateCache);
    return templateCache;
  }
  if (templatePromise) return templatePromise;
  const request = ++templateRequest;
  if (!templateCache) renderTemplateLoading();
  templatePromise = (async () => {
    try {
      const payload = await api("/api/vault/template");
      if (request !== templateRequest) return null;
      templateCache = payload;
      renderTemplate(payload);
      renderCurrent(payload);
      return payload;
    } catch (error) {
      if (request !== templateRequest) return null;
      templateCache = null;
      renderCurrent(null);
      renderTemplateError(error);
      return null;
    } finally {
      if (request === templateRequest) templatePromise = null;
    }
  })();
  return templatePromise;
}

async function loadDatabaseList() {
  if (!available()) return;
  const request = ++listRequest;
  try {
    const payload = await api("/api/workspaces");
    if (request !== listRequest) return;
    listData = payload;
    renderDatabaseList(payload);
    setStatus("#vault-database-status", "");
  } catch (error) {
    if (request !== listRequest) return;
    setStatus("#vault-database-status", `读取数据库列表失败：${error.message}`, "error");
  }
}

function renderDatabaseList(payload) {
  const target = $("#vault-database-list");
  if (!target) return;
  const hint = $("#vault-database-hint");
  if (hint) {
    hint.textContent = scoped()
      ? "切换库 = 在 Obsidian 里打开另一个库；每个库的索引、备份与审计都在自己的 .torch-os 里。"
      : "切换会写入本地 .env，重启本地服务后生效；不会删除任何数据。";
  }
  const items = Array.isArray(payload?.workspaces) ? payload.workspaces : [];
  if (!items.length) {
    target.innerHTML = '<p class="empty-state">还没有登记其他数据库；创建后会出现在这里。</p>';
    return;
  }
  target.innerHTML = items.map((item) => {
    const name = String(item.name || "");
    const folder = String(item.folder || "");
    const uri = vaultOpenUri(folder || name);
    const actions = [
      `<button class="text-button" type="button" data-vault-open="${escapeHtml(uri)}">在 Obsidian 打开</button>`,
      `<button class="text-button" type="button" data-vault-copy="${escapeHtml(folder)}">复制路径</button>`,
    ];
    if (!scoped() && item.registered !== false) {
      actions.push(`<button class="text-button" type="button" data-vault-activate="${escapeHtml(name)}">${item.active ? "重新应用" : "切换到此工作区"}</button>`);
    }
    return `<div class="workspace-row">
      <div class="workspace-info">
        <p class="workspace-name"><strong>${escapeHtml(name)}</strong>${item.active ? badge("当前运行", "ok") : ""}${item.read_only ? badge("只读", "warn") : ""}${item.registered === false ? badge("未登记", "warn") : ""}</p>
        <p class="meta">文件夹：<span class="preserve-lines">${escapeHtml(folder)}</span></p>
      </div>
      <div class="section-actions">${actions.join("")}</div>
    </div>`;
  }).join("");
}

function copyText(value, label = "路径") {
  const text = String(value || "");
  if (!text) return;
  const done = () => {
    setStatus("#vault-admin-status", `已复制${label}。`, "success");
    toast(`已复制${label}`, "success");
  };
  const fail = (error) => setStatus("#vault-admin-status", `复制失败：${error?.message || "浏览器未允许剪贴板访问"}。`, "error");
  const clipboard = globalThis.navigator?.clipboard;
  if (clipboard?.writeText) clipboard.writeText(text).then(done, fail);
  else fail(null);
}

function openVault(uri) {
  const text = String(uri || "");
  if (!text) return;
  // 直接交给系统处理 obsidian://；插件目前还不处理 torch:open-vault，因此不额外 postMessage。
  window.open(text, "_blank", "noopener");
  setStatus("#vault-admin-status", "已请求 Obsidian 打开；若没有反应，请确认已安装 Obsidian 并允许浏览器打开该链接。");
}

function renderCreateResult(payload) {
  const target = $("#vault-create-result");
  if (!target) return;
  const steps = Array.isArray(payload?.next_steps) ? payload.next_steps : [];
  const path = String(payload?.path || "");
  const uri = String(payload?.obsidian_uri || vaultOpenUri(path || payload?.name || ""));
  const warning = payload?.registered === false
    ? String(payload?.warning || "文件夹已创建，但没能写进数据库列表；请把上面的路径手动添加为工作区。")
    : "";
  const pluginVersion = String(payload?.plugin?.version || "版本未知");
  target.innerHTML = `
    <p class="workspace-name"><strong>✓ 已创建 ${escapeHtml(payload?.name || "")}</strong>${payload?.registered === false ? badge("未登记", "warn") : badge("已登记", "ok")}</p>
    <p class="vault-path">${escapeHtml(path)}</p>
    <p class="meta">${Number(payload?.folders) || 0} 个文件夹 · ${Number(payload?.files) || 0} 个文件 · 插件 ${escapeHtml(pluginVersion)} · layout ${escapeHtml(layoutLabel(payload?.layout_version))}</p>
    ${warning ? `<p class="inline-warning">${escapeHtml(warning)}</p>` : ""}
    ${steps.length ? `<ol class="extract-list">${steps.map((step) => `<li>${escapeHtml(step)}</li>`).join("")}</ol>` : ""}
    <div class="section-actions">
      <button class="text-button" type="button" data-vault-copy="${escapeHtml(path)}" ${path ? "" : "disabled"}>复制路径</button>
      <button class="text-button" type="button" data-vault-open="${escapeHtml(uri)}" ${uri ? "" : "disabled"}>在 Obsidian 打开</button>
    </div>`;
  target.hidden = false;
}

async function submitCreate(event) {
  event.preventDefault();
  if (busy || !available()) return;
  const parent = String($("#vault-create-parent")?.value || "").trim();
  const name = String($("#vault-create-name")?.value || "").trim();
  if (!parent) {
    setStatus("#vault-admin-status", "请填写父文件夹（完整路径，例如 C:\\Users\\你\\Desktop）。", "error");
    $("#vault-create-parent")?.focus?.();
    return;
  }
  const problem = vaultNameProblem(name);
  if (problem) {
    setStatus("#vault-admin-status", problem, "error");
    $("#vault-create-name")?.focus?.();
    return;
  }
  const target = targetPath(parent, name);
  if (!target) {
    setStatus("#vault-admin-status", "目标路径不完整，请检查父文件夹和名称。", "error");
    return;
  }
  const accepted = await confirmAction({
    title: `创建个人数据库「${name}」？`,
    message: `目标：${target}\n只会在父文件夹里新建这一个文件夹（四库目录、.obsidian 预设和 torch 插件），不会改动当前库。`,
    confirmLabel: "创建新文件夹",
  });
  if (!accepted) {
    setStatus("#vault-admin-status", "已取消；没有写入任何文件。");
    return;
  }
  busy = true;
  syncCreateForm();
  setStatus("#vault-admin-status", "正在创建数据库…");
  try {
    const payload = await api("/api/vault/create", jsonBody("POST", createVaultPayload(parent, name, true)));
    renderCreateResult(payload);
    setStatus("#vault-admin-status", `已创建「${payload?.name || name}」。`, "success");
    toast("个人数据库已创建", "success");
    const nameInput = $("#vault-create-name");
    if (nameInput) nameInput.value = "";
    updateCreatePreview();
    // 模板与数据库列表都重新读取：创建后新条目应立即出现在列表里。
    templateCache = null;
    await Promise.all([loadTemplate({ force: true }), loadDatabaseList()]);
  } catch (error) {
    const suffix = error.status === 409 ? "（目标已存在或被占用）" : error.status === 422 ? "（输入不符合要求）" : "";
    setStatus("#vault-admin-status", `创建失败${suffix}：${error.message}`, "error");
  } finally {
    busy = false;
    syncCreateForm();
  }
}

async function activateDatabase(name) {
  if (busy || !available() || scoped()) return;
  const entry = (listData?.workspaces || []).find((item) => item.name === name);
  if (!entry) return;
  const accepted = await confirmAction({
    title: `切换到「${entry.name}」？`,
    message: "将把 DATA_DIR、VAULT_DIR 与读写模式写入本地 .env，重启本地服务后生效。不会删除当前库的任何文件。",
    confirmLabel: "写入本地 .env",
  });
  if (!accepted) return;
  busy = true;
  syncCreateForm();
  try {
    const payload = await api("/api/workspaces/activate", jsonBody("POST", { name: entry.name }));
    setStatus("#vault-database-status", payload?.note || "已写入本地 .env；重启服务后生效。", "success");
    toast("已保存切换；重启服务后生效");
  } catch (error) {
    setStatus("#vault-database-status", `切换失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncCreateForm();
  }
}

async function onCardClick(event) {
  const open = event.target.closest("[data-vault-open]");
  if (open) {
    openVault(open.dataset.vaultOpen);
    return;
  }
  const copy = event.target.closest("[data-vault-copy]");
  if (copy) {
    copyText(copy.dataset.vaultCopy);
    return;
  }
  const activate = event.target.closest("[data-vault-activate]");
  if (activate) return activateDatabase(activate.dataset.vaultActivate);
  const retry = event.target.closest("[data-vault-template-retry]");
  if (retry) return loadTemplate({ force: true });
}

/** 设置页可见时懒加载模板与数据库列表；模板命中缓存时只重绘。 */
export async function loadVaultAdmin({ force = false } = {}) {
  if (!available()) {
    syncControls();
    return;
  }
  await Promise.all([loadTemplate({ force }), loadDatabaseList()]);
}

export function initVaultAdmin() {
  $("#vault-create-form")?.addEventListener("submit", submitCreate);
  $("#vault-create-parent")?.addEventListener("input", updateCreatePreview);
  $("#vault-create-name")?.addEventListener("input", updateCreatePreview);
  $("#vault-admin-card")?.addEventListener("click", onCardClick);
  document.addEventListener("click", (event) => {
    if (event.target.closest('[data-settings-nav="obsidian"]') && sectionVisible()) return loadVaultAdmin();
  });
  document.addEventListener("config-changed", () => {
    syncControls();
    if (state.view === "settings" && sectionVisible()) loadVaultAdmin();
  });
  syncControls();
}
