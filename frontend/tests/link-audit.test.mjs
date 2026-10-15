import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

// Synthetic offline event tests. No archive, model, browser, or private data access.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const records = Array.from({ length: 24 }, (_, index) => ({ path: `sample-${index}.md`, type: "markdown", import_allowed: true, sensitive: index === 0, size: 20 }));
const emptyAudit = { counts: {}, items: [], warnings: [], content_verified: false, attachments_verified: false, report_path: "reports/synthetic.json" };
const emptyPlan = { summary: { supported: 0, skipped: 0, copy_bytes: 0, notes_needing_import: 0, notes_blocked: 0 }, notes: [], warnings: [], writes: false };

async function harness(supported = true) {
  const nodes = new Map();
  const checkboxes = new Map();
  class Element {
    constructor() { this.listeners = new Map(); this.dataset = {}; this.attributes = {}; this.value = ""; this.checked = false; this.disabled = false; this.hidden = false; this.textContent = ""; this.innerHTML = ""; this.classList = { add() {}, remove() {} }; }
    addEventListener(type, handler) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(handler); }
    async emit(type, values = {}) { for (const handler of this.listeners.get(type) || []) await handler({ target: this, currentTarget: this, preventDefault() {}, ...values }); }
    setAttribute(name, value) { this.attributes[name] = value; }
    replaceChildren() { this.innerHTML = ""; }
    querySelector(selector) { return getNode(selector); }
  }
  const getNode = (selector) => { if (!nodes.has(selector)) nodes.set(selector, new Element()); return nodes.get(selector); };
  const requests = [];
  let respond = (url) => url.endsWith("/preview") ? { files: records } : url.endsWith("/links/audit") ? emptyAudit : url.endsWith("/attachments/plan") ? emptyPlan : url.endsWith("/apply") ? { imported: 1, skipped: 0, errors: [] } : [];
  const context = vm.createContext({
    document: { querySelector: getNode, querySelectorAll: (selector) => selector === "[data-import-path]" ? [...checkboxes.values()] : [], addEventListener() {}, dispatchEvent() {} },
    window: { setTimeout: () => 1, clearTimeout() {} },
    AbortController, TypeError, Intl, Date, CustomEvent: class { constructor(type) { this.type = type; } },
    fetch: async (url, options) => { requests.push({ url, options }); return { ok: true, status: 200, json: async () => respond(url, options) }; },
  });
  const modules = new Map();
  const pending = new Map();
  async function load(name) {
    const filename = path.resolve(frontend, name);
    if (modules.has(filename)) return modules.get(filename);
    if (pending.has(filename)) return pending.get(filename);
    const promise = (async () => {
      const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context, identifier: filename });
      await module.link((specifier, referrer) => load(path.relative(frontend, path.resolve(path.dirname(referrer.identifier), specifier))));
      await module.evaluate();
      modules.set(filename, module);
      pending.delete(filename);
      return module;
    })();
    pending.set(filename, promise);
    return promise;
  }
  // Preload the shared core before linking the imports -> library -> core diamond.
  // load() caches after async readFile; parallel linking could otherwise construct
  // two core instances and set capabilities on the one imports does not use.
  const core = (await load("core.js")).namespace;
  const decisions = [];
  const confirmations = [];
  const coreKeys = Object.keys(core);
  const coreModule = new vm.SyntheticModule(coreKeys, function () {
    for (const key of coreKeys) {
      this.setExport(key, key === "confirmAction"
        ? (options) => { confirmations.push(options); return Promise.resolve(decisions.shift() ?? false); }
        : core[key]);
    }
  }, { context });
  modules.set(path.join(frontend, "core.js"), coreModule);
  const imports = (await load("imports.js")).namespace;
  core.state.config = { capabilities: {
    link_audit: supported, attachment_plan: supported, attachment_localize: supported,
    import_history: supported, history_restore: supported, history_cleanup_preview: supported,
  } };
  imports.initImports();
  getNode("#import-package").value = "synthetic package";
  const preview = () => getNode("#preview-import").emit("click");
  const select = async (filePath, checked = true) => {
    const input = checkboxes.get(filePath) || { dataset: { importPath: filePath }, checked: false, closest() { return this; } };
    checkboxes.set(filePath, input);
    input.checked = checked;
    await getNode("#import-file-list").emit("change", { target: input });
    return input;
  };
  return { imports, getNode, context, requests, confirmations, decisions, preview, select, setResponse(callback) { respond = callback; } };
}

