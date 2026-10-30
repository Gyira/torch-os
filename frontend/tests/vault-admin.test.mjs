import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the personal-database settings card. Pure helpers load with a
// bare core stub (no DOM); rendering / request tests use a small DOM double.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const moduleFile = path.join(frontend, "vault-admin.js");
const coreExports = ["$", "state", "api", "escapeHtml", "jsonBody", "setStatus", "toast", "confirmAction"];
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[character]));

async function loadWithCore(coreFactory, contextValues = {}) {
  const context = vm.createContext(contextValues);
  const core = new vm.SyntheticModule(coreExports, coreFactory, { context });
  const module = new vm.SourceTextModule(await readFile(moduleFile, "utf8"), { context, identifier: moduleFile });
  await module.link((specifier, referring) => {
    const resolved = path.resolve(path.dirname(referring.identifier), specifier);
    if (resolved === path.join(frontend, "core.js")) return core;
    throw new Error(`unexpected import ${specifier}`);
  });
  await module.evaluate();
  return module.namespace;
}

// Pure helpers: no document / window at all.
const helpers = await loadWithCore(function () {
  this.setExport("$", () => null);
  this.setExport("state", { view: "settings", config: null });
  this.setExport("api", async () => ({}));
  this.setExport("escapeHtml", escapeHtml);
  this.setExport("jsonBody", (method, data) => ({ method, body: JSON.stringify(data) }));
  this.setExport("setStatus", () => {});
  this.setExport("toast", () => {});
  this.setExport("confirmAction", async () => true);
});

class NodeDouble {
  constructor(id = "") {
    this.id = id;
    this.listeners = new Map();
    this.dataset = {};
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.textContent = "";
    this.innerHTML = "";
    this.className = "";
    this.attributes = {};
    this.open = false;
    this.focused = false;
  }
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(callback);
  }
  async emit(type, values = {}) {
    const event = { target: this, currentTarget: this, preventDefault() {}, ...values };
    for (const callback of this.listeners.get(type) || []) await callback(event);
    return event;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  focus() { this.focused = true; }
  querySelectorAll() { return []; }
}

function templateFixture() {
  const pillars = [
    { key: "life", title: "人生路", dir: "00_人生路", count: 16 },
    { key: "self", title: "自我档案", dir: "01_自我档案", count: 7 },
    { key: "ops", title: "生活运营", dir: "02_生活运营", count: 4 },
    { key: "knowledge", title: "知识库", dir: "03_知识库", count: 8 },
  ];
  const built = pillars.map((pillar) => ({
    ...pillar,
    chapters: Array.from({ length: pillar.count }, (_, index) => ({
      key: `${pillar.key}_${index + 1}`,
      title: `${pillar.title}章节${index + 1}`,
      path: `${pillar.dir}/第${index + 1}章`,
      default_type: "note",
    })),
  }));
  return {
    layout_version: "pillars-v1",
    pillars: built,
    chapters: built.flatMap((pillar) => pillar.chapters.map((chapter) => ({ ...chapter, pillar: pillar.key }))),
    directories: built.flatMap((pillar) => pillar.chapters.map((chapter) => chapter.path)),
    extra_directories: [
      "00_人生路/07_任务与待办/收集箱", "00_人生路/07_任务与待办/已完成",
      "00_人生路/07_任务与待办/回收站", "00_人生路/07_任务与待办/_系统",
      ".obsidian/plugins/torch", ".torch-os",
    ],
    seed_files: ["01_自我档案/01_内核与三观/身份（identity）.md"],
    manifest_file: ".torch-os/vault-layout.json",
    obsidian: {
      "app.json": {},
      "appearance.json": {},
      "core-plugins.json": { "file-explorer": true, "global-search": true, "tag-pane": true },
      "community-plugins.json": ["torch"],
      plugin: { id: "torch", version: "0.99.2", files: ["main.js", "manifest.json", "styles.css", "data.json"] },
    },
    current: {
      path: "C:\\Users\\你\\Desktop\\个人数据库",
      runtime_label: "personal",
      vault_scoped: true,
    },
    template_source: "backend/app/vault_layout.py（唯一来源；改模板即改这里）",
    note: "新库的章节目录与四库映射一一对应；创建只写新文件夹，不碰当前库。",
  };
}

