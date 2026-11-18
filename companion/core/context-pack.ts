import { hash } from "./protocol.ts";
const roots = ["人生路", "自我档案", "生活运营", "知识库"];
const excluded = /微信|回收|备份|backup|trash|credential|secret|apikey|api_key|冲突/i;
const key = /(?:sk-[A-Za-z0-9_-]{16,}|-----BEGIN .*PRIVATE KEY|(?:api[_-]?key|access[_-]?token|password|密码)\s*[:=]\s*['"]?[A-Za-z0-9_+/=-]{12,})/i;
export function contextSource(path: string) {
  const parts = path.split("/");
  return roots.includes(parts[0]) && path.endsWith(".md") && !parts.some(p => !p || p === ".." || p === "_Torch" || p === "_系统" || p.startsWith(".") || excluded.test(p));
}
export type Pack = { exported_at: string; sources: { path: string; sha256: string; bytes: number }[]; files: { name: string; markdown: string }[]; source_hash: string; uploaded: false };
export async function contextPack(paths: string[], read: (path: string) => Promise<Uint8Array | null>, budget = 2000000): Promise<Pack> {
  if (!paths.length || paths.length > 1000 || budget < 1000 || budget > 8000000) throw new Error("请明确选源并使用有效预算");
  const groups = new Map(roots.map(root => [root, [] as string[]])), sources: Pack["sources"] = [];
  let size = 0;
  for (const path of [...new Set(paths)].sort()) {
    if (!contextSource(path)) throw new Error("不能导出系统、备份、微信或凭据文件：" + path);
    const bytes = await read(path);
    if (!bytes) throw new Error("手机尚未取得源文件：" + path);
    size += bytes.length;
    if (size > budget) throw new Error("已超出资料包预算，请减少选源；正文未截断");
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    if (key.test(text)) throw new Error("疑似包含密钥，请检查：" + path);
    sources.push({ path, sha256: await hash(bytes), bytes: bytes.length });
    groups.get(path.split("/")[0])!.push(`## 来源：${path}\n\n${text}\n`);
  }
  const exported_at = new Date().toISOString(), files: Pack["files"] = [];
  for (const [i, root] of roots.entries()) if (groups.get(root)!.length) files.push({ name: `${String(i + 1).padStart(2, "0")}-${root}.md`,
    markdown: `# ${root}\n\n资料导出于：${exported_at}\n附件未包含。区分用户原文与 AI 建议；以源笔记日期为准。\n\n` + groups.get(root)!.join("\n\n") });
  if (files.reduce((n, f) => n + new TextEncoder().encode(f.markdown).length, 0) > budget) throw new Error("包含来源信息后超过预算，请减少选源");
  return { exported_at, sources, files, source_hash: await hash(new TextEncoder().encode(JSON.stringify(sources))), uploaded: false };
}
export async function changedSources(pack: Pack, read: (path: string) => Promise<Uint8Array | null>) {
  const changed = [];
  for (const source of pack.sources) { const bytes = await read(source.path); if (!bytes || await hash(bytes) !== source.sha256) changed.push(source.path); }
  return changed;
}
