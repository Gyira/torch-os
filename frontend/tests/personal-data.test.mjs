// 个性化数据桥：.torch-os/personal-data.json 与 localStorage 的双向同步。
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

class FakeStorage {
  constructor(entries = {}, quota = Infinity) {
    this.map = new Map(Object.entries(entries));
    this.quota = quota;
  }
  get length() { return this.map.size; }
  key(index) { return [...this.map.keys()][index] ?? null; }
  getItem(key) { return this.map.has(key) ? this.map.get(key) : null; }
  setItem(key, value) {
    const text = String(value);
    if (text.length > this.quota) {
      const error = new Error("QuotaExceededError");
      error.name = "QuotaExceededError";
      throw error;
    }
    this.map.set(String(key), text);
  }
  removeItem(key) { this.map.delete(String(key)); }
}

async function harness({ local = {}, server = {}, failGet = false, vaultId = "vault-a", quota = Infinity } = {}) {
  const storage = new FakeStorage(local, quota);
  const serverValues = { ...server };
  const requests = [];
  async function fetch(url, options = {}) {
    requests.push({ url, options });
    if (failGet && options.method !== "PUT") throw new TypeError("offline");
    if (options.method === "PUT") {
      const values = JSON.parse(options.body).values;
      for (const [key, value] of Object.entries(values)) {
        if (value === null) delete serverValues[key];
        else serverValues[key] = value;
      }
    }
    return { ok: true, status: 200, json: async () => ({ vault_id: vaultId, values: { ...serverValues } }) };
  }
  const context = vm.createContext({
    window: { localStorage: storage, setTimeout, clearTimeout },
    localStorage: storage,
    Storage: FakeStorage,
    document: { querySelector: () => ({ addEventListener() {} }), querySelectorAll: () => [], addEventListener() {} },
    fetch,
    AbortController,
    TypeError,
    console,
  });
  const modules = new Map();
  async function load(name) {
    const filename = path.resolve(frontend, name);
    if (modules.has(filename)) return modules.get(filename);
    const module = new vm.SourceTextModule(await readFile(filename, "utf8"), {
      context, identifier: filename,
    });
    modules.set(filename, module);
    await module.link((specifier, referring) => load(path.relative(frontend, path.resolve(path.dirname(referring.identifier), specifier))));
    await module.evaluate();
    return module;
  }
  const api = (await load("personal-data.js")).namespace;
  return { api, storage, serverValues, requests };
}

test("only torch-os namespaces are mirrored", async () => {
  const { api } = await harness();
  assert.equal(api.isPersonalKey("torch-os:weather-city"), true);
  assert.equal(api.isPersonalKey("torch:rail-groups:workbench"), true);
  assert.equal(api.isPersonalKey("torch.welcome.v1.dismissed"), true);
  assert.equal(api.isPersonalKey("torch-os:"), false);
  assert.equal(api.isPersonalKey("other-app:value"), false);
  assert.equal(api.isPersonalKey("torch-os:天气 城市"), false);
});

test("boot pulls database values into localStorage and pushes local-only keys back", async () => {
  const { api, storage, serverValues, requests } = await harness({
    local: {
      "__torch_personal_owner": "vault-a",
      "torch-os:time-view": "month",
      "other-app:secret": "keep-local",
    },
    server: { "torch-os:weather-cities": '["上海"]' },
  });
  const result = await api.initPersonalData();
  assert.equal(result.synced, true);
  assert.equal(result.switched, false);
  assert.equal(result.remote, 1);
  assert.equal(result.pushed, 1);
  assert.equal(storage.getItem("torch-os:weather-cities"), '["上海"]');
  assert.equal(storage.getItem("torch-os:time-view"), "month");
  assert.equal(storage.getItem("other-app:secret"), "keep-local");

  await sleep(600);
  const put = requests.find(({ options }) => options.method === "PUT");
  assert.ok(put, "local-only personalization should be mirrored");
  assert.deepEqual(JSON.parse(put.options.body).values, { "torch-os:time-view": "month" });
  assert.equal(serverValues["other-app:secret"], undefined);
});

test("the first upgrade migrates old localStorage keys and marks the cache owner", async () => {
  const { api, storage, serverValues, requests } = await harness({
    local: { "torch-os:time-view": "month", "other-app:secret": "keep-local" },
    server: {},
  });
  const result = await api.initPersonalData();
  assert.equal(result.synced, true);
  assert.equal(result.switched, false);
  assert.equal(result.pushed, 1);
  assert.equal(storage.getItem("__torch_personal_owner"), "vault-a");
  assert.equal(storage.getItem("other-app:secret"), "keep-local");
  await sleep(600);
  const put = requests.find(({ options }) => options.method === "PUT");
  assert.deepEqual(JSON.parse(put.options.body).values, { "torch-os:time-view": "month" });
  assert.equal(serverValues["torch-os:time-view"], "month");
});

