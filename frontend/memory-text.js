// Display-only cleaner so review lists read like memories; stored data is untouched.
const LINK = /!?\[([^\]]*)\]\([^)]*\)/g;
const HTML_TAG = /<[^>\n]{1,200}>/g;
const INLINE_CODE = /`([^`]*)`/g;
const HEADING = /^\s{0,3}#{1,6}\s+/;
const QUOTE = /^\s{0,3}>\s?/;
const LIST = /^\s{0,3}(?:[-*+]|\d{1,3}[.)])\s+/;
const TASK = /^\[[ xX]\]\s*/;
const TABLE_SEPARATOR = /^\s*\|?[\s:|-]+\|?\s*$/;

export function cleanMemoryText(value) {
  let text = String(value ?? "").replace(/\u200b/g, " ");
  text = text.replace(LINK, "$1").replace(HTML_TAG, "").replace(INLINE_CODE, "$1");
  const lines = [];
  for (let line of text.split(/\r?\n/)) {
    line = line.replace(HEADING, "").replace(QUOTE, "").replace(LIST, "").replace(TASK, "");
    if (line.includes("|")) line = line.replace(/\s*\|\s*/g, " ").replace(/^[|\s]+|[|\s]+$/g, "");
    if (TABLE_SEPARATOR.test(line)) continue;
    line = line.replace(/\*\*|__|~~|\*|_/g, "");
    lines.push(line.trim());
  }
  return lines.filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
}

export function memoryDisplayTitle(memory) {
  const cleaned = cleanMemoryText(memory?.content || memory?.source_quote || "");
  if (!cleaned) return String(memory?.title || "未命名记忆");
  let sentence = cleaned;
  for (const ender of ["。", "！", "？", "!", "?"]) {
    const index = sentence.indexOf(ender);
    if (index >= 0 && index < 200) { sentence = sentence.slice(0, index + 1); break; }
  }
  return sentence.length <= 24 ? sentence : `${sentence.slice(0, 24)}…`;
}
