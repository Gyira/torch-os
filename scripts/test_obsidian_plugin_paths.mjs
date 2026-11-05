// 插件路径发现回归：本机仓库/Python 设置不能随库同步。
// 用假的 obsidian 模块加载 main.js，只跑纯路径逻辑，不启动 Obsidian。
import test from "node:test";
import assert from "node:assert/strict";
import Module from "node:module";
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const stub = {
  Plugin: class {},
  ItemView: class {},
  Notice: class { constructor(message) { this.message = message; } },
  PluginSettingTab: class {},
  Setting: class {},
  requestUrl: async () => ({ status: 500 }),
  TFile: class {},
  normalizePath: (value) => value,
};
const originalLoad = Module._load;
Module._load = function patchedLoad(request, parent, isMain) {
  if (request === "obsidian") return stub;
  return originalLoad.call(this, request, parent, isMain);
};
const TorchPlugin = require("../obsidian-plugin/main.js");
Module._load = originalLoad;
const helpers = TorchPlugin._test;

function makeRepo(root) {
  fs.mkdirSync(path.join(root, "backend", "app"), { recursive: true });
  fs.writeFileSync(path.join(root, "backend", "app", "run.py"), "# fake\n", "utf8");
  const python = process.platform === "win32"
    ? path.join(root, ".venv", "Scripts", "python.exe")
    : path.join(root, ".venv", "bin", "python");
  fs.mkdirSync(path.dirname(python), { recursive: true });
  fs.writeFileSync(python, "", "utf8");
  return python;
}

function tempRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "torch-plugin-paths-"));
}

