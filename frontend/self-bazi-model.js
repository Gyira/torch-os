// 个人八字系统的纯渲染函数（2026-11-01）。
// 不 import core.js / DOM；node --test 可直接跑。
import { escape } from "./self-archive-model.js";

export const TOPICS = ["综合", "性格与天赋", "事业与学业", "财富", "感情", "健康", "大运流年"];
export const WUXING = ["木", "火", "土", "金", "水"];
export const WUXING_CLASS = { 木: "wood", 火: "fire", 土: "earth", 金: "metal", 水: "water" };

export function profileFormMarkup(profile = {}) {
  return `<form class="bazi-form" data-bazi-form>
    <div class="bazi-form-grid">
      <label class="field">称呼<input name="name" maxlength="60" value="${escape(profile.name || "")}" placeholder="例如：王衡岳"></label>
      <label class="field">性别<select name="sex">
        <option value=""${!profile.sex ? " selected" : ""}>未填</option>
        <option value="male"${profile.sex === "male" ? " selected" : ""}>男</option>
        <option value="female"${profile.sex === "female" ? " selected" : ""}>女</option>
      </select></label>
      <label class="field">公历生日<input name="birth_date" type="date" value="${escape(profile.birth_date || "")}"></label>
      <label class="field">出生时间<input name="birth_time" type="time" value="${escape(profile.birth_time || "")}"></label>
      <label class="field">出生地<input name="birth_place" maxlength="120" value="${escape(profile.birth_place || "")}" placeholder="例如：江西赣州"></label>
    </div>
    <details class="bazi-manual"${profile.pillars_manual ? " open" : ""}>
      <summary>已经排好四柱？直接填写</summary>
      <label class="field">四柱<input name="pillars_manual" maxlength="40" value="${escape(profile.pillars_manual || "")}" placeholder="癸未 辛酉 丙戌 戊子"></label>
      <p class="field-help">填了四柱就优先用它；出生日期/时间可以留空，但大运可能排不出来。</p>
    </details>
    <div class="form-actions">
      <button type="submit" class="button primary" data-bazi-save>保存并排盘</button>
      <button type="button" class="button secondary" data-bazi-preview>试算</button>
      <span class="status-message" data-bazi-form-status role="status"></span>
    </div>
  </form>`;
}

export function pillarsMarkup(chart = {}) {
  const rows = Array.isArray(chart.pillars) ? chart.pillars : [];
  if (!rows.length) return `<p class="field-help">还没有四柱。</p>`;
  const cell = (row, key) => `<td class="${WUXING_CLASS[row?.[`${key === "gan" ? "gan_wx" : "zhi_wx"}`]] || ""}">${escape(row?.[key] || "—")}</td>`;
  return `<div class="bazi-chart">
    <div class="bazi-chart-head">
      <span>${escape(chart.name || "我的命盘")}</span>
      <span class="meta">${escape(chart.day_master || "")}${chart.time_note ? ` · ${escape(chart.time_note)}` : ""}</span>
    </div>
    <table class="bazi-pillars">
      <thead><tr><th></th>${rows.map((row) => `<th>${escape(row.slot || "")}</th>`).join("")}</tr></thead>
      <tbody>
        <tr><th>天干</th>${rows.map((row) => cell(row, "gan")).join("")}</tr>
        <tr><th>地支</th>${rows.map((row) => cell(row, "zhi")).join("")}</tr>
        <tr><th>十神</th>${rows.map((row) => `<td>${escape(row.gan_ss || "")}</td>`).join("")}</tr>
        <tr><th>藏干</th>${rows.map((row) => `<td>${escape(row.hidden || "")}</td>`).join("")}</tr>
        <tr><th>藏干十神</th>${rows.map((row) => `<td>${escape(row.zhi_ss || "")}</td>`).join("")}</tr>
      </tbody>
    </table>
    <div class="bazi-summary">
      <span>日主 <strong>${escape(chart.day_master || "—")}</strong></span>
      <span>喜 ${escape((chart.favor || []).join("、") || "待解读")}</span>
      <span>忌 ${escape((chart.avoid || []).join("、") || "待解读")}</span>
    </div>
  </div>`;
}

export function elementsMarkup(chart = {}) {
  const counts = chart.elements || {};
  const max = Math.max(1, ...WUXING.map((item) => Number(counts[item]) || 0));
  return `<section class="bazi-elements" aria-label="五行强弱">
    <p class="eyebrow">FIVE ELEMENTS</p>
    <div class="bazi-element-bars">${WUXING.map((item) => {
      const value = Number(counts[item]) || 0;
      return `<div class="bazi-element-row" data-wx="${WUXING_CLASS[item]}">
        <span>${item}</span>
        <progress class="bazi-element-bar" max="${max}" value="${value}" aria-label="${item} ${value}"></progress>
        <em>${value}</em>
      </div>`;
    }).join("")}</div>
    <p class="field-help">按天干 + 地支本气粗算，只用于页面展示，不做命理定论。</p>
  </section>`;
}

