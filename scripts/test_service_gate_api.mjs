// 插件模式下 api() 的服务门控：503 启动中等待并重试一次、失败不重试、可关闭等待。
// 独立进程运行，因为 service-gate 在模块加载时读取 host=obsidian。
import test from "node:test";
import assert from "node:assert/strict";

globalThis.window = globalThis;
globalThis.location = { search: "?host=obsidian" };
globalThis.document = {
  getElementById: () => null,
  querySelector: () => ({ addEventListener() {} }),
  createElement: () => ({ style: {}, classList: { toggle() {}, add() {}, remove() {} }, setAttribute() {} }),
  dispatchEvent: () => true,
  addEventListener() {},
};

const { api } = await import("../frontend/core.js");
const originalFetch = globalThis.fetch;
test.afterEach(() => { globalThis.fetch = originalFetch; });

function serviceResponse(phase, failure = null) {
  return Response.json({
    mode: "plugin",
    actionToken: "test-token",
    autoStart: true,
    ui: { port: 9400, origin: "http://127.0.0.1:9400" },
    capabilities: {},
    service: { phase, failure, startedAt: new Date().toISOString(), autoStart: true },
  });
}

test("503 starting：等待 /__torch/service 就绪后只重试一次", async () => {
  const calls = [];
  let apiAttempts = 0;
  globalThis.fetch = async (url, options = {}) => {
    calls.push(String(url));
    if (String(url) === "/__torch/service") return serviceResponse("ready");
    apiAttempts += 1;
    if (apiAttempts === 1) {
      return new Response(JSON.stringify({
        detail: { code: "service_starting", message: "本地服务启动中…" },
      }), {
        status: 503,
        headers: { "Content-Type": "application/json", "X-Torch-Service": "starting" },
      });
    }
    return Response.json({ ok: true, n: 2 });
  };
  const result = await api("/api/workbench/today?day=2099-01-01");
  assert.deepEqual(result, { ok: true, n: 2 });
  assert.equal(apiAttempts, 2);
  assert.ok(calls.includes("/__torch/service"));
});

test("503 failed：立即失败，不等待也不重试", async () => {
  let apiAttempts = 0;
  let servicePolls = 0;
  globalThis.fetch = async (url) => {
    if (String(url) === "/__torch/service") {
      servicePolls += 1;
      return serviceResponse("failed", {
        code: "numbered_vault", message: "旧编号目录", hint: "先整理", canRetry: true,
      });
    }
    apiAttempts += 1;
    return new Response(JSON.stringify({
      detail: { code: "numbered_vault", message: "旧编号目录", hint: "先整理" },
    }), {
      status: 503,
      headers: { "Content-Type": "application/json", "X-Torch-Service": "failed" },
    });
  };
  await assert.rejects(api("/api/time/tasks"), (error) => (
    error.status === 503 && /旧编号目录/.test(error.message)
  ));
  assert.equal(apiAttempts, 1);
  assert.equal(servicePolls, 0);
});

test("waitForService:false：503 时立即返回错误，用于首屏个人设置", async () => {
  let apiAttempts = 0;
  globalThis.fetch = async (url) => {
    if (String(url) === "/__torch/service") return serviceResponse("starting");
    apiAttempts += 1;
    return new Response(JSON.stringify({
      detail: { code: "service_starting", message: "本地服务启动中…" },
    }), {
      status: 503,
      headers: { "Content-Type": "application/json", "X-Torch-Service": "starting" },
    });
  };
  await assert.rejects(api("/api/personal-data", { waitForService: false }), /启动中/);
  assert.equal(apiAttempts, 1);
});

test("stale 缓存响应照常返回，不当作错误", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ cached: true }), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "X-Torch-Cache": "stale",
      "X-Torch-Cache-At": new Date().toISOString(),
    },
  });
  assert.deepEqual(await api("/api/workbench/today?day=2099-01-01"), { cached: true });
});
