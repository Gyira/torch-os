// 本地界面层回归：静态文件、反向代理、会话 Cookie、缓存、动作令牌与 WebSocket。
// 只使用假后端和临时目录，不启动 Obsidian/Python，也不碰真实个人数据。
import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import Module, { createRequire } from "node:module";

const originalLoad = Module._load;
Module._load = function patchedLoad(request, parent, isMain) {
  if (request === "obsidian") {
    return {
      Plugin: class {}, ItemView: class {}, PluginSettingTab: class {},
      Setting: class {}, Notice: class {}, TFile: class {},
      requestUrl: async () => ({ status: 500 }),
      normalizePath: (value) => value,
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};
const main = createRequire(import.meta.url)("../obsidian-plugin/main.js");
Module._load = originalLoad;
const {
  LocalUiServer, BackendController, UiCache, VaultMigrator,
  classifyBackendFailure, readLogSegment, safeStaticPath, isCacheableApiPath,
} = main._test;

function tempRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "torch-ui-server-"));
}

function makeFrontend(root) {
  fs.mkdirSync(path.join(root, "vendor"), { recursive: true });
  fs.writeFileSync(path.join(root, "index.html"), "<!doctype html><title>Torch</title>", "utf8");
  fs.writeFileSync(path.join(root, "app.js"), "export const ok = 1;\n", "utf8");
  fs.writeFileSync(path.join(root, "vendor", "lunar.js"), "export const lunar = 1;\n", "utf8");
  return root;
}

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.removeListener("error", reject);
      resolve(server.address().port);
    });
  });
}

function close(server) {
  return new Promise((resolve) => {
    if (!server) return resolve();
    try { server.closeAllConnections?.(); } catch { /* ignore */ }
    server.close(() => resolve());
  });
}

function request(port, pathName, { method = "GET", headers = {}, body = null } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: "127.0.0.1", port, path: pathName, method, agent: false, headers,
    }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks),
      }));
    });
    req.setTimeout(5000, () => req.destroy(new Error("test request timeout")));
    req.on("error", reject);
    if (body != null) req.write(body);
    req.end();
  });
}

function makeController(port, phase = "ready") {
  const state = {
    phase, port: phase === "ready" ? port : 0,
    url: phase === "ready" ? `http://127.0.0.1:${port}` : "",
    vaultId: "vault-test", failure: null, startedAt: new Date().toISOString(),
    autoStart: true, ensureCalls: 0,
  };
  return {
    state,
    snapshot() { return { ...state, failure: state.failure && { ...state.failure } }; },
    async ensure() { state.ensureCalls += 1; return this.snapshot(); },
    async restart() { state.phase = "starting"; return this.snapshot(); },
    markRequestFailure() {},
    logPath: "",
  };
}

async function fakeBackend({ onRequest = () => {} } = {}) {
  const seen = [];
  const sockets = new Set();
  const server = http.createServer((req, res) => {
    const record = { method: req.method, url: req.url, headers: req.headers, body: "" };
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      record.body = Buffer.concat(chunks).toString("utf8");
      seen.push(record);
      onRequest(req, res, record);
      if (req.url === "/") {
        res.writeHead(200, {
          "Content-Type": "text/html; charset=utf-8",
          "Set-Cookie": "torch_session_abcdef0123456789=secret; Path=/; HttpOnly",
        });
        res.end("<html><body>backend index</body></html>");
        return;
      }
      if (req.url.startsWith("/api/blob")) {
        res.writeHead(200, {
          "Content-Type": "application/octet-stream",
          "Content-Disposition": 'attachment; filename="data.bin"',
          "Content-Range": "bytes 0-3/4",
        });
        res.end(Buffer.from([1, 2, 3, 4]));
        return;
      }
      res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: true, url: req.url, method: req.method, body: record.body }));
    });
  });
  server.on("connection", (socket) => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
  });
  server.on("upgrade", (req, socket) => {
    socket.write("HTTP/1.1 101 Switching Protocols\r\n"
      + "Upgrade: websocket\r\nConnection: Upgrade\r\n"
      + "Sec-WebSocket-Accept: test-accept\r\n\r\n");
    socket.on("data", (chunk) => socket.write(Buffer.concat([Buffer.from("echo:"), chunk])));
    socket.on("error", () => {});
  });
  const port = await listen(server);
  return {
    server, port, seen,
    close: () => {
      for (const socket of sockets) {
        try { socket.destroy(); } catch { /* ignore */ }
      }
      return close(server);
    },
  };
}

