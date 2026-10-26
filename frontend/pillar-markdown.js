// 四库阅读视图用的 Obsidian 风格 Markdown 渲染（无第三方依赖）。
// 安全边界：所有文本先转义，再按白名单生成 HTML；绝不把原始 HTML 写进页面。
import { escapeHtml } from "./core.js";

const IMAGE_SUFFIXES = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp"]);
const TOKEN = "\u0000";
const PIPE = "\u0001";

const CALLOUTS = {
  note: { icon: "ℹ", label: "笔记" },
  info: { icon: "ℹ", label: "说明" },
  important: { icon: "❗", label: "重要" },
  abstract: { icon: "📋", label: "摘要" },
  summary: { icon: "📋", label: "摘要" },
  tldr: { icon: "📋", label: "摘要" },
  todo: { icon: "☑", label: "待办" },
  tip: { icon: "💡", label: "提示" },
  hint: { icon: "💡", label: "提示" },
  success: { icon: "✅", label: "完成" },
  check: { icon: "✅", label: "完成" },
  done: { icon: "✅", label: "完成" },
  question: { icon: "❓", label: "问题" },
  help: { icon: "❓", label: "问题" },
  faq: { icon: "❓", label: "问题" },
  warning: { icon: "⚠", label: "注意" },
  caution: { icon: "⚠", label: "注意" },
  attention: { icon: "⚠", label: "注意" },
  failure: { icon: "✖", label: "失败" },
  fail: { icon: "✖", label: "失败" },
  missing: { icon: "✖", label: "缺失" },
  danger: { icon: "🚨", label: "危险" },
  error: { icon: "🚨", label: "错误" },
  bug: { icon: "🐛", label: "缺陷" },
  example: { icon: "🧪", label: "示例" },
  quote: { icon: "❝", label: "引用" },
  cite: { icon: "❝", label: "引用" },
  idea: { icon: "✨", label: "想法" },
};

function decodeEntities(value) {
  return String(value ?? "")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&");
}

function leadingWidth(line) {
  const match = /^[ \t]*/.exec(String(line ?? ""));
  return (match ? match[0] : "").replace(/\t/g, "    ").length;
}

