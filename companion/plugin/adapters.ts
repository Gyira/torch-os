import { Plugin, TFile, normalizePath, requestUrl } from "obsidian";
import type { JournalDisk, LocalFiles } from "../core/journal.ts";
import { validateNotePath } from "../core/journal.ts";
import type { SyncNotes, SyncState, StateStore } from "../core/engine.ts";
import type { Wire, Request } from "../baidu/api.ts";
import { hash } from "../core/protocol.ts";
import { bounded } from "../core/retry.ts";
const encode = (value: string) => new TextEncoder().encode(value);
const decode = (value: Uint8Array) => new TextDecoder("utf-8", { fatal: true }).decode(value);

export class ObsidianWire implements Wire {
  async send(request: Request) {
    const url = new URL(request.url);
    if (url.protocol !== "https:" || url.username || url.password || url.port ||
        !(url.hostname === "pan.baidu.com" || url.hostname === "openapi.baidu.com" || url.hostname === "d.pcs.baidu.com" || url.hostname.endsWith(".baidupcs.com"))) throw new Error("网络目标不在允许范围");
    let response;
    try { response = await bounded(requestUrl({ ...request, throw: false })); } catch { throw new Error("百度网络请求失败或超时；本地记录保留"); }
    if (response.arrayBuffer.byteLength > 2 * 1048576) throw new Error("响应超出大小限制");
    return { status: response.status, bytes: response.arrayBuffer };
  }
}
export class PluginJournal implements JournalDisk {
  plugin: Plugin; root: string;
  constructor(plugin: Plugin) { this.plugin = plugin; this.root = `${plugin.app.vault.configDir}/plugins/${plugin.manifest.id}/local`; }
  private path(key: string) {
    if (!/^(operations\/[a-f0-9]{64}\.json|(?:previews|packs)\/(?:\d{13}-)?[a-f0-9]{64}\.json|candidates\/[a-f0-9]{64}\.md|(?:applied|uploaded)\/[a-f0-9]{64}|states\/\d{12}-[a-f0-9]{64}\.json)$/.test(key)) throw new Error("本机日志路径无效");
    return normalizePath(this.root + "/" + key);
  }
  async read(key: string) {
    const path = this.path(key), adapter = this.plugin.app.vault.adapter;
    if (!await adapter.exists(path)) return null;
    const stat = await adapter.stat(path); if (!stat || stat.size > 12 * 1048576) throw new Error("日志超出预算");
    const bytes = new Uint8Array(await adapter.readBinary(path));
    if (/^(previews|packs)\//.test(key) && await hash(bytes) !== /([a-f0-9]{64})\.json$/.exec(key)![1]) throw new Error("本机预览或资料包记录损坏，停止以保护原文");
    return bytes;
  }
  async create(key: string, bytes: Uint8Array): Promise<void> {
    const adapter = this.plugin.app.vault.adapter, path = this.path(key);
    const old = await this.read(key);
    if (old) { if (await hash(old) !== await hash(bytes)) throw new Error("不可变日志不匹配"); return; }
    const parent = path.slice(0, path.lastIndexOf("/"));
    if (!await adapter.exists(parent)) await adapter.mkdir(parent);
    const temp = parent + "/.pending-" + crypto.randomUUID();
    await adapter.writeBinary(temp, bytes.slice().buffer);
    if (await hash(new Uint8Array(await adapter.readBinary(temp))) !== await hash(bytes)) throw new Error("日志写入校验失败");
    if (await adapter.exists(path)) { await adapter.remove(temp); return this.create(key, bytes); }
    await adapter.rename(temp, path);
    if (await hash((await this.read(key))!) !== await hash(bytes)) throw new Error("日志发布校验失败");
  }
  async list(prefix: string) {
    if (!["operations/", "states/", "previews/", "packs/"].includes(prefix)) throw new Error("日志列表范围无效");
    const adapter = this.plugin.app.vault.adapter, directory = normalizePath(this.root + "/" + prefix);
    if (!await adapter.exists(directory)) return [];
    return (await adapter.list(directory)).files.filter(p => !p.slice(p.lastIndexOf("/") + 1).startsWith(".pending-")).map(p => prefix + p.slice(p.lastIndexOf("/") + 1));
  }
}
export class PluginState implements StateStore {
  journal: PluginJournal; sequence = 0;
  constructor(journal: PluginJournal) { this.journal = journal; }
  async load(): Promise<SyncState | null> {
    const paths = (await this.journal.list("states/")).sort();
    if (!paths.length) return null;
    const sequences = new Set<string>();
    for (const path of paths) { const sequence = path.slice(7, 19); if (sequences.has(sequence)) throw new Error("本机状态出现并行写入分叉，请保留插件目录并停止同步"); sequences.add(sequence); }
    const path = paths[paths.length - 1], match = /^states\/(\d{12})-([a-f0-9]{64})\.json$/.exec(path);
    if (!match) throw new Error("同步状态名称无效");
    const bytes = await this.journal.read(path);
    if (!bytes || await hash(bytes) !== match[2]) throw new Error("同步状态损坏，停止以保护原文");
    this.sequence = Math.max(this.sequence, Number(match[1])); return JSON.parse(decode(bytes));
  }
  async save(state: SyncState) {
    const bytes = encode(JSON.stringify(state)), name = String(++this.sequence).padStart(12, "0");
    await this.journal.create(`states/${name}-${await hash(bytes)}.json`, bytes);
  }
}
export class VaultNotes implements SyncNotes, LocalFiles {
  plugin: Plugin; excludes: string[];
  assets = new Set<string>(); missingNames = new Set<string>();
  blocked = new Set<string>(); warnings: string[] = [];
  assetBudget = 50 * 1048576;
  constructor(plugin: Plugin, excludes: string[] = []) { this.plugin = plugin; this.excludes = excludes; }
  allowed(path: string) {
    if (/^_Torch\/(?:收件|回执)\/[a-f0-9-]{36}\.md$/.test(path)) return true;
    if (this.blocked.has(path)) return false;
    if (this.excludes.some(p => path === p || path.startsWith(p.replace(/\/$/, "") + "/"))) return false;
    if (/\.(png|jpe?g|webp|gif|avif|pdf)$/i.test(path)) return !path.split("/").some(p => p.startsWith(".") || p === "_Torch" || /微信|回收|备份|冲突/.test(p)) && (this.assets.has(path) || this.missingNames.has(path.split("/").at(-1)!));
    return /^(人生路|自我档案|生活运营|知识库)\//.test(path) && path.endsWith(".md") &&
      !path.split("/").some(p => p.startsWith(".") || /回收站|重构备份|冲突|^_系统$/.test(p));
  }
  accepts(path: string) { return this.allowed(path); }
  refreshScope() {
    this.assets.clear(); this.missingNames.clear(); this.blocked.clear(); this.warnings = [];
    const vault = this.plugin.app.vault, metadata = this.plugin.app.metadataCache;
    if (!metadata) return;
    for (const file of vault.getMarkdownFiles().filter(f => this.allowed(f.path) && !f.path.startsWith("_Torch/"))) {
      const cache = metadata.getFileCache(file);
      for (const ref of [...(cache?.embeds ?? []), ...(cache?.links ?? [])]) {
        const target = metadata.getFirstLinkpathDest(ref.link.split("#")[0], file.path);
        if (target && /\.(png|jpe?g|webp|gif|avif|pdf)$/i.test(target.path)) this.assets.add(target.path);
        else {
          let link = ref.link.split("#")[0]; try { link = decodeURIComponent(link); } catch { continue; }
          if (!/\.(png|jpe?g|webp|gif|avif|pdf)$/i.test(link) || /^[a-z]+:/i.test(link)) continue;
          if (!link.includes("/")) this.missingNames.add(link);
          else for (const candidate of [link, file.path.slice(0, file.path.lastIndexOf("/") + 1) + link]) {
            const parts: string[] = [];
            for (const part of candidate.split("/")) { if (part === "..") parts.pop(); else if (part && part !== ".") parts.push(part); }
            this.assets.add(parts.join("/"));
          }
        }
      }
    }
  }
  async list() {
    this.refreshScope();
    const vault = this.plugin.app.vault, files = vault.getFiles ? vault.getFiles() : vault.getMarkdownFiles();
    let used = 0;
    return files.filter(f => this.allowed(f.path)).filter(f => {
      if (f.path.endsWith(".md")) return true;
      if (f.stat.size > 1048576 || used + f.stat.size > this.assetBudget) { this.blocked.add(f.path); this.warnings.push(f.path + "：附件超出预算，保留原件；可手动排除或压缩副本"); return false; }
      used += f.stat.size; return true;
    }).map(f => f.path);
  }
  canCreate(path: string, bytes: Uint8Array) {
    if (path.endsWith(".md")) return true;
    return bytes.length <= 1048576 && this.plugin.app.vault.getFiles().filter(f => !f.path.endsWith(".md") && this.allowed(f.path)).reduce((n, f) => n + f.stat.size, 0) + bytes.length <= this.assetBudget;
  }
  private validate(path: string) { validateNotePath(path); if (!this.allowed(path)) throw new Error("文件不在本设备同步范围"); }
  async read(path: string) {
    this.validate(path); const file = this.plugin.app.vault.getAbstractFileByPath(path);
    if (!file) return null; if (!(file instanceof TFile) || file.stat.size > 1048576) throw new Error("文件无效或超过 1 MiB");
    return path.endsWith(".md") ? encode(await this.plugin.app.vault.read(file)) : new Uint8Array(await this.plugin.app.vault.readBinary(file));
  }
  async create(path: string, body: Uint8Array) {
    this.validate(path); const vault = this.plugin.app.vault;
    if (vault.getAbstractFileByPath(path)) return false;
    const parent = path.slice(0, path.lastIndexOf("/"));
    if (parent && !vault.getAbstractFileByPath(parent)) await vault.createFolder(parent);
    if (body.length > 1048576) throw new Error("文件超过 1 MiB，保留主机原件并用本机排除设置跳过");
    if (!path.endsWith(".md") && vault.getFiles().filter(f => !f.path.endsWith(".md") && this.allowed(f.path)).reduce((n, f) => n + f.stat.size, 0) + body.length > this.assetBudget) throw new Error("附件下载超出 50 MiB 预算，原文件保留");
    try { if (path.endsWith(".md")) await vault.create(path, decode(body)); else await vault.createBinary(path, body.slice().buffer); return true; }
    catch (error) { if (vault.getAbstractFileByPath(path)) return false; throw error; }
  }
  async rename(from: string, to: string) {
    this.validate(from); this.validate(to); const vault = this.plugin.app.vault;
    const file = vault.getAbstractFileByPath(from);
    if (!(file instanceof TFile) || vault.getAbstractFileByPath(to)) return false;
    const parent = to.slice(0, to.lastIndexOf("/")); if (parent && !vault.getAbstractFileByPath(parent)) await vault.createFolder(parent);
    try { await vault.rename(file, to); return true; }
    catch (error) { if (vault.getAbstractFileByPath(to)) return false; throw error; }
  }
  async compareAndWrite(path: string, expected: Uint8Array | null, body: Uint8Array | null) {
    this.validate(path);
    if (expected === null || body === null || !path.endsWith(".md")) return false; // binary replacement/deletion stays reviewable
    const file = this.plugin.app.vault.getAbstractFileByPath(path);
    if (!(file instanceof TFile)) return false;
    const original = decode(expected), replacement = decode(body);
    try {
      await this.plugin.app.vault.process(file, current => { if (current !== original) throw new Error("CONCURRENT_EDIT"); return replacement; });
      return true;
    } catch (error) { if (error instanceof Error && error.message === "CONCURRENT_EDIT") return false; throw error; }
  }
}