test("静态文件：/assets 映射到 frontend 根目录，并带 Obsidian 可用的 CSP", async (t) => {
  const root = tempRoot();
  const frontendDir = makeFrontend(path.join(root, "frontend"));
  const backend = await fakeBackend();
  const controller = makeController(backend.port);
  const server = new LocalUiServer({ frontendDir, vaultId: "vault-test", controller });
  await server.listen({ preferredPort: 0 });
  t.after(async () => {
    await server.close();
    await backend.close();
    fs.rmSync(root, { recursive: true, force: true });
  });

  const index = await request(server.port, "/");
  assert.equal(index.status, 200);
  assert.match(String(index.headers["content-type"]), /text\/html/);
  assert.match(String(index.headers["content-security-policy"]), /frame-ancestors.*app:/);
  assert.match(index.body.toString("utf8"), /Torch/);

  const app = await request(server.port, "/assets/app.js");
  assert.equal(app.status, 200);
  assert.match(String(app.headers["content-type"]), /text\/javascript/);
  assert.match(app.body.toString("utf8"), /export const ok/);

  const nested = await request(server.port, "/assets/vendor/lunar.js");
  assert.equal(nested.status, 200);
  assert.match(nested.body.toString("utf8"), /lunar/);

  const missing = await request(server.port, "/assets/missing.js");
  assert.equal(missing.status, 404);

  assert.equal(safeStaticPath(frontendDir, "/assets/../index.html"), null);
  assert.equal(safeStaticPath(frontendDir, "/assets/%2e%2e/index.html"), null);
  assert.equal(safeStaticPath(frontendDir, "/api/data"), null);
});

test("代理：改写 Host/Origin、携带后端 Cookie、保留下载头、不向页面暴露 Set-Cookie", async (t) => {
  const root = tempRoot();
  const frontendDir = makeFrontend(path.join(root, "frontend"));
  const backend = await fakeBackend();
  const controller = makeController(backend.port);
  const server = new LocalUiServer({ frontendDir, vaultId: "vault-test", controller });
  await server.listen({ preferredPort: 0 });
  t.after(async () => {
    await server.close();
    await backend.close();
    fs.rmSync(root, { recursive: true, force: true });
  });

  const get = await request(server.port, "/api/health");
  assert.equal(get.status, 200);
  assert.equal(JSON.parse(get.body.toString("utf8")).ok, true);
  assert.equal(get.headers["set-cookie"], undefined);
  const first = backend.seen.find((row) => row.url === "/");
  assert.ok(first, "应先 GET 后端 / 取得会话 Cookie");
  assert.equal(first.headers.host, `127.0.0.1:${backend.port}`);

  const health = backend.seen.filter((row) => row.url === "/api/health").pop();
  assert.equal(health.headers.host, `127.0.0.1:${backend.port}`);
  assert.match(String(health.headers.cookie), /^torch_session_abcdef0123456789=secret$/);

  const post = await request(server.port, "/api/echo", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Origin: `http://127.0.0.1:${server.port}`,
    },
    body: JSON.stringify({ hello: "world" }),
  });
  assert.equal(post.status, 200);
  const echo = backend.seen.filter((row) => row.url === "/api/echo").pop();
  assert.equal(echo.headers.origin, `http://127.0.0.1:${backend.port}`);
  assert.match(echo.headers.cookie, /torch_session_abcdef0123456789=secret/);
  assert.equal(JSON.parse(echo.body).hello, "world");

  const blob = await request(server.port, "/api/blob");
  assert.equal(blob.status, 200);
  assert.equal(blob.headers["content-disposition"], 'attachment; filename="data.bin"');
  assert.equal(blob.headers["content-range"], "bytes 0-3/4");
  assert.deepEqual([...blob.body], [1, 2, 3, 4]);
});

