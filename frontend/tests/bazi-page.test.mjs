import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 「八字合婚」：出生信息 + 可选四柱 → 本地排盘 + dsh 合婚分析 → 历史页面。
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = (name) => readFile(path.join(frontend, name), "utf8");

test("恋爱系统有独立的八字合婚页与历史入口", async () => {
  const html = await source("index.html");
  const js = await source("love.js");
  const css = await source("styles.css");

  assert.match(html, /data-love-page="bazi"><span>06<\/span>八字合婚/);
  assert.match(js, /bazi: "八字合婚"/);
  assert.match(js, /bazi: renderBazi/);
  assert.match(js, /\/api\/love\/bazi\/generate/);
  assert.match(js, /\/api\/love\/bazi\/history/);
  assert.match(js, /escapeHtml\(person\.name/);
  assert.match(js, /baziPersonFields\("him"/);
  assert.match(js, /baziPersonFields\("her"/);
  assert.match(js, /data-love-action="bazi-select"/);
  assert.match(js, /data-love-action="bazi-delete"/);

  for (const className of [
    "love-bazi-hero", "love-bazi-seal", "love-bazi-pair", "love-bazi-pillars",
    "love-bazi-points", "love-bazi-years", "love-bazi-details",
    "love-bazi-fieldset", "love-bazi-history-list",
  ]) {
    assert.ok(css.includes(className), `缺少样式：${className}`);
  }
});

test("八字合婚页不写内联样式、不直接访问外部网络", async () => {
  const js = await source("love.js");
  const start = js.indexOf("// ── 八字合婚");
  const end = js.indexOf("// ── 恋爱大师", start);
  const bazi = js.slice(start, end);
  assert.equal(/style\s*=/.test(bazi), false);
  assert.equal(/https?:\/\//.test(bazi), false);
  assert.equal(/srcdoc/.test(bazi), false);
  assert.match(bazi, /lunar|四柱|日主/);
});
