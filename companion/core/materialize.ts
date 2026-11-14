/** Conservative real-file materialization where the editor offers no atomic CAS. */
import { hash } from "./protocol.ts";
import type { Binding } from "./protocol.ts";
import { loadOperation } from "./journal.ts";
import type { JournalDisk } from "./journal.ts";

export interface NoteDestination {
  read(path: string): Promise<Uint8Array | null>;
  /** Publish complete bytes only if path is absent. Never replace a file. */
  create(path: string, body: Uint8Array): Promise<boolean>;
}
export type Materialization = { status: string; path: string; operation: string; candidate?: string };
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });

export async function materialize(disk: JournalDisk, notes: NoteDestination,
    binding: Binding, id: string): Promise<Materialization> {
  const op = await loadOperation(disk, binding, id);
  const current = await notes.read(op.path);
  const currentHash = current === null ? null : await hash(current);
  const result = (status: string, candidate?: string): Materialization => ({ status, path: op.path, operation: id, ...(candidate ? { candidate } : {}) });
  const applied = await disk.read(`applied/${id}`);
  if (applied) {
    if (decoder.decode(applied) !== id) throw new Error("Corrupt local acknowledgement");
    return result(currentHash === op.record.revision.content ? "applied" : "changed_after_apply");
  }
  if (currentHash === op.record.revision.content) {
    await disk.create(`applied/${id}`, encoder.encode(id));
    return result("recovered");
  }
  if (op.body === null) return result("delete_requires_review");
  const body = new Uint8Array(op.body);
  // An external deletion after planning must not silently resurrect the old note.
  let raced = false;
  if (current === null && op.expectedHash === null) {
    if (await notes.create(op.path, body)) {
      const written = await notes.read(op.path);
      const same = written !== null && await hash(written) === op.record.revision.content;
      await disk.create(`applied/${id}`, encoder.encode(id));
      return result(same ? "created" : "changed_after_apply");
    }
    raced = true;
  }
  // Includes regular remote updates and a concurrent same-name create. Nothing is overwritten.
  // Candidates stay in local state, outside the indexed/synchronized note tree.
  const candidate = `candidates/${id}.md`;
  await disk.create(candidate, body);
  return result(!raced && currentHash === op.expectedHash ? "update_requires_review" : "conflict_preserved", candidate);
}
