// Markdown 编辑器纯数据层（2026-10-29，v1.0 P2）。
// 不碰 DOM；所有操作输入/输出 {text, selectionStart, selectionEnd}，方便 node --test。
// 编辑器只写 Markdown 源，预览由 pillar-markdown.js 负责，保持与 Obsidian 兼容。

export const TOOLBAR_ACTIONS = Object.freeze([
  { id: "h1", label: "H1", title: "大标题（#）", group: "heading" },
  { id: "h2", label: "H2", title: "标题（##）", group: "heading" },
  { id: "h3", label: "H3", title: "小标题（###）", group: "heading" },
  { id: "bold", label: "B", title: "加粗（Ctrl/⌘ B）", group: "format" },
  { id: "italic", label: "I", title: "斜体（Ctrl/⌘ I）", group: "format" },
  { id: "strike", label: "S", title: "删除线", group: "format" },
  { id: "code", label: "</>", title: "行内代码", group: "format" },
  { id: "bullet", label: "•", title: "无序列表", group: "list" },
  { id: "numbered", label: "1.", title: "有序列表", group: "list" },
  { id: "task", label: "☑", title: "待办清单", group: "list" },
  { id: "quote", label: "❝", title: "引用", group: "block" },
  { id: "link", label: "🔗", title: "链接（Ctrl/⌘ K）", group: "block" },
  { id: "table", label: "▦", title: "插入表格", group: "block" },
  { id: "divider", label: "—", title: "分割线", group: "block" },
]);

export function wrapSelection(text, start, end, before, after = before, placeholder = "") {
  const value = String(text || "");
  const from = Math.max(0, Math.min(Number(start) || 0, value.length));
  const to = Math.max(from, Math.min(Number(end) || from, value.length));
  const selected = value.slice(from, to) || placeholder;
  const next = value.slice(0, from) + before + selected + after + value.slice(to);
  const selectionStart = from + before.length;
  return {
    text: next,
    selectionStart,
    selectionEnd: selectionStart + selected.length,
  };
}

function lineRange(text, start, end) {
  const value = String(text || "");
  const lineStart = value.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
  let lineEnd = value.indexOf("\n", Math.max(0, end));
  if (lineEnd < 0) lineEnd = value.length;
  return { lineStart, lineEnd };
}

/** 给选中的每一行加/去前缀；空行保持空行，避免把分隔线也变成列表。 */
export function prefixLines(text, start, end, prefix) {
  const value = String(text || "");
  const from = Math.max(0, Math.min(Number(start) || 0, value.length));
  const to = Math.max(from, Math.min(Number(end) || from, value.length));
  const { lineStart, lineEnd } = lineRange(value, from, to);
  const block = value.slice(lineStart, lineEnd);
  const lines = block.split("\n");
  const meaningful = lines.filter((line) => line.trim());
  const allPrefixed = meaningful.length > 0
    && meaningful.every((line) => line.startsWith(prefix));
  const changed = lines.map((line) => {
    if (!line.trim()) return line;
    return allPrefixed ? line.slice(prefix.length) : `${prefix}${line}`;
  }).join("\n");
  return {
    text: value.slice(0, lineStart) + changed + value.slice(lineEnd),
    selectionStart: lineStart,
    selectionEnd: lineStart + changed.length,
  };
}

export function insertSnippet(text, start, end, snippet) {
  const value = String(text || "");
  const from = Math.max(0, Math.min(Number(start) || 0, value.length));
  const to = Math.max(from, Math.min(Number(end) || from, value.length));
  const next = value.slice(0, from) + snippet + value.slice(to);
  return { text: next, selectionStart: from + snippet.length, selectionEnd: from + snippet.length };
}

export function applyEditorAction(text, start, end, actionId) {
  switch (String(actionId || "")) {
    case "h1": return prefixLines(text, start, end, "# ");
    case "h2": return prefixLines(text, start, end, "## ");
    case "h3": return prefixLines(text, start, end, "### ");
    case "bold": return wrapSelection(text, start, end, "**", "**", "加粗文字");
    case "italic": return wrapSelection(text, start, end, "*", "*", "斜体文字");
    case "strike": return wrapSelection(text, start, end, "~~", "~~", "删除线");
    case "code": return wrapSelection(text, start, end, "`", "`", "代码");
    case "link": return wrapSelection(text, start, end, "[", "](https://)", "链接文字");
    case "bullet": return prefixLines(text, start, end, "- ");
    case "numbered": return prefixLines(text, start, end, "1. ");
    case "task": return prefixLines(text, start, end, "- [ ] ");
    case "quote": return prefixLines(text, start, end, "> ");
    case "table": return insertSnippet(text, start, end, "\n\n| 列 1 | 列 2 |\n| --- | --- |\n|  |  |\n\n");
    case "divider": return insertSnippet(text, start, end, "\n\n---\n\n");
    default: return { text: String(text || ""), selectionStart: start, selectionEnd: end };
  }
}

/** 日记文件包含 front matter；编辑器只改正文，保存时把原 front matter 原样接回去。 */
export function splitFrontMatter(text) {
  const value = String(text || "").replace(/\r\n?/g, "\n");
  if (!value.startsWith("---\n")) return { frontmatter: "", body: value };
  const end = value.indexOf("\n---", 4);
  if (end < 0) return { frontmatter: "", body: value };
  const bodyStart = value.indexOf("\n", end + 4);
  return {
    frontmatter: value.slice(0, end + 4),
    body: (bodyStart < 0 ? "" : value.slice(bodyStart + 1)).replace(/^\n+/, ""),
  };
}

export function joinFrontMatter(frontmatter, body) {
  const head = String(frontmatter || "").trim();
  const text = String(body || "").replace(/^\n+/, "");
  return head ? `${head}\n\n${text}` : text;
}

export function ensureJournalFrontMatter(day, frontmatter = "") {
  const head = String(frontmatter || "").trim();
  if (/^---\n[\s\S]*\btype:\s*journal\b[\s\S]*\n---$/.test(head)) return head;
  return `---\ntype: journal\ndate: ${String(day || "").slice(0, 10)}\n---`;
}

/** 汉字算 1 字，英文/数字按单词计（和项目其它字数提示一致）。 */
export function wordCount(text) {
  const value = String(text || "");
  const chinese = (value.match(/[一-鿿]/g) || []).length;
  const words = (value.replace(/[一-鿿]/g, " ").match(/[A-Za-z0-9_]+/g) || []).length;
  return chinese + words;
}

export function normalizeBody(text) {
  return String(text || "").replace(/\r\n?/g, "\n").replace(/[ \t]+$/gm, "");
}

export function isDirty(current, saved) {
  return normalizeBody(current) !== normalizeBody(saved);
}

/**
 * 编辑已有笔记时一次“保存”可能包含两件事：写正文、移动到另一个本子。
 * 先写正文再移动，移动失败时至少内容不丢；新建笔记不移动。
 */
export function savePlan({ mode = "new", dirty = false, currentChapter = "", targetChapter = "" } = {}) {
  const editing = String(mode) === "edit";
  const target = String(targetChapter || currentChapter || "");
  return {
    saveContent: editing && Boolean(dirty),
    move: editing && Boolean(target) && target !== String(currentChapter || ""),
    targetChapter: target,
  };
}

export function tagsFromInput(value) {
  return String(value || "")
    .split(/[,，、\s]+/)
    .map((item) => item.trim().replace(/^#/, ""))
    .filter(Boolean)
    .slice(0, 24);
}

export function tagsToInput(tags) {
  return (Array.isArray(tags) ? tags : []).join("、");
}
