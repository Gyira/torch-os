/** Small-object XPan implementation; upstream references/notice in third-party/. */
import { md5 } from "../vendor/md5.ts";
import { buildMultipartFormData } from "../vendor/multipart.ts";
import { BaiduReader } from "./reader.ts";
import type { TokenProvider, Entry } from "./reader.ts";

export type Request = { url: string; method: string; headers?: Record<string, string>; body?: string | ArrayBuffer };
export type Reply = { status: number; bytes: ArrayBuffer };
export interface Wire { send(request: Request): Promise<Reply>; }
export interface BlobStore { get(key: string): Promise<Uint8Array>; put(key: string, bytes: Uint8Array): Promise<void>; list(prefix: string): Promise<string[]>; }
const decode = (response: Reply): Record<string, unknown> => {
  if (response.bytes.byteLength > 2 * 1048576) throw new Error("JSON response budget exceeded");
  let value;
  try { value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(response.bytes)); }
  catch { throw new Error("Invalid Baidu JSON response"); }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid API response");
  return value;
};
export class BaiduApi implements BlobStore {
  wire: Wire; tokens: TokenProvider; root: string;
  private entries = new Map<string, Entry>();
  constructor(wire: Wire, tokens: TokenProvider, root: string) {
    new BaiduReader({ get: async () => { throw new Error("validation only"); } }, tokens, root);
    this.wire = wire; this.tokens = tokens; this.root = root;
  }
  private path(key: string) {
    if (!/^(objects\/[a-f0-9]{64}|commits\/[a-f0-9]{64}\.json|binding)$/.test(key)) throw new Error("Invalid protocol object key");
    return this.root + "/" + key;
  }
  private async call(endpoint: string, params: Record<string, string>, form?: URLSearchParams, binary?: { body: ArrayBuffer; contentType: string }) {
    for (let attempt = 0; attempt < 2; attempt++) {
      let token: string;
      try { token = await this.tokens.token(attempt > 0); } catch { throw new Error("Baidu authorization failed"); }
      const url = endpoint + "?" + new URLSearchParams({ ...params, access_token: token });
      let reply: Reply;
      try { reply = await this.wire.send({ url, method: form || binary ? "POST" : "GET",
        ...(form ? { body: form.toString(), headers: { "Content-Type": "application/x-www-form-urlencoded" } } : {}),
        ...(binary ? { body: binary.body, headers: { "Content-Type": binary.contentType } } : {}) }); }
      catch { throw new Error("Baidu request failed"); }
      if (reply.status !== 200) throw new Error(`Baidu HTTP ${reply.status}`);
      const data = decode(reply);
      if (data.errno === -6 && attempt === 0) continue;
      // PCS upload replies use error_code instead of errno, with md5 on success.
      if (data.errno !== 0 && !(params.method === "upload" && data.errno === undefined && (data.error_code === undefined || data.error_code === 0) && typeof data.md5 === "string")) {
        throw new Error(`Baidu API rejected operation (${Number.isInteger(data.errno) ? data.errno : "invalid response"})`);
      }
      return data;
    }
    throw new Error("Baidu authorization expired");
  }
  async initializeDirectories(): Promise<void> {
    const endpoint = "https://pan.baidu.com/rest/2.0/xpan/file";
    // Explicit setup action, never inferred from an error during normal listing.
    for (const path of [this.root, this.root + "/objects", this.root + "/commits"]) {
      try { await this.call(endpoint, { method: "create" }, new URLSearchParams({ path, isdir: "1", rtype: "0" })); }
      catch (error) { if (!(error instanceof Error) || error.message !== "Baidu API rejected operation (-8)") throw error; }
    }
    const existing = await this.call(endpoint, { method: "list", dir: this.root, start: "0", limit: "1000", order: "name" });
    if (!Array.isArray(existing.list) || existing.list.length > 3 || existing.has_more && existing.has_more !== 0 ||
        existing.list.some((entry: Entry) => !entry || ![this.root + "/objects", this.root + "/commits", this.root + "/binding"].includes(entry.path) || entry.isdir !== (entry.path.endsWith("/binding") ? 0 : 1))) throw new Error("独立同步目录存在其他文件或枚举不完整，拒绝初始化");
  }
  async list(prefix: string): Promise<string[]> {
    if (prefix !== "commits/" && prefix !== "objects/") throw new Error("Invalid list prefix");
    const reader = new BaiduReader({ get: async url => {
      let reply: Reply;
      try { reply = await this.wire.send({ url, method: "GET" }); } catch { throw new Error("Baidu list failed"); }
      return { status: reply.status, data: decode(reply) };
    } }, this.tokens, this.root);
    const entries = await reader.listDirectory(prefix.slice(0, -1) as "objects" | "commits");
    for (const entry of entries) this.entries.set(entry.path, entry);
    return entries.map(entry => { const key = entry.path.slice(this.root.length + 1); this.path(key); return key; });
  }
  async get(key: string): Promise<Uint8Array> {
    const path = this.path(key), slash = path.lastIndexOf("/");
    let entry = this.entries.get(path);
    if (!entry) {
      const listing = await this.call("https://pan.baidu.com/rest/2.0/xpan/file", { method: "list", dir: path.slice(0, slash), order: "name", start: "0", limit: "1000" });
      if (!Array.isArray(listing.list)) throw new Error("Invalid object lookup");
      for (const item of listing.list) if (typeof item?.path === "string") this.entries.set(item.path, item);
      entry = this.entries.get(path);
    }
    if (!entry && key !== "binding") {
      // Full paginated lookup; never call a partial first-page miss a deleted object.
      const reader = new BaiduReader({ get: async url => { const r = await this.wire.send({ url, method: "GET" }); return { status: r.status, data: decode(r) }; } }, this.tokens, this.root);
      entry = (await reader.listDirectory(key.startsWith("objects/") ? "objects" : "commits")).find(e => e.path === path);
    }
    if (!entry) throw new Error("Protocol object missing");
    if (entry.isdir !== 0 || entry.path !== path || !/^\d+$/.test(String(entry.fs_id)) || !Number.isSafeInteger(entry.size) || entry.size < 0 || entry.size > 2 * 1048576) throw new Error("Invalid protocol object metadata or size");
    const metadata = await this.call("https://pan.baidu.com/rest/2.0/xpan/multimedia", { method: "filemetas", fsids: JSON.stringify([entry.fs_id]), dlink: "1" });
    if (!Array.isArray(metadata.list) || metadata.list.length !== 1) throw new Error("Invalid download metadata");
    const item = metadata.list[0];
    if (item.path !== path || String(item.fs_id) !== String(entry.fs_id)) throw new Error("Object metadata mismatch");
    const url = new URL(item.dlink);
    if (url.protocol !== "https:" || url.username || url.password || url.port ||
        !(url.hostname.endsWith(".baidupcs.com") || url.hostname === "d.pcs.baidu.com")) throw new Error("Download host not allowed");
    url.searchParams.set("access_token", await this.tokens.token());
    let response: Reply;
    try { response = await this.wire.send({ url: url.toString(), method: "GET", headers: { "User-Agent": "pan.baidu.com" } }); }
    catch { throw new Error("Baidu download failed"); }
    if (response.status !== 200 || response.bytes.byteLength > 2 * 1048576) throw new Error("Download failed or exceeds budget");
    return new Uint8Array(response.bytes);
  }
  async put(key: string, bytes: Uint8Array): Promise<void> {
    const path = this.path(key);
    if (bytes.byteLength > 2 * 1048576) throw new Error("Object exceeds upload budget");
    const body = bytes.slice().buffer, block = md5(body), blocks = JSON.stringify([block]);
    const pre = await this.call("https://pan.baidu.com/rest/2.0/xpan/file", { method: "precreate" },
      new URLSearchParams({ path, size: String(bytes.byteLength), isdir: "0", autoinit: "1", rtype: "3", block_list: blocks }));
    if (typeof pre.uploadid !== "string" || !Array.isArray(pre.block_list) || pre.block_list.some((n: unknown) => n !== 0)) throw new Error("Invalid precreate response");
    if (pre.block_list.length) {
      const slice = await this.call("https://d.pcs.baidu.com/rest/2.0/pcs/superfile2", { method: "upload", type: "tmpfile", uploadid: pre.uploadid, partseq: "0", path }, undefined, buildMultipartFormData(body));
      if (slice.md5 !== block) throw new Error("Upload slice checksum mismatch");
    }
    const created = await this.call("https://pan.baidu.com/rest/2.0/xpan/file", { method: "create" },
      new URLSearchParams({ path, size: String(bytes.byteLength), isdir: "0", rtype: "3", uploadid: pre.uploadid, block_list: blocks }));
    if (created.path !== path || created.size !== bytes.byteLength) throw new Error("Created object mismatch");
    this.entries.delete(path); // read back the actual newly published object
  }
}
