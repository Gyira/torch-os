// 人生路写作模板（2026-10-31）。
// 参考 jotsy 的内置日记模板：标题下的「从模板开始」建议条、完整的模板面板、
// 按当下时段/星期推荐的规则，以及八套内置模板。
//
// 纯数据 + 纯函数，不碰 DOM：模板正文在这里编译成 Markdown，{{变量}} 在套用
// 当天解析；保存仍走现有的 /api/writing/notes 与 /api/journal/write。
// 模板内容与 jotsy 的 built_in/diary_template_built_ins.dart 一一对应：
// 小标题是加粗行 + emoji，冒号不加粗；空行留给用户填写；分隔线收束段落。

export const TEMPLATE_CATEGORIES = Object.freeze([
  Object.freeze({ key: "daily", label: "日常" }),
  Object.freeze({ key: "review", label: "复盘" }),
  Object.freeze({ key: "mood", label: "情绪" }),
  Object.freeze({ key: "life", label: "生活" }),
]);

const CATEGORY_LABELS = Object.fromEntries(
  TEMPLATE_CATEGORIES.map((category) => [category.key, category.label]));

const blanks = (count) => Array.from({ length: count }, () => "");
const WEEKDAYS = Object.freeze(["周日", "周一", "周二", "周三", "周四", "周五", "周六"]);

// 与 jotsy 的题库一致：同一天固定一题，跨天轮换，「换一题」把偏移加一。
const QUESTIONS = Object.freeze([
  "今天让你微笑的一件小事是什么？",
  "如果今天可以重来，你会改变什么？",
  "最近一次感到自豪是什么时候？",
  "此刻你最想对谁说一句话？",
  "今天学到的一件新东西是什么？",
  "最近有什么事让你一直放不下？",
  "你理想中的周末是什么样子？",
  "今天有哪个瞬间让你觉得被理解？",
  "最近在读、在看或在听什么？它带给你什么？",
  "你现在最需要的是休息、陪伴还是挑战？",
  "一年前的今天你在担心什么？现在呢？",
  "今天你为自己做了什么？",
  "最近一次大笑是因为什么？",
  "有什么习惯是你想开始或想放下的？",
  "用三个词形容今天的自己。",
  "今天最大的困难是什么？你是怎么应对的？",
  "你最近感激的一个人是谁？为什么？",
  "如果明天只能做一件事，你会做什么？",
  "最近有什么让你改变了看法？",
  "今天的天气让你想起了什么？",
  "最近一次走出舒适区是什么时候？",
  "十年后的你会怎样看待今天的烦恼？",
  "今天有没有哪句话让你印象深刻？",
  "最近让你感到平静的地方是哪里？",
  "你想对小时候的自己说些什么？",
  "今天的你，比昨天多了解自己一点了吗？",
  "最近有什么值得庆祝的小进步？",
  "有什么事一直想尝试却还没开始？是什么在阻碍你？",
  "今天你是怎样照顾自己的情绪的？",
  "如果给今天打分（1–10），你会打几分？为什么？",
]);

// 24×24 线性图标：与 jotsy 的 FontAwesome 图标一一对应，颜色由外层徽章决定。
const ICONS = Object.freeze({
  pen: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20.5 14.2A8.5 8.5 0 1 1 9.8 3.5a7 7 0 0 0 10.7 10.7Z"/>',
  heart: '<path d="M20.8 5.6a5 5 0 0 0-7.1 0L12 7.3l-1.7-1.7a5 5 0 0 0-7.1 7.1L12 21l8.8-8.3a5 5 0 0 0 0-7.1Z"/>',
  question: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.6 2.6 0 1 1 3.6 2.4c-.7.4-1.1 1-1.1 1.8v.3"/><path d="M12 17h.01"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 11h18"/>',
  smile: '<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5a4 4 0 0 0 7 0"/><path d="M9 9.5h.01M15 9.5h.01"/>',
  book: '<path d="M12 7c-2-1.5-4.5-2-8-2v13c3.5 0 6 .5 8 2 2-1.5 4.5-2 8-2V5c-3.5 0-6 .5-8 2Z"/><path d="M12 7v13"/>',
});

