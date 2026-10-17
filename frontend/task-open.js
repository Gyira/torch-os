// 打开任务文档（2026-10-17）：插件里走宿主桥，浏览器里走 obsidian://。
// 旧任务没有自己的文档，path 指向 任务（tasks）.md，打开的就是那份只读总文件。
// 这个模块刻意不依赖 core.js/DOM：调用方负责把 false 转成状态行提示。

/** 手写查询串解析：不依赖 URLSearchParams（测试用的 vm 上下文/旧环境也能跑）。 */
function queryValue(search, key) {
  const text = String(search || "").replace(/^\?/, "");
  for (const part of text.split("&")) {
    if (!part) continue;
    const [name, ...rest] = part.split("=");
    let decoded = "";
    try {
      decoded = decodeURIComponent(rest.join("="));
    } catch {
      decoded = rest.join("=");
    }
    let decodedName = name;
    try {
      decodedName = decodeURIComponent(name);
    } catch {
      // 保留原文
    }
    if (decodedName === key) return decoded;
  }
  return null;
}

/** 当前页面是否由 Obsidian 插件以 iframe 承载（插件会带 host=obsidian）。 */
export function isObsidianHost(search = globalThis.location?.search || "") {
  return queryValue(search, "embed") === "1" && queryValue(search, "host") === "obsidian";
}

/** Obsidian 深链接：vault 名 + 相对路径，参数各自编码。 */
export function obsidianOpenUri(vaultName, path) {
  const vault = String(vaultName || "").trim();
  const file = String(path || "").trim();
  if (!vault || !file) return "";
  return `obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(file)}`;
}

/** 发给插件的消息体（独立成纯函数，方便 Node 测试）。 */
export function openNoteMessage(path) {
  return { type: "torch:open-note", path: String(path || "") };
}

/**
 * 打开任务文档。返回 false = 没有可打开的目标（调用方负责提示）。
 * 在 Obsidian 内由插件调用 app.vault.getAbstractFileByPath → openFile；
 * 插件版本太旧（没有宿主桥）时退回 obsidian://，仍然能到达同一个库。
 */
export function openTaskDocument(task, board) {
  const path = String(task?.path || "").trim();
  if (!path) return false;
  if (isObsidianHost() && globalThis.parent && globalThis.parent !== globalThis) {
    try {
      globalThis.parent.postMessage(openNoteMessage(path), "*");
      return true;
    } catch {
      // 落到 obsidian:// 兜底
    }
  }
  const uri = obsidianOpenUri(board?.vault_name || "", path);
  if (!uri) return false;
  try {
    globalThis.open?.(uri, "_blank", "noopener");
  } catch {
    return false;
  }
  return true;
}