export function dayunMarkup(chart = {}) {
  const cycles = Array.isArray(chart.dayun_cycles) ? chart.dayun_cycles : [];
  if (!cycles.length) return `<section class="bazi-dayun"><p class="eyebrow">DAYUN</p><p class="field-help">还没有大运；补全出生日期与时间后重试。</p></section>`;
  const currentYear = new Date().getFullYear();
  const currentIndex = cycles.findIndex((cycle) => {
    const match = String(cycle.years || "").match(/(\d{4})\s*[-–—]\s*(\d{4})/);
    if (!match) return false;
    return currentYear >= Number(match[1]) && currentYear <= Number(match[2]);
  });
  return `<section class="bazi-dayun" aria-label="大运">
    <p class="eyebrow">DAYUN · 大运</p>
    <ol class="bazi-dayun-list">${cycles.map((cycle, index) => `<li${index === currentIndex ? ' class="is-current"' : ""}>
      <strong>${escape(cycle.gz || "")}</strong><span>${escape(cycle.years || "")}</span>
    </li>`).join("")}</ol>
    ${chart.dayun ? `<p class="field-help">${escape(chart.dayun)}</p>` : ""}
  </section>`;
}

export function chartBoardMarkup(chart = {}) {
  if (!chart || !Array.isArray(chart.pillars) || !chart.pillars.length) {
    return `<div class="bazi-empty"><p>还不能排盘。</p>
      <p class="field-help">填写出生日期，或直接填写四柱后保存。</p></div>`;
  }
  return `${pillarsMarkup(chart)}
    <div class="bazi-board-lower">${elementsMarkup(chart)}${dayunMarkup(chart)}</div>
    ${chart.dayun ? "" : ""}`;
}

export function topicsMarkup(active = "") {
  return `<div class="bazi-topics" role="group" aria-label="解读主题">${TOPICS.map((topic) => `<button type="button" class="bazi-topic${topic === active ? " is-active" : ""}" data-bazi-topic="${escape(topic)}">${escape(topic)}</button>`).join("")}</div>`;
}

export function readingMarkup(reading = {}, renderHtml = null) {
  const body = String(reading.body || "").trim();
  const html = body
    ? (typeof renderHtml === "function" ? renderHtml(body) : `<p>${escape(body).replace(/\n/g, "<br>")}</p>`)
    : `<p class="field-help">这篇解读没有正文。</p>`;
  const sources = Array.isArray(reading.sources) ? reading.sources : [];
  return `<article class="bazi-reading" data-bazi-reading="${escape(reading.name || "")}">
    <header>
      <div><p class="eyebrow">${escape(reading.topic || "综合")} · ${escape(reading.date || "")}</p>
        <h3>${escape(reading.title || "八字解读")}</h3></div>
      <button type="button" class="text-button" data-bazi-delete="${escape(reading.name || "")}">删除</button>
    </header>
    <div class="md-body">${html}</div>
    ${sources.length ? `<p class="field-help">来源：${sources.map((item) => escape(item.title || item.id || item)).join("、")}</p>` : ""}
  </article>`;
}

export function historyMarkup(readings = []) {
  const rows = Array.isArray(readings) ? readings : [];
  return rows.length
    ? `<ul class="bazi-history">${rows.map((item) => `<li>
        <button type="button" class="bazi-history-row" data-bazi-open="${escape(item.name || "")}">
          <span class="bazi-history-main"><strong>${escape(item.title || "八字解读")}</strong>
            <em>${escape(item.topic || "综合")} · ${escape(item.date || "")}</em></span>
          <span class="bazi-history-arrow" aria-hidden="true">›</span>
        </button></li>`).join("")}</ul>`
    : `<div class="bazi-empty"><p>还没有解读。</p><p class="field-help">在「AI 解读」里选一个主题，让导师结合你的命盘和档案来写。</p></div>`;
}

export function baziPageMarkup({ profile = {}, chart = null, readings = [], view = "chart" } = {}) {
  const exists = Boolean(profile?.exists);
  return `<div class="bazi-shell">
    <header class="bazi-head">
      <div><p class="eyebrow">BAZI · 我的八字</p><h2>我的八字</h2>
        <p class="field-help">本地排盘，AI 只写解读；不宿命、不替代现实决策。</p></div>
      <div class="bazi-head-actions">
        <button type="button" class="button secondary" data-bazi-edit>${exists ? "编辑出生信息" : "填写出生信息"}</button>
        <button type="button" class="button primary" data-bazi-generate>生成解读</button>
      </div>
    </header>
    ${exists ? chartBoardMarkup(chart || {}) : `<div class="bazi-empty"><p>还没有命盘。</p><p class="field-help">先填写出生信息或四柱，然后保存。</p>${profileFormMarkup(profile)}</div>`}
    ${exists ? `<details class="bazi-form-wrap"${view === "chart" ? "" : ""}><summary>编辑出生信息 / 四柱</summary>${profileFormMarkup(profile)}</details>` : ""}
    <section class="bazi-readings-section">
      <header class="bazi-section-head"><div><p class="eyebrow">AI READING</p><h3>AI 解读</h3></div>
        <button type="button" class="text-button" data-bazi-history>历史 ${Array.isArray(readings) ? readings.length : 0}</button></header>
      ${topicsMarkup()}
      <div class="bazi-reading-host" data-bazi-reading-host>
        ${readings.length ? readingMarkup(readings[0]) : `<div class="bazi-empty"><p>还没有解读。</p><p class="field-help">选一个主题，让导师结合命盘、档案与记忆来写。</p></div>`}
      </div>
    </section>
    <section class="bazi-history-section" hidden>
      <header class="bazi-section-head"><div><p class="eyebrow">HISTORY</p><h3>解读历史</h3></div>
        <button type="button" class="text-button" data-bazi-back>返回当前</button></header>
      <div data-bazi-history-host>${historyMarkup(readings)}</div>
    </section>
  </div>`;
}
