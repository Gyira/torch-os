/** Platform-neutral immutable version protocol. No Obsidian, Node or Baidu APIs. */
export type Binding = { protocol: 1; workspace: string; account: string; remoteRoot: string };
export type Revision = {
  protocol: 1; binding: Binding; file: string; device: string; operation: string;
  parents: string[]; kind: "put" | "delete"; content: string | null;
};
export type Commit = { id: string; revision: Revision };
export interface Transport {
  putObject(hash: string, bytes: Uint8Array): Promise<void>;
  getObject(hash: string): Promise<Uint8Array>;
  putCommit(commit: Commit): Promise<void>;
  listCommits(): Promise<Commit[]>;
}
const encoder = new TextEncoder();
const identifier = /^[A-Za-z0-9_-]{1,128}$/;
const digest = /^[a-f0-9]{64}$/;

export async function hash(bytes: Uint8Array): Promise<string> {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map(b => b.toString(16).padStart(2, "0")).join("");
}

function bindingKey(b: Binding): string {
  if (b.protocol !== 1 || !identifier.test(b.workspace) || !b.account || b.account.length > 256 ||
      !b.remoteRoot.startsWith("/apps/") || b.remoteRoot.includes("\\") ||
      b.remoteRoot.split("/").some(p => p === "." || p === ".." || !p && b.remoteRoot.indexOf("//") >= 0)) {
    throw new Error("Invalid workspace/account/remote binding");
  }
  return JSON.stringify([b.protocol, b.workspace, b.account, b.remoteRoot]);
}

export function assertBinding(expected: Binding, actual: Binding): void {
  if (bindingKey(expected) !== bindingKey(actual)) throw new Error("Binding changed; explicit re-pairing required");
}

function canonical(r: Revision): string {
  bindingKey(r.binding);
  if (r.protocol !== 1 || !identifier.test(r.file) || !identifier.test(r.device) || !identifier.test(r.operation) ||
      !Array.isArray(r.parents) || r.parents.length > 128 || r.parents.some(p => !digest.test(p)) ||
      new Set(r.parents).size !== r.parents.length ||
      !["put", "delete"].includes(r.kind) ||
      (r.kind === "put" ? !digest.test(r.content ?? "") : r.content !== null)) {
    throw new Error("Invalid revision");
  }
  return JSON.stringify({ protocol: 1,
    binding: [r.binding.protocol, r.binding.workspace, r.binding.account, r.binding.remoteRoot],
    file: r.file, device: r.device, operation: r.operation,
    parents: [...r.parents].sort(), kind: r.kind, content: r.content });
}

export async function commit(revision: Revision): Promise<Commit> {
  // Copy caller-owned values; no mutable global 'latest manifest'.
  const copy = structuredClone(revision);
  copy.parents.sort();
  return { id: await hash(encoder.encode(canonical(copy))), revision: copy };
}

export async function publish(transport: Transport, expected: Binding, record: Commit, body?: Uint8Array): Promise<void> {
  assertBinding(expected, record.revision.binding);
  if ((await commit(record.revision)).id !== record.id) throw new Error("Commit integrity mismatch");
  if (record.revision.kind === "put") {
    if (!body || await hash(body) !== record.revision.content) throw new Error("Body integrity mismatch");
    await transport.putObject(record.revision.content!, body);
    if (await hash(await transport.getObject(record.revision.content!)) !== record.revision.content) {
      throw new Error("Uploaded body not verified; commit not published");
    }
  }
  await transport.putCommit(record);
}

export type View = {
  heads: Map<string, Commit[]>; pending: string[]; records: Map<string, Commit>;
};

export async function inspect(transport: Transport, expected: Binding): Promise<View> {
  bindingKey(expected);
  const input = await transport.listCommits(); // failures must propagate; never become empty state
  if (input.length > 10000) throw new Error("Commit scan limit exceeded; no destructive action allowed");
  const records = new Map<string, Commit>();
  const operations = new Map<string, string>();
  for (const supplied of input) {
    const record = structuredClone(supplied);
    assertBinding(expected, record.revision.binding);
    if ((await commit(record.revision)).id !== record.id) throw new Error("Commit integrity mismatch");
    const operationKey = JSON.stringify([record.revision.device, record.revision.operation]);
    const previous = operations.get(operationKey);
    if (previous && previous !== record.id) throw new Error("Operation replay has different content");
    operations.set(operationKey, record.id);
    records.set(record.id, record);
  }
  const ready = new Set<string>();
  const verified = new Set<string>();
  // Iterative closure avoids recursive stack growth and tolerates incomplete/out-of-order listings.
  for (let changed = true; changed;) {
    changed = false;
    for (const [id, r] of records) {
      if (ready.has(id)) continue;
      for (const parent of r.revision.parents) {
        const found = records.get(parent);
        if (found && found.revision.file !== r.revision.file) throw new Error("Cross-file ancestor");
      }
      if (!r.revision.parents.every(parent => ready.has(parent))) continue;
      ready.add(id);
      changed = true;
    }
  }
  const parentIds = new Set<string>();
  for (const id of ready) for (const parent of records.get(id)!.revision.parents) parentIds.add(parent);
  const heads = new Map<string, Commit[]>();
  for (const id of [...ready].sort()) {
    if (parentIds.has(id)) continue;
    const r = records.get(id)!;
    const group = heads.get(r.revision.file) ?? [];
    group.push(r);
    heads.set(r.revision.file, group);
  }
  // Ancestors need only metadata for causality. Download current/conflicting heads,
  // not every historical body on a mobile device. Historical reads verify on demand.
  for (const group of heads.values()) for (const record of group) {
    const content = record.revision.content;
    if (content && !verified.has(content)) {
      if (await hash(await transport.getObject(content)) !== content) throw new Error("Stored object corrupted");
      verified.add(content);
    }
  }
  return { heads, records, pending: [...records.keys()].filter(id => !ready.has(id)).sort() };
}

export function decision(view: View, file: string): { action: string; heads: string[] } {
  const group = view.heads.get(file) ?? [];
  if (view.pending.some(id => view.records.get(id)?.revision.file === file)) {
    return { action: "wait_incomplete_history", heads: group.map(r => r.id) };
  }
  if (!group.length) return { action: "no_remote_instruction", heads: [] };
  const values = new Set(group.map(r => r.revision.kind + ":" + r.revision.content));
  return { action: group.length === 1 ? (group[0].revision.kind === "delete" ? "explicit_delete" : "remote_version")
    : values.size === 1 ? "equal_content_needs_join" : "conflict_preserve_all", heads: group.map(r => r.id) };
}

export function assertLocalUnchanged(expectedHash: string | null, actualHash: string | null): void {
  if (expectedHash !== actualHash) throw new Error("Local file changed after planning; preserve and replan");
}
