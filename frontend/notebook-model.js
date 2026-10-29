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

const NOTEBOOKS = { life: LIFE_NOTEBOOKS, knowledge: KNOWLEDGE_NOTEBOOKS };
const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

export function notebooksFor(pillar) {
  const list = NOTEBOOKS[String(pillar || "")] || [];
  return list.slice();
}

export function notebookById(pillar, key) {
  return notebooksFor(pillar).find((item) => item.key === String(key || "")) || null;
}

export function notebookForChapter(pillar, chapter) {
  const target = String(chapter || "");
  if (!target) return null;
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
export function buildNotebookCards(pillar, chapterRows = []) {
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
  // 清单里新增但还没登记到本子的章节，放进“其他”，避免内容在首页消失。
  const leftovers = rows.filter((row) => row && !known.has(row.key)
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
      ? "可以继续补充，也可以翻开一个本子。"
      : "不必写完整。写一句今天的天气、心情，或者一件小事。",
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
