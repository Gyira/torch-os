import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = path.join(frontend, "theme-bridge.js");

async function loadModule() {
  const module = new vm.SourceTextModule(await readFile(SOURCE, "utf8"), {
    context: vm.createContext({}),
    identifier: SOURCE,
  });
  await module.link(() => { throw new Error("no imports expected"); });
  await module.evaluate();
  return module.namespace;
}

const FULL_TOKENS = {
  "--dsw-alias-bg-base": "#151517",
  "--dsw-alias-bg-layer-1": "#232324",
  "--dsw-alias-label-primary": "#f9fafb",
  "--dsw-specific-sidebar-fill": "#1b1b1c",
  "--unknown-token": "#ff0000",
};

function fakeWindow({ embedded = false, dark = false } = {}) {
  const host = { posted: [], postMessage(data, origin) { this.posted.push({ data, origin }); } };
  const listeners = {};
  const win = {
    parent: embedded ? host : null,
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    removeEventListener(type, fn) { listeners[type] = (listeners[type] || []).filter((item) => item !== fn); },
    matchMedia: () => ({ matches: dark, addEventListener() {} }),
  };
  if (!embedded) win.parent = win;
  const props = new Map();
  const element = {
    dataset: {},
    style: {
      setProperty: (name, value) => props.set(name, value),
      getPropertyValue: (name) => props.get(name) ?? "",
    },
  };
  return {
    win, host, element, props,
    doc: { documentElement: element },
    fire(data, { origin = "http://127.0.0.1:3080", source = win.parent } = {}) {
      for (const fn of listeners.message || []) fn({ data, origin, source });
    },
  };
}

test("isLoopbackOrigin accepts only loopback hosts", async () => {
  const { isLoopbackOrigin } = await loadModule();
  for (const good of ["http://127.0.0.1:3080", "http://localhost:3080", "https://localhost", "http://[::1]:8765"]) {
    assert.equal(isLoopbackOrigin(good), true, good);
  }
  for (const bad of ["", "null", "http://127.0.0.1.evil.com", "http://example.com", "http://192.168.1.9:3080", "file://", null, undefined]) {
    assert.equal(isLoopbackOrigin(bad), false, String(bad));
  }
});

test("readTheme keeps only known dsh tokens and defaults the scheme to light", async () => {
  const { readTheme, THEME_MESSAGE } = await loadModule();
  const theme = readTheme({ type: THEME_MESSAGE, scheme: "dark", tokens: FULL_TOKENS });
  assert.equal(theme.scheme, "dark");
  assert.deepEqual(Object.keys(theme.tokens).sort(), [
    "--dsw-alias-bg-base", "--dsw-alias-bg-layer-1", "--dsw-alias-label-primary", "--dsw-specific-sidebar-fill",
  ]);
  assert.equal(readTheme({ type: THEME_MESSAGE, tokens: FULL_TOKENS }).scheme, "light");
  assert.equal(readTheme({ type: THEME_MESSAGE, scheme: "chartreuse", tokens: {} }).scheme, "light");
  for (const bad of [null, "torch:theme", {}, { type: "other" }, { type: THEME_MESSAGE, tokens: null }]) {
    const result = readTheme(bad);
    if (bad && bad.type === THEME_MESSAGE) assert.equal(Object.keys(result.tokens).length, 0);
    else assert.equal(result, null);
  }
});

test("readTheme drops empty, oversized and non-string token values", async () => {
  const { readTheme, THEME_MESSAGE } = await loadModule();
  const theme = readTheme({
    type: THEME_MESSAGE,
    tokens: {
      "--dsw-alias-bg-base": "   ",
      "--dsw-alias-bg-layer-1": "x".repeat(400),
      "--dsw-alias-label-primary": 42,
      "--dsw-alias-bg-overlay": " #e9ecf2 ",
    },
  });
  assert.deepEqual({ ...theme.tokens }, { "--dsw-alias-bg-overlay": "#e9ecf2" });
});