test("服务未就绪：立即返回 503 + X-Torch-Service，而不是等待", async (t) => {
  const root = tempRoot();
  const frontendDir = makeFrontend(path.join(root, "frontend"));
  const backend = await fakeBackend();
  const controller = makeController(backend.port, "starting");
  const server = new LocalUiServer({ frontendDir, vaultId: "vault-test", controller });
  await server.listen({ preferredPort: 0 });
  t.after(async () => {
    await server.close();
    await backend.close();
    fs.rmSync(root, { recursive: true, force: true });
  });

  const started = Date.now();
  const response = await request(server.port, "/api/time/tasks");
  assert.ok(Date.now() - started < 1000, "未就绪时不应阻塞");
  assert.equal(response.status, 503);
  assert.equal(response.headers["x-torch-service"], "starting");
  const payload = JSON.parse(response.body.toString("utf8"));
  assert.equal(payload.detail.code, "service_starting");

  controller.state.phase = "failed";
  controller.state.failure = { code: "numbered_vault", message: "旧编号目录", hint: "先整理", canMigrate: true };
  const failed = await request(server.port, "/api/time/tasks");
  assert.equal(failed.status, 503);
  assert.equal(failed.headers["x-torch-service"], "failed");
  assert.equal(JSON.parse(failed.body.toString("utf8")).detail.code, "numbered_vault");
});

test("只读缓存：服务未就绪时回放上次 JSON，并标注 stale", async (t) => {
  const root = tempRoot();
  const frontendDir = makeFrontend(path.join(root, "frontend"));
  const cacheDir = path.join(root, "cache");
  const backend = await fakeBackend();
  const controller = makeController(backend.port);
  const cache = new UiCache({ dir: cacheDir });
  await cache.init();
  const server = new LocalUiServer({ frontendDir, vaultId: "vault-test", controller, cache });
  await server.listen({ preferredPort: 0 });
  t.after(async () => {
    await server.close();
    await backend.close();
    fs.rmSync(root, { recursive: true, force: true });
  });

  const url = "/api/workbench/today?day=2099-01-01";
  const first = await request(server.port, url);
  assert.equal(first.status, 200);
  await new Promise((resolve) => setTimeout(resolve, 120));

  controller.state.phase = "starting";
  controller.state.port = 0;
  controller.state.url = "";
  const stale = await request(server.port, url);
  assert.equal(stale.status, 200);
  assert.equal(stale.headers["x-torch-cache"], "stale");
  assert.ok(stale.headers["x-torch-cache-at"]);
  assert.equal(JSON.parse(stale.body.toString("utf8")).ok, true);

  const personal = await request(server.port, "/api/personal-data");
  assert.equal(personal.status, 503, "个人设置不应从缓存回放");
  assert.equal(isCacheableApiPath("/api/personal-data"), false);
  assert.equal(isCacheableApiPath("/api/workbench/today?day=1"), true);
});

test("动作令牌、Host 与跨来源写操作校验", async (t) => {
  const root = tempRoot();
  const frontendDir = makeFrontend(path.join(root, "frontend"));
  const backend = await fakeBackend();
  const controller = makeController(backend.port, "failed");
  const server = new LocalUiServer({ frontendDir, vaultId: "vault-test", controller });
  await server.listen({ preferredPort: 0 });
  t.after(async () => {
    await server.close();
    await backend.close();
    fs.rmSync(root, { recursive: true, force: true });
  });

  const service = await request(server.port, "/__torch/service");
  const payload = JSON.parse(service.body.toString("utf8"));
  assert.equal(payload.mode, "plugin");
  assert.ok(payload.actionToken);

  const denied = await request(server.port, "/__torch/start", {
    method: "POST",
    headers: { Origin: `http://127.0.0.1:${server.port}` },
  });
  assert.equal(denied.status, 403);

  const started = await request(server.port, "/__torch/start", {
    method: "POST",
    headers: {
      Origin: `http://127.0.0.1:${server.port}`,
      "X-Torch-UI-Token": payload.actionToken,
    },
  });
  assert.equal(started.status, 202);
  assert.ok(controller.state.ensureCalls > 0);

  const badHost = await request(server.port, "/", {
    headers: { Host: "evil.example" },
  });
  assert.equal(badHost.status, 403);

  const crossOrigin = await request(server.port, "/api/echo", {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: "https://evil.example" },
    body: "{}",
  });
  assert.equal(crossOrigin.status, 403);

  const crossSiteGet = await request(server.port, "/api/health", {
    headers: { "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors" },
  });
  assert.equal(crossSiteGet.status, 403, "跨站 GET 不能借用代理里的后端会话");
  controller.state.phase = "ready";
  controller.state.port = backend.port;
  controller.state.url = `http://127.0.0.1:${backend.port}`;
  const obsidianLike = await request(server.port, "/api/health", {
    headers: { "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "same-origin" },
  });
  assert.equal(obsidianLike.status, 200, "Obsidian iframe 的 cross-site + same-origin 应放行");
  const imageLike = await request(server.port, "/api/health", {
    headers: { "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors" },
  });
  assert.equal(imageLike.status, 200, "图片/媒体子资源（no-cors）不应被误伤");
});

