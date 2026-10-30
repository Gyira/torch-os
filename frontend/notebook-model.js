// 人生路/知识库「本子」数据层（2026-10-30）。
// 纯函数，不碰 DOM、不读 localStorage：把后端章节清单聚合成用户能理解的本子，
// 让首页先回答“我现在要做什么”，而不是把章节摊成一列小字。
//
// 本子只是展示层概念：数据仍是四库里的普通 Markdown 章节，Obsidian 随时可改。
// 新增章节时只要在本文件登记到某个本子；没有登记的可写章节会自动落到「其他」，
// 不会被前端静默藏起来。

export const LIFE_NOTEBOOKS = Object.freeze([
  {
    key: "journal", title: "日记", eyebrow: "DAILY", icon: "日", tone: "amber",
    description: "每天一页，留住今天。", chapters: ["journal"], daily: true,
  },
  {
    key: "reflection", title: "反思复盘", eyebrow: "REVIEW", icon: "思", tone: "plum",
    description: "把经历变成下一步。", chapters: ["reflection", "review_week", "review_month"],
  },
  {
    key: "insight", title: "心得体会", eyebrow: "INSIGHT", icon: "得", tone: "sage",
    description: "生活、工作、学习里的收获。",
    chapters: ["insight_life", "insight_work", "insight_study"],
  },
  {
    key: "goal", title: "目标", eyebrow: "GOALS", icon: "标", tone: "blue",
    description: "年度、季度与月度方向。",
    chapters: ["goal_year", "goal_quarter", "goal_month"],
  },
  {
    key: "project", title: "项目", eyebrow: "PROJECTS", icon: "项", tone: "indigo",
    description: "项目计划与项目复盘。", chapters: ["project_plan", "work_review_project"],
  },
  {
    key: "essay", title: "随笔", eyebrow: "ESSAY", icon: "笔", tone: "rose",
    description: "还没归类的想法与片段。", chapters: ["essay"],
  },
  {
    key: "work", title: "工作复盘", eyebrow: "WORK", icon: "工", tone: "slate",
    description: "会议收获与踩坑记录。", chapters: ["work_review_meeting", "work_review_pitfall"],
  },
]);

export const KNOWLEDGE_NOTEBOOKS = Object.freeze([
  {
    key: "inbox", title: "收件箱", eyebrow: "INBOX", icon: "收", tone: "amber",
    description: "先接住，再慢慢分流。", chapters: ["inbox"], primary: true,
  },
  {
    key: "topic", title: "主题笔记", eyebrow: "TOPICS", icon: "题", tone: "blue",
    description: "围绕一个问题持续长的笔记。", chapters: ["topic"],
  },
  {
    key: "reading", title: "读书笔记", eyebrow: "READING", icon: "读", tone: "sage",
    description: "读完留下真正能用的一条。", chapters: ["reading"],
  },
  {
    key: "course", title: "课程笔记", eyebrow: "COURSE", icon: "课", tone: "indigo",
    description: "课程、讲座与训练营。", chapters: ["course"],
  },
  {
    key: "concept", title: "概念卡片", eyebrow: "CONCEPTS", icon: "念", tone: "plum",
    description: "一个概念一张卡，方便回看。", chapters: ["concept"],
  },
  {
    key: "method", title: "方法库", eyebrow: "METHODS", icon: "法", tone: "rose",
    description: "工作方法与通用方法。", chapters: ["method_work", "method_general"],
  },
  {
    key: "output", title: "输出与作品", eyebrow: "OUTPUT", icon: "出", tone: "slate",
    description: "写出来的文章、作品与分享。", chapters: ["output"],
  },
]);

// 人生路只有一个「写点什么」入口：默认先落到随笔，写完再改本子。
export const DEFAULT_LIFE_WRITE_CHAPTER = "essay";
export const DEFAULT_KNOWLEDGE_WRITE_CHAPTER = "topic";

export function defaultWriteChapter(pillar) {
  return String(pillar || "") === "knowledge"
    ? DEFAULT_KNOWLEDGE_WRITE_CHAPTER
    : DEFAULT_LIFE_WRITE_CHAPTER;
}

