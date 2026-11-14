import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve, sep } from "node:path";
import { FileJournal } from "../node/files.ts";
import { prepare, applyPending, uploadPending, loadOperation, pendingOperations, validateNotePath } from "../core/journal.ts";
import type { JournalDisk, LocalFiles } from "../core/journal.ts";
import { commit, hash } from "../core/protocol.ts";
import type { Binding, Commit, Transport } from "../core/protocol.ts";

const binding: Binding = { protocol: 1, workspace: "test-workspace", account: "test-account", remoteRoot: "/apps/test-journal" };
const encode = (s: string) => new TextEncoder().encode(s);

class Files implements LocalFiles {
  values = new Map<string, Uint8Array>();
  beforeWrite?: () => void;
  async read(path: string) { return this.values.get(path)?.slice() ?? null; }
  async compareAndWrite(path: string, expected: Uint8Array | null, body: Uint8Array | null) {
    this.beforeWrite?.();
    // Test model: atomic cooperating-editor transaction, no awaits inside check/write.
    const actual = this.values.get(path) ?? null;
    if (JSON.stringify(actual ? [...actual] : null) !== JSON.stringify(expected ? [...expected] : null)) return false;
    if (body === null) this.values.delete(path); else this.values.set(path, body.slice());
    return true;
  }
}
async function fixture(t: { after: (fn: () => Promise<void>) => void }) {
  const root = await mkdtemp(join(tmpdir(), "torch-journal-"));
  t.after(async () => {
    const target = resolve(root);
    if (!target.startsWith(resolve(tmpdir()) + sep + "torch-journal-")) throw new Error("Refusing unsafe test cleanup");
    await rm(target, { recursive: true, force: true });
  });
  return { root, disk: new FileJournal(root), files: new Files() };
}
async function record(body: Uint8Array | null, operation = "one"): Promise<Commit> {
  return commit({ protocol: 1, binding, file: "diary", device: "phone", operation,
    parents: [], kind: body === null ? "delete" : "put", content: body === null ? null : await hash(body) });
}

test("persisted operation reopens after process state is discarded", async t => {
  const { root, disk } = await fixture(t);
  const r = await record(encode("new"));
  await prepare(disk, binding, "日记/今天.md", r, encode("old"), encode("new"));
  const reopened = new FileJournal(root);
  assert.deepEqual(await pendingOperations(reopened), [r.id]);
  const saved = await loadOperation(reopened, binding, r.id);
  assert.deepEqual(saved.backup, [...encode("old")]);
});

test("crash after local write before acknowledgement recovers without overwriting later edits", async t => {
  const { disk, files } = await fixture(t);
  files.values.set("日记.md", encode("old"));
  const r = await record(encode("new"));
  await prepare(disk, binding, "日记.md", r, encode("old"), encode("new"));
  let fail = true;
  const fault: JournalDisk = { read: k => disk.read(k), list: p => disk.list(p), create: async (k, v) => {
    if (k.startsWith("applied/") && fail) { fail = false; throw new Error("crash"); }
    return disk.create(k, v);
  } };
  await assert.rejects(applyPending(fault, files, binding, r.id), /crash/);
  assert.deepEqual(await files.read("日记.md"), encode("new"));
  assert.equal(await applyPending(disk, files, binding, r.id), "recovered");
  files.values.set("日记.md", encode("new user edit"));
  assert.equal(await applyPending(disk, files, binding, r.id), "changed_after_apply");
  assert.deepEqual(await files.read("日记.md"), encode("new user edit"));
});

test("edit during download/write planning is preserved", async t => {
  const { disk, files } = await fixture(t);
  files.values.set("日记.md", encode("old"));
  const r = await record(encode("remote"));
  await prepare(disk, binding, "日记.md", r, encode("old"), encode("remote"));
  files.beforeWrite = () => files.values.set("日记.md", encode("user draft"));
  assert.equal(await applyPending(disk, files, binding, r.id), "conflict_preserved");
  assert.deepEqual(await files.read("日记.md"), encode("user draft"));
});

test("explicit deletion backs up old bytes and does not delete modified content", async t => {
  const { disk, files } = await fixture(t);
  files.values.set("日记.md", encode("changed"));
  const r = await record(null);
  await prepare(disk, binding, "日记.md", r, encode("old"), null);
  assert.equal(await applyPending(disk, files, binding, r.id), "conflict_preserved");
  assert.deepEqual((await loadOperation(disk, binding, r.id)).backup, [...encode("old")]);
});

test("lost upload acknowledgement retries same immutable operation", async t => {
  const { root, disk } = await fixture(t);
  const body = encode("outbox");
  const r = await record(body);
  await prepare(disk, binding, "日记.md", r, null, body);
  const objects = new Map<string, Uint8Array>(), commits = new Set<string>();
  let lost = true;
  const transport: Transport = {
    putObject: async (id, b) => { objects.set(id, b); },
    getObject: async id => objects.get(id)!,
    putCommit: async c => { commits.add(c.id); if (lost) { lost = false; throw new Error("response lost"); } },
    listCommits: async () => []
  };
  await assert.rejects(uploadPending(disk, transport, binding, r.id), /response lost/);
  await uploadPending(new FileJournal(root), transport, binding, r.id);
  assert.equal(commits.size, 1);
  assert.ok(await disk.read(`uploaded/${r.id}`));
});

test("operation path/binding/checksum changes fail closed", async t => {
  const { root, disk } = await fixture(t);
  const r = await record(encode("new"));
  await prepare(disk, binding, "日记.md", r, null, encode("new"));
  await assert.rejects(loadOperation(disk, { ...binding, remoteRoot: "/apps/other" }, r.id), /Binding changed/);
  const path = join(root, "operations", `${r.id}.json`);
  const json = JSON.parse(await readFile(path, "utf8"));
  json.path = "other.md";
  await writeFile(path, JSON.stringify(json));
  await assert.rejects(loadOperation(disk, binding, r.id), /checksum/);
});

test("unsafe and incompatible filenames never enter operation journal", () => {
  for (const path of ["../a.md", "/a.md", "C:/a.md", ".obsidian/a.md", "_Torch/a.md", "_torch/a.md", "CON.md", "a /b.md", "a\\b.md", "a?.md"]) {
    assert.throws(() => validateNotePath(path), /Unsafe/);
  }
});

test("same commit cannot be re-targeted to another file", async t => {
  const { disk } = await fixture(t);
  const body = encode("content");
  const r = await record(body);
  await prepare(disk, binding, "a.md", r, null, body);
  await assert.rejects(prepare(disk, binding, "b.md", r, null, body), /Immutable journal/);
});

test("corrupted completion marker cannot suppress work", async t => {
  const { disk, files } = await fixture(t);
  const r = await record(encode("new"));
  await prepare(disk, binding, "note.md", r, null, encode("new"));
  await disk.create(`applied/${r.id}`, encode("wrong acknowledgement"));
  await assert.rejects(applyPending(disk, files, binding, r.id), /Corrupt local acknowledgement/);
  assert.equal(await files.read("note.md"), null);
});
