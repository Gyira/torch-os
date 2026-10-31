// 工作台横幅（照 Apex Dashboard 的 banner，2026-10-05）：引言/统计双模式、多图轮换、
// 焦点构图、遮罩深浅。数据全在 01-Identity/身份（identity）.md（mottos/heroes/hero_overlay/
// hero_focal/banner_mode）——编辑走 banner-editor.js。
// 纯函数 + 一个应用函数，无 core.js 依赖，node 直测友好。

function esc(value) {
  return String(value).replace(/[&<>"']/g, (char) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]
  ));
}

// 字体栈只允许字体名用得上的字符（与后端 identity.py 的 FONT_PATTERN 同一套规则）。
const FONT_PATTERN = /^[A-Za-z0-9\s,'"()\-_.㐀-鿿]{1,120}$/;

/** 归一化字体栈：不合法或超长就当没填（宁可用默认字体，也不让别的字符进样式）。 */
export function safeFont(value) {
  const text = String(value ?? "").trim();
  return FONT_PATTERN.test(text) ? text : "";
}

/** 横幅字体候选（照 Apex Dashboard 的 quoteFont 列表）：value 是完整的 CSS 字体栈，
 *  一次选择在 Windows / macOS 上都落得到字体；label 是字体名，不翻译。 */
export const BANNER_FONT_GROUPS = [
  {
    label: "中文",
    fonts: [
      { label: "楷体", value: '"Kaiti SC","STKaiti","KaiTi","楷体",serif' },
      { label: "宋体", value: '"Songti SC","STSong","SimSun","宋体",serif' },
      { label: "仿宋", value: '"STFangsong","FangSong","仿宋",serif' },
      { label: "隶书", value: '"LiSu","隶书","STLiti","华文隶书",serif' },
      { label: "行楷", value: '"STXingkai","华文行楷","Xingkai SC","行楷",cursive' },
      { label: "圆体", value: '"Yuanti SC","YouYuan","幼圆",sans-serif' },
      { label: "黑体", value: '"PingFang SC","Microsoft YaHei","Hiragino Sans GB",sans-serif' },
      { label: "霞鹜文楷", value: '"LXGW WenKai","LXGW WenKai Lite","霞鹜文楷","Kaiti SC","KaiTi",serif' },
    ],
  },
  {
    label: "西文",
    fonts: [
      { label: "Georgia", value: "Georgia,serif" },
      { label: "Times New Roman", value: '"Times New Roman",Times,serif' },
      { label: "Palatino", value: '"Palatino Linotype",Palatino,"Book Antiqua",serif' },
      { label: "Baskerville", value: 'Baskerville,"Libre Baskerville",Georgia,serif' },
      { label: "Garamond", value: 'Garamond,"EB Garamond",Georgia,serif' },
      { label: "Didot", value: 'Didot,"Bodoni MT",Georgia,serif' },
      { label: "Helvetica", value: '"Helvetica Neue",Helvetica,Arial,sans-serif' },
      { label: "Courier", value: '"Courier New",Courier,monospace' },
    ],
  },
];

/** 字体栈里第一个能读的字体名——用来给手写/历史值一个下拉项标签。 */
export function firstFontName(stack) {
  const quoted = /^"([^"]+)"/.exec(String(stack).trim());
  if (quoted?.[1]) return quoted[1];
  const bare = /^([^,]+)/.exec(String(stack).trim());
  return (bare?.[1] ?? stack).trim();
}