const baseWorkspaces = {
  workspaces: [
    {
      name: "个人数据库", folder: "C:\\Users\\你\\Desktop\\个人数据库",
      data: "C:\\Users\\你\\Desktop\\个人数据库\\.torch-os",
      read_only: false, active: true, registered: true,
    },
    {
      name: "家人的库", folder: "D:\\Obsidian\\Family",
      data: "D:\\Obsidian\\Family\\.torch-os",
      read_only: true, active: false, registered: true,
    },
  ],
  active: "个人数据库",
  restart_required_for_changes: true,
  note: "换工作区就是换文件夹；重启服务后生效。",
};

const createdPayload = {
  created: true,
  name: "第二大脑",
  path: "C:\\Users\\你\\Desktop\\第二大脑",
  folders: 40,
  files: 10,
  layout_version: "pillars-v1",
  plugin: { version: "0.99.2", path: "C:\\Users\\你\\Desktop\\第二大脑\\.obsidian\\plugins\\torch" },
  registered: true,
  warning: "",
  obsidian_uri: "obsidian://open?vault=%E7%AC%AC%E4%BA%8C%E5%A4%A7%E8%84%91",
  next_steps: ["打开 Obsidian →「打开文件夹作为库」，选中上面的路径。", "在插件列表里启用「薪火OS」。"],
  note: "创建不会改动当前库。",
};

function clickOn(selector, dataset) {
  return { target: { closest: (wanted) => (wanted === selector ? { dataset } : null) } };
}

async function harness({
  capabilities = { vault_admin: true },
  config = {},
  template = templateFixture(),
  templateError = null,
  workspaces = baseWorkspaces,
  workspacesAfterCreate = null,
  createResult = createdPayload,
  createError = null,
  activateResult = { name: "家人的库", note: "已写入本地 .env；重启服务后切换到该工作区。" },
  confirmAnswer = true,
} = {}) {
  const nodes = new Map();
  const requests = [];
  const confirmations = [];
  const toasts = [];
  const opened = [];
  const copied = [];
  const documentListeners = new Map();
  let created = false;
  const getNode = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, new NodeDouble(selector));
    return nodes.get(selector);
  };
  const document = {
    activeElement: null,
    querySelector: getNode,
    querySelectorAll: () => [],
    addEventListener(type, callback) {
      if (!documentListeners.has(type)) documentListeners.set(type, []);
      documentListeners.get(type).push(callback);
    },
    dispatchEvent(event) {
      for (const callback of documentListeners.get(event?.type) || []) callback(event);
      return true;
    },
  };
  const window = { open: (...args) => opened.push(args) };
  const navigator = { clipboard: { writeText: async (text) => { copied.push(text); } } };
  const state = {
    view: "settings",
    config: {
      capabilities,
      vault_name: "个人数据库",
      vault_dir: "C:\\Users\\你\\Desktop\\个人数据库",
      vault_scoped: true,
      ...config,
    },
  };
  const namespace = await loadWithCore(function () {
    this.setExport("$", getNode);
    this.setExport("state", state);
    this.setExport("api", async (url, options = {}) => {
      requests.push({ url, options });
      if (url === "/api/vault/template") {
        if (templateError) throw templateError;
        return template;
      }
      if (url === "/api/workspaces" && (options.method || "GET") === "GET") {
        return created && workspacesAfterCreate ? workspacesAfterCreate : workspaces;
      }
      if (url === "/api/vault/create") {
        if (createError) throw createError;
        created = true;
        return createResult;
      }
      if (url === "/api/workspaces/activate") return activateResult;
      throw new Error(`unexpected ${url}`);
    });
    this.setExport("escapeHtml", escapeHtml);
    this.setExport("jsonBody", (method, data) => ({ method, body: JSON.stringify(data) }));
    this.setExport("setStatus", (selector, message, tone = "neutral") => {
      const node = getNode(selector);
      node.textContent = message;
      node.tone = tone;
    });
    this.setExport("toast", (message) => toasts.push(message));
    this.setExport("confirmAction", async (options) => { confirmations.push(options); return confirmAnswer; });
  }, { document, window, navigator });
  // Mirror the hidden attributes in index.html so "hidden until success" is tested.
  ["#vault-admin-card", "#vault-template", "#vault-create-form", "#vault-create-result",
   "#vault-admin-support", "#vault-database-section"].forEach((selector) => {
    getNode(selector).hidden = true;
  });
  return { namespace, requests, confirmations, toasts, opened, copied, getNode, state, document };
}

