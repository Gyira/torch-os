import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";
import { runInNewContext } from "node:vm";
import { fileURLToPath } from "node:url";
test("plugin loads offline, has no bundled Node imports and refuses a copied wrong-vault configuration", async () => {
  const source = buildSync({ entryPoints: [fileURLToPath(new URL("../plugin/main.ts", import.meta.url))], bundle: true, platform: "browser", format: "cjs", external: ["obsidian"], write: false }).outputFiles[0].text;
  let network = 0;
  class Base {
    app = { vault: { configDir: ".obsidian", on: () => ({}), getAbstractFileByPath: () => null }, workspace: { onLayoutReady: () => {}, getLeavesOfType: () => [] } };
    manifest = { id: "torch-companion" }; commands: { id: string }[] = [];
    async loadData() { return null; } async saveData(_: unknown) {}
    registerView() {} addSettingTab() {} addRibbonIcon() {} registerEvent() {} registerInterval() {} registerDomEvent() {}
    addCommand(value: { id: string }) { this.commands.push(value); }
  }
  const module = { exports: {} as { default: typeof Base & (new () => Base & { onload(): Promise<void>; engine(): Promise<unknown>; settings: { workspace: string; account: string; password: string } }) } };
  runInNewContext(source, { module, exports: module.exports, crypto, TextEncoder, TextDecoder, Uint8Array, ArrayBuffer, structuredClone, URL, URLSearchParams,
    window: { setInterval: () => 1 }, document: {},
    require: (name: string) => { assert.equal(name, "obsidian"); return { Plugin: Base, Modal: class {}, ItemView: class {}, PluginSettingTab: class {}, Notice: class {}, requestUrl: () => { network++; throw new Error("offline"); } }; }
  });
  const plugin = new module.exports.default(); await plugin.onload();
  assert.ok(plugin.commands.some(c => c.id === "capture")); assert.ok(plugin.commands.some(c => c.id === "queue"));
  assert.equal(network, 0);
  plugin.settings.workspace = "copied-other-workspace"; plugin.settings.account = "account"; plugin.settings.password = "sync-password-123";
  await assert.rejects(plugin.engine(), /身份/); assert.equal(network, 0);
});