function plainHeading(text) {
  return String(text ?? "")
    .replace(/!?\[\[([^\]|]+)(?:\|[^\]]*)?\]\]/g, "$1")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[`*_~=]/g, "")
    .trim();
}

function slugify(text) {
  const slug = plainHeading(text)
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .trim()
    .replace(/[\s-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "section";
}

function headingMarkup(level, text, ctx) {
  const bodyLevel = Math.max(2, Math.min(6, level + 1));
  return pushHeading(bodyLevel, text, ctx);
}

function pushHeading(level, text, ctx) {
  const plain = plainHeading(text);
  const base = slugify(plain);
  const seen = (ctx.slugs.get(base) || 0) + 1;
  ctx.slugs.set(base, seen);
  const id = seen === 1 ? base : `${base}-${seen}`;
  const inner = inline(text, ctx);
  ctx.headings.push({ id, level, text: plain, html: inner });
  return `<h${level} id="${escapeHtml(id)}" class="md-heading">${inner}</h${level}>`;
}

/** 把相对附件路径解析成库内路径；越出库根时返回空串。 */
function resolveDocPath(notePath, href) {
  let raw = String(href ?? "").trim();
  if (!raw) return "";
  try { raw = decodeURIComponent(raw); } catch { /* keep raw */ }
  if (/^[a-z][a-z0-9+.-]*:/i.test(raw) || raw.startsWith("//")) return "";
  const parts = raw.startsWith("/") ? [] : String(notePath || "").split("/").slice(0, -1);
  for (const piece of raw.replace(/^\/+/, "").split("/")) {
    if (!piece || piece === ".") continue;
    if (piece === "..") {
      if (!parts.length) return "";
      parts.pop();
      continue;
    }
    parts.push(piece);
  }
  return parts.join("/");
}

function assetUrl(notePath, src) {
  const value = String(src ?? "").trim();
  if (!value || /^[a-z][a-z0-9+.-]*:/i.test(value) || value.startsWith("//")) return "";
  const resolved = resolveDocPath(notePath, value);
  if (!resolved) return "";
  const suffix = (resolved.match(/\.[a-z0-9]+$/i) || [""])[0].toLowerCase();
  if (!IMAGE_SUFFIXES.has(suffix)) return "";
  return `/api/notes/asset?path=${encodeURIComponent(resolved)}`;
}

function imageMarkup(alt, src, ctx) {
  const decoded = decodeEntities(src);
  if (/^https?:\/\//i.test(decoded)) {
    // CSP 只允许本站图片；外部图片渲染成可点开的链接，避免被拦截后留下破图。
    return `<a class="md-image-link" href="${escapeHtml(decoded)}" target="_blank" rel="noreferrer">🖼 ${alt || escapeHtml(decoded)}</a>`;
  }
  const url = assetUrl(ctx.notePath, decoded);
  if (url) {
    return `<span class="md-image"><img src="${escapeHtml(url)}" alt="${alt}" loading="lazy" decoding="async">${alt ? `<span class="md-image-caption">${alt}</span>` : ""}</span>`;
  }
  return `<span class="md-image-missing" title="${escapeHtml(decoded)}">🖼 ${alt || escapeHtml(decoded)}</span>`;
}

function splitWiki(inner) {
  let target = String(inner ?? "");
  let alias = "";
  const pipe = target.indexOf("|");
  if (pipe >= 0) {
    alias = target.slice(pipe + 1).trim();
    target = target.slice(0, pipe);
  }
  let anchor = "";
  const hash = target.indexOf("#");
  if (hash >= 0) {
    anchor = target.slice(hash + 1).trim();
    target = target.slice(0, hash);
  }
  return { target: target.trim(), anchor, alias };
}

function wikilinkMarkup(inner, embed, ctx) {
  const { target, anchor, alias } = splitWiki(inner);
  const label = alias || target || anchor;
  if (!label) return "";
  const targetPath = decodeEntities(target);
  if (embed) {
    const url = assetUrl(ctx.notePath, targetPath);
    if (url) {
      return `<span class="md-image"><img src="${escapeHtml(url)}" alt="${escapeHtml(alias || target)}" loading="lazy" decoding="async">${alias ? `<span class="md-image-caption">${alias}</span>` : ""}</span>`;
    }
    return `<button type="button" class="md-embed" data-md-note="${escapeHtml(targetPath)}">↗ ${label}</button>`;
  }
  return `<button type="button" class="md-wikilink" data-md-note="${escapeHtml(targetPath)}"${anchor ? ` data-md-anchor="${escapeHtml(decodeEntities(anchor))}"` : ""}>${label}</button>`;
}

function linkMarkup(label, href, ctx) {
  const rawHref = decodeEntities(href);
  const text = label || escapeHtml(rawHref);
  if (!rawHref) return text;
  if (rawHref.startsWith("#")) {
    const id = slugify(decodeEntities(rawHref.slice(1)));
    return `<a class="md-anchor" href="#${escapeHtml(id)}">${text}</a>`;
  }
  if (/^https?:|^mailto:|^obsidian:/i.test(rawHref)) {
    return `<a href="${escapeHtml(rawHref)}" target="_blank" rel="noreferrer">${text}</a>`;
  }
  if (/^(?:javascript|data|vbscript|file):/i.test(rawHref) || rawHref.startsWith("//")) {
    return text;
  }
  if (/\.md(?:#.*)?$/i.test(rawHref)) {
    // 没有 notePath（通用渲染/单测）时保留普通链接；阅读视图里再解析成库内跳转。
    if (!ctx.notePath) return `<a href="${escapeHtml(rawHref)}">${text}</a>`;
    const resolved = resolveDocPath(ctx.notePath, rawHref);
    if (!resolved) return text;
    return `<button type="button" class="md-wikilink" data-md-note="${escapeHtml(resolved)}">${text}</button>`;
  }
  return text;
}

function inline(rawText, ctx) {
  const tokens = [];
  const stash = (html) => {
    tokens.push(html);
    return `${TOKEN}${tokens.length - 1}${TOKEN}`;
  };
  let value = escapeHtml(String(rawText ?? ""));
  // Obsidian 注释 %%...%% 不显示。
  value = value.replace(/%%[\s\S]*?%%/g, "");
  // 反斜杠转义先落成占位符，避免后面的强调规则再吃掉它。
  value = value.replace(/\\([\\`*_{}\[\]()#+\-.!>~=|])/g, (_, char) => stash(escapeHtml(char)));
  value = value.replace(/`+([^`]+?)`+/g, (_, code) => stash(`<code class="md-inline-code">${code}</code>`));
  // 链接目标允许一层成对括号，避免 javascript:alert(1) 这类内容把右括号留在正文里。
  const destination = String.raw`((?:[^()\s]|\([^()\s]*\))+)`;
  value = value.replace(new RegExp(String.raw`!\[([^\]]*)\]\(\s*${destination}(?:\s+(?:"([^"]*)"|'([^']*)'))?\s*\)`, "g"),
    (_, alt, src) => stash(imageMarkup(alt, src, ctx)));
  value = value.replace(/(!?)\[\[([^\[\]]+)\]\]/g,
    (_, bang, inner) => stash(wikilinkMarkup(inner, Boolean(bang), ctx)));
  value = value.replace(new RegExp(String.raw`\[([^\]]*)\]\(\s*${destination}(?:\s+(?:"([^"]*)"|'([^']*)'))?\s*\)`, "g"),
    (_, label, href) => stash(linkMarkup(label, href, ctx)));
  value = value.replace(/&lt;(https?:\/\/[^&\s]+?)&gt;/gi,
    (_, url) => stash(`<a class="md-autolink" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(url)}</a>`));
  value = value.replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>");
  value = value.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  value = value.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  value = value.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  value = value.replace(/(^|[^\w])_([^_\n]+)_(?=[^\w]|$)/g, "$1<em>$2</em>");
  value = value.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  value = value.replace(/==([^=]+)==/g, '<mark class="md-highlight">$1</mark>');
  value = value.replace(/(^|[\s(（])#([\p{L}\p{N}_/-]{1,32})/gu,
    (_, lead, tag) => `${lead}<span class="md-tag">#${tag}</span>`);
  return value.replace(new RegExp(`${TOKEN}(\\d+)${TOKEN}`, "g"),
    (_, index) => tokens[Number(index)] ?? "");
}

