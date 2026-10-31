import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 「八字合婚」是恋爱系统的只读页：数据只来自本机 .torch-os/love/bazi.json。
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = (name) => readFile(path.join(frontend, name), "utf8");

test("恋爱系统有独立的八字合婚页", async () => {
  const html = await source("index.html");
  const js = await source("love.js");
  const css = await source("styles.css");

  assert.match(html, /data-love-page="bazi"><span>12<\/span>八字合婚/);
  assert.match(js, /bazi: "八字合婚"/);
  assert.match(js, /bazi: renderBazi/);
  assert.match(js, /\/api\/love\/bazi/);
  assert.match(js, /escapeHtml\(person\.name/);

  for (const className of [
    "love-bazi-hero", "love-bazi-seal", "love-bazi-pair", "love-bazi-pillars",
    "love-bazi-points", "love-bazi-years", "love-bazi-details",
  ]) {
    assert.ok(css.includes(className), `缺少样式：${className}`);
  }
});

test("八字合婚页不写内联样式、不读外部网络", async () => {
  const js = await source("love.js");
  const bazi = js.slice(js.indexOf("// ── 八字合婚"), js.indexOf("// ── 总览"));
  assert.equal(/style\s*=/.test(bazi), false);
  assert.equal(/https?:\/\//.test(bazi), false);
  assert.equal(/srcdoc/.test(bazi), false);
});
