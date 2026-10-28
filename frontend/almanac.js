// 今日黄历数据层（2026-10-27 重做）：真实黄历来自 vendored lunar-javascript，
// 个人运势沿用原来的确定性内容池（同一天刷新不变、每天自动换一副）。
// 这个模块保持“无 DOM 依赖”的纯函数风格，方便在 node 测试里直接跑。
import { Solar } from "./vendor/lunar.js";

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

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];
export const ALMANAC_MIN_YEAR = 1900;
export const ALMANAC_MAX_YEAR = 2100;

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

function listOrNone(items) {
  return Array.isArray(items) && items.length ? items : ["无"];
}

function solarOf(day) {
  const [year, month, date] = String(day).split("-").map(Number);
  return Solar.fromYmd(year, month, date);
}

/** 真实黄历：农历、干支、纳音、冲煞、彭祖、喜福财方位、宜忌、吉神凶神。 */
export function lunarAlmanac(day) {
  const solar = solarOf(day);
  const lunar = solar.getLunar();
  const chong = String(lunar.getDayChongDesc() || "");
  const sha = String(lunar.getDaySha() || "");
  const weekday = solar.getWeekInChinese();
  return {
    day: solar.toYmd(),
    solar: {
      year: solar.getYear(),
      month: solar.getMonth(),
      day: solar.getDay(),
      weekday,
      weekdayLabel: `星期${weekday}`,
      monthDayLabel: `${solar.getMonth()}月${solar.getDay()}日`,
    },
    lunar: {
      yearGanZhi: lunar.getYearInGanZhi(),
      zodiac: lunar.getYearShengXiao(),
      monthGanZhi: lunar.getMonthInGanZhi(),
      dayGanZhi: lunar.getDayInGanZhi(),
      yearLabel: `${lunar.getYearInGanZhi()}${lunar.getYearShengXiao()}年`,
      monthLabel: `${lunar.getMonthInGanZhi()}月`,
      dayLabel: `${lunar.getDayInGanZhi()}日`,
      dateLabel: `农历${lunar.getMonthInChinese()}月${lunar.getDayInChinese()}`,
      monthChinese: lunar.getMonthInChinese(),
      dayChinese: lunar.getDayInChinese(),
    },
    wuxing: lunar.getDayNaYin(),
    chong: `冲${chong}`,
    sha: sha ? `煞${sha}` : "",
    chongSha: `冲${chong}${sha ? ` 煞${sha}` : ""}`,
    pengzu: [lunar.getPengZuGan(), lunar.getPengZuZhi()].filter(Boolean),
    gods: [
      { label: "喜神", direction: lunar.getDayPositionXiDesc() },
      { label: "福神", direction: lunar.getDayPositionFuDesc() },
      { label: "财神", direction: lunar.getDayPositionCaiDesc() },
    ],
    yi: listOrNone(lunar.getDayYi()),
    ji: listOrNone(lunar.getDayJi()),
    jiShen: listOrNone(lunar.getDayJiShen()),
    xiongSha: listOrNone(lunar.getDayXiongSha()),
  };
}

/** 真实黄历 + 原来的个人运势/生活宜忌。 */
export function almanacForDay(day) {
  return { ...lunarAlmanac(day), personal: buildAlmanac(day) };
}

/**
 * 月历数据（周一起始）。today 传入才标记“今天”，保持纯函数。
 * cells 里 null 是月初/月末的占位；其余含公历日、农历日、节气。
 */
export function monthGridData(year, month, today = "") {
  const first = new Date(Date.UTC(year, month - 1, 1));
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const offset = (first.getUTCDay() + 6) % 7;
  const cells = Array.from({ length: offset }, () => null);
  for (let date = 1; date <= daysInMonth; date += 1) {
    const day = `${year}-${String(month).padStart(2, "0")}-${String(date).padStart(2, "0")}`;
    const solar = Solar.fromYmd(year, month, date);
    const lunar = solar.getLunar();
    const lunarDay = lunar.getDayInChinese();
    const jieQi = lunar.getJieQi() || "";
    cells.push({
      day,
      solarDay: date,
      lunarLabel: lunarDay === "初一" ? `${lunar.getMonthInChinese()}月` : lunarDay,
      jieQi,
      isToday: day === today,
    });
  }
  while (cells.length % 7 !== 0) cells.push(null);
  return { year, month, offset, cells };
}

