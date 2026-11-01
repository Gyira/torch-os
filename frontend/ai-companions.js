// AI 角色身份层（2026-10-31）：四库/编辑器/选区 → 角色卡。
// 纯函数，不碰 DOM；角色 id 与后端 dsh/roles 保持一致。
export const COMPANIONS = Object.freeze({
  life: Object.freeze({
    roleId: "journal_companion", mode: "discuss", entry: "life.editor",
    title: "写作伙伴", layer: "life", short: "写作伙伴",
  }),
  knowledge_home: Object.freeze({
    roleId: "academic_mentor", mode: "tutor", entry: "knowledge.editor",
    title: "学术导师", layer: "knowledge", short: "学术导师",
  }),
  knowledge_editor: Object.freeze({
    roleId: "academic_mentor", mode: "discuss", entry: "knowledge.editor",
    title: "学术导师", layer: "knowledge", short: "学术导师",
  }),
  ops: Object.freeze({
    roleId: "life_assistant", mode: "overview", entry: "ops.overview",
    title: "生活管家", layer: "ops", short: "生活管家",
  }),
  ops_tasks: Object.freeze({
    roleId: "life_assistant", mode: "tasks", entry: "workbench.tasks",
    title: "生活管家", layer: "ops", short: "生活管家",
  }),
  love: Object.freeze({
    roleId: "love_master", mode: "advice", entry: "love.overview",
    title: "恋爱大师", layer: "love", short: "恋爱大师",
  }),
  health: Object.freeze({
    roleId: "health_doctor", mode: "advice", entry: "health.report",
    title: "私人医生", layer: "health", short: "私人医生",
  }),
});

export function companionForView(view, { chapter = "", pane = "" } = {}) {
  const key = String(view || "");
  if (key === "life") return COMPANIONS.life;
  if (key === "knowledge") return COMPANIONS.knowledge_home;
  if (key === "ops") {
    if (pane === "love-view") return COMPANIONS.love;
    if (pane === "health-view") return COMPANIONS.health;
    if (String(chapter || "") === "task") return COMPANIONS.ops_tasks;
    return COMPANIONS.ops;
  }
  return null;
}

export function companionForEditor(context) {
  const key = String(context || "life");
  if (key === "knowledge") return COMPANIONS.knowledge_editor;
  if (key === "ops") return COMPANIONS.ops_tasks;
  return COMPANIONS.life;
}

export function companionForReader(view, chapter = "") {
  const key = String(view || "");
  if (key === "knowledge") return COMPANIONS.knowledge_editor;
  if (key === "ops" && String(chapter || "") === "task") return COMPANIONS.ops_tasks;
  return companionForView(view, { chapter });
}

export function companionTitle(companion, fallback = "AI 角色") {
  return companion?.title || fallback;
}

/**
 * 阅读器打开角色时先给的几个问题：贴着当前文档，而不是“先复述一遍”。
 * 纯数据，方便测试；真正的资料仍由后端按 note 选择器读取。
 */
export function documentChips(view, chapter = "") {
  const key = String(view || "");
  if (key === "knowledge") {
    return ["这篇最该记住什么？", "提炼成能用的方法", "哪里我还没想清楚？"];
  }
  if (key === "ops" && String(chapter || "") === "task") {
    return ["这个任务该怎么拆？", "先做哪一步？"];
  }
  if (key === "life") {
    if (String(chapter || "") === "journal") {
      return ["今天真正发生了什么？", "帮我找一个没写出来的问题"];
    }
    return ["这篇里我真正想说什么？", "帮我找一句没写出来的话"];
  }
  return ["这篇讲了什么？", "指出我没想清楚的地方"];
}
