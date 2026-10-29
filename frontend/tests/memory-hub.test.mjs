import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const read = (name) => readFile(path.join(frontend, name), "utf8");

test("self page mounts the memory hub and workbench uses the memory strip", async () => {
  const html = await read("index.html");
  assert.match(html, /id="self-view"[\s\S]*?id="self-hub"/);
  assert.match(html, /id="workbench-memory"[^>]*data-memory-strip/);
  assert.match(html, /id="memory-event-dialog"/);
  assert.doesNotMatch(html, /id="workbench-question"/);
});

test("memory hub wires overview, synthesis, cards and the Socratic drawer", async () => {
  const js = await read("memory-hub.js");
  assert.match(js, /\/api\/memory-hub\/overview/);
  assert.match(js, /\/api\/memory-hub\/synthesize/);
  assert.match(js, /\/api\/memory-hub\/cards\//);
  assert.match(js, /data-memory-socrates/);
  assert.match(js, /openRoleDrawer/);
  assert.match(js, /roleId: "yiling", mode: "converse", entry: "self.socrates"/);
});

test("app boot no longer initializes the retired question card", async () => {
  const app = await read("app.js");
  const workbench = await read("workbench.js");
  assert.doesNotMatch(app, /initQuestions/);
  assert.doesNotMatch(workbench, /loadQuestionCard/);
  assert.match(workbench, /loadMemoryStrip/);
});