function infoRow(label, value) {
  return `<div class="almanac-info-row"><span class="almanac-info-key">${esc(label)}</span><span class="almanac-info-value">${esc(value)}</span></div>`;
}

function termList(items, label) {
  return `<span class="almanac-term-label">${esc(label)}</span><span class="almanac-terms">${items.map((item) => `<span class="almanac-term">${esc(item)}</span>`).join("")}</span>`;
}

function personalHtml(personal, name, progress, detailed) {
  const who = String(name || "").trim() || "你";
  const lines = (items) => items.map(([title]) => esc(title)).join(" · ");
  const notes = detailed
    ? `<ul class="almanac-personal-notes">
        ${personal.auspicious.map(([title, note]) => `<li><strong>宜 · ${esc(title)}</strong><span>${esc(note)}</span></li>`).join("")}
        ${personal.inauspicious.map(([title, note]) => `<li><strong>忌 · ${esc(title)}</strong><span>${esc(note)}</span></li>`).join("")}
      </ul>`
    : "";
  const progressLine = progress && Number(progress.total) > 0
    ? `<p class="almanac-meta">今日闭环 ${Number(progress.done) || 0} / ${Number(progress.total) || 0} 项（含日记）</p>`
    : "";
  return `<div class="almanac-personal">
    <div class="almanac-personal-head">
      <span class="almanac-personal-title">${esc(who)} · 个人运势</span>
      <span class="almanac-personal-level">§ ${esc(personal.fortune)} §</span>
    </div>
    <p class="almanac-personal-line"><b class="almanac-personal-good">宜</b> ${lines(personal.auspicious)}</p>
    <p class="almanac-personal-line"><b class="almanac-personal-bad">忌</b> ${lines(personal.inauspicious)}</p>
    ${notes}
    ${progressLine}
  </div>`;
}

/** 图片版式的黄历主体（组件和浮窗共用）。 */
export function almanacSheetHtml(data, { name = "", progress = null, detailed = false } = {}) {
  const info = [
    infoRow("五行", data.wuxing),
    infoRow("冲煞", data.chongSha),
    `<div class="almanac-info-row"><span class="almanac-info-key">彭祖</span><span class="almanac-info-value">${data.pengzu.map((line) => `<span>${esc(line)}</span>`).join("")}</span></div>`,
  ].join("");
  const gods = data.gods.map((god) => (
    `<div class="almanac-god"><span class="almanac-god-label">${esc(god.label)}</span><strong>${esc(god.direction)}</strong></div>`
  )).join("");
  const yiji = ["宜", "忌"].map((label) => {
    const good = label === "宜";
    return `<div class="almanac-yiji-row">
      <span class="almanac-seal ${good ? "is-good" : "is-bad"}" aria-hidden="true">${label}</span>
      <div class="almanac-yiji-terms">${(good ? data.yi : data.ji).map((item) => `<span class="almanac-term">${esc(item)}</span>`).join("")}</div>
    </div>`;
  }).join("");
  return `<div class="almanac-sheet">
    <div class="almanac-top">
      <div class="almanac-datecol">
        <p class="almanac-lunar-name">${esc(data.lunar.dateLabel)}</p>
        <div class="almanac-gz">
          <span>${esc(data.lunar.yearLabel)}</span>
          <span>${esc(data.lunar.monthLabel)}</span>
          <span>${esc(data.lunar.dayLabel)}</span>
          <span>${esc(data.solar.weekdayLabel)}</span>
        </div>
      </div>
      <div class="almanac-info">${info}</div>
    </div>
    <div class="almanac-gods">${gods}</div>
    <div class="almanac-yiji">${yiji}</div>
    <div class="almanac-spirits">
      <p>${termList(data.jiShen, "吉神")}</p>
      <p>${termList(data.xiongSha, "凶神")}</p>
    </div>
    ${personalHtml(data.personal, name, progress, detailed)}
  </div>`;
}

