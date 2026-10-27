// 自我档案首屏：把「一堆笔记」排成一份档案——封面、目录、自传时间线。
// 纯函数，不 import core.js，node --test 可以直接跑。

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPES[char]);

// 顺序就是档案的阅读顺序：我先是谁，再从哪来，然后怎么想、和谁、要去哪。
const FIELDS = [
  ["name", "署名"],
  ["motto", "座右铭"],
  ["worldview", "世界观"],
  ["lifeview", "人生观"],
  ["values", "价值观"],
  ["notes", "自述"],
];

const CHAPTER_HINTS = {
  core_values: "你信什么，什么不能碰",
  memory: "从哪来，经历过什么",
  idea: "突然想到的、还没成形的",
  relationship: "你和谁在一起、怎么相处",
  meaning: "你想去哪，为什么",
  distill_work: "你验证过的方法",
  distill_career: "你的职业底线",
};

export function archiveCompleteness(identity = {}) {
  const missing = [];
  let filled = 0;
  for (const [key, label] of FIELDS) {
    if (valueOf(identity, key)) filled += 1;
    else missing.push(label);
  }
  const total = FIELDS.length;
  return { filled, total, missing, percent: Math.round((filled / total) * 100) };
}

// 座右铭有两处来源：表单写的 `motto`，和工作台横幅用的 `mottos` 列表；都算填过。
function valueOf(identity, key) {
  if (key === "motto") {
    const single = String(identity?.motto || "").trim();
    if (single) return single;
    const list = Array.isArray(identity?.mottos) ? identity.mottos : [];
    return list.map((line) => String(line || "").trim()).filter(Boolean).join(" ");
  }
  return String(identity?.[key] ?? "").trim();
}

function mottoLines(identity) {
  // 座右铭可能是一条多行文本（横幅里就是这么存的），也可能是一个列表；都摊平成逐行。
  const source = String(identity?.motto || "").trim()
    ? [identity.motto]
    : (Array.isArray(identity?.mottos) ? identity.mottos : []);
  return source
    .flatMap((line) => String(line || "").split("\n"))
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 4);
}

function coverMarkup(identity) {
  const stats = archiveCompleteness(identity);
  const name = String(identity?.name || "").trim();
  const mottos = mottoLines(identity);
  const fields = FIELDS.slice(2).map(([key, label]) => {
    const value = String(identity?.[key] ?? "").trim();
    return `<div class="archive-field${value ? "" : " is-empty"}">
        <dt>${escape(label)}</dt>
        <dd>${value ? escape(value.slice(0, 160)) : "还没写"}</dd>
      </div>`;
  }).join("");
  const next = stats.missing.length
    ? `下一步可以补：${stats.missing.slice(0, 3).map(escape).join("、")}`
    : "六个字段都填上了；接下来靠日记和自传往里长。";
  const updated = String(identity?.updated_at || "").slice(0, 10);
  return `<article class="archive-cover">
      <p class="archive-kicker">自我档案 · SELF ARCHIVE${updated ? ` · 最近修订 ${escape(updated)}` : ""}</p>
      <h3 class="archive-name${name ? "" : " is-empty"}">${name ? escape(name) : "还没有署名"}</h3>
      ${mottos.length ? `<blockquote class="archive-motto">${mottos.map((line) => `<span>${escape(line)}</span>`).join("")}</blockquote>` : `<p class="archive-motto is-empty">还没有座右铭。它不是装饰，是你做选择时的第一道筛子。</p>`}
      <dl class="archive-fields">${fields}</dl>
      <div class="archive-meter">
        <span class="archive-meter-label">档案完整度 ${stats.filled}/${stats.total}</span>
        <span class="archive-bar" role="img" aria-label="完整度 ${stats.percent}%"><i style="width:${stats.percent}%"></i></span>
      </div>
      <p class="archive-next">${escape(next)}</p>
      <button type="button" class="button secondary" data-open-identity="${escape(identity?.path || "")}">在 Obsidian 打开身份卡</button>
    </article>`;
}

function tocMarkup(chapters) {
  const rows = (Array.isArray(chapters) ? chapters : []).map((chapter, index) => {
    const count = Number(chapter.count) || 0;
    const hint = CHAPTER_HINTS[chapter.key] || "";
    return `<li>
        <button type="button" class="archive-toc-row" data-pillar-chapter="self" data-chapter="${escape(chapter.key)}">
          <span class="archive-toc-num">${String(index + 1).padStart(2, "0")}</span>
          <span class="archive-toc-body">
            <strong>${escape(chapter.title || "")}</strong>
            ${hint ? `<em>${escape(hint)}</em>` : ""}
          </span>
          <span class="archive-toc-count${count ? "" : " is-empty"}">${count}</span>
        </button>
      </li>`;
  }).join("");
  return `<nav class="archive-toc" aria-label="档案目录">
      <h4 class="archive-h4">目录</h4>
      ${rows ? `<ol class="archive-toc-list">${rows}</ol>` : `<p class="field-help">章节还没有加载出来。</p>`}
    </nav>`;
}

function timelineMarkup(items) {
  const rows = (Array.isArray(items) ? items : []).map((item) => `<li>
      <time class="archive-time">${escape(item.date || "—")}</time>
      <button type="button" class="archive-event" data-note-path="${escape(item.path || "")}">${escape(item.title || "未命名")}</button>
      ${item.summary ? `<p class="archive-event-note">${escape(String(item.summary).slice(0, 90))}</p>` : ""}
    </li>`).join("");
  return `<section class="archive-timeline" aria-label="自传时间线">
      <h4 class="archive-h4">自传时间线</h4>
      ${rows ? `<ol class="archive-timeline-list">${rows}</ol>`
             : `<p class="field-help">还没有写进自传。跟「与神对话」聊你的经历，它会先放进「身份待确认」，你点头才落到这里。</p>`}
    </section>`;
}

export function archiveMarkup({ identity = {}, chapters = [], timeline = [], noteCount = 0 } = {}) {
  const stats = archiveCompleteness(identity);
  return `<div class="archive" data-archive>
      <p class="archive-summary">${Number(noteCount) || 0} 篇记录 · 身份 ${stats.filled}/${stats.total} 项 · 目录 ${Array.isArray(chapters) ? chapters.length : 0} 个分区</p>
      ${coverMarkup(identity)}
      ${tocMarkup(chapters)}
      ${timelineMarkup(timeline)}
    </div>`;
}
