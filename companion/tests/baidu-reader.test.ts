import { test } from "node:test";
import assert from "node:assert/strict";
import { BaiduReader } from "../baidu/reader.ts";
import { FetchJsonHttp } from "../baidu/http.ts";

const root = "/apps/torch-test";
const entry = (name: string) => ({ path: `${root}/commits/${name}.json`, fs_id: name, isdir: 0, size: 100 });
test("token expiry rebuilds request with refreshed token", async () => {
  const calls: string[] = [];
  const reader = new BaiduReader({ get: async url => {
    calls.push(new URL(url).searchParams.get("access_token")!);
    return { status: 200, data: calls.length === 1 ? { errno: -6 } : { errno: 0, list: [] } };
  } }, { token: async force => force ? "new" : "old" }, root);
  assert.deepEqual(await reader.listDirectory("commits"), []);
  assert.deepEqual(calls, ["old", "new"]);
});
test("missing directory and malformed responses never become an empty successful list", async () => {
  for (const data of [{ errno: -9 }, { errno: 31066 }, { errno: 20020 }, {}, { errno: 0 }, { errno: 0, list: "not a list" }]) {
    const reader = new BaiduReader({ get: async () => ({ status: 200, data }) }, { token: async () => "test" }, root);
    await assert.rejects(reader.listDirectory("commits"));
  }
});
test("complete pagination and duplicate-page detection", async () => {
  let page = 0;
  const reader = new BaiduReader({ get: async () => ({ status: 200, data: { errno: 0, list: page++ === 0 ? [entry("a"), entry("b")] : [] } }) }, { token: async () => "test" }, root, 2);
  assert.equal((await reader.listDirectory("commits")).length, 2);
  const repeated = new BaiduReader({ get: async () => ({ status: 200, data: { errno: 0, list: [entry("a")], has_more: 1 } }) }, { token: async () => "test" }, root, 1);
  await assert.rejects(repeated.listDirectory("commits"), /repeated/);
});
test("transport exceptions are redacted", async () => {
  const reader = new BaiduReader({ get: async () => { throw new Error("sensitive-token"); } }, { token: async () => "sensitive-token" }, root);
  await assert.rejects(reader.listDirectory("commits"), error => !String(error).includes("sensitive-token"));
});
test("response cannot point outside the configured namespace", async () => {
  const reader = new BaiduReader({ get: async () => ({ status: 200, data: { errno: 0, list: [{ ...entry("a"), path: "/apps/other/a.json" }] } }) }, { token: async () => "test" }, root);
  await assert.rejects(reader.listDirectory("commits"), /Unexpected entry/);
});

test("fetch adapter is bounded, refuses other targets, and disables redirects", async () => {
  const url = "https://pan.baidu.com/rest/2.0/xpan/file?method=list";
  const adapter = new FetchJsonHttp(async (_url, init) => {
    assert.equal(init?.redirect, "error");
    assert.equal(init?.credentials, "omit");
    return new Response('{"errno":0,"list":[]}');
  });
  assert.equal((await adapter.get(url)).status, 200);
  await assert.rejects(adapter.get("https://example.com/"), /not allowed/);
  const big = new FetchJsonHttp(async () => new Response("too large"), 3);
  await assert.rejects(big.get(url), /response failed/);
});

test("authorization-provider exceptions are redacted", async () => {
  const reader = new BaiduReader({ get: async () => { throw new Error("must not call"); } },
    { token: async () => { throw new Error("secret-refresh-token"); } }, root);
  await assert.rejects(reader.listDirectory("commits"), error => !String(error).includes("secret-refresh-token"));
});