function compactGzLine(data) {
  return [
    `${esc(data.lunar.yearGanZhi)}年`,
    esc(data.lunar.zodiac),
    `${esc(data.lunar.monthGanZhi)}月`,
    `${esc(data.lunar.dayGanZhi)}日`,
  ].join(" ");
}

function compactLunarLabel(data) {
  return `${data.lunar.monthChinese}月${data.lunar.dayChinese}`;
}

/** 简要运势：取个人宜/忌各第一条，完整列表留在详情浮窗。 */
function compactPersonalLine(personal) {
  const good = personal?.auspicious?.[0]?.[0] || "";
  const bad = personal?.inauspicious?.[0]?.[0] || "";
  return [
    good ? `宜 ${esc(good)}` : "",
    bad ? `忌 ${esc(bad)}` : "",
  ].filter(Boolean).join(" · ");
}

function compactTop(data, hint = "") {
  return `<div class="almanac-compact-top">
    <svg class="almanac-lantern" viewBox="0 0 20 28" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" aria-hidden="true" focusable="false">
      <path d="M10 1.5v3M7.5 5h5" />
      <path d="M6 7c0-2 1.8-3 4-3s4 1 4 3v10c0 2-1.8 3-4 3s-4-1-4-3z" />
      <path d="M6.5 11h7M6.5 15h7" />
      <path d="M10 20v4M8.5 25h3" />
    </svg>
    <span class="almanac-gzline">${compactGzLine(data)}</span>
    ${hint}
  </div>`;
}

function lockedHtml(data) {
  return `<div class="almanac-card almanac-locked almanac-compact">
    ${compactTop(data)}
    <p class="almanac-compact-lunar">${esc(compactLunarLabel(data))}</p>
    <p class="almanac-locked-hint">今日运势已封存 · 签到后查看</p>
    <button type="button" class="button primary almanac-checkin" data-almanac-checkin>签到查看今日运势</button>
  </div>`;
}

/** 右栏小组件（2026-10-27 小尺寸重做）：与天气/倒计时同高，只保留干支、农历日期与运势摘要；
 *  未签到只给签到入口；完整黄历、宜忌、吉神凶神、个人运势说明和月历都在点击后的详情浮窗里。 */
export function almanacCardHtml(data, { checkedIn = false } = {}) {
  if (!checkedIn) return lockedHtml(data);
  const yiji = compactPersonalLine(data.personal);
  const lunarLabel = compactLunarLabel(data);
  const aria = `今日黄历 ${lunarLabel}，今日运势 ${data.personal.fortune}，已签到，点击查看详情`;
  return `<div class="almanac-card almanac-unlocked almanac-compact" data-almanac-unlocked="1" tabindex="0" role="button" aria-label="${esc(aria)}">
    ${compactTop(data, '<span class="almanac-open-hint" aria-hidden="true">›</span>')}
    <p class="almanac-compact-lunar">${esc(lunarLabel)}</p>
    <div class="almanac-compact-fortune">
      <span class="almanac-fortune-label">今日运势</span>
      <span class="almanac-fortune">${esc(data.personal.fortune)}</span>
      <span class="almanac-checked-badge">✓ 已签到</span>
    </div>
    ${yiji ? `<p class="almanac-compact-yiji">${yiji}</p>` : ""}
  </div>`;
}

function yearOptions(selected) {
  const years = [];
  for (let year = ALMANAC_MIN_YEAR; year <= ALMANAC_MAX_YEAR; year += 1) {
    years.push(`<option value="${year}"${year === selected ? " selected" : ""}>${year}年</option>`);
  }
  return years.join("");
}

