import test from "node:test";
import assert from "node:assert/strict";

import {
  emptyChatMarkup, memoryMarkup, messageMarkup, pendingMarkup, questionCardMarkup,
  questionsMarkup, sessionListMarkup, sideTabsMarkup, sourcesMarkup,
} from "../mentor-model.js";

test("会话列表区分导师会话与旧忆灵只读会话", () => {
  const html = sessionListMarkup({
    sessions: [{ id: "t1", title: "最近的选择", updated_at: "2026-11-01T10:00:00+08:00" }],
    legacy: [{ id: "t2", title: "旧忆灵", updated_at: "" }],
    currentId: "t1",
  });
  assert.match(html, /data-mentor-thread="t1"/);
  assert.match(html, /is-active/);
  assert.match(html, /旧忆灵对话/);
  assert.match(html, /data-mentor-legacy="1"/);
});

test("导师消息带来源折叠，用户消息不带", () => {
  const assistant = messageMarkup({
    role: "assistant", content: "我记得你写过…",
    sources: [{ id: "S1", title: "2026-10-30 日记", path: "00_人生路/01_日记/2026-10-30.md" }],
  });
  assert.match(assistant, /用了 1 份资料/);
  assert.match(assistant, /2026-10-30 日记/);
  const user = messageMarkup({ role: "user", content: "我最近在纠结" });
  assert.match(user, /我最近在纠结/);
  assert.doesNotMatch(user, /用了 \d+ 份资料/);
});

test("空对话给出可点的开场问题", () => {
  const html = emptyChatMarkup();
  assert.match(html, /我在这里/);
  assert.match(html, /data-mentor-chip/);
});

test("今日三问：有拟答时可确认写入，没有时可自己回答", () => {
  const withAnswer = questionCardMarkup({
    id: "q1", prompt: "冲突时你怎么选？", section_title: "三观与决策",
    status: "pending", suggested: "先讲事实。", suggested_sources: ["S1"],
  });
  assert.match(withAnswer, /data-mentor-q-accept="q1"/);
  assert.match(withAnswer, /确认写入档案/);
  assert.match(withAnswer, /导师拟答/);
  const without = questionCardMarkup({
    id: "q2", prompt: "最近睡眠怎么样？", section_title: "身体与能量", status: "pending",
  });
  assert.doesNotMatch(without, /data-mentor-q-accept/);
  assert.match(without, /data-mentor-q-edit="q2"/);
  assert.match(without, /data-mentor-q-skip="q2"/);
  assert.match(without, /data-mentor-q-never="q2"/);
});

test("已回答/已跳过的问题不再显示操作按钮", () => {
  const answered = questionCardMarkup({
    id: "q3", prompt: "问题", status: "answered", answer: "我的回答",
  });
  assert.match(answered, /我的回答/);
  assert.doesNotMatch(answered, /data-mentor-q-accept/);
  const skipped = questionCardMarkup({ id: "q4", prompt: "问题", status: "skipped" });
  assert.doesNotMatch(skipped, /data-mentor-q-edit/);
  const grid = questionsMarkup({ items: [skipped] });
  assert.match(grid, /data-mentor-draft-questions/);
});

test("待确认卡片复用档案卡片；记忆面板显示核心/时间线/问答", () => {
  const pending = pendingMarkup([{
    pending_id: "p1", section_title: "三观与决策", title: "世界观",
    confidence: "medium", new_text: "世界更像情境决定。", sources: ["S1"],
  }]);
  assert.match(pending, /data-pending-accept="p1"/);
  const empty = pendingMarkup([]);
  assert.match(empty, /没有待确认/);
  const memory = memoryMarkup({
    core: { text: "长期在准备考研。", updated_at: "2026-11-01" },
    timeline_recent: [{ date: "2026-11-01", text: "决定先准备考研" }],
    paths: { core: "a.md", timeline: "b.md", qa: "c.md" },
  }, { text: "Q: ... A: ..." });
  assert.match(memory, /长期在准备考研/);
  assert.match(memory, /记忆时间线/);
  assert.match(memory, /data-mentor-open="a.md"/);
});

test("侧栏 tab 与资料面板", () => {
  assert.equal((sideTabsMarkup("questions").match(/data-mentor-tab/g) || []).length, 4);
  assert.match(sideTabsMarkup("pending"), /待确认/);
  const sources = sourcesMarkup([{ kind: "self_archive", title: "自我档案", path: "x" }]);
  assert.match(sources, /self_archive/);
  assert.match(sources, /自我档案/);
});
