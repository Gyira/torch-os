import test from "node:test";
import assert from "node:assert/strict";

// core.js 在导入时给确认对话框绑定监听；这里给渲染器测试一个最小 DOM 壳。
const node = { addEventListener() {}, open: false, close() {} };
globalThis.document = {
  querySelector: () => node,
  addEventListener() {},
  activeElement: null,
};
const { renderMarkdown } = await import("../pillar-markdown.js");

test("markdown links only allow safe schemes and relative paths", () => {
  const html = renderMarkdown(
    "[安全](https://example.com) [危险](javascript:alert(1)) [数据](data:text/html,x) [相对](../notes/a.md) [锚点](#top)");
  assert.match(html, /href="https:\/\/example\.com"/);
  assert.match(html, /href="\.\.\/notes\/a\.md"/);
  assert.match(html, /href="#top"/);
  assert.doesNotMatch(html, /javascript:/);
  assert.doesNotMatch(html, /data:text/);
  assert.match(html, /危险/);
});

test("raw html and wiki aliases stay escaped", () => {
  const html = renderMarkdown('<img src=x onerror="alert(1)"> [[目标|别名]]');
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
  assert.match(html, /pillar-wikilink/);
  assert.match(html, /别名/);
});
