import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Offline rendering checks for the read-only "认识我" summary. No network, no model, no data writes.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function harness(respond) {
  const nodes = new Map();
  const getNode = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, { id: selector, innerHTML: "", textContent: "", listeners: new Map(), addEventListener(type, callback) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(callback); }, async emit(type, values = {}) { const event = { target: this, preventDefault() {}, ...values }; for (const callback of this.listeners.get(type) || []) await callback(event); return event; }, querySelector: getNode, querySelectorAll: () => [] });
    return nodes.get(selector);
  };
  const requests = [];
  const document = {
    querySelector: getNode,
    querySelectorAll: () => [],
    addEventListener() {},
  };
  const context = vm.createContext({
    document, Intl, Date, console, AbortController, TypeError,
    window: { setTimeout: () => 1, clearTimeout() {}, addEventListener() {} },
    CustomEvent: class { constructor(type) { this.type = type; } },
    fetch: async (url, options) => {
      requests.push({ url, options });
      const result = await respond(url, options);
      return { ok: !result?.httpError, status: result?.httpError || 200, json: async () => result?.httpError ? { detail: result.message } : result };
    },
  });
  const modules = new Map();
  const pending = new Map();
  async function load(filename) {
    if (modules.has(filename)) return modules.get(filename);
    if (pending.has(filename)) return pending.get(filename);
    const promise = (async () => {
      const module = new vm.SourceTextModule(await readFile(filename, "utf8"), { context, identifier: filename });
      await module.link((specifier, referrer) => load(path.resolve(path.dirname(referrer.identifier), specifier)));
      await module.evaluate();
      modules.set(filename, module);
      pending.delete(filename);
      return module;
    })();
    pending.set(filename, promise);
    return promise;
  }
  const profile = (await load(path.join(frontend, "profile.js"))).namespace;
  profile.initProfile();
  return { profile, getNode, requests };
}

const sampleProfile = {
  generated_at: "2026-10-04T12:00:00+08:00",
  version: "0.5.0",
  documents: { available: 3, personal: 3, other_privacy: 0 },
  memories: { active: 2, candidate: 5, rejected: 1, archived: 0, active_usable: 1, active_blocked: { source_unavailable: 1, not_personal: 0, stale_source: 0, expired: 0 }, by_kind: { fact: 1 } },
  active_memories: [
    { id: 7, document_id: 2, kind: "fact", title: "<b>职业</b>", content: "工程师 <script>alert(1)</script>", confidence: "confirmed", revision: 1, updated_at: "2026-10-01T10:00:00+08:00", source_path: "01/profile.md", source_date: "2026-09-01", source_start_line: 3, source_end_line: 4, source_location_basis: "document_body", usage: { uses: 2, last_used_at: "2026-10-04T09:30:00+00:00" } },
  ],
  usage: { window_days: 30, memories_used: 1, recent: [{ memory_id: 7, message_id: 12, conversation_id: 3, used_at: "2026-10-04T09:30:00+00:00", title: "职业 <script>", kind: "fact", status: "active", conversation_title: "关于我", question: "我是谁？<img src=x>" }] },
  candidate_pool: { total: 5, by_source: [{ path: "90-Inbox/杂记.md", count: 3 }, { path: "01/profile.md", count: 2 }], by_source_progress: [{ path: "90-Inbox/杂记.md", candidate: 3, reviewed: 1, total: 4 }, { path: "01/profile.md", candidate: 2, reviewed: 0, total: 2 }], by_month: [{ month: "2026-10", count: 5 }], by_record_month: [{ month: "2026-09", count: 3 }], record_dated_total: 3, record_unknown_total: 2, record_basis: "按来源元数据日期或文件名/路径中的 YYYY-MM-DD 解析；解析不到的计入未标注", oldest_extracted_at: "2026-10-01T00:00:00+00:00", newest_extracted_at: "2026-10-04T00:00:00+00:00", basis: "按提取时间统计，非原始记录日期" },
  goals: { active: 1, paused: 0, completed: 2, open_tasks: 3, completed_tasks: 4, items: [{ id: 1, title: "考<研>", status: "active", progress: 40 }] },
  rhythm: { journal_days_last_7: 2, body_record_days_last_7: 3, habits_active: 1, habit_done_last_7: 3, study_minutes_last_7: 90, study_subjects_active: 2 },
  usage_rules: ["只有已确认且来源有效的记忆会参与回答。", "每次回答最多使用 6 条最相关的证据。"],
  excluded: { candidate: 5, rejected: 1, archived: 0 },
  relationship_vault: { connected: false, note: "关系数据分区尚未接入。" },
  library_health: {
    documents: 4,
    recent: [{ title: "资料 <b>甲</b>", indexed_at: "2026-10-04T10:00:00+08:00" }],
    vault: { exists: true, looks_like_obsidian: true, markdown_files: 9, read_only: true, skipped_directories: [".obsidian"] },
    audit: { checked: 9, ok: 8, issues: 1, truncated: 0, by_code: { missing_fields: 1 }, generated_at: "2026-10-05T00:00:00+08:00" },
    note: "只读摘要：不含正文、不含具体问题路径；体检不会修改或修复任何文件。",
  },
};

