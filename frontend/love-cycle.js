// 恋爱系统 · 经期关怀页面。
// 只读本机 /api/love/cycle/*，不接 AI、不访问外部地址；所有数据由后端本机计算。
import {
  api, escapeHtml, jsonBody, openDialog, confirmAction, toast, setBusy,
} from "./core.js";
import {
  CYCLE_WEEKDAYS, calendarDayClasses, calendarMarkers, currentMonth, cycleTrendChart,
  dayAriaLabel, dayLabel, dayNumber, daysUntilLabel, formatSleep, formatWater,
  monthLabel, monthLeadingBlanks, phaseTone, shiftMonth, validMonth,
} from "./love-cycle-model.js";

const cycle = {
  overview: null,
  month: "",
  seq: 0,
};

function node(id) {
  return document.getElementById(id);
}

function closeDialog() {
  node("detail-dialog")?.close();
}

function loadOverview(month) {
  const query = validMonth(month) ? `?month=${encodeURIComponent(month)}` : "";
  return api(`/api/love/cycle/overview${query}`, { timeoutMs: 30000 });
}

export async function renderLoveCycle(content, { card, isActive, rerender } = {}) {
  if (!content) return;
  const seq = ++cycle.seq;
  const month = validMonth(cycle.month) ? cycle.month : "";
  try {
    const overview = await loadOverview(month);
    if (seq !== cycle.seq || (isActive && !isActive())) return;
    cycle.overview = overview;
    cycle.month = overview.month;
    content.innerHTML = pageMarkup(overview, card);
    if (!overview.read_only) {
      bindDailyForm(content, { rerender: rerender || (() => renderLoveCycle(content, { card, isActive, rerender })) });
    }
  } catch (error) {
    if (seq !== cycle.seq || (isActive && !isActive())) return;
    content.innerHTML = errorMarkup(error.message);
  }
}

export async function handleLoveCycleAction(event, { rerender } = {}) {
  const button = event?.target?.closest?.("[data-love-action]");
  if (!button) return false;
  const action = button.dataset.loveAction;
  const refresh = rerender || (() => Promise.resolve());
  switch (action) {
    case "cycle-month": {
      const base = cycle.overview?.calendar?.month || cycle.month || currentMonth();
      cycle.month = shiftMonth(base, Number(button.dataset.value) < 0 ? -1 : 1);
      await refresh();
      return true;
    }
    case "cycle-today": {
      cycle.month = cycle.overview?.today?.slice(0, 7) || currentMonth();
      await refresh();
      return true;
    }
    case "cycle-retry":
    case "cycle-refresh":
      await refresh();
      return true;
    case "cycle-period-new":
      openPeriodDialog({ presetDate: button.dataset.date || "", rerender: refresh });
      return true;
    case "cycle-period-edit": {
      const id = Number(button.dataset.id);
      const record = (cycle.overview?.periods || []).find((item) => item.id === id);
      if (record) openPeriodDialog({ record, rerender: refresh });
      return true;
    }
    case "cycle-period-delete": {
      const id = Number(button.dataset.id);
      if (!Number.isFinite(id)) return true;
      const confirmed = await confirmAction({
        title: "删除这条经期记录？",
        message: "只会删除这条经期记录，不会删除每日身体状态。",
        confirmLabel: "删除记录",
        danger: true,
      });
      if (!confirmed) return true;
      try {
        await api(`/api/love/cycle/periods/${id}`, { method: "DELETE", timeoutMs: 30000 });
        toast("经期记录已删除。", "success");
        await refresh();
      } catch (error) {
        toast(`删除失败：${error.message}`, "error");
      }
      return true;
    }
    case "cycle-day": {
      const day = (cycle.overview?.calendar?.days || [])
        .find((item) => item.date === button.dataset.date);
      if (day) openDayDialog(day, { rerender: refresh });
      return true;
    }
    case "cycle-log-today":
      openDailyDialog({
        day: cycle.overview?.today,
        log: cycle.overview?.today_log || null,
        rerender: refresh,
      });
      return true;
    case "cycle-settings":
      openSettingsDialog({ rerender: refresh });
      return true;
    default:
      return false;
  }
}

export function cycleDashboardMarkup(overview, card) {
  if (!overview || typeof card !== "function") return "";
  const analysis = overview.analysis || {};
  const partnerName = String(overview.profile?.partner_name || "").trim();
  const subtitle = `${partnerName ? `${partnerName}的周期与身体状态` : "伴侣的周期与身体状态"}，纯本地记录。`;
  if (!analysis.has_data) {
    return card("经期关怀", `
      <p class="love-cycle-help">还没有经期记录。补记最近一次经期后，这里会显示阶段、下次经期和易孕期；数据只在本机计算。</p>
      <div class="love-inline-actions">
        <button class="button secondary" type="button" data-love-action="goto" data-page="cycle">打开经期关怀</button>
      </div>`, {
      eyebrow: "CYCLE CARE",
      subtitle,
    });
  }
  const care = overview.care || {};
  const alert = (care.alerts || [])[0] || null;
  return card("经期关怀", `
    <div class="love-cycle-dashboard">
      <div class="love-cycle-dashboard-head">
        <span class="love-badge ${analysis.phase === "late" ? "warn" : "ok"}">${escapeHtml(analysis.phase_label)}</span>
        <strong>${escapeHtml(daysUntilLabel(analysis.days_until))}</strong>
      </div>
      <p class="love-cycle-dashboard-meta">周期第 ${analysis.cycle_day} 天 · 预计 ${escapeHtml(dayLabel(analysis.next_expected))}</p>
      <p class="love-cycle-dashboard-meta">易孕期 ${escapeHtml(dayLabel(analysis.fertile_start))} ~ ${escapeHtml(dayLabel(analysis.fertile_end))}</p>
      ${alert ? `<p class="love-cycle-help">${escapeHtml(alert.text)}</p>` : ""}
      <div class="love-inline-actions">
        <button class="button secondary" type="button" data-love-action="goto" data-page="cycle">打开经期关怀</button>
      </div>
    </div>`, {
    eyebrow: "CYCLE CARE",
    subtitle,
  });
}