const NOTEBOOKS = { life: LIFE_NOTEBOOKS, knowledge: KNOWLEDGE_NOTEBOOKS };
const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
export const CUSTOM_NOTEBOOK_PREFIX = "custom:";
// 知识库使用更长的前缀，避免两个库出现同名本子时 key 冲突。
export const KNOWLEDGE_NOTEBOOK_PREFIX = "custom:knowledge:";
const CUSTOM_NOTEBOOK_PREFIXES = Object.freeze({
  life: CUSTOM_NOTEBOOK_PREFIX,
  knowledge: KNOWLEDGE_NOTEBOOK_PREFIX,
});
const CUSTOM_TONES = ["amber", "plum", "sage", "blue", "indigo", "rose", "slate"];

/** 自定义本子 key → 所在库（life / knowledge）；不是本子 key 返回空串。 */
export function customNotebookPillar(key) {
  const value = String(key || "");
  if (value.startsWith(KNOWLEDGE_NOTEBOOK_PREFIX)
      && value.length > KNOWLEDGE_NOTEBOOK_PREFIX.length) {
    return "knowledge";
  }
  if (value.startsWith(CUSTOM_NOTEBOOK_PREFIX)
      && !value.startsWith(KNOWLEDGE_NOTEBOOK_PREFIX)
      && value.length > CUSTOM_NOTEBOOK_PREFIX.length) {
    return "life";
  }
  return "";
}

export function isCustomNotebookKey(key) {
  return Boolean(customNotebookPillar(key));
}

export function customNotebookName(key) {
  const pillar = customNotebookPillar(key);
  if (!pillar) return "";
  return String(key).slice(CUSTOM_NOTEBOOK_PREFIXES[pillar].length);
}

export function customNotebookKey(name, pillar = "life") {
  const prefix = CUSTOM_NOTEBOOK_PREFIXES[pillar] || CUSTOM_NOTEBOOK_PREFIX;
  return `${prefix}${String(name || "")}`;
}

function toneForName(name) {
  let total = 0;
  for (const character of String(name || "")) total += character.codePointAt(0) || 0;
  return CUSTOM_TONES[total % CUSTOM_TONES.length];
}

function iconForName(name) {
  const value = String(name || "").trim();
  const first = Array.from(value)[0] || "本";
  return /[0-9]/.test(first) ? "本" : first;
}

/** 自定义本子的最小定义；count/latest 由 overview 的 custom_notebooks 补齐。 */
export function customNotebookDefinition(key) {
  const name = customNotebookName(key);
  const pillar = customNotebookPillar(key);
  if (!name || !pillar) return null;
  const root = pillar === "knowledge" ? "03_知识库" : "00_人生路";
  return {
    key: String(key),
    title: name,
    eyebrow: "MY NOTEBOOK",
    icon: iconForName(name),
    tone: toneForName(name),
    description: `自建本子 · ${root}/${name}`,
    chapters: [String(key)],
    custom: true,
    pillar,
  };
}

export function notebooksFor(pillar) {
  const list = NOTEBOOKS[String(pillar || "")] || [];
  return list.slice();
}

export function notebookById(pillar, key) {
  const target = String(key || "");
  return notebooksFor(pillar).find((item) => item.key === target)
    || customNotebookDefinition(target);
}

export function notebookForChapter(pillar, chapter) {
  const target = String(chapter || "");
  if (!target) return null;
  if (isCustomNotebookKey(target)) return customNotebookDefinition(target);
  return notebooksFor(pillar).find((item) => item.chapters.includes(target)) || null;
}

function latestByDate(rows) {
  const notes = rows.map((row) => row?.latest).filter(Boolean);
  if (!notes.length) return null;
  return notes.slice().sort((left, right) => {
    const leftKey = `${left.date || ""}|${left.updated || ""}|${left.mtime || ""}|${left.path || ""}`;
    const rightKey = `${right.date || ""}|${right.updated || ""}|${right.mtime || ""}|${right.path || ""}`;
    return rightKey.localeCompare(leftKey);
  })[0];
}

