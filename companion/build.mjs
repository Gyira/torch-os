import { build } from "esbuild";
import { mkdir, copyFile } from "node:fs/promises";
await mkdir("dist/torch-companion", { recursive: true });
await build({ entryPoints: ["plugin/main.ts"], outfile: "dist/torch-companion/main.js", bundle: true,
  platform: "browser", format: "cjs", target: "es2022", external: ["obsidian"], sourcemap: false });
await copyFile("plugin/manifest.json", "dist/torch-companion/manifest.json");
await copyFile("plugin/styles.css", "dist/torch-companion/styles.css");
await copyFile("third-party/obsidian-baidu-netdisk-sync/LICENSE", "dist/torch-companion/THIRD-PARTY-LICENSE.txt");
console.log("Built dist/torch-companion (not installed into a personal vault)");