test("targetPath joins Windows parent and name without duplicate separators", () => {
  assert.equal(helpers.targetPath("C:\\Users\\你\\Desktop", "第二大脑"), "C:\\Users\\你\\Desktop\\第二大脑");
  assert.equal(helpers.targetPath("C:\\Users\\你\\Desktop\\", "第二大脑"), "C:\\Users\\你\\Desktop\\第二大脑");
  assert.equal(helpers.targetPath("C:/Users/你/Desktop/", "第二大脑"), "C:/Users/你/Desktop\\第二大脑");
  assert.equal(helpers.targetPath("  C:\\Users\\你\\Desktop  ", " 第二大脑 "), "C:\\Users\\你\\Desktop\\第二大脑");
  assert.equal(helpers.targetPath("C:\\", "第二大脑"), "C:\\第二大脑");
  assert.equal(helpers.targetPath("", "第二大脑"), "");
  assert.equal(helpers.targetPath("C:\\Users\\你\\Desktop", ""), "");
});

test("vaultNameProblem mirrors the backend validation hints", () => {
  assert.equal(helpers.vaultNameProblem("我的数据库"), "");
  assert.equal(helpers.vaultNameProblem(" 我的数据库 "), "");
  assert.match(helpers.vaultNameProblem(""), /请填写名称/);
  assert.match(helpers.vaultNameProblem("   "), /请填写名称/);
  assert.match(helpers.vaultNameProblem("a".repeat(41)), /40/);
  for (const invalid of ["a/b", "a\\b", "a:b", "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b"]) {
    assert.match(helpers.vaultNameProblem(invalid), /不能包含/);
  }
  assert.match(helpers.vaultNameProblem("名字."), /空格或句点/);
  assert.match(helpers.vaultNameProblem("CON"), /保留名/);
});

test("vaultOpenUri encodes the vault folder name", () => {
  assert.equal(helpers.vaultOpenUri("C:\\Users\\你\\Desktop\\我的 库"), `obsidian://open?vault=${encodeURIComponent("我的 库")}`);
  assert.equal(helpers.vaultOpenUri("D:\\Obsidian\\Family\\"), "obsidian://open?vault=Family");
  assert.equal(helpers.vaultOpenUri("我的库"), `obsidian://open?vault=${encodeURIComponent("我的库")}`);
  assert.equal(helpers.vaultOpenUri("A&B#1"), `obsidian://open?vault=${encodeURIComponent("A&B#1")}`);
  assert.equal(helpers.vaultOpenUri(""), "");
});

test("templateTree flattens four pillars and thirty-five chapters", () => {
  const tree = helpers.templateTree(templateFixture());
  assert.equal(tree.length, 4);
  assert.deepEqual(JSON.parse(JSON.stringify(tree.map((pillar) => pillar.chapters.length))), [16, 7, 4, 8]);
  assert.equal(tree.reduce((total, pillar) => total + pillar.chapters.length, 0), 35);
  assert.deepEqual(JSON.parse(JSON.stringify(tree.map((pillar) => pillar.title))), ["人生路", "自我档案", "生活运营", "知识库"]);
  assert.equal(tree[0].chapters[0].path, "00_人生路/第1章");
  assert.equal(tree[3].chapters[7].title, "知识库章节8");
  assert.equal(helpers.templateTree(null).length, 0);
  assert.deepEqual(JSON.parse(JSON.stringify(helpers.templateTree({ pillars: [{ title: "只有标题" }] })[0])), {
    key: "", title: "只有标题", dir: "", chapters: [],
  });
});