/** 八套内置模板；顺序就是模板面板里的默认顺序（日常 → 复盘 → 情绪 → 生活）。 */
export const JOURNAL_TEMPLATES = Object.freeze([
  Object.freeze({
    id: "daily",
    name: "每日记录",
    description: "记下今天的故事，和此刻的心情",
    icon: "pen",
    accent: "blue",
    category: "daily",
    titlePattern: "{{date}} {{weekday}}",
    blocks: Object.freeze([
      { type: "label", mark: "📖", text: "今天的故事：" },
      { type: "paragraph" },
      { type: "gap" },
      { type: "label", mark: "✨", text: "闪光的瞬间：" },
      { type: "paragraph" },
      { type: "gap" },
      { type: "label", mark: "🌿", text: "此刻的心情：" },
      { type: "paragraph" },
      { type: "divider" },
      { type: "label", mark: "🌙", text: "写给今天的一句话：" },
      { type: "quote" },
    ]),
  }),
  Object.freeze({
    id: "morning",
    name: "晨间日记",
    description: "在清晨写下一页，给今天一个温柔的方向",
    icon: "sun",
    accent: "orange",
    category: "daily",
    titlePattern: "晨间 · {{date}}",
    recommend: Object.freeze({ slots: Object.freeze(["morning"]) }),
    blocks: Object.freeze([
      { type: "note", mark: "⏰", text: "{{time}} 醒来" },
      { type: "label", mark: "🎯", text: "今天最重要的三件事：" },
      { type: "checklist", items: blanks(3) },
      { type: "gap" },
      { type: "label", mark: "🌱", text: "今天期待的小事：" },
      { type: "bullets", items: blanks(1) },
      { type: "divider" },
      { type: "label", mark: "☀️", text: "给今天的一句话：" },
      { type: "quote" },
    ]),
  }),
  Object.freeze({
    id: "evening",
    name: "晚间复盘",
    description: "回看今天的得与失，把明天安排妥当",
    icon: "moon",
    accent: "purple",
    category: "review",
    titlePattern: "复盘 · {{date}}",
    recommend: Object.freeze({ slots: Object.freeze(["evening", "night"]) }),
    blocks: Object.freeze([
      { type: "label", mark: "✅", text: "今天完成了：" },
      { type: "bullets", items: blanks(1) },
      { type: "gap" },
      { type: "label", mark: "👍", text: "做得好的地方：" },
      { type: "bullets", items: blanks(1) },
      { type: "gap" },
      { type: "label", mark: "🔧", text: "可以更好的地方：" },
      { type: "bullets", items: blanks(1) },
      { type: "divider" },
      { type: "label", mark: "📅", text: "明天的安排：" },
      { type: "checklist", items: blanks(2) },
      { type: "gap" },
      { type: "label", mark: "🌙", text: "睡前想对自己说：" },
      { type: "quote" },
    ]),
  }),
  Object.freeze({
    id: "gratitude",
    name: "感恩日记",
    description: "写下三件让你心怀感激的小事",
    icon: "heart",
    accent: "pink",
    category: "mood",
    titlePattern: "感恩 · {{date}}",
    tags: Object.freeze(["感恩"]),
    blocks: Object.freeze([
      { type: "label", mark: "🌸", text: "今天让我心怀感激的三件事：" },
      { type: "numbered", items: blanks(3) },
      { type: "gap" },
      { type: "label", mark: "💌", text: "想对谁说声谢谢：" },
      { type: "paragraph" },
      { type: "gap" },
      { type: "label", mark: "🤍", text: "也谢谢今天的自己：" },
      { type: "paragraph" },
    ]),
  }),
  Object.freeze({
    id: "question",
    name: "每日一问",
    description: "每天一个问题，换个角度认识自己",
    icon: "question",
    accent: "cyan",
    category: "daily",
    titlePattern: "每日一问 · {{date}}",
    blocks: Object.freeze([
      { type: "label", mark: "💭", text: "今日一问：" },
      { type: "quote", text: "{{question}}" },
      { type: "gap" },
      { type: "label", mark: "✍️", text: "我的回答：" },
      { type: "paragraph" },
    ]),
  }),
  Object.freeze({
    id: "weekly",
    name: "周回顾",
    description: "收拢一周的亮点与收获，轻装走进下一周",
    icon: "calendar",
    accent: "green",
    category: "review",
    titlePattern: "周回顾 · {{date}}",
    recommend: Object.freeze({ weekdays: Object.freeze([7, 1]) }),
    blocks: Object.freeze([
      { type: "note", mark: "🗓️", text: "第 {{week}} 周 · 今年已走过 {{yearProgress}}" },
      { type: "label", mark: "🌟", text: "本周亮点：" },
      { type: "bullets", items: blanks(1) },
      { type: "gap" },
      { type: "label", mark: "🏆", text: "完成与收获：" },
      { type: "bullets", items: blanks(1) },
      { type: "gap" },
      { type: "label", mark: "⏳", text: "还没做完的事：" },
      { type: "checklist", items: blanks(1) },
      { type: "divider" },
      { type: "label", mark: "🧭", text: "下周想做的事：" },
      { type: "checklist", items: blanks(3) },
      { type: "gap" },
      { type: "label", mark: "💬", text: "写给下周的自己：" },
      { type: "quote" },
    ]),
  }),
  Object.freeze({
    id: "emotion",
    name: "情绪梳理",
    description: "把情绪写下来，看清它从哪里来",
    icon: "smile",
    accent: "brown",
    category: "mood",
    titlePattern: "情绪梳理 · {{date}}",
    blocks: Object.freeze([
      { type: "label", mark: "🌧️", text: "发生了什么：" },
      { type: "paragraph" },
      { type: "gap" },
      { type: "label", mark: "💗", text: "我感受到的情绪：" },
      { type: "paragraph" },
      { type: "gap" },
      { type: "label", mark: "🔍", text: "它从哪里来：" },
      { type: "paragraph" },
      { type: "gap" },
      { type: "label", mark: "🤲", text: "我真正需要的是：" },
      { type: "paragraph" },
      { type: "divider" },
      { type: "label", mark: "🌱", text: "接下来能做的一件小事：" },
      { type: "checklist", items: blanks(1) },
    ]),
  }),
  Object.freeze({
    id: "reading",
    name: "读书笔记",
    description: "摘录与感想，留下读过的痕迹",
    icon: "book",
    accent: "orange",
    category: "life",
    titlePattern: "读书笔记 · {{date}}",
    tags: Object.freeze(["读书"]),
    blocks: Object.freeze([
      { type: "label", mark: "📚", text: "书名：" },
      { type: "label", mark: "🖋️", text: "作者：" },
      { type: "label", mark: "🔖", text: "读到：" },
      { type: "divider" },
      { type: "label", mark: "✒️", text: "摘录：" },
      { type: "quote" },
      { type: "gap" },
      { type: "label", mark: "💡", text: "我的想法：" },
      { type: "paragraph" },
    ]),
  }),
]);

