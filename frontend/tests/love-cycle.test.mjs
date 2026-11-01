import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 经期关怀是恋爱系统内部的新页面：不新增顶层模块，不依赖聊天项目，
// 不调用 AI/外部网络，也不写内联样式（工作台 CSP 只允许 self）。
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function source(name) {
  return readFile(path.join(frontend, name), "utf8");
}

test("经期关怀挂在恋爱系统导航里并加载独立样式", async () => {
  const html = await source("index.html");
  assert.match(html, /data-love-page="cycle"><span>04<\/span>经期关怀/);
  assert.match(html, /<link rel="stylesheet" href="\/assets\/love-cycle\.css"/);
  assert.match(html, /id="love-view"[^>]*data-module="ops"/);

  const navStart = html.indexOf('id="love-nav"');
  const loveNav = html.slice(navStart, html.indexOf("</nav>", navStart));
  assert.ok(loveNav.includes('data-love-page="cycle"'), "经期关怀必须在恋爱系统内部导航");
  assert.equal(html.includes('data-pane="cycle-view"'), false, "不应新增独立二级分区");
});

test("同意文案包含经期聚合边界与免责声明", async () => {
  const html = await source("index.html");
  const consent = html.slice(html.indexOf('id="love-consent"'), html.indexOf('id="health-view"'));
  assert.match(consent, /经期记录只在本机/);
  assert.match(consent, /阶段、预计日期等聚合信息/);
  assert.match(consent, /不发送每日症状明细/);
  assert.match(consent, /不是诊断/);
  assert.match(consent, /避孕依据/);
  assert.match(consent, /02_生活运营\/01_恋爱系统/);
});

test("恋爱系统注册经期页面与动作", async () => {
  const love = await source("love.js");
  assert.match(love, /import \{ renderLoveCycle, handleLoveCycleAction \} from "\.\/love-cycle\.js"/);
  assert.match(love, /cycle: "经期关怀"/);
  assert.match(love, /action\.startsWith\("cycle-"\)/);
  assert.match(love, /handleLoveCycleAction\(event, \{ rerender: renderLovePage \}\)/);
  assert.match(love, /love\.page === "cycle"/);
  assert.match(love, /CONSENT_KEY = "torch-os:love-consent-v3"/);
  assert.match(love, /renderLoveCycle\(content, \{/);
});

test("经期页面只调用本机经期接口，且不依赖聊天项目", async () => {
  const js = await source("love-cycle.js");
  for (const endpoint of [
    "/api/love/cycle/overview",
    "/api/love/cycle/profile",
    "/api/love/cycle/periods",
    "/api/love/cycle/daily/",
  ]) {
    assert.ok(js.includes(endpoint), `缺少接口：${endpoint}`);
  }
  assert.equal(js.includes("/api/love/projects"), false, "经期页面不能依赖聊天项目");
  assert.equal(js.includes("projectQuery"), false);
  assert.equal(/https?:\/\//.test(js), false, "经期页面不应直接访问外部服务");
  assert.equal(/style\s*=/.test(js), false, "CSP style-src 'self' 会阻止内联 style 属性");
  assert.equal(/<style\b/.test(js), false);
  assert.equal(js.includes("srcdoc"), false);
  assert.equal(/localStorage|sessionStorage/.test(js), false, "经期状态不写入浏览器存储");
});

test("纯模型不碰 DOM、存储或网络", async () => {
  const model = await source("love-cycle-model.js");
  assert.equal(/\bdocument\b/.test(model), false);
  assert.equal(/\blocalStorage\b/.test(model), false);
  assert.equal(/\bfetch\s*\(/.test(model), false);
  assert.match(model, /export function cycleTrendChart/);
  assert.match(model, /export function calendarDayClasses/);
});

test("经期样式包含日历、关怀卡片和响应式规则", async () => {
  const css = await source("love-cycle.css");
  for (const className of [
    "love-cycle-hero", "love-cycle-grid", "lc-day", "lc-chart-band",
    "love-cycle-dialog-form", "love-cycle-care-list", "love-cycle-history-row",
  ]) {
    assert.ok(css.includes(className), `缺少样式：${className}`);
  }
  assert.match(css, /@media \(max-width: 900px\)[\s\S]{0,220}love-cycle-care-grid/);
  assert.match(css, /@media \(max-width: 640px\)[\s\S]{0,220}lc-day/);
});

test("生活运营卡片与帮助文案提到经期关怀", async () => {
  const ops = await source("ops-model.js");
  assert.match(ops, /经期关怀/);
  const help = await source("help-content.js");
  assert.match(help, /经期关怀/);
  assert.match(help, /不是诊断/);
  assert.match(help, /不能作为避孕依据/);
});
