import { test } from "node:test";
import assert from "node:assert/strict";
import { capturePage, captureText, sourceUrl, selectMessages } from "../core/share.ts";
import { readFile } from "node:fs/promises";
test("anonymous JSON fallback keeps final messages and rejects non-share URLs", async () => {
  const payload = { current_node: "b", mapping: {
    a: { parent: null, message: { author: { role: "user" }, content: { content_type: "text", parts: ["中文问题"] } } },
    b: { parent: "a", message: { author: { role: "assistant" }, content: { content_type: "text", parts: ["```py\nprint(1)\n```"] }, status: "finished_successfully" } }
  } };
  const result = await capturePage("https://chatgpt.com/share/test123", async url => url.includes("backend-api/") ? JSON.stringify(payload) : "<html>share</html>");
  assert.ok(result.markdown.includes("中文问题")); assert.ok(result.markdown.includes("print(1)"));
  assert.throws(() => sourceUrl("https://evil.test/share/test123"));
});
test("paste preserves content and never claims web verification", async () => {
  const result = await captureText("正文\n\n|A|B|", "", "paste");
  assert.ok(result.markdown.includes("未与网页核验")); assert.ok(result.markdown.includes("|A|B|"));
});
test("portable fingerprint matches the shared Python fixture", async () => {
  const fixture = JSON.parse(await readFile(new URL("../fixtures/capture.json", import.meta.url), "utf8"));
  assert.equal((await captureText(fixture.text)).contentHash, fixture.expectedHash);
});
test("nullable web fields preserve questions and selection excludes unrelated media and reasoning", async () => {
  const message = (role: string, parts: unknown[], content_type = "text") => ({ author: { role }, channel: null, status: null, content: { parts, content_type } });
  const payload = { current_node: "d", mapping: {
    a: { parent: null, message: message("user", ["含图片的问题", { image: "not-downloaded" }], "multimodal_text") },
    b: { parent: "a", message: message("assistant", ["内部推理不可归档"], "thoughts") },
    c: { parent: "b", message: message("user", ["第二轮问题"]) },
    d: { parent: "c", message: message("assistant", ["第二轮回答"]) }
  } };
  const result = await capturePage("https://chatgpt.com/share/test123", async url => url.includes("backend-api") ? JSON.stringify(payload) : "<html></html>", { rounds: "2" });
  assert.ok(result.markdown.includes("第二轮问题")); assert.ok(result.markdown.includes("第二轮回答"));
  assert.ok(!result.markdown.includes("内部推理")); assert.equal(result.partial, false);
  assert.throws(() => selectMessages([["user", "question"]], "2"), /范围/);
});