test("WebSocket 升级经 /harness 原样转发", async (t) => {
  const root = tempRoot();
  const frontendDir = makeFrontend(path.join(root, "frontend"));
  const backend = await fakeBackend();
  const controller = makeController(backend.port);
  const server = new LocalUiServer({ frontendDir, vaultId: "vault-test", controller });
  await server.listen({ preferredPort: 0 });
  t.after(async () => {
    await server.close();
    await backend.close();
    fs.rmSync(root, { recursive: true, force: true });
  });

  await new Promise((resolve, reject) => {
    const req = http.request({
      host: "127.0.0.1",
      port: server.port,
      path: "/harness/ws",
      agent: false,
      headers: {
        Connection: "Upgrade",
        Upgrade: "websocket",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version": "13",
        Origin: `http://127.0.0.1:${server.port}`,
      },
    });
    const timer = setTimeout(() => reject(new Error("upgrade timeout")), 3000);
    req.on("upgrade", (response, socket) => {
      try {
        assert.equal(response.statusCode, 101);
        socket.once("data", (chunk) => {
          clearTimeout(timer);
          assert.match(chunk.toString("utf8"), /^echo:/);
          socket.destroy();
          resolve();
        });
        socket.write("hello");
      } catch (error) {
        clearTimeout(timer);
        reject(error);
      }
    });
    req.on("response", (response) => {
      clearTimeout(timer);
      reject(new Error(`expected 101, got ${response.statusCode}`));
    });
    req.on("error", (error) => { clearTimeout(timer); reject(error); });
    req.end();
  });
});

test("BackendController：子进程秒退时按日志快速失败，不等 20 秒", async () => {
  const root = tempRoot();
  const vault = path.join(root, "vault");
  const repo = path.join(root, "repo");
  fs.mkdirSync(vault, { recursive: true });
  fs.mkdirSync(repo, { recursive: true });
  const settings = { autoStart: true, baseUrl: "http://127.0.0.1:8765", instancePort: 0, uiPort: 0 };
  const child = new EventEmitter();
  child.pid = 4242;
  child.unref = () => {};
  const controller = new BackendController({
    getBinding: () => ({ id: "vault-test", path: vault, name: "vault" }),
    getSettings: () => settings,
    resolveToolchain: () => ({ repoRoot: repo, pythonPath: path.join(repo, "python.exe") }),
    saveDeviceSettings: () => {},
    probe: async () => null,
    portFree: async () => true,
    spawnProcess: () => {
      setTimeout(() => {
        child.emit("spawn");
        setTimeout(() => child.emit("exit", 1, null), 10);
      }, 0);
      return child;
    },
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, Math.min(ms, 10))),
    readLog: () => "Traceback...\nValueError: 此库仍使用带编号的业务目录。",
    onState: () => {},
  });
  const started = Date.now();
  const state = await controller.ensure({ manual: true });
  assert.equal(state.phase, "failed");
  assert.equal(state.failure.code, "numbered_vault");
  assert.equal(state.failure.canMigrate, true);
  assert.ok(Date.now() - started < 1500, "应在 1.5 秒内失败，而不是等 20 秒超时");
  controller.dispose();
  fs.rmSync(root, { recursive: true, force: true });
});

