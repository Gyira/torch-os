import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function loadModel() {
  const context = vm.createContext({});
  const filename = path.join(frontend, "journal-ritual-model.js");
  const module = new vm.SourceTextModule(await readFile(filename, "utf8"), {
    context, identifier: filename,
  });
  await module.link(() => { throw new Error("no imports expected"); });
  await module.evaluate();
  return module.namespace;
}

const payload = {
  digest: { day: "2026-10-28", weekday: "周三", headline: "2 件还挂着", signals: ["还挂着 2 件：背单词", "动过 2 篇笔记"] },
  questions: [
    { prompt: "「背单词」还做吗？", why: "日期还是 10-22", evidence: "任务/背单词.md" },
    { prompt: "今天改的《考研规划》是在调方向吗？", why: "动过这篇", evidence: "项目/考研规划.md" },
  ],
  note: { exists: true, content: "---\ntype: journal\n---\n\n# 10月28日\n\n## 我想说的\n\n手写的一段\n" },
};

test("sectionText 只取指定二级标题下的正文", async () => {
  const model = await loadModel();
  assert.equal(model.sectionText(payload.note.content, "我想说的"), "手写的一段");
  assert.equal(model.sectionText(payload.note.content, "下一步"), "");
  assert.equal(model.existingFreeText(payload.note.content), "手写的一段");
});

test("reviewMarkup 渲染盘点、问题与回填的手写内容", async () => {
  const model = await loadModel();
  const html = model.reviewMarkup(payload);

  assert.match(html, /2026-10-28 · 周三/);
  assert.match(html, /2 件还挂着/);
  assert.match(html, /还挂着 2 件：背单词/);
  assert.match(html, /data-jr-answer="0"/);
  assert.match(html, /data-jr-action="compose"/);
  // 已有日记要提示会被整篇替换，并回填「我想说的」。
  assert.match(html, /旧版本会先备份/);
  assert.match(html, />手写的一段</);
});

test("reviewMarkup 对用户内容转义", async () => {
  const model = await loadModel();
  const html = model.reviewMarkup({
    digest: { day: "2026-10-28", weekday: "周三", headline: "<script>x</script>", signals: ["a & b"] },
    questions: [{ prompt: "<b>问</b>", why: "x" }],
    note: { exists: false, content: "" },
  });
  assert.ok(!html.includes("<script>x</script>"));
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /a &amp; b/);
});

test("composeBody 丢掉空回答并保留封存开关", async () => {
  const model = await loadModel();
  const body = model.composeBody({
    answers: [
      { prompt: "问一", answer: "  答一  " },
      { prompt: "问二", answer: "   " },
    ],
    freeText: "  一句  ",
    nextStep: " 先背 20 个 ",
    mood: "平静",
    sealed: true,
  }, "2026-10-28");

  // vm 里的对象来自另一个 realm，先过一遍 JSON 再比较。
  assert.deepEqual(JSON.parse(JSON.stringify(body)), {
    day: "2026-10-28",
    free_text: "一句",
    answers: [{ prompt: "问一", answer: "答一" }],
    next_step: "先背 20 个",
    summary: "",
    mood: "平静",
    sealed: true,
    mode: "template",
  });
});

test("previewMarkup 把原文放进 pre 并转义", async () => {
  const model = await loadModel();
  const html = model.previewMarkup("# a <b>\n\n## 下一步", "2026-10-28");
  assert.match(html, /00_人生路\/01_日记\/2026-10-28.md/);
  assert.match(html, /data-jr-action="write"/);
  assert.match(html, /# a &lt;b&gt;/);
});

test("distillMarkup 给出回流候选，没有候选就不出界面", async () => {
  const model = await loadModel();
  const html = model.distillMarkup([{
    kind: "reflection",
    title: "下次把背单词挪到早上",
    body: "今天发现晚上总是想不起来。",
    path: "00_人生路/02_反思复盘/01_反思/下次把背单词挪到早上.md",
  }]);
  assert.match(html, /data-jr-action="distill"/);
  assert.match(html, /下次把背单词挪到早上/);
  assert.match(html, /01_反思/);
  assert.equal(model.distillMarkup([]), "");
});

test("只有通道可用时才露出「聊着写」", async () => {
  const model = await loadModel();
  assert.match(model.reviewMarkup(payload, { chatAvailable: true }), /data-jr-action="chat-open"/);
  assert.equal(model.reviewMarkup(payload, { chatAvailable: false }).includes('data-jr-action="chat-open"'), false);
});

test("chatMarkup 突出最后一句，历史折叠", async () => {
  const model = await loadModel();
  const html = model.chatMarkup([
    { role: "ai", text: "今天先说说背单词？" },
    { role: "user", text: "先拿掉" },
    { role: "ai", text: "好，那《考研规划》呢？" },
  ]);
  assert.match(html, /《考研规划》呢/);
  assert.match(html, /data-jr-action="chat-send"/);
  assert.match(html, /data-jr-action="chat-draft"/);
  assert.match(html, /<details>/);
  assert.match(html, /背单词/);
});