function isHr(line) {
  const text = String(line ?? "").trim();
  return /^(?:\*[\s]*){3,}$/.test(text)
    || /^(?:-[\s]*){3,}$/.test(text)
    || /^(?:_[\s]*){3,}$/.test(text);
}

function matchItem(line) {
  const match = /^([ \t]*)([-*+]|\d{1,9}[.)])([ \t]+)(.*)$/.exec(String(line ?? ""));
  if (!match) return null;
  const ordered = /^\d/.test(match[2]);
  return {
    indent: match[1].replace(/\t/g, "    ").length,
    ordered,
    number: ordered ? Number.parseInt(match[2], 10) : 0,
    text: match[4],
  };
}

function splitRow(line) {
  let text = String(line ?? "").trim();
  text = text.replace(/\\\|/g, PIPE);
  if (text.startsWith("|")) text = text.slice(1);
  if (text.endsWith("|")) text = text.slice(0, -1);
  return text.split("|").map((cell) => cell.replaceAll(PIPE, "|").trim());
}

function isTableStart(lines, index) {
  if (index + 1 >= lines.length) return false;
  if (!String(lines[index]).includes("|") || !String(lines[index + 1]).includes("|")) return false;
  const header = splitRow(lines[index]);
  const separator = splitRow(lines[index + 1]);
  if (header.length < 2 || separator.length !== header.length) return false;
  return separator.every((cell) => /^:?-{1,}:?$/.test(cell.replace(/\s/g, "")));
}

