import test from "node:test";
import assert from "node:assert/strict";

import { questionCardMarkup, questionDoneMarkup } from "../questions-markup.js";

// 主动提问卡片：一个问题 + 三个出口。纯渲染，不碰 DOM。

const QUESTION = {
  id: "identity.name",
  kind: "field",
  prompt: "我该怎么称呼你？",
  hint: "写进身份卡的「署名」。",
  placeholder: "小林 / 阿杰",
  target: "name",
  remaining: 4,
};

test("no question means no card", () => {
  assert.equal(questionCardMarkup(null), "");
  assert.equal(questionCardMarkup({}), "");
  assert.equal(questionCardMarkup({ id: "x" }), "", "没有问题文案就不渲染");
});

test("the card carries the prompt, the hint and the placeholder", () => {
  const html = questionCardMarkup(QUESTION);
  assert.match(html, /我该怎么称呼你？/);
  assert.match(html, /写进身份卡的「署名」/);
  assert.match(html, /placeholder="小林 \/ 阿杰"/);
  assert.match(html, /data-question-id="identity.name"/);
});

test("three exits, and only the save one is primary", () => {
  const html = questionCardMarkup(QUESTION);
  assert.match(html, /class="button primary" data-question-action="answer"/);
  assert.match(html, /class="text-button" data-question-action="skip"/);
  assert.match(html, /class="text-button" data-question-action="decline"/);
});

test("a question picked from the vault shows why it was picked", () => {
  const html = questionCardMarkup({
    ...QUESTION, queued: true, id: "queue:q0",
    prompt: "如果没考上，你打算怎么办？",
    why: "考研规划文档只写目标、没写退路",
    hint: "考研规划文档只写目标、没写退路",
  });
  assert.match(html, /从四库里挑的/);
  assert.match(html, /为什么问这个：考研规划文档只写目标、没写退路/);
  assert.equal((html.match(/考研规划文档只写目标/g) || []).length, 1, "依据只出现一次");
});

test("a bank question shows its hint instead of a reason", () => {
  const html = questionCardMarkup({ ...QUESTION, queued: false, why: "" });
  assert.equal(html.includes("为什么问这个"), false);
  assert.match(html, /写进身份卡的「署名」/);
});

test("the remaining count is only shown when there is more than one", () => {
  assert.match(questionCardMarkup(QUESTION), /还有 4 个/);
  assert.equal(questionCardMarkup({ ...QUESTION, remaining: 1 }).includes("还有"), false);
  assert.equal(questionCardMarkup({ ...QUESTION, remaining: 0 }).includes("还有"), false);
});

test("prompt text is escaped, never injected as markup", () => {
  const html = questionCardMarkup({
    ...QUESTION, prompt: "<img src=x onerror=alert(1)>", hint: "<script>bad()</script>",
    placeholder: '"><b>逗号</b>',
  });
  assert.equal(html.includes("<img"), false);
  assert.equal(html.includes("<script>"), false);
  assert.equal(html.includes("<b>"), false);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test("after answering, the card offers one more instead of vanishing", () => {
  const html = questionDoneMarkup({ exhausted: false });
  assert.match(html, /今天的问题答完了/);
  assert.match(html, /data-question-action="another"/);
  assert.match(html, /再问一个/);
});

test("an exhausted bank says so and has nothing to click", () => {
  const html = questionDoneMarkup({ exhausted: true });
  assert.match(html, /题库问完了/);
  assert.equal(html.includes("data-question-action"), false);
  assert.equal(html.includes("再问一个"), false);
});