// ── 页面 ────────────────────────────────────────────────────────────

function pageMarkup(overview, card) {
  const analysis = overview.analysis || {};
  const readOnly = Boolean(overview.read_only);
  const stats = overview.stats || {};
  const care = overview.care || {};
  const cycleDay = analysis.has_data ? `第 ${analysis.cycle_day} 天` : "—";
  const nextLabel = analysis.has_data ? daysUntilLabel(analysis.days_until) : "待记录";
  const nextSmall = analysis.next_expected ? dayLabel(analysis.next_expected) : "记录后开始预测";
  const confidence = analysis.has_data
    ? `${analysis.confidence_label}${analysis.observed_cycles ? ` · ${analysis.observed_cycles} 个周期` : ""}`
    : "暂无";
  const periodCount = Number(stats.total_periods || 0);
  const todaySummary = overview.today_log
    ? `<p class="love-cycle-today-note">今天已记录：${escapeHtml(dailySummaryText(overview.today_log))}</p>`
    : '<p class="love-cycle-today-note">今天还没有记录身体状态。</p>';

  return `<div class="love-cycle-page">
    ${readOnly ? readOnlyBanner() : ""}
    <div class="love-stat-grid">
      <article class="love-stat"><span>周期进度</span><strong>${escapeHtml(cycleDay)}</strong><small>平均 ${analysis.avg_cycle || 28} 天</small></article>
      <article class="love-stat"><span>当前阶段</span><strong>${escapeHtml(analysis.phase_label || "待记录")}</strong><small>${escapeHtml(analysis.has_data ? analysis.summary : "先记录最近一次经期")}</small></article>
      <article class="love-stat"><span>下次经期</span><strong>${escapeHtml(nextLabel)}</strong><small>${escapeHtml(nextSmall)}</small></article>
      <article class="love-stat"><span>预测依据</span><strong>${escapeHtml(confidence)}</strong><small>${periodCount} 条经期记录</small></article>
    </div>
    ${heroMarkup(overview)}
    ${alertsMarkup(care.alerts)}
    <div class="love-cycle-care-grid">
      ${card("照顾她的这几天", `
        <ul class="love-cycle-care-list love-cycle-partner-list">
          ${(care.partner_tips || []).map((tip) => `<li>${escapeHtml(tip)}</li>`).join("")}
        </ul>`, {
        eyebrow: "PARTNER CARE",
        subtitle: "按周期阶段给伴侣的原创关怀提示，不替代她的感受。",
      })}
      ${card("身体提示", `
        <ul class="love-cycle-care-list">
          ${(care.body_tips || []).map((tip) => `<li>${escapeHtml(tip)}</li>`).join("")}
        </ul>`, {
        eyebrow: "BODY CARE",
        subtitle: "通用生活建议；异常情况请咨询医生。",
      })}
    </div>
    ${disclaimerMarkup(care.disclaimer)}
    ${card("月历", calendarMarkup(overview), {
      eyebrow: "CALENDAR",
      subtitle: "实际记录优先于预测；点日期查看当天详情。",
    })}
    ${card("今天的状态", readOnly ? `${todaySummary}<div class="love-cycle-empty">只读模式下不能修改记录。</div>` : `${todaySummary}${dailyFormMarkup(overview.today, overview.today_log, { prefix: "page" })}`, {
      eyebrow: "TODAY",
      subtitle: "疼痛、情绪、睡眠、饮水和症状都只保存在本机。",
    })}
    ${card("周期趋势与统计", trendMarkup(overview), {
      eyebrow: "TRENDS",
      subtitle: "周期长度会有自然波动；趋势只作参考。",
    })}
    ${card("经期历史", historyMarkup(overview), {
      eyebrow: "HISTORY",
      subtitle: "补记、修改或删除过去的经期记录。",
      actions: readOnly ? "" : '<button class="small-button" type="button" data-love-action="cycle-period-new">补记经期</button>',
    })}
  </div>`;
}

