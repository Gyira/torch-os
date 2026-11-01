// 自我档案 v2 的纯渲染函数（2026-11-01）。
// 不 import core.js，node --test 可以直接跑；只负责把接口数据变成 markup。

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPES[char]);

export const BOOKS = [
  { key: "self_facts", title: "基础事实", subtitle: "时间、钱、居住、身份、结构性处境", icon: "实", folder: "01_基础事实" },
  { key: "self_body", title: "身体与能量", subtitle: "睡眠、运动、饮食、身体信号、能量账本", icon: "身", folder: "02_身体与能量" },
  { key: "self_behavior", title: "行为与习惯", subtitle: "守时、回复、拖延、消费、压力动作", icon: "行", folder: "03_行为与习惯" },
  { key: "self_emotion", title: "情绪与压力", subtitle: "底色、触发点、防御、冲突修复", icon: "情", folder: "04_情绪与压力" },
  { key: "self_motivation", title: "动机与需求", subtitle: "驱动力、最怕什么、奖励与惩罚", icon: "动", folder: "05_动机与需求" },
  { key: "self_values", title: "三观与决策", subtitle: "世界观、人生观、价值排序、红线", icon: "观", folder: "06_三观与决策" },
  { key: "self_relationship", title: "关系与边界", subtitle: "信任、付出、边界、冲突修复", icon: "关", folder: "07_关系与边界" },
  { key: "self_narrative", title: "自我叙事", subtitle: "标签、人生故事、自述与行为对照", icon: "叙", folder: "08_自我叙事" },
  { key: "self_paradox", title: "矛盾与盲区", subtitle: "自述不一致、反复卡住、待验证假设", icon: "矛", folder: "09_矛盾与盲区" },
  { key: "self_timeline", title: "人生时间线", subtitle: "阶段、关键选择、后悔与骄傲", icon: "时", folder: "10_人生时间线" },
  { key: "self_experiments", title: "验证与更新", subtitle: "验证实验、更新记录、定期复盘", icon: "验", folder: "11_验证与更新" },
];

export const ENTRY_TYPE_LABELS = {
  fact: "事实",
  behavior: "行为证据",
  inference: "推断",
  narrative: "自我叙事",
  hypothesis: "待验证假设",
};

export const ENTRY_TYPE_ORDER = ["fact", "behavior", "inference", "narrative", "hypothesis"];

export const CONFIDENCE_LABELS = { high: "高", medium: "中", low: "低" };

export function bookByKey(key) {
  return BOOKS.find((book) => book.key === key) || null;
}

export function bookCardsMarkup(books = []) {
  const byKey = new Map((Array.isArray(books) ? books : []).map((book) => [book.key, book]));
  return `<div class="self-book-grid">${BOOKS.map((book) => {
    const data = byKey.get(book.key) || {};
    const count = Number(data.count) || 0;
    return `<button type="button" class="self-book-card" data-self-book="${escape(book.key)}">
      <span class="self-book-icon" aria-hidden="true">${escape(book.icon)}</span>
      <span class="self-book-body">
        <strong>${escape(book.title)}</strong>
        <em>${escape(book.subtitle)}</em>
      </span>
      <span class="self-book-meta">
        <span class="self-book-count${count ? "" : " is-empty"}">${count}</span>
        <span class="self-book-arrow" aria-hidden="true">›</span>
      </span>
    </button>`;
  }).join("")}</div>`;
}

export function portraitMarkup(portrait = {}, renderHtml = null) {
  const body = String(portrait?.text || portrait?.body || "").trim();
  const updated = String(portrait?.updated_at || "").slice(0, 16).replace("T", " ");
  const sources = Array.isArray(portrait?.sources) ? portrait.sources : [];
  const html = body
    ? (typeof renderHtml === "function" ? renderHtml(body) : portraitHtml(body))
    : "";
  return `<section class="self-portrait" aria-label="现状画像">
    <header class="self-section-head">
      <div><p class="eyebrow">PORTRAIT · 现状画像</p><h3>缩略版「我是谁」</h3></div>
      <span class="meta">${updated ? `最近更新 ${escape(updated)}` : "还没有画像"}</span>
    </header>
    ${body
      ? `<div class="self-portrait-body md-body">${html}</div>`
      : `<p class="field-help">还没有画像。点「更新自我档案」，让导师读一遍最近的记录。</p>`}
    ${sources.length ? `<p class="field-help">来源：${sources.map(escape).join("、")}</p>` : ""}
    <div class="self-portrait-actions">
      <button type="button" class="button secondary" data-self-update>更新自我档案</button>
      <button type="button" class="text-button" data-self-open-portrait>在 Obsidian 打开画像</button>
    </div>
  </section>`;
}

