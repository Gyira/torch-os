import test from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir, access } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const root = path.resolve(frontend, "..");

const deletedModules = [
  "identity.js", "profile.js", "memories.js", "memory-text.js", "goals.js",
  "task-view.js", "bazi.js", "body-records.js", "habits.js", "imports.js",
  "share-import.js", "library.js", "second-brain.js", "journal-review.js",
  "journal-ritual.js", "journal-ritual-model.js",
  "journal-calendar.js", "vault-audit.js", "agent-chat.js", "harness-bridge.js",
];

const retiredEndpoints = [
  "/api/bazi", "/api/profile", "/api/suggestions", "/api/chat", "/api/agent/cancel",
  "/api/config/model-key", "/api/steward/ask", "/api/workbench/summarize", "/api/imports/share",
];

async function liveSources() {
  const entries = await readdir(frontend, { withFileTypes: true });
  const files = entries
    .filter((entry) => entry.isFile() && /\.(js|html|css)$/.test(entry.name))
    .map((entry) => entry.name);
  return Promise.all(files.map(async (name) => ({ name, text: await readFile(path.join(frontend, name), "utf8") })));
}

test("retired frontend modules are gone and no live source imports them", async () => {
  for (const name of deletedModules) {
    await assert.rejects(access(path.join(frontend, name)), `${name} should be deleted`);
  }
  const sources = await liveSources();
  for (const { name, text } of sources) {
    for (const module of deletedModules) {
      const escaped = module.replace(".", "\\.");
      const asImport = new RegExp(`from\\s+["'](?:[^"']*[\\\\/])?${escaped}["']`);
      const dynamic = new RegExp(`import\\(\\s*["'](?:[^"']*[\\\\/])?${escaped}["']\\s*\\)`);
      assert.equal(asImport.test(text) || dynamic.test(text), false, `${name} still imports ${module}`);
    }
  }
});

test("live frontend does not call retired endpoints", async () => {
  const sources = await liveSources();
  for (const { name, text } of sources) {
    for (const endpoint of retiredEndpoints) {
      assert.equal(text.includes(endpoint), false, `${name} still references ${endpoint}`);
    }
  }
});

// 2026-10-26：四库只读。前端不再调用任何四库写接口，也不再渲染编辑控件。
const fourLibraryWrites = [
  "/api/capture", "/api/notes/update", "/api/notes/move",
  "/api/reviews/weekly", "/api/reviews/monthly",
  "/api/steward/proposals/apply", "/api/journal/note/generate",
];
const retiredEditHooks = [
  "data-pillar-capture", "data-steward-apply", "data-inbox-apply",
  "data-ops-form", "data-ops-delete",
];

test("four-library UI stays read-only and only jumps to Obsidian", async () => {
  const sources = await liveSources();
  for (const { name, text } of sources) {
    for (const endpoint of fourLibraryWrites) {
      assert.equal(text.includes(endpoint), false, `${name} still calls ${endpoint}`);
    }
    for (const hook of retiredEditHooks) {
      assert.equal(text.includes(hook), false, `${name} still renders ${hook}`);
    }
  }
  const pillars = await readFile(path.join(frontend, "pillars.js"), "utf8");
  assert.ok(pillars.includes("createObsidianNote"), "四库应通过 Obsidian 跳转新建笔记");
  assert.ok(pillars.includes("在 Obsidian 新建"));
  const steward = await readFile(path.join(frontend, "pillar-steward.js"), "utf8");
  assert.equal(steward.includes("/apply"), false, "管家不能再执行提案");
});

test("the tonight journal review entry stays retired", async () => {
  const sources = await liveSources();
  for (const { name, text } of sources) {
    assert.equal(text.includes("data-journal-review"), false, `${name} still renders the tonight review entry`);
    assert.equal(text.includes("今晚复盘"), false, `${name} still mentions 今晚复盘`);
  }
});

test("referenced docs exist and the tool count is 12", async () => {
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  const docs = new Set([...html.matchAll(/docs\/[A-Za-z0-9_./-]+\.(?:md|yml|yaml)/g)].map((match) => match[0]));
  assert.ok(docs.size > 0, "index.html should reference at least one docs file");
  for (const relative of docs) {
    await access(path.join(root, relative));
  }
  assert.ok(html.includes("这 12 个只读工具"), "tool card should say 12");
  assert.equal(html.includes("torch-tools.cordis.yml"), false, "missing Cordis YAML must not be referenced");
});

test("live copy uses the four-pillar vocabulary and keeps legacy hash redirects", async () => {
  const sources = await liveSources();
  for (const { name, text } of sources) {
    assert.equal(text.includes("数字分身"), false, `${name} still mentions 数字分身`);
    assert.equal(text.includes("第二大脑"), false, `${name} still mentions 第二大脑`);
    assert.equal(text.includes("分享导入"), false, `${name} still mentions 分享导入`);
  }
  const workbench = await readFile(path.join(frontend, "workbench.js"), "utf8");
  assert.ok(workbench.includes("人生路"));
  const app = await readFile(path.join(frontend, "app.js"), "utf8");
  assert.match(app, /today:\s*\["workbench",\s*""\]/);
});