test("applyTheme marks the document as host-driven only with the required tokens", async () => {
  const { applyTheme, THEME_MESSAGE, readTheme } = await loadModule();
  const { element, props } = fakeWindow();
  const partial = readTheme({ type: THEME_MESSAGE, scheme: "dark", tokens: { "--dsw-alias-bg-base": "#151517" } });
  assert.equal(applyTheme(element, partial), false);
  assert.equal(element.dataset.torchTheme, "dark");
  assert.equal(element.dataset.torchHost, undefined);

  const full = readTheme({ type: THEME_MESSAGE, scheme: "dark", tokens: FULL_TOKENS });
  assert.equal(applyTheme(element, full), true);
  assert.equal(element.dataset.torchHost, "dsh");
  assert.equal(element.dataset.torchTheme, "dark");
  assert.equal(props.get("--dsw-alias-bg-layer-1"), "#232324");
  assert.equal(props.has("--unknown-token"), false);
  assert.equal(applyTheme(null, full), false);
});

test("initThemeBridge applies a host message from the parent on a loopback origin", async () => {
  const { initThemeBridge, THEME_MESSAGE, THEME_REQUEST } = await loadModule();
  const dom = fakeWindow({ embedded: true });
  initThemeBridge(dom.win, dom.doc);
  assert.equal(dom.host.posted.length, 1, "asks the host once on boot");
  assert.equal(dom.host.posted[0].data.type, THEME_REQUEST);
  assert.equal(dom.host.posted[0].origin, "*");

  dom.fire({ type: THEME_MESSAGE, scheme: "dark", tokens: FULL_TOKENS });
  assert.equal(dom.element.dataset.torchHost, "dsh");
  assert.equal(dom.element.dataset.torchTheme, "dark");
});

test("initThemeBridge ignores other sources, other origins and foreign messages", async () => {
  const { initThemeBridge, THEME_MESSAGE } = await loadModule();
  const dom = fakeWindow({ embedded: true });
  initThemeBridge(dom.win, dom.doc);
  const payload = { type: THEME_MESSAGE, scheme: "dark", tokens: FULL_TOKENS };
  dom.fire(payload, { source: {} });
  dom.fire(payload, { origin: "http://example.com" });
  dom.fire({ type: "torch:navigate", view: "today" });
  assert.equal(dom.element.dataset.torchHost, undefined);
  assert.equal(dom.element.dataset.torchTheme, undefined);
});

test("the local Obsidian app may drive the theme too (app://obsidian.md)", async () => {
  const { initThemeBridge, THEME_MESSAGE } = await loadModule();
  const dom = fakeWindow({ embedded: true });
  initThemeBridge(dom.win, dom.doc);
  dom.fire({ type: THEME_MESSAGE, scheme: "dark", tokens: FULL_TOKENS }, { origin: "app://obsidian.md" });
  assert.equal(dom.element.dataset.torchHost, "dsh");
  assert.equal(dom.element.dataset.torchTheme, "dark");
  assert.equal(dom.props.get("--dsw-alias-bg-base"), "#151517");
});

test("a standalone page follows the system scheme but never claims a host", async () => {
  const { initThemeBridge } = await loadModule();
  const dark = fakeWindow({ dark: true });
  initThemeBridge(dark.win, dark.doc);
  assert.equal(dark.element.dataset.torchTheme, "dark");
  assert.equal(dark.element.dataset.torchHost, undefined);
  assert.equal(dark.host.posted.length, 0, "a standalone page never asks a host");

  const light = fakeWindow({ dark: false });
  initThemeBridge(light.win, light.doc);
  assert.equal(light.element.dataset.torchTheme, "light");
});

test("once the host takes over, the system scheme stops winning", async () => {
  const { initThemeBridge, applyTheme, readTheme, THEME_MESSAGE } = await loadModule();
  const dom = fakeWindow({ embedded: true, dark: true });
  const dispose = initThemeBridge(dom.win, dom.doc);
  applyTheme(dom.element, readTheme({ type: THEME_MESSAGE, scheme: "light", tokens: FULL_TOKENS }));
  assert.equal(dom.element.dataset.torchTheme, "light");
  assert.equal(typeof dispose, "function");
  dispose();
});