function heroMarkup(overview) {
  const analysis = overview.analysis || {};
  const readOnly = Boolean(overview.read_only);
  const partnerName = String(overview.profile?.partner_name || "").trim();
  if (!analysis.has_data) {
    return `<section class="love-cycle-hero">
      <div class="love-cycle-hero-top">
        <div class="love-cycle-hero-main">
          <span class="love-cycle-phase">${partnerName ? `${escapeHtml(partnerName)}的周期` : "CYCLE CARE"}</span>
          <h3>先记录一次经期</h3>
          <p class="love-cycle-hero-summary">补上开始和结束日期后，这里会出现周期阶段、下次经期、排卵日和易孕期。所有数据只在本机计算。</p>
        </div>
      </div>
      <div class="love-cycle-hero-actions">
        ${readOnly ? '<span class="love-badge">只读模式</span>' : `
          <button class="button primary" type="button" data-love-action="cycle-period-new">记录经期</button>
          <button class="button secondary" type="button" data-love-action="cycle-settings">周期设置</button>
          <button class="button secondary" type="button" data-love-action="cycle-log-today">记录今天状态</button>`}
      </div>
    </section>`;
  }
  const regularity = analysis.regularity === "unknown"
    ? "数据不足"
    : `${analysis.regularity_label}${analysis.regularity_sd != null ? ` · σ ${analysis.regularity_sd} 天` : ""}`;
  return `<section class="love-cycle-hero ${escapeHtml(phaseTone(analysis.phase))}">
    <div class="love-cycle-hero-top">
      <div class="love-cycle-hero-main">
        <span class="love-cycle-phase">${partnerName ? `${escapeHtml(partnerName)} · ` : ""}${escapeHtml(analysis.phase_label)} · 周期第 ${analysis.cycle_day} 天</span>
        <h3>${escapeHtml(analysis.phase_label)}</h3>
        <p class="love-cycle-hero-summary">${escapeHtml(analysis.summary || "")}</p>
      </div>
      <div class="love-cycle-countdown">
        <strong>${escapeHtml(daysUntilLabel(analysis.days_until))}</strong>
        <small>预计 ${escapeHtml(dayLabel(analysis.next_expected))}</small>
      </div>
    </div>
    <progress class="love-cycle-progress" max="100" value="${Number(analysis.progress) || 0}" aria-label="本周期进度">${Number(analysis.progress) || 0}%</progress>
    <p class="love-cycle-progress-note">第 ${analysis.cycle_day} 天 / 平均 ${analysis.avg_cycle} 天 · ${escapeHtml(analysis.confidence_label)}${analysis.observed_cycles ? ` · ${analysis.observed_cycles} 个完整周期` : ""}</p>
    <dl class="love-cycle-facts">
      <div><dt>预计下次</dt><dd>${escapeHtml(dayLabel(analysis.next_expected))}</dd></div>
      <div><dt>排卵日</dt><dd>${escapeHtml(dayLabel(analysis.ovulation_date))}</dd></div>
      <div><dt>易孕期</dt><dd>${escapeHtml(dayLabel(analysis.fertile_start))} ~ ${escapeHtml(dayLabel(analysis.fertile_end))}</dd></div>
      <div><dt>规律度</dt><dd>${escapeHtml(regularity)}</dd></div>
    </dl>
    <div class="love-cycle-hero-actions">
      ${readOnly ? '<span class="love-badge">只读模式</span>' : `
        <button class="button primary" type="button" data-love-action="cycle-period-new">记录经期</button>
        <button class="button secondary" type="button" data-love-action="cycle-log-today">记录今天状态</button>
        <button class="button secondary" type="button" data-love-action="cycle-settings">周期设置</button>`}
    </div>
  </section>`;
}

function alertsMarkup(alerts) {
  if (!Array.isArray(alerts) || !alerts.length) return "";
  return `<div class="love-cycle-alerts">
    ${alerts.map((alert) => `<div class="love-cycle-alert ${alert.level === "warn" ? "warn" : "info"}">
      <strong>${escapeHtml(alert.title || "")}</strong><span>${escapeHtml(alert.text || "")}</span>
    </div>`).join("")}
  </div>`;
}

function disclaimerMarkup(text) {
  return `<div class="love-cycle-disclaimer"><strong>请留意</strong><span>${escapeHtml(text || "预测只是估算，不是诊断，也不能作为避孕依据。")}</span></div>`;
}

function readOnlyBanner() {
  return `<div class="love-cycle-alert warn"><strong>只读模式</strong><span>当前只能查看，不能保存经期或身体记录；如需记录，请先在设置里关闭 Obsidian 只读模式并重启服务。</span></div>`;
}

// ── 月历 ────────────────────────────────────────────────────────────

function calendarMarkup(overview) {
  const calendar = overview.calendar || {};
  const month = calendar.month || overview.month || "";
  const readOnly = Boolean(overview.read_only);
  const days = calendar.days || [];
  const hasForecast = days.some((day) => day.is_predicted || day.is_fertile || day.is_ovulation);
  const todayMonth = String(overview.today || "").slice(0, 7);
  const forecastNote = !overview.analysis?.has_data
    ? '<p class="love-cycle-help">记录一次经期后，月历会显示预计经期和易孕期。</p>'
    : (!hasForecast && todayMonth && month > todayMonth
      ? '<p class="love-cycle-help">这个月超出了当前 12 个周期的预测范围；记录新的经期后会重新估算。</p>'
      : "");
  const cells = [];
  for (let index = 0; index < monthLeadingBlanks(month); index += 1) {
    cells.push('<span class="lc-day is-empty" aria-hidden="true"></span>');
  }
  for (const day of days) {
    const markers = calendarMarkers(day)
      .map((marker) => `<span class="lc-day-marker ${escapeHtml(marker.tone)}">${escapeHtml(marker.label)}</span>`)
      .join("");
    cells.push(`<button type="button" class="${calendarDayClasses(day)}" data-love-action="cycle-day" data-date="${escapeHtml(day.date)}" aria-label="${escapeHtml(dayAriaLabel(day))}">
      <span class="lc-day-number">${dayNumber(day.date)}</span>
      <span class="lc-day-markers">${markers}</span>
    </button>`);
  }
  while (cells.length % 7 !== 0) {
    cells.push('<span class="lc-day is-empty" aria-hidden="true"></span>');
  }
  return `<div class="love-cycle-toolbar">
    <div class="love-cycle-month-nav">
      <button class="small-button" type="button" data-love-action="cycle-month" data-value="-1" aria-label="上个月">‹</button>
      <strong>${escapeHtml(monthLabel(month))}</strong>
      <button class="small-button" type="button" data-love-action="cycle-month" data-value="1" aria-label="下个月">›</button>
      <button class="text-button" type="button" data-love-action="cycle-today">回到本月</button>
    </div>
    ${readOnly ? "" : '<button class="small-button" type="button" data-love-action="cycle-period-new">补记经期</button>'}
  </div>
  <div class="love-cycle-weekdays" aria-hidden="true">${CYCLE_WEEKDAYS.map((name) => `<span>${name}</span>`).join("")}</div>
  <div class="love-cycle-grid">${cells.join("")}</div>
  ${forecastNote}
  <div class="love-cycle-legend" aria-hidden="true">
    <span><i class="period"></i>经期</span>
    <span><i class="predicted"></i>预计经期</span>
    <span><i class="fertile"></i>易孕期</span>
    <span><i class="ovulation"></i>排卵日</span>
    <span><i class="log"></i>有身体记录</span>
    <span><i class="today"></i>今天</span>
  </div>`;
}