/**
 * 把 /api/pillars/{pillar}/overview 的 chapters 聚合成本子卡片。
 * 每个本子的 count 是内部章节之和，latest 取最新的一篇；空本子也会保留，
 * 因为“这个本子还没写”本身是引导，不应该被隐藏。
 */
export function buildNotebookCards(pillar, chapterRows = [], customRows = []) {
  const rows = Array.isArray(chapterRows) ? chapterRows : [];
  const byKey = new Map(rows.filter(Boolean).map((row) => [row.key, row]));
  const known = new Set();
  const cards = notebooksFor(pillar).map((notebook) => {
    const mine = notebook.chapters.map((key) => byKey.get(key)).filter(Boolean);
    for (const key of notebook.chapters) known.add(key);
    return {
      ...notebook,
      count: mine.reduce((total, row) => total + (Number(row.count) || 0), 0),
      latest: latestByDate(mine),
      chapters: mine.map((row) => ({
        key: row.key,
        title: row.title || row.key,
        count: Number(row.count) || 0,
      })),
      empty: mine.every((row) => (Number(row.count) || 0) === 0),
    };
  });
  // 自定义本子：前端新建的本子 = 00_人生路 下的文件夹，由后端 overview.custom_notebooks 提供。
  const customCards = (Array.isArray(customRows) ? customRows : [])
    .map((row) => {
      const key = String(row?.key || "");
      const definition = customNotebookDefinition(key);
      if (!definition) return null;
      const count = Number(row.count) || 0;
      return {
        ...definition,
        title: String(row.title || definition.title),
        path: String(row.path || ""),
        count,
        latest: row.latest || null,
        chapters: [{ key, title: String(row.title || definition.title), count }],
        empty: count === 0,
      };
    })
    .filter(Boolean);
  cards.push(...customCards);
  // 清单里新增但还没登记到本子的章节，放进“其他”，避免内容在首页消失。
  const customKeys = new Set(customCards.map((card) => card.key));
  const leftovers = rows.filter((row) => row && !known.has(row.key)
    && !customKeys.has(row.key) && !isCustomNotebookKey(row.key)
    && !["task", "habit"].includes(row.key));
  if (leftovers.length) {
    cards.push({
      key: "other", title: "其他", eyebrow: "OTHERS", icon: "他", tone: "slate",
      description: "还没归入本子的章节。",
      chapters: leftovers.map((row) => ({ key: row.key, title: row.title || row.key, count: Number(row.count) || 0 })),
      count: leftovers.reduce((total, row) => total + (Number(row.count) || 0), 0),
      latest: latestByDate(leftovers),
      empty: leftovers.every((row) => (Number(row.count) || 0) === 0),
    });
  }
  return cards;
}

export function notebookTotal(cards = []) {
  return cards.reduce((total, card) => total + (Number(card.count) || 0), 0);
}

export function subchaptersFor(pillar, notebookKey, chapterRows = []) {
  const notebook = notebookById(pillar, notebookKey);
  if (!notebook) return [];
  const byKey = new Map((Array.isArray(chapterRows) ? chapterRows : [])
    .filter(Boolean).map((row) => [row.key, row]));
  if (isCustomNotebookKey(notebookKey)) {
    const row = byKey.get(String(notebookKey));
    return [{
      key: String(notebookKey), title: row?.title || notebook.title,
      count: Number(row?.count) || 0,
    }];
  }
  return notebook.chapters
    .map((key) => byKey.get(key))
    .filter(Boolean)
    .map((row) => ({ key: row.key, title: row.title || row.key, count: Number(row.count) || 0 }));
}

export function dayLabel(value) {
  const text = String(value || "").trim();
  if (!/^\d{4}-\d{2}-\d{2}/.test(text)) return text;
  try {
    const date = new Date(`${text.slice(0, 10)}T00:00:00Z`);
    const monthDay = new Intl.DateTimeFormat("zh-CN", {
      month: "long", day: "numeric", timeZone: "UTC",
    }).format(date);
    return `${monthDay} ${WEEKDAYS[date.getUTCDay()]}`;
  } catch {
    return text;
  }
}

