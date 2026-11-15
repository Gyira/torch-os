import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

globalThis.window = globalThis;
globalThis.document = { querySelector: () => ({ addEventListener() {} }), addEventListener() {} };
const { api } = await import("../frontend/core.js");
const originalFetch = globalThis.fetch;
test.afterEach(() => { globalThis.fetch = originalFetch; });

test("保留 Headers 对象，GET 不附加 JSON Content-Type", async () => {
  globalThis.fetch = async (_, options) => {
    assert.equal(options.headers.get("X-Test"), "yes");
    assert.equal(options.headers.has("Content-Type"), false);
    return Response.json({ ok: true });
  };
  assert.deepEqual(await api("/test", { headers: new Headers({ "X-Test": "yes" }) }), { ok: true });
});

test("JSON 写入和 FormData 分别使用正确的内容类型", async () => {
  let seen;
  globalThis.fetch = async (_, options) => { seen = options.headers; return new Response(null, { status: 204 }); };
  assert.deepEqual(await api("/test", { method: "POST", body: "{}" }), {});
  assert.equal(seen.get("Content-Type"), "application/json");
  await api("/test", { method: "POST", body: new FormData() });
  assert.equal(seen.has("Content-Type"), false);
});

test("成功状态但损坏的 JSON 不会被当作保存成功", async () => {
  globalThis.fetch = async () => new Response("<html>broken</html>");
  await assert.rejects(api("/test"), /数据不完整/);
});

test("401 保留状态码，非 JSON 错误页不直接显示 HTML", async () => {
  globalThis.fetch = async () => new Response("<html>private</html>", { status: 401 });
  await assert.rejects(api("/test"), error => error.status === 401 && /会话已失效/.test(error.message));
  globalThis.fetch = async () => new Response("<html>private</html>", { status: 502 });
  await assert.rejects(api("/test"), error => error.status === 502 && !error.message.includes("html"));
});

function abortableFetch(_, { signal }) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) reject(signal.reason);
    else signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  });
}

test("调用方主动取消保留 AbortError，已经取消的信号也有效", async () => {
  globalThis.fetch = abortableFetch;
  const controller = new AbortController();
  const request = api("/test", { signal: controller.signal });
  controller.abort("新搜索取代旧搜索");
  await assert.rejects(request, { name: "AbortError" });
  await assert.rejects(api("/test", { signal: controller.signal }), { name: "AbortError" });
});

test("超时覆盖响应体读取，不会误报成功", async () => {
  globalThis.fetch = async (_, { signal }) => ({ ok: true, status: 200,
    json: () => abortableFetch(null, { signal }),
  });
  await assert.rejects(api("/test", { timeoutMs: 5 }), /请求超时/);
});

const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const source = await readFile(new URL("../frontend/time-module.js", import.meta.url), "utf8");
const refreshCode = source.slice(source.indexOf("let taskLoadVersion ="), source.indexOf('let warningSignature =')).replace("export ", "");
function taskHarness() {
  const requests = [], messages = [];
  const env = vm.createContext({
    api: path => { if (path === "/api/goals") return Promise.resolve([]); const request = deferred(); requests.push(request); return request.promise; },
    board: { revision: "initial", tasks: [] }, goalTitles: new Map(), goalSignature: "", activeKey: "", picked: new Set(),
    $: () => ({ hidden: true }), setStatus: (_, message) => messages.push(message), toast: message => messages.push(message),
    paintVaultNote() {}, paintSidebar() {}, paintBoard() {}, paintBulkBar() {},
  });
  vm.runInContext(refreshCode, env);
  return { env, requests, messages };
}

test("晚到的旧任务响应不能覆盖较新任务和 revision", async () => {
  const { env, requests } = taskHarness();
  const old = env.loadTimeTasks(), latest = env.loadTimeTasks();
  requests[1].resolve({ revision: "latest", tasks: [] }); await latest;
  requests[0].resolve({ revision: "old", tasks: [] }); await old;
  assert.equal(env.board.revision, "latest");
});

test("旧请求失败不打断新画面，当前刷新失败保留已有任务", async () => {
  const { env, requests, messages } = taskHarness();
  const old = env.loadTimeTasks(), latest = env.loadTimeTasks();
  requests[1].resolve({ revision: "latest", tasks: [] }); await latest;
  requests[0].reject(new Error("旧请求失败")); await old;
  assert.equal(messages.some(x => x.includes("旧请求失败")), false);
  const failed = env.loadTimeTasks(); requests[2].reject(new Error("网络中断")); await failed;
  assert.equal(env.board.revision, "latest");
  assert.equal(messages.some(x => x.includes("网络中断")), true);
});
