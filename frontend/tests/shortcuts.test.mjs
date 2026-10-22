import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 快捷键规则是纯函数；这里不启动浏览器，也不读取个人数据。
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function loadModel() {
  const filename = path.join(frontend, "shortcut-model.js");
  const module = new vm.SourceTextModule(await readFile(filename, "utf8"), {
    context: vm.createContext({}),
    identifier: filename,
  });
  await module.link(() => { throw new Error("shortcut-model.js 不应有 import"); });
  await module.evaluate();
  return module.namespace;
}

const key = (value, extra = {}) => ({ key: value, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...extra });

test("the shortcut sheet covers every implemented entry and nothing else", async () => {
  const model = await loadModel();
  const items = model.SHORTCUT_GROUPS.flatMap((group) => group.items);
  assert.ok(model.SHORTCUT_GROUPS.length >= 4);
  for (const group of model.SHORTCUT_GROUPS) {
    assert.ok(group.id && group.title, "每个分组都要有 id 与标题");
    assert.ok(group.items.length > 0, `${group.title} 不能是空分组`);
    for (const item of group.items) {
      assert.ok(item.keys.length > 0 && item.label, `${group.title} 的条目缺少按键或说明`);
      assert.ok(item.keys.every((value) => typeof value === "string" && value.length));
    }
  }
  const labels = items.map((item) => item.label).join("\n");
  for (const expected of ["全局搜索", "快捷键总览", "工作台", "知识库", "新任务输入", "捕获弹窗"]) {
    assert.match(labels, new RegExp(expected), `总览里应说明：${expected}`);
  }
  assert.deepEqual(Object.keys(model.VIEW_CHORDS), ["w", "l", "s", "o", "k", ",", "h"]);
  assert.equal(model.VIEW_CHORDS.h, "help");
});

test("editable controls keep single-key input and IME untouched", async () => {
  const { isEditableTarget } = await loadModel();
  assert.equal(isEditableTarget({ tagName: "INPUT" }), true);
  assert.equal(isEditableTarget({ tagName: "textarea" }), true);
  assert.equal(isEditableTarget({ tagName: "SELECT" }), true);
  assert.equal(isEditableTarget({ tagName: "BUTTON" }), false);
  assert.equal(isEditableTarget({ tagName: "DIV", isContentEditable: true }), true);
  assert.equal(isEditableTarget({ tagName: "SPAN", closest: (selector) => selector.includes("contenteditable") ? {} : null }), true);
  assert.equal(isEditableTarget(null), false);
});

test("list navigation wraps, supports Home/End and rejects unrelated keys", async () => {
  const { nextListIndex } = await loadModel();
  assert.equal(nextListIndex("ArrowRight", 0, 5), 1);
  assert.equal(nextListIndex("ArrowDown", 4, 5), 0);
  assert.equal(nextListIndex("ArrowLeft", 0, 5), 4);
  assert.equal(nextListIndex("ArrowUp", 2, 5), 1);
  assert.equal(nextListIndex("Home", 3, 5), 0);
  assert.equal(nextListIndex("End", 0, 5), 4);
  assert.equal(nextListIndex("ArrowRight", -1, 5), 0);
  assert.equal(nextListIndex("ArrowLeft", -1, 5), 4);
  assert.equal(nextListIndex("Enter", 2, 5), -1);
  assert.equal(nextListIndex("ArrowRight", 0, 0), -1);
});

test("global shortcuts respect dialogs, editables, modifiers and IME composition", async () => {
  const { shortcutCommand } = await loadModel();
  assert.equal(shortcutCommand(key("k", { ctrlKey: true })), "open-search");
  assert.equal(shortcutCommand(key("K", { metaKey: true })), "open-search");
  assert.equal(shortcutCommand(key("k", { ctrlKey: true, shiftKey: true })), null);
  assert.equal(shortcutCommand(key("k", { altKey: true })), null);
  assert.equal(shortcutCommand(key("/")), "open-search");
  assert.equal(shortcutCommand(key("?")), "open-shortcuts");
  assert.equal(shortcutCommand(key("？")), "open-shortcuts");
  assert.equal(shortcutCommand(key("/", { shiftKey: true })), "open-shortcuts");
  assert.equal(shortcutCommand(key("G")), "start-chord");
  assert.equal(shortcutCommand(key("n")), "new-capture");
  assert.equal(shortcutCommand(key("x")), null);

  assert.equal(shortcutCommand(key("l"), { chordPending: true }), "navigate:life");
  assert.equal(shortcutCommand(key(","), { chordPending: true }), "navigate:settings");
  assert.equal(shortcutCommand(key("H"), { chordPending: true }), "navigate:help");
  assert.equal(shortcutCommand(key("Escape"), { chordPending: true }), "cancel-chord");
  assert.equal(shortcutCommand(key("x"), { chordPending: true }), null);

  assert.equal(shortcutCommand(key("n"), { editable: true }), null);
  assert.equal(shortcutCommand(key("g"), { editable: true }), null);
  assert.equal(shortcutCommand(key("?"), { editable: true }), null);
  assert.equal(shortcutCommand(key("/"), { editable: true }), null);
  assert.equal(shortcutCommand(key("k", { ctrlKey: true }), { editable: true }), "open-search");

  for (const event of [key("n"), key("?"), key("/"), key("k", { ctrlKey: true })]) {
    assert.equal(shortcutCommand(event, { dialogOpen: true }), null);
  }
  assert.equal(shortcutCommand(key("n", { isComposing: true })), null);
  assert.equal(shortcutCommand(key("n", { defaultPrevented: true })), null);
  assert.equal(shortcutCommand(null), null);
});

test("the shell exposes the shortcut entry points and keeps time tabs roving", async () => {
  const html = await readFile(path.join(frontend, "index.html"), "utf8");
  assert.match(html, /id="open-shortcuts"[^>]*aria-keyshortcuts="\?"/);
  assert.match(html, /id="time-add-input"[^>]*aria-keyshortcuts="N"/);
  assert.match(html, /id="time-tab-tasks"[^>]*aria-controls="time-tasks-page"/);
  assert.match(html, /id="time-tasks-page"[^>]*role="tabpanel"/);
  const timeTabs = [...html.matchAll(/id="time-tab-[a-z]+"[^>]*data-time-page="([a-z]+)"/g)].map((match) => match[1]);
  assert.deepEqual(timeTabs, ["tasks", "calendar", "habits", "quadrant", "trash"]);
  const app = await readFile(path.join(frontend, "app.js"), "utf8");
  assert.match(app, /initKeyboardShortcuts\(\{ navigate: \(target\) => goTo\(target\) \}\)/);
  const guide = await readFile(path.join(frontend, "help-content.js"), "utf8");
  assert.match(guide, /先按 g 再按 w\/l\/s\/o\/k/);
  assert.match(guide, /\? 打开快捷键总览/);
});