function parseTable(lines, start, ctx) {
  if (!isTableStart(lines, start)) return null;
  const header = splitRow(lines[start]);
  const separator = splitRow(lines[start + 1]);
  const align = separator.map((cell) => {
    const text = cell.replace(/\s/g, "");
    const left = text.startsWith(":");
    const right = text.endsWith(":");
    return left && right ? "center" : right ? "right" : left ? "left" : "";
  });
  const rows = [];
  let index = start + 2;
  while (index < lines.length && String(lines[index]).trim() && String(lines[index]).includes("|")) {
    rows.push(splitRow(lines[index]));
    index += 1;
  }
  const cell = (tag, text, column) => `<${tag}${align[column] ? ` class="md-align-${align[column]}"` : ""}>${inline(text, ctx)}</${tag}>`;
  const head = header.map((text, column) => cell("th", text, column)).join("");
  const body = rows.map((row) => `<tr>${header.map((_, column) => cell("td", row[column] || "", column)).join("")}</tr>`).join("");
  return {
    html: `<div class="md-table-wrap"><table class="md-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`,
    next: index,
  };
}

function renderItemText(lines, ctx) {
  const groups = [];
  let group = [];
  for (const line of lines) {
    if (!String(line).trim()) {
      if (group.length) { groups.push(group); group = []; }
      continue;
    }
    group.push(line);
  }
  if (group.length) groups.push(group);
  return groups.map((part, index) => {
    const body = part.map((line) => inline(line, ctx)).join("<br>");
    return index === 0 ? body : `<p>${body}</p>`;
  }).join("");
}

function parseList(lines, start, ctx) {
  const first = matchItem(lines[start]);
  const indent = first.indent;
  const ordered = first.ordered;
  const items = [];
  let index = start;
  while (index < lines.length) {
    const item = matchItem(lines[index]);
    if (!item || item.indent < indent) break;
    if (item.indent > indent) {
      const nested = parseList(lines, index, ctx);
      if (items.length) items[items.length - 1].blocks.push(nested.html);
      index = nested.next;
      continue;
    }
    if (item.ordered !== ordered) break;

    const current = { blocks: [], number: item.number, task: null, textLines: [] };
    let text = item.text;
    const task = /^\[([ xX])\](?:\s+|$)(.*)$/.exec(text);
    if (task) {
      current.task = task[1].toLowerCase() === "x";
      text = task[2];
    }
    current.textLines.push(text);
    index += 1;

    while (index < lines.length) {
      const line = lines[index];
      if (!String(line).trim()) {
        let next = index + 1;
        while (next < lines.length && !String(lines[next]).trim()) next += 1;
        if (next >= lines.length) { index = next; break; }
        const nextItem = matchItem(lines[next]);
        const nextIndent = nextItem ? nextItem.indent : leadingWidth(lines[next]);
        if (nextIndent <= indent) { index = next; break; }
        current.textLines.push("");
        index = next;
        continue;
      }
      const nestedItem = matchItem(line);
      if (nestedItem) {
        if (nestedItem.indent > indent) {
          const nested = parseList(lines, index, ctx);
          current.blocks.push(nested.html);
          index = nested.next;
          continue;
        }
        break;
      }
      if (leadingWidth(line) > indent) {
        current.textLines.push(String(line).trim());
        index += 1;
        continue;
      }
      break;
    }
    items.push(current);
  }

  const tag = ordered ? "ol" : "ul";
  const startNumber = ordered && items[0] && items[0].number > 1 ? ` start="${items[0].number}"` : "";
  const hasTasks = items.some((item) => item.task !== null);
  const html = `<${tag} class="md-list${hasTasks ? " md-task-list" : ""}"${startNumber}>${items.map((item) => {
    const checkbox = item.task === null ? "" : `<input type="checkbox" disabled${item.task ? " checked" : ""}> `;
    const content = renderItemText(item.textLines, ctx) + item.blocks.join("");
    return `<li${item.task === null ? "" : ' class="md-task-item"'}>${checkbox}${content}</li>`;
  }).join("")}</${tag}>`;
  return { html, next: index };
}

