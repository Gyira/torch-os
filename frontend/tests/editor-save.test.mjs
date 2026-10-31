import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { isDirty, normalizeBody } from "../editor-model.js";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("editor records the submitted body after save, not the stripped server body", async () => {
  const source = await readFile(path.join(frontend, "note-editor.js"), "utf8");
  assert.match(source, /function applySavedNote\(/);
  assert.match(source, /applySavedNote\(note, body\)/);
  assert.match(source, /state\.savedBody = normalized/);
  // 回归：服务端 parse 会 trim 首尾空白；若直接把服务端 body 当 savedBody，
  // 用户刚保存就会再次被 dirty() 判成未保存。
  assert.match(source, /后端会去掉正文首尾空白/);
});

test("trailing newline is a real unsaved difference until savedBody is updated", () => {
  assert.equal(isDirty("正文\n", "正文"), true);
  assert.equal(isDirty(normalizeBody("正文\r\n"), "正文\n"), false);
  assert.equal(isDirty(normalizeBody("正文\n"), normalizeBody("正文\n")), false);
});
