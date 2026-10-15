import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline module/event integration for the local four-pillars page. The real
// page layout and the browser flow are verified separately in browser_smoke.py.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const chart = (overrides = {}) => ({
  schema: 1,
  generated_at: "2026-10-04T22:00:00+08:00",
  chart_time: "1990-01-01T12:00",
  day_master: "丙",
  five_elements: { 金: 0, 木: 2, 水: 1, 火: 4, 土: 1 },
  five_elements_note: "按四柱八个字的五行属性计数，只作结构展示。",
  true_solar: { applied: false, note: "未填写经度，按北京时间排盘。" },
  lunar: { text: "一九八九年腊月初五", zodiac: "蛇" },
  notes: ["传统文化参考：用于自我反思与记录。"],
  pillars: [
    { name: "年柱", ganzhi: "己巳", wu_xing: "土火", na_yin: "大林木", shi_shen_gan: "伤官", shi_shen_zhi: ["比肩"] },
    { name: "月柱", ganzhi: "丙子", wu_xing: "火水", na_yin: "涧下水", shi_shen_gan: "比肩", shi_shen_zhi: ["正官"] },
    { name: "日柱", ganzhi: "丙寅", wu_xing: "火木", na_yin: "炉中火", shi_shen_gan: "日主", shi_shen_zhi: ["偏印"] },
    { name: "时柱", ganzhi: "甲午", wu_xing: "木火", na_yin: "沙中金", shi_shen_gan: "偏印", shi_shen_zhi: ["劫财"] },
  ],
  ...overrides,
});

class NodeDouble {
  constructor(id = "") {
    this.id = id;
    this.listeners = new Map();
    this.dataset = {};
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.required = false;
    this.hidden = false;
    this.textContent = "";
    this.innerHTML = "";
  }
  addEventListener(type, callback) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(callback); }
  async emit(type, values = {}) {
    const event = { target: this, currentTarget: this, preventDefault() { this.defaultPrevented = true; }, ...values };
    for (const callback of this.listeners.get(type) || []) await callback(event);
    return event;
  }
  toggleAttribute(name, force) { if (force === undefined || force) this.dataset[name] = "true"; else delete this.dataset[name]; }
  focus() { this.focused = true; }
  closest() { return null; }
}

async function harness({ capabilities = { bazi: true }, profile = null, confirmAnswer = true, putError = "" } = {}) {
  const nodes = new Map();
  const requests = [];
  const getNode = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, new NodeDouble(selector));
    return nodes.get(selector);
  };
  const documentListeners = new Map();
  const document = {
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
  // 八字现在是数字分身里的一个分区：模块名是 identity。
  const state = { view: "identity", config: { capabilities } };
  let current = profile ? { ...profile } : null;
  const respond = (url, options = {}) => {
    if (url === "/api/bazi/profile" && (options.method || "GET") === "GET") {
      return { profile: current, chart: current ? chart() : null };
    }
    if (url === "/api/bazi/profile" && options.method === "PUT") {
      if (putError) throw new Error(putError);
      const body = JSON.parse(options.body);
      current = { ...body, updated_at: "2026-10-04T14:00:00+00:00" };
      return { profile: current, chart: chart() };
    }
    if (url === "/api/bazi/profile/clear") {
      const body = JSON.parse(options.body);
      assert.equal(body.confirm, true);
      current = null;
      return { cleared: true };
    }
    throw new Error(`unexpected request ${url}`);
  };
  const core = new vm.SyntheticModule(
    ["$", "state", "api", "escapeHtml", "jsonBody", "renderState", "setStatus", "toast", "confirmAction"],
    function () {
      this.setExport("$", getNode);
      this.setExport("state", state);
      this.setExport("api", async (url, options = {}) => { requests.push({ url, options }); return respond(url, options); });
      this.setExport("escapeHtml", (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character])));
      this.setExport("jsonBody", (method, data) => ({ method, body: JSON.stringify(data) }));
      this.setExport("renderState", (container, type, message, retry = "") => { container.state = { type, message, retry }; container.innerHTML = `state:${type}:${message}`; });
      this.setExport("setStatus", (selector, message, tone = "") => { getNode(selector).textContent = message; getNode(selector).tone = tone; });
      this.setExport("toast", (message) => { getNode("#toast").textContent = message; });
      this.setExport("confirmAction", async () => confirmAnswer);
    },
    { context: vm.createContext({ document }) },
  );
  const modules = new Map([[path.join(frontend, "core.js"), core]]);
  const pending = new Map();
  async function load(name) {
    const filename = path.resolve(frontend, name);
    if (modules.has(filename)) return modules.get(filename);
    if (pending.has(filename)) return pending.get(filename);
    const promise = (async () => {
      const module = new vm.SourceTextModule(await readFile(filename, "utf8"), {
        context: core.context, identifier: filename,
      });
      await module.link((specifier, referring) => load(path.relative(frontend, path.resolve(path.dirname(referring.identifier), specifier))));
      await module.evaluate();
      modules.set(filename, module);
      pending.delete(filename);
      return module;
    })();
    pending.set(filename, promise);
    return promise;
  }
  const module = await load("bazi.js");
  return { namespace: module.namespace, requests, getNode, state, document };
}

