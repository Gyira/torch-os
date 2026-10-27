import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function loadModel() {
  const context = vm.createContext({});
  const filename = path.join(frontend, "deepseek-balance-model.js");
  const module = new vm.SourceTextModule(await readFile(filename, "utf8"), {
    context, identifier: filename,
  });
  await module.link(() => { throw new Error("no imports expected"); });
  await module.evaluate();
  return module.namespace;
}

function fakeStorage() {
  const map = new Map();
  return {
    getItem: (key) => map.has(key) ? map.get(key) : null,
    setItem: (key, value) => map.set(key, String(value)),
    removeItem: (key) => map.delete(key),
  };
}

test("amount, currency and time formatting stay safe when fields are missing", async () => {
  const {
    formatBalanceAmount, formatBalanceTime,
  } = await loadModel();
  assert.match(formatBalanceAmount("34.74", "CNY"), /34\.74/);
  assert.match(formatBalanceAmount("34.74", "CNY"), /[¥￥]/);
  assert.match(formatBalanceAmount(1, "USD"), /1\.00/);
  assert.equal(formatBalanceAmount(null), "—");
  assert.equal(formatBalanceAmount("n/a"), "—");
  assert.equal(formatBalanceAmount("12.3", "NOT_A_CURRENCY"), "NOT_A_CURRENCY 12.30");
  assert.equal(formatBalanceTime("2026-10-27T09:24:00+08:00"), "09:24");
  assert.equal(formatBalanceTime("bad"), "");
  assert.equal(formatBalanceTime(""), "");
});

test("alert threshold is local-only and clamps to a sane range", async () => {
  const {
    DEEPSEEK_ALERT_KEY, isLowBalance, normalizeAlertThreshold,
    readAlertThreshold, writeAlertThreshold,
  } = await loadModel();
  assert.equal(normalizeAlertThreshold("10.456"), 10.46);
  assert.equal(normalizeAlertThreshold(-5), 0);
  assert.equal(normalizeAlertThreshold("abc"), 0);
  assert.equal(normalizeAlertThreshold(2_000_000_000), 1_000_000_000);
  assert.equal(isLowBalance(10, 10), true);
  assert.equal(isLowBalance(10.01, 10), false);
  assert.equal(isLowBalance(0, 0), false);
  assert.equal(isLowBalance("n/a", 10), false);

  const storage = fakeStorage();
  assert.equal(readAlertThreshold(storage), 0);
  writeAlertThreshold("8.5", storage);
  assert.equal(storage.getItem(DEEPSEEK_ALERT_KEY), "8.5");
  assert.equal(readAlertThreshold(storage), 8.5);
  writeAlertThreshold(0, storage);
  assert.equal(storage.getItem(DEEPSEEK_ALERT_KEY), null);
});

test("balance source labels distinguish saved, env and DSH", async () => {
  const { balanceSourceLabel, balanceSourceShort } = await loadModel();
  assert.equal(balanceSourceLabel("saved"), "本机保存的 Key");
  assert.equal(balanceSourceLabel("env"), "环境变量 DEEPSEEK_API_KEY");
  assert.equal(balanceSourceLabel("dsh"), "DSH 凭据");
  assert.equal(balanceSourceLabel(""), "未配置");
  assert.equal(balanceSourceShort("saved"), "本机");
  assert.equal(balanceSourceShort("dsh"), "DSH");
});

test("the right-side DeepSeek card is wired into the workbench", async () => {
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  const sideStart = html.indexOf('<aside class="workbench-side">');
  const lifeStart = html.indexOf('<section id="life-view"', sideStart);
  const widget = html.indexOf('id="workbench-deepseek"');
  assert.ok(sideStart > 0 && lifeStart > sideStart, "找不到工作台右侧栏");
  assert.ok(widget > sideStart && widget < lifeStart, "DeepSeek 余额卡必须在右侧小组件区");
  assert.match(html, /id="workbench-deepseek"[^>]*data-widget="deepseek"/);
  assert.match(html, /data-widget-label="DeepSeek 余额"/);

  const workbench = await readFile(path.join(frontend, "workbench.js"), "utf8");
  assert.match(workbench, /initDeepSeekBalance/);
  assert.match(workbench, /loadDeepSeekBalance/);

  const widgetJs = await readFile(path.join(frontend, "deepseek-balance.js"), "utf8");
  assert.match(widgetJs, /\/api\/deepseek\/balance/);
  assert.match(widgetJs, /余额预警未开启/);
  assert.match(widgetJs, /去充值/);
  const model = await readFile(path.join(frontend, "deepseek-balance-model.js"), "utf8");
  assert.match(model, /https:\/\/platform\.deepseek\.com\/top_up/);

  const css = await readFile(path.join(frontend, "styles.css"), "utf8");
  assert.match(css, /\.deepseek-card \{/);
  assert.match(css, /\.deepseek-topup \{/);
  assert.match(css, /workbench-widget\[data-widget="deepseek"\]/);
});