// ── 趋势与历史 ──────────────────────────────────────────────────────

function trendMarkup(overview) {
  const stats = overview.stats || {};
  const chart = cycleTrendChart(stats.cycle_lengths || []);
  return `
    <div class="love-cycle-metrics">
      <div class="love-cycle-metric"><span>平均周期</span><strong>${stats.avg_cycle || "—"} 天</strong></div>
      <div class="love-cycle-metric"><span>平均经期</span><strong>${stats.avg_period || "—"} 天</strong></div>
      <div class="love-cycle-metric"><span>规律度</span><strong>${escapeHtml(stats.regularity_label || "未知")}</strong></div>
      <div class="love-cycle-metric"><span>记录天数</span><strong>${stats.logged_days || 0} 天</strong></div>
    </div>
    <div class="love-cycle-metrics">
      <div class="love-cycle-metric"><span>疼痛均值</span><strong>${stats.pain_average != null ? `${stats.pain_average} / 3` : "—"}</strong></div>
      <div class="love-cycle-metric"><span>睡眠均值</span><strong>${stats.sleep_average != null ? `${stats.sleep_average} 小时` : "—"}</strong></div>
      <div class="love-cycle-metric"><span>饮水均值</span><strong>${stats.water_average != null ? `${stats.water_average} ml` : "—"}</strong></div>
      <div class="love-cycle-metric"><span>精力均值</span><strong>${stats.energy_average != null ? `${stats.energy_average} / 5` : "—"}</strong></div>
    </div>
    ${chart ? `<div class="love-cycle-trend">${chart}<p class="love-cycle-trend-note">正常参考带 21–35 天，虚线为 28 天；点上的数字是每个周期长度。</p></div>`
      : '<div class="love-cycle-empty"><strong>还不够画趋势</strong><p>记录至少 3 次经期后，这里会出现最近周期长度。</p></div>'}
    <div class="love-cycle-freq-grid">
      <div><h3 class="love-subhead">常见症状</h3>${freqMarkup(stats.top_symptoms, "症状")}</div>
      <div><h3 class="love-subhead">常见情绪</h3>${freqMarkup(stats.top_moods, "情绪")}</div>
    </div>`;
}

function freqMarkup(items, label) {
  if (!Array.isArray(items) || !items.length) {
    return `<p class="love-cycle-help">还没有${escapeHtml(label)}记录。</p>`;
  }
  const max = Math.max(...items.map((item) => Number(item.count) || 0), 1);
  return `<div class="love-cycle-freq-list">
    ${items.map((item) => `<div class="love-cycle-freq-row">
      <span>${escapeHtml(item.label)}</span>
      <progress max="${max}" value="${Number(item.count) || 0}" aria-label="${escapeHtml(`${item.label} ${item.count} 次`)}"></progress>
      <small>${Number(item.count) || 0} 次</small>
    </div>`).join("")}
  </div>`;
}

function historyMarkup(overview) {
  const periods = Array.isArray(overview.periods) ? overview.periods : [];
  const readOnly = Boolean(overview.read_only);
  if (!periods.length) {
    return '<div class="love-cycle-empty"><strong>还没有经期记录</strong><p>点「记录经期」补上最近一次的开始和结束日期。</p></div>';
  }
  return `<ul class="love-cycle-history">
    ${periods.map((period) => {
      const range = period.end_date
        ? `${dayLabel(period.start_date)} ~ ${dayLabel(period.end_date)}`
        : `${dayLabel(period.start_date)} 起 · 未结束`;
      const symptomBadges = (period.symptom_labels || [])
        .map((label) => `<span class="love-badge">${escapeHtml(label)}</span>`).join("");
      const moodBadges = (period.mood_labels || [])
        .map((label) => `<span class="love-badge">${escapeHtml(label)}</span>`).join("");
      return `<li class="love-cycle-history-row">
        <div class="love-cycle-history-main">
          <span class="love-cycle-history-title">${escapeHtml(range)}</span>
          <div class="love-cycle-history-meta">
            <span class="love-badge">${escapeHtml(period.flow_label)}</span>
            ${period.duration_days ? `<span>${period.duration_days} 天</span>` : "<span>进行中</span>"}
            ${symptomBadges}${moodBadges}
          </div>
          ${period.notes ? `<p class="love-cycle-history-notes">${escapeHtml(period.notes)}</p>` : ""}
        </div>
        ${readOnly ? "" : `<div class="love-cycle-history-actions">
          <button class="small-button" type="button" data-love-action="cycle-period-edit" data-id="${period.id}">编辑</button>
          <button class="small-button reject" type="button" data-love-action="cycle-period-delete" data-id="${period.id}">删除</button>
        </div>`}
      </li>`;
    }).join("")}
  </ul>`;
}

// ── 每日状态表单 ────────────────────────────────────────────────────

