import { test } from "node:test";
import assert from "node:assert/strict";
import { commit, publish, hash, inspect, decision, assertLocalUnchanged } from "../core/protocol.ts";
import type { Binding, Commit, Transport } from "../core/protocol.ts";

const binding: Binding = { protocol: 1, workspace: "test-workspace", account: "test-account", remoteRoot: "/apps/torch-tests" };
const encode = (s: string) => new TextEncoder().encode(s);
class Memory implements Transport {
  objects = new Map<string, Uint8Array>();
  commits = new Map<string, Commit>();
  async putObject(id: string, bytes: Uint8Array) {
    const old = this.objects.get(id);
    if (old) assert.deepEqual(old, bytes);
    this.objects.set(id, bytes.slice());
  }
  async getObject(id: string) { const v = this.objects.get(id); if (!v) throw new Error("missing"); return v.slice(); }
  async putCommit(c: Commit) {
    const old = this.commits.get(c.id);
    if (old) assert.deepEqual(old, c);
    this.commits.set(c.id, structuredClone(c));
  }
  async listCommits() { return [...this.commits.values()].reverse(); }
}
async function edit(t: Memory, text: string, operation: string, parents: string[] = [], device = "phone") {
  const body = encode(text);
  const r = await commit({ protocol: 1, binding, file: "diary", device, operation, parents, kind: "put", content: await hash(body) });
  await publish(t, binding, r, body);
  return r;
}

test("concurrent offline edits preserve both heads; explicit resolution joins them", async () => {
  const t = new Memory();
  const base = await edit(t, "base", "base");
  const [phone, desktop] = await Promise.all([edit(t, "phone", "p1", [base.id]), edit(t, "desktop", "d1", [base.id], "desktop")]);
  assert.equal(decision(await inspect(t, binding), "diary").action, "conflict_preserve_all");
  const joined = await edit(t, "user merged", "join", [phone.id, desktop.id]);
  assert.deepEqual(decision(await inspect(t, binding), "diary").heads, [joined.id]);
  assert.equal(t.commits.size, 4);
});

test("retry after response loss is idempotent", async () => {
  const t = new Memory();
  const r = await edit(t, "text", "one");
  await publish(t, binding, r, encode("text"));
  assert.equal(t.commits.size, 1);
});

test("deletion concurrent with edit is a visible conflict", async () => {
  const t = new Memory();
  const base = await edit(t, "base", "base");
  const del = await commit({ ...base.revision, operation: "delete", parents: [base.id], kind: "delete", content: null });
  await publish(t, binding, del);
  await edit(t, "new local", "new", [base.id], "desktop");
  assert.equal(decision(await inspect(t, binding), "diary").action, "conflict_preserve_all");
});

test("incomplete list never turns missing history into delete", async () => {
  const t = new Memory();
  const base = await edit(t, "base", "base");
  const del = await commit({ ...base.revision, operation: "del", parents: [base.id], kind: "delete", content: null });
  await publish(t, binding, del);
  t.commits.delete(base.id);
  assert.equal(decision(await inspect(t, binding), "diary").action, "wait_incomplete_history");
});

test("empty remote means no instruction; listing failure propagates", async () => {
  const t = new Memory();
  assert.equal(decision(await inspect(t, binding), "diary").action, "no_remote_instruction");
  t.listCommits = async () => { throw new Error("network"); };
  await assert.rejects(inspect(t, binding), /network/);
});

test("wrong account or root requires re-pairing", async () => {
  const t = new Memory();
  await edit(t, "a", "one");
  await assert.rejects(inspect(t, { ...binding, remoteRoot: "/apps/wrong" }), /Binding changed/);
  await assert.rejects(inspect(t, { ...binding, account: "other" }), /Binding changed/);
});

test("unverified upload publishes no commit", async () => {
  const t = new Memory();
  t.getObject = async () => encode("corrupt");
  await assert.rejects(edit(t, "a", "one"), /not verified/);
  assert.equal(t.commits.size, 0);
});

test("same-size edits are distinguished by hash and in-flight edits abort", async () => {
  assert.notEqual(await hash(encode("aaa")), await hash(encode("bbb")));
  assert.throws(() => assertLocalUnchanged("old", "new"), /Local file changed/);
});

test("same operation with different body is rejected", async () => {
  const t = new Memory();
  await edit(t, "a", "one");
  await edit(t, "b", "one");
  await assert.rejects(inspect(t, binding), /Operation replay/);
});

test("equal content keeps both causal heads until a join commit", async () => {
  const t = new Memory();
  await edit(t, "same", "a");
  await edit(t, "same", "b", [], "desktop");
  assert.equal(decision(await inspect(t, binding), "diary").action, "equal_content_needs_join");
});

test("mobile inspection fetches current bodies, not every historical body", async () => {
  const t = new Memory();
  const base = await edit(t, "old large body", "base");
  const latest = await edit(t, "current", "current", [base.id]);
  const get = t.getObject.bind(t);
  const fetched: string[] = [];
  t.getObject = async id => { fetched.push(id); return get(id); };
  const view = await inspect(t, binding);
  assert.deepEqual(fetched, [latest.revision.content]);
  assert.equal(decision(view, "diary").action, "remote_version");
});
