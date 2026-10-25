// 生活运营：体重/账目月度表 + 极简 SVG 趋势（2026-10-26 起只读）。
// 表格只做浏览；记录、修改、删除都在 Obsidian 的 Markdown 月度表里完成。
import { api, escapeHtml, toast } from "./core.js";
import { openObsidianNote } from "./task-open.js";

const DATASETS = {
  health: { title: "健康与体重", accent: "var(--p-pillar-ops)", unit: "kg" },
  finance: { title: "财富与记账", accent: "var(--p-pillar-ops)" },
};

let vaultName = "";
let chapterPaths = null;

async function ensureVaultName() {
  if (!vaultName) {
    try {
      const health = await api("/api/health");
      vaultName = health?.vault_name || "";
    } catch { /* 拿不到库名时只提示 */ }
  }
  return vaultName;
}

async function ensureChapterPaths() {
  if (chapterPaths) return chapterPaths;
  chapterPaths = {};
  try {
    const manifest = await api("/api/pillars");
    const ops = (manifest.pillars || []).find((item) => item.key === "ops");
    for (const chapter of ops?.chapters || []) chapterPaths[chapter.key] = chapter.path || "";
  } catch { /* 用空表兜底 */ }
  return chapterPaths;
}

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

async function openOpsFile(dataset, month, pathHint) {
  const monthKey = month || monthNow();
  let target = String(pathHint || "");
  if (!target) {
    const paths = await ensureChapterPaths();
    const root = paths[dataset] || (dataset === "health" ? "02_生活运营/01_健康与体重" : "02_生活运营/02_财富与记账");
    target = `${root}/${monthKey}.md`;
  }
  await ensureVaultName();
  try {
    await api(`/api/notes?path=${encodeURIComponent(target)}`);
  } catch {
    toast(`这个月还没有表。在 Obsidian 里复制上个月的文件，改名成 ${monthKey} 即可。`, "warning");
    return;
  }
  if (!openObsidianNote(target, vaultName)) toast("没有可用的 Obsidian 库名，请在 Obsidian 里打开。", "warning");
}

function openButton(dataset, month, path) {
  return `<button type="button" class="button secondary" data-ops-open-file="${escapeHtml(dataset)}" data-ops-month-file="${escapeHtml(path || "")}" data-ops-month-key="${escapeHtml(month || "")}">在 Obsidian 打开月度表</button>`;
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

async function renderHealth(host, month) {
  const summary = await api(`/api/ops/health/summary?months=12`);
  const months = summary.months || [];
  const current = months.find((item) => item.month === month) || months[0] || { month, rows: [] };
  const selected = current.month || month;
  host.dataset.month = selected;
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
    <table class="pillar-ops-table"><thead><tr><th>日期</th><th>体重 kg</th><th>睡眠 min</th><th>运动 min</th><th>备注</th></tr></thead>
      <tbody>${rows.map((row) => `<tr>
        <td>${escapeHtml(row.date)}</td><td>${escapeHtml(row.weight_kg)}</td>
        <td>${escapeHtml(row.sleep_minutes)}</td><td>${escapeHtml(row.exercise_minutes)}</td>
        <td>${escapeHtml(row.note)}</td>
      </tr>`).join("")}</tbody>
    </table>
    <div class="form-actions">${openButton("health", selected, current.path)}</div>
    <p class="field-help">这里只读；记录与修改都在 Obsidian 的月度 Markdown 表里做。</p>
  </section>`;
}

async function renderFinance(host, month) {
  const summary = await api(`/api/ops/finance/summary?months=12`);
  const months = summary.months || [];
  const current = months.find((item) => item.month === month) || months[0] || { month, rows: [] };
  const selected = current.month || month;
  host.dataset.month = selected;
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
    <table class="pillar-ops-table"><thead><tr><th>日期</th><th>类型</th><th>金额</th><th>分类</th><th>账户</th><th>备注</th></tr></thead>
      <tbody>${rows.map((row) => `<tr>
        <td>${escapeHtml(row.date)}</td><td>${escapeHtml(row.kind)}</td><td>${escapeHtml(row.amount)}</td>
        <td>${escapeHtml(row.category)}</td><td>${escapeHtml(row.account)}</td><td>${escapeHtml(row.note)}</td>
      </tr>`).join("")}</tbody>
    </table>
    <div class="form-actions">${openButton("finance", selected, current.path)}</div>
    <p class="field-help">这里只读；记账与修改都在 Obsidian 的月度 Markdown 表里做。</p>
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
      ${openButton("health", latestHealth?.month || monthNow(), latestHealth?.path)}
    </section>
    <section class="pillar-card"><p class="eyebrow">财富与记账</p>
      <p class="pillar-stat">${totals.balance.toFixed(2)}</p>
      <p class="field-help">${escapeHtml(latestFinance?.month || monthNow())} · 收入 ${totals.income.toFixed(2)} · 支出 ${totals.expense.toFixed(2)}</p>
      ${openButton("finance", latestFinance?.month || monthNow(), latestFinance?.path)}
    </section>
  </div>`;
}

export async function mountOps(view) {
  const host = document.querySelector(`#${view}-view [data-ops-table], #${view}-view [data-ops-dashboard]`);
  if (!host) return;
  const dataset = host.dataset.opsTable || "";
  try {
    if (dataset === "health") await renderHealth(host, host.dataset.month || monthNow());
    else if (dataset === "finance") await renderFinance(host, host.dataset.month || monthNow());
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
  document.addEventListener("click", async (event) => {
    const open = event.target.closest?.("[data-ops-open-file]");
    if (!open) return;
    await openOpsFile(open.dataset.opsOpenFile,
                      open.dataset.opsMonthKey || "",
                      open.dataset.opsMonthFile || "");
  });
}