function dailyFormMarkup(day, log, { prefix = "daily", dialog = false } = {}) {
  const catalogs = cycle.overview?.catalogs || {};
  const formId = `${prefix}-cycle-log-form`;
  const mood = log?.mood || "";
  const pain = log?.pain ?? "";
  const energy = log?.energy ?? "";
  const sleep = log?.sleep_hours ?? "";
  const water = log?.water_ml ?? "";
  const symptoms = log?.symptoms || [];
  const notes = log?.notes || "";
  return `<form class="love-cycle-form" data-cycle-daily-form data-day="${escapeHtml(day)}" aria-label="${escapeHtml(`${dayLabel(day)} 身体状态`)}">
    <div class="love-cycle-form-grid">
      <div class="love-cycle-field">
        <label for="${formId}-mood">情绪</label>
        <select id="${formId}-mood" name="cycle-log-mood">${selectOptions(catalogs.moods || [], mood, "未记录")}</select>
      </div>
      <div class="love-cycle-field">
        <label for="${formId}-pain">疼痛</label>
        <select id="${formId}-pain" name="cycle-log-pain">${selectOptions(catalogs.pain_levels || [], pain, "未记录")}</select>
      </div>
      <div class="love-cycle-field">
        <label for="${formId}-sleep">睡眠（小时）</label>
        <input id="${formId}-sleep" name="cycle-log-sleep" type="number" min="0" max="24" step="0.5" value="${escapeHtml(sleep)}" placeholder="例如 7.5">
      </div>
      <div class="love-cycle-field">
        <label for="${formId}-water">饮水（ml）</label>
        <input id="${formId}-water" name="cycle-log-water" type="number" min="0" max="5000" step="100" value="${escapeHtml(water)}" placeholder="例如 1200">
      </div>
      <div class="love-cycle-field">
        <label for="${formId}-energy">精力</label>
        <select id="${formId}-energy" name="cycle-log-energy">${selectOptions(catalogs.energy_levels || [], energy, "未记录")}</select>
      </div>
    </div>
    <div class="love-cycle-field">
      <span>症状（可多选）</span>
      <div class="love-cycle-chips">${chipsMarkup(catalogs.symptoms || [], symptoms, "cycle-symptom")}</div>
    </div>
    <div class="love-cycle-field">
      <label for="${formId}-notes">备注</label>
      <textarea id="${formId}-notes" name="cycle-log-notes" rows="3" maxlength="500" placeholder="例如：腰痛，下午休息了一会儿">${escapeHtml(notes)}</textarea>
    </div>
    <div class="love-cycle-form-actions">
      <button class="button primary" type="submit">保存当天状态</button>
      ${log ? '<button class="text-button" type="button" data-cycle-log-clear>清除当天记录</button>' : ""}
      ${dialog ? '<button class="text-button" type="button" data-cycle-dialog-cancel>取消</button>' : ""}
      <span class="status-message" data-cycle-feedback role="status"></span>
    </div>
  </form>`;
}