// 画像正文是 Markdown；前端在 self-archive.js 里用统一渲染器替换这个占位。
function portraitHtml(text) {
  return `<p>${escape(text).replace(/\n/g, "<br>")}</p>`;
}

export function entryBadges(entry = {}) {
  const type = ENTRY_TYPE_LABELS[entry.entry_type] || entry.entry_type || "观察";
  const confidence = CONFIDENCE_LABELS[entry.confidence] || entry.confidence || "中";
  return `<span class="self-entry-badge" data-type="${escape(entry.entry_type || "")}">${escape(type)}</span>
    <span class="self-entry-confidence" data-confidence="${escape(entry.confidence || "")}">置信 ${escape(confidence)}</span>`;
}

export function entryCardMarkup(entry = {}) {
  const evidence = String(entry.evidence || "").trim();
  const counterexample = String(entry.counterexample || "").trim();
  const verify = String(entry.verify || "").trim();
  const sources = Array.isArray(entry.sources) ? entry.sources : [];
  return `<article class="self-entry" data-self-entry="${escape(entry.entry_id || "")}">
    <header class="self-entry-head">
      <div>
        <div class="self-entry-badges">${entryBadges(entry)}</div>
        <h4>${escape(entry.title || "未命名条目")}</h4>
      </div>
      <button type="button" class="text-button" data-self-debate="${escape(entry.entry_id || "")}" data-self-debate-title="${escape(entry.title || "")}">和导师辩论这条</button>
    </header>
    <p class="self-entry-text">${escape(String(entry.text || "").trim())}</p>
    ${evidence ? `<p class="self-entry-detail"><strong>证据：</strong>${escape(evidence)}</p>` : ""}
    ${counterexample ? `<p class="self-entry-detail"><strong>反例：</strong>${escape(counterexample)}</p>` : ""}
    ${verify ? `<p class="self-entry-detail"><strong>待验证：</strong>${escape(verify)}</p>` : ""}
    ${sources.length ? `<p class="self-entry-sources">来源：${sources.map(escape).join("、")}</p>` : ""}
  </article>`;
}

export function entriesMarkup(entries = [], { history = false } = {}) {
  const rows = Array.isArray(entries) ? entries : [];
  if (!rows.length) {
    return `<div class="self-empty"><p>这个本子还没有条目。</p>
      <p class="field-help">${history ? "没有历史版本。" : "点「更新自我档案」让导师整理，或在人生导师里直接聊。"}</p></div>`;
  }
  const groups = new Map();
  for (const entry of rows) {
    const key = entry.entry_type || "inference";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(entry);
  }
  const order = [...ENTRY_TYPE_ORDER.filter((key) => groups.has(key)),
    ...[...groups.keys()].filter((key) => !ENTRY_TYPE_ORDER.includes(key))];
  return order.map((key) => `<section class="self-entry-group" data-entry-type="${escape(key)}">
    <header class="self-entry-group-head">
      <p class="eyebrow">${escape(ENTRY_TYPE_LABELS[key] || key)}</p>
      <span class="meta">${groups.get(key).length} 条</span>
    </header>
    ${groups.get(key).map(entryCardMarkup).join("")}
  </section>`).join("");
}

