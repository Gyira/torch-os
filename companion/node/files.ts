/** Desktop filesystem adapter. Not bundled into Android/Obsidian mobile. */
import { mkdir, open, link, unlink, readFile, readdir, lstat } from "node:fs/promises";
import { resolve, sep, dirname } from "node:path";
import { randomUUID } from "node:crypto";
import type { JournalDisk } from "../core/journal.ts";
import { validateNotePath } from "../core/journal.ts";
import type { NoteDestination } from "../core/materialize.ts";

async function safePath(root: string, relative: string): Promise<string> {
  const path = resolve(root, relative);
  if (!path.startsWith(root + sep)) throw new Error("Path escapes root");
  let cursor = path;
  while (true) {
    try { if ((await lstat(cursor)).isSymbolicLink()) throw new Error("Symlink/junction rejected"); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    const parent = dirname(cursor);
    if (parent === cursor) break;
    cursor = parent;
  }
  return path;
}
async function read(path: string, budget: number): Promise<Uint8Array | null> {
  try {
    const handle = await open(path, "r");
    try {
      const stat = await handle.stat();
      if (!stat.isFile() || stat.size > budget) throw new Error("File exceeds read budget or is not a file");
      const bytes = new Uint8Array(Math.min(budget + 1, stat.size + 1));
      let offset = 0;
      while (offset < bytes.byteLength) {
        const { bytesRead } = await handle.read(bytes, offset, bytes.byteLength - offset, null);
        if (!bytesRead) break;
        offset += bytesRead;
      }
      if (offset > budget) throw new Error("File grew beyond read budget");
      if (offset > stat.size) throw new Error("File changed while reading; retry with a fresh snapshot");
      return bytes.slice(0, offset);
    } finally { await handle.close(); }
  }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error; }
}
async function create(root: string, relative: string, bytes: Uint8Array): Promise<boolean> {
  const path = await safePath(root, relative);
  await mkdir(dirname(path), { recursive: true });
  await safePath(root, relative);
  const temp = resolve(dirname(path), `.pending-${randomUUID()}.tmp`);
  try {
    const handle = await open(temp, "wx");
    try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
    await safePath(root, relative);
    try { await link(temp, path); return true; }
    catch (error) { if ((error as NodeJS.ErrnoException).code === "EEXIST") return false; throw error; }
  } finally { await unlink(temp).catch(() => {}); }
}

export class FileJournal implements JournalDisk {
  root: string;
  constructor(root: string) { this.root = resolve(root); }
  private validate(key: string) {
    if (!/^(operations\/[a-f0-9]{64}\.json|candidates\/[a-f0-9]{64}\.md|(?:applied|uploaded)\/[a-f0-9]{64})$/.test(key)) throw new Error("Unsafe journal key");
  }
  async read(key: string) { this.validate(key); return read(await safePath(this.root, key), 12 * 1048576); }
  async create(key: string, bytes: Uint8Array) {
    this.validate(key);
    if (!await create(this.root, key, bytes)) {
      const previous = await this.read(key);
      if (!previous || !Buffer.from(previous).equals(Buffer.from(bytes))) throw new Error("Immutable journal entry differs");
    }
  }
  async list(prefix: string) {
    if (prefix !== "operations/") throw new Error("Unsupported journal prefix");
    // Validate the directory itself via a child path before enumeration.
    await safePath(this.root, "operations/probe");
    try { return (await readdir(resolve(this.root, "operations"))).filter(p => !p.startsWith(".pending-")).map(p => prefix + p); }
    catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return []; throw error; }
  }
}

export class FileNotes implements NoteDestination {
  root: string;
  constructor(root: string, journalRoot: string) {
    this.root = resolve(root);
    const journal = resolve(journalRoot);
    const comparable = process.platform === "win32" ? (s: string) => s.toLowerCase() : (s: string) => s;
    const left = comparable(this.root), right = comparable(journal);
    if (left === right || left.startsWith(right + sep) || right.startsWith(left + sep)) throw new Error("Journal must be outside notes");
  }
  private async requireRoot() {
    if (!(await lstat(this.root)).isDirectory()) throw new Error("Notes root missing or not a directory");
  }
  async read(path: string) { await this.requireRoot(); validateNotePath(path); return read(await safePath(this.root, path), 1048576); }
  async create(path: string, bytes: Uint8Array) {
    await this.requireRoot();
    validateNotePath(path);
    if (bytes.byteLength > 1048576) throw new Error("Note exceeds budget");
    return create(this.root, path, bytes);
  }
}