function bindDailyForm(root, { rerender } = {}) {
  const form = root?.querySelector?.("[data-cycle-daily-form]");
  if (!form || form.dataset.bound) return;
  form.dataset.bound = "1";
  bindChips(form, "[data-cycle-symptom]", { exclusiveValue: "none" });

  const clearButton = form.querySelector("[data-cycle-log-clear]");
  clearButton?.addEventListener("click", async () => {
    const day = form.dataset.day;
    const confirmed = await confirmAction({
      title: "清除当天的身体记录？",
      message: "只会删除这一天的情绪、疼痛、睡眠、饮水、精力和备注。",
      confirmLabel: "清除记录",
      danger: true,
    });
    if (!confirmed) return;
    try {
      await api(`/api/love/cycle/daily/${encodeURIComponent(day)}`, { method: "DELETE", timeoutMs: 30000 });
      toast("已清除当天状态。", "success");
      if (form.closest("#detail-dialog")) closeDialog();
      await rerender?.();
    } catch (error) {
      formFeedback(form, `清除失败：${error.message}`);
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const day = form.dataset.day;
    if (!day) return;
    const payload = {
      mood: form.querySelector('[name="cycle-log-mood"]')?.value || null,
      pain: numberOrNull(form.querySelector('[name="cycle-log-pain"]')?.value),
      sleep_hours: numberOrNull(form.querySelector('[name="cycle-log-sleep"]')?.value),
      water_ml: numberOrNull(form.querySelector('[name="cycle-log-water"]')?.value),
      energy: numberOrNull(form.querySelector('[name="cycle-log-energy"]')?.value),
      symptoms: selectedValues(form, "[data-cycle-symptom]"),
      notes: form.querySelector('[name="cycle-log-notes"]')?.value || "",
    };
    setBusy(form, true, "保存中…");
    try {
      const result = await api(
        `/api/love/cycle/daily/${encodeURIComponent(day)}`,
        jsonBody("PUT", payload),
      );
      toast(result.deleted ? "已清除当天状态。" : "当天状态已保存。", "success");
      if (form.closest("#detail-dialog")) closeDialog();
      await rerender?.();
    } catch (error) {
      setBusy(form, false);
      formFeedback(form, `保存失败：${error.message}`);
    }
  });
}

// ── 经期 / 设置弹窗 ────────────────────────────────────────────────

function periodFormMarkup({ record = null, presetDate = "" } = {}) {
  const catalogs = cycle.overview?.catalogs || {};
  const today = cycle.overview?.today || "";
  const start = record?.start_date || presetDate || today;
  const end = record?.end_date || "";
  const ongoing = record ? Boolean(record.ongoing) : start === today;
  const flow = record?.flow || "medium";
  const symptoms = record?.symptoms || [];
  const moods = record?.moods || [];
  const notes = record?.notes || "";
  return `<form class="love-cycle-dialog-form" data-cycle-period-form data-period-id="${record ? record.id : ""}">
    <p class="love-cycle-help">经期数据只保存在本机 <code>.torch-os/love/cycle.db</code>；不会发送给 AI 或外部服务。</p>
    <div class="love-cycle-dialog-grid">
      <div class="love-cycle-field">
        <label for="cycle-period-start">开始日期</label>
        <input id="cycle-period-start" name="cycle-period-start" type="date" required max="${escapeHtml(today)}" value="${escapeHtml(start)}">
      </div>
      <div class="love-cycle-field">
        <label for="cycle-period-end">结束日期</label>
        <input id="cycle-period-end" name="cycle-period-end" type="date" max="${escapeHtml(today)}" value="${escapeHtml(end)}">
        <label class="love-cycle-inline-check"><input type="checkbox" name="cycle-period-ongoing"${ongoing ? " checked" : ""}> 还没有结束</label>
      </div>
    </div>
    <div class="love-cycle-field">
      <label for="cycle-period-flow">流量</label>
      <select id="cycle-period-flow" name="cycle-period-flow">${selectOptions(catalogs.flows || [], flow)}</select>
    </div>
    <div class="love-cycle-field">
      <span>症状（可多选）</span>
      <div class="love-cycle-chips">${chipsMarkup(catalogs.symptoms || [], symptoms, "cycle-symptom")}</div>
    </div>
    <div class="love-cycle-field">
      <span>情绪（可多选）</span>
      <div class="love-cycle-chips">${chipsMarkup(catalogs.moods || [], moods, "cycle-mood")}</div>
    </div>
    <div class="love-cycle-field">
      <label for="cycle-period-notes">备注</label>
      <textarea id="cycle-period-notes" name="cycle-period-notes" rows="3" maxlength="500" placeholder="例如：第一天腹痛，休息半天">${escapeHtml(notes)}</textarea>
    </div>
    <div class="love-cycle-form-actions">
      <button class="button primary" type="submit">保存经期</button>
      <button class="text-button" type="button" data-cycle-dialog-cancel>取消</button>
      <span class="status-message" data-cycle-feedback role="status"></span>
    </div>
  </form>`;
}

function bindPeriodForm(root, { rerender } = {}) {
  const form = root?.querySelector?.("[data-cycle-period-form]");
  if (!form) return;
  bindChips(form, "[data-cycle-symptom]", { exclusiveValue: "none" });
  bindChips(form, "[data-cycle-mood]");
  const ongoing = form.querySelector('[name="cycle-period-ongoing"]');
  const endInput = form.querySelector('[name="cycle-period-end"]');
  const syncEnd = () => {
    if (endInput) endInput.disabled = Boolean(ongoing?.checked);
  };
  ongoing?.addEventListener("change", syncEnd);
  syncEnd();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const start = form.querySelector('[name="cycle-period-start"]')?.value || "";
    if (!start) {
      formFeedback(form, "请选择开始日期。");
      return;
    }
    const periodId = form.dataset.periodId;
    const end = endInput?.value || "";
    if (!ongoing?.checked && !end) {
      formFeedback(form, "请填写结束日期，或勾选「还没有结束」。");
      return;
    }
    const payload = {
      start_date: start,
      end_date: ongoing?.checked ? null : end,
      flow: form.querySelector('[name="cycle-period-flow"]')?.value || "medium",
      symptoms: selectedValues(form, "[data-cycle-symptom]"),
      moods: selectedValues(form, "[data-cycle-mood]"),
      notes: form.querySelector('[name="cycle-period-notes"]')?.value || "",
    };
    setBusy(form, true, "保存中…");
    try {
      const path = periodId
        ? `/api/love/cycle/periods/${encodeURIComponent(periodId)}`
        : "/api/love/cycle/periods";
      await api(path, jsonBody(periodId ? "PUT" : "POST", payload));
      if (!periodId) cycle.month = payload.start_date.slice(0, 7);
      toast(periodId ? "经期记录已更新。" : "经期记录已保存。", "success");
      closeDialog();
      await rerender?.();
    } catch (error) {
      setBusy(form, false);
      formFeedback(form, `保存失败：${error.message}`);
    }
  });
}

function profileFormMarkup(profile) {
  return `<form class="love-cycle-dialog-form" data-cycle-profile-form>
    <p class="love-cycle-help">称呼只用于页面显示。周期和经期长度填 0 表示按记录自动推算；黄体期通常为 14 天，可保留默认。所有设置只保存在本机。</p>
    <div class="love-cycle-field">
      <label for="cycle-profile-name">她的称呼</label>
      <input id="cycle-profile-name" name="cycle-profile-name" type="text" maxlength="40" value="${escapeHtml(profile?.partner_name || "")}" placeholder="可选">
    </div>
    <div class="love-cycle-dialog-grid">
      <div class="love-cycle-field">
        <label for="cycle-profile-cycle">周期长度（天）</label>
        <input id="cycle-profile-cycle" name="cycle-profile-cycle" type="number" min="0" max="45" step="1" value="${Number(profile?.cycle_length) || 0}">
        <span class="love-cycle-help">0 = 自动；手动时 21–45 天。</span>
      </div>
      <div class="love-cycle-field">
        <label for="cycle-profile-period">经期长度（天）</label>
        <input id="cycle-profile-period" name="cycle-profile-period" type="number" min="0" max="10" step="1" value="${Number(profile?.period_length) || 0}">
        <span class="love-cycle-help">0 = 自动；手动时 2–10 天。</span>
      </div>
      <div class="love-cycle-field">
        <label for="cycle-profile-luteal">黄体期（天）</label>
        <input id="cycle-profile-luteal" name="cycle-profile-luteal" type="number" min="10" max="16" step="1" value="${Number(profile?.luteal_phase) || 14}">
        <span class="love-cycle-help">排卵后到下次经期的估算天数。</span>
      </div>
      <div class="love-cycle-field">
        <label for="cycle-profile-reminder">提前提醒（天）</label>
        <input id="cycle-profile-reminder" name="cycle-profile-reminder" type="number" min="0" max="7" step="1" value="${Number(profile?.reminder_days) || 0}">
        <span class="love-cycle-help">只做页内提示，不发系统通知。</span>
      </div>
    </div>
    <div class="love-cycle-form-actions">
      <button class="button primary" type="submit">保存设置</button>
      <button class="text-button" type="button" data-cycle-dialog-cancel>取消</button>
      <span class="status-message" data-cycle-feedback role="status"></span>
    </div>
  </form>`;
}

