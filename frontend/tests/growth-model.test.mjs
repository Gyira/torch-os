// 上海时区的周界与百分比口径：纯函数，不需要 DOM。
// （原来挂在 growth.test.mjs 里；那一份测的是已经删除的“学习页”，所以这里单独保留。）
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const context = vm.createContext({});
const cache = new Map();

async function loadModule(file) {
  if (cache.has(file)) return cache.get(file);
  const module = new vm.SourceTextModule(await readFile(file, "utf8"), { context, identifier: file });
  cache.set(file, module);
  // growth-model.js 会再导出 journal-model.js 里的日期工具，所以要按真实依赖链链接。
  await module.link((specifier) => loadModule(path.resolve(path.dirname(file), specifier)));
  await module.evaluate();
  return module;
}

test("week boundaries use Shanghai days through Sunday, Monday and year change", async () => {
  const model = (await loadModule(path.join(frontend, "growth-model.js"))).namespace;
  assert.equal(model.shanghaiDay(new Date("2026-10-04T16:00:00Z")), "2026-10-05");
  assert.equal(model.mondayOf("2026-10-04"), "2026-09-28");
  assert.equal(model.mondayOf("2026-10-05"), "2026-10-05");
  assert.equal(model.mondayOf("2027-01-01"), "2026-12-28");
  assert.equal(model.rateLabel(null), "—");
  assert.equal(model.rateLabel(1.25, 100), "125%");
  assert.deepEqual([...model.weekdayLabels], ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]);
});