test("createVaultPayload trims its input and always targets a new folder", () => {
  const payload = helpers.createVaultPayload(" C:\\Users\\你\\Desktop ", " 第二大脑 ", true);
  assert.deepEqual(JSON.parse(JSON.stringify(payload)), {
    parent_dir: "C:\\Users\\你\\Desktop",
    name: "第二大脑",
    register_workspace: true,
  });
  const optOut = helpers.createVaultPayload("C:\\Temp", "库", false);
  assert.deepEqual(JSON.parse(JSON.stringify(optOut)), {
    parent_dir: "C:\\Temp",
    name: "库",
    register_workspace: false,
  });
});

test("loading renders the current database and the backend template tree", async () => {
  const h = await harness();
  h.namespace.initVaultAdmin();
  await h.namespace.loadVaultAdmin();
  assert.deepEqual(h.requests.map((request) => request.url), ["/api/vault/template", "/api/workspaces"]);

  const current = h.getNode("#vault-admin-current").innerHTML;
  assert.match(current, /个人数据库/);
  assert.match(current, /当前运行/);
  assert.match(current, /C:\\Users\\你\\Desktop\\个人数据库/);
  assert.match(current, /复制路径/);

  assert.equal(h.getNode("#vault-template").hidden, false);
  assert.equal(h.getNode("#vault-template-summary").textContent, "35 个章节 / layout v1");
  const templateHtml = h.getNode("#vault-template-body").innerHTML;
  assert.equal((templateHtml.match(/vault-tree-group/g) || []).length, 4);
  assert.match(templateHtml, /01_自我档案/);
  assert.match(templateHtml, /身份（identity）\.md/);
  assert.match(templateHtml, /3 个已启用：file-explorer、global-search、tag-pane/);
  assert.match(templateHtml, /0\.99\.2/);
  assert.match(templateHtml, /backend\/app\/vault_layout\.py/);

  assert.equal(h.getNode("#vault-create-form").hidden, false);
  assert.equal(h.getNode("#vault-create-parent").value, "C:\\Users\\你\\Desktop");
  assert.match(h.getNode("#vault-create-preview").textContent, /请填写名称/);
});

test("an invalid name is rejected locally and never posts", async () => {
  const h = await harness();
  h.namespace.initVaultAdmin();
  await h.namespace.loadVaultAdmin();
  h.getNode("#vault-create-name").value = "a/b";
  await h.getNode("#vault-create-name").emit("input");
  assert.match(h.getNode("#vault-create-preview").textContent, /不能包含/);
  assert.match(h.getNode("#vault-create-preview").className, /inline-error/);

  await h.getNode("#vault-create-form").emit("submit");
  assert.equal(h.requests.some((request) => request.url === "/api/vault/create"), false);
  assert.match(h.getNode("#vault-admin-status").textContent, /不能包含/);
  assert.equal(h.getNode("#vault-create-name").focused, true);
  assert.equal(h.confirmations.length, 0);
});