test("older backend without link audit support cannot issue unsupported requests", async () => {
  const { preview, select, getNode, requests } = await harness(false);
  await preview();
  await select("sample-1.md");
  assert.equal(getNode("#audit-import-links").disabled, true);
  await getNode("#audit-import-links").emit("click");
  assert.equal(requests.some(({url}) => url.endsWith('/links/audit')), false);
});

test("loadImports lists discovered packages and reports an honest empty list", async () => {
  const { imports, getNode, requests, setResponse } = await harness();
  setResponse((url) => url === "/api/imports"
    ? [{ id: "pkg-1", filename: "synthetic.zip", bytes: 512 }]
    : {});
  await imports.loadImports();
  assert.ok(requests.some(({ url }) => url === "/api/imports"));
  assert.match(getNode("#import-package").innerHTML, /synthetic\.zip/);
  assert.match(getNode("#import-status").textContent, /找到 1 个导出包/);
  // 占位项仍被选中：生成清单必须由人明确选择导出包。
  assert.equal(getNode("#preview-import").disabled, true);
  setResponse(() => []);
  await imports.loadImports();
  assert.match(getNode("#import-status").textContent, /暂未发现已落盘的导出包/);
});

test("link auditing is explicit, uses the existing eligible selection, and caps it at twenty", async () => {
  const { preview, select, getNode, requests } = await harness();
  await preview();
  assert.equal(getNode("#audit-import-links").disabled, true);
  await getNode("#audit-import-links").emit("click");
  assert.equal(requests.some(({ url }) => url.endsWith("/links/audit")), false);
  assert.equal((await select("sample-0.md")).checked, false);
  assert.equal((await select("unknown.md")).checked, false);
  assert.equal(getNode("#audit-import-links").disabled, true);
  for (let index = 1; index <= 20; index += 1) await select(`sample-${index}.md`);
  assert.equal((await select("sample-21.md")).checked, false);
  assert.equal(getNode("#audit-import-links").disabled, false);
  await getNode("#audit-import-links").emit("click");
  const request = requests.find(({ url }) => url.endsWith("/links/audit"));
  assert.equal(request.url, "/api/imports/synthetic%20package/links/audit");
  assert.equal(request.options.method, "POST");
  assert.deepEqual(JSON.parse(request.options.body), { selected_paths: Array.from({ length: 20 }, (_, index) => `sample-${index + 1}.md`) });
  assert.match(getNode("#import-selection-count").textContent, /已选择 20 \/ 20/);
  assert.equal(requests.some(({ url }) => /\/apply$|\/chat|\/model/.test(url)), false);
});