/** 把 identity payload 归一成横幅状态（缺省、钳位都在这里）。 */
export function normalizeBanner(payload) {
  const heroes = Array.isArray(payload?.heroes)
    ? payload.heroes.map((item) => String(item || "").trim()).filter(Boolean) : [];
  const mottos = Array.isArray(payload?.mottos)
    ? payload.mottos.map((item) => String(item || "").trim()).filter(Boolean) : [];
  const overlay = Number(payload?.hero_overlay);
  const accent = String(payload?.banner_accent || "").trim();
  const blur = Number(payload?.banner_blur);
  const darkness = Number(payload?.banner_darkness);
  const leftStat = String(payload?.banner_left_stat || "notes");
  const centerStat = String(payload?.banner_center_stat || "streak");
  return {
    mode: payload?.banner_mode === "stats" ? "stats" : "quote",
    heroes,
    mottos,
    overlay: Number.isFinite(overlay) ? Math.min(0.9, Math.max(0, overlay)) : 0.45,
    focal: String(payload?.hero_focal || "").trim(),
    accent: /^#[0-9a-fA-F]{6}$/.test(accent) ? accent : "",
    blur: Number.isFinite(blur) ? Math.min(20, Math.max(0, blur)) : 6,
    darkness: Number.isFinite(darkness) ? Math.min(60, Math.max(0, darkness)) : 30,
    leftStat: ["notes", "memories", "open", "files"].includes(leftStat) ? leftStat : "notes",
    centerStat: ["streak", "percent"].includes(centerStat) ? centerStat : "streak",
    details: payload?.banner_details !== false,
    font: safeFont(payload?.banner_font),
  };
}

/** 暗度 → (亮度, 暗幕) 的换算，照 Apex 原公式。 */
export function backdropVars(blur, darkness) {
  const safeDarkness = Number.isFinite(darkness) ? darkness : 30;
  const safeBlur = Number.isFinite(blur) ? blur : 6;
  const round3 = (value) => Math.round(value * 1000) / 1000;
  return {
    blur: `${Math.min(20, Math.max(0, safeBlur))}px`,
    bright: String(round3(Math.max(0.3, 1 - (safeDarkness / 100) * 0.7))),
    scrim: String(round3(0.25 + (safeDarkness / 100) * 0.5)),
  };
}