test("creating confirms the target path, posts the payload and shows the result", async () => {
  const afterCreate = {
    ...baseWorkspaces,
    workspaces: [
      ...baseWorkspaces.workspaces,
      {
        name: "第二大脑", folder: "C:\\Users\\你\\Desktop\\第二大脑",
        data: "C:\\Users\\你\\Desktop\\第二大脑\\.torch-os",
        read_only: false, active: false, registered: true,
      },
    ],
  };
  const h = await harness({ workspacesAfterCreate: afterCreate });
  h.namespace.initVaultAdmin();
  await h.namespace.loadVaultAdmin();
  h.getNode("#vault-create-name").value = "第二大脑";
  await h.getNode("#vault-create-name").emit("input");
  assert.match(h.getNode("#vault-create-preview").textContent, /C:\\Users\\你\\Desktop\\第二大脑/);

  await h.getNode("#vault-create-form").emit("submit");
  const createRequest = h.requests.find((request) => request.url === "/api/vault/create");
  assert.ok(createRequest);
  assert.equal(createRequest.options.method, "POST");
  assert.deepEqual(JSON.parse(createRequest.options.body), {
    parent_dir: "C:\\Users\\你\\Desktop",
    name: "第二大脑",
    register_workspace: true,
  });
  assert.equal(h.confirmations.length, 1);
  assert.match(h.confirmations[0].message, /C:\\Users\\你\\Desktop\\第二大脑/);
  assert.match(h.confirmations[0].message, /只会在父文件夹里新建这一个文件夹/);

  const result = h.getNode("#vault-create-result");
  assert.equal(result.hidden, false);
  assert.match(result.innerHTML, /已创建 第二大脑/);
  assert.match(result.innerHTML, /40 个文件夹/);
  assert.match(result.innerHTML, /10 个文件/);
  assert.match(result.innerHTML, /0\.99\.2/);
  assert.match(result.innerHTML, /打开 Obsidian/);
  assert.match(result.innerHTML, /打开 Obsidian →「打开文件夹作为库」/);
  assert.match(h.getNode("#vault-admin-status").textContent, /已创建/);
  assert.match(h.getNode("#vault-database-list").innerHTML, /第二大脑/);
  assert.ok(h.requests.filter((request) => request.url === "/api/workspaces").length >= 2);
  assert.ok(h.requests.filter((request) => request.url === "/api/vault/template").length >= 2);
});

test("a server 409 keeps the detail in the create status line", async () => {
  const conflict = Object.assign(new Error("这个文件夹里已经有东西了；换个名字或换个空文件夹"), { status: 409 });
  const h = await harness({ createError: conflict });
  h.namespace.initVaultAdmin();
  await h.namespace.loadVaultAdmin();
  h.getNode("#vault-create-name").value = "第二大脑";
  await h.getNode("#vault-create-name").emit("input");
  await h.getNode("#vault-create-form").emit("submit");
  assert.match(h.getNode("#vault-admin-status").textContent, /创建失败（目标已存在或被占用）/);
  assert.match(h.getNode("#vault-admin-status").textContent, /这个文件夹里已经有东西了/);
  assert.equal(h.getNode("#vault-create-result").hidden, true);
  assert.equal(h.getNode("#vault-create-submit").disabled, false);
});

test("a cancelled confirmation writes nothing", async () => {
  const h = await harness({ confirmAnswer: false });
  h.namespace.initVaultAdmin();
  await h.namespace.loadVaultAdmin();
  h.getNode("#vault-create-name").value = "第二大脑";
  await h.getNode("#vault-create-name").emit("input");
  await h.getNode("#vault-create-form").emit("submit");
  assert.equal(h.requests.some((request) => request.url === "/api/vault/create"), false);
  assert.match(h.getNode("#vault-admin-status").textContent, /已取消/);
});

test("scoped mode opens vaults in Obsidian but has no workspace switcher", async () => {
  const h = await harness({ config: { vault_scoped: true } });
  h.namespace.initVaultAdmin();
  await h.namespace.loadVaultAdmin();
  const list = h.getNode("#vault-database-list").innerHTML;
  assert.match(list, /当前运行/);
  assert.match(list, /在 Obsidian 打开/);
  assert.match(list, /复制路径/);
  assert.equal(list.includes("切换到此工作区"), false);
  assert.equal(list.includes("重新应用"), false);
  assert.match(list, /obsidian:\/\/open\?vault=Family/);
  assert.match(h.getNode("#vault-database-hint").textContent, /切换库 = 在 Obsidian 里打开另一个库/);
});

