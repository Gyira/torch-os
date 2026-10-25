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

/** Obsidian 新建笔记深链接：文件由 Obsidian 自己创建并打开，插件不写盘。 */
export function obsidianNewUri(vaultName, path, content = "") {
  const vault = String(vaultName || "").trim();
  const file = String(path || "").trim();
  if (!vault || !file) return "";
  const query = `vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(file)}`;
  const text = String(content || "");
  return text ? `obsidian://new?${query}&content=${encodeURIComponent(text)}` : `obsidian://new?${query}`;
}

/** 新建并打开笔记的消息体（插件会先建文件再打开）。 */
export function newNoteMessage(path, content = "") {
  return { type: "torch:new-note", path: String(path || ""), content: String(content || "") };
}

/** 在 Obsidian 里打开一篇已有笔记；返回 false = 没有可打开的目标。 */
export function openObsidianNote(path, vaultName) {
  const target = String(path || "").trim();
  if (!target) return false;
  if (isObsidianHost() && globalThis.parent && globalThis.parent !== globalThis) {
    try {
      globalThis.parent.postMessage(openNoteMessage(target), "*");
      return true;
    } catch {
      // 落到 obsidian:// 兜底
    }
  }
  const uri = obsidianOpenUri(vaultName, target);
  if (!uri) return false;
  try {
    globalThis.open?.(uri, "_blank", "noopener");
  } catch {
    return false;
  }
  return true;
}

/**
 * 在 Obsidian 里新建并打开一篇笔记（写入由 Obsidian 完成），返回 false = 没有可用目标。
 * 插件里走宿主桥（插件建文件 + 打开）；浏览器里走 obsidian://new。
 */
export function createObsidianNote(path, vaultName, content = "") {
  const target = String(path || "").trim();
  if (!target) return false;
  if (isObsidianHost() && globalThis.parent && globalThis.parent !== globalThis) {
    try {
      globalThis.parent.postMessage(newNoteMessage(target, content), "*");
      return true;
    } catch {
      // 落到 obsidian:// 兜底
    }
  }
  const uri = obsidianNewUri(vaultName, target, content);
  if (!uri) return false;
  try {
    globalThis.open?.(uri, "_blank", "noopener");
  } catch {
    return false;
  }
  return true;
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
