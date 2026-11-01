import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const read = (name) => readFile(path.join(frontend, name), "utf8");

test("self page mounts the new three-part shell and workbench uses the mentor strip", async () => {
  const html = await read("index.html");
  assert.match(html, /id="self-view"[\s\S]*?id="self-shell"/);
  assert.match(html, /id="workbench-mentor"[^>]*data-mentor-strip/);
  assert.doesNotMatch(html, /id="self-hub"/);
  assert.doesNotMatch(html, /id="workbench-memory"/);
  assert.doesNotMatch(html, /id="workbench-question"/);
});

test("mentor page wires chat, pending, memory and daily questions", async () => {
  const js = await read("mentor.js");
  assert.match(js, /\/api\/mentor\/overview/);
  assert.match(js, /\/api\/mentor\/questions\/draft/);
  assert.match(js, /\/api\/mentor\/questions\/\$\{encodeURIComponent\(questionId\)\}\/answer/);
  assert.match(js, /\/api\/self\/pending\/\$\{encodeURIComponent\(pendingId\)\}\/accept/);
  assert.match(js, /role_id: "mentor", mode: "converse", entry: "self.mentor"/);
  assert.match(js, /data-mentor-tab/);
});

test("old memory hub boot is retired but backend compatibility remains", async () => {
  const app = await read("app.js");
  const workbench = await read("workbench.js");
  assert.doesNotMatch(app, /initMemoryHub|loadSelfHub/);
  assert.doesNotMatch(workbench, /loadMemoryStrip|initMemoryStrip/);
  assert.match(app, /initMentorStrip/);
  assert.match(app, /initSelf/);
});