function monthOptions(selected) {
  return Array.from({ length: 12 }, (_, index) => {
    const month = index + 1;
    return `<option value="${month}"${month === selected ? " selected" : ""}>${month}月</option>`;
  }).join("");
}

function monthCellsHtml(monthData, selectedDay) {
  return monthData.cells.map((cell) => {
    if (!cell) return '<span class="almanac-day-cell is-empty" aria-hidden="true"></span>';
    const classes = ["almanac-day-cell"];
    if (cell.day === selectedDay) classes.push("is-selected");
    if (cell.isToday) classes.push("is-today");
    if (cell.jieQi) classes.push("has-jieqi");
    const hint = cell.jieQi ? `${cell.jieQi} · 农历${cell.lunarLabel}` : `农历${cell.lunarLabel}`;
    return `<button type="button" class="${classes.join(" ")}" data-almanac-day="${esc(cell.day)}" aria-label="${esc(`${cell.solarDay}日 ${hint}`)}" aria-pressed="${cell.day === selectedDay}">
      <span class="almanac-day-num">${cell.solarDay}</span>
      <span class="almanac-day-lunar">${esc(cell.jieQi || cell.lunarLabel)}</span>
    </button>`;
  }).join("");
}

/** 详细浮窗：年/月/日切换 + 月历 + 选中日黄历。 */
export function almanacDialogHtml(data, {
  name = "", progress = null, viewYear, viewMonth, today = "",
} = {}) {
  const year = Number(viewYear) || data.solar.year;
  const month = Number(viewMonth) || data.solar.month;
  const monthData = monthGridData(year, month, today);
  const isToday = data.day === today;
  return `<div class="almanac-dialog">
    <div class="almanac-dialog-toolbar">
      <div class="almanac-dialog-nav">
        <button type="button" class="small-button" data-almanac-nav="prev-month" aria-label="上个月">‹</button>
        <select data-almanac-year aria-label="年份">${yearOptions(year)}</select>
        <select data-almanac-month aria-label="月份">${monthOptions(month)}</select>
        <button type="button" class="small-button" data-almanac-nav="next-month" aria-label="下个月">›</button>
        <button type="button" class="small-button" data-almanac-nav="today">今天</button>
      </div>
      <div class="almanac-dialog-nav">
        <button type="button" class="small-button" data-almanac-nav="prev-day" aria-label="前一天">前一天</button>
        <button type="button" class="small-button" data-almanac-nav="next-day" aria-label="后一天">后一天</button>
      </div>
    </div>
    <div class="almanac-dialog-body">
      <section class="almanac-calendar" aria-label="选择日期">
        <p class="almanac-calendar-title">${year}年${month}月</p>
        <div class="almanac-weekdays" aria-hidden="true">${WEEKDAYS.map((label) => `<span>${label}</span>`).join("")}</div>
        <div class="almanac-month-grid">${monthCellsHtml(monthData, data.day)}</div>
        <p class="almanac-calendar-hint">点日期查看当天黄历；吉神凶神、真实宜忌都在详情里。</p>
      </section>
      <section class="almanac-detail" aria-label="当日黄历">
        <header class="almanac-detail-head">
          <div>
            <h3>${esc(data.solar.year)}年${esc(data.solar.monthDayLabel)} · ${esc(data.solar.weekdayLabel)}</h3>
            <p>${esc(data.lunar.dateLabel)} · ${esc(data.lunar.yearLabel)} · ${esc(data.lunar.monthLabel)} · ${esc(data.lunar.dayLabel)}</p>
          </div>
          ${isToday ? '<span class="almanac-today-badge">今天</span>' : ""}
        </header>
        ${almanacSheetHtml(data, { name, progress, detailed: true })}
      </section>
    </div>
  </div>`;
}

export { AUSPICIOUS_POOL, INAUSPICIOUS_POOL, FORTUNES };
