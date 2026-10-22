// 今日黄历（挂件）：按日期确定性生成——同一天刷新不变，每天自动换一副。
// 内容池 = 传统黄历腔 + 个人生活梗（含对洛谷运势挂件的致意条目）。
// 这个模块保持“无 DOM 依赖”的纯函数风格，方便在 node 测试里直接跑。

const AUSPICIOUS_POOL = [
  ["祭祀", "获得祖宗的庇护"],
  ["写日记", "把今天留下来"],
  ["背题", "必知必会，一背一个准"],
  ["早起", "一日之计在于晨"],
  ["读书", "翻两页也算数"],
  ["深度专注", "关掉手机那种"],
  ["微运动", "动一分钟也算赢"],
  ["复盘", "看一眼这周走到哪了"],
  ["整理桌面", "财神偏爱干净的地方"],
  ["给家人打电话", "有回响的那种"],
  ["早睡", "今晚的觉是明天的底气"],
  ["记账", "小钱也是钱"],
  ["散步", "灵感都在路上"],
  ["感恩三件小事", "写下来才算数"],
  ["水讨论区", "看到有趣的事情"],
  ["学点新东西", "十五分钟就够"],
  ["喝够水", "一点五升起步"],
  ["抱抱爱人", "会被记住一整天"],
  ["列个清单", "想清楚再动手"],
  ["承认累了", "休息不是偷懒"],
  ["提前十分钟", "从容是种底气"],
  ["吃顿好饭", "认真对待自己的胃"],
  ["晒晒太阳", "五分钟也管用"],
  ["练一道题", "手感是练出来的"],
  ["夸夸自己", "今天也辛苦啦"],
  ["整理相册", "回忆值得被安放"],
];

const INAUSPICIOUS_POOL = [
  ["熬夜", "明天的你会骂今天的你"],
  ["刷短视频", "一刷就是三小时"],
  ["玩我的世界", "转角遇到苦力怕"],
  ["看视频网站", "会被教练看见"],
  ["久坐不动", "起来走两步"],
  ["拖延", "“等一下”是个无底洞"],
  ["不吃早饭", "胃会记仇"],
  ["空腹喝咖啡", "手会抖"],
  ["冲动消费", "购物车先放一夜"],
  ["深夜做决定", "明早你会后悔"],
  ["翻旧账", "过去的就让它过去"],
  ["熬夜追剧", "“再看一集”是陷阱"],
  ["跟键盘侠对线", "你的时间更值钱"],
  ["暴饮暴食", "七分饱刚刚好"],
  ["拖到最后一刻", "心跳加速不值得"],
  ["赖床超十分钟", "越赖越困"],
  ["一次做十件事", "一件一件来"],
  ["生闷气", "便宜了让你生气的人"],
  ["边吃饭边看手机", "饭都没吃出味道"],
  ["跟爱人抬杠", "赢了也是输了"],
  ["睡前刷手机", "蓝光偷走睡眠"],
  ["硬撑", "状态不好就歇会儿"],
  ["忘记存盘", "血泪教训"],
  ["空腹逛超市", "会买一车没用的"],
  ["摸鱼到下班", "任务不会自己消失"],
];

// 权重表：同一天永远抽到同一个结果；多放“中吉/小吉/吉”更符合挂件的口味。
const FORTUNES = [
  ["大吉", 3], ["中吉", 6], ["小吉", 8], ["吉", 8], ["末吉", 5], ["平", 4],
];

function hashDay(day) {
  let hash = 2166136261 >>> 0;
  for (const char of String(day)) {
    hash ^= char.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function mulberry32(seed) {
  return function next() {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pickWeighted(table, random) {
  const total = table.reduce((sum, [, weight]) => sum + weight, 0);
  let roll = random() * total;
  for (const [label, weight] of table) {
    roll -= weight;
    if (roll < 0) return label;
  }
  return table[table.length - 1][0];
}

function pickDistinct(pool, count, random) {
  const copy = pool.slice();
  for (let index = copy.length - 1; index > 0; index -= 1) {
    const swap = Math.floor(random() * (index + 1));
    [copy[index], copy[swap]] = [copy[swap], copy[index]];
  }
  return copy.slice(0, count);
}

export function buildAlmanac(day) {
  const random = mulberry32(hashDay(day));
  return {
    fortune: pickWeighted(FORTUNES, random),
    auspicious: pickDistinct(AUSPICIOUS_POOL, 2, random),
    inauspicious: pickDistinct(INAUSPICIOUS_POOL, 2, random),
  };
}

function esc(value) {
  return String(value).replace(/[&<>"']/g, (char) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]
  ));
}

export function almanacHtml(day, name, progress) {
  const data = buildAlmanac(day);
  const who = String(name || "").trim() || "你";
  const column = (items, label) => items.map(([title, note]) => (
    `<p class="almanac-item"><strong>${label}：</strong>${esc(title)}</p>` +
    `<span class="almanac-note">${esc(note)}</span>`
  )).join("");
  const meta = progress && progress.total
    ? `<p class="almanac-meta">今日闭环 ${Number(progress.done) || 0} / ${Number(progress.total) || 0} 项（含日记）</p>`
    : "";
  return [
    '<div class="almanac-card">',
    `<p class="almanac-title">${esc(who)} 的运势</p>`,
    `<p class="almanac-fortune">§ ${esc(data.fortune)} §</p>`,
    '<div class="almanac-lists">',
    `<div class="almanac-col almanac-good">${column(data.auspicious, "宜")}</div>`,
    `<div class="almanac-col almanac-bad">${column(data.inauspicious, "忌")}</div>`,
    "</div>",
    meta,
    "</div>",
  ].join("");
}

export function renderAlmanac(element, day, name, progress) {
  if (!element) return;
  element.innerHTML = almanacHtml(day, name, progress);
}

export { AUSPICIOUS_POOL, INAUSPICIOUS_POOL, FORTUNES };
