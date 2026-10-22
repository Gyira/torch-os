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
  "journal-calendar.js", "vault-audit.js",
];

const retiredEndpoints = [
  "/api/bazi", "/api/profile", "/api/suggestions", "/api/chat/preview", "/api/imports/share",
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
