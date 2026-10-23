import test from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 2026-10-23 产品调整：工作台内保留唯一的「与神对话」agent 页面；
// 旧的 ACTIVITY / 今日日记面板与前端日记模块已删除。

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function source(name) {
  return readFile(path.join(frontend, name), "utf8");
}

test("工作台里有且只有一个「与神对话」聊天面", async () => {
  const html = await source("index.html");
  assert.match(html, /id="agent-view"/);
  assert.match(html, /data-pane-label="与神对话"/);
  assert.match(html, /id="agent-form"/);
  assert.match(html, /id="agent-input"/);
  assert.match(html, /id="agent-messages"/);
  assert.equal(/id="journal-view"/.test(html), false, "旧日记面板应已移除");
  assert.equal(/今天你动过哪些文件|TODAY'S DIARY/.test(html), false, "旧的 ACTIVITY / 今日日记应已移除");
});

test("与神对话通过唯一端点接入 dsh", async () => {
  const chat = await source("agent-chat.js");
  assert.match(chat, /\/api\/chat\/stream/);
  assert.match(chat, /\/api\/agent\/cancel/);
  const files = await readdir(frontend);
  assert.equal(files.includes("journal.js"), false, "旧 journal.js 应已删除");
  assert.equal(files.includes("journal-note.js"), false, "旧 journal-note.js 应已删除");
});

test("「与神对话」是工作台分区，不是新的顶层模块", async () => {
  const nav = await source("nav-order.js");
  const labels = nav.match(/NAV_LABELS = \{([^}]*)\}/)[1];
  assert.equal(labels.includes("与神对话"), false, "顶层导航不应新增独立对话模块");
  assert.equal(labels.includes("日记"), false);
  const app = await source("app.js");
  assert.match(app, /\["agent-view", "与神对话"\]/);
});
