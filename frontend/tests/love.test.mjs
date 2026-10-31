import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 恋爱系统是「工作台」的二级分区，不是新的顶层模块。
// 同时守住两条边界：页面不写内联样式（工作台 CSP 只允许 self），
// 动画/报告等独立文档才允许由后端单独出页面。

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function source(name) {
  return readFile(path.join(frontend, name), "utf8");
}

test("恋爱系统挂在生活运营的二级菜单里，不在工作台", async () => {
  const html = await source("index.html");
  assert.match(html, /data-pane="love-view"/);
  assert.match(html, /id="love-view"/);
  assert.match(html, /data-module="ops"[^>]*data-pane-label="恋爱系统"|data-pane-label="恋爱系统"/);
  assert.match(html, /data-love-page="dashboard"/);
  assert.match(html, /data-love-page="cycle"><span>02<\/span>经期关怀/);
  assert.match(html, /data-love-page="settings"/);

  // 二级入口必须在生活运营的 nav-sublist 里，而不是工作台的。
  const workbenchSub = html.slice(html.indexOf('id="nav-sub-workbench"'), html.indexOf('id="nav-sub-ops"'));
  const opsSub = html.slice(html.indexOf('id="nav-sub-ops"'), html.indexOf('data-view="knowledge"'));
  assert.equal(workbenchSub.includes('data-pane="love-view"'), false);
  assert.ok(opsSub.includes('data-pane="love-view"'), "生活运营缺少恋爱系统二级入口");
  // 分区本身也必须属于 ops，否则 activatePane 找不到它。
  assert.match(html, /<section id="love-view"[^>]*data-module="ops"/);

  const nav = await source("nav-order.js");
  const labels = nav.match(/NAV_LABELS = \{([^}]*)\}/)[1];
  assert.equal(labels.includes("恋爱系统"), false, "顶层导航不应新增独立恋爱模块");

  const app = await source("app.js");
  assert.match(app, /ops: \{ stacked: false, panes: \[\["ops-view", "生活运营"\], \["love-view", "恋爱系统"\](, \["health-view", "健康系统"\])?\] \}/);
  const workbenchBlock = app.slice(app.indexOf("workbench: {"), app.indexOf("life: {"));
  assert.equal(workbenchBlock.includes("love-view"), false,
    "工作台的二级菜单不应再出现恋爱系统");
  assert.match(app, /"love-view": \(\) => \{ initLovePane\(\); loadLovePane\(\); \}/);
});

test("恋爱页面不违反工作台的内联样式 CSP", async () => {
  const love = await source("love.js");
  assert.equal(/style\s*=/.test(love), false, "CSP style-src 'self' 会阻止内联 style 属性");
  assert.equal(/<style\b/.test(love), false, "页面内不应写 <style>；报告由后端单独文档承载");
  assert.match(love, /widthClass/);
  assert.match(love, /love-width-/);
  // 报告预览必须走后端独立文档，而不是 srcdoc 继承工作台 CSP。
  assert.match(love, /\/report\/preview/);
  assert.equal(/srcdoc/.test(love), false);
});

test("恋爱系统只调用本机原生 API", async () => {
  const love = await source("love.js");
  for (const endpoint of [
    "/api/love/status",
    "/api/love/projects",
    "/analysis",
    "/journal",
    "/profile",
    "/report",
    "/schedule/",
    "/settings/api-key",
  ]) {
    assert.ok(love.includes(endpoint), `缺少接口：${endpoint}`);
  }
  const withoutLocal = love
    .replace(/https:\/\/api\.deepseek\.com\/v1/g, "")
    .replace(/http:\/\/127\.0\.0\.1:\$\{[^}]+\}/g, "")
    .replace(/http:\/\/127\.0\.0\.1:\d+/g, "");
  assert.equal(/https?:\/\//.test(withoutLocal), false,
    "恋爱页面不应直接访问外部服务；AI 调用与微信读取都在后端完成");
});

test("排班页是值班日历，不再包含车次时刻表/沿线天气", async () => {
  const html = await source("index.html");
  const js = await source("love.js");
  assert.match(html, /data-love-page="schedule"><span>11<\/span>值班日历/);
  assert.match(js, /schedule: "值班日历"/);
  assert.match(js, /\/api\/love\/schedule\/month/);
  assert.match(js, /\/api\/love\/schedule\/note/);
  assert.match(js, /day\.duty/);
  assert.match(js, /当天值乘/);
  assert.doesNotMatch(js, /交路|时刻表|沿线天气|查看排班表|schedule_timetable/);
  assert.doesNotMatch(js, /\/api\/love\/schedule\/(timetable|train)/);

  const start = html.indexOf('id="love-view"');
  const end = html.indexOf('id="knowledge-view"', start);
  const loveMarkup = html.slice(start, end);
  assert.doesNotMatch(loveMarkup, /train-tracker|列车追踪/);
});

test("微信同步与每日记忆入口和接口齐全", async () => {
  const html = await source("index.html");
  const js = await source("love.js");
  assert.match(html, /data-love-page="wechat"><span>04<\/span>微信同步/);
  assert.match(html, /data-love-page="memory"><span>09<\/span>每日记忆/);
  assert.match(js, /wechat: "微信同步"/);
  assert.match(js, /memory: "每日记忆"/);
  for (const endpoint of [
    "/api/love/weflow/status",
    "/api/love/weflow/settings",
    "/api/love/weflow/sessions",
    "/api/love/weflow/connect",
    "/api/love/weflow/project",
    "/api/love/weflow/sync",
    "/api/love/weflow/import-preview",
    "/api/love/weflow/import-file",
    "/api/love/memory/days",
    "/api/love/memory/generate",
    "/api/love/memory/drafts",
    "/write",
  ]) {
    assert.ok(js.includes(endpoint), `缺少接口：${endpoint}`);
  }
  assert.match(js, /02_生活运营\/05_情感与记忆/);
  // Token 只提交给本机后端，不进入 localStorage / sessionStorage。
  assert.doesNotMatch(js, /(?:localStorage|sessionStorage)[^\n]{0,80}(?:token|Token)/);
  assert.doesNotMatch(js, /Access Token[^\n]{0,80}writeStorage/);
});

test("每日记忆与微信同步有响应式样式", async () => {
  const css = await source("styles.css");
  for (const className of [
    "love-memory-layout", "love-memory-day", "love-memory-textarea",
    "love-sync-result", "love-divider",
  ]) {
    assert.ok(css.includes(className), `缺少样式：${className}`);
  }
  assert.match(css, /@media \(max-width: 900px\)[\s\S]{0,400}love-memory-layout/);
});
