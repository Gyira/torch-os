// 四象限页（2026-10-08，第 3 轮，照滴答实测规格）：
// 2×2 面板，标题是艾森豪威尔名称，与四档优先级一一对应；「无优先级」那一格把没日期的按「无日期」分组。
// 分桶用 tasks-model.js 的 quadrantBuckets（纯函数）；任务行复用任务板那一套（右键就能改优先级 → 换象限）。
import { escapeHtml } from "./core.js";
import { quadrantBuckets, priorityMeta } from "./tasks-model.js";
import { taskRow } from "./tasks.js";

/** 画四象限页。options：tasks / taskOptions（交给 taskRow）。 */
export function renderQuadrant(container, options = {}) {
  const { tasks = [], taskOptions = {} } = options;
  const buckets = quadrantBuckets(tasks);

  container.replaceChildren();
  const head = document.createElement("header");
  head.className = "time-head";
  head.innerHTML = "<h3>四象限</h3>" +
    '<span class="meta" role="status">按优先级分格：改优先级（右键任务）就会换到对应象限</span>';
  container.append(head);

  const grid = document.createElement("div");
  grid.className = "time-quad";
  for (const bucket of buckets) {
    const meta = priorityMeta(bucket.priority);
    const panel = document.createElement("section");
    panel.className = `time-quad-panel prio-${bucket.priority}`;
    panel.setAttribute("aria-label", `${bucket.label}（${bucket.hint}）`);
    const panelHead = document.createElement("header");
    panelHead.className = "time-quad-head";
    panelHead.innerHTML = `<span class="task-flag prio-${bucket.priority}" aria-hidden="true">${meta.flag || "🏳"}</span>` +
      `<strong>${escapeHtml(bucket.label)}</strong>` +
      `<span class="time-quad-hint">${escapeHtml(bucket.hint)}</span>` +
      `<span class="time-quad-count">${bucket.total}</span>`;
    panel.append(panelHead);

    const list = document.createElement("ul");
    list.className = "task-list";
    // 无优先级那格：先列没日期的（照滴答：里面按「无日期」分组），再列有日期的
    if (bucket.undated.length && bucket.priority === "none") {
      const label = document.createElement("li");
      label.className = "time-quad-group";
      label.textContent = `无日期 ${bucket.undated.length}`;
      list.append(label);
      for (const task of bucket.undated) list.append(taskRow(task, taskOptions));
    }
    for (const task of (bucket.priority === "none" ? bucket.dated : bucket.undated.concat(bucket.dated))) {
      list.append(taskRow(task, taskOptions));
    }
    if (!bucket.total) {
      const empty = document.createElement("li");
      empty.className = "time-quad-empty field-help";
      empty.textContent = "没有任务";
      list.append(empty);
    }
    panel.append(list);
    grid.append(panel);
  }
  container.append(grid);
}