test("browser mode can still switch workspaces and reports the restart note", async () => {
  const h = await harness({ config: { vault_scoped: false } });
  h.namespace.initVaultAdmin();
  await h.namespace.loadVaultAdmin();
  assert.match(h.getNode("#vault-database-list").innerHTML, /切换到此工作区/);
  await h.getNode("#vault-admin-card").emit("click", clickOn("[data-vault-activate]", { vaultActivate: "家人的库" }));
  const activateRequest = h.requests.find((request) => request.url === "/api/workspaces/activate");
  assert.ok(activateRequest);
  assert.deepEqual(JSON.parse(activateRequest.options.body), { name: "家人的库" });
  assert.match(h.getNode("#vault-database-status").textContent, /重启服务后/);
  assert.match(h.getNode("#vault-database-hint").textContent, /重启本地服务后生效/);
});

test("copy and open actions use the real path and obsidian URI", async () => {
  const h = await harness();
  h.namespace.initVaultAdmin();
  await h.namespace.loadVaultAdmin();
  await h.getNode("#vault-admin-card").emit("click", clickOn("[data-vault-copy]", { vaultCopy: "C:\\Users\\你\\Desktop\\个人数据库" }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(h.copied, ["C:\\Users\\你\\Desktop\\个人数据库"]);
  assert.match(h.getNode("#vault-admin-status").textContent, /已复制路径/);
  await h.getNode("#vault-admin-card").emit("click", clickOn("[data-vault-open]", { vaultOpen: "obsidian://open?vault=Family" }));
  assert.deepEqual(h.opened[0], ["obsidian://open?vault=Family", "_blank", "noopener"]);
});

test("without vault_admin the card explains the missing capability and stays inert", async () => {
  const h = await harness({ capabilities: {} });
  h.namespace.initVaultAdmin();
  await h.namespace.loadVaultAdmin();
  assert.equal(h.requests.length, 0);
  assert.equal(h.getNode("#vault-admin-card").hidden, false);
  assert.equal(h.getNode("#vault-admin-support").hidden, false);
  assert.match(h.getNode("#vault-admin-support").textContent, /尚未支持个人数据库管理/);
  assert.equal(h.getNode("#vault-create-form").hidden, true);
  assert.equal(h.getNode("#vault-template").hidden, true);
  assert.equal(h.getNode("#vault-admin-current").hidden, true);
  assert.equal(h.getNode("#vault-database-section").hidden, true);
});

test("a missing template endpoint shows an error state but keeps the form usable", async () => {
  const failure = Object.assign(new Error("请求失败（404），请稍后重试。"), { status: 404 });
  const h = await harness({ templateError: failure });
  h.namespace.initVaultAdmin();
  await h.namespace.loadVaultAdmin();
  assert.equal(h.getNode("#vault-template").hidden, false);
  assert.equal(h.getNode("#vault-template-summary").textContent, "读取失败");
  assert.match(h.getNode("#vault-template-body").innerHTML, /读取模板失败/);
  assert.match(h.getNode("#vault-template-body").innerHTML, /重新读取模板/);
  assert.equal(h.getNode("#vault-create-form").hidden, false);
  assert.equal(h.getNode("#vault-create-parent").value, "");
  assert.match(h.getNode("#vault-admin-current").innerHTML, /C:\\Users\\你\\Desktop\\个人数据库/);
});

test("the obsidian preset accepts both object and list core-plugins.json forms", async () => {
  const template = templateFixture();
  template.obsidian["core-plugins.json"] = ["file-explorer", "tag-pane"];
  const h = await harness({ template });
  h.namespace.initVaultAdmin();
  await h.namespace.loadVaultAdmin();
  const html = h.getNode("#vault-template-body").innerHTML;
  assert.match(html, /core-plugins\.json（2 个已启用：file-explorer、tag-pane）/);
  assert.equal(html.includes("global-search"), false);
});

