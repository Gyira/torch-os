// 人生路 → 习惯追踪：只读展示工作台同一份习惯数据（Markdown 镜像在后端）。
// 2026-10-26：四库只读；要改习惯去工作台，或直接在 Obsidian 里编辑镜像文件。
import { api, escapeHtml, toast } from "./core.js";
import { shanghaiDay } from "./journal-model.js";
import { openObsidianNote } from "./task-open.js";
import { buildHabitGrid, habitGridHtml } from "./habit-grid.js";
import { dayOffset } from "./tasks-model.js";

let habitFile = "";

async function ensureHabitFile() {
  if (habitFile) return habitFile;
  try {
    const manifest = await api("/api/pillars");
    const life = (manifest.pillars || []).find((item) => item.key === "life");
    const chapter = (life?.chapters || []).find((item) => item.key === "habit");
    if (chapter?.path) habitFile = `${chapter.path}/习惯定义.md`;
  } catch { /* 没有路径时只提示 */ }
  return habitFile;
}

function vaultName() {
  try {
    return globalThis.__torchVaultName || "";
  } catch {
    return "";
  }
}

async function openHabitFile() {
  const path = await ensureHabitFile();
  if (!path) {
    toast("还没拿到习惯文件路径，请刷新后重试。", "warning");
    return;
  }
  let name = vaultName();
  if (!name) {
    try { name = (await api("/api/health"))?.vault_name || ""; } catch { /* 没有库名 */ }
  }
  if (!openObsidianNote(path, name)) toast("没有可用的 Obsidian 库名，请在 Obsidian 里打开。", "warning");
}

function rateText(summary) {
  const rate = summary?.completion_rate;
  if (rate === null || rate === undefined) return "还没有可计算的完成率";
  return `完成率 ${Math.round(Number(rate) * 100)}%`;
}

export async function mountHabits() {
  const host = document.querySelector("#life-view [data-habit-panel]");
  if (!host) return;
  const today = shanghaiDay();
  const from = dayOffset(today, -29);
  const habits = await api("/api/habits?status=active");
  if (!Array.isArray(habits) || !habits.length) {
    host.innerHTML = `<section class="pillar-empty">
      <p>还没有在记录的习惯。</p>
      <p class="field-help">习惯打卡在工作台 → 习惯打卡；这里只读显示同一份记录。</p>
      <div class="form-actions">
        <button type="button" class="button secondary" data-open-workbench-habits>去工作台建立习惯</button>
        <button type="button" class="text-button" data-open-habit-file>在 Obsidian 打开习惯文件</button>
      </div>
    </section>`;
    return;
  }
  const cards = await Promise.all(habits.map(async (habit) => {
    let summary = null;
    try {
      summary = await api(`/api/habits/${habit.id}/summary?from_date=${from}&to_date=${today}`);
    } catch (error) {
      summary = null;
    }
    const entries = summary?.entries || [];
    return `<section class="pillar-habit-card">
      <div class="pillar-ops-head">
        <div><p class="eyebrow">习惯</p><h3>${escapeHtml(habit.title)}</h3></div>
        <span class="meta">${escapeHtml(rateText(summary))}</span>
      </div>
      ${habit.cue || habit.minimum_action ? `<p class="field-help">${escapeHtml([habit.cue, habit.minimum_action].filter(Boolean).join(" · "))}</p>` : ""}
      ${habitGridHtml(buildHabitGrid(entries, from, today))}
    </section>`;
  }));
  host.innerHTML = `<div class="pillar-habit-list">${cards.join("")}
    <div class="form-actions">
      <button type="button" class="text-button" data-open-workbench-habits>去工作台打卡</button>
      <button type="button" class="text-button" data-open-habit-file>在 Obsidian 打开习惯文件</button>
    </div>
  </div>`;
}

export function bindHabitEvents() {
  document.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-open-habit-file]")) {
      openHabitFile();
      return;
    }
    if (!event.target.closest?.("[data-open-workbench-habits]")) return;
    location.hash = "workbench";
    window.setTimeout(() => {
      document.querySelector('[data-time-page="habits"]')?.click();
    }, 250);
  });
}
