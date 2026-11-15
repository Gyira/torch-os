/** Serialized sync coordinator; all identities/intents are persisted before side effects. */
import { assertBinding, commit, decision, hash, inspect } from "./protocol.ts";
import type { Binding, Commit, Transport, View } from "./protocol.ts";
import { prepare, uploadPending, applyPending, validateNotePath } from "./journal.ts";
import type { JournalDisk, LocalFiles } from "./journal.ts";
import { materialize } from "./materialize.ts";
import type { NoteDestination } from "./materialize.ts";

export type Tracked = { file: string; path: string; heads: string[]; hash: string | null };
export type Intent = { record: Commit; before: number[] | null; body: number[] | null; localAlreadySaved: boolean; oldPath?: string };
export type SyncState = { version: 1; binding: Binding; device: string; tracked: Record<string, Tracked>; pending: Intent[] };
export interface StateStore { load(): Promise<SyncState | null>; save(state: SyncState): Promise<void>; }
export interface SyncNotes extends NoteDestination { list(): Promise<string[]>; accepts?(path: string): boolean; canCreate?(path: string, bytes: Uint8Array): boolean; rename?(from: string, to: string): Promise<boolean>; }
export type Issue = { file: string; path: string; status: string; heads: string[]; operation?: string };
const array = (value: number[] | null) => value === null ? null : new Uint8Array(value);

function ancestor(view: View, head: string, wanted: string): boolean {
  const stack = [head], seen = new Set<string>();
  while (stack.length) {
    const id = stack.pop()!;
    if (id === wanted) return true;
    if (seen.has(id)) continue;
    seen.add(id);
    stack.push(...(view.records.get(id)?.revision.parents ?? []));
  }
  return false;
}

