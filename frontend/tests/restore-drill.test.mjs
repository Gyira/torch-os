import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline checks for the one-click restore drill card (core.js is stubbed).
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

class NodeDouble {
  constructor(id = "") {
    this.id = id;
    this.listeners = new Map();
    this.disabled = false;
    this.hidden = false;
    this.textContent = "";
    this.innerText = "";
    this.innerHTML = "";
  }
  addEventListener(type, callback) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(callback); }
  async emit(type) {
    for (const callback of this.listeners.get(type) || []) await callback({ target: this, currentTarget: this, preventDefault() {} });
  }
}

async function harness({ capabilities = { restore_drill: true }, drill = null, reports = null, hasReport = true, confirmAnswer = true, error = "" } = {}) {
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
    addEventListener(type, callback) { if (!documentListeners.has(type)) documentListeners.set(type, []); documentListeners.get(type).push(callback); },
    dispatchEvent(event) { for (const callback of documentListeners.get(event?.type) || []) callback(event); return true; },
  };
  const state = { view: "settings", config: { capabilities } };
  const core = new vm.SyntheticModule(
    ["$", "state", "api", "escapeHtml", "formatDate", "jsonBody", "setStatus", "toast", "confirmAction"],
    function () {
      this.setExport("$", getNode);
      this.setExport("state", state);
      this.setExport("api", async (url, options = {}) => {
        requests.push({ url, options });
        if (error) throw new Error(error);
        if (url === "/api/backups/drill/reports") return reports || { count: 0, reports: [], note: "还没有演练报告" };
        return drill;
      });
      this.setExport("escapeHtml", (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character])));
      this.setExport("formatDate", (value) => String(value ?? ""));
      this.setExport("jsonBody", (method, data) => ({ method, body: JSON.stringify(data) }));
      this.setExport("setStatus", (selector, message, tone = "") => { getNode(selector).textContent = message; getNode(selector).tone = tone; });
      this.setExport("toast", (message) => { getNode("#toast").textContent = message; });
      this.setExport("confirmAction", async () => confirmAnswer);
    },
    {
      context: vm.createContext({
        document,
        location: { assign(url) { getNode("#toast").assigned = url; } },
        fetch: async () => ({ ok: hasReport, status: hasReport ? 200 : 404, text: async () => "" }),
      }),
    },
  );
  const filename = path.join(frontend, "restore-drill.js");
  const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context: core.context, identifier: filename });
  await module.link((specifier) => {
    if (specifier === "./core.js") return core;
    throw new Error(`unexpected import ${specifier}`);
  });
  await module.evaluate();
  return { namespace: module.namespace, requests, getNode, document };
}

const drillPayload = {
  ok: true,
  backup: { filename: "torch-os-20261005T000000000000Z-abcdef01.zip", verified: true, file_count: 8 },
  copy: { target_dir: "C:\\data\\restored\\3b31e490<b>copy" },
  verification: { ok: true, sqlite_integrity: "ok", file_count: 8, vault_files: 7 },
  report_path: "C:\\data\\diagnostics\\restore-drill-latest.md",
  note: "演练只写入 data/restored 与 data/diagnostics：没有覆盖当前库、没有删除任何备份、没有切换数据。",
};

test("running the drill asks first, then shows the verified summary with escaped names", async () => {
  const h = await harness({ drill: drillPayload });
  h.namespace.initRestoreDrill();
  await h.namespace.loadDrillReport();
  assert.equal(h.getNode("#download-drill-report").disabled, false);
  await h.getNode("#run-restore-drill").emit("click");
  const request = h.requests.find((item) => item.url === "/api/backups/drill");
  assert.ok(request, "drill request was sent");
  assert.deepEqual(JSON.parse(request.options.body), { confirm: true });
  const html = h.getNode("#drill-summary").innerHTML;
  assert.equal(html.includes("<b>"), false);
  assert.ok(html.includes("&lt;b&gt;"));
  assert.match(html, /verified/);
  assert.match(html, /integrity ok/);
  assert.match(html, /7 篇/);
  assert.match(h.getNode("#drill-status").textContent, /演练通过/);
  assert.match(h.getNode("#toast").textContent, /恢复演练通过/);
});

test("cancelling sends nothing and a failed drill keeps the button usable", async () => {
  const declined = await harness({ drill: drillPayload, confirmAnswer: false });
  declined.namespace.initRestoreDrill();
  await declined.getNode("#run-restore-drill").emit("click");
  assert.equal(declined.requests.filter((item) => item.url === "/api/backups/drill").length, 0);

  const failed = await harness({ drill: drillPayload, error: "备份目录不可写" });
  failed.namespace.initRestoreDrill();
  await failed.getNode("#run-restore-drill").emit("click");
  assert.match(failed.getNode("#drill-status").textContent, /演练失败：备份目录不可写/);
  assert.equal(failed.getNode("#run-restore-drill").disabled, false);
});

test("an older service stays disabled and the report downloads through the endpoint", async () => {
  const old = await harness({ capabilities: {}, hasReport: false });
  old.namespace.initRestoreDrill();
  assert.equal(old.getNode("#run-restore-drill").disabled, true);
  assert.match(old.getNode("#drill-support").textContent, /尚未支持一键恢复演练/);

  const h = await harness({ drill: drillPayload });
  h.namespace.initRestoreDrill();
  h.getNode("#download-drill-report").disabled = false;
  await h.getNode("#download-drill-report").emit("click");
  assert.equal(h.getNode("#toast").assigned, "/api/backups/drill/last.md");
});

test("the report history lists past drills and downloads a chosen one", async () => {
  const h = await harness({
    drill: drillPayload,
    reports: {
      count: 2,
      note: "报告只增不减；每个文件只含演练的计数与路径，不含任何笔记正文。",
      reports: [
        { name: "restore-drill-20261005T010203000001.md", bytes: 900, created_at: "2026-10-05T09:02:03+08:00" },
        { name: "restore-drill-20261005T004500000002.md", bytes: 880, created_at: "2026-10-05T08:45:00+08:00" },
      ],
    },
  });
  h.namespace.initRestoreDrill();
  await h.namespace.loadDrillReports();
  const html = h.getNode("#drill-reports").innerHTML;
  assert.equal(h.getNode("#drill-reports").hidden, false);
  assert.match(html, /最近 2 份演练报告/);
  assert.equal((html.match(/data-drill-report=/g) || []).length, 2);
  assert.match(html, /restore-drill-20261005T010203000001\.md/);

  h.document.dispatchEvent({
    type: "click",
    target: { closest: (selector) => (selector === "[data-drill-report]" ? { dataset: { drillReport: "restore-drill-20261005T004500000002.md" } } : null) },
  });
  assert.equal(h.getNode("#toast").assigned, "/api/backups/drill/reports/restore-drill-20261005T004500000002.md");
});
