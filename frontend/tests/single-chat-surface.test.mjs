import test from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 2026-10-25：「与神对话」= 完整的 DeepSeek Harness Web UI（同源 /harness/）。
// 旧的 torch 自建消息列表/输入框已经删除。

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function source(name) {
  return readFile(path.join(frontend, name), "utf8");
}

test("工作台里有且只有一个「与神对话」dsh 面板", async () => {
  const html = await source("index.html");
  assert.match(html, /id="agent-view"/);
  assert.match(html, /data-pane-label="与神对话"/);
  assert.match(html, /id="dsh-frame"/);
  assert.match(html, /id="dsh-statusbar"/);
  assert.match(html, /id="dsh-overlay"/);
  assert.equal(/id="agent-form"/.test(html), false, "旧自建输入框应已移除");
  assert.equal(/id="agent-messages"/.test(html), false, "旧自建消息列表应已移除");
  assert.equal(/id="model-key-form"/.test(html), false, "模型密钥表单应已移除");
});

test("与神对话通过同源代理加载 dsh", async () => {
  const pane = await source("dsh-pane.js");
  assert.match(pane, /\/harness\//);
  assert.match(pane, /\/api\/dsh\/status/);
  assert.match(pane, /\/api\/dsh\/start/);
  assert.match(pane, /\/api\/dsh\/restart/);
  assert.doesNotMatch(pane, /MODEL_API_KEY|model-key/);
  const files = await readdir(frontend);
  assert.equal(files.includes("agent-chat.js"), false, "旧 agent-chat.js 应已删除");
  assert.equal(files.includes("harness-bridge.js"), false, "旧 harness-bridge.js 应已删除");
});

test("dsh 官网入口仍然保留", async () => {
  const html = await source("index.html");
  assert.match(html, /https:\/\/www\.deepseek\.com\/harness\//);
  assert.match(html, /rel="noopener noreferrer"/);
});

test("「与神对话」是工作台分区，不是新的顶层模块", async () => {
  const nav = await source("nav-order.js");
  const labels = nav.match(/NAV_LABELS = \{([^}]*)\}/)[1];
  assert.equal(labels.includes("与神对话"), false, "顶层导航不应新增独立对话模块");
  const app = await source("app.js");
  assert.match(app, /\["agent-view", "与神对话"\]/);
});

test("与神对话嵌在待办事项下方，右侧留给小组件，二级菜单只做页面定位", async () => {
  const html = await source("index.html");
  const workbenchStart = html.indexOf('id="workbench-view"');
  const timeStart = html.indexOf('id="workbench-time"');
  const agentStart = html.indexOf('<section id="agent-view"');
  const sideStart = html.indexOf('workbench-side-right');
  const lifeStart = html.indexOf('<section id="life-view"');
  assert.ok(workbenchStart > 0, "找不到工作台视图");
  assert.ok(timeStart > workbenchStart, "找不到待办事项主界面");
  assert.ok(agentStart > timeStart && agentStart < sideStart && agentStart < lifeStart,
    "与神对话要在待办事项之后、右侧小组件之前，仍位于工作台主区");
  assert.match(html, /id="agent-view"[^>]*aria-label="与神对话"/);

  const app = await source("app.js");
  assert.match(app, /workbench: \{[\s\S]*?stacked: true,/);
  assert.match(app, /syncModuleNavActive/);
  assert.match(app, /scheduleWorkbenchAnchor/);

  const css = await source("styles.css");
  assert.match(css, /--workbench-side-width: 248px/);
  assert.match(css, /\.workbench-head-row \{[^}]*var\(--workbench-side-width\)/);
  assert.match(css, /\.workbench-agent \{[^}]*margin-top/);
  assert.match(css, /\.workbench-agent \{[^}]*scroll-margin-top/);
});
