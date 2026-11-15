import { assertBinding, commit, hash } from "./protocol.ts";
import type { Binding, Commit, Transport } from "./protocol.ts";
import type { BlobStore } from "../baidu/api.ts";
const encoder = new TextEncoder(), decoder = new TextDecoder("utf-8", { fatal: true });
export class SecureTransport implements Transport {
  blobs: BlobStore; binding: Binding; key: CryptoKey;
  private cachedCommits = new Map<string, Commit>();
  private constructor(blobs: BlobStore, binding: Binding, key: CryptoKey) { this.blobs = blobs; this.binding = binding; this.key = key; }
  static async open(blobs: BlobStore, binding: Binding, password: string): Promise<SecureTransport> {
    if (password.length < 12) throw new Error("同步密码至少 12 位");
    const material = await crypto.subtle.importKey("raw", encoder.encode(password), "PBKDF2", false, ["deriveKey"]);
    const salt = encoder.encode("torch-sync-v1:" + binding.workspace);
    const key = await crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations: 210000, hash: "SHA-256" }, material, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
    return new SecureTransport(blobs, structuredClone(binding), key);
  }
  private async seal(bytes: Uint8Array) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const encrypted = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: encoder.encode(this.binding.workspace) }, this.key, bytes));
    const result = new Uint8Array(13 + encrypted.length); result[0] = 1; result.set(iv, 1); result.set(encrypted, 13); return result;
  }
  private async unseal(bytes: Uint8Array) {
    if (bytes[0] !== 1 || bytes.length < 29) throw new Error("Unsupported encrypted object");
    try { return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: bytes.slice(1, 13), additionalData: encoder.encode(this.binding.workspace) }, this.key, bytes.slice(13))); }
    catch { throw new Error("同步密码错误或密文损坏"); }
  }
  async initializeEmpty(): Promise<void> {
    if ((await this.blobs.list("commits/")).length || (await this.blobs.list("objects/")).length) throw new Error("只能初始化独立空测试目录");
    // Setup must be performed once on the first device, then shared via pairing.
    try { await this.blobs.get("binding"); throw new Error("目录已配对，禁止重新初始化"); }
    catch (error) { if (!(error instanceof Error) || !error.message.includes("Protocol object missing")) throw error; }
    await this.blobs.put("binding", await this.seal(encoder.encode(JSON.stringify(this.binding))));
    await this.verifyBinding();
  }
  async verifyBinding(): Promise<void> {
    const value = JSON.parse(decoder.decode(await this.unseal(await this.blobs.get("binding"))));
    assertBinding(this.binding, value);
  }
  async putObject(id: string, bytes: Uint8Array) {
    await this.verifyBinding();
    if (await hash(bytes) !== id) throw new Error("Object address mismatch");
    await this.blobs.put(`objects/${id}`, await this.seal(bytes));
  }
  async getObject(id: string) {
    if (!/^[a-f0-9]{64}$/.test(id)) throw new Error("Invalid object address");
    const bytes = await this.unseal(await this.blobs.get(`objects/${id}`));
    if (await hash(bytes) !== id) throw new Error("Object checksum mismatch"); return bytes;
  }
  async putCommit(record: Commit) {
    await this.verifyBinding(); assertBinding(this.binding, record.revision.binding);
    if ((await commit(record.revision)).id !== record.id) throw new Error("Commit checksum mismatch");
    await this.blobs.put(`commits/${record.id}.json`, await this.seal(encoder.encode(JSON.stringify(record))));
  }
  async listCommits() {
    await this.verifyBinding();
    const records: Commit[] = [];
    for (const path of await this.blobs.list("commits/")) {
      if (!/^commits\/[a-f0-9]{64}\.json$/.test(path)) throw new Error("Unexpected commit path");
      const cached = this.cachedCommits.get(path);
      if (cached) { records.push(structuredClone(cached)); continue; }
      const record = JSON.parse(decoder.decode(await this.unseal(await this.blobs.get(path)))) as Commit;
      if (path !== `commits/${record.id}.json` || (await commit(record.revision)).id !== record.id) throw new Error("Commit address mismatch");
      assertBinding(this.binding, record.revision.binding); this.cachedCommits.set(path, structuredClone(record)); records.push(record);
    }
    return records;
  }
}