/** 图片地址：http(s) 直接用链接；库内路径走后端沙箱端点（按索引取）。 */
export function imageSrc(entry, index) {
  const value = String(entry || "").trim();
  if (!value) return "";
  if (/^https?:\/\//i.test(value)) return value;
  return `/api/identity/hero?i=${Number(index) || 0}`;
}

function cssUrl(entry, index) {
  return `url("${imageSrc(entry, index).replace(/"/g, "%22")}")`;
}

/** 按图片索引轮换横幅背景（配合 startRotation 使用）。 */
export function setBannerImage(element, banner, index) {
  if (!element || !banner || !banner.heroes.length) return;
  const safeIndex = ((Number(index) || 0) % banner.heroes.length + banner.heroes.length) % banner.heroes.length;
  element.style.backgroundImage = cssUrl(banner.heroes[safeIndex], safeIndex);
  element.style.backgroundPosition = banner.focal || "center";
  element.dataset.heroIndex = String(safeIndex);
}

export function quotesHtml(mottos) {
  return mottos.map((line, index) =>
    `<p class="workbench-motto-line${index === 0 ? " primary" : ""}">${esc(line)}</p>`).join("");
}

/** 编辑器里的座右铭文本：空行分隔多条，同一条内部的换行原样保留。 */
export function parseMottos(value) {
  return String(value ?? "")
    .split(/\n\s*\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/** 反向：把 mottos 数组放回编辑器，用空行分隔，避免把单条内部的换行拆成两条。 */
export function mottoText(mottos) {
  return (Array.isArray(mottos) ? mottos : [])
    .map((item) => String(item ?? "").trim())
    .filter(Boolean)
    .join("\n\n");
}

const HEAT_ROWS = Object.freeze([
  ["life", "人生路"],
  ["knowledge", "知识库"],
  ["self", "自我档案"],
  ["todo_created", "任务创建"],
  ["todo_done", "任务完成"],
  ["habits", "习惯"],
  ["total", "总热度"],
]);

function heatCellHtml(cell) {
  const level = Math.max(0, Math.min(4, Number(cell?.level) || 0));
  const classes = [
    "banner-heat-cell",
    level ? `level-${level}` : "is-empty",
    cell?.today ? "today" : "",
    cell?.future ? "is-future" : "",
  ].filter(Boolean).join(" ");
  const title = esc(cell?.title || cell?.date || "");
  return `<span class="${classes}" title="${title}"></span>`;
}

function heatFallbackRows(days = 28) {
  const cells = Array.from({ length: days }, () =>
    '<span class="banner-heat-cell is-empty"></span>');
  return HEAT_ROWS.map(([key, label]) =>
    `<div class="banner-heatmap-row${key === "total" ? " is-total" : ""}">` +
    `<span class="banner-heatmap-label">${esc(label)}</span>` +
    `<div class="banner-heatmap-cells">${cells.join("")}</div></div>`).join("");
}

function heatLegendHtml() {
  const swatches = [0, 1, 2, 3, 4]
    .map((level) => `<i class="banner-heat-swatch${level ? ` level-${level}` : " is-empty"}"></i>`)
    .join("");
  return `<span class="banner-heatmap-legend" aria-hidden="true"><span>少</span>${swatches}<span>多</span></span>`;
}

/** 按屏宽选择可见天数：格子铺满中间区域，同时保证每个格子不会细到看不清。 */
export function heatDaysForWidth(width) {
  const value = Number(width) || 0;
  if (value >= 1200) return 42;
  if (value >= 900) return 28;
  if (value >= 700) return 24;
  if (value >= 480) return 21;
  return 14;
}

/** 统计模式：左列大数字+底部小条 / 中列铺满的 7 行热力图 / 右列进度条。 */
export function statsHtml(stats, options = {}) {
  const number = (key) => {
    const value = stats ? Number(stats[key]) : NaN;
    return Number.isFinite(value) ? value : null;
  };
  const text = (value) => (value == null ? "—" : String(value));
  const pct = (done, total) => (total ? Math.round((done / total) * 100) : 0);
  const details = options.details !== false;
  const leftStat = ["notes", "memories", "open", "files"].includes(options.leftStat)
    ? options.leftStat : "notes";

  const notes = number("notes");
  const percent = number("today_percent");
  const dailyDone = number("daily_done");
  const dailyTotal = number("daily_total");
  const habitDone = number("habits_done");
  const habitTotal = number("habits_total");
  const openTodos = number("open_todos");
  const todayDocs = number("activity_today");
  const heatScore = Math.max(0, Math.min(100, Math.round((Number(stats?.heat_score) || 0) * 100)));

  const leftMap = {
    notes: [notes, "资料"],
    memories: [number("memories"), "记忆"],
    open: [openTodos, "待办"],
    files: [todayDocs, "今日新增"],
  };
  const [leftValue, leftLabel] = leftMap[leftStat];

  const heatmap = stats?.heatmap && Array.isArray(stats.heatmap.rows)
    ? stats.heatmap : null;
  const availableDays = Math.max(1, Number(heatmap?.days) || 28);
  const requestedDays = Number(options.heatDays) || availableDays;
  const heatDays = Math.max(1, Math.min(availableDays, requestedDays));
  const heatRows = heatmap
    ? heatmap.rows.map((row) => {
      const key = String(row.key || "");
      const label = String(row.label || HEAT_ROWS.find(([name]) => name === key)?.[1] || key);
      const cells = (row.cells || []).slice(-heatDays).map(heatCellHtml).join("");
      return `<div class="banner-heatmap-row${key === "total" ? " is-total" : ""}">` +
        `<span class="banner-heatmap-label">${esc(label)}</span>` +
        `<div class="banner-heatmap-cells">${cells}</div></div>`;
    }).join("")
    : heatFallbackRows(heatDays);
  const heatHead = heatmap
    ? `近 ${heatDays} 天 · 每格一天${heatmap.truncated ? " · 部分数据" : ""}`
    : "热力图读取中";

  const strip = [
    ["📄", `今天新增 ${text(todayDocs ?? 0)} 篇`],
    ["✅", `待办还差 ${text(openTodos ?? 0)} 项`],
    ["🔥", `每日必做 ${text(dailyDone ?? 0)}/${text(dailyTotal ?? 0)}`],
  ];
  const progress = [
    ["今日总热度", `${text(heatScore)}%`, heatScore],
    ["今日完成度", `${text(percent ?? 0)}%`, percent ?? 0],
    ["每日必做", `${text(dailyDone ?? 0)}/${text(dailyTotal ?? 0)}`, pct(dailyDone ?? 0, dailyTotal ?? 0)],
    ["习惯打卡", `${text(habitDone ?? 0)}/${text(habitTotal ?? 0)}`, pct(habitDone ?? 0, habitTotal ?? 0)],
  ];

  return [
    '<div class="banner-stats-col banner-stats-left">',
    `<div class="banner-stats-hero"><span class="banner-stats-num">${text(leftValue)}</span><span class="banner-stats-label">${esc(leftLabel)}</span></div>`,
    details ? `<div class="banner-stats-strip">${strip.map(([icon, line]) =>
      `<span class="banner-stats-strip-item"><i aria-hidden="true">${icon}</i>${esc(line)}</span>`).join("")}</div>` : "",
    "</div>",
    '<div class="banner-stats-col banner-stats-center">',
    details ? `<div class="banner-heatmap" role="img" aria-label="${esc(heatHead)}">` +
      `<div class="banner-heatmap-head"><span>${esc(heatHead)}</span>${heatLegendHtml()}</div>` +
      `${heatRows}</div>` : "",
    "</div>",
    '<div class="banner-stats-col banner-stats-right">',
    progress.map(([label, value, fill]) =>
      `<div class="banner-stats-prog"><div class="banner-stats-prog-head"><span class="banner-stats-prog-title">${esc(label)}</span><span class="banner-stats-prog-val">${esc(value)}</span></div>` +
      (details ? `<div class="banner-stats-prog-track"><div class="banner-stats-prog-fill" style="width:${Math.max(0, Math.min(100, Number(fill) || 0))}%"></div></div>` : "") +
      `</div>`).join(""),
    "</div>",
  ].join("");
}

/** 把横幅状态画进 DOM；返回归一化后的 banner（供轮换与编辑器复用）。 */
export function applyBanner(element, payload, stats) {
  if (!element) return null;
  const banner = normalizeBanner(payload);
  const background = element.querySelector(".banner-bg");
  const overlay = element.querySelector(".banner-overlay");
  const quotes = element.querySelector(".banner-quote");
  const statsBox = element.querySelector(".banner-stats");
  element.classList.toggle("banner-empty", banner.heroes.length === 0);
  if (background) setBannerImage(background, banner, 0);
  if (overlay) overlay.style.opacity = String(banner.overlay);
  if (banner.accent) element.style.setProperty("--banner-accent", banner.accent);
  else element.style.removeProperty("--banner-accent");
  // 字体：座右铭与统计文字共用一条字体栈；没选就交回样式表里的默认字体。
  if (banner.font) element.style.setProperty("--banner-font", banner.font);
  else element.style.removeProperty("--banner-font");
  const backdrop = backdropVars(banner.blur, banner.darkness);
  element.style.setProperty("--banner-blur", backdrop.blur);
  element.style.setProperty("--banner-bright", backdrop.bright);
  element.style.setProperty("--banner-scrim-alpha", backdrop.scrim);
  const showStats = banner.mode === "stats";
  element.classList.toggle("banner-stats-mode", showStats);
  element.dataset.bannerMode = showStats ? "stats" : "quote";
  const modeButton = element.querySelector("#workbench-banner-mode");
  if (modeButton) {
    modeButton.dataset.mode = showStats ? "stats" : "quote";
    modeButton.setAttribute("aria-pressed", String(showStats));
    modeButton.setAttribute("aria-label", showStats ? "切换到图片模式" : "切换到统计模式");
    modeButton.title = showStats ? "切回图片模式" : "切换到统计模式";
    const label = modeButton.querySelector(".banner-tool-text");
    if (label) label.textContent = showStats ? "图片" : "统计";
  }
  if (quotes) {
    quotes.hidden = showStats;
    quotes.innerHTML = quotesHtml(banner.mottos);
  }
  if (statsBox) {
    statsBox.hidden = !showStats;
    if (showStats) {
      const width = typeof window !== "undefined" ? window.innerWidth : 1280;
      statsBox.innerHTML = statsHtml(stats, { ...banner, heatDays: heatDaysForWidth(width) });
    }
  }
  return banner;
}

export const ROTATION_MINUTES = 30;

/** 多图轮换：setTimeout 链式自调度（测试环境没有 setInterval，照时钟同款），可注入。 */
export function startRotation(banner, apply, schedule = (fn, ms) => setTimeout(fn, ms)) {
  if (!banner || banner.heroes.length < 2) return false;
  const interval = ROTATION_MINUTES * 60 * 1000;
  let index = 0;
  const tick = () => {
    index = (index + 1) % banner.heroes.length;
    apply(index);
    schedule(tick, interval);
  };
  schedule(tick, interval);
  return true;
}