test("仓库识别与 Python 发现只认真实存在的目录和 venv", () => {
  const temp = tempRoot();
  try {
    const repo = path.join(temp, "torch-os");
    const python = makeRepo(repo);
    assert.equal(helpers.isRepoRoot(repo), true);
    assert.equal(helpers.isRepoRoot(path.join(temp, "missing")), false);
    assert.equal(helpers.findPython(repo), python);
    assert.equal(helpers.resolvePython(repo, ""), python);
    const toolchain = helpers.resolveToolchain({ settings: { repoRoot: repo } });
    assert.equal(toolchain.repoRoot, repo);
    assert.equal(toolchain.pythonPath, python);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test("同步过来的旧路径在本机不存在时会被忽略并自动找到本机仓库", () => {
  const temp = tempRoot();
  try {
    const vault = path.join(temp, "vault");
    fs.mkdirSync(vault, { recursive: true });
    const repo = path.join(temp, "torch-os");
    makeRepo(repo);
    const toolchain = helpers.resolveToolchain({
      settings: { repoRoot: path.join(temp, "another-device", "Torchos") },
      vaultPath: vault,
      extraCandidates: [repo],
    });
    assert.equal(toolchain.repoRoot, repo);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test("自动发现优先选择带 .venv 的仓库，跳过没有 Python 的旧副本", () => {
  const temp = tempRoot();
  try {
    const bare = path.join(temp, "Torchos");
    fs.mkdirSync(path.join(bare, "backend", "app"), { recursive: true });
    fs.writeFileSync(path.join(bare, "backend", "app", "run.py"), "# fake\n", "utf8");
    const ready = path.join(temp, "torch-os");
    makeRepo(ready);
    assert.equal(helpers.pickRepo([bare, ready]), ready);
    assert.equal(helpers.pickRepo([bare]), bare);
    assert.equal(helpers.pickRepo([path.join(temp, "missing")]), "");
    const toolchain = helpers.resolveToolchain({
      settings: { repoRoot: bare },
      extraCandidates: [ready],
    });
    assert.equal(toolchain.repoRoot, ready);
    assert.ok(toolchain.pythonPath);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test("插件加载时把旧同步路径迁移到本机，saveSettings 不再写机器字段", async () => {
  const temp = tempRoot();
  const oldLocal = process.env.LOCALAPPDATA;
  const oldXdg = process.env.XDG_CONFIG_HOME;
  process.env.LOCALAPPDATA = path.join(temp, "local-app-data");
  process.env.XDG_CONFIG_HOME = path.join(temp, "xdg");
  try {
    const vault = path.join(temp, "vault");
    fs.mkdirSync(vault, { recursive: true });
    const plugin = Object.create(TorchPlugin.prototype);
    plugin.app = {
      vault: {
        getName: () => "个人数据库",
        adapter: { getBasePath: () => vault },
      },
    };
    plugin.settings = {
      baseUrl: "http://127.0.0.1:8765", autoStart: true,
      repoRoot: path.join(temp, "another-device", "Torchos"),
      pythonPath: "", instancePort: 9168,
    };
    plugin.deviceKey = "vault-test";
    plugin.hydrateDeviceSettings({
      baseUrl: "http://127.0.0.1:8765", autoStart: true,
      repoRoot: path.join(temp, "another-device", "Torchos"),
      pythonPath: "", instancePort: 9168,
    });
    assert.equal(helpers.isRepoRoot(plugin.settings.repoRoot), true);
    assert.equal(plugin.settings.instancePort, 0);
    const local = JSON.parse(fs.readFileSync(
      helpers.deviceConfigPath("vault-test"), "utf8"));
    assert.equal(local.repoRoot, plugin.settings.repoRoot);
    assert.equal(local.instancePort, 0);
    let saved = null;
    plugin.loadData = async () => ({
      baseUrl: "http://127.0.0.1:8765", autoStart: true,
      repoRoot: "C:/old/Torchos", pythonPath: "", instancePort: 9168,
    });
    plugin.saveData = async (value) => { saved = value; };
    await plugin.saveSettings();
    assert.deepEqual(saved, {
      baseUrl: "http://127.0.0.1:8765", autoStart: true,
    });
  } finally {
    if (oldLocal === undefined) delete process.env.LOCALAPPDATA;
    else process.env.LOCALAPPDATA = oldLocal;
    if (oldXdg === undefined) delete process.env.XDG_CONFIG_HOME;
    else process.env.XDG_CONFIG_HOME = oldXdg;
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test("机器相关设置只写本机文件，共享设置不包含 repoRoot", () => {
  const temp = tempRoot();
  const oldLocal = process.env.LOCALAPPDATA;
  const oldXdg = process.env.XDG_CONFIG_HOME;
  process.env.LOCALAPPDATA = path.join(temp, "local-app-data");
  process.env.XDG_CONFIG_HOME = path.join(temp, "xdg");
  try {
    const repo = path.join(temp, "torch-os");
    const python = makeRepo(repo);
    assert.match(helpers.localConfigDir(), /torch-os[\\/]obsidian-plugin$/);
    const written = helpers.writeDeviceSettings("vault-id-123", {
      repoRoot: repo, pythonPath: python, instancePort: 9168,
      baseUrl: "http://127.0.0.1:8765",
    });
    assert.ok(written);
    const loaded = helpers.readDeviceSettings("vault-id-123");
    assert.equal(loaded.repoRoot, repo);
    assert.equal(loaded.pythonPath, python);
    assert.equal(loaded.instancePort, 9168);
    assert.equal("baseUrl" in loaded, false);
    assert.equal(helpers.deviceConfigPath("a/b:c"), path.join(
      helpers.localConfigDir(), "a-b-c.json"));
    const shared = helpers.sharedSettings({
      baseUrl: "http://127.0.0.1:9000", autoStart: false,
      repoRoot: repo, pythonPath: python, instancePort: 9168,
    });
    assert.deepEqual(shared, { baseUrl: "http://127.0.0.1:9000", autoStart: false });
  } finally {
    if (oldLocal === undefined) delete process.env.LOCALAPPDATA;
    else process.env.LOCALAPPDATA = oldLocal;
    if (oldXdg === undefined) delete process.env.XDG_CONFIG_HOME;
    else process.env.XDG_CONFIG_HOME = oldXdg;
    fs.rmSync(temp, { recursive: true, force: true });
  }
});
