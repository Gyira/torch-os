import { test } from "node:test";
import assert from "node:assert/strict";
import { SecureTransport } from "../core/secure-transport.ts";
import { exportPair, importPair } from "../core/pairing.ts";
import { hash, commit } from "../core/protocol.ts";
import { BaiduApi } from "../baidu/api.ts";
import type { Request, Reply, Wire } from "../baidu/api.ts";
import { md5 } from "../vendor/md5.ts";
const encode = (s: string) => new TextEncoder().encode(s);
const binding = { protocol: 1 as const, workspace: "test-workspace", account: "account", remoteRoot: "/apps/test" };
class Cloud implements Wire {
  files = new Map<string, { bytes: Uint8Array; id: number }>(); dirs = new Set<string>(); pending = new Map<string, Uint8Array>();
  failSlice = false;
  async send(request: Request): Promise<Reply> {
    const url = new URL(request.url), p = url.searchParams;
    const form = typeof request.body === "string" ? new URLSearchParams(request.body) : new URLSearchParams();
    const json = (value: unknown) => ({ status: 200, bytes: encode(JSON.stringify(value)).buffer });
    const method = p.get("method"), path = form.get("path")!;
    if (method === "list") {
      const dir = p.get("dir")!;
      if (!this.dirs.has(dir)) return json({ errno: -9 });
      const entries = [...this.files].filter(([path]) => path.slice(0, path.lastIndexOf("/")) === dir).sort(([a], [b]) => a.localeCompare(b)).map(([path, f]) => ({ path, size: f.bytes.length, fs_id: f.id, isdir: 0 }));
      return json({ errno: 0, list: entries.slice(Number(p.get("start")), Number(p.get("start")) + Number(p.get("limit"))) });
    }
    if (method === "create" && form.get("isdir") === "1") { if (this.dirs.has(path)) return json({ errno: -8 }); this.dirs.add(path); return json({ errno: 0 }); }
    if (method === "precreate") return json({ errno: 0, uploadid: "test-upload", block_list: [0] });
    if (method === "upload") {
      if (this.failSlice) return json({ error_code: 31034 });
      const bytes = Buffer.from(request.body as ArrayBuffer);
      const begin = bytes.indexOf("\r\n\r\n") + 4, end = bytes.lastIndexOf("\r\n--");
      const body = new Uint8Array(bytes.subarray(begin, end)); this.pending.set(p.get("path")!, body);
      return json({ error_code: 0, md5: md5(body) });
    }
    if (method === "create") {
      const bytes = this.pending.get(path)!; this.files.set(path, { bytes, id: this.files.get(path)?.id ?? this.files.size + 1 });
      return json({ errno: 0, path, size: bytes.length });
    }
    if (method === "filemetas") {
      const id = JSON.parse(p.get("fsids")!)[0], entry = [...this.files].find(([, f]) => f.id === id)!;
      return json({ errno: 0, list: [{ path: entry[0], fs_id: id, dlink: `https://d.pcs.baidu.com/download?id=${id}` }] });
    }
    if (url.pathname === "/download") {
      const entry = [...this.files.values()].find(f => f.id === Number(p.get("id")))!;
      return { status: 200, bytes: entry.bytes.slice().buffer };
    }
    throw new Error("Unexpected fake request");
  }
}
test("encrypted transport performs the full XPan precreate/slice/create/readback flow", async () => {
  const wire = new Cloud(), api = new BaiduApi(wire, { token: async () => "fake" }, binding.remoteRoot);
  await api.initializeDirectories(); await api.initializeDirectories();
  const transport = await SecureTransport.open(api, binding, "test-password-12345");
  await transport.initializeEmpty();
  const body = encode("private 中文正文"), id = await hash(body);
  await transport.putObject(id, body); assert.deepEqual(await transport.getObject(id), body);
  const c = await commit({ protocol: 1, binding, file: "file", path: "03_知识库/test.md", device: "a", operation: "one", parents: [], kind: "put", content: id });
  await transport.putCommit(c); assert.deepEqual(await transport.listCommits(), [c]);
  assert.ok([...wire.files.values()].every(f => !new TextDecoder().decode(f.bytes).includes("private")));
  const wrong = await SecureTransport.open(api, binding, "wrong-password-12345");
  const before = wire.files.size;
  await assert.rejects(wrong.putObject(id, body), /密码/); assert.equal(wire.files.size, before);
});
test("PCS JSON errors prevent file creation and initialized binding cannot be replaced", async () => {
  const wire = new Cloud(), api = new BaiduApi(wire, { token: async () => "fake" }, binding.remoteRoot);
  await api.initializeDirectories(); const transport = await SecureTransport.open(api, binding, "test-password-12345");
  await transport.initializeEmpty(); await assert.rejects(transport.initializeEmpty(), /已配对/);
  wire.failSlice = true;
  await assert.rejects(transport.putObject(await hash(encode("x")), encode("x")), /rejected/);
  assert.equal(wire.files.size, 1);
});
test("pairing encrypts credentials and wrong password fails", async () => {
  const value = { access: "private-credential", workspace: "workspace" };
  const code = await exportPair(value, "pair-password-12345");
  assert.ok(!code.includes(value.access)); assert.deepEqual(await importPair(code, "pair-password-12345"), value);
  await assert.rejects(importPair(code, "wrong-password-12345"));
});
test("vendor MD5 matches standard vectors", () => {
  assert.equal(md5(encode("")), "d41d8cd98f00b204e9800998ecf8427e");
  assert.equal(md5(encode("abc")), "900150983cd24fb0d6963f7d28e17f72");
});
test("unknown root files and malformed existing binding are never mistaken for an empty namespace", async () => {
  const wire = new Cloud(), api = new BaiduApi(wire, { token: async () => "fake" }, binding.remoteRoot);
  wire.files.set(binding.remoteRoot + "/unrelated.txt", { bytes: encode("keep"), id: 1 });
  await assert.rejects(api.initializeDirectories(), /其他文件/);
  assert.equal(new TextDecoder().decode(wire.files.get(binding.remoteRoot + "/unrelated.txt")!.bytes), "keep");
  wire.files.delete(binding.remoteRoot + "/unrelated.txt");
  wire.files.set(binding.remoteRoot + "/binding", { bytes: encode("existing"), id: 1 });
  const send = wire.send.bind(wire);
  wire.send = async request => {
    const reply = await send(request), url = new URL(request.url);
    if (url.searchParams.get("method") === "list" && url.searchParams.get("dir") === binding.remoteRoot) {
      const data = JSON.parse(new TextDecoder().decode(reply.bytes));
      for (const entry of data.list) entry.size = 3 * 1048576;
      return { status: 200, bytes: encode(JSON.stringify(data)).buffer };
    }
    return reply;
  };
  const freshApi = new BaiduApi(wire, { token: async () => "fake" }, binding.remoteRoot);
  const transport = await SecureTransport.open(freshApi, binding, "test-password-12345");
  await assert.rejects(transport.initializeEmpty(), /metadata/);
  assert.equal(new TextDecoder().decode(wire.files.get(binding.remoteRoot + "/binding")!.bytes), "existing");
});