export function bookPageMarkup({ book, entries = [], history = false } = {}) {
  const meta = book || {};
  const title = meta.title || bookByKey(meta.key)?.title || "档案本子";
  const subtitle = meta.subtitle || bookByKey(meta.key)?.subtitle || "";
  const active = entries.filter((entry) => entry.status === "active").length;
  return `<div class="self-book-page">
    <header class="self-section-head self-book-head">
      <div>
        <p class="eyebrow">SELF ARCHIVE · ${escape(title)}</p>
        <h3>${escape(title)}</h3>
        <p class="field-help">${escape(subtitle)}</p>
      </div>
      <div class="self-book-head-actions">
        <span class="meta">${active} 条现行${history ? ` · ${entries.length} 条历史` : ""}</span>
        <button type="button" class="text-button" data-self-history-toggle="${history ? "0" : "1"}">${history ? "看当前" : "看历史"}</button>
        <button type="button" class="text-button" data-self-open-folder="${escape(meta.path || "")}">在 Obsidian 打开文件夹</button>
      </div>
    </header>
    ${entriesMarkup(entries, { history })}
  </div>`;
}

export function migrationMarkup(migration = {}) {
  if (!migration?.needed) return "";
  const files = Number(migration.files) || 0;
  return `<section class="self-migration" aria-label="档案迁移">
    <div>
      <p class="eyebrow">MIGRATION</p>
      <h3>检测到旧版自我档案结构</h3>
      <p class="field-help">${files} 个旧文件可以迁移到新本子；迁移前会预览，执行前会整棵备份。旧忆灵观察会作为历史保留。</p>
    </div>
    <div class="self-migration-actions">
      <button type="button" class="button primary" data-self-migration-preview>预览迁移</button>
    </div>
  </section>`;
}

export function overviewMarkup(overview = {}, renderHtml = null) {
  return `${migrationMarkup(overview.migration)}
    <section class="self-overview-books" aria-label="档案本子">
      <header class="self-section-head">
        <div><p class="eyebrow">BOOKS · 我的档案</p><h3>十一个本子</h3></div>
        <span class="meta">${Number(overview.count) || 0} 条现行记录</span>
      </header>
      ${bookCardsMarkup(overview.books)}
    </section>
    ${portraitMarkup(overview.portrait, renderHtml)}`;
}

export function migrationPreviewMarkup(preview = {}) {
  const plan = Array.isArray(preview.plan) ? preview.plan : [];
  const conflicts = Array.isArray(preview.conflicts) ? preview.conflicts : [];
  return `<div class="self-migration-preview">
    <p>将迁移 <strong>${plan.length}</strong> 个旧文件；备份到 <code>${escape(preview.backup_root || "")}</code>。</p>
    <div class="self-migration-list">${plan.map((row) => `<div class="self-migration-row">
      <span>${escape(row.source)}</span><span aria-hidden="true">→</span><span>${escape(row.target || "保留原处")}</span>
    </div>`).join("")}</div>
    ${conflicts.length ? `<p class="field-help">${conflicts.length} 个目标已存在，将自动编号，不覆盖。</p>` : ""}
  </div>`;
}

export function pendingCardMarkup(card = {}) {
  const sources = Array.isArray(card.sources) ? card.sources : [];
  return `<article class="self-pending-card" data-pending-id="${escape(card.pending_id || "")}">
    <header>
      <div><span class="eyebrow">${escape(card.section_title || "档案修改")}</span>
        <h4>${escape(card.title || "档案修改")}</h4></div>
      <span class="self-entry-confidence" data-confidence="${escape(card.confidence || "")}">置信 ${escape(CONFIDENCE_LABELS[card.confidence] || card.confidence || "中")}</span>
    </header>
    ${card.old_text ? `<p class="self-pending-old"><strong>旧：</strong>${escape(card.old_text)}</p>` : ""}
    <p class="self-pending-new"><strong>新：</strong>${escape(card.new_text || "")}</p>
    ${card.evidence ? `<p class="self-entry-detail"><strong>证据：</strong>${escape(card.evidence)}</p>` : ""}
    ${card.reason ? `<p class="field-help">导师理由：${escape(card.reason)}</p>` : ""}
    ${sources.length ? `<p class="self-entry-sources">来源：${sources.map(escape).join("、")}</p>` : ""}
    <div class="self-pending-actions">
      <button type="button" class="button primary" data-pending-accept="${escape(card.pending_id || "")}">采纳</button>
      <button type="button" class="button secondary" data-pending-edit="${escape(card.pending_id || "")}">改后采纳</button>
      <button type="button" class="text-button" data-pending-reject="${escape(card.pending_id || "")}">驳回</button>
    </div>
  </article>`;
}