test("an empty profile renders guidance and saving sends the birth data once", async () => {
  const { namespace, requests, getNode } = await harness();
  namespace.initBazi();
  await namespace.loadBazi();
  assert.equal(getNode("#bazi-chart").state.type, "empty");
  assert.match(getNode("#bazi-chart").innerHTML, /填好出生日期与时间/);
  assert.equal(requests.length, 1);

  getNode("#bazi-birth-date").value = "1990-01-01";
  getNode("#bazi-birth-time").value = "12:00:00";
  await getNode("#bazi-form").emit("submit");
  const put = requests.at(-1);
  assert.equal(put.options.method, "PUT");
  assert.deepEqual(JSON.parse(put.options.body), {
    birth_date: "1990-01-01", birth_time: "12:00", longitude: null, use_true_solar: false,
  });
  assert.match(getNode("#bazi-chart").innerHTML, /己巳/);
  assert.match(getNode("#bazi-chart").innerHTML, /炉中火/);
  assert.match(getNode("#bazi-chart").innerHTML, /可以拿来问自己的问题/);
  assert.match(getNode("#bazi-status").textContent, /不会发送给任何模型或网络服务/);
});

test("true solar time needs a longitude before anything is sent", async () => {
  const { namespace, requests, getNode } = await harness();
  namespace.initBazi();
  getNode("#bazi-birth-date").value = "1990-01-01";
  getNode("#bazi-birth-time").value = "12:00";
  getNode("#bazi-true-solar").checked = true;
  await getNode("#bazi-true-solar").emit("change");
  await getNode("#bazi-form").emit("submit");
  assert.equal(requests.length, 0);
  assert.match(getNode("#bazi-status").textContent, /需要填写出生地经度/);
  assert.equal(getNode("#bazi-longitude").required, true);

  getNode("#bazi-longitude").value = "116.4";
  await getNode("#bazi-form").emit("submit");
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), {
    birth_date: "1990-01-01", birth_time: "12:00", longitude: 116.4, use_true_solar: true,
  });
});

test("clearing asks first and only removes the profile after confirmation", async () => {
  const rejected = await harness({ profile: { birth_date: "1990-01-01", birth_time: "12:00", longitude: null, use_true_solar: false }, confirmAnswer: false });
  rejected.namespace.initBazi();
  await rejected.namespace.loadBazi();
  assert.equal(rejected.getNode("#bazi-clear").dataset.hasProfile, "true");
  await rejected.getNode("#bazi-clear").emit("click");
  assert.equal(rejected.requests.length, 1);

  const accepted = await harness({ profile: { birth_date: "1990-01-01", birth_time: "12:00", longitude: null, use_true_solar: false } });
  accepted.namespace.initBazi();
  await accepted.namespace.loadBazi();
  await accepted.getNode("#bazi-clear").emit("click");
  assert.equal(accepted.requests.at(-1).url, "/api/bazi/profile/clear");
  assert.equal(accepted.getNode("#bazi-clear").dataset.hasProfile, "false");
  assert.equal(accepted.getNode("#bazi-chart").state.type, "empty");
  assert.match(accepted.getNode("#bazi-status").textContent, /已从本机清除/);
});

test("an older service disables the page and a hostile label stays escaped", async () => {
  const old = await harness({ capabilities: {}, profile: { birth_date: "1990-01-01", birth_time: "12:00", longitude: null, use_true_solar: false } });
  old.namespace.initBazi();
  await old.namespace.loadBazi();
  assert.equal(old.requests.length, 0);
  assert.equal(old.getNode("#bazi-save").disabled, true);
  assert.match(old.getNode("#bazi-support").textContent, /尚未支持八字参考页/);
  old.state.config.capabilities.bazi = true;
  old.document.dispatchEvent({ type: "config-changed" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(old.getNode("#bazi-save").disabled, false);
  assert.equal(old.requests.length, 1);

  const hostile = await harness({
    profile: { birth_date: "1990-01-01", birth_time: "12:00", longitude: null, use_true_solar: false },
  });
  hostile.namespace.initBazi();
  await hostile.namespace.loadBazi();
  assert.equal(hostile.getNode("#bazi-chart").innerHTML.includes("<img"), false);
});
