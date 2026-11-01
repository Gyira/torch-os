import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { companionForReader, documentChips } from "../ai-companions.js";
import {
  drawerSubtitle, emptyMarkup, quoteMarkup,
} from "../roles-model.js";

// 2026-11-01：阅读器右上角「问学术导师 / 问写作伙伴」直接带着当前文档开聊。
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (name) => readFile(path.join(frontend, name), "utf8");

test("reader offers the matching companion and sends the note selector", async () => {
  const [pillars, roles] = await Promise.all([read("pillars.js"), read("roles.js")]);
  assert.match(pillars, /data-reader-ask-ai/);
  assert.match(pillars, /companionForReader\(view, note\.chapter\)/);
  assert.match(pillars, /chips: documentChips\(view, note\.chapter\)/);
  // 浏览器只交标题/路径/字数，正文仍由后端读取。
  assert.match(roles, /entry: "note", path: notePath/);
  assert.match(roles, /正在读《/);
  assert.match(roles, /已自动读取/);
});

test("document note renders its title in the empty state, subtitle and message chip", () => {
  const note = { title: "考研规划提示词", chars: 1234 };
  const empty = emptyMarkup("学术导师", note);
  assert.match(empty, /考研规划提示词/);
  assert.match(empty, /不用先复述/);

  const chip = quoteMarkup({ note });
  assert.match(chip, /role-message-quote is-note/);
  assert.match(chip, /读《考研规划提示词》/);

  const subtitle = drawerSubtitle(
    { name: "学术导师", entry: ["knowledge.editor"] }, "discuss", note);
  assert.match(subtitle, /已读《考研规划提示词》/);
});

test("without a note the drawer keeps its generic copy", () => {
  const empty = emptyMarkup("学术导师", null);
  assert.match(empty, /只提议/);
  assert.doesNotMatch(empty, /不用先复述/);
  assert.equal(quoteMarkup(null), "");
  assert.match(drawerSubtitle({ name: "学术导师" }, "discuss"), /^学术导师 · 聊聊$/);
});

test("reader chips stay with the current pillar", () => {
  assert.equal(companionForReader("knowledge", "inbox").roleId, "academic_mentor");
  assert.ok(documentChips("knowledge", "inbox").length >= 2);
  assert.match(documentChips("knowledge", "inbox").join(" "), /记住|方法|没想清楚/);
  assert.match(documentChips("ops", "task").join(" "), /拆/);
  assert.match(documentChips("life", "essay").join(" "), /想说什么/);
  assert.ok(documentChips("unknown").length >= 1);
});
