// 待办事项的 AI 入口（2026-10-29，v1.0 P1）。
// 只负责把当前任务上下文带进角色抽屉；真正的写入由 proposal 采纳后走后端。
import { $, toast } from "./core.js";
import { openRoleDrawer } from "./roles.js";
import { loadTimeTasks } from "./time-module.js";

let bound = false;

export function initTasksAI() {
  if (bound) return;
  bound = true;
  $("#time-ai-btn")?.addEventListener("click", () => {
    openRoleDrawer({
      roleId: "life_assistant",
      mode: "tasks",
      entry: "workbench.tasks",
      title: "生活管家",
      selectors: [{ entry: "tasks" }, { entry: "identity" }],
      prompt: "帮我理一下今天的任务，先指出真正的瓶颈。",
    }).catch((error) => toast(`打不开 AI 助手：${error.message}`, "error"));
  });
  // 提案采纳后任务页要重读；app.js 已有全局刷新，这里只兜底当前可见页。
  document.addEventListener("torch:proposals-changed", () => {
    if (document.body?.classList?.contains("view-workbench")) {
      loadTimeTasks?.({ silent: true })?.catch?.(() => {});
    }
  });
}
