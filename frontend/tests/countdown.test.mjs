import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 倒计时组件的静态守卫：它必须挂在工作台右栏、由工作台初始化，
// 并且背景只走 CSSOM（工作台 CSP 不允许内联 style 属性）。

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function source(name) {
  return readFile(path.join(frontend, name), "utf8");
}

test("倒计时卡挂在工作的右侧栏", async () => {
  const html = await source("index.html");
  const sideStart = html.indexOf('<aside class="workbench-side">');
  const countdown = html.indexOf('id="workbench-countdown"');
  const agent = html.indexOf('<section id="agent-view"', sideStart);
  assert.ok(sideStart > 0, "找不到工作台右侧栏");
  assert.ok(countdown > sideStart && countdown < agent, "倒计时必须位于工作台右侧栏内");
  assert.match(html, /class="countdown-card"/);
  const workbench = await source("workbench.js");
  assert.match(workbench, /initCountdownWidget/);
});

test("倒计时不写内联样式，设置走本地存储与模型", async () => {
  const js = await source("countdown.js");
  assert.equal(/style\s*=/.test(js), false, "CSP style-src 'self' 会阻止内联 style 属性");
  assert.match(js, /style\.backgroundImage/);
  assert.match(js, /countdown-settings-btn/);
  assert.match(js, /localStorage/);
  assert.match(js, /defaultCountdown/);
  const model = await source("countdown-model.js");
  for (const name of ["countdownState", "normalizeCountdown", "saveCountdown", "loadCountdown"]) {
    assert.ok(model.includes(name), `模型缺少 ${name}`);
  }
});
