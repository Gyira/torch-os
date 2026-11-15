import { inspect, hash } from "./protocol.ts";
import type { Binding, Transport } from "./protocol.ts";
import { validateNotePath } from "./journal.ts";
/** Derivative export, never a sync baseline or an implicit rollback. */
export async function readableExport(remote: Transport, binding: Binding, budget = 100 * 1048576) {
  const view = await inspect(remote, binding, () => false);
  if (view.pending.length) throw new Error("历史不完整，不能声称完整恢复");
  const files: { path: string; body: Uint8Array }[] = [], deleted: string[] = [], conflicts: string[] = [];
  const used = new Set<string>(); let bytes = 0;
  for (const [file, heads] of view.heads) for (const head of heads) {
    const path = head.revision.path;
    if (!path) throw new Error("旧版本缺少路径，需手动核对恢复");
    validateNotePath(path);
    if (!head.revision.content) { deleted.push(path); continue; }
    let target = path;
    if (heads.length > 1 || used.has(path.toLocaleLowerCase("en-US"))) { target = `conflicts/${file}/${head.id}/${path}`; conflicts.push(target); }
    used.add(path.toLocaleLowerCase("en-US"));
    const body = await remote.getObject(head.revision.content);
    if (await hash(body) !== head.revision.content) throw new Error("恢复正文校验失败");
    bytes += body.length; if (bytes > budget) throw new Error("恢复超过本次预算，请在电脑上分批导出");
    files.push({ path: target, body });
  }
  return { files, manifest: { version: 1, workspace_id: binding.workspace, exported_at: new Date().toISOString(), bytes, deleted, conflicts, derivative_only: true } };
}