test("the link report escapes source and target text, renders missing counts as zero, and makes no links clickable", async () => {
  const { preview, select, getNode, setResponse } = await harness();
  await preview();
  await select("sample-1.md");
  setResponse(() => ({ ...emptyAudit, counts: { resolved_note: 2, unsafe: 1, restricted_target: 1, total: 4 }, warnings: ["<img src=x onerror='bad()'>"], report_path: "<report>.json", items: [
    { source: "<source>.md", line: 12, target: "javascript:alert('<bad>')", classification: "unsafe", resolved_path: "<resolved>.md", warnings: ["<warning>"] },
    { source: "safe.md", line: null, target: null, classification: "restricted_target", resolved_path: null, warnings: [] },
  ] }));
  await getNode("#audit-import-links").emit("click");
  const html = getNode("#import-link-audit-result").innerHTML;
  assert.match(html, /笔记路径存在<\/dt><dd>2/);
  assert.match(html, /附件路径存在<\/dt><dd>0/);
  assert.match(html, /外部链接（未访问）<\/dt><dd>0/);
  assert.match(html, /链接总数<\/dt><dd>4/);
  assert.match(html, /&lt;source&gt;\.md/);
  assert.match(html, /javascript:alert\(&#39;&lt;bad&gt;&#39;\)/);
  assert.match(html, /&lt;resolved&gt;\.md/);
  assert.match(html, /&lt;warning&gt;/);
  assert.match(html, /&lt;img/);
  assert.match(html, /&lt;report&gt;\.json/);
  assert.match(html, /第 12 行/);
  assert.match(html, /未展示目标文本/);
  assert.match(html, /未验证附件正文或可用性/);
  assert.match(html, /不复制或本地化附件/);
  assert.doesNotMatch(html, /<a\b|href=|<img\b|<script\b/);
  assert.match(getNode("#import-link-audit-status").textContent, /未导入资料或修改原文/);
});

test("only findings that need review are listed, with at most fifty rows", async () => {
  const { preview, select, getNode, setResponse } = await harness();
  await preview();
  await select("sample-1.md");
  const classifications = ["missing", "unsafe", "restricted_target", "manual_review"];
  setResponse(() => ({ ...emptyAudit, counts: { missing: 16, unsafe: 15, restricted_target: 15, manual_review: 15, resolved_note: 1, external: 1, total: 63 }, items: [
    { source: "safe-note", target: "safe-target", classification: "resolved_note" },
    { source: "remote-source", target: "https://should-not-open.invalid", classification: "external" },
    ...Array.from({ length: 61 }, (_, index) => ({ source: `review-${index}`, target: `target-${index}`, line: index + 1, classification: classifications[index % classifications.length], warnings: [] })),
  ] }));
  await getNode("#audit-import-links").emit("click");
  const html = getNode("#import-link-audit-result").innerHTML;
  assert.equal((html.match(/<li><strong>/g) || []).length, 50);
  assert.match(html, /需要检查 61 项 · 显示 50 项/);
  assert.match(html, /完整清单见本地报告/);
  assert.doesNotMatch(html, /safe-target|should-not-open|review-50/);
});

test("auditing locks preview, import, refresh, selections, and duplicate audit requests until completion", async () => {
  const { imports, preview, select, getNode, requests, setResponse } = await harness();
  await preview();
  const selected = await select("sample-1.md");
  let release;
  setResponse(() => new Promise((resolve) => { release = resolve; }));
  const pending = getNode("#audit-import-links").emit("click");
  for (const id of ["#import-package", "#refresh-imports", "#preview-import", "#apply-import", "#clear-import-selection", "#import-search", "#import-selected-only", "#audit-import-links"]) assert.equal(getNode(id).disabled, true, id);
  assert.equal(selected.disabled, true);
  await getNode("#audit-import-links").emit("click");
  await getNode("#preview-import").emit("click");
  await getNode("#apply-import").emit("click");
  await getNode("#clear-import-selection").emit("click");
  await imports.loadImports();
  assert.equal((await select("sample-1.md", false)).checked, true);
  assert.equal((await select("sample-2.md")).checked, false);
  assert.equal(requests.length, 2);
  assert.match(getNode("#import-selection-count").textContent, /已选择 1 \/ 20/);
  release(emptyAudit);
  await pending;
  assert.equal(getNode("#audit-import-links").disabled, false);
  assert.equal(getNode("#apply-import").disabled, false);
  assert.equal(getNode("#import-package").disabled, false);
  assert.equal(selected.disabled, false);
});

test("a stale audit response cannot overwrite a reset source even after the same package is selected again", async () => {
  const { preview, select, getNode, setResponse } = await harness();
  await preview();
  await select("sample-1.md");
  let release;
  setResponse(() => new Promise((resolve) => { release = resolve; }));
  const pending = getNode("#audit-import-links").emit("click");
  // Disabled controls block human source changes; a programmatic reset must also
  // invalidate the response instead of trusting the same package id afterwards.
  getNode("#import-package").value = "other package";
  await getNode("#import-package").emit("change");
  getNode("#import-package").value = "synthetic package";
  await getNode("#import-package").emit("change");
  release({ ...emptyAudit, report_path: "must-not-appear.json" });
  await pending;
  assert.equal(getNode("#import-link-audit-result").innerHTML, "");
  assert.equal(getNode("#import-link-audit-status").textContent, "");
  assert.equal(getNode("#import-preview").hidden, true);
  assert.equal(getNode("#audit-import-links").disabled, true);
});

test("changing the selected records clears a report so it cannot be mistaken for the new selection", async () => {
  const { preview, select, getNode } = await harness();
  await preview();
  await select("sample-1.md");
  await getNode("#audit-import-links").emit("click");
  assert.match(getNode("#import-link-audit-result").innerHTML, /synthetic\.json/);
  await select("sample-2.md");
  assert.equal(getNode("#import-link-audit-result").innerHTML, "");
  assert.equal(getNode("#import-link-audit-status").textContent, "");
  await getNode("#audit-import-links").emit("click");
  await getNode("#clear-import-selection").emit("click");
  assert.equal(getNode("#import-link-audit-result").innerHTML, "");
  assert.equal(getNode("#audit-import-links").disabled, true);
});

test("a failed audit preserves the selection and supports an explicit local retry", async () => {
  const { preview, select, getNode, context, requests } = await harness();
  await preview();
  await select("sample-1.md");
  context.fetch = async (url, options) => { requests.push({ url, options }); throw new TypeError("offline"); };
  await getNode("#audit-import-links").emit("click");
  assert.match(getNode("#import-link-audit-status").textContent, /已保留当前选择，可重试/);
  assert.match(getNode("#import-selection-count").textContent, /已选择 1 \/ 20/);
  assert.equal(getNode("#audit-import-links").disabled, false);
  assert.equal(getNode("#apply-import").disabled, false);
  context.fetch = async (url, options) => { requests.push({ url, options }); return { ok: true, status: 200, json: async () => emptyAudit }; };
  await getNode("#audit-import-links").emit("click");
  assert.equal(requests.filter(({ url }) => url.endsWith("/links/audit")).length, 2);
  assert.match(getNode("#import-link-audit-status").textContent, /核对完成/);
});

test("a running preview or import prevents an audit request", async () => {
  for (const action of ["preview", "apply"]) {
    const { preview, select, getNode, requests, setResponse } = await harness();
    await preview();
    await select("sample-1.md");
    let release;
    setResponse((url) => url.endsWith(`/${action}`) ? new Promise((resolve) => { release = resolve; }) : []);
    const pending = getNode(action === "preview" ? "#preview-import" : "#apply-import").emit("click");
    assert.equal(getNode("#audit-import-links").disabled, true);
    await getNode("#audit-import-links").emit("click");
    assert.equal(requests.some(({ url }) => url.endsWith("/links/audit")), false);
    release(action === "preview" ? { files: records } : { imported: 1, skipped: 0, errors: [] });
    await pending;
    assert.equal(getNode("#import-package").disabled, false);
  }
});

test("attachment, missing and sensitive hints render with a local human-readable report path", async () => {
  const { preview, select, getNode, setResponse } = await harness();
  await preview();
  await select("sample-1.md");
  setResponse(() => ({ ...emptyAudit,
    summary: { notes: 1, links: 5, needs_review: 4, unresolved: 2 },
    attachments: { referenced: 3, found: 1, missing: 1, restricted: 1, archive_count: 2, truncated: false, items: [] },
    sensitive_hints: { archive_entries: 1, restricted_targets: 2, note: "仅计数" },
    markdown_path: "<report>.md" }));
  await getNode("#audit-import-links").emit("click");
  const html = getNode("#import-link-audit-result").innerHTML;
  assert.match(html, /data-link-audit-summary/);
  assert.match(html, /附件：引用 3 个（存在 1 · 缺失 1）/);
  assert.match(html, /包内附件 2 个 · 敏感提示 3 条/);
  assert.match(html, /人话报告/);
  assert.match(html, /&lt;report&gt;\.md/);
  assert.doesNotMatch(html, /<a\b|href=|<script\b/);
});

test("older audit payloads without the new summary keep the previous layout", async () => {
  const { preview, select, getNode } = await harness();
  await preview();
  await select("sample-1.md");
  await getNode("#audit-import-links").emit("click");
  const html = getNode("#import-link-audit-result").innerHTML;
  assert.doesNotMatch(html, /data-link-audit-summary/);
  assert.doesNotMatch(html, /人话报告/);
  assert.match(html, /本地报告/);
});

test("older backend without attachment-plan support cannot issue unsupported requests", async () => {
  const { preview, select, getNode, requests } = await harness(false);
  await preview();
  await select("sample-1.md");
  assert.equal(getNode("#plan-import-attachments").disabled, true);
  assert.equal(getNode("#apply-attachment-plan").disabled, true);
  assert.equal(getNode("#check-import-history").disabled, true);
  assert.match(getNode("#check-import-history").textContent, /待服务更新/);
  assert.equal(getNode("#preview-history-cleanup").disabled, true);
  assert.match(getNode("#preview-history-cleanup").textContent, /待服务更新/);
  assert.match(getNode("#plan-import-attachments").textContent, /待服务更新/);
  await getNode("#plan-import-attachments").emit("click");
  assert.equal(requests.some(({ url }) => url.endsWith("/attachments/plan")), false);
});

test("attachment plan renders a read-only preview with copy targets, skip reasons and escaping", async () => {
  const { preview, select, getNode, requests, setResponse } = await harness();
  await preview();
  await select("sample-1.md");
  setResponse((url) => url.endsWith("/attachments/plan") ? {
    summary: { notes: 1, attachments: 2, unique_attachments: 1, copy_bytes: 8192, supported: 1, skipped: 1,
      notes_ready: 0, notes_needing_import: 1, notes_blocked: 0 },
    notes: [{ source: "<note>.md", vault_path: "80-Wolai/wolai_x.md", state: "not-imported",
      state_reason: "先在导入页导入这篇记录，再执行附件本地化。", plannable: false, attachments: 2, supported: 1, skipped: 1,
      items: [
        { line: 3, target: "../assets/<img>.png", resolved_path: "assets/<img>.png", size: 4, supported: true,
          skipped_reason: null, copy_to: "80-Wolai/attachments/abc-image.png", new_target: "attachments/abc-image.png" },
        { line: 4, target: "assets/big.bin", resolved_path: "assets/big.bin", size: 99, supported: false,
          skipped_reason: "单个附件超过 25 MiB，第一批不本地化。", copy_to: null, new_target: null },
      ] }],
    warnings: ["<warning>"], writes: false, note: "预览读取附件字节只为计算 SHA-256。",
  } : url.endsWith("/links/audit") ? emptyAudit : {});
  await getNode("#plan-import-attachments").emit("click");
  const html = getNode("#import-attachment-plan-result").innerHTML;
  assert.match(html, /可本地化附件<\/dt><dd>1/);
  assert.match(html, /计划复制<\/dt><dd>8\.0 KB/);
  assert.match(html, /跳过<\/dt><dd>1/);
  assert.match(html, /待导入笔记<\/dt><dd>1/);
  assert.match(html, /尚未导入/);
  assert.match(html, /&lt;note&gt;\.md/);
  assert.match(html, /&lt;img&gt;\.png/);
  assert.match(html, /80-Wolai\/attachments\/abc-image\.png/);
  assert.match(html, /链接改为 <code>attachments\/abc-image\.png<\/code>/);
  assert.match(html, /超过 25 MiB/);
  assert.match(html, /&lt;warning&gt;/);
  assert.match(html, /不会复制附件、不改写原文/);
  assert.equal(getNode("#import-attachment-plan-status").textContent, "附件计划已生成；未复制附件、未修改原文。");
  const request = requests.find(({ url }) => url.endsWith("/attachments/plan"));
  assert.equal(request.options.method, "POST");
  assert.deepEqual(JSON.parse(request.options.body), { selected_paths: ["sample-1.md"] });
  assert.equal(requests.some(({ url }) => /\/apply$|\/chat|\/model/.test(url)), false);
  // 小于 1 KB 的附件用字节显示，避免“0.0 KB”看起来像没有内容。
  setResponse((url) => url.endsWith("/attachments/plan") ? {
    summary: { supported: 1, skipped: 0, copy_bytes: 41, notes_needing_import: 0, notes_blocked: 0 },
    notes: [], warnings: [], writes: false,
  } : {});
  await getNode("#plan-import-attachments").emit("click");
  assert.match(getNode("#import-attachment-plan-result").innerHTML, /计划复制<\/dt><dd>41 B/);
  assert.equal(getNode("#apply-attachment-plan").disabled, true); // 该计划不可执行
  // 改变选择会清掉旧计划，避免被误认为新选择的结论。
  await select("sample-2.md");
  assert.equal(getNode("#import-attachment-plan-result").innerHTML, "");
});

test("localize apply needs a confirmation and renders the outcome", async () => {
  const h = await harness();
  await h.preview();
  await h.select("sample-1.md");
  const plan = {
    summary: { supported: 1, skipped: 0, copy_bytes: 8, notes_needing_import: 0, notes_blocked: 0 },
    notes: [{ source: "sample-1.md", vault_path: "80-Wolai/wolai_x.md", state: "ready", state_reason: null,
      plannable: true, attachments: 1, supported: 1, skipped: 0,
      items: [{ line: 2, target: "../assets/pic.png", resolved_path: "assets/pic.png", size: 8, supported: true,
        skipped_reason: null, copy_to: "80-Wolai/attachments/abc-pic.png", new_target: "attachments/abc-pic.png" }] }],
    warnings: [], writes: false,
  };
  h.setResponse((url) => url.endsWith("/attachments/plan") ? plan : url.endsWith("/attachments/apply") ? {
    counts: { notes_localized: 1, notes_skipped: 0, attachments_referenced: 1, attachments_copied: 1, attachments_reused: 0, bytes_copied: 8 },
    notes: [{ source: "sample-1.md", items: [{ line: 2, target: "../assets/pic.png", new_target: "attachments/abc-pic.png" }] }],
    skipped_notes: [], writes: true,
  } : []);
  await h.getNode("#plan-import-attachments").emit("click");
  assert.equal(h.getNode("#apply-attachment-plan").disabled, false);

  // 取消确认时不发执行请求。
  await h.getNode("#apply-attachment-plan").emit("click");
  assert.equal(h.requests.some(({ url }) => url.endsWith("/attachments/apply")), false);
  assert.equal(h.confirmations.length, 1);
  assert.match(h.confirmations[0].message, /原 ZIP 与 Wolai 原空间不动/);

  h.decisions.push(true);
  await h.getNode("#apply-attachment-plan").emit("click");
  const request = h.requests.find(({ url }) => url.endsWith("/attachments/apply"));
  assert.equal(request.options.method, "POST");
  assert.deepEqual(JSON.parse(request.options.body), { selected_paths: ["sample-1.md"] });
  assert.match(h.getNode("#import-attachment-plan-status").textContent, /本地化完成：1 篇笔记/);
  const html = h.getNode("#import-attachment-plan-result").innerHTML;
  assert.match(html, /已本地化笔记<\/dt><dd>1/);
  assert.match(html, /新复制附件<\/dt><dd>1/);
  assert.match(html, /attachments\/abc-pic\.png/);
  assert.match(html, /\.wolai-import-history\//);
  assert.equal(h.getNode("#apply-attachment-plan").disabled, true); // 执行后需要重新生成计划
});

test("import history lists versions and restores one only after confirmation", async () => {
  const h = await harness();
  await h.preview();
  await h.select("sample-1.md");
  const older = "a".repeat(64);
  const current = "b".repeat(64);
  h.setResponse((url) => url.endsWith("/history") ? {
    summary: { notes: 1, versions: 2, notes_missing: 0, notes_unmanaged: 0 }, writes: false,
    notes: [{ source: "<note>.md", vault_path: "80-Wolai/wolai_x.md", state: "managed",
      current_sha256: current, index_state: "current",
      versions: [
        { sha256: older, bytes: 4096, modified_at: "2026-10-05T03:00:00+08:00", current: false },
        { sha256: current, bytes: 2048, modified_at: "2026-10-05T04:00:00+08:00", current: true },
      ] }],
  } : url.endsWith("/history/restore") ? {
    previous_sha256: current, restored_sha256: older, previous_archived_path: `.wolai-import-history/x/${current}.md`,
    state_before: "managed", writes: true,
  } : []);
  await h.getNode("#check-import-history").emit("click");
  const request = h.requests.find(({ url }) => url.endsWith("/history"));
  assert.equal(request.options.method, "POST");
  assert.deepEqual(JSON.parse(request.options.body), { selected_paths: ["sample-1.md"] });
  const html = h.getNode("#import-history-result").innerHTML;
  assert.match(html, /&lt;note&gt;\.md/);
  assert.match(html, /受管文件/);
  assert.match(html, /aaaaaaaaaaaa/);
  assert.match(html, /4096 字节 · 2026-10-05 03:00/);
  assert.match(html, /当前<\/span>/);
  assert.match(html, /索引已更新/);
  assert.match(html, /无需恢复/);
  assert.equal((html.match(/data-history-restore/g) || []).length, 1);
  assert.equal((html.match(/data-history-scan/g) || []).length, 0);
  assert.match(h.getNode("#import-history-status").textContent, /2 个历史版本/);

  const restoreButton = { dataset: { historyRestore: older, historySource: "sample-1.md" }, closest() { return this; } };
  await h.getNode("#import-history-result").emit("click", { target: restoreButton });
  assert.equal(h.requests.some(({ url }) => url.endsWith("/history/restore")), false);
  assert.equal(h.confirmations.length, 1);
  h.decisions.push(true);
  await h.getNode("#import-history-result").emit("click", { target: restoreButton });
  const restoreRequest = h.requests.find(({ url }) => url.endsWith("/history/restore"));
  assert.equal(restoreRequest.options.method, "POST");
  assert.deepEqual(JSON.parse(restoreRequest.options.body), { source_path: "sample-1.md", sha256: older });
  assert.match(h.getNode("#import-history-status").textContent, /已恢复版本 aaaaaaaaaaaa；恢复前的内容已存入历史/);
  assert.equal(h.requests.filter(({ url }) => url.endsWith("/history")).length, 2);
});

test("a stale index offers an in-place scan that refreshes candidates", async () => {
  const h = await harness();
  await h.preview();
  await h.select("sample-1.md");
  let historyCalls = 0;
  h.setResponse((url) => {
    if (url.endsWith("/history")) {
      historyCalls += 1;
      return {
        summary: { notes: 1, versions: 1, notes_missing: 0, notes_unmanaged: 0, notes_stale: historyCalls === 1 ? 1 : 0 },
        writes: false,
        notes: [{ source: "sample-1.md", vault_path: "80-Wolai/wolai_x.md", state: "managed",
          index_state: historyCalls === 1 ? "stale" : "current", current_sha256: "c".repeat(64),
          versions: [{ sha256: "c".repeat(64), bytes: 10, modified_at: "2026-10-05T03:00:00+08:00", current: true }] }],
      };
    }
    if (url === "/api/vault/scan") return { scanned: 5, changed: 1, skipped: 4, excluded: 0, invalidated: 0, errors: [] };
    return [];
  });
  await h.getNode("#check-import-history").emit("click");
  const firstHtml = h.getNode("#import-history-result").innerHTML;
  assert.match(firstHtml, /索引待刷新/);
  assert.match(firstHtml, /data-history-scan/);
  const scanButton = { closest(selector) { return selector === "[data-history-scan]" ? this : null; } };
  await h.getNode("#import-history-result").emit("click", { target: scanButton });
  const scanRequest = h.requests.find(({ url }) => url === "/api/vault/scan");
  assert.equal(scanRequest.options.method, "POST");
  assert.equal(h.requests.filter(({ url }) => url.endsWith("/history")).length, 2);
  assert.match(h.getNode("#import-history-status").textContent, /扫描完成：扫描 5 · 更新 1 · 跳过 4/);
  assert.doesNotMatch(h.getNode("#import-history-result").innerHTML, /data-history-scan/);
});

test("history cleanup preview lists retention without any delete action", async () => {
  const h = await harness();
  await h.preview();
  await h.select("sample-1.md");
  h.setResponse((url) => url.endsWith("/history/cleanup-preview") ? {
    policy: { keep_newest: 3, min_age_days: 30 },
    summary: { notes: 1, versions: 6, bytes: 205, cleanable: 2, cleanable_bytes: 130 },
    writes: false, note: "这只是预览：不会删除任何文件；是否执行清理仍等你决定。",
    notes: [{ source: "<note>.md", vault_path: "80-Wolai/wolai_x.md", versions: 6, bytes: 205, cleanable: 2, cleanable_bytes: 130,
      items: [
        { sha256: "a".repeat(64), bytes: 10, modified_at: "2026-10-04T03:00:00+08:00", current: false, age_days: 1, keep: true, reason: "最近 3 个版本之一，保留" },
        { sha256: "b".repeat(64), bytes: 90, modified_at: "2026-07-01T03:00:00+08:00", current: false, age_days: 90, keep: false, reason: "超过保留窗口且不是最近版本，可清理（预览）" },
      ] }],
    warnings: ["清理只会删除 .wolai-import-history/ 里的旧副本，不影响当前笔记、原 ZIP 或候选记忆。"],
  } : []);
  await h.getNode("#preview-history-cleanup").emit("click");
  const request = h.requests.find(({ url }) => url.endsWith("/history/cleanup-preview"));
  assert.equal(request.options.method, "POST");
  assert.deepEqual(JSON.parse(request.options.body), { selected_paths: ["sample-1.md"] });
  const html = h.getNode("#import-cleanup-result").innerHTML;
  assert.match(html, /历史版本<\/dt><dd>6/);
  assert.match(html, /可清理（预览）<\/dt><dd>2/);
  assert.match(html, /可释放<\/dt><dd>130 B/);
  assert.match(html, /&lt;note&gt;\.md/);
  assert.match(html, /最近 3 个版本之一，保留/);
  assert.match(html, /可清理（预览）/);
  assert.match(html, /不会删除任何文件/);
  assert.match(html, /这里没有任何删除按钮/);
  assert.doesNotMatch(html, /data-cleanup-delete|删除此版本|执行清理</);
  assert.equal(h.requests.some(({ url }) => /delete|cleanup-apply/.test(url)), false);
  assert.match(h.getNode("#import-cleanup-status").textContent, /可清理 2 个旧副本；未删除任何文件/);
  // 改变选择会清掉旧预览。
  await h.select("sample-2.md");
  assert.equal(h.getNode("#import-cleanup-result").innerHTML, "");
});
