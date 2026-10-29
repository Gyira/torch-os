// Markdown 编辑器纯数据层（2026-10-29，v1.0 P2）。
// 不碰 DOM；所有操作输入/输出 {text, selectionStart, selectionEnd}，方便 node --test。
// 编辑器只写 Markdown 源，预览由 pillar-markdown.js 负责，保持与 Obsidian 兼容。

export const TOOLBAR_ACTIONS = Object.freeze([
  { id: "h2", label: "H2", title: "标题（##）" },
  { id: "bold", label: "B", title: "加粗（**）" },
  { id: "italic", label: "I", title: "斜体（*）" },
  { id: "strike", label: "S", title: "删除线（~~）" },
  { id: "bullet", label: "•", title: "无序列表" },
  { id: "task", label: "☑", title: "待办清单" },
  { id: "quote", label: "❝", title: "引用" },
  { id: "code", label: "</>", title: "行内代码" },
  { id: "link", label: "🔗", title: "链接" },
  { id: "divider", label: "—", title: "分割线" },
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
    case "h2": return prefixLines(text, start, end, "## ");
    case "h3": return prefixLines(text, start, end, "### ");
    case "bold": return wrapSelection(text, start, end, "**", "**", "加粗文字");
    case "italic": return wrapSelection(text, start, end, "*", "*", "斜体文字");
    case "strike": return wrapSelection(text, start, end, "~~", "~~", "删除线");
    case "code": return wrapSelection(text, start, end, "`", "`", "代码");
    case "link": return wrapSelection(text, start, end, "[", "](https://)", "链接文字");
    case "bullet": return prefixLines(text, start, end, "- ");
    case "task": return prefixLines(text, start, end, "- [ ] ");
    case "quote": return prefixLines(text, start, end, "> ");
    case "divider": return insertSnippet(text, start, end, "\n\n---\n\n");
    default: return { text: String(text || ""), selectionStart: start, selectionEnd: end };
  }
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
