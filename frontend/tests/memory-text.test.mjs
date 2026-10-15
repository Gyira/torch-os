import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function loadModule() {
  const context = vm.createContext({});
  const module = new vm.SourceTextModule(await readFile(path.join(frontend, "memory-text.js"), "utf8"), { context, identifier: path.join(frontend, "memory-text.js") });
  await module.link(() => { throw new Error("no imports expected"); });
  await module.evaluate();
  return module.namespace;
}

test("cleanMemoryText strips markdown noise into one readable line", async () => {
  const { cleanMemoryText } = await loadModule();
  assert.equal(cleanMemoryText("# 标题词"), "标题词");
  assert.equal(cleanMemoryText("- [ ] 明天去买菜"), "明天去买菜");
  assert.equal(cleanMemoryText("> 引用内容"), "引用内容");
  assert.equal(cleanMemoryText("看 [这个链接](https://example.com/a) 的内容"), "看 这个链接 的内容");
  assert.equal(cleanMemoryText("| a | b |"), "a b");
  assert.equal(cleanMemoryText("| --- | --- |"), "");
  assert.equal(cleanMemoryText("**加粗**和`代码`"), "加粗和代码");
  assert.equal(cleanMemoryText("<div>标签</div>正文"), "标签正文");
  assert.equal(cleanMemoryText("第一行\n第二行"), "第一行 第二行");
});

test("memoryDisplayTitle prefers the first sentence and falls back to the stored title", async () => {
  const { memoryDisplayTitle } = await loadModule();
  assert.equal(memoryDisplayTitle({ content: "- [ ] 我计划明天去图书馆借书。后面还有别的话。", title: "杂记" }), "我计划明天去图书馆借书。");
  assert.equal(memoryDisplayTitle({ content: "", title: "旧标题" }), "旧标题");
  assert.equal(memoryDisplayTitle({ content: "这是一个超过二十四个字符的很长的句子用于测试省略号的显示效果", title: "x" }).endsWith("…"), true);
});
