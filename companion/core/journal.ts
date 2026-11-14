/** Durable write-ahead operations. Adapters must provide exclusive atomic create. */
import { assertBinding, commit, hash, publish } from "./protocol.ts";
import type { Binding, Commit, Transport } from "./protocol.ts";

export interface JournalDisk {
  read(key: string): Promise<Uint8Array | null>;
  create(key: string, bytes: Uint8Array): Promise<void>; // identical retry OK, different bytes forbidden
  list(prefix: string): Promise<string[]>;
}
export type Operation = {
  version: 1; path: string; expectedHash: string | null;
  record: Commit; body: number[] | null; backup: number[] | null; checksum?: string;
};
export interface LocalFiles {
  read(path: string): Promise<Uint8Array | null>;
  /** Must atomically compare current bytes and replace/remove, or fail without changing anything. */
  compareAndWrite(path: string, expected: Uint8Array | null, body: Uint8Array | null): Promise<boolean>;
}
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const bytes = (value: unknown) => encoder.encode(JSON.stringify(value));

export function validateNotePath(path: string): void {
  const parts = path.split("/");
  if (!path.endsWith(".md") || path.length > 500 || path.normalize("NFC") !== path ||
      /[\\:\x00-\x1f<>"|?*]/.test(path) || parts.some(p => !p || p === "." || p === ".." ||
        p.startsWith(".") || p.toLowerCase() === "_torch" || /[. ]$/.test(p) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(p))) {
    throw new Error("Unsafe or unsupported note path");
  }
}

async function contentHash(body: Uint8Array | null): Promise<string | null> {
  return body === null ? null : hash(body);
}

export async function prepare(disk: JournalDisk, binding: Binding, path: string,
    record: Commit, expected: Uint8Array | null, body: Uint8Array | null): Promise<string> {
  validateNotePath(path);
  if ((body?.byteLength ?? 0) > 1048576 || (expected?.byteLength ?? 0) > 1048576) throw new Error("Note exceeds journal budget");
  assertBinding(binding, record.revision.binding);
  if ((await commit(record.revision)).id !== record.id) throw new Error("Commit integrity mismatch");
  if (record.revision.content !== await contentHash(body)) throw new Error("Operation body does not match revision");
  const operation: Operation = { version: 1, path, expectedHash: await contentHash(expected), record,
    body: body === null ? null : [...body], backup: expected === null ? null : [...expected] };
  operation.checksum = await hash(bytes(operation));
  await disk.create(`operations/${record.id}.json`, bytes(operation));
  return record.id;
}

export async function loadOperation(disk: JournalDisk, binding: Binding, id: string): Promise<Operation> {
  if (!/^[a-f0-9]{64}$/.test(id)) throw new Error("Invalid operation id");
  const raw = await disk.read(`operations/${id}.json`);
  if (!raw) throw new Error("Operation missing");
  if (raw.byteLength > 12 * 1048576) throw new Error("Operation envelope exceeds budget");
  const operation = JSON.parse(decoder.decode(raw)) as Operation;
  const checksum = operation.checksum;
  delete operation.checksum;
  if (checksum !== await hash(bytes(operation))) throw new Error("Operation envelope checksum mismatch");
  validateNotePath(operation.path);
  assertBinding(binding, operation.record.revision.binding);
  if (operation.version !== 1 || operation.record.id !== id ||
      (await commit(operation.record.revision)).id !== id) throw new Error("Invalid persisted operation");
  for (const array of [operation.body, operation.backup]) {
    if (array !== null && (!Array.isArray(array) || array.some(n => !Number.isInteger(n) || n < 0 || n > 255))) {
      throw new Error("Corrupt persisted bytes");
    }
  }
  if (operation.record.revision.content !== await contentHash(operation.body === null ? null : new Uint8Array(operation.body)) ||
      operation.expectedHash !== await contentHash(operation.backup === null ? null : new Uint8Array(operation.backup))) {
    throw new Error("Persisted operation checksum mismatch");
  }
  return operation;
}

export async function uploadPending(disk: JournalDisk, transport: Transport, binding: Binding, id: string): Promise<void> {
  const op = await loadOperation(disk, binding, id);
  const uploaded = await disk.read(`uploaded/${id}`);
  if (uploaded) {
    if (decoder.decode(uploaded) !== id) throw new Error("Corrupt upload acknowledgement");
    return;
  }
  await publish(transport, binding, op.record, op.body === null ? undefined : new Uint8Array(op.body));
  // A crash after remote success but before this marker causes a safe immutable retry.
  await disk.create(`uploaded/${id}`, encoder.encode(id));
}

export async function applyPending(disk: JournalDisk, files: LocalFiles, binding: Binding, id: string): Promise<string> {
  const op = await loadOperation(disk, binding, id);
  const body = op.body === null ? null : new Uint8Array(op.body);
  const current = await files.read(op.path);
  const currentHash = await contentHash(current);
  const applied = await disk.read(`applied/${id}`);
  if (applied) {
    if (decoder.decode(applied) !== id) throw new Error("Corrupt local acknowledgement");
    return currentHash === op.record.revision.content ? "applied" : "changed_after_apply";
  }
  if (currentHash === op.record.revision.content) {
    await disk.create(`applied/${id}`, encoder.encode(id));
    return "recovered";
  }
  if (currentHash !== op.expectedHash) return "conflict_preserved";
  // The complete prior bytes already exist durably inside operations/<id>.json.
  if (!await files.compareAndWrite(op.path, current, body)) return "conflict_preserved";
  await disk.create(`applied/${id}`, encoder.encode(id));
  return "applied";
}

export async function pendingOperations(disk: JournalDisk): Promise<string[]> {
  return (await disk.list("operations/")).map(path => {
    const match = /^operations\/([a-f0-9]{64})\.json$/.exec(path);
    if (!match) throw new Error("Unexpected journal entry");
    return match[1];
  }).sort();
}