function startsBlock(lines, index) {
  const line = String(lines[index] ?? "");
  if (/^ {0,3}(`{3,}|~{3,})/.test(line)) return true;
  if (/^ {0,3}#{1,6}\s+/.test(line)) return true;
  if (/^ {0,3}>/.test(line)) return true;
  if (isHr(line)) return true;
  if (matchItem(line)) return true;
  return isTableStart(lines, index);
}

function quoteMarkup(quoteLines, ctx) {
  const first = String(quoteLines[0] ?? "").trim();
  const callout = /^\[!([A-Za-z0-9_-]+)\][+-]?\s*(.*)$/.exec(first);
  if (!callout) {
    return `<blockquote class="md-quote">${renderBlocks(quoteLines, ctx).join("\n")}</blockquote>`;
  }
  const type = callout[1].toLowerCase();
  const meta = CALLOUTS[type] || CALLOUTS.note;
  const title = callout[2].trim();
  const body = renderBlocks(quoteLines.slice(1), ctx).join("\n");
  const safeType = type.replace(/[^a-z0-9-]/g, "") || "note";
  return `<div class="md-callout md-callout-${safeType}"><p class="md-callout-title"><span class="md-callout-icon" aria-hidden="true">${meta.icon}</span><span>${title ? inline(title, ctx) : escapeHtml(meta.label)}</span></p>${body ? `<div class="md-callout-body">${body}</div>` : ""}</div>`;
}

function renderBlocks(lines, ctx) {
  const out = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!String(line).trim()) { index += 1; continue; }

    const fence = /^ {0,3}(`{3,}|~{3,})\s*([^\s`~]*)\s*$/.exec(line);
    if (fence) {
      const marker = fence[1][0];
      const length = fence[1].length;
      const language = fence[2] || "";
      const code = [];
      index += 1;
      while (index < lines.length) {
        if (new RegExp(`^ {0,3}${marker}{${length},}\\s*$`).test(lines[index])) {
          index += 1;
          break;
        }
        code.push(lines[index]);
        index += 1;
      }
      const langAttr = language ? ` data-lang="${escapeHtml(language)}"` : "";
      out.push(`<pre class="md-code"${langAttr}><code>${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }

    const atx = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
    if (atx) {
      out.push(headingMarkup(atx[1].length, atx[2], ctx));
      index += 1;
      continue;
    }

    if (index + 1 < lines.length && !startsBlock(lines, index)
        && /^ {0,3}(=+|-+)\s*$/.test(lines[index + 1])) {
      const level = String(lines[index + 1]).trim().startsWith("=") ? 2 : 3;
      out.push(pushHeading(level, String(line).trim(), ctx));
      index += 2;
      continue;
    }

    if (isHr(line)) {
      out.push('<hr class="md-hr">');
      index += 1;
      continue;
    }

    const table = parseTable(lines, index, ctx);
    if (table) {
      out.push(table.html);
      index = table.next;
      continue;
    }

    if (/^ {0,3}>/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^ {0,3}>/.test(lines[index])) {
        quoteLines.push(String(lines[index]).replace(/^ {0,3}>\s?/, ""));
        index += 1;
      }
      out.push(quoteMarkup(quoteLines, ctx));
      continue;
    }

    if (matchItem(line)) {
      const list = parseList(lines, index, ctx);
      out.push(list.html);
      index = list.next;
      continue;
    }

    const paragraph = [];
    while (index < lines.length) {
      const current = String(lines[index]);
      if (!current.trim()) { index += 1; break; }
      if (paragraph.length && startsBlock(lines, index)) break;
      paragraph.push(current.trim());
      index += 1;
    }
    out.push(`<p>${paragraph.map((part) => inline(part, ctx)).join("<br>")}</p>`);
  }
  return out;
}

/**
 * 渲染完整笔记。返回 { html, headings }：headings 供阅读视图生成目录。
 * options.notePath 用于解析相对图片附件（/api/notes/asset）。
 */
export function renderMarkdownDocument(text = "", options = {}) {
  const ctx = {
    headings: [],
    notePath: String(options.notePath || ""),
    slugs: new Map(),
  };
  const normalized = String(text ?? "")
    .replace(/\r\n?/g, "\n")
    .replace(/%%[\s\S]*?%%/g, "");
  return { headings: ctx.headings, html: renderBlocks(normalized.split("\n"), ctx).join("\n") };
}

export function renderMarkdown(text = "", options = {}) {
  return renderMarkdownDocument(text, options).html;
}
