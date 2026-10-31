import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (name) => readFile(path.join(frontend, name), "utf8");

test("the editor companion stays inside the editor dialog", async () => {
  const [editor, html, roles, css] = await Promise.all([
    read("note-editor.js"), read("index.html"), read("roles.js"), read("roles.css"),
  ]);
  assert.match(html, /id="note-editor-companion-slot"/);
  assert.match(editor, /host:\s*"editor"/);
  assert.doesNotMatch(editor, /openCompanion[\s\S]{0,500}dialog\?\.close\(\)/,
    "openCompanion 不能关闭编辑器");
  assert.match(roles, /is-embedded/);
  assert.match(css, /role-drawer\.is-embedded/);
  assert.match(css, /is-docked/);
  assert.match(html, /id="role-drawer-dock"/);
});

test("selection ask is wired to the editor and the four role identities", async () => {
  const [html, selection, editor] = await Promise.all([
    read("index.html"), read("selection-ask.js"), read("note-editor.js"),
  ]);
  assert.match(html, /id="selection-ask"/);
  assert.match(selection, /torch:editor-selection-ask/);
  assert.match(editor, /torch:editor-selection-ask/);
  assert.match(selection, /MAX_SELECTION_CHARS = 4000/);
  assert.match(editor, /getDraftText/);
});

test("chat sends selection and draft context without putting raw text in messages", async () => {
  const [roles, model] = await Promise.all([
    read("roles.js"), read("roles-model.js"),
  ]);
  assert.match(roles, /selection_text:/);
  assert.match(roles, /draft_text:/);
  assert.match(roles, /selection\?\.text/);
  assert.doesNotMatch(model, /selection\.text/);
  assert.match(model, /role-message-quote/);
});
