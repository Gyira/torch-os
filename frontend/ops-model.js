// 生活运营首页的纯数据层（2026-10-31）。
// 不碰 DOM、不读 localStorage：首页只回答「待办本子在哪、四个生活系统是什么状态」。
import { buildNotebookCards } from "./notebook-model.js";

export const OPS_SYSTEM_CARDS = Object.freeze([
  {
    key: "love", title: "恋爱系统", eyebrow: "RELATIONSHIP", icon: "爱",
    description: "聊天分析、画像、恋爱日志、经期关怀与恋爱大师。",
    status: "ready", statusLabel: "可用",
  },
  {
    key: "health", title: "健康系统", eyebrow: "HEALTH", icon: "健",
    description: "身体、睡眠、饮食、活动、心理、训练与体检。",
    status: "ready", statusLabel: "可用",
  },
  {
    key: "finance", title: "财务系统", eyebrow: "FINANCE", icon: "财",
    description: "收支、预算与财务报告。",
    status: "soon", statusLabel: "尚未开发",
  },
  {
    key: "family", title: "家庭系统", eyebrow: "FAMILY", icon: "家",
    description: "家庭物资与智能家居。",
    status: "soon", statusLabel: "尚未开发",
  },
]);

export function opsNotebookCards(overview = {}) {
  const chapterRows = (Array.isArray(overview?.chapters) ? overview.chapters : [])
    .filter((chapter) => chapter && chapter.key === "task");
  return buildNotebookCards(
    "ops", chapterRows, [], overview?.deleted_notebooks || []);
}

export function opsHomeState(overview = {}) {
  const cards = opsNotebookCards(overview);
  const recent = (Array.isArray(overview?.recent) ? overview.recent : [])
    .filter((note) => note && note.chapter === "task")
    .slice(0, 6);
  const summary = overview?.task_summary && typeof overview.task_summary === "object"
    ? overview.task_summary : {};
  return {
    taskCard: cards.find((card) => card.key === "task") || cards[0] || null,
    cards,
    recent,
    summary: {
      open: Number(summary.open) || 0,
      overdue: Number(summary.overdue) || 0,
      dueToday: Number(summary.due_today) || 0,
    },
    systems: OPS_SYSTEM_CARDS.slice(),
  };
}
