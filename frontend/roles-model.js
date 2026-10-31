// 角色抽屉的纯渲染模型（2026-10-29，v1.0 P1）。
// 这里不碰 DOM、不读 localStorage、不 import core.js，方便 node --test 直接测。
// 角色回复的 Markdown 由 roles.js 用 pillar-markdown 渲染；本文件只负责安全的结构与转义。

const MODE_LABELS = {
  synthesize: "整理", converse: "对话", classify: "判类", polish: "润色",
  discuss: "聊聊", share_digest: "整理分享", tutor: "学习指导",
  overview: "总览", tasks: "待办", suggest: "建议", advice: "建议", report: "报告",
};

export function escapeRoleText(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));
}

export function modeLabel(mode) {
  return MODE_LABELS[String(mode || "")] || String(mode || "");
}

export function roleTitle(role, mode) {
  const name = String(role?.name || "AI 角色");
  const label = modeLabel(mode);
  return label ? `${name} · ${label}` : name;
}

export function sourceMarkup(sources) {
  const items = (Array.isArray(sources) ? sources : [])
    .filter((source) => source && source.id)
    .map((source) => `<li><span class="role-source-id">${escapeRoleText(source.id)}</span>`
      + `<span>${escapeRoleText(source.title || source.kind || "")}</span></li>`)
    .join("");
  return items ? `<ul class="role-sources" aria-label="本轮参考来源">${items}</ul>` : "";
}

export function quoteMarkup(context) {
  const selection = context?.selection;
  if (!selection) return "";
  const chars = Number(selection.chars) || 0;
  const label = chars ? `引用了选文 · ${chars} 字` : "引用了选文";
  const source = selection.source ? ` data-quote-source="${escapeRoleText(selection.source)}"` : "";
  return `<span class="role-message-quote"${source}>${escapeRoleText(label)}</span>`;
}

export function messageMarkup(message) {
  const role = message?.role === "user" ? "user" : "assistant";
  const label = role === "user" ? "你" : "AI";
  const content = message?.html
    ? String(message.html)
    : `<p>${escapeRoleText(message?.content || "")}</p>`;
  return `<article class="role-message role-message-${role}" data-role-message="${role}">
    <div class="role-message-label">${label}</div>
    ${role === "user" ? quoteMarkup(message?.context) : ""}
    <div class="role-message-body">${content}</div>
    ${sourceMarkup(message?.sources)}
  </article>`;
}

export function emptyMarkup(roleName = "AI 角色") {
  return `<div class="role-empty">
    <p class="eyebrow">ROLE</p>
    <h3>${escapeRoleText(roleName)}</h3>
    <p>说说你现在想理清的事。它只提议，采纳后才写入你的库。</p>
  </div>`;
}

export function quickChips(roleId, mode) {
  if (roleId === "life_assistant" && mode === "tasks") {
    return ["把这段话拆成任务", "帮我排今天", "这周该放弃什么"];
  }
  if (roleId === "journal_companion" && mode === "discuss") {
    return ["我写的东西说明了什么？", "帮我找一个没写出来的问题"];
  }
  if (roleId === "academic_mentor" && mode === "tutor") {
    return ["我理解得浅在哪？", "下一步读什么？"];
  }
  if (roleId === "academic_mentor" && mode === "discuss") {
    return ["这段里哪个概念我没说清？", "帮我找一个反例", "下一步怎么验证？"];
  }
  if (roleId === "life_assistant" && mode === "overview") {
    return ["这周最值得做的一件事", "任务和身体状态怎么平衡？", "我现在最该停掉什么？"];
  }
  if (roleId === "love_master" && mode === "advice") {
    return ["给我三条今天能做的建议", "我是不是在回避沟通？", "怎么设边界？"];
  }
  if (roleId === "yiling" && mode === "converse") {
    return ["我最近在纠结什么？", "我为什么总是这样？", "我想改变自己的……"];
  }
  return [];
}

export function drawerSubtitle(role, mode) {
  const entryCount = Array.isArray(role?.entry) ? role.entry.length : 0;
  return `${roleTitle(role, mode)}${entryCount ? ` · ${entryCount} 个可用入口` : ""}`;
}