const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));

const pad = (value) => String(value).padStart(2, "0");

/** Asia/Shanghai 的今天（与 journal-model.js 同一套边界，模板模块保持无 import）。 */
function shanghaiDay(now) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now);
  const value = (type) => parts.find((part) => part.type === type)?.value || "";
  return `${value("year")}-${value("month")}-${value("day")}`;
}

function dayDate(day) {
  const text = String(day || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return null;
  const date = new Date(`${text}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function dayKeyOf(date) {
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

function dayOfYear(date) {
  return Math.floor((date - Date.UTC(date.getUTCFullYear(), 0, 1)) / 86400000) + 1;
}

function daysInYear(date) {
  return dayOfYear(new Date(Date.UTC(date.getUTCFullYear(), 11, 31)));
}

/** ISO 8601 周数：含当年第一个星期四的那周是第 1 周。 */
export function isoWeekNumber(date) {
  const target = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const weekday = target.getUTCDay() || 7;
  target.setUTCDate(target.getUTCDate() + 4 - weekday);
  const yearStart = Date.UTC(target.getUTCFullYear(), 0, 1);
  return Math.floor((target - yearStart) / 86400000 / 7) + 1;
}

/** 补写旧日记时用日记日期；写今天时用当下。两者都按 Asia/Shanghai。 */
export function templateVariables(day, { now = new Date(), questionShift = 0 } = {}) {
  const date = dayDate(day) || dayDate(shanghaiDay(now));
  const key = date ? dayKeyOf(date) : String(day || "").slice(0, 10);
  const dateText = date
    ? new Intl.DateTimeFormat("zh-CN", {
      timeZone: "UTC", year: "numeric", month: "long", day: "numeric",
    }).format(date)
    : key;
  const time = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(now);
  const dayNumber = date ? Math.floor((date - Date.UTC(1970, 0, 1)) / 86400000) : 0;
  const question = QUESTIONS[((dayNumber + Number(questionShift || 0)) % QUESTIONS.length + QUESTIONS.length) % QUESTIONS.length];
  return Object.freeze({
    date: dateText,
    weekday: date ? WEEKDAYS[date.getUTCDay()] : "",
    time,
    year: date ? String(date.getUTCFullYear()) : "",
    month: date ? `${date.getUTCMonth() + 1}月` : "",
    day: date ? String(date.getUTCDate()) : "",
    week: date ? String(isoWeekNumber(date)) : "",
    yearProgress: date ? `${Math.round((dayOfYear(date) / daysInYear(date)) * 100)}%` : "",
    question,
  });
}

/** 17:00–21:59 是 evening；22:00–4:59 是 night（jotsy 的同一套时段）。 */
export function templateTimeSlot(now = new Date()) {
  const hour = Number(new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Shanghai", hour: "2-digit", hourCycle: "h23",
  }).format(now));
  if (hour >= 5 && hour < 11) return "morning";
  if (hour >= 11 && hour < 17) return "afternoon";
  if (hour >= 17 && hour < 22) return "evening";
  return "night";
}

/**
 * 此刻这个模板是否推荐：不推荐返回 null，推荐时返回命中的条件类别数。
 * 时段看当下，星期看日记日期；补写旧日记时不看时段（早上推荐晨间日记已无意义）。
 */
export function scoreTemplate(template, { day, now = new Date() } = {}) {
  const rules = template?.recommend || {};
  const slots = Array.isArray(rules.slots) ? rules.slots : [];
  const weekdays = Array.isArray(rules.weekdays) ? rules.weekdays : [];
  if (!slots.length && !weekdays.length) return null;
  const date = dayDate(day);
  if (!date) return null;
  const writingToday = dayKeyOf(date) === shanghaiDay(now);
  let score = 0;
  if (slots.length && writingToday) {
    if (!slots.includes(templateTimeSlot(now))) return null;
    score += 1;
  }
  if (weekdays.length) {
    const weekday = date.getUTCDay() || 7;
    if (!weekdays.includes(weekday)) return null;
    score += 1;
  }
  return score || null;
}

/** 全部模板按“推荐条件越多越靠前，同分按内置顺序”排序。 */
export function rankedTemplates({ day, now = new Date() } = {}) {
  return JOURNAL_TEMPLATES
    .map((template, index) => {
      const score = scoreTemplate(template, { day, now });
      return { template, recommended: score !== null, score: score || 0, index };
    })
    .sort((left, right) => right.score - left.score || left.index - right.index)
    .map(({ template, recommended }) => ({ template, recommended }));
}

/**
 * 建议条上的五个模板：先放推荐的，再放最近用过的，不够时按内置顺序补齐；
 * 同一个模板只出现一次（与 jotsy 的 DiaryTemplateLibrary.suggestions 一致）。
 */
export function suggestTemplates({ day, now = new Date(), recent = [], limit = 5 } = {}) {
  const ranked = rankedTemplates({ day, now });
  const byId = new Map(ranked.map((item) => [item.template.id, item]));
  const picked = new Map();
  const add = (item) => {
    if (!item || picked.size >= limit) return;
    if (!picked.has(item.template.id)) picked.set(item.template.id, item);
  };
  for (const item of ranked) if (item.recommended) add(item);
  for (const id of Array.isArray(recent) ? recent : []) add(byId.get(String(id)));
  for (const item of ranked) add(item);
  return [...picked.values()];
}

/** 面板上的筛选项：全部、推荐（此刻有推荐时），以及有模板的分类。 */
export function templateFilters(ranked) {
  const items = Array.isArray(ranked) ? ranked : [];
  const filters = [{ key: "all", label: "全部" }];
  if (items.some((item) => item.recommended)) filters.push({ key: "recommended", label: "推荐" });
  for (const category of TEMPLATE_CATEGORIES) {
    if (items.some((item) => item.template.category === category.key)) {
      filters.push({ key: category.key, label: category.label });
    }
  }
  return filters;
}

function searchTerms(query) {
  const value = String(query || "").trim().toLowerCase();
  return value ? value.split(/\s+/) : [];
}

/** 搜索匹配显示名称、说明与分类名称，每个词都要命中（AND）。 */
export function templateMatchesQuery(template, query) {
  const terms = searchTerms(query);
  if (!terms.length) return true;
  const haystack = [
    template.name,
    template.description,
    CATEGORY_LABELS[template.category] || "",
  ].join("\n").toLowerCase();
  return terms.every((term) => haystack.includes(term));
}

export function templateMatchesFilter(item, filter) {
  const key = String(filter || "all");
  if (key === "all") return true;
  if (key === "recommended") return Boolean(item.recommended);
  return item.template.category === key;
}

/** 过滤 + 搜索后的模板行；空结果是正常状态，由调用方决定空提示。 */
export function filterTemplates(ranked, { filter = "all", query = "" } = {}) {
  return (Array.isArray(ranked) ? ranked : []).filter(
    (item) => templateMatchesFilter(item, filter) && templateMatchesQuery(item.template, query));
}

export function templateById(id) {
  return JOURNAL_TEMPLATES.find((template) => template.id === String(id || "")) || null;
}

export function templateUsesQuestion(template) {
  return JSON.stringify(template?.blocks || []).includes("{{question}}")
    || String(template?.titlePattern || "").includes("{{question}}");
}

function fillTokens(text, variables) {
  return String(text ?? "").replace(/\{\{(\w+)\}\}/g, (match, key) => (
    variables && Object.prototype.hasOwnProperty.call(variables, key) ? String(variables[key]) : match));
}

function markText(block) {
  const text = String(block?.text || "");
  return block?.mark ? `${block.mark} ${text}` : text;
}

/** 把块描述编译成 Markdown；空列表项用不换行空格占位，避免保存时被裁成裸标记。 */
function compileBlocks(blocks, variables) {
  let out = "";
  for (const block of blocks || []) {
    switch (block.type) {
      case "label": {
        const full = fillTokens(markText(block), variables);
        const suffix = /[:：]\s*$/.exec(full);
        const head = suffix ? full.slice(0, suffix.index) : full;
        out += `**${head}**${suffix ? suffix[0] : ""}\n`;
        break;
      }
      case "note":
        out += `*${fillTokens(markText(block), variables)}*\n`;
        break;
      case "paragraph":
        out += `${fillTokens(block.text || "", variables)}\n`;
        break;
      case "gap":
        out += "\n";
        break;
      case "bullets":
        for (const item of block.items || []) {
          out += `- ${fillTokens(item, variables) || "\u00a0"}\n`;
        }
        break;
      case "numbered":
        for (const item of block.items || []) {
          out += `1. ${fillTokens(item, variables) || "\u00a0"}\n`;
        }
        break;
      case "checklist":
        for (const item of block.items || []) {
          const text = fillTokens(item, variables);
          out += `- [ ]${text ? ` ${text}` : ""}\n`;
        }
        break;
      case "quote":
        out += `> ${fillTokens(block.text || "", variables)}\n`;
        break;
      case "divider":
        if (out && !out.endsWith("\n\n")) out += "\n";
        out += "---\n\n";
        break;
      default:
        break;
    }
  }
  return `${out.replace(/\n{4,}/g, "\n\n\n").trimEnd()}\n`;
}

/** 按当天变量展开模板：标题、正文、预设标签与“是否用到今日一问”。 */
export function resolveTemplate(template, variables) {
  if (!template) return null;
  return {
    id: template.id,
    name: template.name,
    title: fillTokens(template.titlePattern, variables),
    body: compileBlocks(template.blocks, variables),
    tags: [...(template.tags || [])],
    usesQuestion: templateUsesQuestion(template),
  };
}

/** 日记正文：模板标题成为当天的 H1，后面接模板正文。 */
export function journalTemplateBody(title, body) {
  const head = String(title || "").trim();
  const text = String(body || "").replace(/^\n+/, "").trimEnd();
  if (!head) return text ? `${text}\n` : "";
  return `# ${head}${text ? `\n\n${text}` : ""}\n`;
}

/**
 * 日记是否只有当天日期的骨架标题（后端用 2026.10.31，前端草稿用 2026-10-31）。
 * 只有这种机器生成的标题算空白；用户写的任何自定义 H1 或正文都算已开始写。
 */
export function isBlankJournalBody(body, day) {
  const text = String(body || "").trim();
  if (!text) return true;
  const date = String(day || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return false;
  const lines = text.split("\n").filter((line) => line.trim());
  if (lines.length !== 1 || !/^#\s+/.test(lines[0])) return false;
  const normalized = lines[0].replace(/^#\s+/, "").replace(/\./g, "-");
  if (!normalized.startsWith(date)) return false;
  // 只认「日期」或「日期（第N周…）」这种骨架标题；日期后面还有别的话就是用户写的。
  const rest = normalized.slice(date.length).trim();
  return rest === "" || /^[（(]第\d+周/.test(rest);
}

/** 套用后光标停在第一个空行的行首；没有空行时停在正文末尾。 */
export function firstEmptyLineOffset(text) {
  const value = String(text || "");
  for (let index = 0; index < value.length; index += 1) {
    if (value[index] === "\n" && (index === 0 || value[index - 1] === "\n")) return index;
  }
  return value.length;
}

/** 行内图标与徽章；颜色只由 data-accent 决定，遵守严格 CSP（无内联样式）。 */
export function templateIconMarkup(icon) {
  const paths = ICONS[icon] || ICONS.pen;
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${paths}</svg>`;
}

export function templateBadgeMarkup(template, { large = false } = {}) {
  return `<span class="template-badge${large ? " is-large" : ""}" data-accent="${esc(template.accent)}" aria-hidden="true">${templateIconMarkup(template.icon)}</span>`;
}

const RECOMMEND_BADGE = '<span class="template-recommend">推荐</span>';

/** 建议条上的一个模板胶囊：图标徽章 + 名称，推荐的带“推荐”标记。 */
export function templateChipMarkup(template, recommended = false) {
  return `<button type="button" class="template-chip" data-template-id="${esc(template.id)}" aria-label="套用模板：${esc(template.name)}${recommended ? "，推荐" : ""}">${templateBadgeMarkup(template)}<span class="template-chip-name">${esc(template.name)}</span>${recommended ? RECOMMEND_BADGE : ""}</button>`;
}

export function templateStripMarkup(suggestions) {
  return (Array.isArray(suggestions) ? suggestions : [])
    .map((item) => templateChipMarkup(item.template, item.recommended))
    .join("");
}

/** 模板面板的一行：图标、名称、两行说明与“推荐”标记。 */
export function templateRowMarkup(template, recommended = false) {
  const label = [`预览模板：${template.name}`];
  if (template.description) label.push(template.description);
  if (recommended) label.push("推荐");
  return `<button type="button" class="template-row" data-template-id="${esc(template.id)}" aria-label="${esc(label.join("，"))}">${templateBadgeMarkup(template, { large: true })}<span class="template-row-body"><span class="template-row-head"><span class="template-row-name">${esc(template.name)}</span>${recommended ? RECOMMEND_BADGE : ""}</span><span class="template-row-desc">${esc(template.description)}</span></span></button>`;
}

export function templateFilterMarkup(filters, active = "all") {
  return (Array.isArray(filters) ? filters : [])
    .map((filter) => `<button type="button" class="template-filter" data-template-filter="${esc(filter.key)}" aria-pressed="${filter.key === active ? "true" : "false"}">${esc(filter.label)}</button>`)
    .join("");
}

export function templateEmptyMarkup(query = "") {
  const value = String(query || "").trim();
  return `<div class="template-empty"><span class="template-empty-glyph" aria-hidden="true">⌕</span><p>${value ? `没有找到与「${esc(value)}」相关的模板` : "这里还没有模板"}</p></div>`;
}
