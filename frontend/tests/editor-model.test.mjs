import test from "node:test";
import assert from "node:assert/strict";

import {
  applyEditorAction, isDirty, normalizeBody, prefixLines, tagsFromInput, tagsToInput,
  wordCount, wrapSelection,
} from "../editor-model.js";

test("bold wraps the selection and selects the inner text", () => {
  const result = wrapSelection("今天很好", 2, 4, "**", "**");
  assert.equal(result.text, "今天**很好**");
  assert.equal(result.text.slice(result.selectionStart, result.selectionEnd), "很好");
});

test("bold with no selection inserts a placeholder", () => {
  const result = wrapSelection("abc", 3, 3, "**", "**", "加粗文字");
  assert.equal(result.text, "abc**加粗文字**");
});

test("heading and task prefixes apply to every selected line and toggle off", () => {
  const first = prefixLines("第一行\n第二行", 0, 7, "## ");
  assert.equal(first.text, "## 第一行\n## 第二行");
  const second = prefixLines(first.text, first.selectionStart, first.selectionEnd, "## ");
  assert.equal(second.text, "第一行\n第二行");

  const task = prefixLines("a\n\nb", 0, 4, "- [ ] ");
  assert.equal(task.text, "- [ ] a\n\n- [ ] b");
});

test("link wraps selection with markdown link syntax", () => {
  const result = applyEditorAction("看这里", 1, 3, "link");
  assert.equal(result.text, "看[这里](https://)");
});

test("divider inserts a block without losing the surrounding text", () => {
  const result = applyEditorAction("前文", 2, 2, "divider");
  assert.ok(result.text.includes("前文\n\n---\n\n"));
});

test("word count mixes Chinese characters and English words", () => {
  assert.equal(wordCount("今天写了 code review"), 4 + 2);
  assert.equal(wordCount(""), 0);
});

test("dirty comparison normalizes CRLF and trailing spaces", () => {
  assert.equal(isDirty("a  \r\nb", "a\nb"), false);
  assert.equal(isDirty("a\nb", "a\nc"), true);
  assert.equal(normalizeBody("a\r\nb"), "a\nb");
});

test("tag input trims separators and hashes", () => {
  assert.deepEqual(tagsFromInput(" #读书、复盘, code "), ["读书", "复盘", "code"]);
  assert.equal(tagsToInput(["读书", "复盘"]), "读书、复盘");
});
