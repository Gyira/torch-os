// Synthetic coordinator benchmark; excludes network, Obsidian and Android I/O.
import { SyncEngine } from "../core/engine.ts";
import { performance } from "node:perf_hooks";
const binding = { protocol: 1, workspace: "synthetic", account: "synthetic", remoteRoot: "/apps/benchmark" };
const objects = new Map(), commits = new Map(); let reads = 0;
const remote = {
  putObject: async (key, bytes) => objects.set(key, bytes.slice()),
  getObject: async key => { reads++; if (!objects.has(key)) throw new Error("missing"); return objects.get(key).slice(); },
  putCommit: async record => commits.set(record.id, structuredClone(record)),
  listCommits: async () => [...commits.values()]
};
function device(name) {
  const files = new Map(), journal = new Map(); let state = null;
  const notes = { list: async () => [...files.keys()], read: async key => files.get(key)?.slice() ?? null,
    create: async (key, bytes) => { if (files.has(key)) return false; files.set(key, bytes.slice()); return true; } };
  const atomic = { read: notes.read, compareAndWrite: async (key, before, body) => {
    if (Buffer.compare(files.get(key) ?? Buffer.alloc(0), before ?? Buffer.alloc(0))) return false;
    if (body) files.set(key, body.slice()); else files.delete(key); return true;
  } };
  return { files, engine: new SyncEngine({ remote, binding, device: name, notes, atomic,
    state: { load: async () => state && structuredClone(state), save: async value => { state = structuredClone(value); } },
    journal: { read: async key => journal.get(key) ?? null, list: async prefix => [...journal.keys()].filter(key => key.startsWith(prefix)),
      create: async (key, bytes) => { if (journal.has(key) && Buffer.compare(journal.get(key), bytes)) throw new Error("immutable mismatch"); journal.set(key, bytes.slice()); } }
  }) };
}
const a = device("desktop"), b = device("phone-model"), encode = new TextEncoder();
for (let i = 0; i < 448; i++) a.files.set(`03_知识库/00_收件箱/synthetic-${i}.md`, encode.encode(`合成样例 ${i}\n` + "合成记录".repeat(200)));
const sourceBytes = [...a.files.values()].reduce((n, bytes) => n + bytes.length, 0), steps = [];
for (const [name, engine] of [["initial_publish", a.engine], ["initial_receive", b.engine], ["unchanged_receive", b.engine]]) {
  const start = performance.now(), before = reads, result = await engine.sync();
  steps.push({ name, milliseconds: Math.round(performance.now() - start), object_reads: reads - before, issues: result.issues.length });
}
console.log(JSON.stringify({ synthetic_only: true, excludes: ["Baidu", "Obsidian SDK", "Android filesystem"], notes: 448, bytes: sourceBytes, commits: commits.size, steps }, null, 2));
