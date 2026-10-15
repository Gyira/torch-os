import test from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 对话统一交给 dsh（用户 2026-10-05 定调）。这条守卫锁住这个决定：
// 产品里不许再长出第二个对话面——没有对话页、没有对话表单、不调用对话端点。

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function source(name) {
  return readFile(path.join(frontend, name), "utf8");
}

test("应用里没有任何自己的对话界面", async () => {
  const html = await source("index.html");
  assert.equal(html.includes('id="chat-view"'), false, "对话页应当已移除");
  assert.equal(html.includes('id="chat-form"'), false, "对话表单应当已移除");
  assert.equal(html.includes('id="chat-input"'), false, "对话输入框应当已移除");
  assert.equal(html.includes('id="chat-log"'), false, "对话记录区应当已移除");
  assert.equal(/data-view="chat"/.test(html), false, "侧边栏不应再有对话入口");
});

test("前端不再有对话模块，也不再调用对话端点", async () => {
  const files = await readdir(frontend);
  const modules = files.filter((name) => name.endsWith(".js"));
  assert.equal(modules.includes("chat.js"), false, "chat.js 应当已删除");
  assert.equal(modules.includes("chat-memory.js"), false, "chat-memory.js 应当已删除");
  for (const name of modules) {
    const text = await source(name);
    assert.equal(/["'`]\/api\/(chat|conversations|harness)\b/.test(text), false,
      `${name} 仍在调用对话端点`);
  }
});

test("导航清单里没有对话页", async () => {
  const nav = await source("nav-order.js");
  assert.equal(nav.includes('"chat"'), false, "默认顺序与标签都不应再含对话页");
  const labels = nav.match(/NAV_LABELS = \{([^}]*)\}/)[1];
  assert.equal(labels.includes("对话"), false);
});