function isoWeekNumber(date) {
  const target = new Date(Date.UTC(
    date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const weekday = target.getUTCDay() || 7;
  target.setUTCDate(target.getUTCDate() + 4 - weekday);
  const yearStart = new Date(Date.UTC(target.getUTCFullYear(), 0, 1));
  return Math.ceil(((target - yearStart) / 86400000 + 1) / 7);
}

export function journalHeading(day) {
  const text = String(day || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return "# 今天";
  try {
    const date = new Date(`${text}T00:00:00Z`);
    return `# ${text}（第${isoWeekNumber(date)}周，${WEEKDAYS[date.getUTCDay()]}）`;
  } catch {
    return `# ${text}`;
  }
}

export function noteCard(note, { pillar = "", chapterTitle = "" } = {}) {
  const value = note && typeof note === "object" ? note : {};
  const journal = value.chapter === "journal";
  const date = String(value.date || value.created || "").slice(0, 10);
  return {
    path: String(value.path || ""),
    title: journal ? `日记 · ${dayLabel(date || value.title)}` : String(value.title || "未命名"),
    excerpt: String(value.excerpt || (journal ? "打开这篇日记" : "")),
    date,
    dateLabel: dayLabel(date),
    tags: Array.isArray(value.tags) ? value.tags.slice(0, 3) : [],
    chapter: String(value.chapter || ""),
    chapterTitle: chapterTitle || "",
    journal,
    pillar,
  };
}

export function noteCards(notes = [], options = {}) {
  const list = Array.isArray(notes) ? notes : [];
  const limit = Number.isFinite(options.limit) ? Math.max(1, options.limit) : list.length;
  return list.slice(0, limit).map((note) => noteCard(note, options));
}

export function greetingForHour(hour) {
  const value = Number(hour);
  if (!Number.isFinite(value)) return "你好";
  if (value < 5) return "夜深了";
  if (value < 11) return "早上好";
  if (value < 14) return "中午好";
  if (value < 18) return "下午好";
  return "晚上好";
}

export function lifeHeroState(overview = {}, today = "") {
  const current = overview?.today || {};
  const week = Array.isArray(current.week) ? current.week : [];
  const day = String(today || current.date || "");
  const written = Boolean(current.journal_written);
  let streak = 0;
  if (written) {
    for (let index = week.length - 1; index >= 0; index -= 1) {
      if (!week[index]?.exists) break;
      streak += 1;
    }
  }
  return {
    day,
    dayLabel: dayLabel(day),
    written,
    streak,
    week,
    weekDone: week.filter((item) => item.exists).length,
    updatedAt: current.journal_updated_at || "",
    todayPath: week.find((item) => item.date === day)?.path || "",
    headline: written ? "今天已经留下一页。" : "今天，写点什么？",
    copy: written
      ? "可以继续补今天的日记，也可以把新灵感先写进随笔。"
      : "不必写完整。写一句今天的天气、心情，或者刚冒出的灵感。",
  };
}

export function knowledgeHeroState(overview = {}) {
  const inbox = Number(overview?.inbox_count) || 0;
  return {
    inboxCount: inbox,
    copy: inbox
      ? `还有 ${inbox} 条收件箱笔记等着分流。`
      : "先问 DeepSeek；值得留下的回答，再带回来归档。",
  };
}

export function mergeNotesByRecency(groups = []) {
  const seen = new Set();
  const merged = [];
  for (const group of groups) {
    for (const note of Array.isArray(group) ? group : []) {
      if (!note?.path || seen.has(note.path)) continue;
      seen.add(note.path);
      merged.push(note);
    }
  }
  return merged.sort((left, right) => {
    const leftKey = `${left.date || ""}|${left.updated || ""}|${left.mtime || ""}`;
    const rightKey = `${right.date || ""}|${right.updated || ""}|${right.mtime || ""}`;
    return rightKey.localeCompare(leftKey);
  });
}
