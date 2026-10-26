import test from "node:test";
import assert from "node:assert/strict";

// core.js 在导入时给确认对话框绑定监听；这里给渲染器测试一个最小 DOM 壳。
const node = { addEventListener() {}, open: false, close() {} };
globalThis.document = {
  querySelector: () => node,
  addEventListener() {},
  activeElement: null,
};
const { renderMarkdown, renderMarkdownDocument } = await import("../pillar-markdown.js");

test("markdown links only allow safe schemes and route relative notes in-page", () => {
  const html = renderMarkdown(
    "[安全](https://example.com) [危险](javascript:alert(1)) [数据](data:text/html,x) [相对](../notes/a.md) [锚点](#top)");
  assert.match(html, /href="https:\/\/example\.com"/);
  assert.doesNotMatch(html, /javascript:/);
  assert.doesNotMatch(html, /data:text/);
  assert.match(html, /危险/);
  assert.match(html, /href="\.\.\/notes\/a\.md"/);
  assert.match(html, /href="#top"/);

  const routed = renderMarkdown("[相对](../notes/a.md)", {
    notePath: "03_知识库/01_主题笔记/示例.md",
  });
  assert.match(routed, /data-md-note="03_知识库\/notes\/a\.md"/);
});

test("raw html and wiki aliases stay escaped", () => {
  const html = renderMarkdown('<img src=x onerror="alert(1)"> [[目标|别名]]');
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
  assert.match(html, /md-wikilink/);
  assert.match(html, /别名/);
});

test("headings are shifted below the note title and collect a table of contents", () => {
  const doc = renderMarkdownDocument("# 一级\n\n## 二级\n\n### 三级\n\n## 重复\n\n## 重复");
  assert.deepEqual(doc.headings.map((item) => [item.level, item.id]), [
    [2, "一级"],
    [3, "二级"],
    [4, "三级"],
    [3, "重复"],
    [3, "重复-2"],
  ]);
  assert.match(doc.html, /<h2 id="一级" class="md-heading">一级<\/h2>/);
  assert.match(doc.html, /<h4 id="三级"/);
});

test("paragraphs keep soft line breaks and blank lines separate blocks", () => {
  const html = renderMarkdown("第一行\n第二行\n\n另一段");
  assert.match(html, /<p>第一行<br>第二行<\/p>/);
  assert.match(html, /<p>另一段<\/p>/);
});

test("inline emphasis, code, strike, highlight and escapes render safely", () => {
  const html = renderMarkdown(
    "**粗** *斜* ~~删~~ ==亮== `a < b` \\*不是斜体\\*");
  assert.match(html, /<strong>粗<\/strong>/);
  assert.match(html, /<em>斜<\/em>/);
  assert.match(html, /<del>删<\/del>/);
  assert.match(html, /<mark class="md-highlight">亮<\/mark>/);
  assert.match(html, /<code class="md-inline-code">a &lt; b<\/code>/);
  assert.match(html, /\*不是斜体\*/);
  assert.doesNotMatch(html, /<em>不是斜体<\/em>/);
});

test("tables render headers, alignment and inline markup", () => {
  const html = renderMarkdown([
    "| 项目 | 数值 |",
    "| :--- | ---: |",
    "| 目标 | **330** |",
  ].join("\n"));
  assert.match(html, /<div class="md-table-wrap">/);
  assert.match(html, /<th class="md-align-left">项目<\/th>/);
  assert.match(html, /<td class="md-align-right"><strong>330<\/strong><\/td>/);
});

test("task lists render disabled checkboxes", () => {
  const html = renderMarkdown("- [ ] 未完成\n- [x] 已完成");
  assert.match(html, /class="md-list md-task-list"/);
  assert.match(html, /<input type="checkbox" disabled> 未完成/);
  assert.match(html, /<input type="checkbox" disabled checked> 已完成/);
});

test("nested lists stay nested", () => {
  const html = renderMarkdown("1. 第一项\n   - 子项 A\n   - 子项 B\n2. 第二项");
  assert.match(html, /<ol class="md-list"><li>第一项<ul class="md-list"><li>子项 A<\/li><li>子项 B<\/li><\/ul><\/li><li>第二项<\/li><\/ol>/);
});

test("obsidian callouts render with title and body", () => {
  const html = renderMarkdown("> [!warning] 先看这里\n> **不要**跳过。\n>\n> 第二段。");
  assert.match(html, /class="md-callout md-callout-warning"/);
  assert.match(html, /md-callout-title/);
  assert.match(html, /先看这里/);
  assert.match(html, /<strong>不要<\/strong>跳过。/);
  assert.match(html, /第二段。/);
});

test("fenced code blocks escape their content and keep the language label", () => {
  const html = renderMarkdown("```js\nconst ok = 1 < 2;\n```");
  assert.match(html, /<pre class="md-code" data-lang="js"><code>const ok = 1 &lt; 2;<\/code><\/pre>/);
});

test("relative images resolve to the vault asset endpoint", () => {
  const html = renderMarkdown("![封面](attachments/cover.png)", {
    notePath: "03_知识库/01_主题笔记/示例.md",
  });
  assert.match(html, /src="\/api\/notes\/asset\?path=03_%E7%9F%A5%E8%AF%86%E5%BA%93%2F01_%E4%B8%BB%E9%A2%98%E7%AC%94%E8%AE%B0%2Fattachments%2Fcover.png"/);
  assert.match(html, /md-image-caption">封面/);
});

test("external images become links because the page CSP only allows local images", () => {
  const html = renderMarkdown("![远程](https://example.com/a.png)");
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /href="https:\/\/example\.com\/a\.png"/);
  assert.match(html, /远程/);
});

test("obsidian comments are hidden, including multi-line comments", () => {
  const html = renderMarkdown("前\n\n%%隐藏\n多行%%\n\n后 %%行内%% 文");
  assert.doesNotMatch(html, /隐藏/);
  assert.doesNotMatch(html, /行内/);
  assert.match(html, /前/);
  assert.match(html, /后/);
});

test("setext headings, quotes and horizontal rules are rendered", () => {
  const html = renderMarkdown("标题\n===\n\n> 引用\n\n---");
  assert.match(html, /<h2 id="标题" class="md-heading">标题<\/h2>/);
  assert.match(html, /<blockquote class="md-quote"><p>引用<\/p><\/blockquote>/);
  assert.match(html, /<hr class="md-hr">/);
});
