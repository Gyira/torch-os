import test from "node:test";
import assert from "node:assert/strict";

import {
  baziPageMarkup, chartBoardMarkup, historyMarkup, pillarsMarkup, profileFormMarkup,
  readingMarkup, topicsMarkup,
} from "../self-bazi-model.js";

const CHART = {
  name: "王衡岳",
  day_master: "丙火",
  favor: ["木", "火"],
  avoid: ["金", "水"],
  time_note: "",
  pillars: [
    { slot: "年柱", gan: "癸", zhi: "未", gan_wx: "水", zhi_wx: "土", gan_ss: "正官", zhi_ss: "伤官", hidden: "己/丁/乙" },
    { slot: "月柱", gan: "辛", zhi: "酉", gan_wx: "金", zhi_wx: "金", gan_ss: "正财", zhi_ss: "正财", hidden: "辛" },
    { slot: "日柱", gan: "丙", zhi: "戌", gan_wx: "火", zhi_wx: "土", gan_ss: "日主", zhi_ss: "食神", hidden: "戊/辛/丁" },
    { slot: "时柱", gan: "戊", zhi: "子", gan_wx: "土", zhi_wx: "水", gan_ss: "食神", zhi_ss: "正官", hidden: "癸" },
  ],
  elements: { 木: 1, 火: 2, 土: 4, 金: 3, 水: 2 },
  dayun_cycles: [{ gz: "戊午", years: "2024-2034" }],
  dayun: "起运 10 年 2 个月；戊午 2024-2034",
};

test("四柱表按年/月/日/时渲染", () => {
  const html = pillarsMarkup(CHART);
  assert.match(html, /癸/);
  assert.match(html, /丙/);
  assert.match(html, /正财/);
  assert.match(html, /藏干十神/);
  assert.match(html, /日主 <strong>丙火/);
});

test("五行条与命盘板都能渲染", () => {
  const html = chartBoardMarkup(CHART);
  assert.match(html, /FIVE ELEMENTS/);
  assert.match(html, /data-wx="fire"/);
  assert.match(html, /<progress class="bazi-element-bar" max="4" value="1"/);
  assert.match(html, /<progress class="bazi-element-bar" max="4" value="4"/);
  assert.doesNotMatch(html, /style=/);
  assert.match(html, /DAYUN/);
  assert.match(html, /戊午/);
});

test("没有命盘时显示空状态", () => {
  assert.match(chartBoardMarkup({}), /还不能排盘/);
});

test("出生信息表单带四柱入口与试算", () => {
  const html = profileFormMarkup({ name: "王衡岳", sex: "male" });
  assert.match(html, /data-bazi-form/);
  assert.match(html, /data-bazi-preview/);
  assert.match(html, /pillars_manual/);
});

test("主题按钮和解读历史", () => {
  assert.equal((topicsMarkup().match(/data-bazi-topic/g) || []).length, 7);
  const html = historyMarkup([{ name: "a.md", title: "综合解读", topic: "综合", date: "2026-11-01" }]);
  assert.match(html, /data-bazi-open="a.md"/);
  assert.match(html, /综合解读/);
});

test("解读正文通过 Markdown 渲染器输出", () => {
  const html = readingMarkup({ name: "a.md", title: "解读", topic: "综合", body: "## 性格\n\n务实。" },
    (text) => `<p>${text.replace(/\n/g, " ")}</p>`);
  assert.match(html, /data-bazi-reading="a.md"/);
  assert.match(html, /## 性格/);
});

test("八字页包含命盘、主题与历史入口", () => {
  const html = baziPageMarkup({ profile: { exists: true, revision: "r1" }, chart: CHART, readings: [] });
  assert.match(html, /我的八字/);
  assert.match(html, /生成解读/);
  assert.match(html, /data-bazi-history/);
  assert.match(html, /data-bazi-edit/);
});
