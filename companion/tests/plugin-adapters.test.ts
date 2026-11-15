import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";
import { runInNewContext } from "node:vm";
import { fileURLToPath } from "node:url";
import type { Plugin } from "obsidian";
import type { Binding } from "../core/protocol.ts";

class FakeFile { path: string; stat = { size: 1 }; constructor(path: string) { this.path = path; } }
function setup() {
  const source = buildSync({ entryPoints: [fileURLToPath(new URL("../plugin/adapters.ts", import.meta.url))], bundle: true, platform: "browser", format: "cjs", external: ["obsidian"], write: false }).outputFiles[0].text;
  const module = { exports: {} as typeof import("../plugin/adapters.ts") };
  runInNewContext(source, { module, exports: module.exports, crypto, TextEncoder, TextDecoder, Uint8Array, ArrayBuffer,
    require: (name: string) => { assert.equal(name, "obsidian"); return { TFile: FakeFile, normalizePath: (p: string) => p.replace(/\/+/g, "/").replace(/\/$/, "") }; } });
  const disk = new Map<string, ArrayBuffer>(), notes = new Map<string, string>(), folders = new Set([".obsidian"]);
  let concurrent: string | null = null;
  const vault = {
    configDir: ".obsidian", getMarkdownFiles: () => [...notes.keys()].map(p => new FakeFile(p)),
    getFiles: () => [...notes.keys()].map(p => { const f = new FakeFile(p); f.stat.size = notes.get(p)!.length; return f; }),
    getAbstractFileByPath: (p: string) => notes.has(p) ? new FakeFile(p) : folders.has(p) ? { path: p } : null,
    createFolder: async (p: string) => { folders.add(p); },
    create: async (p: string, body: string) => { if (notes.has(p)) throw new Error("exists"); notes.set(p, body); },
    read: async (file: FakeFile) => notes.get(file.path)!,
    process: async (file: FakeFile, callback: (s: string) => string) => {
      if (concurrent !== null) { notes.set(file.path, concurrent); concurrent = null; }
      const result = callback(notes.get(file.path)!); notes.set(file.path, result); return result;
    },
    adapter: {
      exists: async (p: string) => disk.has(p) || folders.has(p),
      mkdir: async (p: string) => { folders.add(p); },
      writeBinary: async (p: string, b: ArrayBuffer) => { disk.set(p, b); },
      readBinary: async (p: string) => disk.get(p)!,
      stat: async (p: string) => ({ size: disk.get(p)?.byteLength ?? 0 }),
      remove: async (p: string) => { disk.delete(p); },
      rename: async (p: string, to: string) => { disk.set(to, disk.get(p)!); disk.delete(p); },
      list: async (dir: string) => ({ files: [...disk.keys()].filter(p => p.startsWith(dir + "/") && !p.slice(dir.length + 1).includes("/")), folders: [] })
    }
  };
  const plugin = { app: { vault }, manifest: { id: "torch-companion" } } as unknown as Plugin;
  return { classes: module.exports, plugin, notes, concurrent: (v: string) => concurrent = v, disk };
}
test("Obsidian process adapter refuses changed draft and applies unchanged baseline", async () => {
  const s = setup(), notes = new s.classes.VaultNotes(s.plugin);
  const path = "03_知识库/笔记.md", enc = new TextEncoder(); s.notes.set(path, "old");
  s.concurrent("user draft");
  assert.equal(await notes.compareAndWrite(path, enc.encode("old"), enc.encode("remote")), false);
  assert.equal(s.notes.get(path), "user draft");
  assert.equal(await notes.compareAndWrite(path, enc.encode("user draft"), enc.encode("merged")), true);
  assert.equal(s.notes.get(path), "merged");
});
test("plugin journal reopens checksummed snapshots and excludes private files", async () => {
  const s = setup(), journal = new s.classes.PluginJournal(s.plugin), state = new s.classes.PluginState(journal);
  const binding: Binding = { protocol: 1, workspace: "test", account: "test", remoteRoot: "/apps/test" };
  await state.save({ version: 1, binding, device: "device", tracked: {}, pending: [] });
  const reopened = new s.classes.PluginState(new s.classes.PluginJournal(s.plugin));
  assert.equal((await reopened.load())!.device, "device");
  s.notes.set(".obsidian/private.md", "secret"); s.notes.set("_Torch/工作区.md", "metadata"); s.notes.set("03_知识库/a.md", "text");
  const selected = await new s.classes.VaultNotes(s.plugin).list();
  assert.equal(selected.length, 1); assert.equal(selected[0], "03_知识库/a.md");
  const snapshot = [...s.disk.keys()].find(p => p.includes("/states/"))!;
  s.disk.set(snapshot, new TextEncoder().encode("corrupted").buffer);
  await assert.rejects(reopened.load(), /损坏/);
});
test("only linked small attachments are selected and oversized attachments do not block text", async () => {
  const s = setup(), path = "03_知识库/note.md";
  s.notes.set(path, "![[assets/small.png]]"); s.notes.set("assets/small.png", "png"); s.notes.set("assets/huge.pdf", "x".repeat(1048577)); s.notes.set("assets/unrelated.jpg", "other");
  (s.plugin.app as unknown as { metadataCache: unknown }).metadataCache = {
    getFileCache: () => ({ embeds: [{ link: "assets/small.png" }, { link: "assets/huge.pdf" }] }),
    getFirstLinkpathDest: (link: string) => new FakeFile(link)
  };
  const notes = new s.classes.VaultNotes(s.plugin), files = await notes.list();
  assert.ok(files.includes(path)); assert.ok(files.includes("assets/small.png"));
  assert.ok(!files.includes("assets/unrelated.jpg")); assert.ok(!files.includes("assets/huge.pdf"));
  assert.equal(notes.warnings.length, 1);
});
