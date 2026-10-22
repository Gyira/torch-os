// 今日黄历挂件：确定性（同一天不变、逐日翻新）、抽取规则与转义。
import test from "node:test";
import assert from "node:assert/strict";
import {
  almanacHtml, buildAlmanac, AUSPICIOUS_POOL, INAUSPICIOUS_POOL,
} from "../almanac.js";

test("same day always yields the same reading", () => {
  const first = buildAlmanac("2026-10-05");
  const second = buildAlmanac("2026-10-05");
  assert.deepEqual(first, second);
});

test("every reading has the right shape and comes from the pools", () => {
  const aus = new Set(AUSPICIOUS_POOL.map(([title]) => title));
  const inaus = new Set(INAUSPICIOUS_POOL.map(([title]) => title));
  for (let offset = 0; offset < 60; offset += 1) {
    const day = new Date(Date.UTC(2026, 9, 5 + offset)).toISOString().slice(0, 10);
    const reading = buildAlmanac(day);
    assert.equal(reading.auspicious.length, 2);
    assert.equal(reading.inauspicious.length, 2);
    const ausTitles = reading.auspicious.map(([title]) => title);
    const inausTitles = reading.inauspicious.map(([title]) => title);
    assert.equal(new Set(ausTitles).size, 2, "宜 不重复");
    assert.equal(new Set(inausTitles).size, 2, "忌 不重复");
    assert.ok(ausTitles.every((title) => aus.has(title)));
    assert.ok(inausTitles.every((title) => inaus.has(title)));
    assert.ok(reading.auspicious.every(([, note]) => note.length > 0));
    assert.ok(reading.inauspicious.every(([, note]) => note.length > 0));
  }
});

test("the fortune and picks do move across days", () => {
  const days = Array.from({ length: 30 }, (_, index) =>
    new Date(Date.UTC(2026, 9, 5 + index)).toISOString().slice(0, 10));
  const fortunes = new Set(days.map((day) => buildAlmanac(day).fortune));
  const firstPicks = new Set(days.map((day) => buildAlmanac(day).auspicious[0][0]));
  assert.ok(fortunes.size >= 3, "运气等级应该逐日变化");
  assert.ok(firstPicks.size >= 8, "宜的首条应该逐日变化");
});

test("html escapes the name and the meta line follows progress", () => {
  const html = almanacHtml("2026-10-05", '<img src=x onerror=1>', { done: 3, total: 24 });
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img"));
  assert.ok(html.includes("今日闭环 3 / 24 项（含日记）"));
  assert.ok(html.includes("§ "));
  assert.ok(html.includes("宜："));
  assert.ok(html.includes("忌："));

  const anonymous = almanacHtml("2026-10-05", "", null);
  assert.ok(anonymous.includes("你 的运势"));
  assert.ok(!anonymous.includes("今日闭环"));
});