test("the library health panel shows counts, codes and escapes titles", async () => {
  const { profile, getNode } = await harness(() => structuredClone(sampleProfile));
  await profile.loadProfile();
  const html = getNode("#profile-body").innerHTML;
  assert.match(html, /资料健康度/);
  assert.match(html, /已索引资料/);
  assert.match(html, /只读/);
  assert.match(html, /体检问题/);
  assert.match(html, /字段缺失 1/);
  assert.match(html, /资料 &lt;b&gt;甲&lt;\/b&gt;/);
  assert.doesNotMatch(html, /资料 <b>甲<\/b>/);
  assert.match(html, /不含正文/);
});

test("profile page renders escaped metrics, memories and rhythm from the API", async () => {
  const { profile, getNode, requests } = await harness(() => structuredClone(sampleProfile));
  await profile.loadProfile();
  assert.equal(requests[0].url, "/api/profile");
  assert.equal(requests[0].options.method, undefined);
  const html = getNode("#profile-body").innerHTML;
  assert.match(html, /已确认可用记忆/);
  assert.match(html, /<strong>1<\/strong>/);
  assert.match(html, /&lt;b&gt;职业&lt;\/b&gt;/);
  assert.doesNotMatch(html, /<b>职业<\/b>|<script>/);
  assert.match(html, /考&lt;研&gt;/);
  assert.match(html, /data-document-id="2"/);
  assert.match(html, /data-ask-question="我是谁？"/);
  assert.match(html, /来源不可用 1/);
  assert.match(html, /90/);
  assert.match(html, /近 30 天被 2 次回答引用/);
  assert.match(html, /回答使用记录/);
  assert.match(html, /我是谁？&lt;img src=x&gt;/);
  assert.doesNotMatch(html, /<img src=x>/);
  assert.match(html, /90-Inbox\/杂记\.md/);
  assert.match(html, /3 待确认 \/ 共 4（已审核 1）/);
  assert.match(html, /按提取时间统计，非原始记录日期/);
  assert.match(html, /按原始记录月份/);
  assert.match(html, /2026-09/);
  assert.match(html, /未标注 2 条/);
  assert.match(html, /身体记录/);
  assert.equal(getNode("#profile-status").textContent.includes("待确认 5"), true);
  assert.equal(getNode("#profile-status").textContent.includes("近 30 天被引用 1 条"), true);
});

test("profile failures surface a retryable escaped error and refresh re-reads", async () => {
  let fail = true;
  const { profile, getNode, requests } = await harness(() => fail ? { httpError: 500, message: "<bad> 服务错误" } : structuredClone(sampleProfile));
  await profile.loadProfile();
  assert.match(getNode("#profile-body").innerHTML, /&lt;bad&gt;|服务错误/);
  assert.doesNotMatch(getNode("#profile-body").innerHTML, /<bad>/);
  assert.match(getNode("#profile-body").innerHTML, /data-retry="profile"/);
  fail = false;
  await profile.loadProfile();
  assert.equal(requests.filter(({ url }) => url === "/api/profile").length, 2);
  assert.match(getNode("#profile-body").innerHTML, /已确认可用记忆/);
});

test("late profile responses cannot overwrite a newer request", async () => {
  let release;
  let call = 0;
  const { profile, getNode } = await harness((url) => {
    call += 1;
    if (call === 1) return new Promise((resolve) => { release = () => resolve({ ...structuredClone(sampleProfile), memories: { ...sampleProfile.memories, active_usable: 99 } }); });
    return Promise.resolve({ ...structuredClone(sampleProfile), memories: { ...sampleProfile.memories, active_usable: 1 } });
  });
  const first = profile.loadProfile();
  await profile.loadProfile();
  release();
  await first;
  assert.doesNotMatch(getNode("#profile-body").innerHTML, /<strong>99<\/strong>/);
  assert.match(getNode("#profile-body").innerHTML, /已确认可用记忆/);
});