function bindProfileForm(root, { rerender } = {}) {
  const form = root?.querySelector?.("[data-cycle-profile-form]");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      partner_name: form.querySelector('[name="cycle-profile-name"]')?.value || "",
      cycle_length: numberOrZero(form.querySelector('[name="cycle-profile-cycle"]')?.value),
      period_length: numberOrZero(form.querySelector('[name="cycle-profile-period"]')?.value),
      luteal_phase: numberOrZero(form.querySelector('[name="cycle-profile-luteal"]')?.value) || 14,
      reminder_days: numberOrZero(form.querySelector('[name="cycle-profile-reminder"]')?.value),
    };
    setBusy(form, true, "保存中…");
    try {
      await api("/api/love/cycle/profile", jsonBody("PUT", payload));
      toast("周期设置已保存。", "success");
      closeDialog();
      await rerender?.();
    } catch (error) {
      setBusy(form, false);
      formFeedback(form, `保存失败：${error.message}`);
    }
  });
}

function openPeriodDialog({ record = null, presetDate = "", rerender } = {}) {
  if (!cycle.overview || cycle.overview.read_only) return;
  openDialog(record ? "编辑经期" : "记录经期", periodFormMarkup({ record, presetDate }));
  const root = node("dialog-content");
  bindPeriodForm(root, { rerender });
  root?.querySelector("[data-cycle-dialog-cancel]")?.addEventListener("click", closeDialog);
}

function openDailyDialog({ day, log = null, rerender } = {}) {
  if (!cycle.overview || cycle.overview.read_only || !day) return;
  openDialog(`${dayLabel(day)} · 身体状态`, dailyFormMarkup(day, log, { prefix: "dialog", dialog: true }));
  const root = node("dialog-content");
  bindDailyForm(root, { rerender });
  root?.querySelector("[data-cycle-dialog-cancel]")?.addEventListener("click", closeDialog);
}

function openSettingsDialog({ rerender } = {}) {
  if (!cycle.overview || cycle.overview.read_only) return;
  openDialog("周期设置", profileFormMarkup(cycle.overview.profile || {}));
  const root = node("dialog-content");
  bindProfileForm(root, { rerender });
  root?.querySelector("[data-cycle-dialog-cancel]")?.addEventListener("click", closeDialog);
}

function openDayDialog(dayData, { rerender } = {}) {
  const overview = cycle.overview;
  if (!overview) return;
  const readOnly = Boolean(overview.read_only);
  const day = dayData.date;
  const period = (overview.periods || []).find((item) => item.id === dayData.period_id) || null;
  const log = overview.calendar?.logs?.[day]
    || (day === overview.today ? overview.today_log : null);
  const labels = calendarMarkers(dayData).map((marker) => marker.label).join(" · ");
  openDialog(`${dayLabel(day)}${labels ? ` · ${labels}` : ""}`, `
    <div class="love-cycle-dialog-form">
      ${dayData.is_today ? '<p class="love-cycle-help">今天</p>' : ""}
      ${period ? periodDetailMarkup(period) : ""}
      ${log ? dailyDetailMarkup(log) : ""}
      ${!period && !log ? '<p class="love-cycle-help">这一天还没有经期或身体记录。</p>' : ""}
      <div class="love-cycle-form-actions">
        ${readOnly || dayData.is_future
          ? `<span class="love-cycle-help">${readOnly ? "只读模式下不能修改记录。" : "未来日期不能记录。"}</span>`
          : `<button class="button primary" type="button" data-cycle-open-daily>${log ? "修改当天状态" : "记录当天状态"}</button>
             ${period
               ? '<button class="button secondary" type="button" data-cycle-open-period-edit>编辑经期</button>'
               : '<button class="button secondary" type="button" data-cycle-open-period>从这天补记经期</button>'}`}
        <button class="text-button" type="button" data-cycle-dialog-cancel>关闭</button>
      </div>
    </div>
  `);
  const root = node("dialog-content");
  root?.querySelector("[data-cycle-dialog-cancel]")?.addEventListener("click", closeDialog);
  root?.querySelector("[data-cycle-open-daily]")?.addEventListener("click", () => {
    openDailyDialog({ day, log, rerender });
  });
  root?.querySelector("[data-cycle-open-period]")?.addEventListener("click", () => {
    openPeriodDialog({ presetDate: day, rerender });
  });
  root?.querySelector("[data-cycle-open-period-edit]")?.addEventListener("click", () => {
    if (period) openPeriodDialog({ record: period, rerender });
  });
}

