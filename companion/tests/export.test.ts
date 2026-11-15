import { test } from "node:test";
import assert from "node:assert/strict";
import { readableExport } from "../core/export.ts";
import { commit, hash } from "../core/protocol.ts";
import type { Commit } from "../core/protocol.ts";
test("readable recovery exports independent conflicts and refuses missing history", async () => {
  const binding = { protocol: 1 as const, workspace: "test", account: "test", remoteRoot: "/apps/test" }, bytes = new TextEncoder().encode("原文"), digest = await hash(bytes);
  const make = (operation: string, parents: string[] = []) => commit({ protocol: 1, binding, device: "a", file: "f", operation, parents, path: "03_知识库/a.md", kind: "put", content: digest });
  let records = [await make("one"), await make("two")];
  const remote = { listCommits: async () => records, getObject: async () => bytes, putObject: async () => {}, putCommit: async (_: Commit) => {} };
  const result = await readableExport(remote, binding);
  assert.equal(result.files.length, 2); assert.equal(result.manifest.conflicts.length, 2);
  assert.notEqual(result.files[0].path, result.files[1].path);
  records = [await make("three", ["a".repeat(64)])];
  await assert.rejects(readableExport(remote, binding), /不完整/);
});
