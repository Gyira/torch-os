import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the workspace panel (core.js is stubbed).
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

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
  focus() {}
}

const listing = {
  workspaces: [
    { name: "个人", folder: "D:\\Obsidian\\<img src=x>", data: "D:\\Obsidian\\<img src=x>\\.torch-os", read_only: false, active: true, registered: true },
    { name: "家人A", folder: "D:\\Obsidian\\Family", data: "D:\\Obsidian\\Family\\.torch-os", read_only: true, active: false, registered: true },
  ],
  active: "个人",
  restart_required_for_changes: true,
  note: "换工作区就是换文件夹；重启服务后生效。",
};

async function harness({ capabilities = { workspaces: true }, listing: payload = listing, saved = null,
                        activated = null, config = {} } = {}) {
  const nodes = new Map();
  const requests = [];
  const documentListeners = new Map();
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
  const state = { view: "settings", config: { capabilities, ...config } };
  const core = new vm.SyntheticModule(
    ["$", "state", "api", "escapeHtml", "jsonBody", "setStatus", "toast", "confirmAction"],
    function () {
      this.setExport("$", getNode);
      this.setExport("state", state);
      this.setExport("api", async (url, options = {}) => {
        requests.push({ url, options });
        if (url === "/api/workspaces" && (options.method || "GET") === "GET") return payload;
        if (url === "/api/workspaces" && options.method === "POST") return saved;
        if (url === "/api/workspaces/activate") return activated;
        if (url === "/api/workspaces/delete") return { deleted: "家人A" };
        throw new Error(`unexpected ${url}`);
      });
      this.setExport("escapeHtml", (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character])));
      this.setExport("jsonBody", (method, data) => ({ method, body: JSON.stringify(data) }));
      this.setExport("setStatus", (selector, message, tone = "") => { getNode(selector).textContent = message; getNode(selector).tone = tone; });
      this.setExport("toast", (message) => { getNode("#toast").textContent = message; });
      this.setExport("confirmAction", async () => true);
    },
    { context: vm.createContext({ document }) },
  );
  const filename = path.join(frontend, "workspaces.js");
  const module = new vm.SourceTextModule(await readFile(filename, "utf8"), {
    context: core.context, identifier: filename,
  });
  await module.link((specifier, referring) => {
    const resolved = path.resolve(path.dirname(referring.identifier), specifier);
    if (resolved === path.join(frontend, "core.js")) return core;
    throw new Error(`unexpected import ${specifier}`);
  });
  await module.evaluate();
  return { namespace: module.namespace, requests, getNode };
}

function clickOn(selector, dataKey, value) {
  return {
    target: {
      closest: (wanted) => (wanted === selector ? { dataset: { [dataKey]: value } } : null),
    },
  };
}

test("the workspace list escapes paths and marks the running one", async () => {
  const h = await harness();
  h.namespace.initWorkspaces();
  await h.namespace.loadWorkspaces();
  const html = h.getNode("#workspace-list").innerHTML;
  assert.equal(html.includes("<img"), false);
  assert.ok(html.includes("&lt;img"));
  assert.match(html, /当前运行/);
  assert.match(html, /只读/);
  assert.match(h.getNode("#workspace-status").textContent, /当前工作区：个人/);
  assert.equal(h.requests.at(-1).url, "/api/workspaces");
});

test("saving an existing workspace keeps the original name for rename detection", async () => {
  const h = await harness({ saved: { saved: true, note: "已保存到工作区列表。" } });
  h.namespace.initWorkspaces();
  await h.namespace.loadWorkspaces();
  await h.getNode("#workspace-list").emit("click", clickOn("[data-workspace-edit]", "workspaceEdit", "家人A"));
  assert.equal(h.getNode("#workspace-form").hidden, false);
  assert.equal(h.getNode("#workspace-name").value, "家人A");
  assert.equal(h.getNode("#workspace-folder").value, "D:\\Obsidian\\Family");
  assert.equal(h.getNode("#workspace-read-only").checked, true);

  h.getNode("#workspace-folder").value = "D:\\Obsidian\\Family2";
  await h.getNode("#workspace-form").emit("submit");
  const request = h.requests.filter((item) => item.options?.method === "POST").at(-1);
  assert.deepEqual(JSON.parse(request.options.body), {
    name: "家人A", folder: "D:\\Obsidian\\Family2",
    read_only: true, original_name: "家人A",
  });
  assert.match(h.getNode("#workspace-status").textContent, /已保存/);
});

test("switching posts the name and reports that a restart is required", async () => {
  const h = await harness({ activated: { name: "家人A", restart_required: true, note: "已写入本地 .env；重启本地服务后切换到该工作区。" } });
  h.namespace.initWorkspaces();
  await h.namespace.loadWorkspaces();
  await h.getNode("#workspace-list").emit("click", clickOn("[data-workspace-activate]", "workspaceActivate", "家人A"));
  const request = h.requests.filter((item) => item.options?.method === "POST").at(-1);
  assert.equal(request.url, "/api/workspaces/activate");
  assert.deepEqual(JSON.parse(request.options.body), { name: "家人A" });
  assert.match(h.getNode("#workspace-status").textContent, /重启本地服务后/);
});

test("without the capability the panel stays inert", async () => {
  const h = await harness({ capabilities: {} });
  h.namespace.initWorkspaces();
  await h.namespace.loadWorkspaces();
  assert.equal(h.requests.length, 0);
  assert.equal(h.getNode("#workspace-new").disabled, true);
  assert.match(h.getNode("#workspace-support").textContent, /尚未支持工作区设置/);
});

test("a vault-scoped instance hides the workspace switcher", async () => {
  const h = await harness({
    capabilities: { workspaces: false },
    config: { vault_scoped: true, vault_name: "我的 Obsidian 库" },
  });
  h.namespace.initWorkspaces();
  await h.namespace.loadWorkspaces();
  assert.equal(h.requests.length, 0);
  assert.equal(h.getNode("#workspace-card").hidden, true);
  assert.equal(h.getNode("#vault-scoped-card").hidden, false);
  assert.match(h.getNode("#vault-scoped-text").textContent, /我的 Obsidian 库/);
  assert.match(h.getNode("#vault-scoped-text").textContent, /打开另一个库/);
});