function periodDetailMarkup(period) {
  return `<section class="love-cycle-dialog-section">
    <h4>经期记录</h4>
    <dl class="love-cycle-day-facts">
      <div><dt>日期</dt><dd>${escapeHtml(dayLabel(period.start_date))}${period.end_date ? ` ~ ${escapeHtml(dayLabel(period.end_date))}` : " 起 · 未结束"}</dd></div>
      <div><dt>流量</dt><dd>${escapeHtml(period.flow_label)}</dd></div>
      ${(period.symptom_labels || []).length ? `<div><dt>症状</dt><dd>${period.symptom_labels.map((label) => escapeHtml(label)).join("、")}</dd></div>` : ""}
      ${(period.mood_labels || []).length ? `<div><dt>情绪</dt><dd>${period.mood_labels.map((label) => escapeHtml(label)).join("、")}</dd></div>` : ""}
      ${period.notes ? `<div><dt>备注</dt><dd>${escapeHtml(period.notes)}</dd></div>` : ""}
    </dl>
  </section>`;
}

function dailyDetailMarkup(log) {
  return `<section class="love-cycle-dialog-section">
    <h4>身体状态</h4>
    <dl class="love-cycle-day-facts">
      ${log.mood_label ? `<div><dt>情绪</dt><dd>${escapeHtml(log.mood_label)}</dd></div>` : ""}
      ${log.pain_label ? `<div><dt>疼痛</dt><dd>${escapeHtml(log.pain_label)}</dd></div>` : ""}
      ${log.sleep_hours != null ? `<div><dt>睡眠</dt><dd>${escapeHtml(formatSleep(log.sleep_hours))}</dd></div>` : ""}
      ${log.water_ml != null ? `<div><dt>饮水</dt><dd>${escapeHtml(formatWater(log.water_ml))}</dd></div>` : ""}
      ${log.energy_label ? `<div><dt>精力</dt><dd>${escapeHtml(log.energy_label)}</dd></div>` : ""}
      ${(log.symptom_labels || []).length ? `<div><dt>症状</dt><dd>${log.symptom_labels.map((label) => escapeHtml(label)).join("、")}</dd></div>` : ""}
      ${log.notes ? `<div><dt>备注</dt><dd>${escapeHtml(log.notes)}</dd></div>` : ""}
    </dl>
  </section>`;
}

function dailySummaryText(log) {
  const parts = [];
  if (log.mood_label) parts.push(`情绪${log.mood_label}`);
  if (log.pain_label) parts.push(`疼痛${log.pain_label}`);
  if (log.sleep_hours != null) parts.push(`睡眠 ${formatSleep(log.sleep_hours)}`);
  if (log.water_ml != null) parts.push(`饮水 ${formatWater(log.water_ml)}`);
  if (log.energy_label) parts.push(`精力${log.energy_label}`);
  if ((log.symptom_labels || []).length) parts.push(log.symptom_labels.join("、"));
  return parts.length ? parts.join(" · ") : "还没有填写具体内容";
}

// ── 表单小工具 ──────────────────────────────────────────────────────

function selectOptions(options, selected, emptyLabel = "") {
  const head = emptyLabel ? `<option value="">${escapeHtml(emptyLabel)}</option>` : "";
  return head + (Array.isArray(options) ? options : []).map((option) => {
    const active = String(option.value) === String(selected);
    return `<option value="${escapeHtml(option.value)}"${active ? " selected" : ""}>${escapeHtml(option.label)}</option>`;
  }).join("");
}

function chipsMarkup(options, selected, dataName) {
  const values = new Set((Array.isArray(selected) ? selected : []).map(String));
  return (Array.isArray(options) ? options : []).map((option) => {
    const active = values.has(String(option.value));
    return `<button type="button" class="love-cycle-chip${active ? " is-active" : ""}" data-${dataName} data-value="${escapeHtml(option.value)}" aria-pressed="${active}">${escapeHtml(option.label)}</button>`;
  }).join("");
}

function bindChips(root, selector, { exclusiveValue = "" } = {}) {
  const chips = [...root.querySelectorAll(selector)];
  chips.forEach((button) => {
    button.addEventListener("click", () => {
      const pressed = button.getAttribute("aria-pressed") === "true";
      if (exclusiveValue && button.dataset.value === exclusiveValue) {
        chips.forEach((item) => {
          item.classList.remove("is-active");
          item.setAttribute("aria-pressed", "false");
        });
        button.classList.add("is-active");
        button.setAttribute("aria-pressed", "true");
        return;
      }
      if (exclusiveValue) {
        const exclusive = chips.find((item) => item.dataset.value === exclusiveValue);
        if (exclusive) {
          exclusive.classList.remove("is-active");
          exclusive.setAttribute("aria-pressed", "false");
        }
      }
      button.classList.toggle("is-active", !pressed);
      button.setAttribute("aria-pressed", String(!pressed));
    });
  });
}

function selectedValues(root, selector) {
  return [...root.querySelectorAll(`${selector}[aria-pressed="true"]`)]
    .map((button) => button.dataset.value)
    .filter(Boolean);
}

function numberOrNull(value) {
  if (value === "" || value == null) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function numberOrZero(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function formFeedback(form, message) {
  const feedback = form.querySelector("[data-cycle-feedback]");
  if (!feedback) return;
  feedback.textContent = message;
  feedback.className = "status-message error";
}

function errorMarkup(message) {
  return `<div class="state-message error-state"><strong>经期数据暂时无法读取</strong><p>${escapeHtml(message)}</p><button class="text-button" type="button" data-love-action="cycle-retry">重试</button></div>`;
}