test("BackendController：复用已有实例并记住端口", async () => {
  const root = tempRoot();
  const vault = path.join(root, "vault");
  fs.mkdirSync(vault, { recursive: true });
  const settings = { autoStart: true, baseUrl: "http://127.0.0.1:8765", instancePort: 0, uiPort: 0 };
  let saved = 0;
  const controller = new BackendController({
    getBinding: () => ({ id: "vault-test", path: vault, name: "vault" }),
    getSettings: () => settings,
    resolveToolchain: () => ({ repoRoot: path.join(root, "repo"), pythonPath: path.join(root, "repo", "python.exe") }),
    saveDeviceSettings: () => { saved += 1; },
    probe: async (port) => (port === 9171 ? { status: "ok", service: "torch-os", vault_id: "vault-test" } : null),
    portFree: async () => true,
    spawnProcess: () => { throw new Error("不应启动新进程"); },
    sleep: async () => {},
    readLog: () => "",
    onState: () => {},
  });
  const state = await controller.ensure({ manual: true });
  assert.equal(state.phase, "ready");
  assert.equal(state.port, 9171);
  assert.equal(settings.instancePort, 9171);
  assert.ok(saved > 0);
  controller.dispose();
  fs.rmSync(root, { recursive: true, force: true });
});

test("VaultMigrator：预览解析、数量校验与后台 apply 状态", async () => {
  const root = tempRoot();
  const manifestPath = path.join(root, "manifest.json");
  fs.writeFileSync(manifestPath, JSON.stringify({
    status: "done", backup: ".torch-os/backups/directory-names/test",
  }), "utf8");
  const migrator = new VaultMigrator({
    pythonPath: path.join(root, "python.exe"),
    repoRoot: root,
    vaultPath: path.join(root, "vault"),
    databasePath: path.join(root, "vault", ".torch-os", "torch_os.sqlite3"),
    spawnProcess: (_python, args) => {
      const child = new EventEmitter();
      child.stdout = new PassThrough();
      child.stderr = new PassThrough();
      child.kill = () => {};
      setTimeout(() => {
        if (args.includes("--apply")) child.stdout.write(`${manifestPath}\n`);
        else child.stdout.write(`${JSON.stringify({ directories: 2, files: 3, moves: {} })}\n`);
        setTimeout(() => child.emit("close", 0), 10);
      }, 0);
      return child;
    },
  });
  const preview = await migrator.preview();
  assert.deepEqual({ directories: preview.directories, files: preview.files }, { directories: 2, files: 3 });
  assert.throws(() => migrator.apply({ confirm: true, directories: 9, files: 3 }), /不一致/);
  const job = migrator.apply({ confirm: true, directories: 2, files: 3 });
  assert.equal(job.state, "running");
  await new Promise((resolve) => setTimeout(resolve, 80));
  const finished = migrator.status();
  assert.equal(finished.state, "done");
  assert.equal(finished.result.manifest, manifestPath);
  assert.match(finished.result.backup, /directory-names/);
  fs.rmSync(root, { recursive: true, force: true });
});