test("profile tolerates a summary without usage or candidate-pool fields", async () => {
  const legacy = structuredClone(sampleProfile);
  delete legacy.usage;
  delete legacy.candidate_pool;
  legacy.active_memories = legacy.active_memories.map(({ usage, ...rest }) => rest);
  const { profile, getNode } = await harness(() => legacy);
  await profile.loadProfile();
  const html = getNode("#profile-body").innerHTML;
  assert.match(html, /近 30 天尚未被回答引用/);
  assert.match(html, /还没有保存过的回答引用已确认记忆/);
  assert.match(html, /没有待确认内容/);
  assert.doesNotMatch(html, /按原始记录月份/);
});

test("a 0.11 candidate pool keeps two columns and hides the record-month one", async () => {
  const legacy = structuredClone(sampleProfile);
  delete legacy.candidate_pool.by_record_month;
  delete legacy.candidate_pool.by_source_progress;
  delete legacy.candidate_pool.record_dated_total;
  delete legacy.candidate_pool.record_unknown_total;
  delete legacy.candidate_pool.record_basis;
  const { profile, getNode } = await harness(() => legacy);
  await profile.loadProfile();
  const html = getNode("#profile-body").innerHTML;
  assert.match(html, /按来源/);
  assert.match(html, /按提取月份/);
  assert.match(html, /3 待确认(?! \/ 共)/);
  assert.doesNotMatch(html, /按原始记录月份/);
  assert.doesNotMatch(html, /未标注 0 条/);
});

const sampleActivity = {
  generated_at: "2026-10-04T12:00:00+08:00",
  window: { days: 30, start: "2026-09-05", end: "2026-10-04" },
  journal: { days_recorded: 12 },
  body: { days_recorded: 8 },
  habits: { days_recorded: 20, done: 45, skipped: 3, active_definitions: 2 },
  study: { days_recorded: 10, minutes: 320, subjects_recorded: 2 },
  notes: ["只统计你在这段时间实际填写过的记录，空白日期不代表没有发生。", "<b>不评价</b>也不打分。"],
};

test("profile renders the 30-day coverage facts without scores", async () => {
  const { profile, getNode, requests } = await harness((url) => url.startsWith("/api/profile/activity")
    ? structuredClone(sampleActivity)
    : structuredClone(sampleProfile));
  await profile.loadProfile();
  assert.equal(requests[0].url, "/api/profile");
  assert.equal(requests[1].url, "/api/profile/activity?days=30");
  const html = getNode("#profile-body").innerHTML;
  assert.match(html, /近 30 天记录覆盖面/);
  assert.match(html, /窗口：2026-09-05 至 2026-10-04/);
  assert.match(html, /每日记录<\/span><strong>12<\/strong><small>天<\/small>/);
  assert.match(html, /身体记录<\/span><strong>8<\/strong><small>天<\/small>/);
  assert.match(html, /习惯记录<\/span><strong>20<\/strong><small>天<\/small>/);
  assert.match(html, /学习记录<\/span><strong>10<\/strong><small>天<\/small>/);
  assert.match(html, /学习时长<\/span><strong>320<\/strong><small>分钟<\/small>/);
  assert.match(html, /进行中习惯 2 个 · 明确跳过 3 次 · 学习涉及科目 2 个/);
  assert.match(html, /&lt;b&gt;不评价&lt;\/b&gt;也不打分。/);
  assert.doesNotMatch(html, /<b>不评价<\/b>/);
  assert.doesNotMatch(html, /完成率|得分|连续天数/);
});

test("a failed activity request keeps the rest of the profile usable", async () => {
  const { profile, getNode } = await harness((url) => url.startsWith("/api/profile/activity")
    ? { httpError: 500, message: "统计不可用" }
    : structuredClone(sampleProfile));
  await profile.loadProfile();
  const html = getNode("#profile-body").innerHTML;
  assert.match(html, /已确认可用记忆/);
  assert.match(html, /暂时无法读取近 30 天统计/);
  assert.doesNotMatch(html, /统计不可用</);
});
