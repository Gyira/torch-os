import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("life ops AI card calls the read-only overview and report endpoints", async () => {
  const js = await readFile(path.join(frontend, "ops-ai.js"), "utf8");
  assert.match(js, /\/api\/ops\/ai\/overview/);
  assert.match(js, /\/api\/ops\/ai\/report/);
  for (const domain of ["life", "health", "finance", "love"]) {
    assert.match(js, new RegExp(`\\b${domain}\\b`));
  }
  assert.match(js, /proposalsMarkup/);
});

test("ops view mounts the AI card above the existing ops dashboard", async () => {
  const pillars = await readFile(path.join(frontend, "pillars.js"), "utf8");
  assert.match(pillars, /data-ops-ai/);
  assert.match(pillars, /loadOpsAI/);
});
