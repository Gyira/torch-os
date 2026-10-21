// 人生路 → 习惯追踪：只读展示工作台同一份习惯数据（Markdown 镜像在后端）。
import { api, escapeHtml, toast } from "./core.js";
import { shanghaiDay } from "./journal-model.js";
import { buildHabitGrid, habitGridHtml } from "./habit-grid.js";
import { dayOffset } from "./tasks-model.js";

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
      <p class="field-help">习惯打卡在工作台 → 习惯打卡；这里会同步显示同一份记录。</p>
      <button type="button" class="button secondary" data-open-workbench-habits>去工作台建立习惯</button>
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
    <button type="button" class="text-button" data-open-workbench-habits>去工作台打卡</button>
  </div>`;
}

export function bindHabitEvents() {
  document.addEventListener("click", (event) => {
    if (!event.target.closest?.("[data-open-workbench-habits]")) return;
    location.hash = "workbench";
    window.setTimeout(() => {
      document.querySelector('[data-time-page="habits"]')?.click();
    }, 250);
  });
}
