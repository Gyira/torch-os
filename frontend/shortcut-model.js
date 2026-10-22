// 键盘规则与总览文案：纯数据 + 纯函数，不碰 DOM，方便 Node 测试。
// 快捷键的总览只列真的实现了的入口；不要写成愿景表。

export const SHORTCUT_GROUPS = [
  {
    id: "global",
    title: "全局",
    items: [
      { keys: ["Ctrl/⌘", "K"], label: "打开全局搜索" },
      { keys: ["/"], label: "打开全局搜索（焦点不在输入框时）" },
      { keys: ["?"], label: "打开这个快捷键总览" },
      { keys: ["Esc"], label: "关闭弹窗或取消当前操作" },
    ],
  },
  {
    id: "navigate",
    title: "跳转",
    items: [
      { keys: ["G", "W"], join: "then", label: "工作台" },
      { keys: ["G", "L"], join: "then", label: "人生路" },
      { keys: ["G", "S"], join: "then", label: "自我档案" },
      { keys: ["G", "O"], join: "then", label: "生活运营" },
      { keys: ["G", "K"], join: "then", label: "知识库" },
      { keys: ["G", ","], join: "then", label: "设置" },
      { keys: ["G", "H"], join: "then", label: "设置 → 帮助" },
    ],
  },
  {
    id: "actions",
    title: "常用操作",
    items: [
      { keys: ["N"], label: "四库：打开「记一条」；工作台：聚焦新任务输入" },
      { keys: ["Ctrl/⌘", "Enter"], label: "在捕获弹窗里直接保存" },
    ],
  },
  {
    id: "lists",
    title: "列表与表单",
    items: [
      { keys: ["←", "→", "↑", "↓"], label: "在顶部标签、设置侧栏、章节轨与时间页之间移动" },
      { keys: ["Home", "End"], label: "跳到列表第一项或最后一项" },
      { keys: ["Enter"], label: "打开选中项或激活焦点按钮" },
      { keys: ["Tab"], label: "按顺序移动到下一个控件" },
      { keys: ["Shift", "Tab"], label: "反向移动" },
    ],
  },
];

/** 先按 G 再按这些键：与顶部导航一一对应。 */
export const VIEW_CHORDS = Object.freeze({
  w: "workbench",
  l: "life",
  s: "self",
  o: "ops",
  k: "knowledge",
  ",": "settings",
  h: "help",
});

/** 可编辑控件里的单键留给输入法、光标和浏览器；组合键另算。 */
export function isEditableTarget(target) {
  if (!target) return false;
  const tag = String(target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return true;
  if (target.isContentEditable === true) return true;
  try {
    return Boolean(target.closest?.("input, textarea, select, [contenteditable=''], [contenteditable='true']"));
  } catch {
    return false;
  }
}

/**
 * 列表导航：返回下一个应聚焦的下标，非导航键返回 -1。
 * 方向键循环，Home/End 跳到两端；current 为 -1 时从合理的一端进入。
 */
export function nextListIndex(key, current, length) {
  if (!Number.isInteger(length) || length <= 0) return -1;
  const index = Number.isInteger(current) ? current : -1;
  switch (key) {
    case "ArrowRight":
    case "ArrowDown":
      return index < 0 ? 0 : (index + 1) % length;
    case "ArrowLeft":
    case "ArrowUp":
      return index < 0 ? length - 1 : (index - 1 + length) % length;
    case "Home":
      return 0;
    case "End":
      return length - 1;
    default:
      return -1;
  }
}

/**
 * 把一次 keydown 翻译成全局命令。DOM 负责执行，这里只做判断，便于单测。
 * 命令：open-search / open-shortcuts / start-chord / cancel-chord /
 *       navigate:<view> / new-capture / null
 */
export function shortcutCommand(event, { chordPending = false, editable = false, dialogOpen = false } = {}) {
  if (!event || event.defaultPrevented || event.isComposing) return null;
  const key = String(event.key || "");
  const lower = key.toLowerCase();
  const ctrlOrMeta = Boolean(event.ctrlKey || event.metaKey) && !event.altKey;
  // Ctrl/⌘ K 在输入框里也打开搜索；但弹窗打开时不再叠一层。
  if (ctrlOrMeta && !event.shiftKey && lower === "k") return dialogOpen ? null : "open-search";
  if (event.ctrlKey || event.metaKey || event.altKey) return null;
  if (dialogOpen) return null;
  if (editable) return null;
  if (chordPending) {
    if (key === "Escape") return "cancel-chord";
    const target = VIEW_CHORDS[lower];
    if (target) return `navigate:${target}`;
    // 不认识的组合键：结束组合状态，再按普通单键继续判断。
  }
  if (key === "?" || key === "？" || (event.shiftKey && key === "/")) return "open-shortcuts";
  if (key === "/") return "open-search";
  if (lower === "g") return "start-chord";
  if (lower === "n") return "new-capture";
  return null;
}
