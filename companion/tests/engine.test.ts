import { test } from "node:test";
import assert from "node:assert/strict";
import { SyncEngine } from "../core/engine.ts";
import type { SyncState } from "../core/engine.ts";
import type { Binding, Commit, Transport } from "../core/protocol.ts";
const encode = (s: string) => new TextEncoder().encode(s);
const binding: Binding = { protocol: 1, workspace: "test", account: "test", remoteRoot: "/apps/test" };
class Remote implements Transport {
  objects = new Map<string, Uint8Array>(); commits = new Map<string, Commit>();
  async putObject(k: string, b: Uint8Array) { this.objects.set(k, b.slice()); }
  async getObject(k: string) { if (!this.objects.has(k)) throw new Error("missing"); return this.objects.get(k)!.slice(); }
  async putCommit(c: Commit) { this.commits.set(c.id, structuredClone(c)); }
  async listCommits() { return [...this.commits.values()]; }
}
function device(remote: Remote, name: string) {
  const files = new Map<string, Uint8Array>(), journal = new Map<string, Uint8Array>();
  let state: SyncState | null = null;
  const notes = {
    list: async () => [...files.keys()], read: async (p: string) => files.get(p)?.slice() ?? null,
    create: async (p: string, b: Uint8Array) => { if (files.has(p)) return false; files.set(p, b.slice()); return true; }
    ,rename: async (from: string, to: string) => { if (!files.has(from) || files.has(to)) return false; files.set(to, files.get(from)!); files.delete(from); return true; }
  };
  const options = { remote, device: name, binding, notes,
    state: { load: async () => state && structuredClone(state), save: async (s: SyncState) => { state = structuredClone(s); } },
    journal: { read: async (p: string) => journal.get(p) ?? null, list: async (p: string) => [...journal.keys()].filter(k => k.startsWith(p)),
      create: async (p: string, b: Uint8Array) => { const old = journal.get(p); if (old) assert.deepEqual(old, b); else journal.set(p, b.slice()); } },
    atomic: { read: notes.read, compareAndWrite: async (p: string, expected: Uint8Array | null, body: Uint8Array | null) => {
      if (JSON.stringify([...(files.get(p) ?? [])]) !== JSON.stringify([...(expected ?? [])])) return false;
      if (body === null) files.delete(p); else files.set(p, body.slice()); return true;
    } }
  };
  return { files, options, engine: new SyncEngine(options), readState: () => state };
}

test("excluded pending edits remain private and do not block other notes", async () => {
  const remote = new Remote(), a = device(remote, "a");
  a.files.set("private.md", encode("private"));
  const put = remote.putObject.bind(remote); remote.putObject = async () => { throw new Error("offline"); };
  await assert.rejects(a.engine.sync());
  remote.putObject = put;
  const notes = { ...a.options.notes, accepts: (path: string) => path !== "private.md", list: async () => [...a.files.keys()].filter(p => p !== "private.md"), read: async (path: string) => { if (path === "private.md") throw new Error("excluded read"); return a.options.notes.read(path); } };
  a.files.set("public.md", encode("public"));
  await new SyncEngine({ ...a.options, notes }).sync();
  assert.equal(remote.commits.size, 1); assert.equal(a.readState()!.pending.length, 1);
  assert.equal([...remote.commits.values()][0].revision.path, "public.md");
});

test("remote rename concurrent with an offline edit preserves both bodies", async () => {
  const remote = new Remote(), a = device(remote, "a"), b = device(remote, "b");
  a.files.set("old.md", encode("base")); await a.engine.sync(); await b.engine.sync();
  a.files.set("new.md", a.files.get("old.md")!); a.files.delete("old.md");
  b.files.set("old.md", encode("offline edit"));
  await a.engine.sync([{ from: "old.md", to: "new.md" }]);
  const result = await b.engine.sync();
  assert.deepEqual(b.files.get("new.md"), encode("offline edit"));
  assert.ok(result.issues.some(i => i.status === "conflict_preserve_all"));
  assert.ok([...remote.objects.values()].some(bytes => new TextDecoder().decode(bytes) === "base"));
  assert.ok([...remote.objects.values()].some(bytes => new TextDecoder().decode(bytes) === "offline edit"));
});

