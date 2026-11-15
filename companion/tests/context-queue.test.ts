import { test } from "node:test";
import assert from "node:assert/strict";
import { contextPack, changedSources } from "../core/context-pack.ts";
import { queueRequest, queueEnvelope, queueDecode } from "../core/queue.ts";
import { validateNotePath } from "../core/journal.ts";
import { RetryGate, bounded } from "../core/retry.ts";
const enc = new TextEncoder();
test("mobile context pack requires explicit sources, detects stale files and rejects secrets", async () => {
  let text = "原文";
  const path = "00_人生路/日记.md", read = async () => enc.encode(text);
  const pack = await contextPack([path], read);
  assert.ok(pack.files[0].markdown.includes("原文")); assert.equal(pack.uploaded, false);
  assert.deepEqual(await changedSources(pack, read), []);
  text = "新稿"; assert.deepEqual(await changedSources(pack, read), [path]);
  await assert.rejects(contextPack([], read));
  await assert.rejects(contextPack(["_Torch/private.md"], read));
  text = "api_key: sk-12345678901234567890";
  await assert.rejects(contextPack([path], read), /密钥/);
});
test("queue records are readable immutable Markdown and only scoped queue paths are allowed", () => {
  const request = queueRequest("workspace", "device", "share_preview", { source_url: "https://chatgpt.com/share/test-123" });
  assert.deepEqual(queueDecode(queueEnvelope(request)), request);
  validateNotePath(`_Torch/收件/${request.request_id}.md`);
  assert.throws(() => validateNotePath("_Torch/工作区.md"));
  assert.throws(() => queueRequest("", "", "share_preview", {}));
  assert.throws(() => queueRequest("workspace", "device", "share_preview", { source_url: "http://127.0.0.1/secret" }));
});
test("retry backoff is bounded, resets after success and a stalled network call yields", async () => {
  const gate = new RetryGate(); gate.failure(100, 0.5);
  assert.equal(gate.allowed(5099), false); assert.equal(gate.allowed(5100), true);
  for (let i = 0; i < 20; i++) gate.failure(100, 0.5);
  assert.equal(gate.next, 300100);
  gate.success(); assert.equal(gate.failures, 0); assert.equal(gate.allowed(0), true);
  await assert.rejects(bounded(new Promise(() => {}), 5), /超时/);
});
