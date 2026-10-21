// 生活运营：体重/账目月度表 + 极简 SVG 趋势。数据来自 Markdown 月度表。
import { api, escapeHtml, toast } from "./core.js";

const DATASETS = {
  health: { title: "健康与体重", accent: "var(--p-pillar-ops)", unit: "kg" },
  finance: { title: "财富与记账", accent: "var(--p-pillar-ops)" },
};

function monthNow() {
  const now = new Date();
  const offset = new Date(now.getTime() + (now.getTimezoneOffset() + 480) * 60000);
  return `${offset.getFullYear()}-${String(offset.getMonth() + 1).padStart(2, "0")}`;
}

function sparkline(values, width = 220, height = 44) {
  const numbers = values.map(Number).filter((value) => Number.isFinite(value));
  if (numbers.length < 2) return "";
  const min = Math.min(...numbers);
  const max = Math.max(...numbers);
  const span = max - min || 1;
  const points = numbers.map((value, index) => {
    const x = (index / (numbers.length - 1)) * (width - 8) + 4;
    const y = height - 4 - ((value - min) / span) * (height - 8);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `<svg class="pillar-spark" viewBox="0 0 ${width} ${height}" role="img" aria-label="趋势">
    <polyline points="${points}" fill="none" stroke="${DATASETS.health.accent}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}

function monthOptions(rows, selected) {
  return rows.map((row) => `<option value="${escapeHtml(row.month)}"${row.month === selected ? " selected" : ""}>${escapeHtml(row.month)}</option>`).join("");
}

async function saveRow(dataset, month, values, rowId = "") {
  const path = rowId
    ? `/api/ops/${dataset}/months/${month}/rows/${encodeURIComponent(rowId)}`
    : `/api/ops/${dataset}/months/${month}/rows`;
  return api(path, { method: "POST", body: JSON.stringify(values) });
}

function financeTotals(rows) {
  let income = 0;
  let expense = 0;
  for (const row of rows) {
    const value = Number(row.amount);
    if (!Number.isFinite(value)) continue;
    if (String(row.kind).includes("收入")) income += value;
    else expense += value;
  }
  return { income, expense, balance: income - expense };
}

async function renderHealth(host, dataset, month) {
  const summary = await api(`/api/ops/health/summary?months=12`);
  const months = summary.months || [];
  const current = months.find((item) => item.month === month) || months[0] || { month, rows: [] };
  const selected = current.month || month;
  const rows = current.rows || [];
  const weights = rows.map((row) => Number(row.weight_kg)).filter((value) => Number.isFinite(value));
  host.innerHTML = `<section class="pillar-ops-card">
    <div class="pillar-ops-head">
      <div><p class="eyebrow">健康与体重</p><h3>${escapeHtml(selected)}</h3></div>
      <select data-ops-month="health">${monthOptions(months.length ? months : [{ month: selected }], selected)}</select>
    </div>
    <div class="pillar-ops-stats">
      <div><span>最新体重</span><strong>${weights.length ? `${weights[weights.length - 1]} kg` : "—"}</strong></div>
      <div><span>本月记录</span><strong>${rows.length}</strong></div>
      ${sparkline(weights)}
    </div>
    <table class="pillar-ops-table"><thead><tr><th>日期</th><th>体重 kg</th><th>睡眠 min</th><th>运动 min</th><th>备注</th><th></th></tr></thead>
      <tbody>${rows.map((row) => `<tr>
        <td>${escapeHtml(row.date)}</td><td>${escapeHtml(row.weight_kg)}</td>
        <td>${escapeHtml(row.sleep_minutes)}</td><td>${escapeHtml(row.exercise_minutes)}</td>
        <td>${escapeHtml(row.note)}</td>
        <td><button type="button" class="text-button" data-ops-delete="health" data-row-id="${escapeHtml(row.row_id)}">删除</button></td>
      </tr>`).join("")}</tbody>
    </table>
    <form class="pillar-ops-form" data-ops-form="health">
      <input name="date" type="date" required value="${new Date().toISOString().slice(0, 10)}">
      <input name="weight_kg" inputmode="decimal" placeholder="体重 kg">
      <input name="sleep_minutes" inputmode="numeric" placeholder="睡眠 min">
      <input name="exercise_minutes" inputmode="numeric" placeholder="运动 min">
      <input name="note" placeholder="备注" maxlength="200">
      <button type="submit" class="button secondary">记一条</button>
    </form>
    <p class="field-help">数据写在 ${escapeHtml(current.path || `02_生活运营/01_健康与体重/${selected}.md`)}；Obsidian 里可以直接改。</p>
  </section>`;
}

async function renderFinance(host, dataset, month) {
  const summary = await api(`/api/ops/finance/summary?months=12`);
  const months = summary.months || [];
  const current = months.find((item) => item.month === month) || months[0] || { month, rows: [] };
  const selected = current.month || month;
  const rows = current.rows || [];
  const totals = financeTotals(rows);
  host.innerHTML = `<section class="pillar-ops-card">
    <div class="pillar-ops-head">
      <div><p class="eyebrow">财富与记账</p><h3>${escapeHtml(selected)}</h3></div>
      <select data-ops-month="finance">${monthOptions(months.length ? months : [{ month: selected }], selected)}</select>
    </div>
    <div class="pillar-ops-stats">
      <div><span>收入</span><strong>${totals.income.toFixed(2)}</strong></div>
      <div><span>支出</span><strong>${totals.expense.toFixed(2)}</strong></div>
      <div><span>结余</span><strong>${totals.balance.toFixed(2)}</strong></div>
    </div>
    <table class="pillar-ops-table"><thead><tr><th>日期</th><th>类型</th><th>金额</th><th>分类</th><th>账户</th><th>备注</th><th></th></tr></thead>
      <tbody>${rows.map((row) => `<tr>
        <td>${escapeHtml(row.date)}</td><td>${escapeHtml(row.kind)}</td><td>${escapeHtml(row.amount)}</td>
        <td>${escapeHtml(row.category)}</td><td>${escapeHtml(row.account)}</td><td>${escapeHtml(row.note)}</td>
        <td><button type="button" class="text-button" data-ops-delete="finance" data-row-id="${escapeHtml(row.row_id)}">删除</button></td>
      </tr>`).join("")}</tbody>
    </table>
    <form class="pillar-ops-form" data-ops-form="finance">
      <input name="date" type="date" required value="${new Date().toISOString().slice(0, 10)}">
      <select name="kind"><option value="支出">支出</option><option value="收入">收入</option></select>
      <input name="amount" inputmode="decimal" placeholder="金额" required>
      <input name="category" placeholder="分类" maxlength="40">
      <input name="account" placeholder="账户" maxlength="40">
      <input name="note" placeholder="备注" maxlength="200">
      <button type="submit" class="button secondary">记一笔</button>
    </form>
    <p class="field-help">数据写在 ${escapeHtml(current.path || `02_生活运营/02_财富与记账/${selected}.md`)}；Obsidian 里可以直接改。</p>
  </section>`;
}

async function renderDashboard(host) {
  const [health, finance] = await Promise.all([
    api("/api/ops/health/summary?months=6"),
    api("/api/ops/finance/summary?months=6"),
  ]);
  const latestHealth = health.months?.[0];
  const latestFinance = finance.months?.[0];
  const weights = (latestHealth?.rows || []).map((row) => Number(row.weight_kg)).filter((value) => Number.isFinite(value));
  const totals = financeTotals(latestFinance?.rows || []);
  host.innerHTML = `<div class="pillar-ops-dashboard">
    <section class="pillar-card"><p class="eyebrow">健康与体重</p>
      <p class="pillar-stat">${weights.length ? `${weights[weights.length - 1]} kg` : "还没有数据"}</p>
      ${sparkline(weights)}
      <p class="field-help">${escapeHtml(latestHealth?.month || monthNow())} · ${latestHealth?.rows?.length || 0} 条记录</p>
      <button type="button" class="text-button" data-ops-open="health">打开月度表</button>
    </section>
    <section class="pillar-card"><p class="eyebrow">财富与记账</p>
      <p class="pillar-stat">${totals.balance.toFixed(2)}</p>
      <p class="field-help">${escapeHtml(latestFinance?.month || monthNow())} · 收入 ${totals.income.toFixed(2)} · 支出 ${totals.expense.toFixed(2)}</p>
      <button type="button" class="text-button" data-ops-open="finance">打开月度表</button>
    </section>
  </div>`;
}

export async function mountOps(view) {
  const host = document.querySelector(`#${view}-view [data-ops-table], #${view}-view [data-ops-dashboard]`);
  if (!host) return;
  const dataset = host.dataset.opsTable || "";
  try {
    if (dataset === "health") await renderHealth(host, dataset, host.dataset.month || monthNow());
    else if (dataset === "finance") await renderFinance(host, dataset, host.dataset.month || monthNow());
    else await renderDashboard(host);
  } catch (error) {
    host.innerHTML = `<p class="inline-error">${escapeHtml(error.message)}</p>`;
  }
}

export function bindOpsEvents() {
  document.addEventListener("change", async (event) => {
    const select = event.target.closest?.("[data-ops-month]");
    if (!select) return;
    const host = select.closest("[data-ops-table]");
    if (!host) return;
    host.dataset.month = select.value;
    await mountOps(select.closest(".pillar-view").id.replace("-view", ""));
  });
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest?.("[data-ops-form]");
    if (!form) return;
    event.preventDefault();
    const dataset = form.dataset.opsForm;
    const host = form.closest("[data-ops-table]");
    const month = host?.dataset.month || monthNow();
    const values = Object.fromEntries(new FormData(form).entries());
    try {
      await saveRow(dataset, month, values);
      toast("已记录。");
      await mountOps("ops");
    } catch (error) {
      toast(error.message, "error");
    }
  });
  document.addEventListener("click", async (event) => {
    const remove = event.target.closest?.("[data-ops-delete]");
    if (remove) {
      const dataset = remove.dataset.opsDelete;
      const host = remove.closest("[data-ops-table]");
      const month = host?.dataset.month || monthNow();
      try {
        await api(`/api/ops/${dataset}/months/${month}/rows/${encodeURIComponent(remove.dataset.rowId)}`, { method: "DELETE" });
        await mountOps("ops");
      } catch (error) {
        toast(error.message, "error");
      }
      return;
    }
    const open = event.target.closest?.("[data-ops-open]");
    if (open) {
      const view = open.closest(".pillar-view");
      const shell = view?.querySelector(".pillar-shell");
      const key = view?.id.replace("-view", "");
      if (!shell || !key) return;
      // 复用章节切换：直接触发对应章节按钮。
      const target = shell.querySelector(`[data-pillar-chapter="${key}"][data-chapter="${open.dataset.opsOpen}"]`);
      target?.click();
    }
  });
}