test("scope excludes remote bodies from downloading even during a complete metadata scan", async () => {
  const remote = new Remote(), a = device(remote, "a"), b = device(remote, "b");
  a.files.set("private.md", encode("private")); await a.engine.sync();
  remote.getObject = async () => { throw new Error("private body must not be requested"); };
  const notes = { ...b.options.notes, accepts: () => false };
  assert.deepEqual((await new SyncEngine({ ...b.options, notes }).sync()).issues, []);
});
test("case-colliding names are held for review instead of uploaded or overwritten", async () => {
  const remote = new Remote(), a = device(remote, "a");
  a.files.set("note.md", encode("one")); a.files.set("NOTE.md", encode("two"));
  const result = await a.engine.sync();
  assert.equal(remote.commits.size, 0);
  assert.equal(result.issues.filter(i => i.status === "case_or_unicode_path_collision").length, 2);
});
test("missing remote history is a visible error, never a successful empty sync", async () => {
  const remote = new Remote(), a = device(remote, "a");
  a.files.set("note.md", encode("text")); await a.engine.sync(); remote.commits.clear();
  const result = await a.engine.sync();
  assert.ok(result.issues.some(i => i.status === "remote_history_missing"));
  assert.deepEqual(a.files.get("note.md"), encode("text"));
});
test("two devices converge new and edited notes without re-uploading unchanged content", async () => {
  const remote = new Remote(), a = device(remote, "a"), b = device(remote, "b");
  a.files.set("日记.md", encode("first")); await a.engine.sync(); await b.engine.sync();
  assert.deepEqual(b.files.get("日记.md"), encode("first"));
  a.files.set("日记.md", encode("edited")); await a.engine.sync(); await b.engine.sync();
  assert.deepEqual(b.files.get("日记.md"), encode("edited"));
  await b.engine.sync(); assert.equal(remote.commits.size, 2);
});
test("offline edits retain two branches; explicit user resolution converges", async () => {
  const remote = new Remote(), a = device(remote, "a"), b = device(remote, "b");
  a.files.set("日记.md", encode("first")); await a.engine.sync(); await b.engine.sync();
  a.files.set("日记.md", encode("A")); b.files.set("日记.md", encode("B"));
  await a.engine.sync(); const result = await b.engine.sync();
  const issue = result.issues.find(i => i.status === "conflict_preserve_all")!;
  assert.ok(issue); assert.deepEqual(a.files.get("日记.md"), encode("A")); assert.deepEqual(b.files.get("日记.md"), encode("B"));
  await b.engine.resolve(issue.file, issue.path, encode("merged")); await b.engine.sync(); await a.engine.sync();
  assert.deepEqual(a.files.get("日记.md"), encode("merged")); assert.deepEqual(b.files.get("日记.md"), encode("merged"));
});
test("same filename independently created on two devices stays visible as identity collision", async () => {
  const remote = new Remote(), a = device(remote, "a"), b = device(remote, "b");
  a.files.set("same.md", encode("A")); b.files.set("same.md", encode("B"));
  await a.engine.sync(); const result = await b.engine.sync();
  assert.ok(result.issues.some(i => i.status === "same_path_distinct_files"));
  assert.deepEqual(b.files.get("same.md"), encode("B")); assert.equal(remote.commits.size, 2);
});
test("pending intent survives an upload failure and resumes without duplicate commits", async () => {
  const remote = new Remote(), a = device(remote, "a");
  const publish = remote.putCommit.bind(remote); let fail = true;
  remote.putCommit = async c => { await publish(c); if (fail) { fail = false; throw new Error("response lost"); } };
  a.files.set("note.md", encode("text")); await assert.rejects(a.engine.sync());
  assert.equal(a.readState()!.pending.length, 1);
  await new SyncEngine(a.options).sync(); assert.equal(remote.commits.size, 1); assert.equal(a.readState()!.pending.length, 0);
});

test("matching initial copy deleted locally is not silently resurrected", async () => {
  const remote = new Remote(), a = device(remote, "a"), b = device(remote, "b");
  a.files.set("note.md", encode("text")); await a.engine.sync();
  b.files.set("note.md", encode("text")); await b.engine.sync(); // adopts without a download journal
  b.files.delete("note.md");
  const result = await b.engine.sync(); assert.ok(result.issues.some(i => i.status === "local_missing_requires_review"));
  assert.equal(b.files.has("note.md"), false);
  const file = Object.values(b.readState()!.tracked)[0].file;
  await b.engine.resolve(file, "note.md", null); await b.engine.sync();
  assert.equal(b.files.has("note.md"), false);
});

test("explicit offline rename retains file identity and propagates without duplicating a business note", async () => {
  const remote = new Remote(), a = device(remote, "a"), b = device(remote, "b");
  a.files.set("old.md", encode("text")); await a.engine.sync(); await b.engine.sync();
  const file = a.readState()!.tracked["old.md"].file;
  a.files.set("new.md", a.files.get("old.md")!); a.files.delete("old.md");
  await a.engine.sync([{ from: "old.md", to: "new.md" }]); await b.engine.sync();
  assert.equal(a.readState()!.tracked["new.md"].file, file); assert.equal(b.files.has("old.md"), false);
  assert.deepEqual(b.files.get("new.md"), encode("text")); assert.equal(remote.commits.size, 2);
});

test("same-name independent notes can be separated by explicitly choosing a new path", async () => {
  const remote = new Remote(), a = device(remote, "a"), b = device(remote, "b");
  a.files.set("same.md", encode("A")); b.files.set("same.md", encode("B"));
  await a.engine.sync(); const issues = (await b.engine.sync()).issues;
  const conflict = issues.find(i => i.status === "same_path_distinct_files")!;
  await assert.rejects(b.engine.resolve(conflict.file, "same.md", encode("A")), /另一篇/);
  await b.engine.resolve(conflict.file, "other.md", encode("A")); await b.engine.sync(); await a.engine.sync();
  assert.deepEqual(b.files.get("same.md"), encode("B")); assert.deepEqual(b.files.get("other.md"), encode("A"));
});

test("resolution cannot silently include a newer version the user has not reviewed", async () => {
  const remote = new Remote(), a = device(remote, "a"), b = device(remote, "b");
  a.files.set("note.md", encode("first")); await a.engine.sync(); await b.engine.sync();
  const tracked = b.readState()!.tracked["note.md"], reviewed = [...tracked.heads];
  a.files.set("note.md", encode("new unseen version")); await a.engine.sync();
  const before = remote.commits.size;
  await assert.rejects(b.engine.resolve(tracked.file, "note.md", encode("chosen before update"), reviewed), /重新预览/);
  assert.equal(remote.commits.size, before);
});