test("first upgrade keeps local settings even if another browser already wrote defaults", async () => {
  const { api, storage, serverValues } = await harness({
    local: { "torch-os:time-view": "month" },
    server: {
      "torch-os:time-view": "week",
      "torch-os:nav-order": '["life"]',
    },
  });
  const result = await api.initPersonalData();
  assert.equal(result.synced, true);
  assert.equal(result.switched, false);
  assert.equal(result.pushed, 1);
  assert.equal(storage.getItem("torch-os:time-view"), "month"); // 本机旧值优先
  assert.equal(storage.getItem("torch-os:nav-order"), '["life"]'); // 库里其它键仍然补进来
  await sleep(600);
  assert.equal(serverValues["torch-os:time-view"], "month");
  assert.equal(serverValues["torch-os:nav-order"], '["life"]');
});


test("opening another personal database clears the previous vault's cache", async () => {
  const { api, storage, serverValues, requests } = await harness({
    local: {
      "__torch_personal_owner": "vault-a",
      "torch-os:time-view": "month",
      "torch-os:weather-city": "auto",
      "other-app:secret": "keep-local",
    },
    server: { "torch-os:nav-order": '["life"]' },
    vaultId: "vault-b",
  });
  const result = await api.initPersonalData();
  assert.equal(result.synced, true);
  assert.equal(result.switched, true);
  assert.equal(result.pushed, 0);
  assert.equal(storage.getItem("torch-os:time-view"), null);
  assert.equal(storage.getItem("torch-os:weather-city"), null);
  assert.equal(storage.getItem("torch-os:nav-order"), '["life"]');
  assert.equal(storage.getItem("other-app:secret"), "keep-local");
  assert.equal(storage.getItem("__torch_personal_owner"), "vault-b");
  await sleep(600);
  assert.equal(requests.filter(({ options }) => options.method === "PUT").length, 0);
  assert.deepEqual(serverValues, { "torch-os:nav-order": '["life"]' });
});


test("a failed boot read never pushes local cache over possibly newer database values", async () => {
  const { api, storage, serverValues, requests } = await harness({
    local: { "torch-os:time-view": "month" },
    server: { "torch-os:time-view": "week" },
    failGet: true,
  });
  const result = await api.initPersonalData();
  assert.equal(result.synced, false);
  assert.equal(result.pushed, 0);
  assert.equal(storage.getItem("torch-os:time-view"), "month");
  await sleep(500);
  assert.equal(requests.filter(({ options }) => options.method === "PUT").length, 0);
  assert.equal(serverValues["torch-os:time-view"], "week");

  // 这次会话仍可读写本机缓存，但在成功读到后端之前不开启镜像，避免覆盖库里的新值。
  storage.setItem("torch-os:time-view", "day");
  await sleep(500);
  assert.equal(storage.getItem("torch-os:time-view"), "day");
  assert.equal(serverValues["torch-os:time-view"], "week");
});


test("a full localStorage falls back to memory and still persists the value", async () => {
  const big = "A".repeat(2000);
  const { api, storage, serverValues } = await harness({
    local: {},
    server: { "torch-os:anniversary": big },
    quota: 500,
  });
  const result = await api.initPersonalData();
  assert.equal(result.synced, true);
  assert.equal(storage.getItem("torch-os:anniversary"), big); // 从内存兜底读回

  const second = "B".repeat(2000);
  storage.setItem("torch-os:countdown:v1", second);
  assert.equal(storage.getItem("torch-os:countdown:v1"), second);
  await sleep(600);
  assert.equal(serverValues["torch-os:countdown:v1"], second);
});


test("later writes and removals mirror to the database, transient weather cache does not", async () => {
  const { api, storage, serverValues, requests } = await harness({
    server: { "torch-os:nav-order": '["workbench"]' },
  });
  await api.initPersonalData();
  const before = requests.length;

  storage.setItem("torch-os:nav-order", '["life"]');
  storage.setItem("torch-os:weather", '{"temperature":20}');
  await sleep(600);
  assert.equal(serverValues["torch-os:nav-order"], '["life"]');
  assert.equal(serverValues["torch-os:weather"], undefined);
  assert.ok(requests.length > before);

  storage.removeItem("torch-os:nav-order");
  await sleep(600);
  assert.equal(serverValues["torch-os:nav-order"], undefined);
});