test("插件 ensureUiServer：不需要 Python 就能启动界面并持久化 uiPort", async (t) => {
  const root = tempRoot();
  const oldLocal = process.env.LOCALAPPDATA;
  const oldXdg = process.env.XDG_CONFIG_HOME;
  process.env.LOCALAPPDATA = path.join(root, "local-app-data");
  process.env.XDG_CONFIG_HOME = path.join(root, "xdg");
  const repo = path.join(root, "torch-os");
  const vault = path.join(root, "vault");
  fs.mkdirSync(path.join(repo, "backend", "app"), { recursive: true });
  fs.writeFileSync(path.join(repo, "backend", "app", "run.py"), "# fake\n", "utf8");
  const python = process.platform === "win32"
    ? path.join(repo, ".venv", "Scripts", "python.exe")
    : path.join(repo, ".venv", "bin", "python");
  fs.mkdirSync(path.dirname(python), { recursive: true });
  fs.writeFileSync(python, "", "utf8");
  makeFrontend(path.join(repo, "frontend"));
  fs.mkdirSync(vault, { recursive: true });
  try {
    const plugin = Object.create(main.prototype);
    plugin.app = {
      vault: {
        getName: () => "vault",
        adapter: { getBasePath: () => vault },
      },
    };
    plugin.settings = {
      baseUrl: "http://127.0.0.1:8765", autoStart: true, repoRoot: repo,
      pythonPath: "", instancePort: 0, uiPort: 0, storageConfigPath: "",
    };
    plugin.deviceKey = "vault-test";
    plugin.uiServer = null;
    plugin.uiUrl = "";
    plugin.uiPromise = null;
    plugin.saveDeviceSettings = () => {};
    plugin.controller = {
      snapshot: () => ({ phase: "idle", autoStart: true, failure: null, port: 0 }),
      ensure: async () => ({}),
      restart: async () => ({}),
      markRequestFailure() {},
      logPath: "",
    };
    const server = await plugin.ensureUiServer();
    assert.ok(server.port > 0);
    assert.match(plugin.uiUrl, /^http:\/\/127\.0\.0\.1:\d+$/);
    assert.equal(plugin.settings.uiPort, server.port);
    const index = await request(server.port, "/");
    assert.equal(index.status, 200);
    assert.match(index.body.toString("utf8"), /Torch/);
    await server.close();
  } finally {
    if (oldLocal === undefined) delete process.env.LOCALAPPDATA;
    else process.env.LOCALAPPDATA = oldLocal;
    if (oldXdg === undefined) delete process.env.XDG_CONFIG_HOME;
    else process.env.XDG_CONFIG_HOME = oldXdg;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("UiCache：超过条目上限时只淘汰旧条目，不整库清空", async () => {
  const root = tempRoot();
  let clock = Date.parse("2026-11-22T00:00:00Z");
  const cache = new UiCache({
    dir: path.join(root, "cache"), now: () => clock, maxEntries: 2,
    totalLimit: 1024 * 1024,
  });
  await cache.init();
  const urls = ["/api/time/tasks", "/api/goals", "/api/config/status"];
  for (const url of urls) {
    clock += 61 * 1000;
    await cache.set(url, Buffer.from(JSON.stringify({ ok: true })), "application/json");
    await new Promise((resolve) => setTimeout(resolve, 8));
    await cache.prune();
  }
  const stats = await cache.stats();
  assert.ok(stats.entries >= 1 && stats.entries <= 2, `应保留 1–2 条，实际 ${stats.entries}`);
  assert.ok(await cache.get(urls[2]), "最新一条应保留");
  fs.rmSync(root, { recursive: true, force: true });
});

test("失败分类与日志偏移", () => {
  const numbered = classifyBackendFailure(
    "Traceback...\nValueError: 此库仍使用带编号的业务目录。请先停止后端，运行 scripts/unnumber_vault.py",
    { exitCode: 1 },
  );
  assert.equal(numbered.code, "numbered_vault");
  assert.equal(numbered.canMigrate, true);

  assert.equal(classifyBackendFailure("ModuleNotFoundError: No module named 'uvicorn'").code, "module_missing");
  assert.equal(classifyBackendFailure("ERROR: [WinError 10048] address already in use").code, "port_conflict");
  assert.equal(classifyBackendFailure("", { spawnError: Object.assign(new Error("missing"), { code: "ENOENT" }) }).code, "python_missing");

  const root = tempRoot();
  const logPath = path.join(root, "backend.log");
  fs.writeFileSync(logPath, "old error: numbered_vault\n", "utf8");
  const offset = fs.statSync(logPath).size;
  fs.appendFileSync(logPath, "new error: ModuleNotFoundError\n", "utf8");
  const segment = readLogSegment(logPath, offset, 4096);
  assert.ok(segment.includes("new error"));
  assert.ok(!segment.includes("old error"));
  fs.rmSync(root, { recursive: true, force: true });
});
