import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile, readFile, readdir } from "node:fs/promises";
import { join, resolve, sep } from "node:path";
import { tmpdir } from "node:os";
import { FileJournal, FileNotes } from "../node/files.ts";
import { materialize } from "../core/materialize.ts";
import { prepare } from "../core/journal.ts";
import type { JournalDisk } from "../core/journal.ts";
import { hash, commit } from "../core/protocol.ts";
import type { Binding } from "../core/protocol.ts";

const binding: Binding = { protocol: 1, workspace: "test", account: "test", remoteRoot: "/apps/test" };
const encode = (s: string) => new TextEncoder().encode(s);
async function setup(t: { after: (fn: () => Promise<void>) => void }, text: string | null, old: string | null = null) {
  const root = await mkdtemp(join(tmpdir(), "torch-materialize-"));
  t.after(async () => {
    const target = resolve(root);
    if (!target.startsWith(resolve(tmpdir()) + sep + "torch-materialize-")) throw new Error("Unsafe cleanup");
    await rm(target, { recursive: true, force: true });
  });
  const notesRoot = join(root, "notes"), stateRoot = join(root, "state");
  await mkdir(notesRoot);
  const disk = new FileJournal(stateRoot), notes = new FileNotes(notesRoot, stateRoot);
  const body = text === null ? null : encode(text);
  const r = await commit({ protocol: 1, binding, file: "note", device: "phone", operation: "one", parents: [],
    kind: body === null ? "delete" : "put", content: body === null ? null : await hash(body) });
  await prepare(disk, binding, "日记.md", r, old === null ? null : encode(old), body);
  return { root, notesRoot, stateRoot, disk, notes, id: r.id };
}

test("new note is published completely; reopening recovers its acknowledgement", async t => {
  const s = await setup(t, "完整正文\n```py\nprint(1)\n```\n");
  assert.equal((await materialize(s.disk, s.notes, binding, s.id)).status, "created");
  assert.equal((await materialize(new FileJournal(s.stateRoot), s.notes, binding, s.id)).status, "applied");
  assert.equal((await readdir(s.notesRoot)).length, 1);
});

test("existing note is never overwritten, even when remote is the only changed side", async t => {
  const s = await setup(t, "远端修改", "原文");
  await writeFile(join(s.notesRoot, "日记.md"), "原文");
  const result = await materialize(s.disk, s.notes, binding, s.id);
  assert.equal(result.status, "update_requires_review");
  assert.equal(await readFile(join(s.notesRoot, "日记.md"), "utf8"), "原文");
  const candidate = await s.disk.read(result.candidate!);
  assert.ok(candidate);
  assert.equal(new TextDecoder().decode(candidate), "远端修改");
  assert.equal(await s.disk.read(`applied/${s.id}`), null);
});

test("same-name creation during planning preserves the user's file and stages remote body", async t => {
  const s = await setup(t, "remote");
  const racing = { read: (p: string) => s.notes.read(p), create: async (p: string, b: Uint8Array) => {
    await writeFile(join(s.notesRoot, p), "user created first");
    return s.notes.create(p, b);
  } };
  const result = await materialize(s.disk, racing, binding, s.id);
  assert.equal(result.status, "conflict_preserved");
  assert.ok(result.candidate);
  assert.equal(await readFile(join(s.notesRoot, "日记.md"), "utf8"), "user created first");
});

test("crash after publishing but before receipt does not duplicate or replace the note", async t => {
  const s = await setup(t, "new");
  const failing: JournalDisk = { read: k => s.disk.read(k), list: p => s.disk.list(p), create: async (k, v) => {
    if (k.startsWith("applied/")) throw new Error("crash");
    return s.disk.create(k, v);
  } };
  await assert.rejects(materialize(failing, s.notes, binding, s.id), /crash/);
  assert.equal((await materialize(s.disk, s.notes, binding, s.id)).status, "recovered");
  assert.equal((await readdir(s.notesRoot)).length, 1);
});

test("remote delete requires review, preserving the live original", async t => {
  const s = await setup(t, null, "original");
  await writeFile(join(s.notesRoot, "日记.md"), "original");
  assert.equal((await materialize(s.disk, s.notes, binding, s.id)).status, "delete_requires_review");
  assert.equal(await readFile(join(s.notesRoot, "日记.md"), "utf8"), "original");
});

test("remote update does not resurrect an externally deleted note", async t => {
  const s = await setup(t, "remote", "old");
  assert.equal((await materialize(s.disk, s.notes, binding, s.id)).status, "conflict_preserved");
  assert.equal((await readdir(s.notesRoot)).length, 0);
});

test("missing notes root fails instead of silently creating a new vault", async t => {
  const s = await setup(t, "new");
  await assert.rejects(materialize(s.disk, new FileNotes(join(s.root, "wrong"), s.stateRoot), binding, s.id));
  assert.equal((await readdir(s.root)).includes("wrong"), false);
});

test("concurrent create operations leave exactly one complete file", async t => {
  const s = await setup(t, "new");
  const results = await Promise.all([s.notes.create("竞争.md", encode("A")), s.notes.create("竞争.md", encode("B"))]);
  assert.equal(results.filter(Boolean).length, 1);
  assert.ok(["A", "B"].includes(await readFile(join(s.notesRoot, "竞争.md"), "utf8")));
  assert.equal((await readdir(s.notesRoot)).length, 1);
});
