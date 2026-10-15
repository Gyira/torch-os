// 模块面守卫：每个 `import { … } from "./x.js"` 都要在 x.js 里真的导出。
// 起因：整体覆盖 nav-order.js 时把 applyNavOrder 等导出弄丢了，
// app.js 静态 import 直接报 “does not provide an export named”，37 个测试一起红。
// 这个检查在跑测试之前就能指出“谁少了哪个名字”。
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const defaultRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// 自测用：允许指向一棵临时树（默认是仓库根）。
const root = process.argv[2] ? path.resolve(process.argv[2]) : defaultRoot;
const targets = [path.join(root, "frontend")];

async function jsFiles(directory) {
  const found = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (entry.name === "tests" || entry.name === "node_modules") continue;
      found.push(...await jsFiles(path.join(directory, entry.name)));
    } else if (entry.name.endsWith(".js") || entry.name.endsWith(".mjs")) {
      found.push(path.join(directory, entry.name));
    }
  }
  return found;
}

function exportedNames(source) {
  const names = new Set();
  for (const match of source.matchAll(/export\s+(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)/g)) names.add(match[1]);
  for (const match of source.matchAll(/export\s*\{([^}]*)\}/g)) {
    for (const part of match[1].split(",")) {
      const name = part.trim().split(/\s+as\s+/).pop().trim();
      if (name) names.add(name);
    }
  }
  return names;
}

const problems = [];
const files = (await Promise.all(targets.map((directory) => jsFiles(directory)))).flat();
for (const file of files) {
  const source = await readFile(file, "utf8");
  const relative = path.relative(root, file).replace(/\\/g, "/");
  for (const match of source.matchAll(/import\s*\{([^}]*)\}\s*from\s*["'](\.[^"']+)["']/g)) {
    const specifier = match[2];
    const target = path.resolve(path.dirname(file), specifier);
    let targetSource;
    try {
      targetSource = await readFile(target, "utf8");
    } catch {
      problems.push(`${relative}: 导入了不存在的模块 ${specifier}`);
      continue;
    }
    const available = exportedNames(targetSource);
    for (const part of match[1].split(",")) {
      const name = part.trim().split(/\s+as\s+/)[0].trim();
      if (name && !available.has(name)) {
        problems.push(`${relative}: ${specifier} 没有导出 ${name}（模块被覆盖/改名了？）`);
      }
    }
  }
  for (const match of source.matchAll(/import\s+([A-Za-z_$][\w$]*)\s+from\s*["'](\.[^"']+)["']/g)) {
    const target = path.resolve(path.dirname(file), match[2]);
    let targetSource;
    try {
      targetSource = await readFile(target, "utf8");
    } catch {
      problems.push(`${relative}: 导入了不存在的模块 ${match[2]}`);
      continue;
    }
    if (!/export\s+default/.test(targetSource)) {
      problems.push(`${relative}: ${match[2]} 没有默认导出`);
    }
  }
  for (const match of source.matchAll(/import\s*["'](\.[^"']+)["']/g)) {
    const target = path.resolve(path.dirname(file), match[1]);
    try {
      await readFile(target, "utf8");
    } catch {
      problems.push(`${relative}: 引用了不存在的模块 ${match[1]}`);
    }
  }
}

if (problems.length) {
  console.log("模块面检查未通过：");
  for (const item of problems) console.log(" -", item);
  process.exit(1);
}
console.log(`模块面检查通过（${files.length} 个模块的 import/export 对得上）。`);