export class SyncEngine {
  state: StateStore; journal: JournalDisk; notes: SyncNotes; remote: Transport; binding: Binding; device: string;
  atomic?: LocalFiles;
  running = false;
  // Validate all metadata/causality, download a body only when this device needs it.
  private inspect() { return inspect(this.remote, this.binding, () => false); }
  constructor(options: { state: StateStore; journal: JournalDisk; notes: SyncNotes; remote: Transport; binding: Binding; device: string; atomic?: LocalFiles }) {
    this.state = options.state; this.journal = options.journal; this.notes = options.notes; this.remote = options.remote;
    this.binding = structuredClone(options.binding); this.device = options.device; this.atomic = options.atomic;
  }
  private async load(): Promise<SyncState> {
    const saved = await this.state.load();
    if (!saved) return { version: 1, binding: this.binding, device: this.device, tracked: {}, pending: [] };
    assertBinding(this.binding, saved.binding);
    if (saved.version !== 1 || saved.device !== this.device || !saved.tracked || !Array.isArray(saved.pending)) throw new Error("Invalid device sync state");
    return saved;
  }
  private async flush(state: SyncState): Promise<void> {
    for (;;) {
      const index = state.pending.findIndex(i => !i.record.revision.path || !this.notes.accepts || this.notes.accepts(i.record.revision.path));
      if (index < 0) return; // excluded pending work is retained, never silently uploaded or deleted
      const intent = state.pending[index], record = intent.record, path = record.revision.path;
      if (!path) throw new Error("Intent path missing");
      await prepare(this.journal, this.binding, path, record, array(intent.before), array(intent.body));
      await uploadPending(this.journal, this.remote, this.binding, record.id);
      state.tracked[path] = { file: record.revision.file, path, heads: [record.id], hash: intent.localAlreadySaved ? record.revision.content : intent.before === null ? null : await hash(new Uint8Array(intent.before)) };
      if (intent.oldPath && intent.oldPath !== path) delete state.tracked[intent.oldPath];
      state.pending.splice(index, 1);
      await this.state.save(structuredClone(state));
    }
  }
  private async enqueue(state: SyncState, path: string, file: string, parents: string[], body: Uint8Array | null, before: Uint8Array | null, localAlreadySaved = true, oldPath?: string) {
    const record = await commit({ protocol: 1, binding: this.binding, device: this.device, file, path,
      operation: crypto.randomUUID(), parents, kind: body === null ? "delete" : "put", content: body === null ? null : await hash(body) });
    state.pending.push({ record, before: before === null ? null : [...before], body: body === null ? null : [...body], localAlreadySaved, ...(oldPath ? { oldPath } : {}) });
    await this.state.save(structuredClone(state));
    await this.flush(state);
  }
  async sync(renames: { from: string; to: string }[] = []): Promise<{ issues: Issue[]; tracked: number }> {
    if (this.running) throw new Error("Sync already running");
    this.running = true;
    try {
      const state = await this.load();
      // Unknown remote state must not generate deletes or new local baselines.
      let view = await this.inspect();
      await this.flush(state);
      const issues: Issue[] = [];
      const collisionPaths = new Set<string>();
      const checkCollisions = async () => {
        const spellings = new Map<string, Set<string>>();
        for (const path of [...await this.notes.list(), ...[...view.heads.values()].flat().map(r => r.revision.path).filter((p): p is string => !!p)]) {
          if (this.notes.accepts && !this.notes.accepts(path)) continue;
          const key = path.normalize("NFC").toLocaleLowerCase("en-US"), group = spellings.get(key) ?? new Set();
          group.add(path); spellings.set(key, group);
        }
        for (const group of spellings.values()) if (group.size > 1) for (const path of group) collisionPaths.add(path);
      };
      await checkCollisions();
      for (const original of Object.values(state.tracked)) {
        if (collisionPaths.has(original.path) || this.notes.accepts && !this.notes.accepts(original.path)) continue;
        let target = original.path;
        for (const event of renames) if (event.from === target) { validateNotePath(event.to); target = event.to; }
        if (collisionPaths.has(target) || this.notes.accepts && !this.notes.accepts(target)) continue;
        if (target !== original.path && !state.tracked[target] && await this.notes.read(original.path) === null) {
          const moved = await this.notes.read(target);
          if (moved !== null) await this.enqueue(state, target, original.file, original.heads, moved, moved, true, original.path);
        }
      }
      view = await this.inspect();
      for (const original of Object.values(state.tracked)) {
        if (collisionPaths.has(original.path) || this.notes.accepts && !this.notes.accepts(original.path)) continue;
        const heads = view.heads.get(original.file);
        if (heads?.length !== 1 || !heads[0].revision.path || heads[0].revision.kind !== "put") continue;
        const target = heads[0].revision.path;
        if (collisionPaths.has(target)) continue;
        if (target === original.path || state.tracked[target] || !original.heads.every(p => ancestor(view, heads[0].id, p)) ||
            this.notes.accepts && !this.notes.accepts(target)) continue;
        validateNotePath(target);
        const old = await this.notes.read(original.path), existing = await this.notes.read(target);
        let moved = old === null && existing !== null;
        if (old !== null && existing === null && this.notes.rename) {
          const backup = await hash(new TextEncoder().encode("rename:" + heads[0].id + ":" + await hash(old)));
          await this.journal.create(`candidates/${backup}.md`, old);
          moved = await this.notes.rename(original.path, target);
        }
        if (moved) {
          state.tracked[target] = { ...original, path: target }; delete state.tracked[original.path]; await this.state.save(structuredClone(state));
        }
      }
      const paths = await this.notes.list();
      for (const path of paths) {
        if (collisionPaths.has(path)) continue;
        validateNotePath(path);
        const bytes = await this.notes.read(path);
        if (bytes === null) continue;
        const digest = await hash(bytes);
        const tracked = state.tracked[path];
        if (tracked?.hash === digest) continue;
        if (!tracked) {
          const matching = [...view.heads.values()].flat().filter(r => r.revision.path === path);
          if (matching.length === 1 && matching[0].revision.content === digest) {
            const r = matching[0];
            state.tracked[path] = { file: r.revision.file, path, heads: [r.id], hash: digest };
            await this.state.save(structuredClone(state));
            continue;
          }
        }
        await this.enqueue(state, path, tracked?.file ?? crypto.randomUUID(), tracked?.heads ?? [], bytes, bytes);
      }
      view = await this.inspect();
      await checkCollisions();
      for (const path of collisionPaths) issues.push({ file: state.tracked[path]?.file ?? "", path, status: "case_or_unicode_path_collision", heads: [] });
      for (const [file, heads] of view.heads) {
        const paths = [...new Set(heads.map(r => r.revision.path))];
        if (paths.length !== 1 || !paths[0]) {
          issues.push({ file, path: paths.find(Boolean) ?? "", status: "path_or_rename_requires_review", heads: heads.map(r => r.id) }); continue;
        }
        const path = paths[0]; validateNotePath(path);
        if (collisionPaths.has(path)) continue;
        if (this.notes.accepts && !this.notes.accepts(path)) continue;
        const choice = decision(view, file);
        if (choice.action === "conflict_preserve_all" || choice.action === "wait_incomplete_history") {
          issues.push({ file, path, status: choice.action, heads: choice.heads }); continue;
        }
        const record = heads[0], tracked = state.tracked[path];
        const local = await this.notes.read(path), digest = local === null ? null : await hash(local);
        if (tracked && tracked.file !== file) { issues.push({ file, path, status: "same_path_distinct_files", heads: choice.heads }); continue; }
        if (tracked && local === null && tracked.hash !== null && record.revision.kind === "put") {
          issues.push({ file, path, status: "local_missing_requires_review", heads: choice.heads }); continue;
        }
        if (tracked && !tracked.heads.every(parent => heads.some(r => ancestor(view, r.id, parent)))) {
          issues.push({ file, path, status: "history_not_descendant", heads: choice.heads }); continue;
        }
        if (digest === record.revision.content) {
          state.tracked[path] = { file, path, heads: choice.heads, hash: digest }; continue;
        }
        const body = record.revision.content === null ? null : await this.remote.getObject(record.revision.content);
        if (body && await hash(body) !== record.revision.content) throw new Error("Remote body checksum mismatch");
        if (body && !local && this.notes.canCreate && !this.notes.canCreate(path, body)) { issues.push({ file, path, status: "attachment_budget_waiting", heads: choice.heads }); continue; }
        if (!await this.journal.read(`operations/${record.id}.json`)) await prepare(this.journal, this.binding, path, record, local, body);
        let status: string;
        if (this.atomic && tracked && digest === tracked.hash && record.revision.kind === "put") {
          status = await applyPending(this.journal, this.atomic, this.binding, record.id);
        } else {
          status = (await materialize(this.journal, this.notes, this.binding, record.id)).status;
        }
        if (["created", "recovered", "applied"].includes(status)) state.tracked[path] = { file, path, heads: choice.heads, hash: record.revision.content };
        else issues.push({ file, path, status, heads: choice.heads, operation: record.id });
      }
      for (const item of Object.values(state.tracked)) {
        if (this.notes.accepts && !this.notes.accepts(item.path)) continue;
        if (item.heads.some(id => !view.records.has(id))) issues.push({ file: item.file, path: item.path, status: "remote_history_missing", heads: item.heads });
        if (item.hash !== null && await this.notes.read(item.path) === null && !issues.some(i => i.path === item.path && i.status === "local_missing_requires_review")) issues.push({ file: item.file, path: item.path, status: "local_missing_requires_review", heads: item.heads });
      }
      for (const id of view.pending) {
        const r = view.records.get(id)!;
        if (r.revision.path && (!this.notes.accepts || this.notes.accepts(r.revision.path)) && !issues.some(i => i.file === r.revision.file)) issues.push({ file: r.revision.file, path: r.revision.path, status: "wait_incomplete_history", heads: [] });
      }
      await this.state.save(structuredClone(state));
      return { issues, tracked: Object.keys(state.tracked).length };
    } finally { this.running = false; }
  }
  /** Explicit user choice. All branch heads become parents, originals stay in history. */
  async resolve(file: string, path: string, body: Uint8Array | null, expectedHeads?: string[]): Promise<void> {
    if (this.running) throw new Error("Sync already running");
    this.running = true;
    try {
      validateNotePath(path);
      const state = await this.load(), view = await this.inspect();
      const heads = view.heads.get(file);
      if (!heads?.length || decision(view, file).action === "wait_incomplete_history") throw new Error("Cannot resolve incomplete history");
      if (expectedHeads && JSON.stringify([...expectedHeads].sort()) !== JSON.stringify(heads.map(h => h.id).sort())) throw new Error("冲突版本已变化，请重新预览后再确认");
      if (state.tracked[path] && state.tracked[path].file !== file) throw new Error("目标属于另一篇笔记，请指定新的相对路径保留双方");
      const previous = Object.values(state.tracked).find(t => t.file === file && t.path !== path);
      if (previous && await this.notes.read(previous.path) !== null) {
        if (await this.notes.read(path) !== null || !this.notes.rename) throw new Error("改名目标已存在或设备不支持安全改名");
        const original = (await this.notes.read(previous.path))!;
        await this.journal.create(`candidates/${await hash(new TextEncoder().encode("resolve:" + file + ":" + await hash(original)))}.md`, original);
        if (!await this.notes.rename(previous.path, path)) throw new Error("改名期间目标发生变化，请重新检查");
      }
      await this.enqueue(state, path, file, heads.map(r => r.id), body, await this.notes.read(path), false, previous?.path);
    } finally { this.running = false; }
  }
}
